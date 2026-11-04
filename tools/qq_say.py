# -*- coding: utf-8 -*-
"""在群里说一句：能引用、能带图、能@。

以前只会发纯文字，别人问"你在回哪句"就得自己猜。OneBot 的
`reply` 段就是干这个的 —— 把它放在消息数组最前面，客户端会显示成引用。

用法：
  python tools/qq_say.py <群号> "文字"
  python tools/qq_say.py <群号> "文字" --quote 1234567890
  python tools/qq_say.py <群号> --image qq_state/memes/zai.png
  python tools/qq_say.py <群号> "看这个" --image <path> --quote <mid>
  python tools/qq_say.py <群号> "文字" --at 100000000

规矩（跟 qq_guard 一致）：
  · 发送前按 human_gap() 等一个随机时长
  · 发完写回卡（POST /remember，source=qq-group）
  · 不刷屏：一轮最多一两句
"""
import argparse
import json
import os
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:6700"
TOKEN = "qqai-dsh-http-2026"
CARD_API = "http://127.0.0.1:8848/remember"

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
import qq_guard as G  # noqa: E402


def call(action, **params):
    req = urllib.request.Request(
        BASE + "/" + action,
        data=json.dumps(params).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + TOKEN})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def build(text="", quote=None, image=None, at=None):
    """拼 OneBot 消息数组。顺序有讲究：reply 必须在最前面，
    不然客户端不会渲染成引用（实测放后面就只是一段文字）。"""
    segs = []
    if quote:
        segs.append({"type": "reply", "data": {"id": str(quote)}})
    if at:
        segs.append({"type": "at", "data": {"qq": str(at)}})
        segs.append({"type": "text", "data": {"text": " "}})
    if image:
        # NapCat 认本地绝对路径；用 file:// 有时会被当 URL 去下载
        segs.append({"type": "image", "data": {"file": os.path.abspath(image)}})
    if text:
        segs.append({"type": "text", "data": {"text": text}})
    return segs


def remember(text, kind="text"):
    try:
        req = urllib.request.Request(
            CARD_API,
            data=json.dumps({"text": "在群里说：「%s」" % text,
                             "source": "qq-group", "speaker": "self",
                             "region": "episode"}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8")).get("mem_id")
    except Exception as e:
        print("  ⚠ 写回失败（不阻断发送）：%s" % e)
        return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("gid", type=int)
    ap.add_argument("text", nargs="?", default="")
    ap.add_argument("--quote", default="", help="要引用的 message_id")
    ap.add_argument("--image", default="", help="本地图片路径")
    ap.add_argument("--at", default="", help="@ 谁（QQ 号）")
    ap.add_argument("--no-wait", action="store_true", help="不等随机间隔（测试用）")
    a = ap.parse_args(argv)

    if not a.text and not a.image:
        print("要么给文字，要么给 --image")
        return 2

    segs = build(a.text, a.quote, a.image, a.at)
    print("消息段：%s" % json.dumps(segs, ensure_ascii=False))

    gap = 0 if a.no_wait else G.human_gap(a.text or "图片")
    if gap:
        print("等 %.1f 秒…" % gap)
        time.sleep(gap)

    r = call("send_group_msg", group_id=a.gid, message=segs)
    mid = (r.get("data") or {}).get("message_id")
    print("发出 message_id =", mid)

    if a.text:
        print("写回 mem_id =", remember(a.text))
    else:
        print("写回 mem_id =", remember("[图片] %s" % os.path.basename(a.image)))

    # 自己发的也算已回，别让它挂在未回里
    if mid:
        try:
            G.mark_many([mid], G.ST_REPLIED, by="self")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
