# -*- coding: utf-8 -*-
"""T3-3 六维动态上下文权重（V5.0 §10）

为什么要有这一层：
    V4.1 把四格预算写死成 600 / 1800 / 2000 / 1200。
    结果是聊技术的时候人格格占着 600 没用上，
    执行技能的时候技能格只有 1200 不够用 —— 静态预算 = 呆板。

    这一层做的事只有一件：**总长固定，配额流动**。
    六个维度按「现在在聊什么 + 我现在什么状态」动态分那 6000 token。

六维（V5.0 §10.2）：
    A 身份内核（人格 + 身份锚 + 底线）     永不裁剪，A >= 0.15
    B 自我认知（我是谁、性格、处境）       半稳定
    C 短时记忆（最近 N 条原始对话）         高频变动
    D 陈述记忆（长期池的事实/偏好/习惯）    低频变动
    E 印象记忆（对他的看法、依恋）          慢变动
    F 技能记忆（命中的程序性记忆）          按需

四格映射（V5.0 §10.5）：
    格 1【我是谁】           = A + B
    格 2【我一直记得的事】   = D
    格 3【我们最近在聊】     = C
    格 4【这句话让我想起了】 = E + F

    为什么 E、F 合并：两者都是「本轮被激活的东西」，
    一个是「想起了对他的看法」，一个是「想起了怎么做事」，
    放同一格里模型读起来是一段连续的「我此刻想到的」。
"""

import json
import os
# ---- 六维下标 ----
A, B, C, D, E, F = 0, 1, 2, 3, 4, 5
DIM_NAMES = ["身份内核", "自我认知", "短时记忆", "陈述记忆", "印象记忆", "技能记忆"]

A_MIN = 0.15          # A 的硬保底（V5.0 §10.2）
CELL_MIN = 150        # 每格最低 token（V5.0 §10.4 第 4 步）

# 总预算也变成可调的（2026-11-05 用户 m15228 要求"控制台里要能调"）。
# 以前它是 card2.TOKEN_BUDGET 一个写死的常量 —— 而它恰恰是「这套东西到底花
# 多少上下文」的总闸门，比那七个场景权重更该露在外面。
BUDGET_DEFAULT = 6000
BUDGET_MIN = 1000     # 再低四格保底（4×150）就装不下了
# 上限放到 20 万（2026-11-05 用户：要能适配不同窗口的 API 模型）。
# 原来卡在 60000 —— 那对 128K 窗口的模型连一半都用不满，"适配"就无从谈起。
# 它只是个上限，不是默认值；真往上调要自己知道在干什么（上下文越大越贵、
# 越容易把眼前那句话淹掉），所以默认仍是 6000。
BUDGET_MAX = 200000

# ---- 模型窗口对照表（2026-11-05 用户要"哪种模型大概配多少"）----
# 这些是**常见公开值**，不是从谁那儿抄来的精确参数表 —— 同一个名字不同家
# 的窗口也可能不一样（比如各家都叫 "4o"）。所以它只用来**给个起点**，
# 真值以你自己那家的文档为准；改坏了随时能手动改回去。
#
# 第三列是"建议记忆预算"：不是占满窗口，而是**只给记忆的那一份**。
# 上下文里还有系统提示、工具定义、最近十轮对话、这次要回的这句话本身 ——
# 记忆占满窗口，等于把对话挤出去，人还没开口就已经没地方说话了。
# 经验比例取窗口的 8%~15%，下限 6000（这是现在跑得好好的默认值，
# 别为了"用满窗口"把它往下调），上限 32000（再多就不是记忆是档案了）。
MODEL_WINDOWS = [
    # (匹配用的关键字, 窗口 token, 备注名)
    ("gemini",      1000000, "1M"),
    ("gpt-5",        400000, "400K"),
    ("claude",       200000, "200K"),
    ("glm",          200000, "200K"),
    ("deepseek",     128000, "128K"),
    ("qwen",         128000, "128K"),
    ("mimo",         128000, "128K"),
    ("hy",           128000, "128K"),
    ("llama",        128000, "128K"),
    ("gpt-4",        128000, "128K"),
    ("o1",           200000, "200K"),
    ("o3",           200000, "200K"),
]
WINDOW_FALLBACK = 32000        # 认不出来就按最保守的算
SUGGEST_RATIO = 0.12           # 记忆预算占窗口的比例
SUGGEST_MIN = BUDGET_DEFAULT   # 6000：比这还小就别怪她记不住事
SUGGEST_MAX = 32000


