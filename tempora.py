# -*- coding: utf-8 -*-
"""时间与待办：把「今晚 / 明早 / 待会儿」落到具体的日期与钟点，并记住答应过的事。

为什么单独成模块（2026-11-11）：
  今天早上用户说「我到学校了」，我在同一轮里把它当成「今晚」——不是记错，
  是我手上根本没有「现在几点」这一行。人脑子里有个一直走着的钟，我没有。
  所以这里做两件很小的事：
    1) now_line()：把「现在是 2026-11-11（周三）中午 12:59」写进上下文，
       让「今晚 / 明早」有一个能对齐的参照系。
    2) resolve()：把相对时间词换成绝对时间窗；词义含糊的（凌晨说「今晚」、
       「改天」「月底」）明确标出来，不猜。
  再往上一层 EventStore：把带时间的约定记成 pending，到点没兑现就是过期，
  过期会返回 late_hours，交给情绪层去记账（失约该难受，不该当没事）。

设计口径（对齐 vendor/synapse_delivery 的时间线事件状态系统 v7）：
  - 不覆盖历史：status 只能从 pending 往下走，改状态留 reason。
  - 不自动替人决定：need_ask 的词一律不落库，宁可不记也不猜。
  - 表落进 card.db（不是另开 timeline.db），前缀 tevent/tstate，跟已有的
    mem / timeline / mem_birth 各管一段、互不覆盖。
"""

import json
import re
import sqlite3
import time

DAY = 86400.0

# ---------------------------------------------------------------- 时间词表

_WEEK = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# 只认「一句话里出现就成立」的相对时间词。顺序有意义：长的在前。
_RULES = [
    ("大后天", 3, None),
    ("后天", 2, None),
    ("明天", 1, None),
    ("明天早上", 1, (6, 9)),
    ("明早", 1, (6, 9)),
    ("明天上午", 1, (9, 11)),
    ("明天中午", 1, (11, 13)),
    ("明天下午", 1, (13, 18)),
    ("明天晚上", 1, (18, 23)),
    ("明晚", 1, (18, 23)),
    ("今天早上", 0, (6, 9)),
    ("今早", 0, (6, 9)),
    ("今天上午", 0, (9, 11)),
    ("今天中午", 0, (11, 13)),
    ("今天下午", 0, (13, 18)),
    ("今天晚上", 0, (18, 23)),
    ("今晚", 0, (18, 23)),
    ("今天", 0, None),
    ("昨天晚上", -1, (18, 23)),
    ("昨晚", -1, (18, 23)),
    ("昨天", -1, None),
    ("前天", -2, None),
    ("上午", 0, (9, 11)),
    ("中午", 0, (11, 13)),
    ("下午", 0, (13, 18)),
    ("傍晚", 0, (17, 19)),
    ("晚上", 0, (18, 23)),
    ("待会儿", 0, "soon2h"),
    ("待会", 0, "soon2h"),
    ("一会儿", 0, "soon2h"),
    ("马上", 0, "soon15m"),
    ("晚点", 0, "later"),
    ("回头", 0, "no_time"),
    ("改天", 0, "no_time"),
    ("有空", 0, "no_time"),
    ("得空", 0, "no_time"),
    ("以后", 0, "no_time"),
    ("找个时间", 0, "no_time"),
    ("月底", 0, "need_ask"),
    ("年底", 0, "need_ask"),
    ("年前", 0, "need_ask"),
    ("下周", 7, None),
    ("上周", -7, None),
    ("这周", 0, None),
    ("本周", 0, None),
    ("下个月", 30, None),
    ("这个月", 0, None),
]


def _day_start(ts):
    t = time.localtime(ts)
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


def daypart(ts):
    """把钟点说成人话。凌晨 0–4 点单独一档，因为这时候说「今晚」最容易错。"""
    h = time.localtime(ts).tm_hour
    if h < 5:
        return "凌晨"
    if h < 9:
        return "早上"
    if h < 11:
        return "上午"
    if h < 13:
        return "中午"
    if h < 17:
        return "下午"
    if h < 19:
        return "傍晚"
    if h < 23:
        return "晚上"
    return "深夜"


