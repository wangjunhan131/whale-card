# -*- coding: utf-8 -*-
"""承诺账本（旁路原型，未接进生产 import）。

为什么单独一个模块：卡里现在有「记得发生过什么」（mem / timeline / fact），
也有「最近 72 小时发生过什么」（card2._l0_lines），但**没有「说好了还没做」**。
于是「明天一起弄完」这句话要么被当普通记忆存着（以后永远想不起来它是欠账），
要么根本没人管 —— 到了第二天，她既不记得答应了什么，也没有任何"这人还没来"的落差。

这里只做一件事：一张独立的表 + 四五个函数。
    · 不进 mem 池，不参与召回打分、不参与衰减 —— 它不是记忆，是账本。
    · 不碰 card.db 里任何已有的表；表名 promise，副作用为零。
    · 没有承诺时 promise_block() 返回空串 —— 零开销、零上下文占用。

状态机（比 SYNAPSE 的事件表窄，够用就行）：
    open ──(到点)──▶ due ──(确认做了)──▶ kept
                      │
                      ├──(确认没做/过了宽限)──▶ broken
                      └──(不弄了)────────────▶ cancelled
    open ──(不弄了)──▶ cancelled

为什么不照搬 timeline_store 的 pending/done/expired：
    它把「到期」和「失约」塞进同一个 expired，等于"到点就判人失约"。
    我们的场景里到点和失约之间常隔着几小时（他在学校、在写作业），
    先标 due、再由结算那一步判 broken，才不至于一过点就甩脸。

回滚：环境变量 WHALE_PROMISE=0 时 promise_block() 一律返回空串，
      写入函数仍然可用（不影响排查），只是不再往上下文里塞东西。
"""
from __future__ import annotations

import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

# 表名、状态集合
TABLE = "promise"
STATES = ("open", "due", "kept", "broken", "cancelled")
TERMINAL = ("kept", "broken", "cancelled")
# 谁许下的：user=他、self=我、other=别人
WHO = ("user", "self", "other")

# 到期时间写 0 表示「没有明确期限」，这种账永远不进 due（想起就想起，不催）
NO_DUE = 0.0

DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "card.db")
ENV_DB = "WHALE_PROMISE_DB"      # 测试/副本用：指到另一张卡
ENV_OFF = "WHALE_PROMISE"        # 回滚开关：=0 就不往上下文里塞

_DDL = """
CREATE TABLE IF NOT EXISTS promise (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL NOT NULL DEFAULT 0,      -- 什么时候许下的
    who       TEXT NOT NULL DEFAULT 'user', -- user / self / other
    what      TEXT NOT NULL DEFAULT '',     -- 承诺内容（原话，一句话）
    due_ts    REAL NOT NULL DEFAULT 0,      -- 到期时刻；0 = 没有明确期限
    state     TEXT NOT NULL DEFAULT 'open', -- open/due/kept/broken/cancelled
    closed_ts REAL NOT NULL DEFAULT 0,      -- 结清时刻；0 = 还没结清
    note      TEXT NOT NULL DEFAULT ''      -- 怎么结的 / 为什么取消
)"""
_DDL_IX = "CREATE INDEX IF NOT EXISTS ix_promise_state ON promise(state, due_ts)"


# ---------------------------------------------------------------- 基础设施
def enabled() -> bool:
    """回滚开关。=0 时上下文里不出现承诺块；账本本身照常读写。"""
    return (os.environ.get(ENV_OFF, "1") or "").strip() != "0"


def db_path(db: Optional[str] = None) -> str:
    return db or os.environ.get(ENV_DB) or DEFAULT_DB


def connect(db: Optional[str] = None, create: bool = True):
    """开连接。create=False 时库不存在就返回 None（读路径不该顺手造一个空库）。"""
    path = db_path(db)
    if not create and not os.path.exists(path):
        return None
    conn = sqlite3.connect(path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    if create:
        ensure(conn)
    return conn


def ensure(conn) -> None:
    conn.execute(_DDL)
    conn.execute(_DDL_IX)
    conn.commit()


def _rows(cur) -> List[Dict[str, Any]]:
    return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------- 写
def add_promise(what: str, due_ts: float = NO_DUE, who: str = "user",
                note: str = "", ts: Optional[float] = None,
                db: Optional[str] = None, conn=None) -> int:
    """记一笔承诺。返回 id。

    what    一句话的承诺内容（"一起把那个作业弄完"）
    due_ts  到期时刻；0 = 没有明确期限（这种不会自己变成 due）
    who     user（他许的）/ self（我许的）/ other
    ts      许下的时刻，默认现在
    """
    what = (what or "").strip()
    if not what:
        raise ValueError("promise: what 不能为空")
    who = (who or "user").strip()
    if who not in WHO:
        who = "other"
    own = conn is None
    conn = conn or connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO promise(ts,who,what,due_ts,state,closed_ts,note)"
            " VALUES(?,?,?,?,'open',0,?)",
            (float(ts if ts is not None else time.time()),
             who, what, float(due_ts or NO_DUE), note or ""))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        if own:
            conn.close()


