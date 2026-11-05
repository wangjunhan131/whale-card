# -*- coding: utf-8 -*-
"""把老记忆里的相对时间词（「今天/昨天/等会/上个月」）换成绝对日期。

为什么这是一次**修复**而不是"改写"：
  normalize_time() 本来就在 remember() 里跑（写入时归一化），
  但它是 2026-10-29 之后才加上的 —— 在那之前写进卡的记录，raw 里还留着
  「明天给你账号嘻嘻」这种话。三个月后读，那句"明天"到底指哪天，已经不可知。
  这些记录只是**没赶上这次归一化**，不是"本来就想那么说"。

安全措施（和 tools/fix_sysmark.py 同一套）：
  1) 默认只预览，要 --apply 才写
  2) 写之前自动备份 card.db（sqlite backup API，带时间戳）
  3) 只改 raw / core / subj / verb / obj / detail / vec / emb
     不动 w / t_anchor / pool / region / create_ts / history —— 不碰"它有多重要"
  4) raw 变了就必须重开出生证（timeline.restamp_birth，reason 写明原因），
     否则 verify_birth 会判"原文被篡改"—— 它是对的，所以我们才要留痕
  5) create_ts 当参照时刻（"明天"是相对**说那句话的时候**算的）

跑法：
    python -X utf8 tools/fix_reltime.py            # 只看会改什么
    python -X utf8 tools/fix_reltime.py --apply    # 真改
"""
import os
import shutil
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

# 判"还含相对时间词"的口径要和 normalize_time 的替换表对齐。
# ⚠️ 故意**不含**「等会/待会/一会儿/晚点」——见 card2.normalize_time 里的注释：
#    它们在中文里也指"一小会儿"（时长），换错会把意思改掉，比留着更糟。
_REL = ("今天", "明天", "昨天", "后天", "前天", "大前天", "大后天",
        "今晚", "今早", "今儿", "刚才", "刚刚",
        "昨晚", "昨早", "昨夜", "昨儿", "明早", "明晚", "明夜", "明儿",
        "前晚", "前儿", "上个月", "下个月", "这个月",
        "上周", "这周", "本周", "下周")


def has_rel(t):
    return any(w in (t or "") for w in _REL)


def main():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    rows = c.execute("SELECT id, raw, core, create_ts, region, pool FROM mem"
                     " ORDER BY id").fetchall()
    todo = []
    for r in rows:
        if not has_rel(r["raw"]):
            continue
        new = card2.normalize_time(r["raw"], r["create_ts"])
        if new != r["raw"]:
            todo.append((r, new))
    print("=" * 70)
    print("含相对时间词、且归一化后确实会变的：%d 条（总 %d 条）" % (len(todo), len(rows)))
    print("=" * 70)
    for r, new in todo:
        when = time.strftime("%m-%d", time.localtime(r["create_ts"]))
        print("\n#%d  [%s %s/%s]" % (r["id"], when, r["region"], r["pool"]))
        print("   旧: %s" % r["raw"].replace("\n", " ⏎ ")[:110])
        print("   新: %s" % new.replace("\n", " ⏎ ")[:110])
    if not todo:
        print("\n没有需要改的。")
        return
    if not APPLY:
        print("\n（预览模式，一个字节都没写。加 --apply 才真改）")
        return

    # ---------- 备份 ----------
    bak = DB + ".bak-reltime-%s" % time.strftime("%Y%m%d-%H%M%S")
    s = sqlite3.connect(DB)
    t = sqlite3.connect(bak)
    with t:
        s.backup(t)
    s.close()
    t.close()
    print("\n已备份 → %s" % bak)

    # ---------- 改 ----------
    card = card2.MemoryCard(DB)
    tl = timeline.Timeline(card.db)
    cc = card.db.cursor()
    done = 0
    for r, new in todo:
        mid = int(r["id"])
        core, details = card2.split_memory(new)
        vec = card2.to_vec(new)
        subj, verb, obj = card2.split_predicate(core)
        try:
            emb = card.semantic.encode(new)
        except Exception:
            emb = None
        cc.execute("UPDATE mem SET raw=?, core=?, subj=?, verb=?, obj=?, vec=?, emb=?"
                   " WHERE id=?",
                   (new, core, subj, verb, obj,
                    card2.json.dumps(vec, ensure_ascii=False), emb, mid))
        cc.execute("DELETE FROM detail WHERE parent_id=?", (mid,))
        now = card.clock.now()
        for kind, txt, wgt in details:
            cc.execute("INSERT INTO detail(parent_id,kind,text,weight,last_touch)"
                       " VALUES(?,?,?,?,?)", (mid, kind, txt, wgt, now))
        # 出生证锁的就是 raw，raw 变了必须重开并留理由
        tl.restamp_birth(mid, reason="2026-11-05 补齐时间归一化：把相对时间词换成绝对日期")
        done += 1
    card.db.commit()
    print("已修 %d 条（raw/core/subj/verb/obj/detail/vec/emb 重建，出生证重开留痕）" % done)
    print("权重 / 时间锚 / 分区 / 池 / create_ts 一律没动。")
    print("\n下一步建议：跑 python -X utf8 bridge.py emb_remodel 或 POST /emb/remodel")
    print("（emb 已按新正文重算，remodel 只是把维度对齐，可选）")


if __name__ == "__main__":
    main()
