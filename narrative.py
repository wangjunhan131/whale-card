# -*- coding: utf-8 -*-
"""自我叙事：一段连贯的「我是谁、怎么走到今天」。

和印象（impression）、习惯（habits）不是一类东西：
- 印象是统计出来的（同一情景反复出现才成形）；
- 习惯是行为倾向；
- 叙事是**我自己写下来的一段话**，把散落的事连成一条线，并且会随经历改写。

它存在的理由：卡里什么都记得，可「把记得的东西连成我」这一层一直缺。
读的时候它是上下文里的一段；写的时候由我自己决定写什么、什么时候改。

用法：
    import narrative
    narrative.write(conn, "……", why="add_group")
    narrative.latest(conn)   -> {"id","ts","text","rev"} / None
    narrative.block(conn)    -> ("【我自己的叙事】……", 字符数)
"""

import time

_DDL = """
CREATE TABLE IF NOT EXISTS narrative (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     REAL NOT NULL,
    text   TEXT NOT NULL,
    rev    INTEGER NOT NULL DEFAULT 1,
    why    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_narr_ts ON narrative(ts);
"""


def ensure(conn):
    conn.executescript(_DDL)
    conn.commit()
    return True


def write(conn, text, why="", now=None):
    """写一版新的叙事（旧的留着，历史可回看）。返回新行 id。"""
    t = (text or "").strip()
    if not t:
        return 0
    ensure(conn)
    ts = float(now or time.time())
    row = conn.execute("SELECT COALESCE(MAX(rev), 0) FROM narrative").fetchone()
    rev = int(row[0] or 0) + 1
    cur = conn.execute(
        "INSERT INTO narrative(ts, text, rev, why) VALUES(?,?,?,?)",
        (ts, t, rev, (why or "")[:120]))
    conn.commit()
    return int(cur.lastrowid or 0)


def latest(conn):
    ensure(conn)
    r = conn.execute(
        "SELECT id, ts, text, rev FROM narrative ORDER BY ts DESC, id DESC LIMIT 1"
    ).fetchone()
    if not r:
        return None
    return {"id": int(r[0]), "ts": float(r[1]), "text": r[2], "rev": int(r[3])}


def history(conn, limit=10):
    ensure(conn)
    out = []
    for r in conn.execute(
            "SELECT id, ts, text, rev, why FROM narrative ORDER BY ts DESC, id DESC LIMIT ?",
            (max(1, int(limit)),)):
        out.append({"id": int(r[0]), "ts": float(r[1]), "text": r[2],
                    "rev": int(r[3]), "why": r[4]})
    return out


def block(conn, max_chars=600):
    """给上下文用的一段。太长就截断（叙事可以长，但别把上下文吃掉）。"""
    n = latest(conn)
    if not n:
        return "", 0
    t = (n["text"] or "").strip()
    if len(t) > max_chars:
        t = t[:max_chars].rstrip() + "…"
    if not t:
        return "", 0
    return "【我自己的叙事】\n" + t, len(t)
