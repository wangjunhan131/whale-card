# -*- coding: utf-8 -*-
"""第二期验收：三级池 / 分段双速率 / 巩固 / 侵蚀 / 上下文组装"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from card2 import (MemoryCard, Clock, DAY, POOL_SHORT, POOL_MEDIUM, POOL_LONG,
                   LAM_S1, LAM_M, LAM_L, weight_now, TOKEN_BUDGET)

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + ("  " + detail if detail else ""))


def fresh():
    fd, p = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(p)
    return p


print("=" * 62)
print("【1】空卡：存一条，立刻能想起")
p = fresh()
card = MemoryCard(p, Clock(start=1_700_000_000))
mid = card.remember("今天在学校门口的路边摊吃烧烤，很开心")
hits = card.recall("烧烤", top_k=1)
ok("存完能翻出来", len(hits) == 1)
ok("渲染带地点和情绪", "路边摊" in card.render(hits[0][2]),
   "-> " + card.render(hits[0][2]))

print("\n【2】短期池分段双速率：前3天慢、之后快")
w0 = 1.0
t0 = 0.0
w_1d = weight_now(w0, POOL_SHORT, t0, 1 * DAY)
w_3d = weight_now(w0, POOL_SHORT, t0, 3 * DAY)
w_5d = weight_now(w0, POOL_SHORT, t0, 5 * DAY)
w_7d = weight_now(w0, POOL_SHORT, t0, 7 * DAY)
ok("1天还很结实 (>0.85)", w_1d > 0.85, "w=%.3f" % w_1d)
ok("3天还行 (>0.60)", w_3d > 0.60, "w=%.3f" % w_3d)
ok("5天掉得快 (<0.15)", w_5d < 0.15, "w=%.3f" % w_5d)
ok("7天几乎没了 (<0.02)", w_7d < 0.02, "w=%.4f" % w_7d)
print("    曲线: 1d=%.3f 3d=%.3f 5d=%.3f 7d=%.4f" % (w_1d, w_3d, w_5d, w_7d))

print("\n【3】中期池比短期慢段还慢，长期最慢")
wm_30d = weight_now(1.0, POOL_MEDIUM, 0.0, 30 * DAY)
wl_365d = weight_now(1.0, POOL_LONG, 0.0, 365 * DAY)
ok("中期30天仍存活 (>0.30)", wm_30d > 0.30, "w=%.3f" % wm_30d)
ok("长期365天仍存活 (>0.40)", wl_365d > 0.40, "w=%.3f" % wl_365d)
ok("中期比短期慢段慢", LAM_M < LAM_S1, "λm=%.2e < λs1=%.2e" % (LAM_M, LAM_S1))

print("\n【4】巩固：高情绪+多次回想的记忆被搬进长期池")
p = fresh()
card = MemoryCard(p, Clock(start=1_700_000_000))
mid = card.remember("2026年10月24日晚上在学校门口的路边摊给同学过生日吃烧烤，我特别开心",
                    salience=0.9, oxy=1.0)
# 模拟被反复想起
for _ in range(10):
    card.recall("烧烤 生日", top_k=1)
r = card.db.execute("SELECT * FROM mem WHERE id=?", (mid,)).fetchone()
sc = card._hippocampus_score(r)
ok("海马分够进长期 (>=0.60)", sc >= 0.60, "score=%.3f" % sc)
card.tick(7 * DAY + 3600)   # 熬过 7 天
r2 = card.db.execute("SELECT pool,w FROM mem WHERE id=?", (mid,)).fetchone()
ok("7天后被巩固进长期池", r2 is not None and r2["pool"] == POOL_LONG,
   "pool=%s w=%.3f" % (r2["pool"] if r2 else "GONE", r2["w"] if r2 else 0))

print("\n【5】淘汰：平平无奇又没被想起的记忆，7天后消失")
p = fresh()
card = MemoryCard(p, Clock(start=1_700_000_000))
mid = card.remember("嗯", salience=0.2, emotion=0.1, entities=0, oxy=0.0)
card.tick(7 * DAY + 3600)
gone = card.db.execute("SELECT COUNT(*) FROM mem WHERE id=?", (mid,)).fetchone()[0] == 0
ok("平庸记忆被淘汰", gone)

print("\n【6】跨池流转不暴毙：进中期池后被抬到存活线")
p = fresh()
card = MemoryCard(p, Clock(start=1_700_000_000))
mid = card.remember("在教室跟同桌聊了很久关于未来的事，挺开心的",
                    salience=0.8, emotion=0.9, entities=3, oxy=0.2)
for _ in range(5):
    card.recall("同桌 未来", top_k=1)
card.tick(7 * DAY + 60)
r = card.db.execute("SELECT pool,w,t_anchor FROM mem WHERE id=?", (mid,)).fetchone()
ok("还在（没暴毙）", r is not None)
if r:
    wn = weight_now(r["w"], r["pool"], r["t_anchor"], card.clock.now())
    ok("巩固后权重不低于下限", wn >= 0.05,
       "pool=%s w_now=%.3f" % (r["pool"], wn))

print("\n【7】上下文组装：四格齐全 + 不超预算")
p = fresh()
card = MemoryCard(p, Clock(start=1_700_000_000))
card.remember("用户是高三学生，主号QQ 100000000，昵称使用者", pool=POOL_LONG,
              salience=1.0, emotion=0.8)
card.remember("用户喜欢白米饭", pool=POOL_LONG, salience=0.8)
card.remember("用户说他要给小鲸鱼做一张数字生命卡", pool=POOL_LONG,
              salience=0.9, emotion=0.9)
card.remember("今晚聊了硬盘盒和宇树机器人", salience=0.6)
ctx = card.assemble_context(
    "你还记得我的QQ号吗",
    identity="我是小鲸鱼，傲娇、爱摸鱼、喜欢白米饭。",
    recent_lines=["用户：我们接着聊", "我：好啊"],
)
print("    --- 组装结果 ---")
for ln in ctx["text"].split("\n"):
    print("    " + ln)
print("    --- 统计 ---")
print("    est_tokens=%d / budget=%d  dropped=%d" %
      (ctx["est_tokens"], ctx["budget"], ctx["dropped"]))
print("    sections=" + str(ctx["sections"]))
ok("四格都有", len(ctx["sections"]) == 4, str(list(ctx["sections"].keys())))
ok("不超总预算", ctx["est_tokens"] <= TOKEN_BUDGET,
   "%d <= %d" % (ctx["est_tokens"], TOKEN_BUDGET))
ok("长期格提到QQ号", "100000000" in ctx["text"])

print("\n【8】预算紧张时自动截断，不报错")
ctx2 = card.assemble_context("QQ号", identity="x" * 5000,
                             recent_lines=["y" * 500] * 50)
ok("超大输入不崩", ctx2["est_tokens"] <= TOKEN_BUDGET,
   "%d <= %d" % (ctx2["est_tokens"], TOKEN_BUDGET))
ok("确实截断了", ctx2["dropped"] > 0 or len(ctx2["text"]) < TOKEN_BUDGET)

print("\n【9】老库兼容：card.py 的 317 条能直接读")
legacy = os.path.join(os.path.dirname(os.path.abspath(__file__)), "card.db")
if os.path.exists(legacy):
    c2 = MemoryCard(legacy, Clock())
    st = c2.stats()
    print("    stats=" + str(st))
    ok("老数据全部进来", st["total"] >= 317, "total=%d" % st["total"])
    ok("老数据进了长期池", st["pools"][POOL_LONG]["n"] >= 300,
       "long=%d" % st["pools"][POOL_LONG]["n"])
    hits = c2.recall("我永远值得你付出", top_k=1, access=False)
    ok("能翻出原话", len(hits) > 0 and "值得" in hits[0][2]["raw"],
       hits[0][2]["raw"][:30] if hits else "无命中")
    c2.db.close()
else:
    print("    (跳过，找不到 card.db)")

print("\n" + "=" * 62)
print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：" + ", ".join(FAIL))
    sys.exit(1)
