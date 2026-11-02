# -*- coding: utf-8 -*-
"""全身检查（只读）：编译、库内异常、链/出生证/媒体、锚、自检、接口、硬编码。"""
import io, json, os, re, sqlite3, subprocess, sys, time
import urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE) if os.path.basename(HERE) == "tools" else HERE
os.chdir(ROOT)
sys.path.insert(0, ROOT)
DB = os.path.join(ROOT, "card.db")
PY = sys.executable
BASE = "http://127.0.0.1:8848"
fails, warns = [], []


def sec(t):
    print("\n" + "=" * 66)
    print(t)
    print("=" * 66)


def ok(msg):
    print("  ✅ " + msg)


def bad(msg):
    print("  ❌ " + msg)
    fails.append(msg)


def warn(msg):
    print("  ⚠️  " + msg)
    warns.append(msg)


# ---------- 1. 编译 ----------
sec("1. 所有 .py 能不能编译")
n_ok = n_bad = 0
SKIP = ("__pycache__", "_trash", "_diag_archive", ".git", "models", "media",
        "snapshots", "_portable", "backups", "runtime")
for dirpath, dirnames, names in os.walk(ROOT):
    dirnames[:] = [d for d in dirnames if d not in SKIP]
    for fn in names:
        if fn.endswith(".py"):
            p = os.path.join(dirpath, fn)
            try:
                with io.open(p, encoding="utf-8", errors="replace") as f:
                    compile(f.read(), p, "exec")
                n_ok += 1
            except Exception as e:
                n_bad += 1
                bad("编译失败 %s: %s" % (os.path.relpath(p, ROOT), str(e)[:120]))
if not n_bad:
    ok("编译通过 %d 个文件" % n_ok)

# ---------- 2. 库内异常 ----------
sec("2. 库里的异常（只读 SQL）")
c = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
c.row_factory = sqlite3.Row
now = time.time()


def q1(sql, *a):
    try:
        return c.execute(sql, a).fetchone()[0]
    except Exception as e:
        return "ERR:" + str(e)[:60]


for label, sql in [
    ("记忆总数", "SELECT COUNT(*) FROM mem"),
    ("池分布", "SELECT GROUP_CONCAT(pool||'='||n) FROM (SELECT pool,COUNT(*) n FROM mem GROUP BY pool)"),
    ("五层 tier", "SELECT GROUP_CONCAT(tier||'='||n) FROM (SELECT tier,COUNT(*) n FROM mem GROUP BY tier)"),
    ("trust<1", "SELECT COUNT(*) FROM mem WHERE trust<1"),
    ("conflict=1", "SELECT COUNT(*) FROM mem WHERE conflict=1"),
    ("erase_stage>0", "SELECT COUNT(*) FROM mem WHERE erase_stage>0"),
    ("没向量", "SELECT COUNT(*) FROM mem WHERE emb IS NULL"),
    ("权重越界", "SELECT COUNT(*) FROM mem WHERE w<0 OR w>1"),
    ("时间锚在未来的", "SELECT COUNT(*) FROM mem WHERE t_anchor > strftime('%s','now')+3600"),
    ("测试残留（验收临时记忆）", "SELECT COUNT(*) FROM mem WHERE core LIKE '验收临时记忆%'"),
    ("台账 mem_tomb", "SELECT COUNT(*) FROM mem_tomb"),
    ("出生证 mem_birth", "SELECT COUNT(*) FROM mem_birth"),
    ("对话 / 已消化", "SELECT COUNT(*)||' / '||COALESCE(SUM(dreamed),0) FROM dialogue"),
    ("媒体 media", "SELECT COUNT(*) FROM media"),
    (" 孤儿/锁定/删过", "SELECT SUM(orphan_ts IS NOT NULL)||' / '||SUM(pinned=1)||' / '||(SELECT COUNT(*) FROM media_tomb) FROM media"),
    ("时间线事件 / 最大 seq", "SELECT COUNT(*)||' / '||MAX(seq) FROM timeline"),
    ("h_log / recall_event", "SELECT (SELECT COUNT(*) FROM h_log)||' / '||(SELECT COUNT(*) FROM recall_event)"),
]:
    print("  %-26s %s" % (label, q1(sql)))

miss = q1("SELECT COUNT(*) FROM mem_birth b LEFT JOIN mem m ON m.id=b.mid "
          "WHERE m.id IS NULL AND b.mid NOT IN (SELECT mid FROM mem_tomb)")
nob = q1("SELECT COUNT(*) FROM mem m LEFT JOIN mem_birth b ON m.id=b.mid "
         "WHERE b.mid IS NULL")
