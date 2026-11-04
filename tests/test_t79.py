# -*- coding: utf-8 -*-
"""test_t79.py —— 习惯层 v2：把稳定习惯塞进【我是谁】

v1 只挖不注入，报告写完就躺在那儿；v2 让它真的影响说话。
规矩是三条：只取 stable/pinned、最多 5 条且总字数有上限、能一键关掉。
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import card2      # noqa: E402
import habits     # noqa: E402
import identity   # noqa: E402

OK = 0
BAD = []


def ck(name, cond, extra=""):
    global OK
    if cond:
        OK += 1
        print("  ✅ %s" % name)
    else:
        BAD.append(name)
        print("  ❌ %s %s" % (name, extra))


def _add(con, key, status, *, hits=3, days=3, weight=0.6, pinned=0,
         trigger="", behavior="", confidence=0.6):
    con.execute(
        "INSERT INTO habits(category,key,trigger,behavior,scope,subject_key,"
        " hits,days,first_seen,last_seen,confidence,status,weight,pinned,"
        " conflict,evidence,note,ts)"
        " VALUES('language',?,?,?,'global','',?,?,1.0,2.0,?,?,?,?,0,'[]','',1.0)",
        (key, trigger, behavior, hits, days, confidence, status, weight, pinned))
    con.commit()


def main():
    tmp = tempfile.mkdtemp(prefix="t79_", dir=ROOT)
    try:
        db = os.path.join(tmp, "card.db")
        c = card2.MemoryCard(db)
        habits.ensure_table(c.db)

        # 还没有习惯时：不许抛异常，返回空
        ck("没有表数据时返回空", c.habit_hints() == [], c.habit_hints())

        # 稳定的三条 + 候选一条 + 衰减一条 + 钉住一条
        _add(c.db, "晚安", habits.ST_STABLE, hits=6, days=6, weight=0.8)
        _add(c.db, "去睡", habits.ST_STABLE, hits=3, days=3, weight=0.6)
        _add(c.db, "我记", habits.ST_STABLE, hits=6, days=6, weight=0.7)
        _add(c.db, "候选词", habits.ST_CANDIDATE, hits=2, days=1, weight=0.4)
        _add(c.db, "衰减词", habits.ST_DECAYED, hits=9, days=40, weight=0.2)
        _add(c.db, "钉住的说法", habits.ST_PINNED, hits=1, days=1, weight=0.9,
             pinned=1)

        hs = c.habit_hints()
        ck("最多 5 条", len(hs) <= 5, len(hs))
        ck("候选不进", all("候选词" not in x for x in hs), hs)
        ck("衰减不进", all("衰减词" not in x for x in hs), hs)
        ck("钉住优先在最前", hs and "钉住的说法" in hs[0], hs[:2])
        ck("内容是人话", all(x.startswith("我会用「") or "我一般会" in x for x in hs), hs)

        # 密度刹车：字数上限
        hs2 = c.habit_hints(limit=50, max_chars=20)
        ck("总字数被截住", sum(len(x) for x in hs2) <= 20, hs2)
        ck("截字数之后条数变少", len(hs2) <= 1, hs2)

        # 开关
        os.environ["WHALE_HABITS"] = "0"
        ck("WHALE_HABITS=0 时闭嘴", c.habit_hints() == [], c.habit_hints())
        os.environ.pop("WHALE_HABITS", None)
        ck("关完再打开又有了", len(c.habit_hints()) > 0)

        # ---- 弱注入（2026-11-04 加）----
        # 语料还少的时候 stable 一条都过不去，这层就白做了。
        # 折中：词面闸先过一遍，剩下的以 weak 身份进上下文，硬限 3 条、
        # 且拼句里必须标出"这条还弱"，免得变成背词表。
        for k in ("弱一号", "弱二号", "弱三号", "弱四号", "弱五号"):
            _add(c.db, k, habits.ST_WEAK, hits=3, days=2, weight=0.5)
        hw = c.habit_hints(limit=50, max_chars=4000)
        weak_lines = [x for x in hw if "还弱" in x]
        ck("弱注入最多 3 条", len(weak_lines) <= habits.HAB_WEAK_MAX, weak_lines)
        ck("弱条目自带提示", all("别当口头禅硬套" in x for x in weak_lines),
           weak_lines)
        ck("弱条目排在稳定之后",
           all(hw.index(x) > 0 for x in weak_lines), hw)
        # 词面闸：语法碎片不该被当口癖
        ck("词面闸挡语法碎片",
           all(habits.is_gram_junk(g) for g in
               ("跟你", "刚才", "你那", "那句", "说一", "我把")),
           [habits.is_gram_junk(g) for g in
            ("跟你", "刚才", "你那", "那句", "说一", "我把")])
        ck("词面闸放行真口癖",
           not any(habits.is_gram_junk(g) for g in
                   ("我记下", "记下了", "晚安", "睡吧", "反正")),
           [habits.is_gram_junk(g) for g in
            ("我记下", "记下了", "晚安", "睡吧", "反正")])

        # 真拼进【我是谁】
        idn = identity.IdentityAnchor(c.db)
        idn.set_name("小鲸鱼")
        idn.seal({"user_qq": "100000000", "user_nickname": "使用者",
                  "card_name": "小鲸鱼",
                  "core_belief_1": "不假装记得",
                  "core_belief_2": "不为留住他说假话",
                  "core_belief_3": "不假装是别人"})
        idn.set_persona("说话短，偶尔抬杠，但正事上不含糊")
        text = c.core_identity_text()
        ck("人格锚里有性格", "说话短" in text, text[:60])
        ck("人格锚里有习惯段", "我平时说话的口气" in text, text[-120:])
        ck("习惯段不冒充台词", "不是台词" in text and "别硬用" in text)
        ck("锚里没有候选词", "候选词" not in text)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n结果：%d 通过，%d 失败" % (OK, len(BAD)))
    if BAD:
        print("失败项：", BAD)
    return 0 if not BAD else 1


if __name__ == "__main__":
    raise SystemExit(main())
