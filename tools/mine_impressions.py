# -*- coding: utf-8 -*-
"""mine_impressions.py —— 从卡里已有的记忆里，把印象重新数一遍

为什么要这个工具：

印象层是 2026-11-08 才加的，之前那 1100 多条记忆里，其实已经藏着不少
「他晚上回来常说这句」「他考完试会先骂一句」这类模式 —— 只是没有人去数。
这个脚本只做一件事：把 mem 里**他已经说过的话**按时间顺序喂给
`impression.observe()`，让印象层的计数从历史里长出来。

只读 mem，只写 impression 表。不改任何一条记忆、不删任何东西。

用法：
    python -X utf8 tools/mine_impressions.py            # 数最近 30 天
    python -X utf8 tools/mine_impressions.py --days 90
    python -X utf8 tools/mine_impressions.py --dry      # 只看会数出什么，不写
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import impression as imp  # noqa: E402

DAY = 86400.0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--card", default=os.path.join(ROOT, "card.db"))
    ap.add_argument("--days", type=float, default=30.0)
    ap.add_argument("--limit", type=int, default=5000)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args(argv)

    import sqlite3
    conn = sqlite3.connect(a.card, timeout=30.0)
    conn.row_factory = sqlite3.Row
    now = conn.execute("SELECT MAX(create_ts) FROM mem").fetchone()[0] or 0.0
    since = float(now) - a.days * DAY
    rows = conn.execute(
        "SELECT id, raw, speaker, about_uid, create_ts, source FROM mem"
        " WHERE create_ts >= ? AND COALESCE(dead,0)=0 AND COALESCE(quarantine,0)=0"
        " ORDER BY create_ts ASC LIMIT ?", (since, a.limit)).fetchall()

    n_new = n_again = n_skip = 0
    for r in rows:
        # 只从「有情境」的话里长印象。
        # 时间桶（早/午/晚/夜）太粗，光靠它，半夜说的每一句都会被塞进同一个筐，
        # 于是「安装插件市场」「思考过程用中文」也成了"晚上他常用的反应"——
        # 那不是印象，那只是把所有话堆在一起。所以必须有事件桶。
        if (r["raw"] or "").lstrip().startswith("["):
            n_skip += 1
            continue
        if not imp.event_bucket(r["raw"]):
            n_skip += 1
            continue
        if len((r["raw"] or "")) > 40:
            n_skip += 1
            continue
        subj = (r["about_uid"] or "") if (r["about_uid"] or "") else (
            "user" if (r["speaker"] or "") == "user" else "")
        if not subj:
            # 没有主体就成不了「谁的印象」——跳过，不硬安一个
            continue
        if a.dry:
            continue
        res = imp.observe(conn, subj, r["raw"], ts=r["create_ts"],
                          subject_kind="person", now=now)
        if res["hit"] == "new":
            n_new += 1
        elif res["hit"] == "again":
            n_again += 1

    print("扫了 %d 条（最近 %.0f 天），跳过 %d 条（没有情境 / 太长）"
          % (len(rows), a.days, n_skip))
    if a.dry:
        print("--dry：没有写入")
        conn.close()
        return 0

    imp.fade(conn, now=now)
    stat = imp.snapshot(conn)
    print("新印象 %d 条，重复命中 %d 次" % (n_new, n_again))
    print("印象库：%s" % stat)
    for it in imp.top(conn, limit=10, min_w=0.30):
        scene = "".join(x for x in (it["scene_time"], it["scene_event"]) if x) or "平时"
        print("  [%.2f] %s %s：%s（%d 次）"
              % (it["w"], it["subject"], scene,
                 (it["sample"] or it["reaction"])[:28], it["n"]))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
