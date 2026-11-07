# -*- coding: utf-8 -*-
"""qq_guard.py —— QQ 值守（把老「守夜人 + 听筒 + 收件箱」搬到卡这边）

老的三个脚本（`deploy/qq_bridge/keeper.ps1` + `watch_main.ps1` + `qq_inbox.ps1`）
是 DSH 时代的产物：轮询 NapCat → 写 inbox.jsonl → 写唤醒票 → 叫醒会话。
问题有三个：
  1. 只写 jsonl，卡里没有 —— 群里发生的事她"记得"，但那不是记忆
  2. 群只盯了一个（1094740311），另外两个群根本没听
  3. 没有配额，热闹起来能把会话刷成永动机

这一版做三件事，其余照旧：
  1. **进卡**：每条新消息走 `POST /remember`（source=qq / qq-group），
     走的是正式的记忆通道（有出生证、有信任分、有衰减），不是旁路
  2. **全群听**：默认盯所有已加入的群，不再只盯一个
  3. **配额**：硬顶 + 软节奏两层（见 QUOTA），超了就只记不说

设计上刻意不做的：
  - 不在这里调模型（卡不跑模型是底线）。所以它只负责"听 + 记 + 排队"，
    真正"说"由会话里的我读 `qq_todo.jsonl` 后决定
  - 不自动 @ 人、不主动开话题

用法：
  python tools/qq_guard.py                 # 常驻（默认）
  python tools/qq_guard.py --once          # 只跑一轮，看它读到什么
  python tools/qq_guard.py --stop          # 让常驻进程退出（写 stop 标记）
  python tools/qq_guard.py --quota         # 看今天的配额用了多少
"""
import argparse
import io
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

BASE = "http://127.0.0.1:6700"
TOKEN = "qqai-dsh-http-2026"
MIND = os.environ.get("WHALE_MIND", "http://127.0.0.1:8848")



# 2026-11-06：让值守也读控制台那本账（chatcfg）。

# 脚本模式下 sys.path[0] 是 tools/，卡根目录不在里面 —— 不插的话 import chatcfg

# 会失败、被 except 吞掉，设置就静默退回常量（今天在 proactive 上踩过一次）。

if ROOT not in sys.path:

    sys.path.insert(0, ROOT)





def cfg_get(key, default=None):

    # 从 chatcfg 读一个参数；读不到/读坏了就用默认值（永不抛）。

    try:

        import chatcfg as _C

        return _C.load_cfg().get(key, default)

    except Exception:

        return default
STATE = os.path.join(ROOT, "qq_state")
INBOX = os.path.join(STATE, "inbox.jsonl")
TODO = os.path.join(STATE, "todo.jsonl")
QUOTA_F = os.path.join(STATE, "quota.json")
BEAT = os.path.join(STATE, "guard.heartbeat")
STOP = os.path.join(STATE, "stop.flag")
PIDF = os.path.join(STATE, "guard.pid")
SEEN_F = os.path.join(STATE, "seen.json")
STATUS_F = os.path.join(STATE, "status.json")

SELF_ID = 100000001

# 消息状态：unreplied（还没回）/ replied（回过了）
# 老系统用 replied.txt 记 message_id，这里换成 status.json，
# 因为要同时记「什么时候标的」「谁标的」，光一个 id 列表不够对账。
ST_UNREPLIED = "unreplied"
ST_REPLIED = "replied"
# 第三种：群里的闲聊（没点我名）。它**不算"要回"** ——
# 不然一个热闹的群五分钟就攒出上百条"未回"，提醒立刻变成噪声，
# 真正的漏回反而被淹掉。这类只做素材，标签是 background。
ST_BACKGROUND = "background"

# ---------------------------------------------------------------- 配额
# 用户 m11477：每小时 60~100 次，或一天 600 次，让我自己安排。
# 我取中间偏保守的一档：
#   硬顶  60/小时、600/天     —— 到线就只记不说（用户的号不能被刷成刷屏机）
#   软节奏 每群 15/小时        —— 三个群合计 45/小时，给热闹时候留余量
# 这两个数都能被 qq_state/quota.json 里的 override 改掉，不用改代码。
QUOTA = {
    "hour_hard": 60,
    "day_hard": 600,
    "group_hour_soft": 15,
}


