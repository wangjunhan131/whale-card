#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""t117：收件层「除杂去噪 → 推短期池」验收

用户 2026-11-08 的口径：收件箱只做三件事 —— 把所有原文装进去、除杂去噪、推短期池。
所以这里要验的不是"它挑得准不准"（那是价值判断），而是：
  · 噪声拦得住（纯符号 / 一两字 / 哈哈哈 / 与最近重复 / 别家 AI）
  · 有内容的原文整条过得去（不改写、不摘要）
  · 推进去的是短期池，且回填 kept / kept_mid
  · 没被挑中的留在收件层、到期被清
"""
import os
import shutil
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import _cardcopy  # noqa: E402

FAIL = []


def ck(name, cond, extra=""):
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (("  " + str(extra)) if (extra and not cond) else ""))
    if not cond:
        FAIL.append(name)


def main():
    src = os.path.join(ROOT, "card.db")
    if not os.path.exists(src):
        print("没有 card.db，跳过")
        return 0
    tmp = tempfile.mkdtemp(prefix="t117_")
    db = os.path.join(tmp, "card.db")
    _cardcopy.copy_card_db(src, db)
    import card2
    c = card2.MemoryCard(db)

    print("\n[1] 噪声规则（除杂，不是价值判断）")
    cases = [
        ("😂😂😂😂", "纯符号/表情", 0.2),
        ("嗯", "太短", 0.2),
        ("哈哈哈哈哈哈哈哈", "重复字", 0.2),
        ("？？？", "纯符号", 0.2),
    ]
    for txt, want, cap in cases:
        sc, why = c.inbox_score({"text": txt, "channel": "private"})
        ck("「%s」判噪声（%s）" % (txt[:8], why), sc <= cap, sc)

    print("\n[2] 有内容的原文过得去")
    good = [
        ("今天物理考了 78 分，最后那道电磁感应的题我算错了一个符号", "private"),
        ("我看《月球殖民地小说》第一回，龙孟华对着月亮说可惜身无两翼", "bili"),
        ("我在群里问了半天没人理我", "private"),
    ]
    for txt, ch in good:
        sc, why = c.inbox_score({"text": txt, "channel": ch})
        ck("过线：%s" % txt[:16], sc >= 0.30, "%s %s" % (sc, why))

    print("\n[3] 与最近重复的要压下去")
    base = "今天下午第三节课老师讲电磁感应那道题"
    sc1, _ = c.inbox_score({"text": base, "channel": "private"})
    sc2, why2 = c.inbox_score({"text": base + "。", "channel": "private"},
                              recent_texts=[base])
    ck("重复的被压下去", sc2 < sc1, "%s -> %s (%s)" % (sc1, sc2, why2))

    print("\n[4] 别家 AI 的话降权")
    scb, whyb = c.inbox_score({"text": "今天我来给大家分享一个非常好用的工具",
                               "channel": "group", "note": "bot"})
    scn, _ = c.inbox_score({"text": "今天我来给大家分享一个非常好用的工具",
                            "channel": "group"})
    ck("标了 bot 的更低", scb < scn, "%s vs %s" % (scb, scn))

    print("\n[5] commit：整条原文进短期池 + 回填 kept")
    t1 = "我妈今天说周末带我去买新的机械键盘，预算 300 左右"
    t2 = "嗯嗯"
    i1 = c.inbox_add(t1, source="qq", channel="private", who="使用者",
                     uid="100000000")
    i2 = c.inbox_add(t2, source="qq", channel="private", who="使用者",
                     uid="100000000")
    d = c.inbox_pick(limit=50, commit=True)
    ck("写了东西", d.get("n_wrote", 0) >= 1, d.get("n_wrote"))
    ids = [w["id"] for w in d.get("wrote") or []]
    ck("那条实内容进去了", i1 in ids, ids)
    ck("噪声没进去", i2 not in ids, ids)
    rows = {r["id"]: r for r in c.inbox_list(limit=100, full=True)}
    ck("kept=1 且记了 kept_mid", int(rows[i1].get("kept") or 0) == 1
       and rows[i1].get("kept_mid"), rows[i1].get("kept_mid"))
    ck("噪声仍 kept=0", int(rows[i2].get("kept") or 0) == 0)

    print("\n[6] 进的是短期池，不是别的池")
    mid = rows[i1]["kept_mid"]
    cur = c.db.cursor()
    cur.execute("SELECT pool, raw FROM mem WHERE id=?", (mid,))
    r = cur.fetchone()
    ck("池子是 short", r and r["pool"] == "short", r["pool"] if r else None)
    ck("原文一字不改", r and r["raw"] == t1, (r["raw"] if r else None))

    print("\n[7] 已经挑过的不会再被挑一次")
    d2 = c.inbox_pick(limit=50, commit=True)
    ck("第二轮没重复写同一条", i1 not in [w["id"] for w in d2.get("wrote") or []])

    print("\n[8] 过期清理：没挑中的到期就走")
    n = c.inbox_gc(ttl_hours=0.0)
    ck("gc 删掉了未挑中的", (n.get("dropped", 0) >= 1) or (n.get("blanked", 0) >= 1), n)
    left = c.inbox_list(limit=100, full=False)
    ck("挑中的（kept=1）还在", any(x["id"] == i1 for x in left))

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n" + ("ALL OK" if not FAIL else ("FAILED: " + ", ".join(FAIL))))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
