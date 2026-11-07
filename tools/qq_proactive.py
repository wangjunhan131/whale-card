# -*- coding: utf-8 -*-
"""主动开口（V5.3.4）—— 把 motiv 的「想说话」接到真的发出去。

用户 2026-11-07 授权：可以主动外发消息。所以这个脚本做事很克制：
  1. 先问卡：「现在想不想说话、想说什么」（POST /motiv/tick）
  2. 卡自己会按 安静 / 上课 / 距上次开口不足 1 小时 / 今天额度用完 来否决
  3. 只在拿到 reach_out 或 care_check 时才组一句短话
  4. 发之前再自己核一遍时间窗（不指望上游一定传对）
  5. 发完告诉卡「做了」（POST /motiv/done spoke=True），额度才会被记上

默认 **不发送**：必须显式 python tools/qq_proactive.py --send。
理由：主动开口是会打扰人的动作，默认安全那一侧。
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import requests            # noqa: E402
import chatcfg             # noqa: E402

BASE = os.environ.get("WHALE_CARD_BASE", "http://127.0.0.1:8848")
MAIN_UID = os.environ.get("WHALE_MAIN_UID", "")

# 只在「真的很像人」的时机用的短句。不写"我想你了"这种一天能说十遍的，
# 写"具体到某件东西"的 —— 具体才不像模板。
LINES = {
    "reach_out": [
        "在忙吗",
        "忽然想起你上次说的那个",
        "今天过得怎么样",
        "我先说一句：我这边一切正常",
    ],
    "care_check": [
        "记得吃饭",
        "别熬太晚",
        "要是累了就早点睡",
    ],
}


def _get(path, **params):
    r = requests.get(BASE + path, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def _post(path, payload):
    r = requests.post(BASE + path, json=payload, timeout=20)
    r.raise_for_status()
    return r.json()


def in_school_window(now=None):
    """按用户真实作息判「不该打扰」。写死也行，但读配置更好改。"""
    now = now or time.localtime()
    hm = now.tm_hour * 60 + now.tm_min
    spans = cfg_windows()
    return any(a <= hm < b for a, b in spans)


def cfg_windows():
    raw = ""
    try:
        raw = chatcfg.get("open.school_hours", "")
    except Exception:
        try:
            raw = chatcfg.value("open.school_hours", "")
        except Exception:
            raw = ""
    out = []
    if isinstance(raw, str):
        for part in raw.replace("，", ",").split(","):
            part = part.strip()
            if "-" not in part:
                continue
            a, b = part.split("-", 1)
            try:
                ah, am = [int(x) for x in a.strip().split(":")]
                bh, bm = [int(x) for x in b.strip().split(":")]
            except Exception:
                continue
            out.append((ah * 60 + am, bh * 60 + bm))
    return out or [(7 * 60 + 20, 12 * 60 + 25), (14 * 60 + 40, 22 * 60 + 25)]


def pick_line(kind, drives=None):
    """选一句。带一点点「看心情」：依恋高就更直接，好奇高就更像提问。"""
    pool = LINES.get(kind) or LINES["reach_out"]
    d = drives or {}
    att = float(d.get("attachment", 0.5))
    cur = float(d.get("curiosity", 0.5))
    if kind == "reach_out" and cur > att:
        return "在忙吗"
    idx = int((att * 1000)) % len(pool)
    return pool[idx]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="真的发出去（默认只演习）")
    ap.add_argument("--to", default=MAIN_UID, help="发给谁（QQ 号）")
    ap.add_argument("--force", action="store_true", help="无视时间窗（调试用）")
    a = ap.parse_args(argv)

    raw = _post("/motiv/tick", {"quiet": False, "at_school": False})
    tick = raw.get("motiv") or raw        # 接口包了一层 {ok, motiv}
    chosen = tick.get("chosen") or {}
    kind = chosen.get("kind") or ""
    print("drives:", json.dumps(tick.get("drives", {}), ensure_ascii=False))
    print("chosen:", kind or "(没有想做的事)")

    if kind not in ("reach_out", "care_check"):
        print("skip: 现在想做的不是说话（%s）" % (kind or "空"))
        return 0
    if not a.force and in_school_window():
        print("skip: 在他上课/上班的时间窗里")
        return 0

    line = pick_line(kind, tick.get("drives"))
    print("line:", line)
    if not a.send:
        print("dry-run：没发。要真发加 --send")
        return 0
    if not a.to:
        print("skip: 没有收件人（--to 或 WHALE_MAIN_UID）")
        return 0

    import qq_say
    segs = qq_say.build(text=line)
    ok = bool(qq_say.call("send_private_msg", user_id=int(a.to),
                         message=segs))
    print("sent:", ok)
    _post("/motiv/done", {"kind": kind, "spoke": True})
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
