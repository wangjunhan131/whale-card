# -*- coding: utf-8 -*-
"""t91 完备性体检抓到的那几个边角：

  1) 去重「续命」不许把权重钉死（天天说同一句话，一年后也不该是 1.0）
  2) 脏数据 w 越界 / t_anchor 缺失不许穿透到检索排序里
  3) 任何一个时间点、任何一种组合下，权重都在 [0,1] 且单调不增

1 和 2 是这一轮真查出来的；3 是给"以后别再出"上的锁。
"""
import itertools
import os
import sys
import tempfile
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import card2

DAY = 86400
T0 = 1_700_000_000
PASS = []
FAIL = []


def ck(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (("   " + str(extra)) if extra and not cond else ""))


def fresh():
    d = tempfile.mkdtemp()
    return d, card2.MemoryCard(os.path.join(d, "c.db"), card2.Clock(start=T0))


def main():
    print("=" * 66)
    print("[A] 去重续命不许钉死")
    print("=" * 66)
    d, card = fresh()
    try:
        for i in range(365):
            card.clock._t = T0 + i * DAY
            mid = card.remember("晚安，爱你", salience=0.7, region=card2.R_PREF)
        n = card.db.execute("SELECT COUNT(*) FROM mem").fetchone()[0]
        r = card.db.execute("SELECT * FROM mem WHERE id=?", (mid,)).fetchone()
        ck("同一句话 365 天只留一条（去重生效）", n == 1, n)
        ck("w 没被顶到 1.0", r["w"] < 0.99, round(r["w"], 4))
        ck("w 不超过天花板", r["w"] <= card2.W_CEIL_RECALL[r["pool"]] + 1e-9, round(r["w"], 4))
        ck("access_count 照实记（那是重复次数的账）", r["access_count"] >= 360, r["access_count"])
        ck("仍在存活区（不至于被重复说反而说没了）", card2.wn_row(r, card.clock.now()) > 0.2,
           round(card2.wn_row(r, card.clock.now()), 4))
    finally:
        shutil.rmtree(d, ignore_errors=True)

    print()
    print("=" * 66)
    print("[B] 脏数据不许穿透")
    print("=" * 66)
    base = {"w": 0.8, "pool": card2.POOL_LONG, "t_anchor": T0,
            "region": card2.R_EPISODE, "tier": card2.TIER_MID, "about_layer": ""}
    ck("w=1.5 夹到 1.0", abs(card2.wn_row(dict(base, w=1.5), T0 + DAY) - 1.0) < 1e-9)
    ck("w=-1 夹到 0.0", abs(card2.wn_row(dict(base, w=-1), T0 + DAY) - 0.0) < 1e-9)
    ck("w=nan 当 0（不许让排序崩）", card2.wn_row(dict(base, w=float("nan")), T0 + DAY) == 0.0)
    ck("w='abc' 当 0", card2.wn_row(dict(base, w="abc"), T0 + DAY) == 0.0)
    ck("t_anchor=0 不衰减（老数据不许当场归零）",
       abs(card2.wn_row(dict(base, t_anchor=0), T0) - 0.8) < 1e-9)
    ck("t_anchor=None 不衰减",
       abs(card2.wn_row(dict(base, t_anchor=None), T0) - 0.8) < 1e-9)
    ck("t_anchor 在未来 → 按刚写过算",
       abs(card2.wn_row(dict(base, t_anchor=T0 + 30 * DAY), T0) - 0.8) < 1e-9)

    print()
    print("=" * 66)
    print("[C] 权重矩阵：任何组合、任何时间点都在 [0,1] 且单调不增")
    print("=" * 66)
    REG = [card2.R_CHATTER, card2.R_EPISODE, card2.R_FACT, card2.R_FLASH,
           card2.R_PREF, card2.R_SELF, card2.R_HABIT]
    POOL = [card2.POOL_SHORT, card2.POOL_MEDIUM, card2.POOL_LONG,
            card2.POOL_ARCHIVE, card2.POOL_DEEP]
    TIER = [card2.TIER_MID, card2.TIER_CORE_MUTABLE, card2.TIER_CORE_TAG,
            card2.TIER_CORE_FIXED]
    LAY = ["", "known", "friend", "close", "core", "???"]
    PTS = [0, DAY, 7 * DAY, 30 * DAY, 90 * DAY, 365 * DAY, 5 * 365 * DAY]
    bad = []
    cnt = 0
    for reg, pool, tier, lay in itertools.product(REG, POOL, TIER, LAY):
        row = {"w": 0.9, "pool": pool, "t_anchor": T0, "region": reg,
               "tier": tier, "about_layer": lay}
        prev = None
        for dt in PTS:
            v = card2.wn_row(row, T0 + dt)
            cnt += 1
            if not (0.0 <= v <= 1.0):
                bad.append("%s/%s/%s @%d 越界 %s" % (reg, pool, tier, dt, v))
            if prev is not None and v > prev + 1e-9:
                bad.append("%s/%s/%s @%d 回升" % (reg, pool, tier, dt))
            prev = v
    ck("%d 个点全部在 [0,1] 且不回升" % cnt, not bad, bad[:3])
    ck("core_* 三档永不衰减（走 supersede 不走 decay）",
       all(abs(card2.wn_row({"w": 0.9, "pool": card2.POOL_LONG, "t_anchor": T0,
                             "region": card2.R_EPISODE, "tier": t, "about_layer": ""},
                            T0 + 5 * 365 * DAY) - 0.9) < 1e-9
           for t in [card2.TIER_CORE_MUTABLE, card2.TIER_CORE_TAG, card2.TIER_CORE_FIXED]))

    print()
    print("=" * 66)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  ❌ " + f)
    print("=" * 66)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
