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
import argparse
import json
import os
import secrets
import io
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from card2 import MemoryCard          # noqa: E402
import hormone as hormone_mod          # noqa: E402
import viscera                        # noqa: E402
import timeline as timeline_mod        # noqa: E402

VERSION = "1.0"
DEFAULT_PORT = 8848
DEFAULT_DB = os.path.join(HERE, "card.db")
# 请求体上限。原来 256KB 够文本用，但换头像要走 base64（+33%），
# 一张 512x512 的照片转 PNG 后 base64 常有 600~900KB，所以放宽到 6MB。
# 真正落盘的头像另有 4MB 上限（identity.set_avatar 里卡着）。
MAX_BODY = 6 * 1024 * 1024             # 6 MB，超过直接拒
# 单条文本上限。body 能到 6MB 是为了头像 base64，但「一句话」不该有 30 万字符：
# 那是调用方出错（把整个文件塞进 message），拿去做分词会把一轮对话拖死。
# 20000 字符 ≈ 一万字中文，真人一次消息远到不了这个量。
MAX_TEXT = 20000                       # 单条 message / user_message 超过就 413

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

    def __init__(self, db_path):
        self.lock = threading.RLock()
        self.db_path = db_path
        self.card = MemoryCard(db_path)
        self.opened_ts = time.time()
        self.last_tick_ts = time.time()
        self.requests = 0
        self.errors = 0
        # ---- 醒来：把离线那段结算清楚（V5.0 §11.4）----
        # 必须在任何请求之前跑。策略默认 freeze（拔卡=睡觉，时间不走），
        # 换 compress 就退回「离线照算、超长压缩」的老行为。
        # 这件事要**先于** timeline 的心跳补算 —— 顺序反了会先写一串
        # alive 再改 t_anchor，看起来像「人醒着但记忆在睡」，对不上。
        self.wake = {"gap_s": 0.0, "eff_s": 0.0, "shift_s": 0.0,
                     "policy": "?", "skipped": "未执行"}
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
            if n_birth == 0:
                n, root = self.tl.backfill_birth()
                self.birth_ok, self.birth_bad = self.tl.verify_birth()
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
            if self.anchor.get("verdict") in ("ok", "first"):
                anchor_mod.write(self.db_path)
        except Exception as e:
            self.anchor = {"verdict": "error", "ok": False,
                           "reason": "锚检查失败：%s" % e}

    # ---- 时间：每次请求先让它过去 ----
    def flow_time(self):
        now = time.time()
        # 心跳：每 10 分钟一条，这是「我一直活着」的证据
        try:
            if self.tl is not None:
                self.tl.heartbeat(now=now)
        except Exception:
            pass
        dt = now - self.last_tick_ts
        if dt >= 1.0:                   # 不到 1 秒不动，省得白算
            try:
                self.card.tick(dt)
            finally:
                self.last_tick_ts = now
        # 激素采样（控制台画趋势要用）。5 分钟一条，内部自己节流，
        # 所以这里每 1 秒调一次也不会写库。
        try:
            self.card.log_hormones(now=now)
        except Exception:
            pass
        # 「上次活着」的时间戳平时就要一点点写下去 ——
        # 拔卡那一刻进程是被直接杀掉的，来不及走收尾流程。
        # 没有这个戳，wake_up() 就不知道这次到底睡了多久。
        try:
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
        with self.lock:
            self.flow_time()
            # 🔴 2026-10-31：recent 要**两种形状都收**。
            #    接口文档写的是 recent[]，但没说清元素是字符串还是对象。
            #    按直觉传 [{"role":"user","text":"早"}...] 的调用方会撞上
            #    `TypeError: sequence item 0: expected str instance, dict found`
            #    （assemble_context 里 "\\n".join(lines) 拿到的是一串 dict）。
            #    这里是唯一收口的地方，就在这儿归一，别让调用方猜。
            #    字符串原样收；dict 依次认 text/content/reply 字段。
            _norm = []
            # 🔧 2026-11-08：调用方（MCP 的 mind_context）传的是**条数**，不是消息列表。
            #    原来只认 list，int 会在这里炸成
            #    `TypeError: 'int' object is not iterable` → /context 500。
            #    既然 int 的自然含义就是「最近几条」，那就按条数去 dialogue 里捞，
            #    让两种形状都成立，调用方不用猜。
            if isinstance(recent, int):
                try:
                    recent = self.recent_dialogue(int(recent or 12))
                except Exception:
                    recent = []
            for x in (recent or []):
                if isinstance(x, str):
                    s = x
                elif isinstance(x, dict):
                    s = (x.get("text") or x.get("content")
                         or x.get("reply") or "")
                else:
                    s = ""
                s = str(s).strip()
                if s:
                    _norm.append(s)
            recent = _norm
            kw = {}
            if budget:
                kw["budget"] = int(budget)
            # ⚠️ 2026-10-31：这里原来没传 identity，导致【我是谁】那一格是空的 ——
            # 也就是说身份锚明明盖了章，说话时却一次都没用上。查出来的经过：
            # 问「你答应过我什么」，组装出来的上下文里根本没有底线那三条。
            kw["identity"] = self.card.core_identity_text()
            # 【今天是】—— 时间线给的那句话。物理时间是唯一真值，
            # 认识第几天是派生读数（只显示，不参与衰减）。
            if self.tl is not None:
                try:
                    kw["identity"] = (kw["identity"] + "\n【今天是】"
                                      + self.tl.render_line()).strip()
                    self.tl.note_talk(str(message))
                except Exception:
                    pass
            ctx = self.card.assemble_context(message, recent_lines=recent, touch=True, **kw)
            hits = self.card.recall(message, top_k=5, access=True)
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
                    self.card.add_dialogue(mid, user_message, reply, ts=ts or time.time())
                except Exception:
                    pass
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


