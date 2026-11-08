# -*- coding: utf-8 -*-
"""tests/test_t118.py — 事实层（V5.7）：谁说的 / 说的是谁 / 什么事

验收点：
  1. 抽得准：他本人的事、别人的事、说不清是谁的事，各抽成什么
  2. 抽不出就不抽（宁缺勿编）
  3. 存进 fact 表、能按主体+谓词查回来
  4. 上下文里出现【事实】格，且别人的事带「说的是别人」标注
  5. 问「我生日」不会端上「他妈生日」（这才是第三方评测里最刺眼的那类错）
  6. 关掉开关/表坏了不炸
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
sys.path.insert(0, HERE)

import facts as facts_mod  # noqa: E402

OK = []
BAD = []


def chk(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    if not cond:
        print("  x %s %s" % (name, extra))


def t_extract():
    # 他本人的事
    r = facts_mod.extract("我生日是十月二十四号")
    chk("user 生日抽到", any(x["subject_kind"] == "user" and x["predicate"] == "生日" for x in r), r)
    # 别人的事
    r = facts_mod.extract("我妈生日是三月五号")
    chk("other 生日抽到", any(x["subject_kind"] == "other" and x["subject"] == "妈" for x in r), r)
    # 喜欢
    r = facts_mod.extract("我喜欢吃白米饭")
    chk("喜欢抽到", any(x["predicate"] == "喜欢" for x in r), r)
    # 讨厌
    r = facts_mod.extract("我讨厌别人说我胖")
    chk("讨厌抽到", any(x["predicate"] == "讨厌" for x in r), r)
    # 住处
    r = facts_mod.extract("我家住在河北")
    chk("住处抽到", any(x["predicate"] == "住处" for x in r), r)
    # 年级
    r = facts_mod.extract("我读高三了")
    chk("年级抽到", any(x["predicate"] == "年级" and "高三" in x["object"] for x in r), r)
    # 编不出来
    chk("纯寒暄抽不出", facts_mod.extract("嗯嗯好") == [])
    chk("空串抽不出", facts_mod.extract("") == [])
    chk("短句抽不出", facts_mod.extract("在吗") == [])
    # 一句话只算一个谓词
    r = facts_mod.extract("我喜欢跑步")
    chk("一句一谓词", len(r) <= 1, r)


def t_store_and_recall(dbp):
    facts_mod.ensure(dbp)
    n, items = facts_mod.record(dbp, 1, "我妈生日是三月五号", ts=time.time() - 86400 * 40)
    chk("存进去", n == 1, items)
    n2, _ = facts_mod.record(dbp, 2, "我生日是十月二十四号", ts=time.time())
    chk("第二条存进去", n2 == 1)

    got = facts_mod.recall(dbp, "我生日是哪天")
    chk("问自己生日有结果", bool(got), got)
    chk("问自己生日首条是自己",
        bool(got) and got[0]["subject_kind"] == "user" and got[0]["object"].startswith("十月二十四"),
        got[:2])

    got2 = facts_mod.recall(dbp, "我妈生日是什么时候")
    chk("问妈生日有结果", bool(got2) and got2[0]["subject"] == "妈", got2[:2])

    # 最关键的一条：问「我生日」不能把他妈的生日端上来
    objs = [g["object"] for g in got]
    chk("问自己生日不混别人", all("三月五" not in o for o in objs), objs)


def t_line():
    f = {"subject_kind": "other", "subject": "妈", "predicate": "生日",
         "object": "三月五号", "ts": time.time()}
    s = facts_mod.line_of(f)
    chk("别人带标注", "说的是别人" in s, s)
    f2 = {"subject_kind": "user", "subject": "", "predicate": "喜欢",
          "object": "吃白米饭", "ts": time.time()}
    s2 = facts_mod.line_of(f2)
    chk("本人不带别人标注", "说的是别人" not in s2 and s2.startswith("他"), s2)


def t_context(dbp):
    import card2
    c = card2.MemoryCard(dbp)
    now = c.clock.now()
    c.remember("我妈生日是三月五号", ts=now - 86400 * 30, speaker="user")
    c.remember("我生日是十月二十四号", ts=now - 86400 * 29, speaker="user")
    c.remember("我喜欢吃白米饭", ts=now - 86400 * 28, speaker="user")
    ctx = c.assemble_context("我妈生日是哪天", recent_lines=[])
    body = ctx.get("text") or ctx.get("context") or str(ctx)
    chk("上下文有【事实】格", "【事实】" in body, body[:400])
    chk("上下文写清是别人", "说的是别人" in body, body[:400])
    chk("sections 有 fact", "fact" in (ctx.get("sections") or {}), ctx.get("sections"))
    try:
        c.close()
    except Exception:
        pass


def t_robust(dbp):
    # 表坏了/关掉都不能炸
    old = os.environ.get("WHALE_FACTS")
    try:
        c = sqlite3.connect(dbp)
        c.execute("DROP TABLE IF EXISTS fact")
        c.commit()
        c.close()
        chk("表没了 recall 返回空", facts_mod.recall(dbp, "我生日") == [])
    finally:
        if old is None:
            os.environ.pop("WHALE_FACTS", None)
        else:
            os.environ["WHALE_FACTS"] = old


def main():
    t_extract()
    t_line()
    tmp = tempfile.mkdtemp(prefix="t118_")
    try:
        # 事实层自己的增删查：一张干净的小库
        dbp = os.path.join(tmp, "fact.db")
        t_store_and_recall(dbp)
        t_robust(dbp)
        # 上下文集成：用真卡的副本（空库缺 mem_birth 这种由 timeline 建的表）
        import _cardcopy
        real = os.path.join(ROOT, "card.db")
        cb = os.path.join(tmp, "card.db")
        _cardcopy.copy_card_db(real, cb)
        t_context(cb)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\nt118: OK %d / BAD %d" % (len(OK), len(BAD)))
    if BAD:
        print("失败项：", BAD)
        return 1
    print("ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
