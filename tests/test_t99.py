# -*- coding: utf-8 -*-
"""t99 · 剩下的那 14 项也接上了吗（2026-11-06）

t98 验的是第一批 16 项。这一篇验第二批：被点名、私聊必回、群里随机接话、
每群每小时、连回冷却、拆句、记忆门槛与写卡开关、语音开关、日记。
做法和 t98 一样：把 chatcfg.CFG_PATH 指到临时文件，不动真卡。
最后还做一条「面板与代码对账」——控制台上那 30 个键，必须一个不多一个不少地
等于 chatcfg.SPEC 里的键；否则就是在面板上摆了个假的旋钮。
"""
import io
import os
import re
import sys
import time
import json
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import chatcfg as C          # noqa: E402
import qq_guard as G        # noqa: E402
import quiet_policy as Q    # noqa: E402
import qq_say as S          # noqa: E402
import voice as V           # noqa: E402
import diary as D           # noqa: E402

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


_tmp = tempfile.mkdtemp(prefix="chatcfg_t99_")
C.CFG_PATH = os.path.join(_tmp, "chat_cfg.json")


def setcfg(**kw):
    C.save_cfg(kw)


print("== 被点名必接（reply.at_me）==")
setcfg(**{"reply.at_me": True})
_k = G.decide("这个到底要怎么弄才好", at_me=True)
chk("开着的时候被点名算「值得记」", _k[0] is True and "点" in _k[3], _k)
setcfg(**{"reply.at_me": False})
_k2 = G.decide("这个到底要怎么弄才好", at_me=True)
chk("关掉后就不再因为被点名进卡", _k2[0] is False, _k2)
setcfg(**{"reply.at_me": True})

print("== 记忆门槛（mem.salience_floor）==")
setcfg(**{"mem.salience_floor": 0.75})
chk("门槛 0.75 → mem_floor() 读得到", G.mem_floor() == 0.75, G.mem_floor())
setcfg(**{"mem.salience_floor": 0.9})
chk("改成 0.9 也跟着变", G.mem_floor() == 0.9, G.mem_floor())

print("== 写卡开关（mem.write_private / write_group）==")
setcfg(**{"mem.write_private": False, "mem.write_group": False})
chk("私聊写卡能关", G.cfg_get("mem.write_private", True) is False)
chk("群里写卡能关", G.cfg_get("mem.write_group", True) is False)
setcfg(**{"mem.write_private": True, "mem.write_group": True})
chk("能开回来", G.cfg_get("mem.write_private", True) is True)

print("== 每群每小时（freq.group_hour）==")
_q = {"hour": {"key": time.strftime("%Y-%m-%d %H"), "n": 0},
      "day": {"key": time.strftime("%Y-%m-%d"), "n": 0},
      "groups": {"999": {"n": 2}}, "cfg": {}}
setcfg(**{"freq.group_hour": 2})
_ok, _why = G.quota_allow(_q, 999)
chk("本群这小时满 2 句 → 不许再说", _ok is False, _why)
setcfg(**{"freq.group_hour": 5})
_ok2, _why2 = G.quota_allow(_q, 999)
chk("上限放到 5 → 可以", _ok2 is True, _why2)

print("== 群里随机接话的概率（reply.p_group）==")
setcfg(**{"reply.p_group": 0.0})
chk("概率 0 → 读回来是 0", G.cfg_get("reply.p_group", 0.35) == 0.0)
setcfg(**{"reply.p_group": 1.0})
chk("概率 1 → 读回来是 1", G.cfg_get("reply.p_group", 0.35) == 1.0)
setcfg(**{"reply.p_group": 0.35})

print("== 连回的冷却（quiet.burst_cool_s）==")
_now = time.time()
_rec = [{"sender_id": Q.ME_UID, "sender": Q.ME_UID, "ts": _now - 5, "text": "嗯"}]
setcfg(**{"quiet.burst_n": 1, "quiet.burst_cool_s": 60, "quiet.self_max": 99})
_q1, _w1 = Q.should_quiet(_rec, _now, at_me=True, text="小鲸鱼")
chk("刚回过 5 秒 → 冷却中，先别抢", _q1 is True and "冷却" in _w1, _w1)
# 注意 burst_cool_s 在 SPEC 里有下限（夹紧），拨 0 会被抬到最小值；
# 所以这里验「已经过了冷却就该放行」——把那条我自己说的话推到 60 秒前。
_rec_old = [{"sender_id": Q.ME_UID, "sender": Q.ME_UID, "ts": _now - 60,
            "text": "嗯"}]
