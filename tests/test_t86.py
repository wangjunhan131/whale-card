# -*- coding: utf-8 -*-
"""第 8-6 步：召回结算「同一天只加固一次」（副本上跑，绝不碰真卡）。

修的是这个真 bug：
    原来 _settle_recall_events() 按事件逐条加分，
    同一批里 N 条事件就加 N 次 GAIN_ACCESS。
    衰减每天只扣 0.144，所以一天被召回两次就足够把 w 顶到 1.0 并钉死
    （真卡实测 167 个「同日召回 ≥2 次」组合、71/623 条 w≥0.95）。

期望行为：
    同一天想起 3 次 == 加固 1 次（recall_n 记 3，access_count 只 +1）
    跨两天想起     == 加固 2 次
    已结算的再也不算第二次
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import card2

SRC = os.path.join(ROOT, "card.db")
DAY = 86400.0

ok = 0
bad = []


def check(name, cond, extra=""):
    global ok
    if cond:
        ok += 1
        print("  ✅ %s%s" % (name, ("  " + extra) if extra else ""))
    else:
        bad.append(name)
        print("  ❌ %s%s" % (name, ("  " + extra) if extra else ""))


def temp_card():
    d = tempfile.mkdtemp(prefix="t86_")
    dst = os.path.join(d, "c.db")
    s = sqlite3.connect(SRC)
    t = sqlite3.connect(dst)
    with t:
        s.backup(t)
    s.close()
    t.close()
    return d, dst


def row(db, mid):
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    r = c.execute("SELECT * FROM mem WHERE id=?", (mid,)).fetchone()
    c.close()
    return r


def add_ev(db, mid, ts):
    c = sqlite3.connect(db)
    with c:
        c.execute("INSERT INTO recall_event(mem_id, ts, query_hash, rank, source, settled)"
                  " VALUES(?,?,?,?,?,0)", (mid, ts, "t86", 1, "recall"))
    c.close()


def clear_ev(db):
    c = sqlite3.connect(db)
    with c:
        c.execute("DELETE FROM recall_event")
    c.close()


print("=" * 60)
print("T8-6 召回结算：同一天只加固一次（副本）")
print("=" * 60)

d, db = temp_card()
try:
    card = card2.MemoryCard(db)
    now = time.time()

    # ---------- [1] 同一天 3 次召回 = 一次加固 ----------
    print("\n[1] 同一天想起 3 次，只该加固 1 次")
    # region 必须显式给：这句不带情绪也不带偏好，会被判成闲聊区，
    # 而闲聊的半衰期只有 8.77 小时，两天后就掉到 0.1 —— 那验的就不是加固了。
    mid = card.remember("T86 同一天被想起三次的实验句，用来验证加固只算一次。",
                        salience=0.6, pool=card2.POOL_MEDIUM, region=card2.R_FACT)
    c = sqlite3.connect(db)
    with c:
        c.execute("UPDATE mem SET w=0.500, t_anchor=?, access_count=0, recall_n=0 WHERE id=?",
                  (now, mid))
    c.close()
    for _ in range(3):
        add_ev(db, mid, now)
    n = card._settle_recall_events()
    r = row(db, mid)
    g1 = card2.GAIN_ACCESS / (1.0 + card2.ACCESS_DIMINISH * 0)
    wn0 = card2.weight_now(0.500, r["pool"], now, now, region=r["region"])
    # 🔧 2026-11-05：增益按剩余空间收敛 —— g 要乘 (1 - wn)，
    #    否则 w 高位时 0.02 的加幅 > 0.0019 的日衰减，还是会被钉死在 1.0。
    g1 = g1 * max(0.0, 1.0 - wn0)
    want = min(1.0, wn0 + g1)
    check("结算返回 1 条记忆", n == 1, "n=%d" % n)
    check("w 只加了一次", abs(r["w"] - want) < 1e-6,
          "w=%.4f want=%.4f（三次全算会是 %.4f）" % (r["w"], want, 0.5 + g1 * 2.4))
    # 「被想起过几次」照实记：那是海马分和升长期门票读的账，不是加固次数
    check("access_count 记满 3 次", r["access_count"] == 3, "ac=%d" % r["access_count"])
    check("recall_n 记满 3 次", r["recall_n"] == 3, "rn=%d" % (r["recall_n"] or 0))
    check("事件都标 settled",
          sqlite3.connect(db).execute(
              "SELECT COUNT(*) FROM recall_event WHERE settled=0").fetchone()[0] == 0)

    # ---------- [2] 幂等：再算一遍不再加 ----------
    print("\n[2] 已经结算过的，再跑一次不该再加")
    w_before = row(db, mid)["w"]
    card._settle_recall_events()
    check("w 没动", abs(row(db, mid)["w"] - w_before) < 1e-9,
          "w=%.4f" % row(db, mid)["w"])

    # ---------- [3] 跨两天 = 两次加固 ----------
    print("\n[3] 跨两天各想起一次，该加固两次")
    clear_ev(db)
    c = sqlite3.connect(db)
    with c:
        c.execute("UPDATE mem SET w=0.300, t_anchor=?, access_count=0, recall_n=0 WHERE id=?",
                  (now, mid))
    c.close()
    add_ev(db, mid, now - 2 * DAY)
    add_ev(db, mid, now - 1 * DAY)
    card._settle_recall_events()
    r = row(db, mid)
    # 注意 [3] 开头把 access_count/recall_n 都清零了，所以这里只该有这 2 次
    check("access_count +2", r["access_count"] == 2, "ac=%d" % r["access_count"])
    check("recall_n = 2", r["recall_n"] == 2, "rn=%d" % (r["recall_n"] or 0))
    check("w 涨了两次（> 单次）", r["w"] > 0.30 + g1 * 1.2,
          "w=%.4f" % r["w"])

    # ---------- [4] 高频召回不再能钉到 1.0，但也不该越用越轻 ----------
    print("\n[4] 天天被想起一年：不该顶到 1.0，也不该反而掉下去")
    clear_ev(db)
    c = sqlite3.connect(db)
    with c:
        c.execute("UPDATE mem SET w=0.500, t_anchor=?, access_count=0, recall_n=0 WHERE id=?",
                  (now - 365 * DAY, mid))
    c.close()
    card._settle_recall_events()
    w = 0.500
    t_anchor = now - 365 * DAY
    ac = 0
    for i in range(365):
        ts = now - (365 - i) * DAY
        wn = card2.weight_now(w, "medium", t_anchor, ts, region="episode")
        g = card2.GAIN_ACCESS / (1.0 + card2.ACCESS_DIMINISH
                                 * min(ac, card2.ACCESS_DIMINISH_CAP))
        w = min(1.0, wn + g)
        t_anchor = ts
        ac += 1
    check("365 天每天想起一次后的权重 < 0.95（不钉死）", w < 0.95, "w=%.4f" % w)
    check("而且没掉到 0.5 以下（不越用越轻）", w > 0.50, "w=%.4f" % w)


finally:
    shutil.rmtree(d, ignore_errors=True)

print("\n" + "=" * 60)
print("通过 %d 项，失败 %d 项" % (ok, len(bad)))
if bad:
    for b in bad:
        print("  ❌ " + b)
    sys.exit(1)
print("全部通过 ✅")
