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
from collections import Counter, defaultdict

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
# 弱：语料还够不上 stable 门槛，但词面干净、lift 高，先小范围借来用。
# 为什么要有它：真卡上"像聊天"的回复只有几十条、跨两三天，stable 要
# 5 次/5 天（language）根本达不到；要么永远 0 条注入（等于这层白做），
# 要么放宽门槛把"会话/的事/开始"这类噪声放进来（等于让她开始演）。
# 第三条路：门面先过一遍词面闸，剩下的才允许以"弱"身份进上下文，
# 而且条数硬限 3 条、排在最末尾、拼句时标出来"这条还弱"。
ST_WEAK = "weak"

# 状态强弱次序：只用来回答一个问题——「这次重算出来的状态，该不该盖掉库里的？」
# candidate 是"还没成形"，weak 是"我认了这条、先弱着注入"，stable/pinned 更硬。
# 没有这个次序，24 小时一次的定期重挖会把 weak 打回 candidate，
# 于是 habit_hints()（只读 stable/pinned/weak）读不到，【说话口气】整段消失。
_HAB_RANK = {ST_CANDIDATE: 0, ST_WEAK: 1, ST_STABLE: 2, ST_PINNED: 3,
             ST_DECAYED: 0, ST_CONFLICT: 0}

# 弱注入的硬上限（再多就成了背词表）
HAB_WEAK_MAX = 3

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
LIFT_MAX = 20.0            # 小样本别炸出几百倍，封顶当"极高"
HAB_LIFT_DROP = 1.2        # < 1.2 倍 → 建议丢弃（背景里也常见，不是口音）
HAB_DISTINCT_DROP = 0.35   # 用户也常说（区分度 < 0.35）→ 建议丢弃

# ---------------------------------------------------------------- 词面闸
# 为什么需要它：纯靠 lift 挑"我常说的"会挑出一堆**语法碎片** ——
# 「跟你 / 刚才 / 你那 / 那句 / 说一 / 我把」在 QQ 语料里全是 top，
# 可它们不是口癖，是任何中文句子都得用的连接词。把它们写进
# 【我是谁】她会开始"背语法"，那比没习惯更糟。
#
# 两条规则（白名单，不是黑名单 —— 列得出"什么算习惯"才算数）：
#   1) 词面必须是**完整的话**：≥4 字，或 ≥3 字且以人称/时间/语气起头
#      （我 / 你 / 别 / 反正 / 其实 / 晚安 / 明天 / 记下 …）；
#   2) 2 字纯功能词（"跟你""你那""那句""我这""说一"）一律不算。
# 宁可少注入几条，也不能让她把"跟你"当口头禅。
_FUNCTION_HEADS = ("跟你", "你那", "那句", "我这", "你那", "说一", "我把",
                   "我刚", "刚才", "以后", "不过", "其实", "就是", "然后",
                   "这个", "那个", "一下", "已经", "还是", "还有", "所以",
                   "因为", "但是", "而且", "什么", "怎么", "可以", "没有")
_PHRASE_HEADS = ("我记下", "记下了", "晚安", "睡吧", "去睡", "反正", "明天",
                 "说过", "不骗", "放心", "别急", "回来", "等你",
                 "我不", "我会", "我先", "我这边", "摸鱼",
                 # 2 字真口癖（不列进来会被 len<=3 的规则误伤）
                 "行吧", "好嘞", "收到", "好呀", "嗯嗯", "好吧", "算了",
                 "没事", "在呢", "哈哈", "好嘛", "知道")


def is_gram_junk(gram, hits=0, days=0, lift=0.0):
    """True = 这只是语法碎片，不该当口癖写进人格。

    纯词面判断，不依赖统计阈值；统计阈值交给 suggest_of / status_of。
    """
    g = (gram or "").strip()
    if not g or len(g) < 2:
        return True
    # 白名单：明确是"一句人话"的，直接放行
    for h in _PHRASE_HEADS:
        if g.startswith(h):
            return False
    # 2~3 字的功能词碎片，一律挡掉
    if len(g) <= 3:
        return True
    # 数字/日期碎片一律挡掉：真卡上挖出过「11月0」「1月07」「月07日」
    # 这种玩意儿，还一路升到 stable —— 那是日期，不是口癖。
    if any(ch.isdigit() for ch in g):
        return True
    if g in _FUNCTION_HEADS:
        return True
    return False
