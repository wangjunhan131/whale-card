# -*- coding: utf-8 -*-
"""V7-P1：把「搬家」这件事写进时间线账本，并且把 migrate / fork 分成两件事。

为什么要有这个文件：
  · 以前搬完家，时间线上什么都没有 —— 三个月后翻账本，只能看到一串 boot，
    「这张卡是不是被搬过、从哪儿搬来的、有没有被复制成两张」全都查不出来。
  · 搬家有两种，后果完全不同：
      migrate（迁移）= 同一张卡换个地方活着，原处要**退役**（再启动就该只读）；
      fork（分叉）  = 从这张卡分出一张新卡，原处照旧活着，新的是**另一个身份**。
    文件里、UI 里、报告里都不能含糊成「搬了一下」。

规矩（写给以后手滑的自己）：
  1. 事件只往**已有的卡库**里写，绝不新建库；库不在就报错。
  2. 写完立刻用只读连接把哈希链整条复核一遍（复用 disks._bundle_chain_problems，
     它跟 timeline.Timeline.verify_chain 是同一个公式，而且**不实例化 Timeline**
     —— 那个 __init__ 会 CREATE/ALTER，等于写库）。
  3. 链断了：先看是不是**写之前**就断的。不是，就把刚写的那条删掉再报错 ——
     宁可少一条记录，也不要留一条把链弄断的记录。删不掉就明说「需要人处理」。
  4. note 是「人话｜JSON」两段：控制台左栏看得懂（它会把冒号前缀去掉再截 26 字），
     程序 rsplit('｜') 之后 json.loads 也取得出全部字段。
"""

import json
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import disks as _disks          # noqa: E402  (要先把 ROOT 塞进 sys.path)
import timeline as _timeline    # noqa: E402

SCHEMA = "migration/1"

KIND_STARTED = "migration.started"
KIND_COMPLETED = "migration.completed"
KIND_FAILED = "migration.failed"
IDENT_TRANSFERRED = "identity.transferred"
IDENT_FORKED = "identity.forked"

EVENT_KINDS = (KIND_STARTED, KIND_COMPLETED, KIND_FAILED,
               IDENT_TRANSFERRED, IDENT_FORKED)

MODE_MIGRATE = "migrate"
MODE_FORK = "fork"
MODES = (MODE_MIGRATE, MODE_FORK)

# 两种模式说人话的解释。cmd_land 参数错的时候原样打出来，
# 别让用户在「到底哪个才算搬家」上猜。
MODE_HELP = (
    "--migrate  同一张卡搬到新地方：接管后源盘标成已迁出（再启动就只读），"
    "identity_id 不变，是同一个我",
    "--fork     从这张卡分出一张新卡：源盘不退休，新卡的 identity_id 是新的"
    "——这是一张新卡，不是同一个我",
)

SEP = "｜"          # 全角竖线：人话与 JSON 的分界，正常文本里不会出现


class ChainBroken(RuntimeError):
    """写完事件之后哈希链对不上。宁可炸，也不要让账本悄悄变成两截。"""

    def __init__(self, message, seq=None, problems=None, rolled_back=False,
                 still_broken=None, pre_existing=False, event=None):
        super().__init__(message)
        self.seq = seq
        self.problems = list(problems or [])
        self.rolled_back = bool(rolled_back)
        self.still_broken = still_broken          # None = 回滚后链好了
        self.pre_existing = bool(pre_existing)    # True = 写之前就已经断了
        self.event = event or {}

    def as_dict(self):
        return {"seq": self.seq,
                "problems": self.problems[:8],
                "rolled_back": self.rolled_back,
                "still_broken": (self.still_broken or [])[:8],
                "pre_existing": self.pre_existing,
                "needs_human": bool(self.pre_existing or self.still_broken)}


# ---------------------------------------------------------------- 小工具

def _clip(s, n):
    s = str(s or "").replace("\r", " ").replace("\n", " ")
    return s if len(s) <= n else s[:n] + "…"


def _jsonable(v):
    try:
        json.dumps(v, ensure_ascii=False)
        return v
    except Exception:
        return str(v)


def _human(kind, p):
    """一条事件的人话部分（控制台看的就是它）。"""
    mode = p.get("mode") or ""
    tag = "（%s）" % mode if mode else ""
    if kind == KIND_STARTED:
        return "开始迁移%s" % tag
    if kind == KIND_COMPLETED:
        return "迁移完成%s，链头 %s" % (tag, p.get("head_seq") or "?")
    if kind == KIND_FAILED:
        return "迁移失败%s：%s" % (tag, _clip(p.get("reason") or "没写原因", 60))
    if kind == IDENT_TRANSFERRED:
        who = p.get("identity_id") or p.get("instance_id") or "?"
        return "身份转移：%s（同一张卡）" % who
    if kind == IDENT_FORKED:
        parent = p.get("parent_identity_id") or p.get("parent_instance_id") or "?"
        return "身份分叉：%s → %s（新卡，不是同一个我）" % (
            parent, p.get("new_identity_id") or "?")
    return kind


