# -*- coding: utf-8 -*-
"""t110：动机层（驱力池 → 欲望候选 → 意图仲裁）。

对应 Untitled(3)(1).txt 的 motiv_engine，但砍到「内在天气」这一层
（用户 m35010 授权：可以主动发消息、可以主动学、可以主动升级）。

验收点：
  1. 驱力池有七条，基线不为 0；脏值/缺项按基线补，绝不抛
  2. 静息（全基线）不生任何欲望 —— 「没有动机」是正常状态
  3. 驱力涨上去会生出对应候选，并带 why（能解释）
  4. 仲裁硬否决：上课/安静时段不能主动说话；刚说过话不行；额度用完不行
  5. 学习不能在聊天中途抽身（idle 门槛）
  6. 风险 ≥0.7 一律不自动做
  7. 做完要消耗驱力、占额度；额度只留 14 天
  8. 聊过之后「想说话的劲儿」会泄，但**不会抬高任何驱力**（不是好感度）
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import motiv  # noqa: E402

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


NOW = 1_800_000_000.0

print("==============================================================")
print("  t110  动机层：驱力池 / 欲望候选 / 仲裁否决")
print("==============================================================")

print("\n== 一、驱力池 ==")
b = motiv.base()
chk("七条驱力", len(b) == 7, str(sorted(b)))
chk("基线都不是 0（全 0 等于没有动机）", all(v > 0 for v in b.values()))
chk("脏值按基线补", motiv.normalize({"curiosity": "abc"})["curiosity"] == b["curiosity"])
chk("缺项按基线补", "care" in motiv.normalize({}))
chk("NaN 不炸", motiv.normalize({"play": float("nan")})["play"] == b["play"])
chk("超范围被夹住", motiv.normalize({"play": 99})["play"] <= 1.5)

print("\n== 二、静息不生欲望 ==")
ctx_idle = {"now": NOW, "last_event_ts": NOW - 3600 * 3}
chk("全基线时没有候选（没有动机是正常的）",
    motiv.candidates(b, ctx_idle) == [])
_ch, _rj = motiv.arbitrate([], ctx_idle)
chk("仲裁也返回 None", _ch is None)

print("\n== 三、涨上去了才想做事 ==")
d = motiv.itch(b, "curiosity", 0.35)
cands = motiv.candidates(d, ctx_idle)
kinds = [x["kind"] for x in cands]
chk("好奇心涨 → 有 learn 候选", "learn" in kinds, str(kinds))
_learn = [x for x in cands if x["kind"] == "learn"][0]
chk("候选带 why（能解释为什么想做）", bool(_learn.get("why")))
chk("候选带 urgency 且 0~1", 0 <= _learn["urgency"] <= 1)

d2 = motiv.itch(b, "attachment", 0.4)
c2 = motiv.candidates(d2, ctx_idle)
chk("依恋涨 → 有 reach_out 候选", "reach_out" in [x["kind"] for x in c2])
chk("reach_out 风险 0.45（不到封锁线）",
    [x for x in c2 if x["kind"] == "reach_out"][0]["risk"] < motiv.RISK_BLOCK)

print("\n== 四、仲裁的硬否决 ==")
ctx_ok = {"now": NOW, "last_event_ts": NOW - 3600 * 3,
          "last_sent_ts": NOW - 3600 * 5}
_ch, _rj = motiv.arbitrate(c2, ctx_ok)
chk("正常情况下能选中 reach_out", _ch and _ch["kind"] in ("reach_out", "care_check"),
    str(_ch))

_ch, _rj = motiv.arbitrate(c2, dict(ctx_ok, at_school=True))
chk("上课时段：不打扰", _ch is None or _ch["kind"] not in ("reach_out", "care_check"))
chk("被拒理由说人话", any("上课" in str(r.get("reject")) for r in _rj))

_ch, _rj = motiv.arbitrate(c2, dict(ctx_ok, quiet=True))
chk("安静时段：不打扰", _ch is None or _ch["kind"] not in ("reach_out", "care_check"))
chk("安静理由在", any("安静" in str(r.get("reject")) for r in _rj))

_ch, _rj = motiv.arbitrate(c2, dict(ctx_ok, last_sent_ts=NOW - 60,
                                    last_event_ts=NOW - 60))
chk("刚说过话：还没到间隔", _ch is None or _ch["kind"] not in
    ("reach_out", "care_check"))

_ch, _rj = motiv.arbitrate(c2, dict(ctx_ok, sent_today=99))
chk("额度用完：不开口", _ch is None or _ch["kind"] not in
    ("reach_out", "care_check"))

print("\n== 五、学习不能中途抽身 ==")
c3 = motiv.candidates(d, {"now": NOW, "last_event_ts": NOW - 30})
_ch, _rj = motiv.arbitrate(c3, {"now": NOW, "last_event_ts": NOW - 30})
chk("刚聊完 30 秒：不去学", _ch is None or _ch["kind"] != "learn")
chk("理由写了「正在聊天」",
    any("聊天" in str(r.get("reject")) for r in _rj))

print("\n== 六、风险刹车 ==")
risky = [{"kind": "reach_out", "risk": 0.9, "urgency": 1.0, "why": "x"}]
_ch, _rj = motiv.arbitrate(risky, ctx_ok)
chk("风险 ≥0.7 一律不自动做", _ch is None)
chk("理由写了风险", any("风险" in str(r.get("reject")) for r in _rj))

print("\n== 七、settle 会回落 ==")
hot = motiv.itch(b, "attachment", 0.5)
after_1h = motiv.settle(hot, 1.0)
after_24h = motiv.settle(hot, 24.0)
chk("一小时后回落一点", after_1h["attachment"] < hot["attachment"])
chk("一天后基本回到基线附近",
    abs(after_24h["attachment"] - b["attachment"]) < 0.05,
    "%.3f" % after_24h["attachment"])
chk("回落不会穿过基线（不会越歇越想说话）",
    after_24h["attachment"] >= b["attachment"] - 0.001)

print("\n== 八、bump 是满足不是讨好 ==")
d3 = motiv.bump(b, "attachment", 0.2)
chk("聊过之后依恋下降", d3["attachment"] < b["attachment"])
chk("其他驱力一个都没被抬高",
    all(d3[k] <= b[k] + 1e-9 for k in b))

print("\n==============================================================")
print("  过 %d / 挂 %d" % (passed, failed))
print("==============================================================")
sys.exit(1 if failed else 0)
