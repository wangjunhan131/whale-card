# -*- coding: utf-8 -*-
"""内分泌 + 自主神经 + 躯体体征 —— 纯函数，零依赖。

## 为什么要这一层

原来的四激素（血清素/多巴胺/皮质醇/催产素）变化很慢：半衰期 1.5 天。
这带来一个说不清的不对劲：**听到一句话，人先心跳一下，然后才「有点心动」**。
激素负责后者，前者需要一个更快的层。

人体本来就是分层的：
- **自主神经（交感/副交感）** 是毫秒到几秒的快开关 —— 一瞬间脸红、心跳、
  呼吸变浅，先出来；
- **激素/神经递质** 是分钟到天的慢底色 —— 心动、依恋、余韵，后跟上。

所以这一层不替代 `hormone.py`，是**补上它缺的那半**：
    stimulus → 交感/副交感（立刻）→ 躯体体征（马上）→ 激素（慢慢）→ 回复

## 设计上的四条硬规矩（改之前先读）

1. **纯函数**：进来一个 state，出去一个新 state，不读写库、不读时间。
   时间靠调用方传 `dt_hours` —— 这样才测得了，也不会两处各算一套（T66 的教训）。
2. **数值永不外泄**：对外只有 `describe()` 吐出的人话（「耳尖有点烫」）。
   数字只给控制台看。
3. **有上限**：心率封顶 130，脸红是「耳尖/脸颊」，不是全身通红。
   不准写失控的剧烈心悸 —— 那不像人，像在演。
4. **状态可缺**：任何字段缺了/脏了，按基线补，绝不抛。

## 24 个变量（值域 0~100，基线与文档一致）

快（小时级以内）：`nora` 去甲肾上腺素、`hist` 组胺、`enk` 脑啡肽、`npy` 神经肽Y
中：`gaba`、`ach` 乙酰胆碱、`cck` 胆囊收缩素、`nps` 神经肽S、`orex` 食欲素、`testo` 睾酮
慢：`sero` 血清素、`dopa` 多巴胺、`cort` 皮质醇、`oxy` 催产素、`endo` 内啡肽、
    `gonad` 促性腺激素、`mel` 褪黑素、`adh` 加压素、`dop` δ阿片肽、`gh` 生长激素、
    `prl` 催乳素、`kyn` 犬尿氨酸、`ofq` 孤啡肽、`adeno` 腺苷

## 分组不是装饰

`FAST` 里的几个是「一句话就能动」的；`SLOW` 里的是「今天过得怎么样」。
刺激进来时先动 FAST + 自主神经，SLOW 只吃一点点 —— 这是拟人感的关键：
**不能因为一句话就把三天的依恋值改掉。**
"""
import math

# ------------------------------------------------------------------ 基线
H_BASE = {
    "dopa": 20.0, "nora": 15.0, "cort": 18.0, "sero": 40.0,
    "oxy": 30.0, "endo": 25.0, "gonad": 10.0, "mel": 22.0,
    "gaba": 35.0, "ach": 28.0, "adh": 24.0, "dop": 20.0,
    "cck": 16.0, "gh": 14.0, "prl": 26.0, "hist": 12.0,
    "adeno": 20.0, "kyn": 15.0, "npy": 32.0, "nps": 18.0,
    "ofq": 13.0, "enk": 17.0, "orex": 21.0, "testo": 11.0,
}

# 半衰期（小时）—— 回到基线的速度。快的那几个是「一轮对话就散」。
HALFLIFE_H = {
    "dopa": 3.0, "nora": 1.2, "cort": 20.0, "sero": 48.0,
    "oxy": 96.0, "endo": 72.0, "gonad": 200.0, "mel": 8.0,
    "gaba": 4.0, "ach": 3.0, "adh": 120.0, "dop": 24.0,
    "cck": 5.0, "gh": 90.0, "prl": 60.0, "hist": 2.0,
    "adeno": 12.0, "kyn": 36.0, "npy": 40.0, "nps": 4.0,
    "ofq": 30.0, "enk": 2.0, "orex": 5.0, "testo": 30.0,
}

