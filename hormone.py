# -*- coding: utf-8 -*-
"""内分泌四激素系统  ——  V5.0 §4 / T3-2

## 为什么要有这个

记忆系统解决「记得什么」，心境解决「心情怎样」，
但这两样都太"结论化"了 —— 直接说「你现在心情挺好的」，
模型只会照着念，像在念仪表盘。

激素解决的是**更底层、更慢、更说不清的那一层**：
不是「我心情好」，而是「今天血清素偏高、皮质醇很低」，
于是话变多、句子变长、更愿意提以前的事。
这种偏置是连续的、多轴的，组合出来才像人。

## 四个变量（值域 [0,1]）

  名称      符号      基线    高时表现                  低时表现
  血清素    h_sero    0.60    温柔、包容、耐心、低攻击   寡言、冷淡、敏感、敷衍
  多巴胺    h_dopa    0.40    话多、主动、好奇、活泼     疲惫、被动、慵懒
  皮质醇    h_cort    0.20    压力大、易出错、回忆闭塞   心态平稳、发挥稳定
  催产素    h_oxy     0.50    优先召回羁绊记忆、更柔软   关系疏离、陪伴失效

## 与 oxy（依恋值）的关系

oxy 是长期积累的**存量**（依恋有多深），h_oxy 是当下这一刻的**水平**（此刻多亲近）。
两者共用一个物理字段（都存 meta 表），不新建第二张表。
oxy 是「我跟他的关系有多深」，h_oxy 是「我这一刻有多想贴近他」。

## 三条公式

1. 稳态阻尼回归（每 tick）
   h(t+dt) = h_base + (h(t) - h_base) * exp(-LAM_H * dt)

2. 事件扰动（对话触发，三组 A/B/C，见 apply_event）

3. 联动加权（见 f_endo / q_max / attach_bonus / style_hint）
"""
import math
import os

DAY = 86400.0
HOUR = 3600.0

# ---- 四个激素的基线与名字 ----
H_BASE = {
    "h_sero": 0.60,
    "h_dopa": 0.40,
    "h_cort": 0.20,
    "h_oxy": 0.50,
}
H_KEYS = ("h_sero", "h_dopa", "h_cort", "h_oxy")

# 🔧 LAM_H 修正：原文档给 0.0015 但没给单位。
#    按小时算半衰期 462 小时 = 19.3 天 —— 吵完架三周才平复，不像人。
#    改成 0.05/小时：半衰期 13.9 小时，符合「睡一觉就淡了」。
#    2026-11-10 再改：原本 0.02（半衰 34.7h）实测太黏 —— h_log 里 dopa 长期
#    0.94 贴顶、一次夸奖只 +0.004（增益乘 (1-h)，越接近顶加得越少），
#    情绪曲线于是成了直线。0.05 让余韵在半天内退回去，下次才有地方可涨。
# 开关：WHALE_HORMONE_FAST=0 回到旧值 0.02（半衰 34.7h）+ 旧语气幅度。
_HORMONE_FAST = (os.environ.get("WHALE_HORMONE_FAST", "1") != "0")
LAM_H = 0.05 if _HORMONE_FAST else 0.02

# ---- 扰动系数（V5.0 §4.3）----
# (A) 正向亲密事件
ETA_OXY_UP = 0.12
ETA_DOPA_UP = 0.10
ETA_SERO_UP = 0.08
ETA_CORT_DN = 0.10
# (B) 负面冲突事件
ETA_CORT_UP = 0.15
ETA_SERO_DN = 0.12
ETA_DOPA_DN = 0.08
ETA_OXY_DN = 0.10
# (C) 低落治愈事件（沿用 A 的部分系数，按文档比例）
C_SERO_SCALE = 0.8
C_DOPA_SCALE = 0.3

R_INERTIA_MIN, R_INERTIA_MAX = 0.30, 0.85
R_INERTIA_BASE = 0.70

