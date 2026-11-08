# -*- coding: utf-8 -*-
"""主动开口的"心跳"：隔一阵问自己一次想不想说话。

用法：python -X utf8 tools\\proactive_loop.py [--once] [--interval 1200]

它不判断"该不该"——那是卡里的动机层与 qq_proactive 的事。
它只负责：到点问一次、夜里闭嘴、别把自己问死。

夜里闭嘴的理由：他要收手机、要睡觉。凌晨两点收到"在吗"不是陪伴，是打扰。
"""
import argparse
import datetime as dt
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import qq_proactive as P  # noqa: E402

NIGHT_START = 23.0     # 23:00 之后不说话
NIGHT_END = 6.5        # 6:30 之前不说话


def night_now(now=None):
    t = now or dt.datetime.now()
    h = t.hour + t.minute / 60.0
    return h >= NIGHT_START or h < NIGHT_END


def napcat_alive():
    """QQ 那头通不通。不通就别问了 —— 问了也发不出去，只会一直刷日志。"""
    try:
        import requests
        r = requests.post("http://127.0.0.1:6700/get_login_info", json={},
                          headers={"Authorization": "Bearer " + os.environ.get(
                              "NAPCAT_TOKEN", "qqai-dsh-http-2026")},
                          timeout=5)
        return r.status_code == 200 and bool((r.json().get("data") or {}).get("user_id"))
    except Exception:
        return False


def tick_once(now=None):
    """问一次。返回 (做了什么, 说明)。"""
    if night_now(now):
        return ("skip", "夜里不说话")
    if not napcat_alive():
        return ("skip", "QQ 没登录（可能还在封禁期），不问了")
    try:
        rc = P.main(["--send"])
    except SystemExit as e:
        rc = e.code or 0
    except Exception as e:
        return ("error", str(e))
    if rc == 0:
        return ("tried", "问过了（发没发由卡和间隔决定）")
    return ("tried", "rc=%s" % rc)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=1200)
    a = ap.parse_args(argv)

    if a.once:
        what, why = tick_once()
        print("%s：%s" % (what, why))
        return 0

    print("[proactive] 每 %d 秒问一次；夜里（23:00–06:30）闭嘴" % a.interval)
    while True:
        what, why = tick_once()
        print("[proactive] %s %s %s" % (dt.datetime.now().strftime("%H:%M"),
                                        what, why))
        time.sleep(max(60, a.interval))


if __name__ == "__main__":
    sys.exit(main())