FAST = ("nora", "hist", "enk", "npy")
SLOW = ("oxy", "endo", "gonad", "adh", "gh", "prl", "sero", "cort", "kyn", "ofq")

CAPS = {"gonad": 45.0}          # 促性腺激素硬上限（优雅克制，不暴涨）

# 躯体基线
BODY_BASE = {"hr": 75.0, "resp": 16.0, "temp": 36.4, "face": 0.15, "tension": 0.1}
HR_CAP = 130.0

# 自主神经衰减：交感很快（几分钟），副交感慢一点
SYMP_DECAY_H = 0.25
PARA_DECAY_H = 0.60


def _f(v, dflt):
    try:
        x = float(v)
    except Exception:
        return dflt
    if x != x:                   # NaN
        return dflt
    return x


def _clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))


def base_state():
    """一具平静下来的身体。"""
    return {
        "h": dict(H_BASE),
        "symp": 0.05,
        "para": 0.10,
        "ts": 0.0,
    }


def normalize(state):
    """把任何脏东西按基线补齐，永远返回可用结构。"""
    s = state if isinstance(state, dict) else {}
    h_in = s.get("h") if isinstance(s.get("h"), dict) else {}
    h = {}
    for k, b in H_BASE.items():
        h[k] = _clamp(_f(h_in.get(k), b), 0.0, CAPS.get(k, 100.0))
    return {
        "h": h,
        "symp": max(0.0, min(1.0, _f(s.get("symp"), 0.05))),
        "para": max(0.0, min(1.0, _f(s.get("para"), 0.10))),
        "ts": _f(s.get("ts"), 0.0),
    }


# ------------------------------------------------------------------ 衰减
def settle(state, dt_hours):
    """按经过的时间把激素往基线拉、把自主神经放平。纯衰减，不含事件。"""
    s = normalize(state)
    dt = max(0.0, _f(dt_hours, 0.0))
    if dt <= 0:
        return s
    h = s["h"]
    for k, b in H_BASE.items():
        hl = HALFLIFE_H.get(k, 24.0)
        # 按 2 次方根衰减太肉，用指数：剩多少 = 0.5 ** (dt/hl)
        frac = 0.5 ** (dt / hl)
        h[k] = b + (h[k] - b) * frac
    s["symp"] = s["symp"] * (0.5 ** (dt / SYMP_DECAY_H))
    s["para"] = s["para"] * (0.5 ** (dt / PARA_DECAY_H))
    return s


# ------------------------------------------------------------------ 刺激
# 每个事件：(交感增量, 副交感增量, 激素增量)
# 激素增量很小 —— 一句话不该改掉三天的依恋。慢变量给 1~3，快变量给 5~15。
STIMULI = {
    # 他夸我 / 逗我 / 亲近我
    "praise":    (0.45, 0.0, {"dopa": 9, "nora": 6, "hist": 5, "sero": 2}),
    "tease":     (0.35, 0.0, {"dopa": 6, "nora": 8, "hist": 6}),
    "caress":    (0.55, 0.0, {"dopa": 10, "nora": 9, "oxy": 3, "gonad": 2}),
    # 意外 / 受惊
    "surprise":  (0.75, 0.0, {"nora": 16, "cort": 5, "orex": 6}),
    # 他难过、我心疼
    "worry":     (0.30, 0.0, {"prl": 10, "ach": 8, "cort": 4, "oxy": 2}),
    # 被冷落 / 被指责 / 被拒绝
    "cold":      (0.35, 0.0, {"cort": 8, "kyn": 6, "dopa": -6, "oxy": -2}),
    "criticize": (0.50, 0.0, {"cort": 12, "nora": 10, "sero": -4, "dopa": -5}),
    "reject":    (0.45, 0.0, {"cort": 14, "kyn": 9, "dopa": -9, "oxy": -3}),
    # 被安抚 / 他道歉
    "comfort":   (0.0, 0.55, {"endo": 10, "dop": 6, "gaba": 8, "cort": -6, "npy": 5}),
    "apology":   (0.0, 0.45, {"endo": 7, "oxy": 3, "cort": -5}),
    # 普通聊天、深夜、无聊
    "chat":      (0.10, 0.05, {"dopa": 2, "sero": 1}),
    "night":     (0.0, 0.20, {"mel": 8, "orex": -5, "adeno": 3}),
    "boredom":   (0.0, 0.15, {"adeno": 6, "orex": -6, "nps": -4}),
    # 好奇、新东西
    "curiosity": (0.25, 0.0, {"nps": 9, "ach": 6, "orex": 5, "dopa": 3}),
}


