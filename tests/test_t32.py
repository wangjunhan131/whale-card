# -*- coding: utf-8 -*-
"""T3-2 验收：内分泌四激素

验收标准（施工总表）：
  皮质醇高时 Q 分上限下降；催产素高时羁绊记忆更容易被召回
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import card2
import hormone as H
from card2 import MemoryCard, Clock, DAY
HOUR = 3600.0


def main():
    ok_all = True

    # ---- 1) 基线 ----
    hs = dict(H.H_BASE)
    ok = (hs["h_sero"] == 0.60 and hs["h_dopa"] == 0.40
          and hs["h_cort"] == 0.20 and hs["h_oxy"] == 0.50)
    print(("  ✅" if ok else "  ❌") + f" 基线四激素：{H.describe(hs)}")
    ok_all &= ok

    # ---- 2) 回归：半衰期 34.7 小时 ----
    hs2 = dict(H.H_BASE)
    hs2["h_cort"] = 1.0
    after = H.regress_all(hs2, 34.7)["h_cort"]
    expected = 0.20 + (1.0 - 0.20) * 0.5     # 半衰期后应剩一半
    ok = abs(after - expected) < 0.02
    print(("  ✅" if ok else "  ❌") +
          f" 半衰期 34.7h：皮质醇 1.0 → {after:.3f}（应约 {expected:.3f}）")
    ok_all &= ok

    # 回归方向：都往基线靠
    hs3 = {"h_sero": 0.1, "h_dopa": 0.9, "h_cort": 0.9, "h_oxy": 0.9}
    r3 = H.regress_all(hs3, 48)
    ok = (r3["h_sero"] > 0.1 and r3["h_dopa"] < 0.9
          and r3["h_cort"] < 0.9 and r3["h_oxy"] < 0.9)
    print(("  ✅" if ok else "  ❌") +
          f" 48h 后都往基线靠：{H.describe(r3)}")
    ok_all &= ok

    # ---- 3) 事件分类 ----
    cases = [
        ("我好喜欢你啊", "A"),
        ("谢谢你今天陪我", "A"),
        ("你很烦，闭嘴", "B"),
        ("我今天考砸了，很难受", "C"),
        ("今天上午有课", None),
    ]
    for txt, want in cases:
        kind, s = H.classify_event(txt)
        ok = (kind == want)
        print(("  ✅" if ok else "  ❌") +
              f" 分类「{txt}」→ {kind} (s={s:.2f})，期望 {want}")
        ok_all &= ok

    # ---- 4) 三类事件的扰动方向 ----
    base = dict(H.H_BASE)
    a = H.apply_event(base, "A", 0.9)
    ok = (a["h_oxy"] > base["h_oxy"] and a["h_dopa"] > base["h_dopa"]
          and a["h_cort"] < base["h_cort"])
    print(("  ✅" if ok else "  ❌") + f" A 正向：{H.describe(a)}")
    ok_all &= ok

    b = H.apply_event(base, "B", 0.9)
    ok = (b["h_cort"] > base["h_cort"] and b["h_sero"] < base["h_sero"]
          and b["h_dopa"] < base["h_dopa"])
    print(("  ✅" if ok else "  ❌") + f" B 负面：{H.describe(b)}")
    ok_all &= ok

    c = H.apply_event(base, "C", 0.9)
    ok = (c["h_sero"] > base["h_sero"] and c["h_oxy"] > base["h_oxy"]
          and c["h_cort"] < base["h_cort"])
    print(("  ✅" if ok else "  ❌") + f" C 治愈：{H.describe(c)}")
    ok_all &= ok

    # ---- 5) 依恋惯性：一句狠话不把 h_oxy 打穿 ----
    deep = dict(H.H_BASE)
    deep["h_oxy"] = 0.95
    after_hurt = H.apply_event(deep, "B", 1.0)
    drop = deep["h_oxy"] - after_hurt["h_oxy"]
    ok = drop < 0.05
    print(("  ✅" if ok else "  ❌") +
          f" 依恋 0.95 挨一句狠话只掉 {drop:.4f}（惯性保护）")
    ok_all &= ok
    # 比的是**相对**跌幅：深的跌 3%，浅的跌 7%
    shallow = dict(H.H_BASE)
    shallow["h_oxy"] = 0.30
    after2 = H.apply_event(shallow, "B", 1.0)
    drop2 = shallow["h_oxy"] - after2["h_oxy"]
    rel_deep = drop / deep["h_oxy"]
    rel_shallow = drop2 / shallow["h_oxy"]
    ok = rel_shallow > rel_deep
    print(("  ✅" if ok else "  ❌") +
          f" 相对跌幅：深 {rel_deep:.1%} < 浅 {rel_shallow:.1%}（浅的掉得更狠）")
    ok_all &= ok

    # ---- 6) Q 分上限压制（皮质醇高 → 发挥变差）----
    q_low = H.q_max({"h_cort": 0.20})
    q_high = H.q_max({"h_cort": 1.00})
    ok = (q_low == 5.0 and abs(q_high - 2.0) < 0.01)
    print(("  ✅" if ok else "  ❌") +
          f" Q 上限：皮质醇 0.20 → {q_low:.2f}，1.00 → {q_high:.2f}")
    ok_all &= ok

    # ---- 7) f_endo 范围 ----
    f_min = H.f_endo({"h_oxy": 0.0, "h_cort": 1.0})
    f_max = H.f_endo({"h_oxy": 1.0, "h_cort": 0.0})
    ok = (f_min < 0.80 and f_max > 1.10)
    print(("  ✅" if ok else "  ❌") +
          f" f_endo 区间：{f_min:.3f} ~ {f_max:.3f}")
    ok_all &= ok

    # ---- 8) 陪伴加成 ----
    bonus_lo = H.attach_bonus({"h_oxy": 0.0})
    bonus_hi = H.attach_bonus({"h_oxy": 1.0})
    ok = (abs(bonus_lo - 0.75) < 0.01 and abs(bonus_hi - 1.25) < 0.01)
    print(("  ✅" if ok else "  ❌") +
          f" 陪伴加成：{bonus_lo:.2f} ~ {bonus_hi:.2f}")
    ok_all &= ok

    # ---- 9) style_hint 说人话 ----
    hint_hi = H.style_hint({"h_dopa": 0.9, "h_sero": 0.9, "h_cort": 0.05,
                            "h_oxy": 0.9})
    hint_lo = H.style_hint({"h_dopa": 0.1, "h_sero": 0.2, "h_cort": 0.9,
                            "h_oxy": 0.1})
    print(f"  高多巴胺/高血清素/低皮质醇：{hint_hi}")
    print(f"  低多巴胺/低血清素/高皮质醇：{hint_lo}")
    ok = bool(hint_hi) and bool(hint_lo) and hint_hi != hint_lo
    print(("  ✅" if ok else "  ❌") + " 两种状态给出不同的说话倾向")
    ok_all &= ok

    # ---- 10) 接进卡片：一句话真的推动激素 ----
    tmp = tempfile.mktemp(suffix=".db")
    card = MemoryCard(tmp, Clock(start=1_700_000_000))
    h0 = dict(card.h)
    print(f"  初始：{H.describe(h0)}")
    card.remember("我好喜欢你啊，谢谢你陪着我", salience=0.8)
    h1 = dict(card.h)
    ok = (h1["h_oxy"] > h0["h_oxy"] and h1["h_dopa"] > h0["h_dopa"])
    print(("  ✅" if ok else "  ❌") + f" 一句亲密话后：{H.describe(h1)}")
    ok_all &= ok

    card.remember("你今天真的很烦，闭嘴", salience=0.8)
    h2 = dict(card.h)
    ok = h2["h_cort"] > h1["h_cort"]
    print(("  ✅" if ok else "  ❌") + f" 一句狠话后：{H.describe(h2)}")
    ok_all &= ok

    # ---- 11) tick 后回归 ----
    card.tick(2 * DAY)
    h3 = dict(card.h)
    ok = h3["h_cort"] < h2["h_cort"] or h3["h_cort"] < 0.35
    print(("  ✅" if ok else "  ❌") +
          f" 两天后皮质醇回落：{h2['h_cort']:.3f} → {h3['h_cort']:.3f}")
    ok_all &= ok

    # ---- 12) 落盘 + 重开 ----
    saved = dict(card.h)
    card.close()
    card2b = MemoryCard(tmp, Clock(start=1_700_000_000 + 3 * DAY))
    ok = all(abs(card2b.h[k] - saved[k]) < 1e-6 for k in H.H_KEYS)
    print(("  ✅" if ok else "  ❌") +
          f" 重开库激素保留：{H.describe(card2b.h)}")
    ok_all &= ok

    # ---- 13) 上下文里能看到状态 ----
    #  接近基线时不显示状态句是对的（没状态可报），所以先把状态推高再看。
    for _ in range(6):
        card2b.remember("我真的很喜欢你，谢谢你一直陪着我", salience=0.9)
    ctx = card2b.assemble_context("你还记得我吗", identity="你是小鲸鱼。")
    has = "（" in ctx["text"]
    print(("  ✅" if has else "  ❌") + " 人格格里带上了状态句")
    ok_all &= has
    import re as _re
    m = _re.search(r"（[^）]*）", ctx["text"])
    if m:
        print("    → " + m.group(0))
    # 基线状态下不该硬凑一句
    card3 = MemoryCard(tempfile.mktemp(suffix=".db"),
                       Clock(start=1_700_000_000))
    ctx3 = card3.assemble_context("你好", identity="你是小鲸鱼。")
    quiet = ("（" not in ctx3["text"])
    print(("  ✅" if quiet else "  ❌") + " 基线状态不乱加状态句（不瞎演）")
    ok_all &= quiet
    card3.close()

    card2b.close()
    try:
        os.remove(tmp)
    except OSError:
        pass

    print()
    print("=" * 50)
    print("T3-2 验收：" + ("全部通过 ✅" if ok_all else "有失败项 ❌"))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