_q2, _w2 = Q.should_quiet(_rec_old, _now, at_me=True, text="小鲸鱼")
chk("冷却过了 → 可以接", _q2 is False, _w2)
setcfg(**{"quiet.burst_n": 3, "quiet.burst_cool_s": 60})

print("== 拆句（talk.max_chars / talk.split）==")
_long = "今天下午把控制台那一面全接上了，剩下的十四个参数都在改代码，改完还得一条条验"
_parts = S.split_text(_long, 15, True)
chk("拆成多条（>1）", len(_parts) > 1, len(_parts))
chk("每一条都不超过 15 字", all(len(p) <= 15 for p in _parts), [len(p) for p in _parts])
chk("拼回去没丢字", "".join(_parts).replace(" ", "") == _long.replace(" ", ""))
_cut = S.split_text(_long, 15, False)
chk("关掉拆分 → 只截一条并加…", len(_cut) == 1 and _cut[0].endswith("…"), _cut)
setcfg(**{"talk.max_chars": 20})
chk("按配置读 20 字上限", all(len(p) <= 20 for p in S.split_text(_long, C.load_cfg()["talk.max_chars"], True)))

print("== 语音开关与字数（voice.enabled / voice.max_chars）==")
setcfg(**{"voice.enabled": False, "voice.max_chars": 40})
chk("语音能关", C.load_cfg()["voice.enabled"] is False)
setcfg(**{"voice.max_chars": 5})
_sp, _why3 = V.speakable("这句话明显超过五个字了")
chk("字数上限 5 → 太长，不许发", _sp is False and "5" in _why3, _why3)
setcfg(**{"voice.max_chars": 200})
_sp2, _why4 = V.speakable("这句话明显超过五个字了")
chk("上限放到 200 → 可以发", _sp2 is True, _why4)
setcfg(**{"voice.enabled": True, "voice.max_chars": 40})

print("== 每日日记（diary.enabled / diary.at）==")
_state = D.STATE
D.STATE = os.path.join(_tmp, "diary_last.txt")
setcfg(**{"diary.enabled": False, "diary.at": "22:20"})
_ok5, _w5 = D.ready(time.localtime(time.mktime((2026, 11, 6, 23, 0, 0, 0, 0, -1))))
chk("关掉日记 → 不写", _ok5 is False, _w5)
setcfg(**{"diary.enabled": True, "diary.at": "22:20"})
_ok6, _w6 = D.ready(time.localtime(time.mktime((2026, 11, 6, 21, 0, 0, 0, 0, -1))))
chk("21:00 还没到点 → 不写", _ok6 is False and "22:20" in _w6, _w6)
_ok7, _w7 = D.ready(time.localtime(time.mktime((2026, 11, 6, 22, 30, 0, 0, 0, -1))))
chk("22:30 到点了 → 可以写", _ok7 is True, _w7)
io.open(D.STATE, "w", encoding="utf-8").write("2026-11-06")
_ok8, _w8 = D.ready(time.localtime(time.mktime((2026, 11, 6, 23, 0, 0, 0, 0, -1))))
chk("同一天不写第二篇", _ok8 is False and "已经写过" in _w8, _w8)
D.STATE = _state

print("== 日记正文是能拼出来的（纯函数）==")
_body = D.compose("今天手酸。", ["11:00 他让我把剩下的接上", "14:40 接完了"])
chk("有标题、有事件、有我想说的", ("# " in _body) and ("今天发生过的" in _body) and ("我想说的" in _body))
_body2 = D.compose("", [])
chk("没有素材也不崩", _body2.strip().startswith("# "))

print("== 面板与代码对账 ==")
_src = io.open(os.path.join(ROOT, "console.py"), encoding="utf-8").read()
_m = re.search(r"var LIVE=\[(.*?)\];", _src, re.S)
_live = set(re.findall(r'"([a-z_]+\.[a-z_]+)"', _m.group(1))) if _m else set()
_spec = set(x[0] for x in C.SPEC)
chk("面板上的键 == 代码里的键（不摆假旋钮）", _live == _spec,
    "面板多的:%s 代码多的:%s" % (sorted(_live - _spec), sorted(_spec - _live)))
chk("一共 31 项", len(_spec) == 31, len(_spec))

print()
print("%d 过 / %d 挂" % (OK, FAIL))
if FAIL:
    sys.exit(1)
