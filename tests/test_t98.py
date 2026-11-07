# -*- coding: utf-8 -*-
"""t98 · 对话参数真的生效了吗（2026-11-06）

控制台上那 30 项里，只有标了「已接」的 16 项会改变行为。这个测试逐条把
配置改成极端值，然后看对应的函数反应变没变 —— 防的是「面板上改了、代码里没读」，
今天已经踩过一次（脚本模式下 sys.path 少根目录，chatcfg 根本没 import 进来）。

做法：把 chatcfg.CFG_PATH 指到临时文件，不动真卡上的配置。
"""
import os
import sys
import json
import time
import tempfile
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import chatcfg as C          # noqa: E402
import proactive as P        # noqa: E402
import quiet_policy as Q     # noqa: E402
import qq_say as S           # noqa: E402

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


# 指向临时配置，别动真卡
_tmp = tempfile.mkdtemp(prefix="chatcfg_t98_")
C.CFG_PATH = os.path.join(_tmp, "chat_cfg.json")


def setcfg(**kw):
    C.save_cfg(kw)


def dt(h, m=0):
    return datetime.datetime(2026, 11, 6, h, m, tzinfo=P.TZ)


def row(uid, ts, text="hi", pinged=False):
    # 注意字段名：quiet_policy._uid() 认的是 sender_id / sender，不是 uid。
    # 第一版测试写成 uid，_uid() 全返回 0，「我说过几句」那几项就永远算成 0。
    return {"sender_id": uid, "sender": uid, "ts": ts, "text": text, "pinged": pinged}


print("== 频率：一天最多找你几次 ==")
setcfg(**{"freq.daily_cap": 2})
chk("cap=2 时开了 2 次就不再开", P.quota_ok({"count": 2}) is False)
chk("cap=2 时开了 1 次还能开", P.quota_ok({"count": 1}) is True)
setcfg(**{"freq.daily_cap": 9})
chk("cap 改 9 后 count=5 可以开", P.quota_ok({"count": 5}) is True)

print("== 频率：主动开口的最小间隔 ==")
setcfg(**{"freq.cooldown_min": 30})
_st = {"last_open_ts": dt(10, 0).timestamp()}
chk("间隔 30 分钟：10 分钟后还在冷却", P.cooldown_ok(_st, dt(10, 10).timestamp()) is False)
chk("间隔 30 分钟：31 分钟后可以", P.cooldown_ok(_st, dt(10, 31).timestamp()) is True)
setcfg(**{"freq.cooldown_min": 0})
chk("间隔 0：1 分钟后立刻可以", P.cooldown_ok(_st, dt(10, 1).timestamp()) is True)

print("== 不每句都回：连聊几条就闭嘴 ==")
setcfg(**{"quiet.silent_run": 3, "quiet.self_max": 99, "quiet.window_s": 300,
          "quiet.burst_n": 9, "quiet.allow_emotion": True, "quiet.allow_question": True})
_now = dt(12, 0).timestamp()
_recent = [row(1001 + i, _now - (10 - i)) for i in range(3)]
_q, _why = Q.should_quiet(_recent, _now, at_me=False, text="今天好热")
chk("连聊 3 条 → 闭嘴", _q is True, _why)
setcfg(**{"quiet.silent_run": 30})
_q2, _why2 = Q.should_quiet(_recent, _now, at_me=False, text="今天好热")
chk("阈值放宽到 30 → 可以接", _q2 is False, _why2)

print("== 不每句都回：我刚说过几句就闭嘴 ==")
setcfg(**{"quiet.self_max": 1, "quiet.silent_run": 99, "quiet.window_s": 300})
_recent2 = [row(Q.ME_UID, _now - 30, "嗯")]
_q3, _why3 = Q.should_quiet(_recent2, _now, at_me=False, text="好")
chk("我说过 1 句 → 闭嘴", _q3 is True, _why3)
setcfg(**{"quiet.self_max": 5})
_q4, _why4 = Q.should_quiet(_recent2, _now, at_me=False, text="好")
chk("上限放到 5 → 可以接", _q4 is False, _why4)

print("== 不每句都回：那个窗口有多长 ==")
setcfg(**{"quiet.self_max": 1, "quiet.window_s": 3600})
_old = [row(Q.ME_UID, _now - 1800, "半小时前说的")]
_q5, _why5 = Q.should_quiet(_old, _now, at_me=False, text="好")
chk("窗口 1 小时 → 半小时前那句话还算数", _q5 is True, _why5)
setcfg(**{"quiet.window_s": 300})
_q6, _why6 = Q.should_quiet(_old, _now, at_me=False, text="好")
chk("窗口 5 分钟 → 半小时前不算", _q6 is False, _why6)

