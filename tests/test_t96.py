# -*- coding: utf-8 -*-
"""t96 · 主动开口的门槛（只测纯函数，不发消息）"""
import os, sys, json, datetime, tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import proactive as P  # noqa: E402

OK = 0
FAIL = 0


def chk(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print("  ✅ %s" % name)
    else:
        FAIL += 1
        print("  ❌ %s %s" % (name, extra))


def dt(h, m=0):
    return datetime.datetime(2026, 11, 6, h, m, tzinfo=P.TZ)


def fresh():
    return P.load_state(os.path.join(tempfile.gettempdir(), "proactive_nope.json"))


print("== 时间窗口 ==")
chk("07:29 不开口", P.in_window(dt(7, 29)) is False)
chk("07:30 开始开口", P.in_window(dt(7, 30)) is True)
chk("23:29 还能说", P.in_window(dt(23, 29)) is True)
chk("23:30 闭嘴", P.in_window(dt(23, 30)) is False)
chk("凌晨 02:00 闭嘴", P.in_window(dt(2, 0)) is False)

print("== 冷却 ==")
st = fresh(); st["last_open_ts"] = dt(12, 0).timestamp()
chk("30 分钟后还在冷却", P.cooldown_ok(st, dt(12, 30).timestamp(), 120) is False)
chk("正好 120 分钟可以", P.cooldown_ok(st, dt(14, 0).timestamp(), 120) is True)
chk("没开过口直接可以", P.cooldown_ok(fresh(), dt(12, 0).timestamp()) is True)

print("== 配额 ==")
# 2026-11-06 修：quota_ok() 不带 cap 时读的是控制台那张表（freq.daily_cap），
# 而表里现在是 12、模块常量 CAP 是 8 —— 测试跟着线上配置漂，早晚要假挂。
# 这里显式传 cap，测的是"规则"而不是"今天恰好的那个数"。
st = fresh(); st["count"] = P.CAP
chk("满了不再开", P.quota_ok(st, cap=P.CAP) is False)
st["count"] = P.CAP - 1
chk("差一次还能开", P.quota_ok(st, cap=P.CAP) is True)

print("== 跨天重置 ==")
st = fresh(); st["day"] = "2026-11-05"; st["count"] = 3; st["used"] = ["sleep"]
st = P.roll_day(st, dt(9, 0))
chk("换天 count 归零", st["count"] == 0)
chk("换天 used 清空", st["used"] == [])
st["count"] = 2
st2 = P.roll_day(st, dt(10, 0))
chk("同一天不清零", st2["count"] == 2)

print("== 由头不许重复 ==")
st = fresh(); st["day"] = "2026-11-06"; st["used"] = ["meal_noon"]
c = P.collect(dt(12, 0), st)
chk("用过的 meal_noon 不再出现", all(x["kind"] != "meal_noon" for x in c))

print("== 时段由头 ==")
kinds = lambda h, m=0: [x["kind"] for x in P.collect(dt(h, m), fresh())]
chk("中午有 meal_noon", "meal_noon" in kinds(12, 0))
chk("傍晚有 meal_eve", "meal_eve" in kinds(18, 0))
chk("21 点有 night_talk", "night_talk" in kinds(21, 0))
chk("23 点有 sleep", "sleep" in kinds(23, 0))
chk("下午三点没有饭点由头", "meal_noon" not in kinds(15, 0))

print("== decide 的四道门 ==")
r, why = P.decide(fresh(), dt(3, 0), [{"kind": "x", "txt": "t"}])
chk("夜里 decide 拒绝", r is None and "时段" in why)
st = fresh(); st["count"] = 5
r, why = P.decide(st, dt(12, 0), [{"kind": "x", "txt": "t"}], cap=3, cooldown_min=60)
chk("配额满了拒绝", r is None and "次" in why)
st = fresh(); st["last_open_ts"] = dt(11, 30).timestamp()
r, why = P.decide(st, dt(12, 0), [{"kind": "x", "txt": "t"}], cap=8, cooldown_min=60)
chk("冷却中拒绝", r is None and "冷却" in why)
r, why = P.decide(fresh(), dt(12, 0), [])
chk("没由头拒绝", r is None and "由头" in why)
r, why = P.decide(fresh(), dt(12, 0), [{"kind": "meal_noon", "txt": "到饭点了"}], cap=8, cooldown_min=3)
chk("四道门都过就开口", r is not None and r["kind"] == "meal_noon")

print("== 话要短 ==")
for k in ["ping", "meal_noon", "meal_eve", "night_talk", "sleep"]:
    txts = {"ping": "群 里有人叫我"}
    for x in P.collect(dt(12, 0), fresh()):
        txts[x["kind"]] = x["txt"]
    for x in P.collect(dt(18, 0), fresh()):
        txts[x["kind"]] = x["txt"]
    for x in P.collect(dt(21, 30), fresh()):
        txts[x["kind"]] = x["txt"]
t = txts.get("meal_noon", "")
chk("由头文案 ≤20 字", all(len(v) <= 20 for v in txts.values()), txts)
chk("不带句号", all("。" not in v for v in txts.values()))
chk("不带波浪号", all("～" not in v and "~" not in v for v in txts.values()))

print("== 记一次 ==")
st = fresh(); st["count"] = 0
P.mark_sent(st, {"kind": "sleep"}, dt(23, 0).timestamp())
chk("count +1", st["count"] == 1)
chk("last_open_ts 记下", st["last_open_ts"] == dt(23, 0).timestamp())
chk("used 记下由头", "sleep" in st["used"])

print("\n%d 过 / %d 挂" % (OK, FAIL))
sys.exit(1 if FAIL else 0)