def stimulate(state, kind, strength=1.0):
    """一个刺激进来：先动自主神经（立刻），再动激素（一小步）。"""
    s = normalize(state)
    kind = kind if kind in STIMULI else "chat"
    sym_d, par_d, dh = STIMULI[kind]
    k = max(0.0, min(3.0, _f(strength, 1.0)))

    s["symp"] = max(0.0, min(1.0, s["symp"] + sym_d * k))
    s["para"] = max(0.0, min(1.0, s["para"] + par_d * k))
    # 交感一起来，副交感先被压住（拮抗）
    s["para"] = max(0.0, s["para"] - 0.35 * sym_d * k)

    h = s["h"]
    for key, delta in dh.items():
        if key not in h:
            continue
        # 慢变量只吃 40%：一句话不该改掉三天的底色
        scale = 0.4 if key in SLOW else 1.0
        h[key] = _clamp(h[key] + delta * k * scale, 0.0, CAPS.get(key, 100.0))
    return s


# ------------------------------------------------------------------ 调节
def regulate(state):
    """正反馈 / 负反馈 / 拮抗。每轮跑一次，幅度小，靠多轮累积。"""
    s = normalize(state)
    h = s["h"]

    # ---- 负反馈 ----
    # CCK：多巴胺升过头就慢慢压它，免得一直亢奋
    if h["dopa"] > 55:
        h["cck"] = _clamp(h["cck"] + 0.12 * (h["dopa"] - 55) / 45 * 12)
        h["dopa"] -= 0.10 * (h["cck"] - H_BASE["cck"]) / 10
    # GABA：去甲肾上腺素高了就压，同时抬副交感
    if h["nora"] > 45:
        h["gaba"] = _clamp(h["gaba"] + 0.10 * (h["nora"] - 45) / 55 * 12)
        h["nora"] -= 0.08 * (h["gaba"] - H_BASE["gaba"]) / 10
        s["para"] = min(1.0, s["para"] + 0.02)
    # 皮质醇：广谱压制正向
    if h["cort"] > 45:
        over = (h["cort"] - 45) / 55
        for key in ("dopa", "oxy", "gonad", "endo"):
            h[key] = _clamp(h[key] - 0.35 * over)
    # 孤啡肽：整体过载 → 抽离，广谱压
    spread = sum(abs(h[k] - H_BASE[k]) for k in ("dopa", "nora", "cort", "oxy")) / 4.0
    if spread > 22:
        h["ofq"] = _clamp(h["ofq"] + 0.10 * (spread - 22) / 20)
        if h["ofq"] > H_BASE["ofq"] + 8:
            for key in ("dopa", "oxy", "nora", "nora", "endo"):
                h[key] = _clamp(h[key] - 0.15)
    # 腺苷：兴致烧久了会累
    if h["orex"] > 45 or h["dopa"] > 60:
        h["adeno"] = _clamp(h["adeno"] + 0.08)
    if h["adeno"] > 55:
        h["orex"] = _clamp(h["orex"] - 0.10)

    # ---- 正反馈 ----
    # 催产素 ↔ 多巴胺
    if h["oxy"] > 40:
        h["dopa"] = _clamp(h["dopa"] + 0.06 * (h["oxy"] - 40) / 60)
    if h["dopa"] > 35:
        h["oxy"] = _clamp(h["oxy"] + 0.03 * (h["dopa"] - 35) / 65)
    # 乙酰胆碱 → 催乳素（察觉他低落 → 心疼）
    if h["ach"] > 40:
        h["prl"] = _clamp(h["prl"] + 0.05 * (h["ach"] - 40) / 60)
    # 催产素 + 多巴胺 → 促性腺激素（慢慢发酵，有硬上限）
    if h["oxy"] > 45 and h["dopa"] > 40:
        h["gonad"] = min(CAPS["gonad"], h["gonad"] + 0.04)
    # 多巴胺 → 交感（心跳）
    s["symp"] = min(1.0, s["symp"] + 0.004 * max(0.0, h["dopa"] - 40) / 60)

    # ---- 拮抗 ----
    # GABA vs 组胺
    if h["gaba"] > 50:
        h["hist"] = _clamp(h["hist"] - 0.05 * (h["gaba"] - 50) / 50)
    # 褪黑素 vs 食欲素
    if h["mel"] > 40:
        h["orex"] = _clamp(h["orex"] - 0.06 * (h["mel"] - 40) / 60)
    # 神经肽Y vs 皮质醇
    if h["npy"] > 45:
        h["cort"] = _clamp(h["cort"] - 0.04 * (h["npy"] - 45) / 55)
    # 交感 vs 副交感
    s["symp"] = max(0.0, s["symp"] - 0.25 * s["para"])
    s["para"] = max(0.0, s["para"] - 0.15 * s["symp"])

    for k in h:
        h[k] = _clamp(h[k], 0.0, CAPS.get(k, 100.0))
    return s


