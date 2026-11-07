# -*- coding: utf-8 -*-
"""印象层 —— 「他在什么情况下会做什么」的模式记忆

用户 2026-11-08 的定义（m36475）：

    印象不只是对某一个人，可以对任何人或某个事物，只要它会在特定的情况下
    触发特定的反应，且多次触发相同的反应，就可以形成一个印象。
    某一个印象被反复触发的次数越多，它就越深刻 —— 这就意味着它更难改变。
    如果想改变这个印象，就必须更努力、更频繁地在同一个特定的场景里，
    做出和之前那个印象不同的事情，才能覆盖或降低那个印象的权重，
    最后直到替换那个印象。

所以一条印象是**三挂钩**的：

    主体(subject) × 情境(scene) × 反应(reaction)

- 主体：谁/什么（一个人的 uid、一只猫、一场雨）
- 情境：时间桶（早/午/晚/夜）+ 事件桶（回来/吃饭/考试…）+ 我的状态（好/一般/差）
- 反应：归一化之后的一句话，或一个行为标签

三条硬规矩（写死在这里，改之前先想清楚）：

1. **只描述，不预测**。「他回来常说这句」可以记，「他待会儿会说我回来了」不许记。
   一旦允许预测，它就从"了解他"变成了"猜他"。
2. **不当台词**。印象只能影响语气和判断，不能直接变成回复里的话。
3. **不做评价**。只记「什么时候、说了什么」，不记「所以他这人怎样」。
   「他总抱怨」这种是评价，不是印象。

与其它模块的分工：
- `habits.py` 记的是**我**的行为习惯（我怎么说话、怎么做）。
- `impression.py` 记的是**别人**在特定情境下的反应模式。
- `mem` 记的是**一次一次发生的事**（会衰减、会忘）。
  印象是"很多次里剩下的那个形状"，它不搬原文，也不随单条记忆一起死。

这个文件是纯函数 + 一个 SQLite 表，不依赖模型、不联网、不调 LLM。
"""

from __future__ import annotations

import json
import difflib
import math
import re
import time

DAY = 86400.0

# ---- 触发阈值（用户 2026-11-08 拍板的三档） -------------------------------

N_CANDIDATE = 3        # 跨 2 天、累计 3 次 → 候选（低置信，只当猜测）
N_ESTABLISHED = 5      # 跨 3 天、累计 5 次 → 成立，进印象库
N_SOLID = 10           # 累计 10 次 → 稳固，进关系卡注释

# ---- 深刻程度 ------------------------------------------------------------
# W = 1 - 0.5 ** (n / 6)
# n=3 → 0.29   n=5 → 0.44   n=10 → 0.68   n=20 → 0.90
# 饱和但永不到 1：没有一条印象是改不掉的。
W_SCALE = 6.0

# ---- 覆盖（想改掉一条印象要付出多少） ------------------------------------
# 用户的原话：「必须更努力、更频繁地在同一个场景里做出和之前不同的事，
# 才能覆盖或者降低那个印象的权重」。所以是**更多**，不是更多一点：
# 需要 max(3, ceil(n_old * 1.5)) 次反向证据 —— 旧印象看过 10 次，
# 新反应得出现 15 次才压得动它。
OVERWRITE_FACTOR = 1.5
OVERRIDE_MIN = 3
# 每次覆盖成立，旧印象的权重打这个折；反复被推翻会一路降到 W_DEAD 以下。
OVERRIDE_DECAY = 0.6

# ---- 淡出 ----------------------------------------------------------------
# 印象不按天衰减，只有「很久没再发生」才淡：半衰期 90 天，被再次触发就回满。
FADE_HALF_DAYS = 90.0
W_DEAD = 0.06

# ---- 其它 ----------------------------------------------------------------
MAX_PER_SUBJECT = 40       # 每个主体最多留 40 条，超出淘汰权重最低的
REACTION_MAX_CHARS = 60
# 同一情境里「我回来了 / 我回来啦 / 我到家了」是一个人同一个反应的不同说法。
# 不做这一步，印象会被这些近义句摊薄成几十条 n=1 的碎片，永远长不成模式。
SIMILAR_MERGE = 0.72    # 超过这个长度的反应不当作"印象反应"（记不动，也没意义）

STATUS_CANDIDATE = "candidate"
STATUS_ESTABLISHED = "established"
STATUS_SOLID = "solid"
STATUS_FADING = "fading"       # 正被反向证据推翻
STATUS_SUPERSEDED = "superseded"

