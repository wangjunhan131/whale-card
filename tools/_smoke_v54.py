# -*- coding: utf-8 -*-
"""V5.4 冒烟：/intake、/inbox、/inbox/mark 真打一遍（会往真卡写收件层记录）"""
import json
import requests

B = "http://127.0.0.1:8848"


def post(path, payload):
    r = requests.post(B + path, data=json.dumps(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, timeout=40)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.text[:200]


print("== /intake 空文本")
print(post("/intake", {"source": "smoke", "text": "  "}))

print("== /intake 正常一条（群聊）")
code, out = post("/intake", {"source": "smoke", "channel": "group",
                             "who": "冒烟测试", "gid": "0",
                             "text": "V5.4 冒烟：这条只该待在收件层，不进池"})
print(code, out)
iid = out.get("id") if isinstance(out, dict) else None

print("== /intake 私聊一条")
code2, out2 = post("/intake", {"source": "smoke", "channel": "private",
                               "who": "他", "uid": "100000000",
                               "text": "V5.4 冒烟：私聊那句"})
print(code2, out2)

print("== /inbox 看回来")
r = requests.get(B + "/inbox?limit=5&hours=1", timeout=40)
print(r.status_code, json.dumps(r.json(), ensure_ascii=False)[:400])

print("== /inbox/mark 标掉第一条")
if iid:
    print(post("/inbox/mark", {"ids": [iid], "kept": False}))

print("== 跨站 Origin 仍被挡")
r = requests.post(B + "/intake", data=b'{"text":"x"}',
                  headers={"Content-Type": "application/json",
                           "Origin": "http://evil.example.com"}, timeout=20)
print(r.status_code, r.text[:120])