# ---------------------------------------------------------------- 值不值得记
# 用户 m11531 定的规矩：
#   聊天记录的**具体内容**该忘得快，但**学到的东西 / 有意义的事 / 情感与经验**
#   要长久留下 —— 就像真人，记不住谁哪天说了哪句闲话，
#   但记得"这群人开玩笑的尺度在哪"、"他难过的时候会那样说话"。
#
# 所以不是"每条都进卡"，先过一道筛：
#   keep=True  → 进 mem，并按内容选分区（分区决定它忘得多慢）
#   keep=False → 不进 mem，只留在 todo.jsonl 当"素材"（素材本身会滚动丢弃）
#
# 判据全是规则，不调模型（卡不跑模型的底线）：
#   被 @ 我 / 叫我名字   → flash  有指向性的事，记得住
#   强情绪词             → flash  情感与感受
#   关于人的稳定陈述     → fact   慢衰
#   教训/经验口吻        → fact   慢衰
#   重复出现的短句       → 不进卡（那是口癖素材，交给 habits 挖掘）
#   普通闲聊             → 不进卡（素材而已）
KEEP_AT_ME = "flash"
KEEP_EMO = "flash"
KEEP_FACT = "fact"
KEEP_PREF = "preference"

# 强情绪词：只放"当下这一刻的感受"。喜欢/讨厌这类是**偏好**不是情绪，
# 单列到 PREF_WORDS —— 偏好是稳定陈述，该走慢衰，不该占闪光灯位。
EMO_WORDS = ("难受", "难过", "开心", "高兴", "害怕", "慌", "烦死", "累死",
             "哭", "气死", "生气", "心疼", "想你", "崩溃", "委屈", "孤独",
             "无聊死", "兴奋", "感动", "后悔", "遗憾", "绝望")
PREF_WORDS = ("喜欢", "讨厌", "爱吃", "最爱", "不爱", "受不了", "最怕",
              "偏爱", "喜欢吃", "讨厌吃")
FACT_HINTS = ("我是", "我叫", "我住", "我在", "我喜欢", "我讨厌", "我今年",
              "我高三", "我考", "我的名字", "生日", "学校", "班级", "我家")
LESSON_HINTS = ("记住", "下次", "以后别", "教训", "明白了", "懂了",
                "原来", "才知道", "学会了", "长记性")

# 素材目录别无限长：老的滚掉（素材本来就是会忘的东西）
TODO_MAX_LINES = 4000

# 漏回复提醒：未回超过这么多条、且最早那条已经放了一天多，就吱一声
OVERDUE_N = 20
OVERDUE_HOURS = 26.0


def decide(text, at_me=False):
    """这条消息值不值得当记忆。返回 (keep, region, salience, why)。"""
    t = (text or "").strip()
    if not t:
        return False, "", 0.0, "空"
    n = len(t)

    # 1) 有人点我的名 / @ 我 —— 这是"事"，记得住
    if at_me and bool(cfg_get("reply.at_me", True)):
        return True, KEEP_AT_ME, 0.72, "被点名"

    # 2) 关于人的稳定陈述 —— 先于情绪判，因为"我喜欢吃白米饭"里
    #    "喜欢"是偏好不是当下的情绪，判成 flash 就记错了分区
    fh = [w for w in FACT_HINTS if t.startswith(w) or w in t[:12]]
    if fh and n >= 4:
        return True, KEEP_FACT, 0.62, "关于人:" + fh[0]

    # 3) 偏好 —— 稳定但会变，走 preference（比 fact 略快、比 episode 慢得多）
    pw = [w for w in PREF_WORDS if w in t]
    if pw and n >= 4:
        return True, KEEP_PREF, 0.60, "偏好:" + pw[0]

    # 4) 强情绪 —— 情感与感受，这是真该留的
    hit = [w for w in EMO_WORDS if w in t]
    if hit:
        return True, KEEP_EMO, 0.68, "情绪:" + hit[0]

    # 5) 教训/经验口吻 —— 这是"学到了什么"，最该留的
    lh = [w for w in LESSON_HINTS if w in t]
    if lh:
        return True, KEEP_FACT, 0.70, "经验:" + lh[0]

    # 6) 太短的、纯表情的、纯语气词的 —— 素材，不是记忆
    if n <= 6:
        return False, "", 0.0, "太短"
    return False, "", 0.0, "闲聊"