def window_of(model):
    """这个模型大概多少 token 窗口。认不出来返回保守值（不是 0，也不是瞎猜大）。"""
    m = (model or "").strip().lower()
    if not m:
        return WINDOW_FALLBACK, ""
    for key, win, label in MODEL_WINDOWS:
        if key in m:
            return win, label
    return WINDOW_FALLBACK, ""


def suggest_budget(model_or_window, is_window=False):
    """按模型（或直接给窗口大小）建议一个记忆预算。"""
    if is_window:
        win = int(model_or_window or 0)
    else:
        win, _ = window_of(model_or_window)
    if win <= 0:
        win = WINDOW_FALLBACK
    v = int(win * SUGGEST_RATIO)
    v = max(SUGGEST_MIN, min(SUGGEST_MAX, v))
    v = max(BUDGET_MIN, min(BUDGET_MAX, v))
    # 取整到百位，看着不脏（15400 而不是 15360）
    return int(round(v / 100.0)) * 100


def model_table():
    """给控制台用的对照表（含认不出来那一行）。"""
    rows = []
    for key, win, label in MODEL_WINDOWS:
        rows.append({"match": key, "window": win, "label": label,
                     "suggest": suggest_budget(win, is_window=True)})
    rows.append({"match": "(认不出来)", "window": WINDOW_FALLBACK,
                 "label": "保守",
                 "suggest": suggest_budget(WINDOW_FALLBACK, is_window=True)})
    return rows


# ---- 自动模式：读 DSH 自己的用量账本，看上一轮用的是哪个模型 ----
# 为什么不问环境变量：跑在后台的 mind_server 是另一个进程，宿主换了模型
# 它不知道。而 DSH 每次请求都会往 ~/.dsh/.dshw-usage.json 的 events 里记一笔
# （带 model 名），那是**事实记录**不是推测。读不到就当"认不出来"。
_USAGE_PATH = os.path.join(
    os.path.expanduser("~"), ".dsh", ".dshw-usage.json")


def detect_model(usage_path=None):
    """最近一次真实请求用的模型名。读不到返回 ""。"""
    p = usage_path or _USAGE_PATH
    try:
        with open(p, "r", encoding="utf-8") as f:
            u = json.load(f)
        ev = u.get("events") or []
        for e in reversed(ev):
            m = (e or {}).get("model") or ""
            if m and not m.startswith("auto"):
                return m
        for e in reversed(ev):
            if (e or {}).get("model"):
                return e["model"]
    except Exception:
        pass
    return ""


# ---- 七张场景权重表（V5.0 §10.3）----
SCENE_WEIGHTS = {
    "普通对话": [0.20, 0.18, 0.22, 0.15, 0.15, 0.10],
    "情绪陪伴": [0.18, 0.16, 0.26, 0.10, 0.24, 0.06],
    "图像观察": [0.18, 0.15, 0.30, 0.12, 0.15, 0.10],
    "技能执行": [0.15, 0.10, 0.10, 0.15, 0.10, 0.40],
    "事实查询": [0.16, 0.12, 0.14, 0.34, 0.10, 0.14],
    "深度回忆": [0.18, 0.14, 0.18, 0.22, 0.20, 0.08],
    "冲突争执": [0.22, 0.20, 0.24, 0.08, 0.20, 0.06],
}