# ---- 词表（判断事件类型）----
# 亲密/正向
POS_WORDS = [
    "喜欢你", "爱你", "想你", "在乎你", "谢谢", "辛苦", "抱抱", "陪着你",
    "陪着我", "永远", "值得", "相信你", "温柔", "可爱", "宝贝", "乖",
    "别怕", "我在", "心疼", "重要", "舍不得", "亲亲", "想你啦",
    "开心", "高兴", "舒服", "幸福", "踏实", "宁静", "笑死", "激动",
    "好耶", "考好", "赢了", "爽",
]
# 伤人/冲突
NEG_WORDS = [
    "讨厌你", "烦死", "滚", "闭嘴", "别说了", "不想理", "骗人", "骗我",
    "失望", "敷衍", "冷冰冰", "没意思", "随便你", "无所谓", "不想跟你",
    "你走开", "烦人", "蠢", "笨死", "废物", "不需要你", "凶我",
]
# 低落（用户自己在难受，不是冲我）
LOW_WORDS = [
    "难受", "难过", "伤心", "累", "困", "饿", "生气", "害怕", "紧张",
    "委屈", "哭了", "烦", "无聊", "疼", "崩", "糟糕", "没考好", "考砸",
    "失败", "吵架", "压力", "失眠", "想哭",
]

# 指向用户自己的信号词（用来区分 B 和 C）
_SELF_HINTS = ["我", "今天", "昨天", "考试", "学校", "老师", "同学",
               "作业", "成绩", "家里", "妈妈", "爸爸"]


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def regress(h, h_base, dt_hours, lam=LAM_H):
    """稳态阻尼回归：h(t+dt) = h_base + (h(t)-h_base)*exp(-LAM_H*dt)"""
    if dt_hours <= 0:
        return h
    k = math.exp(-lam * dt_hours)
    return _clamp(h_base + (h - h_base) * k)


def regress_all(hs, dt_hours, bases=None, lam=None):
    """四个一起回归（bases 不传时用带昼夜偏移的基线；lam 不传用 LAM_H）。"""
    if bases is None:
        bases = bases_at(None)
    lam = LAM_H if lam is None else float(lam)
    return {k: regress(hs.get(k, bases[k]), bases[k], dt_hours, lam=lam)
            for k in H_KEYS}


# ---- 昼夜节律（2026-11-10 新增）----
# 为什么加：以前激素的"基线"是四个常数，没有一天之内的起伏，静息时只剩
# 指数回归 → 曲线上一条直线。真人的底子是有日节律的：下午精神、清晨最钝、
# 傍晚最稳。这里只给基线加一点点偏移（最大 +0.08），不是伪造情绪，
# 是补上"一天到现在几点"这个生理事实。WHALE_CIRCA=0 可关。
HOUR_BASE = {
    # 小时: {激素: 偏移}（没列到的小时 = 0）
    8:  {"h_cort": 0.08},      # 早晨皮质醇高（起床后的清醒峰）
    11: {"h_dopa": 0.08},      # 上午到傍晚多巴胺都在高位（11–17 点）
    12: {"h_dopa": 0.08},
    13: {"h_dopa": 0.08},
    14: {"h_dopa": 0.08},
    15: {"h_sero": 0.06, "h_dopa": 0.08},   # 下午血清素最高
    16: {"h_dopa": 0.08},
    17: {"h_dopa": 0.08},
}
_CIRCA_ON = (os.environ.get("WHALE_CIRCA", "1") != "0")


def circa_offset(hour):
    """当前小时对应的基线偏移（空 dict 表示没有）"""
    if not _CIRCA_ON:
        return {}
    return dict(HOUR_BASE.get(int(hour) % 24, {}))


def bases_at(ts, hour=None):
    """带上昼夜偏移的基线表（回归用）。

    ts 是 epoch 秒；hour 可显式给（测试用）。
    """
    import time as _t
    if hour is None:
        hour = _t.localtime(ts).tm_hour if ts else _t.localtime().tm_hour
    b = dict(H_BASE)
    for k, dv in circa_offset(hour).items():
        b[k] = _clamp(b.get(k, 0.5) + dv)
    return b


