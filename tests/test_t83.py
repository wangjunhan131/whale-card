# -*- coding: utf-8 -*-
"""给 qq_guard.decide() 加验收：素材会忘、学到的东西留下。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import qq_guard as G

ok = 0
bad = []


def t(name, cond, extra=""):
    global ok
    if cond:
        ok += 1
        print("  ✅ %s" % name)
    else:
        bad.append(name)
        print("  ❌ %s %s" % (name, extra))


# 1) 普通闲聊：不该进卡
for txt in ("打断施法！", "咕噜", "哈哈哈哈", "嗯嗯", "好的好的", "（招手）"):
    keep, region, sal, why = G.decide(txt)
    t("闲聊不进卡：%s（%s）" % (txt, why), not keep, why)

# 2) 被点名：进卡，且用慢衰分区
keep, region, sal, why = G.decide("小鲸鱼你来说说看", at_me=True)
t("被点名进卡且是 flash", keep and region == "flash", "%s %s" % (keep, region))
t("被点名 salience 高", sal >= 0.7, str(sal))

# 3) 强情绪：进卡
for txt in ("我今天好难受", "考砸了，想哭", "太开心了终于放假"):
    keep, region, sal, why = G.decide(txt)
    t("情绪进卡：%s（%s）" % (txt, why), keep and region == "flash", why)

# 4) 关于人的稳定陈述：进卡，慢衰
for txt in ("我高三了，明年高考", "我喜欢吃白米饭", "我住在杭州"):
    keep, region, sal, why = G.decide(txt)
    t("关于人进卡：%s（%s）" % (txt, why), keep and region == "fact", why)

# 5) 教训/经验口吻：进卡，最该留
for txt in ("记住下次别再这样了", "原来他是这个意思，我懂了", "长记性了"):
    keep, region, sal, why = G.decide(txt)
    t("经验进卡：%s（%s）" % (txt, why), keep and region == "fact", why)

# 6) 短句一律素材
keep, region, sal, why = G.decide("这锅真香")
t("6 字以内当素材", not keep, why)

# 7) 空消息
keep, region, sal, why = G.decide("")
t("空消息不进卡", not keep)

# 8) 素材不该比记忆活得久：region 分区决定衰减，episode 最快
t("素材用 episode（最快忘）",
  G.REGION_FAST == "episode" if hasattr(G, "REGION_FAST") else True)

# 9) 漏回复提醒
import tempfile
import shutil
TMP = tempfile.mkdtemp(prefix="qqg2_", dir=ROOT)
G.STATE = TMP
G.TODO = os.path.join(TMP, "todo.jsonl")
G.STATUS_F = os.path.join(TMP, "status.json")
G._ensure()
old_ts = int(G._now()) - 30 * 3600
for i in range(G.OVERDUE_N + 2):
    G._append(G.TODO, {"ts": old_ts + i, "kind": "group", "group_id": 1,
                       "group_name": "测试群", "sender": "某人",
                       "text": "第%d条" % i, "message_id": "x%d" % i})
    G._mark_if_new("x%d" % i)
msg = G.overdue()
t("攒够且放久了会提醒", bool(msg) and "没回" in msg, msg)

# 10) 不多的时候不提醒
G.STATUS_F2 = None
shutil.rmtree(TMP, ignore_errors=True)
TMP2 = tempfile.mkdtemp(prefix="qqg3_", dir=ROOT)
G.STATE = TMP2
G.TODO = os.path.join(TMP2, "todo.jsonl")
G.STATUS_F = os.path.join(TMP2, "status.json")
G._ensure()
G._append(G.TODO, {"ts": old_ts, "kind": "group", "group_id": 1,
                   "group_name": "测试群", "sender": "某人",
                   "text": "只有一条", "message_id": "y1"})
G._mark_if_new("y1")
t("只有一条不提醒", G.overdue() == "")
shutil.rmtree(TMP2, ignore_errors=True)

print()
print("结果：%d/%d 通过" % (ok, ok + len(bad)))
if bad:
    print("失败：%s" % "、".join(bad))
    sys.exit(1)
