"""恢复演练：把一个真卡「备份出来能不能重新站起来」走一遍。

为什么（2026-11-11 稳定性口径）：备份每天都在做，但「备份能不能用」这件事
从来没验证过 —— 备份文件坏一格、锚丢了、检查点对不上，平时一点症状都没有，
真要恢复那天才发现就太晚了。

做法：全程在临时目录里，不碰真卡。
  1. sqlite backup API 拷一份 card.db（不用 copy 文件的方式，WAL 会丢）
  2. 连锚 anchor.json / seal.key 一起拷（锚不在旁边，恢复出来就是「来历不明」的卡）
  3. 打开副本：integrity_check、行数、链头、检查点校验、锚校验
  4. 逐项打 PASS/FAIL，任何一项 FAIL 就退出码 1
"""
import os
import sqlite3
import shutil
import sys
import tempfile
import json

ROOT = r"whale-card"
SRC = os.path.join(ROOT, "card.db")
sys.path.insert(0, ROOT)

import anchor
import checkpoint


def main():
    tmp = tempfile.mkdtemp(prefix="whale-restore-")
    dst = os.path.join(tmp, "card.db")
    s = sqlite3.connect(SRC)
    d = sqlite3.connect(dst)
    s.backup(d)
    d.close()
    s.close()
    for name in ("anchor.json", "anchor.key", "seal.key"):
        p = os.path.join(ROOT, name)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(tmp, name))

    rows = {}
    c = sqlite3.connect(dst)
    try:
        rows["integrity"] = c.execute("PRAGMA integrity_check").fetchone()[0]
        for t in ("mem", "dialogue", "timeline", "mem_archive", "promise"):
            try:
                rows[t] = int(c.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0])
            except Exception as e:
                rows[t] = "ERR:%s" % e
        rows["head_seq"] = int(c.execute(
            "SELECT COALESCE(MAX(seq),0) FROM timeline").fetchone()[0])
    finally:
        c.close()

    ok = True

    def chk(name, good, detail=""):
        nonlocal ok
        ok = ok and bool(good)
        print(("PASS " if good else "FAIL ") + name + ("  " + str(detail) if detail else ""))

    chk("integrity_check", rows["integrity"] == "ok", rows["integrity"])
    chk("mem 行数", isinstance(rows["mem"], int) and rows["mem"] > 0, rows["mem"])
    chk("dialogue 行数", isinstance(rows["dialogue"], int) and rows["dialogue"] > 0,
        rows["dialogue"])
    chk("timeline 行数", isinstance(rows["timeline"], int) and rows["timeline"] > 0,
        rows["timeline"])

    try:
        live_head = checkpoint.live_head_seq(SRC)
        chk("链头一致", rows["head_seq"] == int(live_head),
            "%s vs %s" % (rows["head_seq"], live_head))
    except Exception as e:
        chk("链头一致", False, "%s: %s" % (type(e).__name__, e))

    try:
        v = checkpoint.verify(dst)
        chk("检查点校验", bool(v.get("ok")), json.dumps(v, ensure_ascii=False)[:160])
    except Exception as e:
        chk("检查点校验", False, "%s: %s" % (type(e).__name__, e))

    # 锚要分两件事看：
    #   ① 签名/内容有没有被动过 → 用锚自己记的 db 标签验签，再比条数与链头
    #   ② 副本换了路径 → 标签当然对不上，这不算损坏（搬家流程里要重新落锚）
    try:
        aj = json.loads(open(os.path.join(tmp, "anchor.json"),
                             encoding="utf-8").read())
        rec = {k: v for k, v in aj.items() if k != "sig"}
        chk("锚签名有效", anchor._sign_rec(rec, aj.get("db")) == aj.get("sig"),
            "head_seq=%s" % aj.get("head_seq"))
        # 锚记的 count 是「时间线/出生链」的条数，不是记忆条数（两组数字别混）
        chk("锚内容一致", int(aj.get("count") or 0) == rows["timeline"],
            "锚 %s 条 / 时间线 %s 条" % (aj.get("count"), rows["timeline"]))
        chk("锚链头一致", int(aj.get("head_seq") or 0) == int(live_head),
            "锚 %s / 卡 %s" % (aj.get("head_seq"), live_head))
        chk("锚路径标注", str(aj.get("db")) != dst,
            "标签指向原卡（副本要重新落锚，属预期）")
    except Exception as e:
        chk("锚签名有效", False, "%s: %s" % (type(e).__name__, e))

    print("\n副本目录：%s" % tmp)
    print("RESULT", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