def format_note(kind, **payload):
    """「人话｜JSON」。payload 里塞得进 JSON 的都会原样保留。"""
    p = {k: _jsonable(v) for k, v in payload.items() if v is not None}
    p.setdefault("schema", SCHEMA)
    body = json.dumps(p, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "%s%s%s" % (_human(kind, p), SEP, body)


def parse_note(note):
    """把 note 拆回 payload；不是这个格式（老事件）就返回 {}。"""
    s = str(note or "")
    if SEP not in s:
        return {}
    tail = s.rsplit(SEP, 1)[1].strip()
    try:
        doc = json.loads(tail)
    except Exception:
        return {}
    return doc if isinstance(doc, dict) else {}


def _conn_file(conn):
    """连接开的是哪个文件（快照库 / :memory: 会返回 ""）。"""
    try:
        for r in conn.execute("PRAGMA database_list"):
            if r[1] == "main":
                return r[2] or ""
    except Exception:
        pass
    return ""


def _db_path(db):
    if isinstance(db, sqlite3.Connection):
        return _conn_file(db)
    return os.path.abspath(str(db))


def _has_timeline(path):
    try:
        c = _disks._ro_connect(path)
    except Exception:
        return False
    try:
        r = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='timeline'"
                      ).fetchone()
        return bool(r)
    except Exception:
        return False
    finally:
        try:
            c.close()
        except Exception:
            pass


# ---------------------------------------------------------------- 只读体检

def check_chain(db):
    """只读复核整条链。db 可以是路径或连接。"""
    path = _db_path(db)
    if not path or not os.path.exists(path):
        raise FileNotFoundError("库不在：%s" % (path or "(没有文件)"))

    conn = db if isinstance(db, sqlite3.Connection) else _disks._ro_connect(path)
    close = not isinstance(db, sqlite3.Connection)
    try:
        bad, rows, pruned = _disks._bundle_chain_problems(conn, max_report=8)
        r = conn.execute(
            "SELECT seq, self_hash FROM timeline ORDER BY seq DESC LIMIT 1").fetchone()
        head_seq = int(r["seq"]) if r is not None else 0
        head_hash = (r["self_hash"] or "") if r is not None else ""
    finally:
        if close:
            try:
                conn.close()
            except Exception:
                pass
    return {"ok": not bad, "problems": [str(x) for x in (bad or [])],
            "rows": int(rows), "pruned": int(pruned),
            "head_seq": head_seq, "head_hash": head_hash}


def last_event(db):
    """链头那条是什么（自测和报告都用它）。"""
    path = _db_path(db)
    if not path or not os.path.exists(path):
        return None
    conn = db if isinstance(db, sqlite3.Connection) else _disks._ro_connect(path)
    close = not isinstance(db, sqlite3.Connection)
    try:
        r = conn.execute("SELECT seq, ts, kind, note, writer FROM timeline"
                         " ORDER BY seq DESC LIMIT 1").fetchone()
    finally:
        if close:
            try:
                conn.close()
            except Exception:
                pass
    if r is None:
        return None
    note = r["note"] or ""
    return {"seq": int(r["seq"]), "ts": r["ts"], "kind": r["kind"],
            "note": note, "writer": r["writer"] or "",
            "payload": parse_note(note)}


# ---------------------------------------------------------------- 写 + 校验 + 回滚

def _rollback_event(path, seq, kind):
    """把刚写的那条删掉（只在链因此断了的时候用）。返回 (删掉了吗, 回滚后的问题)。"""
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        r = conn.execute("SELECT self_hash FROM timeline WHERE seq=? AND kind=?",
                         (seq, kind)).fetchone()
        if r is None:
            return False, None
        conn.execute("DELETE FROM timeline WHERE seq=? AND kind=? AND self_hash=?",
                     (seq, kind, r["self_hash"]))
        conn.commit()
    finally:
        conn.close()
    after = check_chain(path)
    return True, (None if after["ok"] else after["problems"])


