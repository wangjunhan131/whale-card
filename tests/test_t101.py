# -*- coding: utf-8 -*-
"""t101 · 环境体检脚本（tools/env_probe.py）的验收。

为什么值得测：
    到新机器上第一条命令就是它。如果它自己报错、或者把「缺什么」判错，
    那一整晚就废在「到底是环境不对还是脚本不对」上。

只测纯函数和真跑一次，不碰卡、不改系统。
"""
import io
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import env_probe as E

ok = 0
bad = 0


def chk(name, cond, extra=""):
    global ok, bad
    if cond:
        ok += 1
        print("  ✅ %s" % name)
    else:
        bad += 1
        print("  ❌ %s %s" % (name, extra))


print("== 1. 各探测函数都能跑、都返回 dict ==")
probes = [("os", E.probe_os), ("mem", E.probe_mem), ("python", E.probe_python),
          ("node", E.probe_node), ("ffmpeg", E.probe_ffmpeg), ("gpu", E.probe_gpu),
          ("torch", E.probe_torch), ("time", E.probe_time), ("card", E.probe_card),
          ("dsh", E.probe_dsh)]
r = {}
for name, fn in probes:
    try:
        v = fn()
        r[name] = v
        chk("%s() 返回 dict" % name, isinstance(v, dict))
    except Exception as e:
        chk("%s() 不抛" % name, False, repr(e))
        r[name] = {}

print("\n== 2. 磁盘（shutil.disk_usage 是 3 元组，这地方刚踩过） ==")
disks = E.probe_disks()
chk("probe_disks 返回 list", isinstance(disks, list))
chk("至少有一个盘", len(disks) >= 1, str(disks))
if disks:
    d0 = disks[0]
    chk("有 total_gb", isinstance(d0.get("total_gb"), float))
    chk("有 free_gb", isinstance(d0.get("free_gb"), float))
    chk("free <= total", d0["free_gb"] <= d0["total_gb"] + 0.1,
        "%s > %s" % (d0.get("free_gb"), d0.get("total_gb")))
    chk("free > 0", d0["free_gb"] > 0)

print("\n== 3. CPU 名字不能是列名 'Name' ==")
cpu = (r.get("os") or {}).get("cpu") or ""
chk("cpu 非空", bool(cpu))
chk("cpu 不是 'Name'", cpu.strip().lower() != "name", repr(cpu))
chk("cpu 不含换行", "\n" not in cpu, repr(cpu[:40]))

print("\n== 4. python 段能反映真实可用性 ==")
py = r.get("python") or {}
chk("有 version", bool(py.get("version")))
chk("numpy 是布尔", isinstance(py.get("numpy"), bool))
chk("sqlite3 是布尔", isinstance(py.get("sqlite3"), bool))
chk("本机 numpy=True", py.get("numpy") is True)
chk("本机 sqlite3=True", py.get("sqlite3") is True)

print("\n== 5. npm 要能取到版本（.cmd 垫片坑） ==")
node = r.get("node") or {}
if node.get("node"):
    chk("node 有版本", any(c.isdigit() for c in node["node"]), repr(node.get("node")))
if node.get("npm"):
    chk("npm 版本是数字串", any(c.isdigit() for c in node["npm"]), repr(node["npm"]))
else:
    chk("npm 缺失时是 None 而不是空串", node.get("npm") is None)

print("\n== 6. torch 段：没装也要给出原因 ==")
torch = r.get("torch") or {}
chk("有 installed 字段", "installed" in torch)
if not torch.get("installed"):
    chk("没装时有 why", bool(torch.get("why")), repr(torch.get("why")))
else:
    chk("装了就有 arch_list", "arch_list" in torch)

print("\n== 7. 卡自己的信息 ==")
card = r.get("card") or {}
chk("root 指向卡目录", card.get("root") == ROOT, repr(card.get("root")))
chk("card.db 存在", card.get("card_db") is True)
chk("有 card_db_mb", isinstance(card.get("card_db_mb"), (int, float)))
models = card.get("models") or []
names = [m["name"] for m in models]
chk("列出 bge-base-zh-v1.5", "bge-base-zh-v1.5" in names, str(names))
chk("每个模型都有 mb", all(isinstance(m.get("mb"), (int, float)) for m in models))

print("\n== 8. summary 分类逻辑 ==")
s = E.summarize(r)
chk("有 blocking 列表", isinstance(s.get("blocking"), list))
chk("有 optional 列表", isinstance(s.get("optional"), list))
time_state = (r.get("time") or {}).get("service_state")
if time_state and time_state != "RUNNING":
    chk("w32time 非 RUNNING 时进 blocking",
        any("时间" in x or "w32time" in x for x in s["blocking"]),
        str(s["blocking"]))
else:
    chk("w32time RUNNING 时不该冤枉它",
        not any("w32time" in x for x in s["blocking"]))

print("\n== 9. 真跑一次 --json，能被 json.loads 吃下 ==")
p = subprocess.run([sys.executable, "-X", "utf8",
                    os.path.join(ROOT, "tools", "env_probe.py"), "--json"],
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace", timeout=180)
chk("退出码 0", p.returncode == 0, (p.stderr or "")[:200])
try:
    obj = json.loads(p.stdout)
    ok_json = True
except Exception as e:
    obj = {}
    ok_json = False
    print("     ", repr(e), (p.stdout or "")[:200])
chk("stdout 是合法 JSON", ok_json)
chk("JSON 里有 10 个段",
    all(k in obj for k in ("os", "mem", "disks", "python", "node", "ffmpeg",
                           "gpu", "torch", "time", "card")),
    str(sorted(obj.keys())))

print("\n== 10. 给人看的模式也能跑完 ==")
p2 = subprocess.run([sys.executable, "-X", "utf8",
                     os.path.join(ROOT, "tools", "env_probe.py")],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=180)
chk("退出码 0", p2.returncode == 0, (p2.stderr or "")[:200])
out = p2.stdout or ""
chk("输出了标题", "环境体检" in out)
chk("输出了结论段", "必须先解决" in out or "没有阻塞项" in out)
chk("提到了 card.db", "card.db" in out)

print("\n== 11. 文档在（新机器上照着它装东西） ==")
for name in ("docs/ENV-CHECKLIST.md", "requirements.txt"):
    pth = os.path.join(ROOT, name.replace("/", os.sep))
    chk("%s 存在" % name, os.path.exists(pth))

doc = io.open(os.path.join(ROOT, "docs", "ENV-CHECKLIST.md"),
              encoding="utf-8").read()
for kw in ("w32time", "numpy", "ffmpeg", "sm_61", "CosyVoice2", "FishSpeech"):
    chk("清单里写了 %s" % kw, kw in doc)

print("\n" + "=" * 50)
print("t101 结果：%d 过 / %d 挂" % (ok, bad))
sys.exit(1 if bad else 0)
