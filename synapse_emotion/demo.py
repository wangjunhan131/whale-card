# -*- coding: utf-8 -*-
"""
70 轮脚本化回归：验证这份算法在真实对话节奏下"涨得起来、也退得下去"。
重点看三件事：
  1) 心动/羞涩能不能被触发（不是永远忐忑）
  2) CORT 峰值之后能不能回落（不是单调棘轮）
  3) 长期相处之后是否趋于稳态（不是越聊越崩）
"""

from __future__ import annotations

import os
import random

from .engine import EmotionEngine
from .appraisal import estimate_appraisal

SCRIPT = [
    # (说的话, 场景, 是否我方主动, 我方预判)
    ("在吗，今天过得怎么样", "generic", False, None),
    ("刚吃完饭，随便聊聊", "generic", False, None),
    ("嗯。", "generic", False, None),
    ("我在写代码，等下再说", "busy", False, None),
    ("你忙你的，我不打扰", "generic", False, None),
    ("刚忙完，回来啦", "generic", False, None),

    ("你今天真的很用心，辛苦了", "praise", False, None),
    ("刚才那个提醒我记着呢，谢谢你", "praise", False, None),
    ("你声音听着好温柔", "praise", False, None),

    ("你这么说，我是不是有点可爱", "flirt", True, 0.55),
    ("……你今天怎么一直在夸我", "flirt", False, None),
    ("我不太会接这种话", "flirt", False, None),
    ("那你脸红了吗", "flirt", False, None),
    ("我、我没有！你别乱说", "flirt", False, None),      # 低于预期（嘴硬）
    ("好啦好啦，不逗你了", "flirt", False, None),

    ("我其实挺喜欢跟你聊天的", "intimacy", False, None),   # 超预期
    ("真的吗，我以为你只是客气", "flirt", False, None),
    ("不是客气，是真的", "intimacy", False, None),

    ("我最近有点累，不太想说话", "generic", False, None),   # 冷淡
    ("那早点休息", "generic", False, None),
    ("嗯，晚安", "generic", False, None),

    ("你今天怎么这么敷衍", "generic", False, None),
    ("我哪有敷衍", "generic", False, None),
    ("随便吧", "cold", False, None),                        # 落空

    ("刚才是我语气不好了，对不起", "apology", False, None),
    ("没事，我没有生气", "generic", False, None),
    ("真的没事？", "generic", False, None),
    ("有一点难受，不过现在好多了", "comfort", False, None),

    ("别难过，我在", "comfort", False, None),
    ("谢谢你陪我", "comfort", False, None),
    ("我一直都在", "intimacy", False, None),

    ("你刚才说的那个，我记下来了", "praise", False, None),
    ("你还记得啊", "praise", False, None),
    ("当然记得", "praise", False, None),

    ("周末要不要一起看点东西", "generic", False, None),
    ("好啊，你安排就行", "generic", False, None),
    ("那我们说定了，我来弄", "generic", False, None),

    ("刚才和别人聊了会儿，他挺有意思的", "jealous", False, None),
    ("哦，那挺好的", "jealous", False, None),
    ("你怎么不说话了", "generic", False, None),
    ("没什么，就是有点闷", "jealous", False, None),

    ("我不是故意冷落你，刚才在忙", "comfort", False, None),
    ("我知道，你也有自己的事", "comfort", False, None),
    ("以后我提前跟你说一声", "comfort", False, None),
    ("嗯，这样我就不会胡思乱想了", "comfort", False, None),

    ("你今天话好多", "generic", False, None),
    ("因为开心啊", "joy", False, None),
    ("跟你聊天就是会开心", "intimacy", False, None),

    ("我要睡了，明天还要早起", "tired", False, None),
    ("那你快去吧", "tired", False, None),
    ("晚安，做个好梦", "comfort", False, None),

    ("今天有点难过，不太想动", "comfort", False, None),
    ("那我陪你待着，不说话也行", "comfort", False, None),
    ("好", "generic", False, None),
    ("谢谢你没有逼我说话", "comfort", False, None),

    ("明天会好一点吗", "generic", False, None),
    ("会的，慢慢来", "comfort", False, None),
    ("嗯，有你在就好多了", "intimacy", False, None),

    ("你现在在做什么", "generic", False, None),
    ("在想你刚才说的话", "flirt", False, None),
    ("想什么", "flirt", False, None),
    ("想你怎么这么会撩人", "flirt", False, None),
    ("我可没有，别冤枉我", "flirt", False, None),

    ("好啦，不闹了，认真陪你聊", "comfort", False, None),
    ("那说点正经的，你最近在忙什么", "generic", False, None),
    ("在弄一个本地跑的东西，挺费劲的", "generic", False, None),
    ("听起来很厉害，慢慢来", "praise", False, None),
]