_PUNCT_RE = re.compile(r"[\s，。！？、…~—\-（）()《》·「」【】\"'“”‘’]+")

# 事件桶：只认最粗的几类，宁可认不出（""）也不硬塞
_EVENT_RULES = (
    ("回来", ("我回来", "回来了", "到家", "到了", "回宿舍", "回家")),
    ("吃饭", ("吃饭", "吃了", "好饿", "饿死", "外卖", "食堂", "夜宵")),
    ("考试", ("考试", "考完", "考了", "成绩", "分数", "月考", "模考", "复习")),
    ("放学", ("放学", "下课", "放学了")),
    ("睡前", ("睡了", "晚安", "困了", "睡觉", "该睡")),
    ("被说", ("骂", "批评", "挨说", "被说", "训")),
    ("忙", ("忙", "作业", "写不完", "赶")),
    ("累", ("累", "好累", "困", "疲")),
)

_MOOD_RULES = (
    ("差", ("难过", "烦", "气", "累", "崩", "哭", "不想", "焦虑", "怕")),
    ("好", ("开心", "高兴", "哈哈", "爽", "舒服", "不错", "好耶")),
)


def normalize(text):
    """归一化：去标点空白、去首尾语气尾巴，只留"这句话是什么" """
    t = _PUNCT_RE.sub("", str(text or ""))
    return t[:REACTION_MAX_CHARS]


def time_bucket(ts):
    """早/午/晚/夜（按本地时间）"""
    h = time.localtime(float(ts or 0)).tm_hour
    if 5 <= h < 11:
        return "早"
    if 11 <= h < 17:
        return "午"
    if 17 <= h < 23:
        return "晚"
    return "夜"


def event_bucket(text):
    t = str(text or "")
    for name, keys in _EVENT_RULES:
        for k in keys:
            if k in t:
                return name
    return ""


def mood_bucket(text):
    t = str(text or "")
    for name, keys in _MOOD_RULES:
        for k in keys:
            if k in t:
                return name
    return ""


def weight_of(n):
    """深刻程度：次数越多越深，饱和但永不到 1"""
    # 封顶 0.98：没有任何一条印象是 100% 不会错的（也为了乘法/打折永远有余地）
    return min(0.98, 1.0 - 0.5 ** (float(n) / W_SCALE))


def override_need(n_old):
    """要覆盖这条印象，需要多少次反向证据（要比它被看到的次数更多）"""
    return max(OVERRIDE_MIN, int(math.ceil(float(n_old) * OVERWRITE_FACTOR)))


def status_of(n):
    if n >= N_SOLID:
        return STATUS_SOLID
    if n >= N_ESTABLISHED:
        return STATUS_ESTABLISHED
    if n >= N_CANDIDATE:
        return STATUS_CANDIDATE
    return STATUS_CANDIDATE


