# -*- coding: utf-8 -*-
"""t105：算法拟人化的三条验收。

1. human_gap 是对数正态（有长尾、中位数按句长分档、夹在 3~240 秒）
2. 两个「实测会掉分」的开关默认关（防止有人顺手打开）
3. docs/ALGORITHM-REVIEW.md 里的数字与代码一致（防文档漂移）
"""
import io
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)

import qq_guard as G  # noqa: E402
import card2  # noqa: E402

passed = 0
failed = 0


def chk(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print("  ✅ %s" % name)
    else:
        failed += 1
        print("  ❌ %s %s" % (name, extra))


print("==============================================================")
print("  t105  算法拟人化")
print("==============================================================")

print("\n== 一、human_gap 的形状 ==")
short = sorted(G.human_gap("嗯") for _ in range(600))
mid = sorted(G.human_gap("今天考试考得还行吧数学最后一道大题没做完") for _ in range(600))
long_ = sorted(G.human_gap("我觉得你那套方案其实有个问题，内存不够的时候会拖慢整个推理速度，"
                           "得先把上下文砍下来再说，不然显存根本装不下") for _ in range(600))

chk("短句中位在 10~20 秒", 10 <= statistics.median(short) <= 20,
    "%.1f" % statistics.median(short))
chk("中句中位在 22~40 秒", 22 <= statistics.median(mid) <= 40,
    "%.1f" % statistics.median(mid))
chk("长句中位在 45~70 秒", 45 <= statistics.median(long_) <= 70,
    "%.1f" % statistics.median(long_))
chk("句越长等得越久", statistics.median(short) < statistics.median(mid) < statistics.median(long_))

chk("全部落在 3~240 秒之间",
    all(3.0 <= x <= 240.0 for x in short + mid + long_),
    "min=%.1f max=%.1f" % (min(short + mid + long_), max(long_)))
chk("有长尾（p90/p50 > 1.6）",
    mid[540] / statistics.median(mid) > 1.6,
    "p90/p50=%.2f" % (mid[540] / statistics.median(mid)))
chk("不是均匀分布（p10 与 p50 的差明显小于 p50 与 p90 的差）",
    (statistics.median(mid) - mid[60]) < (mid[540] - statistics.median(mid)),
    "%.1f vs %.1f" % (statistics.median(mid) - mid[60],
                      mid[540] - statistics.median(mid)))
chk("返回一位小数的 float", isinstance(G.human_gap("嗯"), float))
chk("空串也不炸", 3.0 <= G.human_gap("") <= 240.0)

print("\n== 二、两个会掉分的开关默认关 ==")
chk("情绪加权默认关", card2._EMOTION_BOOST_ON is False)
chk("同日新近台阶默认关", card2._RECENCY_FRESH_STEP is False)
chk("情绪加权的系数还在（0.25）", abs(card2.EMOTION_RECALL_BOOST - 0.25) < 1e-9)
chk("远期时间常数还是 14 天", abs(card2.RECENCY_SLOW_DAYS - 14.0) < 1e-9)

print("\n== 三、文档与代码不漂 ==")
doc = io.open(os.path.join(ROOT, "docs", "ALGORITHM-REVIEW.md"),
              encoding="utf-8").read()
chk("文档提到 emotion 两档的证据", "869" in doc and "0.25" in doc)
chk("文档写了实测掉分的那张表", "37.5%" in doc and "EVID" in doc)
chk("文档里 human_gap 的参数与代码一致", "0.55" in doc and "14/30/55" in doc.replace("中位 ", ""))
chk("文档写了开关名", "WHALE_EMO_BOOST" in doc and "WHALE_FRESH_STEP" in doc)
chk("文档说了加固已饱和不用改", "已经饱和" in doc or "已经饱和" in doc or "这条不用改" in doc)
chk("文档指出群消息占七成", "250" in doc)

src = io.open(os.path.join(ROOT, "card2.py"), encoding="utf-8").read()
chk("代码里写明了为什么默认关", "默认关" in src and "R@1" in src)

print("\n==============================================================")
print("  过 %d / 挂 %d" % (passed, failed))
print("==============================================================")
sys.exit(1 if failed else 0)
