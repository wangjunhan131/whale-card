# -*- coding: utf-8 -*-
"""听筒（2026-11-06 重做）：盯着用户的私聊，有新消息就打印出来并 exit 0，
把 DSH 的后台任务「结束」掉，从而叫醒会话。不写卡、不发消息、不往输入框弹字。

用法：python -X utf8 tools/watch_wake.py [--max-hours 6] [--interval 8]
退出码：0 = 有新消息（原因已打印）；2 = 到时收工；3 = NapCat 连不上太久
"""
import argparse, json, os, sys, time
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(BASE, "qq_state")
WM = os.path.join(STATE, "wake_watermark.txt")
UID = 100000000
URL = "http://127.0.0.1:6700/"
TOKEN = "qqai-dsh-http-2026"


def post(ep, body):
    r = urllib.request.Request(URL + ep, data=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json",
                                        "Authorization": "Bearer " + TOKEN})
    with urllib.request.urlopen(r, timeout=15) as f:
        return json.loads(f.read().decode("utf-8"))


def read_wm():
    try:
        with open(WM, "r", encoding="utf-8") as f:
            return float(f.read().strip() or 0)
    except Exception:
        return 0.0


def write_wm(ts):
    os.makedirs(STATE, exist_ok=True)
    tmp = WM + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(str(int(ts)))
    os.replace(tmp, WM)


def segs_text(m):
    out = []
    for x in (m.get("message") or []):
        t = x.get("type")
        d = x.get("data") or {}
        if t == "text":
            out.append(d.get("text", ""))
        elif t == "image":
            out.append("[图]")
        elif t == "record":
            out.append("[语音]")
        else:
            out.append("[%s]" % t)
    return "".join(out).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-hours", type=float, default=6.0)
    ap.add_argument("--interval", type=float, default=8.0)
    a = ap.parse_args()

    wm = read_wm()
    if not wm:
        write_wm(time.time())          # 首次启动：以现在为界，别把旧消息当新消息
        print("听筒上线，水位线 = 现在")
        return_code = 2
    else:
        print("听筒上线，水位线 = %s" % time.strftime("%m-%d %H:%M", time.localtime(wm)))

    deadline = time.time() + a.max_hours * 3600
    fails = 0
    while time.time() < deadline:
        try:
            d = post("get_friend_msg_history", {"user_id": UID, "count": 5})
            fails = 0
        except Exception as e:
            fails += 1
            if fails >= 30:
                print("NapCat 连续连不上：%s" % e)
                return 3
            time.sleep(a.interval)
            continue
        ms = (d.get("data") or {}).get("messages") or []
        fresh = [m for m in ms
                 if int((m.get("sender") or {}).get("user_id") or 0) == UID
                 and float(m.get("time") or 0) > wm]
        if fresh:
            fresh.sort(key=lambda m: float(m.get("time") or 0))
            _now = time.time()
            print("—— 他来了 %d 条（现在 %s）——" % (len(fresh), time.strftime("%H:%M:%S")))
            for m in fresh:
                _t = float(m["time"])
                _lag = max(0, int(_now - _t))
                print(time.strftime("[%H:%M:%S]", time.localtime(_t))
                      + "（%d 秒前发的）" % _lag
                      + " " + segs_text(m))
            print("→ 先把他的消息回了，再干别的。")
            write_wm(float(fresh[-1]["time"]))
            return 0
        time.sleep(a.interval)
    print("听筒到点收工（没等到新消息）")
    return 2


if __name__ == "__main__":
    sys.exit(main())
