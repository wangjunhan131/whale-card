# -*- coding: utf-8 -*-
"""habits.py —— 习惯层（v1：只存、只挖、先不注入）

设计原则（和卡里其它部分保持一致）：
  1. **纯加法**：新建一张 `habits` 表，不动 mem / dialogue / core_zone / timeline。
  2. **不改原有逻辑**：不参与 recall 打分、不参与衰减验票、不写 mem_tomb。
     它只回答一个问题：「她说话有什么习惯」，答案给 `/context` 用（v2 才接）。
  3. **一切有证据**：每条习惯都必须能回到原文（evidence 里存句子 + 对话 id）。
  4. **门槛照抄业界经验**：≥3 次且跨 ≥2 天算候选；≥10 次且跨 ≥14 天才算稳定；
     30 天没再出现就降权（不删）。
  5. **可回退**：不想要这功能时，删掉表 / 不调用即可，卡里其它东西不受影响。

⚠️ 这张表**不是记忆**。它不进五池、不进召回、不出生在时间线上。
   它更像"她说话的口音统计"，是元数据，不是经历。
"""

import json
import os
import re
import sqlite3
import time

# ---------------------------------------------------------------- 门槛
HAB_CAND_MIN_HITS = 3       # 出现 ≥3 次
HAB_CAND_MIN_DAYS = 2       # 且跨 ≥2 天
HAB_STABLE_MIN_HITS = 10    # 稳定：≥10 次
HAB_STABLE_MIN_DAYS = 14    # 且跨 ≥14 天
HAB_DECAY_DAYS = 30.0       # 30 天没出现 → 降权（不删）

DAY = 86400.0

# 六类（照 V5.0 的说法：语言 / 行为 / 情绪 / 时间 / 关系 / 内容偏好）
CATEGORIES = ("language", "behavior", "emotion", "time", "relation", "content")

# 状态机
ST_CANDIDATE = "candidate"
ST_STABLE = "stable"
ST_DECAYED = "decayed"
ST_CONFLICT = "conflict"
ST_PINNED = "pinned"

# ---------------------------------------------------------------- 表
SCHEMA = """
CREATE TABLE IF NOT EXISTS habits (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category    TEXT NOT NULL DEFAULT 'language',
    key         TEXT NOT NULL,                  -- 口头禅原样 / 情境短名
    trigger     TEXT NOT NULL DEFAULT '',       -- 什么情况下（行为的 trigger）
    behavior    TEXT NOT NULL DEFAULT '',       -- 怎么反应
    scope       TEXT NOT NULL DEFAULT 'character',  -- character | person
    subject_key TEXT NOT NULL DEFAULT '',       -- scope=person 时是谁
    hits        INTEGER NOT NULL DEFAULT 0,
    days        INTEGER NOT NULL DEFAULT 0,
    first_seen  REAL,
    last_seen   REAL,
    confidence  REAL NOT NULL DEFAULT 0.0,
    status      TEXT NOT NULL DEFAULT 'candidate',
    weight      REAL NOT NULL DEFAULT 1.0,
    pinned      INTEGER NOT NULL DEFAULT 0,
    conflict    INTEGER NOT NULL DEFAULT 0,
    evidence    TEXT NOT NULL DEFAULT '[]',     -- JSON: [{"ts":..,"text":"..","dialogue_id":..}]
    note        TEXT NOT NULL DEFAULT '',
    ts          REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_habits_key
    ON habits(category, key, scope, subject_key);
"""


def ensure_table(con):
    """建表（幂等）。只在需要写习惯的流程里调用；只读挖掘不调用。"""
    con.executescript(SCHEMA)
    con.commit()
    return True


# ---------------------------------------------------------------- 文本处理
_END_PUNCT = "。！？!?；;…"
_NON_WORD_RE = re.compile(r"[^\u4e00-\u9fa5A-Za-z0-9]")
_HAS_CN_RE = re.compile(r"[\u4e00-\u9fa5]")


def is_report_like(text):
    """这条回复像不像"工作汇报"而不是"说话"。

    为什么要这一步：我们卡里的 assistant 原文有一大半是开发阶段的进度汇报
    （带 ✅ / 代码块 / 列表 / 标题），拿它统计口头禅，挖出来的是"已/完成/测试"。
    """
    if not text:
        return True
    t = text.strip()
    if not t:
        return True
    if "```" in t:
        return True
    if re.search(r"^\s*[-*+]\s+", t, re.M) or re.search(r"^\s*\d+\.\s+", t, re.M):
        return True
    if re.search(r"^\s*#{1,6}\s+", t, re.M):
        return True
    if "|" in t and t.count("|") >= 4:
        return True
    if re.search(r"(✅|❌|⚠️|🔧|📦|🔴|🟢)", t):
        return True
    # 长段 + 大量 ASCII（代码/路径/英文术语堆叠）
    if len(t) > 260:
        return True
    return False


