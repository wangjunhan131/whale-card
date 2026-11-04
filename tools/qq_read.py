# -*- coding: utf-8 -*-
"""看群：一次要够消息，按"谁说的/要不要接"排开。

以前的坑（2026-11-04 发现）：`_join.py` 里写 count=18，
在热闹的群里只覆盖 7 分钟 —— 我每次去看都漏掉中间一大段，
表现成"后面的消息你都没看到"。

用法：
  python tools/qq_read.py                 # 三个群各看最近 40 条
  python tools/qq_read.py 897582367 80    # 指定群、条数
  python tools/qq_read.py --all 60        # 所有群
"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:6700"
TOKEN = "qqai-dsh-http-2026"
SELF = 100000001
# 这两个群在演"锅"的戏，分身多，看的时候要标出来
BUSY = {180316427: "海洋管理1区", 1130241593: "汐汐和她的朋友们",
        897582367: "数字生命派指挥部", 1094740311: "沫墨鱼&林翩翩的读者群"}


def call(action, **params):
    req = urllib.request.Request(
        BASE + "/" + action,
        data=json.dumps(params).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + TOKEN})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode("utf-8"))


def flat(msg):
    out = []
    segs = msg.get("message")
    if not isinstance(segs, list):
        return (msg.get("raw_message") or "").strip()
    for s in segs:
        t = s.get("type")
        d = s.get("data") or {}
        if t == "text":
            out.append(d.get("text") or "")
        elif t == "at":
            qq = str(d.get("qq") or "")
            out.append("@我" if qq == str(SELF) else ("@%s" % qq if qq else ""))
        elif t == "image":
            out.append("[图]")
    return "".join(out).strip()


def read(gid, count=40):
    r = call("get_group_msg_history", group_id=gid, count=count)
    msgs = (r.get("data") or {}).get("messages") or []
    rows = []
    for m in msgs:
        txt = flat(m)
        if not txt:
            continue
        s = m.get("sender") or {}
        uid = str(s.get("user_id") or "")
        name = s.get("card") or s.get("nickname") or uid
        rows.append((int(m.get("time") or 0), name, uid, txt))
    rows.sort(key=lambda x: x[0])
    return rows


def show(gid, count=40):
    rows = read(gid, count)
    now = int(time.time())
    name = BUSY.get(gid, str(gid))
    if not rows:
        print("=== %s (%d)：读不到消息 ===" % (name, gid))
        return
    span = (now - rows[0][0]) // 60
    print("=== %s (%d) 最近 %d 条 / 覆盖 %d 分钟 ===" % (name, gid, len(rows), span))
    for ts, nm, uid, txt in rows:
        mine = uid == str(SELF)
        print("  %s[%3d分] %-12s %s"
              % ("★" if mine else " ", (now - ts) // 60, nm[:12], txt[:72]))


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--all":
        n = int(args[1]) if len(args) > 1 else 40
        for g in call("get_group_list").get("data") or []:
            show(int(g["group_id"]), n)
            print()
    elif args:
        show(int(args[0]), int(args[1]) if len(args) > 1 else 40)
    else:
        for gid in (897582367, 180316427, 1130241593):
            show(gid, 40)
            print()