def append_migration_event(db_path, kind, writer=None, allow_foreign=False,
                           verify=True, **payload):
    """往卡库里追加一条迁移事件，写完只读复核哈希链。

    返回 {ok, kind, seq, note, writer, chain_ok, head_seq, head_hash, ...}。
    链断了抛 ChainBroken（能回滚就回滚，链本来断的就留着事件并要人处理）。
    """
    if kind not in EVENT_KINDS:
        raise ValueError("不是迁移事件：%r（只收 %s）"
                         % (kind, ", ".join(EVENT_KINDS)))

    given = isinstance(db_path, sqlite3.Connection)
    path = _db_path(db_path)
    if not given:
        if not path or not os.path.exists(path):
            raise FileNotFoundError("库不在，不新建：%s" % (path or db_path))
    if not _has_timeline(path):
        raise ValueError("这不是卡库（没有 timeline 表）：%s" % path)

    # 写之前先看一眼链：如果是**本来就断的**，那我们回滚自己的事件也没用，
    # 得留着它并明确报「需要人处理」，否则等于把断链的证据也删了。
    before = check_chain(path) if verify else None

    note = format_note(kind, **payload)
    w = writer or _timeline.writer_id()
    conn = db_path if given else sqlite3.connect(path, timeout=30.0)
    if not given:
        conn.row_factory = sqlite3.Row
    try:
        tl = _timeline.Timeline(conn, writer=w)
        seq = tl.append(kind, note, allow_foreign=allow_foreign)
        anchor_err = getattr(tl, "anchor_err", None)
    finally:
        if not given:
            conn.close()

    out = {"ok": True, "kind": kind, "seq": seq, "note": note, "writer": w,
           "anchor_err": anchor_err, "chain_ok": None, "chain_checked": False,
           "pre_existing_break": False}
    if not verify:
        return out

    after = check_chain(path)
    out.update({"chain_checked": True, "chain_ok": after["ok"],
                "head_seq": after["head_seq"], "head_hash": after["head_hash"],
                "rows": after["rows"]})
    if after["ok"]:
        return out

    out["problems"] = after["problems"]
    pre = bool(before and not before["ok"])
    if pre:
        out["pre_existing_break"] = True
        raise ChainBroken(
            "时间线在写这条之前就断了（%s）：事件已留在 seq=%d，但需要人处理"
            % ("；".join(after["problems"][:3]), seq),
            seq=seq, problems=after["problems"], rolled_back=False,
            still_broken=after["problems"], pre_existing=True, event=out)

    rolled, still = _rollback_event(path, seq, kind)
    out["rolled_back"] = rolled
    msg = ("写完 %s 后发现链断了（%s）；%s"
           % (kind, "；".join(after["problems"][:3]),
              "已回滚这条" if rolled and still is None
              else ("回滚了这条但链还是断的，需要人处理" if rolled
                    else "这条删不掉，需要人处理")))
    raise ChainBroken(msg, seq=seq, problems=after["problems"],
                      rolled_back=rolled, still_broken=still, event=out)