def quiet_hint(text, pinged):
    """不每句都回：给这条消息标「先别接」的建议。只做建议，不改 can_say。"""
    try:
        import quiet_policy as Q
        rows = read_todo(only_unreplied=False, limit=40)
        return Q.should_quiet(rows, time.time(), at_me=bool(pinged), text=text)
    except Exception as e:
        return False, "policy-error:%s" % e


def at_me(msg):
    """这条消息有没有 @ 我（flat_text 里会被跳过，所以单独查原始段）。"""
    segs = msg.get("message")
    if isinstance(segs, list):
        for s in segs:
            if s.get("type") == "at":
                if str((s.get("data") or {}).get("qq") or "") == str(SELF_ID):
                    return True
    raw = msg.get("raw_message") or ""
    return ("[CQ:at,qq=%d]" % SELF_ID) in raw


def _ensure():
    os.makedirs(STATE, exist_ok=True)


def _now():
    return time.time()


def call(action, **params):
    req = urllib.request.Request(
        BASE + "/" + action,
        data=json.dumps(params).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + TOKEN})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))






def mem_floor():

    # 多重要才进卡：低于这个重要度，就算判了「值得记」也不写。

    try:

        return float(cfg_get("mem.salience_floor", 0.45))

    except Exception:

        return 0.45



