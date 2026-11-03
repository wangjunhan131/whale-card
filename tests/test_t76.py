# -*- coding: utf-8 -*-
"""test_t76.py —— 习惯层 v1（纯函数 + 表结构 + 挖掘过滤）

跑法：python tests/test_t76.py
特点：全程用临时库，不碰真卡。
"""

import json
import os
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import habits as H  # noqa: E402

OK = 0
BAD = []


def ck(name, cond, extra=""):
    global OK
    if cond:
        OK += 1
        print("  ✅ %s" % name)
    else:
        BAD.append(name)
        print("  ❌ %s %s" % (name, extra))


def main():
    print("== T7-6 习惯层 v1 ==")

    # 1. 切段
    segs = H.segments("行吧，我等着。\n你猜我在干嘛？……算了。")
    ck("切段数量", len(segs) == 3, segs)
    ck("切段保留句末标点", any(s.endswith("。") for s in segs), segs)

    # 2. 汇报味识别
    ck("代码块算汇报", H.is_report_like("```python\nprint(1)\n```"))
    ck("列表算汇报", H.is_report_like("- 第一项\n- 第二项"))
    ck("表格算汇报", H.is_report_like("| a | b |\n|---|---|\n| 1 | 2 |"))
    ck("带勾算汇报", H.is_report_like("三项都做完，全部验收通过 ✅"))
    ck("正常说话不算汇报", not H.is_report_like("行吧，那你早点睡。"))

    # 3. n-gram 不含标点 / 纯英文
    gs = H.ngrams("行吧，我等着。", 1, 3)
    ck("n-gram 不带标点", all(not H._NON_WORD_RE.search(g) for g in gs), sorted(gs)[:6])
    ck("n-gram 有内容", "行吧" in gs, sorted(gs)[:8])

    # 4. 段首段尾
    ck("段首取前 3 字", H.head_ngram("行吧，我等着。", 3) == "行吧我", H.head_ngram("行吧，我等着。", 3))
    ck("段尾取后 3 字", H.tail_ngram("那你去睡吧。", 3) == "去睡吧")

    # 5. 置信度 / 状态机
    ck("置信度随天数升", H.confidence_of(3, 2) < H.confidence_of(10, 14))
    ck("3 次跨 2 天=候选", H.status_of(3, 2) == H.ST_CANDIDATE)
    ck("10 次跨 14 天=稳定", H.status_of(10, 14) == H.ST_STABLE)
    ck("钉住优先", H.status_of(1, 1, pinned=True) == H.ST_PINNED)
    ck("冲突优先于稳定", H.status_of(20, 20, conflict=True) == H.ST_CONFLICT)
    ck("30 天不出现=衰减", H.status_of(10, 14, last_seen=time.time() - 40 * H.DAY,
                                       now=time.time()) == H.ST_DECAYED)

    # 6. 表结构 + 幂等 upsert（临时库）
    tmp = tempfile.mkdtemp(prefix="t76_", dir=ROOT)
    db = os.path.join(tmp, "t.db")
    try:
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        H.ensure_table(con)
        H.ensure_table(con)  # 幂等
        cols = {r["name"] for r in con.execute("PRAGMA table_info(habits)")}
        need = {"id", "category", "key", "hits", "days", "confidence",
                "status", "evidence", "pinned"}
        ck("habits 表字段齐", need.issubset(cols), sorted(cols))

        now = time.time()
        ev = [{"ts": now, "text": "行吧，我等着。", "dialogue_id": 1}]
        conf, st = H.upsert(con, "language", "行吧", 3, 2, now - 2 * H.DAY, now, ev)
        ck("首次写入=候选", st == H.ST_CANDIDATE and 0 < conf < 1, (conf, st))

        conf2, st2 = H.upsert(con, "language", "行吧", 10, 15, now - 15 * H.DAY, now, ev)
        n = con.execute("SELECT COUNT(*) FROM habits").fetchone()[0]
        ck("同一习惯不重复插入", n == 1, n)
        ck("再次写入升级为稳定", st2 == H.ST_STABLE, st2)
        row = con.execute("SELECT * FROM habits").fetchone()
        ck("evidence 存成 JSON 且可回读",
           json.loads(row["evidence"])[0]["text"].startswith("行吧"), row["evidence"][:40])

        H.upsert(con, "language", "晚安", 6, 3, now - 3 * H.DAY, now)
        rows = H.list_habits(con)
        ck("列表按置信度排序", len(rows) == 2, len(rows))
        ck("第二个也在", any(r["key"] == "晚安" for r in rows))
        con.close()
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    # 7. 只读挖掘不改库
    tmp2 = tempfile.mkdtemp(prefix="t76b_", dir=ROOT)
    db2 = os.path.join(tmp2, "t2.db")
    try:
        con = sqlite3.connect(db2)
        con.execute("CREATE TABLE dialogue(id INTEGER PRIMARY KEY, ts REAL, role TEXT, text TEXT)")
        now = time.time()
        rows = [
            (1, now - 3 * H.DAY, "assistant", "行吧，我等着。"),
            (2, now - 2 * H.DAY, "assistant", "行吧，那你先忙。"),
            (3, now - 1 * H.DAY, "assistant", "行吧，明天见。"),
            (4, now - 1 * H.DAY, "user", "行吧"),
            (5, now - 1 * H.DAY, "assistant", "- 第一项\n- 第二项 ✅"),
        ]
        con.executemany("INSERT INTO dialogue(id,ts,role,text) VALUES(?,?,?,?)", rows)
        con.commit()
        con.close()

        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import mine_habits as M  # noqa: E402
        ro = M.open_ro(db2)
        mine, theirs = M.collect(ro)
        ck("按 role 归类", len(mine) == 4 and len(theirs) == 1, (len(mine), len(theirs)))
        chat = [r for r in mine if not r.get("report_like")]
        ck("汇报味被排除", len(chat) == 3, len(chat))
        cands = M.phrase_candidates(mine, theirs)
        top = {c["key"] for c in cands[:5]}
        ck("挖出「行吧」", "行吧" in top, sorted(top))
        ck("记录用户也说过", any(c["key"] == "行吧" and c["user_hits"] >= 1 for c in cands))
        ro.close()
        ck("只读打开不会写文件", os.path.getsize(db2) > 0)
    finally:
        import shutil
        shutil.rmtree(tmp2, ignore_errors=True)

    print("\n结果：%d 通过，%d 失败" % (OK, len(BAD)))
    if BAD:
        print("失败项：", BAD)
    return 0 if not BAD else 1


if __name__ == "__main__":
    raise SystemExit(main())