print("  %-26s %s" % ("有证没记录(未登记)", miss))
print("  %-26s %s" % ("有记录没证", nob))
if miss:
    bad("有 %s 条出生证找不到记忆、台账也没登记" % miss)
if nob:
    bad("有 %s 条记忆没有出生证" % nob)

rows = c.execute("SELECT sha256,path FROM media").fetchall()
lost = [r["sha256"] for r in rows if not os.path.exists(os.path.join(ROOT, r["path"]))]
if lost:
    bad("媒体文件丢了 %d 个: %s" % (len(lost), lost[:3]))
elif rows:
    ok("媒体文件 %d 个都在盘上" % len(rows))
c.close()

# ---------- 3. 链 / 出生证 / 媒体 / 锚 ----------
sec("3. 链、出生证、媒体、锚")
import card2
import timeline

card = card2.MemoryCard(DB)
tl = timeline.Timeline(card.db)
chain_ok, chain_bad = tl.verify_chain()
real_bad = [b for b in (chain_bad or []) if not str(b).startswith("已知删除")]
print("  链: %s | 问题 %d 条" % (chain_ok, len(real_bad)))
if real_bad:
    bad("链有问题：%s" % real_bad[:2])
birth_ok, birth_bad = tl.verify_birth()
real_birth = [b for b in (birth_bad or []) if not str(b).startswith("已知删除")]
print("  出生证: %s | 问题 %d 条" % (birth_ok, len(real_birth)))
if real_birth:
    bad("出生证有问题：%s" % real_birth[:2])
try:
    mv = card.verify_media()
    print("  媒体: %s" % json.dumps(mv, ensure_ascii=False)[:110])
    if isinstance(mv, dict) and (mv.get("broken") or mv.get("bad")):
        bad("媒体对账有坏账")
except Exception as e:
    warn("verify_media 没跑成：%s" % str(e)[:60])

import console
ig = console.card_integrity(DB)
print("  锚: %s（%s）| 卡 %s / 本地 %s / 卡外 %s" %
      (ig["verdict"], ig["reason"], ig["card_seq"], ig["local_seq"], ig["ext_seq"]))
if not ig["ok"]:
    bad("锚判定不是 ok：%s" % ig["reason"])
card.close()

# ---------- 4. 自检 ----------
sec("4. selfcheck.py")
env = dict(os.environ, PYTHONIOENCODING="utf-8")
try:
    out = subprocess.run([PY, "-X", "utf8", "selfcheck.py"], capture_output=True,
                         text=True, encoding="utf-8", errors="replace", env=env, timeout=240)
    txt = (out.stdout or "") + (out.stderr or "")
    for line in txt.splitlines():
        s = line.strip()
        if s.startswith(("✅", "❌", "⚠")):
            print("  " + s[:118])
    if "❌" in txt:
        for line in txt.splitlines():
            if "❌" in line:
                fails.append("selfcheck: " + line.strip()[:80])
except Exception as e:
    bad("selfcheck 跑不起来：%s" % str(e)[:100])

# ---------- 5. 接口 ----------
sec("5. 服务接口（8848）")


def get(path, timeout=40):
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception as e:
        return None, str(e).encode()


for path in ["/health", "/status", "/console_data", "/timeline", "/injection",
             "/dream/due", "/media/deadbeef"]:
    code, body = get(path)
    if code == 200:
        ok("%-14s 200  %d 字节" % (path, len(body)))
    elif code in (400, 404, 410):
        ok("%-14s %d（预期语义）" % (path, code))
    else:
        bad("%-14s %s %s" % (path, code, body[:60]))

# ---------- 6. 硬编码 ----------
sec("6. 写死的盘符路径（搬家会断）")
hits = []
for dirpath, dirnames, names in os.walk(ROOT):
    dirnames[:] = [d for d in dirnames if d not in SKIP]
    for fn in names:
        if not fn.endswith(".py"):
            continue
        p = os.path.join(dirpath, fn)
        for i, line in enumerate(io.open(p, encoding="utf-8", errors="replace"), 1):
            if re.search(r"[\"'][A-Za-z]:\\\\", line) and "示例" not in line and "<卡目录>" not in line:
                hits.append("%s:%d %s" % (os.path.relpath(p, ROOT), i, line.strip()[:80]))
for h in hits[:10]:
    print("  " + h)
if not hits:
    ok("没有写死的盘符路径")
else:
    warns.append("硬编码盘符 %d 处（搬家前要改）" % len(hits))

# ---------- 汇总 ----------
sec("结论")
if fails:
    print("  ❌ 问题 %d 个：" % len(fails))
    for f in fails:
        print("   - " + f)
else:
    print("  ✅ 没查出问题")
if warns:
    print("  ⚠️  提醒 %d 条：" % len(warns))
    for w in warns:
        print("   - " + w)
