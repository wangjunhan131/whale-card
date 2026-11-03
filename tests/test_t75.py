# -*- coding: utf-8 -*-
"""T7-5 验收：把"遗忘曲线"里的三个隐性错误钉住

2026-11-03 晚和用户一起对账算法时发现的，不是手感问题，是公式/口径问题：

  ① 长期池的 90 天保护期被分区倍率偷偷放大
     原来：age *= region_scale 之后再扣 90 天
     → flash 区（0.30 倍）的保护期变成 300 个**现实**天，self 区 150 天。
     保护期说的是"刚想起来过，多久之内不掉"，那是现实时间，
     跟这条记忆自己衰减得多慢是两件事。现在改成先扣保护期、再乘倍率。

  ② 短期池的 7 天到期判定没走分区倍率
     衰减按缩放后的时间算（flash 活得慢），到期却按真实时间砍，
     等于一边说"活得慢"一边按快的时间结算。现在两边同一把尺子。

  ③ 心境回归是线性的，20 天后精确归零
     `r = min(1, k*days)` → 第 20 天 r=1，心境**丝毫不差**地变成 0.5。
     真实的心境是"越靠近中性越慢"，永远差一点没回满。改成指数回归。
"""

import math
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import card2

DAY = card2.DAY
PASS = FAIL = 0


def chk(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✅ %s" % name)
    else:
        FAIL += 1
        print("  ❌ %s  %s" % (name, extra))


def first_decay_day(region):
    """返回这条记忆"第一次开始掉"是第几天（整数）"""
    for d in range(1, 1200):
        if card2.weight_now(1.0, card2.POOL_LONG, 0.0, d * DAY, region) < 0.99999:
            return d
    return None


print("① 长期池：90 天保护期是现实时间，不被分区倍率放大")
for region in (None, card2.R_EPISODE, card2.R_FLASH, card2.R_SELF, card2.R_HABIT):
    d = first_decay_day(region)
    chk("region=%-8s 第 %s 天开始衰减（应为 91）" % (str(region), d), d == 91)


print("② 短期池：到期按现实时间（位置是预算），但到期那一刻的权重按分区倍率")
tmp = tempfile.mkdtemp(prefix="t75_")
try:
    db = os.path.join(tmp, "t.db")
    clock = card2.Clock(start=1_700_000_000.0)
    card = card2.MemoryCard(db, clock=clock)
    now = clock.now()

    def make(kind_region, days_ago):
        mid = card.remember("测试短期到期：%s" % kind_region, salience=0.9)
        card.db.execute(
            "UPDATE mem SET pool='short', region=?, t_anchor=?, w=0.9 WHERE id=?",
            (kind_region, now - days_ago * DAY, mid))
        card.db.commit()
        return mid

    def pool_of(mid):
        row = card.db.execute("SELECT pool, w FROM mem WHERE id=?", (mid,)).fetchone()
        return None if row is None else row["pool"]

    # 到期那一刻（刚好 7 天）两条都还在，但权重差得很明显
    m_ep = make(card2.R_EPISODE, 6.9)
    m_fl = make(card2.R_FLASH, 6.9)
    w_ep = card2.wn_row(card.db.execute("SELECT * FROM mem WHERE id=?", (m_ep,)).fetchone(), now)
    w_fl = card2.wn_row(card.db.execute("SELECT * FROM mem WHERE id=?", (m_fl,)).fetchone(), now)
    chk("6.9 天时 flash 权重 %.4f 明显高于 episode %.4f" % (w_fl, w_ep), w_fl > w_ep * 3)

    # 现实 8 天：两个都该被结算（位置按现实时间腾）
    m_ep8 = make(card2.R_EPISODE, 8)
    m_fl8 = make(card2.R_FLASH, 8)
    card.tick(1.0)
    chk("episode 区 8 天：已到期、离开 short", pool_of(m_ep8) != "short")
    chk("flash 区 8 天：同样到期（位置按现实时间腾）", pool_of(m_fl8) != "short")
    card.close()
finally:
    shutil.rmtree(tmp, ignore_errors=True)


print("③ 心境回归有长尾，不会 20 天精确归零")
m10 = card2.mood_drift_home(0.10, 10.0)
m30 = card2.mood_drift_home(0.10, 30.0)
m365 = card2.mood_drift_home(0.10, 365.0)
chk("10 天：0.10 → %.3f（应 ≈ 0.257）" % m10, abs(m10 - 0.257) < 0.01)
chk("30 天：%.3f，还没到 0.5" % m30, m30 < 0.5 and m30 > 0.10)
chk("365 天：%.6f，仍不是精确 0.5" % m365, abs(m365 - 0.5) > 1e-9)
chk("单调向中性：%.3f < %.3f < %.3f" % (m10, m30, m365), m10 < m30 < m365)
expect10 = card2.MOOD_NEUTRAL + (0.10 - card2.MOOD_NEUTRAL) * math.exp(-card2.K_MOOD_HOME * 10)
chk("10 天与指数公式一致（%.3f）" % expect10, abs(m10 - expect10) < 1e-9)


print("④ 重复说同一件事：打分与验票必须用同一本账")
tmp2 = tempfile.mkdtemp(prefix="t75b_")
try:
    db2 = os.path.join(tmp2, "t.db")
    clock2 = card2.Clock(start=1_700_000_000.0)
    c2 = card2.MemoryCard(db2, clock=clock2)
    m = c2.remember("我最爱吃白米饭，一碗就够了", salience=0.6)
    m2 = c2.remember("我最爱吃白米饭，一碗就够了", salience=0.6)
    row = c2.db.execute("SELECT * FROM mem WHERE id=?", (m,)).fetchone()
    chk("同一句话重复说 → 还是同一条记忆", m2 == m)
    chk("重复说一次，access_count=1（实测 %d）" % row["access_count"], row["access_count"] == 1)
    chk("重复说一次，recall_n=1（实测 %d）——否则打分会说值得、验票却说没被想起过" % row["recall_n"],
        row["recall_n"] == 1)
    sc = c2._hippocampus_score(row)
    chk("海马分把这次重复算进去了（%.3f > 0）" % sc, sc > 0.0)
    c2.close()
finally:
    shutil.rmtree(tmp2, ignore_errors=True)


print("\n结果：%d 过 / %d 挂" % (PASS, FAIL))
raise SystemExit(0 if FAIL == 0 else 1)