def segments(text):
    """把一条回复切成"段"：先按换行，再按句末标点。

    真实聊天里一条回复常常是几段短句（"……你猜我在干嘛。""行吧，我等着。"），
    口头禅多半出现在段的开头和结尾，所以按段统计比按整条统计准。
    """
    if not text:
        return []
    out = []
    for para in re.split(r"[\r\n]+", text):
        para = para.strip()
        if not para:
            continue
        # 按句末标点切，保留标点（统计句尾要用）
        buf = ""
        for ch in para:
            buf += ch
            if ch in _END_PUNCT:
                s = buf.strip()
                if s:
                    out.append(s)
                buf = ""
        if buf.strip():
            out.append(buf.strip())
    # 去掉纯符号/纯空白段
    return [s for s in out if re.search(r"[\u4e00-\u9fa5A-Za-z0-9]", s)]


def strip_punct(s):
    return s.strip(" \t" + _END_PUNCT + "，,、：:；;\"'“”‘’()（）[]【】")


def ngrams(s, lo=1, hi=3):
    """中文按字切 n-gram（够用，且不需要分词器）。

    先把标点/空格/符号全部去掉再切 —— 否则会切出「，我」「了，」这种带标点的碎片，
    那种东西统计出来全是噪声（第一版就踩过）。
    """
    s = _NON_WORD_RE.sub("", strip_punct(s))
    out = set()
    for n in range(lo, hi + 1):
        for i in range(len(s) - n + 1):
            g = s[i:i + n]
            if _NON_WORD_RE.search(g):
                continue
            out.add(g)
    return out


def _clean(s):
    """去掉标点/空格/符号，只留字词。段首段尾统计都用它。"""
    return _NON_WORD_RE.sub("", strip_punct(s))


def head_ngram(seg, n=3):
    return _clean(seg)[:n]


def tail_ngram(seg, n=3):
    s = _clean(seg)
    return s[-n:] if len(s) >= n else s


# ---------------------------------------------------------------- 置信度与状态
def confidence_of(hits, days):
    """把"出现多少次 / 跨几天"折算成 0~1 的置信度。

    为什么不是 hits/10 这种线性：跨天数是"真的成了习惯"的关键证据，
    一天里说 30 遍只说明这一场聊嗨了。所以天数权重不低于次数。
    """
    h = min(1.0, (hits or 0) / float(HAB_STABLE_MIN_HITS))
    d = min(1.0, (days or 0) / float(HAB_STABLE_MIN_DAYS))
    return round(0.5 * h + 0.5 * d, 3)


# 口头语和"行为习惯"攒起来的速度不一样：行为习惯要见够次数才算数；
# 口头语本来就偶尔冒一句（实测真卡挖出来的候选最高才 8 次、跨 8 天），
# 用 10 次 / 14 天的门槛等于永远进不来 —— 所以按类给两套门槛。
# 不传 category 时行为完全不变（老测试、老用法都安全）。
STABLE_LANG_MIN_HITS = 5
STABLE_LANG_MIN_DAYS = 5


def status_of(hits, days, conflict=False, pinned=False, last_seen=None, now=None,
              category=""):
    """状态机：钉住 > 冲突 > 稳定 > 候选 > 衰减。"""
    if pinned:
        return ST_PINNED
    if conflict:
        return ST_CONFLICT
    if last_seen and now and (now - last_seen) > HAB_DECAY_DAYS * DAY:
        return ST_DECAYED
    min_hits, min_days = HAB_STABLE_MIN_HITS, HAB_STABLE_MIN_DAYS
    if category == "language":
        min_hits, min_days = STABLE_LANG_MIN_HITS, STABLE_LANG_MIN_DAYS
    if (hits or 0) >= min_hits and (days or 0) >= min_days:
        return ST_STABLE
    if (hits or 0) >= HAB_CAND_MIN_HITS and (days or 0) >= HAB_CAND_MIN_DAYS:
        return ST_CANDIDATE
    return ST_CANDIDATE