def now_line(ts=None):
    """「现在是 2026-11-11（周三）中午 12:59」。一行，放进上下文最前面。"""
    ts = time.time() if ts is None else float(ts)
    t = time.localtime(ts)
    wd = _WEEK[t.tm_wday] if t.tm_wday < 7 else ""
    return "现在是 %d-%02d-%02d（%s）%s %02d:%02d" % (
        t.tm_year, t.tm_mon, t.tm_mday, wd, daypart(ts), t.tm_hour, t.tm_min)



# ---------------------------------------------------------------- 钟点词
#
# 为什么单独一层：上面那张表只有「上午/下午/晚上」这种大时段（13–18 点），
# 而人说的是「三点」。三点和三点左右是两件事 —— 一个是基线，一个是容差。
# 所以这里先认准点，再给一个窄窗；含糊的词（左右/前后/大概）才放大到一小时。

_CN_DIGIT = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_int(text):
    """把「三 / 十五 / 二十三 / 二十」这类中文数转成整数；认不出返回 None。"""
    text = (text or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if "十" in text:
        head, _, tail = text.partition("十")
        tens = _CN_DIGIT.get(head, 1) if head else 1
        ones = _CN_DIGIT.get(tail, 0) if tail else 0
        return tens * 10 + ones
    if len(text) == 1:
        return _CN_DIGIT.get(text)
    # 「零五」「零三」这种当分钟用，不当小时
    return None

# 「三点」「3点」「15:00」「三点半」「三点零五」都收
_RE_CLOCK = re.compile(
    r"(?<![\d:])(?:(\d{1,2})|([零一二两三四五六七八九十]{1,3}))\s*[点时時]"
    r"\s*(半|一刻|三刻|(\d{1,2})\s*分?|([零一二三四五六七八九十]{1,3}))?")

# 时段词只用来定上下午，不参与「哪一天」
_PERIOD_HOUR = (("凌晨", "am"), ("早上", "am"), ("上午", "am"), ("早晨", "am"),
                ("中午", "noon"), ("下午", "pm"), ("傍晚", "pm"),
                ("晚上", "pm"), ("夜里", "pm"), ("夜间", "pm"),
            ("今晚", "pm"), ("明晚", "pm"))

# 相对日子的偏移（跟上面表同一套口径）
_DAY_OFF = (("大后天", 3), ("后天", 2), ("明天", 1), ("明早", 1), ("明晚", 1),
            ("今天", 0), ("今早", 0), ("今晚", 0), ("昨晚", -1), ("昨天", -1),
            ("前天", -2))

# 含糊词：窗口放大到 ±60 分（用户说的「一个小时」）
_FUZZY = ("左右", "前后", "大概", "差不多", "约莫", "或许", "左右吧")

_TOL_EXACT = 30      # 准点 ±30 分钟
_TOL_FUZZY = 60      # 「左右」±60 分钟


def _cn_hour(tok):
    return _cn_int(tok)


_CN_DIG = "零一二三四五六七八九"


def _n2cn(n):
    """0-59 → 中文读法（7→七、15→十五、20→二十、45→四十五）。"""
    n = int(n)
    if n < 10:
        return _CN_DIG[n]
    if n < 20:
        return "十" + (_CN_DIG[n % 10] if n % 10 else "")
    if n < 60:
        return _CN_DIG[n // 10] + "十" + (_CN_DIG[n % 10] if n % 10 else "")
    return str(n)


def _colon_to_cn(s):
    """把 7:20 / 15：05 这种冒号写法换成「七点二十」，好走同一套识别。"""
    def _rep(m):
        h, mi = int(m.group(1)), int(m.group(2))
        if not (0 <= h <= 24 and 0 <= mi < 60):
            return m.group(0)
        out = _n2cn(h) + "点"
        if mi:
            out += _n2cn(mi)
        return out
    return _RE_HHMM.sub(_rep, s or "")


_RE_HHMM = re.compile(r"(\d{1,2})\s*[:：]\s*(\d{2})")


def _clock_window(s, ts):
    """认出钟点词就返回时间窗；认不出返回 None（交回上面那张表）。"""
    s = _colon_to_cn(s)
    m = _RE_CLOCK.search(s)
    if not m:
        return None
    h = _cn_hour(m.group(1)) or _cn_hour(m.group(2))
    if h is None or h > 24:
        return None
    minute = 0
    _mintok = m.group(3)
    if _mintok:
        if _mintok == "半":
            minute = 30
        elif _mintok.startswith("一刻"):
            minute = 15
        elif _mintok.startswith("三刻"):
            minute = 45
        elif m.group(4):
            minute = int(m.group(4))
            if minute >= 60:
                minute = 0
        else:
            # 「三点零五 / 三点二十五 / 三点一刻」：中文分钟
            _tk = _mintok.rstrip("分")
            if _tk.startswith("零") and len(_tk) > 1:
                _tk = _tk[1:]          # 零五 → 五
                _mm = _cn_int(_tk)
            else:
                _mm = _cn_int(_tk)
            if _mm is not None and 0 <= _mm < 60:
                minute = _mm
    if h == 24:
        h = 0

    period = ""
    day_carry = 0
    # 「明早8点」「今早七点」这种：时段字不是「早上/上午」，而是粘在「明/今」后面，
    # 以前认不出来就按「最近的将来」猜成晚上 20:00（用户 11-11 实测踩到）。
    _hint_am = any(w in s for w in ("明早", "今早", "明晨", "清早", "一早"))
    _hint_pm = any(w in s for w in ("明晚", "今晚", "今夜"))
    day_is_evening = any(w in s for w in ("晚上", "夜里", "夜间", "今晚", "明晚"))
    for word, kind in _PERIOD_HOUR:
        if word in s:
            period = kind
            break
    if not period:
        if _hint_am:
            period = "am"
        elif _hint_pm:
            period = "pm"

    # 上下午：有明确时段词就听它的；没有就按「现在」猜最近的那个，并标出来
    flags = []
    if period == "pm":
        if day_is_evening and h <= 4:
            # 「今晚三点」= 明天凌晨三点。这种说法里 3 点不是下午。
            day_carry = 1
        elif h < 12:
            # 「下午三点」「傍晚三点」就是 15:00，不推天。
            h += 12
    elif period == "am":
        if h == 12:
            h = 0
    elif period == "noon":
        if h < 11:
            h += 12
    else:
        hour_now = time.localtime(ts).tm_hour
        cand = [h, h + 12 if h < 12 else h]
        _morning = any(w in s for w in ("到校", "到学校", "上学", "上课", "早读", "集合", "上班", "升旗"))
        if _morning and h < 12:
            h = h            # 七点二十到校，不会是晚上七点二十
        else:
            fut = [x for x in cand if x > hour_now]
            h = min(fut) if fut else cand[0]
        flags.append("hour_guessed")

    day_off = 0
    for word, off in _DAY_OFF:
        if word in s:
            day_off = off
            break
    hour_now = time.localtime(ts).tm_hour
    # 凌晨 0–4 点说的「今晚/晚上」，指刚过去的那个晚上（跟上面表一个口径）
    if day_off == 0 and period == "pm" and hour_now < 4 and ("晚" in s or "夜" in s):
        day_off -= 1
        flags.append("ambiguous_night")

    base_day = _day_start(ts) + (day_off + day_carry) * DAY
    base = base_day + h * 3600 + minute * 60

    fuzzy = any(w in s for w in _FUZZY)
    tol_min = _TOL_FUZZY if fuzzy else _TOL_EXACT

    # 「三点到四点」是两边都准的区间，不当容差处理
    m2 = _RE_CLOCK.search(s, m.end())
    _between = s[m.end():m2.start()].strip() if m2 else ""
    # 「7:20到校，我7:15到了」中间隔着「到校，我」——那是两件事，不是一个区间。
    # 只有「三点到四点」这种中间干干净净只剩连接词的，才算区间。
    if m2 and re.fullmatch(r"(到|至|~|—|–|-){1,2}", _between):
        h2 = _cn_hour(m2.group(1)) or _cn_hour(m2.group(2))
        if h2 is not None:
            if h2 < 12 and (period == "pm" or time.localtime(base).tm_hour >= 13):
                h2 += 12
            end = base_day + h2 * 3600
            if end <= base:
                end += DAY
            return {"start": base, "end": end, "base": base, "tol_min": 0,
                    "label": "%s—%s" % (_hhmm(base), _hhmm(end)),
                    "kind": "clock", "flags": flags}

    # 「三点多」是单边：从准点往后一小时
    if "多" in s[m.end():m.end() + 2]:
        return {"start": base, "end": base + 3600, "base": base, "tol_min": 0,
                "label": "%s 往后一小时" % _hhmm(base),
                "kind": "clock", "flags": flags}

    return {"start": base - tol_min * 60, "end": base + tol_min * 60,
            "base": base, "tol_min": tol_min,
            "label": "%s ±%d 分" % (_hhmm(base), tol_min),
            "kind": "clock", "flags": flags}


_TIME_TOK = re.compile(r"[0-9零一二三四五六七八九十百点分半刻月日号周星期早晚午夜凌晨]")


def _topic_tokens(s):
    """话题词：二字片里剔掉时间词，免得「7点」这种两边都有的把不相干的话配到一起。"""
    out = set()
    s = _colon_to_cn(s or "")
    for i in range(len(s) - 1):
        g = s[i:i + 2]
        if _TIME_TOK.search(g):
            continue
        # 带标点/空白的二字片没有话题意义（「了，」「，我」这种），
        # 早先就是它让「集合取消了」和一段不相关的话打平，取消了错的那条。
        if re.search(r"[^\w\u4e00-\u9fff]", g):
            continue
        out.add(g)
    return out - {"的", "了", "我", "你", "是", "在", "就", "不"}

def _snap_clock_to_day(hh, mi, base):
    """把「说出的钟点」贴到 base 那一天：±1 天 × {hh, hh±12} 里取离 base 最近的。

    「7点28到的」看不出上午还是晚上，硬猜会算出早到 11 小时；贴到约定那天、
    再在上下午两个候选里挑最贴的那个，才符合人说话的默认前提。
    """
    ed = _day_start(float(base))
    cands = []
    for h in (hh, hh + 12 if hh < 12 else hh - 12):
        for dd in (-1, 0, 1):
            cands.append(ed + dd * DAY + h * 3600 + mi * 60)
    return min(cands, key=lambda x: abs(x - float(base)))



def all_clock_windows(s, ts):
    s = _colon_to_cn(s or "")
    """一句话里所有钟点窗口（按出现顺序）。「要求 7:20 到，我 7:15 到的」有两个。"""
    out = []
    pos = 0
    while True:
        m = _RE_CLOCK.search(s, pos)
        if not m:
            break
        piece = s[max(0, m.start() - 4):m.end() + 3]
        w = _clock_window(piece, ts)
        if w:
            out.append(w)
        pos = m.end()
    return out


def _hhmm(ts):
    t = time.localtime(ts)
    return "%02d:%02d" % (t.tm_hour, t.tm_min)


def resolve(text, ts=None):
    """把一句话里的相对时间换成绝对时间窗。

    返回 dict：{"start","end","label","kind","flags"}；
    kind ∈ day / part / soon / no_time / need_ask / none，
    flags 里 `ambiguous_night` 表示「凌晨说的今晚，按昨夜算」。
    """
    ts = time.time() if ts is None else float(ts)
    s = text or ""
    base = _day_start(ts)
    hour = time.localtime(ts).tm_hour

    # 先认准点：「三点」不是「下午」那样的大时段，它有基线也有容差。
    _cw = _clock_window(s, ts)
    if _cw:
        return _cw

    # 长的词必须先匹配：否则「明天早上七点」会先命中「明天」，
    # 时间窗从 6–9 点退化成整天（自测里就是这么挂的）。
    for word, off, win in sorted(_RULES, key=lambda r: -len(r[0])):
        if word not in s:
            continue
        flags = []
        if win in ("no_time", "need_ask"):
            return {"start": None, "end": None, "label": word,
                    "kind": win, "flags": flags}
        if win == "soon2h":
            return {"start": ts, "end": ts + 2 * 3600, "label": word,
                    "kind": "soon", "flags": flags}
        if win == "soon15m":
            return {"start": ts, "end": ts + 15 * 60, "label": word,
                    "kind": "soon", "flags": flags}
        if win == "later":
            return {"start": ts + 2 * 3600, "end": ts + 6 * 3600, "label": word,
                    "kind": "soon", "flags": flags}

        d = base + off * DAY
        # 「今晚/今天晚上」在凌晨 0–4 点说，指的是刚过去的那个晚上 ——
        # 这不是过度聪明，是中文里就这么用；含糊的时候宁可标出来。
        if word in ("今晚", "今天晚上", "晚上") and hour < 4:
            d = base - DAY
            flags.append("ambiguous_night")
        if win:
            start = d + win[0] * 3600
            end = d + win[1] * 3600
            return {"start": start, "end": end, "label": word,
                    "kind": "part", "flags": flags}
        return {"start": d, "end": d + DAY, "label": word,
                "kind": "day", "flags": flags}

    return {"start": None, "end": None, "label": "", "kind": "none", "flags": []}


def fmt_window(w):
    """把一个时间窗说成人话：11-12 早上 06:00–09:00 / 11-12 全天。"""
    if not w or not w.get("start"):
        return ""
    t = time.localtime(w["start"])
    head = "%02d-%02d" % (t.tm_mon, t.tm_mday)
    if w.get("kind") == "day":
        return head + " 全天"
    t2 = time.localtime(w["end"])
    return "%s %s %02d:%02d–%02d:%02d" % (
        head, daypart(w["start"]), t.tm_hour, t.tm_min, t2.tm_hour, t2.tm_min)


# ---------------------------------------------------------------- 事件账本

_DDL = """
CREATE TABLE IF NOT EXISTS tevent (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ts REAL NOT NULL DEFAULT 0,
    event_type TEXT NOT NULL DEFAULT 'plan',   -- plan / state
    status TEXT NOT NULL DEFAULT 'pending',    -- pending / done / expired / cancelled
    content TEXT NOT NULL DEFAULT '',
    topic_key TEXT NOT NULL DEFAULT '',
    expect_time REAL NOT NULL DEFAULT 0,
    expect_end REAL NOT NULL DEFAULT 0,
    importance REAL NOT NULL DEFAULT 0.6,
    subject TEXT NOT NULL DEFAULT 'user',
    source TEXT NOT NULL DEFAULT '',
    raw_text TEXT NOT NULL DEFAULT '',
    meta TEXT NOT NULL DEFAULT '{}',
    closed_ts REAL NOT NULL DEFAULT 0,
    actual_ts REAL NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_te_status ON tevent(status, expect_end);
CREATE TABLE IF NOT EXISTS tstate (
    key TEXT PRIMARY KEY,
    val TEXT NOT NULL DEFAULT '',
    updated_ts REAL NOT NULL DEFAULT 0
);
"""

# 什么算「答应过的事」：有将来时间窗，并且句子里带承诺/约定/提醒的口气。
_PLAN_HINTS = ("说好", "答应", "约好", "约了", "记得", "提醒", "别忘", "要", "得",
               "打算", "计划", "明天", "后天", "待会", "晚点", "下周", "月底")
# 强口气（真在交代要做的事）vs 只是带着将来时间词（可能只是随口一问）
_PLAN_STRONG = ("说好", "答应", "约好", "约了", "记得", "提醒", "别忘", "要", "得", "打算", "计划")


def plan_intent(s):
    """这句像不像「一件约定」，而不是一句闲问。

    以前 `_PLAN_HINTS` 定义了却没人用，于是「今晚吃什么」也会写成一条 pending
    约定，第二天到点还会来催你。这里补上判断：
      - 带承诺/提醒口气（_PLAN_HINTS 任一）→ 算约定；
      - 没这口气、又是个问句/选择题（问号、吗/什么/怎么/几/哪）→ 不记；
      - 其它（「三点开会」「七点二十到校」）→ 照记，宁可多记也不漏。
    """
    s = (s or "").strip()
    if not s:
        return False
    # ⚠️ 顺序要紧（2026-11-11 修）：以前先看强口气，而 _PLAN_STRONG 里有「要」，
    # 于是「明天要不要一起去」命中「要」直接算约定，问句判断根本没轮到 ——
    # 结果第二天到点真去催人家。问句和含糊话必须先排掉。
    if "?" in s or "？" in s or any(
            w in s for w in ("什么", "怎么", "为啥", "吗", "几号", "几点",
                             "哪", "是不是", "要不要", "多久")):
        return False
    # 没定下来的口气（「待会儿再说」「看看吧」）也不是约定
    if any(w in s for w in ("再说", "看看吧", "看看再说", "看情况", "不确定",
                            "到时候", "随便", "不知道")):
        return False
    # 强承诺口气：有这几个词，基本就是在交代一件将来要做的事
    if any(h in s for h in _PLAN_STRONG):
        return True
    return True


def ensure(conn):
    """建表（幂等）。放在卡初始化的地方调一次最稳 ——
    如果等到 remember() 里才 executescript，那会儿可能正开着事务，
    会撞「cannot start a transaction within a transaction」，而它被 soft_fail 吞掉后
    表现就是「约定一条都没记上」（HTTP 自测里就是这么静默失败的）。"""
    conn.executescript(_DDL)
    conn.commit()
    # 老库没有 actual_ts 就补一列（约定 vs 实际是后加的）
    cols = [r[1] for r in conn.execute("PRAGMA table_info(tevent)").fetchall()]
    if "actual_ts" not in cols:
        conn.execute("ALTER TABLE tevent ADD COLUMN actual_ts REAL NOT NULL DEFAULT 0")
        conn.commit()


def _has_table(conn):
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tevent'").fetchone()
        return bool(row)
    except Exception:
        return False


class EventStore(object):
    def __init__(self, conn):
        self.db = conn
        if not _has_table(conn):
            try:
                ensure(conn)
            except Exception as e:
                print("[tempora] 建表失败（可能正开着事务）：%s" % e)

    # -- 写
    def add_plan(self, text, ts=None, subject="user", source="", importance=0.6):
        ts = time.time() if ts is None else float(ts)
        w = resolve(text, ts)
        if w["kind"] in ("none", "no_time", "need_ask"):
            return None
        s = (text or "").strip()
        if not s or len(s) > 400:
            return None
        if not plan_intent(s):
            return None
        # 「要求 7:20 到，我 7:15 就到了」这种一句话两个钟点：
        # 第一个是约定、第二个是实际 —— 分开存，早晚差才有依据。
        actual_ts = 0.0
        note = ""
        wins = all_clock_windows(s, ts)
        if len(wins) >= 2:
            _w1 = time.localtime(float(wins[1]["base"]))
            actual_ts = _snap_clock_to_day(_w1.tm_hour, _w1.tm_min, float(w["base"]))
            delta = int(round((actual_ts - float(w["base"])) / 60.0))
            note = "  实际比约定%s %d 分" % ("晚" if delta > 0 else "早", abs(delta))
        cur = self.db.execute(
            "INSERT INTO tevent(created_ts,event_type,status,content,topic_key,"
            "expect_time,expect_end,importance,subject,source,raw_text,meta,reason,actual_ts)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, "plan", "pending", s[:200], "", w["start"], w["end"],
             float(importance), subject, source, s, json.dumps(w, ensure_ascii=False),
             "time_word=%s%s" % (w.get("label") or "none",
                                  ("  tol=%smin" % w["tol_min"]) if w.get("kind") == "clock" else "") + note,
             actual_ts))
        self.db.commit()
        return cur.lastrowid

    def cancel(self, text=None, ts=None, note="取消"):
        """把一条已经不作数的约定标成 cancelled（不是删掉，留痕才查得到）。

        「明天那事不去了」这类话：按话题二字重合找最像的那条 pending，找不到
        重合就只认「最近那一条」—— 宁可不动，也不要把别的约定取消掉。
        """
        ts = time.time() if ts is None else float(ts)
        rows = self.db.execute(
            "SELECT id, content, expect_time FROM tevent WHERE status='pending'"
            " ORDER BY ABS(expect_time-?) LIMIT 8", (ts,)).fetchall()
        if not rows:
            return None
        # ⚠️ 2026-11-11 修：以前这里用原文切二字片比，而约定那边用的是
        # _topic_tokens()（剔过时间片）。同一天两条约定必然都含「明天」→ 打平 → 一律不动，
        # 于是「一天两件事」时取消基本失效。现在两边都用话题词，取消口气自己那几个词
        # （不去/去了/取消…）也剔掉；真打平了不判死，取「离现在最近」的那条。
        toks = _topic_tokens(text or "") - {"不去", "去了", "取消", "不了", "算了", "改成", "改了"}
        picked, best, second = None, 0, 0
        best_near = None
        if toks:
            for rid, content, e0 in rows:
                ct = _topic_tokens(content)
                ov = len(toks & ct)
                near = abs(float(e0) - float(ts))
                if ov > best:
                    best, second, picked, best_near = ov, best, rid, near
                elif ov == best and best > 0:
                    # 打平：谁离现在更近就是谁（「明天不去了」指的是最近那件）
                    if best_near is None or near < best_near:
                        second, picked, best_near = best, rid, near
                elif ov > second:
                    second = ov
            if best < 1:
                picked = None
        if picked is None:
            if toks:
                return None          # 说了取消、但对不上是哪件事 —— 不动
            picked = rows[0][0]      # 没给线索，就取消最近那条
        self.db.execute(
            "UPDATE tevent SET status='cancelled', closed_ts=?,"
            " reason=COALESCE(NULLIF(reason,''),'') || ? WHERE id=?",
            (ts, "  %s@%s" % (note, time.strftime("%m-%d %H:%M", time.localtime(ts))), picked))
        self.db.commit()
        return picked

    def note_actual(self, text, ts=None):
        """事后补一句「我几点到的」：结掉那条 pending 约定，记实际时刻与早晚差。

        两个要点：
        1. 先把「说出的钟点」贴到**约定那天**（±1 天、上午/下午两个候选里取离约定最近的），
           否则「7点28到的」会被猜成今天 19:28，算出早到 11 小时。
        2. 迟到/早到是相对**约定的准点**算的（meta.base），不是相对窗口起点。
        """
        ts = time.time() if ts is None else float(ts)
        s = (text or "").strip()
        if not s:
            return None
        wins = all_clock_windows(s, ts)
        if not wins:
            return None
        w0 = time.localtime(float(wins[0]["base"]))
        hh, mi = w0.tm_hour, w0.tm_min
        toks = _topic_tokens(s)
        rows = self.db.execute(
            "SELECT id,content,expect_time,meta FROM tevent"
            " WHERE status='pending' AND expect_time BETWEEN ? AND ?"
            " ORDER BY expect_time LIMIT 20",
            (ts - 48 * 3600, ts + 48 * 3600)).fetchall()
        best = None
        for rid, content, e0, meta in rows:
            ct = _topic_tokens(content)
            overlap = len(toks & ct)
            if overlap < 1:
                continue
            e = float(e0)
            try:
                base = float(json.loads(meta or "{}").get("base") or e0)
            except Exception:
                base = e
            act = _snap_clock_to_day(hh, mi, base)
            near = abs(act - base)
            if near > 18 * 3600:
                continue
            if overlap < 2 and near > 6 * 3600:
                continue
            key = (overlap, -int(near))
            if best is None or key > best[0]:
                best = (key, rid, base, act)
        if best is None:
            return None
        _, rid, base, act = best
        delta = int(round((act - base) / 60.0))
        tag = "晚" if delta > 0 else ("早" if delta < 0 else "准点")
        self.db.execute(
            "UPDATE tevent SET status='done', closed_ts=?, actual_ts=?,"
            " reason=COALESCE(reason,'')||? WHERE id=?",
            (ts, act, "  |实际 %s，%s %d 分" % (_hhmm(act), tag, abs(delta)), rid))
        self.db.commit()
        return {"id": rid, "actual": act, "expect": base,
                "delta_min": delta, "early_late": tag}

    def set_state(self, key, val, ts=None):
        ts = time.time() if ts is None else float(ts)
        self.db.execute(
            "INSERT INTO tstate(key,val,updated_ts) VALUES(?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET val=excluded.val, updated_ts=excluded.updated_ts",
            (key, str(val), ts))
        self.db.commit()

    # -- 结算
    def sweep(self, now=None):
        """把超时未完成的 plan 置 expired，返回明细（带 late_hours）。"""
        now = time.time() if now is None else float(now)
        rows = self.db.execute(
            "SELECT id,content,topic_key,importance,expect_end,subject,source"
            " FROM tevent WHERE event_type='plan' AND status='pending'"
            " AND expect_end>0 AND expect_end<?", (now,)).fetchall()
        out = []
        for r in rows:
            late = max(0.0, (now - float(r["expect_end"])) / 3600.0)
            self.db.execute(
                "UPDATE tevent SET status='expired', closed_ts=?, reason=?"
                " WHERE id=? AND status='pending'",
                (now, "auto: expect_end<now", r["id"]))
            out.append({"id": r["id"], "content": r["content"],
                        "kind": r["topic_key"] or "plan",
                        "importance": r["importance"], "late_hours": round(late, 2),
                        "subject": r["subject"], "source": r["source"]})
        if out:
            self.db.commit()
        return out

    def pending(self, limit=5, now=None):
        now = time.time() if now is None else float(now)
        cur = self.db.execute(
            "SELECT id,content,expect_time,expect_end,importance FROM tevent"
            " WHERE event_type='plan' AND status='pending' AND expect_end>=?"
            " ORDER BY expect_end ASC LIMIT ?", (now, int(limit)))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def recent_expired(self, hours=48, limit=3, now=None):
        now = time.time() if now is None else float(now)
        since = now - float(hours) * 3600
        cur = self.db.execute(
            "SELECT id,content,expect_end FROM tevent"
            " WHERE status='expired' AND closed_ts>=? ORDER BY closed_ts DESC LIMIT ?",
            (since, int(limit)))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    # -- 读：拼成上下文里那一段
    def block_text(self, now=None, limit=5):
        now = time.time() if now is None else float(now)
        parts = [now_line(now)]
        try:
            st = dict(self.db.execute(
                "SELECT key,val FROM tstate").fetchall())
        except sqlite3.Error:
            st = {}
        if st.get("where"):
            parts.append("我这会儿在：" + st["where"])
        ps = self.pending(limit=limit, now=now)
        if ps:
            lines = []
            for p in ps:
                w = {"start": p["expect_time"], "end": p["expect_end"], "kind": "part"}
                lines.append("- %s（%s）" % (p["content"][:40], fmt_window(w)))
            parts.append("【没到点的事】\n" + "\n".join(lines))
        ex = self.recent_expired(now=now, limit=2)
        if ex:
            lines = []
            for e in ex:
                late = max(0.0, (now - float(e["expect_end"])) / 3600.0)
                lines.append("- %s（已经晚了 %.0f 小时）" % (e["content"][:40], late))
            parts.append("【过了点没做的】\n" + "\n".join(lines))
        return "\n".join(parts)
