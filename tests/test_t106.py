# -*- coding: utf-8 -*-
"""t106：内分泌 + 自主神经 + 躯体体征（快反应层）。

验收点（对应「先有生理反应，才有情绪变化」那条要求）：
  1. 纯函数、可缺省、不抛
  2. 一句话进来 → 交感先动 → 心率/呼吸/脸红立刻变
  3. 慢激素几乎不动（不能一句话改掉三天的依恋）
  4. 会回落（几小时后回到基线附近）
  5. 负反馈：多巴胺狂飙会被 CCK 压回来；去甲过高会被 GABA 压
  6. 拮抗：交感 vs 副交感
  7. 上限：促性腺激素封顶；心率封顶 130
  8. describe() 只说人话、绝不出现数字
  9. 接进上下文：出现【身体】格，且很省字（<120 字）
 10. 接口 /viscera 与 /viscera/stim 在源里存在
"""
import io
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import _cardcopy  # noqa: E402
import card2  # noqa: E402
import viscera as V  # noqa: E402

passed = 0
failed = 0


def chk(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print("  ✅ %s" % name)
    else:
        failed += 1
        print("  ❌ %s %s" % (name, extra))


print("==============================================================")
print("  t106  内分泌 + 自主神经 + 躯体")
print("==============================================================")

print("\n== 一、纯函数与容错 ==")
s0 = V.base_state()
chk("基线有 24 个变量", len(s0["h"]) == 24, str(len(s0["h"])))
chk("基线心率接近 75", abs(V.body(s0)["hr"] - 75) < 3, str(V.body(s0)["hr"]))
chk("空字典不炸", isinstance(V.normalize(None), dict) and isinstance(V.normalize({}), dict))
chk("脏值按基线补", abs(V.normalize({"h": {"dopa": "xx"}, "symp": None})["h"]["dopa"] - 20.0) < 1e-9)
chk("超范围会被夹住", V.normalize({"h": {"dopa": 999}})["h"]["dopa"] <= 100)
chk("不动时间就不衰减", V.step(s0, 0.0, [])["h"]["dopa"] == s0["h"]["dopa"])

print("\n== 二、先生理、后情绪 ==")
after = V.step(s0, 0.05, [("praise", 1.0)])
b0, b1 = V.body(s0), V.body(after)
chk("一句夸奖心率就上去了", b1["hr"] - b0["hr"] >= 8, "%.1f→%.1f" % (b0["hr"], b1["hr"]))
chk("脸也红了", b1["face"] > b0["face"] + 0.2, "%.2f→%.2f" % (b0["face"], b1["face"]))
chk("呼吸变浅", b1["resp"] >= b0["resp"], "%.1f→%.1f" % (b0["resp"], b1["resp"]))
chk("交感确实被拉起来了", after["symp"] > s0["symp"] + 0.2, "%.2f→%.2f" % (s0["symp"], after["symp"]))

slow_keys = ("oxy", "endo", "gonad", "adh")
slow_moves = [abs(after["h"][k] - s0["h"][k]) for k in slow_keys]
chk("慢激素几乎没动（一句话不改三天）", max(slow_moves) <= 2.0,
    "max=%.2f" % max(slow_moves))
chk("快激素动了", after["h"]["nora"] - s0["h"]["nora"] >= 4,
    "nora %.1f→%.1f" % (s0["h"]["nora"], after["h"]["nora"]))

print("\n== 三、会回落 ==")
later = V.step(after, 6.0, [])
chk("六小时后心率回到基线附近", abs(V.body(later)["hr"] - 75) < 6,
    "%.1f" % V.body(later)["hr"])
chk("交感也散了", later["symp"] < 0.25, "%.2f" % later["symp"])

print("\n== 四、负反馈与拮抗 ==")
hot = V.base_state()
hot["h"]["dopa"] = 95.0
r1 = V.regulate(hot)
chk("多巴胺过高压出 CCK", r1["h"]["cck"] > V.H_BASE["cck"], "%.1f" % r1["h"]["cck"])
chk("CCK 反过来压多巴胺", r1["h"]["dopa"] < 95.0, "%.1f" % r1["h"]["dopa"])

nor = V.base_state()
nor["h"]["nora"] = 95.0
r2 = V.regulate(nor)
chk("去甲过高抬 GABA", r2["h"]["gaba"] > V.H_BASE["gaba"], "%.1f" % r2["h"]["gaba"])
chk("GABA 抬了副交感", r2["para"] > nor["para"])

ant = V.base_state()
ant["symp"] = 1.0
ant["para"] = 1.0
r3 = V.regulate(ant)
chk("交感副交感互相削", r3["symp"] < 1.0 and r3["para"] < 1.0,
    "%.2f / %.2f" % (r3["symp"], r3["para"]))

print("\n== 五、上限 ==")
g = V.base_state()
for _ in range(200):
    g = V.step(g, 0.01, [("caress", 2.0)])
chk("促性腺激素封顶 45", g["h"]["gonad"] <= 45.0, "%.2f" % g["h"]["gonad"])
chk("心率不超过 130", V.body(g)["hr"] <= 130.0, "%.1f" % V.body(g)["hr"])
chk("心率不低于 58", V.body(V.normalize({"para": 1.0}))["hr"] >= 58.0)

print("\n== 六、只说人话 ==")
for kind in ("praise", "criticize", "comfort", "night", "cold"):
    txt = V.describe(V.step(s0, 0.1, [(kind, 1.0)]))
    chk("%s 的描述没有数字" % kind, not any(c.isdigit() for c in txt), txt)
chk("描述非空", len(V.describe(s0)) > 0)
chk("描述不会太长（<=3 段）", V.describe(V.step(s0, 0.1, [("praise", 1.5)])).count("；") <= 2)

print("\n== 七、猜刺激 ==")
chk("夸 → praise", V.guess("你今天真可爱")[0][0] == "praise")
chk("冷 → cold", V.guess("随便吧")[0][0] == "cold")
chk("累 → worry", V.guess("我好累啊")[0][0] == "worry")
chk("普通话 → chat", V.guess("晚安")[0][0] == "chat")
chk("猜不出也不会抛", isinstance(V.guess(None), list))

print("\n== 八、接进卡：上下文里真有【身体】 ==")
tmp = os.path.join(os.environ.get("TEMP", "."), "t106_card")
os.makedirs(tmp, exist_ok=True)
dst = os.path.join(tmp, "card.db")
_cardcopy.copy_card_db(os.path.join(ROOT, "card.db"), dst)
c = card2.MemoryCard(dst)
ctx = c.assemble_context("你今天真可爱", touch=True)
chk("sections 里有 viscera", "viscera" in ctx["sections"], str(ctx["sections"]))
chk("正文里有【身体】", "【身体】" in ctx["text"])
chk("身体格很省字", ctx["sections"].get("viscera", 999) <= 120,
    str(ctx["sections"].get("viscera")))
chk("身体格里没有数字",
    not any(ch.isdigit() for ch in ctx["text"].split("【身体】")[1].split("\n\n")[0]))
snap = c.viscera_snapshot()
chk("快照有 24 个激素", len(snap.get("h") or {}) == 24)
chk("不动就不写（touch=False 不新增格）",
    "viscera" not in c.assemble_context("嗯", touch=False)["sections"])

print("\n== 九、接口在源里 ==")
src = io.open(os.path.join(ROOT, "mind_server.py"), encoding="utf-8").read()
chk("有 GET /viscera", '"/viscera"' in src)
chk("有 POST /viscera/stim", '"/viscera/stim"' in src)
chk("assemble_context 传了 touch=True", "touch=True" in src)

print("\n==============================================================")
print("  过 %d / 挂 %d" % (passed, failed))
print("==============================================================")
sys.exit(1 if failed else 0)
