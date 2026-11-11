# -*- coding: utf-8 -*-
"""backfill_dialogue.py —— 把「原文表停更」那段时间的原文补回去（一次性）。

为什么会有这个空洞（2026-11-09 查出来）：
  dialogue 的唯一写入口 add_dialogue 里那句 INSERT 把 source 写成了 None，
  而 source 列是 NOT NULL —— 每次调用都抛 IntegrityError，调用方却
  `except Exception: pass` 吞掉。于是从 10 月底起：
    · 「最近对话」翻原话，翻到的是十天前的；
    · 习惯挖掘读的正是 dialogue，挖到的是十月底的口癖；
    · 梦层 pending 永远是 0。

补法：拿 mem 里那条记忆的原文（mem.text）回填一条 dialogue，
source 统一写 "backfill"，方便一眼认出来是补的、也能一键删掉：
    DELETE FROM dialogue WHERE source='backfill';

用法：
  python tools\\backfill_dialogue.py --db card.db            # 看会补多少（不写）
  python tools\\backfill_dialogue.py --db card.db --apply    # 真写
"""
import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def collect(con, since=None):
    """返回要补的行：[(mem_id, ts, role, text)]

    since：只补这个时间点之后的（默认 = 最后一条 dialogue 的时间）。
    不设的话会把卡里最早那些「思考过程用中文」之类的指令也当成对话补进去，
    那不是她说过的话，是配置。
    """
    if since is None:
        row = con.execute("SELECT MAX(ts) FROM dialogue").fetchone()
        since = float((row[0] if row else 0) or 0)
    have = {r[0] for r in con.execute(
        "SELECT DISTINCT mem_id FROM dialogue WHERE mem_id IS NOT NULL")}
    rows = con.execute(
        "SELECT id, create_ts, speaker, raw FROM mem"
        " WHERE raw IS NOT NULL AND raw != '' AND create_ts > ? ORDER BY id",
        (since,)).fetchall()
    out = []
    for r in rows:
        mid = int(r[0])
        if mid in have:
            continue
        spk = (r[2] or "").strip()
        role = "assistant" if spk in ("self", "assistant") else "user"
        out.append((mid, float(r[1] or 0), role, str(r[3])))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="card.db")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--source", default="backfill")
    ap.add_argument("--since", type=float, default=None,
                    help="只补这个 epoch 之后的（默认=最后一条 dialogue 的时间）")
    a = ap.parse_args(argv)
    if not os.path.exists(a.db):
        print("找不到卡：%s" % a.db)
        return 2
    con = sqlite3.connect(a.db, timeout=30)
    con.row_factory = sqlite3.Row
    rows = collect(con, a.since)
    n0 = con.execute("SELECT COUNT(*) FROM dialogue").fetchone()[0]
    print("dialogue 现有 %d 行；待补 %d 行" % (n0, len(rows)))
    if rows:
        print("最早 %s / 最晚 %s" % (rows[0][1], rows[-1][1]))
        for r in rows[:3]:
            print("  例：", r[0], r[2], r[3][:40].replace("\n", " "))
    if not a.apply:
        print("\n（只是看看；加 --apply 才真写）")
        return 0
    cur = con.cursor()
    for mid, ts, role, text in rows:
        cur.execute(
            "INSERT INTO dialogue(mem_id,ts,role,text,source) VALUES(?,?,?,?,?)",
            (mid, ts, role, text, a.source))
    con.commit()
    n1 = con.execute("SELECT COUNT(*) FROM dialogue").fetchone()[0]
    print("\n写完：dialogue %d → %d（+%d，source=%s）"
          % (n0, n1, n1 - n0, a.source))
    print("后悔药：DELETE FROM dialogue WHERE source='%s';" % a.source)
    return 0


if __name__ == "__main__":
    sys.exit(main())