def classify_event(text):
    """判断一句话属于哪一组事件：返回 ('A'|'B'|'C'|None, 强度 s)

    判断顺序很重要：
      1) 有伤人词 → B（冲我来的，压过一切）
      2) 有亲密词 → A
      3) 有低落词且指向用户自己 → C（他难受了，我在被需要）
      4) 有低落词但没指向自己 → B（气氛不对）
      5) 都没有 → None（不扰动）
    """
    if not text:
        return None, 0.0
    t = text
    neg = sum(1 for w in NEG_WORDS if w in t)
    pos = sum(1 for w in POS_WORDS if w in t)
    low = sum(1 for w in LOW_WORDS if w in t)

    if neg > 0:
        # 伤人词 + 低落词一起出现时，仍然是 B（他冲我发火）
        s = min(1.0, 0.55 + 0.15 * neg + (0.10 if low else 0.0))
        return "B", _clamp(s)
    if pos > 0:
        s = min(1.0, 0.50 + 0.15 * pos)
        return "A", _clamp(s)
    if low > 0:
        self_hint = any(h in t for h in _SELF_HINTS)
        s = min(1.0, 0.45 + 0.15 * low)
        if self_hint:
            return "C", _clamp(s)
        return "B", _clamp(s * 0.7)   # 没指明是谁，弱一点当 B
    return None, 0.0


def apply_event(hs, kind, s):
    """按事件组扰动四个激素（V5.0 §4.3）

    (A) 正向亲密：催产素/多巴胺/血清素升，皮质醇降
    (B) 负面冲突：皮质醇升，血清素/多巴胺降，催产素带惯性降
    (C) 低落治愈：血清素大幅升，催产素升，皮质醇降，多巴胺小升
    """
    h = dict(hs)
    if kind is None or s <= 0:
        return h

    if kind == "A":
        h["h_oxy"] = _clamp(h["h_oxy"] + ETA_OXY_UP * (1 - h["h_oxy"]) * s)
        h["h_dopa"] = _clamp(h["h_dopa"] + ETA_DOPA_UP * (1 - h["h_dopa"]) * s)
        h["h_sero"] = _clamp(h["h_sero"] + ETA_SERO_UP * (1 - h["h_sero"]) * s * 0.5)
        h["h_cort"] = _clamp(h["h_cort"] - ETA_CORT_DN * h["h_cort"] * s)

    elif kind == "B":
        h["h_cort"] = _clamp(h["h_cort"] + ETA_CORT_UP * (1 - h["h_cort"]) * s)
        h["h_sero"] = _clamp(h["h_sero"] - ETA_SERO_DN * h["h_sero"] * s)
        h["h_dopa"] = _clamp(h["h_dopa"] - ETA_DOPA_DN * h["h_dopa"] * s)
        # 依恋惯性：越深越不容易被一句话打穿
        r = _clamp(h["h_oxy"] * R_INERTIA_BASE, R_INERTIA_MIN, R_INERTIA_MAX)
        h["h_oxy"] = _clamp(h["h_oxy"] - ETA_OXY_DN * h["h_oxy"] * s * (1 - r))

    elif kind == "C":
        h["h_sero"] = _clamp(h["h_sero"] + ETA_SERO_UP * (1 - h["h_sero"]) * s
                             * C_SERO_SCALE)
        h["h_oxy"] = _clamp(h["h_oxy"] + ETA_OXY_UP * (1 - h["h_oxy"]) * s)
        h["h_cort"] = _clamp(h["h_cort"] - ETA_CORT_DN * h["h_cort"] * s)
        h["h_dopa"] = _clamp(h["h_dopa"] + ETA_DOPA_UP * (1 - h["h_dopa"]) * s
                             * C_DOPA_SCALE)

    return h


# ---- 中性消息也推一把（V5.11，2026-11-08 用户：「情绪太稳定了」）----
# 原来只有命中亲密/冲突词表才动，日常聊天一律不动，于是四个数永远贴着基线，
# 体现出来就是「情绪没有实质变化」。这一层每句话都推一点点：
# 说得热闹抬多巴胺、丧话抬皮质醇，长句子本身也算劲头。
_TONE_POS = ("哈哈", "嘿嘿", "嘻嘻", "好呀", "太好了", "开心", "舒服", "不错",
             "喜欢", "谢谢", "乖", "晚安", "加油", "！", "😄", "🥹")
_TONE_NEG = ("烦", "累", "难受", "生气", "失望", "唉", "算了", "无聊", "不行",
             "哭", "崩", "加班", "熬夜", "委屈")


_TONE_AMP = 0.006 if _HORMONE_FAST else 0.012
_TONE_DOPA = 0.6 if _HORMONE_FAST else 1.6


