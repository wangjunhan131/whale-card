# -*- coding: utf-8 -*-
"""t97 · 不每句都回的判断（纯函数）"""
import os, sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import quiet_policy as Q  # noqa: E402

OK = FAIL = 0


def chk(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print("  ✅ %s" % name)
    else:
        FAIL += 1
        print("  ❌ %s %s" % (name, extra))


NOW = 1793940000.0


def msg(uid, text="", ts=None, pinged=False):
    return {"sender_id": uid, "text": text, "ts": NOW - 10 if ts is None else ts, "pinged": pinged}


OTHERS = [msg(101 + i, "随便说点") for i in range(8)]

print("== 别人连聊没人叫我 → 闭嘴 ==")
q, why = Q.should_quiet(OTHERS, NOW)
chk("连续 6 条以上闭嘴", q is True and "没叫我" in why, why)
q, _ = Q.should_quiet(OTHERS[:3], NOW)
chk("只聊了 3 条可以接", q is False)

print("== 我刚说过话 ==")
# 2026-11-06 修：阈值现在从控制台表里现读（用户把 self_max 调到了 6），
# 测试跟着线上参数漂就假挂了。传固定 cfg，测的是规则本身。
FIX_CFG = {"quiet.self_max": 2, "quiet.silent_run": 6, "quiet.window_s": 300,
           "quiet.burst_n": 3, "quiet.burst_cool_s": 60,
           "quiet.allow_emotion": True, "quiet.allow_question": True}
two = [msg(Q.ME_UID, "我刚才说了", ts=NOW - 30)] + [msg(Q.ME_UID, "又说一句", ts=NOW - 60)] + OTHERS[:2]
q, why = Q.should_quiet(two, NOW, cfg=FIX_CFG)
chk("5 分钟内说了 2 句就闭嘴", q is True and "说了 2 句" in why, why)
old = [msg(Q.ME_UID, "很久以前", ts=NOW - 3600)] * 3 + OTHERS[:2]
q, _ = Q.should_quiet(old, NOW)
chk("一小时前的话不算", q is False)

print("== 例外：被点名 / 强情绪 / 在问我 ==")
q, why = Q.should_quiet(OTHERS, NOW, at_me=True)
chk("被点名要开口", q is False and why == "被点名", why)
q, why = Q.should_quiet(OTHERS + [msg(9, "我有点难受")], NOW, text="我有点难受")
chk("强情绪要开口", q is False and why == "强情绪", why)
q, why = Q.should_quiet(OTHERS, NOW, text="你觉得怎么样？")
chk("在问我 要开口", q is False and why == "在问我", why)
q, why = Q.should_quiet(OTHERS, NOW, text="今天好热")
chk("跟我无关的陈述不豁免", q is True, why)

print("== 被点名也不能刷屏 ==")
burst = [msg(Q.ME_UID, "接一句", ts=NOW - 10), msg(Q.ME_UID, "接两句", ts=NOW - 20), msg(Q.ME_UID, "接三句", ts=NOW - 30)]
q, why = Q.should_quiet(burst, NOW, at_me=True, cfg=FIX_CFG)
chk("一分钟接 3 次强制停", q is True and "3 次" in why, why)

print("== 我自己说的会打断连聊计数 ==")
mixed = OTHERS[:4] + [msg(Q.ME_UID, "我接了", ts=NOW - 20)] + OTHERS[:4]
chk("我刚说过，连聊计数归零", Q.silent_run(mixed) == 4, Q.silent_run(mixed))
chk("别人点我，计数也归零", Q.silent_run(OTHERS[:2] + [msg(9, "@你", pinged=True)] + OTHERS[:2]) == 2)

print("\n%d 过 / %d 挂" % (OK, FAIL))
sys.exit(1 if FAIL else 0)