# ---- 运行时热改（V5.0 §10.9）----
#
# 为什么要有这一层：
#     这张场景权重表是**调参**的东西 —— 一定得能现场试、现场改。
#     写死在代码里，改一个数就要重启服务，那等于没人会去改它。
#
# 存在哪：卡里的 runtime_config 表（跟记忆同一个库，拔卡跟着走）。
#     为什么不放 config.json：多一个文件就多一处能跟库不同步的地方，
#     而「拔卡带走」这件事只有一个真值源才不会出错。
#
# 怎么生效：每轮 compute() 先读一行 `_rev`（单行主键查询，微秒级），
#     跟缓存里的 rev 不一样就重载全表。所以改完**下一轮立即生效**，不用重启。
RC_TABLE = "runtime_config"
RC_REV_KEY = "_rev"

# 英文别名 → 中文场景名（文档 §10.9 举的例子用的是 emotion 这种写法）
SCENE_ALIAS = {
    "normal": "普通对话", "plain": "普通对话", "chat": "普通对话",
    "emotion": "情绪陪伴", "emo": "情绪陪伴", "comfort": "情绪陪伴",
    "image": "图像观察", "vision": "图像观察",
    "skill": "技能执行", "exec": "技能执行",
    "fact": "事实查询", "query": "事实查询",
    "recall": "深度回忆", "memory": "深度回忆",
    "conflict": "冲突争执", "quarrel": "冲突争执",
}

_DEFAULT_CFG = {
    "scene_weights": dict((k, list(v)) for k, v in SCENE_WEIGHTS.items()),
    "a_min": A_MIN,
    "cell_min": CELL_MIN,
    "budget": BUDGET_DEFAULT,
    "bad": [],          # 解析失败的键（写错了要能看见，不能静默用默认值）
}

# 缓存：id(db) → (rev, cfg, db 强引用)
# 强引用是为了不让 id() 被回收后重用 —— 两个不同的库撞同一个 id 会读到别人的配置。
_CFG_CACHE = {}


def _parse_six(s):
    """把 "0.18,0.16,0.26,0.10,0.24,0.06" 解析成 6 个非负 float。

    不合格就返回 None（调用方保留默认值并记进 bad）—— 宁可这一项没改，
    也不能让一个手滑的逗号把某一维权重变成 0 然后整格消失。
    """
    try:
        parts = [x for x in str(s).replace("，", ",").split(",") if x.strip()]
        if len(parts) != 6:
            return None
        vals = [float(x) for x in parts]
        if any(v < 0 or v != v or v > 1.0 for v in vals):
            return None
        if sum(vals) <= 0:
            return None
        return vals
    except Exception:
        return None


def _parse_num(s):
    """单个标量参数。空 / 非数 / nan / inf 一律 None（调用方记 bad 并保留默认）。"""
    try:
        v = float(str(s).strip())
    except Exception:
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


def ensure_table(db):
    """建 runtime_config 表（老库没有就补）。"""
    try:
        db.execute(
            "CREATE TABLE IF NOT EXISTS runtime_config("
            " key TEXT PRIMARY KEY, val TEXT NOT NULL,"
            " ts REAL NOT NULL DEFAULT 0)")
        db.commit()
        return True
    except Exception:
        return False


def rc_set(db, key, val, ts=None):
    """写一条配置并 bump `_rev`（这样下一轮 compute 就会重载）。"""
    import time as _time
    t = float(ts if ts is not None else _time.time())
    c = db.cursor()
    c.execute("INSERT INTO runtime_config(key,val,ts) VALUES(?,?,?)"
              " ON CONFLICT(key) DO UPDATE SET val=excluded.val, ts=excluded.ts",
              (str(key), str(val), t))
    c.execute("INSERT INTO runtime_config(key,val,ts) VALUES(?,?,?)"
              " ON CONFLICT(key) DO UPDATE SET val=excluded.val, ts=excluded.ts",
              (RC_REV_KEY, "%.6f" % t, t))
    db.commit()
    return t


def rc_all(db):
    """把整张表读成 dict（不含 _rev）。"""
    out = {}
    try:
        for r in db.execute("SELECT key, val FROM runtime_config"):
            out[str(r[0])] = str(r[1])
    except Exception:
        pass
    out.pop(RC_REV_KEY, None)
    return out


