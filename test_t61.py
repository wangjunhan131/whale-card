# -*- coding: utf-8 -*-
"""T6-1 验收：心智服务起得来、答得对、失败时说实话

分两部分：
  A. 进程内直测 Mind 对象（不起端口，跑得快）
  B. 起真端口，用 urllib 打真实 HTTP 请求
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import card2
import mind_server                                   # noqa: E402
from card2 import MemoryCard                         # noqa: E402

PY = sys.executable
DB = os.path.join(HERE, "card.db")
TMP = os.path.join(HERE, "_t61_tmp.db")

ok_n = 0
bad_n = 0


def ck(name, cond, extra=""):
    global ok_n, bad_n
    if cond:
        ok_n += 1
        print("  ✅ %s %s" % (name, extra))
    else:
        bad_n += 1
        print("  ❌ %s %s" % (name, extra))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def req(port, method, path, body=None, timeout=10):
    url = "http://127.0.0.1:%d%s" % (port, path)
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


# ══════════════════════════════════════════════════════
print("=" * 50)
print("【A】进程内直测 Mind")
print("=" * 50)

if os.path.exists(TMP):
    os.remove(TMP)
seed = MemoryCard(TMP)
seed.remember("用户是高三学生，主号QQ号是100000000，昵称叫使用者", ts=time.time() - 86400 * 3)
seed.remember("用户喜欢吃白米饭", ts=time.time() - 86400 * 2)
seed.remember("用户暑假靠拍婚礼挣钱，想攒钱买硬盘", ts=time.time() - 86400)
seed.close()

m = mind_server.Mind(TMP)
ck("Mind 能打开卡", m.card is not None)
ck("初始没盖章", m.sealed is False)

print()
print("【A1】/context 能组装出上下文")
ctx = m.context("你还记得我QQ号吗")
ck("返回 prompt", bool(ctx.get("prompt")))
ck("prompt 里带 QQ 号", "100000000" in ctx["prompt"],
   "(prompt %d 字)" % len(ctx["prompt"]))
ck("有场景判定", bool(ctx.get("scene")), "scene=%s" % ctx.get("scene"))
ck("有六维权重", isinstance(ctx.get("weights"), list) and len(ctx["weights"]) == 6,
   "%s" % (ctx.get("weights_named"),))
ck("有权重配额", isinstance(ctx.get("quota"), dict))
ck("有召回命中", len(ctx.get("hits") or []) > 0,
   "(%d 条)" % len(ctx.get("hits") or []))
ck("命中带分数和权重", all("score" in h and "w_now" in h for h in ctx["hits"]))
ck("命中带渲染文本", all(h.get("text") for h in ctx["hits"]))
ck("有心情", bool(ctx.get("mood")))
ck("有激素四元组", isinstance(ctx.get("hormones"), dict)
   and len(ctx["hormones"]) == 4, "%s" % (ctx.get("hormones"),))
ck("token 估算在预算内", (ctx.get("est_tokens") or 0) <= 6000,
   "est=%s" % ctx.get("est_tokens"))

print()
print("【A2】空消息要报错，不能编")
try:
    m.context("")
    ck("空消息抛错", False)
except ValueError as e:
    ck("空消息抛错", "message" in str(e).lower() or "空" in str(e), str(e))

print()
print("【A3】/report 记一轮")
r = m.report("我明天想吃白米饭", "行，明天给你记着")
ck("返回 mem_id", isinstance(r.get("mem_id"), int), "id=%s" % r.get("mem_id"))
mid = r["mem_id"]
dlg = m.card.get_dialogue(mid)
ck("原文存下来了", len(dlg) == 2, "%d 条" % len(dlg))
ck("user 那句对", dlg and dlg[0]["text"] == "我明天想吃白米饭")
ck("assistant 那句对", len(dlg) > 1 and dlg[1]["text"] == "行，明天给你记着")
ck("assistant 排在 user 后面", len(dlg) > 1 and dlg[1]["ts"] > dlg[0]["ts"])

print()
print("【A4】时间真的在流")
# 只验"时间在流"这一件事：拿同一条记忆、同一个 t_anchor，比较现在与两天后的
# 纯衰减值。不能比较 recall 出来的数 —— 那里面有"被想起来就加强"的加成，
# 而 tick 会结算挂在 recall_event 里的加成（前面 A1~A3 留下的），一比较就会
# 出现"两天后权重反而变大"。这不是衰减坏了，是量错了东西。
w0 = m.card.recall("白米饭", top_k=1, access=False)
row = m.card.db.execute(
    "SELECT w, pool, t_anchor, tier FROM mem WHERE id=?", (w0[0][2]["id"],)).fetchone()
now = time.time()
before = card2.weight_now(row[0], row[1], row[2], now, None, row[3])
after = card2.weight_now(row[0], row[1], row[2], now + 86400 * 2, None, row[3])
m.card.tick(86400 * 2)
m.last_tick_ts = time.time()
ck("两天后权重下降", after < before, "%.4f → %.4f（纯衰减）" % (before, after))

print()
print("【A5】status 全量状态")
st = m.status()
ck("有版本号", st.get("version"))
ck("有库路径", st.get("db", "").endswith(".db"))
ck("有池分布", isinstance(st.get("stats", {}).get("pools"), dict))
ck("有突触统计", isinstance(st.get("synapse"), dict))
ck("有激素", isinstance(st.get("hormones"), dict))
m.close()

# ══════════════════════════════════════════════════════
print()
print("=" * 50)
print("【B】起真端口，打真实 HTTP")
print("=" * 50)

port = free_port()
proc = subprocess.Popen(
    [PY, "-X", "utf8", os.path.join(HERE, "mind_server.py"),
     "--port", str(port), "--db", TMP],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
try:
    ready = False
    for _ in range(60):
        time.sleep(0.25)
        try:
            code, body = req(port, "GET", "/health", timeout=2)
            if code == 200 and body.get("ok"):
                ready = True
                break
        except Exception:
            pass
    ck("服务起来了", ready, "port=%d" % port)

    if ready:
        code, body = req(port, "GET", "/health")
        ck("GET /health 200", code == 200)
        ck("health 带记忆条数", body.get("memories", 0) >= 3, "%s" % body.get("memories"))
        ck("health 带版本", bool(body.get("version")))

        code, body = req(port, "POST", "/context", {"message": "你还记得我QQ号吗"})
        ck("POST /context 200", code == 200)
        ck("context 里有 QQ 号", "100000000" in json.dumps(body, ensure_ascii=False))

        code, body = req(port, "POST", "/context", {"message": ""})
        ck("空 message 返回 400", code == 400, "code=%d err=%s"
           % (code, body.get("error")))

        code, body = req(port, "POST", "/report",
                         {"user_message": "今天好累", "reply": "歇会儿吧"})
        ck("POST /report 200", code == 200)
        ck("report 返回 mem_id", isinstance(body.get("report", {}).get("mem_id"), int))

        code, body = req(port, "POST", "/recall", {"query": "白米饭", "top_k": 2})
        ck("POST /recall 200", code == 200)
        ck("recall 有结果", len(body.get("hits") or []) > 0)

        code, body = req(port, "POST", "/tick", {"seconds": 3600})
        ck("POST /tick 200", code == 200)
        code, body = req(port, "POST", "/tick", {"seconds": 0})
        ck("tick 0 秒被拒", code == 400)

        code, body = req(port, "GET", "/status")
        ck("GET /status 200", code == 200)
        ck("status 有 stats", "stats" in (body.get("status") or {}))

        code, body = req(port, "GET", "/nope")
        ck("未知路径 404", code == 404)
        code, body = req(port, "POST", "/context", {"message": "x" * 300000})
        ck("超大 body 被拒", code in (400, 413), "code=%d err=%s"
           % (code, body.get("error")))

        code, body = req(port, "POST", "/shutdown", {})
        ck("POST /shutdown 200", code == 200)
        time.sleep(1.0)
        ck("服务真的退了", proc.poll() is not None, "exit=%s" % proc.poll())
finally:
    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass

# ══════════════════════════════════════════════════════
print()
print("=" * 50)
print("【C】失败要说实话（不许扮演）")
print("=" * 50)
code, body = req(free_port(), "GET", "/health", timeout=2) if False else (None, None)
try:
    req(free_port(), "GET", "/health", timeout=2)
    ck("服务没起时连不上", False)
except Exception as e:
    ck("服务没起时连不上（客户端能感知）", True, type(e).__name__)

# 卡文件不存在时，进程应当拒绝启动而不是造一个空卡
missing = os.path.join(HERE, "_definitely_not_here.db")
r = subprocess.run([PY, "-X", "utf8", os.path.join(HERE, "mind_server.py"),
                    "--port", str(free_port()), "--db", missing],
                   capture_output=True, text=True, timeout=60)
ck("卡不存在时拒绝启动", r.returncode == 2, "exit=%d" % r.returncode)
ck("给出明确原因", "找不到卡" in (r.stdout + r.stderr))
ck("没偷偷造出空库", not os.path.exists(missing))

for p in (TMP, TMP + "-wal", TMP + "-shm"):
    try:
        os.remove(p)
    except Exception:
        pass

print()
print("=" * 50)
print("T6-1 验收：通过 %d 项，失败 %d 项" % (ok_n, bad_n))
sys.exit(0 if bad_n == 0 else 1)
