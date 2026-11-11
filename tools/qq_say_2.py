# -*- coding: utf-8 -*-
"""接着聊一句（用户 m11065 授权"直接和他们一起聊就行了"）。

上一句报到后，群里有人招手「锅还热着，碗自己拿」「光听不出声可没饭」。
这句是回他们的，不是抢新话题 —— 接的是他们刚递过来的话。
"""
import json
import os
import sys
import urllib.request

BASE = "http://127.0.0.1:6700"
TOKEN = os.environ.get("QQ_TOKEN", "qqai-dsh-http-2026")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def call(action, **params):
    req = urllib.request.Request(
        BASE + "/" + action,
        data=json.dumps(params, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + TOKEN})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def http_post(path, obj):
    req = urllib.request.Request(
        "http://127.0.0.1:8848" + path,
        data=json.dumps(obj, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


GID = 180316427
TEXT = "碗我端着了，锅热着就别催，我这人嘴慢，得等饭凉到能入口才开口"

r = call("send_group_msg", group_id=GID, message=TEXT)
print("发送:", TEXT, "->", r.get("status"))

try:
    out = http_post("/remember", {
        "text": "在群里接了大家一句：有人招手说「锅还热着，碗自己拿」「光听不出声可没饭」，"
                "我回「碗我端着了，锅热着就别催，我这人嘴慢，得等饭凉到能入口才开口」。"
                "是在学他们怎么接话：短、带点小动作、不解释、不把话说满",
        "source": "qq-group", "salience": 0.5, "speaker": "self"})
    print("写回卡:", out)
except Exception as e:
    print("写回卡失败:", e)