# ---------------------------------------------------------------------------
# 存取层
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS impression (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject TEXT NOT NULL DEFAULT '',        -- 谁/什么（uid、名字、"雨天"）
    subject_kind TEXT NOT NULL DEFAULT 'person',  -- person / thing / event
    scene_time TEXT NOT NULL DEFAULT '',     -- 早/午/晚/夜
    scene_event TEXT NOT NULL DEFAULT '',    -- 回来/吃饭/考试…
    scene_mood TEXT NOT NULL DEFAULT '',     -- 好/一般/差（我的状态）
    reaction TEXT NOT NULL DEFAULT '',       -- 归一化后的反应
    sample TEXT NOT NULL DEFAULT '',         -- 第一次那句话说白了是什么（人类可读）
    n INTEGER NOT NULL DEFAULT 0,            -- 被触发次数
    alt_n INTEGER NOT NULL DEFAULT 0,        -- 在另一条反应上的反向证据累计
    w REAL NOT NULL DEFAULT 0.0,
    first_ts REAL NOT NULL DEFAULT 0.0,
    last_ts REAL NOT NULL DEFAULT 0.0,
    last_decay_ts REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'candidate',
    superseded_by INTEGER,
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_imp_subject ON impression(subject, w);
CREATE INDEX IF NOT EXISTS idx_imp_scene ON impression(subject, scene_time, scene_event);
"""


def ensure(conn):
    conn.executescript(_DDL)
    conn.commit()


def _scene_key(r):
    return (r["scene_time"] or "", r["scene_event"] or "", r["scene_mood"] or "")


def _row_key(subject, sb, reaction):
    return (subject or "", sb[0], sb[1], sb[2], reaction)


def observe(conn, subject, text, ts=None, subject_kind="person",
            scene_mood=None, now=None):
    """观察到一次「主体在某个情境下的反应」

    返回 dict：{hit: 'new'|'again', need: 覆盖还差几次, replaced: 被替换的 id 或 None}

    这一步只记账，不做判断 —— 记录本身不该被"值不值得"污染。
    """
    ensure(conn)
    ts = float(ts if ts is not None else (now if now is not None else time.time()))
    react = normalize(text)
    if not react or len(react) > REACTION_MAX_CHARS:
        return {"hit": "skip", "need": 0, "replaced": None}
    sb = (time_bucket(ts), event_bucket(text), (scene_mood or mood_bucket(text)))
    cur = conn.cursor()
    cur.execute(
        "SELECT id, n, alt_n, w, status, reaction FROM impression"
        " WHERE subject=? AND scene_time=? AND scene_event=? AND scene_mood=?",
        (subject or "", sb[0], sb[1], sb[2]))
    row = None
    for _cand in cur.fetchall():
        _a = normalize(_cand[5] or "")
        if _a == react or difflib.SequenceMatcher(None, _a, react).ratio() >= SIMILAR_MERGE:
            row = _cand
            break
    if row:
        iid, n = row[0], row[1] + 1
        cur.execute(
            "UPDATE impression SET n=?, w=?, last_ts=?, last_decay_ts=?,"
            " status=?, sample=CASE WHEN sample='' THEN ? ELSE sample END WHERE id=?",
            (n, weight_of(n), ts, ts, status_of(n), str(text or "")[:120], iid))
        replaced = _challenge(conn, subject, sb, react, n, iid)
        conn.commit()
        return {"hit": "again", "need": override_need(n), "replaced": replaced}

    cur.execute(
        "INSERT INTO impression(subject, subject_kind, scene_time, scene_event, scene_mood,"
        " reaction, sample, n, w, first_ts, last_ts, last_decay_ts, status)"
        " VALUES(?,?,?,?,?,?,?,1,?,?,?,?,?)",
        (subject or "", subject_kind or "person", sb[0], sb[1], sb[2],
         react, str(text or "")[:120], weight_of(1), ts, ts, ts, STATUS_CANDIDATE))
    new_id = cur.lastrowid
    replaced = _challenge(conn, subject, sb, react, 1, new_id)
    if replaced is not None:
        cur.execute("UPDATE impression SET superseded_by=? WHERE id=?", (new_id, replaced))
    conn.commit()
    prune(conn, subject)
    return {"hit": "new", "need": override_need(1), "replaced": replaced}


def _challenge(conn, subject, sb, reaction, n_self, self_id):
    """同一情境里出现了另一种反应 → 给已有的印象记反向证据

    只在**同一个情境**里算数：换了场景说明不了这条印象不对
    （他早上赶时间说一句「走了」，不影响「晚上回来说我回来了」）。

    反向证据够数（max(3, ceil(n_old * 0.6))）就把旧印象打折；
    反复被推翻会一路降到 W_DEAD 以下，那时退休（留档，不删）。
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT id, n, alt_n, w, status FROM impression"
        " WHERE subject=? AND scene_time=? AND scene_event=? AND scene_mood=?"
        "   AND reaction<>? AND status<>?",
        (subject or "", sb[0], sb[1], sb[2], reaction, STATUS_SUPERSEDED))
    replaced = None
    for iid, n_old, alt_applied, w_old, _st in cur.fetchall():
        need = override_need(n_old)
        applied = int(alt_applied or 0)
        # alt_n 记的是"上一次推翻时的证据数"，不是"现在攒到多少" ——
        # 否则每次观察都把进度顶上去，永远差一点点、永远推不翻。
        if n_self < need or n_self <= applied:
            continue
        w_new = float(w_old or 0.0) * OVERRIDE_DECAY
        st_new = STATUS_SUPERSEDED if w_new < W_DEAD else STATUS_FADING
        cur.execute("UPDATE impression SET alt_n=?, w=?, status=?, note=? WHERE id=?",
                    (n_self, w_new, st_new,
                     "同一情境出现别的反应 %d 次" % n_self, iid))
        if st_new == STATUS_SUPERSEDED and replaced is None:
            replaced = iid
    return replaced