HAB_LIFT_DROP = 1.2        # < 1.2 倍 → 建议丢弃（背景里也常见，不是口音）
HAB_DISTINCT_DROP = 0.35   # 用户也常说（区分度 < 0.35）→ 建议丢弃


def rate_per_1k(hits, total_segments):
    """每千段的出现率，避免两边段数不同导致不可比。"""
    t = float(total_segments or 0)
    if t <= 0:
        return 0.0
    return 1000.0 * float(hits or 0) / t


def lift_of(chat_hits, chat_segments, bg_hits, bg_segments):
    """聊天里的出现率 ÷ 背景里的出现率（加一平滑）。

    为什么不再返回 99.0：背景一次没出现时，旧写法给 99，QQ 语料里几乎
    每个词背景都是 0，结果所有候选 lift 全 99，排序退化成按次数 ——
    背景对照事实上失效。加一平滑后比值是有限的，能分出高下：

        只在我说：chat 6/68 段、背景 0/111 段 → ≈ 11
        大家都说：chat 3/68 段、背景 30/111 段 → ≈ 0.16

    上下限：>= LIFT_MAX 记 LIFT_MAX（避免小样本炸出几百倍）；
    背景为 0 且 chat 也为 0 时给 1.0（等于"没信号"，不该排前面）。
    """
    cs = max(1, int(chat_segments or 0))
    bs = max(1, int(bg_segments or 0))
    a = (float(chat_hits or 0) + 1.0) / (cs + 2.0)
    b = (float(bg_hits or 0) + 1.0) / (bs + 2.0)
    if b <= 0:
        return 1.0
    v = a / b
    if v > LIFT_MAX:
        return LIFT_MAX
    return round(v, 2)


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
           subject_key="", note="", pinned=False, now=None, force_status=""):
    """按 (category,key,scope,subject_key) 合并一条习惯。v1 只给测试/工具用。

    force_status：显式指定状态（"weak" 之类），给了就绕过 status_of。
    为什么需要：弱注入的条目统计上永远是 candidate，但我们要它进上下文；
    与其放宽全局门槛（噪声会跟着进来），不如让写入方明确说"这条我认，先标弱"。
    """
    now = time.time() if now is None else now
    ensure_table(con)
    row = con.execute(
        "SELECT * FROM habits WHERE category=? AND key=? AND scope=? AND subject_key=?",
        (category, key, scope, subject_key)).fetchone()
    ev = json.dumps(evidence or [], ensure_ascii=False)
    conf = confidence_of(hits, days)
    if force_status:
        st = str(force_status)
    else:
        st = status_of(hits, days, conflict=False, pinned=pinned,
                       last_seen=last_seen, now=now, category=category)
    # 🔧 2026-11-10：**不许把已经标过的状态降级回 candidate**。
    #   踩过的坑：15:19 那次挖矿把「晚安/我记下/记下了」写成 weak（弱注入，
    #   进上下文），24 小时后再挖一次，统计量没变（3 次 / 3 天）→ status_of 又
    #   算成 candidate → 覆盖掉 weak → habit_hints 读不到 → 【说话口气】整段消失。
    #   状态是"我认过这条"的标记，不该被一次重扫抹掉；真要衰减交给 _decay/status_of 用
    #   last_seen 判断，而不是靠覆盖。
    if row is not None and not force_status:
        try:
            _old = con.execute(
                "SELECT status FROM habits WHERE id=?", (row[0],)).fetchone()
            _old = (_old[0] if _old else "") or ""
            if _HAB_RANK.get(_old, 0) > _HAB_RANK.get(st, 0):
                st = _old
        except Exception:
            pass
    if row is None:
        _cur = con.execute(
            "INSERT INTO habits(category,key,trigger,behavior,scope,subject_key,"
            "hits,days,first_seen,last_seen,confidence,status,weight,pinned,"
            "conflict,evidence,note,ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (category, key, trigger, behavior, scope, subject_key, hits, days,
             first_seen, last_seen, conf, st, 1.0, 1 if pinned else 0, 0, ev,
             note, now))
        _new_id = _cur.lastrowid
    else:
        # 注意：这里必须用位置索引，不能用 row["id"] —— 调用方的连接
        # 不一定设了 row_factory（mine_habits.write_back 就没设），
        # 按列名取会拿到 tuple 然后 TypeError。SELECT * 的第一列就是 id。
        con.execute(
            "UPDATE habits SET hits=?, days=?, first_seen=?, last_seen=?,"
            " confidence=?, status=?, evidence=?, trigger=?, behavior=?, note=? WHERE id=?",
            (hits, days, first_seen, last_seen, conf, st, ev, trigger, behavior,
             note, row[0]))
    con.commit()
    if row is None:
        _stamp_event(con, "habit", _new_id)
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


