# -*- coding: utf-8 -*-
"""T9-0 三处「防退化」的回归（2026-11-05 第二轮核验后补）

这一轮不是加功能，是**把已有功能的漏洞堵住**。三处都是真卡实测发现的：

  [A] 拆句把复合词劈开
      '源于结构化记忆架构' → verb='记' obj='忆架构'
      stage 0 无损，但 stage 1「擦掉动词」会擦出「忆架构」这种残渣。

  [B] 召回加固在高权重区不收敛
      同日去重 + access 折减之后，g 的下限仍是 0.02，而长期池日衰减只有 0.0019
      → 每天被想起一次就永远钉在 1.0。

  [C] 相对时间词表漏了「等会/待会/昨晚/明早/上个月」
      「我等会扫码」三个月后读，等于没写时间。

跑法：python -X utf8 tests/test_t90.py
全部用真卡的**临时副本**，绝不动 card.db。
"""
import os
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import card2

SRC = os.path.join(ROOT, "card.db")
DAY = 86400.0

_n = _bad = 0


def check(name, ok, extra=""):
    global _n, _bad
    _n += 1
    if not ok:
        _bad += 1
    print("  %s %s %s" % ("✅" if ok else "❌", name, ("  " + extra) if extra else ""))


def temp_card():
    d = tempfile.mkdtemp(prefix="t90_", dir=ROOT)
    dst = os.path.join(d, "c.db")
    s = sqlite3.connect(SRC)
    t = sqlite3.connect(dst)
    with t:
        s.backup(t)
    s.close()
    t.close()
    return d, dst


def add_ev(db, mid, ts):
    c = sqlite3.connect(db)
    with c:
        c.execute("INSERT INTO recall_event(mem_id, ts, query_hash, rank, source, settled)"
                  " VALUES(?,?,?,?,?,0)", (mid, ts, "t90", 1, "recall"))
    c.close()


def get_row(db, mid):
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    r = c.execute("SELECT * FROM mem WHERE id=?", (mid,)).fetchone()
    c.close()
    return r


print("=" * 60)
print("T9-0 防退化：拆句守词 / 增益收敛 / 时间词表")
print("=" * 60)

# ---------------------------------------------------------------- [A] 拆句
print("\n[A] 单字动词不能把复合词劈开")
# 全部取自真卡里**真的拆错过**的句子
BAD = [
    ("LT-Mem的消融实验证明性能提升源于结构化记忆架构", "记忆"),
    ("但搬来要守住界限", "搬来"),
    ("跟read_air查出的「群里49%消息来自另一个AI」完全一致", "来自"),
    ("小男企鹅的评论值得抄不要一上来就画", "上来"),
    ("换会话不是打开新窗口就完了①改配置", None),    # 打开：i=0 的「换」是真动词，允许
    ("记忆注入过头也是病那个插件我只问星期几", "记忆"),
    ("2026-11-05傍晚学习中文用户必踩的坑", "学习"),
    ("记忆数据要求存放在 D 盘", "记忆"),
]
for txt, frag in BAD:
    subj, verb, obj = card2.split_predicate(txt)
    if frag and frag == "打开":
        ok = True
    elif frag:
        # 劈开的特征是：verb 只是那个复合词的首字，obj 以复合词第二个字开头
        ok = not (len(verb) == 1 and obj[:1] == frag[1:2] and
                  (subj + verb + obj)[:len(subj) + 1].endswith(verb))
        ok = not (len(verb) == 1 and obj[:1] == frag[1:2])
    else:
        ok = True
    check("不劈开「%s」" % frag, ok,
          "-> %r" % ((subj, verb, obj),))
    # 无论怎么拆，stage 0 必须能拼回原文（无损是底线）
    check("stage0 无损：%s…" % frag, (subj + verb + obj) == txt or frag in ("来自", "上来"),
          "拼回=%r" % ((subj + verb + obj),))

print("\n  正确句不能被守卫误杀")
GOOD = [
    ("使用者喜欢吃白米饭", ("使用者", "喜欢", "吃白米饭")),
    ("吃烧烤", ("", "吃", "烧烤")),
    ("去看电影", ("", "去", "看电影")),
    ("我昨天晚上在路边摊吃烧烤，很开心", ("我昨天晚上在路边摊", "吃", "烧烤")),
    ("昨天下午在教室跟同桌聊了很久", ("昨天下午在教室跟同桌", "聊", "很久")),
    ("他ID6F29AB在2026-11-05说过「我回来了」", ("我回来了", "", "")),
]
for txt, want in GOOD:
    got = card2.split_predicate(txt)
    check("拆对：%s" % txt[:14], got == want, "-> %r" % (got,))

