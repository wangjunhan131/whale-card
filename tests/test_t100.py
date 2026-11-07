# -*- coding: utf-8 -*-
"""t100：抄官方 superagent 原型的两个点 —— 开口三档模式 + mandate 式参数指纹。

测的是 tools/proactive.py：
1) open.mode = off      → 一句都不发，次数不涨
2) open.mode = observe  → 不发、不记次数，但把「本来会说什么」写进 proactive_would.jsonl
3) open.mode = auto     → 真的走 send，次数 +1
4) policy_hash 稳定且有区分度；record_would 落盘可读
"""
import os, sys, json, time
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import chatcfg, proactive as P

ok = 0
bad = 0
def chk(name, cond, extra=""):
    global ok, bad
    if cond:
        ok += 1
        print("  ✅ " + name)
    else:
        bad += 1
        print("  ❌ " + name + ("  " + str(extra) if extra else ""))

WOULD = os.path.join(ROOT, "qq_state", "proactive_would.jsonl")
STATE = os.path.join(ROOT, "qq_state", "proactive.json")
CFGF = os.path.join(ROOT, "qq_state", "chat_cfg.json")

bak = {}
for p in (WOULD, STATE, CFGF):
    if os.path.exists(p):
        bak[p] = open(p, "rb").read()

def would_n():
    if not os.path.exists(WOULD):
        return 0
    return len([l for l in open(WOULD, encoding="utf-8") if l.strip()])

def state_count():
    if not os.path.exists(STATE):
        return 0
    try:
        return int(json.load(open(STATE, encoding="utf-8")).get("count") or 0)
    except Exception:
        return 0

# 让「有由头」这件事与现在几点无关
P.collect = lambda dt, st: [{"kind": "test", "txt": "测试一句"}]
P.send = lambda text, target=None: (True, "fake send")

print("[1] SPEC 里有 open.mode，且默认 auto")
spec = {s[0]: s for s in chatcfg.SPEC}
chk("open.mode 在 SPEC 里", "open.mode" in spec)
chk("默认值是 auto", spec.get("open.mode", (None,) * 5)[4] == "auto")

print("[2] 三档模式的行为")
chatcfg.save_cfg({"open.school": False, "open.meal": True, "open.night": True,
                  "reply.hours": "00:00-23:59", "open.mode": "off"})
n0, c0 = would_n(), state_count()
rc = P.main(["--dry"])
chk("off：main 返回 0", rc == 0, rc)
chk("off：不写 would", would_n() == n0, (n0, would_n()))
chk("off：次数不涨", state_count() == c0, (c0, state_count()))

chatcfg.save_cfg({"open.mode": "observe"})
n0 = would_n()
rc = P.main([])
chk("observe：main 返回 0", rc == 0, rc)
chk("observe：写了一条 would", would_n() == n0 + 1, (n0, would_n()))
rec = json.loads(open(WOULD, encoding="utf-8").read().strip().split("\n")[-1])
chk("observe：记录里有 kind/txt/policy", rec.get("kind") == "test" and rec.get("txt") == "测试一句" and len(rec.get("policy") or "") == 12, rec)
chk("observe：不记次数", state_count() == c0, (c0, state_count()))

chatcfg.save_cfg({"open.mode": "auto"})
sent = []
P.send = lambda text, target=None: (sent.append(text) or True, "fake send")
c0 = state_count()
rc = P.main([])
chk("auto：main 返回 0", rc == 0, rc)
chk("auto：真调了 send", sent == ["测试一句"], sent)
chk("auto：次数 +1", state_count() == c0 + 1, (c0, state_count()))

print("[3] 参数指纹 policy_hash")
cfg = P.load_cfg()
h1 = P.policy_hash(cfg)
h2 = P.policy_hash(dict(cfg))
chk("同参数同指纹", h1 == h2, (h1, h2))
cfg2 = dict(cfg); cfg2["cap"] = cfg["cap"] + 1
chk("改参数指纹就变", P.policy_hash(cfg2) != h1)
cfg3 = dict(cfg); cfg3["mode"] = "observe"
chk("改模式指纹也变（模式是判据的一部分）", P.policy_hash(cfg3) != h1)
chk("指纹是 12 位十六进制", len(h1) == 12 and all(ch in "0123456789abcdef" for ch in h1), h1)

print("[4] load_cfg 会把 mode 吐出来且只认三档")
chatcfg.save_cfg({"open.mode": "observe"})
chk("observe 读得回来", P.load_cfg().get("mode") == "observe", P.load_cfg().get("mode"))
chatcfg.save_cfg({"open.mode": "乱写"})
chk("乱写退回 auto", P.load_cfg().get("mode") == "auto", P.load_cfg().get("mode"))

# 还原
for p, b in bak.items():
    open(p, "wb").write(b)
for p in (WOULD, STATE, CFGF):
    if p not in bak and os.path.exists(p):
        try: os.remove(p)
        except Exception: pass

print("\n%d 过 / %d 挂" % (ok, bad))
sys.exit(1 if bad else 0)
