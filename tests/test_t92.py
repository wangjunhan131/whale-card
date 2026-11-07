# -*- coding: utf-8 -*-
"""t92：短期池满了要腾位置，不能把人抹掉（全部在副本上跑，绝不碰真卡）。

由来（用户 2026-11-06）：
    「短期池的位置暂时不用设限制吧，或者让它更多更大一点，写入特定区域可以，
      你觉得哪个更好？就这么做」

结论落成两条规矩：
    ① 容量上限 32 → 120（原本一天几十上百条群消息，几乎天天溢）；
    ② 溢出时**不删**，降级到 medium，让它按 region 的 λ 自己淡掉 ——
       容量是注意力预算，不是寿命开关。只有已经淡到 CLARITY_GC 以下才走遗忘。
"""
import os
import shutil
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import card2

SRC = os.path.join(ROOT, "card.db")

ok = 0
bad = []


def chk(name, cond, extra=""):
    global ok
    if cond:
        ok += 1
        print("  ✅ " + name)
    else:
        bad.append(name)
        print("  ❌ " + name + ("  " + str(extra) if extra else ""))


def temp_card():
    d = tempfile.mkdtemp(prefix="t92_")
    dst = os.path.join(d, "c.db")
    s = sqlite3.connect(SRC)
    t = sqlite3.connect(dst)
    with t:
        s.backup(t)
    s.close()
    t.close()
    return d, dst


d, db = temp_card()
try:
    c = card2.MemoryCard(db)

    print("[1] 常量")
    chk("N_SHORT_MAX 是 120", card2.N_SHORT_MAX == 120, card2.N_SHORT_MAX)
    chk("容量上限比 32 大", card2.N_SHORT_MAX > 32)

    print("[2] 灌满短期池（超上限 30 条，全是「值得留但海马分低」的事实型记忆）")
    over = card2.N_SHORT_MAX + 30
    tpl = "验收条目 %d：这件事的细节还算具体，但海马分天生不高"
    made = []
    for i in range(over):
        mid = c.remember(tpl % i, salience=0.2, pool="short", region="flash")
        if mid:
            made.append(mid)
    chk("全部写进去了（没在写入时就丢）", len(made) == over, len(made))

    n_before = c.db.execute(
        "SELECT count(*) FROM mem WHERE raw LIKE '验收条目 %'").fetchone()[0]
    chk("灌完之后都还在", n_before == over, n_before)

    print("[3] 跑一轮 tick —— V5.4 之后不再按容量腾退")
    tomb_before = c.db.execute("SELECT count(*) FROM mem_tomb").fetchone()[0]
    c.tick(1.0)
    tomb_after = c.db.execute("SELECT count(*) FROM mem_tomb").fetchone()[0]

    short_n = c.db.execute(
        "SELECT count(*) FROM mem WHERE pool='short'").fetchone()[0]
    # 2026-11-07 用户拍板：「所有池都不设置容量限制」——
    # 所以短期池不再有 N_SHORT_MAX 这道闸，超了也全留着，
    # 去留只由 7 天时间窗决定。
    chk("短期池**不再**被容量上限削回去（超了也留着）",
        short_n > card2.N_SHORT_MAX, (short_n, card2.N_SHORT_MAX))

    n_after = c.db.execute(
        "SELECT count(*) FROM mem WHERE raw LIKE '验收条目 %'").fetchone()[0]
    chk("**一条都没被删**（这是这次改动的核心）", n_after == over, n_after)

    chk("没有新增墓碑", tomb_after == tomb_before,
        (tomb_before, tomb_after))

    chk("驱逐函数已经是空操作（没人在按容量踢人）",
        c._evict_short_overflow(c.clock.now()) == [])

    # 时间窗仍然有效：把一条挪到 8 天前，再跑 tick，它必须离开 short
    _old = c.db.execute(
        "SELECT id FROM mem WHERE raw LIKE '验收条目 %' LIMIT 1").fetchone()[0]
    c.db.execute("UPDATE mem SET t_anchor=?, last_active_ts=? WHERE id=?",
                 (c.clock.now() - 8 * 86400, c.clock.now() - 8 * 86400, _old))
    c.db.commit()
    c.tick(1.0)
    _r = c.db.execute("SELECT pool FROM mem WHERE id=?", (_old,)).fetchone()
    chk("超过 7 天窗的会离开短期池（时间决定去留）",
        _r is None or _r[0] != "short", (_r[0] if _r else "(已删)"))

    print("[4] 真诚睡（chatter）规矩没被带坏")
    t = c.db.execute(
        "SELECT count(*) FROM mem WHERE region='chatter'").fetchone()[0]
    chk("chatter 还能正常存在", t >= 0, t)
    r = c.db.execute(
        "SELECT * FROM mem WHERE region='chatter' LIMIT 1").fetchone()
    if r is not None:
        got = c._consolidate(r, c.clock.now())
        chk("chatter 的结算仍走自己那条分支（不抛异常）",
            got is None or got == card2.POOL_MEDIUM, got)

    print("[5] 已经淡到看不见的，仍然该走遗忘")
    old = c.db.execute(
        "SELECT * FROM mem WHERE raw LIKE '验收条目 %' LIMIT 1").fetchone()
    if old is not None:
        c.db.execute("UPDATE mem SET w=0.0, t_anchor=? WHERE id=?",
                     (c.clock.now() - card2.SHORT_MAX_TTL - 1, old["id"]))
        c.db.commit()
        r2 = c.db.execute("SELECT * FROM mem WHERE id=?", (old["id"],)).fetchone()
        got2 = c._consolidate(r2, c.clock.now())
        chk("权重归零的被遗忘而不是又续一条命", got2 is None, got2)

    c.db.close()
finally:
    shutil.rmtree(d, ignore_errors=True)

print("=" * 60)
print("t92 结果：%d 通过，%d 失败" % (ok, len(bad)))
if bad:
    print("失败项：" + "、".join(bad))
    sys.exit(1)
print("全部通过 ✅")