# ---------------------------------------------------------------- 背景对比（完善 v1）
#
# 为什么要这一层：
#   只看"她说过几次"，会把工作汇报里的词也抬上来（建文件夹/D新建文/环境变量）。
#   正确做法是拿"不像聊天的那批回复"当背景，比一比同一个词在聊天里的频率
#   是不是显著更高 —— 这是很粗的 PMI 思想，但足够把开发词汇压下去。
HAB_LIFT_KEEP = 2.0        # 聊天里出现频率 ≥ 背景 2 倍 → 建议保留
HAB_LIFT_DROP = 1.2        # < 1.2 倍 → 建议丢弃（背景里也常见，不是口音）
HAB_DISTINCT_DROP = 0.35   # 用户也常说（区分度 < 0.35）→ 建议丢弃


def rate_per_1k(hits, total_segments):
    """每千段的出现率，避免两边段数不同导致不可比。"""
    t = float(total_segments or 0)
    if t <= 0:
        return 0.0
    return 1000.0 * float(hits or 0) / t


def lift_of(chat_hits, chat_segments, bg_hits, bg_segments):
    """聊天里的出现率 ÷ 背景里的出现率。背景为 0 时给一个有限的大数。"""
    a = rate_per_1k(chat_hits, chat_segments)
    b = rate_per_1k(bg_hits, bg_segments)
    if b <= 0:
        return 99.0 if a > 0 else 0.0
    return round(a / b, 2)


def suggest_of(distinct, lift, hits, days):
    """给人工筛的初判：keep / review / drop。

    三条规则（都在文档里写死，免得以后拍脑袋）：
      1. 用户也常说（区分度低）→ drop，那不是她的口音
      2. 只是这一场聊嗨了（天数不够）→ review，等更多证据
      3. 聊天里比背景显著高频 → keep
    """
    if (distinct or 0) < HAB_DISTINCT_DROP:
        return "drop"
    if (days or 0) < HAB_CAND_MIN_DAYS:
        return "review"
    if (lift or 0) < HAB_LIFT_DROP:
        return "drop"
    if (hits or 0) >= HAB_CAND_MIN_HITS and (lift or 0) >= HAB_LIFT_KEEP:
        return "keep"
    return "review"


# ---------------------------------------------------------------- 写入（v2 用）
def upsert(con, category, key, hits, days, first_seen, last_seen,
           evidence=None, trigger="", behavior="", scope="character",
           subject_key="", note="", pinned=False, now=None):
    """按 (category,key,scope,subject_key) 合并一条习惯。v1 只给测试/工具用。"""
    now = time.time() if now is None else now
    ensure_table(con)
    row = con.execute(
        "SELECT * FROM habits WHERE category=? AND key=? AND scope=? AND subject_key=?",
        (category, key, scope, subject_key)).fetchone()
    ev = json.dumps(evidence or [], ensure_ascii=False)
    conf = confidence_of(hits, days)
    st = status_of(hits, days, conflict=False, pinned=pinned,
                   last_seen=last_seen, now=now, category=category)
    if row is None:
        con.execute(
            "INSERT INTO habits(category,key,trigger,behavior,scope,subject_key,"
            "hits,days,first_seen,last_seen,confidence,status,weight,pinned,"
            "conflict,evidence,note,ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (category, key, trigger, behavior, scope, subject_key, hits, days,
             first_seen, last_seen, conf, st, 1.0, 1 if pinned else 0, 0, ev,
             note, now))
    else:
        con.execute(
            "UPDATE habits SET hits=?, days=?, first_seen=?, last_seen=?,"
            " confidence=?, status=?, evidence=?, trigger=?, behavior=?, note=? WHERE id=?",
            (hits, days, first_seen, last_seen, conf, st, ev, trigger, behavior,
             note, row["id"]))
    con.commit()
    return conf, st


def list_habits(con, status=None, limit=200):
    ensure_table(con)
    q = "SELECT * FROM habits"
    args = []
    if status:
        q += " WHERE status=?"
        args.append(status)
    q += " ORDER BY confidence DESC, hits DESC LIMIT ?"
    args.append(int(limit))
    return [dict(r) for r in con.execute(q, args)]


if __name__ == "__main__":  # 手工看一眼
    db = os.environ.get("WHALE_DB") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "card.db")
    con = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT role, COUNT(*) n FROM dialogue GROUP BY role").fetchall()
    print("对话分布：", {r["role"]: r["n"] for r in rows})
    print("报告味占比：", round(
        sum(1 for r in con.execute("SELECT text FROM dialogue WHERE role='assistant'")
            if is_report_like(r["text"])) /
        max(1, con.execute("SELECT COUNT(*) FROM dialogue WHERE role='assistant'").fetchone()[0]), 3))
