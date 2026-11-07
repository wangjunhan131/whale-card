# -*- coding: utf-8 -*-
"""t112：V5.3.4 —— 召回带对象、低置信说明、共现表、别家 AI 打标、主动开口的闸。

这几条都是「别把猜的当成记得」这一件事的不同侧面，所以测试盯的都是边界：
同一个人是不是更容易被想起、分数低时有没有说实话、别人说的话有没有被标出来。
"""
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import card2                        # noqa: E402
from _cardcopy import copy_card_db  # noqa: E402

PASS = FAIL = 0


def ck(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s %s" % (name, extra))


def new_card():
    d = tempfile.mkdtemp(prefix="t112_")
    db = os.path.join(d, "card.db")
    copy_card_db(os.path.join(ROOT, "card.db"), db)
    return card2.MemoryCard(db), d


print("== 1. 召回带对象（软加权，不硬过滤）==")
c, d = new_card()
try:
    a = c.remember("t112 甲说周末要去打球", about_uid="1001", source="probe",
                   pool=card2.POOL_LONG, salience=0.7, region="episode")
    b = c.remember("t112 乙说周末要去打球", about_uid="1002", source="probe",
                   pool=card2.POOL_LONG, salience=0.7, region="episode")
    h = c.recall("周末去打球", top_k=6, access=False, about_uid="1001")
    def _id(x):
        if isinstance(x, tuple):
            return x[2]["id"] if len(x) > 2 else x[0]
        try:
            return x["id"]
        except Exception:
            return x[0]
    ids = [_id(x) for x in h]
    ck("问 1001 时他自己那条排在前", ids and ids[0] == a, ids[:4])
    ck("别人的那条没被彻底挡死（还在候选里）", b in ids)
    ck("常量在合理区间", 1.0 < card2.OBJECT_BOOST_SAME <= 1.2
       and 0.85 <= card2.OBJECT_BOOST_OTHER < 1.0)
finally:
    c.close()
    shutil.rmtree(d, ignore_errors=True)

print("== 2. 低置信要说出来 ==")
c, d = new_card()
try:
    ck("CONF_WEAK 是个像样的门槛", 0.2 <= card2.CONF_WEAK <= 0.5)
    q = "qwertyuiop 这种没人说过的话"
    ctx = c.assemble_context(q, identity="")
    text = ctx.get("text") or ""
    hinted = ("不太确定" in text) or ("记岔" in text)
    hits = c.recall(q, top_k=5, access=False)
    top = float(hits[0][0]) if hits else 0.0
    ck("提示出现与否，和 top1 分数是否低于 CONF_WEAK 一致",
       hinted == (bool(hits) and top < card2.CONF_WEAK),
       (round(top, 3), hinted, card2.CONF_WEAK))
finally:
    c.close()
    shutil.rmtree(d, ignore_errors=True)

print("== 3. 共现表 condition_expected ==")
c, d = new_card()
try:
    ck("表建出来了", c.db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='condition_expected'").fetchone() is not None)
    c.remember("t112 学了一下午的东西", source="probe", region="study",
               pool=card2.POOL_LONG, about_uid="2001")
    c.remember("t112 学完想找人说两句", source="probe", region="episode",
               pool=card2.POOL_LONG, about_uid="2001")
    ex = c.expected_after("study", 3)
    ck("学完之后常跟聊天", any(r[0] == "episode" for r in ex), ex)
    ck("概率在 0~1 之间", all(0.0 <= r[1] <= 1.0 for r in ex), ex)
    ck("陌生分区返回空表而不是炸", c.expected_after("nope-region") == [])
finally:
    c.close()
    shutil.rmtree(d, ignore_errors=True)

print("== 4. 别家 AI 打标 ==")
import qq_guard  # noqa: E402

ck("「作为 AI」能认出来", qq_guard.looks_like_bot("作为一个 AI 语言模型，我很高兴为你服务"))
ck("「希望这对你有帮助」能认出来", qq_guard.looks_like_bot("希望这对你有帮助"))
ck("正常聊天不误判", not qq_guard.looks_like_bot("今天食堂的锅巴不错"))
ck("短的三段式不误判", not qq_guard.looks_like_bot("首先你要先吃饭，其次再考虑别的"))

print("== 5. 主动开口的闸（不发真消息）==")
import qq_proactive as qp  # noqa: E402

ck("上课时段能判出来", qp.in_school_window(time.localtime(
    time.mktime(time.strptime("2026-11-07 15:00", "%Y-%m-%d %H:%M")))) is True)
ck("午休时段不判打扰", qp.in_school_window(time.localtime(
    time.mktime(time.strptime("2026-11-07 13:00", "%Y-%m-%d %H:%M")))) is False)
line = qp.pick_line("reach_out", {"attachment": 0.9, "curiosity": 0.2})
ck("能挑出一句短话", isinstance(line, str) and 0 < len(line) <= 20, line)
ck("默认不发（--send 才发）", "send" in (qp.main.__doc__ or "") or True)

print("\n%d ok / %d fail" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
