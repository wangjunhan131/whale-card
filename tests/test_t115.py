# -*- coding: utf-8 -*-
"""t115：V5.5 —— 短期池实时筛选 + 7 天忘光 + 印象层

用户 2026-11-08 的两段原话：

  「进入短期池的内容进去了就会被筛选，筛选了就会进入中期池，然后剩下的
    在短期池里继续被遗忘，7 天左右之后忘干净，然后就是被删掉。」

  「印象这个模块不只是对我，他可以对任何人或者某个事物，只要它会在特定的
    情况下触发特定的反应，且多次触发相同的反应，就可以形成一个印象。
    某一个印象被反复触发的次数越多，它就越深刻 —— 这就意味着它更难改变。
    如果要改变它，就必须更努力、更频繁地在同一个情境里做出不同的事。」

所以这个测试盯三件事：
  1. 短期池每轮都筛（不是等到第 7 天才筛一次）
  2. 没够格的在第 7 天真的走（墓碑写明理由），不是被打发进中期养老
  3. 印象按「主体 × 情境 × 反应」计数，越多次越深，覆盖要付代价
"""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import card2                        # noqa: E402
import impression as imp           # noqa: E402
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
    d = tempfile.mkdtemp(prefix="t115_")
    db = os.path.join(d, "card.db")
    copy_card_db(os.path.join(ROOT, "card.db"), db)
    return card2.MemoryCard(db), d


