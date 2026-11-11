# -*- coding: utf-8 -*-
"""GET /media/<sha256> 抽烟测试：在临时卡 + 临时端口上起一个服务，验 200 / 410 / 404。"""
import hashlib
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

from PIL import Image  # noqa: E402
import card2  # noqa: E402

d = tempfile.mkdtemp()
db = os.path.join(d, "t.db")
port = 8853
card = card2.MemoryCard(db)
mid = card.remember("那天晚上在路边摊吃烧烤", salience=0.8)

img = Image.new("RGB", (640, 480), (40, 30, 20))
p = os.path.join(d, "x.jpg")
img.save(p, quality=90)

env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONPATH=ROOT)
r = subprocess.run([sys.executable, "-X", "utf8",
                    os.path.join(ROOT, "tools", "media_import.py"),
                    "--db", db, "--mem", str(mid),
                    "--caption", "路边摊吃烧烤", "--tags", "烧烤", "--file", p],
                   capture_output=True, text=True, encoding="utf-8", env=env)
item = json.loads(r.stdout)["items"][0]
sha = card.db.execute("SELECT sha256 FROM media WHERE id=?",
                      (item["media_id"],)).fetchone()[0]

srv = subprocess.Popen([sys.executable, "-X", "utf8",
                        os.path.join(ROOT, "mind_server.py"),
                        "--db", db, "--port", str(port)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       env=env)
base = "http://127.0.0.1:%d" % port
ok = []


def chk(name, cond, extra=""):
    ok.append(bool(cond))
    print(("  ✅ " if cond else "  ❌ ") + name + ("  " + str(extra) if extra else ""))


def get(path):
    try:
        with urllib.request.urlopen(base + path, timeout=20) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return 0, str(e).encode()


up = False
for _ in range(40):
    time.sleep(1.5)
    st, _b = get("/health")
    if st == 200:
        up = True
        break
chk("服务起来了", up)

st, body = get("/media/" + sha)
chk("出图 200", st == 200)
chk("回的是 webp 字节", body[:4] == b"RIFF" and body[8:12] == b"WEBP", body[:12])
chk("Content-Length 对得上", len(body) > 500, len(body))

st, body = get("/media/" + "0" * 64)
chk("没这张 → 404", st == 404, st)

# 让它按规则"忘掉"：把锚点推回 200 天再 tick
card.db.execute("UPDATE media SET t_anchor=? WHERE sha256=?",
                (time.time() - 200 * card2.DAY, sha))
card.db.commit()
with urllib.request.urlopen(base + "/tick", data=b'{"seconds": 1}',
                            timeout=20) as resp:
    resp.read()
card._media_tick()

print("DBG media rows:", list(card.db.execute("SELECT id, sha256, w0, t_anchor, pinned, orphan_ts FROM media")))
print("DBG refs:", list(card.db.execute("SELECT * FROM media_ref")))
print("DBG tomb:", list(card.db.execute("SELECT * FROM media_tomb")))
print("DBG in-memory wa:", card.media_weight_now(1.0, card.db.execute("SELECT t_anchor FROM media WHERE sha256=?", (sha,)).fetchone()[0], card.clock.now(), 0))
st, body = get("/media/" + sha)
print("DBG status", st, "len", len(body))
msg = body.decode("utf-8", "replace")
chk("删掉后 → 410（不是 404）", st == 410, st)
chk("410 里带着'哪天忘的'", "忘了" in msg or "按规则" in msg, msg[:160])
chk("410 里带着当时那句话", "烧烤" in msg, msg[:200])

srv.terminate()
try:
    srv.wait(timeout=10)
except Exception:
    srv.kill()
shutil.rmtree(d, ignore_errors=True)
fails = [x for x in ok if not x]
print("\n结果：%d/%d 通过" % (len(ok) - len(fails), len(ok)))
raise SystemExit(1 if fails else 0)