def close_promise(pid: int, state: str, note: str = "",
                  now: Optional[float] = None, db: Optional[str] = None,
                  conn=None) -> bool:
    """结清一笔账。state 只能是 kept / broken / cancelled。

    note 留空就保留原来的备注（不覆盖写）。
    """
    state = (state or "").strip()
    if state not in TERMINAL:
        raise ValueError("promise: 结清状态只能是 %s，收到 %r"
                         % ("/".join(TERMINAL), state))
    own = conn is None
    conn = conn or connect(db)
    try:
        row = conn.execute("SELECT id,note FROM promise WHERE id=?",
                           (int(pid),)).fetchone()
        if not row:
            return False
        note_out = note if note else (row["note"] or "")
        cur = conn.execute(
            "UPDATE promise SET state=?, closed_ts=?, note=? WHERE id=?",
            (state, float(now if now is not None else time.time()),
             note_out, int(pid)))
        conn.commit()
        return cur.rowcount > 0
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------- 读
def due_promises(now: Optional[float] = None, limit: int = 20,
                 grace_hours: Optional[float] = None,
                 db: Optional[str] = None, conn=None) -> List[Dict[str, Any]]:
    """到期还没结清的账，按到期时间从早到晚。

    顺手做两件事（幂等，重复调用不会错）：
      1. open 且 due_ts<=now → 标成 due（到点了，但还没判失约）
      2. grace_hours 不为 None 时，due 且超过宽限 → 标成 broken

    为什么默认 grace_hours=None：到点就判失约太狠。真实场景里"晚上八点一起弄"
    九点才回消息是很正常的事，这段宽限该由调用方（或情绪结算）决定，不该写死。
    """
    now = float(now if now is not None else time.time())
    own = conn is None
    conn = conn or connect(db)
    try:
        conn.execute(
            "UPDATE promise SET state='due'"
            " WHERE state='open' AND due_ts>0 AND due_ts<=?", (now,))
        if grace_hours is not None and grace_hours >= 0:
            cut = now - float(grace_hours) * 3600.0
            conn.execute(
                "UPDATE promise SET state='broken', closed_ts=?"
                " WHERE state='due' AND due_ts>0 AND due_ts<=?", (now, cut))
        conn.commit()
        rows = conn.execute(
            "SELECT * FROM promise"
            " WHERE state IN ('open','due') AND due_ts>0 AND due_ts<=?"
            " ORDER BY due_ts ASC, id ASC LIMIT ?", (now, int(limit))).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        # 表还没建（旧库 / 只读副本）—— 读路径安静返回空，别在上下文里抛栈
        return []
    finally:
        if own:
            conn.close()


def open_promises(now: Optional[float] = None, limit: int = 20,
                  db: Optional[str] = None, conn=None) -> List[Dict[str, Any]]:
    """所有还没结清的账（含未到期）。排查用，不进上下文。"""
    own = conn is None
    conn = conn or connect(db)
    try:
        rows = conn.execute(
            "SELECT * FROM promise WHERE state IN ('open','due')"
            " ORDER BY due_ts ASC, id ASC LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------- 给上下文用
def _fmt_ts(ts: float) -> str:
    try:
        t = time.localtime(float(ts))
    except (ValueError, OverflowError, OSError):
        return ""
    return "%d月%02d日 %02d:%02d" % (t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min)


def _late_text(due_ts: float, now: float) -> str:
    late_h = max(0.0, (now - float(due_ts)) / 3600.0)
    if late_h < 1.0:
        return "刚到点"
    if late_h < 24.0:
        return "过了 %d 小时" % int(round(late_h))
    return "过了 %d 天" % int(round(late_h / 24.0))


def promise_block(now: Optional[float] = None, limit: int = 3,
                  max_chars: int = 220, db: Optional[str] = None,
                  conn=None) -> str:
    """给上下文拼一小段【没结清的事】。没有到期的事就返回 ""（零开销）。

    形状：
        【没结清的事】
        - 11月10日 20:00｜一起把那个作业弄完（过了 15 小时）
        （别讨债：只影响语气，不要复述「你说好要…」）

    🔴 最后那行不是装饰。总文档的铁律是「计划落差只通过语气/话量/停顿渗透」，
       模型看见一张欠账清单，第一反应就是把清单念出来（"你说好要一起弄的"）。
       把禁令写在同一个块里，比写在别处更容易被遵守。
    """
    if not enabled():
        return ""
    now = float(now if now is not None else time.time())
    try:
        rows = due_promises(now=now, limit=max(1, int(limit)), db=db, conn=conn)
    except Exception:
        return ""
    if not rows:
        return ""

    lines, used = [], 0
    for r in rows:
        head = _fmt_ts(r.get("due_ts") or 0)
        what = (r.get("what") or "").strip()
        if not what:
            continue
        tag = _late_text(r.get("due_ts") or 0, now)
        line = "- %s｜%s（%s）" % (head, what, tag) if head else "- %s（%s）" % (what, tag)
        if used + len(line) > max_chars:
            break
        lines.append(line)
        used += len(line)
    if not lines:
        return ""
    return ("【没结清的事】\n" + "\n".join(lines)
            + "\n（别讨债：只影响语气，不要复述「你说好要…」）")


__all__ = [
    "TABLE", "STATES", "TERMINAL", "WHO", "NO_DUE",
    "enabled", "db_path", "connect", "ensure",
    "add_promise", "close_promise",
    "due_promises", "open_promises", "promise_block",
]


if __name__ == "__main__":       # 手工看一眼：python promises.py
    print("db =", db_path(), "| WHALE_PROMISE =", os.environ.get(ENV_OFF, "(unset)"))
    print("enabled =", enabled())
    _n = time.time()
    _due = due_promises(now=_n)
    print("due   =", len(_due))
    for _r in _due:
        print("   ", _r["id"], _r["what"], _fmt_ts(_r["due_ts"]))
    _blk = promise_block(now=_n)
    print("block =", repr(_blk) if _blk else "(空)")
