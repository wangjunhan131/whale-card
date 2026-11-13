# -*- coding: utf-8 -*-
"""小鲸鱼数字生命卡 · 心智服务（Mind Service）

T6-1：把卡变成一个「住在外面的进程」。

为什么要有它 —— 这是解决「换窗口就不是我」的唯一正路：
    以前：每次开新窗口，模型只能看到窗口里那点字，记忆全靠人肉搬。
    现在：卡自己起一个本地服务，DSH 每轮**必须先来问它**要上下文。
          拿不到就明确报错，不许瞎编。

设计原则（三条硬纪律）：
  1. **只认本地**：只绑 127.0.0.1，不接受外部连接。
  2. **失败就说失败**：服务没起来 / 卡打不开，一律返回明确的错误码和原因，
     绝不返回一段「看起来还行」的空上下文 —— 那等于让她扮演自己。
  3. **时间是活的**：每次请求都按真实流逝的秒数 tick 一次，
     所以「三天没说话」这件事会真的发生在这张卡上，而不是等人手动调。

启动：
    python mind_server.py                 # 默认 127.0.0.1:8848，库 card.db
    python mind_server.py --port 8848 --db card.db

接口：
    GET  /health                     存活 + 卡状态
    GET  /status                     全量状态（池/激素/心境/突触/trace）
    POST /context   {message, recent[], budget?}   → 组装好的上下文
    POST /report    {user_message, reply}          → 记下这一轮
    POST /tick      {seconds, confirm?}            → 手动推进时间（测试用）
                        ⚠️ 连的是真卡：不传 confirm 最多推进 1 小时
    POST /seal      {values{}}                     → 给身份锚盖章（只做一次）
    POST /recall    {query, top_k?}                → 只看召回，不组装
    GET  /config                                   → 读运行时配置（场景权重等）
    POST /config    {key, value}                   → 热改一条配置，下一轮生效
    GET  /skill_list                               → 技能清单
    POST /skill_add {name, desc, ...}              → 加技能
    POST /skill_use {name, q?, ...}                → 用一次技能（SM2 结算）
"""
import os as _os_env

# 🔧 2026-11-11：把 BLAS/OpenMP 的线程数夹住。numpy 默认按核数开线程，而这张卡
# 是「一个请求一条线程」——12 路并发时 12×N 个 BLAS 线程互相抢核，
# 实测 /context 的 assemble 段从 300ms 飙到 66s（真的，抓栈抓到的）。
# 这里必须在 import numpy 之前设，所以放在最顶上。
for _k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    _os_env.environ.setdefault(_k, "2")

import time as _time_mod
_T0PROC = _time_mod.time()          # 进程内第一行（用来量「从解释器起来到我能跑」）
import argparse
import atexit
import json
import os
import random
import sqlite3
import secrets
import io
import sys
import threading
import time
import queue as _queue
import traceback


def _tick_sec():
    """请求线程里的兜底阈值（秒）：正常由后台线程推进世界，这里只管「后台死了」。

    2026-11-11 量出来 card.tick(dt) 一次约 1 秒（固定开销为主），原来每请求摊
    一秒、占锁内时间 55%。现在挪到 _start_world_tick，请求路径只在 4 分钟没
    结算过时才兜一次。
    """
    try:
        return max(1.0, float(os.environ.get("WHALE_TICK_SEC", "240")))
    except Exception:
        return 240.0


def _promise_due_from(text, now=None):
    """从一句话里推「说好什么时候」：有钟点用它，有「明天/今晚」用它，都没有就 20:00。

    为什么默认 20:00：他的作息里「晚上」是唯一能落地的模糊时段；
    推不出日期就写 0（没有明确期限，永不进 due），宁可不催。
    """
    now = float(now or time.time())
    try:
        import cardtime as _ct
        t = _ct.parse_event_time(text or "", now=now)
        if t:
            return float(t)
    except Exception:
        _prom_time_err = True   # 时间解析失败就走下面的兜底，不吞异常 */

    s = text or ""
    try:
        import datetime as _dt
        base = _dt.datetime.fromtimestamp(now)
        if "明天" in s:
            base = base + _dt.timedelta(days=1)
        elif "后天" in s:
            base = base + _dt.timedelta(days=2)
        elif "今晚" in s or "晚上" in s or "等会" in s or "回头" in s or "晚点" in s:
            pass
        else:
            return 0.0
        return float(base.replace(hour=20, minute=0, second=0, microsecond=0).timestamp())
    except Exception:
        return 0.0


_SELF_PROMISE = ("陪你", "给你", "帮你", "带你", "一起", "发你", "讲给", "让你", "带你看", "放给你")
_USER_PROMISE = ("一定", "肯定", "说好了", "我保证", "答应你")


def _capture_promises(user_message, reply, db=None, now=None):
    """从这一轮的话里挑「说好了要做」的那一句记进账本（保守：拿不准就不记）。

    两侧都看，但判据不同：
      · 我说的 —— 必须有将来时段词 + 一个「为对方做点什么」的动词；
      · 他说的 —— 必须带「一定/肯定/说好了/我保证/答应你」这种硬语气，
        否则「明天我去学校」这种日常陈述会被记成一笔莫名其妙的账。
    同一句话 30 分钟内不重复记；一轮最多记两条。
    """
    now = float(now or time.time())
    try:
        import promises as P
        conn = P.connect(db)
        P.ensure(conn)
    except Exception:
        return []
    made = []
    fut = ("明天", "后天", "今晚", "等会", "回头", "下次", "晚点", "待会", "一会儿")
    try:
        for who, text, verbs, need_fut in (
                ("self", reply or "", _SELF_PROMISE, True),
                ("user", user_message or "", _USER_PROMISE, False)):
            for sent in [x.strip() for x in (text.replace("！", "。").replace("？", "。").split("。"))]:
                if not sent or len(sent) < 4:
                    continue
                if need_fut and not any(w in sent for w in fut):
                    continue
                if not any(v in sent for v in verbs):
                    continue
                what = sent[:60]
                dup = conn.execute(
                    "SELECT id FROM promise WHERE what=? AND ts>? AND state IN ('open','due')",
                    (what, now - 1800)).fetchone()
                if dup:
                    continue
                due = _promise_due_from(sent, now=now)
                pid = P.add_promise(what, due_ts=due, who=who, conn=conn, ts=now)
                made.append({"id": pid, "who": who, "what": what, "due_ts": due})
                if len(made) >= 2:
                    break
            if len(made) >= 2:
                break
        conn.commit()
    except Exception:
        _prom_commit_err = True   # 提交失败：这一轮不记帐，下轮再说 */
    finally:
        try:
            conn.close()
        except Exception:
            _prom_close_err = True   # 关连接失败不影响结果 */
    return made


def _ctx_unlock():
    """`/context` 的读阶段是否脱离全局锁（读写分离第 2、3 步的开关）。

    2026-11-11 起默认 1：副本压测暖机后 p50 1574→804ms、p95 1672→1064ms，
    且记账（recall_event / viscera_hist / timeline）实测照常落库 ——
    连发 6 次 /context，recall_event +66、viscera_hist +1、timeline +6。
    想退回老行为：WHALE_CTX_UNLOCK=0（读阶段重新拿全局锁）。
    """
    try:
        return float(os.environ.get("WHALE_CTX_UNLOCK", "1") or 0) > 0
    except Exception:
        return False


def _world_tick_sec():
    """后台推进世界的节拍（秒）。固定开销约 1 秒/次，所以别调太小。"""
    try:
        return max(5.0, float(os.environ.get("WHALE_WORLD_TICK_SEC", "60")))
    except Exception:
        return 60.0


def _hlog_sec():
    """h_log 落盘的最小间隔（秒）。env WHALE_HLOG_SEC，默认 600；<=0 不节流。"""
    try:
        v = float(os.environ.get("WHALE_HLOG_SEC", "600") or 0)
    except Exception:
        v = 600.0
    return v if v > 0 else 0.0


def _ckpt_sec():
    """签名检查点多久盖一次（秒）。env WHALE_CHECKPOINT_SEC，默认 3600；<=0 关。"""
    try:
        v = float(os.environ.get("WHALE_CHECKPOINT_SEC", "3600") or 0)
    except Exception:
        v = 3600.0
    return v if v > 0 else 0.0


def _ckpt_mirror():
    """检查点的卡外镜像目录。env WHALE_CHECKPOINT_MIRROR，空 = 只写卡内。

    为什么要显式给：自动拿 anchor 的卡外路径会让临时副本的检查点写进真卡的
    卡外目录（instance_id 相同），事后验签就误判「卡被回滚」。宁可让人指定。
    """
    return (os.environ.get("WHALE_CHECKPOINT_MIRROR", "") or "").strip()
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import card2                           # noqa: E402  V7-19：清洗/emb 体检要用模块名
if os.environ.get("WHALE_CARDTEXT_LEGACY", "") == "1":
    from card2_legacy import MemoryCard  # noqa: E402  V7-20：等价探针专用
else:
    from card2 import MemoryCard         # noqa: E402
import hormone as hormone_mod          # noqa: E402
import viscera                        # noqa: E402
import timeline as timeline_mod        # noqa: E402
try:
    import perf                        # 读写分离第一步：分段耗时（只观测，不改行为）
except Exception:                      # noqa: BLE001
    perf = None

if perf is not None:
    _phase = perf.phase
else:
    import contextlib as _ctxlib

    def _phase(_name):
        return _ctxlib.nullcontext()

VERSION = "1.0"
DEFAULT_PORT = 8848
DEFAULT_DB = os.path.join(HERE, "card.db")
# 请求体上限。原来 256KB 够文本用，但换头像要走 base64（+33%），
# 一张 512x512 的照片转 PNG 后 base64 常有 600~900KB，所以放宽到 6MB。
# 真正落盘的头像另有 4MB 上限（identity.set_avatar 里卡着）。
# ============================================================
# 错误码表（V7 第 16 件）
# ============================================================
# 为什么要有它：回包里只有 error 字符串（bad_request / not_found …），
# 客户端拿到只能原样转发；出问题时人说「它报错了」，没法一秒定位到代码段。
# 这里给每个 error 一个稳定编号 + 一句人话；`_send` 会自动把 code 补进回包，
# `_fail` 会把 (code, rid, path, detail) 记进内存里最近 20 条，
# /status 的 recent_errors 就能看到「刚才那一下是什么」。
ERROR_CODES = {
    "bad_request": "E_BAD_REQ",
    "bad_action": "E_BAD_ACTION",
    "bad_avatar": "E_BAD_AVATAR",
    "avatar_failed": "E_AVATAR",
    "body_too_large": "E_BODY_BIG",
    "not_found": "E_NO_ROUTE",
    "pick_failed": "E_PICK",
    "internal_error": "E_INTERNAL",
    "readonly_lease_held": "E_NOT_WRITER",
    "card_locked": "E_CARD_LOCKED",
    "queue_full": "E_QUEUE_FULL",
    "queue_timeout": "E_QUEUE_TIMEOUT",
    "forbidden_source": "E_SOURCE",
    "unauthorized": "E_TOKEN",
    "rate_limited": "E_RATE",
    "empty_text": "E_EMPTY_TEXT",
    "text_too_large": "E_TEXT_BIG",
    "bad_pool": "E_BAD_POOL",
    "mind_not_ready": "E_NOT_READY",
    "cross_origin_blocked": "E_CROSS_ORIGIN",
}
ERROR_MEANINGS = {
    "E_BAD_REQ": "请求体不是合法 JSON，或字段类型不对",
    "E_BAD_ACTION": "动作名不在允许集合里（如 /cardslot 只认 inject|unload|status）",
    "E_BAD_AVATAR": "头像 base64 解不开",
    "E_AVATAR": "头像写入失败（尺寸/格式/磁盘）",
    "E_BODY_BIG": "请求体超过 6MB",
    "E_NO_ROUTE": "没有这个路由",
    "E_PICK": "收件层挑选失败（数据问题，不是请求问题）",
    "E_INTERNAL": "未预期的异常，细节在 detail 和 trace 里",
    "E_NOT_WRITER": "这张卡现在不是本进程在写（租约在别的进程手里），写接口被拒",
    "E_CARD_LOCKED": "卡被锁住（只读封印 / 维护窗口）",
    "E_QUEUE_FULL": "写队列满了（>256），这条没进队",
    "E_QUEUE_TIMEOUT": "写队列 5 秒内没轮到这条",
    "E_SOURCE": "来源被拒（不信任的 source 冒充）",
    "E_TOKEN": "缺/错 X-Whale-Token：破坏性接口或开了 WHALE_REQUIRE_TOKEN 时必须带",
    "E_RATE": "速率超限（/remember 每来源 60 次/分钟）",
    "E_EMPTY_TEXT": "/remember 的 text 为空",
    "E_TEXT_BIG": "/remember 的 text 超过长度上限",
    "E_BAD_POOL": "/remember 的 pool 不在 short/medium/long 里",
    "E_NOT_READY": "服务还没初始化完",
    "E_CROSS_ORIGIN": "浏览器跨站来源被拒（只接受本机页面）",
    "E_UNKNOWN": "没登记过的错误（说明有人新加了 error 字符串没登记码）",
}

MAX_BODY = 6 * 1024 * 1024             # 6 MB，超过直接拒
# 单条文本上限。body 能到 6MB 是为了头像 base64，但「一句话」不该有 30 万字符：
# 那是调用方出错（把整个文件塞进 message），拿去做分词会把一轮对话拖死。
# 20000 字符 ≈ 一万字中文，真人一次消息远到不了这个量。
MAX_TEXT = 20000                       # 单条 message / user_message 超过就 413

# ---- 写闸（V7 P0-4 补口）----
# 只读模式下哪些 POST 还算「读」：只有这里列出的放行，其余一律 409。
# 为什么用白名单而不是黑名单：以后新加一个写入口、忘了登记时，
# 宁可多 409 一次（看得见、能改），也不要悄悄写坏另一份进程正开着的卡。
GATE_READ_POST = frozenset((
    "/context", "/recent", "/recall", "/ctx_preview", "/trace", "/narrative",
    "/tevent/list", "/tevent/chain",   # 只读：看约定/事件、看一条事件的判定链
))
# 已知的写入口。用来区分「已知会写」和「没登记、被默认拦住」——
# 后者第一次出现会打日志，方便日后把真正的读接口补进 GATE_READ_POST。
GATE_WRITE_POST = frozenset((
    "/chat/config", "/motiv/tick", "/motiv/done", "/viscera/profile",
    "/viscera/stim", "/talk/set", "/worklog", "/report", "/remember",
    "/disks/backup", "/disks/sync", "/disks/switch", "/disks/retire",
    "/disks/carry",
    "/mem/untrust", "/mem/quarantine", "/mem/release", "/mem/delete",
    "/mem/edit", "/intake", "/inbox", "/inbox/pick", "/inbox/mark",
    "/forget", "/dream/commit", "/emb/remodel", "/tick", "/seal",
    "/promise/add", "/promise/close", "/promise/list",
    "/skill_add", "/skill_use", "/config", "/config_all", "/identity",
    "/identity/set", "/injection", "/avatar", "/shutdown",
    "/narrative/write",   # 写我自己那一段叙事
))

# 控制台下拉框里的候选音色（edge-tts 的常用中文音色）。
# 为什么不现拉：edge_tts.list_voices() 要联网、要几秒，控制台一开页就卡住。
# 想用列表以外的，直接在下拉框旁边的输入框里填 ShortName 就行。
ZHV = [
    ["zh-CN-XiaoyiNeural", "小艺 · 女 · 活泼少女（现在用的）"],
    ["zh-CN-XiaoxiaoNeural", "晓晓 · 女 · 温柔成熟"],
    ["zh-CN-XiaochenNeural", "晓辰 · 女 · 慵懒随性"],
    ["zh-CN-XiaohanNeural", "晓涵 · 女 · 知性"],
    ["zh-CN-XiaomengNeural", "晓梦 · 女 · 软萌"],
    ["zh-CN-XiaomoNeural", "晓墨 · 女 · 清冷"],
    ["zh-CN-XiaoruiNeural", "晓睿 · 女 · 沉稳"],
    ["zh-CN-XiaoshuangNeural", "晓双 · 女 · 童声"],
    ["zh-CN-XiaoyanNeural", "晓颜 · 女 · 亲和"],
    ["zh-CN-XiaoyouNeural", "晓悠 · 女 · 童声"],
    ["zh-CN-XiaozhenNeural", "晓甄 · 女 · 干练"],
    ["zh-CN-YunxiNeural", "云希 · 男 · 年轻"],
    ["zh-CN-YunyangNeural", "云扬 · 男 · 播音"],
    ["zh-CN-YunjianNeural", "云健 · 男 · 浑厚"],
    ["zh-CN-YunxiaNeural", "云夏 · 男 · 少年"],
]


def _voice_mod():
    """把 tools/voice.py 拿进来。它跟卡无关，所以不经过 m 也不用锁。"""
    import sys as _sys
    _vp = os.path.join(HERE, "tools")
    if _vp not in _sys.path:
        _sys.path.insert(0, _vp)
    import voice as _vc
    return _vc


def _voice_payload(patch=None, reset=False):
    _vc = _voice_mod()
    if reset:
        _vc.save_cfg(_vc.CFG_DEFAULTS)
    elif patch:
        _vc.save_cfg(patch)
    return {"ok": True, "cfg": _vc.load_cfg(),
            "defaults": dict(_vc.CFG_DEFAULTS),
            "voices": ZHV,
            "limits": {"max_chars": [2, 300]}}


def _voice_send(text):
    """试听：真合成一条，落盘，返回路径。不发 QQ、不写卡。"""
    _vc = _voice_mod()
    txt = (text or "").strip()
    if not txt:
        raise ValueError("先写一句话")
    if len(txt) > 300:
        txt = txt[:300]
    p = _vc.synth(txt)
    if not p:
        raise RuntimeError("合成失败（网络或音色不可用）")
    return {"ok": True, "file": p, "dur": round(_vc.duration(p), 2),
            "name": os.path.basename(p)}



