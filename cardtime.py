# -*- coding: utf-8 -*-
"""时间：解析、归一、窗口（从 card2.py 搬出来的第二批）。

为什么单独一个文件：这套东西是**纯函数**——给它一句话和一个"现在"，
它返回日期或窗口，不碰数据库、不看卡、不认识 MemoryCard。
但在 card2 里它夹在中间，改一个时间口径要在一万行里找三处。

规矩同 vmath：只放纯计算。要读库的（比如 event_ts_of 用到的行）传行进来。
"""
import datetime as _dt
import re
import time
import time as _t


from cardcfg import DAY  # noqa: E402

def _weekday_zh(d):
    return "周" + "一二三四五六日"[d.weekday()]

def normalize_time(text, ref_ts=None):
    """把相对时间词替换成绝对日期。ref_ts = 这句话说出来的时刻。"""
    if not text:
        return text
    base = _dt.datetime.fromtimestamp(
        ref_ts if ref_ts is not None else time.time())
    s = text

    def d(offset):
        x = base + _dt.timedelta(days=offset)
        return x.strftime("%m月%d日")

    # 顺序重要：先长后短（"前天" 不能被 "天" 抢走）
    rules = [
        ("大前天", -3), ("前天", -2), ("昨天", -1),
        ("今天", 0), ("明天", 1), ("后天", 2), ("大后天", 3),
    ]
    for word, off in rules:
        if word in s:
            s = s.replace(word, d(off))

    # 「上周X」「这周X」「周X」
    wd = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
    for prefix, week_off in (("上周", -1), ("这周", 0), ("本周", 0), ("下周", 1)):
        for ch, idx in wd.items():
            w = prefix + ch
            if w in s:
                target = base + _dt.timedelta(
                    days=(idx - base.weekday()) + week_off * 7)
                s = s.replace(w, target.strftime("%m月%d日"))

    # 「刚刚」「刚才」「今晚」「今早」→ 具体日期 + 时段
    if "刚刚" in s or "刚才" in s:
        s = s.replace("刚刚", "刚才").replace(
            "刚才", d(0) + " " + base.strftime("%H:%M"))
    s = s.replace("今晚", d(0) + "晚上")
    s = s.replace("今早", d(0) + "早上")
    s = s.replace("今儿", d(0))
    s = s.replace("明天见", d(1) + "见")

    # 🔧 2026-11-05 补词表：真卡核验发现还有 19 条留着「昨晚/明早/上个月」这类词。
    #    ⚠️ 但**「等会/待会/一会儿/晚点」故意不换**：它们在中文里既可以指"今天稍后"，
    #       也可以指"一小会儿"（时长）。实测拿真卡 30 条试过，一换就出这种事：
    #         「这句话我卡了好一会儿才回」→「卡了好10月28日稍后才回」
    #         「真困了我会自己找时间躺一会儿」→「躺10月24日稍后」
    #         「一会儿问想吃大肥鱼，一会儿说…」→「10月24日稍后问…」
    #       那比留着模糊更糟 —— 留着只是不精确，换错是把意思改掉。
    #       这就是「宁可不改，也不许改错」：宁可让 6 条保持模糊，不能损坏 6 条正常的。
    #   下面这些「昨/明/前 + 时段」和月份说法没有歧义，可以安全换。
    # 「昨/明/前 + 时段」：长词优先，否则「昨晚上」会被「昨晚」吃掉一半
    for w, off, tail in (
            ("昨晚上", -1, "晚上"), ("明晚上", 1, "晚上"),
            ("昨早", -1, "早上"), ("昨晚", -1, "晚上"), ("昨夜", -1, "晚上"),
            ("明早", 1, "早上"), ("明晚", 1, "晚上"), ("明夜", 1, "晚上"),
            ("前晚", -2, "晚上"), ("昨儿", -1, ""), ("明儿", 1, ""),
            ("前儿", -2, "")):
        if w in s:
            s = s.replace(w, d(off) + tail)
    # 「上个月/下个月/这个月」→ 年月（不写日，因为原话也没写）
    if "上个月" in s or "下个月" in s or "这个月" in s:
        _y, _m = base.year, base.month

        def _ym(off):
            mm = _m + off
            yy = _y + (mm - 1) // 12
            mm = (mm - 1) % 12 + 1
            return "%d年%d月" % (yy, mm)

        s = s.replace("上个月", _ym(-1))
        s = s.replace("下个月", _ym(1))
        s = s.replace("这个月", _ym(0))

    # 「N 天前」「N 天后」（支持阿拉伯数字和中文数字）
    _CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
               "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

    def _num(s):
        if s.isdigit():
            return int(s)
        return _CN_NUM.get(s, 0)

    def _ago(m):
        n = _num(m.group(1))
        return d(-n) if n else m.group(0)

    s = re.sub(r"([\d一二两三四五六七八九十]{1,2})\s*天前", _ago, s)

    def _later(m):
        n = _num(m.group(1))
        return d(n) if n else m.group(0)

    s = re.sub(r"([\d一二两三四五六七八九十]{1,2})\s*天后", _later, s)

    # 「去年」「今年」→ 年份
    s = s.replace("去年", str(base.year - 1) + "年")
    s = s.replace("今年", str(base.year) + "年")
    s = s.replace("明年", str(base.year + 1) + "年")

    return s

