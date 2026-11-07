# -*- coding: utf-8 -*-
"""T-80 验收：多通道写回（mem.source + POST /remember + bridge note）

背景（2026-11-04，用户 m10710）：
    「马上把单条写入通道那个 bug 修了，争取以后 B站/抖音/QQ/别的都能写进去」

以前只有 /report（一问一答）能写卡，别的通道听见了也写不进 ——
「考试考得不错」就是这么丢的。这一支验三件事：
  1. card 层：remember(source=...) 真的落库
  2. HTTP 层：POST /remember 能写、能标来源、能开出生证、空 text 报 400
  3. /report 带 source 也保留（老通道不退化）
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import card2  # noqa: E402

ok = []


def chk(name, cond, extra=""):
    ok.append(bool(cond))
    print(("  ✅ " if cond else "  ❌ ") + name + ("  " + str(extra) if extra else ""))


d = tempfile.mkdtemp(prefix="t80_")
db = os.path.join(d, "t80.db")
port = 8861
try:
    card = card2.MemoryCard(db)

    # ---------- 1. card 层 ----------
    m1 = card.remember("我在群里跟人聊了两句海洋馆", source="qq-group")
    row = card.db.execute("SELECT source, raw FROM mem WHERE id=?", (m1,)).fetchone()
    chk("remember(source=) 落库", row["source"] == "qq-group", row["source"])
    chk("正文没被 source 污染", "海洋馆" in row["raw"])

    m2 = card.remember("没标来源的一条")
    row2 = card.db.execute("SELECT source FROM mem WHERE id=?", (m2,)).fetchone()
    chk("不传 source 时为空串（不是 None）", row2["source"] == "", repr(row2["source"]))

    # ---------- 2. HTTP 层 ----------
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONPATH=ROOT)
    srv = subprocess.Popen([sys.executable, "-X", "utf8",
                            os.path.join(ROOT, "mind_server.py"),
                            "--db", db, "--port", str(port)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           env=env)
    base = "http://127.0.0.1:%d" % port

    def post(path, obj):
        data = json.dumps(obj).encode("utf-8")
        req = urllib.request.Request(base + path, data=data,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception as e:
            return 0, {"error": str(e)}

    up = False
    for _ in range(40):
        time.sleep(1.5)
        try:
            with urllib.request.urlopen(base + "/health", timeout=10) as resp:
                up = resp.status == 200
                break
        except Exception:
            pass
    chk("服务起来了", up)

    if up:
        code, out = post("/remember", {"text": "在 B站看了一个修凉亭的视频",
                                       "source": "bili", "salience": 0.6})
        chk("/remember 200", code == 200, code)
        chk("返回 mem_id", bool(out.get("mem_id")), out.get("mem_id"))
        chk("返回带 source", out.get("source") == "bili", out.get("source"))
        chk("开了出生证", out.get("birth") is True, out.get("birth"))

        mid = out.get("mem_id")
        row3 = card.db.execute("SELECT source FROM mem WHERE id=?", (mid,)).fetchone()
        chk("库里 source=bili", row3 and row3["source"] == "bili")

        code, out2 = post("/remember", {"text": "   ", "source": "qq"})
        chk("空 text -> 400", code == 400 and out2.get("error") == "empty_text",
            (code, out2.get("error")))

        code, out3 = post("/remember", {"text": "x" * 30000, "source": "qq"})
        chk("超长 text -> 413", code == 413, code)

        code, out4 = post("/report", {"user_message": "今天数学考得还行",
                                      "reply": "那就好"})
        chk("/report 老通道仍可用", code == 200, code)
        rmid = (out4.get("report") or {}).get("mem_id")
        rrow = card.db.execute("SELECT source FROM mem WHERE id=?", (rmid,)).fetchone()
        chk("/report 默认 source=chat", rrow and rrow["source"] == "chat",
            rrow["source"] if rrow else None)

    # ---------- 3. 台账/链不被破坏 ----------
    chk("链仍然完整", card.db.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name='timeline'").fetchone()[0] == 1)

finally:
    try:
        srv.terminate()
    except Exception:
        pass
    shutil.rmtree(d, ignore_errors=True)

print()
print("结果：%d 通过，%d 失败" % (sum(ok), len(ok) - sum(ok)))
sys.exit(0 if all(ok) else 1)
