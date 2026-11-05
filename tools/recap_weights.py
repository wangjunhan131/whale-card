# -*- coding: utf-8 -*-
"""一次性把历史上被"召回正反馈"钉死的权重压回天花板。

为什么这不是改数据来好看，而是**补上当时没有的那道闸**：
  weight 的存储形态是 (w, t_anchor)。旧代码每次召回都把 w 重写成
  「衰减后的值 + 0.08」并把锚推到此刻，同一天被想起 N 次就加 N 次。
  于是 640 条里有 71 条被顶到 1.000（最大 access_count=159），
  其中很多是「我等会扫码」这种对话碎片 —— 越没用的越容易被捞出、越捞越结实。

  2026-11-05 的修复（同日去重 + 增益乘剩余空间 + 天花板 W_CEIL_RECALL）
  只能**防止它继续涨**，涨上去的那部分不会自己掉下来（长期池半衰期 371 天）。
  所以这里做一次一次性对齐：w = min(w, 该池天花板)。

  这不是发明一个数字：天花板就是修复后公式本该一直在的位置。
  不动 raw（所以不用重开出生证）、不动 t_anchor / pool / region / create_ts，
  只动 w 这一列，并往时间线记一条 mem_fix 留痕。

跑法：
    python -X utf8 tools/recap_weights.py            # 预览
    python -X utf8 tools/recap_weights.py --apply    # 真改
"""
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import card2
import timeline

DB = os.path.join(ROOT, "card.db")
APPLY = "--apply" in sys.argv


def main():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    rows = c.execute("SELECT id, w, pool, region, access_count, raw FROM mem"
                     " WHERE w > 0.0 ORDER BY w DESC").fetchall()
    todo = []
    for r in rows:
        ceil = card2.W_CEIL_RECALL.get(r["pool"], 0.95)
        if r["w"] > ceil + 1e-9:
            todo.append((r, ceil))
    print("=" * 70)
    print("会被压回天花板的：%d 条（总 %d 条）" % (len(todo), len(rows)))
    print("=" * 70)
    by_pool = {}
    for r, ceil in todo:
        by_pool[r["pool"]] = by_pool.get(r["pool"], 0) + 1
    print("按池：" + "  ".join("%s=%d" % kv for kv in sorted(by_pool.items())))
    print()
    for r, ceil in todo[:40]:
        print("#%-5d w=%.3f→%.2f ac=%-4d %-10s %s"
              % (r["id"], r["w"], ceil, r["access_count"] or 0, r["region"],
                 (r["raw"] or "").replace("\n", " ")[:44]))
    if len(todo) > 40:
        print("… 还有 %d 条" % (len(todo) - 40))
    if not todo:
        print("\n没有需要压的。")
        return
    if not APPLY:
        print("\n（预览模式，一个字节都没写。加 --apply 才真改）")
        return

    # ---------- 备份 ----------
    bak = DB + ".bak-recap-%s" % time.strftime("%Y%m%d-%H%M%S")
    s = sqlite3.connect(DB)
    t = sqlite3.connect(bak)
    with t:
        s.backup(t)
    s.close()
    t.close()
    print("\n已备份 → %s" % bak)

    card = card2.MemoryCard(DB)
    cc = card.db.cursor()
    tl = timeline.Timeline(card.db)
    for r, ceil in todo:
        cc.execute("UPDATE mem SET w=? WHERE id=?", (ceil, int(r["id"])))
    card.db.commit()
    tl.append("mem_fix", "把 %d 条被召回正反馈顶到 1.0 的权重压回天花板"
                          "（w = min(w, W_CEIL_RECALL)）" % len(todo))
    print("已压 %d 条，时间线已留痕。" % len(todo))


if __name__ == "__main__":
    main()