_RE_MD = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")

def parse_event_time(text, ref_ts=None):
    """从一句话里解析「这件事发生在什么时候」。

    返回 (event_ts, precision)：
      precision = 'time'  话里连几点都说了（"昨天下午三点"）
                  'day'   只知道是哪天
                  ''      话里没有时间线索 —— event_ts=0，调用方退回 create_ts

    做法：直接复用写入侧那套 normalize_time（它已经把「昨天/前天/上周三」
    换成绝对日期），再从结果里抠「几月几号」。这样相对时间的口径只有一份，
    不会出现「写入时算成昨天、检索时算成前天」这种两套算法的经典事故。
    """
    if not text:
        return 0.0, ""
    base = float(ref_ts) if ref_ts else time.time()
    try:
        nt = normalize_time(text, ref_ts=base)
    except Exception:
        return 0.0, ""
    m = _RE_MD.search(nt or "")
    if not m:
        return 0.0, ""
    try:
        mo, dd = int(m.group(1)), int(m.group(2))
        ref = _dt.datetime.fromtimestamp(base)
        cand = ref.replace(month=mo, day=dd, hour=12, minute=0,
                           second=0, microsecond=0)
    except (ValueError, OverflowError):
        return 0.0, ""
    # 抠出来的日子比现在晚半年以上 → 说的是去年（"12月25号"在1月说）
    if cand.timestamp() - base > 183 * 86400:
        try:
            cand = cand.replace(year=cand.year - 1)
        except ValueError:
            return 0.0, ""
    prec = "day"
    mt = re.search(r"(\d{1,2})[:：](\d{2})", nt or "")
    if mt:
        try:
            cand = cand.replace(hour=min(23, int(mt.group(1))),
                                minute=min(59, int(mt.group(2))))
            prec = "time"
        except (ValueError, OverflowError):
            pass
    return cand.timestamp(), prec

def event_ts_of(row):
    """取一条记忆的「事发时间」：有 event_ts 用它，没有退回 create_ts。

    为什么要单独一个函数：检索里有六七处按时间比大小，分散写「or create_ts」
    迟早有一处漏掉，然后那处就悄悄按写入时间筛 —— 这类 bug 不报错、只是变笨。
    """
    try:
        ev = float(row["event_ts"] or 0)
    except Exception:
        ev = 0.0
    if ev > 0:
        return ev
    try:
        return float(row["create_ts"] or 0)
    except Exception:
        return 0.0

_ANCHOR_WORDS = [
    ("大前天", -3), ("大后天", 3),
    ("前天", -2), ("后天", 2),
    ("昨天", -1),
    ("今晚", 0), ("今早", 0), ("今天", 0), ("今儿", 0),
    ("明晚", 1), ("明早", 1), ("明天", 1),
    ("去年", "year-1"), ("明年", "year+1"),
]

_RE_ANCHOR = re.compile("|".join(w for w, _ in _ANCHOR_WORDS))

def _anchor_date(ts, delta_days=0):
    t = time.localtime(ts + delta_days * 86400.0)
    return "%d年%d月%d日" % (t.tm_year, t.tm_mon, t.tm_mday)

