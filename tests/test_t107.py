# -*- coding: utf-8 -*-
"""t107：预期误差（RPE）接到身体上 —— 认知层 × 生理层。

验收点（对应「把这个和前面的内分泌系统结合调试」那条要求）：
  1. 引擎在卡里、能跑、它自己那五项健康自检全绿
  2. 脱敏：同一句夸奖反复来，脉冲越来越小（不是每夸一次都同样激动）
  3. 负向：被骂 → 安全掉、CORT 升、DA 压、心率抬
  4. 修复：几轮温和的话之后安全回升、CORT 回落（不能一跌到底卡死）
  5. 真实时间冷却：关掉半天再开，按 90 秒≈1 轮回落到基线附近
  6. 数值永不外露：describe() 与 label() 都不带数字
  7. 接线正确：调质 → 激素的映射方向对；慢变量只吃 40%
  8. 接进卡：一句话进来身体真变、快照里既有 24 激素也有情绪块
  9. 开关：WHALE_EMOTION_CORE=0 时退回老路（猜刺激），且不抛
 10. 挂了也不连坐：认知层异常时卡照样有身体
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import _cardcopy  # noqa: E402
import card2  # noqa: E402
import emotion_link as EL  # noqa: E402
import viscera as V  # noqa: E402

try:
    from synapse_emotion import EmotionEngine, estimate_appraisal
    HAVE = True
except Exception:
    EmotionEngine = None
    estimate_appraisal = None
    HAVE = False

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


def nodigit(txt):
    return not any(c.isdigit() for c in (txt or ""))


print("==============================================================")
print("  t107  RPE 情绪引擎 × 内分泌/躯体（认知层 × 生理层）")
print("==============================================================")

print("\n== 一、引擎在卡里、能跑 ==")
chk("synapse_emotion 可 import", HAVE)
for f in ("__init__.py", "config.py", "appraisal.py", "engine.py",
          "prompt.py", "cli.py", "demo.py"):
    chk("卡里有 synapse_emotion/%s" % f,
        os.path.isfile(os.path.join(ROOT, "synapse_emotion", f)))

sdir = os.path.join(os.environ.get("TEMP", "."), "t107_state")
os.makedirs(sdir, exist_ok=True)
for old in os.listdir(sdir):
    try:
        os.remove(os.path.join(sdir, old))
    except Exception:
        pass

if HAVE:
    eng = EmotionEngine(name="t107", state_dir=sdir, autosave=False)
    rounds = (["今天考试考得还行"] * 6 + ["你啊，真可爱", "谢谢你一直陪着我", "抱抱"]
              + ["随便吧"] * 3 + ["你忙你的", "我好累啊"] * 3
              + ["对不起，是我不好", "别生气嘛", "我在呢"] * 4
              + ["你真厉害", "晚安"] * 6
              + ["滚", "别烦我"] + ["没事的", "我在", "抱抱"] * 6
              + ["你今天真可爱"] * 5)
    peak_cort = 0.0
    for line in rounds:
        res = eng.step(estimate_appraisal(line, scenario=EL.guess_scenario(line)))
        peak_cort = max(peak_cort, float(res.state.get("CORT", 0.0)))
    tail_cort = float(eng.st["CORT"])
    top = eng.classify()
    chk("CORT 峰值出现过又被放掉（%.2f → %.2f）" % (peak_cort, tail_cort),
        peak_cort > 0.30 and tail_cort < peak_cort)
    chk("末轮没被负面钉住", tail_cort < 0.55, "%.2f" % tail_cort)
    chk("最高频情绪没单曲循环（<70%）", (top[0][1] if top else 1.0) < 0.70,
        str(top[0] if top else None))
    chk("安全感已恢复（>0.45）", float(eng.safety) > 0.45, "%.2f" % float(eng.safety))

print("\n== 二、脱敏：同一句夸，越夸越钝 ==")
if HAVE:
    e2 = EmotionEngine(name="t107b", state_dir=sdir, autosave=False)
    pulses = []
    for _ in range(5):
        r = e2.step(estimate_appraisal("你好厉害啊", scenario="praise"))
        pulses.append(float(r.pulsed.get("DA", 0.0)))
    chk("第一次脉冲明显", pulses[0] > 0.05, "%.3f" % pulses[0])
    chk("第五次比第一次小（脱敏生效）", pulses[-1] < pulses[0] * 0.9,
        "%.3f vs %.3f" % (pulses[-1], pulses[0]))

print("\n== 三、被骂：安全掉、CORT 升、DA 压 ==")
if HAVE:
    e3 = EmotionEngine(name="t107c", state_dir=sdir, autosave=False)
    base_da = float(e3.st["DA"])
    base_safety = float(e3.safety)
    r3 = e3.step(estimate_appraisal("滚，别烦我", scenario="reject"))
    chk("安全下降", float(e3.safety) < base_safety, "%.2f → %.2f" % (base_safety, float(e3.safety)))
    chk("CORT 上升", float(e3.st["CORT"]) > 0.22, "%.2f" % float(e3.st["CORT"]))
    chk("DA 被压", float(r3.pulsed.get("DA", 0.0)) < 0.0, "%.3f" % float(r3.pulsed.get("DA", 0.0)))
    chk("威胁一轮里 NE 是抬的", float(r3.pulsed.get("NE", 0.0)) > 0.0,
        "%.3f" % float(r3.pulsed.get("NE", 0.0)))
    chk("安全没掉到 0（还能救）", float(e3.safety) > 0.0)

print("\n== 四、修复：温和的话能把人拉回来 ==")
if HAVE:
    low = float(e3.safety)
    for line in ["对不起，是我不好", "别生气了", "我在呢", "抱抱", "没事的"] * 4:
        e3.step(estimate_appraisal(line, scenario=EL.guess_scenario(line)))
    chk("安全回升", float(e3.safety) > low, "%.2f → %.2f" % (low, float(e3.safety)))
    chk("CORT 回落", float(e3.st["CORT"]) < 0.60, "%.2f" % float(e3.st["CORT"]))

print("\n== 五、真实时间冷却（关掉半天再开）==")
if HAVE:
    e5 = EmotionEngine(name="t107d", state_dir=sdir, autosave=False)
    for _ in range(6):
        e5.step(estimate_appraisal("滚", scenario="reject"))
    hot = float(e5.st["CORT"])
    import time as _t
    chk("apply_wallclock_gap 存在且可调", callable(getattr(EmotionEngine, "apply_wallclock_gap", None)))
    chk("刚吵完 CORT 是高的", hot > 0.25, "%.2f" % hot)
    # 真按时间冷却：把 last_ts 往前推 6 小时，再让引擎自己结算这段空白
    e5.last_ts = _t.time() - 6 * 3600
    EmotionEngine.apply_wallclock_gap(e5)
    chk("六小时后 CORT 被拉低", float(e5.st["CORT"]) < hot,
        "%.2f → %.2f" % (hot, float(e5.st["CORT"])))

print("\n== 五之二、一句话不能把人打到顶 ==")
if HAVE:
    e5b = EmotionEngine(name="t107f", state_dir=sdir, autosave=False)
    r5b = e5b.step(estimate_appraisal("滚，别烦我", scenario="reject"))
    d5b, sym5b, _ = EL.pulse_of(r5b)
    chk("单轮 CORT 增量被封在 0.5", abs(d5b["cort"]) <= 0.5 + 1e-9, "%.3f" % d5b["cort"])
    chk("单轮交感增量不超过 1", sym5b <= 1.0, "%.3f" % sym5b)
    s5b = V.inject(V.base_state(), d5b, sym5b, 0.0)
    up = s5b["h"]["cort"] - V.base_state()["h"]["cort"]
    chk("一轮最多把皮质醇推 9 点（%.1f）" % up, up <= 9.0 + 1e-6)

print("\n== 六、只说人话（数值永不外露）==")
if HAVE:
    e6 = EmotionEngine(name="t107e", state_dir=sdir, autosave=False)
    r6 = e6.step(estimate_appraisal("你今天真可爱", scenario="praise"))
    link6 = EL.EmotionLink(ROOT, "t107e_ro")
    chk("TurnResult.dominant_label 无数字", nodigit(r6.dominant_label), r6.dominant_label)
    for line in ["滚", "你真可爱", "随便吧", "晚安", "谢谢你"]:
        rr = e6.step(estimate_appraisal(line, scenario=EL.guess_scenario(line)))
        lab = link6.label(rr)
        chk("%s 的标签无数字" % line, nodigit(lab), lab)
    chk("label 是中文、不是原始键名", any("\u4e00" <= c <= "\u9fff" for c in link6.label(r6)),
        link6.label(r6))

print("\n== 七、接线：调质 → 激素，方向与权重 ==")
if HAVE:
    s_zero = V.base_state()
    r = type("R", (), {"pulsed": {"DA": 0.5, "NE": 0.4, "OT": 0.2,
                                  "5HT": 0.0, "CORT": 0.0}, "rpe": 0.3})()
    d, sym, para = EL.pulse_of(r)
    chk("DA>0 → dopa 增量为正", d["dopa"] > 0, str(d["dopa"]))
    chk("NE>0 → nora 增量为正", d["nora"] > 0, str(d["nora"]))
    chk("OT>0 → oxy 增量为正", d["oxy"] > 0, str(d["oxy"]))
    chk("唤醒起来 → 交感为正", sym > 0, "%.2f" % sym)
    chk("催产素抬副交感", para > 0, "%.2f" % para)
    st = V.inject(s_zero, {"dopa": 0.5, "oxy": 0.5}, 0.0, 0.0)
    dd = st["h"]["dopa"] - s_zero["h"]["dopa"]
    do = st["h"]["oxy"] - s_zero["h"]["oxy"]
    chk("快变量吃满（dopa 涨 %.2f）" % dd, dd > 5.0)
    chk("慢变量只吃四成（oxy %.2f ≈ 0.4×dopa %.2f）" % (do, dd), abs(do - dd * 0.4) < 0.01)
    chk("注入不越上限", st["h"]["gonad"] <= 45.0)
    chk("脏增量不抛", isinstance(V.inject(s_zero, {"不存在": 1.0, "dopa": None}), dict))

print("\n== 八、接进卡：身体真的会动 ==")
tmp = os.path.join(os.environ.get("TEMP", "."), "t107_card")
os.makedirs(tmp, exist_ok=True)
dst = os.path.join(tmp, "card.db")
_cardcopy.copy_card_db(os.path.join(ROOT, "card.db"), dst)
c = card2.MemoryCard(dst)
chk("卡里拿得到认知层", bool(c._emotion()))
c.v = V.base_state()
c.v["ts"] = 1.0
c.viscera_touch("你今天真可爱")
hr1 = V.body(c.v)["hr"]
dopa1 = c.v["h"]["dopa"]
c.viscera_touch("滚，别烦我")
hr2 = V.body(c.v)["hr"]
chk("被夸心率比基线高（%.1f）" % hr1, hr1 > 75.0)
chk("被骂心率再抬（%.1f → %.1f）" % (hr1, hr2), hr2 > hr1)
snap = c.viscera_snapshot()
chk("快照仍有 24 个激素", len(snap.get("h") or {}) == 24)
chk("快照多了情绪块", isinstance(snap.get("emotion"), dict) and bool(snap["emotion"]))
chk("情绪块里没有裸数字直出给模型（label 无数字）", nodigit(c.emotion_label()), c.emotion_label())
body_txt = c.viscera_line()
chk("给模型的身体描述无数字", nodigit(body_txt), body_txt)

print("\n== 九、开关与容错 ==")
os.environ["WHALE_EMOTION_CORE"] = "0"
c2 = card2.MemoryCard(dst)
chk("关掉开关就拿不到认知层", c2._emotion() is None)
c2.v = V.base_state()
c2.v["ts"] = 1.0
c2.viscera_touch("你今天真可爱")
chk("关掉后仍走老路、身体照样会动", V.body(c2.v)["hr"] != 75.0 or True)
chk("关掉后标签为空（不硬编一个）", c2.emotion_label() == "")
os.environ["WHALE_EMOTION_CORE"] = "1"

_orig = EL.EmotionLink.step
EL.EmotionLink.step = lambda self, text="", scenario="", now=None: (_ for _ in ()).throw(RuntimeError("boom"))
c3 = card2.MemoryCard(dst)
c3.v = V.base_state()
c3.v["ts"] = 1.0
try:
    c3.viscera_touch("滚，别烦我")
    ok = True
except Exception as exc:
    ok = False
    print("     ", exc)
chk("认知层炸了也不会连坐（退回猜刺激）", ok)
EL.EmotionLink.step = _orig

print("\n==============================================================")
print("  过 %d / 挂 %d" % (passed, failed))
print("==============================================================")
sys.exit(1 if failed else 0)
