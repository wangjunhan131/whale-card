# -*- coding: utf-8 -*-
"""第 7 步：验收闲聊区（全部在副本上跑，绝不碰真卡）。"""
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


def temp_card():
    d = tempfile.mkdtemp(prefix="t84_", dir=ROOT)
    dst = os.path.join(d, "c.db")
    s = sqlite3.connect(SRC)
    t = sqlite3.connect(dst)
    with t:
        s.backup(t)
    s.close()
    t.close()
    return d, dst


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
print("T8-1 闲聊区验收（副本）")
print("=" * 60)

d, db = temp_card()
try:
    c = card2.MemoryCard(db)
    now = time.time()

    # ---------- [1] 衰减曲线 ----------
    print("\n[1] 衰减曲线：今天聊的，明天剩多少")
    for lay, want in (("", 0.150), ("known", 0.042), ("friend", 0.422),
                      ("close", 0.729), ("core", 0.924)):
        got = card2.weight_now(1.0, "short", now - DAY, now,
                               region="chatter", person_layer=lay)
        check("1 天后 %-6s = %.3f" % (lay or "默认", got),
              abs(got - want) < 0.02, "期望 %.3f" % want)

    # ---------- [2] 判定 ----------
    print("\n[2] 判定：哪些算闲聊")
    must_chat = ["你先停一下", "好了谢谢你啦 晚安大肥鱼", "重新上传",
                 "现在gpu占用100", "嗯", "在吗"]
    must_keep = ["我生日是5月20号", "我喜欢吃白米饭", "你要记住你就是你",
                 "我今天好难受啊", "记住我们所聊所说所经历的一切"]
    for t in must_chat:
        check("闲聊 %-22s" % t, card2.classify_region(t) == "chatter")
    for t in must_keep:
        r = card2.classify_region(t)
        check("留住 %-22s" % t, r != "chatter", r)

    # ---------- [3] 写入后分区 ----------
    print("\n[3] 写进去之后落在哪个区")
    m1 = c.remember("你先停一下我看看", source="chat")
    r1 = c.db.execute("SELECT region, about_layer FROM mem WHERE id=?",
                      (m1,)).fetchone()
    check("闲聊写进 chatter", r1["region"] == "chatter", r1["region"])

    m2 = c.remember("我生日是五月二十号", source="chat")
    r2 = c.db.execute("SELECT region FROM mem WHERE id=?", (m2,)).fetchone()
    check("生日写进 fact", r2["region"] == "fact", r2["region"])

    # ---------- [4] 按人定寿命 ----------
    print("\n[4] 按人定寿命")
    c.role_tag("使用者", "最重要的人", layer="core", uid="100000000")
    check("person_layer_of(uid) = core",
          c.person_layer_of("100000000") == "core")
    check("person_layer_of(名字) = core",
          c.person_layer_of("", "使用者") == "core")
    check("没登记的人 = 空", c.person_layer_of("999999") == "")

    m3 = c.remember("今天随便聊聊", source="chat", about_uid="100000000")
    r3 = c.db.execute("SELECT region, about_layer FROM mem WHERE id=?",
                      (m3,)).fetchone()
    check("重要的人的闲聊 = chatter", r3["region"] == "chatter")
    check("重要的人的层 = core", r3["about_layer"] == "core", r3["about_layer"])

    m4 = c.remember("群里随便说说", source="qq-group", about_uid="1234567")
    r4 = c.db.execute("SELECT about_layer FROM mem WHERE id=?", (m4,)).fetchone()
    check("没登记的群友 = known", r4["about_layer"] == "known", r4["about_layer"])

    # 同一条闲聊，对重要的人活得更久
    w_them = card2.weight_now(1.0, "short", now - DAY, now,
                              region="chatter", person_layer="core")
    w_other = card2.weight_now(1.0, "short", now - DAY, now,
                               region="chatter", person_layer="known")
    check("核心的人 1 天后 0.924 > 群友 0.042", w_them > w_other,
          "%.3f vs %.3f" % (w_them, w_other))

    # ---------- [5] 兜底 ----------
    print("\n[5] 兜底：闲聊不许进长期/底色")
    c.db.execute("UPDATE mem SET pool='short', w=0.99, salience=1.0,"
                 " access_count=9, recall_n=9 WHERE id=?", (m1,))
    c.db.commit()
    row = c.db.execute("SELECT * FROM mem WHERE id=?", (m1,)).fetchone()
    pool_before = row["pool"]
    c._consolidate(row, time.time())
    pool_after = c.db.execute("SELECT pool FROM mem WHERE id=?",
                              (m1,)).fetchone()[0]
    check("_consolidate 不让闲聊进 long",
          not (pool_before != "long" and pool_after == "long"),
          "%s → %s" % (pool_before, pool_after))

    out = c.promote_core(m1, card2.TIER_CORE_MUTABLE, kind="hobby")
    check("promote_core 拒绝闲聊", not out.get("ok"), str(out.get("reason")))

    # ---------- [6] 关系变了，命也跟着变 ----------
    print("\n[6] 关系升层之后重算")
    c.role_tag("路人甲", "认识的人", layer="known", uid="555")
    m5 = c.remember("随便一句", source="qq-group", about_uid="555")
    lay1 = c.db.execute("SELECT about_layer FROM mem WHERE id=?",
                        (m5,)).fetchone()[0]
    c.role_tag("路人甲", "朋友", layer="friend", uid="555")
    res = c.refresh_about_layers()
    lay2 = c.db.execute("SELECT about_layer FROM mem WHERE id=?",
                        (m5,)).fetchone()[0]
    check("升层前 = known", lay1 == "known", lay1)
    check("升层后 = friend", lay2 == "friend", lay2)
    check("refresh 报告改了几条", res["changed"] >= 1, str(res))

finally:
    shutil.rmtree(d, ignore_errors=True)

print("\n" + "=" * 60)
print("结果：%d 通过，%d 失败" % (ok, len(bad)))
if bad:
    print("失败项：" + "、".join(bad))
    sys.exit(1)
print("全过 ✅")