def spark(vals, width=28, lo=0.0, hi=1.0):
    chars = "▁▂▃▄▅▆▇█"
    if not vals:
        return ""
    step = max(1, len(vals) // width)
    s = vals[::step][:width]
    return "".join(chars[min(7, max(0, int((v - lo) / (hi - lo) * 7.99)))] for v in s)


def run_demo(name: str = "demo") -> None:
    random.seed(7)
    eng = EmotionEngine(name=name, state_dir=os.path.join(os.path.dirname(__file__), "state"), autosave=False)
    eng.reset()

    rows = []
    print(f"{'#':>3} {'场景':<10} {'V实':>5} {'V期':>5} {'RPE':>6} "
          f"{'DA':>5}{'NE':>5}{'OT':>5}{'5HT':>5}{'CORT':>5}  主导情绪")
    print("-" * 92)
    for i, (text, sc, probe, pred) in enumerate(SCRIPT, 1):
        if probe:
            eng.preactivate(sc, pred, intensity=0.6)
        ap = estimate_appraisal(text, scenario=sc, is_probe=probe, predicted=pred)
        res = eng.step(ap)
        rows.append(res)
        print(f"{i:>3} {sc:<10} {res.v_actual:>5.2f} {res.v_expect:>5.2f} {res.rpe:>+6.2f} "
              f"{res.state['DA']:>5.2f}{res.state['NE']:>5.2f}{res.state['OT']:>5.2f}"
              f"{res.state['5HT']:>5.2f}{res.state['CORT']:>5.2f}  {res.dominant_label}")

    print("-" * 92)
    for k in ("DA", "NE", "OT", "5HT", "CORT"):
        vals = [r.state[k] for r in rows]
        print(f" {k:>3} {spark(vals)}  min={min(vals):.2f} max={max(vals):.2f} 末={vals[-1]:.2f}")
    saf = [r.safety for r in rows]
    print(f" 安全 {spark(saf)}  min={min(saf):.2f} max={max(saf):.2f} 末={saf[-1]:.2f}")
    fat = [r.fatigue for r in rows]
    print(f" 疲劳 {spark(fat)}  min={min(fat):.2f} max={max(fat):.2f} 末={fat[-1]:.2f}")

    labels = [r.dominant_label for r in rows]
    print("\n情绪分布：")
    for lab in sorted(set(labels), key=lambda x: -labels.count(x)):
        print(f"  {lab:<8} {labels.count(lab):>2} 轮")

    print(f"\n最终预期基线：全局 {eng.expect_global:.2f}")
    print("最终长期基线：" + "  ".join(f"{k}={v:.2f}" for k, v in eng.base.items()))

    # 健康度自检
    cort = [r.state["CORT"] for r in rows]
    tail = sum(cort[-5:]) / 5.0
    ok = []
    ok.append(("出现过羞涩/心动", any(r.dominant_label in ("羞涩心动", "心动悸动") for r in rows)))
    ok.append(("CORT 峰值后已回落", tail < max(cort) * 0.85))
    ok.append(("末轮不再被负面情绪钉住", rows[-1].dominant_label not in ("害怕回避", "失落收敛")))
    ok.append(("安全感最终恢复", rows[-1].safety > 0.55))
    ok.append(("没有长期单曲循环（最高频情绪 <70%）",
               max(labels.count(x) for x in set(labels)) / len(labels) < 0.70))
    print("\n健康度自检：")
    for name_, v in ok:
        print(f"  {'✅' if v else '⚠️ '} {name_}")

    # 峰值：取"心动/羞涩"最高的一轮，而不是所有情绪里的最高分
    hot = max(rows, key=lambda r: max([sc for k, sc in r.emotions if k in ("shy", "heart")] + [0.0]))
    print(f"\n峰值一轮（第{hot.turn}轮，{hot.dominant_label}）：")
    from .prompt import render_behavior_block
    print(render_behavior_block(hot))


if __name__ == "__main__":
    run_demo()