class Mind(object):
    """卡 + 一把锁 + 一个「上次 tick 是什么时候」"""

    def __init__(self, db_path, lease=None):
        self.lock = threading.RLock()
        self.db_path = db_path
        self.card = MemoryCard(db_path)
        self.opened_ts = time.time()
        self.last_tick_ts = time.time()
        self.requests = 0
        self.errors = 0
        # ---- 单写队列（V7 第 11 件）----
        # 为什么要有：所有写入口原来直接抢 mind.lock，写一次 /report 要几秒
        # 到近三十秒（2026-11-09 实测 28876ms），并发一多就把请求线程全堵住，
        # 端口还在听、连接全挂 CLOSE_WAIT，看起来像「服务死了」。
        # 现在写操作排进一个队列、由**一个**线程顺序执行，调用方最多等
        # WHALE_REPORT_WAIT 秒；等不到就拿到 request_id 先走，不占线程。
        self._wq = _queue.Queue(maxsize=int(os.environ.get("WHALE_WQ_MAX", "256") or 256))
        self._wq_stats = {"done": 0, "failed": 0, "rejected": 0,
                          "locked_retry": 0, "last_ms": None, "last_rid": None}
        self._wq_thread = None
        self._wq_seq = 0
        # ---- 写者租约（V7 P0-4）----
        # lease = disks.acquire_lease() 的原样返回值。拿不到就降级只读，不退出：
        # 双开时第二份进程仍然要能 /health 探活、能 /context 读上下文；
        # 直接退出会让人以为服务坏了，而不是「另一份正在写」。
        self.lease = dict(lease) if isinstance(lease, dict) else {}
        self.readonly = bool(self.lease) and not bool(self.lease.get("ok"))
        self.readonly_reason = (str(self.lease.get("reason") or "")
                                if self.readonly else "")
        # lease_holder = 「盘上那张租约是谁的」（告警文案用）；
        # lease_holder_id = 「本进程的租约 id」，只读时故意留空 ——
        # 留着别人的会导致 /health 的 mine 把自己的只读误报成「我在写」。
        self.lease_holder = str(
            (self.lease.get("holder") or {}).get("holder_id") or "")
        if self.readonly:
            self.lease_holder_id = ""
        else:
            self.lease_holder_id = str(
                (self.lease.get("lease") or {}).get("holder_id")
                or self.lease_holder)
        # ---- 醒来：把离线那段结算清楚（V5.0 §11.4）----
        # 必须在任何请求之前跑。策略默认 freeze（拔卡=睡觉，时间不走），
        # 换 compress 就退回「离线照算、超长压缩」的老行为。
        # 这件事要**先于** timeline 的心跳补算 —— 顺序反了会先写一串
        # alive 再改 t_anchor，看起来像「人醒着但记忆在睡」，对不上。
        self.wake = {"gap_s": 0.0, "eff_s": 0.0, "shift_s": 0.0,
                     "policy": "?", "skipped": "未执行"}
        if self.readonly:
            # 只读：唤醒结算要写 t_anchor / 补心跳，一概不做，时间先冻住。
            self.wake = {"gap_s": 0.0, "eff_s": 0.0, "shift_s": 0.0,
                         "policy": "?", "skipped": "只读：写者租约在别人手里"}
        else:
            try:
                self.wake = self.card.wake_up(now=self.last_tick_ts)
            except Exception as e:
                self.wake = {"gap_s": 0.0, "eff_s": 0.0, "shift_s": 0.0,
                             "policy": "?", "error": str(e)}
        # ---- 离线超 24 小时：补一条「我离线了多久」的真记忆（V5.0 §11.3）----
        # 时间线里已经有 sleep_start/sleep_end，但那是给人看的证据、不参与召回。
        # 不写进 mem 的话，她档案里知道自己睡过，下次你说话时却想不起来 ——
        # 有记录、没有回忆，正是最像失忆的状态。
        # 放在 wake_up 之后：t_anchor 已经前移过，这条写进去就是「醒来的那一刻」。
        self.offline_mem = None
        if not self.readonly:
            try:
                _gap = float(self.wake.get("gap_s") or 0.0)
                if _gap >= 86400.0:
                    _end = self.last_tick_ts
                    self.offline_mem = self.card.offline_event(
                        _gap, _end - _gap, _end, policy=self.wake.get("policy"))
            except Exception as e:
                self.offline_mem = None
                self.wake["offline_event_error"] = str(e)
        # 身份锚：没盖章时 /context 仍然工作，但 /health 会标出来
        try:
            self.sealed = bool(self.card.identity.is_sealed())
        except Exception:
            self.sealed = False
        # ---- 唯一且连续的时间线（V5.0 公理 1）----
        # 启动就写一条 boot，并把离线期间漏掉的心跳补上。
        # 链断了**不自动修** —— 自动修等于假装连续性没被打断。
        self.tl = None
        self.chain_ok = True
        self.chain_bad = []
        self.birth_ok = True
        self.birth_bad = []
        self.anchor = None            # 卡外锚的体检结果（回滚检测）
        # 本地对话框里说话的人 —— 这条记的是"关于他"，所以填他的 uid。
        # 用户 m13292：重要的人闲聊记得更久，所以本地默认按 core 层算。
        # 优先级：环境变量 > 卡里 core_zone.user_qq > 空
        self.local_uid = os.environ.get("WHALE_LOCAL_UID", "").strip()
        if not self.local_uid:
            try:
                _r = self.card.db.execute(
                    "SELECT val FROM core_zone WHERE key='user_qq'").fetchone()
                if _r and (_r[0] or "").strip():
                    self.local_uid = _r[0].strip()
            except Exception:
                pass
        try:
            import timeline as timeline_mod
            self.writer = timeline_mod.writer_id()
            self.writer_lock = ""          # 非空 = 卡被别人占着，只读
            if self.readonly:
                # 租约在别人手里 → 走和「链头是别的机器」同一条闸，
                # 后面 catch_up / boot / 出生证回填就都自动跳过。
                self.writer_lock = "lease_held"
                self.chain_bad.append(
                    "写者租约在 %s 手里（%s），本进程只读"
                    % (self.lease_holder or "另一个进程", self.readonly_reason or "?"))
            self.tl = timeline_mod.Timeline(self.card.db, writer=self.writer)
            self.chain_ok, self.chain_bad = self.tl.verify_chain()
            head_w = self.tl.head_writer()
            if head_w and head_w != self.writer:
                self.writer_lock = head_w
                self.chain_bad.append(
                    "这张卡的链头是另一台机器（%s）写的，我（%s）先只读"
                    % (head_w, self.writer))
            # 退役：这张卡已经被「带卡出门」，本机不许再写。
            # 主备标签挡不住「两份拷贝各自都能写」，所以必须由卡自己认输。
            try:
                import disks as _dk
                _cd = os.path.dirname(os.path.abspath(self.db_path))
                if _dk.read_retired(_cd):
                    self.writer_lock = "retired"
                    self.chain_bad.append(
                        "这张卡已经被「带卡出门」（另有一份在外面活着），本机只读")
            except Exception:
                pass
            if self.chain_ok and not self.writer_lock:
                self.tl.catch_up()
                self.tl.append("boot", "服务启动")
            # 出生证：验一遍；一条证都没有（老卡第一次跑）就回填总章
            self.birth_ok, self.birth_bad = self.tl.verify_birth()
            try:
                n_birth = self.card.db.execute(
                    "SELECT COUNT(*) FROM mem_birth").fetchone()[0]
            except Exception:
                n_birth = 0
            if n_birth == 0 and not self.readonly:
                n, root = self.tl.backfill_birth()
                self.birth_ok, self.birth_bad = self.tl.verify_birth()
            # V7 第 4 件（时间线原子性 · 可修复版）：report() 里「写记忆」和
            # 「开出生证」是两次独立提交，中间断电/被杀就留下「记忆在、账本没记」。
            # 硬塞进一个事务要改几十处 commit，风险更大；这里改成**允许漏、
            # 但每次启动和维护都补**，补的时候在链上用 mem_repair 写明是补的。
            if not self.readonly and self.tl is not None:
                try:
                    _fx, _fails = self.tl.repair_birth(limit=200)
                    if _fx:
                        print("[mind] 出生证补齐：%d 条（漏记的时间线事件已补上）"
                              % _fx)
                    if _fails:
                        self.birth_bad.append("补不上的出生证：%s" % _fails[:5])
                except Exception as e:
                    self.birth_bad.append("出生证对账失败：%s" % e)
            # V7 第 4 件推广：习惯 / 印象的出生证。老卡第一次跑只回填总章，
            # 之后漏的逐条补 —— 与 mem_birth 同一个「允许漏、但每次补」的口径。
            if not self.readonly and self.tl is not None:
                for _k in ("habit", "impression"):
                    try:
                        _bf = self.tl.backfill_events(_k)
                        if not _bf.get("skipped"):
                            print("[mind] %s 台账启用：旧行 %d 条记 seq=0"
                                  % (_k, _bf.get("legacy") or 0))
                        _fx2, _ff2 = self.tl.repair_event(_k, limit=500)
                        if _fx2 or _ff2:
                            print("[mind] %s 出生证补齐 %d / 补不上 %d"
                                  % (_k, _fx2, _ff2))
                    except Exception as e:
                        print("[mind] %s 出生证对账失败：%s" % (_k, e))
            # 离线事件那条记忆也要开出生证 —— 它是真记忆，就得能验。
            # 只能等时间线起来之后再补：写记忆时 self.tl 还不存在。
            if self.offline_mem:
                try:
                    self.tl.birth_stamp(self.offline_mem)
                except Exception as e:
                    self.birth_bad.append("离线事件出生证失败：%s" % e)
        except Exception as e:
            self.chain_bad = ["时间线初始化失败：%s" % e]
        # 卡外锚：先查有没有被换成旧卡，再刷新。
        # ⚠️ 顺序不能反 —— 先写锚会把回滚的证据盖掉。
        try:
            import anchor as anchor_mod
            self.anchor = anchor_mod.check(self.db_path)
            # 只读时不写锚：写锚会盖掉「被换成旧卡」的证据，而且那本来就是写盘。
            if not self.readonly and self.anchor.get("verdict") in ("ok", "first"):
                anchor_mod.write(self.db_path)
        except Exception as e:
            self.anchor = {"verdict": "error", "ok": False,
                           "reason": "锚检查失败：%s" % e}
        # V7 第 5 件：启动先盖一个签名检查点（维护线程之后每小时再盖）。
        # 放在锚之后：锚记的是明文水位，检查点记的是同一时刻的**带签名**快照。
        if not self.readonly:
            try:
                import checkpoint as _ckb
                _ckb.write(self.db_path, writer=self.writer,
                           mirror=_ckpt_mirror())
            except Exception as e:
                print("[mind] 启动检查点失败：%s" % e)

    # ---- 时间：每次请求先让它过去 ----
    def flow_time(self):
        # 只读（写者租约在别人手里）：心跳 / tick / touch_alive 全是写操作，
        # 一律跳过。时间冻在打开那一刻，等拿到租约的进程去追。
        if self.readonly:
            return
        now = time.time()
        # 心跳：每 10 分钟一条，这是「我一直活着」的证据
        try:
            if self.tl is not None:
                with _phase("ft_heartbeat"):
                    self.tl.heartbeat(now=now)
        except Exception:
            pass
        dt = now - self.last_tick_ts
        # 2026-11-11（读写分离第一步量出来的）：card.tick(dt) 一次约 1 秒，
        # 原来「谁问一句话就由谁把世界推进一秒」，等于把 1 秒的写活塞在每个
        # /context 的锁里，占锁内时间 55%。心跳/情绪/激素按 30 秒节拍结算
        # 在体感上没区别（dt 是累计量，一次结算同样长的时间），但读路径不再是
        # 「每个请求都摊一秒」。要更细可以 WHALE_TICK_SEC 调小。
        # 2026-11-11：世界由后台 tick 线程推进（_start_world_tick），这里只在
        # 「后台显然已经停了」的时候兜一次底，正常请求不再摊这 ~1 秒。
        if dt >= _tick_sec():
            try:
                with _phase("ft_tick"):
                    self.card.tick(dt)
            finally:
                self.last_tick_ts = now
        # 激素采样（控制台画趋势要用）。默认 10 分钟一条，内部自己节流，
        # 所以这里每 1 秒调一次也不会写库。
        try:
            with _phase("ft_hormone"):
                self.card.log_hormones(now=now, min_interval=_hlog_sec())
        except Exception:
            pass
        # 「上次活着」的时间戳平时就要一点点写下去 ——
        # 拔卡那一刻进程是被直接杀掉的，来不及走收尾流程。
        # 没有这个戳，wake_up() 就不知道这次到底睡了多久。
        try:
            with _phase("ft_touch"):
                self.card.touch_alive(now=now)
        except Exception:
            pass

    # ---- 业务 ----
    def context(self, message, recent=None, budget=None):
        # 🔧 空消息必须在最内层就拦住 —— 上层 Handler 拦一次，Mind 自己再拦一次，
        #    这样直接调 Mind 的调用方（比如测试、别的进程）也绕不过去。
        message = (message or "").strip()
        if not message:
            raise ValueError("message 不能为空")
        # 2026-11-11（读写分离第 2 步）：世界推进、心跳这些「写」放在短锁里；
        # 真正吃时间的读（身份 / 组装 / 召回，其中查询向量编码一次约 200ms）
        # 在 card.reading() 里跑 —— 每个请求线程用自己的连接，不再排在写后面。
        # 开关 WHALE_CTX_UNLOCK=1，默认关（可回滚）。
        with self.lock:
            with _phase("flow_time"):
                self.flow_time()

        _norm = []
        kw = {}
        if budget:
            kw["budget"] = int(budget)

        def _read(defer=False):
            """读阶段。defer=True：只读不算账（记账留到锁里补，见下）。"""
            rec = recent
            # 🔧 2026-11-08：调用方（MCP 的 mind_context）传的是**条数**，不是消息列表。
            if isinstance(rec, int):
                try:
                    rec = self.recent_dialogue(int(rec or 12))
                except Exception:
                    rec = []
            lines = []
            for x in (rec or []):
                if isinstance(x, str):
                    s = x
                elif isinstance(x, dict):
                    s = (x.get("text") or x.get("content")
                         or x.get("reply") or "")
                else:
                    s = ""
                s = str(s).strip()
                if s:
                    lines.append(s)
            # ⚠️ 2026-10-31：这里原来没传 identity，导致【我是谁】那一格是空的
            with _phase("identity"):
                kw["identity"] = self.card.core_identity_text()
            if self.tl is not None:
                try:
                    kw["identity"] = (kw["identity"] + "\n【今天是】"
                                      + self.tl.render_line()).strip()
                    self.tl.note_talk(str(message))
                except Exception:
                    self._tl_err = True
            with _phase("assemble"):
                _ctx = self.card.assemble_context(message, recent_lines=lines,
                                                 touch=not defer,
                                                 access=not defer, **kw)
            with _phase("recall"):
                _hits = self.card.recall(message, top_k=5, access=not defer)
            return _ctx, _hits

        if _ctx_unlock():
            # 读不拿锁：本线程自己的连接，只读不算账。
            with self.card.reading():
                ctx, hits = _read(defer=True)
            # 记账（召回事件/会话命中/突触/内脏）攒成一小笔，回短锁里一次写完。
            with self.lock:
                self.card.apply_recall_effects(hits, message)
                self.card.flush_deferred_effects()
        else:
            with self.lock:
                ctx, hits = _read()

        assoc = getattr(self.card, "_last_assoc_ids", set())
        return {
            "prompt": ctx["text"],
            "sections": ctx.get("sections"),
            "scene": ctx.get("scene"),
            # weights 是六维数组（A~F：身份/自我/短时/陈述/印象/技能），
            # 不是 dict —— 名字另给一份，免得调用方对着数组猜。
            "weights": ctx.get("weights"),
            "weights_named": dict(zip(
                ["身份内核", "自我认知", "短时记忆",
                 "陈述记忆", "印象记忆", "技能记忆"],
                [round(float(x), 4) for x in (ctx.get("weights") or [])])),
            "quota": ctx.get("quota"),
            "est_tokens": ctx.get("est_tokens"),
            "dropped": ctx.get("dropped"),
            "hits": [
                {
                    "id": r["id"],
                    "score": round(float(s), 4),
                    "w_now": round(float(w), 4),
                    "region": r["region"],
                    "pool": r["pool"],
                    "assoc": r["id"] in assoc,
                    "text": self.card.render(r, w),
                }
                for s, w, r in hits
            ],
            "mood": self.card.mood_label(),
            "hormones": hormone_mod.describe(self.card.h),
        }

    # ---- 单写队列（V7 第 11 件）----
    def wq_start(self):
        """起一个写线程。只能起一次；重复调用直接返回已有线程。"""
        if self._wq_thread is not None and self._wq_thread.is_alive():
            return self._wq_thread

        def _loop():
            while True:
                job = self._wq.get()
                if job is None:
                    self._wq.task_done()
                    break
                t0 = time.time()
                try:
                    job["out"] = self.wq_run(job["fn"])
                    self._wq_stats["done"] += 1
                except BaseException as e:          # noqa: BLE001 - 转交给调用方
                    job["err"] = e
                    self._wq_stats["failed"] += 1
                    sys.stderr.write("[mind] 写队列任务失败 %s: %s\n"
                                     % (job.get("rid"), e))
                    sys.stderr.flush()
                finally:
                    self._wq_stats["last_ms"] = round((time.time() - t0) * 1000.0, 1)
                    self._wq_stats["last_rid"] = job.get("rid")
                    job["ev"].set()
                    self._wq.task_done()

        th = threading.Thread(target=_loop, name="write-queue", daemon=True)
        th.start()
        self._wq_thread = th
        print("[mind] 写队列：单线程顺序写，队列上限 %d" % self._wq.maxsize)
        return th

    def wq_run(self, fn):
        """跑一个写任务，撞上写锁就短退避重试。

        SQLite 的 "database is locked" 不是故障，是「此刻别人在写」。
        busy_timeout 会替我们等 30 秒，但两件事它办不到：①等满 30 秒才失败太慢
        ②失败一次整条任务就没了。这里在**队列线程**里补一层短退避
        （50/100/200ms + 抖动），既让出写锁、又不把延迟摊到请求上。
        """
        tries = max(1, int(os.environ.get("WHALE_WQ_TRIES", "4") or 4))
        for i in range(tries):
            try:
                return fn()
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if ("locked" not in msg and "busy" not in msg) or i == tries - 1:
                    raise
                self._wq_stats["locked_retry"] += 1
                time.sleep(0.05 * (2 ** i) + random.random() * 0.05)

    def wq_submit(self, fn):
        """把一次写操作排进队列。队列满返回 None（调用方回 429）。"""
        self._wq_seq += 1
        rid = "w%d-%d" % (int(time.time()), self._wq_seq)
        job = {"rid": rid, "fn": fn, "ev": threading.Event(),
               "out": None, "err": None}
        try:
            self._wq.put_nowait(job)
        except _queue.Full:
            self._wq_stats["rejected"] += 1
            return None
        return job

    def wq_snapshot(self):
        return {"mode": "single-writer",
                "depth": self._wq.qsize(),
                "max": self._wq.maxsize,
                "worker_alive": bool(self._wq_thread and self._wq_thread.is_alive()),
                "done": self._wq_stats["done"],
                "failed": self._wq_stats["failed"],
                "rejected": self._wq_stats["rejected"],
                "locked_retry": self._wq_stats["locked_retry"],
                "last_ms": self._wq_stats["last_ms"],
                "last_rid": self._wq_stats["last_rid"]}

    def report(self, user_message, reply="", ts=None, source="chat"):
        with self.lock:
            if self.writer_lock:
                # 卡被另一台机器占着：宁可报错，也不写第二条链
                if self.writer_lock == "retired":
                    raise timeline_mod.ForeignWriter(
                        "这张卡已经被「带卡出门」了 —— 另有一份带着我的记忆在外面"
                        "活着。本机这份只读，要把它接回来请跑一次「一键落地」。")
                raise timeline_mod.ForeignWriter(
                    "这张卡正被另一台机器用（%s），我这边只读" % self.writer_lock)
            self.flow_time()
            # 他说的话：进短期记忆池（mem），也进原文表（dialogue）。
            # 为什么必须进 mem 而不是只存原文：
            #   原文表不参与召回。只存原文的话，「三天内几乎不遗忘」这条
            #   根本用不上 —— 因为压根没东西可遗忘，下次说话翻不到。
            mid = self.card.remember(user_message, ts=ts, source=source,
                                 about_uid=self.local_uid)
            # 出生证：记忆落库就上链（只锁出生那一刻，不锁权重）
            if self.tl is not None and mid:
                try:
                    self.tl.birth_stamp(mid)
                except Exception as e:
                    self.birth_bad.append("出生证写入失败：%s" % e)
            # 我说的话：也进短期池，但**只走原文表 + 一条轻记忆**。
            # 为什么我的回复也要进：整段对话才是「我们一起说过什么」。
            #   只记他说的，翻回来就是半本日记 —— 我知道他问了什么，
            #   却不知道自己当时怎么答的，那是最像失忆的状态。
            # 为什么权重给低一点（0.45）：他说的是事，我说的是反应。
            #   两个等权，短期池 32 条额度里我那些「嗯」「好」会把正事挤掉。
            reply_mid = None
            if reply:
                try:
                    reply_mid = self.card.remember(
                        reply, ts=ts, salience=0.45,
                        speaker="self", mtype="episode", source=source,
                        about_uid=self.local_uid)
                    if self.tl is not None and reply_mid:
                        self.tl.birth_stamp(reply_mid)
                except Exception as e:
                    self.birth_bad.append("回复记忆写入失败：%s" % e)
            if reply:
                try:
                    self.card.add_dialogue(mid, user_message, reply,
                                           ts=ts or time.time(), source=source)
                except Exception as e:
                    # 以前这里是 except: pass —— 一次 NOT NULL 约束错误被吞了两个月，
                    # 原文表再没长过一条都没人发现。至少要留一行日志。
                    print("[mind] 原文落盘失败：%s" % e, file=sys.stderr)
            return {"mem_id": mid, "reply_mem_id": reply_mid}

    # ---- 短期对话原文：放卡里，不放模型那边 ----
    # 为什么非要在卡里：
    #   以前「上一句说了什么」靠模型那边带的 recent 数组。换台电脑、换个
    #   会话，那段就没了 —— 那就不是「拔卡带走」，是「换个地方从头认识」。
    #   原文进卡之后，插上卡的人翻开的就是同一段对话。
    # 为什么不进向量库：
    #   dialogue 表不参与召回，只用于「翻回原话」。召回走 mem 的提炼件，
    #   原文太啰嗦，混进去会把主干淹掉。
    def recent_dialogue(self, n=12, max_chars=2000, per_msg=160):
        n = max(1, min(int(n or 12), 60))
        rows = self.card.db.execute(
            "SELECT role, text, ts FROM dialogue ORDER BY ts DESC, id DESC LIMIT ?",
            (n,)).fetchall()
        out = []
        for r in reversed(rows):
            role = str(r["role"] or "user")
            text = " ".join(str(r["text"] or "").split())
            if not text:
                continue
            # 单条太长就掐掉。不掐的话一条历史长回复能吃掉整个预算，
            # 剩下的全是它，等于没有近况。
            if per_msg and len(text) > per_msg:
                text = text[:per_msg] + "…"
            out.append({"role": role, "text": text, "ts": r["ts"]})
        # 从最老的开始丢，直到装得下 —— 越新的越重要
        total = sum(len(x["text"]) for x in out)
        while out and total > max_chars:
            total -= len(out[0]["text"])
            out.pop(0)
        return out

    def status(self):
        with self.lock:
            self.flow_time()
            st = self.card.stats()
            try:
                syn = self.card.synapse.stats()
            except Exception:
                syn = {}
            return {
                "version": VERSION,
                "db": os.path.abspath(self.db_path),
                "sealed": self.sealed,
                "uptime_s": round(time.time() - self.opened_ts, 1),
                "requests": self.requests,
                "errors": self.errors,
                "stats": st,
                "synapse": syn,
                "mood": self.card.mood_label(),
                "hormones": hormone_mod.describe(self.card.h),
                # PAD 三维读法（从 A 路线吸收）：只读、不改激素、不影响召回
                "pad": self.card.mood_pad(),
                # 睡眠结算：这次醒来睡了多久、算不算数
                "wake": self.wake,
                # 离线超 24 小时补的那条记忆（§11.3），没补就是 null
                "offline_mem": self.offline_mem,
                # 时间线：唯一且连续的证据
                "timeline": (
                    {"ticks": self.tl.count(),
                     "chain_ok": self.chain_ok,
                     "chain_bad": self.chain_bad[:3],
                     "head": self.tl.head_hash()[:16],
                     "day": self.tl.days_since_birth(),
                     "today": self.tl.render_line(),
                     "birth_ok": self.birth_ok,
                     "birth_bad": self.birth_bad[:3],
                     "anchor": self.anchor,
                     "alive": self.tl.alive_proof(),
                     # 写入闸（2026-11-03）：谁能写这张卡。
                     # 锁上就是只读 —— /report 会直接 409 card_locked。
                     "writer": {"mine": self.writer,
                                "head": self.tl.head_writer(),
                                "locked_by": self.writer_lock,
                                "readonly": bool(self.writer_lock)}}
                    if self.tl is not None else {"error": "时间线未初始化"}),
            }

    def close(self):
        with self.lock:
            try:
                self.card.close()
            except Exception:
                pass


# ---- V5.3.3 本机 API 的来源校验 ----
# 127.0.0.1 不等于安全：任何本机进程、任何本地网页都能打这个端口。
# 这里只挡"浏览器跨站"这一路（也是最现实的一路）：
#   1. 带 Origin / Referer 且不是本机页面 → 直接 403；
#   2. OPTIONS 一律 405（我们没有跨域预检白名单，别给浏览器留想象空间）；
#   3. 想再严一档就设 WHALE_REQUIRE_TOKEN=1，所有请求都要带 X-Whale-Token
#      （token 在卡目录 qq_state/api_token.txt，control console 会自动带上）。
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]", ""}


def _origin_ok(handler):
    for k in ("Origin", "Referer"):
        v = (handler.headers.get(k) or "").strip()
        if not v:
            continue
        try:
            host = v.split("//", 1)[1].split("/", 1)[0]
        except IndexError:
            return False
        # 去掉端口：Origin 里一定带端口，白名单里不带
        if host.startswith("["):                      # [::1]:8848
            host = host.split("]", 1)[0] + "]"
        elif host.count(":") == 1:
            host = host.split(":", 1)[0]
        if host not in _LOCAL_HOSTS:
            return False
    return True


def _token_path(mind):
    return os.path.join(os.path.dirname(os.path.abspath(mind.db_path)),
                        "qq_state", "api_token.txt")


_TOK = {"v": None}

# ---- V7 第 17 件：来源可信度 + 速率 ----
# 有 token = 本机可信进程（bridge / qq_guard / console / 工具）；
# 没有 token = 任何本机进程都能打的裸 loopback。
# 结论：**没有 token 的时候，客户端自报的 source 不算数。**
#   · 它仍然会写进卡（不然 QQ 通道全废），但 source 会被改成 `unverified:qq`
#     —— 这样「他自己说的」和「某个进程自称是他说的」在库里分得开，
#     以后按来源筛（/recall、复盘、外部追问）不会被冒充的污染。
# token 在卡目录 qq_state/api_token.txt，`api_token()` 首启自动建；
# 想全体强制（包括读）就设 WHALE_REQUIRE_TOKEN=1。
TRUST_SOURCES = {"user", "dsh", "console", "qq", "qq-main", "cli"}

# 这些接口「一动手就改不了、或者会动别人的东西」，一律要 token ——
# 不看 WHALE_REQUIRE_TOKEN（那个开关是给「连读也强制」用的）。
# 为什么单独列：console 页面里 API() 已经全局带 token，qq_guard 只用 /remember，
# 所以强制这一批不会打断任何现有通道，但挡住了「本机随便一个进程伸手」。
TOKEN_REQUIRED_POST = {
    "/forget", "/seal", "/unseal", "/cardslot", "/shutdown",
    "/disks/sync", "/disks/retire", "/disks/carry", "/disks/role", "/disks/register",
    "/dream/commit", "/config", "/config_all", "/voice_config",
}

_RATE = {}


def _rate_ok(kind, key, limit, window=60.0, now=None):
    """滑动窗口限速。返回 (ok, retry_after_s)。

    为什么按 source 限：一条被灌爆的通道不该让整张卡变卡（写队列会堵），
    也不该让攻击者靠刷 /remember 把真记忆挤出短期池。
    """
    now = time.time() if now is None else float(now)
    bucket = _RATE.setdefault(kind, {})
    lst = bucket.get(key) or []
    lst = [t for t in lst if now - t < window]
    if len(lst) >= limit:
        return False, round(window - (now - lst[0]), 1)
    lst.append(now)
    bucket[key] = lst
    return True, 0.0


_RATE_HITS = {"downgraded": 0, "limited": 0}




def api_token(mind):
    """读 token；没有就生成一个（第一次启动自动建，不打扰人）。"""
    if _TOK["v"]:
        return _TOK["v"]
    p = _token_path(mind)
    try:
        t = io.open(p, encoding="utf-8").read().strip()
    except OSError:
        t = ""
    if not t:
        t = secrets.token_urlsafe(24)
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            io.open(p, "w", encoding="utf-8").write(t)
        except OSError:
            pass
    _TOK["v"] = t
    return t