# ---------------------------------------------------------------- 挖矿（v1.1）
#
# 2026-11-09：把「挖口头禅」这套逻辑从 tools/mine_habits.py 搬进了层里。
# 为什么搬：线上从来没人跑那个脚本 —— 真卡 habits 表里只有手工挖过一次的 2 行，
# 于是 habit_hints() 永远只吐那两条，这层等于没接上（用户：「所有系统都已全部
# 接入并调用」）。搬进来以后，mind_server 的维护线程每天调一次 mine_and_write()，
# 挖 → 存 → 注入 才是同一条活链路。
# tools/mine_habits.py 现在只是它的命令行外壳（出报告 + JSON），逻辑不再分叉。

_STOP_PHRASES = set("""
的 了 是 在 我 你 他 她 它 们 这 那 有 没 就 都 也 又 还 很 太 不 要 会 能 可 对 吧 啊 呀 呢 哦 嗯 嗯嗯 哈哈 嘿嘿 嘻嘻
一个 一下 一点 一些 这个 那个 这样 那样 什么 怎么 为什么 因为 所以 但是 不过 而且 然后 如果 要是 虽然 或者 还是 就是 只是 已经 现在 时候
我们 你们 他们 自己 别人 东西 事情 问题 地方 时间 感觉 觉得 知道 应该 可以 需要 不用 不是 没有 不会 不能 不要 就是 还有 也是 都是
要是你 你要是 我可以 你可以 我就不 你不 我就 我就说 我不是 我又 你也不 我也不
""".split())

# 纯虚词组成的碎片（"的你""得我""了我"）不是口癖，是 n-gram 切出来的渣
_GRAM_STOP_CHARS = set("的得地了着过是我你他她它们在有没就都也又还太不要会能可对吧啊呀呢哦嗯嘛啦咯哈哟喂嘿唉")

# 开发汇报词（默认排除的补充过滤）
_DEV_WORDS = ("测试", "回归", "提交", "代码", "文件", "接口", "配置", "数据库", "脚本",
              "日志", "字段", "函数", "报错", "修复", "验收", "文档", "仓库", "部署",
              # 2026-11-10 补：她自己给项目写汇报时常用的词，被当成"口癖"挖出来过
              "原文", "池子", "召回", "缓冲", "权重", "插件", "端口", "服务", "重启",
              "进程", "线程", "向量", "索引", "参数", "情境", "主体", "锚", "链完好",
              "窗口", "上下文", "半衰期", "结算", "筛选", "工具", "bug", "Bug")


def _gram_all_function(g):
    """全是虚词/单字功能词拼起来的 -> 丢。"""
    return bool(g) and all(ch in _GRAM_STOP_CHARS for ch in g)


def has_dev_word(s):
    return any(w in s for w in _DEV_WORDS)


def _day_of(ts):
    try:
        return time.strftime("%Y-%m-%d", time.localtime(float(ts)))
    except Exception:
        return "?"


def collect_dialogue(con):
    """从 dialogue 表分两堆：她自己说的（mine）/ 别人说的（theirs）。"""
    rows = [dict(r) for r in con.execute(
        "SELECT id, ts, role, text FROM dialogue ORDER BY id")]
    mine, theirs = [], []
    for r in rows:
        text = (r.get("text") or "").strip()
        if not text:
            continue
        if r["role"] == "assistant":
            r["report_like"] = is_report_like(text) or has_dev_word(text)
            mine.append(r)
        else:
            theirs.append(r)
    return mine, theirs


