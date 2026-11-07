# -*- coding: utf-8 -*-
"""
评价层（Appraisal）：把一句人话，翻译成引擎能吃的数值向量。

它解决的是 RPE 公式里的 V_actual 从哪来：
    不是模型自己瞎猜"这句话值几分"，而是按一组可解释的社交线索打分。
如果你后面想接本地小模型做语义打分，只要让它输出同样这几个字段即可（见 from_llm_dict）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Dict


# ---------------------------------------------------------------------------
# 一、线索词表（中文为主，少量英文与口语变体）
# 每条线索一个 0~1 的强度，靠命中次数累加后饱和
# ---------------------------------------------------------------------------
CUES: Dict[str, list] = {
    # 正向温度
    "praise": ["好看", "漂亮", "可爱", "厉害", "优秀", "真棒", "喜欢你", "佩服", "聪明",
               "温柔", "帅", "好听", "好厉害", "进步", "用心", "记得", "靠谱"],
    "care": ["累不累", "休息", "早点睡", "注意身体", "别熬夜", "吃药", "喝水", "心疼",
             "担心你", "照顾好", "抱抱", "没事吧", "辛苦了", "陪你"],
    "share": ["今天", "刚才", "我跟你说", "分享", "看到一个", "突然想到", "给你看",
              "你猜", "我发现", "顺便说"],
    "intimacy": ["想你", "喜欢", "抱", "靠", "贴", "陪着", "专属", "只对你", "靠近",
                 "悄悄", "小声", "一直在", "心动", "脸红", "害羞", "宝宝", "亲爱的",
                 "撩", "逗你", "杂鱼", "笨蛋"],
    "empathy": ["我知道你", "理解你", "换我", "辛苦了", "不容易", "你别难过",
                "我懂", "感同身受", "心疼你"],

    # 负向
    "cold": ["随便吧", "随便你", "随你", "算了", "没什么", "不聊了", "没空",
             "没必要", "无所谓", "别烦", "不想说", "不想聊", "不回应", "已读不回"],
    "threat": ["滚", "闭嘴", "烦死", "讨厌你", "恶心", "废物", "蠢", "没用",
               "再这样", "警告", "后果", "别来找我", "绝交", "拉黑"],
    "boundary": ["不要这样", "别这样", "停", "不舒服", "过分了", "越界", "别碰",
                 "不想", "拒绝", "恶心到", "不合适"],
    "neglect": ["很久没", "已读不回", "不回我", "消失", "没消息", "等了你",
                "又不回", "联系不上"],

    # 语气填充（表示投入度）
    "engagement": ["？", "?", "！", "!", "哈哈", "嘿嘿", "呜", "欸", "诶", "呀", "啦"],

    # 修复信号（道歉、安抚）：它的作用是让安全感回升，而不是又记一笔账
    "repair": ["对不起", "抱歉", "是我不好", "我错了", "原谅我", "别生气", "不要生气",
               "不生气", "没生气", "没关系", "没事的", "不怪你", "没怪你", "不介意",
               "我理解", "我懂你", "我在呢", "我一直都在", "我在陪", "不是为了怪你"],
}

# 越界/威胁类要求"软拒绝"歧义处理：出现"别这样"但语气柔和时降权
SOFTENERS = ["有点", "稍微", "我不太", "可能", "要不", "稍微有点", "有一点点"]


@dataclass
class Appraisal:
    """一轮交互的评价结果（全部 0~1）"""
    # 实际价值相关
    praise: float = 0.0
    care: float = 0.0
    share: float = 0.0
    intimacy: float = 0.0
    attention: float = 0.5
    # 负向
    cold: float = 0.0
    threat: float = 0.0
    boundary: float = 0.0
    neglect: float = 0.0
    # 独立通路
    empathy_pain: float = 0.0   # 对方在痛苦 → 共情通道（会抬 CORT，但不算我的价值损失）
    repair: float = 0.0         # 修复信号：道歉/安抚 → 安全感回升
    # 元信息
    scenario: str = "generic"   # 场景键，用于 TD 预期分档
    is_probe: bool = False      # 是否是我方主动发起试探（预激活）
    predicted: float | None = None  # 我方发起前的主观预判价值（None=用历史预期）
    note: str = ""
    raw: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_llm_dict(cls, d: dict) -> "Appraisal":
        """给本地小模型/LLM 打分的接入口：只取它认识的字段，其余走默认。"""
        allowed = {f for f in cls.__dataclass_fields__ if f not in ("raw", "note")}
        kw = {k: v for k, v in d.items() if k in allowed}
        for k in ("praise", "care", "share", "intimacy", "attention",
                  "cold", "threat", "boundary", "neglect", "empathy_pain"):
            if k in kw:
                kw[k] = _clip(float(kw[k]))
        return cls(**kw)


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _hits(text: str, words: list) -> int:
    n = 0
    for w in words:
        if w in text:
            n += 1
    return n


def _soften(text: str) -> float:
    """语气缓和系数：有'有点/稍微'这类词，把负向线索压低。"""
    return 0.55 if _hits(text, SOFTENERS) else 1.0


def estimate_appraisal(text: str, scenario: str = "generic",
                       is_probe: bool = False,
                       predicted: float | None = None) -> Appraisal:
    """规则版评价函数：单句文本 → Appraisal。纯本地、零延迟。"""
    t = (text or "").strip()
    ln = len(t)

    a = Appraisal(scenario=scenario, is_probe=is_probe, predicted=predicted)
    if not t:
        a.cold = 0.6
        a.attention = 0.1
        a.note = "空回复视为冷处理"
        return a

    soft = _soften(t)

    # 否定语境前置处理："我不生气""没关系""不怪你"里的负向词不该计分
    t_eff = t
    for phrase in ("不生气", "没生气", "不怪你", "没怪你", "不介意", "没关系",
                   "不打扰你", "不烦你", "没有讨厌", "不讨厌"):
        t_eff = t_eff.replace(phrase, "")
    # 道歉句里的"过分了/不舒服"是在说自己，不是在指责我
    a_repair = _clip(_hits(t, CUES["repair"]) / 2.0)

    # 每类线索：命中数 → 饱和（3 次命中就接近满值）
    a.praise = _clip(_hits(t, CUES["praise"]) / 3.0)
    a.care = _clip(_hits(t, CUES["care"]) / 3.0)
    a.share = _clip(_hits(t, CUES["share"]) / 3.0)
    a.intimacy = _clip(_hits(t, CUES["intimacy"]) / 3.0)
    a.empathy_pain = _clip(_hits(t, CUES["empathy"]) / 3.0) * (1.0 if "难过" in t or "累" in t else 0.6)
    a.repair = a_repair

    a.cold = _clip(_hits(t_eff, CUES["cold"]) / 2.5) * soft
    a.threat = _clip(_hits(t_eff, CUES["threat"]) / 2.0)
    a.boundary = _clip(_hits(t_eff, CUES["boundary"]) / 2.0) * soft
    a.neglect = _clip(_hits(t_eff, CUES["neglect"]) / 2.0)

    # 道歉/安抚优先：把当轮的负向线索整体打折（她在修，不该当新账记）
    if a_repair > 0:
        for f in ("cold", "threat", "boundary"):
            setattr(a, f, getattr(a, f) * (1.0 - 0.7 * a_repair))

    # —— 误判修正：这些话看着"冷淡"，其实只是普通语气 ——
    if any(w in t for w in ("随便聊聊", "随便说说", "随便问问", "随便看看")):
        a.cold = max(0.0, a.cold - 0.30)
    # 单字敷衍（"嗯""哦""行"）才算冷，出现在长句里不算
    if ln <= 4 and t.strip("。！？?!~ ") in ("嗯", "哦", "啊", "行", "好", "呃"):
        a.cold = max(a.cold, 0.45)
    # "我在忙/有点忙"是报备，不是冷淡；"别打扰"才是
    if any(w in t for w in ("我在忙", "有点忙", "忙着呢", "等下再说", "晚点聊", "先忙")):
        a.cold = max(0.0, a.cold - 0.35)
        a.attention = _clip(a.attention + 0.05)
    # 语气很软时，边界词只算轻微不适
    if _soften(t) < 1.0 and a.boundary > 0:
        a.boundary = a.boundary * 0.55

    # 投入度：长度 + 语气词 + 反问，但被冷/威胁压制
    att = 0.5 + min(ln, 120) / 240.0
    att += 0.08 * min(_hits(t, CUES["engagement"]), 3)
    if "。" in t and ln <= 6:
        att -= 0.18          # 极短句 + 句号 = 敷衍（"回来啦。"这种不算太短，别误伤）
    att *= (1.0 - 0.6 * max(a.cold, a.threat))
    a.attention = _clip(att)

    # 威胁会顺带触发警戒
    if a.threat > 0.5:
        a.note = "威胁信号"
    elif a.boundary > 0.5:
        a.note = "边界信号"
    a.raw = {k: round(v, 3) for k, v in asdict(a).items()
             if isinstance(v, (int, float)) and k not in ("predicted",)}
    return a