def load_config(db=None, force=False):
    """读运行时配置；没有库或读不了就返回默认值。

    每次只多一条 `_rev` 主键查询。rev 没变就直接吃缓存。
    """
    if db is None:
        return _DEFAULT_CFG
    try:
        row = db.execute("SELECT val FROM runtime_config WHERE key=?",
                         (RC_REV_KEY,)).fetchone()
    except Exception:
        return _DEFAULT_CFG          # 老库还没这张表
    rev = str(row[0]) if row is not None else ""
    hit = _CFG_CACHE.get(id(db))
    if hit is not None and hit[0] == rev and not force:
        return hit[1]

    cfg = {
        "scene_weights": dict((k, list(v)) for k, v in SCENE_WEIGHTS.items()),
        "a_min": A_MIN,
        "cell_min": CELL_MIN,
        "budget": BUDGET_DEFAULT,
        "auto": False,      # 自动跟随模型（BUDGET_AUTO=1 打开）
        "bad": [],
    }
    for key, val in rc_all(db).items():
        k = key.strip()
        if k.startswith("scene_weight:"):
            name = k.split(":", 1)[1].strip()
            name = SCENE_ALIAS.get(name.lower(), name)
            six = _parse_six(val)
            if six is None or name not in SCENE_WEIGHTS:
                cfg["bad"].append("%s=%s" % (key, val))
                continue
            cfg["scene_weights"][name] = six
        elif k == "A_MIN":
            v = _parse_num(val)
            # 上限 0.80：A 保底再高，其余五维加起来不够分，归一之后等于没设
            if v is None or not (0.0 <= v <= 0.80):
                cfg["bad"].append("%s=%s" % (key, val))
                continue
            cfg["a_min"] = v
        elif k == "CELL_MIN":
            v = _parse_num(val)
            if v is None or not (0 <= v <= 4000):
                cfg["bad"].append("%s=%s" % (key, val))
                continue
            cfg["cell_min"] = int(v)
        elif k == "BUDGET":
            v = _parse_num(val)
            if v is None or not (BUDGET_MIN <= v <= BUDGET_MAX):
                cfg["bad"].append("%s=%s" % (key, val))
                continue
            cfg["budget"] = int(v)
        elif k == "BUDGET_AUTO":
            cfg["auto"] = str(val).strip() not in ("", "0", "false", "False", "no")
        # 认不出的键不报错也不生效 —— 留着给以后扩展用
    # 自动模式下的模型 / 建议值**不缓存在这里** —— cfg 是按 _rev 缓存的，
    # 而换模型不会 bump _rev，缓存住就会一直是换之前那个模型。
    # 真正的读取放在 auto_now() 里，每轮现读（读一个本地 JSON，很便宜）。
    _CFG_CACHE[id(db)] = (rev, cfg, db)
    return cfg


def auto_now(db=None):
    """自动模式这一刻该用多少预算。返回 {on, model, window, budget, label}。

    `on` 只表示「这个值要不要真用」；识别出来的模型和建议值**无论开没开都填**。
    一开始关着就返回空，结果控制台上写「认不出来」—— 人想问的恰恰是
    「我这会儿接的是哪个模型、它建议多少」，关掉自动不等于不想知道。
    """
    cfg = load_config(db) if db is not None else _DEFAULT_CFG
    m = detect_model()
    win, label = window_of(m)
    return {"on": bool(cfg.get("auto")), "model": m, "window": win,
            "budget": suggest_budget(m), "label": label}