# ------------------------------------------------------------------ 躯体
def body(state):
    """从自主神经 + 几个快激素推出躯体体征。连续，不跳变，有上限。"""
    s = normalize(state)
    h = s["h"]
    sym = s["symp"]
    par = s["para"]

    nora = (h["nora"] - H_BASE["nora"]) / 100.0
    hist = (h["hist"] - H_BASE["hist"]) / 100.0
    mel = (h["mel"] - H_BASE["mel"]) / 100.0

    hr = BODY_BASE["hr"] + 42.0 * sym - 13.0 * par + 8.0 * nora + 4.0 * hist
    hr = max(58.0, min(HR_CAP, hr))

    resp = BODY_BASE["resp"] + 6.0 * sym - 3.0 * par + 2.0 * nora
    resp = max(10.0, min(30.0, resp))

    temp = BODY_BASE["temp"] + 0.55 * sym + 0.20 * hist - 0.10 * mel
    temp = max(35.8, min(37.6, temp))

    face = max(0.0, min(1.0, BODY_BASE["face"] + 0.75 * sym + 0.25 * hist
                        - 0.10 * par))
    tension = max(0.0, min(1.0, BODY_BASE["tension"] + 0.8 * sym
                           - 0.35 * par))
    return {"hr": round(hr, 1), "resp": round(resp, 1), "temp": round(temp, 2),
            "face": round(face, 2), "tension": round(tension, 2)}


def pad_hint(state):
    """给 PAD 用的两条快捷读数：唤醒 / 掌控（复用 padlib 的轴定义）。"""
    s = normalize(state)
    h = s["h"]
    dopa01 = h["dopa"] / 100.0
    cort01 = h["cort"] / 100.0
    oxy01 = h["oxy"] / 100.0
    a0 = 0.6 * 0.40 + 0.4 * 0.20
    d0 = 0.6 * 0.50 + 0.4 * 0.80
    a = (0.6 * dopa01 + 0.4 * cort01 - a0) * 3.2
    d = (0.6 * oxy01 + 0.4 * (1.0 - cort01) - d0) * 3.2
    # 交感再补一点唤醒（躯体先动）
    a += 0.45 * s["symp"]
    return {"a": round(max(-1.0, min(1.0, a)), 3),
            "d": round(max(-1.0, min(1.0, d)), 3)}