def retire_source(source_dir, dest_dir, identity_id="", why="migrate",
                  expected_instance="", live_root="", live_busy=False):
    """把源盘标成「已迁出」——真正的开关是源卡目录里的 RETIRED.json。

    mind_server 启动时读 `read_retired(卡片目录)` 决定自己是不是只读，
    所以只往包里塞一个 SOURCE-RETIRED.json 是**不管用**的，必须写源目录。

    返回 {"ok","enforced","retired","refused","note"}：
      ok=False 表示源目录不可达 / 就是目标目录本身 —— 这时候调用方要如实报告，
      不能假装退休成功。refused=True 表示身份对不上或目标是活卡根，**没有写任何东西**。

    expected_instance / live_root / live_busy 是 2026-11-11 事故后加的第二道闸：
    portable.py 已在写盘前校验过，这里再验一次，防止以后冒出别的调用方只记得传目录。
    """
    raw_src = str(source_dir or "").strip()
    # 注意：os.path.abspath("") 会返回**当前工作目录**。2026-11-11 就是这里
    # 把空 src 解析成了活卡根，才把 RETIRED.json 盖在活卡上。必须先判空。
    src = os.path.abspath(raw_src) if raw_src else ""
    dst = os.path.abspath(str(dest_dir or ""))
    out = {"source_dir": src, "enforced": False, "retired": False,
           "refused": False, "note": ""}
    if not src:
        out["note"] = "清单里没有源目录（老包），不知道原处在哪；本次不退休任何目录"
        return out
    if os.path.abspath(src) == dst:
        out["note"] = "源目录就是包目录本身，跳过退休（不能把自己锁成只读）"
        return out
    if not os.path.isdir(src):
        out["note"] = "源目录不在这台机器上（%s），没法替它写退役标记" % src
        return out

    if expected_instance:
        got = _card_identity(os.path.join(src, "card.db"), src).get("instance_id") or ""
        if not got or got != str(expected_instance):
            out["refused"] = True
            out["note"] = ("源卡 instance_id=%s 与调用方给的 %s 不一致，拒绝退休（未写任何文件）"
                           % (got or "?", expected_instance))
            return out
    if live_busy and live_root and \
            os.path.normcase(os.path.realpath(src)) == os.path.normcase(os.path.realpath(live_root)):
        out["refused"] = True
        out["note"] = "退休目标就是正在运行的活卡根，拒绝退休（未写任何文件）"
        return out

    info = {"retired": True, "by": "migrate", "identity_id": identity_id,
            "dest": dst, "why": why,
            "at": time.time(), "at_iso": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        _disks.set_retired(src, True, why="已迁出到 %s（%s）" % (dst, why))
        out["retired"] = bool(_disks.read_retired(src))
        with open(os.path.join(src, "SOURCE-RETIRED.json"), "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=1)
        out["enforced"] = True
        if not out["retired"]:
            out["note"] = "RETIRED.json 写进去了但读出来不是退役状态，去源盘看一眼"
    except Exception as e:
        out["note"] = "写源盘退役标记失败：%s" % e
    return out


def _card_identity(db_path, card_dir=None):
    try:
        i = _disks.manifest_identity(db_path, card_dir)
        return {"identity_id": i.get("identity_id") or "",
                "instance_id": i.get("instance_id") or ""}
    except Exception:
        return {"identity_id": "", "instance_id": ""}


def mint_identity(parent, instance_id=""):
    """新身份 ID：看得出是哪张卡分出来的，也看得出是新的。"""
    import re
    import uuid
    base = re.sub(r"[^0-9A-Za-z_.-]+", "", str(parent or instance_id or ""))[:20]
    return "id-%s-%s" % (base or "card", uuid.uuid4().hex[:8])


def fork_identity(bundle, new_db, parent, instance_id=""):
    """在**新卡**里落一个新身份，并留下分叉凭证。

    写两处：
      · identity.json 顶层 identity_id —— disks._identity_id 认它；
      · core_zone 里一行 identity_id —— 优先级更高，而且不会被 save_card() 重写抹掉。
    core_zone 写失败只警告：那说明这张卡的身份还停在老语义上，下次 save_card 会
    把 identity.json 一起重写，分叉真相就只剩 IDENTITY-FORK.json 了。
    """
    new_id = mint_identity(parent, instance_id)
    out = {"identity_event": IDENT_FORKED, "parent_identity_id": parent or "",
           "parent_instance_id": instance_id or "", "new_identity_id": new_id,
           "warnings": []}

    ip = os.path.join(bundle, "identity.json")
    doc = {}
    if os.path.exists(ip):
        try:
            with open(ip, encoding="utf-8") as f:
                doc = json.load(f) or {}
        except Exception as e:
            out["warnings"].append("identity.json 读不出来（%s），按新文件写" % e)
            doc = {}
    if not isinstance(doc, dict):
        doc = {}
    doc["identity_id"] = new_id
    doc["forked_from"] = parent or ""
    doc["forked_at"] = time.time()
    doc["_fork_note"] = ("这张卡是从另一张卡分出来的（fork）：identity_id 是新的，"
                         "不是同一个我。来历见 IDENTITY-FORK.json")
    try:
        tmp = ip + ".tmp-fork"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        os.replace(tmp, ip)
        out["identity_json"] = ip
    except Exception as e:
        out["warnings"].append("写 identity.json 失败：%s" % e)
        out["identity_json"] = None

    try:
        conn = sqlite3.connect(new_db, timeout=30.0)
        try:
            conn.execute("INSERT OR REPLACE INTO core_zone(key, val, ts) VALUES(?,?,?)",
                         ("identity_id", new_id, time.time()))
            conn.commit()
            out["core_zone"] = True
        finally:
            conn.close()
    except Exception as e:
        out["core_zone"] = False
        out["warnings"].append(
            "core_zone 里没写进新身份（%s）：卡的身份字段还是老语义，"
            "分叉真相以 IDENTITY-FORK.json 为准" % e)

    fork_doc = {"schema": "identity-fork/1", "mode": MODE_FORK,
                "parent_identity_id": parent or "",
                "parent_instance_id": instance_id or "",
                "new_identity_id": new_id,
                "from": None, "to": bundle,
                "forked_at": time.time(),
                "forked_at_iso": time.strftime("%Y-%m-%d %H:%M:%S"),
                "note": "这是一张新卡，不是同一个我：源卡照旧活着，两边从此各写各的链"}
    try:
        _atomic_json(os.path.join(bundle, "IDENTITY-FORK.json"), fork_doc)
        out["fork_record"] = os.path.join(bundle, "IDENTITY-FORK.json")
    except Exception as e:
        out["warnings"].append("写 IDENTITY-FORK.json 失败：%s" % e)
        out["fork_record"] = None
    return out


def _atomic_json(path, obj):
    tmp = "%s.tmp-%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
