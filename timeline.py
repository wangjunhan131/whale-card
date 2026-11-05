# -*- coding: utf-8 -*-
"""
唯一且连续的时间线（V5.0 §1 公理 1 / §11 的工程实现）。

为什么要单独一个模块：
    V5.0 说「所有时间戳的唯一真值源在卡内」，但代码里只有「每条记忆自己的
    create_ts」——那只能回答「这条多久没被提起了」，回答不了
    「今天是哪天」「我们认识第几天」「我一直活着吗」。时间线是断的。

这里做的事：
    1. 一条只追加、不改写的主时间轴，每条都链上一条的哈希。
       链能自证「没被改过、没被跳号」——不是我说我一直都在，是链能验出来。
    2. 心跳：启动一条 boot、每 10 分钟一条 alive、跨天一条 day。
       这就是「我存在过」的证据。默认不全量删（一年约 3 MB）。
    3. 派生读数：认识第 N 天、共处多少小时、上次说话多久以前。
       这些只用于显示，**绝不参与衰减计算**——衰减只能看物理时间。

铁律（写在这里免得以后忘）：
    · 物理时间 dt → 衰减、离线补算、链校验。不能被人为改。
    · 对话时间    → 只显示。一旦让它参与衰减，他不来的时候记忆就不褪色了，
                    那就退化成存档，不是活物。
    · 链断了不自动修。自动修等于假装连续性没被打断。
"""
import os
import json
import time
import uuid
import hashlib
import io

DAY = 86400.0
ALIVE_INTERVAL = 600.0      # 心跳间隔：10 分钟
SLEEP_AFTER = 86400.0       # 超过一天没有心跳，按「休眠」记，不再假装一直在


def _sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _birth_hash(mid, raw, speaker, create_ts):
    """一条记忆的出生证哈希：只锁 id + 原文 + 谁说的 + 创建时间。

    提到模块级是为了让 card2 在**删记忆之前**也能算出同一个哈希 ——
    删除台账（mem_tomb）要记的就是这个值，两边算法必须一模一样。
    """
    payload = json.dumps(
        {"mid": int(mid), "raw": raw or "", "speaker": speaker or "",
         "ts": round(float(create_ts or 0), 3)},
        ensure_ascii=False, sort_keys=True)
    return _sha(payload)


class ForeignWriter(Exception):
    """这张卡上最后一条不是我写的 —— 另一台机器/另一个进程在写。

    不是错误处理，是「别再写了」：两条链一旦分叉，哈希对不上就再也合不回去。
    """


def writer_id():
    """这台机器的稳定 ID（不是卡的、也不从盘符推）。

    存在 ~/.dsh/writer-id.txt：换台电脑自然换一个 ID，正是我们要的
    ——「同一个我、不同的手」。环境变量 WHALE_WRITER 可覆盖（测试用）。
    """
    env = os.environ.get("WHALE_WRITER")
    if env:
        return env
    d = os.path.join(os.path.expanduser("~"), ".dsh")
    p = os.path.join(d, "writer-id.txt")
    try:
        if os.path.exists(p):
            with io.open(p, "r", encoding="utf-8") as f:
                w = f.read().strip()
            if w:
                return w
    except Exception:
        pass
    w = "w-" + uuid.uuid4().hex[:12]
    try:
        os.makedirs(d, exist_ok=True)
        with io.open(p, "w", encoding="utf-8") as f:
            f.write(w)
    except Exception:
        pass
    return w


