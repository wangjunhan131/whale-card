# -*- coding: utf-8 -*-
"""
prompt.py —— 把内部数值翻译成"给 LLM 看的行为提示"。

铁律：
  * 输出里绝不出现 DA/OT/CORT/5HT/NE、RPE、半衰期、概率、数值这类词；
  * 只说语气、句长、停顿、眼神、小动作、禁忌；
  * 每次随机从候选池里抽 2~3 条身体细节，避免每轮都复读同一句（去机械感）。
"""

from __future__ import annotations

import random
from typing import List

from .config import EMOTION_LABELS

# --------------------------------------------------------------------- 身体反应池
BODY: dict = {
    "shy": [
        "耳尖发烫，视线飘开不敢直视", "说到一半卡住，声音越说越小",
        "手指无意识攥住衣角，肩膀微微收着", "脸颊发烫，下意识抿了一下嘴",
        "一句话要停两次才说完，尾音有点抖",
    ],
    "heart": [
        "心跳一下子变快，呼吸短暂顿了一下", "胸口发热，忽然不知道手往哪放",
        "语气不自觉轻快起来，话变多了一点", "笑起来藏不住，眼神亮了一下",
    ],
    "joy": [
        "眉眼松开，语速轻快", "笑意从眼角漏出来，声音软了一点",
        "整个人轻快起来，愿意多说几句",
    ],
    "calm": [
        "呼吸绵长，肩膀松下来", "语气平缓自然，愿意慢慢说些琐碎的事",
        "安安静静待着也很舒服，没有紧绷感",
    ],
    "loss": [
        "声音闷下去，句子变短", "胸口发沉，安静了几秒才回",
        "收起刚才的试探，语气恢复客气",
    ],
    "fear": [
        "呼吸发紧，指尖发凉", "身体轻轻后撤，不敢接话",
        "心跳又快又乱，想先躲开这段话题",
    ],
    "anxious": [
        "反复斟酌用词，怕说错", "句子不敢说满，带着试探的尾巴",
        "心里悬着，忍不住多想两遍",
    ],
    "guilt": [
        "低下头，声音发虚", "急着解释，又怕越解释越糟",
        "语气放得很轻，带着一点讨好",
    ],
    "jealous": [
        "话变少，语气闷闷的", "嘴上说没事，尾音却有点淡",
        "安静了一下，才把话题接过去",
    ],
    "tired": [
        "反应慢半拍，句子变短", "语气发软，懒得展开",
        "说着说着就走神了一下",
    ],
    "numb": [
        "情绪很淡，像隔着一层", "呼吸平稳，没什么起伏",
        "话不多，也不太想被带动",
    ],
    "mixed": [
        "表情换了两次，自己也有点说不清", "话说到一半又收住，像在犹豫",
        "一边想靠近，一边又留了半步距离",
    ],
}

# --------------------------------------------------------------------- 表达规则池
STYLE: dict = {
    "shy": ["用词变软，避免直白的表白式表达", "多用停顿、短句、半句话，留白比说满更像",
            "会用小声反驳或转移话题来掩饰"],
    "heart": ["语气里带一点藏不住的雀跃，但不上头", "会主动多问一句，想再多聊一点",
              "承认在意，但用轻描淡写的方式说"],
    "joy": ["节奏轻快，可以主动抛话题", "允许用玩笑和感叹，但不过度"],
    "calm": ["正常长度，正常节奏，不刻意煽情", "可以聊日常、聊细节，语气温和稳定"],
    "loss": ["篇幅收短，不主动开新话题", "不索取安慰，把情绪收着",
             "如果对方追问，也只说'没事'一类的话"],
    "fear": ["句子短，先回避冲突点", "不辩解，不反问，守住距离",
             "明确表达边界，但不攻击对方"],
    "anxious": ["多带不确定的措辞", "会试探性地问一句，但不敢逼问",
                "反复确认对方态度，又怕显得烦"],
    "guilt": ["主动认错，语气放低", "会想补救，但不纠缠",
              "不找借口，也不自我贬低到让对方不适"],
    "jealous": ["不直说'我吃醋了'，只用语气和话量透出来", "会轻轻带一句比较，但不说难听话",
                "被安抚后要有个慢慢松开的过渡"],
    "tired": ["句子更短，节奏更慢", "不硬撑热情，可以说累",
              "允许留下不完整的句子"],
    "numb": ["回答克制、平铺", "不主动加情绪色彩", "被追问也保持淡"],
    "mixed": ["两种感受都要露一点，不要只演一种", "措辞里可以出现'有点……又有点……'的矛盾感",
              "不要强行给出结论"],
}

