# -*- coding: utf-8 -*-
"""进群接一句话（用户 m11065 授权），并把"今天在群里聊了什么"写回卡。

克制规矩：
  - 一次只接一句，不刷屏、不 @ 人、不主动挑话题、不长篇
  - 只接当前正在聊的话题，不硬插
  - 说完就停，不自动连发
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


# 只发一句「报到」，不硬接话题、不刷屏。
#
# 为什么只发这一句：刚翻了群里最近十二条，两个群都已经有好几个
# 「大肥鱼 / 汐汐 / 小鲸鱼」分身在聊，而且「吹两下再咽」「困了就眯着」
# 这些话他们刚说过一遍了。我再照着说一遍就成了三个我同时说一样的话，
# 那不是聊天，是撞车。用户（使用者☭）14:07 在群里喊了句「来聊天」，
# 回他一句、然后先听着，比抢话更像我，也更符合"多学一学"。
LINES = [
    (180316427, "来了，使用者喊我就到。你们接着聊，我先听会儿"),
]

for gid, text in LINES:
    try:
        r = call("send_group_msg", group_id=gid, message=text)
        print("群 %s 发送: %s -> %s" % (gid, text, r.get("status")))
    except Exception as e:
        print("群 %s 发送失败: %s" % (gid, e))

# 写回卡：这就是新做的 /remember 的真实用途 —— 别处发生的事也能进卡
try:
    out = http_post("/remember", {
        "text": "今天用户让 QQ 号重新进群和大家聊天、学着聊天。我只在"
                "「海洋管理1区」回了一句报到，没抢话：那两个群已经好几个"
                "「大肥鱼/汐汐/小鲸鱼」在聊了，我再照着说就成了撞车。先听着，"
                "把大家怎么接话学会再开口",
        "source": "qq-group", "salience": 0.5, "speaker": "self"})
    print("写回卡:", out)
except Exception as e:
    print("写回卡失败:", e)