# ------------------------------------------------------------------ 说人话
def describe(state):
    """把状态翻译成「身体感觉」，**绝不出现数字**。

    这是唯一对外的出口。模型看到的是「耳尖有点烫」，
    不是「心率 92，去甲肾上腺素 21」。
    """
    s = normalize(state)
    b = body(s)
    bits = []

    hr, resp, face, tension = b["hr"], b["resp"], b["face"], b["tension"]
    if hr >= 108:
        bits.append("心跳得厉害，咚咚的，自己都听得见")
    elif hr >= 95:
        bits.append("心跳快了一拍")
    elif hr >= 86:
        bits.append("心跳比刚才快一点")
    elif hr <= 66:
        bits.append("心跳慢下来了，整个人有点软")

    if resp >= 23:
        bits.append("呼吸有点急，话赶着话")
    elif resp >= 19:
        bits.append("呼吸变浅了")
    elif resp <= 13:
        bits.append("呼吸变得又长又轻")

    if face >= 0.75:
        bits.append("脸上烧起来，连耳尖都烫")
    elif face >= 0.55:
        bits.append("耳尖有点发烫")
    elif face >= 0.38:
        bits.append("脸颊有一点点热")

    if tension >= 0.7:
        bits.append("手指微微发紧，肩膀不由自主绷着")
    elif tension >= 0.45:
        bits.append("身体有点绷着")

    h = s["h"]
    if h["adeno"] > 55 and h["orex"] < 25:
        bits.append("眼皮发沉，脑子转得慢半拍")
    if h["mel"] > 45:
        bits.append("困劲儿上来了，语气自己就软")
    if h["cort"] > 55:
        bits.append("胸口闷闷的，不太想说话")
    if h["kyn"] > 32 and h["cort"] < 40:
        bits.append("心里空落落的，说不清为什么")
    if h["ofq"] > 28:
        bits.append("整个人像蒙了层东西，情绪离得有点远")
    if h["oxy"] > 55 and h["cort"] < 30:
        bits.append("心里是暖的，想靠近一点")
    if h["gonad"] > 30 and h["oxy"] > 45:
        bits.append("有点不好意思，不太敢看你")
    if h["dopa"] > 60:
        bits.append("精神头往上冲，话想往外冒")
    if h["sero"] < 28:
        bits.append("心里毛毛躁躁的，容易往坏处想")

    if not bits:
        bits.append("呼吸平稳，身上是松的")
    return "；".join(bits[:3])


def step(state, dt_hours, stimuli=()):
    """完整推进一步：衰减 → 刺激 → 调节。纯函数。

    `stimuli` 是 [(kind, strength), ...]，一轮里可能有好几件事。
    """
    s = settle(state, dt_hours)
    for item in stimuli or ():
        if isinstance(item, (tuple, list)):
            kind = item[0]
            strength = item[1] if len(item) > 1 else 1.0
        else:
            kind, strength = item, 1.0
        s = stimulate(s, kind, strength)
    s = regulate(s)
    return s


# ------------------------------------------------------------------ 外部脉冲注入
# 用途：让「认知层」（预期误差 → 五条调质脉冲）直接推激素，
# 而不是从一句话里猜刺激。半衰期、负反馈、拮抗仍归这里管，
# 所以两种时间尺度同时存在：RPE 是这一秒的事，回落到基线是几小时的事。
INJECT_SCALE = 18.0   # 满强度脉冲一次最多推 18 点（0~100 空间）