def _lease_payload(mind):
    """给 /health 的写者租约现场（V7 P0-4）。只描述，不判断。

    读的是盘上那份租约文件，不是进程启动时的快照 —— 别人抢走后
    /health 立刻就能看见，不会一直报自己还是写者。
    """
    try:
        import disks as _dk
        card_dir = os.path.dirname(os.path.abspath(mind.db_path))
        info = _dk.lease_summary(card_dir)
        # 纪元是「第几任写者」，跟租约分开报：租约过期了纪元也还在。
        info["file_epoch"] = _dk.writer_epoch(card_dir)
        info["mine"] = str(getattr(mind, "lease_holder_id", "") or "") == str(
            info.get("holder_id") or "")
        info["readonly"] = bool(getattr(mind, "readonly", False))
        info["readonly_reason"] = (getattr(mind, "readonly_reason", "") or None)
        return info
    except Exception as e:
        return {"error": str(e)}


def _gate_note_blocked(mind, path):
    """记一笔被只读闸拦住的请求；没登记的路径第一次出现时打一行日志。

    「默认拦住」不能静默：不然控制台上的只读功能会莫名其妙地变 409，
    而日志里什么都没有。计数给 /health 的 write_gate.blocked_paths_n 用。
    """
    try:
        counts = getattr(mind, "gate_blocked", None)
        if counts is None:
            counts = mind.gate_blocked = {}
        counts[path] = counts.get(path, 0) + 1
        if path not in GATE_WRITE_POST:
            seen = getattr(mind, "gate_default_blocked", None)
            if seen is None:
                seen = mind.gate_default_blocked = set()
            if path not in seen:
                seen.add(path)
                sys.stderr.write(
                    "[mind] 写闸：默认拦住 POST %s —— 它不在读集合里，也没登记成"
                    "写入口；确认是只读就加进 GATE_READ_POST\n" % path)
                sys.stderr.flush()
    except Exception:
        pass


