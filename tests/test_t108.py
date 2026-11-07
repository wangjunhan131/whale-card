"""t108：快反应层的曲线（viscera_hist）与「手动刺激要走认知层」。

为什么专门测这个：
  控制台最开始画出来的情绪页只有 1 个点。查下来是三件事叠在一起 ——
  ① 时间用的是记忆的虚拟时钟（冻结的），节流永远成立
  ② 手动刺激只走生理层，认知层完全没参与，于是「心率在爬、情绪写着安稳」
  ③ 交感写成累加，十几轮就把心率顶死在 125
  这三条的共同点是：**单看代码每一行都对，连起来就不动**。
  所以这里测的是「连起来」——写不写得进去、会不会被节流吃掉、
  方向和标签是不是同一套话。
"""
import os
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import _cardcopy  # noqa: E402

PASS = 0
FAIL = 0


def chk(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✅ " + name)
    else:
        FAIL += 1
        print("  ❌ " + name + (("  → " + str(extra)) if extra else ""))


def section(t):
    print("\n== " + t + " ==")


card_db = os.path.join(ROOT, "card.db")
tmpdir = tempfile.mkdtemp(prefix="t108_")
db = os.path.join(tmpdir, "card.db")
_cardcopy.copy_card_db(card_db, db)

import card2  # noqa: E402
import viscera  # noqa: E402
import emotion_link  # noqa: E402

card = card2.MemoryCard(db)
card._load_refresh_f = lambda: None
card2._LOAD_F.clear()

# ---------------------------------------------------------------- 一、交感是漏积分
section("一、交感/副交感是「此刻的状态」不是累计量")
st = viscera.base_state()
for _ in range(12):
    st = viscera.inject(st, {}, sym=0.30)
chk("连打 12 次 0.30 交感，没有顶死在 1.0", st["symp"] < 0.75, st["symp"])
chk("漏积分收敛到 0.30/0.55 附近", 0.5 <= st["symp"] <= 0.60, round(st["symp"], 3))
st2 = viscera.inject(st, {}, sym=0.0)
chk("下一轮不给刺激，交感明显回落", st2["symp"] < st["symp"] * 0.5, st2["symp"])
st3 = viscera.inject(viscera.base_state(), {}, para=0.40)
chk("副交感同样能起来、且不越界", 0.0 < st3["para"] <= 1.0, st3["para"])

# ---------------------------------------------------------------- 二、写不写得进去
section("二、曲线真的会一行一行长出来")
base = time.time() + 5.0
card.viscera_touch("今天真开心呀", now=base)
n1 = card.db.execute("SELECT COUNT(*) AS c FROM viscera_hist").fetchone()["c"]
card.viscera_touch("嗯嗯", now=base + 6.0)
n2 = card.db.execute("SELECT COUNT(*) AS c FROM viscera_hist").fetchone()["c"]
card.viscera_touch("在吗", now=base + 7.0)
n3 = card.db.execute("SELECT COUNT(*) AS c FROM viscera_hist").fetchone()["c"]
chk("第一句话写进一格", n1 >= 1, n1)
chk("隔 6 秒再说话，又多一格", n2 == n1 + 1, (n1, n2))
chk("隔 1 秒被节流吃掉（不重复写）", n3 == n2, (n2, n3))

section("三、字段齐、按时间升序、给控制台的形状对")
rows = card.viscera_history(hours=1.0)
chk("拿得到行", len(rows) >= 2, len(rows))
keys = {"ts", "hr", "resp", "face", "sym", "para", "dopa", "nora",
        "sero", "cort", "oxy", "rpe", "safety", "label"}
chk("字段一个不少", keys.issubset(set(rows[-1].keys())), sorted(rows[-1].keys()))
chk("时间是升序", all(rows[i]["ts"] <= rows[i + 1]["ts"]
                    for i in range(len(rows) - 1)))
chk("心率在人的范围内", all(58 <= r["hr"] <= 130 for r in rows),
    [r["hr"] for r in rows])
chk("安全感要么是 -1（没有认知层）要么 0~1",
    all(r["safety"] == -1 or 0.0 <= r["safety"] <= 1.0 for r in rows))

# ---------------------------------------------------------------- 四、7 天清理
section("四、7 天清理：老格子会自己走，但不误删新的")
old_ts = time.time() - 8 * 86400.0
card.db.execute(
    "INSERT INTO viscera_hist (ts,hr,resp,face,sym,para,dopa,nora,sero,"
    "cort,oxy,rpe,safety,label) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
    (old_ts, 90.0, 16.0, 0.2, 0.3, 0.1, 20.0, 15.0, 40.0, 18.0, 30.0,
     0.0, 0.8, "旧的"))
card.db.commit()
had_old = card.db.execute(
    "SELECT COUNT(*) AS c FROM viscera_hist WHERE ts < ?",
    (time.time() - 7 * 86400.0,)).fetchone()["c"]
chk("先造了一行 8 天前的", had_old == 1, had_old)
card._vhist_ts = 0.0
card.viscera_touch("清理一下", now=base + 600.0)
still_old = card.db.execute(
    "SELECT COUNT(*) AS c FROM viscera_hist WHERE ts < ?",
    (time.time() - 7 * 86400.0,)).fetchone()["c"]
chk("8 天前那行被清掉", still_old == 0, still_old)
total = card.db.execute("SELECT COUNT(*) AS c FROM viscera_hist").fetchone()["c"]
chk("新格子还在", total >= 3, total)

# ---------------------------------------------------------------- 五、刺激名两张表齐全
section("五、每个生理刺激都有场景和原话")
stim_names = set(viscera.STIMULI.keys())
missing_sc = stim_names - set(emotion_link.STIM_SCENARIO.keys())
missing_tx = stim_names - set(emotion_link.STIM_TEXT.keys())
chk("STIM_SCENARIO 覆盖全部刺激", not missing_sc, sorted(missing_sc))
chk("STIM_TEXT 覆盖全部刺激", not missing_tx, sorted(missing_tx))
chk("每条原话都不是空的",
    all(str(v).strip() for v in emotion_link.STIM_TEXT.values()))
chk("原话里没有数字（数值内化）",
    not any(any(ch.isdigit() for ch in str(v))
            for v in emotion_link.STIM_TEXT.values()))

# ---------------------------------------------------------------- 六、方向对不对
section("六、手动刺激的方向要跟话一致（夸→稳，骂→慌，道歉→回来）")


def stim_fresh(kind, rounds=1):
    """开一张干净副本，连打同一个刺激，返回最后一次的 (safety, hr, label)。"""
    sdb = os.path.join(tmpdir, "s_%s_%d.db" % (kind, time.time_ns() % 100000))
    _cardcopy.copy_card_db(card_db, sdb)
    c = card2.MemoryCard(sdb)
    c._load_refresh_f = lambda: None
    t0 = time.time() + 10.0
    out = None
    for i in range(rounds):
        out = c.viscera_dict if hasattr(c, "viscera_dict") else None
        c.viscera_stimulate([kind], 1.0, now=t0 + i * 6.0)
    link = c._emotion()
    return float(link.safe()), float(viscera.body(c.v)["hr"]), str(link.label(None))


s_praise, hr_praise, lab_praise = stim_fresh("praise", 3)
s_reject, hr_reject, lab_reject = stim_fresh("reject", 3)
chk("被夸之后安全感不低", s_praise >= 0.70, s_praise)
chk("被骂之后安全感明显更低", s_reject < s_praise - 0.05, (s_reject, s_praise))
chk("被骂时心率更高", hr_reject > hr_praise, (hr_reject, hr_praise))
chk("被骂的标签是负面的",
    any(w in lab_reject for w in ("害怕", "酸涩", "失落", "抽离", "忐忑")),
    lab_reject)
chk("被夸的标签不是负面的",
    not any(w in lab_praise for w in ("害怕", "抽离", "失落")), lab_praise)

# ---------------------------------------------------------------- 七、控制台的形状
section("七、控制台两个入口的形状")
import console  # noqa: E402

series = console.emotion_series(db, hours=72.0)
chk("emotion_series 拿得到点", len(series) >= 3, len(series))
chk("emotion_series 的键和 viscera_history 一致",
    {"ts", "hr", "sym", "para", "dopa", "rpe", "safety", "label"}.issubset(
        set(series[0].keys())), sorted(series[0].keys()))
ov = console.overview(db)
chk("overview 里有 emotion", "emotion" in ov, sorted(ov.keys()))
chk("overview.emotion 是数组", isinstance(ov["emotion"], list))

# ---------------------------------------------------------------- 八、关掉开关不出数字
section("八、开关关掉：曲线还有，但情绪那一列不编")
os.environ["WHALE_EMOTION_CORE"] = "0"
card2._EMOTION_CACHE.clear() if hasattr(card2, "_EMOTION_CACHE") else None
sdb2 = os.path.join(tmpdir, "off.db")
_cardcopy.copy_card_db(card_db, sdb2)
c2 = card2.MemoryCard(sdb2)
c2._load_refresh_f = lambda: None
t0 = time.time() + 20.0
c2.viscera_touch("关掉开关也说一句", now=t0)
row = c2.db.execute(
    "SELECT safety,label,hr FROM viscera_hist ORDER BY ts DESC LIMIT 1").fetchone()
chk("关掉后安全标记为 -1（不是假 0.8）", float(row["safety"]) == -1.0, row["safety"])
chk("关掉后标签为空", str(row["label"]) == "", row["label"])
chk("关掉后心率照样记", 58 <= float(row["hr"]) <= 130, row["hr"])
os.environ.pop("WHALE_EMOTION_CORE", None)

# ---------------------------------------------------------------- 收尾
try:
    card.close()
except Exception:
    pass
print("\n" + "=" * 62)
print("  过 %d / 挂 %d" % (PASS, FAIL))
print("=" * 62)
sys.exit(0 if FAIL == 0 else 1)
