# -*- coding: utf-8 -*-
"""
小鲸鱼数字生命卡 · 第一期最小实现
=====================================

只做四件事（对应 V4.0 §20 第一期）：
  1. 记得住     —— 存进 SQLite，带主干 + 细节 + 向量
  2. 忘得对     —— 按 V4.0 §2.2 分层褪色，细节比主干掉得快
  3. 翻得出     —— 余弦相似度检索（暴力，不做图，不做电势）
  4. 答得上     —— 按当前清晰度渲染成一句人话

关键设计：**所有模块不许读系统时间**。时间只能由外部注入（Clock），
这样"隔一天"才能被测试，而不是干等一天。

依赖：只用 Python 标准库。不需要装任何东西。
"""

import json
import math
import re
import sqlite3
import time

DAY = 86400.0

# ============================================================
# 参数（V4.0 §1.2 的子集）
# ============================================================
K_CORE = 1.10e-6        # 主干衰减，半衰期 7.3 天
# 🔧 细节按类型分别衰减：日期最先忘，情绪最后忘（更像人）
K_DETAIL_BY_KIND = {
    "TIME": 4.5e-6,      # 精确日期，最先模糊
    "NUMBER": 4.5e-6,    # 数字同理
    "MODIFIER": 4.0e-6,  # 修饰细节
    "PLACE": 3.2e-6,     # 地点
    "EMOTION": 1.2e-6,   # 情绪，留得最久
}
K_DETAIL_DEFAULT = 4.0e-6
# 长期档案的衰减：半衰期约 535 天（3 年还有 ~0.28）
# 历史对话导入时用它，让记忆按「真实年龄」褪色，而不是今天才录就全新
K_ARCHIVE = 1.5e-8
GAIN_ACCESS = 0.15      # 被访问时的清晰度增益
ACCESS_DIMINISH = 0.30  # 增益边际衰减
CLARITY_GC = 0.01       # 低于此值回收
# 🔧 第一期先用宽 TTL：巩固层还没做，7 天硬删会把还没搬走的记忆直接烧掉。
#    等第二期做完「巩固进陈述记忆」，这里改回 7 * DAY。
SHORT_MAX_TTL = 90 * DAY
SIG_THRESHOLD = 0.40    # 巩固准入线

# 细节渲染分级（V4.0 §2.2）
SHOW_FULL = 0.60        # 完整细节
SHOW_SCENE = 0.30       # 丢精确值，留场景
SHOW_HINT = 0.15        # 只剩情绪物件
KEEP_DETAIL = 0.02      # 低于此值归档


# ============================================================
# 时钟：唯一的时间来源
# ============================================================
class Clock:
    def __init__(self, start=None):
        self._t = float(start if start is not None else time.time())

    def now(self):
        return self._t

    def advance(self, seconds):
        self._t += float(seconds)
        return self._t


def compress_offline_dt(dt_raw):
    """V4.0 §3.3：离线久了不能等比衰减，否则睡一觉就失忆"""
    if dt_raw <= 7 * DAY:
        return dt_raw
    if dt_raw <= 30 * DAY:
        return 7 * DAY + (dt_raw - 7 * DAY) * 0.5
    return min(7 * DAY + 23 * DAY * 0.5 + (dt_raw - 30 * DAY) * 0.15, 90 * DAY)


# ============================================================
# 向量化：字符 bigram + 简易权重（第一期够用，不依赖模型）
# ============================================================
_PUNCT = re.compile(r"[，。！？、；：“”‘’（）\s,.!?;:()\[\]{}]")


def to_vec(text):
    """中文按字 bigram 切成稀疏向量。'我昨天吃烧烤' -> {'我昨':1,'昨天':1,...}"""
    t = _PUNCT.sub("", text or "")
    if not t:
        return {}
    vec = {}
    if len(t) == 1:
        vec[t] = 1.0
        return vec
    for i in range(len(t) - 1):
        bg = t[i:i + 2]
        vec[bg] = vec.get(bg, 0.0) + 1.0
    # 单字也留一点，防止短查询匹配不上
    for ch in t:
        key = ch
        vec[key] = vec.get(key, 0.0) + 0.3
    return vec