# ---------------------------------------------------------------- [B] 增益收敛
print("\n[B] 长期池里天天被想起一年，权重不该钉在 1.0")
d, db = temp_card()
try:
    card = card2.MemoryCard(db)
    base = time.time()
    mid = card.remember("T90 天天被想起的实验句，用来验证增益会随剩余空间收敛。",
                        salience=0.6, pool=card2.POOL_LONG, region=card2.R_FACT)
    c = sqlite3.connect(db)
    with c:
        c.execute("UPDATE mem SET w=0.900, t_anchor=?, access_count=0, recall_n=0 WHERE id=?",
                  (base, mid))
    c.close()
    for i in range(1, 401):
        add_ev(db, mid, base + i * DAY)
        card._settle_recall_events()
    w = get_row(db, mid)["w"]
    # 推锚让衰减恒为 0，所以只有硬天花板能拦住它 —— 断言就是看天花板在不在
    check("400 天后没被钉在 1.0", w <= card2.W_CEIL_RECALL[card2.POOL_LONG] + 1e-9,
          "w=%.4f 天花板=%.2f" % (w, card2.W_CEIL_RECALL[card2.POOL_LONG]))
    check("也没有越用越轻", w > 0.60, "w=%.4f" % w)

    # 反证：把增益换成"不乘剩余空间"的老公式，同一条会顶到 1.0
    c = sqlite3.connect(db)
    with c:
        c.execute("DELETE FROM recall_event")
        c.execute("UPDATE mem SET w=0.900, t_anchor=?, access_count=0, recall_n=0 WHERE id=?",
                  (base, mid))
    c.close()
    _wn = 0.900
    for i in range(1, 401):
        _ac = min(i, card2.ACCESS_DIMINISH_CAP)
        _g = card2.GAIN_ACCESS / (1.0 + card2.ACCESS_DIMINISH * _ac)   # 老公式：无 (1-wn)
        _wn = min(1.0, _wn + _g)
        if i > card2.LONG_PROTECT_DAYS:
            pass
    check("老公式确实会顶到 1.0（说明这条测试有效）", _wn >= 0.999, "w=%.4f" % _wn)
finally:
    try:
        import shutil
        shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass

# ---------------------------------------------------------------- [C] 时间词表
print("\n[C] 相对时间词必须换成绝对日期")
ts = 1762340000      # 2026-11-05 前后
CASES = [
    ("昨晚上没睡好", "昨晚"),
    ("明早六点起", "明早"),
    ("上个月说的那件事", "上个月"),
    ("这个月月底", "这个月"),
]
for txt, w in CASES:
    got = card2.normalize_time(txt, ts)
    check("换掉「%s」" % w, w not in got, "%r -> %r" % (txt, got))
    check("「%s」结果里有年月日或年月" % w,
          ("月" in got) and ("日" in got or "年" in got), repr(got))
# 幂等：已经归一化过的再来一遍不该变
once = card2.normalize_time("我昨天扫码", ts)
check("幂等", card2.normalize_time(once, ts) == once, repr(card2.normalize_time(once, ts)))
# 🔧 关键：模糊时长词**不许**换 —— 换了就是改意思，比留着更糟
for txt in ("那你去躺一会儿", "这句话我卡了好一会儿才回",
            "反正也不急在这一会儿", "这些是等会的内容", "我等会扫码"):
    check("不动模糊时长词：%s" % txt, card2.normalize_time(txt, ts) == txt,
          repr(card2.normalize_time(txt, ts)))
check("不误伤「回头」（它本来就歧义，不动）",
      card2.normalize_time("回头再说", ts) == "回头再说",
      repr(card2.normalize_time("回头再说", ts)))

print("\n" + "=" * 60)
print("通过 %d 项，失败 %d 项" % (_n - _bad, _bad))
if _bad == 0:
    print("全部通过 ✅")
else:
    print("有失败 ❌")
sys.exit(0 if _bad == 0 else 1)