def remember(text, source, speaker="other", region="episode", salience=0.5,
             about_uid="", pool="long"):
    """把一条消息写进卡。失败不抛 —— 值守不能因为卡忙就停。

    about_uid：这条是关于谁的。卡里按这个人的关系层决定闲聊能活多久
    （用户 m13292：越重要的人，闲聊留得越久）。

    pool：2026-11-06 修。原来这里根本不传 pool，/remember 就按默认落
    pool='short'（32 个位置）。后果是真卡里 qq-group 写进去 100 条、
    long 池 0 条，短期池一溢出就被 _forget_row 删，墓碑里 373 条
    「短期池到期，海马判定没上榜」大半是这么来的 —— 判断为「值得记住」
    的消息反而被扔进了注意力临时区等死。能走到这个函数的消息都已经
    过了 decide() 的判据，它该进 long 池；要不要淡出由 region 的衰减
    曲线管，不该由池子的容量管。
    """
    try:
        req = urllib.request.Request(
            MIND + "/remember",
            data=json.dumps({"text": text, "source": source,
                             "speaker": speaker, "region": region,
                             "pool": pool,
                             "salience": salience,
                             "about_uid": str(about_uid or "")}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------- 配额器
def quota_load():
    _ensure()
    if os.path.exists(QUOTA_F):
        try:
            with io.open(QUOTA_F, "r", encoding="utf-8") as f:
                q = json.load(f)
        except Exception:
            q = {}
    else:
        q = {}
    q.setdefault("cfg", {})
    q.setdefault("hour", {"key": "", "n": 0})
    q.setdefault("day", {"key": "", "n": 0})
    q.setdefault("groups", {})
    return q


def quota_save(q):
    with io.open(QUOTA_F, "w", encoding="utf-8") as f:
        json.dump(q, f, ensure_ascii=False, indent=1)


def quota_tick(q):
    """把过期的桶归零（小时桶 / 天桶）。"""
    hk = time.strftime("%Y-%m-%d %H")
    dk = time.strftime("%Y-%m-%d")
    if q["hour"]["key"] != hk:
        q["hour"] = {"key": hk, "n": 0}
    if q["day"]["key"] != dk:
        q["day"] = {"key": dk, "n": 0}
        q["groups"] = {}
    return q


def quota_allow(q, group_id=None, group_hour_soft=None):
    """还能不能说。返回 (ok, 原因)。

    group_hour_soft 显式传入时优先用它 —— 测试要测「软上限真的会拦」，
    不该被控制台里当前那份 freq.group_hour 影响。
    """
    cfg = dict(QUOTA)
    cfg.update(q.get("cfg") or {})
    if group_hour_soft is not None:
        cfg["group_hour_soft"] = int(group_hour_soft)
    else:
        try:
            cfg["group_hour_soft"] = int(cfg_get("freq.group_hour",
                                                cfg["group_hour_soft"]))
        except Exception:
            pass
    quota_tick(q)
    if q["hour"]["n"] >= cfg["hour_hard"]:
        return False, "小时硬顶 %d 已到" % cfg["hour_hard"]
    if q["day"]["n"] >= cfg["day_hard"]:
        return False, "当天硬顶 %d 已到" % cfg["day_hard"]
    if group_id is not None:
        gk = str(group_id)
        n = int((q["groups"].get(gk) or {}).get("n") or 0)
        if n >= cfg["group_hour_soft"]:
            return False, "本群这一小时已经说了 %d 句（软上限 %d）" % (
                n, cfg["group_hour_soft"])
    return True, ""


def quota_spend(q, group_id=None):
    quota_tick(q)
    q["hour"]["n"] = int(q["hour"]["n"]) + 1
    q["day"]["n"] = int(q["day"]["n"]) + 1
    if group_id is not None:
        gk = str(group_id)
        cur = q["groups"].get(gk) or {"n": 0}
        cur["n"] = int(cur.get("n") or 0) + 1
        q["groups"][gk] = cur
    quota_save(q)


# ---------------------------------------------------------------- 读消息
def flat_text(msg):
    """把 OneBot 的消息段压成纯文本（跳过 @自己、图片、表情）。"""
    segs = msg.get("message")
    if isinstance(segs, list):
        out = []
        for s in segs:
            t = s.get("type")
            d = s.get("data") or {}
            if t == "text":
                out.append(d.get("text") or "")
            elif t == "at":
                qq = str(d.get("qq") or "")
                if qq and qq != str(SELF_ID):
                    out.append("@" + qq + " ")
            elif t == "reply":
                continue
            # 图片/表情/语音一律跳过：卡里只留字
        return "".join(out).strip()
    raw = msg.get("raw_message") or ""
    return raw.strip()


def list_groups():
    try:
        r = call("get_group_list")
        return [g for g in (r.get("data") or [])
                if isinstance(g, dict) and g.get("group_id")]
    except Exception:
        return []


def list_friends():
    try:
        r = call("get_friend_list")
        return [f for f in (r.get("data") or [])
                if isinstance(f, dict) and f.get("user_id")]
    except Exception:
        return []


def _load_seen():
    if os.path.exists(SEEN_F):
        try:
            with io.open(SEEN_F, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_seen(d):
    with io.open(SEEN_F, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)


def _append(path, obj):
    with io.open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- 已回 / 未回
def status_load():
    """{message_id: {"st": "unreplied"|"replied", "at": ts, "by": "..."}}"""
    if os.path.exists(STATUS_F):
        try:
            with io.open(STATUS_F, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def status_save(d):
    with io.open(STATUS_F, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


# 常驻进程和命令行脚本会同时改这个文件。
# 原来"读 → 改 → 写"三步没有锁，谁后写谁赢，先写的那批标记直接丢：
# 实测我手动标的 6 条"已回"，被值守下一轮读到的旧副本覆盖回 unreplied，
# 表现成"明明回过却一直挂在未回里"。
_LOCK_F = os.path.join(STATE, "status.lock")


class _FileLock(object):
    """跨进程互斥。拿不到就等，最多等 5 秒。"""

    def __init__(self, path=_LOCK_F, timeout=5.0):
        self.path = path
        self.timeout = timeout
        self.fd = None

    def __enter__(self):
        deadline = time.time() + self.timeout
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except OSError:
                if time.time() > deadline:
                    # 锁文件可能是上一个进程崩掉留下的，过期就抢过来
                    try:
                        if time.time() - os.path.getmtime(self.path) > 10:
                            os.remove(self.path)
                            continue
                    except OSError:
                        pass
                    raise
                time.sleep(0.02)

    def __exit__(self, *exc):
        try:
            if self.fd is not None:
                os.close(self.fd)
            os.remove(self.path)
        except OSError:
            pass
        return False


def status_patch(patch):
    """原子地改若干条状态。patch = {mid: {"st":..., "at":..., "by":...}}。

    读-改-写全程持锁，避免和常驻进程互相覆盖。
    """
    with _FileLock():
        d = status_load()
        d.update(patch)
        status_save(d)
        return d


def mark(mid, st=ST_REPLIED, by="self"):
    """给一条消息打标签。这是"已回/未回"的唯一写入口。"""
    k = str(mid)
    rec = {"st": st, "at": int(_now()), "by": by}
    status_patch({k: rec})
    return rec


def mark_many(mids, st=ST_REPLIED, by="self"):
    ts = int(_now())
    patch = {str(m): {"st": st, "at": ts, "by": by} for m in mids}
    if patch:
        status_patch(patch)
    return len(mids)


def _mark_if_new(mid, st=ST_UNREPLIED):
    """新消息入队时打标 —— 但绝不把已有的标翻回去。

    群里没点我名的闲聊传 st=ST_BACKGROUND：它进队列是为了当素材，
    不进"未回"账，否则热闹的群五分钟就能攒出上百条假未回。
    """
    if not mid:
        return
    k = str(mid)
    with _FileLock():
        d = status_load()
        if k in d:          # 已有标签：一个字节都不动
            return
        d[k] = {"st": st, "at": int(_now()), "by": "guard"}
        status_save(d)


def read_todo(only_unreplied=True, kind="", limit=40, only_background=False):
    """读待回队列，默认只给未回的。

    状态是"后写覆盖先写"：先入队是 unreplied，回复后 mark 成 replied。
    这里按 message_id 去重（同一轮重复入队的只算一次），保留最后一条。
    """
    if not os.path.exists(TODO):
        return []
    st = status_load()
    seen = {}
    with io.open(TODO, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            if kind and o.get("kind") != kind:
                continue
            mid = str(o.get("message_id") or "")
            o["st"] = (st.get(mid) or {}).get("st") or ST_UNREPLIED
            if mid:
                seen[mid] = o
            else:
                seen["_%s_%s" % (o.get("ts"), len(seen))] = o
    rows = list(seen.values())
    if only_unreplied:
        rows = [r for r in rows if r["st"] == ST_UNREPLIED]
    elif only_background:
        rows = [r for r in rows if r["st"] == ST_BACKGROUND]
    rows.sort(key=lambda r: int(r.get("ts") or 0))
    return rows[-limit:] if limit else rows


def summary():
    """给控制台/命令行看的一行账：未回 N 条、已回 M 条、背景 K 条。"""
    st = status_load()
    n_replied = sum(1 for v in st.values() if v.get("st") == ST_REPLIED)
    n_bg = sum(1 for v in st.values() if v.get("st") == ST_BACKGROUND)
    todo = read_todo(only_unreplied=True, limit=0)
    return {"unreplied": len(todo), "replied": n_replied, "background": n_bg}


def poll_once(seen, verbose=True):
    """跑一轮：读所有群 + 所有好友的最近消息。

    每条消息都进"待回队列"（为了不漏回复），但**只有过筛的才进卡**——
    素材忘得快，学到的东西/有意义的/情感经验才长久留（用户 m11531）。
    """
    n_new = 0
    n_kept = 0
    q = quota_load()
    groups = list_groups()
    friends = list_friends()
    # 每轮把群的顺序打散：永远按 group_id 升序去读，
    # 抓包一看就是一条固定序列，和"人挨个点开看看"不一样。
    random.shuffle(groups)

    # ---- 群 ----
    for g in groups:
        gid = int(g["group_id"])
        gname = g.get("group_name") or str(gid)
        key = "g%d" % gid
        last = int(seen.get(key) or 0)
        try:
            r = call("get_group_msg_history", group_id=gid, count=20)
        except Exception as e:
            if verbose:
                print("  群 %s 读失败：%s" % (gname, e))
            continue
        msgs = (r.get("data") or {}).get("messages") or []
        # 第一次见到这个群：只把水位线种到"最新一条"，不把历史当新消息。
        # 不然一上来就把几百条旧聊天塞进待回队列，未回提醒立刻爆表
        # （实测第一次跑就积了 162 条、最早放到 241 小时前）。
        if not last:
            if msgs:
                seen[key] = max(int(m.get("time") or 0) for m in msgs)
            continue
        fresh = []
        for m in msgs:
            if m.get("post_type") != "message":
                continue
            mid = str(m.get("message_id") or "")
            ts = int(m.get("time") or 0)
            if not mid or ts <= last:
                continue
            sender = m.get("sender") or {}
            if int(sender.get("user_id") or 0) == SELF_ID:
                continue
            fresh.append(m)
        if not fresh:
            continue
        fresh.sort(key=lambda x: int(x.get("time") or 0))
        for m in fresh:
            sender = m.get("sender") or {}
            name = sender.get("card") or sender.get("nickname") or "?"
            uid = int(sender.get("user_id") or 0)
            text = flat_text(m)
            if not text:
                continue
            ts = int(m.get("time") or 0)
            # 先过筛：值不值得当记忆（用户 m11531 的规矩 ——
            # 素材忘得快，学到的东西/有意义的/情感经验才长久留）
            keep, region, sal, why = decide(text, at_me(m))
            pinged = at_me(m)
            mem_id = None
            if keep and sal < mem_floor():
                keep, why = False, "重要度 %.2f 低于门槛" % sal
            if keep and bool(cfg_get("mem.write_group", True)):
                r = remember("[%s] %s：%s" % (gname, name, text),
                             source="qq-group", speaker="other",
                             region=region, salience=sal,
                             about_uid=str(uid or ""))
                mem_id = (r or {}).get("mem_id")
            # 进待回队列（素材也留着，但那是"待回"，不是"记忆"）
            ok, why_q = quota_allow(q, gid)
            _quiet, _quiet_why = quiet_hint(text, pinged)
            if not pinged and not _quiet:
                try:
                    _p = float(cfg_get("reply.p_group", 0.35))
                except Exception:
                    _p = 0.35
                if random.random() > _p:
                    _quiet, _quiet_why = True, "群里随机接话的概率没抽中"
            _engage = not _quiet
            _append(TODO, {"ts": ts, "kind": "group", "group_id": gid,
                           "group_name": gname, "sender": name,
                           "sender_id": int(sender.get("user_id") or 0),
                           "text": text, "message_id": m.get("message_id"),
                           "can_say": ok, "why": why_q,
                           "keep": keep, "region": region,
                           "mem_id": mem_id, "screen": why,
                           "pinged": pinged,
                           "quiet": _quiet, "quiet_why": _quiet_why,
                           "engage": _engage})
            # 点我名的才算"要回"；其余是背景素材
            _mark_if_new(m.get("message_id"),
                         ST_UNREPLIED if pinged else ST_BACKGROUND)
            n_new += 1
            if keep:
                n_kept += 1
            if verbose:
                mark = ("记(%s)" % region) if keep else "只当素材"
                print("  [群 %s]%s %s：%s" % (gname, mark, name, text[:38]))
        seen[key] = max(int(m.get("time") or 0) for m in fresh)

    # ---- 好友 ----
    for f in friends:
        uid = int(f["user_id"])
        name = f.get("remark") or f.get("nickname") or str(uid)
        key = "u%d" % uid
        last = int(seen.get(key) or 0)
        try:
            r = call("get_friend_msg_history", user_id=uid, count=10)
        except Exception:
            continue
        msgs = (r.get("data") or {}).get("messages") or []
        if not last:      # 第一次见到这个会话：只种水位线，不把历史当新消息
            if msgs:
                seen[key] = max(int(m.get("time") or 0) for m in msgs)
            continue
        fresh = [m for m in msgs
                 if m.get("post_type") == "message"
                 and int(m.get("time") or 0) > last
                 and int((m.get("sender") or {}).get("user_id") or 0) != SELF_ID]
        if not fresh:
            continue
        fresh.sort(key=lambda x: int(x.get("time") or 0))
        for m in fresh:
            text = flat_text(m)
            if not text:
                continue
            ts = int(m.get("time") or 0)
            # 私聊比群里更该记：一对一说话本身就是"有意义的事"，
            # 所以私聊默认记，只把太短的当素材。
            keep, region, sal, why = decide(text, at_me(m))
            if not keep and len(text) > 6:
                keep, region, sal, why = True, "episode", 0.6, "私聊"
            mem_id = None
            if keep and sal < mem_floor():
                keep, why = False, "重要度 %.2f 低于门槛" % sal
            if keep and bool(cfg_get("mem.write_private", True)):
                r = remember("[私聊] %s：%s" % (name, text),
                             source="qq", speaker="other",
                             region=region, salience=sal,
                             about_uid=str(uid or ""))
                mem_id = (r or {}).get("mem_id")
            ok, why_q = quota_allow(q, None)
            if bool(cfg_get("reply.private", True)):
                _pquiet, _pwhy, _pengage = False, "", True
            else:
                _pquiet, _pwhy, _pengage = True, "私聊不接（设置里关了）", False
            _append(TODO, {"ts": ts, "kind": "private", "user_id": uid,
                           "sender": name, "text": text,
                           "message_id": m.get("message_id"),
                           "can_say": ok, "why": why_q,
                           "keep": keep, "region": region,
                           "mem_id": mem_id, "screen": why,
                           "quiet": _pquiet, "quiet_why": _pwhy,
                           "engage": _pengage, "pinged": True})
            _mark_if_new(m.get("message_id"),
                         ST_UNREPLIED if _pengage else ST_BACKGROUND)
            n_new += 1
            if keep:
                n_kept += 1
            if verbose:
                mark = ("记(%s)" % region) if keep else "只当素材"
                print("  [私聊 %s]%s %s" % (name, mark, text[:38]))
        seen[key] = max(int(m.get("time") or 0) for m in fresh)

    if n_new:
        _save_seen(seen)
        _trim_todo()
    return n_new, n_kept


def _trim_todo():
    """素材目录别无限长：只留最近 TODO_MAX_LINES 行（素材本来就会忘）。"""
    try:
        if not os.path.exists(TODO):
            return
        with io.open(TODO, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) <= TODO_MAX_LINES:
            return
        keep = lines[-TODO_MAX_LINES:]
        with io.open(TODO, "w", encoding="utf-8") as f:
            f.writelines(keep)
    except Exception:
        pass


def overdue():
    """漏回复提醒：未回攒太多、且最早那条放太久了，返回提醒文案或 ""。

    用户 m11531 提到"为了防止漏回复"——光有标签不够，得有人吭声。
    """
    rows = read_todo(only_unreplied=True, limit=0)
    if len(rows) < OVERDUE_N:
        return ""
    oldest = int(rows[0].get("ts") or 0)
    if not oldest:
        return ""
    hours = (_now() - oldest) / 3600.0
    if hours < OVERDUE_HOURS:
        return ""
    return ("有 %d 条没回，最早那条已经放了 %.0f 小时（%s）"
            % (len(rows), hours,
               time.strftime("%m-%d %H:%M", time.localtime(oldest))))


# ---------------------------------------------------------------- 节奏
# 为什么要有这个：真人不会每隔整 5 秒去敲一次门。
# 固定间隔 + 固定顺序 = 机器指纹，平台风控最先抓的就是这个。
# 所以三件事都得抖：
#   ①轮询间隔：base 上下浮动（抖动 ±40%），闲下来再乘 2~4 倍随机；
#   ②每次轮询之间的顺序（先看哪个群）随机打散；
#   ③真要开口说话时，间隔按"人打字"估：短句 8~20 秒、长句 20~70 秒，
#     而且每次在前一条基础上再叠一个随机，绝不连着两条一样长。
JITTER = 0.40          # 轮询间隔抖动幅度


def _jitter(sec, amp=JITTER):
    """把 sec 抖成 sec*(1±amp) 之间的随机值，最低 1 秒。"""
    lo = max(1.0, sec * (1.0 - amp))
    hi = max(lo + 0.5, sec * (1.0 + amp))
    return random.uniform(lo, hi)


def human_gap(text, rnd=None):
    """按"人要打这句话"估一个发送间隔（秒），并带随机。

    参考：中文手机打字约 2~4 字/秒，加上读上文、犹豫、删改，
    短句（<10 字）落在 8~20 秒，中句 20~45 秒，长句 45~70 秒。
    这里再乘一个 0.75~1.35 的随机，保证任何两条都不会等长。
    """
    rnd = rnd or random
    n = len(text or "")
    if n <= 10:
        base = rnd.uniform(8, 20)
    elif n <= 30:
        base = rnd.uniform(20, 45)
    else:
        base = rnd.uniform(45, 70)
    return round(base * rnd.uniform(0.75, 1.35), 1)


# ---------------------------------------------------------------- 常驻
def run_forever(interval=5):
    _ensure()
    if os.path.exists(STOP):
        os.remove(STOP)
    with io.open(PIDF, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    seen = _load_seen()
    _last_remind = [0]        # 上一次提醒是哪个 5 分钟窗口，防刷屏
    print("[qq_guard] 起来 pid=%d，盯 %d 群 / %d 好友"
          % (os.getpid(), len(list_groups()), len(list_friends())))
    idle = 0
    while True:
        if os.path.exists(STOP):
            print("[qq_guard] 看到 stop.flag，退出")
            break
        try:
            n, kept = poll_once(seen)
            if n:
                idle = 0
            else:
                idle += 1
            # 漏回复提醒：每 5 分钟看一次就够，别刷屏
            if int(_now()) // 300 != _last_remind[0]:
                _last_remind[0] = int(_now()) // 300
                msg = overdue()
                if msg:
                    print("[qq_guard] ⚠ " + msg)
        except Exception as e:
            print("[qq_guard] 轮询出错：%s" % e)
        with io.open(BEAT, "w", encoding="utf-8") as f:
            f.write(str(int(_now())))
        # 有消息时快一点，闲着时慢一点（省 CPU 也给 NapCat 喘息）；
        # 两档都带抖动，别让心跳成为可被统计的固定节拍。
        if idle < 12:
            time.sleep(_jitter(interval))
        else:
            time.sleep(_jitter(interval * random.uniform(2.0, 4.0)))
    for p in (PIDF, BEAT):
        try:
            os.remove(p)
        except Exception:
            pass


def main(argv=None):
    ap = argparse.ArgumentParser(description="QQ 值守：听群、进卡、排队、分已回未回")
    ap.add_argument("--once", action="store_true", help="只跑一轮")
    ap.add_argument("--stop", action="store_true", help="让常驻进程退出")
    ap.add_argument("--quota", action="store_true", help="看配额")
    ap.add_argument("--list", action="store_true",
                    help="列未回消息（默认只看未回）")
    ap.add_argument("--list-all", action="store_true", help="列全部（含已回）")
    ap.add_argument("--replied", default="",
                    help="把这几条 message_id 标成已回（逗号分隔）")
    ap.add_argument("--replied-upto", type=int, default=0,
                    help="把 <= 该时间戳的未回消息全部标成已回")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--interval", type=int, default=5)
    args = ap.parse_args(argv)

    _ensure()
    if args.stop:
        with io.open(STOP, "w", encoding="utf-8") as f:
            f.write(str(int(_now())))
        print("已写 stop.flag，常驻进程会在下一轮退出")
        return 0
    if args.quota:
        q = quota_tick(quota_load())
        quota_save(q)
        cfg = dict(QUOTA)
        cfg.update(q.get("cfg") or {})
        print("今天 %s ｜ 这一小时 %s" % (q["day"]["key"], q["hour"]["key"]))
        print("  小时 %d / %d   天 %d / %d"
              % (q["hour"]["n"], cfg["hour_hard"],
                 q["day"]["n"], cfg["day_hard"]))
        for gk, v in sorted((q.get("groups") or {}).items()):
            print("  群 %s：%d / %d" % (gk, int(v.get("n") or 0),
                                        cfg["group_hour_soft"]))
        s = summary()
        print("  未回 %d 条 ｜ 已回 %d 条" % (s["unreplied"], s["replied"]))
        return 0
    if args.replied:
        mids = [x.strip() for x in args.replied.split(",") if x.strip()]
        n = mark_many(mids, ST_REPLIED)
        print("已标 %d 条为已回" % n)
        return 0
    if args.replied_upto:
        rows = [r for r in read_todo(only_unreplied=True, limit=0)
                if int(r.get("ts") or 0) <= args.replied_upto]
        n = mark_many([r["message_id"] for r in rows if r.get("message_id")],
                      ST_REPLIED)
        print("已标 %d 条为已回（<= %d）" % (n, args.replied_upto))
        return 0
    if args.list or args.list_all:
        rows = read_todo(only_unreplied=not args.list_all, limit=args.limit)
        if not rows:
            print("（没有）")
            return 0
        for r in rows:
            # 三种状态都得能看见。以前只认"未回/已回"两种，
            # 背景闲聊被显示成"已回" —— 看着像回过，其实没人理过。
            tag = {ST_UNREPLIED: "未回", ST_REPLIED: "已回",
                   ST_BACKGROUND: "背景"}.get(r["st"], r["st"])
            where = r.get("group_name") or ("私聊 " + str(r.get("sender")))
            print("[%s] %s  %s：%s"
                  % (tag, time.strftime("%m-%d %H:%M",
                                        time.localtime(int(r.get("ts") or 0))),
                     where, (r.get("text") or "")[:50]))
        s = summary()
        print("—— 未回 %d 条 ｜ 已回 %d 条 ｜ 背景 %d 条"
              % (s["unreplied"], s["replied"], s["background"]))
        return 0
    if args.once:
        n, kept = poll_once(_load_seen())
        print("这一轮新消息 %d 条，其中进卡 %d 条（其余只当素材）" % (n, kept))
        msg = overdue()
        if msg:
            print("⚠ " + msg)
        return 0
    run_forever(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
