# -*- coding: utf-8 -*-
"""PAD 三维情绪 —— 纯函数，不依赖任何东西（连 numpy 都不要）

为什么单独一个文件：
    这套公式有两个使用者 —— `card2`（每轮算当前状态）和 `console`
    （从 h_log 里补算历史点画曲线）。以前 console 里抄了一份，两份各自
    进化就会悄悄分家，而且没有任何东西会报错。现在公式只住在这里，
    谁要算就 import 它。别的地方再出现 `0.6 * dopa` 这种算式，就是错的。

三个轴（都映射到 −1..1，基线 = 平静 = 0）：
    P 愉悦：(mood − 0.5) × 2                  —— 心境是事件推出来的慢量
    A 唤醒：(0.6·dopa + 0.4·cort − a0) × 3.2  —— 高=绷着，低=没劲
    D 掌控：(0.6·oxy + 0.4·(1−cort) − d0) × 3.2 —— 高=自在，低=发怵

为什么要 A：心境只有一维，说不出「低落但平静」和「低落又紧绷」的区别。
为什么要相对基线：减掉基线，「比平时还低」才读得出来。
这两条是这套东西唯一的价值，改公式前先想清楚。
"""
import math

# 激素基线（和 hormone.H_BASE 保持一致；写在本地是为了不把 hormone 拖进来）
H_BASE = {"h_sero": 0.60, "h_dopa": 0.40, "h_cort": 0.20, "h_oxy": 0.50}
MOOD_NEUTRAL = 0.50
SCALE = 3.2          # 激素推到满时接近 ±1；太小天天顶格，太钝读不出来

PAD_NEUTRAL_BAND = 0.15      # |P|、|A| 都小于它，读作"平静"
PAD_AROUSAL_EDGE = 0.35      # A 超过它算"绷着"，低于负数算"没劲"


def _num(v, dflt):
    try:
        x = float(v)
    except Exception:
        return dflt
    return dflt if x != x else x          # NaN 兜底


def pad_of(mood, sero=None, dopa=None, cort=None, oxy=None):
    """核心公式。输入可以缺，缺了按基线补；输出恒在 [−1, 1]。"""
    mood = _num(mood, MOOD_NEUTRAL)
    sero = _num(sero, H_BASE["h_sero"])
    dopa = _num(dopa, H_BASE["h_dopa"])
    cort = _num(cort, H_BASE["h_cort"])
    oxy = _num(oxy, H_BASE["h_oxy"])

    a0 = 0.6 * H_BASE["h_dopa"] + 0.4 * H_BASE["h_cort"]
    d0 = 0.6 * H_BASE["h_oxy"] + 0.4 * (1.0 - H_BASE["h_cort"])
    k_a = 0.6 * dopa + 0.4 * cort
    k_d = 0.6 * oxy + 0.4 * (1.0 - cort)

    p = (mood - MOOD_NEUTRAL) * 2.0
    a = (k_a - a0) * SCALE
    d = (k_d - d0) * SCALE
    p, a, d = (round(max(-1.0, min(1.0, x)), 3) for x in (p, a, d))
    return {"p": p, "a": a, "d": d, "label": pad_label(p, a),
            "src": {"mood": round(mood, 3),
                    "sero": round(sero, 3), "dopa": round(dopa, 3),
                    "cort": round(cort, 3), "oxy": round(oxy, 3)}}


def pad_label(p, a):
    """给 P/A 两个轴起个中文名。只用来显示，不参与任何判定。"""
    if abs(p) < PAD_NEUTRAL_BAND and abs(a) < PAD_NEUTRAL_BAND:
        return "平静"
    if p >= PAD_NEUTRAL_BAND:
        if a >= PAD_AROUSAL_EDGE:
            return "雀跃"          # 高兴得坐不住
        if a <= -PAD_AROUSAL_EDGE:
            return "安稳"          # 高兴但懒懒的
        return "愉快"
    if p <= -PAD_NEUTRAL_BAND:
        if a >= PAD_AROUSAL_EDGE:
            return "紧绷"          # 低落又绷着 —— 一维心境说不出的那种
        if a <= -PAD_AROUSAL_EDGE:
            return "低落"          # 低落且没劲
        return "闷闷的"
    # 心情不高不低，但有情绪张力：往哪边偏，读法不一样
    #   P 略正 + 高唤醒 = 亢奋；P 略负 + 高唤醒 = 坐立不安
    if a >= PAD_AROUSAL_EDGE:
        return "亢奋" if p >= 0.0 else "坐立不安"
    if a <= -PAD_AROUSAL_EDGE:
        return "没劲"
    return "平静"


def style_hint(pad):
    """把 PAD 翻成一句"我现在什么状态"，塞进上下文给宿主参考。

    刻意只给状态、不给命令（跟 hormone.style_hint 同一套原则）：
    「我在难过，别闹」比「请表现得难过一点」管用 —— 后者是让她演。

    🔧 2026-11-08 改：以前只有 |P|≥0.15 或 |A|≥0.35 才吐字，于是日子过得
       平顺的时候这一格永远是空串 —— 情绪再动也传不到嘴上。现在改成
       **四档细读、常态也说**：开心说得出来，没劲也说得出来。
    """
    if not pad:
        return ""
    p = _num(pad.get("p"), 0.0)
    a = _num(pad.get("a"), 0.0)
    d = _num(pad.get("d"), 0.0)
    bits = []
    if p >= 0.35:
        bits.append("心情很好")
    elif p >= 0.12:
        bits.append("心情不错")
    elif p <= -0.35:
        bits.append("心情很低")
    elif p <= -0.12:
        bits.append("心情不太好")
    # 唤醒轴：管语速和话量，是最容易被听出来的那一维
    if a >= 0.30:
        bits.append("劲头足，话会多一点、句子会长")
    elif a >= 0.12:
        bits.append("有点精神")
    elif a <= -0.45:
        bits.append("没什么劲，话短、懒得绕")
    elif a <= -0.12:
        bits.append("懒懒的")
    # 掌控轴：管底气，管敢不敢开玩笑
    if d >= 0.25:
        bits.append("心里踏实，玩笑敢接")
    elif d <= -0.35:
        bits.append("没什么底气，别接玩笑")
    elif d <= -0.12:
        bits.append("有点发怵")
    if not bits:
        bits.append("平平的，没什么起伏")
    return "、".join(bits)
