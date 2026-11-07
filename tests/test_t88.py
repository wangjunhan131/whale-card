# -*- coding: utf-8 -*-
"""第 8-8 步：情绪弱化词分级（副本上跑，绝不碰真卡）。

修的问题（真卡实测）：
  「我有点难过」被判成 episode —— 一天后还剩 0.977，跟「我们聊了一晚上」
  一个寿命。用户原话是「今天闲聊，明天就忘得差不多了，除非有重要的事情
  或学到的知识，或者有很丰富的情绪」，而**"有点难过"不是丰富的情绪**。

根因：is_chatter 里只要命中情绪词就直接判"不是闲聊"，而 remember() 给的
emotion 只有 1.0 / 0.25 两档（看 EMO_WORDS 命中没），所以「我有点难过」
拿到 emotion=1.0，一路落到 episode 区。
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


def check(name, cond, extra=""):
    global ok
    if cond:
        ok += 1
        print("  ✅ %s%s" % (name, ("  " + extra) if extra else ""))
    else:
        bad.append(name)
        print("  ❌ %s%s" % (name, ("  " + extra) if extra else ""))


print("=" * 60)
print("T8-8 情绪弱化词分级")
print("=" * 60)

print("\n[1] 弱化情绪 → 算闲聊（明天就该淡掉）")
for s in ("我有点难过", "稍微有点烦", "我略微有点害怕",
          "今天有一点点开心", "还好吧，不太难过"):
    check("闲聊：" + s, card2.is_chatter(s, 1.0) is True,
          "region=%s" % card2.classify_region(s, 1.0))

print("\n[2] 没弱化的情绪 → 不算闲聊（该留住）")
for s in ("我很难过", "我今天特别开心", "我崩溃了", "我气得要死"):
    check("不是闲聊：" + s, card2.is_chatter(s, 1.0) is False,
          "region=%s" % card2.classify_region(s, 1.0))

print("\n[3] 弱化情绪不刻闪光灯（闪光灯永不消散，留给真正砸下来的那一刻）")
check("有点害怕 ≠ flash",
      card2.classify_region("我有点害怕", 1.0) != card2.R_FLASH,
      card2.classify_region("我有点害怕", 1.0))
check("害怕 = flash",
      card2.classify_region("我现在特别害怕", 1.0) == card2.R_FLASH,
      card2.classify_region("我现在特别害怕", 1.0))

print("\n[4] 重要 / 知识 / 偏好 依然优先（弱化词不能把它们冲掉）")
for s, want in (
        ("我有点喜欢他", card2.R_PREF),
        ("我有点明白原理了", card2.R_CHATTER),
):
    got = card2.classify_region(s, 0.25)
    if want == card2.R_CHATTER:
        check("知识句不被当闲聊：" + s, got != card2.R_CHATTER, "region=%s" % got)
    else:
        check("偏好优先：" + s, got == want, "region=%s" % got)

print("\n[5] 真实写卡：一天后该剩多少")
d = tempfile.mkdtemp(prefix="t88_")
try:
    dst = os.path.join(d, "c.db")
    s0 = sqlite3.connect(SRC)
    t0 = sqlite3.connect(dst)
    with t0:
        s0.backup(t0)
    s0.close()
    t0.close()

    DAY = 86400
    t = 1_700_000_000
    card = card2.MemoryCard(dst, card2.Clock(start=t))
    # 测公式，不测今天流量（拥挤系数按真实写量重算会把半衰期整体拖慢）
    card._load_refresh_f = lambda: None
    card2._LOAD_F.clear()
    m1 = card.remember("我有点难过", emotion=1.0, ts=t)
    m2 = card.remember("我很难过", emotion=1.0, ts=t)
    rows = {r["id"]: r for r in card.db.execute(
        "SELECT * FROM mem WHERE id IN (?,?)", (m1, m2)).fetchall()}
    check("两条都写进去了", m1 and m2, "%s %s" % (m1, m2))
    check("弱的是 chatter", rows[m1]["region"] == card2.R_CHATTER, rows[m1]["region"])
    check("强的是 episode/flash",
          rows[m2]["region"] in (card2.R_EPISODE, card2.R_FLASH), rows[m2]["region"])

    w1 = card2.wn_row(rows[m1], t + DAY)
    w2 = card2.wn_row(rows[m2], t + DAY)
    check("一天后弱情绪掉一半以上", w1 < 0.5, "w=%.4f" % w1)
    check("一天后强情绪还剩七成以上", w2 > 0.7, "w=%.4f" % w2)
    check("两者差 3 倍以上", w2 / max(w1, 1e-9) > 3.0,
          "%.4f vs %.4f = %.1f 倍" % (w2, w1, w2 / max(w1, 1e-9)))
finally:
    shutil.rmtree(d, ignore_errors=True)

print("\n" + "=" * 60)
print("通过 %d 项，失败 %d 项" % (ok, len(bad)))
if bad:
    for b in bad:
        print("  ❌ " + b)
    sys.exit(1)
print("全部通过 ✅")