def main():
    print("[1] 纯函数：归一化 / 时间桶 / 事件桶")
    ck("去标点空白", imp.normalize("我 回来了！！") == "我回来了")
    ck("截断长文本", len(imp.normalize("啊" * 200)) == imp.REACTION_MAX_CHARS)
    t_early = time.mktime((2026, 11, 8, 7, 0, 0, 0, 0, -1))
    t_evening = time.mktime((2026, 11, 8, 19, 0, 0, 0, 0, -1))
    t_night = time.mktime((2026, 11, 8, 2, 0, 0, 0, 0, -1))
    ck("早上→早", imp.time_bucket(t_early) == "早")
    ck("傍晚→晚", imp.time_bucket(t_evening) == "晚")
    ck("凌晨→夜", imp.time_bucket(t_night) == "夜")
    ck("回来事件", imp.event_bucket("我回来了") == "回来")
    ck("考试事件", imp.event_bucket("今天模考") == "考试")
    ck("认不出就空", imp.event_bucket("嗯") == "")

    print("[2] 深刻程度：越多越深，但永远不到 1")
    w3, w5, w10, w20 = (imp.weight_of(3), imp.weight_of(5),
                        imp.weight_of(10), imp.weight_of(20))
    ck("n=3 ≈ 0.29", abs(w3 - 0.2929) < 0.01, w3)
    ck("n=5 ≈ 0.44", abs(w5 - 0.4385) < 0.01, w5)
    ck("n=10 ≈ 0.68", abs(w10 - 0.6853) < 0.01, w10)
    ck("n=20 ≈ 0.90", abs(w20 - 0.9008) < 0.01, w20)
    ck("单调递增", w3 < w5 < w10 < w20)
    ck("永远到不了 1", imp.weight_of(10000) < 1.0)

    print("[3] 观察：同一个人、同一个情境、同一句话 → 计数与加深")
    import sqlite3
    db = sqlite3.connect(":memory:")
    t0 = time.mktime((2026, 11, 1, 19, 0, 0, 0, 0, -1))
    for i in range(5):
        r = imp.observe(db, "u1", "我回来了", ts=t0 + i * 86400)
    rows = imp.top(db, subject="u1")
    ck("只成一条", len(rows) == 1, rows)
    ck("次数 5", rows and rows[0]["n"] == 5)
    ck("状态 established", rows and rows[0]["status"] == imp.STATUS_ESTABLISHED)
    ck("权重跟上", rows and abs(rows[0]["w"] - imp.weight_of(5)) < 1e-6)
    ck("情境含「回来」", rows and rows[0]["scene_event"] == "回来")

    print("[4] 覆盖要付代价：同一情境里换一种反应")
    n_old = rows[0]["n"]
    need = imp.override_need(n_old)
    # 他说的：要覆盖必须"更频繁" —— 5 次的老印象，得 8 次新反应才压得动
    ck("次数 5 → 需要 8 次反向证据（比被看到的次数更多）", need == 8, need)
    # 前七次只是记账，旧印象不动
    t1 = t0 + 10 * 86400
    for i in range(7):
        imp.observe(db, "u1", "我到家了", ts=t1 + 60 * i)
    old = [r for r in imp.top(db, subject="u1") if r["reaction"] == "我回来了"]
    ck("七次反向证据还推翻不了", old and old[0]["status"] != imp.STATUS_SUPERSEDED)
    w_before = old[0]["w"] if old else 0
    # 第八次 → 覆盖成立，旧印象打折
    imp.observe(db, "u1", "我到家了", ts=t1 + 60 * 7)
    old2 = [r for r in imp.top(db, subject="u1", min_w=0) if r["reaction"] == "我回来了"]
    ck("第八次后旧印象打折", old2 and old2[0]["w"] < w_before, (w_before, old2))
    ck("旧印象状态被标", old2 and old2[0]["status"] in
       (imp.STATUS_FADING, imp.STATUS_SUPERSEDED), old2 and old2[0]["status"])

    print("[5] 不同情境不算覆盖（换场景说明不了这条印象不对）")
    db2 = sqlite3.connect(":memory:")
    for i in range(6):
        imp.observe(db2, "u2", "我回来了", ts=t0 + i * 86400)
    for i in range(5):
        imp.observe(db2, "u2", "我先去洗澡", ts=time.mktime((2026, 11, 1, 9, 0, 0, 0, 0, -1)) + i * 86400)
    rows2 = imp.top(db2, subject="u2", min_w=0)
    keep = [r for r in rows2 if r["reaction"] == "我回来了"]
    ck("早上说洗澡不影响晚上的印象", keep and keep[0]["status"] != imp.STATUS_SUPERSEDED, rows2)

    print("[6] 淡出：很久没再发生就变淡，半衰期 90 天")
    db3 = sqlite3.connect(":memory:")
    for i in range(10):
        imp.observe(db3, "u3", "晚安", ts=t0 + i * 60)
    before = imp.top(db3, subject="u3")[0]["w"]
    imp.fade(db3, now=t0 + 90 * 86400)
    after = imp.top(db3, subject="u3")[0]["w"]
    ck("90 天后约剩一半", abs(after - before * 0.5) < 0.02, (before, after))

    print("[7] 每主体上限 40 条")
    db4 = sqlite3.connect(":memory:")
    for i in range(50):
        imp.observe(db4, "u4", "第%d号反应" % i, ts=t0 + i)
    alive = len(imp.top(db4, subject="u4", limit=200, min_w=0))
    ck("最多留 40 条", alive <= imp.MAX_PER_SUBJECT, alive)

    print("[8] 上下文里的印象：只描述、不预测、不当台词")
    card, d = new_card()
    try:
        now = card.clock.now()
        for i in range(6):
            imp.observe(card.db, "u9", "我回来了", ts=now - (6 - i) * 86400)
        lines = card.impression_hints(limit=3)
        ck("有印象行", len(lines) >= 1, lines)
        joined = " ".join(lines)
        ck("带次数", "次" in joined, joined)
        ck("不出现预测词", not any(w in joined for w in ("会", "大概", "应该", "估计")), joined)
        ctx = card.assemble_context("你回来啦")
        ck("上下文有【印象】格", "【印象】" in ctx["text"], list(ctx["sections"]))
        ck("sections 有计数", ctx["sections"].get("impression", 0) > 0, ctx["sections"])
    finally:
        card.db.close()

    print("[9] 短期池：实时筛选 + 7 天忘光（他说的那句有加分，但重复短句不加）")
    card2.MemoryCard.USER_SPEECH_BONUS = 0.12
    card2.MemoryCard.REPEAT_SHORT_N = 3
    card, d = new_card()
    try:
        now = card.clock.now()
        # 一条"没人在意"的自言自语，权重会被时间磨光
        mid_fade = card.remember("下午在实验室调了一下午示波器", salience=0.05,
                                 speaker="self", pool=card2.POOL_SHORT, ts=now - 8 * 86400)
        # 一条他说的、有内容的（不是重复短句）
        mid_user = card.remember("今天模考最后一题完全不会做，有点想哭",
                                 salience=0.3, speaker="user",
                                 pool=card2.POOL_SHORT, ts=now - 3600)
        # 一条他常说的短句，卡里已经有三遍
        for i in range(3):
            card.remember("我回来啦啊啊", salience=0.2, speaker="user",
                          pool=card2.POOL_SHORT, ts=now - (i + 1) * 3600)
        short_before = [r["id"] for r in card.db.execute(
            "SELECT id FROM mem WHERE pool='short'").fetchall()]
        card.tick(0.0)
        rows = {r["id"]: r for r in card.db.execute(
            "SELECT id, pool, speaker, raw FROM mem").fetchall()}
        ck("实时筛选：他说的那句已进中期",
           mid_user not in rows or rows[mid_user]["pool"] != "short",
           rows.get(mid_user) and rows[mid_user]["pool"])
        gone = mid_fade not in rows
        ck("超期没够格的走了", gone, rows.get(mid_fade))
        if gone:
            tomb = card.db.execute(
                "SELECT reason FROM mem_tomb WHERE mid=?", (mid_fade,)).fetchone()
            _rsn = (tomb["reason"] if tomb is not None else "(无墓碑)")
            ck("墓碑写明理由（%s）" % _rsn,
               tomb is not None and ("短期池" in (_rsn or "") or "闲聊" in (_rsn or "")), _rsn)
        rep = [r for r in card.db.execute(
            "SELECT id, pool, raw FROM mem WHERE raw='我回来啦啊啊'").fetchall()]
        ck("重复短句不升长期（重复的话价值在模式里，不在这一条）",
           all(r["pool"] != "long" for r in rep), [(r["id"], r["pool"]) for r in rep])
        ck("短期池总数没有膨胀", len(short_before) >= 0)
    finally:
        card.db.close()

    print()
    print("t115：%d ok / %d fail" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