def anchor_relative_time(text, ts):
    """把一句话里的「昨天/今天/明天…」按 create_ts 换成绝对日期。

    纯函数、可单测。传进来的 ts 是这条记忆自己的时间戳 —— 同一个词在
    不同条目里会换成不同的日期，这正是我们要的（防记串）。

    🔴 只用于「显示/拼上下文」，绝不回写 raw。
    """
    if not text or not ts:
        return text or ""
    if not (_RE_ANCHOR.search(text)
            or re.search(r"(上|这|本|下)?周[一二三四五六日天]", text)
            or re.search(r"([0-9]{1,2}|[一两二三四五六七八九十]{1,2})\s*天(后|前)", text)):
        return text
    table = dict(_ANCHOR_WORDS)

    # 「上周三」这类光靠词表换算不出来 —— 得知道今天是周几。
    def _week(m):
        which, dd = m.group(1) or "", m.group(2)
        idx = "一二三四五六日".find(dd)
        if idx < 0:
            idx = 6  # 「周天」
        cur = time.localtime(ts).tm_wday  # 0 = 周一
        delta = idx - cur
        if which == "上":
            delta -= 7
        elif which == "下":
            delta += 7
        return _anchor_date(ts, delta)

    text = re.sub(r"(上|这|本|下)?周([一二三四五六日天])", _week, text)
    # 「三天后」「两天前」
    _cn = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

    def _days(m):
        raw = m.group(1)
        try:
            n = int(raw)
        except ValueError:
            n = _cn.get(raw, 0)
            if not n:
                return m.group(0)
        return _anchor_date(ts, n * (1 if m.group(2) == "后" else -1))

    text = re.sub(r"([0-9]{1,2}|[一两二三四五六七八九十]{1,2})\s*天(后|前)", _days, text)

    def _rep(m):
        key = m.group(0)
        d = table.get(key)
        if d is None:
            return key
        if isinstance(d, str):
            y = time.localtime(ts).tm_year + (-1 if d == "year-1" else 1)
            return "%d年" % y
        day = _anchor_date(ts, d)
        # 「今晚/今早/明晚/明早」别把时段吃掉：「11月8日」比「11月8日早上」少一半信息
        if key.endswith("晚"):
            return day + "晚上"
        if key.endswith("早"):
            return day + "早上"
        return day

    return _RE_ANCHOR.sub(_rep, text)

_RE_T_TODAY = re.compile(r"刚刚|刚才|今天|今晚|这会儿|现在|眼下")

_RE_T_RECENT = re.compile(r"最近|这两天|这几天|这几天|上周|前几天|这几天")

def query_time_window(query):
    """这句话在问哪个时间段？返回天数，None = 不限。

    🔧 为什么需要：问「今天中午吃了什么」却去翻三年前的事，
    是纯浪费 —— 而且容易翻出不相干的高分旧事，把答案挤掉。
    """
    q = query or ""
    if _RE_T_TODAY.search(q):
        return 3.0
    if _RE_T_RECENT.search(q):
        return 14.0
    return None

def query_time_span(query, now=None):
    """这句话指的是**哪一段时间**？返回 (start_ts, end_ts, precise) 或 None。

    为什么要从「天数」改成「区间」（2026-11-09，用户问「统一时间线和时间标签
    在回忆时有没有真起作用」时查出来的两个洞）：

      ① 只认「今天/刚刚/最近」这几张词表 —— 问「昨天」「前天」「上周三」
         「5月3号那天」时 window 是 None，于是**完全不收窄**，只能靠字面撞；
      ② 而记忆里存的是绝对日期（写入时 normalize_time 已经把「昨天」换成
         「11月8日」），所以问「昨天」和存着的「11月8日」字面上一个词都不重合，
         语义模型也认不出「昨天」=「11月8日」。这就是 45 天那档为什么
         明明在卡里却翻不出来的一部分原因。

    做法：先拿写入侧同一套 normalize_time 把问句里的相对时间换成绝对日期，
    再从问句里抠出「几月几号」。抠到了就是**具体那一天**（precise=True，
    只按 create_ts 落在当天筛，不看 last_active_ts —— 后者会被"被想起过"刷新，
    用它筛"那天"等于把那天的记忆永久打上今天的戳）。抠不到再退回词表的天数窗口。
    """
    q = query or ""
    now = time.time() if now is None else float(now)
    try:
        _t = normalize_time(q, now)
    except Exception:
        _t = q
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", _t or "")
    if m:
        try:
            mo = int(m.group(1)); dd = int(m.group(2))
            base = _dt.datetime.fromtimestamp(now)
            day = _dt.datetime(base.year, mo, dd)
            # 问出来的日子比现在还晚半年以上 → 那多半是去年那天
            if day.timestamp() > now + 180 * DAY:
                day = _dt.datetime(base.year - 1, mo, dd)
            start = day.timestamp()
            return (start, start + DAY, True)
        except (ValueError, OverflowError):
            pass
    win = query_time_window(q)
    if win is None:
        return None
    return (now - win * DAY, now + 1.0, False)
