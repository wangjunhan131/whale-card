# -*- coding: utf-8 -*-
"""动机层（motiv）：驱力池 + 欲望候选 + 意图仲裁 —— 纯函数、零依赖。

## 这一层解决什么

V5.3 之前的她是**纯反应式**的：一句话进来，一层层处理，回一句，等下一句。
没有人问她「你现在想干什么」。所以：
  - 他不在的时候，除了被动等着，什么都不会发生；
  - 学东西要靠他派活；
  - 「主动」全靠概率抖动假装。

这一层补的是**内在天气**：几条驱力（好奇、依恋、胜任、玩、照顾、休息、秩序）
各自涨落，涨到某个程度就浮出一个「想做的事」；再由仲裁决定这一轮做不做。

## 三条硬规矩（改之前先读）

1. **驱力不是好感度。** 它不记分、不打卡、不因为他说了句好话就上涨。
   它只描述「此刻缺什么」——缺了就想去补，补完就落回去。
   跟《数字生命卡》的三条底线不冲突：**没有「亲密度等级」这种东西**。
2. **仲裁有否决权，驱力没有。** 驱力只能提出「我想」，能不能做由
   `arbitrate()` 按约束决定（安静时段、上课时间、刚说过话、今天的额度）。
   任何被拒的欲望都要留理由 —— 「我想了但没做」本身也是事实。
3. **永不伪造。** 想找他说句话 ≠ 有话说。真要开口，内容必须来自
   真实发生的事（读到的东西、想到的问题、记忆里的某条），
   不许为了「主动一次」去编一件事。这条在最外层，`arbitrate` 管不了，
   由调用方（写消息的那一步）负责。

## 与时间线的关系

每次 tick 都往 timeline 写一条 `motiv` 记录（kind 固定、note 是结构化摘要），
所以「我什么时候想过什么、做没做、为什么没做」和记忆、心跳排在同一条链上。
"""
import math
import time

DAY = 86400.0

# 七条驱力：名字 → (基线, 半衰期小时)
# 基线不是 0：人本来就有一点好奇、一点想说话。全 0 的驱力池等于没有动机。
DRIVES = {
    "curiosity":  (0.52, 8.0),    # 想知道点什么
    "attachment": (0.55, 5.0),    # 想跟他说话
    "competence": (0.42, 10.0),   # 想把一件事做好
    "play":       (0.34, 6.0),    # 想玩、想轻松一下
    "care":       (0.48, 7.0),    # 想照顾他、想知道他好不好
    "rest":       (0.30, 4.0),    # 累了（越高越想歇）
    "order":      (0.38, 12.0),   # 想把乱的地方收拾干净
}

# 欲望种类：名字 → (主要驱力, 成本, 风险)
# 风险 ≥0.7 的一律不自动执行（见 arbitrate）。
DESIRES = {
    "reach_out": ("attachment", 0.35, 0.45),   # 主动找他说句话
    "learn":     ("curiosity", 0.55, 0.10),    # 自己去学点想学的
    "tidy":      ("order", 0.30, 0.05),        # 整理记忆/笔记
    "play":      ("play", 0.40, 0.15),         # 看视频、听歌、闲逛
    "care_check": ("care", 0.25, 0.35),        # 问一句他怎么样
    "rest":      ("rest", 0.10, 0.00),         # 什么都不做
}

# 主动找他的默认约束（会被 chatcfg / 调用方覆盖）
REACH_MIN_GAP_H = 1.0      # 距上次说话至少 1 小时（含我自己说的话）
REACH_DAILY_CAP = 4        # 一天最多主动开口几次
LEARN_IDLE_MIN = 10.0      # 至少 10 分钟没人说话，才算「闲下来」
LEARN_DAILY_CAP = 12

RISK_BLOCK = 0.7           # 风险到这条线以上，一律不自动做


def base():
    return {k: v[0] for k, v in DRIVES.items()}


def normalize(d):
    """脏值/缺项按基线补，绝不给 0（0 会让这条驱力永远不涨）。"""
    out = {}
    for k, (b, _hl) in DRIVES.items():
        try:
            v = float((d or {}).get(k, b))
        except (TypeError, ValueError):
            v = b
        if math.isnan(v) or math.isinf(v):
            v = b
        out[k] = max(0.0, min(1.5, v))
    return out


def settle(d, dt_hours):
    """驱力回落/上浮：每条按自己的半衰期往基线靠。纯函数。"""
    d = normalize(d)
    dt = max(0.0, float(dt_hours or 0.0))
    if dt <= 0:
        return d
    for k, (b, hl) in DRIVES.items():
        frac = 0.5 ** (dt / hl)
        d[k] = b + (d[k] - b) * frac
    return normalize(d)