def cos_sim(a, b):
    if not a or not b:
        return 0.0
    dot = sum(w * b[k] for k, w in a.items() if k in b)
    na = math.sqrt(sum(w * w for w in a.values()))
    nb = math.sqrt(sum(w * w for w in b.values()))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ============================================================
# 细节拆分：把一句话拆成 主干 + 若干细节单元（V4.0 §4.5）
# ============================================================
# 时间词
RE_TIME = re.compile(
    r"(\d{1,2}\s*月\s*\d{1,2}\s*[日号]|[上下]午\s*\d{1,2}\s*[点时]|"
    r"晚上\s*\d{1,2}\s*[点时]|昨天|今天|前天|后天|明天|上周|这周|那年|去年|"
    r"\d{4}\s*年|\d{1,2}\s*[点时]\s*\d{0,2}\s*分?)"
)
# 数字（整体捕获，注意 group(1) 不能是可选的，否则没小数时返回 None）
RE_NUM = re.compile(r"(\d+(?:\.\d+)?)")
# 情绪词
EMO_WORDS = [
    "很开心", "开心", "高兴", "难受", "难过", "伤心", "累", "困", "饿",
    "生气", "害怕", "紧张", "激动", "委屈", "踏实", "宁静", "幸福",
    "哭了", "笑死", "绷不住", "舒服", "无聊", "烦",
]
# 地点：捕获整段（含介词"在/去/到/从"），用贪婪匹配才能吃到"学校门口的路边摊"
RE_PLACE = re.compile(
    r"((?:在|去|到|从)[^\s，。！？、；：]{1,12}"
    r"(?:门口|路边摊|路边|学校|教室|宿舍|食堂|超市|公园|医院|车站|"
    r"公司|店里|楼上|楼下|操场|图书馆|家))"
)


def split_memory(text):
    """返回 (主干, [(kind, text, weight), ...])"""
    details = []
    core = text

    # 1) 时间
    for m in RE_TIME.finditer(text):
        details.append(("TIME", m.group(1), 1.0))
        core = core.replace(m.group(1), "")

    # 2) 地点
    for m in RE_PLACE.finditer(text):
        details.append(("PLACE", m.group(1), 1.0))
        core = core.replace(m.group(1), "")

    # 3) 情绪
    for w in EMO_WORDS:
        if w in core:
            details.append(("EMOTION", w, 1.0))
            core = core.replace(w, "")
            break

    # 4) 数字（避开已被时间吃掉的部分）
    for m in RE_NUM.finditer(core):
        details.append(("NUMBER", m.group(1), 1.0))

    # 5) 主干：去掉已提取的细节后剩下的骨架
    core = _PUNCT.sub(" ", core)
    core = re.sub(r"\s+", "", core).strip()
    # 去掉开头的连接词/残留虚词
    core = re.sub(r"^[了的地得和还也又就都才再在是有]+", "", core)
    if not core:
        core = text  # 保底：拆没了就用原文

    return core, details


