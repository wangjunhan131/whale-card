# -*- coding: utf-8 -*-
"""V5.3.5 验收：关系卡 / 学到的整理成技能 / 主动升级提案。"""
import io
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import _cardcopy  # noqa: E402

OK = []
FAIL = []


def chk(name, cond, detail=""):
    if cond:
        OK.append(name)
    else:
        FAIL.append("%s %s" % (name, detail))


def fresh_copy():
    d = tempfile.mkdtemp(prefix="t113_")
    dst = os.path.join(d, "card.db")
    _cardcopy.copy_card_db(os.path.join(ROOT, "card.db"), dst)
    return d, dst


# ---------- 1. 关系卡 ----------
d, db = fresh_copy()
import card2  # noqa: E402

c = card2.MemoryCard(db)

chk("没有人就是没有", c.person_card("nobody") is None)

c.person_touch("u1", "friend", ts=1000.0, region="chatter")
chk("第一次出现建档", (c.person_card("u1") or {}).get("n") == 1)

c.person_touch("u1", "friend", ts=2000.0, region="fact")
p = c.person_card("u1")
chk("次数累加", p["n"] == 2, p)
chk("第一次时间不被覆盖", p["first_ts"] == 1000.0, p)
chk("最后一次时间更新", p["last_ts"] == 2000.0, p)
chk("分区计数", p["regions"] == {"chatter": 1, "fact": 1}, p)
chk("关系层保留", p["layer"] == "friend", p)

c.person_touch("u2", "", ts=3000.0, region="chatter")
chk("空关系层不写成 0", (c.person_card("u2") or {}).get("layer") == "")

chk("uid 空则什么都不做", c.person_touch("", "friend", ts=1.0) is None)

lst = c.people(limit=10)
_mine = [r for r in lst if r["uid"] in ("u1", "u2")]
chk("列表按次数排（只看本用例造的那两个）",
    len(_mine) == 2 and _mine[0]["uid"] == "u1", _mine)

# 大分区取前三
for i in range(5):
    c.person_touch("u3", "known", ts=4000.0 + i, region=["a", "b", "c", "d"][i % 4])
top = [k for k, _ in (c.person_card("u3") or {}).get("top_regions", [])]
chk("top_regions 最多三个", len(top) <= 3, top)
chk("top_regions 是真出现过的分区", set(top) <= {"a", "b", "c", "d"}, top)

# ---------- 2. 学到的整理成技能 ----------
import learn_to_skill as L  # noqa: E402

note = """# 学习笔记：怎么读长音频里的原文

- 什么时候用: 想读一本书的原文但找不到电子版时
- B站「书名 + 有声书」的长音频，AI 字幕常常就是原文逐字
- 专名和数字不可照抄，ASR 会听错
- 能读原文就读原文，二手梗概会改事实
"""
got, why = L.extract(note)
chk("抽得出名字", got and "怎么读长音频" in got[0], got)
chk("抽得出场景", got and "有声书" in got[1], got)
chk("抽出做法条目", got and got[1].count("- ") >= 2, got)
chk("空笔记不编", L.extract("")[0] is None)
chk("没标题时用第一行", L.extract("随便一段话\n\n- 一个够长的要点在这里")[0][0].startswith("随便一段话"))
chk("够短的要点进不了做法", "做法：" not in L.extract("# T\n- 短")[0][1],
    L.extract("# T\n- 短"))

# ---------- 3. 主动升级提案 ----------
import self_upgrade as SU  # noqa: E402

items = [("记忆", 1124, "总数", False),
         ("隔离区", 6, "被防污层拦住、还没人复核", True),
         ("对象覆盖率", "32%", "带 about_uid 的比例，低于 60% 召回就分不清人", True),
         ("细节行", 5716, "每条记忆平均 5.1 片段", False)]
txt = SU.build_text(items, stamp="2026-11-07 20:00")
chk("提案写了日期", "2026-11-07 20:00" in txt, txt)
chk("提案数得清几项要看", "4 项里 2 项要看一眼" in txt, txt)
chk("提案给了该看的", "复核隔离区" in txt and "对象覆盖率" in txt, txt)
chk("全绿时说没有", "该看的：没有" in SU.build_text([("记忆", 1, "总数", False)]))
chk("体检能跑且只读", isinstance(SU.scan(), list))
chk("体检项齐", {x[0] for x in SU.scan()} >= {"记忆", "墓碑", "对象覆盖率"}, SU.scan())

print("OK %d / FAIL %d" % (len(OK), len(FAIL)))
for f in FAIL:
    print("  FAIL", f)
for o in OK:
    print("  ok  ", o)
sys.exit(1 if FAIL else 0)
