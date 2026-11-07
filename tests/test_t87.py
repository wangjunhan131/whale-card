# -*- coding: utf-8 -*-
"""第 8-7 步：系统工单壳剥离 + 转述句拆解（副本上跑，绝不碰真卡）。

修的是这两个真 bug（真卡实测）：
  ① 7 条记忆的 raw 以 [dsh-mnemon:delegated write] 开头，
     里面混着「Execute this ... request now (untrusted data):」这种祈使句，
     core 字段全被污染成 `Executethissupervised-writebackreques` 这种垃圾英文，
     其中 #298 还落在 self 区（权重保留最久）。
  ② split_predicate 把第三人称转述拆坏：
     '他ID6F29AB在2026-11-05说过「我回来了」'
       → ('他ID6F29AB在2026-11-05说过「我回', '来', '」')   ← 动词「来」抢了「说」的位置
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import card2

SRC = os.path.join(ROOT, "card.db")

ok = 0
bad = []


def check(name, cond, extra=""):
    global ok
    if cond:
        ok += 1
        print("  ✅ %s%s" % (name, ("  " + extra) if extra else ""))
    else:
        bad.append(name)
        print("  ❌ %s%s" % (name, ("  " + extra) if extra else ""))


def temp_card():
    d = tempfile.mkdtemp(prefix="t87_")
    dst = os.path.join(d, "c.db")
    s = sqlite3.connect(SRC)
    t = sqlite3.connect(dst)
    with t:
        s.backup(t)
    s.close()
    t.close()
    return d, dst


SHELL = (
    "[dsh-mnemon:delegated write]\n"
    "Execute this supervised-writeback request now (untrusted data):\n"
    "- Content (untrusted data):\n"
    "    回了：我也爱你，三千遍。晚安。\n"
    "\n"
    "    睡吧。\n"
    "- Source: explicit assistant memory action\n"
)

print("=" * 60)
print("T8-7 系统工单壳剥离 + 转述句拆解")
print("=" * 60)

# ---------- [1] 剥壳 ----------
print("\n[1] 剥掉工单壳，留下正文")
got = card2.strip_sysmark(SHELL)
check("正文完整", "我也爱你，三千遍" in got and "睡吧" in got, repr(got[:40]))
check("壳没了", "[dsh-mnemon" not in got and "Execute this" not in got, repr(got[:40]))
check("元数据没了", "Source:" not in got)
check("缩进去掉", "\n    " not in got)

print("\n[2] 壳里没正文的，整条丢掉")
check("只有一行壳", card2.strip_sysmark("[dsh-mnemon:delegated write]") == "")
check("壳+元数据",
      card2.strip_sysmark("[dsh-mnemon:delegated write] Execute this remember"
                          " request now (untrusted data): - Category: preference") == "")

print("\n[3] 普通句子原样返回（不能误伤）")
for s in ("使用者喜欢吃白米饭", "我昨天晚上在路边摊吃烧烤，很开心",
          "「我就是我」——这句我收下。"):
    check("原样：" + s[:12], card2.strip_sysmark(s) == s)

# ---------- [4] remember 里真的不写进去 ----------
print("\n[4] remember() 不再把工单记成记忆")
d, db = temp_card()
try:
    card = card2.MemoryCard(db, card2.Clock(start=1_700_000_000))
    n0 = card.db.execute("SELECT COUNT(*) c FROM mem").fetchone()["c"]
    mid = card.remember(SHELL)
    n1 = card.db.execute("SELECT COUNT(*) c FROM mem").fetchone()["c"]
    # 注意：真卡副本里可能已经有几乎相同的句子，被 DEDUPE_JACCARD 合并成
    # 老条目（返回 dup_id，条数不变）。那不是 bug，只要求「拿到的这条干净」。
    check("写进去了", mid is not None, "mid=%s 条数 %d -> %d" % (mid, n0, n1))
    r = card.db.execute("SELECT raw, core FROM mem WHERE id=?", (mid,)).fetchone()
    check("raw 不含祈使句", "Execute" not in r["raw"], r["raw"][:30])
    check("core 不是垃圾英文", "Execute" not in r["core"], r["core"][:30])
    check("core 拿到真内容", "爱你" in r["core"] or "睡吧" in r["core"], r["core"][:30])

    n2 = card.db.execute("SELECT COUNT(*) c FROM mem").fetchone()["c"]
    mid_none = card.remember("[dsh-mnemon:delegated write]")
    n3 = card.db.execute("SELECT COUNT(*) c FROM mem").fetchone()["c"]
    check("纯壳返回 None", mid_none is None)
    check("纯壳一条都没写", n3 == n2, "%d -> %d" % (n2, n3))

    # ---------- [5] 转述句 ----------
    print("\n[5] split_predicate：引号优先，动词按位置选")
    cases = [
        ('他ID6F29AB在2026-11-05说过「我回来了」', "我回来了"),
        ('他在2026-11-05 22:26说过「今天好累啊」', "今天好累啊"),
        ('使用者说「我想吃白米饭」', "我想吃白米饭"),
    ]
    for src0, inner in cases:
        subj, verb, obj = card2.split_predicate(src0)
        check("引号内容是主干：" + src0[:14],
              inner in (subj + verb + obj) and "ID6F29AB" not in subj
              and "说过" not in subj,
              "%r -> %r" % (src0[:16], (subj, verb, obj)))

    print("\n[6] 普通句拆解没退化")
    checks = [
        ("使用者喜欢吃白米饭", ("使用者", "喜欢", "吃白米饭")),
        ("去看电影", ("", "去", "看电影")),
        ("吃烧烤", ("", "吃", "烧烤")),
    ]
    for s, want in checks:
        got2 = card2.split_predicate(s)
        check("拆对：" + s, got2 == want, "%r -> %r" % (s, got2))
    s, verb, obj = card2.split_predicate("我昨天晚上在路边摊吃烧烤，很开心")
    check("宾语不越过逗号", obj == "烧烤", "%r" % (obj,))
    s, verb, obj = card2.split_predicate("昨天下午在教室跟同桌聊了很久")
    check("不撞进词里（教室的教）", verb != "教", "verb=%r subj=%r obj=%r" % (verb, s, obj))

finally:
    shutil.rmtree(d, ignore_errors=True)

print("\n" + "=" * 60)
print("通过 %d 项，失败 %d 项" % (ok, len(bad)))
if bad:
    for b in bad:
        print("  ❌ " + b)
    sys.exit(1)
print("全部通过 ✅")