# ============================================================
# 主体
# ============================================================
class MemoryCard:
    def __init__(self, db_path=":memory:", clock=None):
        self.clock = clock or Clock()
        self.db = sqlite3.connect(db_path)
        self.db.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        c = self.db.cursor()
        c.execute("""
        CREATE TABLE IF NOT EXISTS mem (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            raw            TEXT NOT NULL,      -- 原文，永不删
            core           TEXT NOT NULL,      -- 主干
            vec            TEXT NOT NULL,      -- 稀疏向量 JSON
            clarity        REAL NOT NULL,      -- 当前清晰度
            salience       REAL NOT NULL,      -- 重要性
            access_count   INTEGER NOT NULL DEFAULT 0,
            k              REAL NOT NULL DEFAULT 0.0000011,  -- 这条自己的衰减率
            create_ts      REAL NOT NULL,
            last_active_ts REAL NOT NULL
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS detail (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_id  INTEGER NOT NULL,
            kind       TEXT NOT NULL,
            text       TEXT NOT NULL,
            weight     REAL NOT NULL,
            last_touch REAL NOT NULL
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS ix_detail ON detail(parent_id)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_mem_clarity ON mem(clarity)")
        self.db.commit()

    # ---------- 存 ----------
    def remember(self, text, salience=0.6, ts=None, k=None):
        """存一条记忆。

        ts : 这条记忆真实发生的时间（默认=现在）。
             历史导入要传，它决定了「进来时有多淡」。
        k  : 这条自己的衰减率（默认 K_CORE；历史档案传 K_ARCHIVE，衰减慢）。
        """
        core, details = split_memory(text)
        vec = to_vec(text)
        now = self.clock.now()
        at = now if ts is None else float(ts)
        at = min(at, now)                       # 不允许未来时间
        kk = K_CORE if k is None else float(k)
        age = max(0.0, now - at)

        # 按真实年龄折旧：老记忆进来就是淡的，不会因为「今天才录」而变新
        clarity = min(1.0, math.exp(-kk * age))
        # 细节跟着主干的慢衰减一起放慢
        detail_scale = kk / K_CORE

        c = self.db.cursor()
        c.execute(
            "INSERT INTO mem(raw,core,vec,clarity,salience,k,create_ts,last_active_ts)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (text, core, json.dumps(vec, ensure_ascii=False),
             clarity, salience, kk, at, at),
        )
        mid = c.lastrowid
        for kind, txt, w in details:
            dk = K_DETAIL_BY_KIND.get(kind, K_DETAIL_DEFAULT) * detail_scale
            ww = w * math.exp(-dk * age)
            if ww < KEEP_DETAIL:
                continue
            c.execute(
                "INSERT INTO detail(parent_id,kind,text,weight,last_touch)"
                " VALUES(?,?,?,?,?)", (mid, kind, txt, ww, at))
        self.db.commit()
        return mid

    # ---------- 老 ----------
    def tick(self, dt_seconds):
        """时间流逝：主干慢衰，细节快衰（V4.0 §4.2）"""
        dt_raw = max(0.0, dt_seconds)
        self.clock.advance(dt_raw)              # 时钟按真实时间走
        dt = compress_offline_dt(dt_raw)        # 衰减按压缩后的时间算
        c = self.db.cursor()
        c.execute("SELECT id, clarity, k FROM mem")
        rows = c.fetchall()
        for r in rows:
            kk = r["k"] or K_CORE
            new_c = r["clarity"] * math.exp(-kk * dt)
            c.execute("UPDATE mem SET clarity=? WHERE id=?", (new_c, r["id"]))
        c.execute("SELECT d.id, d.kind, d.weight, m.k FROM detail d JOIN mem m ON m.id=d.parent_id")
        for d in c.fetchall():
            k = K_DETAIL_BY_KIND.get(d["kind"], K_DETAIL_DEFAULT) * ((d["k"] or K_CORE) / K_CORE)
            nw = d["weight"] * math.exp(-k * dt)
            if nw < KEEP_DETAIL:
                c.execute("DELETE FROM detail WHERE id=?", (d["id"],))
            else:
                c.execute("UPDATE detail SET weight=? WHERE id=?", (nw, d["id"]))
        self.db.commit()
        self._gc()

    def _gc(self):
        """回收：清晰度低于阈值、或超过 TTL"""
        now = self.clock.now()
        c = self.db.cursor()
        c.execute("SELECT id, clarity, create_ts, k FROM mem")
        for r in c.fetchall():
            kk = r["k"] or K_CORE
            # TTL 只对短时池生效；长期档案只看清晰度
            is_short = kk >= K_CORE * 0.5
            dead = (r["clarity"] < CLARITY_GC) or (
                is_short and (now - r["create_ts"] > SHORT_MAX_TTL))
            if dead:
                c.execute("DELETE FROM detail WHERE parent_id=?", (r["id"],))
                c.execute("DELETE FROM mem WHERE id=?", (r["id"],))
        self.db.commit()

    # ---------- 想起 ----------
    def recall(self, query, top_k=3, access=True):
        """三因子加权：像不像 × 清晰度 × 近期度（V4.0 核心）"""
        qv = to_vec(query)
        now = self.clock.now()
        c = self.db.cursor()
        c.execute("SELECT * FROM mem")
        scored = []
        for r in c.fetchall():
            mv = json.loads(r["vec"])
            sim = cos_sim(qv, mv)                      # ① 像不像
            clarity = r["clarity"]                     # ② 还清不清楚
            age_days = (now - r["last_active_ts"]) / DAY
            recency = math.exp(-age_days / 14.0)       # ③ 多久没提
            score = (0.60 * sim + 0.25 * clarity + 0.15 * recency) * (0.5 + r["salience"])
            if sim <= 0:
                continue
            scored.append((score, r))

        scored.sort(key=lambda x: -x[0])
        hits = scored[:top_k]

        if access:
            for _score, r in hits:
                g = GAIN_ACCESS / (1.0 + ACCESS_DIMINISH * r["access_count"])
                new_c = min(1.0, r["clarity"] + g)
                c.execute(
                    "UPDATE mem SET clarity=?, access_count=access_count+1,"
                    " last_active_ts=? WHERE id=?",
                    (new_c, now, r["id"]))
                # 细节也跟着被唤醒
                c.execute(
                    "UPDATE detail SET weight=MIN(1.0, weight+?), last_touch=?"
                    " WHERE parent_id=?", (g * 0.8, now, r["id"]))
            self.db.commit()

        return [(s, r) for s, r in hits]

    # ---------- 说 ----------
    def render(self, mem_row):
        """按细节权重分级渲染：这就是'褪色'的观感（V4.0 §2.2）"""
        c = self.db.cursor()
        c.execute("SELECT kind, text, weight FROM detail WHERE parent_id=?"
                  " ORDER BY weight DESC", (mem_row["id"],))
        parts = {"TIME": [], "PLACE": [], "EMOTION": [], "NUMBER": []}
        for d in c.fetchall():
            w = d["weight"]
            if w >= SHOW_FULL:
                parts.setdefault(d["kind"], []).append(d["text"])
            elif w >= SHOW_SCENE:
                if d["kind"] == "TIME":
                    parts["TIME"].append("某天")
                elif d["kind"] == "PLACE":
                    # 保留介词，地点名整体换掉（只替换介词前缀会剩下半截地名）
                    mm = re.match(r"^(在|去|到|从)", d["text"])
                    parts["PLACE"].append((mm.group(1) if mm else "") + "某个地方")
                elif d["kind"] == "EMOTION":
                    parts["EMOTION"].append(d["text"])
            elif w >= SHOW_HINT:
                if d["kind"] == "EMOTION":
                    parts["EMOTION"].append(d["text"])

        bits = []
        if parts["TIME"]:
            bits.append(parts["TIME"][0])
        if parts["PLACE"]:
            bits.append(parts["PLACE"][0])
        # 长句没能切开时，主干会是一整段 —— 截短，别把原文整句倒出来
        core = mem_row["core"]
        if len(core) > 40:
            core = core[:40] + "……"
        bits.append(core)
        if parts["EMOTION"]:
            bits.append("，" + parts["EMOTION"][0])

        s = "".join(bits)
        # 太模糊时改说法
        if mem_row["clarity"] < 0.35:
            return "我记得好像有这么回事：" + s + "。（具体记不清了）"
        if mem_row["clarity"] < 0.60:
            return s + "。（细节有点模糊了）"
        return s + "。"

    def stats(self):
        c = self.db.cursor()
        c.execute("SELECT COUNT(*) n, AVG(clarity) avg_c FROM mem")
        r = c.fetchone()
        c.execute("SELECT COUNT(*) n FROM detail")
        d = c.fetchone()
        return {"memories": r["n"], "avg_clarity": round(r["avg_c"] or 0, 3),
                "details": d["n"]}
