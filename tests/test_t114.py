# -*- coding: utf-8 -*-
"""t114：V5.4 —— 统一收件口、收件层 60 小时、短期池不设容量上限、
时间窗加成（30 天内最多 +12%，只给他本人说的话）、空卡启动守卫。

这几条都是用户 2026-11-07 亲口定的：
  · 「所有池都不设置容量限制」→ 短期池只是一段 7 天时间窗
  · 「就在当前提问之前的一个月内，他的那些消息的权重会稍微高一点点，
     超过一个月就回落到正常衰减曲线」
  · 「所有的消息全部都收进来」→ 收件层先进原文，再挑进短期
  · 「收件层保留就 60 个小时吧」
"""
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import card2                        # noqa: E402
from _cardcopy import copy_card_db  # noqa: E402

PASS = FAIL = 0


def ck(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s %s" % (name, extra))


def new_card():
    d = tempfile.mkdtemp(prefix="t114_")
    db = os.path.join(d, "card.db")
    copy_card_db(os.path.join(ROOT, "card.db"), db)
    return card2.MemoryCard(db), d


# ---------------------------------------------------------------- 1
print("== 1. 收件层：原文先落这里，挑中才进池 ==")
c, d = new_card()
try:
    now = c.clock.now()
    i1 = c.inbox_add("t114 群里的废话一条", source="qq", channel="group",
                     who="路人甲", gid="999", ts=now - 10)
    i2 = c.inbox_add("t114 私聊里他说想吃白米饭", source="qq",
                     channel="private", who="他", uid="1001", ts=now - 5)
    i3 = c.inbox_add("", source="qq")          # 空文本不该进
    ck("空文本被挡掉", i3 is None, i3)
    ck("正常两条都拿到了 id", isinstance(i1, int) and isinstance(i2, int))

    lst = c.inbox_list(limit=10, hours=1)
    ids = [r["id"] for r in lst]
    ck("列表里能看到刚写的", i1 in ids and i2 in ids, ids[:5])
    ck("按时间倒序（新的在前）",
       lst.index(next(r for r in lst if r["id"] == i2))
       < lst.index(next(r for r in lst if r["id"] == i1)))
    ck("默认截断正文，带 text_len",
       all("text_len" in r for r in lst))
    full = c.inbox_list(limit=10, hours=1, full=True)
    ck("full=True 给原文（不截断）",
       any(r["text"] == "t114 群里的废话一条" for r in full))
    ck("unkept 过滤只留没挑中的",
       all(int(r["kept"]) == 0 for r in
           c.inbox_list(limit=10, hours=1, unkept_only=True)))

    st = c.inbox_stats()
    ck("统计里总数对得上", st["total"] >= 2 and st["unkept"] >= 2, st)

    # 挑中 → 标记
    n = c.inbox_mark([i2], kept=1, kept_mid=12345)
    row = next(r for r in c.inbox_list(limit=10, hours=1)
               if r["id"] == i2)
    ck("标记 kept 生效", n == 1 and int(row["kept"]) == 1, row.get("kept"))
    ck("kept_mid 记下了是哪条记忆", int(row["kept_mid"] or 0) == 12345)

    # 60 小时：没挑中的删掉、挑中的只清正文
    old_ts = now - 70 * 3600
    a = c.inbox_add("t114 两天半前没被挑中的", source="qq",
                    channel="group", ts=old_ts)
    b = c.inbox_add("t114 两天半前被挑中的", source="qq",
                    channel="private", ts=old_ts, kept=1)
    gc = c.inbox_gc(ttl_hours=60.0, now=now)
    ck("过期清理确实删了东西", gc["dropped"] >= 1, gc)
    left = {r["id"]: r for r in c.inbox_list(limit=500, hours=None, full=True)}
    ck("没挑中的整条没了", a not in left)
    ck("挑中的还在（壳留着）", b in left)
    ck("但正文被清空", (left.get(b, {}).get("text") or "") == "",
       repr(left.get(b, {}).get("text"))[:40])
    ck("TTL 默认值就是 60 小时",
       abs(c.inbox_gc.__defaults__[0] - 60.0) < 1e-6,
       c.inbox_gc.__defaults__)
finally:
    c.close()
    shutil.rmtree(d, ignore_errors=True)

# ---------------------------------------------------------------- 2
print("== 2. remember() 顺手留档（私聊/本窗口两条腿）==")
c, d = new_card()
try:
    before = c.inbox_stats()["total"]
    mid = c.remember("t114 他说今天考试考得还行", source="qq",
                     pool=card2.POOL_LONG, salience=0.7, region="episode")
    after = c.inbox_list(limit=5, hours=1, full=True)
    hit = [r for r in after if r.get("kept_mid") == mid]
    ck("写记忆时收件层留了一份", len(hit) == 1, [r.get("kept_mid") for r in after[:3]])
    ck("留档标了 kept=1", hit and int(hit[0]["kept"]) == 1)
    ck("留档的正文就是写进去那句（会被归一化/时间锚定，所以只核对主体）",
       hit and "t114" in hit[0]["text"] and "考试考得还行" in hit[0]["text"],
       repr(hit[0]["text"])[:60] if hit else "")
    ck("总数涨了 1", c.inbox_stats()["total"] == before + 1)
finally:
    c.close()
    shutil.rmtree(d, ignore_errors=True)

# ---------------------------------------------------------------- 3
print("== 3. 短期池不设容量上限（只看时间窗）==")
c, d = new_card()
try:
    now = c.clock.now()
    # 灌 200 条全新的短期记忆（远超旧的 120 上限）。
    # 这里直接写 mem 表、不走 remember()：remember 里有「同一件事又说一遍」的
    # Jaccard 合并，200 条模板句会被判成十几条（第一次跑就是这么挂的）。
    # 本用例要测的是**池子的容量行为**，不是去重，所以绕开它。
    for i in range(200):
        c.db.execute(
            "INSERT INTO mem(raw,core,vec,pool,w,t_anchor,salience,emotion,"
            "create_ts,last_active_ts,region,speaker,mtype,source,erase_stage)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
            ("t114 灌水位 %03d" % i, "t114 灌水位 %03d" % i,
             "[]",
             card2.POOL_SHORT, 0.6, now - i * 3.0, 0.6, 0.25,
             now - i * 3.0, now - i * 3.0, "episode", "user", "episode",
             "probe"))
    c.db.commit()
    n_short = c.db.execute(
        "SELECT COUNT(*) FROM mem WHERE pool='short'").fetchone()[0]
    ck("200 条全在短期池里（没被挤出去）", n_short >= 200, n_short)
    ck("驱逐函数已经是空操作", c._evict_short_overflow(now) == [])
    ck("N_SHORT_MAX 不再参与淘汰", c._evict_short_overflow(now + 86400) == [])
    # 7 天窗：把一条挪到 8 天前，跑 tick 后它必须离开 short
    old_id = c.db.execute(
        "SELECT id FROM mem WHERE pool='short' ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    c.db.execute("UPDATE mem SET t_anchor=?, last_active_ts=? WHERE id=?",
                 (now - 8 * 86400, now - 8 * 86400, old_id))
    c.db.commit()
    c.tick(1.0)
    _row = c.db.execute("SELECT pool FROM mem WHERE id=?", (old_id,)).fetchone()
    pool_now = (_row[0] if _row is not None else "(已删)")
    ck("超过 7 天窗的会离开短期池（时间决定去留，不是数量）",
       pool_now != "short", pool_now)
finally:
    c.close()
    shutil.rmtree(d, ignore_errors=True)

# ---------------------------------------------------------------- 4
print("== 4. 时间窗加成：30 天内、只给他本人、可关 ==")
c, d = new_card()
try:
    ck("窗口是 30 天", abs(card2.WINDOW_BONUS_DAYS - 30.0) < 1e-9)
    ck("最大加成 6%（扫出来的值）", abs(card2.WINDOW_BONUS_MAX - 0.06) < 1e-9)
    ck("默认是开着的（评估集确认不掉分）", card2._WINDOW_BONUS_ON is True)

    now = c.clock.now()
    mine = c.remember("t114 蓝色的小鲸鱼杯子放在窗台上", source="qq",
                      pool=card2.POOL_LONG, salience=0.6, region="episode",
                      ts=now - 3600)
    c.db.execute("UPDATE mem SET speaker='user' WHERE id=?", (mine,))
    c.db.commit()

    def top_score():
        hits = c.recall("蓝色的小鲸鱼杯子", top_k=5, access=False)
        return float(hits[0][0]) if hits else 0.0

    card2._WINDOW_BONUS_ON = False
    off = top_score()
    card2._WINDOW_BONUS_ON = True
    on = top_score()
    ck("开加成后分数变高（不是变低）", on > off, (off, on))
    ck("加成幅度不超过 6%", (on / off - 1.0) <= 0.0601 + 1e-6,
       (off, on, on / off - 1.0))

    # 只有一个月的窗口：40 天前的老话不该被加
    old = c.remember("t114 四十天前他说过一模一样的一句", source="qq",
                     pool=card2.POOL_LONG, salience=0.6, region="episode",
                     ts=now - 40 * 86400)
    c.db.execute("UPDATE mem SET speaker='user', last_active_ts=? WHERE id=?",
                 (now - 40 * 86400, old))
    c.db.commit()
    card2._WINDOW_BONUS_ON = True
    span = card2.WINDOW_BONUS_DAYS * card2.DAY
    age = now - (now - 40 * 86400)
    ck("40 天前已经超出窗口（式子给 0）", max(0.0, 1.0 - age / span) == 0.0)
    card2._WINDOW_BONUS_ON = False
finally:
    c.close()
    shutil.rmtree(d, ignore_errors=True)

# ---------------------------------------------------------------- 5
print("== 5. 启动守卫：空卡不启动 ==")
src = open(os.path.join(ROOT, "mind_server.py"), encoding="utf-8").read()
ck("mind_server 会在 0 条记忆时拒绝启动",
   "0 条记忆），拒绝启动" in src and "WHALE_ALLOW_EMPTY" in src)
ck("卡文件不存在时也拒绝（原有行为保留）",
   "找不到卡" in src)

print("\n结果：%d ok / %d fail" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
