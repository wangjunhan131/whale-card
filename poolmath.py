# -*- coding: utf-8 -*-
"""池子数学：一条记忆"现在还值多少分"（拆门面第 4 批）。

搬的时候把它的调参常量一起搬过来了——不搬就得在 card2 里来回对名字。
这些数（LAM_S1/LAM_S2/LAM_M/LAM_L 等）是衰减率，改一个就会改"什么会先被忘掉"，
所以整块放在一起，别散。

规矩同前三批：只放纯计算，输入行/输入数值，输出分数。
"""
import json
from cardcfg import DAY, POOL_SHORT, POOL_MEDIUM, POOL_LONG  # noqa: E402
import math
import time

from vmath import _clamp01, cos_sim, vec_norm  # 纯数学在 vmath.py（第 1 批）


# 池名：card2 那边是一行三元赋值，这里单独写（值一样）


_json = json

LAM_S1 = 1.80e-6     # 短期池 · 前 3 天（半衰期 ~4.5 天）

LAM_S2 = 1.50e-5     # 短期池 · 第 4~7 天（半衰期 ~13 小时，没被想起就掉得快）

LAM_M = 2.70e-7      # 中期池（半衰期 ~29.7 天）

LAM_L = 2.16e-8      # 长期池·长衰减档（半衰期 ~371 天）

S_SHORT_PHASE = 3 * DAY   # 短期池慢段的长度

R_FACT = "fact"          # 语义事实：QQ号、名字、学校、身份

R_EPISODE = "episode"    # 情景事件：某天发生的具体事

R_SELF = "self"          # 自图式：关于我自己的稳定认知

R_HABIT = "habit"        # 程序性习惯：作息、口头禅、怎么做事

R_PREF = "preference"    # 偏好：喜欢什么讨厌什么

R_FLASH = "flash"        # 闪光灯：高唤醒、不合并、衰减最慢

REGION_DECAY_SCALE = {
    R_FLASH: 0.30,
    R_FACT: 0.50,
    R_SELF: 0.60,
    R_HABIT: 0.70,
    R_PREF: 0.80,
    R_EPISODE: 1.00,
}

R_CHATTER = "chatter"

LAM_CHATTER = math.log(2.0) / (12 * 3600.0)   # 半衰期 12 小时

CHATTER_PERSON_SCALE = {
    "":        1.0,    # 无标签（陌生群友）      半衰期 8.8 小时
    "known":   0.6,    # 群里见过             5.3 小时
    "friend":  2.2,    # 常聊的朋友            19 小时
    "close":   6.0,    # 亲近的人              2.2 天
    "core":   24.0,    # 我的人                8.8 天
}

def chatter_lambda(person_layer=""):
    """闲聊的衰减率。关系越近，λ 越小（活得越久）。"""
    sc = CHATTER_PERSON_SCALE.get((person_layer or "").strip(), 1.0)
    if sc <= 0:
        sc = 1.0
    return LAM_CHATTER / sc

def region_decay_scale(region):
    return REGION_DECAY_SCALE.get(region, 1.0)

def lam_for(pool, age):
    """当前该用哪个衰减率"""
    if pool == POOL_SHORT:
        return LAM_S1 if age <= S_SHORT_PHASE else LAM_S2
    if pool == POOL_MEDIUM:
        return LAM_M
    return LAM_L

R_STUDY = "study"       # 学到的知识、笔记、教程

R_PLAY = "play"         # 娱乐：视频、游戏、梗

R_WORK = "work"         # 工作：项目、工程、修 bug

REGION_HALF_LIFE = {
    R_CHATTER: 12 * 3600.0,   # 闲聊：一天之后剩 25%
    R_PLAY: 3 * DAY,          # 娱乐：三天之后剩一半
    R_STUDY: 7 * DAY,         # 学习：一周之后剩一半
    R_WORK: 15 * DAY,         # 工作：半个月之后剩一半
}

REGION_OWN_LAMBDA = {k: math.log(2.0) / v for k, v in REGION_HALF_LIFE.items()}

_LOAD_F = {}          # region -> 拥挤系数（由 MemoryCard 维护）

