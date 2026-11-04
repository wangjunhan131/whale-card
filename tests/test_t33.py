# -*- coding: utf-8 -*-
"""T3-3 验收：六维动态上下文权重（V5.0 §10）

验收标准（施工总表）：
  普通对话格 4 拿 1500；切技能场景格 4 拿 3000（翻倍），格 3 从 1320 压到 600
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import card2
import ctxweight as W
from card2 import MemoryCard, Clock, DAY


def main():
    ok_all = True

    # ---- 1) 七张场景表：每张必须严格归一 ----
    for name, v in W.SCENE_WEIGHTS.items():
        s = sum(v)
        ok = abs(s - 1.0) < 1e-9
        print(("  ✅" if ok else "  ❌") +
              f" 场景「{name}」权重和 = {s:.6f}")
        ok_all &= ok

    # ---- 2) 普通对话：与 V5.0 §10.5 的示例逐格对表 ----
    r = W.compute("今天中午吃了什么", 6000)
    b = r["budget"]
    print(f"  普通对话 → 格1 {b['identity']} / 格2 {b['longterm']}"
          f" / 格3 {b['recent']} / 格4 {b['recall']}")
    ok = (b["identity"] == 2280 and b["longterm"] == 900
          and b["recent"] == 1320 and b["recall"] == 1500)
    print(("  ✅" if ok else "  ❌") + " 对表 §10.5 示例：2280/900/1320/1500")
    ok_all &= ok

    # ---- 3) 技能执行：格 4 翻倍到 3000，格 3 压到 600 ----
    r2 = W.compute("帮我把这个脚本改一下，报错了", 6000)
    b2 = r2["budget"]
    print(f"  技能执行 → 格1 {b2['identity']} / 格2 {b2['longterm']}"
          f" / 格3 {b2['recent']} / 格4 {b2['recall']}")
    ok = (b2["recall"] == 3000 and b2["recent"] == 600)
    print(("  ✅" if ok else "  ❌") +
          " 对表 §10.5 示例：格4=3000（翻倍）、格3=600")
    ok_all &= ok
    ok = b2["recall"] == 2 * b["recall"]
    print(("  ✅" if ok else "  ❌") +
          f" 技能格相对普通对话翻倍：{b['recall']} → {b2['recall']}")
    ok_all &= ok

    # ---- 4) 场景判定 ----
    cases = [
        ("我今天很难受，想哭", "情绪陪伴"),
        ("帮我看一下这个报错怎么修", "技能执行"),
        ("我的QQ号是多少", "事实查询"),
        ("你还记得那次烧烤吗", "深度回忆"),
        ("你根本不懂我，闭嘴", "冲突争执"),
        ("今天天气不错", "普通对话"),
    ]
    for q, want in cases:
        got = W.classify_scene(q)
        ok = got == want
        print(("  ✅" if ok else "  ❌") + f" 「{q}」→ {got}（期望 {want}）")
        ok_all &= ok

    # ---- 5) 归一：任何修饰后都严格归一 ----
    rng = [
        {"h_oxy": 0.9, "h_cort": 0.9, "h_dopa": 0.9, "h_sero": 0.9},
        {"h_oxy": 0.1, "h_cort": 0.1, "h_dopa": 0.1, "h_sero": 0.1},
    ]
    for h in rng:
        v = W.normalize(W.apply_hormones(W.base_weights("普通对话"), h))
        ok = abs(sum(v) - 1.0) < 1e-9 and v[W.A] >= W.A_MIN - 1e-9
        print(("  ✅" if ok else "  ❌") +
              f" 极端激素后仍归一：和={sum(v):.6f} A={v[W.A]:.3f}")
        ok_all &= ok

    # ---- 6) A 保底：就算所有修饰都压 A，也不低于 0.15 ----
    v = W.base_weights("技能执行")
    v[W.A] = 0.001
    v = W.normalize(v)
    ok = v[W.A] >= W.A_MIN - 1e-9
    print(("  ✅" if ok else "  ❌") + f" A 保底生效：A={v[W.A]:.4f}")
    ok_all &= ok

    # ---- 7) 内分泌修饰方向 ----
    v0 = W.base_weights("普通对话")
    hi_oxy = W.apply_hormones(v0, {"h_oxy": 0.9})
    ok = hi_oxy[W.E] > v0[W.E]
    print(("  ✅" if ok else "  ❌") +
          f" 催产素高 → 印象格权重 {v0[W.E]:.3f} → {hi_oxy[W.E]:.3f}")
    ok_all &= ok
    hi_cort = W.apply_hormones(v0, {"h_cort": 0.9})
    ok = hi_cort[W.D] < v0[W.D]
    print(("  ✅" if ok else "  ❌") +
          f" 皮质醇高 → 陈述格权重 {v0[W.D]:.3f} → {hi_cort[W.D]:.3f}（压力大往事想不起来）")
    ok_all &= ok

    # ---- 8) 意图修饰 ----
    v1 = W.apply_intent(W.base_weights("普通对话"), "你还记得以前那次吗")
    ok = v1[W.D] > W.base_weights("普通对话")[W.D]
    print(("  ✅" if ok else "  ❌") + " 问往事 → D 权重上调")
    ok_all &= ok

    # ---- 9) 会话长度 / 空闲 ----
    v_short = W.apply_session(W.base_weights("普通对话"), 1)
    v_long = W.apply_session(W.base_weights("普通对话"), 30)
    ok = v_short[W.C] > v_long[W.C]
    print(("  ✅" if ok else "  ❌") +
          f" 刚开场 C={v_short[W.C]:.3f} > 聊久了 C={v_long[W.C]:.3f}")
    ok_all &= ok
    v_idle = W.apply_idle(W.base_weights("普通对话"), 25 * 3600)
    ok = v_idle[W.D] > W.base_weights("普通对话")[W.D]
    print(("  ✅" if ok else "  ❌") + " 空闲超 24h → D 权重上调")
    ok_all &= ok

    # ---- 10) 保底：任何场景每格都不低于 CELL_MIN ----
    for name in W.SCENE_WEIGHTS:
        b = W.allocate(6000, W.SCENE_WEIGHTS[name])
        ok = all(x >= W.CELL_MIN for x in b.values())
        print(("  ✅" if ok else "  ❌") +
              f" 「{name}」四格均 >= {W.CELL_MIN}："
              f"{b['identity']}/{b['longterm']}/{b['recent']}/{b['recall']}")
        ok_all &= ok

    # ---- 11) 总额恒等于预算 ----
    for budget in (6000, 3000, 12000):
        for name in W.SCENE_WEIGHTS:
            b = W.allocate(budget, W.SCENE_WEIGHTS[name])
            ok = sum(b.values()) == budget
            if not ok:
                print(f"  ❌ 「{name}」预算 {budget} 合计 {sum(b.values())}")
            ok_all &= ok
    print(("  ✅" if ok_all else "  ❌") +
          " 6000/3000/12000 三档预算下，七场景四格合计都精确等于预算")

    # ---- 12) 接进卡片：不同场景真的给出不同配额 ----
    tmp = tempfile.mktemp(suffix=".db")
    card = MemoryCard(tmp, Clock(start=1_700_000_000))
    card.remember("用户是高三学生，主号QQ号是100000000，昵称叫使用者",
                  salience=0.95)
    card.remember("用户喜欢吃白米饭", salience=0.8)

    c1 = card.assemble_context("今天过得怎么样", identity="你是小鲸鱼。")
    c2 = card.assemble_context("帮我把这个脚本改一下，报错了",
                               identity="你是小鲸鱼。")
    print(f"  普通 → 场景「{c1['scene']}」配额 {c1['quota']}")
    print(f"  技能 → 场景「{c2['scene']}」配额 {c2['quota']}")
    ok = (c2["quota"]["recall"] > c1["quota"]["recall"]
          and c2["quota"]["recent"] < c1["quota"]["recent"])
    print(("  ✅" if ok else "  ❌") +
          " 卡片里实测：技能场景格4变大、格3变小")
    ok_all &= ok

    # ---- 13) 总长度永不超预算 ----
    junk = ["x" * 200] * 60
    c3 = card.assemble_context("你还记得什么", identity="你是小鲸鱼。",
                               recent_lines=junk)
    ok = c3["est_tokens"] <= 6000
    print(("  ✅" if ok else "  ❌") +
          f" 塞 60 条超长垃圾后 est_tokens={c3['est_tokens']} <= 6000")
    ok_all &= ok

    # ---- 14) 情绪场景格 4 变大 ----
    c4 = card.assemble_context("我今天很难受，想哭", identity="你是小鲸鱼。")
    ok = c4["scene"] == "情绪陪伴" and c4["quota"]["recall"] > c1["quota"]["recall"]
    print(("  ✅" if ok else "  ❌") +
          f" 情绪场景「{c4['scene']}」格4 {c1['quota']['recall']}"
          f" → {c4['quota']['recall']}（更依赖印象）")
    ok_all &= ok

    # ---- 15) 冲突场景 A+B 占比最高 ----
    r5 = W.compute("你根本不懂我，闭嘴", 6000)
    ab = r5["weights"][W.A] + r5["weights"][W.B]
    r6 = W.compute("帮我把这个脚本改一下", 6000)
    ab2 = r6["weights"][W.A] + r6["weights"][W.B]
    ok = ab > ab2
    print(("  ✅" if ok else "  ❌") +
          f" 冲突时 A+B={ab:.3f} > 技能时 A+B={ab2:.3f}（吵架先保底线）")
    ok_all &= ok

    card.close()
    try:
        os.remove(tmp)
    except OSError:
        pass

    print()
    print("=" * 50)
    print("T3-3 验收：" + ("全部通过 ✅" if ok_all else "有失败项 ❌"))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