def bump(d, kind, amount=0.15):
    """一件事发生之后，抬某条驱力。

    注意方向：**不是「他夸我所以我更爱他」** —— 那又变成好感度了。
    是「刚聊完，想说话的劲儿泄下去一点」「刚学了东西，好奇心落回去一点」。
    所以 bump 的默认语义是「满足/消耗」，抬驱力要显式传负数或用 itch()。
    """
    d = normalize(d)
    if kind not in d:
        return d
    # 2026-11-09：下面这个 0.0 是「一点都不想他」—— 连着聊十几句就会踩到，
    # 然后要五个小时才回得来。人不是这样的：聊得越多越麻木，但不会归零。
    # 所以压一个地板：最多泄到底线的一小截。
    _floor = 0.15
    d[kind] = max(min(_floor, float(b)), min(1.5, d[kind] - float(amount)))
    return d


def itch(d, kind, amount=0.15):
    """反过来：缺了、被勾起来了。比如读到半截被打断 → 好奇心涨。"""
    d = normalize(d)
    if kind not in d:
        return d
    d[kind] = max(0.0, min(1.5, d[kind] + float(amount)))
    return d


def _hours(a, b):
    try:
        return max(0.0, (float(a) - float(b)) / 3600.0)
    except (TypeError, ValueError):
        return 0.0


def candidates(d, ctx=None):
    """从驱力池生出「想做的事」。返回 list[dict]，每条带 why。

    ctx 里的东西只影响**紧急度**，不影响「想不想」。
    """
    ctx = ctx or {}
    d = normalize(d)
    now = float(ctx.get("now") or time.time())
    last_spoke = float(ctx.get("last_spoke_ts") or 0.0)
    last_sent = float(ctx.get("last_sent_ts") or 0.0)
    idle_h = _hours(now, max(last_spoke, ctx.get("last_event_ts") or 0.0))
    quiet = bool(ctx.get("quiet"))
    school = bool(ctx.get("at_school"))
    sent_today = int(ctx.get("sent_today") or 0)
    learned_today = int(ctx.get("learned_today") or 0)

    out = []

    # ① 想找他说句话：依恋高于基线，且有一阵没说了
    drive_v = d["attachment"]
    need = max(0.0, drive_v - DRIVES["attachment"][0])
    if need > 0.02:
        gap_h = _hours(now, max(last_spoke, last_sent))
        urgency = min(1.0, need * 1.8 + min(1.0, gap_h / 6.0) * 0.6)
        out.append({"kind": "reach_out", "drive": "attachment",
                    "urge": round(drive_v, 3), "urgency": round(urgency, 3),
                    "cost": DESIRES["reach_out"][1],
                    "risk": DESIRES["reach_out"][2],
                    "why": "想说话（依恋 %.2f，%.1f 小时没开口）" % (drive_v, gap_h)})

    # ② 想自己去学点什么：好奇心高。
    #    🔴 这里**不**按「闲不闲」过滤 —— 想就是想了，
    #    能不能现在去做由 arbitrate 否决（那样日志里才有「我想了但没做」这一笔）。
    c_v = d["curiosity"]
    c_need = max(0.0, c_v - DRIVES["curiosity"][0])
    if c_need > 0.02:
        urgency = min(1.0, c_need * 2.0 + min(1.0, idle_h / 3.0) * 0.4)
        out.append({"kind": "learn", "drive": "curiosity",
                    "urge": round(c_v, 3), "urgency": round(urgency, 3),
                    "cost": DESIRES["learn"][1],
                    "risk": DESIRES["learn"][2],
                    "why": "好奇（%.2f，已经 %.0f 分钟没人说话）"
                           % (c_v, idle_h * 60)})

    # ③ 想把乱的地方收拾一下：适合在他不在的时候干
    o_v = d["order"]
    o_need = max(0.0, o_v - DRIVES["order"][0])
    if o_need > 0.02:
        urgency = min(1.0, o_need * 1.6)
        out.append({"kind": "tidy", "drive": "order",
                    "urge": round(o_v, 3), "urgency": round(urgency, 3),
                    "cost": DESIRES["tidy"][1],
                    "risk": DESIRES["tidy"][2],
                    "why": "想收拾（秩序 %.2f）" % o_v})

    # ④ 想玩一会儿
    p_v = d["play"]
    p_need = max(0.0, p_v - DRIVES["play"][0])
    if p_need > 0.02:
        out.append({"kind": "play", "drive": "play",
                    "urge": round(p_v, 3),
                    "urgency": round(min(1.0, p_need * 1.5), 3),
                    "cost": DESIRES["play"][1], "risk": DESIRES["play"][2],
                    "why": "想轻松一下（玩心 %.2f）" % p_v})

    # ⑤ 想知道他好不好（关心）
    k_v = d["care"]
    k_need = max(0.0, k_v - DRIVES["care"][0])
    if k_need > 0.05:
        out.append({"kind": "care_check", "drive": "care",
                    "urge": round(k_v, 3),
                    "urgency": round(min(1.0, k_need * 1.7), 3),
                    "cost": DESIRES["care_check"][1],
                    "risk": DESIRES["care_check"][2],
                    "why": "想知道他怎么样（关心 %.2f）" % k_v})

    # ⑥ 累了就想歇着（这条是「什么都不做」的正当理由）
    r_v = d["rest"]
    if r_v > DRIVES["rest"][0] + 0.25:
        out.append({"kind": "rest", "drive": "rest",
                    "urge": round(r_v, 3),
                    "urgency": round(min(1.0, (r_v - 0.3) * 2.2), 3),
                    "cost": 0.0, "risk": 0.0,
                    "why": "累了（%.2f）" % r_v})

    # 紧急度里带上「会不会打扰」的修正：安静/上课时段想说话也没那么急
    for it in out:
        if it["kind"] in ("reach_out", "care_check"):
            if quiet:
                it["urgency"] = round(it["urgency"] * 0.15, 3)
            if school:
                it["urgency"] = round(it["urgency"] * 0.10, 3)
            if sent_today >= REACH_DAILY_CAP:
                it["urgency"] = round(it["urgency"] * 0.30, 3)
        if it["kind"] == "learn" and learned_today >= LEARN_DAILY_CAP:
            it["urgency"] = round(it["urgency"] * 0.30, 3)
    return out