def relation_scale(person_layer):
    """越亲近的人，这条记忆活得越久（倍率直接除 λ）。

    注意：这不是好感度打分。分数会变、会诱导讨好；这里只是把**已经存在的
    关系层**接到遗忘速度上，跟闲聊区原来那套是同一个东西，只是推广到全区。
    """
    sc = CHATTER_PERSON_SCALE.get((person_layer or "").strip(), 1.0)
    return sc if sc > 0 else 1.0

def load_factor(region):
    """今天这个区挤不挤。没有数据就是 1.0（不调节）。"""
    try:
        return float(_LOAD_F.get(region, 1.0))
    except Exception:
        return 1.0

def effective_lambda(region, pool, person_layer=""):
    """这个区在这个池子里的有效 λ。没有自己的 λ 就返回 None（走老曲线）。"""
    lam = REGION_OWN_LAMBDA.get(region)
    if lam is None:
        return None
    return lam * stage_factor(pool) * load_factor(region) \
        / relation_scale(person_layer)

def wn_row(r, now):
    """按行算当前权重。person_layer 从 about_layer 列读（没有就当空）。"""
    """按行算权重：core_* 三档不衰减，其余照旧。
    单独包一层是因为调用点多，改签名容易漏。"""
    try:
        tier = r["tier"]
    except Exception:
        tier = None
    try:
        _lay = r["about_layer"] or ""
    except Exception:
        _lay = ""
    return weight_now(r["w"], r["pool"], r["t_anchor"], now, r["region"], tier,
                      person_layer=_lay)

def pool_of_k(k):
    """老数据兼容：用 k 反推它属于哪个池"""
    if k is None:
        return POOL_SHORT
    if k >= LAM_S1 * 0.5:
        return POOL_SHORT
    if k >= LAM_M * 0.5:
        return POOL_MEDIUM
    return POOL_LONG