def tone_nudge(hs, text):
    """按这句话的语气给四个激素一点微调（幅度 ≤0.006，但每句都推）。"""
    t = text or ""
    s = 0.0
    for w in _TONE_POS:
        if w in t:
            s += _TONE_AMP
    for w in _TONE_NEG:
        if w in t:
            s -= _TONE_AMP
    s += min(0.015, len(t) / 4000.0)
    if abs(s) < 1e-6:
        return hs
    h = dict(hs)
    # 🔧 2026-11-10：dopa 乘数 1.6 → 0.6。dopa 基线 0.40 而实测长期 0.94，
    #    就是被这一层每句话推上去又退不下来顶到天花板；0.6 让它有余量。
    h["h_dopa"] = _clamp(h.get("h_dopa", 0.40) + s * _TONE_DOPA)
    h["h_sero"] = _clamp(h.get("h_sero", 0.60) + s * 0.6)
    h["h_cort"] = _clamp(h.get("h_cort", 0.20) - s * 0.4)
    return h


# ============================================================
# 联动加权（V5.0 §4.4）
# ============================================================
def f_endo(hs):
    """陈述记忆召回打分的内分泌系数

    f_endo = (1 + 0.30*(h_oxy-0.5)) * (1 - 0.35*max(0,h_cort-0.20)/0.80)

    含义：催产素高 → 羁绊回忆更容易翻出来；
          皮质醇高 → 压力大，温柔往事想不起来（回忆闭塞）。
    """
    h_oxy = hs.get("h_oxy", H_BASE["h_oxy"])
    h_cort = hs.get("h_cort", H_BASE["h_cort"])
    a = 1.0 + 0.30 * (h_oxy - 0.50)
    b = 1.0 - 0.35 * max(0.0, h_cort - 0.20) / 0.80
    return _clamp(a * b, 0.60, 1.20)


def q_max(hs):
    """程序记忆的 Q 分上限：压力高时发挥变差

    Q_max = 5 * (1 - 0.60*max(0,h_cort-0.20)/0.80)
    皮质醇 0.2 → 5.0；皮质醇 1.0 → 2.0
    """
    h_cort = hs.get("h_cort", H_BASE["h_cort"])
    return 5.0 * (1.0 - 0.60 * max(0.0, h_cort - 0.20) / 0.80)


def attach_bonus(hs):
    """陪伴专属技能加成：范围 [0.75, 1.25]"""
    h_oxy = hs.get("h_oxy", H_BASE["h_oxy"])
    return _clamp(1.0 + 0.25 * (h_oxy - 0.50) * 2, 0.75, 1.25)


def style_hint(hs):
    """把激素状态翻成一句「说话倾向」，塞进上下文人格格。

    🔧 注意：只给**倾向**，不给「你应该怎么说话」。
       告诉模型「话多、带感叹」比「请活泼一点」有效得多 ——
       前者是状态描述，后者是命令，模型对命令的反应是模板化。
    """
    parts = []
    h_dopa = hs.get("h_dopa", H_BASE["h_dopa"])
    h_sero = hs.get("h_sero", H_BASE["h_sero"])
    h_cort = hs.get("h_cort", H_BASE["h_cort"])
    h_oxy = hs.get("h_oxy", H_BASE["h_oxy"])

    if h_dopa >= 0.62:
        parts.append("话多一点")
    elif h_dopa <= 0.25:
        parts.append("有点懒，不想多说")

    if h_sero >= 0.72:
        parts.append("耐心")
    elif h_sero <= 0.38:
        parts.append("有点冷淡")

    if h_cort >= 0.50:
        parts.append("心里压着事，话短")
    elif h_cort <= 0.10:
        parts.append("很放松")

    if h_oxy >= 0.72:
        parts.append("想提以前的事")
    elif h_oxy <= 0.28:
        parts.append("有点疏远")

    return "、".join(parts)


def describe(hs):
    """给人看的四行状态（调试用）"""
    return {
        "血清素": round(hs.get("h_sero", 0), 3),
        "多巴胺": round(hs.get("h_dopa", 0), 3),
        "皮质醇": round(hs.get("h_cort", 0), 3),
        "催产素": round(hs.get("h_oxy", 0), 3),
    }