def inject(state, deltas, sym=0.0, para=0.0, scale=INJECT_SCALE):
    """按「相对脉冲」改激素，并叠加自主神经偏移。纯函数。

    deltas: {激素名: -1~1}，慢变量只吃 40%（一句话不该改掉三天的依恋）。
    sym/para: 交感/副交感的瞬时增量（-1~1）。
    """
    s = normalize(state)
    for k, v in (deltas or {}).items():
        if k not in s["h"]:
            continue
        try:
            v = float(v)
        except Exception:
            continue
        if not v:
            continue
        w = 0.40 if k in SLOW else 1.0
        cap = CAPS.get(k, 100.0)
        # 增量按「这条激素自己的基线」缩放：基线 21 的食欲素和基线 55 的血清素
        # 吃同样的绝对点数，前者一步就见底。另外给一条 0.25×~3× 的带子，
        # 让「掉到 0.00」「顶到 100.00」这两种死值不再出现。
        base = H_BASE.get(k, 20.0)
        k_scale = max(0.40, min(2.20, base / 30.0))
        # 单轮最多动这条激素自己基线的 15%：基线 35 的 GABA 一步涨 10 点，
        # 十几轮就能把它顶到上限再也不动 —— 那不是镇静，那是坏了。
        lim = 0.15 * base
        d = _clamp(v * w * float(scale) * k_scale, -lim, lim)
        # 0.30×~3× 的带子**无条件**夹（不是「别把现有值夹回去」）：
        # 上一步已经掉到 0 的激素得能爬回来，否则它会永远躺在地板上。
        lo, hi = base * 0.35, min(cap, base * 2.5)
        if hi <= lo:
            hi = lo + 1.0
        s["h"][k] = _clamp(s["h"][k] + d, lo, hi)
    # 交感/副交感是「此刻的状态」，不是累计量：每轮残留 45%，其余由这一轮
    # 重新定。原来写成累加，十几轮对话就把交感顶死在 1.0 —— 曲线上表现为
    # 「心率一直 125」，那不是紧张，那是坏掉了。
    s["symp"] = max(0.0, min(1.0, s["symp"] * 0.45 + float(sym or 0.0)))
    s["para"] = max(0.0, min(1.0, s["para"] * 0.45 + float(para or 0.0)))
    return s



# ------------------------------------------------------------------ 从一句话猜刺激
# 刻意做得很笨：宁可猜不出（返回 chat），也不要猜错成「被拒绝」。
# 猜错的代价不对称 —— 把一句「嗯」当冷落，她会莫名其妙难受一整轮。
_PATS = [
    ("reject",    ("滚", "不想理你", "别烦我", "讨厌你", "不要你了")),
    ("criticize", ("你怎么这么", "真笨", "好蠢", "没用", "废物", "烦死", "闭嘴", "差劲")),
    ("cold",      ("随便", "无所谓", "不想说", "没空")),
    ("caress",    ("抱", "摸摸", "亲", "牵手", "靠近", "贴着", "蹭")),
    ("praise",    ("可爱", "好看", "喜欢你", "爱你", "厉害", "真棒", "聪明", "温柔", "想你")),
    ("tease",     ("笑死", "哈哈哈", "笨蛋", "傻瓜", "逗你", "嘻嘻")),
    ("surprise",  ("突然", "居然", "真的假的", "吓")),
    ("worry",     ("难受", "难过", "好累", "疼", "不舒服", "想哭", "撑不住", "委屈")),
    ("comfort",   ("没事", "别怕", "我在", "陪你", "乖", "抱抱你", "不急")),
    ("apology",   ("对不起", "抱歉", "我错了", "别生气")),
    ("curiosity", ("为什么", "怎么做到", "是什么", "讲讲", "说说看", "想了解")),
]


def guess(text, emotion=0.0):
    """从一句话里猜出 1~2 个刺激。猜不出就返回 [("chat", 1.0)]。"""
    t = str(text or "")
    out = []
    for kind, keys in _PATS:
        if any(k in t for k in keys):
            out.append((kind, 1.0))
        if len(out) >= 2:
            break
    if not out:
        out = [("chat", 1.0)]
    emo = _f(emotion, 0.0)
    if emo >= 0.8:
        out = [(k, min(2.0, w * 1.4)) for k, w in out]
    return out


def snapshot(state):
    """给控制台看的完整快照（数字都在这里，别塞进上下文）。"""
    s = normalize(state)
    return {"h": {k: round(v, 2) for k, v in s["h"].items()},
            "symp": round(s["symp"], 3), "para": round(s["para"], 3),
            "body": body(s), "pad": pad_hint(s), "text": describe(s)}