print("== 不每句都回：被点名连回几次就强制停 ==")
setcfg(**{"quiet.burst_n": 1, "quiet.self_max": 99, "quiet.window_s": 300})
_q7, _why7 = Q.should_quiet(_recent2, _now, at_me=True, text="小鲸鱼")
chk("burst_n=1 时，已经接过 1 次就别再抢", _q7 is True, _why7)
setcfg(**{"quiet.burst_n": 3})
_q8, _why8 = Q.should_quiet(_recent2, _now, at_me=True, text="小鲸鱼")
chk("burst_n=3 时，被点名要开口", _q8 is False, _why8)

print("== 不每句都回：情绪与问句的豁免开关 ==")
setcfg(**{"quiet.silent_run": 3, "quiet.allow_emotion": False, "quiet.self_max": 99})
_q9, _why9 = Q.should_quiet(_recent, _now, at_me=False, text="我好难过")
chk("关掉情绪豁免 → 难过也不抢", _q9 is True, _why9)
setcfg(**{"quiet.allow_emotion": True, "quiet.allow_question": False})
_q10, _why10 = Q.should_quiet(_recent, _now, at_me=False, text="你在干嘛？")
chk("关掉问句豁免 → 问我也不抢", _q10 is True, _why10)

print("== 主动开口：总开关 ==")
setcfg(**{"open.enabled": False})
chk("关掉总开关 → load_cfg 读到 False", P.load_cfg()["enabled"] is False)
setcfg(**{"open.enabled": True})
chk("打开 → True", P.load_cfg()["enabled"] is True)

print("== 主动开口：饭点 / 夜里那几条由头 ==")
_kinds = lambda h: [c["kind"] for c in P.collect(dt(h), P.load_state(os.path.join(_tmp, "no_state.json")))]
chk("中午有 meal_noon", "meal_noon" in _kinds(12))
chk("21 点有 night_talk", "night_talk" in _kinds(21))

print("== 上学或工作时段：开关 + 自定义时段 ==")
setcfg(**{"open.school": True, "open.school_hours": "08:00-11:30,14:00-17:00"})
_cfg = P.load_cfg()
chk("9 点在上课时段 → 不开口", P.in_school(dt(9), _cfg["school_hours"]) is True)
chk("12 点午休 → 可以开口", P.in_school(dt(12), _cfg["school_hours"]) is False)
chk("15 点在上课时段 → 不开口", P.in_school(dt(15), _cfg["school_hours"]) is True)
chk("18 点放学 → 可以开口", P.in_school(dt(18), _cfg["school_hours"]) is False)
setcfg(**{"open.school_hours": "08:00-11:30，14:00-17:00"})
chk("中文逗号也认", P.in_school(dt(15), P.load_cfg()["school_hours"]) is True)
setcfg(**{"open.school_hours": "08:00-11:30 14:00-17:00"})
chk("空格分隔也认", P.in_school(dt(15), P.load_cfg()["school_hours"]) is True)
setcfg(**{"open.school": False})
chk("关掉开关 → 读回来是 False", P.load_cfg()["school"] is False)
setcfg(**{"open.school": True})

print("== 活跃时段：这条也要真的接上 ==")
setcfg(**{"reply.hours": "09:00-10:00"})
chk("9:30 在活跃时段里", P.in_window(dt(9, 30)) is True)
chk("12:00 不在活跃时段里", P.in_window(dt(12, 0)) is False)
setcfg(**{"reply.hours": "07:30-23:30"})
chk("改回 07:30-23:30 → 12:00 可以", P.in_window(dt(12, 0)) is True)

print("== 说话：句号与波浪号 ==")
setcfg(**{"talk.no_period": True, "talk.no_wave": True})
chk("句号被去掉", S.polish("今天好累。") == "今天好累")
chk("波浪号被去掉", S.polish("好哦～") == "好哦")
chk("句号+波浪号一起清", S.polish("今天好累。～") == "今天好累")
setcfg(**{"talk.no_period": False})
chk("关掉去句号 → 句号留着", S.polish("今天好累。") == "今天好累。")
setcfg(**{"talk.no_wave": False, "talk.no_period": True})
chk("关掉去波浪号 → 波浪号留着", S.polish("好哦～") == "好哦～")

print("== 落盘与夹紧 ==")
setcfg(**{"freq.daily_cap": 9999})
chk("超上限被夹到 40", C.load_cfg()["freq.daily_cap"] == 40)
setcfg(**{"freq.cooldown_min": -5})
chk("负数被夹到 0", C.load_cfg()["freq.cooldown_min"] == 0)
chk("不认识的键不会被写进去", "nope.key" not in C.load_cfg())

print("== 坏文件不该把它弄崩 ==")
with open(C.CFG_PATH, "w", encoding="utf-8") as f:
    f.write("{ 这不是 json")
chk("坏文件 → 全用默认值（不抛）", C.load_cfg()["freq.daily_cap"] == 8)

print()
print("%d 过 / %d 挂" % (OK, FAIL))
if FAIL:
    sys.exit(1)