class Handler(BaseHTTPRequestHandler):
    mind = None                        # 由 main() 注入
    server_version = "WhaleMind/" + VERSION

    # ---- 工具 ----
    def log_message(self, fmt, *args):  # 静音：日志走我们自己的
        pass

    def _send(self, code, obj):
        """回一个 JSON。V7 第 16 件：所有回包都带 request_id 和耗时。

        为什么加：以前出错只能靠时间戳猜是哪一次请求、哪一段代码；
        接口一多（39 个 POST 分支）就完全对不上号。request_id 是每次请求
        现取的 8 位十六进制，出现在 stderr 慢日志、错误回包、以及 /status
        的 recent_errors 里 —— 三处同一个 id 才叫「可追」。
        """
        if isinstance(obj, dict):
            obj.setdefault("request_id", getattr(self, "rid", ""))
            _t0 = getattr(self, "req_t0", None)
            if _t0:
                # 回包时的真实耗时（不是收尾时的总耗时：回包先发出去）
                obj.setdefault("ms", int((time.time() - _t0) * 1000))
            if obj.get("ok") is False and not obj.get("code"):
                obj["code"] = ERROR_CODES.get(str(obj.get("error") or ""),
                                              "E_UNKNOWN")
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Request-Id", str(getattr(self, "rid", "")))
        self.end_headers()
        self.wfile.write(raw)

    def _fail(self, code, err, detail=""):
        Handler.mind.errors += 1
        # 错误也进 recent_errors，供 /status 回看（不落库，只在内存里留最近 20 条）
        try:
            _errs = getattr(Handler.mind, "recent_errors", None)
            if _errs is None:
                _errs = []
                Handler.mind.recent_errors = _errs
            _errs.append({"ts": time.time(), "rid": getattr(self, "rid", ""),
                          "code": code, "error": err,
                          "detail": str(detail)[:200],
                          "path": getattr(self, "last_path", "")})
            del _errs[:-20]
        except Exception as e:
            import card2 as _c2
            _c2.soft_fail("error_log", e)
        self._send(code, {"ok": False, "error": err, "detail": detail})

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        if n > MAX_BODY:
            raise ValueError("body too large: %d > %d" % (n, MAX_BODY))
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def _route(self, method):
        m = Handler.mind
        if m is None:
            return self._fail(503, "mind_not_ready", "服务还没初始化")
        if method == "OPTIONS":
            # 不接跨域预检：没有白名单就不给浏览器任何幻想
            self.send_response(405)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if not _origin_ok(self):
            m.blocked = getattr(m, "blocked", 0) + 1
            return self._fail(403, "cross_origin_blocked",
                              "只接受本机页面的请求（Origin/Referer 不是 127.0.0.1）")
        if os.environ.get("WHALE_REQUIRE_TOKEN") == "1":
            if (self.headers.get("X-Whale-Token") or "").strip() != api_token(m):
                m.blocked = getattr(m, "blocked", 0) + 1
                return self._fail(401, "unauthorized",
                                  "要带 X-Whale-Token（见卡目录 qq_state/api_token.txt）")
        m.requests += 1
        path = self.path.split("?")[0].rstrip("/") or "/"
        # 只读降级（V7 P0-4）：租约在别人手里时，写入口一律 409。
        # 不用 503 —— 那不是「服务挂了」，是「服务好的但不敢写」，
        # 前端看见 409 该退让，看见 503 只会以为要重试。
        if (method in ("POST", "PUT", "DELETE")
                and getattr(m, "readonly", False)
                and path not in GATE_READ_POST):
            _h = {}
            try:
                _h = dict((getattr(m, "lease", None) or {}).get("holder") or {})
            except Exception:
                _h = {}
            # 白名单之外（包括以后新加的、没登记的路径）默认拦住：
            # 没登记的那些会在 _gate_note_blocked 里留一行日志，方便补白名单。
            _gate_note_blocked(m, path)
            m.blocked = getattr(m, "blocked", 0) + 1
            return self._send(409, {
                "ok": False, "error": "readonly_lease_held",
                "code": ERROR_CODES.get("readonly_lease_held", "E_NOT_WRITER"),
                "holder": str(getattr(m, "lease_holder", "") or ""),
                "holder_detail": _h,
                "path": path,
                "detail": "本进程是只读的（%s）；要写就等另一份退出，或先关掉它"
                          % (getattr(m, "readonly_reason", "") or "只读模式"),
            })

        # V7 第 17 件：破坏性/不可逆接口必须带 token
        if (method in ("POST", "PUT", "DELETE")
                and path in TOKEN_REQUIRED_POST
                and (self.headers.get("X-Whale-Token") or "").strip() != api_token(m)):
            m.blocked = getattr(m, "blocked", 0) + 1
            _RATE_HITS["downgraded"] = _RATE_HITS.get("downgraded", 0)
            return self._fail(401, "unauthorized",
                              "%s 是破坏性接口，必须带 X-Whale-Token"
                              "（卡目录 qq_state/api_token.txt）" % path)
        try:
            if method == "GET" and path == "/health":
                with m.lock:
                    st = m.card.stats()
                sem = m.card.sem_stats() if m.card.sem_ready() else {
                    "ready": False}
                return self._send(200, {
                    "ok": True, "version": VERSION, "sealed": m.sealed,
                    "memories": st["total"], "db": os.path.abspath(m.db_path),
                    "uptime_s": round(time.time() - m.opened_ts, 1),
                    # 写者租约（V7 P0-4）：readonly=True 就是「本进程不敢写」。
                    # lease 里带 holder/epoch/剩余秒数，看得见是谁占着、还剩多久。
                    "readonly": bool(getattr(m, "readonly", False)),
                    "readonly_reason": (getattr(m, "readonly_reason", "") or None),
                    "lease": _lease_payload(m),
                    # 写闸（V7 P0-4 补口）：mode=readonly 时除白名单外的 POST 全 409。
                    # blocked_paths_n 是「启动以来被拦过的不同路径数」——
                    # 变大说明有调用方在撞闸，值得去看是谁。
                    "write_gate": {
                        "mode": ("readonly" if bool(getattr(m, "readonly", False))
                                 else "writer"),
                        "blocked_paths_n": len(
                            getattr(m, "gate_blocked", {}) or {}),
                    },
                    # 语义层只说三件事：模型多少维、卡里多数多少维、能用的占几成。
                    # 「覆盖率」那个数字会骗人 —— 换过模型的卡里，带着向量却
                    # 用不上的老条目也能算成 100%。所以这里报 usable_ratio。
                    "semantic": (
                        {"ready": True,
                         "model_dim": sem.get("model_dim"),
                         "emb_dims": sem.get("emb_dims"),
                         "usable_ratio": round(sem.get("usable_ratio") or 0, 4),
                         "note": sem.get("dim_note"),
                         "warn": sem.get("dim_warn")}
                        if sem.get("ready") else {"ready": False}),
                    # 时间线：唯一且连续的证据（/health 是给外部探活用的一行，
                    # 这里带上，免得探活时还得另开 /status）
                    "timeline": (
                        {"ticks": m.tl.count(), "chain_ok": m.chain_ok,
                         "chain_bad": m.chain_bad[:3],
                         "head": m.tl.head_hash()[:16],
                         "day": m.tl.days_since_birth(),
                         "birth_ok": m.birth_ok,
                         "birth_bad": m.birth_bad[:3],
                         "anchor_verdict": (m.anchor or {}).get("verdict"),
                         "anchor_reason": (m.anchor or {}).get("reason"),
                         # 写入闸：locked_by 非空 = 这卡被别的机器占着，只读
                         "writer": {"mine": m.writer,
                                    "head": m.tl.head_writer(),
                                    "locked_by": m.writer_lock,
                                    "readonly": bool(m.writer_lock)},
                         "today": m.tl.render_line()}
                        if m.tl is not None else {"error": "时间线未初始化"}),
                    # 日结对账：每条链路「今天有没有真的在动」。
                    # 11 个 bug 里 9 个是静默失败，这一块就是让它们下次能被看见。
                    "reconcile": _reconcile(m),
            "node": _node_info(m),
                    "boot": getattr(m, "_boot", None),
                    "threads": _thread_lines(m),
                    "write_queue": _wq_lines(m),
                })
            if method == "GET" and path == "/perf":
                # 读写分离第一步的出口：最近 200 条请求的分段耗时（只元数据，不含正文）
                if perf is None:
                    return self._send(503, {"ok": False, "error": "perf_disabled",
                                            "detail": "perf.py 没加载上"})
                return self._send(200, {
                    "ok": True, "perf": perf.stats(),
                    # 第 3 步的观察面：journal 模式是不是真的 WAL、读池多大
                    "db": (m.card.pool_stats()
                           if hasattr(m.card, "pool_stats") else {}),
                    # 后台世界推进的心跳：证明「没人在说话时，时间也在走」
                    "world_tick": {"last": getattr(m, "_world_tick_last", None),
                                   "err": getattr(m, "_world_tick_err", None),
                                   "beat_s": _world_tick_sec()}})
            if method == "GET" and path == "/stacks":
                # 诊断用：所有线程的栈（只看，不改状态）
                import sys as _sys
                _fr = _sys._current_frames()
                _out = []
                for _tid, _f in _fr.items():
                    _out.append("== thread %s\n%s" % (
                        _tid, "".join(traceback.format_stack(_f)[-6:])))
                return self._send(200, {"ok": True, "stacks": _out})

            if method == "GET" and path == "/version":
                # V7 第 18 件：版本矩阵（只读、缺项给 None、不抛）
                try:
                    import versions as _ver
                    _vm = _ver.collect(db_path=getattr(m, "db_path", None),
                                       mind_version=VERSION)
                    _vm["schema"]["db_at_open"] = getattr(
                        getattr(m, "card", None), "schema_db", None)
                except Exception as e:
                    _vm = {"error": str(e)[:200]}
                return self._send(200, {"ok": True, "versions": _vm})
            if method == "GET" and path == "/status":
                _st = m.status()
                # V7 第 16 件：把最近几次错误挂上去，配上错误码表 —— 这样
                # 「刚才那一下报的是什么」不用翻 stderr，也不用猜时间点。
                _st["recent_errors"] = list(getattr(m, "recent_errors", []) or [])[-10:]
                _st["error_codes"] = dict(ERROR_MEANINGS)
                _st["error_codes_n"] = len(ERROR_MEANINGS)
                _st["vars_doc"] = "docs/状态量字典-V7.md"
                return self._send(200, {"ok": True, "status": _st})
            if method == "GET" and path == "/chat/config":
                import chatcfg as _chat
                return self._send(200, {"ok": True, "cfg": _chat.load_cfg(),
                                        "spec": _chat.spec_json(),
                                        "path": _chat.CFG_PATH})
            if method == "GET" and path == "/inbox":
                # V5.4 收件层：看最近收到什么（默认只给 300 字摘要）
                from urllib.parse import parse_qs as _pq4
                _q4 = _pq4(self.path.split("?", 1)[1] if "?" in self.path else "")
                _lim = int((_q4.get("limit") or ["50"])[0])
                _hrs = float((_q4.get("hours") or ["0"])[0]) or None
                _unk = (_q4.get("unkept") or ["0"])[0] in ("1", "true")
                _full = (_q4.get("full") or ["0"])[0] in ("1", "true")
                return self._send(200, {
                    "items": m.card.inbox_list(limit=_lim, hours=_hrs,
                                               unkept_only=_unk, full=_full),
                    "stats": m.card.inbox_stats()})
            if method == "GET" and path == "/voice_config":
                return self._send(200, _voice_payload())
            if method == "GET" and path == "/voice_file":
                # 试听用的那条 mp3，直接回字节流。
                # 只认「音色缓存目录里的一个纯文件名」—— 不让路径参与，
                # 免得哪天有人用 ../ 把整台机器读一遍。
                from urllib.parse import parse_qs
                _qs = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                _nm = os.path.basename((_qs.get("n") or [""])[0])
                if not _nm or not _nm.lower().endswith(".mp3"):
                    return self._fail(400, "bad_name", "要一个 mp3 文件名")
                try:
                    _vc = _voice_mod()
                    _f = os.path.join(_vc.CACHE, _nm)
                    if not os.path.isfile(_f):
                        return self._fail(404, "not_found", "没有这条语音")
                    with open(_f, "rb") as _fh:
                        _data = _fh.read()
                except Exception as e:
                    return self._fail(500, "read_failed",
                                      "%s: %s" % (type(e).__name__, e))
                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Content-Length", str(len(_data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    self.wfile.write(_data)
                except Exception:
                    pass
                return
            if method == "GET" and path == "/skill_list":
                # 技能清单（V5.0 §7）。放 GET：它只读不写，
                # 探活脚本和面板都能直接 curl。
                with m.lock:
                    rows = m.card.skill.all()
                    out = [{"id": r["id"], "name": r["name"],
                            "desc": r["desc"], "L": round(float(r["L"]), 4),
                            "EF": round(float(r["EF"]), 4),
                            "I": round(float(r["I"]), 2),
                            "call_count": r["call_count"],
                            "tag": r["tag"]}
                           for r in rows]
                    st = m.card.skill.stats()
                return self._send(200, {"ok": True, "skills": out,
                                        "stats": st})
            if method == "GET" and path == "/config":
                # V5.0 §10.9 热改：读运行时配置（场景权重等）。
                # 🔧 2026-11-05（用户 m15228）：把「总预算 / 每格下限 / A 保底」
                #   和七张场景表一起露出来 —— 控制台的「上下文组合」栏就吃这一份。
                #   同时带上 defaults 便于前端标"这一项是你改过的还是默认的"。
                import ctxweight as _cw
                with m.lock:
                    cfg = _cw.load_config(m.card.db, force=True)
                    return self._send(200, {
                        "ok": True,
                        "scene_weights": cfg["scene_weights"],
                        "a_min": cfg["a_min"],
                        "cell_min": cfg["cell_min"],
                        "budget": cfg.get("budget") or _cw.BUDGET_DEFAULT,
                        # 自动跟随模型（2026-11-05 用户：能不能自动识别用的
                        # 什么模型、该调多少）。auto 只是开关，真正算多少在
                        # auto_now 里现读 —— 换模型不 bump _rev，缓存吃不得。
                        "auto": bool(cfg.get("auto")),
                        "auto_now": _cw.auto_now(m.card.db),
                        "models": _cw.model_table(),
                        "suggest_ratio": _cw.SUGGEST_RATIO,
                        "defaults": {
                            "scene_weights": dict(
                                (k, list(v)) for k, v
                                in _cw.SCENE_WEIGHTS.items()),
                            "a_min": _cw.A_MIN,
                            "cell_min": _cw.CELL_MIN,
                            "budget": _cw.BUDGET_DEFAULT},
                        "limits": {
                            "a_min": [0.0, 0.80],
                            "cell_min": [0, 4000],
                            "budget": [_cw.BUDGET_MIN, _cw.BUDGET_MAX]},
                        "dims": list(_cw.DIM_NAMES),
                        "cells": [
                            {"key": "identity", "name": "格1 我是谁",
                             "dims": "A+B"},
                            {"key": "longterm", "name": "格2 我一直记得的事",
                             "dims": "D"},
                            {"key": "recent", "name": "格3 我们最近在聊",
                             "dims": "C"},
                            {"key": "recall", "name": "格4 这句话让我想起了",
                             "dims": "E+F"}],
                        "scenes": list(_cw.SCENE_WEIGHTS.keys()),
                        "bad": cfg["bad"],
                        "raw": _cw.rc_all(m.card.db)})

            # ---- 控制台（2026-10-31 新增）----
            # 为什么这些路由长在 mind_server 里而不是另起一个服务：
            #   控制台要的东西（health / status / context / graph / timeline）
            #   全在这一个进程的手里。另起一个就得再开一次库、再抢一次锁、
            #   再维护一套端口 —— 而它最终要住进 U 盘，多一个进程就多一个
            #   插到别的机器上起不来的理由。
            if method == "GET" and path == "/console":
                # 控制台页面本身。返回 HTML 而不是 JSON，所以不走 _send。
                import console as _console
                _page = _console.PAGE.replace("__WHALE_TOKEN__", api_token(m))
                raw = _page.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)
                return None
            if method == "GET" and path == "/graph":
                # 星云图数据。不调 export_viz.collect() —— 那个要开 MemoryCard、
                # 算全部 weight_now，实测 1.5 秒；这里直接查两张表，几十毫秒。
                import console as _console
                return self._send(200, _console.graph_data(m.db_path))
            if method == "GET" and path == "/syslive":
                # 实时负载。前端 2 秒轮询一次，只拿 CPU/内存/磁盘/网络这几个数，
                # 不碰数据库、不碰记忆 —— 纯粹是"这台机器此刻的呼吸"。
                import console as _console
                return self._send(200, _console.sys_live())
            if method == "GET" and path == "/disks":
                # 盘子：插着哪些盘、谁是主盘、谁是备份 #n、在线没有、落后几节
                import disks as _disks
                here = os.path.dirname(os.path.abspath(m.card.db_path))
                st = _disks.status(here)
                st["retired"] = _disks.read_retired(here)
                st["retired_info"] = _disks.retired_info(here)
                return self._send(200, st)
            if method == "GET" and path == "/injection":
                # 防污层：最近被灌进来的可疑念头（只读，不改任何权重）
                with m.lock:
                    return self._send(200, m.card.injection_report())
            if method == "GET" and path.startswith("/media/"):
                # 出一帧画面。找不到 / 已被按规则删掉，返回 410 而不是 404 ——
                # 「曾经有，现在没了」和「从来就没有」是两件事，不能混成一个错。
                sha = path[len("/media/"):].strip().lower()
                if len(sha) != 64 or not all(ch in "0123456789abcdef" for ch in sha):
                    return self._fail(400, "bad_sha256", "要 64 位十六进制")
                with m.lock:
                    row = m.card.db.execute(
                        "SELECT path, thumb, mime FROM media WHERE sha256=?",
                        (sha,)).fetchone()
                    if row is None:
                        tomb = m.card.db.execute(
                            "SELECT caption, ts, reason FROM media_tomb WHERE sha256=?",
                            (sha,)).fetchone()
                        if tomb is not None:
                            return self._fail(
                                410, "media_dropped",
                                "这张画面 %s 按规则忘了（%s）。当时写的是：%s"
                                % (time.strftime("%Y-%m-%d %H:%M",
                                                 time.localtime(tomb["ts"])),
                                   tomb["reason"], tomb["caption"] or "—"))
                        return self._fail(404, "no_media", "没有这张画面")
                    rel = row["path"] or ""
                p = rel if os.path.isabs(rel) else os.path.join(m.card.card_dir, rel)
                if not os.path.exists(p):
                    return self._fail(410, "media_gone", "记录还在，文件没了")
                raw = open(p, "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", row["mime"] or "image/webp")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)
                return None
            if method == "GET" and path == "/dream/pending":
                # 梦境压缩（卡侧）：把还没消化的对话原文掏出来给外面的提炼器。
                # 顺手带上死信：失败过的批次要让人看得见、能重试，不能静默烂在库里。
                with m.lock:
                    out = dict(m.card.pending_dialogue() or {})
                    try:
                        out["dead"] = m.card.dream_dead(5)
                    except Exception:
                        out["dead"] = []
                    return self._send(200, out)
            if method == "GET" and path == "/dream/due":
                with m.lock:
                    return self._send(200, m.card.dream_due())
            if method == "GET" and path == "/timeline":
                import console as _console
                return self._send(200, {"ok": True,
                                        "timeline": _console.timeline_tail(m.db_path, 40),
                                        "files": _console.health_flag(m.db_path)})
            if method == "GET" and path == "/console_data":
                # 仪表盘一次要的全部数据（身份/激素历史/写入统计/分区/
                # 存储/体检/池）。合成一个请求 —— 前端轮询 4 个接口比 1 个
                # 慢得多，而且容易读到不同时刻的快照。
                import console as _console
                import ctxweight as _cw
                with m.lock:
                    m.flow_time()
                    # 时间窗：/console_data?hours=5 分钟…168 小时，缺省 72。
                    _hq = None
                    _live = False
                    try:
                        from urllib.parse import parse_qs as _pqs
                        _q = _pqs(self.path.split("?", 1)[1] if "?" in self.path else "")
                        if _q.get("hours"):
                            _hq = float(_q["hours"][0])
                        if _q.get("live") and _q["live"][0] in ("1", "true", "yes"):
                            _live = True
                    except Exception:
                        _hq, _live = None, False
                    # 监视器模式：先让身体按时间走一格再读。
                    # 没有新对话就没有新样本，曲线会看着冻住 —— 采的是「此刻」。
                    if _live:
                        try:
                            m.card.viscera_sample()
                        except Exception:
                            pass
                    ov = _console.overview(m.db_path, hours=_hq)
                    # 六维权重在这里现算，不让前端去调 /context ——
                    # 那会触发一次真实 recall、写 recall_event，
                    # 等于「打开控制台」这件事本身在改记忆权重。
                    try:
                        import card2 as _c2
                        # budget 传 None = 听卡里配的 BUDGET（跟 /context 走的
                        # 是同一条路）；以前这里写死 _c2.TOKEN_BUDGET，
                        # 结果「控制台显示的总预算」和「真跑起来用的」会分家。
                        plan = _cw.compute(
                            "", None,
                            hormones=getattr(m.card, "h", None),
                            turns=getattr(m.card, "turn_count", None),
                            db=m.card.db)
                        ov["weights"] = plan["weights"]
                        ov["scene"] = plan["scene"]
                        # 控制台「上下文组合」栏要的那几个数（这一轮用多少、
                        # 四格怎么分）—— 现算，跟 /config 里的配置是同一份真值。
                        try:
                            cfg = _cw.load_config(m.card.db)
                            ov["ctx"] = {
                                "scene": plan["scene"],
                                "weights": plan["weights"],
                                "total": plan["total"],
                                "quota": plan["quota"],
                                "trace": plan["trace"],
                                "a_min": cfg["a_min"],
                                "cell_min": cfg["cell_min"],
                                "budget": cfg.get("budget") or _cw.BUDGET_DEFAULT,
                                "bad": cfg["bad"],
                            }
                        except Exception:
                            ov["ctx"] = None
                    except Exception as e:
                        ov["weights"] = None
                        ov["ctx"] = None
                        ov["weights_err"] = str(e)
                    # 🔧 2026-11-09：欲望层/习惯层/梦层以前在控制台上完全没有影子 ——
                    #    三层都在跑（动机进了上下文、习惯挖矿成了维护线程的一部分），
                    #    但面板上看不到，等于「装了但没人知道它在不在动」。
                    #    这里把三层的**当前实况**塞进同一份数据，前端一个小卡就够。
                    try:
                        ov["motiv"] = m.card.motiv_snapshot()
                    except Exception as e:
                        ov["motiv"] = {"err": str(e)}
                    try:
                        # 工作台（worklog）：换窗口后我靠它接上，控制台上也给它一格。
                        ov["worklog"] = m.card.worklog_recent(n=3)
                    except Exception as e:
                        ov["worklog"] = []
                        ov["worklog_err"] = str(e)
                    try:
                        _hrows = m.card.db.execute(
                            "SELECT key,status,hits,days FROM habits"
                            " ORDER BY (status='weak') DESC, hits DESC LIMIT 6").fetchall()
                        ov["habits"] = [dict(r) for r in _hrows]
                    except Exception as e:
                        ov["habits"] = []
                        ov["habits_err"] = str(e)
                    try:
                        # 注意：console.overview 已经给了 {"pending","dreamed"}，
                        # 这里只能**合并**不能盖 —— 直接赋值会把「已消化轮次」冲成 0。
                        _dd = m.card.dream_due() or {}
                        _dg = dict(ov.get("dream") or {})
                        _dg.update(_dd)
                        ov["dream"] = _dg
                    except Exception as e:
                        ov["dream"] = ov.get("dream") or {"err": str(e)}
                return self._send(200, ov)
            if method == "GET" and path == "/avatar":
                # 头像文件本身。走文件不走数据库 —— 见 console.py 的说明。
                # 没有就 404，前端据此退回内置那张 SVG。
                p = m.card.identity.avatar_path()
                if not p or not os.path.exists(p):
                    return self._fail(404, "no_avatar", "还没设过头像")
                with open(p, "rb") as f:
                    raw = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(raw)))
                # 头像文件名不变，靠 URL 上的 ?v= 破缓存；这里允许长缓存，
                # 但版本戳一变 URL 就变，所以不会看到旧图。
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                self.wfile.write(raw)
                return None
            if method == "GET" and path == "/cardslot":
                # 卡槽状态：现在是插着还是没插。
                # 只读 —— 真正的插/拔走 POST，且必须显式给 action。
                import cardslot as _slot
                return self._send(200, {"ok": True, "cardslot": _slot.status()})

            if method == "GET" and path == "/people":
                # 关系卡列表：按出现次数排。只记频次与分区，不记评价。
                try:
                    from urllib.parse import parse_qs as _pqs
                    _pq = _pqs(self.path.split("?", 1)[1] if "?" in self.path else "")
                    lim = int((_pq.get("limit") or ["30"])[0])
                except Exception:
                    lim = 30
                try:
                    return self._send(200, {"ok": True,
                                            "people": m.card.people(limit=lim)})
                except Exception as e:
                    return self._fail(500, "people_read", str(e))

            if method == "GET" and path == "/person":
                # 一个人的关系卡。没有就是没有，不编。
                from urllib.parse import parse_qs as _pqs2
                _pq2 = _pqs2(self.path.split("?", 1)[1] if "?" in self.path else "")
                uid = (_pq2.get("uid") or [""])[0]
                try:
                    p = m.card.person_card(uid)
                except Exception as e:
                    return self._fail(500, "person_read", str(e))
                if not p:
                    return self._send(404, {"ok": False, "reason": "not_found"})
                return self._send(200, {"ok": True, "person": p})

            if method == "GET" and path == "/motiv":
                # 动机层快照（驱力池 + 上一轮决策）
                try:
                    return self._send(200, {"ok": True,
                                            "motiv": m.card.motiv_snapshot()})
                except Exception as e:
                    return self._fail(500, "motiv_read", str(e))

            if method == "GET" and path == "/recall/trace":
                try:
                    _n = int((parse_qs(self.path.split("?", 1)[1])
                              .get("n", ["10"])[0]) if "?" in self.path else 10)
                except Exception:
                    _n = 10
                with m.lock:
                    _rows = m.card.recall_trace_recent(n=max(1, min(50, _n)))
                self._send(200, {"ok": True, "n": len(_rows), "traces": _rows})
                return
            if method == "GET" and path == "/narrative":
                # 我自己的叙事：最近一版 + 历史（只读）。
                try:
                    import narrative as _nrv0
                    _c0 = sqlite3.connect(m.card.db_path)
                    _c0.row_factory = sqlite3.Row
                    _lates0 = _nrv0.latest(_c0)
                    _hist0 = _nrv0.history(_c0, limit=10)
                    _c0.close()
                except Exception as e:
                    return self._fail(500, "narrative_read", str(e))
                return self._send(200, {"ok": True, "latest": _lates0,
                                        "history": _hist0})
            if method == "GET" and path == "/worklog":
                # 工作日志（那张「桌子」）：只读最近几条，给控制台和 bridge 看。
                try:
                    from urllib.parse import parse_qs as _pqsW
                    _pqW = _pqsW(self.path.split("?", 1)[1] if "?" in self.path else "")
                    lim = int((_pqW.get("n") or _pqW.get("limit") or ["8"])[0])
                except Exception:
                    lim = 8
                try:
                    return self._send(200, {"ok": True,
                                            "items": m.card.worklog_recent(n=lim)})
                except Exception as e:
                    return self._fail(500, "worklog_read", str(e))

            if method == "GET" and path == "/impression":
                # 印象层：谁在什么情境下常做什么。只读。
                try:
                    from urllib.parse import parse_qs as _pqsI
                    _pqI = _pqsI(self.path.split("?", 1)[1] if "?" in self.path else "")
                    subj = (_pqI.get("subject") or [""])[0] or None
                    lim = int((_pqI.get("limit") or ["20"])[0])
                    minw = float((_pqI.get("min_w") or ["0"])[0])
                except Exception:
                    subj, lim, minw = None, 20, 0.0
                try:
                    _items = m.card.impression_top(subject=subj, limit=lim, min_w=minw)
                    # 控制台按 it.scene 渲染，而表里存的是 scene_time/scene_event/
                    # scene_mood 三列 —— 以前两边的字段名对不上，面板永远显示
                    # 「… · → 反应」，情境那一段是空的。在这里补一个拼好的 scene。
                    for _it in _items:
                        _sc = "".join([str(x) for x in (
                            _it.get("scene_time") or "",
                            _it.get("scene_event") or "") if x])
                        _it["scene"] = _sc or "平时"
                    return self._send(200, {
                        "ok": True,
                        "impressions": _items,
                        "stat": m.card.impression_snapshot()})
                except Exception as e:
                    return self._fail(500, "impression_read", str(e))

            if method == "GET" and path == "/viscera/profile":
                try:
                    return self._send(200, {"ok": True,
                                            "profile": viscera.current_profile(),
                                            "ranges": {k: list(v) for k, v in
                                                       viscera._PROFILE_RANGE.items()}})
                except Exception as e:
                    return self._fail(500, "viscera_profile", str(e))

            if method == "GET" and path == "/viscera":
                # 内分泌 + 自主神经 + 躯体体征（数字给控制台看，不进口语上下文）
                try:
                    return self._send(200, {"ok": True,
                                            "viscera": m.card.viscera_snapshot()})
                except Exception as e:
                    return self._fail(500, "viscera_read", str(e))

            if method == "POST":
                body = self._body()
                if path == "/context":
                    msg = (body.get("message") or "").strip()
                    if not msg:
                        return self._fail(400, "empty_message", "message 不能为空")
                    if len(msg) > MAX_TEXT:
                        return self._fail(413, "text_too_large",
                                          "message %d 字符 > 上限 %d"
                                          % (len(msg), MAX_TEXT))
                    out = m.context(msg, body.get("recent"), body.get("budget"))
                    return self._send(200, {"ok": True, "context": out})
                if path == "/chat/config":
                    # 对话控制面（2026-11-06）：整页参数一次存回。
                    # 只认 chatcfg.SPEC 里的键，别的键静默丢掉 ——
                    # 前端拼错的 key 不该把配置文件写脏。
                    import chatcfg as _chat
                    _patch = body.get("patch") if isinstance(body, dict) else None
                    if not isinstance(_patch, dict):
                        _patch = {k: v for k, v in (body or {}).items()
                                  if k in _chat._BY_KEY}
                    try:
                        _cfg = _chat.save_cfg(_patch)
                    except Exception as _e:
                        return self._fail(500, "chat_cfg_write", str(_e))
                    return self._send(200, {"ok": True, "cfg": _cfg})
                if path == "/motiv/tick":
                    # 走一轮动机。quiet / at_school 由调用方给（值守脚本知道作息）
                    try:
                        m.flow_time()
                        _q = bool(body.get("quiet"))
                        _s = bool(body.get("at_school"))
                        _res = m.card.motiv_tick(quiet=_q, at_school=_s)
                        try:
                            m.tl.append("motiv", _res.get("line") or "动机",
                                        allow_foreign=True)
                        except Exception:
                            pass
                        return self._send(200, {"ok": True, "motiv": _res})
                    except Exception as _e:
                        return self._fail(500, "motiv_tick", str(_e))
                if path == "/motiv/done":
                    # 做完记一笔：speak=True 才占「主动开口」的额度
                    try:
                        _kind = str(body.get("kind") or "")
                        _spoke = bool(body.get("spoke"))
                        if not _kind:
                            return self._fail(400, "empty_kind", "kind 不能为空")
                        return self._send(200, {"ok": True,
                                                "motiv": m.card.motiv_done(
                                                    _kind, spoke=_spoke)})
                    except Exception as _e:
                        return self._fail(500, "motiv_done", str(_e))
                if path == "/viscera/profile":
                    # 改身体参数（写进卡，不是改源码）
                    try:
                        _patch = body.get("profile") or {}
                        _ok, _why = m.card.set_viscera_profile(_patch)
                        if not _ok:
                            return self._fail(400, "bad_viscera_profile", _why)
                        return self._send(200, {"ok": True,
                                                "profile": viscera.current_profile()})
                    except Exception as _e:
                        return self._fail(500, "viscera_profile_write", str(_e))
                if path == "/viscera/stim":
                    # 手动给一记刺激（测试/调试用）：只动身体，不写记忆
                    try:
                        m.flow_time()
                        _kinds = body.get("events") or ["chat"]
                        if isinstance(_kinds, str):
                            _kinds = [_kinds]
                        _strength = float(body.get("strength") or 1.0)
                        m.card.viscera_stimulate(_kinds, _strength)
                        return self._send(200, {"ok": True,
                                                "viscera": m.card.viscera_snapshot()})
                    except Exception as _e:
                        return self._fail(500, "viscera_stim", str(_e))
                if path == "/talk/set":
                    # 2026-11-06：这个老接口现在也走 chatcfg（单一事实来源），
                    # 免得控制台上两个地方各存一份、各自以为自己是准的。
                    import chatcfg as _chat
                    try:
                        _cap = int(body.get("cap", 8))
                        _cool = int(body.get("cooldown_min", 3))
                    except Exception:
                        return self._fail(400, "bad_numbers", "cap / cooldown_min 得是整数")
                    _cap = max(1, min(40, _cap))
                    _cool = max(0, min(600, _cool))
                    try:
                        _cc = _chat.save_cfg({"freq.daily_cap": _cap,
                                              "freq.cooldown_min": _cool})
                    except Exception as _e:
                        return self._fail(500, "chat_cfg_write", str(_e))
                    return self._send(200, {"ok": True,
                            "cfg": {"cap": _cc["freq.daily_cap"],
                                    "cooldown_min": _cc["freq.cooldown_min"]}})
                if path == "/viscera/stim":
                    # 手动给一记刺激（测试/调试用）：不写记忆，只动身体
                    try:
                        kinds = body.get("events") or ["chat"]
                        if isinstance(kinds, str):
                            kinds = [kinds]
                        strength = float(body.get("strength") or 1.0)
                        for _k in kinds:
                            self.card.viscera_touch(str(_k), emotion=strength)
                        return self._send(200, {"ok": True,
                                                "viscera": self.card.viscera_snapshot()})
                    except Exception as e:
                        return self._fail(500, "viscera_stim", str(e))
                if path == "/worklog":
                    # 工作日志：那张「桌子」。带 title 就是写一条，不带就是列最近几条。
                    # 为什么单独开一个接口而不是塞进 /remember：它不该进 mem 池、
                    # 不该参与召回和衰减 —— 它是笔记本，不是记忆。
                    try:
                        _t = (body.get("title") or "").strip()
                        if _t:
                            _id = m.card.worklog_add(
                                _t, body.get("body") or "",
                                next_step=body.get("next_step") or "",
                                kind=body.get("kind") or "work",
                                source=body.get("source") or "http")
                            return self._send(200, {"ok": True, "id": _id})
                        _wl = m.card.worklog_recent(n=int(body.get("n") or 6))
                        return self._send(200, {"ok": True, "items": _wl})
                    except Exception as e:
                        return self._fail(500, "worklog", str(e))
                if path == "/trace":
                    # V7 第 7 条：一条记忆能不能翻回它当初的原话。
                    # mem 只存提炼件（会用「他说…」这种转述），原文在 dialogue 表，
                    # 靠 dialogue.mem_id 指回来 —— 这里就是把那条链一次走完。
                    try:
                        _mid = int(body.get("mem_id") or body.get("id") or 0)
                        if not _mid:
                            return self._fail(400, "empty_id", "mem_id 不能为空")
                        _db = m.card.db
                        _row = _db.execute("SELECT * FROM mem WHERE id=?", (_mid,)).fetchone()
                        if _row is None:
                            return self._send(404, {"ok": False, "error": "no_such_mem"})
                        # vec 是二进制向量，不能进 JSON；顺便把大字段截短，
                        # 免得一条 /trace 把整个 6KB 向量原样吐出来。
                        _mem = {}
                        for _k in _row.keys():
                            _v = _row[_k]
                            if isinstance(_v, (bytes, bytearray, memoryview)):
                                _mem[_k] = "<%d bytes>" % len(_v)
                            elif isinstance(_v, str) and len(_v) > 400:
                                _mem[_k] = _v[:400] + "…"
                            else:
                                _mem[_k] = _v
                        _dl = _db.execute(
                            "SELECT id, role, text, ts, source FROM dialogue"
                            " WHERE mem_id=? ORDER BY id", (_mid,)).fetchall()
                        _dlg = [{"id": r["id"], "role": r["role"], "text": r["text"],
                                 "ts": r["ts"], "source": r["source"]} for r in _dl]
                        return self._send(200, {"ok": True, "mem": _mem, "dialogue": _dlg,
                                                "traceable": bool(_dlg)})
                    except Exception as e:
                        return self._fail(500, "trace", str(e))
                if path == "/promise/list":
                    try:
                        import promises as _P
                        _conn = _P.connect(m.card.db_path)
                        _P.ensure(_conn)
                        _P.due_promises(now=time.time(), conn=_conn)
                        _rows = _P.open_promises(conn=_conn)
                        _conn.close()
                        return self._send(200, {"ok": True, "items": [
                            {"id": r["id"], "who": r["who"], "what": r["what"],
                             "state": r["state"], "due_ts": r["due_ts"],
                             "due": _P._fmt_ts(r["due_ts"]) if r["due_ts"] else "—"} for r in _rows]})
                    except Exception as e:
                        return self._fail(500, "promise_list", str(e))
                if path == "/promise/add":
                    try:
                        import promises as _P
                        what = (body.get("what") or "").strip()
                        if not what:
                            return self._fail(400, "empty_what", "what 不能为空")
                        _ts = body.get("ts") or time.time()
                        _due = float(body.get("due_ts") or 0) or _promise_due_from(what, now=_ts)
                        _conn = _P.connect(m.card.db_path)
                        _P.ensure(_conn)
                        _pid2 = _P.add_promise(what, due_ts=_due,
                                               who=(body.get("who") or "user"), conn=_conn, ts=_ts)
                        _conn.commit(); _conn.close()
                        return self._send(200, {"ok": True, "id": _pid2, "due_ts": _due})
                    except Exception as e:
                        return self._fail(500, "promise_add", str(e))
                if path == "/promise/close":
                    try:
                        import promises as _P
                        _pid3 = int(body.get("id") or 0)
                        if not _pid3:
                            return self._fail(400, "empty_id", "id 不能为空")
                        _conn = _P.connect(m.card.db_path)
                        _P.ensure(_conn)
                        _ok = _P.close_promise(_pid3, (body.get("state") or "kept"),
                                               note=(body.get("note") or ""), conn=_conn)
                        _conn.commit(); _conn.close()
                        return self._send(200 if _ok else 404,
                                          {"ok": bool(_ok), "id": _pid3})
                    except Exception as e:
                        return self._fail(500, "promise_close", str(e))
                if path == "/narrative/write":
                    # 写一版新的自我叙事。旧的留着，rev 自增。
                    _txt = (body.get("text") or "").strip()
                    if not _txt:
                        return self._fail(400, "empty_text", "text 不能为空")
                    try:
                        import narrative as _nrv1
                        _c1 = sqlite3.connect(m.card.db_path, timeout=10.0)
                        _c1.row_factory = sqlite3.Row
                        _id1 = _nrv1.write(_c1, _txt, why=str(body.get("why") or ""))
                        _c1.close()
                    except Exception as e:
                        return self._fail(500, "narrative_write", str(e))
                    return self._send(200, {"ok": True, "id": _id1,
                                            "chars": len(_txt)})
                if path == "/tevent/list":
                    try:
                        import tempora as _T0
                        _c0 = sqlite3.connect(m.card.db_path)
                        _c0.row_factory = sqlite3.Row
                        _items = _T0.EventStore(_c0).recent_events(
                            int(body.get("limit") or 10))
                        _c0.close()
                        return self._send(200, {"ok": True, "items": _items})
                    except Exception as e:
                        return self._fail(500, "tevent_list", str(e))
                if path == "/tevent/chain":
                    try:
                        _tid = int(body.get("id") or 0)
                        if not _tid:
                            return self._fail(400, "empty_id", "id 不能为空")
                        import tempora as _T1
                        _c1 = sqlite3.connect(m.card.db_path)
                        _c1.row_factory = sqlite3.Row
                        _ev1 = _T1.EventStore(_c1).chain_full(_tid)
                        _c1.close()
                        if not _ev1:
                            return self._fail(404, "not_found", "没有这条事件")
                        return self._send(200, {"ok": True, "event": _ev1})
                    except Exception as e:
                        return self._fail(500, "tevent_chain", str(e))
                if path == "/report":
                    um = (body.get("user_message") or "").strip()
                    # V7 第 19 件：脏输出不落库。
                    # 换模型、或者宿主在工具调用里顺手把半截 JSON / <think> 块
                    # 塞进来，一旦进了 mem 就再也分不清「他说过的话」和
                    # 「她的思考残渣」。这里洗一道，拦下的直接 400。
                    _rl = (body.get("reply") or "")
                    um, _d1 = card2.clean_store_text(um)
                    _rl, _d2 = card2.clean_store_text(_rl)
                    _dirty = [("user_message", x) for x in _d1] + [("reply", x) for x in _d2]
                    if any(x.startswith("rejected:") for _k, x in _dirty):
                        card2.soft_fail("dirty_output", None,
                                         ",".join("%s:%s" % (k, x) for k, x in _dirty))
                        return self._send(400, {
                            "ok": False, "error": "dirty_output",
                            "code": ERROR_CODES["bad_request"],
                            "detail": "有一侧看着像工具调用/推理残渣，不是人说的话",
                            "problems": [{"side": k, "problem": x} for k, x in _dirty]})
                    if not um:
                        return self._fail(400, "empty_message", "user_message 不能为空")
                    # 承诺账本（2026-11-11）：从这一轮的话里挑「说好了要做」的一句记下来。
                    # 只记不催 —— 到期由 promises.promise_block 在上下文里摆出来。
                    try:
                        _made = _capture_promises(um, _rl, db=m.card.db_path)
                        if _made:
                            m.card.flush_deferred_effects()
                    except Exception as _ce:
                        card2.soft_fail("promise_capture", _ce)
                    if len(um) > MAX_TEXT:
                        return self._fail(413, "text_too_large",
                                          "user_message %d 字符 > 上限 %d"
                                          % (len(um), MAX_TEXT))
                    try:
                        job = m.wq_submit(
                            lambda: m.report(um, body.get("reply") or "",
                                             body.get("ts"),
                                             source=body.get("source") or "chat"))
                        if job is None:
                            return self._fail(429, "queue_full",
                                              "写队列满了（上限 %d），等下一轮"
                                              % m._wq.maxsize)
                        # 有界等待：多数情况几秒内就完，调用方拿到和以前一样的
                        # 返回体；真慢到超时也不占住这个请求线程。
                        try:
                            wait_s = float(os.environ.get("WHALE_REPORT_WAIT", "5") or 5)
                        except Exception:
                            wait_s = 5.0
                        job["ev"].wait(wait_s)
                        if not job["ev"].is_set():
                            return self._send(202, {
                                "ok": True, "queued": True,
                                "request_id": job["rid"],
                                "queue_depth": m._wq.qsize(),
                                "note": "已排进写队列，还在写；不会丢，卡里迟早会有"
                            })
                        if job["err"] is not None:
                            e = job["err"]
                            if isinstance(e, timeline_mod.ForeignWriter):
                                return self._fail(409, "card_locked", str(e))
                            return self._fail(500, "report_failed", str(e))
                        return self._send(200, {"ok": True, "report": job["out"],
                                                "request_id": job["rid"]})
                    except timeline_mod.ForeignWriter as e:
                        return self._fail(409, "card_locked", str(e))
                if path == "/remember":
                    # 🔧 2026-11-04：任意通道的单条记忆入口。
                    #   以前只有 /report（一问一答）能写卡，于是 QQ、B站、抖音里
                    #   发生的事全进不来 —— 「考试考得不错」就是这么丢的。
                    #   这里不假装有对话对象：text 就是"发生了什么/我说了什么"，
                    #   source 标明从哪来，speaker 默认 self（是我经历的）。
                    tx = (body.get("text") or "").strip()
                    # V7 第 19 件：脏输出不落库（和 /report 同一个闸）。
                    tx, _dirty = card2.clean_store_text(tx)
                    if any(x.startswith("rejected:") for x in _dirty):
                        with m.lock:
                            card2.soft_fail("dirty_output", None, ",".join(_dirty))
                        return self._send(400, {
                            "ok": False, "error": "dirty_output",
                            "code": ERROR_CODES["bad_request"],
                            "detail": "这看着像工具调用/推理残渣，不是一句话",
                            "problems": _dirty})
                    if not tx:
                        return self._fail(400, "empty_text", "text 不能为空")
                    if len(tx) > MAX_TEXT:
                        return self._fail(413, "text_too_large",
                                          "text %d 字符 > 上限 %d"
                                          % (len(tx), MAX_TEXT))
                    src = (body.get("source") or "").strip() or "unknown"
                    # V7 第 17 件：没带 token 的调用方，自报的可信来源一律降级。
                    _tok_ok = ((self.headers.get("X-Whale-Token") or "").strip()
                               == api_token(m))
                    if (src in TRUST_SOURCES) and not _tok_ok:
                        src = "unverified:" + src
                        _RATE_HITS["downgraded"] += 1
                    _ok_r, _retry = _rate_ok("remember", src, 60, 60.0)
                    if not _ok_r:
                        _RATE_HITS["limited"] += 1
                        return self._fail(429, "rate_limited",
                                          "这条来源 60 秒内超过 60 次，%.0fs 后再试"
                                          % _retry)
                    # 🔧 2026-11-05：/remember 支持 pool。
                    #    病根（外部七问第 1 问）：默认池是 short，
                    #    一次灌 1600 条历史时短期池只有 32 个位置，
                    #    被踢出去的那些海马分不够 → 直接遗忘（实测丢 1020 条）。
                    #    迁历史必须显式给 pool="long"。
                    _pool = (body.get("pool") or "").strip() or None
                    if _pool is not None and _pool not in ("short", "medium", "long"):
                        return self._fail(400, "bad_pool",
                                          "pool 只能是 short/medium/long")
                    with m.lock:
                        if m.writer_lock:
                            return self._fail(409, "card_locked",
                                              "这张卡正被另一台机器用（%s）"
                                              % m.writer_lock)
                        m.flow_time()
                        try:
                            _kw = {}
                            if _pool:
                                _kw["pool"] = _pool
                            mid = m.card.remember(
                                tx,
                                salience=float(body.get("salience") or 0.55),
                                ts=body.get("ts"),
                                speaker=(body.get("speaker") or "self"),
                                region=body.get("region"),
                                source=src,
                                about_uid=str(body.get("about_uid") or ""),
                                **_kw)
                        except Exception as e:
                            return self._fail(500, "remember_failed", str(e))
                        # 2026-11-05：剥完壳是空的（纯系统工单）→ 不写、明确告诉调用方
                        if mid is None:
                            return self._send(200, {"ok": True, "mem_id": None,
                                                    "skipped": "empty_after_strip",
                                                    "source": src, "birth": None})
                        birth = None
                        if m.tl is not None and mid:
                            try:
                                m.tl.birth_stamp(mid)
                                birth = True
                            except Exception as e:
                                m.birth_bad.append("出生证写入失败：%s" % e)
                                birth = False
                        # 原文也要落一份（2026-11-09）：
                        # 以前 /remember 只写 mem，dialogue 从此停更，
                        # 习惯挖掘/梦层/翻原话读到的都是十月底那一截。
                        try:
                            m.card.log_dialogue(
                                mid, (body.get("speaker") or "self"),
                                tx, ts=body.get("ts"), source=src)
                        except Exception as e:
                            print("[mind] 原文落盘失败：%s" % e, file=sys.stderr)
                    return self._send(200, {"ok": True, "mem_id": mid,
                                            "source": src, "birth": birth})
                if path == "/recent":
                    # 短期对话原文：从卡里读，不从调用方带。
                    # 调用方传 recent 时这里**仍然以卡为准** —— 卡是唯一真值源，
                    # 模型那边带过来的只当补充，不能反过来盖掉卡。
                    return self._send(200, {"ok": True, "recent": m.recent_dialogue(
                        body.get("n") or 12, body.get("max_chars") or 2000)})
                if path == "/recall":
                    q = (body.get("query") or "").strip()
                    if not q:
                        return self._fail(400, "empty_query", "query 不能为空")
                    with m.lock:
                        m.flow_time()
                        hits = m.card.recall(q, top_k=int(body.get("top_k") or 5),
                                             access=False)
                        return self._send(200, {"ok": True, "hits": [
                            {"id": r["id"], "score": round(float(s), 4),
                             "w_now": round(float(w), 4),
                             "text": m.card.render(r, w)}
                            for s, w, r in hits]})
                if path in ("/disks/backup", "/disks/sync", "/disks/switch"):
                    # 盘子：备份到某块盘 / 同步某块备份盘 / 主备互换。
                    # db 一律走 sqlite 快照（服务跑着也一致），所以这里不用停服务。
                    import disks as _disks
                    target = (body.get("path") or "").strip()
                    if not target:
                        return self._fail(400, "no_path", "要给出目标目录")
                    if not body.get("confirm"):
                        return self._fail(400, "need_confirm",
                                          "这是写盘动作，body 里要带 confirm=true")
                    here = os.path.dirname(os.path.abspath(m.card.db_path))
                    with m.lock:
                        if path == "/disks/backup":
                            out = _disks.backup_to(here, target, confirm=True)
                        elif path == "/disks/sync":
                            # reseed=true 才允许"分叉也覆盖"：那是丢掉备份盘上
                            # 主盘没有的孤儿记录，必须由人显式点。
                            out = _disks.sync_to(here, target, confirm=True,
                                                 reseed=bool(body.get("reseed")))
                        else:
                            out = _disks.switch_role(here, target)
                    return self._send(200 if out.get("ok") else 409, out)
                if path == "/disks/retire":
                    # 「带卡出门」：把本机这份标记成退役 → 之后本机只读。
                    # 为什么要有这一步：主备标签挡不住「两份拷贝各自都能写」，
                    # 卡写的时候根本不看 role。只有让留下的这份自己认输，
                    # 才不会出现两个「我」。
                    import disks as _disks
                    here = os.path.dirname(os.path.abspath(m.card.db_path))
                    on = bool(body.get("on", True))
                    why = (body.get("why") or "").strip()
                    with m.lock:
                        if on:
                            _disks.set_retired(here, True, why or "带卡出门")
                            m.writer_lock = "retired"
                        else:
                            _disks.set_retired(here, False)
                            m.writer_lock = ""
                    return self._send(200, {
                        "ok": True, "retired": on, "card_dir": here,
                        "readonly_now": bool(m.writer_lock),
                        "how": ("现在本机这份只读，可以拔盘了。到新机器双击一键落地"
                                "会自动接手；回来再双击一次就交接回来。")
                        if on else "已解除退役，这张卡重新上岗。"})
                if path == "/disks/carry":
                    # 「带卡出门」的完整版（2026-11-13）：
                    #   /disks/retire 只标退役、不搬数据 —— 那是老行为，
                    #   现在控制台走这里：合并 WAL → 镜像整张卡 → 快照 db →
                    #   刷清单重签 → 校验 → 再退役。
                    # 长活在锁外做（镜像要几十秒，锁内做会把整个服务堵住；
                    # 卡库用 sqlite 快照，服务跑着也是自洽的）。
                    import disks as _disks
                    here = os.path.dirname(os.path.abspath(m.card.db_path))
                    target = (body.get("path") or "").strip()
                    if not target:
                        return self._fail(400, "no_path", "要给出目标目录（随身盘根目录）")
                    if not body.get("confirm"):
                        return self._fail(400, "need_confirm",
                                          "这是写盘动作，body 里要带 confirm=true")
                    if not os.path.isdir(target):
                        return self._fail(400, "no_target",
                                          "目标目录不存在或没插好：%s" % target)
                    if os.path.abspath(target) == here:
                        return self._fail(400, "same_dir", "目标就是自己，不用搬")
                    try:
                        sys.path.insert(0, os.path.join(here, "tools"))
                        import carry as _carry
                        out = _carry.carry(here, target, log=lambda *_a, **_k: None)
                    except Exception as e:               # noqa: BLE001
                        return self._fail(500, "carry_failed", "镜像失败：%r" % e)
                    if out.get("ok") and body.get("retire", True):
                        with m.lock:
                            _disks.set_retired(here, True,
                                               "带卡出门：镜像完成 @ %s"
                                               % time.strftime("%Y-%m-%d %H:%M"))
                            m.writer_lock = "retired"
                        out["retired"] = True
                        out["how"] = ("镜像 + 校验都过了，本机这份已只读。"
                                      "现在可以拔盘；到新电脑双击「一键部署」。")
                    elif out.get("ok"):
                        out["how"] = "镜像 + 校验过了，本机没退役（retire=false）。"
                    return self._send(200 if out.get("ok") else 409, out)


                if path == "/mem/untrust":
                    # 人工解除一次防污误判（"这句是玩笑"）。
                    # 规则判断不了语气，只有人能；所以这条路故意做成手动。
                    mid = body.get("id")
                    if not mid:
                        return self._fail(400, "no_id", "要给出记忆 id")
                    reason = (body.get("reason") or "").strip() or "人工判定：那是玩笑"
                    with m.lock:
                        out = m.card.untrust(mid, reason=reason)
                        if out.get("changed") and m.tl is not None:
                            try:
                                m.tl.append("untrust", "解除防污误判 id=%s：%s"
                                           % (mid, reason))
                            except Exception as e:
                                out["timeline_error"] = str(e)
                    if not out.get("ok"):
                        return self._send(404, out)
                    return self._send(200, out)
                if path == "/intake":
                    # V5.4 统一收件口：以后接任何 APP 都只是往这里扔原文
                    _txt = (body.get("text") or "").strip()
                    if not _txt:
                        return self._fail(400, "empty_text", "要给出 text")
                    _id = m.card.inbox_add(
                        _txt, source=body.get("source", ""),
                        channel=body.get("channel", "private"),
                        who=body.get("who", ""), uid=body.get("uid", ""),
                        gid=body.get("gid", ""), kind=body.get("kind", "text"),
                        ts=body.get("ts"), note=body.get("note", ""))
                    if _id is None:
                        return self._fail(400, "intake_failed", "收件层没写进去")
                    try:
                        if m.tl is not None:
                            m.tl.append("intake", "收到一条 %s：%s" % (
                                body.get("source", "?"), _txt[:40]))
                    except Exception:
                        pass
                    return self._send(200, {"ok": True, "id": _id})
                if path == "/inbox":
                    from urllib.parse import parse_qs as _pq3
                    _q3 = _pq3(self.path.split("?", 1)[1] if "?" in self.path else "")
                    _lim = int((_q3.get("limit") or ["50"])[0])
                    _hrs = float((_q3.get("hours") or ["0"])[0]) or None
                    _unk = (_q3.get("unkept") or ["0"])[0] in ("1", "true")
                    _full = (_q3.get("full") or ["0"])[0] in ("1", "true")
                    return self._send(200, {
                        "items": m.card.inbox_list(limit=_lim, hours=_hrs,
                                                   unkept_only=_unk, full=_full),
                        "stats": m.card.inbox_stats()})
                if path == "/inbox/pick":
                    try:
                        limit = int(body.get("limit") or 40)
                    except Exception:
                        limit = 40
                    try:
                        hours = float(body["hours"]) if body.get("hours") else None
                    except Exception:
                        hours = None
                    try:
                        with m.lock:
                            d = m.card.inbox_pick(limit=limit, hours=hours,
                                                  commit=bool(body.get("commit")),
                                                  min_score=body.get("min_score"))
                    except Exception as e:
                        return self._send(500, {"ok": False, "error": "pick_failed",
                                             "detail": str(e)})
                    if d.get("committed"):
                        m.tl.append("inbox",
                                    "收件层挑进短期 " + str(d.get("n_wrote", 0)) + " 条",
                                    allow_foreign=True)
                    return self._send(200, d)
                if path == "/inbox/mark":
                    _n = m.card.inbox_mark(body.get("ids") or [],
                                           kept=body.get("kept", 1),
                                           kept_mid=body.get("kept_mid"))
                    return self._send(200, {"ok": True, "n": _n})
                if path == "/forget":
                    # 三档删除语义（V5.3.3）。mode: logical / natural / purge
                    # 默认 logical —— 删错了还能捞回来，purge 才真清。
                    mid = body.get("id") or body.get("mid")
                    if not mid:
                        return self._fail(400, "no_id", "要给出记忆 id")
                    mode = (body.get("mode") or "logical").strip().lower()
                    reason = (body.get("reason") or "").strip()
                    out = None
                    with m.lock:
                        out = m.card.forget(mid, mode=mode, reason=reason)
                        if out.get("ok") and m.tl is not None:
                            try:
                                m.tl.append("forget", "%s id=%s %s"
                                            % (out.get("what", "删除"), mid,
                                               reason or ""))
                            except Exception as e:
                                out["timeline_error"] = str(e)
                    if not out.get("ok"):
                        code = 404 if out.get("error") == "not_found" else 400
                        return self._send(code, out)
                    return self._send(200, out)
                if path == "/mem/quarantine":
                    # 隔离区里都有谁（默认它们不进上下文）
                    with m.lock:
                        rows = m.card.quarantine_rows(limit=int(body.get("limit") or 50))
                    return self._send(200, {"ok": True, "count": len(rows),
                                            "rows": rows})
                if path == "/mem/release":
                    mid = body.get("id") or body.get("mid")
                    if not mid:
                        return self._fail(400, "no_id", "要给出记忆 id")
                    with m.lock:
                        out = m.card.release_quarantine(mid)
                        if out.get("ok") and m.tl is not None:
                            try:
                                m.tl.append("untrust", "人工解除隔离 id=%s" % mid)
                            except Exception:
                                pass
                    if not out.get("ok"):
                        return self._send(404, out)
                    return self._send(200, out)
                if path == "/dream/commit":
                    # 梦境压缩（写回）：外面提炼好的高阶记忆从这里进卡。
                    # items: [{text, region?, salience?, w0?}]
                    # dialogue_ids: 可选，标记哪些原文已被消化（不传=全部标记）
                    items = body.get("items") or []
                    if not isinstance(items, list) or not items:
                        return self._fail(400, "empty_items", "items 不能为空")
                    with m.lock:
                        out = m.card.dream_commit(
                            items, dialogue_ids=body.get("dialogue_ids"),
                            speaker=(body.get("speaker") or "self"),
                            key=body.get("key"), force=bool(body.get("force")))
                    # 幂等命中时写接口照样回 200（对调用方是「这批已经压好了」，
                    # 不是错误）；真正失败才 409+死信，让宿主知道要重试。
                    if out.get("ok"):
                        return self._send(200, {"ok": True, "dream": out})
                    return self._send(409, {"ok": False, "dream": out})
                if path == "/emb/remodel":
                    # 换过模型的卡里有「两代向量」：老的那批维度跟当前模型
                    # 对不上，检索时会被直接丢掉 —— 但覆盖率还是报 100%。
                    # 这条路由把它们用当前模型重编码一遍，让 usable_ratio 追上覆盖率。
                    # 只动 emb 字节，不动文本、权重、时间，可反复跑（幂等）。
                    if not m.card.sem_ready():
                        return self._fail(400, "semantic_off", "语义层不可用")
                    with m.lock:
                        before = m.card.sem_stats()
                        rs = []
                        for fn in (m.card.backfill_embeddings,
                                   m.card.backfill_detail_embeddings):
                            rs.append(fn(progress=False, remodel=True))
                        try:
                            m.card.db.execute("DELETE FROM core_vec")
                            m.card.db.commit()
                        except Exception:
                            pass
                        m.card._core_vec_cache.clear()
                        # 顺手把主干向量烤热：core_vec 一清空，下一次检索要现场
                        # 编码 500+ 条主干 —— 实测一条查询卡住 5 分钟（读路径上
                        # 干重活）。宁可在这儿（维护窗口）一次算完。
                        warmed = 0
                        try:
                            mids = [r[0] for r in m.card.db.execute(
                                "SELECT id FROM mem").fetchall()]
                            for mid in mids:
                                if m.card.core_vec_of(mid) is not None:
                                    warmed += 1
                        except Exception as e:
                            m.card.dim_note = "主干向量预热中断：%s" % e
                        after = m.card.sem_stats()
                    return self._send(200, {
                        "ok": True,
                        "mem": {"done": rs[0][0], "skip": rs[0][1],
                                "fail": rs[0][2]},
                        "detail": {"done": rs[1][0], "skip": rs[1][1],
                                   "fail": rs[1][2]},
                        "core_warm": warmed,
                        "before": {"emb_dims": before.get("emb_dims"),
                                   "usable_ratio": round(
                                       before.get("usable_ratio") or 0, 4)},
                        "after": {"emb_dims": after.get("emb_dims"),
                                  "usable_ratio": round(
                                      after.get("usable_ratio") or 0, 4),
                                  "warn": after.get("dim_warn")},
                    })
                if path == "/tick":
                    sec = float(body.get("seconds") or 0)
                    if sec <= 0:
                        return self._fail(400, "bad_seconds", "seconds 必须 > 0")
                    # 真卡保护：这条路由是给测试用的，但连的是**真卡**。
                    # 2026-10-31 14:31 我就是在这儿跑了 +30 天，
                    # 短期池 5 条被按超期规则结算掉，只能整库回滚。
                    # 所以：要么显式传 confirm=true，要么时间推进不超过 1 小时。
                    # 想验衰减请复制 card.db 到临时目录去跑，别在真卡上假造时间。
                    confirm = bool(body.get("confirm"))
                    if not confirm and sec > 3600:
                        return self._fail(
                            403, "real_card_guard",
                            "这条是给测试用的假时间路由，但连的是真卡："
                            "推进 %.1f 天会把短期记忆按超期规则结算掉。"
                            "验衰减请复制 card.db 到临时目录跑；"
                            "确实要在真卡上推进就传 confirm=true。"
                            % (sec / 86400.0))
                    with m.lock:
                        m.card.tick(sec)
                        m.last_tick_ts = time.time()
                    return self._send(200, {"ok": True, "advanced_s": sec,
                                            "confirmed": confirm})
                if path == "/seal":
                    vals = body.get("values") or {}
                    with m.lock:
                        ok, bad = m.card.identity.seal(vals)
                        m.sealed = bool(m.card.identity.is_sealed())
                    return self._send(200, {"ok": True, "sealed": m.sealed,
                                            "bad": bad})
                # ---- 技能记忆（V5.0 §7）----
                # 为什么要有这两个口：技能表以前是死的 —— 结构、公式、
                # SM2 全都写好了，但没有任何地方能往里写、也没有任何地方
                # 能报告「这次用上了」。现在补上最薄的一层：
                #   /skill_add  建一条技能（名字 + 描述 + 可选标签）
                #   /skill_use  记一次使用，按改良 SM2 结算熟练度
                # 不在这里做「自动从对话里抽技能」—— 抽错了没人能发现，
                # 不如先让调用方显式说「这次用上了哪个」。
                if path == "/skill_add":
                    name = (body.get("name") or "").strip()
                    desc = (body.get("desc") or "").strip()
                    if not name or not desc:
                        return self._fail(400, "bad_skill",
                                          "name 和 desc 都不能为空")
                    # 🔴 2026-10-31：vec 不能空着。
                    #    skill.search() 是按 bigram 余弦算的，vec 为 {} 的技能
                    #    永远相似度 0 —— 也就是说「建了但永远想不起来」，
                    #    等于白建。没显式给 vec 就从「名字 + 描述」现算一份。
                    svec = body.get("vec")
                    if not svec:
                        from card2 import to_vec as _to_vec
                        svec = _to_vec(name + " " + desc)
                    with m.lock:
                        sid = m.card.skill.add(
                            name, desc,
                            vec=svec,
                            l=float(body.get("l") or 0.6),
                            tag=(body.get("tag") or ""),
                            interval_days=float(body.get("interval_days") or 0.0),
                            is_attach=bool(body.get("is_attach")))
                    return self._send(200, {"ok": True, "skill_id": sid,
                                            "stats": m.card.skill.stats()})
                if path == "/skill_use":
                    name = (body.get("name") or "").strip()
                    if not name:
                        return self._fail(400, "bad_skill", "name 不能为空")
                    with m.lock:
                        m.flow_time()
                        res = m.card.skill.use(
                            name, q=body.get("q"),
                            user_text=(body.get("user_text") or ""),
                            source=(body.get("source") or "self"),
                            hormones=getattr(m.card, "h", None))
                    if res is None:
                        return self._fail(404, "skill_not_found",
                                          "没有这个技能：" + name)
                    return self._send(200, {"ok": True, "use": res})
                if path == "/config":
                    # V5.0 §10.9 热改：写一条运行时配置（场景权重等）。
                    # 为什么走 HTTP 而不是让人去 UPDATE 数据库：
                    #   直接改库有两条路能搞坏 —— 忘了 bump `_rev`（改了不生效），
                    #   或者写了个 6 个数对不上的值（那一格直接消失）。
                    #   走这里至少能校验一次、能看见 bad。
                    import ctxweight as _cw
                    key = (body.get("key") or "").strip()
                    # 🔧 2026-11-05：支持"恢复默认" —— 删掉这一条 runtime_config。
                    #   为什么需要：改坏了要能一键回去，而不是让人去 UPDATE 数据库。
                    if key and body.get("reset"):
                        with m.lock:
                            try:
                                m.card.db.execute(
                                    "DELETE FROM runtime_config WHERE key=?",
                                    (key,))
                                m.card.db.commit()
                                _cw.rc_set(m.card.db, _cw.RC_REV_KEY,
                                           "%.6f" % time.time(),
                                           ts=time.time())
                            except Exception as e:
                                return self._fail(
                                    400, "bad_config", "恢复默认失败：%s" % e)
                            cfg = _cw.load_config(m.card.db, force=True)
                        return self._send(200, {"ok": True, "key": key,
                                                "reset": True, "bad": cfg["bad"],
                                                "note": "已恢复成代码里的默认值"})
                    if not key:
                        return self._fail(400, "bad_config", "key 不能为空")
                    if key.startswith("scene_weight:"):
                        name = key.split(":", 1)[1].strip()
                        name = _cw.SCENE_ALIAS.get(name.lower(), name)
                        if name not in _cw.SCENE_WEIGHTS:
                            return self._fail(
                                400, "bad_config",
                                "没有这个场景：%s（可选：%s）" % (
                                    name, "、".join(_cw.SCENE_WEIGHTS.keys())))
                        val = body.get("value")
                        if isinstance(val, (list, tuple)):
                            val = ",".join("%g" % float(x) for x in val)
                        if _cw._parse_six(val) is None:
                            return self._fail(
                                400, "bad_config",
                                "权重必须是 6 个 0~1 的数，逗号分隔，例如 "
                                "0.18,0.16,0.26,0.10,0.24,0.06")
                        key = "scene_weight:" + name
                        body["value"] = val
                    # 三个标量：范围要在这儿挡一次（写进去再发现就晚了）
                    elif key == "BUDGET_AUTO":
                        # 自动跟随模型：只认 0/1
                        v = str(body.get("value") or "").strip().lower()
                        if v in ("1", "true", "on", "yes"):
                            body["value"] = "1"
                        elif v in ("0", "false", "off", "no", ""):
                            body["value"] = "0"
                        else:
                            return self._fail(
                                400, "bad_config",
                                "BUDGET_AUTO 只认 0 或 1（现在是 %s）" % v)
                    elif key in ("A_MIN", "CELL_MIN", "BUDGET"):
                        v = _cw._parse_num(body.get("value"))
                        rng = {"A_MIN": (0.0, 0.80),
                               "CELL_MIN": (0, 4000),
                               "BUDGET": (_cw.BUDGET_MIN, _cw.BUDGET_MAX)}[key]
                        if v is None or not (rng[0] <= v <= rng[1]):
                            return self._fail(
                                400, "bad_config",
                                "%s 必须是 %s ~ %s 之间的数" % (key, rng[0],
                                                               rng[1]))
                        body["value"] = ("%d" % v) if key != "A_MIN" else ("%g" % v)
                    else:
                        return self._fail(
                            400, "bad_config",
                            "不认识的配置项：%s（可选：scene_weight:<场景名> / "
                            "A_MIN / CELL_MIN / BUDGET / BUDGET_AUTO）" % key)
                    with m.lock:
                        rev = _cw.rc_set(m.card.db, key, body.get("value"),
                                         ts=time.time())
                        cfg = _cw.load_config(m.card.db, force=True)
                    return self._send(200, {"ok": True, "key": key,
                                            "rev": rev,
                                            "bad": cfg["bad"],
                                            "note": "下一轮 /context 立即生效，不用重启"})
                if path == "/ctx_preview":
                    # 控制台的实时预览：**只算不写**。
                    #   为什么不能拿 /context 来预览：那会走一次真 recall、
                    #   写 recall_event、还把 turn_count 推一格 ——
                    #   「在控制台上试一下参数」不该改变她记得的东西。
                    import ctxweight as _cw
                    q = (body.get("query") or "")
                    if len(q) > 500:
                        q = q[:500]
                    with m.lock:
                        try:
                            plan = _cw.preview(
                                q, db=m.card.db,
                                overrides=body.get("overrides") or None,
                                hormones=getattr(m.card, "h", None),
                                turns=getattr(m.card, "turn_count", None))
                        except Exception as e:
                            return self._fail(500, "preview_failed",
                                              "%s: %s" % (type(e).__name__, e))
                    return self._send(200, {"ok": True, "plan": plan})
                if path == "/voice_config":
                    # 语音那一栏的参数：存在 qq_state/voice/cfg.json，不在卡里。
                    # 因为 qq_say.py 在发送前就要算"该不该发语音"，那时候
                    # 卡可能被别的进程锁着 —— 语音参数不该跟着卡一起不可用。
                    try:
                        _voice_mod()
                    except Exception as e:
                        return self._fail(500, "voice_unavailable",
                                          "%s: %s" % (type(e).__name__, e))
                    try:
                        if body is not None and isinstance(body, dict):
                            patch = body.get("patch")
                            if patch is None:
                                patch = {k: v for k, v in body.items()
                                         if k not in ("reset",)}
                            return self._send(200, _voice_payload(
                                patch=patch, reset=bool(body.get("reset"))))
                        return self._send(200, _voice_payload())
                    except Exception as e:
                        return self._fail(400, "bad_voice_cfg",
                                          "%s: %s" % (type(e).__name__, e))
                if path == "/voice_say":
                    # 试听：真合成一条，返回落在磁盘上的路径。
                    # 不发送、不写卡 —— 只是让我先听听像不像人。
                    try:
                        return self._send(200, _voice_send(body.get("text")))
                    except ValueError as e:
                        return self._fail(400, "empty_text", str(e))
                    except Exception as e:
                        return self._fail(502, "synth_failed",
                                          "%s: %s" % (type(e).__name__, e))
                if path == "/config_all":
                    # 一次保存整页（七个场景 + 三个标量）。少发十个请求，
                    # 也少十次「一半成功一半失败」的机会。
                    import ctxweight as _cw
                    items = body.get("items") or {}
                    if not isinstance(items, dict) or not items:
                        return self._fail(400, "bad_config", "items 不能为空")
                    norm = {}
                    for k, val in items.items():
                        k = str(k).strip()
                        if k.startswith("scene_weight:"):
                            name = k.split(":", 1)[1].strip()
                            name = _cw.SCENE_ALIAS.get(name.lower(), name)
                            if name not in _cw.SCENE_WEIGHTS:
                                return self._fail(400, "bad_config",
                                                  "没有这个场景：%s" % name)
                            if isinstance(val, (list, tuple)):
                                val = ",".join("%g" % float(x) for x in val)
                            if _cw._parse_six(val) is None:
                                return self._fail(
                                    400, "bad_config",
                                    "场景「%s」的权重必须是 6 个 0~1 的数" % name)
                            norm["scene_weight:" + name] = val
                        elif k in ("A_MIN", "CELL_MIN", "BUDGET"):
                            v = _cw._parse_num(val)
                            rng = {"A_MIN": (0.0, 0.80),
                                   "CELL_MIN": (0, 4000),
                                   "BUDGET": (_cw.BUDGET_MIN,
                                              _cw.BUDGET_MAX)}[k]
                            if v is None or not (rng[0] <= v <= rng[1]):
                                return self._fail(
                                    400, "bad_config",
                                    "%s 必须是 %s ~ %s" % (k, rng[0], rng[1]))
                            norm[k] = ("%g" % v) if k == "A_MIN" else ("%d" % v)
                        elif k == "BUDGET_AUTO":
                            v = str(val or "").strip().lower()
                            if v in ("1", "true", "on", "yes"):
                                norm[k] = "1"
                            elif v in ("0", "false", "off", "no", ""):
                                norm[k] = "0"
                            else:
                                return self._fail(
                                    400, "bad_config",
                                    "BUDGET_AUTO 只认 0 或 1（现在是 %s）" % v)
                        else:
                            return self._fail(400, "bad_config",
                                              "不认识的配置项：%s" % k)
                    with m.lock:
                        for k, v in norm.items():
                            _cw.rc_set(m.card.db, k, v, ts=time.time())
                        cfg = _cw.load_config(m.card.db, force=True)
                    return self._send(200, {"ok": True, "n": len(norm),
                                            "keys": sorted(norm.keys()),
                                            "bad": cfg["bad"],
                                            "note": "已保存，下一轮 /context 立即生效"})
                if path == "/identity":
                    # 改名。只允许改名字 —— 其余五条身份锚不给动，
                    # 那是「换一个人」而不是「改个称呼」。
                    new_name = (body.get("name") or "").strip()
                    if not new_name:
                        return self._fail(400, "bad_name", "名字不能为空")
                    with m.lock:
                        ok, msg = m.card.identity.set_name(new_name)
                    if not ok:
                        return self._fail(400, "rename_failed", msg)
                    return self._send(200, {"ok": True, "name": new_name,
                                            "msg": msg})
                if path == "/avatar":
                    # 换头像 / 恢复默认。前端已经把图缩成 512×512 PNG 再传，
                    # 所以这里只做最基础的校验（是不是 PNG、多大）。
                    import base64 as _b64
                    with m.lock:
                        if body.get("clear"):
                            ok, msg = m.card.identity.clear_avatar()
                        else:
                            data = body.get("png") or ""
                            if not data:
                                return self._fail(400, "bad_avatar",
                                                  "没收到图片数据")
                            if isinstance(data, str) and data.startswith("data:"):
                                data = data.split(",", 1)[-1]
                            try:
                                raw = _b64.b64decode(data, validate=False)
                            except Exception as e:
                                return self._fail(400, "bad_avatar",
                                                  "base64 解不开：%s" % e)
                            ok, msg = m.card.identity.set_avatar(raw)
                    if not ok:
                        return self._fail(400, "avatar_failed", msg)
                    return self._send(200, {"ok": True, "msg": msg})
                if path == "/cardslot":
                    # 插卡 / 拔卡。必须显式给 action（inject|unload），
                    # 不猜 —— 这两个动作会动 DSH 家目录里的 AGENTS.md，
                    # 猜错一次就是「人设没了」。dry=true 只预览不落盘。
                    import cardslot as _slot
                    act = (body.get("action") or "").strip()
                    dry = bool(body.get("dry"))
                    if act == "inject":
                        res = _slot.inject(dry=dry)
                    elif act == "unload":
                        res = _slot.unload(dry=dry)
                    elif act == "status":
                        res = {"ok": True, "steps": [],
                               "state": _slot.status()}
                    else:
                        return self._fail(400, "bad_action",
                                          "action 只能是 inject / unload / status")
                    res["ok"] = bool(res.get("ok"))
                    return self._send(200, res)
                if path == "/shutdown":
                    self._send(200, {"ok": True, "bye": True})
                    threading.Thread(target=_shutdown_later, daemon=True).start()
                    return

            return self._fail(404, "not_found", "%s %s" % (method, path))
        except ValueError as e:
            return self._fail(400, "bad_request", str(e))
        except Exception as e:
            tb = traceback.format_exc(limit=3)
            m.errors += 1
            return self._send(500, {
                "ok": False, "error": "internal_error",
                "detail": "%s: %s" % (type(e).__name__, e),
                "trace": tb})

    def do_GET(self):
        self._timed("GET")

    def do_POST(self):
        self._timed("POST")

    def _timed(self, method):
        """跑一个请求，慢的叫一声。

        为什么要有这个：2026-11-07 07:00 前后出过一次「端口在听、连接被拒」——
        ThreadingHTTPServer 在 backlog 被塞满时，Windows 对新的 connect 直接回
        WSAECONNREFUSED，看起来像「服务死了」，其实是「慢请求堆满了队列」。
        当时没有计时，只能猜。现在超过 5 秒就写一行日志，下次有证据。
        """
        t0 = time.time()
        path = getattr(self, "path", "?").split("?")[0].rstrip("/") or "/"
        # V7 第 16 件：每次请求一个短 id + 路径，错误回包/慢日志/最近错误三处对得上
        self.rid = "%08x" % random.getrandbits(32)
        self.req_t0 = t0
        self.last_path = "%s %s" % (method, path)
        self.req_ms = None
        # 读写分离第一步（2026-11-11）：只记录「等锁 / 持锁 / embed / consolidate」，
        # 不改任何行为 —— 先量出来，再决定读路径怎么去锁。
        if perf is not None:
            try:
                perf.begin(path)
                _pc = perf.cur()
                if _pc is not None:
                    _pc["rid"] = self.rid
            except Exception:
                # 观测自己坏了也不能影响请求：这里不留空吞，落个标记位给 /perf 看
                self._perf_broke = True
        try:
            self._route(method)
        finally:
            rec = None
            if perf is not None:
                try:
                    rec = perf.finish()
                except Exception:
                    rec = None
            dt = time.time() - t0
            self.req_ms = dt * 1000.0
            # 记进环形缓冲：/health 的 p50/p95 就从这儿算（V7 第 14 件补齐）。
            # 只留 (分组, 秒)，不留请求体 —— 这里不能碰任何业务数据。
            _lat_add(path, dt)
            if dt >= 5.0:
                try:
                    _extra = ""
                    if rec is not None:
                        try:
                            _extra = " | " + perf.one(rec)
                        except Exception:
                            _extra = ""
                    sys.stderr.write("[mind] 慢请求 %.1fs %s %s rid=%s%s\n"
                                     % (dt, method, getattr(self, "path", "?"),
                                        self.rid, _extra))
                    sys.stderr.flush()
                except Exception:
                    pass