def effective_budget(budget=None, db=None):
    """这一轮到底用多少 token：显式传了听显式的，没传就听卡里的配置。

    为什么优先级是这个方向：`/context` 的 budget 参数是**调用方的一次性覆盖**
    （比如某个客户端这轮要塞长文），而 runtime_config 里的 BUDGET 是
    **这张卡平时的样子**。一次性的东西不该反过来改写长期设定。
    """
    if budget:
        try:
            return max(BUDGET_MIN, min(BUDGET_MAX, int(float(budget))))
        except Exception:
            pass
    if db is not None:
        try:
            # 自动模式优先：它读的是"这一轮真实在用的模型"，比手动那个值更该听
            a = auto_now(db)
            if a["on"] and a["budget"]:
                return int(a["budget"])
            return int(load_config(db).get("budget") or BUDGET_DEFAULT)
        except Exception:
            pass
    return BUDGET_DEFAULT


# ---- 场景判定关键词 ----
import re

_RE_EMO = re.compile(
    r"难受|难过|想哭|哭|委屈|烦|累|孤单|害怕|焦虑|崩溃|压力|"
    r"开心|高兴|喜欢|爱你|想你|抱抱|陪你|陪陪")
_RE_SKILL = re.compile(
    r"怎么做|如何|步骤|操作|帮我做|帮我改|写个|写一个|生成|"
    r"命令|代码|脚本|配置|安装|运行|跑一下|调试|报错|修复")
_RE_FACT = re.compile(
    r"是多少|是什么|叫什么|哪个|什么时候|多少号|QQ号|手机号|"
    r"生日|几点|几天|多久|在哪|在哪个")
_RE_RECALL = re.compile(
    r"还记得|记得吗|以前|那次|那天|当时|曾经|上一次|之前|"
    r"回忆|想起来|说过")
_RE_CONFLICT = re.compile(
    r"你错了|不对|你根本|凭什么|闭嘴|烦人|讨厌你|你骗|敷衍|"
    r"生气|吵架|失望|为什么不")

# ---- 意图修饰（V5.0 §10.4 第 2 条）----
INTENT_RULES = [
    (_RE_RECALL, {D: 1.30, C: 0.85}),        # 问往事 → 多装旧事
    (re.compile(r"你现在|刚刚|刚才|现在"), {C: 1.30}),
    (re.compile(r"你觉得|你怎么看|你认为"), {E: 1.25}),
    (re.compile(r"你会不会|能不能|可不可以"), {F: 1.20}),
]


def classify_scene(query, recent_lines=None):
    """判断现在属于哪个场景。冲突 > 情绪 > 回忆 > 技能 > 事实 > 普通。

    顺序是有意的：吵架时就算问的是「还记得吗」，也该先保人格和底线。
    """
    q = query or ""
    if _RE_CONFLICT.search(q):
        return "冲突争执"
    if _RE_EMO.search(q):
        return "情绪陪伴"
    if _RE_RECALL.search(q):
        return "深度回忆"
    if _RE_SKILL.search(q):
        return "技能执行"
    if _RE_FACT.search(q):
        return "事实查询"
    return "普通对话"


def base_weights(scene, cfg=None):
    cfg = cfg or _DEFAULT_CFG
    table = cfg.get("scene_weights") or SCENE_WEIGHTS
    return list(table.get(scene, table.get("普通对话", SCENE_WEIGHTS["普通对话"])))


def apply_hormones(v, h):
    """内分泌修饰（V5.0 §10.4 第 1 条）

    只改「装多少」，不改「装什么」—— 这是防模型编造的一道闸。
    """
    v = list(v)
    if not h:
        return v
    if h.get("h_oxy", 0.5) > 0.7:
        v[E] *= 1.25
    if h.get("h_cort", 0.2) > 0.5:
        v[D] *= 0.80
    if h.get("h_dopa", 0.4) > 0.7:
        v[C] *= 1.15
    if h.get("h_sero", 0.6) > 0.7:
        v[B] *= 1.10
    return v


def apply_intent(v, query):
    v = list(v)
    q = query or ""
    for rx, deltas in INTENT_RULES:
        if rx.search(q):
            for idx, mul in deltas.items():
                v[idx] *= mul
    return v


def apply_session(v, turns):
    """会话长度修饰（V5.0 §10.4 第 3 条）"""
    v = list(v)
    if turns is not None:
        if turns < 3:
            v[C] *= 1.15
        elif turns > 20:
            v[C] *= 0.90
    return v


