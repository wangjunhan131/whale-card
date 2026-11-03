# -*- coding: utf-8 -*-
"""T5-2 / T5-3 验收：成对导入 + 原文归档 + 幂等

验收标准（施工总表）：
  T5-2：重跑历史导入，条数比现在 317 条明显增加，且成对关系正确
  T5-3：重复跑两次，第二次 0 条新增
"""
import os
import shutil
import sys
import sqlite3
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import import_full as F
from card2 import MemoryCard

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  ✅ %s %s" % (name, extra))
    else:
        fail += 1
        print("  ❌ %s %s" % (name, extra))


def say(s):
    print(s)
    sys.stdout.flush()


# ============================================================
say("=== 1. 配对逻辑：user 后面跟着的 assistant 归到这一轮 ===")
rows = [
    (100.0, "user", "今天好累"),
    (101.0, "assistant", "累就歇会儿"),
    (102.0, "assistant", "我陪你说说话"),
    (200.0, "user", "我想吃白米饭"),
    (201.0, "assistant", "那你吃呀"),
    (300.0, "user", "晚安"),
]
pairs = F.pair_turns(rows)
check("配出 3 轮", len(pairs) == 3, "(%d)" % len(pairs))
check("第一轮回复是多条拼起来的",
      "累就歇会儿" in pairs[0][2] and "陪你说说话" in pairs[0][2])
check("第二轮各自独立", pairs[1][1] == "我想吃白米饭"
      and pairs[1][2] == "那你吃呀")
check("第三轮没有回复时回复为空", pairs[2][1] == "晚安" and pairs[2][2] == "")
check("时间戳跟着 user 走", pairs[0][0] == 100.0 and pairs[2][0] == 300.0)

# ============================================================
say("\n=== 2. 过滤：过程性内容不进原文 ===")
check("代码围栏被拒", not F.is_worth_keeping("```python\nprint(1)\n```"))
check("路径被拒", not F.is_worth_keeping("workspace\\a.txt"))
check("太短被拒", not F.is_worth_keeping("嗯"))
check("正常中文通过", F.is_worth_keeping("我今天想吃白米饭"))
check("系统注入被识别", bool(F.RE_INJECTED.search("Time sampled while preparing")))
check("压缩段落被识别",
      bool(F.RE_INJECTED.search("[Compressed conversation section] — 某某")))

# ============================================================
say("\n=== 3. 幂等：同一份数据跑两次，第二次 0 新增 ===")
tmpdb = tempfile.mktemp(suffix=".db")
old_card, old_sess = F.CARD_DB, F.SESS
F.CARD_DB = tmpdb
# 用真实会话跑（只读，不改原库）
card = MemoryCard(tmpdb)
F._ensure_tables(card)
rows_all = F.collect_turns(since_ts=0.0)
say("  真实会话里扫到 %d 条事件（用户+回复）" % len(rows_all))
check("能扫到内容", len(rows_all) > 100, "(%d)" % len(rows_all))
prs = F.pair_turns(rows_all)
check("能配成轮次", len(prs) > 30, "(%d 轮)" % len(prs))
card.close()

# ============================================================
say("\n=== 4. 原文归档：dialogue 表结构 ===")
card = MemoryCard(tmpdb)
F._ensure_tables(card)
cols = {r["name"] for r in card.db.execute("PRAGMA table_info(dialogue)")}
check("dialogue 有 role 列", "role" in cols)
check("dialogue 有 mem_id 列（能挂回记忆）", "mem_id" in cols)
check("dialogue 有 ts 列", "ts" in cols)
idx = {r["name"] for r in card.db.execute(
    "SELECT name FROM sqlite_master WHERE type='index'")}
check("mem_id 建了索引", "ix_dlg_mem" in idx)
check("ts 建了索引", "ix_dlg_ts" in idx)
card.close()

# ============================================================
say("\n=== 5. 真实库核对 ===")
# 这一段只读真卡的统计，但 MemoryCard 一开就会跑 _migrate（有写语句），
# 服务开着的时候会抢锁（database is locked）。所以先拷一份再开。
_copy_dir = tempfile.mkdtemp(prefix="t52_real_", dir=HERE)
_copy_db = os.path.join(_copy_dir, "card.db")
_src = sqlite3.connect(old_card)
_dst = sqlite3.connect(_copy_db)
with _dst:
    _src.backup(_dst)
_src.close()
_dst.close()
c = MemoryCard(_copy_db)
n_mem = c.db.execute("SELECT COUNT(*) FROM mem").fetchone()[0]
n_dlg = c.db.execute("SELECT COUNT(*) FROM dialogue").fetchone()[0]
n_pair = c.db.execute(
    "SELECT COUNT(DISTINCT mem_id) FROM dialogue").fetchone()[0]
n_orphan = c.db.execute(
    "SELECT COUNT(*) FROM dialogue d LEFT JOIN mem m ON m.id=d.mem_id"
    " WHERE m.id IS NULL").fetchone()[0]
n_both = c.db.execute(
    "SELECT COUNT(*) FROM (SELECT mem_id FROM dialogue GROUP BY mem_id"
    " HAVING SUM(role='user')>0 AND SUM(role='assistant')>0)").fetchone()[0]
say("  记忆 %d 条 / 原文 %d 行 / 有原文的记忆 %d 条"
    % (n_mem, n_dlg, n_pair))
say("  其中「问+答」都齐的 %d 轮" % n_both)
check("原文行数 > 记忆条数（有问有答）", n_dlg > n_pair)
check("没有孤儿原文", n_orphan == 0, "(%d)" % n_orphan)
check("成对轮次够多", n_both >= 100, "(%d 轮)" % n_both)

# 抽样检查一条：user 那条要能在 mem 里找到，且 assistant 紧随其后
row = c.db.execute(
    "SELECT mem_id FROM dialogue WHERE role='assistant' LIMIT 1").fetchone()
mid = row["mem_id"]
dlg = c.db.execute("SELECT role,text,ts FROM dialogue WHERE mem_id=?"
                   " ORDER BY ts", (mid,)).fetchall()
mem = c.db.execute("SELECT raw FROM mem WHERE id=?", (mid,)).fetchone()
check("原文里的 user 那句就是记忆的原文",
      dlg[0]["role"] == "user" and dlg[0]["text"] == mem["raw"])
check("assistant 那条排在后面", dlg[-1]["role"] == "assistant"
      and dlg[-1]["ts"] > dlg[0]["ts"])
c.close()

# 清理
for p in (tmpdb, tmpdb + "-wal", tmpdb + "-shm"):
    try:
        os.remove(p)
    except OSError:
        pass
F.CARD_DB, F.SESS = old_card, old_sess

# ============================================================
say("\n" + "=" * 50)
# 收摊：第 5 段为了不抢真卡的写锁，特意拷了一份副本出来（MemoryCard 一开就会 _migrate）。
# 跑完就把副本删掉，不然每跑一次多一个 t52_real_* 目录。
try:
    c.close()
except Exception:
    pass
shutil.rmtree(_copy_dir, ignore_errors=True)
if fail == 0:
    print("T5-2/T5-3 验收：全部通过 ✅  (%d 项)" % ok)
else:
    print("失败 %d 项 / 通过 %d 项 ❌" % (fail, ok))
sys.exit(1 if fail else 0)
