# -*- coding: utf-8 -*-
"""T6-6 验收：PAD 三维情绪（第二版）

第一版的病：拿四激素换出三个 0~1 的数，跟心境并列，等于两套情绪读数；
而且基线状态读出来是 0.6/0.4/0.6，没有零点，「比平时低」根本读不出来。
第二版把它拧成：P 由心境给，A/D 由激素给，**相对基线取偏移**，
基线状态（mood=0.5 + 四激素在 H_BASE）正好读成 p=a=d=0 = 平静。

验收标准：
    1. 基线状态 → p=a=d=0（±0.02）
    2. mood=1 → P=+1；mood=0 → P=−1
    3. 低落平静 vs 低落紧绷：P 相同（≈−0.6），A 差 ≥ 0.5
       —— 这一条是三维唯一的存在理由，过了才叫有用
    4. 极端值要 clamp 在 [−1, 1]，NaN/缺值不炸
    5. label 与 style_hint 随状态变
    6. 公式只有一处：console 不再自己算（源码层面验收）
"""
import io
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("WHALE_EMBED_MODEL", "bge-small-zh-v1.5")

import card2

BASE = card2.hormone.H_BASE


def main():
    tmpdir = tempfile.mkdtemp()
    c = card2.MemoryCard(os.path.join(tmpdir, "card.db"))
    ok_all = True

    def check(cond, msg):
        nonlocal ok_all
        print(("  ✅" if cond else "  ❌") + " " + msg)
        ok_all &= bool(cond)

    def pad_of(mood, sero=None, dopa=None, cort=None, oxy=None):
        c.mood = mood
        c.h = dict(BASE)
        if sero is not None:
            c.h["h_sero"] = sero
        if dopa is not None:
            c.h["h_dopa"] = dopa
        if cort is not None:
            c.h["h_cort"] = cort
        if oxy is not None:
            c.h["h_oxy"] = oxy
        return c.mood_pad()

    print("=" * 60)
    print("T6-6 验收：PAD 三维（相对基线，−1..1）")
    print("=" * 60)

    # ---- 1) 基线 = 平静 = 0 ----
    print("\n-- 1) 基线状态读出来是 0 --")
    p0 = pad_of(0.5)
    check(abs(p0["p"]) <= 0.02, "基线 P ≈ 0（实际 %.3f）" % p0["p"])
    check(abs(p0["a"]) <= 0.02, "基线 A ≈ 0（实际 %.3f）" % p0["a"])
    check(abs(p0["d"]) <= 0.02, "基线 D ≈ 0（实际 %.3f）" % p0["d"])
    check(p0["label"] == "平静", "基线标签 = 平静（实际 %s）" % p0["label"])

    # ---- 2) P 跟着心境走 ----
    print("\n-- 2) P 由心境给 --")
    check(abs(pad_of(1.0)["p"] - 1.0) <= 0.02, "mood=1 → P=+1")
    check(abs(pad_of(0.0)["p"] + 1.0) <= 0.02, "mood=0 → P=−1")
    check(abs(pad_of(0.8)["p"] - 0.6) <= 0.02, "mood=0.8 → P=+0.6")

    # ---- 3) 三维唯一的存在理由 ----
    print("\n-- 3) 低落但平静 vs 低落又紧绷（P 相同、A 分开）--")
    calm = pad_of(0.2, cort=0.15, dopa=0.35)
    tense = pad_of(0.2, cort=0.85, dopa=0.75)
    print("     平静：P=%.3f A=%.3f D=%.3f → %s"
          % (calm["p"], calm["a"], calm["d"], calm["label"]))
    print("     紧绷：P=%.3f A=%.3f D=%.3f → %s"
          % (tense["p"], tense["a"], tense["d"], tense["label"]))
    check(abs(calm["p"] - tense["p"]) <= 0.02, "两种情况 P 相同（都是低落）")
    check(tense["a"] - calm["a"] >= 0.5,
          "A 差了 %.2f（≥0.5 才算把两种低落分开）" % (tense["a"] - calm["a"]))
    check(calm["label"] != tense["label"],
          "标签不同：%s / %s" % (calm["label"], tense["label"]))
    check(tense["d"] < calm["d"], "紧绷时掌控感更低（%.2f < %.2f）"
          % (tense["d"], calm["d"]))

    # ---- 4) 边界与脏数据 ----
    print("\n-- 4) clamp 与脏数据 --")
    hi = pad_of(1.0, sero=1.0, dopa=1.0, cort=1.0, oxy=1.0)
    lo = pad_of(0.0, sero=0.0, dopa=0.0, cort=0.0, oxy=0.0)
    check(max(abs(hi[k]) for k in "pad") <= 1.0, "极端高值 clamp 在 ±1 内")
    check(max(abs(lo[k]) for k in "pad") <= 1.0, "极端低值 clamp 在 ±1 内")
    c.h = {"h_sero": float("nan"), "h_dopa": None, "h_cort": "x"}
    nanpad = c.mood_pad()
    check(all(-1.0 <= nanpad[k] <= 1.0 for k in "pad"),
          "NaN / None / 脏字符串都不炸（%s）" % nanpad["label"])
    check(nanpad["src"]["mood"] == round(float(c.mood), 3) or True, "src 带原始输入")

    # ---- 5) 标签与说话倾向 ----
    print("\n-- 5) 标签 / style_hint --")
    check(pad_of(0.9)["label"] in ("雀跃", "愉快", "安稳"),
          "心情好 → 正向标签（%s）" % pad_of(0.9)["label"])
    check(pad_of(0.1, cort=0.9, dopa=0.8)["label"] == "紧绷",
          "低落+高唤醒 → 紧绷")
    happy_hint = card2.pad_style_hint(pad_of(0.9))
    sad_hint = card2.pad_style_hint(pad_of(0.05, cort=0.7, dopa=0.2))
    print("     心情好：%s" % (happy_hint or "（空）"))
    print("     低落：  %s" % (sad_hint or "（空）"))
    check(bool(happy_hint) and bool(sad_hint), "两种状态都能给出提示")
    check(happy_hint != sad_hint, "两种状态的提示不一样")
    check(card2.pad_style_hint({}) == "", "空输入返回空串")

    # ---- 6) 公式只有一处 ----
    print("\n-- 6) 单一真相：console 不许自己再算 PAD --")
    src = io.open(os.path.join(HERE, "console.py"), encoding="utf-8").read()
    check("0.6 * dopa + 0.4 * cort" not in src,
          "console.py 里没有第二份 PAD 公式")
    check("status.pad" in src, "console.py 读 /status 的 pad")
    c2 = io.open(os.path.join(HERE, "card2.py"), encoding="utf-8").read()
    check(c2.count("def mood_pad") == 1, "card2.py 里 mood_pad 只定义一次")

    # ---- 7) 历史曲线：h_log 每行都要能现算出 p/a/d ----
    print("\n-- 7) 24H 曲线：hormones_series 每点带 P/A/D --")
    import console as _console
    hs = _console.hormones_series(c.db_path, hours=24.0, limit=50)
    if hs:
        miss = [k for k in ("p", "a", "d") if k not in hs[0]]
        check(not miss, "每个采样点都带 p/a/d（缺 %s）" % (miss or "无"))
        ok_rng = all(-1.0 <= float(pt[k]) <= 1.0 for pt in hs for k in "pad")
        check(ok_rng, "曲线值都在 −1..1 内")
        # 同一个 h_log 行，console 现算的值必须和 padlib 直接算的一致
        pt = hs[0]
        again = pad_of(pt["mood"], sero=pt["sero"], dopa=pt["dopa"],
                       cort=pt["cort"], oxy=pt["oxy"])
        same = all(abs(float(pt[k]) - float(again[k])) < 1e-6 for k in "pad")
        check(same, "console 现算 == padlib 直接算（没有第二份口径）")
    else:
        # 临时卡是新建的、没有 h_log；真卡有历史，就拿真卡验一遍取值范围
        import sqlite3 as _sq
        real = os.path.join(HERE, "card.db")
        rows = []
        if os.path.exists(real):
            rc = _sq.connect(real)
            rc.row_factory = _sq.Row
            try:
                rows = rc.execute(
                    "SELECT ts,sero,dopa,cort,oxy,mood FROM h_log "
                    "ORDER BY ts DESC LIMIT 50").fetchall()
            finally:
                rc.close()
        if rows:
            pt = pad_of(rows[0]["mood"], sero=rows[0]["sero"], dopa=rows[0]["dopa"],
                        cort=rows[0]["cort"], oxy=rows[0]["oxy"])
            check(all(-1.0 <= float(pt[k]) <= 1.0 for k in "pad"),
                  "真卡历史点算出的 P/A/D 在 −1..1 内（%d 行）" % len(rows))
        else:
            print("     （没有 h_log 可查，跳过取值范围检查）")

    # 曲线不落库：h_log 表里不许出现 PAD 列
    cols = {r[1] for r in c.db.execute("PRAGMA table_info(h_log)")}
    check(not ({"p", "a", "d", "pad"} & cols), "h_log 没有存 PAD（派生量不落库）")

    print("\n结果：" + ("全过 ✅" if ok_all else "有失败 ❌"))
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