def apply_idle(v, idle_seconds):
    """空闲时长修饰（V5.0 §10.4 第 4 条）：久别重逢，先回忆旧事"""
    v = list(v)
    if idle_seconds is not None and idle_seconds > 24 * 3600:
        v[D] *= 1.15
    return v


def normalize(v, a_min=None):
    """钳位 + 归一（V5.0 §10.4 收尾两步）"""
    v = [max(0.0, float(x)) for x in v]
    v[A] = max(v[A], A_MIN if a_min is None else a_min)
    s = sum(v)
    if s <= 0:
        return list(SCENE_WEIGHTS["普通对话"])
    return [x / s for x in v]


def allocate(budget, v, cell_min=None):
    """权重 → 四格 token 配额（V5.0 §10.5 + 保底）

    返回 dict: identity / longterm / recent / recall
    保证四格合计不超过 budget，且每格 >= CELL_MIN（预算够的话）。
    """
    cmin = CELL_MIN if cell_min is None else max(0, int(cell_min))
    raw = {
        "identity": (v[A] + v[B]) * budget,
        "longterm": v[D] * budget,
        "recent": v[C] * budget,
        "recall": (v[E] + v[F]) * budget,
    }
    keys = list(raw.keys())
    # 1) 先保底
    out = {}
    for k in keys:
        out[k] = max(cmin, int(round(raw[k])))
    total = sum(out.values())
    # 2) 超了就从「非保底部分」按比例削
    if total > budget:
        excess = total - budget
        flex = {k: max(0, out[k] - cmin) for k in keys}
        flex_sum = sum(flex.values())
        if flex_sum > 0:
            for k in keys:
                out[k] -= int(round(excess * flex[k] / float(flex_sum)))
    # 3) 还超（保底本身就超）就硬削，从最大的那格削
    total = sum(out.values())
    while total > budget:
        k = max(keys, key=lambda x: out[x])
        if out[k] <= 1:
            break
        out[k] -= 1
        total -= 1
    # 4) 没花完的补给「最近聊的」（真人说话最依赖眼前）
    total = sum(out.values())
    if total < budget:
        out["recent"] += budget - total
    return out


def compute(query, budget=None, hormones=None, turns=None, idle_seconds=None,
            recent_lines=None, db=None, cfg=None):
    """一步到位：算场景 → 套修饰 → 归一 → 分配

    db 传进来时会读卡里的 runtime_config（支持热改，见 §10.9）。
    不传就用代码里的默认表 —— 离线测试 / 没库的场景照旧能跑。
    cfg 传进来则直接用这份配置（控制台"改之前先看一眼会变成什么样"用它，
    不写库、不碰卡，纯算）。

    budget 不传（None）→ 用卡里配的 BUDGET，没配就是 BUDGET_DEFAULT。

    返回 dict：
      scene     —— 判出来的场景名
      weights   —— 归一后的六维权重
      budget    —— 四格配额（**沿用旧名，含义没变**，历史调用方都在用）
      quota     —— 同 budget 的别名（新代码用这个更好读）
      total     —— 这一轮的总预算（以前的"budget"参数，现在能被打配置覆盖）
      trace     —— 人话解释（为什么这么分）
      source    —— "db"（卡里的配置）/ "default" / "inline"（传了 cfg）
    """
    if cfg is None:
        cfg = load_config(db) if db is not None else _DEFAULT_CFG
        source = "db" if db is not None else "default"
    else:
        source = "inline"
    # 预算优先级：显式传的 budget > 这份 cfg 里的 budget > 默认 6000。
    #   ⚠️ 顺序不能反：cfg 是从卡里（或 preview 的 overrides 里）读的**用户设定**，
    #   budget 参数是调用方这一轮的一次性覆盖。一次性的东西压长期设定，
    #   才符合"我就这一轮多塞点"的直觉。
    if budget is not None:
        total_budget = effective_budget(budget, None)
    else:
        # 自动模式优先（读的是这一轮真实用的模型），其次手动值，最后默认。
        #   注意 auto_now 要现读，不能吃 cfg 的缓存 —— 换模型不会 bump _rev。
        _a = {"on": False}
        if source != "inline":
            try:
                _a = auto_now(db)
            except Exception:
                _a = {"on": False}
        if _a.get("on") and _a.get("budget"):
            total_budget = int(_a["budget"])
        else:
            try:
                total_budget = int(cfg.get("budget") or BUDGET_DEFAULT)
            except Exception:
                total_budget = BUDGET_DEFAULT
    total_budget = max(BUDGET_MIN, min(BUDGET_MAX, total_budget))
    scene = classify_scene(query, recent_lines)
    v0 = base_weights(scene, cfg)
    v = apply_hormones(v0, hormones)
    v = apply_intent(v, query)
    v = apply_session(v, turns)
    v = apply_idle(v, idle_seconds)
    v = normalize(v, cfg.get("a_min"))
    b = allocate(total_budget, v, cfg.get("cell_min"))
    trace = "场景「%s」→ 格1 %d / 格2 %d / 格3 %d / 格4 %d（共 %d）" % (
        scene, b["identity"], b["longterm"], b["recent"], b["recall"],
        total_budget)
    return {"scene": scene, "weights": v, "budget": b, "quota": b,
            "total": total_budget, "trace": trace, "source": source}


