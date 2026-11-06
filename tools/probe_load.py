# -*- coding: utf-8 -*-
"""probe_load.py —— 只读量一下：我们每天到底往里写多少条。

为什么要有这个：
    方案（plan-20261106-memory-regions.md）里「今天的拥挤程度」要用一个
    预算做分母：
        ratio = 当天该区新写的条数 / 该区一天预算
    预算不能拍脑袋。这个脚本只读真卡，把最近 N 天的真实写入量量出来，
    好让 budget 有个根据。

严格只读：以 `?mode=ro` 打开，不写库、不改卡、不建临时表。
"""
import os
import sys
import sqlite3
import time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DB = os.path.join(ROOT, "card.db")
DAYS = 30


def q(c, sql, args=()):
    return c.execute(sql, args).fetchall()


def fmt_row(label, vals, unit="条"):
    if not vals:
        print("  %-8s 无数据" % label)
        return
    vals = sorted(vals)
    n = len(vals)
    def pct(p):
        i = min(n - 1, max(0, int(round(p / 100.0 * (n - 1)))))
        return vals[i]
    print("  %-8s 天=%2d  日均 %5.1f %s  中位 %3d   p90 %3d   p99 %3d   最大 %3d"
          % (label, n, sum(vals) / float(n), unit, pct(50), pct(90), pct(99), vals[-1]))


def main():
    if not os.path.exists(DB):
        print("找不到卡：%s" % DB)
        return 2
    c = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
    c.row_factory = sqlite3.Row

    since = int(time.time()) - DAYS * 86400

    print("=" * 78)
    print("记忆写入负载探针（只读）  卡：%s" % DB)
    print("窗口：最近 %d 天" % DAYS)
    print("=" * 78)

    total = q(c, "SELECT count(*) n FROM mem WHERE create_ts >= ?", (since,))[0]["n"]
    print("\n【总览】窗口内共写入 %d 条。\n" % total)

    # --- 1. 每天总量 ---
    rows = q(c, """
        SELECT date(create_ts,'unixepoch','localtime') d, count(*) n
        FROM mem WHERE create_ts >= ? GROUP BY d ORDER BY d
    """, (since,))
    per_day = [r["n"] for r in rows]
    print("【每天写入总量】")
    fmt_row("全部", per_day)
    for r in rows:
        bar = "#" * min(60, r["n"])
        print("    %s  %3d  %s" % (r["d"], r["n"], bar))

    # --- 2. 按来源 ---
    print("\n【按来源分】（来源就是将来路由到哪个区的依据）")
    rows = q(c, """
        SELECT COALESCE(NULLIF(source,''),'(空)') s, count(*) n
        FROM mem WHERE create_ts >= ? GROUP BY s ORDER BY n DESC
    """, (since,))
    for r in rows:
        print("    %-12s %4d" % (r["s"], r["n"]))

    # --- 3. 每个来源的「每天」分布 ---
    print("\n【每个来源的日分布】（定 budget 就靠这个）")
    by_src_day = defaultdict(list)
    rows = q(c, """
        SELECT COALESCE(NULLIF(source,''),'(空)') s,
               date(create_ts,'unixepoch','localtime') d, count(*) n
        FROM mem WHERE create_ts >= ? GROUP BY s, d ORDER BY s, d
    """, (since,))
    for r in rows:
        by_src_day[r["s"]].append(r["n"])
    for s in sorted(by_src_day, key=lambda k: -sum(by_src_day[k])):
        fmt_row(s, by_src_day[s])

    # --- 4. 按现有分区看日分布 ---
    print("\n【按现有 partition (region) 的日分布】")
    by_reg_day = defaultdict(list)
    rows = q(c, """
        SELECT region r, date(create_ts,'unixepoch','localtime') d, count(*) n
        FROM mem WHERE create_ts >= ? GROUP BY r, d ORDER BY r, d
    """, (since,))
    for r in rows:
        by_reg_day[r["r"] or "(空)"].append(r["n"])
    for s in sorted(by_reg_day, key=lambda k: -sum(by_reg_day[k])):
        fmt_row(s, by_reg_day[s])

    # --- 5. 一天里最忙的那一小时 ---
    print("\n【一天里最忙的一小时】（决定 f() 的上限要不要很大）")
    rows = q(c, """
        SELECT strftime('%Y-%m-%d %H', create_ts, 'unixepoch', 'localtime') h,
               count(*) n
        FROM mem WHERE create_ts >= ?
        GROUP BY h ORDER BY n DESC LIMIT 10
    """, (since,))
    for r in rows:
        print("    %s   %3d 条" % (r["h"], r["n"]))

    # --- 6. 现在池子里各有多少 ---
    print("\n【现在池子/分区快照】（不是窗口，是全部）")
    for r in q(c, "SELECT pool, count(*) n FROM mem GROUP BY pool ORDER BY n DESC"):
        print("    pool %-10s %4d" % (r["pool"], r["n"]))
    print()
    for r in q(c, "SELECT region, count(*) n FROM mem GROUP BY region ORDER BY n DESC"):
        print("    region %-10s %4d" % (r["region"] or "(空)", r["n"]))

    c.close()
    print("\n" + "=" * 78)
    print("完。以上全部只读。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