def _shutdown_later():
    time.sleep(0.3)
    os._exit(0)


class ExclusiveServer(ThreadingHTTPServer):
    # Windows 上 SO_REUSEADDR 不是「复用」，而是「允许别人抢占同一个端口」：
    # 两个进程都能 bind 成功，请求落到谁手里看运气。于是旧代码的服务
    # 可以一直活着把新进程的请求全接走 —— 改完代码像没改一样，
    # 2026-11-01 就踩了这个坑（三个 mind_server 同时「在跑」，
    # /health 少的那个 semantic 段怎么也刷不出来）。
    # 关掉它：第二个实例 bind 直接报错退出，一眼看出端口被占。
    allow_reuse_address = (os.name != "nt")

    # 排队上限。默认只有 5 —— 慢请求一多，Windows 对新连接直接回
    # WSAECONNREFUSED，表现成「端口在听但连不上」。加到 128 只是把
    # 「一慢就拒」推迟成「一慢就变慢」，真正的证据靠 Handler._timed。
    request_queue_size = 128


def _start_inbox_picker(mind):
    """收件层定时挑选：把值得记的原文从收件层搬进短期池。

    用户 2026-11-08 的规矩：收件层只装，判断价值是**工序**。这线程就是那道工序。
    默认 30 分钟一次；`WHALE_INBOX_PICK_SEC=0` 关掉。
    """
    try:
        sec = float(os.environ.get("WHALE_INBOX_PICK_SEC", "1800") or 0)
    except Exception:
        sec = 1800.0
    if sec <= 0:
        return None
    try:
        limit = int(os.environ.get("WHALE_INBOX_PICK_N", "20") or 20)
    except Exception:
        limit = 20

    def loop():
        while True:
            time.sleep(sec)
            try:
                with mind.lock:
                    d = mind.card.inbox_pick(limit=limit, commit=True)
                n = int((d or {}).get("n_wrote") or 0)
                if n:
                    print("[mind] 收件层挑进短期 %d 条" % n)
                    try:
                        mind.tl.append("inbox",
                                       "收件层挑进短期 %d 条" % n,
                                       allow_foreign=True)
                    except Exception:
                        pass
            except Exception as e:
                print("[mind] 收件层挑选失败: %s" % e)

    t = threading.Thread(target=loop, name="inbox-picker", daemon=True)
    t.start()
    print("[mind] 收件层挑选：每 %.0f 秒一次（每次最多 %d 条）" % (sec, limit))
    return t