def _safe_json(txt):
    """存进去的是 JSON 文本；读坏了就当没有，别让一条脏行毁掉整张图。"""
    if not txt:
        return {}
    try:
        v = _json.loads(txt)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def weight_now(w0, pool, t_anchor, now, region=None, tier=None,
                person_layer=""):
    """从锚点算到现在。分段双速率用积分，不能简单相乘。

    V4.1：分区衰减倍率直接乘在时间上 —— 等价于「这条记忆活得更慢」。
    flash 区 0.30 倍时间 ⇒ 半衰期变成 3.3 倍长。

    2026-11-01（五层记忆）：core_* 三档不衰减，直接被时间原样返回。
    它们的"变"走的是 supersede（新事实替换旧正文），不是 decay。
    """
    if tier in CORE_TIERS:
        return _clamp01(w0)
    # 🔧 2026-11-05（完备性体检）：两个必须兜的边角。
    #    a) w 本身越界（脏数据 / 别处的 bug 写进来）：以前原样透传 ——
    #       1.5 会永远排第一且永远衰减不到回收线，-1 会永远垫底。
    #       权重是"多重"的度量，它只有 [0,1] 这段有意义。
    #    b) t_anchor 是 0 / None（老数据没补、或者某条写入路径忘了设锚）：
    #       以前 age 会算成"从 1970 年到现在"，任何一条都会被当场归零。
    #       不知道什么时候写的最安全解释是"不衰减"—— 宁可记得太牢，
    #       不能因为一个缺失的字段就凭空忘掉。
    w0 = _clamp01(w0)
    if not t_anchor:
        return w0
    age = max(0.0, now - t_anchor)
    # 🔧 2026-11-03：长期池的保护期必须按**真实时间**算，再乘分区倍率。
    #    之前是「先把 age 乘上 region_scale，再扣 90 天」—— 于是 flash 区
    #    （0.30 倍）的保护期变成 90/0.3 = 300 天真·现实天数，self 区 150 天。
    #    也就是说「刚被想起来过的记忆受 90 天保护」这条规矩，被分区倍率
    #    偷偷改成了「受 90 天 × 1/倍率 保护」。保护期说的是"多久之内不掉"，
    #    是现实里的时间，跟这条记忆自己衰减得多慢是两件事。
    if pool == POOL_LONG:
        if age > LONG_PROTECT_DAYS * DAY:
            age -= LONG_PROTECT_DAYS * DAY
        else:
            age = 0.0
    # 🔧 2026-11-06（V5.2 分区细分）：有自己 λ 的区，走统一公式
    #      有效 λ = 区 λ × 池阶段倍率 × 今天挤不挤 ÷ 关系倍率
    #    这样「学习 7 天、工作 15 天」这种区差在升池之后依然保留；
    #    开关默认关着，关着时下面那条老路径一字不改。
    if _REGION_LAMBDA_ON:
        lam = effective_lambda(region, pool, person_layer or "")
        if lam is not None:
            return w0 * math.exp(-max(1e-12, lam) * age)
    # 🔧 2026-11-05：闲聊不乘分区倍率，用自己的固定 λ。
    #    乘倍率会让它先落短期池时半衰期只剩 1.3 小时，比"明天忘光"快太多。
    if region == R_CHATTER:
        lam = chatter_lambda(person_layer or "")
        return w0 * math.exp(-lam * age)
    if region:
        age *= region_decay_scale(region)
    # 🔧 V5.6：小池的衰减放慢到三分之一。
    #    为什么不能沿用主池的 λ：小池 TTL 21 天，而短期 λ 半衰期只有 4.5 天，
    #    那样到第 14 天 w 已经趋零 —— "寿命更长"就成了一句空话。
    if pool == POOL_SHORT_WARM:
        return w0 * math.exp(-LAM_S1 * WARM_SLOW * age)
    if pool == POOL_MEDIUM_WARM:
        return w0 * math.exp(-LAM_M * WARM_SLOW * age)
    if pool == POOL_SHORT:
        if age <= S_SHORT_PHASE:
            return w0 * math.exp(-LAM_S1 * age)
        w = w0 * math.exp(-LAM_S1 * S_SHORT_PHASE)
        return w * math.exp(-LAM_S2 * (age - S_SHORT_PHASE))
    if pool == POOL_MEDIUM:
        return w0 * math.exp(-LAM_M * age)
    if pool == POOL_ARCHIVE:
        return w0 * math.exp(-K_ARCHIVE * age)
    if pool == POOL_DEEP:
        return w0 * math.exp(-K_DEEP * age)
    # 长期池：刚被想起来过的 90 天里不掉，之后照常慢衰。
    # 「重要 ≠ 永久活跃」：它不会被砍，但也不会永远占着最亮的位置。
    return w0 * math.exp(-LAM_L * age)

# 底色三层（core_*）：时间磨不掉，只能被新事实改写。
TIER_CORE_MUTABLE = "core_mutable"
TIER_CORE_TAG = "core_tag"
TIER_CORE_FIXED = "core_fixed"
CORE_TIERS = (TIER_CORE_MUTABLE, TIER_CORE_TAG, TIER_CORE_FIXED)


WARM_SLOW = 1.0 / 3.0         # 小池里的衰减放慢到三分之一

POOL_ARCHIVE = "archive"     # 潜意识：不参与实时检索，但还在

POOL_DEEP = "deep"           # 深层痕迹：几乎忘光，只剩模糊的存在感

K_ARCHIVE = 1.5e-8           # 半衰期 ~535 天

K_DEEP = 3.0e-9              # 半衰期 ~2674 天

POOL_SHORT_WARM = "short_warm"

POOL_MEDIUM_WARM = "medium_warm"

LONG_PROTECT_DAYS = 90

_REGION_LAMBDA_ON = False

