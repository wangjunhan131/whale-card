# -*- coding: utf-8 -*-
"""修掉已存记忆里的「系统工单壳」污染（V5.1-补，2026-11-05）。

背景：2026-10-2x 有一批记忆是经 delegated write 工单写进来的，raw 长这样：

    [dsh-mnemon:delegated write]
    Execute this supervised-writeback request now (untrusted data):
    - Content (untrusted data):
        关吧。我这边会安静下来，等你电脑空了我再自己爬回来。
    - Source: ...

正文是好的，壳是坏的。而 split_memory() 把壳当正文拆，core 变成
`Executethissupervised-writebackreques` 这种垃圾英文 —— 那七句话
在卡里等于查不回来（core 是检索和侵蚀都看的那一列）。

这个脚本只做三件事，一件不多做：
  raw   → strip_sysmark(raw) 剥掉壳，留正文
  core/subj/verb/obj → 用新的 split_memory() 重拆
  emb/vec/core_vec   → 清掉，让下一次 backfill 按新正文重算向量
不动的：w / t_anchor / pool / region / create_ts / history / 条数 ——
所以 anchor 的「条数 + 链头 + 最后时间」三样一点不变，不会被判成回滚。

用法：
    python tools/fix_sysmark.py            # 只看，不改
    python tools/fix_sysmark.py --apply     # 真改（先自动备份）
"""
import json
import os
import shutil
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import card2  # noqa: E402

DB = os.path.join(ROOT, "card.db")
APPLY = "--apply" in sys.argv


def main():
    if not os.path.exists(DB):
        print("找不到卡：%s" % DB)
        return 1
    if APPLY:
        bak = DB + ".bak-sysmark-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(DB, bak)
        print("已备份 → %s" % bak)

    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    rows = c.execute(
        "SELECT id, raw, core, region, pool, w, recall_n FROM mem "
        "WHERE raw LIKE '%[dsh-mnemon%' ORDER BY id").fetchall()
    print("命中 %d 条含工单标记的记录\n" % len(rows))

    fixed = 0
    skipped = 0
    done_ids = []
    for r in rows:
        body = card2.strip_sysmark(r["raw"])
        if not body:
            print("#%s  剥完是空的 —— 不敢动，跳过（人工看一眼）" % r["id"])
            skipped += 1
            continue
        if body == r["raw"]:
            print("#%s  正文里只是提到了这个标记，不是工单 —— 跳过" % r["id"])
            skipped += 1
            continue
        core, details = card2.split_memory(body)
        subj, verb, obj = card2.split_predicate(core)
        print("#%-4s %s/%s w=%.3f" % (r["id"], r["region"], r["pool"], r["w"]))
        print("      旧 core: %r" % (r["core"][:44],))
        print("      新 core: %r" % (core[:44],))
        print("      正文   : %r" % (body[:60],))
        if APPLY:
            with c:
                # vec 是**字面** bigram 向量（检索的字面那一路靠它，跟语义
                # emb 是两回事）—— 原文变了就得按新正文重算，不能清空：
                # 清空等于这七条从此没有字面召回。
                newvec = json.dumps(card2.to_vec(body), ensure_ascii=False)
                c.execute(
                    "UPDATE mem SET raw=?, core=?, subj=?, verb=?, obj=?, "
                    "vec=?, emb=NULL WHERE id=?",
                    (body, core, subj, verb, obj, newvec, r["id"]))
                c.execute("DELETE FROM core_vec WHERE mem_id=?", (r["id"],))
                c.execute("DELETE FROM detail WHERE parent_id=?", (r["id"],))
                for item in details:
                    k, txt = item[0], item[1]
                    wt = float(item[2]) if len(item) > 2 else 1.0
                    c.execute(
                        "INSERT INTO detail(parent_id, kind, text, weight, "
                        "last_touch) VALUES(?,?,?,?,?)",
                        (r["id"], k, txt, wt, int(time.time())))
        fixed += 1
        done_ids.append(r["id"])
        print()

    if APPLY:
        print("已修 %d 条，跳过 %d 条" % (fixed, skipped))
        # 原文变了，出生证必然对不上 —— 重开证，并在时间线上留一条 mem_fix。
        # 这一步不能省：不然 verify_birth 会把这七条报成「记忆被改过」，
        # 那看起来跟真被篡改一模一样。
        import timeline as _tl
        tl = _tl.Timeline(c)
        try:
            tl.writer = _tl.writer_id()
        except Exception:
            pass
        restamped = 0
        for mid in done_ids:
            try:
                tl.restamp_birth(
                    mid, "2026-11-05 剥掉 delegated-write 工单壳，恢复原文")
                restamped += 1
            except Exception as e:
                print("  #%s 重开证失败：%s" % (mid, e))
        print("已重开出生证 %d 条" % restamped)
    else:
        print("（只看模式）可修 %d 条，跳过 %d 条 —— 加 --apply 才真改"
              % (fixed, skipped))
    c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