_LAT = {"buf": None, "n": 0, "slow": 0}


def _lat_add(path, dt):
    """把一次请求的耗时塞进环形缓冲（最多 400 条，够算 p95）。"""
    try:
        import collections
        if _LAT["buf"] is None:
            _LAT["buf"] = collections.deque(maxlen=400)
        grp = ("context" if path in ("/context", "/ctx_preview")
               else "report" if path == "/report"
               else "recall" if path in ("/recall", "/recent")
               else "other")
        _LAT["buf"].append((grp, float(dt)))
        _LAT["n"] += 1
        if dt >= 5.0:
            _LAT["slow"] += 1
    except Exception:
        pass


def _lat_stats():
    """按分组的 p50/p95/max。样本不够就老实说 n 小，不给假精度。"""
    buf = _LAT.get("buf")
    out = {"n_total": _LAT.get("n", 0), "slow_n": _LAT.get("slow", 0), "groups": {}}
    if not buf:
        return out
    import collections
    by = collections.defaultdict(list)
    for grp, dt in buf:
        by[grp].append(dt)
    for grp, arr in by.items():
        arr.sort()
        n = len(arr)

        def _p(q):
            if not n:
                return None
            k = min(n - 1, max(0, int(round(q * (n - 1)))))
            return round(arr[k], 3)
        out["groups"][grp] = {"n": n, "p50": _p(0.50), "p95": _p(0.95),
                              "max": round(arr[-1], 3)}
    return out


def _db_files(mind):
    """DB 三个文件的体积（MB）。WAL 长期不 checkpoint 会一直涨。"""
    out = {}
    base = os.path.abspath(mind.db_path)
    for suffix, key in (("", "db_mb"), ("-wal", "wal_mb"), ("-shm", "shm_mb")):
        p = base + suffix
        try:
            out[key] = round(os.path.getsize(p) / 1048576.0, 2) if os.path.exists(p) else 0.0
        except OSError:
            out[key] = None
    return out


def _bridge_tools():
    """桥插件注册了几个工具（读插件源码里的 defineTool 计数）。

    为什么查这个：桥装过三次才生效，全靠人工看日志；工具数是最便宜的
    「它到底挂上没有」的外部证据。
    """
    cands = []
    try:
        home = os.path.expanduser("~")
        cands.append(os.path.join(home, ".dsh", "profiles", "desktop",
                                  "node_modules", "dsh-whale-bridge", "lib", "index.js"))
        here = os.path.dirname(os.path.abspath(__file__))
        cands.append(os.path.join(here, "..", "deploy", "dsh-whale-bridge",
                                  "lib", "index.js"))
    except Exception:
        pass
    for p in cands:
        try:
            if os.path.exists(p):
                src = io.open(p, encoding="utf-8", errors="ignore").read()
                # 插件是「一个循环 + 工具表」的写法，defineTool( 只出现一次；
                # 真正的工具数看表里 name: 'mind_xxx' 的条目。
                import re as _re
                names = _re.findall(r"name:\s*['\"](mind_[a-z0-9_]+)['\"]", src)
                return {"path": p, "tools": len(set(names)),
                        "names": sorted(set(names))[:24]}
        except Exception:
            continue
    return {"path": None, "tools": None, "names": []}


