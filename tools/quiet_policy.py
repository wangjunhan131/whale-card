# -*- coding: utf-8 -*-
"""不每句都回 —— 什么时候该闭嘴。

不是"少说两句"的模糊感觉，是三条能数的规矩：
1) 我刚说过话（5 分钟内 ≥2 句）→ 除非被点名，否则闭嘴；
2) 别人连着聊了 6 条、没有一句叫到我 → 闭嘴（别硬插）；
3) 刚被 @ 或有人直接问我 → 开口，但同一分钟内连回 3 次就强制停 60 秒。

纯函数，只吃数据不碰网络，方便测试。
"""
import time
# 直接跑脚本时 sys.path[0] 是 tools/，卡根目录不在里面 → chatcfg import 失败、
# 设置被静默忽略（2026-11-06 实测：脚本模式下「上学时段」不生效）。补一句根目录。
import os as _os
import sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

ME_UID = 100000001
QUIET_WINDOW = 300.0        # 5 分钟
QUIET_SELF_MAX = 2          # 5 分钟内我说过 2 句就闭嘴
QUIET_SILENT_RUN = 6        # 连续 6 条没人叫我
BURST_COOL = 60.0           # 被点名也太密时，停 60 秒
BURST_N = 3

EMPHASIS = ("难受", "难过", "害怕", "哭", "崩溃", "撑不住", "好累", "想死", "救命")
ASK_MARK = ("?", "？", "吗", "呢", "怎么", "为什么", "是不是")


def _uid(r):
    try:
        return int(r.get("sender_id") or r.get("sender") or 0)
    except Exception:
        return 0


def count_self(recent, now, self_uid=ME_UID, window=QUIET_WINDOW):
    n = 0
    for r in recent:
        if _uid(r) != self_uid:
            continue
        try:
            if now - float(r.get("ts") or 0) <= window:
                n += 1
        except Exception:
            pass
    return n


def last_self_ts(recent, self_uid=ME_UID):
    # 我最近一次说话的时间（没有就返回 0）。被点名时的冷却从这一刻算。
    ts = 0.0
    for r in recent or []:
        if _uid(r) != self_uid:
            continue
        try:
            ts = max(ts, float(r.get("ts") or 0))
        except Exception:
            pass
    return ts


def silent_run(recent, self_uid=ME_UID):
    """从最近往前数：连续多少条是别人说的、且没点我。遇到我自己说的就停。"""
    n = 0
    for r in reversed(recent):
        if _uid(r) == self_uid:
            break
        if r.get("pinged"):
            break
        n += 1
    return n


def last_times(self_uid=ME_UID):
    return 0.0


def _cfg():
    """读控制台那张表；读不到就用文件里这些默认值（模块要能单独跑测试）。"""
    try:
        import chatcfg as _cc
        return _cc.load_cfg()
    except Exception:
        return {}


def should_quiet(recent, now, at_me=False, text="", self_uid=ME_UID, cfg=None):
    """返回 (quiet, why)。quiet=True 表示这条先别接。

    2026-11-06：阈值全部改成从 chatcfg 现读（控制台改完下一轮生效）。
    另加 cfg 参数：测试/预览时传一份固定配置进来，免得测试跟着线上
    参数漂（用户一改控制台，老测试就假挂）。
    """
    recent = list(recent or [])
    c = dict(cfg) if cfg is not None else _cfg()
    self_max = int(c.get("quiet.self_max", QUIET_SELF_MAX))
    silent_n = int(c.get("quiet.silent_run", QUIET_SILENT_RUN))
    window = float(c.get("quiet.window_s", QUIET_WINDOW))
    burst_n = int(c.get("quiet.burst_n", BURST_N))
    allow_emo = bool(c.get("quiet.allow_emotion", True))
    allow_q = bool(c.get("quiet.allow_question", True))
    mine = count_self(recent, now, self_uid, window)

    if at_me:
        if mine >= burst_n:
            cool = float(c.get("quiet.burst_cool_s", BURST_COOL))
            last = last_self_ts(recent, self_uid)
            if last and (now - last) < cool:
                return True, "被点名但刚连回 %d 次，冷却中（还剩 %.0f 秒）" % (
                    mine, cool - (now - last))
            return False, "被点名（冷却已过）"
        return False, "被点名"

    t = text or ""
    if allow_emo and any(w in t for w in EMPHASIS):
        return False, "强情绪"
    if allow_q and t.strip().endswith(("？", "?")) and ("你" in t or "小鲸鱼" in t):
        return False, "在问我"

    if mine >= self_max:
        return True, "%.0f 分钟内已经说了 %d 句" % (window / 60.0, mine)
    run = silent_run(recent, self_uid)
    if run >= silent_n:
        return True, "别人连聊 %d 条没叫我" % run
    return False, "可以接"