def preview(query, budget=None, db=None, overrides=None, hormones=None,
            turns=None, idle_seconds=None, recent_lines=None):
    """控制台的实时预览：**不写库、不读记忆、不动 turn_count**。

    它把「现在这张卡的配置」拷一份，套上 overrides（{"scene_weight:普通对话":
    [..], "BUDGET": "8000"} 这种还没保存的值），算一遍给你看。
    所以控制台上改数字、还没点保存的时候，右边就能看到四格怎么变。
    """
    base = load_config(db, force=True) if db is not None else _DEFAULT_CFG
    cfg = {
        "scene_weights": dict((k, list(v))
                              for k, v in base["scene_weights"].items()),
        "a_min": base["a_min"],
        "cell_min": base["cell_min"],
        "budget": base.get("budget") or BUDGET_DEFAULT,
        "auto": bool(base.get("auto")),
        "bad": [],
    }
    _has_budget = False
    for key, val in (overrides or {}).items():
        k = str(key).strip()
        if k.startswith("scene_weight:"):
            name = k.split(":", 1)[1].strip()
            name = SCENE_ALIAS.get(name.lower(), name)
            six = _parse_six(val)
            if six is not None and name in SCENE_WEIGHTS:
                cfg["scene_weights"][name] = six
        elif k == "A_MIN":
            v = _parse_num(val)
            if v is not None and 0.0 <= v <= 0.80:
                cfg["a_min"] = v
        elif k == "CELL_MIN":
            v = _parse_num(val)
            if v is not None and 0 <= v <= 4000:
                cfg["cell_min"] = int(v)
        elif k == "BUDGET":
            v = _parse_num(val)
            if v is not None and BUDGET_MIN <= v <= BUDGET_MAX:
                cfg["budget"] = int(v)
                _has_budget = True
        elif k == "BUDGET_AUTO":
            cfg["auto"] = str(val).strip() not in ("", "0", "false", "False", "no")
    # 自动模式在预览里也要生效，否则"控制台上看到 6000、真跑起来 15400"。
    #   但页面上手动填了 BUDGET 时以手填的为准（人正在改的那一下要看得见）。
    if cfg.get("auto") and not _has_budget:
        cfg["budget"] = suggest_budget(detect_model())
    return compute(query or "", budget=None, hormones=hormones, turns=turns,
                   idle_seconds=idle_seconds, recent_lines=recent_lines,
                   db=None, cfg=cfg)


def weights_line(v):
    """给人看的一行权重"""
    return " ".join("%s=%.2f" % (n, x) for n, x in zip(DIM_NAMES, v))
