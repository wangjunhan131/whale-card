# -*- coding: utf-8 -*-
"""qq_guard 的验收：全在临时目录 + 假 NapCat 上跑，不碰真卡、不发真消息。"""
import io
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import qq_guard as G

ok = 0
bad = []


def t(name, cond, extra=""):
    global ok
    if cond:
        ok += 1
        print("  ✅ %s" % name)
    else:
        bad.append(name)
        print("  ❌ %s %s" % (name, extra))


TMP = tempfile.mkdtemp(prefix="qqguard_", dir=ROOT)
G.STATE = TMP
G.TODO = os.path.join(TMP, "todo.jsonl")
G.STATUS_F = os.path.join(TMP, "status.json")
G.QUOTA_F = os.path.join(TMP, "quota.json")
G.SEEN_F = os.path.join(TMP, "seen.json")
G.BEAT = os.path.join(TMP, "guard.heartbeat")
G.STOP = os.path.join(TMP, "stop.flag")
G.PIDF = os.path.join(TMP, "guard.pid")

try:
    # ---- 1) 未回 / 已回 两个标签分开 ----
    G._ensure()
    G.mark_many(["m1", "m2", "m3"], G.ST_UNREPLIED, by="guard")
    d = G.status_load()
    t("新消息默认是未回", all(d[k]["st"] == G.ST_UNREPLIED for k in ("m1", "m2", "m3")))

    G.mark("m2", G.ST_REPLIED)
    d = G.status_load()
    t("标已回只改那一条",
      d["m2"]["st"] == G.ST_REPLIED and d["m1"]["st"] == G.ST_UNREPLIED)
    t("已回记录里留了时间和来源",
      d["m2"]["at"] > 0 and d["m2"]["by"] == "self")

    # ---- 2) 入队 + 读队列 ----
    for i, (mid, txt) in enumerate([("m1", "第一条"), ("m2", "第二条"),
                                    ("m3", "第三条")]):
        G._append(G.TODO, {"ts": 1000 + i, "kind": "group",
                           "group_id": 111, "group_name": "测试群",
                           "sender": "某人", "text": txt, "message_id": mid})

    rows = G.read_todo(only_unreplied=True, limit=0)
    t("未回队列只给未回的（2 条）", len(rows) == 2, str([r["text"] for r in rows]))
    t("未回队列里没有 m2",
      all(r["message_id"] != "m2" for r in rows), str(rows))

    rows_all = G.read_todo(only_unreplied=False, limit=0)
    t("全量队列 3 条", len(rows_all) == 3)

    # ---- 3) 重复入队去重 ----
    G._append(G.TODO, {"ts": 1005, "kind": "group", "group_id": 111,
                       "group_name": "测试群", "sender": "某人",
                       "text": "第三条（重复）", "message_id": "m3"})
    rows = G.read_todo(only_unreplied=True, limit=0)
    t("同一条重复入队只算一次", len(rows) == 2, str(len(rows)))

    # ---- 4) 入队时不把已回的打回未回 ----
    G._mark_if_new("m2")
    t("_mark_if_new 不会把已回翻回未回",
      G.status_load()["m2"]["st"] == G.ST_REPLIED)
    G._mark_if_new("m9")
    t("_mark_if_new 给新消息打未回",
      G.status_load()["m9"]["st"] == G.ST_UNREPLIED)

    # ---- 5) 批量按时间标已回 ----
    n = G.mark_many(["m1", "m3", "m9"], G.ST_REPLIED)
    t("批量标已回 3 条", n == 3)
    t("标完之后未回队列清空",
      len(G.read_todo(only_unreplied=True, limit=0)) == 0)

    s = G.summary()
    t("账目对得上（未回 0 / 已回 4）",
      s["unreplied"] == 0 and s["replied"] == 4, str(s))

    # ---- 6) 配额 ----
    q = G.quota_load()
    G.quota_tick(q)
    ok1, _ = G.quota_allow(q)
    t("初始配额允许说话", ok1)
    for _ in range(G.QUOTA["hour_hard"]):
        G.quota_spend(q, 111)
    ok2, why2 = G.quota_allow(q)
    t("小时硬顶到了就不许说", not ok2 and "小时硬顶" in why2, why2)

    # ---- 7) 每群软上限 ----
    G.QUOTA = {"hour_hard": 999, "day_hard": 999, "group_hour_soft": 3}
    q2 = G.quota_load()
    G.quota_tick(q2)
    for _ in range(3):
        G.quota_spend(q2, 222)
    ok3, why3 = G.quota_allow(q2, 222)
    ok4, _ = G.quota_allow(q2, 333)
    t("本群软上限到了不许说", not ok3 and "软上限" in why3, why3)
    t("别的群还能说", ok4)

    # ---- 8) 天硬顶 ----
    G.QUOTA = {"hour_hard": 999, "day_hard": 2, "group_hour_soft": 999}
    q3 = G.quota_load()
    G.quota_tick(q3)
    G.quota_spend(q3, 444)
    G.quota_spend(q3, 444)
    ok5, why5 = G.quota_allow(q3)
    t("天硬顶到了就不许说", not ok5 and "当天硬顶" in why5, why5)

    # ---- 9) 配额能按天/小时自动归零 ----
    q4 = G.quota_load()
    q4["day"] = {"key": "1970-01-01", "n": 600}
    q4["hour"] = {"key": "1970-01-01 00", "n": 60}
    G.quota_tick(q4)
    t("跨小时桶自动归零", q4["hour"]["n"] == 0)
    t("跨天桶自动归零", q4["day"]["n"] == 0)

    print()
    print("结果：%d/%d 通过" % (ok, ok + len(bad)))
    if bad:
        print("失败：%s" % "、".join(bad))
        sys.exit(1)
finally:
    shutil.rmtree(TMP, ignore_errors=True)