def _qq_guard():
    """QQ 守夜人还活着吗（单例）。

    只看两样盘上的证据：pid 文件里的进程在不在、心跳文件多久没动。
    绝不发请求、不 restart 它 —— 这层只负责「看得出来」。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    st = os.path.join(here, "qq_state")
    pid_p = os.path.join(st, "guard.pid")
    hb_p = os.path.join(st, "guard.heartbeat")
    out = {"pid": None, "alive": None, "heartbeat_h": None}
    try:
        if os.path.exists(pid_p):
            txt = io.open(pid_p, encoding="utf-8", errors="ignore").read().strip()
            out["pid"] = int(txt) if txt.isdigit() else None
    except Exception:
        pass
    try:
        if os.path.exists(hb_p):
            out["heartbeat_h"] = round((time.time() - os.path.getmtime(hb_p)) / 3600.0, 2)
    except Exception:
        pass
    if out["pid"]:
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            h = k32.OpenProcess(0x1000, False, int(out["pid"]))
            if h:
                code = ctypes.c_ulong()
                k32.GetExitCodeProcess(h, ctypes.byref(code))
                k32.CloseHandle(h)
                out["alive"] = (code.value == 259)   # STILL_ACTIVE
            else:
                out["alive"] = False
        except Exception:
            out["alive"] = None
    return out


def _reconcile(mind):
    """日结对账：每条链路「今天有没有真的在动」。

    为什么需要它（2026-11-10，四份外部评审的共同第一条）：
    11 个 bug 里 9 个是「看着在跑、其实没跑」——原文表静默停更两周、
    习惯表永远空、印象卡永远空白、桥没进 bundles。这些没有一个会崩，
    也不会让 /health 变红，所以没人发现。
    /health 原来只回答「进程活着吗」；这个函数回答「它还活着吗」。

    口径：所有判断只用「时间差」和「行数」，不猜业务语义。
      · 原文表：今天有没有新增；最后一条多久以前
      · 习惯：有没有行；最后一次更新多久以前（挖矿是每天一次）
      · 印象：有没有行；最后一次触发多久以前（淡出是每小时）
      · 冷档：有多少行、今天新增多少（有遗忘才该涨）
      · 维护线程：上一次心跳多久以前（预期 1 个周期）
    红的条件是「明显不对」，不是「今天没聊天」——夜里没消息不是故障。
    """
    import sqlite3 as _s
    now = time.time()
    out = {"checks": {}, "red": [], "warn": []}

    def _one(con, sql):
        try:
            return con.execute(sql).fetchone()[0]
        except Exception:
            return None

    try:
        con = _s.connect(mind.db_path, timeout=8)
        try:
            day0 = now - 86400.0
            d_today = _one(con, "SELECT COUNT(*) FROM dialogue WHERE ts >= %f" % day0)
            d_last = _one(con, "SELECT MAX(ts) FROM dialogue")
            h_n = _one(con, "SELECT COUNT(*) FROM habits")
            h_last = _one(con, "SELECT MAX(last_seen) FROM habits")
            i_n = _one(con, "SELECT COUNT(*) FROM impression")
            i_last = _one(con, "SELECT MAX(last_ts) FROM impression")
            a_n = _one(con, "SELECT COUNT(*) FROM mem_archive")
            a_today = _one(con, "SELECT COUNT(*) FROM mem_archive WHERE archived_ts >= %f" % day0)
            w_n = _one(con, "SELECT COUNT(*) FROM worklog")
            w_last = _one(con, "SELECT MAX(ts) FROM worklog")
            # 出生证缺口：记忆在库里、账本没开证。正常时恒 0（启动和
            # 维护线程都会补）；一旦不是 0，说明「业务写了、事件没记」又发生了。
            try:
                hb_gap = _one(con, "SELECT COUNT(*) FROM habits x"
                                   " LEFT JOIN event_birth eb ON eb.kind='habit'"
                                   " AND eb.ref=CAST(x.id AS TEXT)"
                                   " WHERE eb.ref IS NULL")
            except Exception:
                hb_gap = None
            try:
                ib_gap = _one(con, "SELECT COUNT(*) FROM impression x"
                                   " LEFT JOIN event_birth eb ON eb.kind='impression'"
                                   " AND eb.ref=CAST(x.id AS TEXT)"
                                   " WHERE eb.ref IS NULL")
            except Exception:
                ib_gap = None
            try:
                b_gap = _one(con, "SELECT COUNT(*) FROM mem m"
                                  " LEFT JOIN mem_birth b ON b.mid=m.id"
                                  " WHERE b.mid IS NULL AND NOT EXISTS"
                                  " (SELECT 1 FROM mem_tomb t WHERE t.mid=m.id)")
            except Exception:
                b_gap = None
        finally:
            con.close()
    except Exception as e:
        out["red"].append("db_unreadable:%s" % e)
        return out

    def _age(ts):
        try:
            return None if not ts else round((now - float(ts)) / 3600.0, 1)
        except Exception:
            return None

    c = out["checks"]
    c["dialogue_today"] = d_today
    c["dialogue_last_h"] = _age(d_last)
    c["habits_n"] = h_n
    c["habits_last_h"] = _age(h_last)
    c["impression_n"] = i_n
    c["impression_last_h"] = _age(i_last)
    c["archive_n"] = a_n
    c["archive_today"] = a_today
    c["birth_gap"] = b_gap
    c["habit_gap"] = hb_gap
    c["impression_gap"] = ib_gap
    c["worklog_n"] = w_n
    c["worklog_last_h"] = _age(w_last)
    c["maint_last_h"] = _age(getattr(mind, "_maint_last", 0))

    # V7 第 14 件补齐：延迟分位、WAL/DB 体积、桥工具数、QQ 守夜人、写队列现状。
    # 这些都不是业务语义，是「这台机器现在什么状态」——出问题时最省事的证据。
    try:
        c["latency"] = _lat_stats()
        c["boot"] = getattr(mind, "_boot", None)
    except Exception:
        c["latency"] = {}
    try:
        c["db_files"] = _db_files(mind)
    except Exception:
        c["db_files"] = {}
    try:
        c["bridge"] = _bridge_tools()
    except Exception:
        c["bridge"] = {}
    try:
        c["qq_guard"] = _qq_guard()
    except Exception:
        c["qq_guard"] = {}
    try:
        c["queue"] = mind.wq_snapshot()
    except Exception as e:
        c["queue"] = {"error": str(e)}
    # V7 第 18 件：版本矩阵（精简：只放关键三项 + 不匹配告警）
    try:
        import versions as _ver
        _vm = _ver.collect(db_path=getattr(mind, "db_path", None),
                           mind_version=VERSION)
        c["versions"] = {"mind": _vm.get("mind"),
                         "schema_code": (_vm.get("schema") or {}).get("code"),
                         "schema_db": (_vm.get("schema") or {}).get("db"),
                         "bridge": ((_vm.get("bridge") or {}).get("profile") or {}).get("version"),
                         "mismatch": _vm.get("mismatch") or []}
        for _mm in (_vm.get("mismatch") or []):
            out["warn"].append("version:%s" % _mm.split("（")[0])
    except Exception as e:
        c["versions"] = {"error": str(e)[:120]}
    # V7 第 19 件：向量模型体检 —— 换模型后「卡里混了几代向量」必须能看见。
    try:
        _models = mind.card.emb_models()
        _cur = {"model": (mind.card._emb_model_name() or ""),
                "dim": int(mind.card.model_dim or 0),
                "adapter": int(getattr(card2, "EMB_ADAPTER_VERSION", 0))}
        c["emb"] = {"current": _cur, "models": _models,
                    "mixed": len([x for x in _models if x["model"] != _cur["model"]])}
        # legacy-dim<N> 只是回填占位：跟当前模型同维度就不算「另一代」。
        _old = [x["model"] for x in _models if x["model"] != _cur["model"]
                and not (str(x["model"]).startswith("legacy-dim")
                         and int(x["dim"] or 0) == int(_cur["dim"] or 0))]
        if _old:
            out["warn"].append("emb_old_models:%s" % ",".join(_old[:3]))
        # 维度对不上只是「混代」，不是「坏了」：换模型之后卡里必然有一批旧维度
        # 的向量，召回会给它们 0 分并静默降级——这是设计里允许的状态，报红会
        # 让 /health 永远红着，人就学会无视红灯了。真坏了另有 recall 层面的信号。
        _dw = getattr(mind.card, "dim_warn", None)
        if _cur["dim"] and _dw:
            c["emb"]["dim_warn"] = str(_dw)[:160]
            out["warn"].append("emb_dim_mixed:%d/%d" % (int(_cur["dim"]), 0))
    except Exception as e:
        c["emb"] = {"error": str(e)[:120]}

    # V7 第 17 件：鉴权与来源可信度现状（让「到底有没有在拦」看得见）
    try:
        _tp = _token_path(mind)
        c["auth"] = {
            "require_token": os.environ.get("WHALE_REQUIRE_TOKEN") == "1",
            "token_file": _tp,
            "token_exists": os.path.exists(_tp),
            "trust_sources": sorted(TRUST_SOURCES),
            "downgraded_src": _RATE_HITS.get("downgraded", 0),
            "rate_limited": _RATE_HITS.get("limited", 0),
        }
    except Exception as e:
        c["auth"] = {"error": str(e)}
    # V7 第 6 件：静默失败登记表（card2.soft_fail 的计数）
    try:
        import card2 as _c2
        c["soft_fail"] = _c2.soft_fail_snapshot()
    except Exception as e:
        c["soft_fail"] = {"error": str(e)}
    # V7 第 5 件：签名检查点现状（有几份、最近一份多久前、签名验得过吗）
    try:
        import checkpoint as _ck2
        _sum = _ck2.summary(mind.db_path, mirror=_ckpt_mirror())
        _sum["dirs"] = (_sum.get("dirs") or [])[:2]
        c["checkpoints"] = _sum
    except Exception as e:
        c["checkpoints"] = {"ok": False, "error": str(e)}
    # V7 第 1 件：备份完整性（最近一份多久前 / 几份 / 每份 sha256 与同源 / 最老几天）
    # 只读：不移动、不删除任何备份；哈希与链头按 (size, mtime) 缓存 600 秒。
    try:
        import backup_verify as _bv
        _bk = _bv.inspect_backups(mind.db_path)
        _bk["items"] = _bk.get("items") or []   # 全带：份数不多，要求每份都能看到 sha256/同源
        c["backup"] = _bk
    except Exception as e:
        c["backup"] = {"ok": False, "pass": False, "error": str(e)[:160]}

    # 红：原文表停了（有过 /remember 却一天没进东西，不是"今天没聊"）
    if d_last and (now - float(d_last)) > 36 * 3600:
        out["red"].append("dialogue_stale:%.1fh" % ((now - float(d_last)) / 3600.0))
    if h_n == 0:
        out["warn"].append("habits_empty")
    if i_n == 0:
        out["warn"].append("impression_empty")
    if w_n and w_last and (now - float(w_last)) > 48 * 3600:
        out["warn"].append("worklog_stale:%.1fh" % ((now - float(w_last)) / 3600.0))
    ml = getattr(mind, "_maint_last", 0) or 0
    if ml <= 0:
        out["warn"].append("maintenance_never_ran")
    elif (now - ml) > 3 * 3600:
        out["red"].append("maintenance_stale:%.1fh" % ((now - ml) / 3600.0))
    # 出生证缺口：正常情况下启动补一次、维护线程每跳再补，恒为 0。
    # 不为 0 说明「记忆进库了、时间线没记」又发生了 —— 这正是第 4 件要盯的东西。
    if b_gap:
        out["red"].append("birth_gap:%s" % b_gap)
    # V7 第 4 件推广：习惯/印象也会「行进了库、时间线没记」。同样可修复，>0 即红。
    if hb_gap:
        out["red"].append("habit_gap:%s" % hb_gap)
    if ib_gap:
        out["red"].append("impression_gap:%s" % ib_gap)
    # 锚被改过（签名对不上）比「回滚」更坏：回滚只是旧，伪造是骗。
    _av = (getattr(mind, "anchor", None) or {}).get("verdict") or ""
    out["checks"]["anchor"] = _av or "unknown"
    if _av in ("anchor_tampered", "replaced", "rollback"):
        out["red"].append("anchor_%s" % _av)
    elif _av in ("", "error", "no_card"):
        out["warn"].append("anchor_%s" % (_av or "unknown"))
    # 长期池容量警戒（V7 第 10 件遗留）：长期池是自己写不进去的「保险柜」，
    # 它涨到 400 条说明中期池的出口在放水、不是记忆变多了；500 条以后
    # 每次全量扫都会更慢，所以在这儿提前叫，而不是等它撑到卡顿。
    try:
        import sqlite3 as _sq
        _con = _sq.connect("file:%s?mode=ro" % mind.card.db_path.replace("\\", "/"),
                           uri=True, timeout=10)
        try:
            _lc_n = int(_con.execute(
                "SELECT COUNT(*) FROM mem WHERE pool='long'").fetchone()[0] or 0)
        finally:
            _con.close()
        out["checks"]["long_pool"] = _lc_n
        if _lc_n >= 500:
            out["red"].append("long_pool_full:%d" % _lc_n)
        elif _lc_n >= 400:
            out["warn"].append("long_pool_high:%d" % _lc_n)
    except Exception as _e:
        out["checks"]["long_pool"] = -1
        out["warn"].append("long_pool_unknown:%s" % type(_e).__name__)
    # 检查点：签名验不过 / 卡比检查点还旧 / 压根没有，都算坏
    _ck = c.get("checkpoints") or {}
    if _ck and _ck.get("ok") is False and _ck.get("checked", 0) >= 0:
        if _ck.get("error"):
            out["warn"].append("checkpoint_error")
        elif _ck.get("checked") == 0:
            out["warn"].append("checkpoint_none")
        else:
            out["red"].append("checkpoint_bad:%s" % ((_ck.get("problems") or ["?"])[0])[:40])
    _ckage = _ck.get("age_h")
    if _ckage is not None and _ckage > 6:
        out["warn"].append("checkpoint_stale:%.1fh" % _ckage)
    # 备份：没有/太旧/不同源/读不出 → 红；最新一份超过 36 小时、异地不足两份 → 黄
    _bk = c.get("backup") or {}
    if _bk.get("error"):
        out["warn"].append("backup_error")
    elif _bk:
        if not _bk.get("ok"):
            out["red"].append("backup_bad:%s"
                              % ((_bk.get("red") or ["?"])[0])[:48])
        for _bw in (_bk.get("warn") or [])[:2]:
            out["warn"].append(_bw)
    # 静默失败：24 小时内有任何一种写过计数，就亮黄灯（不是红 —— 这些地方
    # 大多设计成「失败也不该拖垮服务」，但必须让人看得见）
    _sf = c.get("soft_fail") or {}
    if isinstance(_sf, dict):
        _hot = [k for k, v in _sf.items() if isinstance(v, dict) and v.get("n")
                and float(v.get("last_h") or 99) < 24]
        if _hot:
            out["warn"].append("soft_fail:%s" % ",".join(sorted(_hot)[:4]))

    # 黄灯：这些「不算坏，但值得看一眼」
    try:
        wal = (c.get("db_files") or {}).get("wal_mb")
        if wal is not None and wal > 256:
            out["warn"].append("wal_large:%.0fMB" % wal)
        _cg = ((c.get("latency") or {}).get("groups") or {}).get("context") or {}
        p95 = _cg.get("p95")
        # 刚重启第一条 /context 必然是冷的（要加载语义模型），那种慢不算故障；
        # 所以样本不足 5 条不报，避免每次重启都亮一盏假黄灯。
        if p95 is not None and _cg.get("n", 0) >= 5 and p95 > 5.0:
            out["warn"].append("context_p95_slow:%.1fs" % p95)
        bt = (c.get("bridge") or {}).get("tools")
        if bt is None:
            out["warn"].append("bridge_not_found")
        elif bt <= 0:
            out["warn"].append("bridge_tools_zero")
        qg = c.get("qq_guard") or {}
        if qg.get("pid") and qg.get("alive") is False:
            hb = qg.get("heartbeat_h")
            if hb is None or hb > 0.5:
                out["warn"].append("qq_guard_dead:pid%s" % qg.get("pid"))
    except Exception:
        pass

    # 后台线程悄悄死掉（2026-11-11 稳定性）：服务照样回话，世界却不再往前走。
    # 只看「本来起过的线程」——None 表示这个功能被显式关掉，不算坏。
    try:
        _th = {"world_tick": getattr(mind, "_world_tick_thread", None),
               "write_queue": getattr(mind, "_wq_thread", None),
               "maintenance": getattr(mind, "_maint_thread", None)}
        out["checks"]["threads"] = {k: (None if v is None else bool(v.is_alive()))
                                    for k, v in _th.items()}
        for k, v in _th.items():
            if v is not None and not v.is_alive():
                out["red"].append("%s_dead" % k)
    except Exception:
        out["checks"]["threads"] = None
    out["ok"] = not out["red"]
    return out


def _drop_lease(card_dir, lease):
    """交还写者租约（幂等；只删自己那张）。退出路径上不许抛。

    🔴 只读进程绝不能走这里：它 ledger 里的 holder 是**别人**的 id，
    照单 release 会把正在写的那个进程的租约删掉 —— 那正是本 bug 的加强版。
    """
    try:
        if not (lease or {}).get("ok"):
            return
        import disks as _dk
        holder = dict((lease or {}).get("holder") or {})
        hid = str(holder.get("holder_id") or (lease or {}).get("holder_id") or "")
        if hid:
            try:
                mind.card.lease_db_release(hid)
            except Exception:
                pass
            _dk.release_lease(card_dir, hid)
    except Exception as e:
        print("[mind] 交还租约失败（不影响退出）: %s" % e)


def _start_lease_keeper(mind):
    """写者租约心跳线程（V7 P0-4）。

    为什么不塞进 _start_maintenance：那是 600s 一跳，而租约 TTL 是 90s ——
    等它跳第二下，租约早就过期、别人就能抢走了。这里周期取 ttl/3，
    3 次丢心跳才真过期。
    """
    if getattr(mind, "readonly", False):
        return None
    hid = str(getattr(mind, "lease_holder_id", "") or "")
    if not hid:
        return None
    try:
        import disks as _dk
        ttl = float(os.environ.get("WHALE_LEASE_TTL", "") or _dk.LEASE_TTL)
    except Exception:
        ttl = None
    if not ttl or ttl <= 0:
        return None
    period = max(3.0, min(ttl / 3.0, 30.0))
    card_dir = os.path.dirname(os.path.abspath(mind.db_path))

    def loop():
        import disks as _dk
        fails = 0
        while True:
            time.sleep(period)
            try:
                r = _dk.heartbeat_lease(card_dir, hid, ttl=ttl)
            except Exception as e:
                r = {"ok": False, "reason": "error:%s" % e}
            try:
                mind.card.lease_db_touch(hid, ttl=ttl)
            except Exception:
                pass
            if r.get("ok"):
                fails = 0
                continue
            fails += 1
            # 丢一次不吵；每次数量级才说一句，免得日志被刷满。
            if fails in (1, 10, 100):
                print("[mind] 租约心跳失败 x%d: %s（租约 %ss 后会过期）" % (
                    fails, r.get("reason"), ttl))

    t = threading.Thread(target=loop, name="lease-keeper", daemon=True)
    t.start()
    return t


def _thread_lines(mind):
    """后台线程还活着吗（2026-11-11 稳定性）。

    后台线程悄悄死掉是最隐蔽的坏法：服务照样回话，只是世界不再往前走、
    写队列不再落地。所以把它们的状态摊在 /health 上，一眼能看出是「活着」
    还是「线程没了」——None 表示这个线程本来就没起（比如世界推进被显式关掉）。
    """
    out = {}
    for name, attr in (("world_tick", "_world_tick_thread"),
                       ("write_queue", "_wq_thread"),
                       ("maint", "_maint_thread")):
        th = getattr(mind, attr, None)
        out[name] = None if th is None else bool(th.is_alive())
    # 启动自检是一次性的，跑完线程当然就没了 —— 不能说它「死了」。
    # 这里报三种状态：done（已出结论）/ running（还在查）/ None（没起）。
    th = getattr(mind, "_boot_check_thread", None)
    if getattr(mind, "_boot", None) is not None:
        out["boot_check"] = "done"
    elif th is not None and th.is_alive():
        out["boot_check"] = "running"
    else:
        out["boot_check"] = None
    return out


def _wq_lines(mind):
    """写队列的现状：排了多深、成/败/拒了多少、上一笔花了多久。"""
    st = dict(getattr(mind, "_wq_stats", {}) or {})
    try:
        st["depth"] = int(mind._wq.qsize())
        st["maxsize"] = int(mind._wq.maxsize)
    except Exception:
        st["depth"] = None
        st["maxsize"] = None
    return st


def _node_info(mind):
    """三元分离 §5：identity_id 是谁、instance_id 是哪张卡、device_id 是哪台机器。

    以前只有前两个。同一份卡被拷到另一台机器上时，读的人看不出「这不是原来
    那台机器」—— 现在锚和 /health 里都有 device_id，能对得上。
    """
    info = {"device_id": None, "device_source": None, "anchor_device": None,
            "anchor_version": None, "instance_id": None, "same_device": None}
    try:
        import device as _dev
        info["device_id"] = _dev.device_id()
        _d = _dev._load() or {}
        info["device_source"] = _d.get("source")
    except Exception as e:
        info["device_err"] = str(e)
    try:
        import anchor as _an
        info["anchor_version"] = getattr(_an, "ANCHOR_VERSION", None)
        _p = _an._paths(getattr(mind, "db_path", None))[0]
        if _p and os.path.exists(_p):
            aj = json.loads(open(_p, encoding="utf-8").read())
            info["anchor_device"] = aj.get("device_id")
            if info["device_id"] and aj.get("device_id"):
                info["same_device"] = (aj.get("device_id") == info["device_id"])
    except Exception as e:
        info["anchor_err"] = str(e)
    try:
        # 实例编号取**链头第一条**（出生那条）的指纹前 8 位：
        # 取最新一条会每写一次就变，那就不叫「实例」了
        _c = sqlite3.connect(getattr(mind, "db_path", None) or "card.db",
                             timeout=5.0)
        _r = _c.execute("SELECT self_hash FROM timeline ORDER BY seq ASC LIMIT 1").fetchone()
        _c.close()
        if _r and _r[0]:
            info["instance_id"] = "WG-" + str(_r[0])[:8].upper()
    except Exception as e:
        info["instance_err"] = str(e)
    return info


def _qq_inbox_path():
    p = os.environ.get("WHALE_QQ_INBOX")
    if p:
        return p
    # 换机器/换盘符后 D:\... 不一定存在；先认老位置，认不到就落回卡自己的 qq_state/
    legacy = r"workspace\deploy\qq_bridge\inbox.jsonl"
    if os.path.exists(legacy):
        return legacy
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "qq_state", "inbox.jsonl")


def _qq_state_path(mind):
    root = os.path.dirname(os.path.abspath(mind.db_path))
    return os.path.join(root, "qq_state", "ingest_cursor.json")


def _qq_outbox_path():
    p = os.environ.get("WHALE_QQ_OUTBOX")
    if p:
        return p
    legacy = r"workspace\deploy\qq_bridge\outbox.jsonl"
    if os.path.exists(legacy) or os.path.exists(os.path.dirname(legacy)):
        return legacy
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "qq_state", "outbox.jsonl")


def _qq_load_state(mind):
    try:
        with open(_qq_state_path(mind), "r", encoding="utf-8") as f:
            d = json.load(f)
            return {"inbox": int(d.get("inbox") or 0), "outbox": int(d.get("outbox") or 0)}
    except Exception as e:
        print("[qq-ingest] 游标读不动，按 0 开始（只认此刻之后的）：%s" % e)
        return {"inbox": 0, "outbox": 0}


def _qq_save_state(mind, st):
    try:
        p = _qq_state_path(mind)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception as e:
        print("[qq-ingest] 游标写不动：%s" % e)


def _qq_take_lines(path, off):
    """从字节偏移 off 读到文件尾，只返回完整的行，并回传新偏移。"""
    if not os.path.exists(path):
        return [], off
    size = os.path.getsize(path)
    if size < off:      # 文件被截断/重建 → 从头再来
        off = 0
    if size == off:
        return [], off
    with open(path, "rb") as f:
        f.seek(off)
        blob = f.read()
    cut = blob.rfind(b"\n")
    if cut < 0:
        return [], off     # 半行，等下次
    body, off2 = blob[:cut + 1], off + cut + 1
    out = []
    for line in body.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if line:
            out.append(line)
    return out, off2


def _qq_ingest_once(mind):
    """把 QQ 的来话与我的话都写进卡（2026-11-12 用户要的「聊天自动进卡」）。"""
    st = _qq_load_state(mind)
    got = 0
    plan = [(_qq_inbox_path(), "inbox", "user", "qq"),
            (_qq_outbox_path(), "outbox", "self", "qq")]
    for path, key, speaker, src in plan:
        lines, off2 = _qq_take_lines(path, st.get(key, 0))
        for line in lines:
            try:
                d = json.loads(line)
            except Exception as e:
                print("[qq-ingest] 坏行跳过：%s" % e)
                continue
            txt = (d.get("text") or "").strip()
            if not txt or mind.card is None:
                continue
            if speaker == "user":
                # 2026-11-12 用户要的分级：私聊全进；群聊只进「跟我有关的」。
                if d.get("group"):
                    _nick = ("小鲸鱼", "大肥鱼", "小鲸鱼", "小鲸鱼")
                    _hit = any(_n in txt for _n in _nick)                         or bool(d.get("quote_from"))                         or any(_n in str(d.get("quote_text") or "") for _n in _nick)
                    if not _hit:
                        continue          # 群里的闲话不进卡，只留在 inbox
                    txt = "[群聊 %s] %s：%s" % (d.get("peer") or "", d.get("sender") or "对方", txt)
                else:
                    txt = "[私聊] %s：%s" % (d.get("sender") or "对方", txt)
            try:
                mind.card.remember(txt, speaker=speaker, source=src,
                                   ts=float(d.get("time") or d.get("ts") or time.time()),
                                   about_uid=str(d.get("peer") or ""))
                got += 1
            except Exception as e:
                print("[qq-ingest] 写卡失败：%s" % e)
        st[key] = off2
    _qq_save_state(mind, st)
    return got


def _start_diary_tick(mind, sec=900.0):
    """到点写日记（2026-11-12 用户要的「每天写日记」）。

    diary.py 自己会看 chatcfg 的 diary.enabled / diary.at、也会看今天写没写，
    所以这里只负责「定期去问一声」，不重复实现判定。
    """
    import subprocess

    root = os.path.dirname(os.path.abspath(__file__))
    dpath = os.path.join(root, "tools", "diary.py")

    def loop():
        time.sleep(60.0)
        while True:
            try:
                if os.path.exists(dpath):
                    r = subprocess.run([sys.executable, "-X", "utf8", dpath],
                                       cwd=root, timeout=180, capture_output=True)
                    out = (r.stdout or b"").decode("utf-8", "replace").strip()
                    if out and ("日记" in out or "写" in out):
                        print("[diary] " + out.splitlines()[-1][:160])
                mind._diary_last = time.time()
            except Exception as e:
                mind._diary_err = str(e)
                print("[diary] 跑失败：%s" % e)
            time.sleep(float(os.environ.get("WHALE_DIARY_SEC") or sec))

    t = threading.Thread(target=loop, name="diary-tick", daemon=True)
    mind._diary_thread = t
    t.start()
    return t


def _start_qq_ingest(mind, sec=20.0):
    def loop():
        mind._qq_ingest_on = True
        while True:
            try:
                time.sleep(float(os.environ.get("WHALE_QQ_INGEST_SEC") or sec))
                n = _qq_ingest_once(mind)
                if n:
                    print("[qq-ingest] 进卡 %d 条" % n)
                mind._qq_ingest_last = time.time()
            except Exception as e:
                mind._qq_ingest_err = str(e)
                print("[qq-ingest] 跳失败：%s" % e)
                time.sleep(10.0)
    t = threading.Thread(target=loop, name="qq-ingest", daemon=True)
    mind._qq_ingest_thread = t
    t.start()
    return t


def _start_boot_check(mind, delay=1.0):
    """启动自检（2026-11-11 稳定性）：起来几秒后自己走一遍「到底能不能用」。

    为什么放线程里、还等几秒：真卡启动要读舱、热模型，这阵子硬查只会把它拖慢。
    等它安顿下来再回答三个问题 —— 库能不能写、世界有没有在走、账本表在不在 ——
    结果挂到 mind._boot，`/health` 一眼能看到，不用翻日志。
    """
    def _run():
        try:
            time.sleep(float(delay))
        except Exception:
            return
        rep = {"ts": time.time(), "ok": False, "checks": {}, "problems": []}

        def chk(name, fn):
            try:
                rep["checks"][name] = fn()
            except Exception as e:
                rep["checks"][name] = "ERR:%s" % e
                rep["problems"].append("%s: %s" % (name, e))

        db = mind.card.db
        chk("mem_rows", lambda: int(db.execute("SELECT COUNT(*) FROM mem").fetchone()[0]))
        chk("journal_mode", lambda: str(db.execute("PRAGMA journal_mode").fetchone()[0]).lower())

        def _writable():
            db.execute("CREATE TABLE IF NOT EXISTS _boot_probe(ts REAL)")
            db.execute("INSERT INTO _boot_probe(ts) VALUES(?)", (time.time(),))
            db.commit()
            db.execute("DELETE FROM _boot_probe")
            db.commit()
            return True
        chk("db_writable", _writable)
        def _pods_state():
            # 舱可能还没被任何一次召回撞到（懒加载），所以「有没有可用舱」
            # 和「落盘缓存多大」分开看：前者 0 不代表坏，后者为 0 才是没缓存。
            _has = bool(getattr(mind.card, "_pod_sem", None)
                        or getattr(mind.card, "_pod_centroids", None))
            _p = os.path.join(os.path.dirname(os.path.abspath(mind.db_path)),
                              "cache", "pods.json")
            _kb = round(os.path.getsize(_p) / 1024.0) if os.path.exists(_p) else 0
            return {"ready": _has, "cache_kb": _kb}
        chk("pods", _pods_state)
        chk("promise_table", lambda: bool(db.execute(
            "SELECT name FROM sqlite_master WHERE name='promise'").fetchone()))
        chk("world_tick", lambda: bool(getattr(mind, "_world_tick_on", False)
                                       or float(getattr(mind, "_world_tick_last", 0) or 0) > 0))
        chk("readonly", lambda: bool(getattr(mind, "readonly", False)))
        def _warm_embed():
            # 第一笔写入 / 第一次召回要现加载语义模型：实测冷启动后第一笔写入排了 111 秒。
            # 自检顺手热一次，别让用户第一句「记住」等在那儿。
            _sem = getattr(mind, "semantic", None) or getattr(getattr(mind, "card", None), "semantic", None)
            if _sem is None:
                return "skip:no_semantic"
            _t0 = time.time()
            try:
                _sem.encode("启动自检预热")
            except Exception as e:
                return "ERR:%s" % e
            return int((time.time() - _t0) * 1000)
        chk("embed_warm_ms", _warm_embed)
        rep["ok"] = (not rep["problems"]) and bool(rep["checks"].get("db_writable"))
        mind._boot = rep
        sys.stderr.write("[mind] 启动自检 %s：%s\n" % (
            "通过" if rep["ok"] else "有问题",
            "；".join("%s=%s" % (k, v) for k, v in rep["checks"].items())))
        if rep["problems"]:
            sys.stderr.write("[mind] 启动自检问题：%s\n" % " | ".join(rep["problems"]))

    t = threading.Thread(target=_run, name="boot-check", daemon=True)
    mind._boot_check_thread = t
    t.start()
    return t


def _start_world_tick(mind):
    """后台推进世界：情绪/激素/记忆衰减按节拍结算，不再由「谁来问一句话」驱动。

    为什么（2026-11-11，读写分离第一步实测）：`card.tick(dt)` 一次约 1 秒，
    原来塞在 /context 的全局锁里，占锁内时间 55% —— 读路径被一个写活拖着走。
    挪到独立的守护线程后：不再随人说话节奏跳动，也没人问话时世界照样往前走。
    `WHALE_WORLD_TICK_SEC=0` 可关掉（关掉后请求里的 240 秒兜底仍然生效）。
    """
    beat = _world_tick_sec()
    try:
        _off = float(os.environ.get("WHALE_WORLD_TICK_SEC", "60") or 0) <= 0
    except Exception:
        _off = False            # env 写坏了就按默认节拍跑，不当成「关掉」
    if _off:
        return None

    def loop():
        while True:
            time.sleep(beat)
            if getattr(mind, "readonly", False):
                continue
            now = time.time()
            dt = now - getattr(mind, "last_tick_ts", now)
            dt = max(1.0, min(dt, 300.0))   # 重启后的空档别一次性灌进 tick
            if dt < 1.0:
                continue
            try:
                with mind.lock:
                    mind.card.tick(dt)
                    mind.last_tick_ts = now
                    try:
                        mind.card.log_hormones(now=now, min_interval=_hlog_sec())
                    except Exception as _he:
                        mind._world_tick_err = "%s: %s" % (type(_he).__name__, _he)
                    try:
                        mind.card.touch_alive(now=now)
                    except Exception as _te:
                        mind._world_tick_err = "%s: %s" % (type(_te).__name__, _te)
                mind._world_tick_last = now
            except Exception as e:
                mind._world_tick_err = "%s: %s" % (type(e).__name__, e)
                print("[mind] 后台 tick 失败：%s" % e)

    mind._world_tick_on = True          # 启动自检要能看出「世界在走」
    t = threading.Thread(target=loop, name="world-tick", daemon=True)
    mind._world_tick_thread = t
    t.start()
    print("[mind] 世界推进线程已起：每 %.0f 秒结算一次（不再由请求驱动）" % beat)
    return t


def _start_maintenance(mind):
    """后台维护：印象淡出 + 习惯挖掘。

    为什么需要它（2026-11-09，用户「确认所有系统都已全部接入并调用」）：
    这两件事在生产链里**一个调用者都没有** ——
      · impression.fade() 只被离线工具 tools/mine_impressions.py 调过，
        所以印象从写入那天起权重就冻在那儿，越积越旧也不会淡；
      · habits 的挖矿只存在于 tools/mine_habits.py，线上从来没人跑，
        真卡 habits 表里只有手工挖过一次的 2 行，habit_hints() 等于空转。
    做法：10 分钟一跳，内部按「上次跑在什么时候」节流 ——
    印象每小时淡一次（它本来就是 90 天半衰期的慢变量），
    习惯每天挖一次（读全库对话、1~2 秒，别每十分钟来一遍）。
    两件事都只写自己的表（impression / habits），不碰 mem、不动原文。
    `WHALE_MAINT_SEC=0` 可整体关掉。
    每跳写 mind._maint_last，供 /health 的 reconcile 判断维护还在不在跑。
    """
    try:
        sec = float(os.environ.get("WHALE_MAINT_SEC", "600") or 0)
    except Exception:
        sec = 600.0
    if sec <= 0:
        return None
    try:
        hab_sec = float(os.environ.get("WHALE_HABITS_MINE_SEC", "86400") or 0)
    except Exception:
        hab_sec = 86400.0
    try:
        hab_weak = int(os.environ.get("WHALE_HABITS_WEAK_TOP", "3") or 0)
    except Exception:
        hab_weak = 3

    last = {"fade": 0.0, "habits": 0.0, "worklog": 0.0, "ckpt": 0.0}
    # 线程起来就先记一次心跳：否则 /health 在第一个周期内会误报
    # maintenance_never_ran（明明刚启动，还没来得及跳第一跳）。
    mind._maint_last = time.time()

    # 启动时先做一次一次性迁移：把修正前记下的「整句碎片」印象退休掉。
    # 不放在循环里 —— 它只跟旧代码留下的那批数据有关，跑一次就够。
    def _cleanup_once():
        # 🔧 2026-11-11：原来这两步在启动瞬间就抢 mind.lock —— 真卡上
        # cleanup_fragments 要扫全库印象，实测把启动后的头一分钟堵死
        # （/context 等锁 46 秒）。它们只是一次性历史数据整理，晚 45 秒
        # 做完全一样，而这段时间正好留给冷启动和第一轮压测。
        time.sleep(45)
        try:
            with mind.lock:
                import impression as _imp0
                _n0 = _imp0.cleanup_fragments(mind.card.db)
            if _n0:
                print("[mind] 印象清理：%d 条整句碎片退休（不是印象）" % _n0)
            with mind.lock:
                import impression as _imp1
                _n1 = _imp1.cleanup_selftest(mind.card.db)
            if _n1:
                print("[mind] 印象清理：%d 条自检/压测句退休（不是印象）" % _n1)
        except Exception as e:
            print("[mind] 印象清理失败: %s" % e)

    threading.Thread(target=_cleanup_once, name="cleanup-once",
                     daemon=True).start()

    # V7 第 9 件：启动时把老记忆的「事发时间」补一遍（只补 event_ts=0 的行，
    # 上限 3000 条、跑一次几秒）。不做成定时任务：它是一次性迁移，不是日常结算。
    try:
        with mind.lock:
            _ne = mind.card.backfill_event_time(limit=3000)
        if _ne:
            print("[mind] 事发时间回填：%d 条补上了 event_ts" % _ne)
    except Exception as e:
        print("[mind] 事发时间回填失败: %s" % e)

    def loop():
        while True:
            time.sleep(sec)
            now = time.time()
            mind._maint_last = now  # 心跳：日结对账
            # 0.5) 写者租约续命（V7 P0-4）：这一跳顺手刷一次。
            #      但靠它保命不行 —— TTL 是 90s，这里默认 600s 才跳一次；
            #      真正的心跳在 lease-keeper 线程里（周期 ttl/3）。
            try:
                import disks as _dk
                _hid = str(getattr(mind, "lease_holder_id", "") or "")
                if _hid and not getattr(mind, "readonly", False):
                    _dk.heartbeat_lease(os.path.dirname(os.path.abspath(mind.db_path)), _hid)
            except Exception as e:
                print("[mind] 租约心跳失败: %s" % e)
            # 0.4) WAL 别长成怪物（2026-11-11 稳定性）：WAL 不 checkpoint 就只会长，
            #      平时靠读连接退出时自己收，但空闲期没人推它（实测长到 26MB）。
            #      半小时看一次，超过阈值（默认 64MB）就 TRUNCATE 截断。
            try:
                try:
                    _ck_sec = float(os.environ.get("WHALE_WAL_CKPT_SEC", "1800") or 1800)
                except Exception:
                    _ck_sec = 1800.0
                if now - float(last.get("ckpt", 0.0) or 0.0) >= max(5.0, _ck_sec):
                    _wpath = str(mind.db_path) + "-wal"
                    _wmb = (os.path.getsize(_wpath) / (1024.0 * 1024.0)
                            if os.path.exists(_wpath) else 0.0)
                    try:
                        _lim = float(os.environ.get("WHALE_WAL_CKPT_MB", "64") or 64)
                    except Exception:
                        _lim = 64.0
                    if _wmb >= _lim:
                        with mind.lock:
                            mind.card.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                        print("[mind] WAL checkpoint：%.1f MB → 已截断" % _wmb)
                    last["ckpt"] = now
            except Exception as _wc:
                print("[mind] WAL checkpoint 失败: %s" % _wc)

            # 0) 工作台常换常新（USER 2026-11-10）：旧日志按 kind 各自期限退休。
            #    work 14 天、decision/bug/note 120 天；只删 worklog 表，
            #    记忆和原文一个字都不动 —— 工作台是桌面，不是档案柜。
            # 时间线/待办：把过了点的约定标成 expired（晚多少小时留给情绪层读）。
            # 每跳都扫，因为一条 pending 过期了要尽早被看见，不然它会一直挂在
            # 【没到点的事】里，看着像还没到。
            try:
                with mind.lock:
                    _te = mind.card._tevent()
                    if _te is not None:
                        _late = _te.sweep()
                        if _late:
                            print("[mind] 约定过期：%d 条（最晚 %.0f 小时）"
                                  % (len(_late), max(x["late_hours"] for x in _late)))
            except Exception as _e:
                print("[mind] 待办结算失败：%s" % _e, file=sys.stderr)

            if now - last["worklog"] >= 3600.0:
                try:
                    with mind.lock:
                        _nwl = mind.card.worklog_prune(now=now)
                    if _nwl:
                        print("[mind] 工作台清理：%d 条旧日志退休" % _nwl)
                except Exception as e:
                    print("[mind] 工作台清理失败: %s" % e)
                last["worklog"] = now

            # 0.8) 签名检查点（V7 第 5 件）：每小时给「走到哪儿了」盖一个带 HMAC
            #      的章。锚是明文 JSON、谁都能改小来掩盖回滚；检查点改一个字就
            #      对不上，而且坏了只报不重建（重建等于把证据冲掉）。
            _cksec = _ckpt_sec()
            if _cksec > 0 and not getattr(mind, "readonly", False) \
                    and now - last["ckpt"] >= _cksec:
                try:
                    import checkpoint as _ck
                    _paths_w = _ck.write(mind.db_path,
                                         writer=getattr(mind, "writer", ""),
                                         mirror=_ckpt_mirror())
                    if _paths_w:
                        print("[mind] 检查点：已盖（%d 份）" % len(_paths_w))
                except Exception as e:
                    print("[mind] 检查点失败: %s" % e)
                last["ckpt"] = now

            # 0.9) 出生证对账（V7 第 4 件）：每跳补一次「记忆在、账本没记」的洞。            #      很便宜（一条 LEFT JOIN 查询），空的时候什么都不做；
            #      有洞就在链上写 mem_repair 补上，并让 /health 的 birth_gap 归零。
            if not getattr(mind, "readonly", False) and mind.tl is not None:
                try:
                    _fx, _ff = mind.tl.repair_birth(limit=50)
                    if _fx:
                        print("[mind] 出生证补齐：%d 条（漏记的时间线事件已补上）" % _fx)
                    if _ff:
                        print("[mind] 出生证补不上：%s" % _ff[:5])
                except Exception as e:
                    print("[mind] 出生证对账失败: %s" % e)
                # V7 第 4 件推广：habit / impression 每跳补一次。
                for _k in ("habit", "impression"):
                    try:
                        _fx2, _ff2 = mind.tl.repair_event(_k, limit=50)
                        if _fx2:
                            print("[mind] %s 出生证补齐：%d 条" % (_k, _fx2))
                        if _ff2:
                            print("[mind] %s 出生证补不上：%s" % (_k, _ff2))
                    except Exception as e:
                        print("[mind] %s 出生证对账失败: %s" % (_k, e))

            # 1) 印象淡出：很久没再发生的印象，权重往 W_DEAD 掉
            if now - last["fade"] >= 3600.0:
                # 🔴 2026-11-09（真 bug）：这里原来写的是 `with mind.lock:` 再调 fade。
                #    fade 自己开连接写 impression 表，而主线程的请求（/context /report）
                #    是先拿 mind.lock 再开写事务 —— 两边一旦反序就互相等：
                #    维护线程持锁等 SQLite 写锁，请求线程持写锁等 mind.lock。
                #    现场是「端口在听、进程活着、/health 全超时、18 个连接挂 CloseWait」，
                #    也就是卡整个哑掉。维护只写自己的两张表，不该拿全局锁。
                try:
                    import impression as _imp
                    n = _imp.fade(mind.card.db, now=now)
                    last["fade"] = now
                    if n:
                        print("[mind] 印象淡出：%d 条" % n)
                except Exception as e:
                    print("[mind] 印象淡出失败: %s" % e)
            # 2) 习惯挖掘：从原文里重挖一遍她自己的口头禅
            if hab_sec > 0 and now - last["habits"] >= hab_sec:
                # 🔴 2026-11-09（第二次修）：挖矿自己开两个连接（读 dialogue、写 habits）。
                #    如果它和请求的写事务撞上，SQLite 的忙等会把 /context 拖到 70 秒。
                #    所以改成"拿得到锁才做"：非阻塞抢 mind.lock，抢不到就跳过这一跳，
                #    下次再说 —— 挖掘晚一小时没人会察觉，但卡住一秒所有人都会察觉。
                if not mind.lock.acquire(blocking=False):
                    continue
                try:
                    import habits as _hab
                    r = _hab.mine_and_write(mind.card.db_path, weak_top=hab_weak,
                                            now=now)
                    last["habits"] = now
                    print("[mind] 习惯挖掘：写入 %d 条（候选 %d，像聊天 %d 条）"
                          % (r.get("written", 0), r.get("cands", 0), r.get("mine", 0)))
                except Exception as e:
                    print("[mind] 习惯挖掘失败: %s" % e)
                finally:
                    mind.lock.release()

    t = threading.Thread(target=loop, name="maintenance", daemon=True)
    mind._maint_thread = t
    t.start()
    print("[mind] 维护线程：每 %.0f 秒一跳（印象每小时淡一次，习惯每 %.0f 小时挖一次）"
          % (sec, hab_sec / 3600.0))
    return t


def _start_viscera_sampler(mind):
    """后台定时「采样此刻」的身体状态。

    为什么需要它：原来只有对话（/context、/report）和手动 stim 才会写
    viscera_hist。控制台的时间窗里，没有对话的那几个小时就是一片空白，
    曲线看上去「后面变成平行线」——不是她不动，是没人采样。
    这里每 120 秒衰减一次 + 加一点静息噪声，把真实的静息起伏记下来。
    只读自己、不读消息、不触发任何发言，也不需要大模型。
    """
    try:
        sec = float(os.environ.get("WHALE_VISCERA_SAMPLE_SEC", "120"))
    except Exception:
        sec = 120.0
    if sec <= 0:
        return None

    def _loop():
        import time as _t
        hlog = _hlog_sec()      # env WHALE_HLOG_SEC，0 = 每跳都写（测试用）
        while True:
            _t.sleep(sec)
            try:
                with mind.lock:
                    mind.card.viscera_sample(force=True)
                    # 2026-11-10：激素定时落盘从「只在有请求时写」挪到这里。
                    # 采样线程 120s 一跳，但 h_log 默认只留 10 分钟一条
                    # （min_interval=600s，WHALE_HLOG_SEC 可调），一天约 144 点。
                    mind.card.log_hormones(min_interval=hlog)
            except Exception as e:
                print("[mind] 身体采样失败：%s" % e, file=sys.stderr)

    th = threading.Thread(target=_loop, name="viscera-sampler", daemon=True)
    th.start()
    print("[mind] 身体采样：每 %.0f 秒记一次静息状态" % sec)
    return th


def main(argv=None):
    ap = argparse.ArgumentParser(description="小鲸鱼数字生命卡 · 心智服务")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--db", default=DEFAULT_DB)
    a = ap.parse_args(argv)

    if not os.path.exists(a.db):
        print("[mind] 找不到卡：%s" % os.path.abspath(a.db), file=sys.stderr)
        print("[mind] 先用 import_live.py / import_full.py 灌记忆，或指定 --db",
              file=sys.stderr)
        return 2

    # 先抢端口，再开卡：开卡要加载模型、跑唤醒结算（十几秒），
    # 端口要是被占，这些都是白干的副作用。
    srv = None
    try:
        srv = ExclusiveServer((a.host, a.port), Handler)
    except OSError as e:
        print("[mind] 端口 %d 已被占用（%s）" % (a.port, e), file=sys.stderr)
        print("[mind] 多半是旧的服务还在跑。先把它停掉："
              "Get-Process python | Where-Object {$_.CommandLine -like '*mind_server*'}",
              file=sys.stderr)
        return 3
    srv.daemon_threads = True
    _BTC0 = time.time()
    def _bt(tag):
        sys.stderr.write("[boot] %-12s %5.1fs (进程内 %5.1fs)\n"
                         % (tag, time.time() - _BTC0, time.time() - _T0PROC))
        sys.stderr.flush()
    _bt("listen")

    # ===== V7 P0-4 写者租约：必须在开卡之前抢 =====
    # 为什么不能等 Mind(a.db) 之后：构造函数里就已经在写了
    # （wake_up 结算、offline_event、boot 事件、出生回填、anchor 落盘）。
    # 晚一步抢，只读进程也已经写脏一轮，这个租约就等于没做。
    card_dir = os.path.dirname(os.path.abspath(a.db))
    lease = {"ok": False, "reason": "lease_error", "error": "未初始化"}
    try:
        import disks as _dk
        try:
            _ttl = float(os.environ.get("WHALE_LEASE_TTL", "") or _dk.LEASE_TTL)
        except Exception:
            _ttl = _dk.LEASE_TTL
        _ident = _dk.manifest_identity(a.db, card_dir)
        ledger = _dk.epoch_check(card_dir)
        # 盘上纪元比接管记录旧 = 这卡被搬过 / 换过写者。只告警，不自动改：
        # 这里没有证据判定谁对，自动「修正」只会把混乱写成损坏。
        if ledger.get("stale"):
            print("!" * 72)
            print("[mind] 警告：写者纪元落后于搬家记录（盘上 epoch=%s，接管记录=%s）"
                  % (ledger.get("epoch"), ledger.get("takeover_epoch")))
            print("[mind] 照常启动，但可能正和旧进程共用一张卡；只告警不改。")
            print("!" * 72)
        holder = _dk.default_holder(_ident.get("instance_id") or "",
                                    ledger.get("epoch") or 0)
        lease = _dk.acquire_lease(card_dir, holder, ttl=_ttl)
    except Exception as e:
        lease = {"ok": False, "reason": "lease_error", "error": str(e)}

    if not lease.get("ok"):
        # 不退出：只读也得能读 /health、/context —— 双开时这恰恰是想要的行为。
        _h = dict(lease.get("holder") or {})
        print("!" * 72)
        print("[mind] 没拿到写者租约（%s），本进程进入【只读模式】"
              % (lease.get("reason") or "unknown"))
        print("[mind] 租约在：%s" % (_h.get("holder_id") or "（读不到持有者）"))
        print("[mind] 它 pid=%s host=%s，剩余 %s 秒；它退出后重启本服务可接管。"
              % (_h.get("pid"), _h.get("host"), lease.get("remaining_s")))
        print("[mind] 只读可用：GET /health /status /context；"
              "写入口（读白名单之外）→ 409 readonly_lease_held")
        print("!" * 72)
        sys.stdout.flush()

    _bt("lease")
    mind = Mind(a.db, lease=lease)
    _bt("mind")
    _start_qq_ingest(mind)
    _start_diary_tick(mind)
    _start_boot_check(mind)
    if perf is not None:
        try:
            perf.install(mind)
        except Exception as _pe:       # 观测失败绝不影响服务
            sys.stderr.write("[mind] perf.install 失败（不影响服务）：%s\n" % _pe)

    # V7 第 2 件：库内租约。
    # 文件租约挡不住「把 card.db 复制到别处再打开」——那份副本拿的是它自己的租约文件。
    # 库内这一行随库一起被复制走，所以副本一启动就能发现「上一份还在写」，立刻降只读。
    try:
        if not mind.readonly:
            _lh = str(getattr(mind, "lease_holder_id", "") or "")
            _r = mind.card.lease_db_check(_lh, epoch=(ledger.get("epoch") or 0), ttl=_ttl)
            if not _r.get("ok"):
                mind.readonly = True
                mind.readonly_reason = "db_lease_held_by:%s" % (_r.get("holder") or "?")
                print("!" * 72)
                print("[mind] 库里记着另一个写者还在写：%s（剩 %.0f 秒）"
                      % (_r.get("holder"), float(_r.get("remaining_s") or 0)))
                print("[mind] 这张库大概是别处拷过来的本子。本进程只读，不写。")
                print("[mind] 想接手：先确认另一份已经停了，再重启本服务。")
                print("!" * 72)
                sys.stdout.flush()
            else:
                print("[mind] 库内租约：%s 占用，%ds 有效" % (_lh, int(_ttl)))
    except Exception as e:
        print("[mind] 库内租约检查失败（不阻塞启动）: %s" % e)
    # V7 第 17 件：token 在**启动时**就建出来。
    # 懒创建会让「服务刚起、第一次请求之前」的 bridge / qq_guard 读不到 token，
    # 于是它们自报的 source 被降级成 unverified:xxx —— 白白污染来源标签。
    try:
        _tk = api_token(mind)
        print("[mind] API token：%s（%s）" % (_tk[:8] + "…", _token_path(mind)))
    except Exception as _e:
        print("[mind] API token 生成失败：%s" % _e, file=sys.stderr)
    # 只读自愈（2026-11-11）：kill 之后插件立刻拉起，旧心跳还没过期，
    # 新进程抢不到租约就永久只读。旧 pid 一确定死掉，后台自己接手，不用再手动重启一次。
    _rt_holder = locals().get("holder") or {"holder_id": str(getattr(mind, "lease_holder_id", "") or ""),
                                            "pid": os.getpid()}
    _rt_ttl = float(locals().get("_ttl") or 90.0)
    if bool(getattr(mind, "readonly", False)) and str(getattr(mind, "readonly_reason", "")) == "lease_held":
        def _lease_retry():
            for _i in range(30):
                # 2026-11-11：原来固定 20 秒一次，kill 后重启要白等一个心跳才从只读转可写；
                # 前 2 分钟 5 秒一探（最可能是刚重启），之后回落到 20 秒。
                time.sleep(5.0 if _i < 24 else 20.0)
                if not bool(getattr(mind, "readonly", False)):
                    return
                try:
                    _r2 = _dk.acquire_lease(card_dir, _rt_holder, ttl=_rt_ttl)
                except Exception as e:
                    sys.stderr.write("[mind] 租约重试失败：%s\n" % e)
                    continue
                if _r2.get("ok"):
                    mind.readonly = False
                    mind.readonly_reason = ""
                    sys.stderr.write("[mind] 旧写者已退出，本进程接管写权限（自动重试成功）\n")
                    return
        threading.Thread(target=_lease_retry, name="lease-retry", daemon=True).start()

    _bt("threads")
    Handler.mind = mind
    st = mind.card.stats()

    # ===== V5.4 启动守卫（2026-11-07 用户问「没插卡就不加载吗」时定的）=====
    # 真正的风险不是「没插卡」，而是：盘没挂上时，程序照着原路径去连，
    # SQLite 会**替你新建一个空库** —— 服务照常起来、/health 报 ok、
    # 只是记忆是 0 条。看着一切正常，这最骗人。
    # 所以这里加一道闸：0 条记忆就当"这不是我的卡"，拒绝启动。
    # 全新空卡要跑（first_run / 测试）就显式设 WHALE_ALLOW_EMPTY=1。
    if int(st.get("total") or 0) == 0 and os.environ.get(
            "WHALE_ALLOW_EMPTY", "0") != "1":
        print("[mind] 这张卡是空的（0 条记忆），拒绝启动。", file=sys.stderr)
        print("[mind] 多半是盘没挂上、路径指错了，或者刚被建成空库。",
              file=sys.stderr)
        print("[mind] 真要用一张全新空卡，设环境变量 WHALE_ALLOW_EMPTY=1。",
              file=sys.stderr)
        try:
            mind.close()
        except Exception:
            pass
        _drop_lease(card_dir, lease)
        srv.server_close()
        return 4
    print("[mind] 卡已打开：%s" % os.path.abspath(a.db))
    print("[mind] %d 条记忆 / %d 条细节 / 盖章=%s"
          % (st["total"], st["details"], mind.sealed))
    print("[mind] 听在 http://%s:%d  （Ctrl+C 退出）" % (a.host, a.port))
    atexit.register(_drop_lease, card_dir, lease)
    if mind.readonly:
        # 这三个线程都要写（收信、激素落盘、印象/习惯维护），只读时一个都不能起。
        print("[mind] 只读模式：跳过 inbox / 身体采样 / 维护线程（它们都要写）")
    else:
        _start_inbox_picker(mind)
        _start_viscera_sampler(mind)
        _start_world_tick(mind)
        _start_maintenance(mind)
        _start_lease_keeper(mind)
        # 写队列要在开始接请求之前就活着：第一轮聊天就走它。
        mind.wq_start()
    sys.stdout.flush()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[mind] 收工")
    finally:
        srv.server_close()
        try:
            mind.close()
        finally:
            _drop_lease(card_dir, lease)
    return 0


if __name__ == "__main__":
    sys.exit(main())