def mine_phrases(mine, theirs, include_reports=False, bg_rows=None):
    """候选口头禅：2~4 字，出现在我方回复里，统计次数/天数/用户是否也说。

    返回 (cands, totals)，totals 里有聊天段数与背景段数 —— 算 lift 要用。
    背景默认 = 被判定为"像工作汇报"的那批回复（同一个词在聊天里是不是显著更高频）。
    用外部语料时改传 bg_rows=别人的话：那时要问的是「这个词是我常说的，
    还是大家都这么说」—— 那才是口音。
    """
    stat = defaultdict(lambda: {"hits": 0, "days": set(), "ids": [], "samples": [],
                                "full": 0})
    user_stat = Counter()
    bg_hits = Counter()
    chat_segs = bg_segs = 0

    if bg_rows:
        for r in bg_rows:
            for s in segments(r["text"]):
                bg_segs += 1
                for g in ngrams(s, 2, 4):
                    if _HAS_CN_RE.search(g):
                        bg_hits[g] += 1

    for r in theirs:
        for s in segments(r["text"]):
            for g in ngrams(s, 2, 4):
                user_stat[g] += 1

    for r in mine:
        segs = segments(r["text"])
        is_bg = bool(r.get("report_like")) and not bg_rows
        if is_bg and not include_reports:
            # 不算进候选，但算进背景频率
            bg_segs += len(segs)
            for s in segs:
                for g in ngrams(s, 2, 4):
                    if _HAS_CN_RE.search(g):
                        bg_hits[g] += 1
            continue
        chat_segs += len(segs)
        for s in segs:
            core = strip_punct(s)
            if len(core) < 2:
                continue
            # 段级闸（2026-11-10）：整段在讲工程/开发，切出来的 n-gram 不是口癖。
            # 只在词面闸上拦是不够的 —— 「这周的对话原文都在库里，压缩只发生在池子里」
            # 会被切出「话的原文」「对话的原」这类东西，还真能升到 candidate。
            if has_dev_word(core):
                continue
            grams = set()
            # 整段很短（<=8 字）本身就是一个候选；否则取 2~4 字 n-gram
            if len(core) <= 8:
                grams.add(core)
            grams |= ngrams(core, 2, 4)
            for g in grams:
                if g in _STOP_PHRASES or len(g) < 2 or _gram_all_function(g):
                    continue
                if is_gram_junk(g):              # 词面闸：2 字功能词不算口癖
                    continue
                if not _HAS_CN_RE.search(g):     # 纯英文/数字不算口癖
                    continue
                if g.isdigit():
                    continue
                st = stat[g]
                st["hits"] += 1
                # full：这个词曾经**单独成段**（而不是从长句里切出来的片段）。
                # 为什么要记：weak 注入只认「整句短话」——「晚安」是她真会说的一句，
                # 「说过」只是"我说过…"里切出来的渣，凭 lift 高也会被选上去。
                if g == core:
                    st["full"] += 1
                st["days"].add(_day_of(r["ts"]))
                if len(st["ids"]) < 6:
                    st["ids"].append(r["id"])
                if len(st["samples"]) < 3:
                    st["samples"].append(s[:40])

    # 过滤：被更长短语覆盖的短候选（"行吧" 和 "行吧我等着" 同时出现时留长的）
    keys = [k for k, v in stat.items()
            if v["hits"] >= HAB_CAND_MIN_HITS and len(v["days"]) >= HAB_CAND_MIN_DAYS]
    keys.sort(key=len, reverse=True)
    keep = []
    for k in keys:
        if any(k != o and k in o for o in keep):
            continue
        keep.append(k)

    out = []
    for k in keep:
        v = stat[k]
        u = user_stat.get(k, 0)
        # 区分度：她说得多、用户说得少 → 更像她的口音
        distinct = round(v["hits"] / float(v["hits"] + u), 3)
        lift = lift_of(v["hits"], chat_segs, bg_hits.get(k, 0), bg_segs)
        out.append({
            "key": k,
            "hits": v["hits"],
            "days": len(v["days"]),
            "user_hits": u,
            "bg_hits": bg_hits.get(k, 0),
            "full": v.get("full", 0),
            "distinct": distinct,
            "lift": lift,
            "suggest": suggest_of(distinct, lift, v["hits"], len(v["days"])),
            "confidence": confidence_of(v["hits"], len(v["days"])),
            "status": status_of(v["hits"], len(v["days"])),
            "dialogue_ids": v["ids"],
            "samples": v["samples"],
        })
    out.sort(key=lambda x: (x["suggest"] != "keep", -x["lift"], -x["hits"]))
    return out, {"chat_segments": chat_segs, "bg_segments": bg_segs}


