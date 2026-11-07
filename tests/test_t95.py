# -*- coding: utf-8 -*-
"""t95 —— L0 时间线接进上下文（2026-11-06，用户 m21769 / m21777）

验收四件事：
1. 时间线只取「最近 72 小时」且剔掉 alive 心跳；
2. 顺读（老 → 新），日期写绝对值（几月几日），不写「昨天」；
3. 条数与字数都受控，超了不硬塞；
4. assemble_context 里真的出现【最近发生】这一格，且和「最近在聊」共用 recent 配额。

跑法：python tests/test_t95.py      （需要 PYTHONIOENCODING=utf-8）
"""
import os
import shutil
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import card2  # noqa: E402
import timeline  # noqa: E402

DAY = 86400.0
OK = 0
FAIL = 0


def chk(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print("  ✅ %s" % name)
    else:
        FAIL += 1
        print("  ❌ %s  %s" % (name, extra))


tmp = tempfile.mkdtemp(prefix="t95_")
dbp = os.path.join(tmp, "card.db")
clock = card2.Clock(start=1_800_000_000.0)
card = card2.MemoryCard(dbp, clock=clock)
tl = timeline.Timeline(card.db)

now = clock.now()
# 五条正常事件（由老到新）+ 一条 alive 心跳 + 一条超过 72 小时的旧事件
tl.append("mem", "他#1 那天在路边摊吃烧烤", ts=now - 5 * 3600)
tl.append("boot", "", ts=now - 4 * 3600)
tl.append("talk", "他说了句我回来了", ts=now - 3 * 3600)
tl.append("mem_fix", "改了那条时间锚点", ts=now - 2 * 3600)
tl.append("incident", "卡库被锁过一次，已恢复", ts=now - 1 * 3600)
tl.append("alive", "", ts=now - 30 * 60)             # 心跳：必须被剔
tl.append("mem", "五天前的老事件不该出现", ts=now - 5 * DAY)

lines = card._l0_lines(now=now)

print("① 取哪些")
chk("拿到了东西", bool(lines), lines)
joined = "\n".join(lines)
chk("剔掉 alive 心跳", "alive" not in joined.lower())
chk("72 小时以前的不进", "五天前的老事件" not in joined)
chk("72 小时内的都在", "路边摊吃烧烤" in joined and "我回来了" in joined)
chk("条数不超过 limit", len(lines) <= 8, len(lines))

print("② 顺着读、写绝对日期")
chk("是老 → 新（烧烤在最前）", lines[0].find("烧烤") >= 0, lines[0])
chk("不是倒着念（恢复在最后）", "恢复" in lines[-1], lines[-1])
chk("日期写成几月几日", "月" in lines[0] and "日" in lines[0], lines[0])
chk("不写「昨天/今天」", "昨天" not in joined and "今天" not in joined)

print("③ 配额受控")
short = card._l0_lines(now=now, max_chars=40)
chk("字数上限挡得住", sum(len(x) for x in short) <= 40, short)
chk("限 2 条就只给 2 条", len(card._l0_lines(now=now, limit=2)) <= 2)

print("④ 拼上下文时真的接上了")
ctx = card.assemble_context("吃烧烤", identity="我是小鲸鱼", recent_lines=["他：吃烧烤吗", "我：吃"])
chk("出现【最近发生】", "【最近发生】" in ctx["text"], ctx["text"][:200])
chk("sections 里有 l0", "l0" in ctx["sections"], ctx["sections"])
chk("l0 长度受 720 限制", ctx["sections"].get("l0", 0) <= 760, ctx["sections"].get("l0"))
chk("人格格没被挤掉", "【我是谁】" in ctx["text"])
chk("最近在聊也在", "【我们最近在聊】" in ctx["text"])

print("")
print("结果：%d 过 / %d 挂" % (OK, FAIL))
shutil.rmtree(tmp, ignore_errors=True)
if FAIL:
    sys.exit(1)
print("全部通过 ✅")