def stage_factor(pool):
    """升池变慢的倍率，以短期池慢段为基准。

    学习记忆半衰期 7 天：短期 7 天 → 中期 46.7 天 → 长期 583 天。
    这样「升池 = 改判为更要紧」的手感还在，而区与区的差别不会被抹平
    （旧算法里曲线是池子定的，一进中期分区差异就没了）。
    """
    if pool == POOL_SHORT:
        return 1.0
    if pool == POOL_SHORT_WARM:
        return WARM_SLOW
    if pool == POOL_MEDIUM_WARM:
        return (LAM_M * WARM_SLOW) / LAM_S1
    if pool == POOL_MEDIUM:
        return LAM_M / LAM_S1
    if pool == POOL_LONG:
        return LAM_L / LAM_S1
    if pool == POOL_ARCHIVE:
        return K_ARCHIVE / LAM_S1
    if pool == POOL_DEEP:
        return K_DEEP / LAM_S1
    return 1.0


MEDIA_DROP_W = 0.10            # 低于它删帧。落在约 65 天"没见过一次"的图


MEDIA_DECAY_B = 0.55           # 幂律指数：越大掉得越快


MEDIA_TAU_DAYS = 1.0           # 时间尺度 τ（天）


LOAD_TAU = 86400.0                   # 负载指数平滑的时间常数 = 24 小时


def _f_of(val, budget):
    """负载 → 拥挤系数。**这是 f 的唯一一份公式**，别处只许调它。

    （教训：两套公式不互相校验，只会悄悄分家还不报错。）
    """
    if not budget or budget <= 0:
        return 1.0
    ratio = max(0.0, float(val)) / float(budget)
    f = math.sqrt(ratio) if ratio > 0 else 0.0
    return max(LOAD_F_MIN, min(LOAD_F_MAX, f))


def media_weight_now(w0, t_anchor, now, pinned=0):
    """画面权重：幂律，不是指数（模块级，控制台画曲线也要用同一个函数）。

    w(t) = w0 · (1 + t/τ)^(-b)，τ=1 天、b=0.55
    单图（w0=1）：1 天 .68 / 7 天 .32 / 30 天 .15 / 60 天 .10 —— 到 0.10 就删。
    幂律有长尾，符合"很久以后突然想起来"；指数会到点归零，与艾宾浩斯数据不符。
    """
    if pinned:
        return 1.0
    age = max(0.0, float(now) - float(t_anchor or 0)) / DAY
    try:
        return float(w0) * (1.0 + age / MEDIA_TAU_DAYS) ** (-MEDIA_DECAY_B)
    except Exception:
        return float(w0 or 0.0)


def region_snapshot_from_db(conn, now=None):
    """从 meta 读负载、算 f、返回控制台要的快照 —— 不用建 MemoryCard。

    控制台只有 sqlite 连接、没有卡对象（也不想为画一张图去加载模型），
    所以给它一个纯函数版本；公式仍然只有 `_f_of` 一份。
    """
    now = time.time() if now is None else float(now)
    raw = {}
    try:
        row = conn.execute(
            "SELECT val FROM meta WHERE key=?", ("region_load",)).fetchone()
        if row and row[0]:
            raw = json.loads(row[0]) or {}
    except Exception:
        raw = {}
    on = False
    try:
        row = conn.execute(
            "SELECT val FROM meta WHERE key=?", ("region_lambda_on",)).fetchone()
        on = bool(row and str(row[0]).strip() in ("1", "true", "True"))
    except Exception:
        on = False
    regions = []
    for reg in (R_CHATTER, R_PLAY, R_STUDY, R_WORK):
        val = 0.0
        try:
            v = raw.get(reg)
            if v:
                val = float(v[0]) * math.exp(-max(0.0, now - float(v[1])) / LOAD_TAU)
        except Exception:
            val = 0.0
        budget = REGION_DAY_BUDGET.get(reg, 0.0)
        regions.append({
            "region": reg,
            "half_life_h": REGION_HALF_LIFE.get(reg, 0.0) / 3600.0,
            "load": round(val, 2),
            "budget": budget,
            "factor": round(_f_of(val, budget), 3),
        })
    return {"on": on, "regions": regions}


REGION_DAY_BUDGET = {
    R_CHATTER: 40.0,
    R_PLAY: 10.0,
    R_STUDY: 20.0,
    R_WORK: 8.0,
}


LOAD_F_MIN, LOAD_F_MAX = 0.55, 2.2   # 拥挤系数的上下限（第 7 批从 card2 搬来，_f_of 用它）