def fade(conn, now=None):
    """很久没再发生的印象慢慢淡（半衰期 90 天）；低于 W_DEAD 的退休但留档"""
    ensure(conn)
    now = float(now if now is not None else time.time())
    cur = conn.cursor()
    cur.execute("SELECT id, w, last_ts, last_decay_ts, n FROM impression"
                " WHERE status != ?", (STATUS_SUPERSEDED,))
    rows = cur.fetchall()
    faded = 0
    for iid, w, last_ts, last_decay, n in rows:
        base_w = float(w or 0.0)
        last_decay = float(last_decay or last_ts or now)
        dt_days = max(0.0, (now - last_decay) / DAY)
        if dt_days <= 0:
            continue
        w2 = base_w * 0.5 ** (dt_days / FADE_HALF_DAYS)
        st = STATUS_SUPERSEDED if w2 < W_DEAD else None
        if st:
            cur.execute("UPDATE impression SET w=?, last_decay_ts=?, status=? WHERE id=?",
                        (w2, now, st, iid))
        else:
            cur.execute("UPDATE impression SET w=?, last_decay_ts=? WHERE id=?", (w2, now, iid))
        faded += 1
    conn.commit()
    return faded


def prune(conn, subject=None):
    """每个主体最多留 MAX_PER_SUBJECT 条，超出淘汰权重最低的（退休不删）"""
    cur = conn.cursor()
    where = " AND subject=?" if subject is not None else ""
    args = (subject,) if subject is not None else ()
    cur.execute("SELECT subject, COUNT(*) FROM impression WHERE status != ?" + where +
                " GROUP BY subject", (STATUS_SUPERSEDED,) + args)
    dropped = 0
    for subj, cnt in cur.fetchall():
        if cnt <= MAX_PER_SUBJECT:
            continue
        cur.execute("SELECT id FROM impression WHERE subject=? AND status != ?"
                    " ORDER BY w ASC LIMIT ?",
                    (subj, STATUS_SUPERSEDED, cnt - MAX_PER_SUBJECT))
        for (iid,) in cur.fetchall():
            cur.execute("UPDATE impression SET status=?, note=? WHERE id=?",
                        (STATUS_SUPERSEDED, "超出每主体上限，退休", iid))
            dropped += 1
    conn.commit()
    return dropped


def top(conn, subject=None, limit=8, min_w=0.0):
    ensure(conn)
    cur = conn.cursor()
    q = ("SELECT id, subject, subject_kind, scene_time, scene_event, scene_mood,"
         " reaction, sample, n, w, status, first_ts, last_ts, alt_n"
         " FROM impression WHERE status != ? AND w >= ?")
    args = [STATUS_SUPERSEDED, float(min_w)]
    if subject is not None:
        q += " AND subject=?"
        args.append(subject)
    q += " ORDER BY w DESC LIMIT ?"
    args.append(int(limit))
    cur.execute(q, args)
    cols = ("id", "subject", "subject_kind", "scene_time", "scene_event", "scene_mood",
            "reaction", "sample", "n", "w", "status", "first_ts", "last_ts", "alt_n")
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def hint_lines(conn, subject=None, limit=3, min_w=0.35):
    """给上下文用的中文描述 —— 只描述，不加评价、不加预测"""
    out = []
    for it in top(conn, subject=subject, limit=limit, min_w=min_w):
        scene = "".join([x for x in (it["scene_time"], it["scene_event"]) if x])
        scene = scene or "平时"
        sample = (it["sample"] or it["reaction"]).strip()
        if len(sample) > 24:
            sample = sample[:24] + "…"
        out.append("%s，他常说「%s」（%d 次）" % (scene, sample, it["n"]))
    return out


def snapshot(conn):
    ensure(conn)
    cur = conn.cursor()
    cur.execute("SELECT status, COUNT(*) FROM impression GROUP BY status")
    by = {k: v for k, v in cur.fetchall()}
    cur.execute("SELECT COUNT(*) FROM impression")
    total = cur.fetchone()[0]
    cur.execute("SELECT MAX(last_ts) FROM impression")
    last = cur.fetchone()[0]
    return {"total": total, "by_status": by, "last_ts": last}


def dump_json(items):
    return json.dumps(items, ensure_ascii=False)