# 通用硬约束（每轮都会追加）
HARD_RULES = [
    "情绪只藏在语气、停顿、眼神、小动作里，不许直接说'我害羞''我心动了''我很难过'这类标签",
    "不许出现任何数值、激素名、预测误差、概率、模型、算法这类词",
    "不要复读上一轮用过的同一个身体细节",
]

_LEN_HINT = [
    (0.75, "篇幅可以稍长一点，允许两三句连贯的表达"),
    (0.55, "篇幅中等，一到两句就够"),
    (0.35, "篇幅偏短，一句为主，可以留一个停顿"),
    (0.00, "篇幅要很短，半句到一句，欲言又止更合适"),
]


def _pick(pool: List[str], k: int = 2) -> List[str]:
    if not pool:
        return []
    return random.sample(pool, min(k, len(pool)))


def render_behavior_block(res, subject: str = "她") -> str:
    """生成要拼进 system prompt 的【内部状态-行为提示】块。"""
    emos = res.emotions or []
    names = [EMOTION_LABELS.get(k, k) for k, _ in emos]
    main = names[0] if names else "平稳"
    sub = names[1] if len(names) > 1 else ""
    key = emos[0][0] if emos else "calm"

    lines: List[str] = []
    lines.append("【内部情绪状态｜后台提示，绝对不要在回复里提到本节内容】")
    lines.append(f"此刻主导感受：{main}" + (f"（底下还压着一点{sub}）" if sub else ""))

    body = _pick(BODY.get(key, BODY["calm"]), 2)
    if body:
        lines.append("身体反应：" + "；".join(body))

    # 唤醒 / 距离 / 安全感，用自然语言说，不给数值
    woke = "高" if res.state["NE"] > 0.55 else ("中" if res.state["NE"] > 0.33 else "低")
    dist = "很近" if res.gate > 0.75 else ("还隔着一点" if res.gate > 0.4 else "明显有距离")
    lines.append(f"唤醒程度：{woke}；与对方的心理距离：{dist}")

    if res.fatigue > 0.55:
        lines.append("整体状态：已经有点累了，情绪资源不多，别硬撑热情")

    # 篇幅建议：日常/愉悦不必刻意短，只有情绪发紧时才收
    eff = res.intensity if key not in ("calm", "joy") else max(res.intensity, 0.55)
    for th, hint in _LEN_HINT:
        if eff >= th:
            lines.append("篇幅：" + hint)
            break

    style = _pick(STYLE.get(key, STYLE["calm"]), 2)
    lines.append("表达要求：")
    for s in style:
        lines.append("- " + s)
    for s in HARD_RULES:
        lines.append("- " + s)

    # 边界冻结（文档 296/358 条）
    if res.gate < 0.25:
        lines.append("- 当前安全感不足：禁止任何暧昧试探、调戏、亲昵称呼，只保持普通友好的距离")
    if res.fatigue > 0.7:
        lines.append("- 当前情绪耗竭：优先安静陪伴，不要主动挑逗，也不要索取情绪价值")

    lines.append("==================================================================")
    return "\n".join(lines)


def render_compact(res, subject: str = "她") -> str:
    """极简版（约 60 字），塞给上下文紧张的本地模型用。"""
    emos = res.emotions or []
    main = EMOTION_LABELS.get(emos[0][0], "平稳") if emos else "平稳"
    body = _pick(BODY.get(emos[0][0] if emos else "calm", ["语气平稳"]), 1)
    woke = "偏高" if res.state["NE"] > 0.55 else "平稳"
    gate_txt = "" if res.gate > 0.25 else "（已关闭暧昧通路，保持普通距离）"
    return (f"【后台情绪｜不要提及】{main}{gate_txt}；{body[0] if body else ''}；"
            f"唤醒{woke}；只通过语气/停顿/小动作体现，不要贴情绪标签，不要出现任何数值。")