class Handler(BaseHTTPRequestHandler):
    mind = None                        # 由 main() 注入
    server_version = "WhaleMind/" + VERSION

    # ---- 工具 ----
    def log_message(self, fmt, *args):  # 静音：日志走我们自己的
        pass

    def _send(self, code, obj):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _fail(self, code, err, detail=""):
        Handler.mind.errors += 1
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
                })
            if method == "GET" and path == "/status":
                return self._send(200, {"ok": True, "status": m.status()})
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
                # 梦境压缩（卡侧）：把还没消化的对话原文掏出来给外面的提炼器
                with m.lock:
                    return self._send(200, m.card.pending_dialogue())
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
                    return self._send(200, {
                        "ok": True,
                        "impressions": m.card.impression_top(subject=subj, limit=lim, min_w=minw),
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
                if path == "/report":
                    um = (body.get("user_message") or "").strip()
                    if not um:
                        return self._fail(400, "empty_message", "user_message 不能为空")
                    if len(um) > MAX_TEXT:
                        return self._fail(413, "text_too_large",
                                          "user_message %d 字符 > 上限 %d"
                                          % (len(um), MAX_TEXT))
                    try:
                        out = m.report(um, body.get("reply") or "", body.get("ts"),
                                       source=body.get("source") or "chat")
                    except timeline_mod.ForeignWriter as e:
                        return self._fail(409, "card_locked", str(e))
                    return self._send(200, {"ok": True, "report": out})
                if path == "/remember":
                    # 🔧 2026-11-04：任意通道的单条记忆入口。
                    #   以前只有 /report（一问一答）能写卡，于是 QQ、B站、抖音里
                    #   发生的事全进不来 —— 「考试考得不错」就是这么丢的。
                    #   这里不假装有对话对象：text 就是"发生了什么/我说了什么"，
                    #   source 标明从哪来，speaker 默认 self（是我经历的）。
                    tx = (body.get("text") or "").strip()
                    if not tx:
                        return self._fail(400, "empty_text", "text 不能为空")
                    if len(tx) > MAX_TEXT:
                        return self._fail(413, "text_too_large",
                                          "text %d 字符 > 上限 %d"
                                          % (len(tx), MAX_TEXT))
                    src = (body.get("source") or "").strip() or "unknown"
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
                            speaker=(body.get("speaker") or "self"))
                    return self._send(200, {"ok": True, "dream": out})
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
        try:
            self._route(method)
        finally:
            dt = time.time() - t0
            if dt >= 5.0:
                try:
                    sys.stderr.write("[mind] 慢请求 %.1fs %s %s\n"
                                     % (dt, method, getattr(self, "path", "?")))
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
        while True:
            _t.sleep(sec)
            try:
                with mind.lock:
                    mind.card.viscera_sample(force=True)
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
    mind = Mind(a.db)
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
        srv.server_close()
        return 4
    print("[mind] 卡已打开：%s" % os.path.abspath(a.db))
    print("[mind] %d 条记忆 / %d 条细节 / 盖章=%s"
          % (st["total"], st["details"], mind.sealed))
    print("[mind] 听在 http://%s:%d  （Ctrl+C 退出）" % (a.host, a.port))
    _start_inbox_picker(mind)
    _start_viscera_sampler(mind)
    sys.stdout.flush()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[mind] 收工")
    finally:
        srv.server_close()
        mind.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