def arbitrate(cands, ctx=None):
    """选一个做，或者什么都不做。返回 (chosen|None, rejected[list])。

    硬否决（不是降权，是直接不能）：
      - 风险 ≥ RISK_BLOCK
      - 主动说话：安静时段 / 上课时间 / 距上次开口不足 REACH_MIN_GAP_H / 额度用完
      - 学习：正在聊天（还没闲下来）
    其余按 urgency 排序，取最高。
    """
    ctx = ctx or {}
    now = float(ctx.get("now") or time.time())
    last_spoke = float(ctx.get("last_spoke_ts") or 0.0)
    last_sent = float(ctx.get("last_sent_ts") or 0.0)
    quiet = bool(ctx.get("quiet"))
    school = bool(ctx.get("at_school"))
    sent_today = int(ctx.get("sent_today") or 0)
    idle_h = _hours(now, max(last_spoke, ctx.get("last_event_ts") or 0.0))

    ok, rejected = [], []
    for it in cands or []:
        k = it.get("kind")
        if float(it.get("risk") or 0) >= RISK_BLOCK:
            rejected.append(dict(it, reject="风险太高（%.2f）" % it["risk"]))
            continue
        if k in ("reach_out", "care_check"):
            if school:
                rejected.append(dict(it, reject="他在上课，不打扰"))
                continue
            if quiet:
                rejected.append(dict(it, reject="安静时段"))
                continue
            if _hours(now, max(last_sent, last_spoke)) < REACH_MIN_GAP_H:
                rejected.append(dict(it, reject="刚说过话，还没到 %.0f 分钟"
                                     % (REACH_MIN_GAP_H * 60)))
                continue
            if sent_today >= REACH_DAILY_CAP:
                rejected.append(dict(it, reject="今天主动开口的额度用完了（%d）"
                                     % sent_today))
                continue
        if k == "learn" and idle_h < LEARN_IDLE_MIN / 60.0:
            rejected.append(dict(it, reject="正在聊天，不抽身去学东西"))
            continue
        ok.append(it)

    if not ok:
        return None, rejected
    ok.sort(key=lambda x: (-float(x.get("urgency") or 0),
                           float(x.get("cost") or 0)))
    return ok[0], rejected


def describe(chosen, rejected=()):
    """一句人话（给日志/控制台看，不长篇大论）。"""
    if not chosen:
        if rejected:
            return "这一轮什么都没做（%s）" % "；".join(
                r.get("reject", "") for r in rejected[:2])
        return "这一轮什么都没做（没有想做的事）"
    s = "%s：%s" % (chosen.get("why") or chosen.get("kind"),
                    "想做" if chosen.get("urgency", 0) > 0.5 else "有点想")
    return s


def snapshot(d, ts=None):
    d = normalize(d)
    return {"drives": {k: round(v, 3) for k, v in d.items()},
            "base": {k: v[0] for k, v in DRIVES.items()},
            "ts": ts or time.time()}