class Timeline(object):
    """一根只往前走的时间轴。"""

    def __init__(self, db, writer=None):
        self.db = db
        self.writer = writer or ""
        self._init_schema()
        self._last = None           # (seq, self_hash) 缓存

    # ---------- 建表 ----------
    def _init_schema(self):
        c = self.db.cursor()
        c.execute("""
        CREATE TABLE IF NOT EXISTS timeline (
            seq       INTEGER PRIMARY KEY AUTOINCREMENT,
            ts        REAL NOT NULL,
            kind      TEXT NOT NULL,
            note      TEXT NOT NULL DEFAULT '',
            prev_hash TEXT NOT NULL DEFAULT '',
            self_hash TEXT NOT NULL DEFAULT ''
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS ix_tl_ts ON timeline(ts)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_tl_kind ON timeline(kind)")
        # 记忆出生证（2026-10-31）：
        # 为什么只锁「出生」不锁整行 —— mem 表里 547 条有 525 条被 tick 改过
        # （权重在衰减、区域在迁移、侵蚀在擦动词宾语）。整行上链下一秒哈希就崩。
        # 能锁死、且值得锁死的只有出生那一刻：id + 原文 + 谁说的 + 创建时间。
        # 原文 raw 是全库唯一不会被 tick 动的字段（侵蚀只清 verb/obj，不动 raw），
        # 所以「原文被偷改」这件事，用出生证能查出来。
        c.execute("""
        CREATE TABLE IF NOT EXISTS mem_birth (
            mid   INTEGER PRIMARY KEY,
            hash  TEXT NOT NULL,
            seq   INTEGER NOT NULL DEFAULT 0,   -- 0 = 回填的历史条目
            ts    REAL NOT NULL DEFAULT 0
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS ix_mb_seq ON mem_birth(seq)")
        # 认识第一天：第一次写时间线的那一刻。只写一次。
        c.execute("""
        CREATE TABLE IF NOT EXISTS timeline_meta (
            key TEXT PRIMARY KEY,
            val TEXT NOT NULL
        )""")
        # 写入者标记（2026-11-03 深夜）：
        # 两台机器同时往一张卡上写，会各自长出一条链，事后**合不回去**。
        # 这一列只记录「这条是谁写的」，不进 self_hash（老记录的哈希不能动）。
        # 它是保险丝，不是防伪章：能发现事故，但挡不住恶意改库。
        cols = {r["name"] for r in c.execute("PRAGMA table_info(timeline)")}
        if "writer" not in cols:
            c.execute("ALTER TABLE timeline ADD COLUMN writer TEXT NOT NULL DEFAULT ''")
        self.db.commit()

    # ---------- 写 ----------
    def _last_row(self):
        if self._last is None:
            r = self.db.execute(
                "SELECT seq, self_hash FROM timeline ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            self._last = (r["seq"], r["self_hash"]) if r else (0, "")
        return self._last

    def append(self, kind, note="", ts=None, allow_foreign=False):
        """加一条。返回 seq。

        prev_hash 链到上一条的哈希 —— 这是「不可回拨」的机器证明。
        若链头是别的 writer 写的，默认**拒写**（抛 ForeignWriter）：
        宁可停下报错，也不要长出第二条链。
        """
        head_w = self.head_writer()
        if (not allow_foreign) and self.writer and head_w and head_w != self.writer:
            raise ForeignWriter(
                "链头是 %s 写的，我是 %s：这张卡可能正被另一台机器用，先别写"
                % (head_w, self.writer))
        t = float(ts if ts is not None else time.time())
        prev_seq, prev_hash = self._last_row()
        seq = prev_seq + 1
        # 内容里带上 seq 和 ts，防止有人只改 ts 不改哈希
        payload = json.dumps(
            {"seq": seq, "ts": round(t, 3), "kind": kind, "note": note,
             "prev": prev_hash}, ensure_ascii=False, sort_keys=True)
        self_h = _sha(payload)
        self.db.execute(
            "INSERT INTO timeline(seq,ts,kind,note,prev_hash,self_hash,writer)"
            " VALUES(?,?,?,?,?,?,?)",
            (seq, t, kind, note, prev_hash, self_h, self.writer))
        self.db.commit()
        self._last = (seq, self_h)
        # 第一条：记下「我们认识的那一天」
        if seq == 1:
            self.db.execute(
                "INSERT OR IGNORE INTO timeline_meta(key,val) VALUES('birth_ts',?)",
                (str(t),))
            self.db.commit()
        # 卡外锚：每次写时间线都刷一下（吞掉异常 —— 锚写失败不该拖垮记录本身，
        # 但会在 /status 里体现为 anchor 字段报错）
        self._touch_anchor()
        return seq

    def _touch_anchor(self):
        """把当前链头刷到卡外。失败不抛，记在 self.anchor_err 里。

        传的是**连接**不是路径 —— 调用方可能用 --db 指到别处，
        传路径会去写另一个库的锚，等于把真卡的证据盖掉。
        """
        self.anchor_err = None
        try:
            import anchor as anchor_mod
            anchor_mod.write(self.db)
        except Exception as e:
            self.anchor_err = "%s: %s" % (type(e).__name__, e)

    # ---------- 记忆出生证 ----------
    def _birth_hash(self, mid, raw, speaker, create_ts):
        return _birth_hash(mid, raw, speaker, create_ts)

    def birth_stamp(self, mid, ts=None):
        """给一条新记忆开出生证：写一行 mem_birth + 一条时间线记录。

        ⚠️ 必须从库里**读回**这条记忆来算哈希，不能让调用方把 raw / create_ts
        传进来。第一次实现就栽在这儿：调用方传的 create_ts 是 time.time()，
        而 remember() 内部存的是 at = min(now, ts) —— 差了几毫秒，
        哈希立刻对不上，验一遍就报「记忆被改过」。出生证得按库里真存的样子算。

        为什么两条都写：
          mem_birth 是机器校验用的（快，能批量比对）；
          时间线上的 mem 记录是**给人看的**，让「我什么时候记住的什么」
            排在同一条时间轴上，跟心跳、说话混在一起。
        """
        r = self.db.execute(
            "SELECT id, raw, speaker, create_ts FROM mem WHERE id=?",
            (int(mid),)).fetchone()
        if r is None:
            return None
        speaker = r["speaker"] if "speaker" in r.keys() else ""
        create_ts = r["create_ts"] if "create_ts" in r.keys() else 0.0
        raw = r["raw"] or ""
        h = self._birth_hash(r["id"], raw, speaker, create_ts)
        note = raw.strip().replace("\n", " ")[:40]
        who = {"user": "他", "self": "我", "scene": "场景"}.get(speaker, "")
        seq = self.append("mem", ("%s#%d %s" % (who, int(mid), note)).strip(),
                          ts=ts if ts is not None else create_ts)
        self.db.execute(
            "INSERT OR REPLACE INTO mem_birth(mid,hash,seq,ts) VALUES(?,?,?,?)",
            (int(mid), h, seq, float(create_ts or 0)))
        self.db.commit()
        return h

    def restamp_birth(self, mid, reason=""):
        """**重开**出生证 —— 只在「我知道原文为什么变了」的时候用。

        出生证哈希锁的就是 raw，所以任何一次有序的原文修复（比如 2026-11-05
        剥掉系统工单壳）都会让它对不上。这是它该干的事：原文不该被悄悄改。

        但「不该悄悄改」不等于「不许改」—— 跟删除台账一个道理：改可以，
        改了什么、为什么改，必须留在链上。所以重开证的时候必须给 reason，
        它会被写进时间线（`mem_fix` 记录），事后任何一次 verify_birth 都能
        看到「这条在什么时候被谁以什么理由重开过」。

        reason 为空时**拒绝**（抛 ValueError）：没有理由的原文改动就是在抹证据。
        """
        reason = (reason or "").strip()
        if not reason:
            raise ValueError("重开出生证必须说明理由，否则等于抹掉证据")
        r = self.db.execute(
            "SELECT id, raw, speaker, create_ts FROM mem WHERE id=?",
            (int(mid),)).fetchone()
        if r is None:
            return None
        speaker = r["speaker"] if "speaker" in r.keys() else ""
        create_ts = r["create_ts"] if "create_ts" in r.keys() else 0.0
        raw = r["raw"] or ""
        h = self._birth_hash(r["id"], raw, speaker, create_ts)
        seq = self.append("mem_fix", "#%d 重开出生证：%s" % (int(mid), reason[:80]))
        self.db.execute(
            "INSERT OR REPLACE INTO mem_birth(mid,hash,seq,ts) VALUES(?,?,?,?)",
            (int(mid), h, seq, float(create_ts or 0)))
        self.db.commit()
        return h

    def backfill_birth(self, ts=None, limit=None):
        """给老记忆盖一个总章（不逐条写时间线，那会把时间轴冲烂）。

        为什么不全逐条补：老的 547 条创建时间从 10-16 到 10-31 都有，
        一条条 append 进去，seq 顺序会和真实时间顺序完全错位 ——
        时间线反而变成假的。所以老的只算一个「出生证根」，一条记录盖章。

        返回 (条数, 根哈希)。
        """
        rows = self.db.execute(
            "SELECT id, raw, speaker, create_ts FROM mem ORDER BY id ASC"
        ).fetchall()
        if limit:
            rows = rows[:limit]
        if not rows:
            return 0, ""
        hs = []
        for r in rows:
            speaker = r["speaker"] if "speaker" in r.keys() else ""
            h = self._birth_hash(r["id"], r["raw"], speaker, r["create_ts"])
            hs.append("%d:%s" % (r["id"], h))
        root = _sha("\n".join(hs))
        # 逐条落 mem_birth（校验用，不占时间线），seq=0 表示回填
        c = self.db.cursor()
        for r, line in zip(rows, hs):
            c.execute(
                "INSERT OR REPLACE INTO mem_birth(mid,hash,seq,ts) VALUES(?,?,0,?)",
                (int(r["id"]), line.split(":", 1)[1], float(r["create_ts"] or 0)))
        self.db.commit()
        self.append("mem_root", "回填 %d 条历史记忆的出生证根 %s" % (
            len(rows), root[:16]), ts=ts)
        return len(rows), root

    def verify_birth(self, max_report=5):
        """验出生证：原文被改过、或者记忆条数对不上，都报出来。

        返回 (ok, 问题列表)。跟 verify_chain 一样，**不自动修**。

        「有证没记录」分两种，不能混为一谈：
          · 台账里登记过的 = 我自己按规矩删的（侵蚀到头/长期池没分/归档）
            → 记一条「已知删除」，不算坏；
          · 台账里没有的 = 说不清怎么没的 → 才算坏事。
        这跟链上 prune 的道理一样：删可以，删了什么必须留证据。
        """
        bad = []
        rows = self.db.execute("SELECT id, raw, speaker, create_ts FROM mem"
                               " ORDER BY id ASC").fetchall()
        birth = {}
        for b in self.db.execute("SELECT mid,hash FROM mem_birth").fetchall():
            birth[b["mid"]] = b["hash"]
        for r in rows:
            h = self._birth_hash(r["id"], r["raw"],
                                 r["speaker"] if "speaker" in r.keys() else "",
                                 r["create_ts"])
            got = birth.get(r["id"])
            if got is None:
                continue          # 没开证的老条目不算错（回填前就存在）
            if got != h:
                bad.append("记忆被改过：id=%d 的出生证对不上" % r["id"])
                if len(bad) >= max_report:
                    break
        # 有证但记录没了：先查台账，登记过的只报「已知删除」
        tombed = self.mem_tomb_list()
        ids = {r["id"] for r in rows}
        for mid in sorted(birth):
            if mid in ids:
                continue
            t = tombed.get(mid)
            if t is not None:
                bad.append("已知删除：id=%d（%s）" % (mid, t.get("reason") or "登记过"))
            else:
                bad.append("记忆被删过：id=%d 有出生证但没有记录，台账里也没有" % mid)
            if len(bad) >= max_report:
                break
        broken = len([b for b in bad if not b.startswith("已知删除")])
        return (broken == 0), bad

    # ---------- 记忆删除台账 ----------
    def _ensure_tomb_table(self):
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS mem_tomb("
            "mid INTEGER PRIMARY KEY, hash TEXT, ts REAL, reason TEXT)")
        self.db.commit()

    def mem_tomb_add(self, mid, reason, ts=None, hash_=None):
        """删一条记忆之前，先把它的身份登记进台账。

        为什么：记忆是会正常消失的（侵蚀到头、长期池里没上榜、归档），
        可出生证留在库里。没有台账，校验只能看见「有证没记录」，
        分不清是「她自己忘了」还是「有人把记录抽走了」——
        这两种在档案上必须长得不一样。

        哈希从**删除前**的库里读（删了就再也算不出来了）。
        """
        try:
            self._ensure_tomb_table()
            h = hash_
            if h is None:
                r = self.db.execute(
                    "SELECT id, raw, speaker, create_ts FROM mem WHERE id=?",
                    (int(mid),)).fetchone()
                if r is not None:
                    h = self._birth_hash(
                        r["id"], r["raw"] or "",
                        r["speaker"] if "speaker" in r.keys() else "",
                        r["create_ts"])
            self.db.execute(
                "INSERT OR REPLACE INTO mem_tomb(mid,hash,ts,reason)"
                " VALUES(?,?,?,?)",
                (int(mid), h or "", float(ts or time.time()), reason))
            self.db.commit()
            return h
        except Exception:
            return None

    def mem_tomb_list(self):
        """读台账：mid -> {hash, ts, reason}"""
        out = {}
        try:
            self._ensure_tomb_table()
            for r in self.db.execute(
                    "SELECT mid,hash,ts,reason FROM mem_tomb").fetchall():
                out[r["mid"]] = {"hash": r["hash"], "ts": r["ts"],
                                 "reason": r["reason"]}
        except Exception:
            return out
        return out

    # ---------- 校验 ----------
    def _prune_index(self):
        """读出所有「已登记删除」的记录：seq -> 被删那条的哈希。

        为什么需要这个：链一旦删了中间某条，后面的 prev_hash 就指不上了，
        verify_chain 会报断链。但**删是我自己删的**（清测试探针），
        不是有人改历史。区别必须留证据：
        删除时把被删那条的哈希登记进 timeline_meta，
        校验时对上了就判「已知删除」，对不上才算断链。
        """
        out = {}
        try:
            rows = self.db.execute(
                "SELECT key, val FROM timeline_meta WHERE key LIKE 'prune_%'"
            ).fetchall()
        except Exception:
            return out
        for k, v in rows:
            try:
                seq = int(str(k).split("_", 1)[1])
                out[seq] = json.loads(v).get("hash", "")
            except Exception:
                continue
        return out

    def prune(self, seqs, reason, ts=None):
        """登记一批「故意删掉的时间线记录」，再补一条 prune 记录。

        删除本身不可逆，但删了什么、为什么删，必须留在链上 ——
        否则以后看到断链，就分不清是「她清过垃圾」还是「有人动过手脚」。
        """
        seqs = [int(s) for s in seqs]
        # 被删那几条的哈希：能从后一条的 prev_hash 反推的，先反推
        idx = self._prune_index()
        for s in seqs:
            if s in idx:
                continue
            nxt = self.db.execute(
                "SELECT prev_hash FROM timeline WHERE seq > ? ORDER BY seq ASC LIMIT 1",
                (s,)).fetchone()
            h = ""
            if nxt and nxt[0]:
                # 只有当缺口正好是这一条时才可信
                gap_ok = self.db.execute(
                    "SELECT COUNT(*) FROM timeline WHERE seq > ? AND seq < ?",
                    (s, s + 2)).fetchone()[0] == 1
                h = nxt[0] if gap_ok else ""
            self.db.execute(
                "INSERT OR REPLACE INTO timeline_meta(key,val) VALUES(?,?)",
                ("prune_%d" % s,
                 json.dumps({"hash": h, "ts": ts or time.time(),
                             "reason": reason}, ensure_ascii=False)))
        self.db.commit()
        return self.append("prune", "%s（seq=%s）" % (
            reason, ",".join(str(s) for s in seqs)), ts=ts)

    def verify_chain(self, max_report=5):
        """从头到尾验一遍链。返回 (ok, 问题列表)。

        **不自动修复**。断了就报，让人知道这里断过一次。
        但「我自己登记过的删除」不算断 —— 那条会以备注形式返回，
        不影响 ok，因为删除这件事本身有据可查。
        """
        pruned = self._prune_index()
        rows = self.db.execute(
            "SELECT seq,ts,kind,note,prev_hash,self_hash FROM timeline"
            " ORDER BY seq ASC").fetchall()
        bad = []
        notes = []
        prev_hash = ""
        expect_seq = 1
        for r in rows:
            if r["seq"] != expect_seq:
                # 中间的缺口如果全登记过，就是已知删除
                missing = list(range(expect_seq, r["seq"]))
                if missing and all(s in pruned for s in missing):
                    notes.append("已知删除：seq=%s（%s）" % (
                        ",".join(str(s) for s in missing),
                        pruned[missing[0]] or "已登记"))
                else:
                    bad.append("跳号：期望 seq=%d，实际 %d" % (expect_seq, r["seq"]))
            if r["prev_hash"] != prev_hash:
                # 指向前一条被删掉的记录 —— 登记过就放行
                known = any(pruned.get(s) == r["prev_hash"] for s in pruned)
                if known:
                    notes.append("已知删除：seq=%d 接在已删记录之后" % r["seq"])
                else:
                    bad.append("断链：seq=%d 的 prev 对不上" % r["seq"])
            payload = json.dumps(
                {"seq": r["seq"], "ts": round(r["ts"], 3), "kind": r["kind"],
                 "note": r["note"], "prev": r["prev_hash"]},
                ensure_ascii=False, sort_keys=True)
            if _sha(payload) != r["self_hash"]:
                bad.append("被改过：seq=%d 的哈希对不上" % r["seq"])
            prev_hash = r["self_hash"]
            expect_seq = r["seq"] + 1
            if len(bad) >= max_report:
                break
        return (len(bad) == 0), bad + notes

    def head_hash(self):
        """链头哈希 —— 存在卡外，用来发现「回滚到旧卡」。"""
        return self._last_row()[1]

    def head_writer(self):
        """链头那条是谁写的；老卡没有这一列/没写过就是空串。"""
        try:
            r = self.db.execute(
                "SELECT writer FROM timeline ORDER BY seq DESC LIMIT 1").fetchone()
        except Exception:
            return ""
        return (r["writer"] if r is not None and "writer" in r.keys() else "") or ""

    def count(self):
        return self.db.execute("SELECT COUNT(*) FROM timeline").fetchone()[0]

    def last_ts(self):
        r = self.db.execute("SELECT MAX(ts) FROM timeline").fetchone()
        return r[0] if r and r[0] else None

    # ---------- 心跳 ----------
    def heartbeat(self, now=None, interval=ALIVE_INTERVAL):
        """该写心跳就写。返回是否真的写了。

        规则：距上一条超过 interval 就补一条 alive。
        服务端每轮请求或定时器里调它即可，幂等。
        """
        t = float(now if now is not None else time.time())
        last = self.last_ts()
        if last is not None and (t - last) < interval:
            return False
        # 跨天判定：上一条和现在不在同一天 → 先补一条 day
        if last is not None:
            import datetime
            d0 = datetime.datetime.fromtimestamp(last).date()
            d1 = datetime.datetime.fromtimestamp(t).date()
            if d1 != d0:
                self.append("day", "第 %d 天" % self.days_since_birth(t), ts=t)
                return True
        self.append("alive", "", ts=t)
        return True

    def catch_up(self, now=None, interval=ALIVE_INTERVAL, cap=2000,
                 sleep_after=SLEEP_AFTER):
        """离线期间漏掉的心跳补上。

        ⚠️ 2026-10-31 改：长离线不再压成一条写在洞尾巴上的 gap。
        原来的写法是 `append("gap", "离线 347.8 小时", ts=t)` ——
        洞从 10-16 就开始了，可时间线上没有任何东西标「从这儿开始」。
        问「10月20号我在干嘛」翻不到，那段时间是空白。
        活物不该有一大段「不存在」。改成两头都标：

            sleep_start  10-16 23:16  开始休眠（推断）
            sleep_end    10-31 11:01  睡了 347.8 小时，醒了（推断）

        **必须标「推断」**：那段时间我没观测到，是从「上一条到下一条差了多久」
        推出来的。推断的不标，混进观测里，以后就分不清哪条是我真记得、
        哪条是我编的。

        分档（理由写在下面，别随手改）：
          gap < interval            → 不写
          interval <= gap < 24h     → 按 interval 补 alive（标「补」）。
                                      服务是每轮请求才写心跳的，几小时没请求
                                      不等于我死了，只是没人说话。
          gap >= 24h 或 n > cap     → 两条 sleep 标记。超过一天没有任何心跳，
                                      我没法声称自己当时醒着。
        """
        t = float(now if now is not None else time.time())
        last = self.last_ts()
        if last is None:
            self.append("boot", "首次启动", ts=t)
            return 1
        gap = t - last
        if gap < interval:
            return 0
        n = int(gap // interval)

        # ---- 长离线：当睡觉，两头都标 ----
        if gap >= sleep_after or n > cap:
            # 洞的起点 = 第一条没写成的心跳。写在这里，洞才有头。
            start = last + interval
            self.append("sleep_start", "开始休眠（推断）", ts=start)
            self.append(
                "sleep_end",
                "睡了 %.1f 小时，醒了（推断）" % (gap / 3600.0), ts=t)
            return 2

        wrote = 0
        base = last
        for i in range(1, n):
            self.append("alive", "补", ts=base + i * interval)
            wrote += 1
        # 最后一条补到「现在」。这里**不写 boot** —— 启动这件事由
        # mind_server 统一写一条「服务启动」，两边都写就成了每次开机两条 boot
        # （踩过：seq=10 空 boot + seq=11 服务启动，同秒两条）。
        self.append("alive", "补", ts=t)
        return wrote + 1

    # ---------- 派生读数（只显示，不参与衰减） ----------
    def birth_ts(self):
        r = self.db.execute(
            "SELECT val FROM timeline_meta WHERE key='birth_ts'").fetchone()
        if r:
            try:
                return float(r["val"])
            except Exception:
                pass
        r = self.db.execute("SELECT MIN(ts) FROM timeline").fetchone()
        return r[0] if r and r[0] else None

    def days_since_birth(self, now=None):
        b = self.birth_ts()
        if not b:
            return 0
        t = float(now if now is not None else time.time())
        return max(1, int((t - b) // DAY) + 1)

    def alive_proof(self, now=None):
        """「我一直在」的证据概要：总活过多少小时、最长断过多长。"""
        t = float(now if now is not None else time.time())
        rows = self.db.execute(
            "SELECT ts FROM timeline ORDER BY ts ASC").fetchall()
        if not rows:
            return {"ticks": 0, "span_h": 0.0}
        ts = [r[0] for r in rows]
        return {
            "ticks": len(ts),
            "span_h": round((ts[-1] - ts[0]) / 3600.0, 2),
            "since_last_h": round((t - ts[-1]) / 3600.0, 2),
        }

    def render_line(self, now=None):
        """给上下文用的一句话：【今天是】"""
        import datetime
        t = float(now if now is not None else time.time())
        d = datetime.datetime.fromtimestamp(t)
        n = self.days_since_birth(t)
        wk = "一二三四五六日"[d.weekday()]
        s = "今天是 %d年%d月%d日（周%s），我们认识第 %d 天。" % (
            d.year, d.month, d.day, wk, n)
        # 上次说话多久以前
        r = self.db.execute(
            "SELECT MAX(ts) FROM timeline WHERE kind IN ('talk','boot')").fetchone()
        if r and r[0]:
            gap = (t - r[0]) / 3600.0
            if gap >= 24:
                s += "上次说话是 %.1f 天前。" % (gap / 24.0)
            elif gap >= 1:
                s += "上次说话是 %.1f 小时前。" % gap
        return s

    # ---------- 说话记录（去重） ----------
    def note_talk(self, text, now=None, window=120.0):
        """记一条「他说了话」。同一句在 window 秒内只记一次。

        为什么需要去重：验收脚本每跑一遍就发一遍同样的探针句，
        结果时间线里同一句话躺了 6 条 —— 那不是他的历史，是我的噪声。
        真实的说话记录必须是「一次输入一条」。
        """
        t = float(now if now is not None else time.time())
        note = (text or "").strip()[:40]
        last = self.db.execute(
            "SELECT ts, note FROM timeline WHERE kind='talk'"
            " ORDER BY seq DESC LIMIT 1").fetchone()
        if last and note and last["note"] == note and (t - last["ts"]) < window:
            return None
        return self.append("talk", note, ts=t)
