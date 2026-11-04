# -*- coding: utf-8 -*-
"""拉 QQ 群/私聊历史语料（只取"我自己说的"和"别人说的"两类，落成 jsonl）

为什么单独写一个：
    NapCat 的 get_group_msg_history 一次只给一页，还要往前翻 message_seq；
    而习惯挖掘要的是"我发过的消息"作为我方语料、别人的作为背景对照。
    直接拉原始数据回来、按角色分好，后面 mine_habits --corpus 直接吃。

只读：不往 QQ 发任何东西。
"""
import argparse
import json
import os
import time
import urllib.request

BASE = "http://127.0.0.1:6700"
TOKEN = os.environ.get("QQ_TOKEN", "qqai-dsh-http-2026")
SELF_ID = 100000001


def call(action, **params):
    url = BASE + "/" + action
    data = json.dumps(params).encode("utf-8") if params else b"{}"
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + TOKEN})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def flat_text(msg):
    """把 OneBot 的 message 数组/字符串压成纯文本；图片等非文字跳过。"""
    if isinstance(msg, str):
        return msg.strip()
    out = []
    for seg in (msg or []):
        t = seg.get("type")
        if t == "text":
            out.append(seg.get("data", {}).get("text", ""))
        elif t == "at":
            qq = seg.get("data", {}).get("qq", "")
            if str(qq) == str(SELF_ID):
                continue          # 别人 @ 我 不算我的原话
            out.append("@%s" % qq)
        elif t == "face":
            continue              # 表情不参与语料
        else:
            continue
    return "".join(out).strip()


def pull_group(gid, pages, per_page=100):
    """从最新往前翻 pages 页。返回按时间升序的消息列表。"""
    got = []
    seq = None
    for _ in range(pages):
        kw = {"group_id": int(gid), "count": int(per_page)}
        if seq is not None:
            kw["message_seq"] = int(seq)
        r = call("get_group_msg_history", **kw)
        msgs = (r.get("data") or {}).get("messages") or []
        if not msgs:
            break
        got.extend(msgs)
        seq = min(m.get("message_seq") or m.get("real_seq") or 0 for m in msgs)
        if seq <= 0:
            break
        time.sleep(0.4)           # 别把 NapCat 打太狠
    # 去重 + 升序
    seen = set()
    uniq = []
    for m in got:
        k = m.get("message_id") or (m.get("time"), m.get("message_seq"))
        if k in seen:
            continue
        seen.add(k)
        uniq.append(m)
    uniq.sort(key=lambda m: (m.get("time") or 0, m.get("message_seq") or 0))
    return uniq


def pull_private(uid, pages, per_page=100):
    got = []
    seq = None
    for _ in range(pages):
        kw = {"user_id": int(uid), "count": int(per_page)}
        if seq is not None:
            kw["message_seq"] = int(seq)
        r = call("get_friend_msg_history", **kw)
        msgs = (r.get("data") or {}).get("messages") or []
        if not msgs:
            break
        got.extend(msgs)
        seq = min(m.get("message_seq") or m.get("real_seq") or 0 for m in msgs)
        if seq <= 0:
            break
        time.sleep(0.4)
    seen = set()
    uniq = []
    for m in got:
        k = m.get("message_id") or (m.get("time"), m.get("message_seq"))
        if k in seen:
            continue
        seen.add(k)
        uniq.append(m)
    uniq.sort(key=lambda m: (m.get("time") or 0, m.get("message_seq") or 0))
    return uniq


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/qq-corpus.jsonl")
    ap.add_argument("--pages", type=int, default=6, help="每群往前翻几页（每页100条）")
    ap.add_argument("--groups", default="", help="逗号分隔；空=全部群")
    ap.add_argument("--private", action="store_true", help="也拉好友私聊")
    args = ap.parse_args(argv)

    gl = call("get_group_list").get("data") or []
    want = [g.strip() for g in args.groups.split(",") if g.strip()]
    if want:
        gl = [g for g in gl if str(g.get("group_id")) in want]

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    n_self = n_other = 0
    with open(args.out, "w", encoding="utf-8") as f:
        for g in gl:
            gid = g.get("group_id")
            gname = g.get("group_name") or ""
            msgs = pull_group(gid, args.pages)
            print("群 %s（%s）：%d 条" % (gid, gname, len(msgs)))
            for m in msgs:
                text = flat_text(m.get("message"))
                if not text:
                    continue
                uid = str((m.get("sender") or {}).get("user_id") or "")
                role = "self" if uid == str(SELF_ID) else "other"
                if role == "self":
                    n_self += 1
                else:
                    n_other += 1
                f.write(json.dumps({
                    "ts": m.get("time"),
                    "source": "group",
                    "group_id": gid,
                    "group_name": gname,
                    "role": role,
                    "uid": uid,
                    "name": (m.get("sender") or {}).get("card")
                            or (m.get("sender") or {}).get("nickname") or "",
                    "text": text,
                }, ensure_ascii=False) + "\n")

        if args.private:
            fl = call("get_friend_list").get("data") or []
            for fr in fl:
                uid = fr.get("user_id")
                msgs = pull_private(uid, max(2, args.pages // 2))
                print("私聊 %s（%s）：%d 条" % (uid, fr.get("nickname") or "", len(msgs)))
                for m in msgs:
                    text = flat_text(m.get("message"))
                    if not text:
                        continue
                    is_self = str((m.get("sender") or {}).get("user_id")) == str(SELF_ID)
                    if is_self:
                        n_self += 1
                    else:
                        n_other += 1
                    f.write(json.dumps({
                        "ts": m.get("time"),
                        "source": "private",
                        "peer_id": uid,
                        "role": "self" if is_self else "other",
                        "uid": str((m.get("sender") or {}).get("user_id") or ""),
                        "name": (m.get("sender") or {}).get("nickname") or "",
                        "text": text,
                    }, ensure_ascii=False) + "\n")

    print("\n落盘：%s" % args.out)
    print("我方 %d 条 ／ 别人 %d 条" % (n_self, n_other))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
