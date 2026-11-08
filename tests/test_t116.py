# -*- coding: utf-8 -*-
"""t116：V5.6 —— 短期池 / 中期池各加一个附属小池

用户 2026-11-08 的原话（m36716 / m36724）：
  「在短期里被反复召回的东西，会被放到那个短期池的附属小区里面，
    那个附属小区里面的遗忘时间就会更长一点，它就可以留存得更久；
    如果在…附属小区里面又被经常提起，就可以转入中期池。」
  「我说的是加一个小池」——所以它是真池子，不是给池子挂个标记。

两条我自己加的判断：
  · 进小池要「跨天」：同一场对话里连点十次不算，得不同日子各想起一次。
    不然会重演 11-04 那个 bug（越没用的越容易被捞出、越捞越结实）。
  · 被想起**不再往前推 t_anchor**：续命靠搬进小池，不靠把寿命重新开始算。
"""
import os
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
DAY = card2.DAY


def ck(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s %s" % (name, extra))


def new_card():
    d = tempfile.mkdtemp(prefix="t116_")
    db = os.path.join(d, "card.db")
    copy_card_db(os.path.join(ROOT, "card.db"), db)
    return card2.MemoryCard(db), d


def sql(c, q, args=()):
    """测试里直接改库（MemoryCard 没有 self.lock，锁在 mind.lock）"""
    cur = c.db.cursor()
    cur.execute(q, args)
    c.db.commit()
    return cur


def put(c, text, pool, age_days, w=0.6, rn=0, rd=0, salience=0.6):
    """直接塞一条记忆并把它摆到指定池、指定年龄"""
    now = c.clock.now()
    mid = c.remember(text, salience=salience, pool=card2.POOL_LONG,
                     ts=now - age_days * DAY)
    # region 也钉成 episode：文案短容易被判成 chatter，而 chatter 的
    # 衰减倍率是 24 倍，会让这条断言测的变成"区倍率"而不是"加固"。
    sql(c, "UPDATE mem SET pool=?, w=?, t_anchor=?, recall_n=?,"
           " recall_days=?, region='episode' WHERE id=?",
        (pool, w, now - age_days * DAY, rn, rd, mid))
    return mid


def row(c, mid):
    cur = c.db.cursor()
    return cur.execute("SELECT * FROM mem WHERE id=?", (mid,)).fetchone()


# ---------------------------------------------------------------- 1
print("== 1. 常量与小池的衰减曲线 ==")
ck("短期小池 21 天 > 短期 7 天",
   card2.SHORT_WARM_TTL == 21 * DAY and card2.SHORT_WARM_TTL > card2.SHORT_MAX_TTL,
   (card2.SHORT_WARM_TTL, card2.SHORT_MAX_TTL))
ck("中期小池 90 天", card2.MEDIUM_WARM_TTL == 90 * DAY, card2.MEDIUM_WARM_TTL)
ck("小池衰减放慢到 1/3", abs(card2.WARM_SLOW - 1.0 / 3.0) < 1e-9)

now = time.time()
w_short = card2.weight_now(0.8, card2.POOL_SHORT, now - 10 * DAY, now, "episode")
w_warm = card2.weight_now(0.8, card2.POOL_SHORT_WARM, now - 10 * DAY, now,
                          "episode")
ck("同样 10 天，小池里剩下的权重更多", w_warm > w_short * 1.5,
   "short=%.3f warm=%.3f" % (w_short, w_warm))
ck("小池在 21 天时还看得见（>0.05）",
   card2.weight_now(0.8, card2.POOL_SHORT_WARM, now - 21 * DAY, now,
                    "episode") > 0.05)

ck("stage_factor：短期小池 1/3",
   abs(card2.stage_factor(card2.POOL_SHORT_WARM) - card2.WARM_SLOW) < 1e-9)
ck("stage_factor：中期小池比中期慢",
   card2.stage_factor(card2.POOL_MEDIUM_WARM) < card2.stage_factor(card2.POOL_MEDIUM))

# ---------------------------------------------------------------- 2
print("== 2. 召回不再续命，但会记「跨了几天」 ==")
c, d = new_card()
try:
    now = c.clock.now()
    mid = put(c, "t116 召回不许续命", card2.POOL_SHORT, age_days=3.0, w=0.6)
    before = float(row(c, mid)["pool_in_ts"])
    base_w = card2.wn_row(row(c, mid), now)      # 结算前的"应有权重"
    for k in (0, 1):
        sql(c, "INSERT INTO recall_event(mem_id, ts, query_hash, rank, source,"
               " settled) VALUES(?,?,?,?,?,0)",
            (mid, now - k * 3600, "h116", 1, "t116"))
    c._settle_recall_events()          # 同一天两条 → 只加固一次、只算一天
    r1 = row(c, mid)
    # 一天最多加一次 GAIN_ACCESS（还要乘剩余空间）——比 0.6 那个旧断言准
    ck("同一天两次召回只加固一次",
       base_w < float(r1["w"]) <= base_w + card2.GAIN_ACCESS + 1e-6,
       "base=%.3f after=%.3f" % (base_w, float(r1["w"])))
    ck("同一天只记 1 个「召回日」", int(r1["recall_days"]) == 1, r1["recall_days"])
    # 想起只涨权重、不延长池内寿命：pool_in_ts 必须原地不动
    ck("被想起不延长池内年龄", abs(float(r1["pool_in_ts"]) - before) < 1.0,
       (before, r1["pool_in_ts"]))
    ck("但权重参考点 t_anchor 会推进（所以 w 才能累加）",
       float(r1["t_anchor"]) > before + 60, (before, r1["t_anchor"]))
    ck("recall_n 记了 2 次（另一本账）", int(r1["recall_n"]) >= 2, r1["recall_n"])

    sql(c, "INSERT INTO recall_event(mem_id, ts, query_hash, rank, source,"
           " settled) VALUES(?,?,?,?,?,0)", (mid, now + DAY, "h116", 1, "t116"))
    c._settle_recall_events()
    r2 = row(c, mid)
    ck("第二天被想起 → 召回日变 2", int(r2["recall_days"]) == 2, r2["recall_days"])

    # ------------------------------------------------------------ 3
    print("== 3. 短期 → 短期小池 → 中期 ==")
    mid2 = put(c, "t116 反复被想起的那句", card2.POOL_SHORT,
               age_days=2.0, w=0.6, rn=2, rd=2)
    c.tick(10.0)
    r3 = row(c, mid2)
    ck("跨 2 天 + 被想起 2 次 → 进短期小池",
       r3 is not None and r3["pool"] == card2.POOL_SHORT_WARM,
       r3["pool"] if r3 else None)
    ck("进小池时抬到存活下限",
       r3 is not None and float(r3["w"]) >= card2.W_FLOOR_MEDIUM - 1e-6,
       r3["w"] if r3 else None)
    ck("进小池时不跨级进中期",
       r3 is not None and r3["pool"] != card2.POOL_MEDIUM)

    sql(c, "UPDATE mem SET recall_n=3, recall_days=3 WHERE id=?", (mid2,))
    c.tick(10.0)
    r4 = row(c, mid2)
    ck("小池里跨 3 天再被想起 → 升中期",
       r4 is not None and r4["pool"] in (card2.POOL_MEDIUM, card2.POOL_LONG),
       r4["pool"] if r4 else None)

    # ------------------------------------------------------------ 4
    print("== 4. 小池到期就走（不退回主池） ==")
    old_short = put(c, "t116 小池里躺了 22 天", card2.POOL_SHORT_WARM,
                    age_days=22.0, w=0.5, rn=1, rd=1)
    old_med = put(c, "t116 中期小池躺了 91 天", card2.POOL_MEDIUM_WARM,
                  age_days=91.0, w=0.5, rn=1, rd=1)
    c.tick(10.0)
    ck("短期小池 21 天到期被忘", row(c, old_short) is None)
    ck("中期小池 90 天到期被忘", row(c, old_med) is None)

    cur = c.db.cursor()
    tomb = cur.execute(
        "SELECT reason FROM mem_tomb WHERE mid=? ORDER BY rowid DESC LIMIT 1",
        (old_short,)).fetchone()
    ck("墓碑写明是小池到期", tomb is not None and "小池" in (tomb["reason"] or ""),
       tomb["reason"] if tomb else None)

    # ------------------------------------------------------------ 5
    print("== 5. 中期池也有它自己的小池 ==")
    mid3 = put(c, "t116 中期里被反复想起", card2.POOL_MEDIUM,
               age_days=5.0, w=0.5, rn=3, rd=3)
    c.tick(10.0)
    r5 = row(c, mid3)
    ck("中期里跨 3 天 + 3 次 → 进中期小池",
       r5 is not None and r5["pool"] == card2.POOL_MEDIUM_WARM,
       r5["pool"] if r5 else None)

    # ------------------------------------------------------------ 6
    print("== 6. 统计里看得见两个小池 ==")
    st = c.stats()
    pools = st.get("pools") or {}
    ck("stats 有小池的键", card2.POOL_SHORT_WARM in pools
       and card2.POOL_MEDIUM_WARM in pools, list(pools.keys())[:8])
finally:
    try:
        c.close()
    except Exception:
        pass

print("\n%d ok / %d fail" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