def write_candidates(con, cands, weak_top=0, now=None):
    """把建议保留/待定的候选 upsert 进 habits 表，返回写入条数。

    weak_top > 0 时，额外把 lift 最高、词面最干净的前 N 条标成 weak（弱注入）。
    为什么要有弱注入：stable 门槛（language 5 次/5 天）在语料还少的时候一条都
    过不去，这层就白做了；但放宽全局门槛会把噪声放进来。折中：词面闸先过一遍，
    剩下的允许以"弱"身份进上下文，硬限 3 条、排在最后、拼句时标出来。
    """
    ensure_table(con)
    n = 0
    now = float(now if now is not None else time.time())
    for c in cands:
        if c.get("suggest") == "drop":
            continue
        hits = int(c.get("hits") or 0)
        days = int(c.get("days") or 0)
        upsert(con, category="language", key=c["key"], hits=hits,
               days=days, first_seen=now - days * DAY,
               last_seen=now, evidence=c.get("samples") or [],
               note="自动挖出（lift=%.2f，%s）" % (c.get("lift") or 0, c.get("suggest")),
               now=now)
        n += 1
    if weak_top > 0:
        # 弱注入只从「整句短话」里挑：这个词至少两次是单独成段的。
        # 不加这道闸，真卡上第一个被选中的是「说过」（来自"我说过…"的片段），
        # 让她在回复里凭空多一个"说过"的口气，比不注入更糟。
        # 闸门分两档（2026-11-10 调）：
        #   · 两个字的口癖要「单独成段至少一次」（full>=1）——「睡吧」「晚安」这种
        #     真会自己说一句，而「说过」永远是从长句里切出来的渣；
        #   · 三个字以上的短语本身就是完整说法（"我记下了""我自己的"），
        #     不要求单独成段 —— 它们几乎总是带在句子里。
        # 原来一律要求 full>=2，结果真卡上 8 条干净口癖一条都进不了 weak，
        # habit_hints() 只读 weak/stable/pinned，于是【说话口气】整段消失。
        def _ok_weak(c):
            k = c.get("key") or ""
            if c.get("suggest") != "keep" or is_gram_junk(k):
                return False
            if len(k) >= 3:
                return True
            return int(c.get("full") or 0) >= 1
        pool = [c for c in cands if _ok_weak(c)]
        pool.sort(key=lambda x: (-(x.get("lift") or 0), -(x.get("hits") or 0)))
        for c in pool[:int(weak_top)]:
            hits = int(c.get("hits") or 0)
            days = int(c.get("days") or 0)
            upsert(con, category="language", key=c["key"], hits=hits,
                   days=days, first_seen=now - days * DAY,
                   last_seen=now, evidence=c.get("samples") or [],
                   note="弱注入（语料还少，lift=%.2f）：先借来用，"
                        "等跨够 5 天再升 stable" % (c.get("lift") or 0),
                   now=now, force_status=ST_WEAK)
    con.commit()
    return n


def mine_and_write(db_path, weak_top=3, include_reports=False, now=None):
    """生产入口：读一遍对话原文 → 挖口头禅 → 写进 habits 表。

    为什么默认给 weak_top=3：真卡上"像聊天"的回复总共几十条、跨两三天，
    stable 门槛（language 5 次/5 天）一条都过不去；不弱注入的话这层永远
    吐不出东西。3 条正好是 HAB_WEAK_MAX 的硬上限，宁少不滥。
    读用只读连接（不碰原文表），写用独立可写连接。
    """
    path = str(db_path).replace("\\", "/")
    ro = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=10.0)
    ro.row_factory = sqlite3.Row
    try:
        mine, theirs = collect_dialogue(ro)
    finally:
        ro.close()
    if not mine:
        return {"written": 0, "mine": 0, "theirs": len(theirs), "cands": 0}
    cands, tot = mine_phrases(mine, theirs, include_reports=include_reports)
    rw = sqlite3.connect(str(db_path), timeout=30.0)
    try:
        n = write_candidates(rw, cands, weak_top=weak_top, now=now)
    finally:
        rw.close()
    return {"written": n, "mine": len(mine), "theirs": len(theirs),
            "cands": len(cands), "totals": tot}


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

def _stamp_event(con, kind, ref):
    """新行就地盖出生证。失败只打印，留给启动/维护线程补（可修复口径）。"""
    try:
        import timeline as _tl
        old = con.row_factory
        if old is None:
            con.row_factory = sqlite3.Row
        try:
            tl = _tl.Timeline(con, writer=_tl.writer_id())
            return tl.stamp_event(kind, ref)
        finally:
            con.row_factory = old
    except Exception as e:
        print("[habits] %s#%s 出生证没写成：%s: %s"
              % (kind, ref, type(e).__name__, e))
        return None
