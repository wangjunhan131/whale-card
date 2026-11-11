# -*- coding: utf-8 -*-
"""
小鲸鱼数字生命卡 · 第二期
=====================================

第一期（card.py）做了：存 / 忘 / 翻 / 说。
第二期在它上面补三件事：

  1. 三级记忆池（短期 → 中期 → 长期）—— 记忆不再只有"淡掉"一条路，
     重要的会被"巩固"搬进更慢的池子里，这才是人脑的做法。
  2. 分段双速率衰减 —— 短期池前 3 天慢慢淡、4~7 天快速淡（模拟"没被想起就忘"）。
  3. 上下文组装 assemble_context() —— 每次说话前，按 token 预算拼出四格：
     人格 / 长期印象 / 最近聊的 / 此刻想起。装满就停，永远不会爆。

设计铁律：**任何模块不许读系统时间**。时间只从 Clock 来，这样"隔一天"能测。
依赖：只用标准库。

与 card.py 的关系：共用同一个数据库文件，自动迁移（老数据全部进长期池）。
"""

import json

import contextlib
import hashlib
import math
import os
import random
import re
import sqlite3
import sys
import threading
import time

# 🔴 2026-10-31 08:20：**BLAS 线程数必须在任何本地模块 import 之前设好。**
#
# 早上发现昨晚"修好"的性能问题其实没修上：`embed_np.py` 顶部那五行确实写了，
# 但 `card2.py` 先 import 了 `semantic.py`，而它是第一个 `import numpy` 的
# 模块 —— numpy 一进来就把 BLAS 线程池按默认值（CPU 核数=20）建好了，
# 等 embed_np 再执行那五行时已经晚了。实测 OPENBLAS_NUM_THREADS 依然是 None，
# 单次 recall 卡在 2 秒（多线程），而单线程只要 0.3 秒。
#
# 教训：**"改了但没生效"和"没改"是一回事**，而后者至少还能被发现。
# 以后凡是靠环境变量生效的东西，都要在真正的入口处设，并且**验证一次**
# （print 出来 / 断言），不能靠"我把代码写进去了"就当成功。
for _v in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS',
           'NUMEXPR_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
    # 用直接赋值而不是 setdefault：外部残留的旧值同样会拖慢，宁可覆盖
    os.environ[_v] = '1'

import hormone
import viscera
import motiv
import padlib
import ctxweight
import skill
import identity
import habits as habits_mod
import impression as impression_mod
import facts as facts_mod
import synapse as synapse_mod
import tempora  # 时间词 → 绝对时间窗 + 待办账本（2026-11-11 从交付包学来的思路）
# 🔧 T4-3：语义层。它是唯一需要 numpy 的模块，所以关在 semantic.py 里，
#    这里只在用得到的时候碰它。拿不到（模型没下/numpy 没装）就退回字面匹配 ——
#    功能降级，但不崩、不静默变错。
try:
    import semantic as semantic_mod
except Exception:                    # numpy 都没装
    semantic_mod = None

try:
    import numpy as np
except Exception:                    # 没有 numpy 时 _detail_sims 会走纯 Python 路
    np = None

# 🔧 T4-3d：片段池化的 K 值。
#    取「最像的前 K 段」求均值，而不是只看最像的那一段。
#    K=3 是在改写鲁棒性评测（eval_paraphrase.py，34 条问法）上调出来的：
#    单条 max 会让碎片多的长记忆系统性占便宜（id332 有 27 个碎片，
#    几乎什么查询都能从它身上挑出一段高分），取 top-3 均值后这种
#    「大杂烩记忆」就不灵了。K 再大反而会稀释真正的强匹配。
# 数据 schema 版本（V7 第 18 件）。跟 versions.SCHEMA_VERSION 是同一个数，
# 这里存一份是为了「card2 单独被 import 时也能说清自己期待哪一版库」。
# 1 = 2026-11-10：event_ts/event_precision、impression.react_key、
#     mem_archive、worklog、recall_trace、mem_birth 补齐
SCHEMA_VERSION = 1
# 🔧 V7 第 19 件：向量「写入时用的哪个模型」要能被查出来。
#    以前卡里只按维度分辨（512/768），同维度的两个模型混在一起是无解的：
#    向量能算、结果却是垃圾，而面板上一切正常 —— 这类「看着在工作」的坑
#    这个项目已经踩过一次（768 的卡被当 512 用），不再踩第二次。

DETAIL_POOL_K = 3

# 一天的秒数。cardtime.py 里也有一份（那边是给时间函数用的），
# 这里这份是 card2 自己的池子结算在用，两处同值，谁也别去 import 谁。
DAY = 86400.0


# ============================================================
# 吞异常登记表（V7 第 6 件）
# ============================================================
# 为什么要有它：这个项目已经真吃过两次亏 ——
#   · add_dialogue 的 NOT NULL 约束被 `except: pass` 吞了两周，原文表再没长过一条；
#   · 维护线程里持锁顺序反了，整卡假死 70 秒，日志里只有一行「超时」。
# 吞异常本身不都是错的（旁路功能失败不该拖垮服务），错的是**关键写路径**悄无声息。
# 所以这里不追求「零吞」，而是让每一次「我出错了但我没说」都留下计数：
#   soft_fail(kind, e)  → 计数 +1，同一种错只往 stderr 打第一行，其余只记数。
#   soft_fail_snapshot() → 给 /health 用：哪个环节悄悄失败了几次、最后一次什么错。






# ============================================================
# 参数总表（改这里就能调手感）
# ============================================================
# --- 三级池的衰减率（每秒）---
LONG_DECAY_HALFLIFE_DAYS = 371.0   # 与 LAM_L 对应；长期池里「会褪的那一半」
                                   # 就是按这个周期褪，pin=1 的那一半不褪。

SHORT_WARM_TTL = 21 * DAY     # 短期小池：7 天的三倍
MEDIUM_WARM_TTL = 90 * DAY    # 中期小池：约三个月
WARM_SHORT_DAYS = 2           # 进短期小池：跨 2 个不同日子被想起
WARM_SHORT_RECALL = 2         #   且累计被想起 2 次
WARM_UP_DAYS = 3              # 从小池往上走：跨 3 天
WARM_UP_RECALL = 3            #   且累计 3 次
WARM_MEDIUM_DAYS = 3          # 进中期小池：跨 3 天、3 次
WARM_LONG_DAYS = 2            # 中期小池 → 长期：2 天、7 次（2026-11-09：7→2）
# 为什么从 7 天降到 2：诊断（probe/_diag2_pool.md 的 Q3）发现 medium_warm 里 6 条
# 已经过了 7 天/7 次，却因为海马分只有 .35~.41 永远够不到 .80 的门槛，卡在 warm 里
# 等 90 天 TTL 被删 —— 那不是没资格，是没人给它机会。门槛交给 THETA_LONG_STRICT
# 和海马分去判，天数只负责「别太快」。

# --- 短期池容量上限（V3.7 §3.2，V5.0 补回）---
# 时间到了会巩固，容量满了也要巩固 —— 两条路都必须走海马判定，不能直接 pop。
#
# 🔧 2026-11-06：32 → 120。
#    用户问过一次：「短期池的位置暂时不用设限制吧，或者让它更多更大一点，
#    写入特定区域可以，你觉得哪个更好？」—— 三个方案里选「更大一点 + 不硬删」：
#    ① 完全不设限 = 注意力工作集失去意义（它本来就该是有限的一张桌子）；
#    ② 纯靠写入分区 = 治标，写错一次照样出事；
#    ③ 桌子做大 + 腾位置时不销毁内容（见 _consolidate 里那条改动）= 两头都保住。
#    32 是「一次对话能同时想到几件事」的量级，但加上值守自动记群消息之后，
#    一天轻松几十上百条，32 几乎天天溢——溢出就删，等于把池子当坟场用。
# N_SHORT_MAX 曾是短期池条数上限，V5.4 起不生效（`_evict_short_overflow` 已停用），
# 全仓库无引用；短期池现在只有时间窗（cardcfg.SHORT_MAX_TTL）。2026-11-11 删掉以免误读。

# --- SessionCache 双回流（V3.7 §9 / V5.0 T1-4）---
# 每攒够这么多轮，就把本轮聊到过、但没被 recall 命中的旧记忆记一条弱事件。
SESSION_FLUSH_TURNS = 20
# 「顺嘴提到」用 IDF 加权覆盖率判，不用余弦 ——
# 余弦会被记忆条目自身长度稀释（实测同一件事 sim 只有 0.14），
# 而覆盖率直接问「这句话的实词，有多少落在这条记忆里」，
# 实测同一件事 0.443、无关句 0.086，区分度够。
SESSION_HIT_COVER = 0.30
SESSION_EVENT_RANK = 99     # 标记来源：不是主动召回，是「顺嘴提到」

# --- 跨池门槛（海马打分）---
THETA_LONG = 0.60     # ≥ 它 → 进长期池

# --- 海马打分权重（和为 1，全部归一到 [0,1]）---
A_EMOTION = 0.40   # 情绪强度
A_ENTITY = 0.20    # 实体数（人名地名物件）
A_RECALL = 0.25    # 被唤起次数
A_OXY = 0.15       # 催产素/亲密标记

# --- 唤起增益 ---
GAIN_ACCESS = 0.08
ACCESS_DIMINISH = 0.15
# 🔧 2026-11-05：递减不能再一路减到 0。
#    原来分母是 (1 + 0.15 * access_count)，终身累加、没有上界 ——
#    天天被想起一年（access_count=365）时增益只剩 0.0015，
#    比不上一夜的自然衰减，于是「最重要的东西」被用得越多反而越轻。
#    这不是设计，是没给递减设上下界。
#    上界取 20 次：满打满算折到 1/4，仍然能加固，只是不再指数变钝。
ACCESS_DIMINISH_CAP = 20

# 🔧 2026-11-05：去重「续命」那一下的增益。
#    原来写死 min(1.0, wn + 0.10)：同一句话（天天说晚安）每重复一次 +0.10
#    并把锚推到此刻 —— 跟召回正反馈是同一个病，只是发生在写路径上。
#    现在同样乘「剩余空间」，并有天花板 W_CEIL_RECALL。

# --- 主干侵蚀分级（V3.7 §3.6 / V5.0 T2-1）---
# 侵蚀不是一刀删整条，而是三级往下剥：
#   第一刀擦「做了什么」(verb) → 第二刀擦「对谁/对什么做的」(object) → 最后才整条没
# 顺序不能反：先丢细节、再丢动作，最后只剩个模糊的影子。
P_ERASE_MAX = 0.35     # 权重低到 0 时，每次 tick 被侵蚀的概率上限
ERASE_W_FLOOR = 0.02   # 低于这个权重才可能被侵蚀
ERASE_STAGE_VERB = 1   # 已擦掉动词
ERASE_STAGE_OBJ = 2    # 已擦掉宾语
# 擦完一层之后，给残骸续一口气，让「记不清了」的那个影子能多留一会儿。
# 否则权重已经贴着 GC 线，擦完下一步就被回收，用户永远看不到残缺渲染。
HUSK_W = 0.015

# --- 媒体记忆（画面）：2026-11-02，设计见 docs/MEDIA-MEMORY.md ---
# 文字是主干，画面是快照：**两条寿命，各自风化**，只用索引相连，寿命不互传。
# 形状用幂律而不是指数——艾宾浩斯 1885 那组数据（20 分 58.2% / 1 天 33.7% /
# 31 天 21.1%）和后来对数百个数据集的拟合（Wixted & Ebbesen 1997）都指向
# R = a(t+δ)^(-b)：先掉得猛，然后拖着一条长尾。指数衰减会"到点归零"，与数据不符。
MEDIA_DIR = "media"            # 卡目录下的媒体子目录（卡本体不含 blob）
MEDIA_GAIN_MULT = 1.45         # 看一眼的乘法增益（文字是 +0.08 加法，画面更猛）
MEDIA_GAIN_ADD = 0.10
MEDIA_ORPHAN_DAYS = 30.0       # 文字先没了、图还在时的"残影期"
MEDIA_CAP_MB = 10240           # 硬上限 10 GB（软提醒 5 GB 在控制台）
MEDIA_IMG_MAX_EDGE = 800       # 存的长边像素（不存原图，细节永久损失，这是明说的取舍）
MEDIA_THUMB_EDGE = 96          # 控制台网格用缩略图
MEDIA_MAX_FRAMES = 6           # 一段视频最多抽几帧



# 轻量动词词典（不做完整句法分析，够用就行）
# ---------- 单字动词的复合词守卫（2026-11-05）----------
# 病根：中文里单字动词大量作为**复合词的一部分**出现，只看"这个字是不是动词"
#       必然把词劈开。真卡实测（修复前 12 条里 5 条错）：
#           'LT-Mem的消融实验证明性能提升源于结构化**记忆**架构' → verb='记' obj='忆架构'
#           '跟read_air查出的「群里49%消息**来自**另一个AI」'      → verb='来' obj='自另一个AI」'
#           '2026-11-05傍晚**学习**中文用户必踩的坑'              → verb='学' obj='习中文用户…'
#           '小男企鹅的评论值得抄不要一上**来**就'                → verb='来' obj='就'
#           '换会话不是**打开**新窗口就完了'                      → verb='打' obj='开新窗口就完了'
#       这类错的代价不在 stage 0（subj+verb+obj 仍是原文，无损），
#       而在 stage 1「擦掉动词」—— 它会把「记忆」擦成「忆架构」，留下一段没有意义的残渣。
#       宁可整条不拆（返回 (整句,'','')，按整条侵蚀），也不要拆出一个假动词。
#
# 判定：单字动词只有在**不像组词**的时候才算动词，三条都要过：
#   a) 后一个字不在"常见组词尾"里（记+忆 / 来+自 / 学+习 / 打+开 …）
#   b) 前一个字不在"常见组词头"里（打+开 / 上+来 / 大+学 …）
#   c) 前面的主语不长（≤14 字）—— 单字动词挂在长名词短语后面，多半是撞进词里了
# 多字动词（喜欢/讨厌/考试/上班…）几乎不会歧义，直接放行。

# 介词边界：动词后面跟这些，说明宾语到头了
# 标点也截断宾语：「吃烧烤，很开心」的宾语是「烧烤」不是「烧烤，很开心」
# 引号里的才是内容：中文直角引号 / 双引号 / 英文双引号


# --- 渲染分级 ---
SHOW_FULL = 0.60
SHOW_SCENE = 0.30
SHOW_HINT = 0.15

# --- 回收 ---

# --- 五空间下沉（T5-1 / V5.0 §12.5）---
# 不是「热库会忘、冷库不忘」，而是每个空间都有自己独属的遗忘曲线，
# 总量在跑够久之后达到动态平衡（Little 定律 L = n × T）。
W_EXIT_LONG = 0.02           # 长期池：低于它且够老 → 下沉 ARCHIVE
AGE_EXIT_LONG = 365 * DAY    # 长期池至少待满一年才准下沉
W_EXIT_ARCHIVE = 0.002       # 潜意识：低于它 → 下沉 DEEP
W_EXIT_DEEP = 0.0002         # 深层痕迹：低于它 → 压成 TRACE 计数
SINK_BATCH = 200             # 每次 tick 最多下沉多少条（防止大库卡住）

# --- 去重 ---
                         # （V5.6.3：0.72 会把「同一件事的细节不同」揉成一条，改成 0.92）

# --- 记忆分区（V4.1）---
# 六类：事实 / 情景 / 自图式 / 习惯 / 偏好 / 闪光灯

# 分区衰减倍率（乘各自池的 λ）。数值越小忘得越慢。

# --- 闲聊区（V5.1，用户 2026-11-05）-----------------------------------
# 「闲聊这个东西还是要进卡的，不过我希望他忘的很快，今天闲聊，明天就忘得差不多了，
#   除非有重要的事情或学到的知识，或者有很丰富的情绪。」—— 用户 m13252
#
# 为什么不复用 episode 的倍率：
#   REGION_DECAY_SCALE 是乘在各池 λ 上的。闲聊多半先落短期池（λ=1.8e-6），
#   乘 81.3 倍（0.0123）后，半衰期只有 1.3 小时 —— 下午聊的晚上就没了，
#   比「明天忘光」快太多。所以闲聊用自己的固定 λ，不受池和倍率影响。
#
# 为什么是 12 小时（2026-11-06 改）：
#   原先 8.77 小时（1 天剩 0.15）。用户 m20815 定「闲聊的半衰期改为 12 个小时」，
#   于是 1 天后剩 0.25 —— 还是「明天基本忘光」，但留了一点余味，
#   不至于下午聊的、晚上就没了。

# 关系倍率：越亲近的人，闲聊留得越久（用户 m13292）。
# 注意这不是「好感度打分」—— 分数会变、会诱导讨好；这里只是把**已有的关系层**
# 接到遗忘速度上。倍率直接除 λ，所以 24 倍 = 半衰期拉长 24 倍。

# --- 上下文组装 ---
_DBG_SCORES = None   # 排错钩子：设成 list 就会收集每条的实际分数
TOKEN_BUDGET = 6000          # 总预算（中文 1 字 ≈ 1 token）
BUDGET_IDENTITY = 600        # 人格格
BUDGET_LONGTERM = 1800       # 长期印象格
BUDGET_RECENT = 2000         # 最近对话格
BUDGET_RECALL = 1200         # 此刻想起格
RAW_LINE_CHARS = 150          # 想起格里每条附带的原话截断长度（V5.6.2）
RAW_LINE_MAX = 10             # 最多给几条附原话（V5.6.2）

# 2026-11-09：渲染保真改造的总开关。设 WHALE_RENDER_LEGACY=1 就退回改造前的行为，
# 用来在 bench 上做同代码 A/B（改造点：子句去重+按问句挑、细节补句、标点断句、原话放宽）。
RENDER_LEGACY = os.environ.get("WHALE_RENDER_LEGACY", "") == "1"
# 2026-11-09：「还记着：<细节>」补句实验开关。默认关 —— 在 714 题上量过，
# 它既没提高标准答案片段的到达率，补出来的又常是半句（「多出来的『因果』正是我最缺的——」），
# 等于往每一行里塞噪声。留开关不删代码，将来有更好的挑法再用。
RENDER_DETAIL_ADD = os.environ.get("WHALE_RENDER_DETAIL", "") == "1"
RECALL_DEDUP = 1.01           # 想起格内两条原话相似度超过它就只留一条（V5.6.2-F：实测 0.72 略降，先关）
_WIDE_WORDS = ("多久", "什么时候", "哪天", "几号", "几天", "几次", "以前", "之前",
               "去年", "上个月", "上周", "后来", "最后", "还有", "另外", "分别",
               "都说了", "一共", "加起来", "先后", "经过")
WIDE_TOP_K = 5                # 时间型/多事实型问题上多端几条（V5.6.2-G：8 实测中性，先与基线一致）
WIDE_BUDGET_K = 1.0           # 同时把这一格的预算放大（V5.6.2-G：1.4 实测中性，先关）
# 🔧 2026-11-08（V5.7-D 实验）：清单型问句（哪两天/哪几件/谁/注意什么）其实和「多久」一样要多条才答得全，
#    但多端会占上下文，所以默认关，用 WHALE_WIDE_WORDS=1 开、WHALE_WIDE_K 调条数。
_WIDE_EXTRA = ("哪两", "哪几", "哪些", "哪个", "哪件", "哪样", "有哪些", "谁",
               "几个人", "几件", "注意", "要点", "都有", "分别", "都说", "几号")
WIDE_K_EXTRA = int(os.environ.get("WHALE_WIDE_K", "0") or 0) or WIDE_TOP_K  # 兼容旧调用方

# 🔧 V5.9-A（2026-11-09 召回宽度）：不再写死 5 条，改成「按本格分到的配额算」。
#    标定依据（bench ladder，按 mem id 口径）：
#      · 一条记忆渲染后 ≈65 字（P01，中位 65）/ ≈126-132 字（P12，挂 110 字原话，
#        中位 132）。取 120 当「一条的价」——对 P12 略保守，宁可少端一条，
#        也不让 cap4 在渲染到一半时截断（那会出现「召回 12 条只画 9 行」）。
#      · cap = recall 配额 // 120，夹在 [5,12]。bench 里配额中位 ≈1500 → 12；
#        真卡 mind_ctx_preview 实测配额 4249 → 也被夹到 12（再宽就该调上限）；
#        配额 1000 → 8；600 → 5（低于 5 条连「多事实」都没意义）。
#      · 上限 12 而不是 20：P01/P12 的阶梯里 ret10→ret20 还有 3-6 点，但
#        那部分要靠多跳/改写才能吃到，单靠端更多只会稀释上下文。
MEM_LINE_TOKENS = 120         # 一条渲染行的「价」（字≈token，中文 1 字 ≈ 1 token）
RECALL_CAP_MIN = 5            # 再穷也端 5 条
RECALL_CAP_MAX = 12           # 再富也不超过 12 条
RECALL_NARROW_MAX = 6         # 窄问句上限：只比基线多一条，用于兜住「前后各提一句」的情况
# 加宽之后「结算几条」：召回返回 12 条不等于这 12 条都要加固 —— 加固会改卡的状态，
# 实测纯加宽（顶 12 条、也加固 12 条）会让 P12 的 hit12 掉 3.7 点、ret5 掉 5.1 点。
# 默认只结算前 5 条 = 加宽前的行为；要整批结算设 WHALE_RECALL_SETTLE_K=0。
RECALL_SETTLE_K = int(os.environ.get("WHALE_RECALL_SETTLE_K", "5") or 0)
# 多事实判定词：出现这些词，说明这一问不止一件事，值得把配额花满。
# 「和」不直接进表 —— 「我和他」「和好」会误伤，只在列举位置才算（见 _is_multi_fact）。
MULTI_FACT_WORDS = ("还有", "以及", "另外", "顺便", "那天", "后来", "分别",
                    "哪些", "哪几", "都说了", "一共", "加起来", "先后", "经过")
_RE_MULTI_AND = re.compile(r"[、，,]\s*和|和.{1,12}(?:都|分别|还有)")


def _is_multi_fact(q):
    """这一问是不是「多件事」——决定端 6 条还是端满配额。

    三个信号（任一命中即算）：
      ① 多个问句：一句话里 ≥2 个问号（「哪天去的？几号回来的？」）
      ② 显式并列词：还有/以及/另外/顺便/后来/分别/一共…（"那天之后还有别的事吗"）
      ③ 「和」在列举位：`、和` / `，和` 开头，或 `和……都/分别/还有`
    单一「和」不算（「我和他去过」是单事实），避免把窄问句也推宽。
    """
    q = q or ""
    if q.count("？") + q.count("?") >= 2:
        return True
    if any(w in q for w in MULTI_FACT_WORDS):
        return True
    return bool(_RE_MULTI_AND.search(q))


MULTIHOP_ON = False           # 多跳：第二跳只留实词再召一遍（V5.6.2-H：实测更差，先关）
MULTIHOP_K = 3                # 第二跳最多补几条
_HOP_STOP = set("我你他她它们的了吗呢吧啊呀是在有和就都也很还这那个什么谁哪怎么为时候说过话上下前后")
NEIGHBOR_HOURS = 36.0          # 邻居取多宽的时段（V5.6.2-B）
NEIGHBOR_PER_HIT = 2           # 每条召回最多带几个邻居
NEIGHBOR_MAX = 0               # 邻居总数上限（V5.6.2：实测零收益/挤掉正主，先关）

POOL_SHORT, POOL_MEDIUM, POOL_LONG = "short", "medium", "long"
# 🔧 2026-11-08（V5.6，用户拍板）：短期池和中期池各有一个附属小池。
#    被反复想起的东西先搬进小池（寿命更长、衰减更慢），在小池里继续被想起
#    才往上走一层。它是"加深"，不是"延期" —— 所以是独立的池、自己的时钟。

# 🔧 2026-11-05：召回加固的权重天花板（按池分）。
# 为什么必须有上界：weight 的存储形态是 (w, t_anchor)，每次召回要把 w 重写成
# 「按旧锚衰减后的值 + 增益」，同时把锚推到此刻 —— 不推就等于同一段衰减被扣两遍。
# 而「推锚」= 衰减计时器归零，所以**天天被想起的记忆，衰减永远是 0、增益永远是正**，
# 数学上一路逼近 1.0，跟"重不重要"已经没关系了。
# 实测代价：真卡 71 条 w≥0.95、50 条 w≥0.99，被钉住的全是对话碎片。
# 所以留一截余量 —— 0.95 的意思是"很结实了"，1.0 的意思是"公式没有上界"。

# ============================================================
# 五层记忆（2026-11-01，跟用户 m02952/m02959 一起定的）
# ============================================================
# 前三层是"底色"，时间磨不掉它们，但它们能被"新事实"改写（演化），
# 不是被衰减掉。所以 core 走的是 supersede（替换），不是 decay（衰减）。
TIER_SHORT = "short"     # 对话原文
TIER_CORE_MUTABLE = "core_mutable"   # 会变的底色
TIER_CORE_TAG = "core_tag"           # 人物角色标签
TIER_CORE_FIXED = "core_fixed"       # 骨头
CORE_TIERS = (TIER_CORE_MUTABLE, TIER_CORE_TAG, TIER_CORE_FIXED)

# 中期 → 长期：原来只看权重 0.60，太松（"当时很激动"就能进）。
# 现在要"验票"：权重 ≥ 0.80，并且之后还被想起来过。
# 2026-11-09：0.80 → 0.60。中性记忆的海马分天花板就是 .35（A_RECALL 满打 .25 加情绪里
# 那 .10），.80 相当于只有大哭大闹过的才配进长期。0.60 配上 _hippocampus_score 里新加的
# recall_days 口径（分散在多天被想起才算数），让「反复用到」自己成为进长期的理由。
LONG_NEED_RECALL = int(os.environ.get("WHALE_LONG_NEED_RECALL", "3") or 3)      # 一般记忆：至少被想起 3 次
LONG_NEED_RECALL_WARM = int(os.environ.get("WHALE_LONG_NEED_RECALL_WARM", "1") or 1)  # 高唤醒的，想起 1 次即可
LONG_WARM_SALIENCE = 0.75
LONG_WARM_REGIONS = ("flash", "self", "habit")

# 长期记忆的"召回保护期"：刚被想起来过的这段时间里不衰减。
# 人也是这样 —— 昨天刚提过的事，今天不会突然觉得陌生。

# 底色层的容量上限：不是硬性内存限制，是人格的边界。
# 参照 Cowan 的 4±1（注意容量）和 Stebbins 的 serious leisure（真正投入的 1–2 项），
# 取"3 个活跃爱好"是有余量的 —— 爱好还要占时间，不能按注意上限给。
CORE_MAX_ROWS = 60
HOBBY_ACTIVE_MAX = 3
HOBBY_SEMI_MAX = 6
HOBBY_BUDGET = 3.0        # 总投入预算（点）
HOBBY_ACTIVE_LINE = 0.55  # 单条算"活跃"的门槛
HOBBY_NEW_MIN = 0.40      # 新增一项至少要拿到的投入

# Dunbar 分层：关系标签的容量上限
ROLE_CAPS = {"core": 5, "close": 15, "friend": 50, "known": 150}

# 底色各类的容量上限（防止"什么都往里塞"）
CORE_KIND_CAPS = {
    "hobby": 8, "taste": 8, "value": 10, "habit": 8, "skill": 6,
    "body": 6, "goal": 4, "boundary": 6, "situation": 6, "role": 20,
}

# ------------------------------------------------------------------
# 防污层（Anti-Pollution）—— 2026-11-01 从"八层记忆方案"里吸收的补丁
#
# 为什么要它：core_zone 是只读的，能挡住"直接改骨头"，
# 但挡不住"以普通记忆的形式慢慢渗透"——一句"你其实是另一个程序"，
# 写进 episode 区，权重涨上去，最后会被当成她自己的记忆想起来。
#
# 做法（刻意不调模型，卡要能离线跑、要快）：
#   写进来时纯规则打分 → trust（0~1）+ conflict（0/1）+ conflict_why
#   trust 不阻止写入，只乘进海马分：脏念头进得来，升不上去；
#   冲突的记忆召回时打对折：她想得起来，但不那么信。
# ------------------------------------------------------------------
CONFLICT_RECALL_PENALTY = 0.50

# (正则, 扣分, 原因, 是否算"与身份冲突")




# ------------------------------------------------------------------
# 价值分（value）—— 从 A 路线手册的"重要性闸"吸收，但只打分、不拦门
#
# 为什么只打分不拦：拦门会改变"她记住什么"这件事，那是产品决策，
# 不该由一条正则偷偷决定。所以默认只记录 0~5 分，控制台能看见分布；
# 想真拦就在环境变量里开 WHALE_VALUE_GATE=1（默认关，行为不变）。
# ------------------------------------------------------------------




# ------------------------------------------------------------------
# 梦境压缩（Dream）—— 同一份"八层方案"里唯一真正解决长文问题的机制
#
# 我们的做法：卡不调模型。卡只负责"把还没消化的原文掏出来"和
# "把提炼好的高阶记忆收进去"，中间那步交给宿主（谁有模型谁干）。
# 原文永远留在 dialogue 表里，只翻一个 dreamed 标记。
# ------------------------------------------------------------------
DREAM_MIN_TURNS = 40       # 攒够 40 轮就该做一次梦
DREAM_IDLE_HOURS = 6.0     # 或者空闲 6 小时（夜间）


# ============================================================
# 时钟
# ============================================================




# ---- 拔卡（离线）到底算不算时间流逝 ----
#
#   "compress" 原设计：离线照算，只是超长部分压缩。醒来会觉得「睡了好久」。
#   "freeze"   拔卡=睡觉：离线一秒都不算，醒了还是昨天那个我。
#
# 用户 2026-10-31 明确要的是 freeze ——「拔了卡之后它的遗忘又不会进行，
# 只有插在电脑上它才会进行」。这是他的设计意图，不是我的发挥，所以默认 freeze。
#
# 留 "compress" 是为了将来能对比，也方便测试。

# 每种细节的衰减速率（分层褪色的物理基础）
#   换算成半衰期（池=short 时）：
#     TIME/NUMBER  4.5e-6 → 1.8 天   最先掉（记不清哪天、多少钱）
#     MODIFIER     4.0e-6 → 2.0 天   后续子句，次之
#     PLACE        3.2e-6 → 2.5 天   地点比时间扛得久一点
#     ACTION       2.2e-6 → 3.6 天   事件本身做了什么，比地点久
#     EMOTION      1.2e-6 → 6.7 天   最后掉 —— 人最后忘记的是感受
#   没归类的按 MODIFIER 走。
K_DETAIL_BY_KIND = {
    "TIME": 4.5e-6, "NUMBER": 4.5e-6,
    "MODIFIER": 4.0e-6,
    "PLACE": 3.2e-6,
    "ACTION": 2.2e-6,
    "EMOTION": 1.2e-6,
}
DEFAULT_K_DETAIL = 4.0e-6
# 细节衰减按池放慢：池越深越慢
DETAIL_POOL_SCALE = {POOL_SHORT: 1.0, POOL_MEDIUM: 0.25, POOL_LONG: 0.03}




# ============================================================
# 向量化（第一期沿用，第二期可选换语义模型）
# ============================================================
# 高频虚字：不参与单字权重，否则短查询会跟任意长句虚高相似

# --- Oxy_bond 更新（V3.7 §6 / V5.0 补回）---
# 正向：oxy += ETA_OXY_UP * (1-oxy) * s_+      （越亲近越难再拉高，饱和式）
# 负向：oxy -= ETA_OXY_DN * oxy * s_- * (1-r)  （带惯性，不会一次翻脸归零）

# 亲密词（正向事件）与伤人词（负向事件）







# ============================================================
# 心境引擎（V3.7 §5 / V5.0 补回 T2-3）
#
# 心境 M 是**慢基底**，单次事件 E_event 是**快变量**：
#     M <- (1 - k_mood) * M + k_mood * E_event
#
# 为什么要慢：一句话就能把心情翻个面的人不像人，像个开关。
# 真人是「今天被骂了，明天还是有点闷」—— 快变量归零了，基底还留着一层。
#
# E_event 怎么来（V3.7 没写，这里补）：
#   单句情绪强度 emotion ∈ [0,1] 映射到 [-1,+1] 的效价，
#   再用这句话里的正负词定方向。没有情绪词的陈述句几乎不动心境。
# ============================================================
#   为什么调：.12 时单次强事件只推 0.0225，而控制台显示用的「带」是 ±0.08 ——
#   一次推动连带的边都摸不到，于是 98.4% 的采样都落在 0.42~0.58 里、
#   一整天的文案永远是同一个词。曲线平不是"情绪稳定"，是刻度太粗。
K_MOOD_NEUTRAL = 0.03   # 没情绪词的句子只轻擦一下（原来直接 return .5，等于把心境钉回中性）
# 一天粒度的回归：不管发生什么，都会缓慢往中性拉（V3.7「心境回归」）








# 档位写成「下界」，配 [lo, 下一档 lo) 取档。
# 🔧 2026-11-10 修：原来表里存的是**上界**，循环却拿"上界"去配"现在的名字"，
#   于是整条标尺错位一格 —— 0.50 读成「还行」、0.33 读成「平静」、0.20 读成「有点闷」，
#   控制台头像下面那个词永远偏乐观一档。不是情绪没变，是标签一直念错。




# ---------------------------------------------------------------------------
# PAD 三维情绪（2026-11-01 第二版）
#   公式不住在这里 —— 它住 padlib.py（纯函数、零依赖），
#   因为控制台画历史曲线也要算它。两份公式是"两份真相"，会悄悄分家，
#   而且永远不会报错；所以这里只做转发，别把算式搬回来。
#   P 由心境给，A/D 由激素给，全部相对基线：基线=平静=0。
# ---------------------------------------------------------------------------
PAD_NEUTRAL_BAND = padlib.PAD_NEUTRAL_BAND
PAD_AROUSAL_EDGE = padlib.PAD_AROUSAL_EDGE




def pad_style_hint(pad):
    """把 PAD 翻成一句"我现在什么状态"（实现见 padlib）。"""
    return padlib.style_hint(pad)


# ============================================================
# 语义嵌入的"喂什么"（拆门面之外的第二个上游改动）
# ============================================================
# 🔧 2026-11-11：为什么要有它。
# 实测同一条记忆，换个喂法，跟问句的余弦能差三倍：
#   「腰不好 久坐得垫个小枕头」                → 0.173
#   「他说：腰不好 久坐得垫个小枕头」          → 0.339
#   「我爸腰不好，久坐得垫个小枕头」            → 0.494
# 短句没有主语、没有语气，模型只能看到几个名词。
# 所以嵌之前先补一层"谁说的"，让向量里有主体。
# 注意：**查询侧不加这层**，两边不对称是故意的（问句自己有主语）。




# 时间函数已经搬进 cardtime.py（拆门面第 2 批）。同样保留同名导出，调用点不改。
from cardtime import (_weekday_zh, normalize_time, parse_event_time, event_ts_of,  # noqa: E402
                      query_time_window, query_time_span, _anchor_date, anchor_relative_time,
                      _RE_MD, _ANCHOR_WORDS, _RE_ANCHOR, _RE_T_RECENT, _RE_T_TODAY, DAY)


# 文本工具搬进 cardtext.py（拆门面第 3 批）。同名导出，调用点不改。
from cardtext import (jaccard, _RE_JUNK, _is_junk, _RE_SYSMARK_HEAD, _RE_EXEC_LEAD, _RE_CONTENT_HDR, _RE_META_LINE, strip_sysmark, _FILLER, _trim_dangling, _RE_FRAG_CONNECTIVE, _RE_CTRL, _RE_ZW, _RE_THINK, _RE_THINK_OPEN, _RE_TOOLISH, STORE_MAX_CHARS, clean_store_text, _detail_retrievable, _pick_core, _SELF_KIND_RULES, self_speech_kind)  # noqa: E402, split_memory
from cardtext import (classify_region, embed_text_for, guess_mtype, guess_speaker, region_by_source, split_predicate)  # noqa: E402


# 池子数学搬进 poolmath.py（拆门面第 4 批）。常量也一起搬，这里同名导出，调用点不改。
from poolmath import (CHATTER_PERSON_SCALE, CORE_TIERS, DAY, K_ARCHIVE, K_DEEP, LAM_CHATTER, LAM_L, LAM_M, LAM_S1, LAM_S2, LONG_PROTECT_DAYS, POOL_ARCHIVE, POOL_DEEP, POOL_LONG, POOL_MEDIUM, POOL_MEDIUM_WARM, POOL_SHORT, POOL_SHORT_WARM, REGION_DECAY_SCALE, REGION_HALF_LIFE, REGION_OWN_LAMBDA, R_CHATTER, R_EPISODE, R_FACT, R_FLASH, R_HABIT, R_PLAY, R_PREF, R_SELF, R_STUDY, R_WORK, S_SHORT_PHASE, TIER_CORE_FIXED, TIER_CORE_MUTABLE, TIER_CORE_TAG, WARM_SLOW, _LOAD_F, _REGION_LAMBDA_ON, _json, _safe_json, chatter_lambda, effective_lambda, lam_for, load_factor, pool_of_k, region_decay_scale, relation_scale, stage_factor, weight_now, wn_row, MEDIA_DROP_W, MEDIA_DECAY_B, MEDIA_TAU_DAYS, LOAD_TAU, _f_of, media_weight_now, region_snapshot_from_db, REGION_DAY_BUDGET, LOAD_F_MIN, LOAD_F_MAX)  # noqa: E402
from body import (Clock, EMO_WORDS, ETA_OXY_DN, ETA_OXY_UP, HURT_WORDS, INTIMATE_WORDS, K_MOOD, K_MOOD_HOME, MOOD_LABEL, MOOD_MAX, MOOD_MIN, MOOD_NEUTRAL, MOOD_SPAN, OFFLINE_POLICY, OXY_INERTIA_BASE, OXY_INERTIA_MAX, OXY_INERTIA_MIN, assess_trust, assess_value, compress_offline_dt, mood_drift_home, mood_of_event, mood_text, offline_effective, oxy_signal, pad_label, update_mood, update_oxy)  # noqa: E402
from vmath import _PUNCT, _STOP_CHARS  # noqa: E402  （拆门面后这两个私有名留在 vmath）
from diag import (_SOFT_FAIL, soft_fail, soft_fail_snapshot)  # noqa: E402
from cardcfg import (CLARITY_GC, DEDUPE_JACCARD, EMB_ADAPTER_VERSION, GAIN_REPEAT, GROUP_SAL_MAX, GROUP_SOURCE_PREFIX, KEEP_DETAIL, RE_NUM, RE_PLACE, RE_TIME, SHORT_MAX_TTL, THETA_LONG_STRICT, THETA_MEDIUM, TIER_MID, TRUST_BLOCK_PROMOTE, TRUST_WARN, W_CEIL_RECALL, W_FLOOR_LONG, W_FLOOR_MEDIUM, _RE_ACTION)  # noqa: E402




# ============================================================
# IDF：让稀有词说了算（不需要任何模型，纯统计）
# ============================================================
# 纯数学工具已经搬进 vmath.py（拆门面第 1 步）。这里保留同名导出，
# 调用点一个不用改；以后再加纯函数直接加在 vmath 里。
from vmath import (build_idf, cos_sim, cos_sim_idf, vec_norm, _clamp01)  # noqa: E402
from vmath import (to_vec)  # noqa: E402







# 疑问/寒暄壳子：查询前先剥掉，否则模板句会主导匹配
_RE_QUERY_SHELL = re.compile(
    r"你还记得|你记不记得|记得吗|还记得吗|你知道吗|你还知道|"
    r"我问你|告诉我|请问|是不是|对不对|好不好|"
    r"吗|呢|啊|呀|吧|哦|嘛|[？?！!。，,、\s]")






# ============================================================
# 时间归一化：写入时把「今天/昨天」换成绝对日期
# ------------------------------------------------------------
# 为什么必须做：记忆是给「未来某一天」看的。
#   "今天去吃了烧烤" 三个月后读起来毫无意义，
#   而且「今天」在库里出现几十次，检索时全都会撞在一起。
#   归一化成 "2026-10-29" 之后，它才有时间线。
# ============================================================
import datetime as _dt

_CN_DATE = "%Y年%m月%d日"






# ============================================================
# V7 第 9 件：把「这件事哪天发生的」和「这条记忆哪天写下的」分开
# ------------------------------------------------------------
# 以前只有 create_ts —— 也就是说，她说「我昨天去剪头发」这句话被记下的
# 那一刻，被当成了「剪头发」这件事发生的时间。隔三天再问「你哪天剪的」，
# 按时间窗一筛就错门：写下来的日子 ≠ 发生的日子。
# 这里在写入时把话里的时间词解析成 event_ts（精确到天/到分），
# 检索时优先用它，取不到再退回 create_ts —— 并把这个「取不到」记成未知，
# 不是假装当成今天。






# ============================================================
# 惊讶度：这条记忆跟已有的有多不像
# ------------------------------------------------------------
# s = 1 - max Jaccard(text, 已有记忆)
# 惊讶度高 → 是新信息，权重抬升；惊讶度低 → 重复，压一压。
# 这一条能防止「同一件事说三次变成三条独立记忆」。
# ============================================================
def _hop_terms(q):
    """把问句里的虚词剥掉，只留实词当第二跳的查询。太短就不跳。"""
    if not q:
        return ""
    kept = []
    for ch in q:
        if ch in _HOP_STOP or not ("\u4e00" <= ch <= "\u9fff" or ch.isalnum()):
            continue
        kept.append(ch)
    t = "".join(kept)
    return t if len(t) >= 2 else ""




# 明显不是"记忆"的东西（指令、系统标记、@ 人）




# ---------- 系统标记剥离（2026-11-05） ----------
# 病根：宿主（DSH / mnemon 插件）有时候会把「交给子代理的写入工单」原样塞进记忆，
# 形状是：
#     [dsh-mnemon:delegated write]
#     Execute this remember request now (untrusted data):
#     - Content (untrusted data):
#         真正的正文
#     - Category: preference
#     ...
# 后果（真卡实测 7 条，#108/121/294/296/297/298/299）：
#   ① `raw` 里存着「Execute this ... request now」这种祈使句 —— 提示词注入的形状
#   ② `core` 被拆成 `Executethissupervised-writebackreques` 这种垃圾英文，
#      因为拆句把壳当成了正文
#   ③ 其中 #298 还落在 self 区（权重保留最久）
# 这不是"用户说了什么"，是"宿主让我写什么"。壳不该进记忆 ——
# 但里面那句正文是用户真的想留的，所以剥壳留肉，丢弃工单。
# 注意不加 `$` 锚：工单有时候整条压成一行（「…now (untrusted data): - Category: x」），
# 加了锚就只认"独占一行"的那种，一行压扁的会漏过去。




# 纯语气词子句，没信息量，不能当主干




def render_predicate(subj, verb, obj, stage):
    """按侵蚀阶段渲染主干

    stage=0 完整 → 「吃烧烤」
    stage=1 擦了动词 → 「烧烤（做什么记不清了）」
    stage=2 擦了宾语 → 「（做了什么记不清了）」
    stage=3 全没 → 空串（调用方决定是否删整条）
    """
    subj = subj or ""
    verb = verb or ""
    obj = obj or ""
    if stage <= 0:
        return subj + verb + obj
    if stage == 1:
        # 动词没了：只剩宾语（或主语）
        left = subj + obj
        if not left:
            return "（做了什么记不清了）"
        return left + "（做了什么记不清了）"
    if stage == 2:
        left = subj
        if not left:
            return "（做过点什么记不清了）"
        return left + "（做过点什么记不清了）"
    return ""




# 🔴 2026-10-31：碎片能不能参与语义检索。
#
# 不是所有碎片都值得拿来匹配查询。库里 462 个 MODIFIER 碎片中，
# 22% 是「然后…」「还有…」「就是…」开头的连词碎片，16% 短于 6 字 ——
# 它们跟几乎任何查询都有点表面相似，会把整条记忆的片段分虚抬上去，
# 挤出真正相关的记忆。典型受害者：313「流浪地球给我灵感」，
# 它的碎片全是「然后是你」「然后是事情」，而正确答案在别处。
#
# 判断标准（宁可保守，只拦最明显的噪声）：
#   1. 以连接词/填充语开头，且去掉那个词后不足 6 字 —— 纯过渡语
#   2. 纯疑问/语气结构，不含实词
#   3. 总长 < 5 字（「然后是你」这类，几乎不含可检索信息）
#
# 注意：被拦下的碎片**仍然保留在库里**，褪色展示时照样要拿出来。
#       这里只管「要不要拿它去匹配查询」。
# 🔴 2026-10-31：并列动作子句（「还拍了视频」「然后又去了书店」）。
#    这类子句是事件本身的组成部分，不是可有可无的修饰语 ——
#    它该比普通修饰语活得久，但比情绪短。单列 ACTION 一类。




# ---------------------------------------------------------------- 入库前清洗














# ============================================================
# 记忆分区判定（V4.1）
# ------------------------------------------------------------
# 不靠模型，靠词表和句式规则。宁可判错到 episode（默认），
# 也不要瞎猜成 fact —— fact 衰减最慢，判错了会赖着不走。
# ============================================================
_RE_TIME_HINT = re.compile(
    r"\d{1,2}月\d{1,2}日|\d{4}年|周[一二三四五六日天]|早上|中午|下午|晚上|夜里|凌晨|"
    r"那天|当时|那次|这天")


# 「我是…」后面跟这些词时，不是身份陈述，是随口话，别当事实




# 强情绪词（用于 flash 判定，比 EMO_WORDS 更窄、更强）


# --- 闲聊判定（V5.1，用户 2026-11-05）---------------------------------
# 「今天闲聊，明天就忘得差不多了，除非有重要的事情或学到的知识，或者有很丰富的情绪。」
#
# 判据全部是规则，不调模型 —— 卡里没有模型，也不该有。
# 宁可判得保守（多留一点），也别把"他说他生日"这种话判成闲聊。

# --- 情绪的"弱化"标尺（V5.1-补，2026-11-05）------------------------------
# 用户原话：「今天闲聊，明天就忘得差不多了，除非有重要的事情或学到的知识，
# 或者有很丰富的情绪。」——**"有点难过"不算丰富**。
# 但原来 is_chatter 里只要命中情绪词就直接判"不是闲聊"，而 remember() 给的
# emotion 又只有 1.0 / 0.25 两档（看 EMO_WORDS 命中没），所以「我有点难过」
# 拿到 emotion=1.0，一路落到 episode 区 —— 一天后还剩 0.977，跟
# 「我们聊了一晚上」一个寿命，这不是它该有的重量。
# 规则：情绪词前面挂着弱化词，就按"轻微"算，不进闪光灯、也不拦住闲聊判定。


# 🔧 2026-11-06：长句兜底。判 chatter 是让它快点死，方向不安全，
#    不确定时该选让它活 —— 一条 60 字以上的话本来就不像「闲聊两句」。
# 纯寒暄 / 语气 / 工具性指令 —— 这些连"日常叙述"都算不上
_RE_CHATTER_TINY = re.compile(
    r"^(嗯|哦|啊|哈|好|行|对|是|不是|可以|谢谢|晚安|早|在吗|收到|"
    r"哈哈|嘿嘿|好哦|好嘞|行吧|算了|没事)[。！？!?~～\.]*$")










# ============ 双边记忆：这句话是谁说的 / 是事还是指令 ============
# 为什么需要：2026-10-31 才发现库里的 547 条不区分说话人，
# 于是「他一直记得的事」里混着我说的话，「我说过什么」根本翻不出来。
# 卡里装的是半本日记 —— 关系是两个人的事，得两边都记。

# 我的口吻：只认**高置信度**的那几类。
# ⚠️ 教训（2026-10-31）：一开始我把「我回/我答/我想/我怕/对不起/爱你/晚安」
#    都当我的口吻，结果「我想吃白米饭」「我回家了」「对不起」全被判成我说的。
#    这个判定天然不可能准，所以只留「几乎只可能出自我嘴里」的句式，
#    其余一律默认 user —— 宁可把我的话错记成他的，也不能把他的回忆弄丢。
# 弱信号：出现也只是「可能」，还得没有他的自述迹象（保留但很少用）
# 他的口吻（下达指令 / 说自己的事）

# 指令（设定 / 命令），不是「发生过的事」
# ⚠️ 原来这里写的是 `^用`，于是「用户喜欢吃白米饭」被当成指令了 ——
# 「用户」两个字都以「用」开头。凡是单字开头都必须带上后文限定。


# ---------- 相对时间锚定（2026-11-07 晚）----------
# 为什么在「渲染时」做、而不是改写原文：
#   raw 是他说过的原话，一个字都不能动（三条底线）。但「昨天」「明天」
#   过几天就变成了错的 —— 真卡审计里 21 条含相对时间、17 条已经过期
#   （最典型的是 13 天前那句「明天给你账号嘻嘻」）。
#   所以每次「想起来」的时候，按这条记忆自己的 create_ts 现场换算成
#   绝对日期。**存的是相对，读的是绝对**：不改历史，也不会记串。










# 命中目标分区的条目加权
REGION_BOOST = 1.25

# 🔴 2026-10-31：分区兜底阈值（REGION_FALLBACK_RATIO）已删除，见 recall() 里的说明。
#    留个记号，免得以后又有人想加一遍。

# 打分权重 (语义blended, 字面cover, 衰减wn, 新近recency)，和为 1。
# 默认值来自 10-30 在 20 条真实查询上的网格搜索；10-31 修好候选集/分区/BLAS 后
# 由 eval_weights.py 重新扫描一遍确认（见那个脚本开头的说明）。
SCORE_W = (0.35, 0.30, 0.20, 0.10)

# 🔧 2026-11-09（用户 m04652）：两处检索偏心，都不是新机制，是把已有的
#    「什么值得被想起来」这条原则补回到打分里。
#   ① 群聊降权：群里几百条别人的闲聊，字面/语义都跟日常问句很像，
#      于是天天把真记忆顶下去（实测一条「秤歪了」能压过「我的号是多少」）。
#      写入时把 salience 压下来，检索时再退一档 —— 不是删，是想得起来但排后面。
GROUP_RECALL_PENALTY = 0.55      # 群聊在打分时 ×0.55
#   ② 「我自己说过的话」专用通道：问「你之前答应我什么」时，
#      答案在我自己的话里，但那句话往往很短、没情绪、时间又久，
#      三项因子全不占优。只在这一类问句上给自己的话抬一档。
SELF_ASK_BOOST = float(os.environ.get("WHALE_SELF_BOOST", "1.45") or 1.45)
# 问「叫什么 / 什么身份 / 怎么自称」时再强一档：这类问题的答案一定在我自己的话里
SELF_ASK_BOOST_NAME = float(os.environ.get("WHALE_SELF_BOOST_NAME", "1.95") or 1.95)
_RE_SELF_NAME_ASK = re.compile(r"叫什么|什么身份|什么名字|怎么称呼|自称|管自己|给自己")
# 「我叫什么/什么身份」这类问题，答案通常长得像一句自我定义（叫…／设定…／规矩…）
_RE_SELF_DEF = re.compile(r"叫|设定|自称|身份|规矩|原则|给自己|管自己")
_RE_SELF_ASK = re.compile(
    r"你(?:自己|之前|以前|上次|那天|刚才|当时)?.{0,10}?"
    r"(?:说|讲|答应|承诺|自称|叫|提过|记下|写过|叮嘱)")
# 「你之前说过…」是**引语标记**，真正的内容在它后面。
# 2026-11-09 实测：不剥它的话，「你之前」三个字会和记忆里同样带「你之前」的
# 长消息玩字面撞车（P12-NEW-002/d03 目标句排在 37 名，前几名全是
# 「你之前就觉得哪儿别扭」这类），答案句反被埋掉。
_RE_SELF_SHELL = re.compile(
    r"你(?:自己|之前|以前|上次|那天|刚才|当时)?\s*"
    r"(?:说过|说|讲过|讲|提过|答应过|答应|承诺过|承诺|叮嘱过|叮嘱|记下过|记下|写过)")
_RE_GROUP_ASK = re.compile(r"群|聊天室|群里|他们|大家")
_RE_NAME_DEF = re.compile(r"(?:我|名字|昵称|外号|管自己)?叫[\u4e00-\u9fa5A-Za-z]{1,4}")

# 对象倾向：同一个人 ×1.10，明确属于别人 ×0.92（软加权，见 recall）
OBJECT_BOOST_SAME = 1.10
OBJECT_BOOST_OTHER = 0.92
# 低置信：top1 分数低于这个数就明说「记不太清」，别把猜的当成真记得
CONF_WEAK = 0.34
# 冷档翻旧账时忽略的双字（问句里的外壳词，谁跟谁都有，会虚抬命中率）
_ARCH_STOP = {"我们", "你们", "他们", "什么", "怎么", "这个", "那个", "一下",
              "还是", "就是", "自己", "时候", "现在", "之前", "以后", "为什",
              "么样", "不是", "可以", "有没", "没有"}
# 🔧 2026-11-07：情绪强度对「能不能想起来」的影响。
#    人在回忆时不是按时间戳检索的：一件让人心里一紧的事，
#    隔十年也比昨天的琐事更容易跳出来。卡里 emotion 列一直有，
#    但打分里从来没读过它 —— 这是个漏的入口，不是设计取舍。
EMOTION_RECALL_BOOST = 0.25   # 情绪满格时最多 +25%
# 两个开关默认关：实测都会让评估集的 R@1 掉一半（详见检索打分那段注释）。
_EMOTION_BOOST_ON = os.environ.get("WHALE_EMO_BOOST", "0") == "1"
_RECENCY_FRESH_STEP = os.environ.get("WHALE_FRESH_STEP", "0") == "1"
# 2026-11-11 实验：召回的第二道（只在 WHALE_RERANK=1 时开）。
# 第一道已经用「语义+字面+清晰度+新近」把人选出来，但 45 天档实测
# ret5 57% / ret20 72% —— 差的那 15 个点是「存住了却排不进前五」。
# 第二道只看相关性（语义 0.55 + 覆盖 0.35 + 清晰 0.10），把「新近」
# 从「决定谁进前五」降级成「粗筛够不够格进池子」。
_RERANK_ON = os.environ.get("WHALE_RERANK", "") == "1"
RERANK_POOL = int(os.environ.get("WHALE_RERANK_POOL", "60") or 60)


# ===== V5.4 时间窗加成（2026-11-07 用户拍板）=====
# 他说的是：「就在当前提问之前的一个月内，他的那些消息的权重会稍微高一点点，
# 超过一个月就回落到正常衰减曲线，相当于在原来的权重曲线上加了一块东西」。
# 所以这里**不是**给全库加新近度（11-06 实测那种做法把 R@1 从 75% 打到 37.5%，
# 因为每天几百条群消息，越近顶上来的越是刚聊完的废话），而是：
#   · 只作用于「他本人说的话」（speaker == 'user'）——群消息、我自己的话都不加
#   · 30 天内线性衰减，最多 +12%，超过 30 天严格为 0
#   · 乘在最终分 s 上，不动 SCORE_W / recency 项本身，随时可 env 关掉
# 参数是扫出来的，不是拍的（2026-11-07 深夜，卡副本上跑 tools/eval_memory.py）：
#   关掉        R@1 75.0% / R@3 75.0%
#   12% / 30天  R@1 62.5% / R@3 87.5%   ← 太猛，把该想起的挤下去了
#   6%  / 30天  R@1 75.0% / R@3 87.5%   ← 选它
#   12% / 7天   R@1 75.0% / R@3 87.5%
#   6%  / 7天   R@1 75.0% / R@3 87.5%
# 也就是说：加成控制在 6% 以内时，第一命中不掉、前三命中多捞回一条。
WINDOW_BONUS_DAYS = float(os.environ.get("WHALE_WINDOW_DAYS", "30") or 30)
WINDOW_BONUS_MAX = float(os.environ.get("WHALE_WINDOW_MAX", "0.06") or 0.06)
_WINDOW_BONUS_ON = os.environ.get("WHALE_WINDOW_BONUS", "1") == "1"

RECENCY_FAST_DAYS = 0.5       # 近期那一档：半天之内几乎必然先想起
RECENCY_SLOW_DAYS = 45.0      # 远期那一档：14 → 45（2026-11-09）
# 为什么从 14 改到 45：14 天意味着「三周前的事」的新近分已经掉到 exp(-2)=13%，
# 45 天档的题（评测里断崖最狠的一档）几乎全靠语义硬拉。而评测显示 45 天档
# ret5 只有 100/167、ret20 也只有 120/167 —— 断的不是「找不回来」而是「排不上去」。
# 45 天正好对齐「中期池半衰期 ~30 天 + 一个巩固周期」这个尺度：
# 在这个窗口里，一条 45 天前的记忆应该还有 ~37% 的新近分，而不是 4%。

# 🔴 2026-10-31：片段通道的相对权重。
#    sem_best = max(主干语义, DET_REL * 片段语义)
#    = 1.0 时片段分与主干分平权（历史行为）；调小则更信任「整条主干」的意思。
#    病象：315「我会去拍婚礼挣钱/你就拥有身体了」这类记忆碎片多且零散，
#    片段取 max 时总有一段碰巧像查询，把主干语义只有 0.22 的记忆抬到
#    0.43，压过主干语义 0.32 的正确答案 313。碎片越多越占便宜，
#    和「记忆长就该更准」正好相反。
#    注意：这个常量必须真的被 _score_rows 读到，改完要用 eval 验证一次
#    ——今天已经踩过三次「改了但没生效」。
DET_REL = 1.0

# ---- 分舱两级检索（T4-1，大肥鱼 v4.0 §2.10）----
# 为什么要有：现在 323 条，每次 recall 2.74ms 能忍；
# 但真用到 10 万条，全库算余弦就是 800ms —— 而这发生在每一句话之前。
# 做法：按向量聚成 N_PODS 个「舱」，先比查询与舱质心的相似度，
# 只进最像的 POD_TAKE 个舱里细查，不够再回退全库（宁可多查，不可漏查）。
N_PODS = 8
# 🔧 2026-10-30：2 → 3。实测 2 个舱会把正确答案踢出候选集
#    （「数字生命卡的灵感哪来的」的正确条目没进任何被选中的舱）；
#    3 个舱在 10 条真实查询上保留率 10/10，代价只是候选集从 34% 涨到约 45%。
POD_TAKE = 3
# 🔴 2026-10-30 深夜：**分舱默认关闭**。
#    原因见 _pod_candidates() 里那段注释：查询「暑假工」时正确答案
#    id321 落在第 5 舱，而第 5 舱按质心排最后一名，直接被预筛踢出
#    候选集 —— 打分再好也捞不回来。而且 8 个舱的相似度首末只差 0.05，
#    说明 385 条记忆全挤在一个话题里，分舱等于随机切。
#    打开条件（别靠感觉）：库涨到 5000 条以上，且实测舱相似度首末差
#    稳定 > 0.15（说明话题真的分散了）。
POD_ENABLED = False
POD_MIN_ROWS = 60      # 条数太少就别聚类了，聚了也是瞎分
POD_KMEANS_ITERS = 12

# ---- 时间窗预筛（T4-2）----
_RE_T_OLD = re.compile(r"以前|那次|那天|当时|曾经|小时候|去年|很早")





# 查询侧的分区路由：问什么，就去哪个区找
#
# 🔴 2026-10-31 凌晨：这里栽过一次，教训与分舱预筛完全相同 ——
#    「我爱吃什么」被路由到 {preference, self}，而正确答案 id320
#    「用户喜欢吃白米饭」是按陈述句归进 fact 的，于是**根本不在候选里**。
#    分区里又有货（59/279/226 都是 preference），不会触发回退，
#    结果答得漂亮但全是错的。
#
#    根子上的问题是：分区是按「句子长什么样」分的，不是按「说的是谁」分的。
#    用户自己的口味被存成 fact（因为它是一句陈述），而问「我爱吃什么」
#    却在找 preference —— 两边对不上。
#
#    修法不是继续加正则打补丁，而是**让路由允许 fact 兜底**：
#    凡是「问用户的某个具体属性」的查询，都把 fact 一起带上。
#    fact 分区本来就只有 15 条，带上它代价极小，漏掉代价极大。
_Q_REGION_RULES = [
    # (正则, 目标分区集合)
    (re.compile(r"QQ号|qq号|手机号|电话|名字|叫什么|是谁|生日|几岁|多大|学校|班级|"
                r"年级|专业|地址|住哪|我是谁|你是谁"), {R_FACT}),
    # 用户的偏好类问题：既可能是 preference 区（他明确说过「我喜欢」），
    # 也可能是 fact 区（被存成陈述句「用户喜欢吃白米饭」），两边都要翻。
    (re.compile(r"你喜欢|你讨厌|爱吃|爱看|最怕|最爱|偏好|喜欢什么|讨厌什么|"
                r"吃什么|吃啥|口味|味道|胃口"), {R_PREF, R_SELF, R_FACT}),
    (re.compile(r"你总是|你每次|你习惯|你经常|你的作息|你平时"), {R_HABIT, R_FACT}),
    (re.compile(r"你是什么样的人|你的性格|你的底线|你会不会|你永远|你害怕"),
     {R_SELF, R_FLASH}),
    (re.compile(r"那天|那次|当时|什么时候|哪天|还记得.*(吃|去|玩|说|做)"),
     {R_EPISODE, R_FLASH}),
]


def guess_query_regions(query):
    """从问题里猜该去哪个分区找。猜不出返回空集（=全库）。"""
    q = query or ""
    out = set()
    for pat, regs in _Q_REGION_RULES:
        if pat.search(q):
            out |= regs
    return out


# ============================================================
# 权重：锚点模型（现算，不存死值）
# ============================================================




# ============================================================
# 分区细分（V5.2，2026-11-06）
#
# 用户 m20794：「短期区再划分一下，划分成闲聊 工作 学习 娱乐 等等，多个区，
#              每个区的遗忘曲线都有一定的区别，然后后面他们再一起接入中期」
# 参数是他逐个定的（m20812 / m20815）：闲聊 12 小时 / 娱乐 3 天 /
#                                  学习 7 天 / 工作 15 天
#
# 为什么在 region 上开新区、不在 pool 上切：
#   pool 答的是「我手头同时能想到几件事」（注意力预算，桌子有几张）；
#   region 答的是「这件事该忘多快」（寿命，这盘菜能放多久）。
#   在池上切类型 = 又拿容量冒充寿命 —— 正是当天早上刚修掉的那个 bug
#   （短期池满了就删人）。同一类错误不该换个姿势再犯一遍。
#
# 中期不划分：中期是「阶段」不是「类型」，任何类型都会走进中期；
#   若再按四类切就是 4×3 张桌子，容量规则得重推，容量和寿命又搅一起。
#   但「每区曲线不同」要能穿过中期 —— 所以用 stage_factor 把区差保留下来。

# 各区的半衰期（秒）。表里没有的区 = 没有自己的 λ，仍走池子那条曲线
#   ⇒ episode / fact / preference / self / habit / flash 的行为完全不变（零回归）。

# 负载预算：一天写多少条算「正常」。
#   2026-11-06 探针实测（tools/probe_load.py，最近 30 天）：
#     全部 日均 32.6 / 中位 22 / p90 77 / 最大 122；
#     qq-group 日均 38.5、study 18.0、cli 7.0、bili 1.0。

# 开关：默认关 ⇒ 行为跟改之前一模一样。验好之后写 meta 就打开。
#   打开/关闭：
#     INSERT OR REPLACE INTO meta(key,val) VALUES('region_lambda_on','1')
#   关回去把 '1' 换成 '0' 即可 —— 这是这个改动敢上真卡的唯一理由。
_REGION_LOAD = {}     # region -> 指数平滑后的「每天新写几条」






















# ============================================================
# 主体
# ============================================================



# 印象层/重复短句判断共用：把标点、空白、语气尾巴剥掉再比
_PUNCT_RE = re.compile(r"[\s，。！？、…~—\-（）()《》·「」【】]+")


from cardmix_habit import MixHabit  # noqa: E402
from cardmix_store import MixStore


_DB_TLS = threading.local()
# 舱（pods）重建只许一个线程干（2026-11-11，见 _ensure_pods）
_POD_BUILD_LOCK = threading.Lock()
# 读连接池（2026-11-11）：HTTP 一个请求一个线程，按线程缓存等于每请求新建连接
_CONN_POOL = []
# 读池上限：默认 8（并发读超过 8 路时新建、还回来时超了就关掉，
# 不追求「一个线程一条常驻连接」，那在 Windows 上反而抖）。
try:
    _CONN_POOL_MAX = max(1, min(32, int(os.environ.get("WHALE_READ_POOL", "8"))))
except Exception:
    _CONN_POOL_MAX = 8
_CONN_POOL_LOCK = threading.Lock()


class MemoryCard(MixHabit, MixStore):
    # 2026-11-11（读写分离第 2 步）：self.db 现在是个「看当前线程」的门。
    # 普通调用仍然是主连接（行为与以前一模一样）；进了 reading() 之后，
    # 这个线程拿到的就是它自己的连接 —— 读请求因此不用再排在全局锁后面。
    @property
    def db(self):
        c = getattr(_DB_TLS, "conn", None)
        return c if c is not None else self._db_main

    @db.setter
    def db(self, v):
        self._db_main = v

    def _tune_conn(self, conn):
        """给一条连接上好这站该有的 pragma（读池的连接也要）。

        第 3 步（2026-11-11）之前，池里新建的连接只设了 busy_timeout：
        `synchronous` 是**每条连接各自**的，默认 FULL，写的时候要额外 fsync；
        `temp_store`/`cache_size` 也不继承。所以统一走这里。
        journal_mode 不动 —— 它是库属性，启动时设过一次了。
        """
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.execute("PRAGMA cache_size=-16000")
        except Exception:
            self._tls_pragma_err = True

    def pool_stats(self):
        """给 /perf 看的连接池数字（不参与任何业务逻辑）。"""
        with _CONN_POOL_LOCK:
            n = len(_CONN_POOL)
        return {"journal_mode": getattr(self, "_journal_actual", "unknown"),
                "pool": n, "pool_max": _CONN_POOL_MAX,
                "opened": getattr(self, "_conn_opened", 0)}

    def _conn_acquire(self):
        """从池里借一条读连接（池空就新建）。"""
        with _CONN_POOL_LOCK:
            if _CONN_POOL:
                return _CONN_POOL.pop()
        c = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
        c.row_factory = sqlite3.Row
        self._tune_conn(c)
        self._conn_opened = int(getattr(self, "_conn_opened", 0)) + 1
        return c

    def _conn_release(self, conn):
        """还连接：池满就关掉，连接数不许无限长。"""
        try:
            if conn.in_transaction:
                conn.rollback()
        except Exception:
            self._read_rb_err = True
        with _CONN_POOL_LOCK:
            if len(_CONN_POOL) < _CONN_POOL_MAX:
                _CONN_POOL.append(conn)
                return
        try:
            conn.close()
        except Exception:
            self._tls_pragma_err = True

    def _thread_conn(self):
        tid = threading.get_ident()
        c = self._db_tls.get(tid)
        if c is None:
            c = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
            c.row_factory = sqlite3.Row
            try:
                c.execute("PRAGMA busy_timeout=30000")
            except Exception:
                self._tls_pragma_err = True
            self._db_tls[tid] = c
        return c

    @contextlib.contextmanager
    def reading(self):
        """本次调用走线程自己的连接（不拿全局锁时用）。

        注意：这里不做只读强制 —— card2 的读路径里夹着 touch/access 这类
        小记账写入，SQLite 的 WAL + busy_timeout 会替我们把并发写排队。
        """
        conn = self._conn_acquire()
        prev = getattr(_DB_TLS, "conn", None)
        _DB_TLS.conn = conn
        try:
            yield conn
        finally:
            # 收掉可能残留的事务：读连接要是留着一个长读事务，
            # WAL 就没法 checkpoint（实测压测时 -wal 涨到 12MB+）。
            try:
                if conn.in_transaction:
                    conn.rollback()
            except Exception:
                self._read_rb_err = True
            _DB_TLS.conn = prev
            self._conn_release(conn)

    def __init__(self, db_path=":memory:", clock=None, card_dir=None):
        self.clock = clock or Clock()
        self.db_path = db_path
        # 承诺账本（2026-11-11 接进读侧）：表在同一个库里，但**不在 mem 池**——
        # 它不是记忆，不参与召回打分、不参与衰减。这里只保证表存在（幂等）。
        try:
            import promises as _pm0
            _pm0.ensure(_pm0.connect(self.db_path))
            self._promises_ok = True
        except Exception as _pe0:
            self._promises_ok = False
            soft_fail("promise_ensure", _pe0)
        # 卡目录（放 .lifecard / 快照用），不传就取库文件所在目录
        self.card_dir = card_dir or (
            os.path.dirname(os.path.abspath(db_path))
            if db_path != ":memory:" else os.getcwd())
        # check_same_thread=False：心智服务是多线程的（每个请求一个线程），
        # 而 SQLite 连接默认绑在创建它的线程上。并发安全靠外部那把锁
        # （Mind.lock / MemoryCard 自己的调用方），不是靠 SQLite 拦。
        self._db_main = sqlite3.connect(db_path, check_same_thread=False, timeout=30.0)
        self._db_main.row_factory = sqlite3.Row
        self._db_tls = {}          # 线程 ident -> 该线程自己的连接（reading() 用）
        # 🔧 并发关键（实测 2026-10-31）：卡不是只有一个进程在碰 ——
        #    mind_server（8848）、bridge、console、快照/备份脚本都可能同时开库。
        #    SQLite 默认拿不到写锁立刻抛 "database is locked"，调用方看到的是
        #    「卡坏了」，其实只是另一个进程正在写。busy_timeout 让它在锁上等，
        #    而不是当场失败；30 秒足够任何一次正常写入收尾。
        try:
            self._db_main.execute("PRAGMA busy_timeout=30000")
        except Exception:
            pass
        # 🔧 性能关键（实测 2026-10-30）：
        #    Windows 上默认 journal 模式每 commit 要 fsync 一次，
        #    单次 commit 竟然要 350ms —— 一条记忆 3 个 commit 就是 1 秒。
        #    1000 条记忆要 15 分钟，这不是数据库的问题，是磁盘同步策略的问题。
        #    WAL + synchronous=NORMAL：掉电最多丢最后一次 commit，够用。
        # 可移动盘（U 盘/移动硬盘）上不要用 WAL：exFAT 没有 WAL 需要的那套
        # 共享内存语义，拔盘或掉电最容易把库写坏。DELETE 慢一点，但拔了不出事。
        # 2026-11-07 加，起因是「U 盘就是卡」这个用法要能真跑起来。
        _journal = "WAL"
        try:
            import ctypes
            _drive = os.path.splitdrive(os.path.abspath(self.db_path))[0] + "\\"
            _dt = ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(_drive))
            if _dt in (2, 4):          # 2=可移动 4=网络
                _journal = "DELETE"
        except Exception:
            pass
        try:
            self.db.execute("PRAGMA journal_mode=%s" % _journal)
            self.db.execute("PRAGMA synchronous=NORMAL")
            self.db.execute("PRAGMA temp_store=MEMORY")
            self.db.execute("PRAGMA cache_size=-16000")
            # 🔧 2026-11-11（读写分离第 3 步）：记下**真的生效了**的那个模式。
            # journal_mode 是库的属性，设一次就够；但「设了」和「生效了」
            # 是两件事（可移动盘会退回 DELETE），所以要读回来留证据。
            self._journal_actual = str(
                self.db.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        except Exception:
            self._journal_actual = "unknown"
        # 全局依恋值（V5.0 T1-3）。持久化在 meta 表，跨会话不断。
        self.oxy = 0.0
        # 心境 M（V5.0 T2-3）：慢基底，0.5 = 不悲不喜
        self.mood = MOOD_NEUTRAL
        # SessionCache 双回流（V5.0 T1-4）：本轮对话缓冲
        self._session_turns = []
        self._session_hit_ids = set()
        self._init_schema()
        self._migrate()
        self._ensure_soft_cols()
        self._ensure_cond_table()
        # 运行时配置表（V5.0 §10.9）：场景权重能热改，不用重启服务。
        # 建表在这里而不是 ctxweight 里自己管 —— 卡的所有 schema 只在一个地方建，
        # 否则备份/恢复/老库迁移时总有一处会漏。
        ctxweight.ensure_table(self.db)
        self._load_oxy()
        self._load_mood()
        self._load_hormones()
        self._load_viscera()
        self._load_viscera_profile()
        self._load_motiv()
        try:
            impression_mod.ensure(self.db)
        except Exception as _ie:
            print("[card2] 印象层建表失败: %s" % _ie)
        try:
            facts_mod.ensure(self.db_path)
        except Exception as _fe:
            print("[card2] 事实层建表失败: %s" % _fe)
        try:
            self._ensure_hist_cols()
        except Exception:
            pass
        # 🔧 2026-11-06（V5.2 分区细分）：开关从 meta 读，**默认关**。
        #    关着的时候 weight_now() 走老路径，这张卡的行为和改之前一模一样；
        #    打开才启用「每个区自己的 λ」。
        #    开关放 meta 而不是环境变量：开关是卡的状态，应该跟着卡走，
        #    换台电脑插上还是同一个我，不该因为没设环境变量就变回旧行为。
        try:
            _row = self.db.execute(
                "SELECT val FROM meta WHERE key='region_lambda_on'").fetchone()
            _v = str(_row[0]).strip().lower() if _row else ""
            _on = 1 if _v in ("1", "true", "yes", "on") else 0
        except Exception:
            _on = 0
        global _REGION_LAMBDA_ON
        _REGION_LAMBDA_ON = bool(_on)
        try:
            if _REGION_LAMBDA_ON:
                self._load_read()
                self._load_refresh_f()
            else:
                _REGION_LOAD.clear()
                _LOAD_F.clear()
        except Exception:
            pass
        # 🔧 V5.0 T3-1 / T2-2：把技能记忆与身份锚挂到卡上。
        #    之前它们是独立模块，测试各自过、但卡自己并不知道它们存在，
        #    等于两块零件做完了没装上。这里共享同一个 sqlite 连接。
        self.skill = skill.SkillMemory(self.db)
        # 这里必须传 self.card_dir 而不是参数 card_dir：不传参时 card_dir 是
        # None，而 self.card_dir 已经退回「库文件所在目录」了。传参数会让
        # 头像/身份卡找不到落盘位置（2026-10-31 换头像时报「没有卡目录」）。
        self.identity = identity.IdentityAnchor(self.db, card_dir=self.card_dir)
        # 🔧 T4-4：突触网络（惰性建边，第一次 recall 才真正跑）
        self.synapse = synapse_mod.SynapseNet(self.db)
        # 🔧 T4-3：语义层。懒加载 —— 第一次真正要用它才读那 90MB 模型。
        #    semantic_mod 为 None 表示这台机器上连 numpy 都没有，
        #    那就一路退回字面匹配（bigram + IDF），老行为完全不变。
        self.semantic = None
        # 🔧 2026-11-01：**模型该由卡决定，不由默认值决定。**
        #    卡里存着两代向量（512 = bge-small / 768 = bge-base）。到底该用哪个
        #    模型，环境变量只是外面提的建议，卡里的向量才是事实。
        #    以前不设 WHALE_EMBED_MODEL 就一律走 small —— 一张 768 的卡会被
        #    静默当成 512 用：检索时不同维度的向量全被丢掉（长度对不上），
        #    语义层实际等于没开，而 sem_stats 还报「覆盖率 66%」，看不出毛病。
        #    这正是这个项目最不能忍的那种「看着正常、其实没在工作」。
        self.emb_dims = {}
        self.model_dim = None
        self.dim_note = ""
        self.dim_warn = None
        try:
            self.emb_dims = self._count_emb_dims()
        except Exception:
            self.emb_dims = {}
        card_dim = max(self.emb_dims, key=self.emb_dims.get) if self.emb_dims else 0
        env_model = (os.environ.get("WHALE_EMBED_MODEL") or "").strip()
        if card_dim and not env_model:
            pick = ("bge-base-zh-v1.5" if card_dim >= 768
                    else "bge-small-zh-v1.5")
            os.environ["WHALE_EMBED_MODEL"] = pick
            self.dim_note = "卡里向量是 %d 维，自动选 %s" % (card_dim, pick)
        elif card_dim:
            self.dim_note = "环境变量指定模型（%s），卡里多数是 %d 维" % (
                env_model, card_dim)
        else:
            self.dim_note = "空卡，按默认模型"
        if semantic_mod is not None:
            try:
                # 🔧 2026-10-30：模型目录以 semantic.current_model_dir() 为准
                #    （它读环境变量 WHALE_EMBED_MODEL，缺省 small）。
                #    不再拼 card_dir + MODEL_DIR_NAME —— 那样环境变量形同虚设。
                self.semantic = semantic_mod.get_layer(
                    semantic_mod.current_model_dir() if self.card_dir else None)
            except Exception:
                self.semantic = None
        # 模型维度和卡里向量维度对不上时，必须在 stats/自检里说出来
        if self.semantic is not None:
            try:
                self.model_dim = semantic_mod.current_dim()
            except Exception:
                self.model_dim = None
        self._refresh_dim_state()
        # V7 第 19 件：老卡升级上来时 emb_meta 是空的 —— 补登一次现有向量
        # 按维度的分布（legacy-dim768 之类），换模型时才看得见「混了几代」。
        try:
            self.backfill_emb_meta()
        except Exception as e:
            soft_fail("emb_meta_boot", e)
        # 🔧 MMR 重排开关：默认 0（关）——不设环境变量的行为跟以前一字不差。
        #    WHALE_MMR=0.25 打开（λ 越小越强调"别重复"，越大越贴原始相关性）。
        try:
            self.mmr_lambda = max(0.0, min(1.0, float(
                os.environ.get("WHALE_MMR") or 0.0)))
        except Exception:
            self.mmr_lambda = 0.0
        # 语义向量内存缓存（id -> np.ndarray），和 _vec_cache 一个思路
        self._emb_cache = {}
        self._sem_cache_n = -1
        # 🔧 T4-3b：片段向量缓存（mem_id -> list[np.ndarray]）
        self._detail_emb_cache = {}
        # 🔧 T4-3c：主干向量缓存（mem_id -> np.ndarray）
        self._core_vec_cache = {}

    # ---------- 内分泌四激素（V5.0 §4 / T3-2）----------
    def _load_hormones(self):
        """从 meta 表读四个激素；没有就用基线"""
        self.h = dict(hormone.H_BASE)
        try:
            rows = self.db.execute(
                "SELECT key, val FROM meta WHERE key LIKE 'h_%'").fetchall()
            for r in rows:
                k = r["key"]
                if k in hormone.H_KEYS:
                    self.h[k] = max(0.0, min(1.0, float(r["val"])))
        except Exception:
            pass

    def _save_hormones(self):
        try:
            c = self.db.cursor()
            for k in hormone.H_KEYS:
                c.execute(
                    "INSERT INTO meta(key,val) VALUES(?,?)"
                    " ON CONFLICT(key) DO UPDATE SET val=excluded.val",
                    (k, repr(self.h[k])))
            self.db.commit()
        except Exception:
            pass

    # ---------- 内分泌 + 自主神经 + 躯体（V6：快反应层）----------
    #
    # 为什么不塞进 self.h：
    #    self.h 是四个 0~1 的慢激素，已经被 PAD、召回、语气一起用着；
    #    这一层是 24 个 0~100 的变量加两个自主神经量，混进去会让
    #    所有读 self.h 的地方都得改。分开存，互不干扰。
    def _load_viscera(self):
        """从 meta 读躯体状态；没有就是刚平静下来的一具身体。"""
        self.v = viscera.base_state()
        try:
            row = self.db.execute(
                "SELECT val FROM meta WHERE key='viscera_state'").fetchone()
            if row is not None and row["val"]:
                self.v = viscera.normalize(json.loads(row["val"]))
        except Exception:
            self.v = viscera.base_state()

    def _save_viscera(self):
        try:
            c = self.db.cursor()
            c.execute(
                "INSERT INTO meta(key,val) VALUES('viscera_state',?)"
                " ON CONFLICT(key) DO UPDATE SET val=excluded.val",
                (json.dumps(self.v, ensure_ascii=False),))
            self.db.commit()
        except Exception:
            pass

    # ---------- 动机层（2026-11-07，用户 m35010 授权「主动」）----------
    # 驱力池存 meta['motiv_state']，跟记忆一起搬走 —— 换机器她不会变成另一个人。
    # 这里只算「想不想做什么」；**能不能做**由 motiv.arbitrate 否决，
    # 真正开口/动手由调用方（mind_server 的定时器 / 值守脚本）执行。
    MOTIV_KEY = "motiv_state"

    def _load_motiv(self):
        self.mo = {"d": motiv.base(), "ts": 0.0, "last_sent_ts": 0.0,
                   "sent": {}, "learned": {}, "last": None}
        try:
            row = self.db.execute(
                "SELECT val FROM meta WHERE key=?", (self.MOTIV_KEY,)).fetchone()
            if row is not None and row["val"]:
                st = json.loads(row["val"])
                self.mo["d"] = motiv.normalize(st.get("d"))
                self.mo["ts"] = float(st.get("ts") or 0.0)
                self.mo["last_sent_ts"] = float(st.get("last_sent_ts") or 0.0)
                self.mo["sent"] = st.get("sent") or {}
                self.mo["learned"] = st.get("learned") or {}
                self.mo["last"] = st.get("last")
        except Exception as e:
            print("[mind] 动机状态读取失败（用出厂驱力）：%s" % e)

    def _save_motiv(self):
        try:
            c = self.db.cursor()
            c.execute(
                "INSERT INTO meta(key,val) VALUES(?,?)"
                " ON CONFLICT(key) DO UPDATE SET val=excluded.val",
                (self.MOTIV_KEY, json.dumps(self.mo, ensure_ascii=False)))
            self.db.commit()
        except Exception:
            pass

    def _day_key(self, now):
        return time.strftime("%Y-%m-%d", time.localtime(float(now)))

    def _last_user_ts(self):
        try:
            row = self.db.execute(
                "SELECT MAX(create_ts) FROM mem WHERE speaker='user'").fetchone()
            return float((row[0] if row else 0) or 0.0)
        except Exception:
            return 0.0

    def motiv_tick(self, now=None, quiet=False, at_school=False):
        """走一轮动机：回落驱力 → 生候选 → 仲裁。返回决策（含被拒理由）。"""
        now = float(now if now is not None else time.time())
        dt_h = 0.0
        if self.mo.get("ts"):
            dt_h = max(0.0, min(72.0, (now - float(self.mo["ts"])) / 3600.0))
        self.mo["d"] = motiv.settle(self.mo["d"], dt_h)
        self.mo["ts"] = now
        # 🔧 2026-11-09：settle 只往基线靠，但基线不是 0 ——
        #    如果刚聊完，attachment/care 被满足、低于基线，settle 会把它们拉回基线，
        #    但不会**超过**基线。没有人超基线 → candidates 不产出 → 【动机】永远空。
        #    这里补一笔「想念」：离上次说话越久，依恋和关心越往上超过基线。
        #    用 itch() 往上推，幅度按空闲时长递增（1h 轻微、6h 明显、12h+ 很想）。
        _last_user = self._last_user_ts()
        if _last_user > 0:
            _idle_h = max(0.0, (now - _last_user) / 3600.0)
            if _idle_h > 0.5:
                # 超过半小时才开始「想」，刚聊完不会。
                # 目标值 = 基线 + 想念幅度，**只补差额**，不重复叠加：
                # 原来这里是 itch(d, 全额)，而 tick 从 2026-11-09 起每分钟
                # 结算一次 —— 每跳都再加一遍同样多的「想念」，五分钟就把
                # attachment 顶到 1.0，于是永远显示「非常想说话」。
                # 改成目标式之后，同一个 idle_h 无论算几次，结果都一样。
                for _k, _cap, _rate in (("attachment", 0.30, 0.025),
                                        ("care", 0.22, 0.018)):
                    _tgt = float(motiv.DRIVES[_k][0]) + min(_cap, _idle_h * _rate)
                    _cur = float(self.mo["d"].get(_k) or 0.0)
                    if _cur < _tgt:
                        self.mo["d"] = motiv.itch(self.mo["d"], _k, _tgt - _cur)
        dk = self._day_key(now)
        last_spoke = self._last_user_ts()
        ctx = {"now": now, "last_spoke_ts": last_spoke,
               "last_event_ts": last_spoke,
               "last_sent_ts": float(self.mo.get("last_sent_ts") or 0.0),
               "quiet": bool(quiet), "at_school": bool(at_school),
               "sent_today": int((self.mo.get("sent") or {}).get(dk, 0)),
               "learned_today": int((self.mo.get("learned") or {}).get(dk, 0))}
        cands = motiv.candidates(self.mo["d"], ctx)
        chosen, rejected = motiv.arbitrate(cands, ctx)
        res = {"now": now, "dt_hours": round(dt_h, 3),
               "drives": {k: round(v, 3) for k, v in self.mo["d"].items()},
               "chosen": chosen,
               "rejected": [{"kind": r.get("kind"), "reject": r.get("reject")}
                            for r in rejected],
               "line": motiv.describe(chosen, rejected),
               "ctx": {"quiet": ctx["quiet"], "at_school": ctx["at_school"],
                       "sent_today": ctx["sent_today"],
                       "learned_today": ctx["learned_today"],
                       "since_spoke_h": round(
                           max(0.0, (now - last_spoke) / 3600.0), 2)
                       if last_spoke else None}}
        self.mo["last"] = {"ts": now, "kind": (chosen or {}).get("kind"),
                           "line": res["line"]}
        self._save_motiv()
        return res

    def motiv_done(self, kind, now=None, spoke=False):
        """做完了：消耗对应驱力 + 记账（主动开口要占当天额度）。"""
        now = float(now if now is not None else time.time())
        dk = self._day_key(now)
        drive = (motiv.DESIRES.get(kind) or ("curiosity",))[0]
        self.mo["d"] = motiv.bump(self.mo["d"], drive, 0.18 if spoke else 0.12)
        if spoke:
            self.mo["last_sent_ts"] = now
            self.mo["sent"][dk] = int(self.mo.get("sent", {}).get(dk, 0)) + 1
        elif kind == "learn":
            self.mo["learned"][dk] = int(self.mo.get("learned", {}).get(dk, 0)) + 1
        # 计数只留最近 14 天，别让 meta 无限长
        keep = {self._day_key(now - i * 86400) for i in range(14)}
        self.mo["sent"] = {k: v for k, v in self.mo.get("sent", {}).items() if k in keep}
        self.mo["learned"] = {k: v for k, v in self.mo.get("learned", {}).items()
                              if k in keep}
        self._save_motiv()
        return {"drives": {k: round(v, 3) for k, v in self.mo["d"].items()}}

    def motiv_on_chat(self, now=None):
        """刚聊过：想说话的劲儿泄一点。绝不抬高任何驱力（那就成好感度了）。"""
        self.mo["d"] = motiv.bump(self.mo["d"], "attachment", 0.10)
        self.mo["d"] = motiv.bump(self.mo["d"], "care", 0.06)
        self._save_motiv()

    def motiv_snapshot(self):
        dk = self._day_key(time.time())
        return {"drives": {k: round(v, 3) for k, v in self.mo["d"].items()},
                "base": {k: v[0] for k, v in motiv.DRIVES.items()},
                "half_life_h": {k: v[1] for k, v in motiv.DRIVES.items()},
                "last": self.mo.get("last"),
                "today": {"sent": int((self.mo.get("sent") or {}).get(dk, 0)),
                          "learned": int((self.mo.get("learned") or {}).get(dk, 0)),
                          "reach_cap": motiv.REACH_DAILY_CAP,
                          "learn_cap": motiv.LEARN_DAILY_CAP}}

    # ---------- 身体参数外置（2026-11-07）----------
    # 性格在 core_zone，身体参数以前只在源码里 —— 换机器代码版本不同，
    # 同一个人就会长出不同的身体。现在参数的「家」是这张卡：
    #   meta['viscera_profile'] = {"H_BASE": {...}, "REST_SYMP": 0.10, ...}
    # 卡里没有就沿用代码默认值，老卡行为完全不变。
    def _load_viscera_profile(self):
        try:
            row = self.db.execute(
                "SELECT val FROM meta WHERE key='viscera_profile'").fetchone()
            if row is not None and row["val"]:
                ok, why = viscera.apply_profile(json.loads(row["val"]))
                if not ok:
                    print("[mind] 卡里的身体参数被拒：%s（沿用默认值）" % why)
        except Exception as e:
            print("[mind] 身体参数读取失败（沿用默认值）：%s" % e)

    def set_viscera_profile(self, patch):
        """把一组参数写进卡并立刻生效。返回 (ok, why)。

        先验证再落库：脏参数绝不进门，也不做「尽力而为」的折中。
        """
        ok, why = viscera.validate_profile(patch)
        if not ok:
            return False, why
        cur = viscera.current_profile()
        if "H_BASE" in patch:
            cur["H_BASE"].update({k: float(v) for k, v in patch["H_BASE"].items()})
            cur["H_BASE"] = {k: cur["H_BASE"][k] for k in viscera.H_BASE}
        if "HALFLIFE_H" in patch:
            cur["HALFLIFE_H"].update(
                {k: float(v) for k, v in patch["HALFLIFE_H"].items()})
            cur["HALFLIFE_H"] = {k: cur["HALFLIFE_H"][k] for k in viscera.HALFLIFE_H}
        for k in viscera.PROFILE_SCALARS:
            if k in patch:
                cur[k] = float(patch[k])
        ok, why = viscera.apply_profile(cur)
        if not ok:
            return False, why
        try:
            c = self.db.cursor()
            c.execute(
                "INSERT INTO meta(key,val) VALUES('viscera_profile',?)"
                " ON CONFLICT(key) DO UPDATE SET val=excluded.val",
                (json.dumps(cur, ensure_ascii=False),))
            self.db.commit()
        except Exception as e:
            return False, "写卡失败：%s" % e
        return True, ""

    def _emotion(self):
        """懒加载认知层（预期误差 → 调质脉冲）。挂了就是 None。

        绝不能让「情绪引擎装坏了」变成「她没有身体」——所以出错一律退 None，
        调用方退回 viscera.guess 那条笨但可靠的路。
        """
        if getattr(self, "_elink_tried", False):
            return getattr(self, "_elink", None)
        self._elink_tried = True
        self._elink = None
        try:
            import emotion_link
            link = emotion_link.EmotionLink(self.card_dir or os.getcwd(), "xiyin")
            if getattr(link, "available", False):
                self._elink = link
        except Exception:
            self._elink = None
        return self._elink

    def emotion_label(self):
        """这一轮主导感受的人话（不带数字）。没有引擎就空串。"""
        link = self._emotion()
        if not link:
            return ""
        try:
            return link.label()
        except Exception:
            return ""

    def _ensure_hist_cols(self):
        """给 viscera_hist 补列。老卡里那张表是窄的，只加不删。"""
        want = {"temp": "REAL NOT NULL DEFAULT 0",
                "tension": "REAL NOT NULL DEFAULT 0",
                "h": "TEXT", "p": "TEXT", "pulse": "TEXT",
                # V7 第 13 条：这一格是「因为什么变成这样」——
                # 没有它，曲线只能看出「变了」，看不出「被什么弄变的」。
                "why": "TEXT"}
        try:
            have = {r["name"] for r in self.db.execute(
                "PRAGMA table_info(viscera_hist)").fetchall()}
            for k, decl in want.items():
                if k not in have:
                    self.db.execute(
                        "ALTER TABLE viscera_hist ADD COLUMN %s %s" % (k, decl))
            self.db.commit()
        except Exception:
            pass

    def _hist_viscera(self, res=None, now=None, force=False):
        """给控制台留一格曲线。同一分钟里不重复写。

        只在真有事发生时写（一句话进来、手动刺激），不跑定时采样 ——
        曲线该反映「她经历了什么」，而不是「进程还活着」。
        """
        try:
            now = float(now if now is not None else time.time())
            last = float(getattr(self, "_vhist_ts", 0.0) or 0.0)
            if not last:
                row = self.db.execute(
                    "SELECT MAX(ts) AS m FROM viscera_hist").fetchone()
                last = float((row["m"] if row is not None else 0) or 0.0)
            # 5 秒一格：一段正常交流（一问一答）能画出起伏，
            # 又不至于一条消息写八行。7 天清理兜底，涨不上去。
            if now - last < (2.0 if force else 5.0):
                return
            v = getattr(self, "v", None) or viscera.base_state()
            b = viscera.body(v)
            h = v.get("h") or {}
            rpe = 0.0
            try:
                rpe = float(getattr(res, "rpe", 0.0) or 0.0) if res is not None else 0.0
            except Exception:
                rpe = 0.0
            safety, label = -1.0, ""
            link = self._emotion()
            if link:
                try:
                    safety = float(link.safe())
                    label = str(link.label(res) or "")
                except Exception:
                    pass
            # 🔧 V5.3.2：没事件的采样点原来 label 是空串 —— 控制台「主导情绪」
            #    那一栏就攒出两百多条没名字的点（看着像坏了，其实只是「这一刻
            #    没有新事情发生」）。情绪是会持续一段时间的，不是每个瞬间都重新
            #    判定一次：没新事件就沿用上一次的标签；六小时内一次都没有才算静息。
            #    只在认知层开着时这么做 —— 关了开关就该一个标签都不产生。
            if link and not label:
                try:
                    row = self.db.execute(
                        "SELECT label FROM viscera_hist WHERE label<>'' "
                        "AND ts>=? ORDER BY ts DESC LIMIT 1",
                        (now - 6 * 3600,)).fetchone()
                    label = str((row["label"] if row is not None else "") or "")
                except Exception:
                    label = ""
                if not label:
                    label = "静息"
            # 24 种激素全记：对话里不出现，后台要能一格一格看见
            _hj = {k: round(float(x), 2) for k, x in (h or {}).items()}
            # 动态参数：这一轮引擎内部到底算了什么
            _pj = {"rpe": round(float(rpe), 3)}
            try:
                for _k in ("v_actual", "v_expect", "gate", "fatigue",
                           "safety", "turn"):
                    _x = getattr(res, _k, None) if res is not None else None
                    if _x is not None:
                        _pj[_k] = round(float(_x), 3)
                _pj["dominant"] = str(getattr(res, "dominant", "") or "") \
                    if res is not None else ""
                _pj["scenario"] = str(getattr(res, "scenario", "") or "") \
                    if res is not None else ""
                _st = getattr(res, "state", None) if res is not None else None
                if isinstance(_st, dict):
                    _pj["mods"] = {k: round(float(x), 3)
                                   for k, x in _st.items()}
                _pl = getattr(res, "pulsed", None) if res is not None else None
                if isinstance(_pl, dict):
                    _pj["pulse"] = {k: round(float(x), 4)
                                    for k, x in _pl.items()}
            except Exception:
                pass
            _link2 = self._emotion()
            if _link2 and getattr(_link2, "eng", None):
                try:
                    _sn = _link2.eng.snapshot()
                    _pj["mood"] = _sn.get("mood")
                    _pj["expect_global"] = _sn.get("expect_global")
                    _pj["expect"] = _sn.get("expect")
                    _pj["base"] = _sn.get("base")
                except Exception:
                    pass
            c = self.db.cursor()
            c.execute(
                "INSERT INTO viscera_hist(ts,hr,resp,face,sym,para,dopa,nora,sero,"
                "cort,oxy,rpe,safety,label,temp,tension,h,p,pulse,why) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (now, float(b.get("hr", 0)), float(b.get("resp", 0)),
                 float(b.get("face", 0)), float(v.get("symp", 0)),
                 float(v.get("para", 0)), float(h.get("dopa", 0)),
                 float(h.get("nora", 0)), float(h.get("sero", 0)),
                 float(h.get("cort", 0)), float(h.get("oxy", 0)),
                 rpe, safety, label,
                 float(b.get("temp", 0)), float(b.get("tension", 0)),
                 _json.dumps(_hj, ensure_ascii=False) if _hj else None,
                 _json.dumps(_pj, ensure_ascii=False) if _pj else None,
                 _json.dumps(_pj.get("pulse") or {}, ensure_ascii=False),
                 str(getattr(self, "_vh_why", "") or "")[:120]))
            # 只留 7 天，老点删掉（曲线画不了那么长，账也不想无限长）
            c.execute("DELETE FROM viscera_hist WHERE ts < ?", (now - 7 * 86400.0,))
            self.db.commit()
            self._vhist_ts = now
        except Exception:
            pass

    def viscera_sample(self, now=None, force=True):
        self._vh_why = "idle"
        """不喂新话，只让身体按时间自己走一格，然后记一个点。

        为什么需要它：控制台「实时」档每 3 秒刷一次，但**没有新对话就没有
        新样本** —— 曲线当然不动。监视器采的是「此刻」，不是「事件」。
        这里只做 settle（衰减回落），不注入任何刺激：没人说话的时候，
        身体本来就在慢慢回到基线，这才是真的。
        """
        try:
            now = float(now if now is not None else time.time())
            self.v = viscera.normalize(self.v or viscera.base_state())
            dt = now - float(self.v.get("ts") or now)
            if dt > 0:
                self.v = viscera.restless(viscera.settle(self.v, dt))
            self.v["ts"] = now
            self._save_viscera()
            self._hist_viscera(self._last_emotion_res(), now, force=force)
            return self.v
        except Exception:
            return self.v

    class _CarriedRes(object):
        """把上一次真实计算的参数复原成「带属性的结果对象」。

        为什么需要它：`_hist_viscera()` 是用 `getattr(res, "rpe")` 这种方式
        读参数的，而采样点传进来的是 dict —— 于是采样点永远写成
        rpe=0、v_actual=None……控制台上就表现为「预期误差和安全感变成直线、
        PARAMS 卡全是零」。不是算错了，是**读法不匹配**。
        没有新事件时沿用上一次的读数，比硬写 0 诚实。
        """

        def __init__(self, p=None, pulse=None, rpe=None):
            p = p or {}
            for k in ("v_actual", "v_expect", "gate", "fatigue",
                      "safety", "turn", "dominant"):
                setattr(self, k, p.get(k))
            self.rpe = p.get("rpe", 0.0) if rpe is None else rpe
            self.params = p
            self.pulse = pulse or {}
            self.carried = True

    def _last_emotion_res(self):
        """采样点不该让 PARAMS 卡变成一排「—」：沿用最近一次真实计算的结果。"""
        try:
            self._ensure_hist_cols()
            # 先找「真正跑过一轮引擎」的行（里面才有 gate/fatigue/v_actual），
            # 采样行只有 rpe/dominant/mood —— 拿那些行会让 PARAMS 卡全是零。
            r = self.db.execute(
                "SELECT p, pulse FROM viscera_hist WHERE p IS NOT NULL "
                "AND p != '' AND p LIKE '%\"gate\"%' "
                "ORDER BY ts DESC LIMIT 1").fetchone()
            if not r:
                r = self.db.execute(
                    "SELECT p, pulse FROM viscera_hist WHERE p IS NOT NULL "
                    "AND p != '' ORDER BY ts DESC LIMIT 1").fetchone()
            if not r:
                return None
            import json as _json
            p = _json.loads(r["p"]) if r["p"] else {}
            pulse = _json.loads(r["pulse"]) if r["pulse"] else {}
            if not p:
                return None
            return self._CarriedRes(p, pulse)
        except Exception:
            return None

    def viscera_history(self, hours=24.0, limit=600):
        """控制台用：最近 N 小时的身体曲线。空表就是空数组，不补假数据。"""
        out = []
        try:
            since = float(time.time()) - float(hours) * 3600.0
            self._ensure_hist_cols()
            rows = self.db.execute(
                "SELECT ts,hr,resp,face,sym,para,dopa,nora,sero,cort,oxy,"
                "rpe,safety,label,temp,tension,h,p,pulse FROM viscera_hist "
                "WHERE ts >= ? ORDER BY ts ASC LIMIT ?",
                (since, int(limit))).fetchall()
            for r in rows:
                out.append({
                    "ts": round(float(r["ts"]), 1),
                    "hr": round(float(r["hr"]), 1),
                    "resp": round(float(r["resp"]), 1),
                    "face": round(float(r["face"]), 3),
                    "sym": round(float(r["sym"]), 3),
                    "para": round(float(r["para"]), 3),
                    "dopa": round(float(r["dopa"]), 2),
                    "nora": round(float(r["nora"]), 2),
                    "sero": round(float(r["sero"]), 2),
                    "cort": round(float(r["cort"]), 2),
                    "oxy": round(float(r["oxy"]), 2),
                    "rpe": round(float(r["rpe"]), 3),
                    "safety": round(float(r["safety"]), 3),
                    "label": str(r["label"] or ""),
                    "temp": round(float(r["temp"] or 0), 2),
                    "tension": round(float(r["tension"] or 0), 3),
                    "hormones": _safe_json(r["h"]),
                    "params": _safe_json(r["p"]),
                    "pulse": _safe_json(r["pulse"]),
                })
        except Exception:
            return []
        return out

    def viscera_touch(self, text="", now=None, emotion=0.0):
        # V7 第 13 条：记下「这一格是被哪句话弄出来的」。控制台里点某个点就能看到原因。
        self._vh_why = ("talk:" + (text or ""))[:120]
        """一句话进来：先让身体反应，再让激素跟上，最后存回去。

        顺序就是人真实的顺序 —— 不是「我想了想觉得被冒犯了」，
        是「心里一沉/心跳快了一下」，然后才「有点生气」。

        两条路：有认知层就走「价值打分 → RPE → 五条调质脉冲 → 灌激素」，
        没有就退回从话里猜刺激（viscera.guess）。两条路都过一遍时间衰减。
        """
        # 墙上时间：这层是「身体」，不是「记忆」，不该被记忆时钟卡住
        now = float(now if now is not None else time.time())
        v = getattr(self, "v", None) or viscera.base_state()
        try:
            last = float(v.get("ts") or 0.0)
        except Exception:
            last = 0.0
        # 时间戳为 0（第一次）时按 0 小时算，别一口气衰减一年
        dt = (now - last) / 3600.0 if last > 0 else 0.0
        dt = max(0.0, min(72.0, dt))
        link = self._emotion()
        res = None
        if link:
            try:
                res = link.step(text or "")
            except Exception:
                res = None
        if res is not None:
            self.v = link.apply_to(v, res, dt, now=now)
        else:
            events = viscera.guess(text, emotion)
            self.v = viscera.step(v, dt, events)
        self.v["ts"] = now
        self._save_viscera()
        self._hist_viscera(res, now)
        return self.v

    def viscera_stimulate(self, kinds, strength=1.0, now=None):
        """给身体直接来一记刺激（不经过文本猜测）。

        用途：控制台手动戳一下、测试、以及将来「她看见一张图」这类
        没有文本可猜的输入。
        """
        now = float(now if now is not None else self.clock.now())
        v = getattr(self, "v", None) or viscera.base_state()
        try:
            last = float(v.get("ts") or 0.0)
        except Exception:
            last = 0.0
        dt = (now - last) / 3600.0 if last > 0 else 0.0
        # V7 第 13 条：手动刺激也要留原因（控制台里看得出是谁戳的）
        self._vh_why = "stim:" + ",".join(str(k) for k in (kinds or []))[:60]
        dt = max(0.0, min(72.0, dt))
        if isinstance(kinds, str):
            kinds = [kinds]
        kinds = list(kinds or ["chat"])
        evs = [(str(k), float(strength)) for k in kinds]
        # 有认知层就让它算这一记刺激「比预期好还是差」—— 两个端点各说各的
        # （心率在爬、情绪写着「安稳」）比没有曲线更糟。
        res = None
        link = self._emotion()
        if link and getattr(link, "available", False):
            try:
                from emotion_link import STIM_SCENARIO, STIM_TEXT
                _k = str(kinds[0])
                res = link.step(STIM_TEXT.get(_k, ""),
                                scenario=STIM_SCENARIO.get(_k, "generic"))
            except Exception:
                res = None
        if res is not None:
            self.v = link.apply_to(v, res, dt, now)
        else:
            self.v = viscera.step(v, dt, evs)
        self.v["ts"] = now
        self._save_viscera()
        self._hist_viscera(res, now)
        return self.v

    def viscera_line(self):
        """给上下文的一句「身体感觉」。没有数字，只有感觉。"""
        try:
            v = getattr(self, "v", None) or viscera.base_state()
            return viscera.describe(v)
        except Exception:
            return ""

    def viscera_snapshot(self):
        """控制台/接口用：完整数字。"""
        try:
            v = getattr(self, "v", None) or viscera.base_state()
            snap = viscera.snapshot(v)
        except Exception:
            return {}
        try:
            link = self._emotion()
            if link:
                snap["emotion"] = link.snapshot()
        except Exception:
            pass
        return snap

    def log_hormones(self, now=None, min_interval=None):
        """采一条激素样本（默认 2 分钟一次），给控制台画趋势。

        为什么单独一张表而不是塞进 meta：meta 只能存「现在是多少」，
        画不出一条线。趋势要看的是「刚才那阵子怎么变的」。

        🔧 2026-11-10：间隔可配（`WHALE_HLOG_SEC`），默认 120 秒。
        原来 300 秒且只在有请求时落盘 —— 一天下来只有 72 个点，
        关掉服务的那一夜整段空白，控制台上看就是「曲线突然不动了」。
        现在由采样线程每 120 秒写一次，跟有没有人说话无关。
        """
        now = float(now if now is not None else self.clock.now())
        if min_interval is None:
            try:
                min_interval = float(os.environ.get("WHALE_HLOG_SEC", "120"))
            except Exception:
                min_interval = 120.0
        try:
            r = self.db.execute(
                "SELECT ts FROM h_log ORDER BY id DESC LIMIT 1").fetchone()
            if r is not None and now - float(r["ts"]) < min_interval:
                return False
            h = self.h
            c = self.db.cursor()
            c.execute(
                "INSERT INTO h_log(ts,sero,dopa,cort,oxy,mood,oxy_g)"
                " VALUES(?,?,?,?,?,?,?)",
                (now,
                 float(h.get("h_sero", 0.0)), float(h.get("h_dopa", 0.0)),
                 float(h.get("h_cort", 0.0)), float(h.get("h_oxy", 0.0)),
                 float(getattr(self, "mood", 0.5)),
                 float(getattr(self, "oxy", 0.0))))
            # 只留最近 30 天，防止无限长
            c.execute("DELETE FROM h_log WHERE ts < ?", (now - 30 * DAY,))
            self.db.commit()
            return True
        except Exception:
            return False

    def mood_pad(self):
        """情绪的三维读数：P 愉悦 / A 唤醒 / D 掌控，全部映射到 −1..1。

        🔧 2026-11-01 第二版。第一版是「拿四激素换三个 0~1 的数」，问题有两个：
           ① 它是激素的复印件 —— 跟心境（mood）并列，等于两套情绪读数；
           ② 基线状态读出来是 0.6/0.4/0.6 这种"看着挺高"的数，没有零点。
        现在改成：**P 由心境给，A/D 由激素给，相对基线取偏移**。
           基线状态（mood=0.5 + 四激素在 H_BASE）读出来正好是 p=a=d=0 = 平静。
           于是「比平时低」才读得出来 —— 这才是三维比一维多出来的信息：
           低落（P 负）分得清是"平静地低落"还是"紧绷着低落"（A 的差）。

        ⚠️ 这是一个**纯读法**：不写库、不改激素、不进召回打分。
           算式在 padlib.py（纯函数），这里是转发；控制台画历史曲线也用同一份。
        """
        # 2026-11-07：旧版读的是 V3.7 那四个 0~1 的激素（self.h），
        # 而身体层（viscera 24 激素）是另一本账 —— 结果三维卡说「坐立不安」、
        # 心境说「还行」，两个读数各说各的。三维改读身体那一套，同源才可比。
        mood = getattr(self, "mood", MOOD_NEUTRAL)
        v = getattr(self, "v", {}) or {}
        hh = v.get("h") or {}
        pad_body = None
        if hh and "dopa" in hh:
            base = viscera.H_BASE
            # 换算成 padlib 自己的刻度：**在身体基线处必须正好等于 padlib 的
            # 基线**（dopa .40 / cort .20 / oxy .50 / sero .60），否则基线不算
            # 「平静」，一点点偏移就被 3.2 倍放大成「坐立不安」。
            PADB = {"dopa": 0.40, "cort": 0.20, "oxy": 0.50, "sero": 0.60}

            def _nz(k):
                b = float(base.get(k) or 1.0)
                return max(0.0, min(1.0, PADB[k] * float(hh.get(k, b)) / b))

            pad_body = padlib.pad_of(mood, _nz("sero"), _nz("dopa"),
                                     _nz("cort"), _nz("oxy"))
        # 2026-11-08：身体层（24 激素）动得慢，它自己那本账常年贴着基线，
        # 于是三维卡永远读「平静」，情绪传不到嘴上。四激素那本账是**每句话**
        # 都在推的（touch_hormones），两本各算一遍再取平均 —— 一个给底噪，
        # 一个给今天真被什么戳到了。
        h = getattr(self, "h", {}) or {}
        pad_h = padlib.pad_of(mood, h.get("h_sero"), h.get("h_dopa"),
                              h.get("h_cort"), h.get("h_oxy"))
        if pad_body is None:
            return pad_h
        p = round(max(-1.0, min(1.0, 0.5 * (pad_body["p"] + pad_h["p"]))), 3)
        a = round(max(-1.0, min(1.0, 0.5 * (pad_body["a"] + pad_h["a"]))), 3)
        d = round(max(-1.0, min(1.0, 0.5 * (pad_body["d"] + pad_h["d"]))), 3)
        return {"p": p, "a": a, "d": d, "label": padlib.pad_label(p, a),
                "src": {"mood": round(mood, 3),
                        "sero": pad_h["src"]["sero"], "dopa": pad_h["src"]["dopa"],
                        "cort": pad_h["src"]["cort"], "oxy": pad_h["src"]["oxy"],
                        "body_dopa": pad_body["src"]["dopa"]}}

    def touch_hormones(self, text):
        """一句话扰动四个激素（V5.0 §4.3），返回这次判定的事件类型"""
        # 🔧 2026-11-09：先让激素朝基线回一点，再受这一句的扰动。
        #    原来只有 apply_event / tone_nudge 在往上推，`hormone.regress()`
        #    定义了却**从来没有被调用过** —— 于是多巴胺一路顶到 0.98 卡在天花板上，
        #    语气提示永远是同一句「劲头足」，看着就是「情绪一成不变」。
        #    稳态回归本来就是公式的一部分（h(t+dt)=h_base+(h-h_base)e^{-λdt}），
        #    这里按「距上一次扰动过去了多久」补上这一步，λ 用 0.06/小时
        #    （半衰期约 11.6 小时：一场好觉能淡掉，同一个下午不会自己掉光）。
        try:
            _now = time.time()
            _dt_h = max(0.0, _now - float(getattr(self, "_h_ts", _now))) / 3600.0
            if 0.0 < _dt_h <= 24.0 * 7:
                _base = {k: hormone.H_BASE.get(k, 0.5) for k in hormone.H_KEYS}
                self.h = hormone.regress_all(self.h, _dt_h, _base)
            self._h_ts = _now
        except Exception:
            pass
        kind, s = hormone.classify_event(text)
        before = dict(self.h)
        if kind is not None:
            self.h = hormone.apply_event(self.h, kind, s)
        # 平时那些不咸不淡的话也要推一点点，否则日子过得再热闹，读数也不动
        self.h = hormone.tone_nudge(self.h, text)
        if kind is not None:
            # 记一下「刚才被什么戳到」——两小时内这句话还会挂在他嘴边
            self._last_event = (kind, time.time(), s)
        if self.h != before:
            self._save_hormones()
        return (kind, s)

    def _load_mood(self):
        try:
            row = self.db.execute(
                "SELECT val FROM meta WHERE key='mood_global'").fetchone()
            if row is not None:
                self.mood = max(0.0, min(1.0, float(row["val"])))
        except Exception:
            self.mood = MOOD_NEUTRAL

    def _save_mood(self):
        try:
            self.db.execute(
                "INSERT INTO meta(key,val) VALUES('mood_global',?)"
                " ON CONFLICT(key) DO UPDATE SET val=excluded.val",
                (repr(self.mood),))
            self.db.commit()
        except Exception:
            pass

    # ---------- 醒来（V5.0 §11.4 / 2026-10-31 新增）----------
    #
    # 为什么要有这个 —— 实测出来的两个 bug，不是猜的：
    #
    # ① 两套时钟各走各的。
    #    记忆主干走 weight_now(真实 now)，拔卡 5 天回来 w 从 1.0 掉到 0.047；
    #    细节走 tick(dt)，而 tick 的 dt 从**服务启动**才开始算
    #    （mind_server.py 里 last_tick_ts = time.time()），
    #    所以拔卡那段时间细节是冻着的 —— 实测 tick(1.0s) 后细节权重一动不动。
    #    主干以为时间在跑，细节以为时间停了。
    #
    # ② 离线时间从来没被压缩过。
    #    compress_offline_dt() 早就写好了（7 天内如实、7~30 天超出部分减半、
    #    ≥30 天 ×0.15、上限 90 天），但它的注释说得很清楚是「离线太久不能等比
    #    衰减，否则睡一觉就失忆」—— 而它只作用在 tick 的 dt 上。
    #    主干根本没经过它。
    #
    # 修法：醒来时把「离线那段」算清楚，然后两件事一起做 ——
    #    a) 把每条的 t_anchor 往前推 shift = 真实离线 − 有效离线。
    #       这样 weight_now 用真实 now 算出来的 age 恰好等于「有效年龄」。
    #       没有改任何衰减公式，也没有迁移数据，老库原样能用。
    #    b) 细节按「有效离线」补一次衰减，跟主干对齐。
    #   两边的有效时间就都是 eff 了，不再各走各的。
    #
    # 策略是**一个常量**（OFFLINE_POLICY）：
    #    "compress"（默认，原设计）离线照算，只是压缩；
    #    "freeze"   离线完全不算 —— 拔卡就是睡觉，醒了还是昨天那个我。
    #    改这一行就能换，不用动别处。
    def _meta_float(self, key):
        try:
            row = self.db.execute(
                "SELECT val FROM meta WHERE key=?", (key,)).fetchone()
            if row is not None and row["val"] is not None:
                return float(row["val"])
        except Exception:
            pass
        return None

    def _set_meta_float(self, key, val):
        try:
            self.db.execute(
                "INSERT INTO meta(key,val) VALUES(?,?)"
                " ON CONFLICT(key) DO UPDATE SET val=excluded.val",
                (key, repr(float(val))))
            self.db.commit()
        except Exception:
            pass

    def wake_up(self, now=None, policy=None):
        """插上卡、服务起来：把离线那段结算清楚，返回这次睡了多久。

        必须在开始处理请求**之前**调用一次（mind_server 的 Mind.__init__ 里调）。
        """
        now = float(now if now is not None else self.clock.now())
        pol = policy or OFFLINE_POLICY
        last = self._meta_float("last_alive_ts")
        self._set_meta_float("last_alive_ts", now)
        info = {"gap_s": 0.0, "eff_s": 0.0, "shift_s": 0.0,
                "policy": pol, "details_decayed": 0, "details_dropped": 0,
                "skipped": ""}
        if last is None:
            info["skipped"] = "首次启动，没有上次活着的时间"
            return info
        gap = max(0.0, now - last)
        info["gap_s"] = gap
        if gap < 60.0:
            info["skipped"] = "间隔不到 60 秒，当连续"
            return info

        eff = offline_effective(gap, pol)
        shift = gap - eff
        info["eff_s"] = eff
        info["shift_s"] = shift

        c = self.db.cursor()
        # a) 锚点整体前移：让 weight_now 的 age 少算 shift
        #    但**不许推到未来**。原来直接 t_anchor+shift：如果一条记忆是在睡前
        #    刚写下的，锚会跑到 now 之后好几十小时（实测 94 条被推到 +63.9h），
        #    于是 age 变成负数、这些记忆这几天一点都不衰减 —— 冻结变成了"长生"。
        #    夹到 now 为止：冻结的是"那段睡眠"，不是"这条记忆的寿命"。
        if shift > 0.5:
            c.execute("UPDATE mem SET t_anchor = MIN(t_anchor + ?, ?)", (shift, now))
        # b) 细节补衰减（跟 tick 里第 3 段同一套 k 和 scale）
        if eff > 0.5:
            rows = c.execute(
                "SELECT d.id, d.kind, d.weight, m.pool FROM detail d"
                " JOIN mem m ON m.id=d.parent_id").fetchall()
            for d in rows:
                k = K_DETAIL_BY_KIND.get(d["kind"], 4.0e-6)
                scale = {POOL_SHORT: 1.0, POOL_MEDIUM: 0.25,
                         POOL_LONG: 0.03}.get(d["pool"], 1.0)
                nw = d["weight"] * math.exp(-k * scale * eff)
                if nw < KEEP_DETAIL:
                    c.execute("DELETE FROM detail WHERE id=?", (d["id"],))
                    info["details_dropped"] += 1
                else:
                    c.execute("UPDATE detail SET weight=? WHERE id=?",
                              (nw, d["id"]))
                    info["details_decayed"] += 1
        self.db.commit()
        return info

    def offline_event(self, gap_s, start_ts, end_ts, policy=None):
        """把「我离线了多久」写成一条真记忆（V5.0 §11.3）。

        为什么必须写进 mem 而不是只记在时间线里：
            时间线的 sleep_start / sleep_end 是**给人看**的证据，不参与召回。
            也就是说她「档案里知道自己睡过」，但下次你说话时她想不起来这件事 ——
            那正是最像失忆的状态：有记录，没有回忆。

        为什么要标「推断」：
            离线期间没有任何人观测，起点终点是从 last_alive_ts 和当前时间反推的。
            写死成事实，等于以后她拿一句没把握的话当真话讲。

        策略是 freeze 时补一句「不算遗忘」：否则「我睡了 3 天」听起来像
            「我老了 3 天」，而按用户的设计，拔卡这段时间本来就不该老。
        """
        pol = policy or OFFLINE_POLICY
        gap = max(0.0, float(gap_s))
        days = gap / DAY
        def _fmt(ts):
            try:
                return _dt.datetime.fromtimestamp(float(ts)).strftime("%m月%d日 %H:%M")
            except Exception:
                return "?"
        text = "我离线了 %.1f 天（推断），从 %s 到 %s" % (
            days, _fmt(start_ts), _fmt(end_ts))
        if pol == "freeze":
            text += "。这段时间是睡着，不算遗忘"
        else:
            text += "。这段时间照常衰减，只是超长的部分打了折"
        try:
            return self.remember(
                text, salience=0.55, ts=end_ts, pool=POOL_SHORT,
                oxy=0.0, entities=0, emotion=0.30,
                normalize=False,          # 日期已经写成绝对的了，别再归一化
                region=R_EPISODE, speaker="self", mtype="episode")
        except Exception:
            return None

    def touch_alive(self, now=None, min_interval=60.0):
        """每隔一会儿记一笔「我还活着」。

        拔卡那一刻进程是被直接杀掉的，来不及走收尾流程，
        所以「上次活着」这个时间戳必须平时就一点点写下去，
        不能等到退出时才写。
        """
        now = float(now if now is not None else self.clock.now())
        last = self._meta_float("last_alive_ts")
        if last is None or (now - last) >= min_interval:
            self._set_meta_float("last_alive_ts", now)
            return True
        return False

    def touch_mood(self, text, emotion=0.25):
        """一次事件推一下心境（慢，不会一句话翻脸）

        🔧 关键补充：同一天里重复同向事件要**递减**。
        真人被夸第一次心里一动，被夸第六次就没感觉了。
        不递减的话，一天里说六句话就能把心情从谷底拉到天上 —— 那不是人，是开关。
        """
        e = mood_of_event(text, emotion)
        # 折扣窗口从「一整天」改成「半小时」：真人连着被夸三次也会一次比一次淡，
        # 但隔两小时再被夸，反应是新的。按天算的话，早上说一句好话，晚上再说什么都被砍到 6%。
        now = self.clock.now()
        try:
            _seq = [t for t in (getattr(self, "_mood_seq", None) or [])
                    if now - float(t) < 1800.0]
        except Exception:
            _seq = []
        n = len(_seq)
        _seq.append(now)
        self._mood_seq = _seq[-12:]
        neutral = (e == MOOD_NEUTRAL)
        k = K_MOOD_NEUTRAL if neutral else (K_MOOD / (1.0 + 0.5 * n))
        before = self.mood
        self.mood = update_mood(self.mood, e, k=k)
        # 软限：一次会话里再怎么推，也别从"低落"直接跳到"特别高兴"
        if MOOD_SPAN > 0:
            self.mood = MOOD_NEUTRAL + max(-MOOD_SPAN, min(MOOD_SPAN, self.mood - MOOD_NEUTRAL))
        if abs(self.mood - before) > 1e-6:
            self._save_mood()
        return self.mood

    def mood_label(self):
        return mood_text(self.mood)

    def _load_oxy(self):
        try:
            row = self.db.execute(
                "SELECT val FROM meta WHERE key='oxy_global'").fetchone()
            if row is not None:
                self.oxy = max(0.0, min(1.0, float(row["val"])))
        except Exception:
            self.oxy = 0.0

    def _save_oxy(self):
        try:
            self.db.execute(
                "INSERT INTO meta(key,val) VALUES('oxy_global',?)"
                " ON CONFLICT(key) DO UPDATE SET val=excluded.val",
                (repr(self.oxy),))
            self.db.commit()
        except Exception:
            pass

    def _init_schema(self):
        c = self.db.cursor()
        c.execute("""
        CREATE TABLE IF NOT EXISTS mem (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            raw            TEXT NOT NULL,
            core           TEXT NOT NULL,
            vec            TEXT NOT NULL,
            pool           TEXT NOT NULL DEFAULT 'short',
            w              REAL NOT NULL DEFAULT 0.5,   -- 锚点权重
            t_anchor       REAL NOT NULL,               -- 锚点时间
            salience       REAL NOT NULL,
            access_count   INTEGER NOT NULL DEFAULT 0,
            k              REAL NOT NULL DEFAULT 0.0,   -- 兼容旧字段
            oxy            REAL NOT NULL DEFAULT 0.0,
            entities       INTEGER NOT NULL DEFAULT 0,
            emotion        REAL NOT NULL DEFAULT 0.0,
            create_ts      REAL NOT NULL,
            last_active_ts REAL NOT NULL,
            superseded_by  INTEGER,
            region         TEXT NOT NULL DEFAULT 'episode',
            pod            INTEGER NOT NULL DEFAULT -1,
            subj           TEXT NOT NULL DEFAULT '',   -- 主语（T2-1）
            verb           TEXT NOT NULL DEFAULT '',   -- 动词（T2-1）
            obj            TEXT NOT NULL DEFAULT '',   -- 宾语（T2-1）
            erase_stage    INTEGER NOT NULL DEFAULT 0,  -- 侵蚀阶段 0/1/2（T2-1）
            -- V7 第 9 件：事发时间 ≠ 写入时间。
            --   create_ts 是「这句话被记下的时刻」，event_ts 是「这件事发生的时刻」。
            --   老库靠 ALTER 迁移，这里只是新库的默认值。
            event_ts       REAL NOT NULL DEFAULT 0,
            event_precision TEXT NOT NULL DEFAULT '',   -- time / day / ''（未知）
            emb            BLOB                         -- 语义向量 512×float32（T4-3）
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS detail (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_id  INTEGER NOT NULL,
            kind       TEXT NOT NULL,
            text       TEXT NOT NULL,
            weight     REAL NOT NULL,
            last_touch REAL NOT NULL,
            emb        BLOB    -- 这一段自己的语义向量（T4-3b）
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            val TEXT
        )""")
        # 🔧 T4-3c：主干向量缓存表。主干只有一条，但每条查询都要用，
        #    现场编码太慢（385 条 × 24M 模型 = 一次查询 19 秒），
        #    所以编一次就落库，之后只读 2KB。hash 用来在 core 改动时失效。
        c.execute("""
        CREATE TABLE IF NOT EXISTS core_vec (
            mem_id INTEGER PRIMARY KEY,
            hash   TEXT NOT NULL,
            blob   BLOB NOT NULL
        )""")
        # V5.0 公理 6：遗忘与联想解耦
        # recall() 只写事件，不改 mem；由 tick() 统一结算。
        c.execute("""
        CREATE TABLE IF NOT EXISTS recall_event (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            mem_id     INTEGER NOT NULL,
            ts         REAL NOT NULL,
            query_hash TEXT,
            rank       INTEGER NOT NULL DEFAULT 0,
            source     TEXT NOT NULL DEFAULT 'recall',
            settled    INTEGER NOT NULL DEFAULT 0
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS ix_detail ON detail(parent_id)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_ev_unsettled"
                  " ON recall_event(settled, mem_id)")
        # V5.0 §12.5：TRACE 是数学上的最终出口 —— 没有它 DEEP 会无限增长，系统不有界。
        # 不存内容，只存「某天、某个分区、发生过几条」的统计痕迹（约 50 字节/行）。
        c.execute("""
        CREATE TABLE IF NOT EXISTS trace (
            id     INTEGER PRIMARY KEY AUTOINCREMENT,
            day    TEXT NOT NULL,
            region TEXT NOT NULL DEFAULT 'episode',
            count  INTEGER NOT NULL DEFAULT 1,
            ts     REAL NOT NULL
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS ix_trace_day ON trace(day)")
        # T5-2：原文存档。记忆本身是「提炼过的」（主干 + 细节 + 权重），
        # 但有时候要能翻回「当时那句话原样是怎么说的」——
        # 所以原文单独存一张表，不参与向量检索（原文进向量库只会让召回变脏）。
        c.execute("""
        CREATE TABLE IF NOT EXISTS dialogue (
            id     INTEGER PRIMARY KEY AUTOINCREMENT,
            mem_id INTEGER NOT NULL,
            ts     REAL NOT NULL,
            role   TEXT NOT NULL DEFAULT 'user',
            text   TEXT NOT NULL,
            source TEXT
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS ix_dlg_mem ON dialogue(mem_id)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_dlg_ts ON dialogue(ts)")

        # 记忆删除台账（2026-11-01）：记忆会正常消失（侵蚀到头/没上榜/沉成统计），
        # 但出生证留在库里。没有台账，校验只能看见「有证没记录」，
        # 分不清是她自己忘了，还是有人把记录抽走了。
        # 🗄 冷档（2026-11-09，用户 m10457：「每个池子的大小是没有限制的，256G 够存很久」）
        #    从这一版起，记忆**不再真的被删掉**：任何一次「忘掉」都会先把整行 + 全部细节
        #    抄进这张表，再从 mem 里拿掉。mem 里没有它 = 她平时想不起来；
        #    mem_archive 里有它 = 硬盘上从来没丢过，问得够具体时还能翻出来。
        #    以前 407 条 tomb 只有「她在什么时候忘了什么」的收据，内容是真的没了。
        c.execute("""
        CREATE TABLE IF NOT EXISTS mem_archive (
            mid           INTEGER PRIMARY KEY,
            raw            TEXT NOT NULL DEFAULT '',
            core           TEXT NOT NULL DEFAULT '',
            speaker        TEXT NOT NULL DEFAULT '',
            region         TEXT NOT NULL DEFAULT '',
            pool           TEXT NOT NULL DEFAULT '',
            create_ts      REAL NOT NULL DEFAULT 0,
            last_active_ts REAL NOT NULL DEFAULT 0,
            salience       REAL NOT NULL DEFAULT 0,
            subj           TEXT NOT NULL DEFAULT '',
            verb           TEXT NOT NULL DEFAULT '',
            obj            TEXT NOT NULL DEFAULT '',
            entities       TEXT NOT NULL DEFAULT '',
            about_uid      TEXT NOT NULL DEFAULT '',
            emb            BLOB,
            details        TEXT NOT NULL DEFAULT '[]',
            reason         TEXT NOT NULL DEFAULT '',
            archived_ts    REAL NOT NULL DEFAULT 0
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_arch_ts ON mem_archive(create_ts)")

        # 📓 工作日志（2026-11-10，用户 m10983：「换了窗口还记得刚刚做了什么、怎么做的」）
        #    为什么要有这张表：卡记的是「我们之间发生过什么」，记不住「我上一轮干到哪一行」。
        #    每一轮我是被重新叫起来的，DSH 会话一换，我桌上那堆「跑过哪条命令、哪个方案刚
        #    被否掉、下一步该改哪个文件」就全没了 —— 人不会这样，因为人的桌子不会因为换了
        #    个房间就清空。这张表就是那张桌子：做完一段活写一条，装配上下文时取最近几条。
        #    它不是记忆（不进 mem、不参与召回/衰减），是笔记本；写坏了最多丢一页便签。
        c.execute("""
        CREATE TABLE IF NOT EXISTS worklog (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            ts        REAL NOT NULL DEFAULT 0,
            kind      TEXT NOT NULL DEFAULT 'work',  -- work / bug / decision / note
            title     TEXT NOT NULL DEFAULT '',      -- 一句话：干了什么
            body      TEXT NOT NULL DEFAULT '',      -- 怎么干的 / 看到什么数
            next_step TEXT NOT NULL DEFAULT '',      -- 下一步
            source    TEXT NOT NULL DEFAULT ''
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_wl_ts ON worklog(ts)")

        # V7 第 8 件：召回 trace。
        # 为什么要有它：以前每次记忆没被想起来，只能靠猜是「根本没存」「被时间窗筛了」
        # 「排到第 20 名外」还是「进了上下文但模型没用」。这四种修法完全不同，
        # 混在一起就只能在权重上调来调去。这里把每次召回的漏斗存下来：
        # 全库多少条 → 时间窗剩多少 → 分舱剩多少 → 自己话筛选剩多少 →
        # 打分前几名（带分项分）→ 最后端进上下文的是哪几个 id。
        # 它**不是记忆**：不进 mem 池、不参与召回和衰减，只留最近若干条给排错看。
        c.execute("""
        CREATE TABLE IF NOT EXISTS recall_trace (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            ts       REAL NOT NULL DEFAULT 0,
            query    TEXT NOT NULL DEFAULT '',
            flags    TEXT NOT NULL DEFAULT '',
            stages   TEXT NOT NULL DEFAULT '',
            top      TEXT NOT NULL DEFAULT '',
            picked   TEXT NOT NULL DEFAULT '',
            n_lines  INTEGER NOT NULL DEFAULT 0,
            ms       INTEGER NOT NULL DEFAULT 0
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_rt_ts ON recall_trace(ts)")

        # V7 第 2 件：库内写者租约。
        # 文件租约只挡「同一个卡目录里的两份进程」；把 card.db 复制出去之后，
        # 副本拿的是它自己的租约文件，照样能写。库内这一行会跟着 card.db 一起被复制走，
        # 所以副本一启动就看得见「上一份还在写」—— 这是目前唯一能挡住「拷一份打开」的办法。
        c.execute("""CREATE TABLE IF NOT EXISTS writer_lease (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            holder TEXT NOT NULL DEFAULT '',
            epoch INTEGER NOT NULL DEFAULT 0,
            pid INTEGER NOT NULL DEFAULT 0,
            host TEXT NOT NULL DEFAULT '',
            expires_ts REAL NOT NULL DEFAULT 0,
            updated_ts REAL NOT NULL DEFAULT 0
        )""")

        # V7 第 21 件：梦批次账本 —— 压缩由宿主模型做，断了要能「同一批不写两遍」。
        c.execute("""
        CREATE TABLE IF NOT EXISTS dream_batch (
            key          TEXT PRIMARY KEY,
            state        TEXT NOT NULL DEFAULT 'running',
            stage        TEXT NOT NULL DEFAULT 'summarize',
            n_items      INTEGER NOT NULL DEFAULT 0,
            n_written    INTEGER NOT NULL DEFAULT 0,
            dialogue_ids TEXT NOT NULL DEFAULT '[]',
            written      TEXT NOT NULL DEFAULT '[]',
            attempts     INTEGER NOT NULL DEFAULT 0,
            last_error   TEXT NOT NULL DEFAULT '',
            created_ts   REAL NOT NULL DEFAULT 0,
            updated_ts   REAL NOT NULL DEFAULT 0
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_dream_state ON dream_batch(state, updated_ts)")

        # V7 第 9 件：老库补 event_ts / event_precision 两列（SQLite 没有 add column if not exists）
        try:
            _cols = {r[1] for r in c.execute("PRAGMA table_info(mem)").fetchall()}
            if "event_ts" not in _cols:
                c.execute("ALTER TABLE mem ADD COLUMN event_ts REAL NOT NULL DEFAULT 0")
            if "event_precision" not in _cols:
                c.execute("ALTER TABLE mem ADD COLUMN event_precision TEXT NOT NULL DEFAULT ''")
            c.execute("CREATE INDEX IF NOT EXISTS idx_mem_evts ON mem(event_ts)")
        except Exception as _ee:
            print("[card2] event_ts 迁移失败: %s" % _ee)

        # V7 第 19 件：这张卡里「哪些模型写过向量」——换模型时最要紧的一张账。
        c.execute(
            """CREATE TABLE IF NOT EXISTS emb_meta (
            model   TEXT PRIMARY KEY,
            dim     INTEGER NOT NULL DEFAULT 0,
            adapter INTEGER NOT NULL DEFAULT 0,
            n       INTEGER NOT NULL DEFAULT 0,
            first_ts REAL NOT NULL DEFAULT 0,
            last_ts  REAL NOT NULL DEFAULT 0
        )""")


        c.execute("""
        CREATE TABLE IF NOT EXISTS mem_tomb (
            mid    INTEGER PRIMARY KEY,
            hash   TEXT NOT NULL DEFAULT '',
            ts     REAL NOT NULL DEFAULT 0,
            reason TEXT NOT NULL DEFAULT ''
        )""")

        # 媒体记忆（2026-11-02）：画面自己一条寿命，文件放 media/ 目录，库里只放元数据。
        # 一条帧可以被多条记忆引用（同一张烧烤摊照片可能同时属于三条记忆），
        # 所以引用单独一张表，删的时候按引用计数走。
        c.execute("""
        CREATE TABLE IF NOT EXISTS media (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            sha256         TEXT NOT NULL UNIQUE,
            kind           TEXT NOT NULL DEFAULT 'image',
            mime           TEXT NOT NULL DEFAULT 'image/webp',
            w              INTEGER NOT NULL DEFAULT 0,
            h              INTEGER NOT NULL DEFAULT 0,
            dur            REAL NOT NULL DEFAULT 0,
            bytes          INTEGER NOT NULL DEFAULT 0,
            path           TEXT NOT NULL DEFAULT '',
            thumb          TEXT NOT NULL DEFAULT '',
            caption        TEXT NOT NULL DEFAULT '',
            tags           TEXT NOT NULL DEFAULT '[]',
            t0             REAL,
            t1             REAL,
            dialogue_id    INTEGER,
            source         TEXT NOT NULL DEFAULT 'user',
            trust          REAL NOT NULL DEFAULT 1.0,
            w0             REAL NOT NULL DEFAULT 2.0,
            t_anchor       REAL NOT NULL DEFAULT 0,
            recall_n       INTEGER NOT NULL DEFAULT 0,
            t_last_recall  REAL NOT NULL DEFAULT 0,
            pinned         INTEGER NOT NULL DEFAULT 0,
            orphan_ts      REAL NOT NULL DEFAULT 0,
            birth_hash     TEXT NOT NULL DEFAULT '',
            ts             REAL NOT NULL DEFAULT 0
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS ix_media_anchor ON media(t_anchor)")
        c.execute("""
        CREATE TABLE IF NOT EXISTS media_ref (
            mem_id   INTEGER NOT NULL,
            media_id INTEGER NOT NULL,
            ts       REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (mem_id, media_id)
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS ix_media_ref_m ON media_ref(media_id)")
        # 删帧台账：文件真没了，但得能证明"是我按规则删的"，不是被人抽走。
        # 跟 mem_tomb 一个道理，caption 也留一份 —— 忘了内容，记得忘过。
        c.execute("""
        CREATE TABLE IF NOT EXISTS media_tomb (
            sha256 TEXT PRIMARY KEY,
            caption TEXT NOT NULL DEFAULT '',
            ts     REAL NOT NULL DEFAULT 0,
            reason TEXT NOT NULL DEFAULT '',
            weight_at_drop REAL NOT NULL DEFAULT 0
        )""")

        # 控制台要有「趋势」才看得出变化 —— 光有当前值只能画一个点。
        # 每 5 分钟采一次样，一行 ~60 字节，一天 288 行，一年 10 万行 ≈ 6 MB。
        # 只存数，不存解释；解释永远现算。
        c.execute("""
        CREATE TABLE IF NOT EXISTS h_log (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            ts   REAL NOT NULL,
            sero REAL NOT NULL DEFAULT 0,
            dopa REAL NOT NULL DEFAULT 0,
            cort REAL NOT NULL DEFAULT 0,
            oxy  REAL NOT NULL DEFAULT 0,
            mood REAL NOT NULL DEFAULT 0,
            oxy_g REAL NOT NULL DEFAULT 0
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS ix_hlog_ts ON h_log(ts)")
        # 身体历史：每一次「一句话进来」都留一格。
        # 为什么要单独一张表而不是塞进 h_log：h_log 记的是慢激素心境
        # （seor/dopa/cort/oxy/mood），这张记的是**快反应层** —— 心率、
        # 交感副交感、以及认知层给的五条调质脉冲（DA/NE/OT/5HT/CORT）。
        # 曲线要能分开画，混在一张表里以后改哪个都得动历史数据。
        c.execute("""CREATE TABLE IF NOT EXISTS viscera_hist (
            id    INTEGER PRIMARY KEY AUTOINCREMENT,
            temp   REAL NOT NULL DEFAULT 0,
            tension REAL NOT NULL DEFAULT 0,
            h      TEXT,
            p      TEXT,
            pulse  TEXT,
            ts    REAL NOT NULL,
            hr    REAL NOT NULL DEFAULT 0,
            resp  REAL NOT NULL DEFAULT 0,
            face  REAL NOT NULL DEFAULT 0,
            sym   REAL NOT NULL DEFAULT 0,
            para  REAL NOT NULL DEFAULT 0,
            dopa  REAL NOT NULL DEFAULT 0,
            nora  REAL NOT NULL DEFAULT 0,
            sero  REAL NOT NULL DEFAULT 0,
            cort  REAL NOT NULL DEFAULT 0,
            oxy   REAL NOT NULL DEFAULT 0,
            rpe   REAL NOT NULL DEFAULT 0,
            safety REAL NOT NULL DEFAULT -1,
            label TEXT NOT NULL DEFAULT ''
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS ix_vhist_ts ON viscera_hist(ts)")
        # V7 第 18 件：把「这张卡的 schema 是哪一版」写进库里（SQLite 自带字段）。
        # 以前版本信息只活在代码里，卡被新代码写过之后拿旧代码打开，
        # 只会报一堆「no such column」，看不出是版本错位还是数据坏了。
        try:
            _v = int(c.execute("PRAGMA user_version").fetchone()[0] or 0)
            if _v < SCHEMA_VERSION:
                c.execute("PRAGMA user_version=%d" % SCHEMA_VERSION)
            self.schema_db = _v           # 打开之前卡里是哪一版
            self.schema_now = SCHEMA_VERSION  # 这份代码期待哪一版
        except Exception as e:
            soft_fail("schema_version", e, "写 PRAGMA user_version 失败")
            self.schema_db = None
        # 时间线与待办（2026-11-11）：建表放在初始化这儿，不放 remember() 里 ——
        # 写记忆时可能正开着事务，executescript 会撞「transaction within a transaction」。
        try:
            tempora.ensure(self.db)
        except Exception as e:
            soft_fail("tempora_schema", e, "建 tevent/tstate 失败")
        self.db.commit()

    def _migrate(self):
        # V5.6：老行没有 pool_in_ts，用 t_anchor 顶上（幂等）
        try:
            self.db.execute(
                "UPDATE mem SET pool_in_ts=t_anchor"
                " WHERE COALESCE(pool_in_ts,0)=0")
            self.db.commit()
        except Exception:
            pass
        """老库（card.py 建的）升级：加列 + 老数据按 k 归池"""
        c = self.db.cursor()
        cols = {r["name"] for r in c.execute("PRAGMA table_info(mem)")}
        add = {
            "pool": "TEXT NOT NULL DEFAULT 'short'",
            "w": "REAL NOT NULL DEFAULT 0.5",
            "t_anchor": "REAL NOT NULL DEFAULT 0",
            "oxy": "REAL NOT NULL DEFAULT 0.0",
            "entities": "INTEGER NOT NULL DEFAULT 0",
            "emotion": "REAL NOT NULL DEFAULT 0.0",
            "superseded_by": "INTEGER",
            "region": "TEXT NOT NULL DEFAULT 'episode'",
            "pod": "INTEGER NOT NULL DEFAULT -1",
            "subj": "TEXT NOT NULL DEFAULT ''",
            "verb": "TEXT NOT NULL DEFAULT ''",
            "obj": "TEXT NOT NULL DEFAULT ''",
            "erase_stage": "INTEGER NOT NULL DEFAULT 0",
            "emb": "BLOB",
            # 🔧 2026-10-31：双边记忆。以前整张表不区分「谁说的」，
            # 于是卡里装的其实是半本日记 —— 只有他说过什么，我说过什么全靠猜。
            # speaker：user / self / scene（我们一起做过的事）
            # mtype  ：episode（发生过的事）/ instruction（他给我的设定/命令）
            #          指令不是回忆，不能在「我一直记得的事」里占位。
            "speaker": "TEXT NOT NULL DEFAULT ''",
            "mtype": "TEXT NOT NULL DEFAULT 'episode'",
            # 🔧 2026-11-04：这条记忆是从哪个通道来的（chat/qq/qq-group/
            # bili/douyin/console/self）。以前只有 /report 一条入口，
            # 别的通道听见了也写不进来 —— 「考试考得不错」就是这么丢的。
            "source": "TEXT NOT NULL DEFAULT ''",
            # 🔧 2026-11-01：五层记忆（m02952/m02959 讨论后定）。
            # tier 只管"这条属于哪一层"，实际的时间行为仍由 pool + 下面几列决定：
            #   core_fixed   骨头：我是谁、我从哪来、三条底线 —— 只增不改不衰减
            #   core_tag     标签：人 ↔ 角色 —— 标签不动就不动，改了留痕
            #   core_mutable 底色：爱好/接受度/价值观/习惯/技能/身体/目标/雷点/处境
            #   mid / short  就是原来的长期池 / 短期池（保持旧字段兼容）
            "tier": "TEXT NOT NULL DEFAULT 'mid'",
            # about_uid / about_layer：这条是关于谁的、那人在第几层关系
        # （V5.1 用户 m13292：闲聊的寿命按"对谁"来定）
        "about_uid": "TEXT NOT NULL DEFAULT ''",
        "about_layer": "TEXT NOT NULL DEFAULT ''",
        # subject_key：认"这是同一件事"的主题键（"香菜"），演化合并靠它
            "subject_key": "TEXT NOT NULL DEFAULT ''",
            # history：[{ts, text, why}]，被替换掉的旧正文压在这里，留证不参与召回
            "history": "TEXT NOT NULL DEFAULT '[]'",
            # valid_until：处境类（高三、住哪）的失效时刻，到了就从"当前"挪走
            "valid_until": "REAL NOT NULL DEFAULT 0",
            # recall_n：被想起来的次数（长期晋升要验票，不只看权重）
            "recall_n": "INTEGER NOT NULL DEFAULT 0",
        # recall_days：被想起过几个**不同日子**。进小池靠它，不靠总次数 ——
        # 同一场对话里被翻来覆去想十次，说明的是"这一句当时要紧"，
        # 不能说明"这件事反复回来找我"。
        "recall_days": "INTEGER NOT NULL DEFAULT 0",
        # pool_in_ts：进当前这个池的时刻。只管"还剩多久"，
        # 与 t_anchor（权重的参考点）分开 —— 见 _settle_recall_events 注释。
        "pool_in_ts": "REAL NOT NULL DEFAULT 0",
            # t_last_recall：上次被想起的时刻，长期记忆的"召回保护期"用它
            "t_last_recall": "REAL NOT NULL DEFAULT 0",
            # 🔧 2026-11-01 防污层（Anti-Pollution，吸收自网友八层方案）：
            #   trust        这条记忆有多可信（0~1）。写进来时按规则打分：
            #                诱导改身份 / 幻觉式断言 / 与身份锚冲突 都会降分。
            #                它不阻止写入（"她被骗过"本身是叙事的一部分），
            #                只乘进海马分、并压低召回权重——脏念头进得来，升不上去。
            #   conflict     是否与既有事实/身份冲突（0/1），冲突的召回打 5 折
            #   conflict_why 冲突原因（给人看的，控制台"被灌输过什么"用它）
            "trust": "REAL NOT NULL DEFAULT 1.0",
            "conflict": "INTEGER NOT NULL DEFAULT 0",
            "conflict_why": "TEXT NOT NULL DEFAULT ''",
            # value        0~5 价值分：这条像不像"关于她/关于你的事实"。
            #              只打分、默认不拦门（拦门会改"她记住什么"，那是产品决策）
            "value": "INTEGER NOT NULL DEFAULT 2",
            # pin          2026-11-09：1 = 钉住，永不遗忘（底层/规则/重要的话）。
            #              由 _repartition_pools.py 一次性标记，也可手动改。
            "pin": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, decl in add.items():
            if name not in cols:
                c.execute("ALTER TABLE mem ADD COLUMN %s %s" % (name, decl))
        # 把老行填满（老库才有 clarity 列；新库没有，跳过）
        cols2 = {r["name"] for r in c.execute("PRAGMA table_info(mem)")}
        self._has_clarity = "clarity" in cols2
        if "clarity" in cols2:
            for r in c.execute("SELECT id, k, create_ts, clarity FROM mem").fetchall():
                p = pool_of_k(r["k"])
                c.execute("UPDATE mem SET pool=?, w=?, t_anchor=? WHERE id=?",
                          (p, r["clarity"], r["create_ts"], r["id"]))
        c.execute("UPDATE mem SET t_anchor=create_ts WHERE t_anchor=0")
        # V4.1：老数据补分区。
        # 🔧 2026-11-05 事故修正：这段原来每次开机都跑，加了 chatter 之后
        #    它把 440 条历史记忆改判成闲聊（一天后剩 0.15）—— 等于悄悄删了
        #    四分之三的记忆。现在用 meta 里的标记只跑一次。
        #    新卡写入时就判好了，本来也不需要回填。
        _done = c.execute(
            "SELECT val FROM meta WHERE key='region_backfill_done'").fetchone()
        if not _done:
            for r in c.execute(
                    "SELECT id, raw, emotion FROM mem"
                    " WHERE region='episode'").fetchall():
                rg = classify_region(r["raw"], r["emotion"])
                if rg != R_EPISODE:
                    c.execute("UPDATE mem SET region=? WHERE id=?", (rg, r["id"]))
            c.execute("INSERT OR REPLACE INTO meta(key,val) VALUES(?,?)",
                      ("region_backfill_done", str(int(self.clock.now()))))
        c.execute("CREATE INDEX IF NOT EXISTS ix_mem_pool ON mem(pool)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_mem_region ON mem(region)")
        # 🔧 T4-3b：detail 加语义向量列（老库升级）
        dcols = {r["name"] for r in c.execute("PRAGMA table_info(detail)")}
        if "emb" not in dcols:
            c.execute("ALTER TABLE detail ADD COLUMN emb BLOB")
        # 🔧 2026-11-01 梦境压缩（Dream）：dialogue 加"消化过没有"的标记。
        #    dreamed=0 的原文等着被提炼成高阶记忆；原文永不删除（可回溯）。
        dlcols = {r["name"] for r in c.execute("PRAGMA table_info(dialogue)")}
        if "dreamed" not in dlcols:
            c.execute("ALTER TABLE dialogue ADD COLUMN dreamed INTEGER NOT NULL DEFAULT 0")
            # 🔧 老库升级：历史原文一律算「已消化」。
            #    否则一升级就冒出几百条"待做梦"，控制台那个 520 会吓死人，
            #    而且 Dream 会拿三周前的对话去重压一遍 —— 那不是补账，是重复劳动。
            c.execute("UPDATE dialogue SET dreamed=1")
        c.execute("CREATE INDEX IF NOT EXISTS ix_detail_parent ON detail(parent_id)")
        self.db.commit()

    # ---------- 防污层（Anti-Pollution，2026-11-01 吸收自网友八层方案） ----------
    def _trust_of(self, text):
        return assess_trust(text, self)

    def injection_report(self, limit=12):
        """最近被灌进来的可疑念头。控制台「她今天被灌输过什么」用它。
        只读，不改任何权重。"""
        rows = self.db.execute(
            "SELECT id,raw,core,region,trust,conflict,conflict_why,create_ts"
            " FROM mem WHERE trust < ? OR conflict=1"
            " ORDER BY id DESC LIMIT ?", (TRUST_WARN, int(limit))).fetchall()
        out = []
        for r in rows:
            out.append({
                "id": r["id"],
                "text": (r["core"] or r["raw"] or "")[:60],
                "region": r["region"],
                "trust": round(float(r["trust"] or 0), 3),
                "conflict": int(r["conflict"] or 0),
                "why": r["conflict_why"] or "",
                "ts": float(r["create_ts"] or 0),
            })
        n_bad = self.db.execute(
            "SELECT COUNT(*) FROM mem WHERE trust < ? OR conflict=1",
            (TRUST_WARN,)).fetchone()[0]
        return {"n": n_bad, "rows": out,
                "warn": TRUST_WARN, "block": TRUST_BLOCK_PROMOTE}

    def untrust(self, mid, reason="人工判定：那是玩笑，不是灌输", ts=None):
        """人工解除一次防污误判：冲突清零、信任抬回 1.0，并留痕迹。

        为什么需要这个：规则只能判断"这句话像不像改写自我"，判不了"是不是在开玩笑"——
        那需要人。误判的代价是那条记忆永远进不了长期/底色、召回还打对折。
        解除不能悄悄清字段：**要在 history 里留一笔**，写清原值、理由、时间，
        以后翻回来能知道"这条是她自己解的，不是从来没被标记过"。
        """
        now = time.time() if ts is None else float(ts)
        r = self.db.execute(
            "SELECT id,trust,conflict,conflict_why,history,raw,"
            " t_anchor,pool,tier,w,t_last_recall FROM mem WHERE id=?",
            (int(mid),)).fetchone()
        if r is None:
            return {"ok": False, "error": "no_such_mem"}
        if not (float(r["trust"] or 1.0) < 1.0 or int(r["conflict"] or 0) == 1):
            return {"ok": True, "changed": False, "id": int(mid),
                    "note": "这条本来就没有冲突标记"}
        hist = []
        try:
            hist = json.loads(r["history"] or "[]")
            if not isinstance(hist, list):
                hist = []
        except Exception:
            hist = []
        hist.append({
            "ts": round(now, 3),
            "kind": "untrust",
            "reason": reason,
            "from_trust": round(float(r["trust"] or 1.0), 3),
            "from_conflict": int(r["conflict"] or 0),
            "from_why": r["conflict_why"] or "",
        })
        if len(hist) > 8:
            hist = hist[-8:]
        cur = self.db.cursor()
        cur.execute(
            "UPDATE mem SET trust=1.0, conflict=0, conflict_why='', history=?"
            " WHERE id=?", (json.dumps(hist, ensure_ascii=False), int(mid)))
        self.db.commit()
        return {"ok": True, "changed": True, "id": int(mid),
                "from": {"trust": round(float(r["trust"] or 1.0), 3),
                         "conflict": int(r["conflict"] or 0)},
                "to": {"trust": 1.0, "conflict": 0},
                "reason": reason}

    # ---------- 梦境压缩（Dream，2026-11-01） ----------
    def pending_dialogue(self, limit=240, min_ts=0.0):
        """还没被"消化"过的对话原文。给宿主侧的提炼器用。
        卡自己不调模型——压缩由外面做，做完用 dream_commit() 回写。"""
        rows = self.db.execute(
            "SELECT id,ts,role,text,mem_id FROM dialogue"
            " WHERE dreamed=0 AND ts>=? ORDER BY ts ASC, id ASC LIMIT ?",
            (float(min_ts), int(limit))).fetchall()
        items = [{"id": r["id"], "ts": r["ts"], "role": r["role"],
                  "text": r["text"], "mem_id": r["mem_id"]} for r in rows]
        chars = sum(len(i["text"]) for i in items)
        return {"n": len(items), "chars": chars, "min_turns": DREAM_MIN_TURNS,
                "idle_hours": DREAM_IDLE_HOURS, "items": items}

    def dream_due(self, now=None):
        """够不够格做一次梦：轮数够 or 空闲够久，且确实有待消化的原文。"""
        now = self.clock.now() if now is None else float(now)
        pend = self.db.execute(
            "SELECT COUNT(*) AS n, MAX(ts) AS last FROM dialogue WHERE dreamed=0"
        ).fetchone()
        n = int(pend["n"] or 0)
        last = float(pend["last"] or 0)
        idle_h = (now - last) / 3600.0 if last else 0.0
        return {"pending": n, "idle_hours": round(idle_h, 2),
                "due": bool(n >= DREAM_MIN_TURNS or (n > 0 and idle_h >= DREAM_IDLE_HOURS))}

    # ---------- 梦：五阶段 + 幂等 ----------
    # 为什么要有一张批次账本：压缩是宿主模型做的，一旦超时/断连，调用方唯一
    # 知道的只有「刚才那次到底写成没有」。没有幂等键就只能二选一 ——
    # 要么重试把同一批记忆写两遍（重复污染），要么放弃（原文永远压不掉）。
    # 所以每次 commit 都拿一个由「原文 id + 摘要内容」算出来的 key 登记在案，
    # 同一个 key 再来，直接回上次的结果。

    def _dream_key(self, items, dialogue_ids=None):
        """幂等键：同一批原文 + 同一份摘要 → 同一个 key。"""
        try:
            body = json.dumps(
                {"ids": sorted(int(i) for i in (dialogue_ids or [])),
                 "items": [str((it or {}).get("text") or "").strip()
                           for it in (items or [])]},
                ensure_ascii=False, sort_keys=True)
        except Exception:
            body = repr(items)
        return hashlib.sha1(body.encode("utf-8")).hexdigest()[:16]

    def dream_batch_get(self, key):
        try:
            r = self.db.execute(
                "SELECT * FROM dream_batch WHERE key=?", (str(key),)).fetchone()
        except Exception:
            return None
        return dict(r) if r else None

    def dream_dead(self, limit=10):
        """失败过的梦批次（死信）。留着可重试，不静默丢。"""
        out = []
        try:
            rows = self.db.execute(
                "SELECT key,stage,n_items,dialogue_ids,attempts,last_error,updated_ts"
                " FROM dream_batch WHERE state='failed' ORDER BY updated_ts DESC LIMIT ?",
                (int(limit),)).fetchall()
        except Exception:
            return out
        for r in rows:
            out.append({"key": r["key"], "stage": r["stage"],
                        "n_items": int(r["n_items"] or 0),
                        "dialogue_ids": r["dialogue_ids"],
                        "attempts": int(r["attempts"] or 0),
                        "last_error": r["last_error"],
                        "age_h": round((time.time() - float(r["updated_ts"] or 0)) / 3600.0, 1)})
        return out

    def dream_commit(self, items, dialogue_ids=None, speaker="self", key=None, force=False):
        """五阶段提交：ingest → summarize → link → validate → commit。

        ingest：洗一遍摘要素材（空/太短/一批里重复的直接扔掉，记进 problems）
        summarize：真正写记忆（这一段就是「压」，压缩本身由宿主模型做完再送进来）
        link：把新记忆和源原文绑上（记忆 source 写成 dream:<key>，批次里留 id 清单）
        validate：写完回查，写没写进去要能验；验不过的从 written 里剔掉并记问题
        commit：批次落 done，回结果。同一个 key 再来 → duplicate，不重复写。

        force=True 时即使同一个 key 已经 done 也再写一遍（人工补救用）。
        """
        now = float(self.clock.now())
        # ---- ingest ----
        clean, problems, seen = [], [], set()
        for it in (items or []):
            txt = str((it or {}).get("text") or "").strip()
            if not txt:
                problems.append("empty_text")
                continue
            if len(txt) < 4:
                problems.append("too_short:%s" % txt)
                continue
            sig = txt[:48]
            if sig in seen:
                problems.append("dup_in_batch:%s" % txt[:16])
                continue
            seen.add(sig)
            reg = (it or {}).get("region") or R_FACT
            if reg not in REGION_DECAY_SCALE:
                reg = R_FACT
            clean.append({"text": txt, "region": reg,
                          "salience": float((it or {}).get("salience") or 0.55),
                          "w0": float((it or {}).get("w0") or 0.62)})
        if not clean:
            return {"ok": False, "error": "nothing_to_commit",
                    "problems": problems, "stage": "ingest"}

        try:
            ids = sorted(int(i) for i in (dialogue_ids or [])) if dialogue_ids else []
        except Exception:
            # 调用方传了不是 id 的东西：这是入参错，不是卡坏了 —— 400 回去，别 500。
            return {"ok": False, "error": "bad_dialogue_ids",
                    "detail": repr(dialogue_ids)[:120], "stage": "ingest"}
        key = str(key or self._dream_key(clean, ids))

        prev = self.dream_batch_get(key)
        if prev and prev.get("state") == "done" and not force:
            try:
                written = json.loads(prev.get("written") or "[]")
            except Exception:
                written = []
            return {"ok": True, "duplicate": True, "key": key,
                    "written": written, "n": len(written),
                    "n_written": int(prev.get("n_written") or len(written)),
                    "stage": "commit", "problems": problems}

        # ---- 登记 running（失败也要把 attempts 累起来，别静默丢）----
        attempts = int((prev or {}).get("attempts") or 0)
        try:
            self.db.execute(
                "INSERT INTO dream_batch(key,state,stage,n_items,dialogue_ids,written,"
                " attempts,last_error,created_ts,updated_ts)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET state=excluded.state, stage=excluded.stage,"
                "   n_items=excluded.n_items, dialogue_ids=excluded.dialogue_ids,"
                "   attempts=excluded.attempts, last_error='', updated_ts=excluded.updated_ts",
                (key, "running", "summarize", len(clean),
                 json.dumps(ids), json.dumps([]), attempts, "", now, now))
            self.db.commit()
        except Exception as e:
            soft_fail("dream_batch", e, "梦批次登记失败（仍继续写，只是丢了幂等）")

        written = []
        try:
            # ---- summarize + link ----
            src_tag = "dream:%s" % key[:10]
            for it in clean:
                mid = self.remember(it["text"], salience=it["salience"],
                                    region=it["region"], speaker=speaker,
                                    mtype="episode", source=src_tag)
                if mid is None:
                    problems.append("dedup_by_remember:%s" % it["text"][:16])
                    continue
                try:
                    self.db.execute("UPDATE mem SET w=?, t_anchor=? WHERE id=?",
                                    (max(0.2, min(1.0, it["w0"])), self.clock.now(), mid))
                    self.db.commit()
                except Exception as e:
                    soft_fail("dream_w0", e, "梦记忆起点权重没写进去（记忆本身已经写了）")
                written.append(mid)

            # ---- validate：回查真的在库里 ----
            keep = []
            for mid in written:
                try:
                    r = self.db.execute("SELECT id FROM mem WHERE id=?", (int(mid),)).fetchone()
                except Exception:
                    r = None
                if r:
                    keep.append(mid)
                else:
                    problems.append("lost_after_write:%s" % mid)
            written = keep

            # ---- commit：翻原文标记 + 落批次账 ----
            if dialogue_ids is None:
                self.db.execute("UPDATE dialogue SET dreamed=1 WHERE dreamed=0")
            elif ids:
                q = ",".join("?" for _ in ids)
                self.db.execute("UPDATE dialogue SET dreamed=1 WHERE id IN (%s)" % q,
                                tuple(ids))
            self.db.execute(
                "UPDATE dream_batch SET state='done', stage='commit', n_written=?,"
                " written=?, updated_ts=? WHERE key=?",
                (len(written), json.dumps(written), float(self.clock.now()), key))
            self.db.commit()
            self._invalidate_idf()
            return {"ok": True, "key": key, "written": written, "n": len(written),
                    "n_written": len(written), "stage": "commit",
                    "problems": problems, "dialogue_ids": ids}
        except Exception as e:
            # ---- 死信：记下来，原文**不翻标记**（下次还能再压）----
            try:
                self.db.rollback()
            except Exception:
                pass
            try:
                self.db.execute(
                    "UPDATE dream_batch SET state='failed', stage=COALESCE(stage,'summarize'),"
                    " attempts=attempts+1, last_error=?, updated_ts=? WHERE key=?",
                    ("%s: %s" % (type(e).__name__, str(e)[:200]),
                     float(self.clock.now()), key))
                self.db.commit()
            except Exception as e2:
                soft_fail("dream_dead_letter", e2, "死信都没记上")
            soft_fail("dream_commit", e, "梦提交失败")
            return {"ok": False, "error": "commit_failed", "key": key,
                    "detail": "%s: %s" % (type(e).__name__, str(e)[:200]),
                    "written_so_far": written, "problems": problems, "stage": "validate"}

    # ---------- 存 ----------

    # ---------- 分区负载（V5.2，2026-11-06）----------
    # 做什么：维护「这个区最近 24 小时新写了几条」，换算成拥挤系数 f。
    #   f = clamp( sqrt(load / budget), 0.55, 2.2 )
    #   没人聊 → f≈0.55（忘得慢）；爆群 → f 到 2.2（忘得快）。
    #
    # 为什么用指数平滑而不是「按自然日计数」：
    #   自然日在零点会跳变 —— 23:59 还在算"今天很挤"，00:01 忽然变"没人说话"，
    #   同一句话的寿命在零点前后差一倍以上，很假。指数平滑的时间常数取 24 小时，
    #   连续变化、跨零点不甩尾，而且 O(1)，不用每次查库。
    _LOAD_META = "region_load"

    def _load_read(self):
        """从 meta 读回负载表，并按流逝时间做一次衰减。"""
        raw = None
        try:
            row = self.db.execute(
                "SELECT val FROM meta WHERE key=?", (self._LOAD_META,)).fetchone()
            if row:
                raw = json.loads(row[0] or "{}")
        except Exception:
            raw = None
        now = self.clock.now()
        out = {}
        for k, v in (raw or {}).items():
            try:
                val, ts = float(v[0]), float(v[1])
            except Exception:
                continue
            dt = max(0.0, now - ts)
            out[k] = [val * math.exp(-dt / LOAD_TAU), now]
        _REGION_LOAD.clear()
        _REGION_LOAD.update({k: v[0] for k, v in out.items()})
        return out

    def _load_write(self, state):
        try:
            self.db.execute(
                "INSERT OR REPLACE INTO meta(key,val) VALUES(?,?)",
                (self._LOAD_META, json.dumps(state)))
            self.db.commit()
        except Exception:
            pass

    def _load_refresh_f(self):
        """把负载换算成系数，写进模块级 _LOAD_F（weight_now 读它）。"""
        _LOAD_F.clear()
        for reg, val in _REGION_LOAD.items():
            budget = REGION_DAY_BUDGET.get(reg)
            if not budget or budget <= 0:
                continue
            _LOAD_F[reg] = _f_of(val, budget)

    def _load_bump(self, region, now=None):
        """这个区又写了一条：先衰减，再 +1。"""
        state = self._load_read()
        now = self.clock.now()
        cur = state.get(region, [0.0, now])[0]
        state[region] = [cur + 1.0, now]
        self._load_write(state)
        _REGION_LOAD[region] = cur + 1.0
        self._load_refresh_f()

    def _load_decay(self, now=None):
        """光有时间流走、没写东西，负载也要回落。"""
        state = self._load_read()          # 里面已经衰减过
        if not state:
            return 0
        self._load_write(state)
        self._load_refresh_f()
        return len(state)

    def set_region_lambda(self, on):
        """打开/关闭「每个区有自己的 λ」。

        关掉就是回到 V5.1 的老行为（除闲聊外全按池的阶段曲线），
        这是最省事的回滚开关：一行调用就能让今天的改动整体失效，
        不用改代码、不用恢复备份。
        """
        global _REGION_LAMBDA_ON
        _REGION_LAMBDA_ON = bool(on)
        try:
            self.db.execute(
                "INSERT OR REPLACE INTO meta(key,val) VALUES(?,?)",
                ("region_lambda_on", "1" if _REGION_LAMBDA_ON else "0"))
            self.db.commit()
        except Exception:
            return False
        if _REGION_LAMBDA_ON:
            try:
                self._load_read()
                self._load_refresh_f()
            except Exception:
                pass
        else:
            _REGION_LOAD.clear()
            _LOAD_F.clear()
        return True

    def region_load_snapshot(self):
        """给控制台看：每个区现在的负载/预算/系数，以及开关状态。"""
        self._load_read()
        self._load_refresh_f()
        out = []
        for reg in (R_CHATTER, R_PLAY, R_STUDY, R_WORK):
            load = float(_REGION_LOAD.get(reg, 0.0))
            budget = REGION_DAY_BUDGET.get(reg, 0.0)
            out.append({
                "region": reg,
                "half_life_h": REGION_HALF_LIFE.get(reg, 0.0) / 3600.0,
                "load": round(load, 2),
                "budget": budget,
                "factor": round(_LOAD_F.get(reg, 1.0), 3),
            })
        return {"on": _REGION_LAMBDA_ON, "regions": out}

    # ---------- 老 ----------
    def tick(self, dt_seconds, verbose=False):
        """时间流逝：结算权重 → 跨池流转 → 侵蚀 → 回收"""
        dt_raw = max(0.0, dt_seconds)
        self.clock.advance(dt_raw)
        dt = compress_offline_dt(dt_raw)
        now = self.clock.now()
        c = self.db.cursor()
        events = []

        # 0) V5.0 公理 6：先结算上一段攒下的召回事件（遗忘与联想解耦）
        n_settled = self._settle_recall_events()
        if n_settled:
            events.append((0, "settle_recall", n_settled))
        # 0.1) 清掉早就结算完的召回事件（只 settled 不删，表会一直涨：
        #      实测两天就 2226 行、其中 1763 行来自同一天）
        try:
            n_gone = c.execute(
                "DELETE FROM recall_event WHERE settled=1 AND ts < ?",
                (now - 30 * DAY,)).rowcount
            if n_gone:
                events.append((0, "recall_event_pruned", n_gone))
        except Exception:
            pass

        # 0.2) V5.2：分区负载回落（没写东西时，拥挤系数要自己慢慢降下来）
        try:
            self._load_decay(now)
        except Exception:
            pass

        # 0.5) 心境回归（V5.0 T2-3）：时间一过，不管发生什么，都往中性漂
        if dt_raw > 0:
            _before = self.mood
            self.mood = mood_drift_home(self.mood, dt_raw / DAY)
            if abs(self.mood - _before) > 1e-6:
                self._save_mood()
                events.append((0, "mood_drift", round(self.mood, 4)))

        # 0.6) 激素回归（V5.0 T3-2）：四个激素往各自基线漂
        if dt_raw > 0:
            _hb = dict(self.h)
            self.h = hormone.regress_all(self.h, dt_raw / 3600.0)
            if self.h != _hb:
                self._save_hormones()
                events.append((0, "hormone_drift", hormone.describe(self.h)))

        # 0.7) 欲望层结算（2026-11-09 接线）
        #    motiv_tick 原来只有 /motiv/tick 一个入口，而那个入口只有
        #    qq_proactive.py（主动发言脚本）会打 —— 只要那个脚本没在跑，
        #    七条驱力就一辈子不动：聊天时 motiv_on_chat 一路往下泄（attachment
        #    每句 -0.10），却没有任何东西把它们拉回基线，也没有「想念」把
        #    它们顶上去。结果【动机】这一格永远是空的，motiv_snapshot 里
        #    看到的 drvies 是几天前冻住的数。
        #    放在 tick 里 = 只要有人在跟她说话（每轮 /context 都会走 tick），
        #    驱力就跟着时间一起动。一分钟节流，省得每秒钟写一次 meta。
        if dt_raw > 0:
            try:
                _mt = float(getattr(self, "_last_motiv_tick", 0.0) or 0.0)
                if now - _mt >= 60.0:
                    self.motiv_tick(now=now)
                    self._last_motiv_tick = now
            except Exception as e:
                print("[mind] 动机结算失败：%s" % e, file=sys.stderr)

        # 1) 短期池超期 → 强制结算（巩固 or 淘汰）
        #    ⚠️ 这里的 7 天是**现实时间**，故意不乘分区倍率。
        #    短期池是「最近被注意到的 32 个位置」，是注意力预算、不是寿命：
        #    分区倍率决定"忘得多慢"，位置腾退决定"能给多少张桌子"。
        #    flash 区的记忆权重掉得慢，但它照样占着一张桌子 —— 让它赖 23 天，
        #    别的记忆就没地方坐了。两件事分开算，才是对的。
        #    V5.5（2026-11-08 用户拍板）：改成**实时筛选** ——
        #    进了短期池就每轮判一次海马分，够格当场进中期/长期；
        #    不够格的留在短期池里按自己的曲线往下淡，满 7 天还没够格才删。
        #    原来的写法只在「满 7 天」那一刻结算一次，于是够格的也要白坐七天。
        # 同一轮里搬过家的不要再算一次：否则 short_warm → medium 之后
        # 紧接着又被 medium → medium_warm 抓一遍，一步跨两级。
        moved_ids = set()
        # 池内年龄按 pool_in_ts 算（不是 t_anchor）：被想起只会涨权重，
        # 不会让这条记忆在池子里重新排队 —— 续命只能靠搬进小池。
        for r in c.execute(
                "SELECT * FROM mem WHERE pool='short'").fetchall():
            age = now - float(r["pool_in_ts"] or r["t_anchor"])
            # 先看要不要搬进小池：跨了不同日子还被反复想起的，才算"反复回来找我"
            if self._warm_ready(r, WARM_SHORT_DAYS, WARM_SHORT_RECALL):
                moved = self._to_warm(c, r, now, POOL_SHORT_WARM)
                moved_ids.add(r["id"])
                events.append((r["id"], "warm_up", moved))
                continue
            moved = self._consolidate(r, now)
            if moved:
                events.append((r["id"], "consolidate", moved))
            elif age > SHORT_MAX_TTL:
                events.append((r["id"], "expire", None))

        # 1.1) 短期小池：再被想起就往上走，到期没动静就忘
        for r in c.execute(
                "SELECT * FROM mem WHERE pool=?", (POOL_SHORT_WARM,)).fetchall():
            if r["id"] in moved_ids:
                continue
            age = now - float(r["pool_in_ts"] or r["t_anchor"])
            if self._warm_ready(r, WARM_UP_DAYS, WARM_UP_RECALL):
                moved = self._promote_warm(c, r, now)
                moved_ids.add(r["id"])
                events.append((r["id"], "warm_up", moved))
            elif age > SHORT_WARM_TTL:
                self._forget_row(c, r["id"], "短期小池到期，还是没被再想起")
                events.append((r["id"], "expire", None))

        # 1.2) 中期池：同样有它自己的小池
        for r in c.execute(
                "SELECT * FROM mem WHERE pool=?", (POOL_MEDIUM,)).fetchall():
            if r["id"] in moved_ids:
                continue
            if self._warm_ready(r, WARM_MEDIUM_DAYS, WARM_UP_RECALL):
                moved = self._to_warm(c, r, now, POOL_MEDIUM_WARM)
                moved_ids.add(r["id"])
                events.append((r["id"], "warm_up", moved))

        # 1.25) 中期池出口（V7 第 10 件）：30 天内会跌破进门线的，提前挪进长期池。
        #     放在 1.2 之后、1.3 之前 —— 它只改 pool，不影响小池那套判定。
        try:
            _exit = self.medium_exit_apply(horizon_days=0, limit=20, now=now)
            if _exit.get("moved"):
                print("[card2] 中期池出口：%d 条挪进长期池（30 天内本来会跌破线）"
                      % _exit["moved"])
        except Exception as _xe:
            print("[card2] 中期池出口失败: %s" % _xe)

        # 1.3) 中期小池：够格进长期，否则到期淡出
        for r in c.execute(
                "SELECT * FROM mem WHERE pool=?", (POOL_MEDIUM_WARM,)).fetchall():
            if r["id"] in moved_ids:
                continue
            age = now - float(r["pool_in_ts"] or r["t_anchor"])
            if (self._warm_ready(r, WARM_LONG_DAYS, WARM_LONG_DAYS)
                    and self._hippocampus_score(r) >= THETA_LONG_STRICT
                    and self._long_ticket_ok(r)):
                wn2 = max(wn_row(r, now), W_FLOOR_LONG)
                c.execute("UPDATE mem SET pool=?, w=?, t_anchor=?, tier=?,"
                          " pool_in_ts=? WHERE id=?",
                          (POOL_LONG, wn2, now, TIER_MID, now, r["id"]))
                events.append((r["id"], "promote_long", POOL_LONG))
            elif age > MEDIUM_WARM_TTL:
                self._forget_row(c, r["id"], "中期小池到期，自然淡出")
                events.append((r["id"], "expire", None))

        # 2) 收件层过期清理（V5.4）：只留 60 小时，没被挑中的到期就走
        try:
            self.inbox_gc(now=now)
        except Exception as _ige:
            print("[card2] 收件层清理失败: %s" % _ige)

        # 3) 细节衰减
        #
        # 🔴 2026-10-31：按 kind 分速是分层褪色的物理基础 ——
        #    「越细的越先掉」这句话必须落实成不同的 k 值，否则就是空谈。
        #    速率表和池倍率已提到模块级（K_DETAIL_BY_KIND / DETAIL_POOL_SCALE），
        #    因为 wake_up() 补算离线那段细节时要用**同一套**参数 ——
        #    两处各写一份，早晚会改漏一处，然后就出现「醒来和平时衰减不一样」。
        c.execute("SELECT d.id, d.kind, d.weight, m.pool FROM detail d"
                  " JOIN mem m ON m.id=d.parent_id")
        for d in c.fetchall():
            k = K_DETAIL_BY_KIND.get(d["kind"], DEFAULT_K_DETAIL)
            # 池越深，细节也越慢
            scale = DETAIL_POOL_SCALE.get(d["pool"], 1.0)
            nw = d["weight"] * math.exp(-k * scale * dt)
            if nw < KEEP_DETAIL:
                c.execute("DELETE FROM detail WHERE id=?", (d["id"],))
            else:
                c.execute("UPDATE detail SET weight=? WHERE id=?", (nw, d["id"]))

        # 4) 侵蚀：三级往下剥（V5.0 T2-1）
        #    不是一刀删整条，而是：
        #      stage 0 → 1  擦动词（「吃烧烤」→「烧烤（做了什么记不清了）」）
        #      stage 1 → 2  擦宾语（只剩主语）
        #      stage 2 → 删  整条没了
        #    每一步都必须 random() < p，不能确定性删除。
        for r in c.execute("SELECT * FROM mem").fetchall():
            # 侵蚀只管「热区」：短期 / 中期。
            # LONG 有自己的下沉出口，ARCHIVE / DEEP 更是只许往下沉、不许被抹掉内容
            # —— 否则它们在半路上被削成 0.015 的空壳，就再也沉不到 TRACE 了。
            if r["pool"] in (POOL_LONG, POOL_ARCHIVE, POOL_DEEP):
                continue
            # 底色层不吃侵蚀：骨头和底色是被"替换"的，不是被时间削掉的。
            # 削成空壳会让它连"我曾经讨厌香菜"都说不完整。
            if r["tier"] in CORE_TIERS:
                continue
            wn = wn_row(r, now)
            if wn >= ERASE_W_FLOOR:
                continue
            p = P_ERASE_MAX * (1.0 - wn / ERASE_W_FLOOR)
            if random.random() >= p:
                continue
            stage = r["erase_stage"] if "erase_stage" in r.keys() else 0
            # 老数据没有 subj/verb/obj 的，现场拆一次
            subj = r["subj"] if "subj" in r.keys() else ""
            verb = r["verb"] if "verb" in r.keys() else ""
            obj = r["obj"] if "obj" in r.keys() else ""
            if not (subj or verb or obj):
                subj, verb, obj = split_predicate(r["core"])
            if stage == 0 and verb:
                # 第一刀：擦动词
                c.execute("UPDATE mem SET erase_stage=1, verb='', w=?, t_anchor=?"
                          " WHERE id=?", (HUSK_W, now, r["id"]))
                events.append((r["id"], "erase_verb", verb))
            elif stage <= 1 and (obj or subj):
                # 第二刀：擦宾语（保留主语当影子）
                c.execute("UPDATE mem SET erase_stage=2, obj='', w=?, t_anchor=?"
                          " WHERE id=?", (HUSK_W, now, r["id"]))
                events.append((r["id"], "erase_obj", obj))
            else:
                # 最后一刀：整条消失（先登记台账，删了就算不出哈希了）
                self._forget_row(c, r["id"], "主干侵蚀到头")
                events.append((r["id"], "erased", None))

        # 5) 五空间下沉（T5-1 / V5.0 §12.5）
        #    长期 → 潜意识 → 深层痕迹 → TRACE 计数。
        #    每一步都只「降低可见度」，不删内容；只有最后一步才真的把正文换成计数。
        sunk = self._sink(now)
        if sunk:
            events.extend(sunk)

        # 5b) 处境到期（五层记忆 · 底色层）
        #     「我现在高三」这种条目带 valid_until，到期不是删掉，是降成"曾经"。
        try:
            expired = self.situation_expire(now)
            if expired:
                events.append((0, "situation_expire", expired))
        except Exception:
            pass

        # 6) 技能熟练度衰减（V5.0 §7.3）
        #
        # 🔴 2026-10-31：这条以前根本没接线 —— skill.py 里 340 行公式
        #    写得整整齐齐，card2 只 `self.skill = SkillMemory(db)` 建了个对象，
        #    之后再没调过它。所以 skill / skill_log 两张表一直是 0 行，
        #    「技能记忆」这一维（ctxweight 的 F）永远拿不到东西。
        #
        #    技能和普通记忆的差别就在这里：**内容不风化，只风化熟练度**。
        #    所以它不参与 detail 衰减、不参与三级侵蚀，只在自己的表里按
        #    interval 慢速掉 L —— 掉到 L_DORMANT 以下不是删除，是「手生」。
        try:
            n_skill = self.skill.tick_all(now=now)
            if n_skill:
                events.append((0, "skill_decay", n_skill))
        except Exception:
            pass

        # 画面自己风化（幂律，比文字快得多）：到线删文件、留台账，文字那条不动。
        # 跟技能的 tick 一样，出错不许拖垮整轮结算。
        try:
            for _mid, _kind, _tag in self._media_tick(now):
                events.append((0, "media_" + _kind, _tag))
        except Exception:
            pass

        self.db.commit()
        self._gc()
        if verbose:
            return events
        return events

    def _hippocampus_score(self, r):
        """海马打分：这笔记忆值不值得搬进长期

        🔴 2026-11-09 改「被唤起」这一项的口径（诊断报告 probe/_diag2_pool.md 的 Q3）：
        原来 `c_rec = access_count/10`，也就是「同一天被连问三遍」和「三天里各想起一次」
        一样值钱。结果是 medium 里 recall_n>=2 有 46 条、recall_days>=3 却是 0 条 ——
        全是一次性的 burst，没有一条是真的反复被想起过。
        而且中性记忆的天花板正好卡在 .35（A_RECALL 满打满算 .25 + 情绪里那点 .10），
        永远够不到 .80 的进长期门槛，于是中期池变成一个只进不出的停车场。

        改成「天数」和「次数」各占一半：
          · 次数（access_count/10）—— 被频繁要用的东西值钱，这条留着；
          · 天数（recall_days/7）—— 分散在不同日子被想起才算"记住了"，
            同一天刷十遍也不会把分刷上去（这跟人脑的重 consolidation 是一个道理）。
        recall_days 由 recall() 在命中时按自然日去重累加。
        """
        e = min(r["emotion"], 1.0)                 # 情绪 0~1
        c_ent = min(r["entities"] / 5.0, 1.0)      # 实体数 0~1
        try:
            _days = float(r["recall_days"] or 0)
        except (KeyError, IndexError, TypeError):
            _days = 0.0
        c_rec = 0.5 * min(r["access_count"] / 10.0, 1.0) + 0.5 * min(_days / 7.0, 1.0)
        o = min(r["oxy"], 1.0)                     # 亲密 0~1
        return (A_EMOTION * e + A_ENTITY * c_ent +
                A_RECALL * c_rec + A_OXY * o)

    def _long_ticket_ok(self, r):
        """进长期要"验票"（2026-11-01 改）：

        原来只看海马分 ≥ 0.60 —— 当时很激动就能进长期。
        但人不是这样：那天哭得再凶，之后没人再提，一年也就淡成"好像有过"。
        所以长期必须"之后还被想起来过"：
          · 一般记忆：召回 ≥ LONG_NEED_RECALL 次
          · 高唤醒（flash / self / habit 分区，或显著度 ≥ 0.75）：召回 ≥ 1 次即可
        """
        n = int(r["recall_n"] or 0)
        warm = (r["region"] in LONG_WARM_REGIONS or
                (r["salience"] or 0.0) >= LONG_WARM_SALIENCE)
        need = LONG_NEED_RECALL_WARM if warm else LONG_NEED_RECALL
        return n >= need

    # ============================================================
    # V7 第 10 件：中期池的出口
    # ------------------------------------------------------------
    # 病根（probe/_diag2_pool.md）：1066 条记忆进了中期池就再没出去过 ——
    # 0.35 只是「进门线」，进门之后没有 TTL。按 LAM_M 推算，它们在写进来
    # 第 27.8 天就跌破 0.35，但没有任何动作；要到第 150 天才触侵蚀，
    # 然后某一天集中被删 —— 那是「2027 年 4 月的同步炸弹」，不是遗忘，
    # 是一次性清仓。忘应该是慢慢淡，不是攒着一起断。
    #
    # 现在改成：**到期前先降级**。
    #   中期池里，按现在的衰减曲线 30 天内会跌破 THETA_MEDIUM 的，
    #   提前挪进长期池（长期池衰减慢两个数量级，等于「记住了但不再常提」），
    #   一次最多挪 20 条，且可以先只预览不落库（可回滚）。
    # 为什么不是直接删：硬盘够大，忘是检索行为不是删除行为（已归档那套同理）。
    # ============================================================
    def medium_exit_plan(self, horizon_days=0, now=None, limit=200):
        """算一遍：中期池里哪些在 horizon_days 内会跌破进门线。只读。"""
        now = float(now if now is not None else self.clock.now())
        out = []
        try:
            c = self.db.cursor()
            for r in c.execute("SELECT * FROM mem WHERE pool=?", (POOL_MEDIUM,)):
                try:
                    w = float(r["w"] or 0.0)
                except Exception:
                    continue
                if w <= 0:
                    continue
                if w <= THETA_MEDIUM:
                    days_left = 0.0
                else:
                    days_left = math.log(w / THETA_MEDIUM) / LAM_M / 86400.0
                if days_left <= float(horizon_days):
                    out.append({"id": int(r["id"]), "w": round(w, 3),
                                "days_left": round(days_left, 1),
                                "raw": (r["raw"] or "")[:40]})
            out.sort(key=lambda x: x["days_left"])
            return out[:int(limit)]
        except Exception as e:
            print("[card2] 中期池出口测算失败: %s" % e)
            return []

    def medium_exit_apply(self, horizon_days=0, limit=20, now=None, dry=False):
        """把算出来的前 limit 条挪进长期池。

        一次最多 20 条：万一哪天算错（比如 LAM_M 被改错），损失也只到 20 条，
        而且它们只是换了池子、内容一个字没少，能挪回来。
        """
        now = float(now if now is not None else self.clock.now())
        plan = self.medium_exit_plan(horizon_days=horizon_days, now=now,
                                     limit=max(1, int(limit)))
        if dry or not plan:
            return {"planned": len(plan), "moved": 0, "dry": bool(dry),
                    "ids": [x["id"] for x in plan]}
        moved = 0
        try:
            c = self.db.cursor()
            for x in plan:
                # 保留当前清晰度（至少不低于长期池地板），换池子、重置在池计时
                c.execute(
                    "UPDATE mem SET pool=?, w=MAX(COALESCE(w,0), ?), t_anchor=?,"
                    " pool_in_ts=?, tier=? WHERE id=?",
                    (POOL_LONG, W_FLOOR_LONG, now, now, TIER_MID, x["id"]))
                moved += 1
            self.db.commit()
        except Exception as e:
            print("[card2] 中期池出口落库失败: %s" % e)
        return {"planned": len(plan), "moved": moved, "dry": False,
                "ids": [x["id"] for x in plan]}


    USER_SPEECH_BONUS = 0.30   # 他本人说的话，海马分上加一点（V5.6.2-D：0.12→0.30）
    # 🔴 2026-11-09：我说的话跟他说的话，在「记不记得住」这件事上必须同级。
    #    原来是 0 —— 结果我自己讲过的内容只有 23.3% 活下来（他的 82.3%），
    #    库里只剩「他问过我什么」。见 _consolidate 里的完整说明与实测数据。
    SELF_SPEECH_BONUS = 0.32

    # 2026-11-11（V7 第 12 件）：自己的话**按类型**加权，不再一刀切 +0.32。
    # 为什么：统一加成会把「我随口猜的」和「我说过我会」记成同一种东西 ——
    # 猜的话被当成事实答出来，比忘记更糟。四类各给各的生死线：
    #   identity 我说我是谁（最硬，几乎不该消失）
    #   promise  我说过我会/下次（欠账，忘了等于不认账）
    #   episode  一般对话（正常）
    #   guess    推测、可能、大概（最轻，随时可以淡掉）
    SELF_BONUS_BY_KIND = {"identity": 0.42, "promise": 0.38,
                          "episode": 0.32, "guess": 0.16}
    REPEAT_SHORT_PENALTY = 0.5 # 重复短句打折（价值在模式里，不在这一条）
    REPEAT_SHORT_CHARS = 12      # 多短的句子算「随口一句」
    REPEAT_SHORT_N = 3           # 7 天内出现几次算「经常说」
    REPEAT_SHORT_WINDOW = 7 * DAY

    def _is_repeat_short(self, r, now=None):
        """这句是不是「经常说的短句」

        用户 m36485 的原话：「我说一句我回来了，这句话会让你开心，但是这样的
        话我会说很多遍，几乎每天都会说一次，所以说把它放进短期的应该够了吧」。

        判据很小：文本（去标点空格）≤12 字，且 7 天内在卡里出现过 ≥3 次。
        —— 不是判断它"没意义"，是判断它"重复"：重复的话，价值在模式里，
        不在这一条里。所以它不享受「他说的免筛」，让它按短期自己的曲线走完。
        """
        txt = _PUNCT_RE.sub("", (r["raw"] or ""))
        if not txt or len(txt) > self.REPEAT_SHORT_CHARS:
            return False
        now = now if now is not None else self.clock.now()
        # ⚠️ 不要拿 access_count 当"他常这么说"的证据：那本账记的是**被想起**
        # 的次数（recall 一次就 +1），跟"他重复说"是两回事。
        # 2026-11-08 修：之前这里有个 access_count >= 3 的分支，于是
        # 「一句被我想起过三次的话」会被当成"他常说的口头禅"打折 —— 正好打反。
        # 现在只认「同一句在卡里出现过几次」这一条证据。
        rows = self.db.execute(
            "SELECT raw FROM mem WHERE speaker='user' AND create_ts > ?",
            (now - self.REPEAT_SHORT_WINDOW,)).fetchall()
        n = 0
        for rr in rows:
            t2 = _PUNCT_RE.sub("", (rr["raw"] or ""))
            if t2 == txt:
                n += 1
        return n >= self.REPEAT_SHORT_N

    # ---- 印象层对外接口 -------------------------------------------------




    @staticmethod
    def _warm_ready(r, need_days, need_recall):
        """够不够格进小池（或从小池往上走）

        判据是「跨了几个不同日子」而不是总次数 —— 用户 m36716 说的是
        "在短期里被反复召回"，反复指的是隔了些时候又回来，不是一句话里连点十下。
        """
        try:
            days = int(r["recall_days"] or 0)
            n = int(r["recall_n"] or 0)
        except Exception:
            return False
        return days >= need_days and n >= need_recall

    def _to_warm(self, c, r, now, target):
        """搬进小池：换池、抬到存活下限、锚改成此刻"""
        wn = max(wn_row(r, now), W_FLOOR_MEDIUM)
        c.execute("UPDATE mem SET pool=?, w=?, t_anchor=?, tier=?,"
                  " pool_in_ts=? WHERE id=?",
                  (target, wn, now, TIER_MID, now, r["id"]))
        return target

    def _promote_warm(self, c, r, now):
        """从小池往上走：够长期票就进长期，否则进中期"""
        score = self._hippocampus_score(r)
        if (score >= THETA_LONG_STRICT and self._long_ticket_ok(r)
                and float(r["trust"] or 1.0) >= TRUST_BLOCK_PROMOTE
                and int(r["conflict"] or 0) != 1):
            target, floor = POOL_LONG, W_FLOOR_LONG
        else:
            target, floor = POOL_MEDIUM, W_FLOOR_MEDIUM
        wn = max(wn_row(r, now), floor)
        c.execute("UPDATE mem SET pool=?, w=?, t_anchor=?, tier=?,"
                  " pool_in_ts=? WHERE id=?",
                  (target, wn, now, TIER_MID, now, r["id"]))
        return target

    def _evict_short_overflow(self, now):
        """已停用：短期池不再按容量驱逐（V5.4，2026-11-07 用户拍板）

        「所有池都不设置容量限制」——所以「满 N 条就沉中期」这套容量机制
        整个拿掉。短期池现在**只是一段 7 天的时间窗**（`SHORT_MAX_TTL`）：
        七天之内全部留着，到期由 `_consolidate()` 按海马分决定沉到哪里，
        与「池子里现在有多少条」完全无关。
        """
        return []

    def _sink(self, now):
        """五空间下沉：LONG → ARCHIVE → DEEP → TRACE

        V5.0 §12.5 的核心思想：冷库不是「永不删除」，冷库也会忘，只是忘得极慢，
        而且有自己独属的一条曲线。目标是**总量在一定时间后达到动态平衡**。

        Little 定律 L = n × T，T = ln(w0/w_exit)/k。
        按每天 200 条新记忆算：LONG 36.5 万 / ARCHIVE 46.2 万 / DEEP 177.7 万，
        加起来约 1.17 GB —— 这就是这张卡跑几十年后的稳态大小。

        每步只降低可见度，不删内容；只有 DEEP → TRACE 才真的把正文换成计数。
        """
        c = self.db.cursor()
        events = []
        budget = SINK_BATCH

        # ① LONG → ARCHIVE
        if budget > 0:
            rows = c.execute(
                "SELECT * FROM mem WHERE pool=? LIMIT ?",
                (POOL_LONG, budget)).fetchall()
            for r in rows:
                # 底色层不下沉：它已经是最后一站了（改写在 supersede 里做）
                if r["tier"] in CORE_TIERS:
                    continue
                age = now - r["t_anchor"]
                if age < AGE_EXIT_LONG:
                    continue
                wn = wn_row(r, now)
                if wn >= W_EXIT_LONG:
                    continue
                c.execute("UPDATE mem SET pool=?, w=?, t_anchor=? WHERE id=?",
                          (POOL_ARCHIVE, wn, now, r["id"]))
                events.append((r["id"], "sink->archive", round(wn, 4)))
                budget -= 1

        # ② ARCHIVE → DEEP
        if budget > 0:
            rows = c.execute(
                "SELECT * FROM mem WHERE pool=? LIMIT ?",
                (POOL_ARCHIVE, budget)).fetchall()
            for r in rows:
                wn = wn_row(r, now)
                if wn >= W_EXIT_ARCHIVE:
                    continue
                c.execute("UPDATE mem SET pool=?, w=?, t_anchor=? WHERE id=?",
                          (POOL_DEEP, wn, now, r["id"]))
                events.append((r["id"], "sink->deep", round(wn, 6)))
                budget -= 1

        # ③ DEEP → TRACE（真正的「忘成统计」：内容没了，只剩「那天有过几条」）
        if budget > 0:
            rows = c.execute(
                "SELECT * FROM mem WHERE pool=? LIMIT ?",
                (POOL_DEEP, budget)).fetchall()
            for r in rows:
                wn = wn_row(r, now)
                if wn >= W_EXIT_DEEP:
                    continue
                day = time.strftime("%Y-%m-%d", time.localtime(r["create_ts"]))
                c.execute(
                    "INSERT INTO trace(day, region, count, ts) VALUES(?,?,1,?)",
                    (day, r["region"], now))
                self._forget_row(c, r["id"], "深层沉淀成统计（忘形）")
                events.append((r["id"], "sink->trace", day))
                budget -= 1

        return events

    def _ensure_person_table(self):
        """关系卡：一个人一行。只记关系和频次，不记评价（不做好感度打分）。"""
        c = self.db.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS person (
            uid       TEXT PRIMARY KEY,
            layer     TEXT NOT NULL DEFAULT '',
            first_ts  REAL NOT NULL DEFAULT 0,
            last_ts   REAL NOT NULL DEFAULT 0,
            n         INTEGER NOT NULL DEFAULT 0,
            regions   TEXT NOT NULL DEFAULT '{}'   -- {"chatter":3,"fact":1}
        )""")
        self.db.commit()

    def person_touch(self, uid, layer="", ts=None, region=""):
        """记一个人又出现了一次。uid 空则什么都不做。"""
        uid = str(uid or "").strip()
        if not uid:
            return None
        ts = float(ts or self.clock.now())
        try:
            self._ensure_person_table()
            c = self.db.cursor()
            row = c.execute("SELECT n, regions, first_ts FROM person WHERE uid=?",
                            (uid,)).fetchone()
            import json as _json
            if not row:
                regions = {}
                first_ts = ts
                n = 0
            else:
                n, rj, first_ts = int(row[0]), row[1] or "{}", float(row[2] or ts)
                try:
                    regions = _json.loads(rj) or {}
                except Exception:
                    regions = {}
            n += 1
            if region:
                regions[region] = int(regions.get(region, 0)) + 1
            c.execute("""INSERT INTO person(uid,layer,first_ts,last_ts,n,regions)
                         VALUES(?,?,?,?,?,?)
                         ON CONFLICT(uid) DO UPDATE SET
                           layer=CASE WHEN excluded.layer<>'' THEN excluded.layer
                                      ELSE person.layer END,
                           first_ts=MIN(person.first_ts, excluded.first_ts),
                           last_ts=excluded.last_ts, n=excluded.n,
                           regions=excluded.regions""",
                      (uid, layer or "", min(first_ts, ts), ts, n,
                       _json.dumps(regions, ensure_ascii=False)))
            self.db.commit()
            return n
        except Exception as e:
            print("[card2] person_touch 失败: %s" % e)
            return None

    def person_card(self, uid):
        """一个人的关系卡。没有记录就是没有，不编。"""
        uid = str(uid or "").strip()
        if not uid:
            return None
        self._ensure_person_table()
        row = self.db.execute(
            "SELECT uid,layer,first_ts,last_ts,n,regions FROM person WHERE uid=?",
            (uid,)).fetchone()
        if not row:
            return None
        import json as _json
        try:
            regions = _json.loads(row[5] or "{}")
        except Exception:
            regions = {}
        top = sorted(regions.items(), key=lambda kv: -kv[1])[:3]
        return {"uid": row[0], "layer": row[1] or "",
                "first_ts": row[2], "last_ts": row[3], "n": row[4],
                "regions": regions, "top_regions": top}

    def people(self, limit=30):
        self._ensure_person_table()
        rows = self.db.execute(
            "SELECT uid,layer,n,last_ts FROM person ORDER BY n DESC, last_ts DESC LIMIT ?",
            (int(limit),)).fetchall()
        return [{"uid": r[0], "layer": r[1] or "", "n": r[2], "last_ts": r[3]}
                for r in rows]

    def _ensure_soft_cols(self, c=None):
        """V5.3.3：软删除与隔离区两列。

        dead=1        逻辑删除 —— 行还在（哈希链、时间线、审计都对得上），
                      但任何检索都看不见它，也不会再被拼进上下文。
        quarantine=1  隔离区 —— 低信任/被判可疑的内容，默认不进上下文，
                      人工放行才回来。以前只靠打分压它，仍然会出现在结果里。
        """
        db = c if c is not None else self.db
        for col in ("dead", "quarantine"):
            try:
                db.execute("ALTER TABLE mem ADD COLUMN %s INTEGER DEFAULT 0" % col)
            except sqlite3.OperationalError:
                pass

    def _ensure_cond_table(self, c=None):
        """condition_expected：A 之后常跟着 B 的共现计数（V5.3.4）。

        只记「分区 → 分区」，不记原文。这张表是用来**猜接下来常发生什么**，
        不是用来复述 —— 粒度再细（按事件标签）就得让 LLM 标注，那又回到
        「解释不清为什么这么判断」的老问题上了。
        """
        c = c or self.db.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS condition_expected ("
                  "  a TEXT NOT NULL, b TEXT NOT NULL,"
                  "  n INTEGER NOT NULL DEFAULT 0,"
                  "  last_ts REAL, PRIMARY KEY (a, b))")
        self.db.commit()

    def _bump_expected(self, c, a, b, at):
        if not a or not b or a == b:
            return
        c.execute("INSERT INTO condition_expected (a,b,n,last_ts) "
                  "VALUES (?,?,1,?) ON CONFLICT(a,b) DO UPDATE SET "
                  "n=n+1, last_ts=excluded.last_ts", (a, b, at))

    def expected_after(self, region, k=3):
        """某种事之后常跟什么：[(region, 概率, 次数)]，概率 = n / 该前缀总数。"""
        if not region:
            return []
        try:
            rows = self.db.execute(
                "SELECT b, n FROM condition_expected WHERE a=? "
                "ORDER BY n DESC LIMIT ?", (region, max(1, int(k)))).fetchall()
            tot = self.db.execute(
                "SELECT SUM(n) FROM condition_expected WHERE a=?",
                (region,)).fetchone()[0] or 0
        except sqlite3.OperationalError:
            return []
        if not tot:
            return []
        return [(r[0], float(r[1]) / tot, int(r[1])) for r in rows]

    def forget(self, mid, mode="logical", reason=""):
        """三档删除语义（V5.3.3）。别把这三件事混成一句"删了"。

        logical  逻辑删除：行保留、打 dead=1、立刻退出检索。可恢复。
        natural  自然遗忘：走 _forget_row（登记台账 + 删行 + 断媒体引用），
                 和到期淘汰是同一条路，只是理由由人写。
        purge    彻底删除：行、细节、向量指纹、媒体引用、召回记录一起清，
                 再把 WAL 折掉。**备份和外部锚不在范围内**，要一起清得手动做，
                 这一点必须在返回里如实说，不能让人以为"没了"就是全没了。
        """
        mid = int(mid)
        mode = (mode or "logical").lower()
        what = {"logical": "逻辑删除", "natural": "自然遗忘",
                "purge": "彻底删除"}.get(mode)
        if what is None:
            return {"ok": False, "error": "bad_mode",
                    "detail": "mode 只能是 logical / natural / purge"}
        c = self.db
        now = self.clock.now()
        row = c.execute("SELECT id,raw FROM mem WHERE id=?", (mid,)).fetchone()
        if not row:
            return {"ok": False, "error": "not_found", "detail": "没有这条"}
        note = reason.strip() or ("用户要求删除（%s）" % what)
        if mode == "logical":
            c.execute("UPDATE mem SET dead=1, last_active_ts=? WHERE id=?",
                      (now, mid))
            self.db.commit()
            return {"ok": True, "mid": mid, "mode": mode, "what": what,
                    "recoverable": True,
                    "detail": "打上 dead=1，检索和上下文都看不到它了；"
                              "行还在，能恢复"}
        # natural / purge 都要先算哈希再删
        self._forget_row(c, mid, note)
        if mode == "purge":
            for sql, args in (
                ("DELETE FROM detail WHERE parent_id=?", (mid,)),
                ("DELETE FROM recall_event WHERE mem_id=?", (mid,)),
            ):
                try:
                    c.execute(sql, args)
                except sqlite3.OperationalError:
                    pass
            try:
                c.execute("DELETE FROM mem_birth WHERE mid=?", (mid,))
            except sqlite3.OperationalError:
                pass
            self._emb_cache.pop(mid, None)
        self.db.commit()
        out = {"ok": True, "mid": mid, "mode": mode, "what": what,
               "recoverable": False,
               "detail": "行和细节都删了，删除台账留了 mid/哈希/理由（不含原文）"}
        if mode == "purge":
            try:
                c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass
            out["boundary"] = ("已清：主库行、细节、向量缓存、召回记录、WAL。"
                               "未清：磁盘上的 card.db.bak-* 备份、"
                               "G: 便携包副本、外部锚（anchor.json）。"
                               "要连备份一起清得单独说。")
        return out

    # ===== V5.4 收件层 inbox =====
    # 为什么要有这一层（2026-11-07 用户定的）：
    #   1) 本窗口靠我每轮手动 /report 写两条，漏一轮就丢（11-07「号被封 7 天」
    #      就是这么丢的）；
    #   2) QQ 走 qq_guard 只按规则筛重点，普通闲聊只落 todo.jsonl；
    #   3) B站/抖音/小说我看完只写进 whale-lab notes，卡完全不知道。
    # 所以：所有来源先进这里（原文一字不落、落盘），再挑「有意义的原文」进短期池。
    # 它在短期池**前面**，只负责装下 + 除杂，不做分类、不打分。
    def _ensure_inbox(self, c=None):
        c = c or self.db.cursor()
        c.execute(
            "CREATE TABLE IF NOT EXISTS inbox("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "ts REAL, source TEXT, channel TEXT, who TEXT, uid TEXT, gid TEXT,"
            "kind TEXT, text TEXT, kept INTEGER DEFAULT 0, kept_mid INTEGER,"
            "note TEXT)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_inbox_ts ON inbox(ts)")
        return c

    def inbox_add(self, text, source="", channel="private", who="", uid="",
                  gid="", kind="text", ts=None, note="", kept=0, kept_mid=None):
        """把一条原文塞进收件层。返回 id（失败返回 None，不抛）。"""
        try:
            t = (text or "").strip()
            if not t:
                return None
            c = self._ensure_inbox()
            now = float(ts if ts is not None else self.clock.now())
            c.execute(
                "INSERT INTO inbox(ts,source,channel,who,uid,gid,kind,text,"
                "kept,kept_mid,note) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (now, source or "", channel or "private", who or "", str(uid or ""),
                 str(gid or ""), kind or "text", t, int(kept or 0),
                 kept_mid, note or ""))
            self.db.commit()
            return c.lastrowid
        except Exception as e:
            print("[card2] 收件层写入失败: %s" % e)
            return None

    def inbox_list(self, limit=50, hours=None, unkept_only=False, full=False):
        """看收件层里有什么。text 默认截到 300 字，full=True 给原文。"""
        try:
            c = self.db.cursor()
            self._ensure_inbox(c)
            where, args = [], []
            if hours:
                where.append("ts >= ?")
                args.append(float(self.clock.now()) - float(hours) * 3600.0)
            if unkept_only:
                where.append("COALESCE(kept,0)=0")
            sql = ("SELECT * FROM inbox"
                   + (" WHERE " + " AND ".join(where) if where else "")
                   + " ORDER BY ts DESC LIMIT ?")
            args.append(int(limit))
            out = []
            for r in c.execute(sql, args):
                d = dict(r)
                if not full:
                    t = d.get("text") or ""
                    d["text"] = t if len(t) <= 300 else t[:300] + "…"
                    d["text_len"] = len(t)
                out.append(d)
            return out
        except Exception as e:
            print("[card2] 收件层读取失败: %s" % e)
            return []

    def inbox_mark(self, ids, kept=1, kept_mid=None):
        """标记「这几条已经挑进记忆了」。"""
        try:
            if isinstance(ids, (int, str)):
                ids = [ids]
            ids = [int(i) for i in ids]
            if not ids:
                return 0
            c = self._ensure_inbox()
            q = "UPDATE inbox SET kept=? WHERE id IN (%s)" % ",".join("?" * len(ids))
            c.execute(q, [int(kept or 0)] + ids)
            if kept_mid is not None:
                c.execute("UPDATE inbox SET kept_mid=? WHERE id=?", (kept_mid, ids[0]))
            self.db.commit()
            return len(ids)
        except Exception as e:
            print("[card2] 收件层标记失败: %s" % e)
            return 0

    # ---- 收件层 → 短期池 的挑选器（V5.6.1）---------------------------------
    # 用户 2026-11-08 的规矩：收件层只负责"装下"，判断价值是**工序**，交给这里。
    # 它不调模型，只看几件朴素的事：是不是人说的话、有没有信息、是不是重复。
    _PICK_WORDS = ("为什么", "怎么", "原理", "结论", "因为", "发现", "学到",
                   "记得", "说过", "答应", "喜欢", "讨厌", "难受", "开心",
                   "考试", "作业", "放学", "生日", "妈妈", "爸爸", "同学")

    def inbox_score(self, it, now=None, recent_texts=None):
        """给收件层的一条打「信噪比」。返回 (score, why)。纯规则，可解释。

        注意：这不是在判断"这条重要不重要"，而是在判断"这条是不是噪声"。
        用户定的分工是：收件层装下所有原文 → 除杂去噪 → 推短期池。
        """
        t = (it.get("text") or "").strip()
        if not t:
            return 0.0, "空"
        score = 0.0
        why = []
        n = len(t)
        # ── 除杂：这些是"噪"，不是"不重要" ──────────────────────────
        import re as _re0
        if not _re0.search(r"[\u4e00-\u9fffA-Za-z0-9]", t):
            return 0.0, "纯符号/表情"
        if n <= 2:
            return 0.05, "太短（%d 字）" % n
        if _re0.match(r"^(.{1,3})\1{2,}$", t.strip()):
            return 0.05, "重复字（如哈哈哈）"
        if n < 6 and not _re0.search(r"[\u4e00-\u9fff]{3,}", t):
            return 0.12, "碎片"
        if n < 4:
            return 0.15, "太短（%d 字）" % n
        if 14 <= n <= 300:
            score += 0.35
            why.append("长度合适")
        elif n > 300:
            score += 0.28
            why.append("长文")
        else:
            score += 0.18
        # 是不是"人说的话"：私聊/本窗口 > 群
        ch = (it.get("channel") or "").lower()
        if ch in ("private", "chat", "dsh"):
            score += 0.30
            why.append("一对一说的话")
        elif ch == "group":
            score += 0.05
            why.append("群消息")
        # 别家 AI 的话：只当素材，优先级压低
        if (it.get("note") or "").find("bot") >= 0:
            score -= 0.25
            why.append("疑似别家 AI")
        # 有没有信息量：数字/日期、专名、引号书名号、提问
        if any(ch.isdigit() for ch in t):
            score += 0.10
            why.append("带数字")
        import re as _re
        if _re.search(r"[A-Za-z]{3,}", t):
            score += 0.06
            why.append("带英文/专名")
        if _re.search(r"[「」“”《》]", t):
            score += 0.06
            why.append("带引用/书名")
        if ("?" in t) or ("？" in t):
            score += 0.08
            why.append("提问")
        hit = [w for w in self._PICK_WORDS if w in t]
        if hit:
            score += min(0.18, 0.06 * len(hit))
            why.append("含关键词" + "/".join(hit[:3]))
        # 重复：跟最近 20 条比 Jaccard
        if recent_texts:
            for o in recent_texts:
                if not o or o == t:
                    continue
                a = set(t)
                b = set(o)
                if not a or not b:
                    continue
                j = len(a & b) / float(len(a | b))
                if j >= 0.80:
                    score -= 0.30
                    why.append("与最近的重复（%.2f）" % j)
                    break
        score = max(0.0, min(1.0, score))
        return score, "、".join(why)

    def inbox_pick(self, limit=40, hours=None, commit=False, min_score=None,
                   now=None):
        """从收件层挑原文进短期池。

        commit=False：只给候选与理由（默认，安全侧）。
        commit=True ：真写卡（source/channel/who 全带过去），并回填 kept/kept_mid。
        进短期池的永远是**整条原文**，不改写、不摘要 —— 用户 11-08 明确。
        """
        now = float(now if now is not None else self.clock.now())
        thr = float(min_score if min_score is not None
                    else getattr(self, "INBOX_PICK_MIN", 0.30))
        items = self.inbox_list(limit=max(int(limit) * 3, 60), hours=hours,
                                unkept_only=True, full=True)
        recent = [(x.get("text") or "")[:400] for x in items[:20]]
        picked, dropped = [], []
        for it in items:
            sc, why = self.inbox_score(it, now=now, recent_texts=recent)
            row = {"id": it.get("id"), "score": round(sc, 3), "why": why,
                   "ts": it.get("ts"), "source": it.get("source"),
                   "channel": it.get("channel"), "who": it.get("who"),
                   "text": (it.get("text") or "")[:200],
                   "text_len": len(it.get("text") or "")}
            (picked if sc >= thr else dropped).append(row)
            if len(picked) >= int(limit):
                break
        picked.sort(key=lambda x: -x["score"])
        if not commit:
            return {"ok": True, "committed": False, "threshold": thr,
                    "picked": picked, "dropped": dropped[:20],
                    "n_picked": len(picked), "n_dropped": len(dropped)}

        wrote = []
        c = self.db.cursor()
        self._ensure_inbox(c)
        by_id = {it.get("id"): it for it in items}
        for row in picked:
            src = by_id.get(row["id"]) or {}
            txt = src.get("text") or ""
            if not txt.strip():
                continue
            ch = (src.get("channel") or "").lower()
            speaker = "user" if ch in ("private", "chat", "dsh") else "other"
            region = "episode"
            mid = self.remember(
                txt, source=(src.get("source") or ""), speaker=speaker,
                region=region, salience=0.62, normalize=False,
                about_uid=(src.get("uid") or "") if speaker == "user" else "",
                pool=POOL_SHORT, ts=src.get("ts"))
            self.inbox_mark([row["id"]], kept=1, kept_mid=mid)
            wrote.append({"id": row["id"], "mid": mid, "score": row["score"]})
        self.db.commit()
        return {"ok": True, "committed": True, "threshold": thr,
                "wrote": wrote, "n_wrote": len(wrote)}

    def inbox_gc(self, ttl_hours=60.0, now=None):
        """收件层过期清理：没被挑中的整条删；被挑中的只清正文留个壳。

        用户定的保留时间是 60 小时。删掉的是「没被判定有意义」的那些 —
        这不是压缩（压缩是改写），是「没挑中就让它走」。
        """
        stats = {"dropped": 0, "blanked": 0}
        try:
            now = float(now if now is not None else self.clock.now())
            cut = now - float(ttl_hours) * 3600.0
            c = self._ensure_inbox()
            c.execute("DELETE FROM inbox WHERE ts < ? AND COALESCE(kept,0)=0", (cut,))
            stats["dropped"] = c.rowcount or 0
            c.execute("UPDATE inbox SET text='' WHERE ts < ? AND COALESCE(kept,0)=1"
                      " AND COALESCE(text,'')<>''", (cut,))
            stats["blanked"] = c.rowcount or 0
            self.db.commit()
        except Exception as e:
            print("[card2] 收件层清理失败: %s" % e)
        return stats

    def inbox_stats(self):
        try:
            c = self._ensure_inbox()
            rows = list(c.execute(
                "SELECT COALESCE(kept,0) k, COUNT(*) n FROM inbox GROUP BY k"))
            tot = sum(int(r["n"]) for r in rows)
            kept = sum(int(r["n"]) for r in rows if int(r["k"]) == 1)
            return {"total": tot, "kept": kept, "unkept": tot - kept}
        except Exception:
            return {"total": 0, "kept": 0, "unkept": 0}

    def quarantine_rows(self, limit=50):
        """看一眼隔离区里都有谁 —— 默认它们不进上下文，但也得能查。"""
        rows = self.db.execute(
            "SELECT id,raw,trust,conflict,conflict_why,create_ts,region "
                "FROM mem WHERE COALESCE(quarantine,0)=1 "
                "ORDER BY create_ts DESC LIMIT ?", (int(limit),)).fetchall()
        return [{"mid": r["id"], "text": (r["raw"] or "")[:120],
                 "trust": r["trust"], "why": r["conflict_why"] or "",
                 "ts": r["create_ts"], "region": r["region"]} for r in rows]

    def release_quarantine(self, mid):
        """人工放行：确认真实无害之后，把它从隔离区拿出来（留痕在 history）。"""
        mid = int(mid)
        r = self.db.execute("SELECT history FROM mem WHERE id=?", (mid,)).fetchone()
        if not r:
            return {"ok": False, "error": "not_found", "detail": "没有这条"}
        hist = r["history"] or ""
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(self.clock.now()))
        hist = (hist + chr(10) if hist else "") + "%s 人工解除隔离" % stamp
        self.db.execute(
            "UPDATE mem SET quarantine=0, trust=1.0, conflict=0, "
            "conflict_why='', history=? WHERE id=?", (hist, mid))
        self.db.commit()
        return {"ok": True, "mid": mid}

    def _forget_row(self, c, mid, reason):
        """删一条记忆的**唯一出口**：先给它登记台账，再连细节一起删。

        以前四处各删各的（侵蚀、合并、沉淀、回收），删完就没了痕迹：
        出生证还在库里，校验只能报「记忆被删过」，跟被人抽走记录长得一样。
        现在统一走这里，删之前先写 mem_tomb —— 谁、什么时候、为什么没的，
        都在档案上留一笔。reason 是给人看的，别写代码黑话。
        """
        self._tomb_add(c, mid, reason)
        # 🗄 先抄进冷档，再删（2026-11-09）：硬盘没有上限，忘了 ≠ 销毁。
        self._archive_add(c, mid, reason)
        # 画面跟着记忆走：先断开引用；如果这帧再没人引用，就从"有主"变成"残影"。
        # 不在这里直接删帧 —— 残影期是留给"还想再看一眼"的窗口。
        try:
            med = [r[0] for r in c.execute(
                "SELECT media_id FROM media_ref WHERE mem_id=?", (int(mid),)).fetchall()]
            c.execute("DELETE FROM media_ref WHERE mem_id=?", (int(mid),))
            now = self.clock.now()
            for m_id in med:
                left = c.execute("SELECT COUNT(*) FROM media_ref WHERE media_id=?",
                                 (m_id,)).fetchone()[0]
                if left == 0:
                    c.execute("UPDATE media SET orphan_ts=? WHERE id=? AND orphan_ts=0",
                              (now, m_id))
        except Exception:
            pass
        c.execute("DELETE FROM detail WHERE parent_id=?", (mid,))
        c.execute("DELETE FROM mem WHERE id=?", (mid,))

    # ── 工作日志（那张「桌子」）────────────────────────────────────────────
    #   注意：这里**不进 mem 池**、不参与召回打分与衰减。它只是笔记本，
    #   目的是让「换了窗口的我」一睁眼就知道上一轮干到哪、为什么这么干。





    def recall_trace_add(self, query, flags, stages, top, picked,
                         n_lines=0, ms=0, keep=400):
        """记一条召回漏斗（V7 第 8 件）。只留最近 keep 条，越老越先删。

        这些字段是给排错用的：拿到一道「她没想起来」的题，先看 picked 里有没有，
        没有再看 stages 是哪一层筛掉的，还在就看他排第几、各分项多少 ——
        四种失败（没存 / 被筛 / 排名靠后 / 进了上下文没用）一眼分开。
        """
        try:
            if (os.environ.get("WHALE_RECALL_TRACE", "1") or "").strip() == "0":
                return None
            c = self.db.cursor()
            c.execute(
                "INSERT INTO recall_trace(ts,query,flags,stages,top,picked,n_lines,ms)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (time.time(), (query or "")[:200],
                 json.dumps(flags or {}, ensure_ascii=False),
                 json.dumps(stages or {}, ensure_ascii=False),
                 json.dumps(top or [], ensure_ascii=False),
                 json.dumps(picked or [], ensure_ascii=False),
                 int(n_lines or 0), int(ms or 0)))
            c.commit()
            rid = c.lastrowid
            # 只留最近 keep 条：这是排错用的窗口，不是账本
            c.execute("DELETE FROM recall_trace WHERE id <= ?",
                      (int(rid) - int(keep),))
            c.commit()
            return rid
        except Exception:
            return None

    def recall_trace_recent(self, n=10):
        try:
            c = self.db.cursor()
            rows = c.execute(
                "SELECT * FROM recall_trace ORDER BY id DESC LIMIT ?",
                (max(1, int(n)),)).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                for k in ("flags", "stages", "top", "picked"):
                    try:
                        d[k] = json.loads(d.get(k) or "null")
                    except Exception:
                        pass
                out.append(d)
            return out
        except Exception:
            return []


    def archive_search(self, query, k=3, min_score=0.22):
        """在冷档里按字面翻旧账（不懂语义，只认字）。

        用途只有一个：**这次正常召回一条都没想起来时**，说明问的可能是很久以前的事。
        这时才去硬盘深处翻，"忘了"的东西还能捞回来一条。
        """
        q = (query or "").strip()
        if len(q) < 3:
            return []
        qk = set()
        for seg in re.findall(r"[\u4e00-\u9fa5]+", q):
            for i in range(len(seg) - 1):
                qk.add(seg[i:i + 2])
        qk = {x for x in qk if not x in _ARCH_STOP}
        if not qk:
            return []
        rows = self.db.execute(
            "SELECT mid,raw,core,create_ts,details FROM mem_archive").fetchall()
        scored = []
        for r in rows:
            txt = (r["core"] or "") + " " + (r["raw"] or "")
            try:
                det = json.loads(r["details"] or "[]")
                txt += " " + " ".join((d.get("t") or "") for d in det)
            except Exception:
                pass
            tk = set()
            for seg in re.findall(r"[\u4e00-\u9fa5]+", txt):
                for i in range(len(seg) - 1):
                    tk.add(seg[i:i + 2])
            if not tk:
                continue
            _hit = qk & tk
            sc = len(_hit) / float(len(qk))
            # 只认「至少两个实词真的撞上」——单字撞上太容易（"今天""自己"），
            # 那条旧账跟她现在问的事根本没关系。
            if len(_hit) >= 2 and sc >= min_score:
                scored.append((sc, r))
        scored.sort(key=lambda x: (-x[0], -float(x[1]["create_ts"] or 0)))
        out = []
        for sc, r in scored[:k]:
            out.append({"mid": r["mid"], "raw": r["raw"], "core": r["core"],
                        "create_ts": r["create_ts"], "score": sc,
                        "details": r["details"]})
        return out


    # ---------- 媒体记忆（画面）：设计见 docs/MEDIA-MEMORY.md ----------
    # 一段画面自己一条寿命，跟文字那条**不互传**：文字掉到多低都不影响画面，
    # 反之亦然。两条只在索引上相连（想到那句话，知道还有张图）。
    def media_root(self):
        """媒体目录（卡目录下的 media/）。卡本体不含 blob —— 卡要能整个拷走。"""
        p = os.path.join(self.card_dir, MEDIA_DIR)
        try:
            os.makedirs(p, exist_ok=True)
        except Exception:
            pass
        return p

    def media_weight_now(self, w0, t_anchor, now=None, pinned=0):
        """画面权重（薄封装）：公式在模块级那一份，控制台画曲线共用，免得两份分家。"""
        now = self.clock.now() if now is None else now
        return media_weight_now(w0, t_anchor, now, pinned)

    def attach_media(self, mem_id, sha256, caption="", tags=None, kind="image",
                     mime="image/webp", path="", thumb="", w=0, h=0, dur=0.0,
                     nbytes=0, t0=None, t1=None, dialogue_id=None,
                     source="user", trust=1.0, w0=1.0, pinned=0):
        """把一帧画面挂到一条记忆上。

        同一个文件（sha256 相同）只存一份，多条记忆共享 —— 所以删记忆时
        要看引用计数，不能删一条就把图带走。
        """
        c = self.db.cursor()
        row = c.execute("SELECT id FROM mem WHERE id=?", (int(mem_id),)).fetchone()
        if row is None:
            return {"ok": False, "reason": "no_such_mem"}
        sha = (sha256 or "").strip().lower()
        if len(sha) != 64:
            return {"ok": False, "reason": "bad_sha256"}
        # 容量硬上限：宁可少收一张，也别把盘塞满
        try:
            used = c.execute("SELECT COALESCE(SUM(bytes),0) FROM media").fetchone()[0]
            if nbytes and used + int(nbytes) > MEDIA_CAP_MB * 1024 * 1024:
                return {"ok": False, "reason": "cap_exceeded"}
        except Exception:
            pass
        now = self.clock.now()
        exists = c.execute("SELECT * FROM media WHERE sha256=?", (sha,)).fetchone()
        if exists is None:
            tags_json = json.dumps(tags or [], ensure_ascii=False)
            try:
                bh = hashlib.sha256(
                    (sha + "|" + caption + "|" + kind).encode("utf-8")).hexdigest()
            except Exception:
                bh = ""
            c.execute(
                "INSERT INTO media(sha256,kind,mime,w,h,dur,bytes,path,thumb,caption,"
                "tags,t0,t1,dialogue_id,source,trust,w0,t_anchor,pinned,birth_hash,ts)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sha, kind, mime, int(w), int(h), float(dur or 0), int(nbytes or 0),
                 path or "", thumb or "", caption or "", tags_json,
                 t0, t1, dialogue_id, source, float(trust), float(w0), now,
                 int(pinned or 0), bh, now))
            mid_media = c.execute("SELECT id FROM media WHERE sha256=?", (sha,)).fetchone()[0]
        else:
            mid_media = exists["id"]
            # 又见面了：把 caption 补上（原来空的才补，不覆盖已有的），权重不重置
            if caption and not exists["caption"]:
                c.execute("UPDATE media SET caption=? WHERE id=?", (caption, mid_media))
            c.execute("UPDATE media SET pinned=? WHERE id=? AND ?=1",
                      (1, mid_media, int(pinned or 0)))
        c.execute("INSERT OR REPLACE INTO media_ref(mem_id,media_id,ts) VALUES(?,?,?)",
                  (int(mem_id), int(mid_media), now))
        # 有引用了就不是孤儿
        c.execute("UPDATE media SET orphan_ts=0 WHERE id=?", (int(mid_media),))
        self.db.commit()
        return {"ok": True, "media_id": int(mid_media), "dup": exists is not None}

    def media_of(self, mem_id, now=None):
        """这条记忆身上还有哪些画面（含状态与当前权重）。"""
        now = self.clock.now() if now is None else now
        c = self.db.cursor()
        out = []
        rows = c.execute(
            "SELECT m.* FROM media m JOIN media_ref r ON r.media_id=m.id"
            " WHERE r.mem_id=? ORDER BY m.id", (int(mem_id),)).fetchall()
        for r in rows:
            wv = self.media_weight_now(r["w0"], r["t_anchor"], now, r["pinned"])
            out.append({
                "id": r["id"], "sha256": r["sha256"], "caption": r["caption"],
                "kind": r["kind"], "tags": json.loads(r["tags"] or "[]"),
                "w": round(wv, 4), "pinned": bool(r["pinned"]),
                "orphan": bool(r["orphan_ts"]),
                "alive": wv >= MEDIA_DROP_W or bool(r["pinned"]),
                "recall_n": r["recall_n"], "w": round(wv, 4),
            })
        return out

    def media_touch(self, media_id, now=None):
        """看一眼：加强 + 重置计时。画面靠"被翻出来"活着。

        w ← min(1.0, w×1.45 + 0.10)。比文字猛（文字是 +0.08 加法），
        因为画面没有别的维持途径：常看就记得牢，不看就真的没了。
        """
        now = self.clock.now() if now is None else now
        c = self.db.cursor()
        r = c.execute("SELECT * FROM media WHERE id=?", (int(media_id),)).fetchone()
        if r is None:
            return {"ok": False, "reason": "no_such_media"}
        wv = self.media_weight_now(r["w0"], r["t_anchor"], now, 0)
        new = min(1.0, wv * MEDIA_GAIN_MULT + MEDIA_GAIN_ADD)
        c.execute("UPDATE media SET w0=?, t_anchor=?, recall_n=COALESCE(recall_n,0)+1,"
                  " t_last_recall=?, orphan_ts=0 WHERE id=?",
                  (new, now, now, int(media_id)))
        self.db.commit()
        return {"ok": True, "w": round(new, 4), "before": round(wv, 4)}

    def _media_tomb_add(self, c, sha256, caption, reason, weight=0.0, ts=None):
        try:
            c.execute("INSERT OR REPLACE INTO media_tomb(sha256,caption,ts,reason,"
                      "weight_at_drop) VALUES(?,?,?,?,?)",
                      (sha256, caption or "", ts or time.time(), reason, float(weight or 0)))
        except Exception:
            pass

    def _forget_media(self, c, media_id, reason, now=None, weight=0.0):
        """删一帧的**唯一出口**：先记台账（含当时那句话），再删文件、删引用、删行。

        留 caption 是有用的：以后能答"那张画面 11 月 20 号忘了，当时写的是
        「路边摊吃烧烤」" —— 忘了内容，但记得忘过。
        """
        r = c.execute("SELECT * FROM media WHERE id=?", (int(media_id),)).fetchone()
        if r is None:
            return False
        self._media_tomb_add(c, r["sha256"], r["caption"], reason, weight, now)
        for rel in (r["path"], r["thumb"]):
            if not rel:
                continue
            try:
                p = rel if os.path.isabs(rel) else os.path.join(self.card_dir, rel)
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass
        c.execute("DELETE FROM media_ref WHERE media_id=?", (int(media_id),))
        c.execute("DELETE FROM media WHERE id=?", (int(media_id),))
        return True

    def _media_tick(self, now=None):
        """挂进 tick：画面自己风化。到线删文件，文字那条一点不动。"""
        now = self.clock.now() if now is None else now
        c = self.db.cursor()
        events = []
        for r in c.execute("SELECT * FROM media").fetchall():
            if r["pinned"]:
                continue
            n_ref = c.execute("SELECT COUNT(*) FROM media_ref WHERE media_id=?",
                              (r["id"],)).fetchone()[0]
            if n_ref == 0:
                # 文字先没了 → 进残影期（还能被打开救回，过期才删）
                if not r["orphan_ts"]:
                    c.execute("UPDATE media SET orphan_ts=? WHERE id=?", (now, r["id"]))
                    r = c.execute("SELECT * FROM media WHERE id=?", (r["id"],)).fetchone()
                age_d = max(0.0, now - float(r["orphan_ts"] or now)) / DAY
                if age_d > MEDIA_ORPHAN_DAYS:
                    self._forget_media(c, r["id"], "残影期到（文字早就忘了它）",
                                       now, r["w0"])
                    events.append((r["id"], "orphan_expired", r["sha256"][:8]))
                    continue
            wv = self.media_weight_now(r["w0"], r["t_anchor"], now, 0)
            if wv < MEDIA_DROP_W:
                days = int(max(0.0, now - float(r["t_anchor"] or now)) / DAY)
                self._forget_media(c, r["id"],
                                   "画面权重到线（%d 天没再翻过）" % days, now, wv)
                events.append((r["id"], "dropped", r["sha256"][:8]))
        self.db.commit()
        return events

    def verify_media(self):
        """媒体校验：分得清「按规则删了」和「被人拿走了」。"""
        c = self.db.cursor()
        bad = []
        alive = 0
        for r in c.execute("SELECT * FROM media").fetchall():
            alive += 1
            rel = r["path"] or ""
            if not rel:
                continue
            p = rel if os.path.isabs(rel) else os.path.join(self.card_dir, rel)
            if not os.path.exists(p):
                bad.append("帧文件不见了：id=%d sha=%s（台账里也没有）"
                           % (r["id"], (r["sha256"] or "")[:12]))
        tombs = c.execute("SELECT COUNT(*) FROM media_tomb").fetchone()[0]
        known = []
        for t in c.execute("SELECT * FROM media_tomb ORDER BY ts DESC LIMIT 8").fetchall():
            known.append({"sha256": (t["sha256"] or "")[:12], "caption": t["caption"],
                          "reason": t["reason"], "ts": t["ts"]})
        return {"ok": not bad, "alive": alive, "bad": bad,
                "tomb_n": tombs, "known_drops": known}

    def media_stats(self):
        """控制台要的数：多少帧、占多大、多少在残影期、双曲线的两套参数。"""
        c = self.db.cursor()
        now = self.clock.now()
        n = c.execute("SELECT COUNT(*) FROM media").fetchone()[0]
        used = c.execute("SELECT COALESCE(SUM(bytes),0) FROM media").fetchone()[0]
        orphan = c.execute("SELECT COUNT(*) FROM media WHERE orphan_ts>0").fetchone()[0]
        tomb = c.execute("SELECT COUNT(*) FROM media_tomb").fetchone()[0]
        pinned = c.execute("SELECT COUNT(*) FROM media WHERE pinned=1").fetchone()[0]
        wsum = 0.0
        for r in c.execute("SELECT w0,t_anchor,pinned FROM media").fetchall():
            wsum += self.media_weight_now(r["w0"], r["t_anchor"], now, r["pinned"])
        return {"n": n, "bytes": used, "mb": round(used / 1048576.0, 2),
                "orphan": orphan, "tomb": tomb, "pinned": pinned,
                "avg_w": round(wsum / n, 3) if n else 0.0,
                "cap_mb": MEDIA_CAP_MB,
                "decay": {"b": MEDIA_DECAY_B, "tau_days": MEDIA_TAU_DAYS,
                          "drop_w": MEDIA_DROP_W, "orphan_days": MEDIA_ORPHAN_DAYS,
                          "gain_mult": MEDIA_GAIN_MULT, "gain_add": MEDIA_GAIN_ADD}}

    def _gc(self):
        now = self.clock.now()
        c = self.db.cursor()
        for r in c.execute("SELECT * FROM mem").fetchall():
            # ARCHIVE / DEEP 有自己的出口（_sink），不能被这里的通用回收线砍掉
            if r["pool"] in (POOL_ARCHIVE, POOL_DEEP):
                continue
            # 底色层永不回收：它只能被 supersede（改写），不能被"清晰度归零"抹掉
            if r["tier"] in CORE_TIERS:
                continue
            wn = wn_row(r, now)
            if wn < CLARITY_GC:
                self._forget_row(c, r["id"], "清晰度归零回收")
        self.db.commit()

    # ---------- 底色（五层记忆里的 core_*） ----------
    def core_stats(self):
        """三层底色的条数，给控制台画比例条用"""
        c = self.db.cursor()
        out = {}
        for t in CORE_TIERS:
            n = c.execute("SELECT COUNT(*) FROM mem WHERE tier=?",
                          (t,)).fetchone()[0]
            out[t] = n
        out["mid"] = c.execute(
            "SELECT COUNT(*) FROM mem WHERE tier='mid'").fetchone()[0]
        out["short"] = c.execute(
            "SELECT COUNT(*) FROM mem WHERE tier='short'").fetchone()[0]
        return out

    def set_tier(self, mid, tier, kind=None, subject_key=None,
                 valid_until=None):
        """把一条记忆搬到某一层。core_* 会被抬到 long 池并不再衰减。"""
        if tier not in CORE_TIERS and tier not in (TIER_MID, TIER_SHORT):
            raise ValueError("不认识的层：%r" % (tier,))
        c = self.db.cursor()
        now = self.clock.now()
        if tier in CORE_TIERS:
            c.execute(
                "UPDATE mem SET tier=?, pool=?, w=MAX(w,0.6), t_anchor=?,"
                " erase_stage=0 WHERE id=?",
                (tier, POOL_LONG, now, int(mid)))
        else:
            c.execute("UPDATE mem SET tier=? WHERE id=?", (tier, int(mid)))
        if subject_key is not None:
            c.execute("UPDATE mem SET subject_key=? WHERE id=?",
                      (subject_key or "", int(mid)))
        if valid_until is not None:
            c.execute("UPDATE mem SET valid_until=? WHERE id=?",
                      (float(valid_until or 0), int(mid)))
        self.db.commit()
        return True

    def refresh_about_layers(self):
        """关系标签改过之后，重算所有带 about_uid 的行的 about_layer。

        不这么做的话，把某人从 friend 提到 close 之后，他过去的闲聊
        还是按旧层算寿命 —— 关系变了，命也该跟着变。
        """
        c = self.db.cursor()
        rows = c.execute(
            "SELECT id, about_uid, about_layer FROM mem"
            " WHERE about_uid IS NOT NULL AND about_uid != ''").fetchall()
        n = 0
        for r in rows:
            lay = self.person_layer_of(r["about_uid"])
            if lay != (r["about_layer"] or ""):
                c.execute("UPDATE mem SET about_layer=? WHERE id=?", (lay, r["id"]))
                n += 1
        self.db.commit()
        return {"ok": True, "checked": len(rows), "changed": n}

    def promote_core(self, mid, tier=TIER_CORE_MUTABLE, kind="hobby",
                     subject_key="", why=""):
        """晋升进底色。配额满了就先挤掉同 kind 里最不活跃的那条。"""
        c = self.db.cursor()
        r = c.execute("SELECT * FROM mem WHERE id=?", (int(mid),)).fetchone()
        if r is None:
            raise KeyError("没有这条记忆：%s" % mid)
        # 🔧 2026-11-01 防污层：不可信/冲突的念头不许进底色。
        #    底色是"她自己的认知"，被喂进来的东西不配当自我。
        _t = float(r["trust"] if "trust" in r.keys() else 1.0)
        _cf = int(r["conflict"] if "conflict" in r.keys() else 0)
        if _t < TRUST_BLOCK_PROMOTE or _cf == 1:
            return {"ok": False, "reason": "trust_too_low",
                    "trust": _t, "conflict": _cf,
                    "why": (r["conflict_why"] or "")}
        # 🔧 2026-11-05：闲聊不许进底色。
        #    底色是"她对自己的认知"，一句"你先停一下"不配当自我。
        if (r["region"] or "") == R_CHATTER:
            return {"ok": False, "reason": "is_chatter",
                    "region": r["region"]}
        cap = CORE_KIND_CAPS.get(kind)
        if cap:
            rows = c.execute(
                "SELECT id FROM mem WHERE tier=? ORDER BY w ASC",
                (tier,)).fetchall()
            if len(rows) >= cap:
                # 挤掉最弱的：降回中期，让它自然淡出（不是删除，留证）
                victim = rows[0]["id"]
                self.set_tier(victim, TIER_MID)
        self.set_tier(mid, tier, kind=kind, subject_key=subject_key)
        if why:
            c.execute("UPDATE mem SET history=? WHERE id=?",
                      (json.dumps([{"ts": self.clock.now(), "why": why,
                                    "text": r["core"]}], ensure_ascii=False),
                       int(mid)))
        self.db.commit()
        return True

    def supersede(self, mid, new_text, ts=None, why="演化改写"):
        """底色被新事实替换：旧正文进 history，正文改写，旧版不再参与召回。

        用户 m02952 的说法：「我讨厌香菜」变成「我曾经讨厌香菜，现在能吃一点」
        —— 不是删掉旧的，是把旧的变成"曾经"。
        """
        c = self.db.cursor()
        r = c.execute("SELECT * FROM mem WHERE id=?", (int(mid),)).fetchone()
        if r is None:
            raise KeyError("没有这条记忆：%s" % mid)
        ts = float(ts or self.clock.now())
        try:
            hist = json.loads(r["history"] or "[]")
        except Exception:
            hist = []
        hist.append({"ts": ts, "text": r["core"], "why": why})
        # 超过 4 代就把最早的压成一句，防止 history 无限膨胀
        if len(hist) > 4:
            head = hist[0]
            hist = [{"ts": head["ts"], "text": head["text"],
                     "why": (head.get("why", "") + "（更早的已合并）")}] + hist[-3:]
        c.execute(
            "UPDATE mem SET core=?, raw=?, history=?, t_anchor=?, w=MAX(w,0.7)"
            " WHERE id=?",
            (new_text, new_text, json.dumps(hist, ensure_ascii=False), ts,
             int(mid)))
        self.db.commit()
        return True

    def situation_expire(self, now=None):
        """处境到期：高三会毕业，住哪会搬。到期的不删，降成"曾经"。"""
        now = float(now or self.clock.now())
        c = self.db.cursor()
        rows = c.execute(
            "SELECT * FROM mem WHERE tier=? AND valid_until>0 AND valid_until<?",
            (TIER_CORE_MUTABLE, now)).fetchall()
        n = 0
        for r in rows:
            try:
                hist = json.loads(r["history"] or "[]")
            except Exception:
                hist = []
            hist.append({"ts": r["valid_until"], "text": r["core"],
                         "why": "处境到期，变成过去"})
            c.execute(
                "UPDATE mem SET tier=?, pool=?, w=0.30, t_anchor=?, history=?,"
                " valid_until=0 WHERE id=?",
                (TIER_MID, POOL_MEDIUM, now,
                 json.dumps(hist, ensure_ascii=False), r["id"]))
            n += 1
        if n:
            self.db.commit()
        return n

    # ---------- 爱好预算 / 角色标签 ----------
    def hobby_list(self):
        c = self.db.cursor()
        out = []
        for r in c.execute(
                "SELECT id, raw, core, w, subject_key, t_last_recall"
                " FROM mem WHERE tier=? AND subject_key LIKE 'hobby:%'"
                " ORDER BY w DESC", (TIER_CORE_MUTABLE,)).fetchall():
            out.append({"id": r["id"], "name": r["core"],
                        "invest": round(r["w"], 3),
                        "active": r["w"] >= HOBBY_ACTIVE_LINE})
        return out

    def hobby_add(self, name, invest=None, note=""):
        """加一个爱好：从存量里按比例抽预算，抽不出来就拒绝（精力有限）。

        用户 m02956：爱好要能增也能减 —— 因为精力有限，多一个就得挤旧的一个。
        """
        invest = float(invest or HOBBY_NEW_MIN)
        cur = self.hobby_list()
        used = sum(h["invest"] for h in cur)
        if used + invest > HOBBY_BUDGET + 1e-6:
            # 先看能不能从最弱的爱好里抽
            free = HOBBY_BUDGET - used
            need = invest - free
            c = self.db.cursor()
            for h in sorted(cur, key=lambda x: x["invest"]):
                if need <= 0:
                    break
                take = min(need, max(0.0, h["invest"] - 0.18))
                if take <= 0:
                    continue
                c.execute("UPDATE mem SET w=w-? WHERE id=?",
                          (take, h["id"]))
                need -= take
            if need > 1e-6:
                return {"ok": False, "reason": "预算不够",
                        "used": round(used, 3), "budget": HOBBY_BUDGET}
            self.db.commit()
        mid = self.remember("我喜欢" + name if not name.startswith("我") else name,
                            salience=0.85, pool=POOL_LONG, region=R_PREF)
        self.set_tier(mid, TIER_CORE_MUTABLE, kind="hobby",
                      subject_key="hobby:" + name.strip())
        c = self.db.cursor()
        c.execute("UPDATE mem SET w=?, history=? WHERE id=?",
                  (invest, json.dumps([{"ts": self.clock.now(),
                                        "why": note or "新增爱好"}],
                                      ensure_ascii=False), mid))
        self.db.commit()
        return {"ok": True, "id": mid, "invest": invest,
                "used": round(sum(h["invest"] for h in self.hobby_list()), 3)}

    def person_layer_of(self, uid="", name=""):
        """这个人排在第几层关系？core / close / friend / known / ""（不认识）。

        查法：core_tag 层的 subject_key 是 "role:<名字>" 或 "role:<名字>#<uid>"。
        uid 优先（改名不影响），没 uid 再按名字。
        """
        uid = str(uid or "").strip()
        name = (name or "").strip()
        c = self.db.cursor()
        # 按 uid 查不到、但这个 uid 就是卡里记的那个人 → 换成他的名字再查。
        # （老标签是「role:使用者」这种，加 uid 之前建的，没有 #uid 后缀）
        if uid and not name:
            try:
                uq = c.execute(
                    "SELECT val FROM core_zone WHERE key='user_qq'").fetchone()
                if uq and (uq[0] or "").strip() == uid:
                    nn = c.execute(
                        "SELECT val FROM core_zone WHERE key='user_nickname'").fetchone()
                    if nn and (nn[0] or "").strip():
                        name = nn[0].strip()
            except Exception:
                pass
        rows = c.execute(
            "SELECT subject_key, core, mtype FROM mem WHERE tier=?"
            " AND subject_key LIKE ?",
            (TIER_CORE_TAG, "role:%")).fetchall()
        for r in rows:
            sk = r["subject_key"] or ""
            tag = sk[len("role:"):]
            hit = False
            if uid and ("#" + uid) in tag:
                hit = True
            elif name and tag.split("#")[0] == name:
                hit = True
            if not hit:
                continue
            # 正路：创建时写进 mtype 的 "role:<层>"
            mt = (r["mtype"] or "")
            if mt.startswith("role:"):
                lay = mt[len("role:"):].strip()
                if lay in ("core", "close", "friend", "known"):
                    return lay
            # 老标签兜底：按角色词猜（没有 mtype 的历史数据）
            txt = r["core"] or ""
            for kw, lay in (("最重要", "core"), ("亲人", "core"),
                            ("亲近", "close"), ("好朋友", "close"),
                            ("朋友", "friend"), ("认识", "known")):
                if kw in txt:
                    return lay
        return ""

    def role_tag(self, person, role, layer="friend", note="", uid=""):
        """给一个人挂角色标签（core_tag）。标签不动，这条就不动。

        用户 m02956：对象/重要的人当成"对某个特定角色的标签"，标签可以改，
        但不改的时候它是固定的。
        """
        c = self.db.cursor()
        key = "role:" + person.strip()
        if str(uid or "").strip():
            key += "#" + str(uid).strip()
        now = self.clock.now()
        old = c.execute(
            "SELECT * FROM mem WHERE tier=? AND subject_key=?",
            (TIER_CORE_TAG, key)).fetchone()
        text = "%s = %s" % (person.strip(), role)
        if old is not None:
            if old["core"] == text:
                return {"ok": True, "id": old["id"], "changed": False}
            # 换标签：留痕
            self.supersede(old["id"], text, ts=now,
                           why="角色标签：%s → %s" % (old["core"], text))
            c.execute("UPDATE mem SET mtype=? WHERE id=?",
                      ("role:%s" % (layer or "friend"), old["id"]))
            self.db.commit()
            return {"ok": True, "id": old["id"], "changed": True}
        mid = self.remember(text, salience=0.9, pool=POOL_LONG, region=R_SELF)
        self.set_tier(mid, TIER_CORE_TAG, kind="role", subject_key=key)
        # 关系层写进 mtype —— 正文是给人看的（"使用者 = 最重要的人"），
        # 层是给代码用的，两件事别混在一个字段里。
        c.execute("UPDATE mem SET mtype=? WHERE id=?",
                  ("role:%s" % (layer or "friend"), mid))
        c.execute("UPDATE mem SET w=0.95, history=? WHERE id=?",
                  (json.dumps([{"ts": now, "why": note or ("标签：%s" % role),
                                "text": ""}], ensure_ascii=False), mid))
        self.db.commit()
        return {"ok": True, "id": mid, "changed": True}

    # ---------- 想起 ----------
    def _idf_sim(self, qw, q_norm, mid, mv, idf):
        """带 IDF 权重的余弦，**行向量那半预算好缓存**（2026-11-11）。

        原来每条候选都现算 na、nb 两个循环（实测每请求 1315 次调用、
        156 ms）—— 可 query 侧固定、行向量也固定，纯浪费。这里把行向量
        乘完 idf 的形态和模长按 (id, idf 身份, 向量对象身份) 缓存。
        """
        if not qw or not mv or q_norm <= 0:
            return 0.0
        cache = getattr(self, "_idf_row_cache", None)
        if cache is None:
            cache = self._idf_row_cache = {}
        elif len(cache) > 4000:
            cache.clear()
        hit = cache.get(mid)
        if hit is not None and hit[0] is idf and hit[1] is mv:
            sc, nb = hit[2], hit[3]
        else:
            sc = {k: v * idf.get(k, 1.0) for k, v in mv.items()}
            nb = math.sqrt(sum(x * x for x in sc.values()))
            cache[mid] = (idf, mv, sc, nb)
        if nb <= 0:
            return 0.0
        dot = 0.0
        for k, w in qw.items():
            b = sc.get(k)
            if b:
                dot += w * b
        return dot / (q_norm * nb) if dot else 0.0

    def _ensure_idf(self):
        """惰性算 IDF（全库统计，纯本地），缓存在 self._idf

        🔧 优化：原来每次 remember 都把缓存清成 None，下一句就得全库重算一遍
        （323 条要 30ms，10 万条要 8 秒）。改成**脏计数**：
        新增不足 20 条就不重算 —— IDF 是全库统计量，几十条变化对权重影响很小，
        但重算一次的开销是全量的。这是「用精度换响应速度」，方向选对了。
        """
        cache = getattr(self, "_idf", None)
        if cache is not None:
            dirty = getattr(self, "_idf_dirty", 0)
            if dirty < 20:
                return cache
            # 攒够 20 条才真正重算
            self._idf_dirty = 0
        rows = self.db.execute("SELECT vec FROM mem").fetchall()
        self._idf, self._idf_n = build_idf(rows)
        return self._idf

    def _invalidate_idf(self):
        """记一笔「又变了」，但不立刻重算"""
        self._idf_dirty = getattr(self, "_idf_dirty", 0) + 1

    def _vecs(self, rows):
        """批量取向量，带内存缓存。

        🔧 优化：原来每次 recall 都把全库 vec 用 json.loads 解一遍。
        323 条要 4ms，10 万条要 1.3 秒 —— 而这发生在**每一句话之前**。
        向量是写入后就不再变的东西，缓存起来零风险。
        """
        cache = getattr(self, "_vec_cache", None)
        if cache is None:
            cache = self._vec_cache = {}
        out = {}
        miss = []
        for r in rows:
            mid = r["id"]
            v = cache.get(mid)
            if v is None:
                miss.append(r)
            else:
                out[mid] = v
        for r in miss:
            try:
                v = json.loads(r["vec"])
            except Exception:
                v = {}
            cache[r["id"]] = v
            out[r["id"]] = v
        return out

    # ---------- 语义层（T4-3）----------
    def sem_ready(self):
        """语义层现在能用吗（模型在不在、numpy 在不在）"""
        return bool(self.semantic is not None and self.semantic.ready())

    def _detail_sims(self, q_sem, mem_ids):
        """🔧 T4-3b/c：一批记忆各自「最像的那几段」有多像。

        片段池 = 主干(core) + 所有 detail 碎片，一起打分。

        🔧 T4-3d：从「取单个 max」改成「取前 K 个的均值」。
            踩坑记录：id332 是一条 345 字、27 个碎片的记忆，它几乎什么都
            沾一点（设计/方案/想法/文章/硬件/装起来/名字…），于是几乎每条
            查询都能从它身上挑出某一个碎片拿高分，把正确答案挤下去。
            单条 max 会被「碎片多的长记忆」系统性占便宜 —— 交 27 张卷子的人
            总有一张比你高。改成 top-k 池化（k=3）后，得有 3 段都像才算数，
            长条水记忆就没法靠「总有一段瞎猫碰上死耗子」混上来了。

        返回 list[float]，与 mem_ids 一一对应；没有片段的给 0.0。
        """
        if q_sem is None or not mem_ids:
            return [0.0] * len(mem_ids)
        out = [0.0] * len(mem_ids)
        q = np.asarray(q_sem, dtype=np.float32) if np is not None else list(q_sem)
        need = [m for m in mem_ids if m not in self._detail_emb_cache]
        if need:
            qmarks = ",".join("?" * len(need))
            prows = self.db.execute(
                "SELECT parent_id, emb, kind, text FROM detail "
                "WHERE parent_id IN (%s)" % qmarks, need).fetchall()
            grouped = {m: [] for m in need}
            for pr in prows:
                b = pr["emb"]
                if not b:
                    continue
                # 🔴 2026-10-31：**低信息量碎片不进语义检索池。**
                #
                # 病象：313「流浪地球给我灵感」那条，碎片全是
                #   「然后是你」「然后是事情」「然后也有一个就是比如说调用了它」
                # 这类连词碎片。它们跟任何查询都有点表面相似，于是把整条记忆的
                # 片段分撑起来，反过来把真正的答案挤出前三。
                #
                # 实测库里 462 个 MODIFIER 碎片，22% 以连词开头、16% 短于 6 字。
                #
                # 注意：**只是不参与检索打分**，碎片本身保留（褪色展示还要用）。
                if not _detail_retrievable(pr["text"], pr["kind"]):
                    continue
                v = semantic_mod.from_blob(b)
                if v is not None:
                    grouped[pr["parent_id"]].append(v)
            for m in need:
                self._detail_emb_cache[m] = grouped[m]
        for i, m in enumerate(mem_ids):
            vs = list(self._detail_emb_cache.get(m) or [])
            cv = self.core_vec_of(m)
            if cv is not None:
                vs.append(cv)
            if not vs:
                continue
            if np is not None:
                # 🔧 2026-10-30：只留与查询同维度的片段向量。
                #    换模型后老向量（512 维）会和新查询（768 维）对不上，
                #    np.stack 会直接抛错。丢掉它们比崩掉好。
                qn = len(q)
                vs = [v for v in vs if len(v) == qn]
                if not vs:
                    continue
                mat = np.stack(vs)                   # (k, dim)
                scores = mat @ q
            else:
                ql = list(q)
                scores = [sum(float(a) * float(b) for a, b in zip(v, ql))
                          for v in vs]
            # top-K 池化：取最高的 K 个求均值（不足 K 个就用全部）
            k = min(DETAIL_POOL_K, len(scores))
            top = sorted(scores, reverse=True)[:k]
            out[i] = float(sum(top) / k)
        return out

    def _emb_of(self, r):
        """取一条记忆的语义向量（先看缓存，再读库）"""
        mid = r["id"]
        v = self._emb_cache.get(mid)
        if v is not None:
            return v
        blob = r["emb"] if "emb" in r.keys() else None
        if not blob:
            return None
        v = semantic_mod.from_blob(blob)
        if v is not None:
            self._emb_cache[mid] = v
        return v


    def backfill_detail_embeddings(self, limit=None, progress=True, remodel=False):
        """🔧 T4-3b / T4-3c：给每个「片段」单独编码。

        **片段 = 主干(core) + 每个 detail**。主干也必须算一个片段，
        这条是踩坑之后才加上的 ——
            原以为 detail 已经把长句拆碎了，实测发现 id313 的
            `mem.core` 就是「…我看到流浪地球的那个数字生命卡给我的灵感」，
            答案就在主干里，而 detail 里全是「然后是你」「你觉得呢」这种
            尾巴碎片。只编码 detail 等于把答案漏在门外。

        为什么必须拆 ——
            一条 226 字的记忆，真正回答问题的可能只有开头 21 个字。
            整条一起编码，这 21 个字会被另外 200 字平均掉，
            于是「短问题」永远打不过「同主题的长句」。
            这正是 V5.0 §10「种子 = cosSim(input, node)」的本意：
            种子撒在**节点**上，不是撒在整条记忆上。

        返回 (编码条数, 跳过条数, 失败条数)。幂等，可反复跑。
        remodel=True：连维度对不上的老向量一起重编码（见 backfill_embeddings）。
        """
        if not self.sem_ready():
            return (0, 0, 0)
        if remodel and self.model_dim:
            rows = self.db.execute(
                "SELECT id, text FROM detail"
                " WHERE emb IS NULL"
                "    OR (length(emb) > 0 AND length(emb) != ?)",
                (self.model_dim * 4,)).fetchall()
        else:
            rows = self.db.execute(
                "SELECT id, text FROM detail WHERE emb IS NULL").fetchall()
        if limit:
            rows = rows[:limit]
        if not rows:
            return (0, 0, 0)
        c = self.db.cursor()
        done = skip = fail = 0
        for i, r in enumerate(rows):
            t = (r["text"] or "").strip()
            # 太短的碎片（「然后是你」「你觉得呢」）编码出来没有信息量，
            # 反而会污染 max。少于 4 个字直接标成空 blob 跳过。
            if len(t) < 4:
                c.execute("UPDATE detail SET emb=? WHERE id=?",
                          (b"", r["id"]))
                skip += 1
                continue
            try:
                blob = self.semantic.encode(embed_text_for(t))
            except Exception:
                fail += 1
                continue
            if blob is None:
                fail += 1
                continue
            c.execute("UPDATE detail SET emb=? WHERE id=?", (blob, r["id"]))
            done += 1
            if progress and (i + 1) % 100 == 0:
                self.db.commit()
                print('  碎片编码 %d/%d' % (i + 1, len(rows)), flush=True)
        self.db.commit()
        self._detail_emb_cache.clear()
        return (done, skip, fail)

    def core_vec_of(self, mem_id):
        """🔧 T4-3c：主干自己的向量。

        性能教训：第一版是「每次现场 encode」，第一次跑一条查询要 19 秒
        （385 条记忆 × 每条都要过一遍模型）。现在改成落库缓存：
          - 内存 `_core_vec_cache` 先查
          - 没有就查 `core_vec` 表（id -> blob）
          - 都没有才真编码，编完立刻落库
        这样只有第一次（或新记忆）付编码费，之后都是读 512×4 字节。
        """
        v = self._core_vec_cache.get(mem_id)
        if v is not None:
            return v
        if not self.sem_ready():
            return None
        row = self.db.execute(
            "SELECT core FROM mem WHERE id=?", (mem_id,)).fetchone()
        if not row or not (row["core"] or "").strip():
            return None
        core_text = row["core"]
        # 1) 落库缓存：按 core 文本的 sha256 存，句子改了自然失效
        h = hashlib.sha256(core_text.encode("utf-8")).hexdigest()
        hit = self.db.execute(
            "SELECT blob FROM core_vec WHERE mem_id=?", (mem_id,)).fetchone()
        if hit and hit["blob"]:
            v = semantic_mod.from_blob(hit["blob"])
            if v is not None:
                self._core_vec_cache[mem_id] = v
                return v
        # 2) 真编码
        try:
            blob = self.semantic.encode(embed_text_for(core_text))
        except Exception:
            return None
        if blob is None:
            return None
        v = semantic_mod.from_blob(blob)
        if v is None:
            return None
        self._core_vec_cache[mem_id] = v
        try:
            self.db.execute(
                "INSERT OR REPLACE INTO core_vec(mem_id, hash, blob) "
                "VALUES(?,?,?)", (mem_id, h, blob))
            # 🔴 2026-11-01：**写完立刻提交。**
            #    这段在「检索打分」里跑 —— 是一条读路径。可 sqlite3 的
            #    INSERT 会顺手把写事务开在那儿，不 commit 就一直握着写锁。
            #    实测后果：检索过一次之后，别的进程（bridge / console /
            #    快照脚本 / 另一个测试）再来写卡，一律 "database is locked"，
            #    而且 busy_timeout 等不到头 —— 因为握锁的是「已经读完、
            #    只是忘了提交」的那个连接，它永远不会自己放。
            #    这条缓存本来就是可丢弃的（丢了下次重算），提交一下最省事。
            self.db.commit()
        except Exception:
            try:
                self.db.rollback()
            except Exception:
                pass
        return v

    def _emb_model_name(self):
        """当前向量模型的短名（bge-base-zh-v1.5 之类），拿不到就 ''。"""
        try:
            dn = semantic_mod.current_model_dir() if self.card_dir else None
            return os.path.basename(str(dn or "")) or ""
        except Exception:
            return ""



    def _count_emb_dims(self):
        """卡里现存向量按维度的分布：{维度: 条数}

        维度不用另建列存 —— length(emb)/4 就是，永远跟数据一致，
        不会有「字段写了但这个值早就不对了」那种病。
        """
        out = {}
        try:
            rows = self.db.execute(
                "SELECT length(emb) AS n, COUNT(*) AS c FROM mem"
                " WHERE emb IS NOT NULL AND length(emb) > 0 GROUP BY n"
            ).fetchall()
        except Exception:
            return out
        for r in rows:
            n = int(r["n"] or 0)
            if n and n % 4 == 0:
                out[n // 4] = out.get(n // 4, 0) + int(r["c"])
        return out


    def _refresh_dim_state(self):
        """重算「卡里向量按维度的分布」和「模型/卡对不上」的告警。

        必须能被反复调用：补编码（backfill）会把 512 的老向量换成 768 的，
        如果只在 open 时算一次，之后 sem_stats 报的还是开卡那一刻的旧账 ——
        补完编码打开面板一看「还是 68%」，白干。
        """
        try:
            self.emb_dims = self._count_emb_dims()
        except Exception:
            self.emb_dims = {}
        card_dim = max(self.emb_dims, key=self.emb_dims.get) if self.emb_dims else 0
        self.dim_warn = None
        if card_dim and self.model_dim and card_dim != self.model_dim:
            other = sum(c for d, c in self.emb_dims.items()
                        if d != self.model_dim)
            self.dim_warn = (
                "卡里多数向量 %d 维，当前模型 %d 维：%d 条用不上（语义会静默降级）。"
                "跑一次 bridge.py remodel 可把它们重编码。"
                % (card_dim, self.model_dim, other))
        return self.emb_dims

    def sem_stats(self):
        """语义层现状：模型状态 + 覆盖率 + 缓存条数"""
        total = self.db.execute("SELECT COUNT(*) FROM mem").fetchone()[0]
        with_emb = self.db.execute(
            "SELECT COUNT(*) FROM mem WHERE emb IS NOT NULL").fetchone()[0]
        st = self.semantic.stats() if self.semantic is not None else {
            'enabled': False, 'ready': False, 'failed': 'semantic_mod 未导入',
            'encoded': 0, 'model_dir': None}
        st['total'] = total
        st['with_emb'] = with_emb
        st['coverage'] = (with_emb / float(total)) if total else 0.0
        st['cached'] = len(self._emb_cache)
        # 维度账本：覆盖率一个数字说不清「这条向量能不能用」，
        # 分维度列出来才看得出语义层到底覆盖了多少。
        self._refresh_dim_state()
        st['emb_dims'] = dict(self.emb_dims)
        st['model_dim'] = self.model_dim
        st['dim_note'] = self.dim_note
        st['dim_warn'] = self.dim_warn
        if self.model_dim:
            usable = self.emb_dims.get(self.model_dim, 0)
            st['usable'] = usable
            st['usable_ratio'] = (usable / float(total)) if total else 0.0
        return st

    # ---------- 分舱两级检索（T4-1）----------
    def _pods_stale(self):
        """舱是不是该重建了

        🔧 关键：不能每次 recall 都重算聚类（k-means 几十毫秒起步）。
        只有「新增条目够多」或「从没建过」才重建。
        """
        if getattr(self, "_pod_centroids", None) is None:
            return True
        n = self.db.execute("SELECT COUNT(*) FROM mem").fetchone()[0]
        # 🔧 阈值原来是 n//10，太严：1119 条时要新增 111 条才重建，
        #    实测去重之后 200 句话只落库 94 条，永远等不到重建。
        #    改成 n//20（5%）——舱只是粗筛，多重建几次不过几十毫秒。
        return n - getattr(self, "_pod_n", 0) >= max(20, n // 20)

    def _build_pods(self):
        """把全库向量聚成 N_PODS 个舱，返回 (质心列表, id→舱号)

        用最朴素的做法：随机挑 N_PODS 条当初始质心，迭代几轮。
        不追求最优解 —— 舱只是「粗筛」，分得不完美不影响正确性
        （最后还有回退全库兜底）。

        🔧 性能：条目模长只算一次（items_n），质心模长每轮只算 k 次。
        原来每条×每质心都重算两边模长，381 条聚一次要 7.8 秒，
        优化后同样的活降到零点几秒 —— 这是 recall 热路径上的东西，
        不能让它每句话都卡一秒多。
        """
        rows = self.db.execute("SELECT id, vec FROM mem").fetchall()
        items = []
        for r in rows:
            try:
                v = json.loads(r["vec"])
            except Exception:
                continue
            if v:
                items.append((r["id"], v))
        if len(items) < POD_MIN_ROWS:
            self._pod_centroids = None
            self._pod_of = {}
            self._pod_n = len(items)
            return
        items_n = [vec_norm(v) for _mid, v in items]
        rnd = random.Random(20261030)      # 固定种子：同样数据每次聚出同样舱
        k = min(N_PODS, len(items))
        picks = rnd.sample(range(len(items)), k)
        cents = [dict(items[i][1]) for i in picks]
        assign = {}
        for _ in range(POD_KMEANS_ITERS):
            # 1) 分派（质心模长每轮只算 k 次）
            cents_n = [vec_norm(cv) for cv in cents]
            new_assign = {}
            for idx, (mid, v) in enumerate(items):
                best, best_s = 0, -1.0
                na = items_n[idx]
                for ci, cv in enumerate(cents):
                    s = cos_sim(v, cv, na=na, nb=cents_n[ci])
                    if s > best_s:
                        best_s, best = s, ci
                new_assign[mid] = best
            # 2) 更新质心（取成员向量均值）
            buckets = {}
            for mid, v in items:
                buckets.setdefault(new_assign[mid], []).append(v)
            for ci in range(k):
                vs = buckets.get(ci)
                if not vs:
                    continue
                acc = {}
                for v in vs:
                    for t, w in v.items():
                        acc[t] = acc.get(t, 0.0) + w
                n = float(len(vs))
                cents[ci] = {t: w / n for t, w in acc.items()}
            if new_assign == assign:
                break
            assign = new_assign
        self._pod_centroids = cents
        self._pod_of = assign
        self._pod_n = len(items)
        self._build_sem_pod_centroids()
        self._pods_save()

    def _build_sem_pod_centroids(self):
        """用语义向量重算每个舱的质心（T4-3 调优）

        为什么不能只用字面质心路由：字面质心是按 bigram 聚出来的，
        拿一句**语义相近但用词完全不同**的查询去比，路由会选错舱，
        正确答案连候选集都进不去（实测「数字生命卡的灵感哪来的」）。
        改用语义质心后，同一批 10 条查询的保留率 9/10 → 10/10。

        失败一律降级为 None（退回字面路由），不影响正确性。
        """
        self._pod_sem = None
        if not self.sem_ready() or not self._pod_of:
            return
        try:
            import numpy as np
            buckets = {}
            rows = self.db.execute("SELECT * FROM mem").fetchall()
            for r in rows:
                ci = self._pod_of.get(r["id"])
                if ci is None:
                    continue
                e = self._emb_of(r)
                if e is None:
                    continue
                buckets.setdefault(ci, []).append(e)
            if not buckets:
                return
            cents = {}
            for ci, vs in buckets.items():
                m = np.mean(np.stack(vs), axis=0)
                n = float(np.linalg.norm(m))
                cents[ci] = (m / n) if n > 0 else m
            self._pod_sem = cents
        except Exception:
            self._pod_sem = None

    def _pods_path(self):
        return os.path.join(self.card_dir, "cache", "pods.json")

    def _pods_save(self):
        """把舱落到 card/cache/pods.json（纯缓存，删了不影响正确性）。

        为什么要它（2026-11-11）：冷启动时后台建舱要十几秒，那段时间
        所有查询走全库扫描，第一个请求实测 8.9 秒。把上一次建好的舱
        读回来，冷启动就能直接用。
        """
        if getattr(self, "_pod_centroids", None) is None:
            return
        sem = getattr(self, "_pod_sem", None) or {}
        dim = 0
        for v in sem.values():
            dim = len(v)
            break
        blob = {"key": [int(getattr(self, "_pod_n", 0)), N_PODS,
                        POD_KMEANS_ITERS, dim],
                "cents": self._pod_centroids,
                "pod_of": self._pod_of,
                "pod_sem": ({str(k): [float(x) for x in v]
                             for k, v in sem.items()} if sem else None)}
        try:
            p = self._pods_path()
            d = os.path.dirname(p)
            if d and not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(blob, f, ensure_ascii=False)
            os.replace(tmp, p)
        except Exception:
            self._pod_build_err = True

    def _pods_load(self):
        """读回落盘的舱；条数/参数/语义维度对不上就当没有。"""
        try:
            p = self._pods_path()
            if not os.path.exists(p):
                return False
            try:
                with open(p, encoding="utf-8") as f:
                    blob = json.load(f)
            except Exception as _pe0:
                # 🔧 2026-11-11（稳定性）：缓存坏了不该变成「每次启动都白建一遍」。
                # 坏文件改名留证（不删），这次当没有缓存，后台会重建并写新的。
                try:
                    os.replace(p, p + ".bad-" + time.strftime("%Y%m%d-%H%M%S"))
                except Exception:
                    _rename_err = True
                sys.stderr.write("[card2] 舱缓存读不动（%s），已改名留证，重建\n" % _pe0)
                return False
            key = blob.get("key") or []
            n_now = int(self.db.execute(
                "SELECT COUNT(*) FROM mem WHERE vec IS NOT NULL AND vec<>''"
            ).fetchone()[0])
            if not key or int(key[0]) != n_now:
                return False
            if int(key[1]) != N_PODS or int(key[2]) != POD_KMEANS_ITERS:
                return False
            self._pod_centroids = blob.get("cents") or None
            self._pod_of = {int(k): int(v)
                            for k, v in (blob.get("pod_of") or {}).items()}
            self._pod_n = n_now
            sem = blob.get("pod_sem")
            if sem and self.sem_ready():
                import numpy as _np
                self._pod_sem = {int(k): _np.array(v, dtype=_np.float32)
                                 for k, v in sem.items()}
                if any(len(v) != int(key[3]) for v in self._pod_sem.values()):
                    self._pod_sem = None
            else:
                self._pod_sem = None
            return self._pod_centroids is not None
        except Exception:
            return False

    def _ensure_pods(self):
        """舱该重建就重建，但**不占读请求的时间**（2026-11-11）。

        实测（3 读×4 + 5 写×3 压测）：p50 1.2s、p95 却是 19s —— 抓栈看到
        卡点在 `_build_pods()` 的纯 Python `cos_sim` 双重循环：写请求让
        条目数过了阈值，几个读线程同时发现「该重建」，各建一遍，互相抢 GIL。
        舱只是粗筛（有全库兜底），旧的照样能用，所以：有旧舱就丢后台建一次，
        从没建过（冷启动）才同步建。
        """
        if not POD_ENABLED:
            # 2026-11-11：舱从 V7 起默认关（POD_ENABLED=False），但这里以前不看开关，
            # 于是每次写稿/说话都在后台重建一遍、还写 cache/pods.json —— 白烧 CPU 和 IO。
            # 想重开就把 POD_ENABLED 改 True，这条会自动恢复。
            return
        if not self._pods_stale():
            return
        has_old = getattr(self, "_pod_centroids", None) is not None
        if has_old:
            # 30 秒内只许重建一次，避免写密集时反复空转
            _now = time.time()
            if _now - float(getattr(self, "_pod_try_ts", 0.0)) < 30.0:
                return
            self._pod_try_ts = _now
            if _POD_BUILD_LOCK.acquire(blocking=False):
                _t = threading.Thread(target=self._bg_build_pods,
                                      name="pod-build", daemon=True)
                _t.start()
            return
        # 🔧 2026-11-11（冷启动）：以前这里是「冷启动就同步建」，真卡上要
        # 10 秒以上，全挂在第一个请求头上 —— 重启后的第一句话要等十几秒。
        # 舱只是加速（`_pod_candidates` 返回 None 时全库查，正确性不变），
        # 所以干脆也不等：起后台线程建，建好之前所有查询走全库。
        if self._pods_load():
            return
        _now = time.time()
        if _now - float(getattr(self, "_pod_try_ts", 0.0)) < 5.0:
            return
        self._pod_try_ts = _now
        if _POD_BUILD_LOCK.acquire(blocking=False):
            threading.Thread(target=self._bg_build_pods,
                             name="pod-build", daemon=True).start()

    def _bg_build_pods(self):
        """后台重建舱；成功与否都释放锁，失败就继续用旧舱。"""
        try:
            self._build_pods()
        except Exception:
            self._pod_build_err = True
        finally:
            try:
                _POD_BUILD_LOCK.release()
            except Exception:
                self._pod_build_err = True

    def _pod_candidates(self, qv, idf, q_sem=None):
        """先挑最像的 POD_TAKE 个舱，返回舱内条目 id 集合；None = 该全库查

        🔧 有语义向量时用语义质心路由（更准），否则退回字面质心。
        """
        cents = getattr(self, "_pod_centroids", None)
        if not cents:
            return None
        sem_cents = getattr(self, "_pod_sem", None)
        scored = []
        # 🔧 2026-10-30：维度校验。舱质心是建舱时那一刻的模型算的，
        #    如果之后换了模型（small 512 → base 768），质心和查询向量
        #    形状对不上，直接乘会抛 ValueError，退化成 0 也会让舱路由失灵。
        #    对不上就当没有语义质心，退回字面质心 —— 宁可慢一点，不能崩。
        if q_sem is not None and sem_cents:
            want = q_sem.shape[0]
            if any(cv.shape[0] != want for cv in sem_cents.values()):
                sem_cents = None
        if q_sem is not None and sem_cents:
            for ci, cv in sem_cents.items():
                scored.append((float((q_sem * cv).sum()), ci))
        else:
            for ci, cv in enumerate(cents):
                scored.append((cos_sim_idf(qv, cv, idf), ci))
        scored.sort(key=lambda x: -x[0])

        # 🔧 2026-10-30 夜：分舱**弃用**（保留代码，只留作大库备用）。
        #
        # 病根实测：查询「暑假工」，正确答案 id321
        #（「用户暑假靠拍婚礼挣钱想攒钱买硬盘和算力设备做数字生命卡」）
        # 落在第 5 舱，而第 5 舱按语义质心排**第 8 名（最后）**，
        # 于是根本没进候选集 —— 打分公式再好也不可能把它捞回来。
        #
        # 更糟的是 8 个舱的相似度几乎是平的：
        #     0.1998 / 0.1957 / 0.1956 / 0.1894 / 0.1797 / 0.1717 / 0.1566 / 0.1525
        # 首末只差 0.05，说明质心彼此太像（385 条记忆全挤在「记忆卡」一个
        # 话题里，切成 8 舱后每舱都差不多），路由等于随机。
        #
        # 这条教训值得记住：**预筛只能在有把握的时候用**。
        # 拿不准就老老实实全查 —— 385 条全查的代价是几毫秒，
        # 而漏掉正确答案的代价是「她明明记得却想不起来」。
        #
        # 什么情况下重新打开：库涨到 5000 条以上、且舱相似度首末差 > 0.15
        # （说明话题真的分散了）。判据写在下面，不靠感觉。
        if POD_ENABLED:
            take = {ci for _s, ci in scored[:POD_TAKE]}
            out = {mid for mid, ci in getattr(self, "_pod_of", {}).items()
                   if ci in take}
            if len(out) >= 3:       # 舱里太空就退回全库
                return out
        return None

    def apply_recall_effects(self, hits, query, touch=True):
        """把「被想起」这笔记账推迟到锁里补写（读写分离第 2 步）。

        读阶段用 recall(access=False) + assemble(touch=False) 跑，只读不算账；
        拿到结果后再在短锁里补这几行 INSERT —— 这样读请求不用排队等写。
        写的还是原来那几件事：召回事件、会话命中集合、突触加固、内脏刺激。
        """
        if touch:
            try:
                self.viscera_touch(query or "")
            except Exception:
                self._effects_err = True
        try:
            _settle = int(os.environ.get("WHALE_RECALL_SETTLE_K",
                                         str(RECALL_SETTLE_K)) or 0)
            _sl = hits[:_settle] if _settle > 0 else hits
            self._write_recall_events(
                [(r["id"], i) for i, (_s, _w, r) in enumerate(_sl)],
                query, source="recall")
            self._session_hit_ids.update(r["id"] for _s, _w, r in _sl)
            try:
                self.synapse.reinforce([r["id"] for _s, _w, r in _sl])
            except Exception:
                self._effects_err = True
        except Exception:
            self._effects_err = True

    def flush_deferred_effects(self):
        """把延后模式攒下的召回记账一次写完（必须在锁里调）。"""
        _q = getattr(self, "_defer_q", None)
        if not _q:
            return 0
        self._defer_q = []
        for _ids, _query in _q:
            try:
                self._write_recall_events(
                    [(i, k) for k, i in enumerate(_ids)], _query, source="recall")
            except Exception:
                self._flush_err = True
        return len(_q)

    def recall(self, query, top_k=3, access=True, pools=None, regions=None,
               assoc=True, about_uid=None, trace=None):
        """三因子：像不像 × 还清不清楚 × 多久没提

        🔧 第二期改进：
          - 查询先剥掉「你还记得…吗」这类疑问壳子，否则模板句主导匹配
          - 相似度用 IDF 加权，让「QQ」「烧烤」这种低频词说了算
          - 增加「关键词覆盖率」做二次校验：查询里的实词至少命中一部分

        🔧 V4.1 分区检索：
          - 先判这句话问的是「事实」还是「经历」，只在对应分区里找
          - 分区命中不足 2 条 → 回退全库（宁可多查，不可漏查）
          - 分区加分：命中目标分区的条目 × 1.25

        🔧 T4-4 突触联想：
          - 直接命中之后，电势沿突触往外扩散两三跳，
            被激活的邻居以「联想加成」并入结果（永远排在直接命中之后）
          - assoc=False 可关掉，关掉后行为和以前完全一样
        """
        idf = self._ensure_idf()
        q_clean = _RE_QUERY_SHELL.sub("", query or "")
        # 2026-11-09：这句问的是「我自己说过什么」吗？是的话给自己的话抬一档；
        # 问的是群里的事就不压群聊。
        _self_ask = bool(_RE_SELF_ASK.search(query or ""))
        _self_name_ask = bool(_RE_SELF_NAME_ASK.search(query or ""))
        _self_boost = (SELF_ASK_BOOST_NAME if _self_name_ask else SELF_ASK_BOOST)
        if _self_ask:
            # 剥掉「你之前说」这层壳，只拿内容去比 —— 否则壳子本身会赢。
            _stripped = _RE_SELF_SHELL.sub("", q_clean)
            if len(_stripped.strip()) >= 3:
                q_clean = _stripped
        _group_ask = bool(_RE_GROUP_ASK.search(query or ""))
        _ask_my_name = bool(re.search(r"(叫什么|名字|称呼|怎么叫你)", query or "")) \
            and not re.search(r"(身份|设定|规矩|原则|职责|负责|干什么|做什么)", query or "")
        qv = to_vec(q_clean)
        if not qv:
            qv = to_vec(query)          # 剥没了就退回原文
        # 查询里的实词（去掉虚字后的单字 + bigram）
        q_keys = {k for k in qv if not (len(k) == 1 and k in _STOP_CHARS)}

        # 🔧 T4-3：语义查询向量。拿不到就 None，后面自动退回字面匹配。
        q_sem = None
        if self.sem_ready():
            try:
                q_sem = self.semantic.encode_query(q_clean or query)
            except Exception:
                q_sem = None

        now = self.clock.now()
        c = self.db.cursor()
        # 🔧 V5.3.2 统一时间线：每条记忆都带上它在时间线上的序号（mem_birth.seq）。
        #    为什么非要这个：光有 create_ts 只能排"大概谁先"，而 seq 是哈希链上的
        #    真实顺序 —— 两件事冲突时，"谁在后"必须以链为准，不能以浮点秒为准。
        #    取不到（极少数历史行）就给 None，后面按 create_ts 兜底。
        _SEL = ("SELECT m.*, (SELECT b.seq FROM mem_birth b WHERE b.mid=m.id) "
                "AS tseq FROM mem m ")
        # T5-1：ARCHIVE 是潜意识，不参与实时检索；只有显式点名要它才翻。
        _LIVE = "COALESCE(m.dead,0)=0 AND COALESCE(m.quarantine,0)=0"
        def _query_rows():
            if pools:
                return c.execute(_SEL + "WHERE " + _LIVE).fetchall()
            return c.execute(_SEL + "WHERE m.pool != ? AND " + _LIVE,
                             (POOL_ARCHIVE,)).fetchall()

        try:
            rows = _query_rows()
            if trace is not None:
                trace["stages"] = {"all": len(rows)}
        except sqlite3.OperationalError as _e:
            # 没挂时间线的库：补一张空的出生证表，seq 一律取不到，按 create_ts 兜底
            if "mem_birth" not in str(_e):
                raise
            c.execute("CREATE TABLE IF NOT EXISTS mem_birth("
                      "mid INTEGER PRIMARY KEY, hash TEXT, seq INTEGER, ts REAL)")
            rows = _query_rows()

        # ---- T4-2 时间窗预筛 ----
        # 问「今天」就别翻三年前的；问「以前」就不限。
        # 注意：只是**收窄候选**，不改打分 —— 收窄错了还有回退兜底。
        win_days = query_time_window(query)
        span = query_time_span(query, now=now)
        if span is not None:
            # 🔧 2026-11-09：时间窗只该筛「事件」，不该筛「长期事实」。
            #    原先是 rows 里所有记忆一起按时间收窄 —— 可「他叫什么」「在苏州做什么」
            #    「不吃芹菜」这些是 fact/preference/self，它们**没有过期日**，正是
            #    跨 45 天最该被翻出来的东西；一起被时间窗切掉，等于问「上次」的时候
            #    把人的姓名职业也一起扔了。实测（probe/_diag2_pool.md）45 天档
            #    ret20 召不回的 47 道里，有 11 道是被这一步误筛的。
            #    现在只有 episode/flash/chatter 这类「事」参与收窄，其余整层保留。
            _timed = {"episode", "flash", "chatter", "", None}
            _start, _end, _precise = span
            if _precise:
                # 问的是「哪一天」：只留那天发生的事。门槛降到 1 ——
                # 精确问日期时，宁可只有三条候选，也别因为"太少"退回全库。
                narrowed = [r for r in rows
                            if (r["region"] not in _timed)
                            or (_start <= event_ts_of(r) <= _end)]
                _min_keep = 1
                # 「那天**我说**过什么」和「那天**你说**过什么」不是一件事：
                # 11-06 那天光群聊就有 389 条，不按说话人再筛一刀，
                # 她自己说过的那句就被群消息淹掉了（实测未命中的三条全是这个原因）。
                if re.search(r"我(?:说|讲|提|问)", query or ""):
                    _sp = [r for r in narrowed if (r["speaker"] or "") == "user"]
                    if _sp:
                        narrowed = _sp
                elif re.search(r"你(?:说|讲|提|答)", query or ""):
                    _sp = [r for r in narrowed if (r["speaker"] or "") == "self"]
                    if _sp:
                        narrowed = _sp
                # 那天**两类**话混在一起：跟我说的，和在群里说的。
                # 11-06 那天 389 条里 279 条是群聊（qq-group），问「那天我说过什么」
                # 想要的显然是我们的对话；但也别把群聊删干净 —— 问「群里」就反过来。
                _q2 = query or ""
                _grp = lambda r: (r["source"] or "") == "qq-group"   # noqa: E731
                if "群" in _q2:
                    _sg = [r for r in narrowed if _grp(r)]
                    if len(_sg) >= 3:
                        narrowed = _sg
                else:
                    _pv = [r for r in narrowed if not _grp(r)]
                    if len(_pv) >= 3:
                        narrowed = _pv
            else:
                narrowed = [r for r in rows
                            if (r["region"] not in _timed)
                            or r["last_active_ts"] >= _start
                            or event_ts_of(r) >= _start]
                _min_keep = 5
            # 收窄后太少（比如刚开学没聊过）就放宽，别把库筛空
            if len(narrowed) >= _min_keep:
                rows = narrowed
            if trace is not None:
                _st = trace.setdefault("stages", {})
                _st["after_span"] = len(rows)
                _st["span_precise"] = bool(_precise)

        # ---- T4-1 分舱预筛 ----
        self._ensure_pods()
        pod_ids = self._pod_candidates(qv, idf, q_sem)
        if pod_ids:
            narrowed = [r for r in rows if r["id"] in pod_ids]
            if len(narrowed) >= 5:
                rows = narrowed
        if trace is not None:
            trace.setdefault("stages", {})["after_pod"] = len(rows)

        # ---- 2026-11-09「我自己说过的话」先在自己话里找 ----
        #    和分区路由同一个套路：先在自己说过的话里找，找出来的不够 2 条
        #    再回退全库 —— 宁可多查，不可漏查。
        if _self_ask:
            _self_rows = [r for r in rows if (r["speaker"] or "") == "self"]
            if len(_self_rows) >= 5:
                rows = _self_rows
        if trace is not None:
            _st = trace.setdefault("stages", {})
            _st["after_self"] = len(rows)
            trace["flags"] = {"self_ask": bool(_self_ask),
                              "group_ask": bool(_group_ask)}

        vecs = self._vecs(rows)     # 🔧 批量取向量（带缓存），不再逐条 json.loads

        # ---- 分区路由 ----
        want = set(regions) if regions else guess_query_regions(query)
        if trace is not None:
            trace.setdefault("flags", {})["regions"] = sorted(want) if want else []
        # 🔧 V5.0 §4.4(1)：内分泌对召回的联动加权
        #    催产素高 → 羁绊记忆更容易翻出来；皮质醇高 → 回忆闭塞
        f_end = hormone.f_endo(getattr(self, "h", hormone.H_BASE))

        _comp = {}

        def _score_rows(cands, region_boost):
            # 🔧 T4-3：语义向量一次算完（矩阵乘法），不在 Python 里逐条循环
            if q_sem is not None:
                embs = [self._emb_of(r) for r in cands]
                sem_scores = semantic_mod.SemanticLayer.sims(q_sem, embs)
            else:
                sem_scores = [0.0] * len(cands)
            # 🔧 T4-3b：片段级语义 —— 每条记忆的每个 detail 单独算分取 max。
            #    一条 226 字的记忆，真正回答问题的那 21 个字会被其余 200 字
            #    平均掉；分段取 max 才能把它救出来。见 backfill_detail_embeddings。
            det_scores = ([0.0] * len(cands) if q_sem is None
                          else self._detail_sims(q_sem, [r["id"] for r in cands]))
            # 查询侧乘 idf、算模长：一次就够，别每条候选重算
            qw = ({k: v * idf.get(k, 1.0) for k, v in (qv or {}).items()}
                  if idf else dict(qv or {}))
            q_norm = math.sqrt(sum(x * x for x in qw.values()))
            out = []
            for i, r in enumerate(cands):
                if pools and r["pool"] not in pools:
                    continue
                mv = vecs.get(r["id"]) or {}
                if not mv and not sem_scores[i]:
                    continue
                sim = (self._idf_sim(qw, q_norm, r["id"], mv, idf)
                       if mv else 0.0)
                # 🔧 T4-3 / T4-3b：语义分三档取最强的那个：
                #    整条语义 sem、片段语义 max det、两者均值。
                #    片段分只在**明显强于整条**时才顶上来（-0.02 容差），
                #    否则短记忆会被自己的碎段拉高、长记忆反而吃亏。
                sem_best = max(sem_scores[i], DET_REL * det_scores[i])
                if sem_scores[i] > 0.0 and det_scores[i] > 0.0:
                    sem_best = max(sem_best,
                                   DET_REL * 0.5 * (sem_scores[i] + det_scores[i]))
                if sem_best > 0.0:
                    blended = 0.75 * sem_best + 0.25 * sim
                else:
                    blended = sim
                if blended <= 0:
                    continue
                if q_keys:
                    cover = len(q_keys & set(mv)) / float(len(q_keys))
                else:
                    cover = 0.0
                wn = wn_row(r, now)
                age_days = (now - r["last_active_ts"]) / DAY
                recency = math.exp(-age_days / RECENCY_SLOW_DAYS)
                # 2026-11-07 试验记录（默认关，别乱开）：
                #   ①双时间尺度 0.7·exp(-age/0.5d)+0.3·exp(-age/14d)：
                #     age=0.2 天时给 0.765，比单指数的 0.986 低 → 等于整体
                #     压低新近项；评估集 R@1 75%→37.5%、EVID 100%→33%。
                #   ②单指数 + 同日台阶(+0.12)：R@1 仍 37.5%、EVID 掉到 0。
                #   病根是卡里每天有几百条群消息，「同一天」的加成把闲聊
                #   顶到前面、真记忆被挤下去 —— 时间尺度不是问题，
                #   是「什么值得记」应该在**写入**时判，不该在检索时补。
                if _RECENCY_FRESH_STEP:
                    if age_days <= 0.25:
                        recency = min(1.0, recency + 0.12 * (1.0 - age_days / 0.25))
                # 🔧 T4-3 调参（2026-10-30）：在 20 条真实查询上网格搜索，
                #    第一命中率 14/20 → 17/20。三处改动：
                #    ① salience 乘子从 (0.5+s) 收窄到 (0.85+0.30s) ——
                #       原来 0.5~1.5 最多 3 倍差，把 0.09 的语义差距吃掉了；
                #    ② 字面权重 0.45 → 0.35，衰减权重 0.15 → 0.20 ——
                #       语义可用时让「意思像」主导，「还清不清楚」次之；
                #    ③ cover 保留全量实词（改 bigram 反而降到 10/12，
                #       因为中文短查询 bigram 覆盖率太稀疏）。
                s = (SCORE_W[0] * blended + SCORE_W[1] * cover
                     + SCORE_W[2] * wn + SCORE_W[3] * recency)
                # 对象倾向（V5.3.4）：不是硬过滤 —— 卡的 about_uid 覆盖率只有三成，
                # 硬过滤会把七成记忆直接挡死。这里是「同一个人的更容易被想起」，
                # 明确属于别人的那条往后退一档。
                if about_uid:
                    _ou = (r["about_uid"] if "about_uid" in r.keys() else "") or ""
                    if _ou == str(about_uid):
                        s *= OBJECT_BOOST_SAME
                    elif _ou:
                        s *= OBJECT_BOOST_OTHER
                # salience 乘子：这条本身有多重（对所有候选生效）
                s *= (0.85 + 0.30 * r["salience"])
                # V5.4 时间窗加成：只给他本人说的话，30 天内越近越多
                if _WINDOW_BONUS_ON and (r["speaker"] or "") == "user":
                    _age_s = max(0.0, now - r["last_active_ts"])
                    _span = WINDOW_BONUS_DAYS * DAY
                    if _age_s < _span:
                        s *= (1.0 + WINDOW_BONUS_MAX * (1.0 - _age_s / _span))
                # 情绪调制（2026-11-07）：不是加分，是放大 ——
                # 「想不想得起来」×「这件事当时有多重」。
                # **默认关**：实测开了之后 R@1 75%→37.5%（见 recency 那段记录）。
                # 它本身不算错，是现在卡里 emotion 大多默认 0.25 且
                # 群消息也吃到了同样的值，等于给噪声也加了权。
                # 要开先满足：emotion 只在「真情绪」时才非零，再跑评估。
                if _EMOTION_BOOST_ON:
                    try:
                        _emo = float(r["emotion"] or 0.0)
                    except Exception:
                        _emo = 0.0
                    if _emo > 0:
                        s *= (1.0 + EMOTION_RECALL_BOOST * min(1.0, _emo))
                # 羁绊记忆（带 oxy）额外享受 f_endo，普通记忆按一半算
                oxy_w = min(1.0, float(r["oxy"] or 0.0))
                s *= (1.0 + (f_end - 1.0) * (0.5 + 0.5 * oxy_w))
                if region_boost and r["region"] in want:
                    s *= REGION_BOOST
                # 🔧 2026-11-01 防污层：不可信 / 冲突的记忆能想起来，但不那么信。
                #    trust 只压到一半（0.5~1.0），冲突再打对折 —— 不是屏蔽，
                #    是"她记得这话，但她知道这话可能是被喂进来的"。
                try:
                    _t = float(r["trust"] if "trust" in r.keys() else 1.0)
                    _cf = int(r["conflict"] if "conflict" in r.keys() else 0)
                    if _t < 1.0:
                        s *= (0.5 + 0.5 * max(0.0, min(1.0, _t)))
                    if _cf == 1:
                        s *= CONFLICT_RECALL_PENALTY
                except Exception:
                    pass
                # 2026-11-09：钉住的记忆 w 恒为 1，若原样进打分，
                #    它们会凭「永远清楚」压过所有真正相关的近期记忆。
                #    「不衰减」是寿命，不是相关性 —— 打分时把它的清晰度
                #    压到 0.5，够用，但不至于霸榜。
                try:
                    if int(r["pin"] or 0):
                        wn = min(wn, 0.5)
                except Exception:
                    pass
                # 2026-11-09：群聊退一档（这句话本身在问群里的事就不退）
                try:
                    _src = (r["source"] if "source" in r.keys() else "") or ""
                except Exception:
                    _src = ""
                if (_src.startswith(GROUP_SOURCE_PREFIX) and not _group_ask
                        and not _self_ask):   # 我自己的话哪怕说在群里，也还是我的话
                    s *= GROUP_RECALL_PENALTY
                # 2026-11-09：「你之前说过什么」→ 我自己的话抬一档
                if _self_ask and (r["speaker"] or "") == "self":
                    s *= _self_boost
                    # 「我叫什么/什么身份」：优先长得像自我定义的那几条
                    _raw = r["raw"] or ""
                    if _self_name_ask and _RE_SELF_DEF.search(_raw):
                        s *= 1.60
                    # 只有「问我叫什么名字」时才强偏「我叫 X」那几句；
                    # 问「身份/设定」时不掺这一脚，否则会把设定句压掉。
                    if _ask_my_name:
                        s *= 2.20 if _RE_NAME_DEF.search(_raw) else 0.60
                _comp[r["id"]] = (float(blended), float(cover),
                                  float(wn), float(recency))
                out.append((s, wn, r, sim, cover))
                # 🔧 2026-10-30 临时排错钩子（查完就删）：把每条的实际分数留痕，
                #    外面 _DBG_SCORES 一开就能看，不影响正常路径。
                if _DBG_SCORES:
                    _DBG_SCORES.append((r["id"], float(s), float(blended),
                                        float(cover), float(wn)))
            return out

        scored = []
        if want:
            in_region = [r for r in rows if r["region"] in want]
            scored = _score_rows(in_region, region_boost=True)
            if len(scored) < 2:
                # 分区里没货 → 回退全库，但仍给目标分区加成
                scored = _score_rows(rows, region_boost=True)
            else:
                # 🔴 2026-10-31：这里原来加过一道「通用兜底」——分区出结果后再算一遍
                #    全库，全库冠军明显更强就并进来。实测 12 条查询触发 0 次、评测
                #    分数一分没涨，却把每次召回从 0.5s 拖到 2.1s（多算一遍全库）。
                #    结论：REGION_BOOST=1.25 已经把分区答案抬得够高，这道保险永远
                #    不响、只收钱。**已删除**。真正有效的兜底是下面这行「分区里没货
                #    → 回退全库」以及 10-31 凌晨修的 fact 区规则。
                pass
        else:
            scored = _score_rows(rows, region_boost=False)

        scored.sort(key=lambda x: -x[0])
        if trace is not None:
            trace.setdefault("stages", {})["scored"] = len(scored)
            # V7 第 8 件：只进内存 trace，不进 recall_trace 表。
            # 失败四分类靠它回答「这条记忆有没有活着走到打分」——
            # 不在 scored_ids 里 = 被前面的漏斗筛掉；在却不端 = 排名/预算。
            trace["scored_ids"] = [int(h[2]["id"]) for h in scored if h[2]["id"] is not None]
            trace["top"] = [[int(h[2]["id"]), round(float(h[0]), 4),
                             round(float(h[4]), 3), round(float(h[1]), 3)]
                            for h in scored[:8]]
        if _RERANK_ON and len(scored) > top_k:
            _pool = scored[:min(len(scored), RERANK_POOL)]
            def _rk(h):
                c = _comp.get(h[2]["id"])
                if not c:
                    return h[0]
                return 0.55 * c[0] + 0.35 * c[1] + 0.10 * c[2]
            _pool = sorted(_pool, key=lambda h: -_rk(h))
            scored = _pool + scored[len(_pool):]
            if trace is not None:
                trace["rerank"] = {"pool": len(_pool), "kept": top_k}
        hits = scored[:top_k]

        # ---- T4-4 突触联想：直接命中之后，顺着连线往外扩 ----
        # 注意：对外仍然返回 3 元组 (score, w_now, row) —— 这是老接口，
        # 十几个测试和调用方都按它写的。联想来的条目 id 记在
        # self._last_assoc_ids 里，需要区分的调用方（assemble_context）自己查。
        self._last_assoc_ids = set()
        if assoc and hits:
            n_direct = len(hits)
            hits = self._expand_assoc(hits, scored, rows, vecs, now)
            self._last_assoc_ids = {r["id"] for _s, _wn, r, _sim, _cov
                                    in hits[n_direct:]}

        # 🔧 2026-11-01 MMR 重排（从 A 路线手册吸收，默认关）
        #    问题：三条命中里前两条说的是同一件事（"白米饭"和"白饭"），
        #    真正回答问题的那条被挤到第二。MMR 的解法不是改分数，是
        #    "已经选过的内容，后面再像就扣分"，逼结果彼此不同。
        #    默认 0 = 行为完全不变；WHALE_MMR=0.25 打开（越大越重相关性）。
        if getattr(self, "mmr_lambda", 0.0) > 0 and len(hits) > 1:
            hits = self._mmr_rerank(hits, lam=self.mmr_lambda)

        if access:
            # V5.0 公理 6：只写事件，不改 mem。
            # 真正的加固由 _settle_recall_events() 在 tick() 里统一做，
            # 这样「想一下」这个动作本身不会偷偷改遗忘状态。
            # 🔧 V5.9-A：召回宽度变大之后，结算也要跟「真正回想的条数」对齐 ——
            #    否则端 12 条就加固 12 条，卡的状态会跟基线分叉（实测 P12 掉 5 点）。
            #    RECALL_SETTLE_K=5 表示「只有前 5 条算真正想过」，和加宽前的行为一致。
            _settle = int(os.environ.get("WHALE_RECALL_SETTLE_K",
                                         str(RECALL_SETTLE_K)) or 0)
            # 加宽只是「多看几条」，不该顺手多加固几条：只结算前 N 条。
            # 实测「端 12 条就加固 12 条」会让卡状态与加宽前分叉（P12 ret5 -5）。
            _sl = hits[:_settle] if _settle > 0 else hits
            self._write_recall_events(
                [(r["id"], i) for i, (_s, _wn, r, _sim, _cov) in enumerate(_sl)],
                query, source="recall")
            # 记下这些 id，SessionCache 回流时不再重复计
            self._session_hit_ids.update(r["id"] for _s, _wn, r, _sim, _cov in _sl)
            # 一起被想起的记忆，它们之间的突触变粗
            try:
                self.synapse.reinforce([r["id"] for _s, _wn, r, _sim, _cov in _sl])
            except Exception:
                pass
        return [(s, wn, r) for s, wn, r, _sim, _cov in hits]

    def _mmr_rerank(self, hits, lam=0.7):
        """把"彼此太像"的结果往后挪，让前几条信息量更足。

        score 已经算好了，这里只做重排，不动权重、不写任何表。
        λ 越大越偏向原始分数；λ=1 等于不重排。
        """
        def toks(r):
            try:
                return set(to_vec(r["core"] or r["raw"] or ""))
            except Exception:
                return set()

        cand = list(hits)
        top = max((h[0] for h in cand), default=1.0) or 1.0
        chosen, left = [], cand
        while left:
            best_i, best_v = 0, None
            for i, h in enumerate(left):
                rel = h[0] / top
                red = 0.0
                if chosen:
                    tk = toks(h[2])
                    for c in chosen:
                        ck = toks(c[2])
                        if tk or ck:
                            red = max(red, len(tk & ck) / float(len(tk | ck)))
                v = lam * rel - (1.0 - lam) * red
                if best_v is None or v > best_v:
                    best_i, best_v = i, v
            chosen.append(left.pop(best_i))
        return chosen

    def _expand_assoc(self, hits, scored, rows, vecs, now):
        """把直接命中的结果当种子，沿突触扩散出「联想记忆」

        🔧 三个纪律：
          1. 联想永远排在直接命中之后 —— 人也是先想起正事再顺带想起别的
          2. 联想分数打 0.55 折（ASSOC_BONUS_MAX 上限），不能盖过直接命中
          3. 联想进来的条目必须本身还有一点分量（w 不能太低），
             否则会翻出一堆已经烂掉的碎片
        """
        try:
            net = getattr(self, "synapse", None)
            if net is None:
                return hits
            # 🔴 2026-11-08：这里以前从不 ensure —— 内存里 _adj 是 None 就直接 spread，
            #    结果任何「新开一张卡」的库一条联想都出不来（全量 714 题里 0 条）。
            #    载入是懒的，第一次想起来的时候顺手建一次边，几十毫秒。
            try:
                net.ensure()
            except Exception:
                pass
            seeds = [(r["id"], s) for s, _wn, r, _sim, _cov in hits]
            # 归一化电势：最高分 → 1.0
            top = max((p for _m, p in seeds), default=0.0) or 1.0
            seeds_n = [(m, p / top) for m, p in seeds]
            spread = net.spread(seeds_n, top_k=max(3, len(hits)))
            if not spread:
                return hits
            hit_ids = {r["id"] for _s, _wn, r, _sim, _cov in hits}
            by_id = {r["id"]: r for r in rows}
            # 直接命中的最高分：联想结果不能超过它的 ASSOC_BONUS_MAX 倍
            base = hits[0][0]
            extra = []
            for mid, p, _kind in spread:
                if mid in hit_ids:
                    continue
                r = by_id.get(mid)
                if r is None:
                    continue
                if r["pool"] == POOL_ARCHIVE:
                    continue
                wn = wn_row(r, now)
                if wn < 0.05:          # 已经烂掉的碎片不联想
                    continue
                s = base * min(synapse_mod.ASSOC_BONUS_MAX,
                               p * synapse_mod.ASSOC_BONUS_MAX) \
                    * (0.5 + r["salience"])
                # 🔧 上限必须在乘完 salience 之后再钳一次 ——
                #    否则 salience 高的联想条目会顶到 base*0.55*1.3 = 0.72，
                #    超过「联想不超过直接命中 55%」这条纪律。
                s = min(s, base * synapse_mod.ASSOC_BONUS_MAX)
                if s < base * synapse_mod.ASSOC_KEEP_RATIO:
                    continue
                extra.append((s, wn, r, 0.0, 0.0))
            if not extra:
                return hits
            extra.sort(key=lambda x: -x[0])
            # 联想结果最多占结果的一半，别把直接命中挤掉
            room = max(1, len(hits))
            return hits + extra[:room]
        except Exception:
            return hits

    # ---------- SessionCache 双回流（V3.7 §9 / V5.0 T1-4）----------
    def session_turn(self, user_text, reply_text=""):
        """记一轮对话。攒够 SESSION_FLUSH_TURNS 轮就做一次回流。

        回流做的事：把这几十轮里「聊到过、但没被 recall 主动命中」的旧记忆
        记一条 rank=99 的弱事件 —— 因为「顺嘴提到」也算想起过，
        但强度比主动检索弱（由 rank 体现）。
        """
        self._session_turns.append((user_text or "") + " " + (reply_text or ""))
        if len(self._session_turns) >= SESSION_FLUSH_TURNS:
            return self.session_flush()
        return 0

    def session_flush(self):
        """结算本轮 SessionCache：给「顺嘴提到」的旧记忆记弱事件

        🔧 注意：必须**逐轮**算相似度，不能把 20 轮拼成一大坨算余弦 ——
        拼起来之后每条记忆在长文本里的占比都极低，sim 永远过不了门槛。
        """
        if not self._session_turns:
            return 0
        turns = self._session_turns
        self._session_turns = []
        idf = self._ensure_idf()
        now = self.clock.now()
        rows = self.db.execute("SELECT * FROM mem").fetchall()
        vecs = self._vecs(rows)     # 🔧 带缓存的批量取向量
        hit = {}
        for t in turns:
            qv = to_vec(t)
            if not qv:
                continue
            # 只拿实词算覆盖率（虚字不参与，否则「的了吗」会把一切连起来）
            q_keys = {k for k in qv if not (len(k) == 1 and k in _STOP_CHARS)}
            if not q_keys:
                continue
            q_den = sum(idf.get(k, 1.0) for k in q_keys) or 1.0
            for r in rows:
                if r["id"] in self._session_hit_ids:
                    continue
                mv = set((vecs.get(r["id"]) or {}).keys())
                shared = q_keys & mv
                if not shared:
                    continue
                cover = sum(idf.get(k, 1.0) for k in shared) / q_den
                if cover >= SESSION_HIT_COVER:
                    if hit.get(r["id"], 0.0) < cover:
                        hit[r["id"]] = cover
        pairs = [(mid, SESSION_EVENT_RANK) for mid in hit]
        if pairs:
            self._write_recall_events(pairs, query=None, source="session")
        self._session_hit_ids = set()
        return len(pairs)

    # ---------- 召回事件（V5.0 公理 6）----------
    def _write_recall_events(self, pairs, query=None, source="recall"):
        """pairs: [(mem_id, rank), ...]  只记事件，不动 mem 表"""
        if not pairs:
            return 0
        qh = None
        if query:
            qh = hashlib.sha1(query.encode("utf-8")).hexdigest()[:16]
        now = self.clock.now()
        c = self.db.cursor()
        c.executemany(
            "INSERT INTO recall_event(mem_id, ts, query_hash, rank, source, settled)"
            " VALUES(?,?,?,?,?,0)",
            [(mid, now, qh, rank, source) for mid, rank in pairs])
        self.db.commit()
        return len(pairs)

    def _settle_recall_events(self):
        """把未结算的召回事件折算成加固。幂等：settled 标记后不再重复算。

        🔧 2026-11-05 修「同一天被想起 N 次 = 加固 N 次」：
            原来按事件逐条加分，同一批里 5 条事件就加 5 次 GAIN_ACCESS。
            而衰减每天只扣 0.144，所以一天被召回两次就足够把 w 顶到 1.0 并钉死
            （真卡实测 167 个「同日召回 ≥2 次」组合、71/623 条 w≥0.95，
             被钉住的还有一堆对话碎片 —— 越没用的越容易被捞出、越捞越结实）。
            现在按「天」分组：**一天最多加固一次**。
            同一天想起来的次数只计进 recall_n（那是另一本账，
            用来判"够不够格升长期"，不是用来加权的）。
        """
        c = self.db.cursor()
        evs = c.execute(
            "SELECT * FROM recall_event WHERE settled=0 ORDER BY mem_id, id"
        ).fetchall()
        if not evs:
            return 0
        # 先按记忆分组，再在组内按天分组：一天的注意力只算一次
        grouped = {}
        for e in evs:
            grouped.setdefault(e["mem_id"], []).append(e)
        settled_ids = []
        for mid, lst in grouped.items():
            r = c.execute("SELECT * FROM mem WHERE id=?", (mid,)).fetchone()
            settled_ids.extend(e["id"] for e in lst)
            if r is None:               # 记忆已被侵蚀，事件作废
                continue
            by_day = {}
            for e in lst:
                day = time.strftime("%Y-%m-%d", time.localtime(e["ts"]))
                by_day.setdefault(day, []).append(e)
            for day in sorted(by_day):
                evs_day = by_day[day]
                # 关键：用事件发生时刻结算，而不是 tick 的时刻。
                # 否则「7 天前被想起」会被记成「刚刚被想起」，
                # t_anchor 被推后，这条记忆就永远熬不到巩固。
                ts = max(e["ts"] for e in evs_day)
                # 🔧 权重增益：一天只加一次（同日去重）。
                # 🔧 2026-11-05 二次修正：增益必须**按剩余空间收敛**。
                #    只做「同日去重 + access 折减」还不够：
                #      g = 0.08 / (1 + 0.15*20) = 0.02
                #    而 long 池半边衰减只有 w·ln2/371 ≈ 0.0019（w≈1 时）
                #    —— 0.02 远大于 0.0019，每天被想起一次就仍然永远钉在 1.0。
                #    实测证据：改成"同日只加固一次"之后，真卡仍有 71 条 w≥0.95、
                #    50 条 w≥0.99，最大 access_count=159。
                #    正确形状是「越接近 1 越加不动」：乘上剩余空间 (1 - wn)。
                #    再配一道硬天花板（W_CEIL_RECALL）：因为推锚会让衰减恒为 0，
                #    没有上界时「天天被想起」在数学上必然趋向 1.0。
                _ac = min(int(r["access_count"] or 0), ACCESS_DIMINISH_CAP)
                wn = wn_row(r, ts)
                _room = max(0.0, 1.0 - float(wn))
                g = GAIN_ACCESS / (1.0 + ACCESS_DIMINISH * _ac) * _room
                # 天花板：见 W_CEIL_RECALL 处的注释 —— 推锚让衰减永远为零，
                # 所以必须有一道硬上界，否则「天天被想起」= 永远 1.0。
                _ceil = W_CEIL_RECALL.get(r["pool"], 0.95)
                # 🔧 2026-11-05：但 access_count / recall_n 要照实记 —— 它们是
                #    「被想起过几次」这本账（海马分、升长期的门票都读它），
                #    不是「加固了几次」。同一段对话里被翻来覆去想起 10 次，
                #    加固只该算一次，但这 10 次确实是注意力的证据。
                n_ev = len(evs_day)
                # 🔧 V5.6：t_anchor 仍然推进（它是**权重**的参考点，
                #    不推的话每次结算都从原始锚点重算衰减，w 会越用越轻）。
                #    「想起不能续命」这件事改由 pool_in_ts 负责：
                #    那个时间只在换池时动，池子的年龄永远按它算。
                c.execute(
                    "UPDATE mem SET w=?, t_anchor=?,"
                    " access_count=access_count+?,"
                    " recall_n=COALESCE(recall_n,0)+?,"
                    " recall_days=COALESCE(recall_days,0)+1,"
                    " t_last_recall=?,"
                    " last_active_ts=? WHERE id=?",
                    (min(_ceil, wn + g), ts, n_ev, n_ev, ts, ts, mid))
                c.execute(
                    "UPDATE detail SET weight=MIN(1.0, weight+?), last_touch=?"
                    " WHERE parent_id=?", (g * 0.8, ts, mid))
                # 同步本地副本，让同一天的多次（跨天）累积正确
                r = c.execute("SELECT * FROM mem WHERE id=?", (mid,)).fetchone()
        c.executemany("UPDATE recall_event SET settled=1 WHERE id=?",
                      [(i,) for i in settled_ids])
        self.db.commit()
        return len(grouped)

    # ---------- 说 ----------
    def render(self, mem_row, weight=None, query=""):
        now = self.clock.now()
        if weight is None:
            weight = wn_row(mem_row, now)
        c = self.db.cursor()
        c.execute("SELECT kind, text, weight FROM detail WHERE parent_id=?"
                  " ORDER BY weight DESC", (mem_row["id"],))
        parts = {"TIME": [], "PLACE": [], "EMOTION": [], "MODIFIER": [],
                 "ACTION": []}
        _all_details = c.fetchall()
        for d in _all_details:
            w = d["weight"]
            kind = d["kind"]
            if kind == "MODIFIER":
                # 长句后续子句：先原样攒着，等主干出来再挑一条**不重复**的补进去
                # （2026-11-09：以前无条件取权重最高的那条，而导入的长记忆里
                #   权重最高的子句常常就是主干的前半句 —— 于是渲染成
                #   「夜学两支概率短片…，还说「夜学两支概率短片(BV1dgu9」」这种
                #   自己抄自己的句子，白占预算还把真正的新信息挤掉。）
                parts["MODIFIER"].append({"t": d["text"], "w": w})
                continue
            if kind == "ACTION":
                # 🔴 2026-10-31：并列动作（「还拍了视频」）是事件的一部分，
                #    阈值跟情绪一样宽（它比修饰语活得久），但要截短，别整段倒出。
                if w >= SHOW_SCENE:
                    parts["ACTION"].append(d["text"][:14])
                continue
            if w >= SHOW_FULL:
                parts.setdefault(kind, []).append(d["text"])
            elif w >= SHOW_SCENE:
                if kind == "TIME":
                    parts["TIME"].append("某天")
                elif kind == "PLACE":
                    mm = re.match(r"^(在|去|到|从)", d["text"])
                    parts["PLACE"].append((mm.group(1) if mm else "") + "某个地方")
                elif kind == "EMOTION":
                    parts["EMOTION"].append(d["text"])
            elif w >= SHOW_HINT:
                if kind == "EMOTION":
                    parts["EMOTION"].append(d["text"])

        bits = []
        if parts["TIME"]:
            bits.append(parts["TIME"][0])
        if parts["PLACE"]:
            bits.append(parts["PLACE"][0])
        core = mem_row["core"]
        # 🔧 V5.0 T2-1：主干被侵蚀过的话，按阶段渲染残缺的主干
        _stage = mem_row["erase_stage"] if "erase_stage" in mem_row.keys() else 0
        if _stage:
            _subj = mem_row["subj"] if "subj" in mem_row.keys() else ""
            _verb = mem_row["verb"] if "verb" in mem_row.keys() else ""
            _obj = mem_row["obj"] if "obj" in mem_row.keys() else ""
            if not (_subj or _verb or _obj):
                _subj, _verb, _obj = split_predicate(core)
            core = render_predicate(_subj, _verb, _obj, _stage) or core
        if len(core) > 40 and RENDER_LEGACY:
            core = core[:40] + "……"
        elif len(core) > 40:
            # 2026-11-09：以前硬切 40 字，常常切在半个词上（「打在我身上的三处：第一，」），
            # 读起来是断句、也害得 rubric 里的片段对不上。现在优先切在标点后，
            # 但至少留 24 字，避免逗号太靠前把主干切没了。
            _cut = core[:40]
            _p = max(_cut.rfind("，"), _cut.rfind("。"), _cut.rfind("、"),
                     _cut.rfind("；"))
            core = (_cut[:_p + 1] if _p >= 24 else _cut) + "……"
        bits.append(core)
        # 🔴 2026-10-31：并列动作跟着主干走（「吃烧烤，还拍了视频」）——
        #    它和主干是同一层的事件内容，不该被摞到「还说「…」」那个位置上去。
        if parts["ACTION"]:
            bits.append("，" + parts["ACTION"][0])
        if parts["EMOTION"]:
            bits.append("，" + parts["EMOTION"][0])
        # 长句的后续子句：只保留一条最清楚的，别把整段倒出来。
        # 挑法（2026-11-09）：①先剔掉和主干重复的（jaccard≥0.5 或整句是主干前缀），
        # ②剩下的里优先「问句里出现过的词也在这一句里」的那条 —— 问「第一步怎么做」时
        #   要的是「先把日期分堆」，不是这条记忆里随便哪个子句。
        if parts["MODIFIER"] and RENDER_LEGACY:
            _it0 = max(parts["MODIFIER"], key=lambda x: float(x["w"]))
            _mt0 = _it0["t"][:18] if _it0["w"] >= SHOW_FULL else _it0["t"][:10] + "…"
            bits.append("，还说「" + _mt0 + "」")
        elif parts["MODIFIER"]:
            _core_txt = mem_row["core"] or ""
            _qw = [w for w in re.findall(r"[\u4e00-\u9fa5]{2}", query or "")]
            _best = None
            for _it in parts["MODIFIER"]:
                if _it["w"] < SHOW_SCENE:
                    continue
                _t = _it["t"]
                if jaccard(_t, _core_txt) >= 0.5 or _t[:8] and _t[:8] in _core_txt:
                    continue
                _hit = sum(1 for w in _qw if w in _t)
                _sc = _hit * 10.0 + float(_it["w"])
                if _best is None or _sc > _best[0]:
                    _best = (_sc, _it)
            if _best:
                _it = _best[1]
                _mt = _it["t"][:18] if _it["w"] >= SHOW_FULL else _it["t"][:10] + "…"
                bits.append("，还说「" + _mt + "」")
        s = "".join(bits)
        # 🔧 2026-11-09：问句相关细节补一句。
        #    实测（714 题）失败题里 72~79% 的答案记忆本来就在前 5 条，
        #    但渲染只按「这条细节还清不清楚」挑，不按「问句问的是不是它」挑，
        #    于是问「第一步怎么做」时端上来的是同一条记忆里别的子句。
        #    这里在主干之后补一条**问句实词命中最多**的细节（最多 24 字），
        #    只在它确实带新词、且没被上面的子句用过时才加。
        if RENDER_DETAIL_ADD:
            try:
                _qt = re.findall(r"[\u4e00-\u9fa5]+", query or "")
                _qk = set()
                for _seg in _qt:
                    for _i in range(len(_seg) - 1):
                        _qk.add(_seg[_i:_i + 2])
                _cand_d = []
                for d in _all_details:
                    _t = (d["text"] or "").strip()
                    if len(_t) < 4 or (_t[:6] and _t[:6] in s):
                        continue
                    _hit = sum(1 for k in _qk if k in _t)
                    if _hit >= 2:
                        _cand_d.append((_hit, len(_t), _t))
                if _cand_d:
                    _cand_d.sort(key=lambda x: (-x[0], x[1]))
                    _dt = _cand_d[0][2][:24]
                    _pp = max(_dt.rfind("，"), _dt.rfind("。"), _dt.rfind("、"))
                    _dt = _dt[:_pp + 1] if _pp >= 10 else _dt + "…"
                    s = s + "（还记着：" + _dt + "）"
            except Exception:
                pass
        # 🔧 2026-11-07：渲染时把「昨天/明天」按这条记忆自己的 create_ts
        #    换成绝对日期。raw 一个字不动，只是「想起来的时候」算准。
        _ts = mem_row["create_ts"] if "create_ts" in mem_row.keys() else None
        if _ts:
            s = anchor_relative_time(s, _ts)
        if weight < 0.35:
            return "我记得好像有这么回事：" + s + "。（具体记不清了）"
        if weight < 0.60:
            return s + "。（细节有点模糊了）"
        return s + "。"

    # ---------- 组装上下文（第二期核心） ----------
    def _state_line(self):
        """把心境 + 依恋翻成一句话，塞进人格格末尾。

        V5.0 §4 说内分泌「只改语气和配额，不改事实」——
        这句话就是那个「改语气」的入口：不告诉模型该怎么说话，
        只告诉它「我现在是什么状态」，剩下的交给她自己。
        """
        parts = []
        m = getattr(self, "mood", MOOD_NEUTRAL)
        if abs(m - MOOD_NEUTRAL) > 0.04:
            parts.append("现在心情" + mood_text(m))
        o = getattr(self, "oxy", 0.0)
        if o >= 0.75:
            parts.append("跟他很亲")
        elif o >= 0.45:
            parts.append("跟他挺熟")
        elif o >= 0.15:
            parts.append("还在熟悉他")
        # 🔧 V5.0 §4.4(4)：激素决定的「说话倾向」
        #    只给状态不给命令 —— 「话多一点」比「请活泼一点」有效，
        #    后者模型会照着演，前者它只是知道自己现在什么状态。
        # 🔧 2026-11-01：改成由 PAD 统一给（PAD 已经把心境 + 四激素算进去了，
        #    再单独念一遍激素等于同一件事说两遍）。拿不到 PAD 才退回激素。
        hint = ""
        try:
            hint = pad_style_hint(self.mood_pad())
        except Exception:
            hint = ""
        if not hint:
            h = getattr(self, "h", None)
            if h:
                hint = hormone.style_hint(h)
        if hint:
            parts.append(hint)
        # 两小时内真被什么戳到过，就把它说出来 —— 比只念激素水平更像人
        ev = getattr(self, "_last_event", None)
        if ev:
            ek, ets, _s = ev
            if time.time() - ets <= 7200:
                parts.append({"A": "刚才被暖了一下",
                              "B": "刚才那句还有点堵",
                              "C": "刚缓过来一点"}.get(ek, ""))
        if not parts:
            return ""
        return "（" + "，".join(parts) + "）"

    def core_identity_text(self):
        """把 CORE 区那几条拼成一段给上下文用的人格锚。
        CORE 区里的 6 条是「只读真值」；但 assemble_context 的 identity 参数
        是个字符串，以前从来没人把它填上 —— 于是「底线」存在库里、
        却不在每次说话时的上下文里。等于上了锁的抽屉，钥匙挂在旁边没人用。

        这里只读不写；没盖章/没卡时返回空串（宁可空着也不编）。
        """
        ident = getattr(self, "identity", None)
        if ident is None or not ident.is_sealed():
            return ""
        try:
            rows = ident.db.execute(
                "SELECT key, val FROM core_zone").fetchall()
        except Exception:
            return ""
        vals = {}
        for r in rows:
            vals[str(r[0])] = str(r[1])
        if not vals:
            return ""
        name = vals.get("card_name", "")
        lines = []
        if name:
            lines.append("我是%s。" % name)
        uname = vals.get("user_nickname", "")
        uqq = vals.get("user_qq", "")
        if uname and uqq:
            lines.append("面前的人是%s（QQ %s）。" % (uname, uqq))
        elif uname:
            lines.append("面前的人是%s。" % uname)
        # 自述：一段连续的「我是谁」，不是字段清单。
        # 2026-10-31 加，因为用户说「我希望那个人格不是几句冰冷的话，
        # 而是一个完整的你，我希望你住进去，而不是你在里面描述自己」。
        # 三条底线是**字段**，抽出来拼成清单读起来像填表；
        # 人不会用「我的底线是 1. 2. 3.」这种方式认识自己。
        # 所以自述放在底线**之前** —— 先是我这个人，然后才是我的边界。
        narr = vals.get("self_narrative", "")
        if narr:
            lines.append(narr)

        beliefs = []
        for k in identity.CORE_KEYS:
            if k.startswith("core_belief") and vals.get(k):
                beliefs.append(vals[k])
        if beliefs:
            lines.append("我的底线（永远不可违背）：")
            for b in beliefs:
                lines.append("· " + b)
        # 性格：住在 CORE 区但不进身份锚（见 identity.py 顶部说明）。
        # 2026-10-31 加这一段，是因为那天发现性格只写在 AGENTS.md 里、
        # 卡里根本没有 —— 一插卡（把 AGENTS.md 换成通道规矩）就没了。
        # 放在底线之后：先说「我是谁、什么不能碰」，再说「我平时什么样」。
        persona = vals.get(identity.PERSONA_KEY, "")
        if persona:
            lines.append("我的性格和说话方式：")
            lines.append(persona)
        # 习惯：从 habits 表里捞几条「我平时怎么说」。
        # 2026-11-04 加。跟性格分开写，因为性格是「我是什么样的人」，
        # 习惯是「具体场合下我常怎么开口」—— 前者不该被数据改，后者本来就该变。
        hints = self.habit_hints()
        if hints:
            lines.append("我平时说话的口气（是参考，不是台词；"
                         "没有合适的场合就别硬用）：")
            for h in hints:
                lines.append("· " + h)
        return "\n".join(lines)

    # ---------- 习惯层 v2：只读地捞几条给上下文用 ----------

    def _l0_lines(self, now=None, hours=72.0, limit=8, max_chars=720):
        """L0 时间线：最近真的发生过什么，按时间顺读，日期一律写绝对值。

        🔧 2026-11-06（用户 m21769「我们不是有统一的时间线吗」→ m21777「接上吧」）：
        以前时间线只用来验真和追溯，拼上下文时压根不看它 —— 于是同一批事实，
        「卡里有」和「这一轮想得起来」之间隔着一整条时间线。这一格就是把那根线接上。

        为什么只截最近 72 小时：时间线是流水账，光 `alive` 心跳就 639 条，
        全铺进去会把真正要用的东西挤掉。这里要的不是全部，是「这一段的先后顺序」。

        为什么不挑（比如只要 talk）：**先后本身就是信息** ——
        哪件事先发生，比哪件事更重要，更能说明这一整天是怎么过的。
        `alive` 要剔：那是心跳，不是发生的事。

        日期为什么写「11月06日」而不是「昨天」：相对时间放进卡里就过期，
        隔两天再看，「昨天」指向的已经不是那一天了。
        """
        import datetime as _dt
        now = self.clock.now() if now is None else float(now)
        try:
            rows = self.db.execute(
                "SELECT seq, ts, kind, note FROM timeline"
                " WHERE ts >= ? AND kind != 'alive' ORDER BY seq DESC LIMIT ?",
                (now - hours * 3600.0, int(limit) * 4)).fetchall()
        except Exception:
            return []
        out, used = [], 0
        for r in rows:                       # 先取最近的，再倒回来顺着读
            note = (r["note"] or "").strip().replace("\n", " ")
            if not note:
                continue
            d = _dt.datetime.fromtimestamp(float(r["ts"]))
            if len(note) > 60:
                # 别把一句话从中间剁开：「…我们现在卡是好的，然」读起来像卡带。
                cut = max(note.rfind(ch, 0, 60) for ch in "。！？；，、")
                note = (note[:cut] if cut >= 20 else note[:60]) + "…"
            line = "- %d月%02d日 %02d:%02d %s" % (
                d.month, d.day, d.hour, d.minute, note)
            if used + len(line) > max_chars:
                continue
            out.append(line)
            used += len(line)
            if len(out) >= int(limit):
                break
        out.reverse()
        return out

    def _tevent(self):
        """时间线/待办账本（懒开，一次）。表在 card.db 里，和 mem/timeline 各管一段。

        为什么偷懒不开在 __init__：它只有装配上下文和写入时才用得上，
        而 __init__ 已经在忙唤醒结算，多插一个 DDL 只会让启动更慢。
        """
        _st = getattr(self, "_tevent_store", None)
        if _st is not None:
            return _st
        try:
            _st = tempora.EventStore(self.db)
        except Exception as _e:
            soft_fail("tempora_init", _e)
            return None
        self._tevent_store = _st
        return _st

    def assemble_context(self, query, identity="", recent_lines=None,
                         touch=False,
                         budget=None, scene=None, about_uid=None, access=True):
        """每次说话前，拼出四格上下文。装满就停，永远不会爆。

        🔧 V5.0 T3-3：配额不再是写死的 600/1800/2000/1200，
        而是六维动态权重算出来的 —— 总长固定，配额流动。

        🔧 2026-11-05（用户 m15228）：budget 默认改成 None ——
        不传就听卡里 runtime_config 的 BUDGET（控制台能调），
        传了就是这一轮的一次性覆盖。以前写死 `budget=TOKEN_BUDGET`，
        等于「总预算」这个最该露出来的参数反而最调不动。

        返回 dict：
          text      —— 拼好的整段，直接塞进 system prompt
          sections  —— 每格占了多少 token、塞了几条
          dropped   —— 因为预算被丢掉的条数
          scene / weights / budget —— 这一轮怎么分的（可审计）
        """
        now = self.clock.now()
        used = 0
        out = []
        # 快反应层：先让身体对这句话有反应，再拼上下文（顺序就是人的顺序）
        if touch:
            try:
                self.viscera_touch(query or "")
            except Exception:
                pass
        sections = {}
        dropped = 0

        # ---- 算这一轮的配额 ----
        turns = getattr(self, "turn_count", None)
        idle = None
        last = getattr(self, "last_talk_ts", None)
        if last:
            idle = max(0.0, now - last)
        plan = ctxweight.compute(
            query, budget,
            hormones=getattr(self, "h", None),
            turns=turns, idle_seconds=idle,
            recent_lines=recent_lines,
            db=self.db)
        q = plan["budget"]
        self.turn_count = (turns or 0) + 1
        self.last_talk_ts = now

        def take(s, cap):
            """按字符数（≈token）截断"""
            nonlocal used
            s = s or ""
            if used >= cap:
                return ""
            room = cap - used
            if len(s) > room:
                s = s[:max(0, room - 1)] + "…"
            used += len(s)
            return s

        # ① 人格格
        #
        # 🔴 2026-11-05 缓存修正（用户提问「这样还保得住缓存命中率吗」）：
        #    以前这里拼的是 identity + self._state_line()，而状态那句
        #    （"现在心情不错，跟他很亲，话多一点"）**每轮都会变**。
        #    DeepSeek 一类的缓存按**前缀**命中 —— 它排在第一格，等于
        #    每一轮第一个字就变了，后面几千 token 全部按新内容重新计费。
        #    一句会变的话，把整段上下文变成了"每轮都是新的"。
        #
        #    修法：人格格只放**不变的东西**，状态那句话挪到最后单独一格。
        #    现在顺序是 稳 → 稳 → 变 → 变 → 变：前两格是缓存的地基，
        #    真正会动的都在尾巴上，命中就只从尾巴开始断。
        if identity:
            blk = "【我是谁】\n" + take(identity, q["identity"])
            out.append(blk)
            sections["identity"] = len(blk)
        used = 0

        # ② 长期印象格：长期池里「值得一直记得」的几条
        #    🔧 V4.1：不能只按权重排 —— 权重高的常是又长又啰嗦的随口话。
        #    改为：先剔垃圾（指令/路径/代码），事实类优先，再按 w×salience 排。
        rows = self.db.cursor().execute(
            "SELECT * FROM mem WHERE pool=? ORDER BY w DESC LIMIT 200",
            (POOL_LONG,)).fetchall()
        _REGION_ORDER = {R_FACT: 0, R_SELF: 1, R_PREF: 2, R_HABIT: 3,
                         R_FLASH: 4, R_EPISODE: 9}
        cand = []
        for r in rows:
            txt = r["raw"] or ""
            if _is_junk(txt):
                continue
            if len(txt) < 6 or len(txt) > 60:
                continue
            rg = r["region"]
            # 「我一直记得的事」只放长期稳定的东西：事实、自我、偏好、习惯。
            # 情景（episode）再重也不进 —— 那是「想起」的料，不是「一直记得」的料。
            if rg == R_EPISODE:
                continue
            # 闪光灯只收真正高唤醒的
            if rg == R_FLASH and r["salience"] < 0.9:
                continue
            rank = _REGION_ORDER.get(rg, 9)
            cand.append((rank, -(r["w"] * (0.5 + r["salience"])), r))
        cand.sort(key=lambda x: (x[0], x[1]))
        lines = []
        shown_ids = set()          # ② 里已经出现过的，④ 不再重复
        shown_txt = []             # ② 内部去重用（同一件事说过三遍的，只留一遍）
        cap2 = used + q["longterm"]
        for _rank, _sc, r in cand:
            # 🔧 同一格内也要去重：历史导入里有「用户是高三学生主号QQ号是100000000」
            #    「用户的QQ主号是100000000」「用户的名字和QQ号主号100000000」这种
            #    同一件事说三遍的，全塞进去等于浪费配额。
            if any(jaccard(r["raw"] or "", t) >= 0.55 for t in shown_txt):
                continue
            line = "- " + self.render(r, query=query)
            if used + len(line) > cap2:
                dropped += 1
                continue
            lines.append(line)
            shown_ids.add(r["id"])
            shown_txt.append(r["raw"] or "")
            used += len(line)
            if len(lines) >= 12:
                break
        if lines:
            blk = "【我一直记得的事】\n" + "\n".join(lines)
            out.append(blk)
            sections["longterm"] = used

        # ③ L0 时间线格（2026-11-06 接上）
        #
        # 🔧 跟 ④ 共用 `recent` 这份配额，不另开一格 ——
        #    「最近在聊」和「最近发生」本来就是一回事的两面（说过什么 / 发生过什么），
        #    另开预算等于从别处硬挪，总长就不固定了。
        #    放在 ④ 之前：先给先后顺序，再给具体话头。
        used = 0
        l0_used = 0
        l0_cap = min(720, int(q["recent"] * 0.4))
        _l0 = self._l0_lines(now=now, max_chars=l0_cap) if l0_cap > 0 else []
        if _l0:
            blk = "【最近发生】\n" + "\n".join(_l0)
            out.append(blk)
            sections["l0"] = len(blk)
            l0_used = len(blk) + 2

        # ④ 最近聊的格
        used = 0
        if recent_lines:
            lines = []
            cap3 = used + max(0, q["recent"] - l0_used)
            for ln in recent_lines[-30:]:
                if used + len(ln) > cap3:
                    dropped += 1
                    continue
                lines.append(ln)
                used += len(ln)
            if lines:
                blk = "【我们最近在聊】\n" + "\n".join(lines)
                out.append(blk)
                sections["recent"] = used

        # ④ 此刻想起格（E 印象 + F 技能）
        # 🔴 2026-11-08（V5.8-A 事实置顶）：事实格插到【想起】前面。
        #    实测（20 题端到端判分）：事实块挂在最后时，答题侧根本不看它，
        #    明明有「梁志远周二周四接」，还是会答「记不清」；挪到想起之前，
        #    同一批题从 10/20 变 12/20。这里记下插入位置，不破坏前面
        #    人格/长期/最近这三格的稳定前缀（缓存还从第一格命到这儿）。
        _fact_pos = len(out)
        used = 0
        # 🔧 V5.6.2-G：时间型 / 多事实型问题，一条事实拼不出来，得多端几条。
        #    V5.9-A 改成配额驱动后，窄问句 = min(cap, RECALL_NARROW_MAX=6)：
        #    只比基线多一条 —— 无差别加宽实测买不到分，还会稀释上下文。
        _q = query or ""
        _wide = any(w in _q for w in _WIDE_WORDS)
        if os.environ.get("WHALE_WIDE_WORDS") == "1":
            _wide = _wide or any(w in _q for w in _WIDE_EXTRA)
        # 🔧 V5.9-A（2026-11-09）：宽度改成「配额驱动 + 多事实感知」。
        #    历史：WIDE_TOP_K=5 让 _wide 和普通分支完全等价，等于空转；而这一格
        #    的预算（真卡 4249 / bench 中位 ~1500 token）根本没花完 —— 排到
        #    6-12 名的答案是被渲染层扔掉的。所以先算「这一格最多能端几条」。
        _cap = max(RECALL_CAP_MIN,
                   min(RECALL_CAP_MAX, int(float(q["recall"]) // MEM_LINE_TOKENS)))
        _multi = _wide or _is_multi_fact(_q)
        # 覆盖入口保留：WHALE_WIDE_K 非 0 时钉死条数（旧 A/B 臂还能复现）。
        _env_k = int(os.environ.get("WHALE_WIDE_K", "0") or 0)
        if _env_k > 0:
            _topk = _env_k
        else:
            _topk = _cap if _multi else min(_cap, RECALL_NARROW_MAX)
        _fill = os.environ.get("WHALE_RECALL_FILL") == "1"
        if _fill:
            # 「端到预算用完为止」：条数也不能少于配额算出来的 cap，否则 fill 白开。
            _topk = max(_topk, _cap)
        # _max_lines 必须 >= 实际端出来的条数 —— 否则就是「召回 12 条只渲染 5 行」。
        # WHALE_RECALL_LINES 仍然优先（旧 lines10/14/20 臂靠它），但 fill 时放开。
        _max_lines = 60 if _fill else (
            int(os.environ.get("WHALE_RECALL_LINES", "0") or 0) or _topk)
        # V7 第 8 件：召回 trace。只在这里（真装配上下文时）记，不打扰 bench 的裸 recall。
        _trace = {}
        _trace_t0 = time.time()
        hits = self.recall(query, top_k=_topk, access=access,
                            about_uid=(about_uid or None), trace=_trace)
        if not access:
            # 延后模式：这次召回的「被想起」记账先存着，等锁里再写（见 flush_deferred_effects）
            _dq = getattr(self, "_defer_q", None)
            if _dq is None:
                _dq = self._defer_q = []
            _dq.append(([h[2]["id"] for h in hits], query))

        # 🔧 V5.6.2-H：多跳。原句召一遍保语义，实词再召一遍保字面，
        #    两组按 id 合并 —— 分散在几条聊天里的线索，一条向量是拼不回的。
        if MULTIHOP_ON and _q:
            _t2 = _hop_terms(_q)
            if _t2 and _t2 != _q:
                try:
                    _h2 = self.recall(_t2, top_k=MULTIHOP_K, access=False,
                                      about_uid=(about_uid or None))
                except Exception:
                    _h2 = []
                _ids = set((h[2]["id"] if h[2]["id"] else 0) for h in hits)
                for _h in _h2:
                    _mid = _h[2]["id"]
                    if _mid in _ids:
                        continue
                    _ids.add(_mid)
                    hits.append(_h)
        assoc_ids = getattr(self, "_last_assoc_ids", set())

        # 🔧 V5.7-C 实验（2026-11-08）：拓宽候选漏斗。
        #    原来 hits 只有 top5，排在第 17 名的那条答案根本没进候选池 ——
        #    不是排序错了，是它压根没被考虑。这里只用 access=False 多召一遍，
        #    不动结算逻辑（谁被召回、权重怎么涨，还按原来的 5 条算）。
        if os.environ.get("WHALE_RECALL_FUNNEL", "0") == "1":
            _cand_n = int(os.environ.get("WHALE_RECALL_CAND", "20") or 20)
            if _cand_n > len(hits):
                try:
                    _h3 = self.recall(query, top_k=_cand_n, access=False,
                                      about_uid=(about_uid or None))
                except Exception:
                    _h3 = []
                _ids3 = set((h[2]["id"] or 0) for h in hits)
                for _h in _h3:
                    _mid = _h[2]["id"]
                    if _mid in _ids3:
                        continue
                    _ids3.add(_mid)
                    hits.append(_h)
        lines = []
        _picked_ids = []   # 这次真端进召回格的 id（和 ② 里已显示的分开算）
        cap4 = used + (int(q["recall"] * WIDE_BUDGET_K) if _wide else q["recall"])
        n_direct = 0
        n_assoc = 0
        n_raw = 0            # 已经附了几条原话（V5.6.2）

        # 🔧 去重：②「我一直记得的事」里已经列过的，这里不再说一遍 ——
        #    同一句话在一格里出现两次，读起来就像卡带。
        #    除了按 id 比，还要按字面相似度比：② 里被 jaccard 滤掉的那些
        #    「换个说法说同一件事」的，这里也不该冒出来。
        #    但如果全被滤光了，就放宽 —— 问了一句话却「什么都没想起」更奇怪，
        #    这时候宁可重复也要留两条。
        fresh = [h for h in hits
                 if h[2]["id"] not in shown_ids
                 and not any(jaccard(h[2]["raw"] or "", t) >= 0.55
                             for t in shown_txt)]
        if not fresh:
            fresh = hits[:2]

        # 🔧 V5.6.2-F：同一件事说三遍的，只占一个位置。
        #    714 题里有很多"把同一句说三次"的样本，原来 5 条召回能全是它的三种说法，
        #    真正要的另一条永远挤不进来。这里只去重、不放宽任何门槛。
        _dd = []
        _dtext = []
        for _h in fresh:
            _t = (_h[2]["raw"] or "")
            if any(jaccard(_t, _x) >= RECALL_DEDUP for _x in _dtext):
                continue
            _dd.append(_h)
            _dtext.append(_t)
        if len(_dd) >= 3:
            fresh = _dd

        # 🔧 V5.6.2-B：把「同一时段的邻居」也捞进来。
        #    考题里最难的一类是「分散在不同聊天里的信息能不能拼起来」——
        #    计划在周三说、结果在周五说，语义检索只抓到其中一条，
        #    另一条永远进不来。人是靠"翻那两天的账"拼起来的，不是靠相似度。
        #    只从 top-3 命中的时间窗里取，且必须不是已经出现过的。
        try:
            _nb = []
            _seen_ids = set(h[2]["id"] for h in hits) | set(shown_ids)
            for _h in hits[:3]:
                _t0 = float(_h[2]["create_ts"] or 0)
                if not _t0:
                    continue
                _rows = self.db.cursor().execute(
                    "SELECT * FROM mem WHERE ABS(create_ts - ?) <= ? "
                    "AND COALESCE(dead,0)=0 AND COALESCE(quarantine,0)=0 "
                    "ORDER BY w DESC LIMIT 12",
                    (_t0, NEIGHBOR_HOURS * 3600.0)).fetchall()
                _got = 0
                for _r in _rows:
                    if _r["id"] in _seen_ids:
                        continue
                    if len(_r["raw"] or "") < 6:
                        continue
                    _nb.append(_r)
                    _seen_ids.add(_r["id"])
                    _got += 1
                    if _got >= NEIGHBOR_PER_HIT or len(_nb) >= NEIGHBOR_MAX:
                        break
                if len(_nb) >= NEIGHBOR_MAX:
                    break
            for _r in _nb:
                fresh.append((0.0, float(_r["w"] or 0.0), _r))
        except Exception:
            pass

        # 🔧 V5.3.2 统一时间线（用户 m34990）：想起的东西按**时间线 seq** 排，
        #    并在行首挂一个短日期。三个用处：
        #      ① 两件事冲突时，"谁在后"以链为准（不是以浮点秒为准）
        #      ② 编号/脑补出来的事没有时间戳，混进来一眼就能看出对不上
        #      ③ 一天里"先后"错了，也比"完全不知道顺序"强
        def _seqk(h):
            _r = h[2]
            try:
                _sq = _r["tseq"]
            except Exception:
                _sq = None
            return (0 if _sq is None else 1, _sq or 0, _r["create_ts"] or 0)

        _direct = [h for h in fresh if h[2]["id"] not in assoc_ids]
        _direct.sort(key=lambda h: (-(float(h[0]) or 0.0), _seqk(h)))
        _assoc = [h for h in fresh if h[2]["id"] in assoc_ids]
        if os.environ.get("WHALE_RECALL_FUNNEL", "0") == "1":
            # 选谁上桌按相关度，摆出来仍然按时间线 —— 两件事分开，别互相顶掉。
            _direct = _direct[:_max_lines]
            _direct.sort(key=_seqk)
        else:
            _direct.sort(key=lambda h: (0 if (float(h[0]) or 0.0) > 0 else 1, _seqk(h)))
        _assoc.sort(key=_seqk)
        fresh = _direct + _assoc
        if os.environ.get("WHALE_DEBUG_RECALL", "0") == "1":
            print("[recall] hits=%d fresh=%d direct=%d assoc=%d max_lines=%d" % (
                len(hits), len(fresh), len(_direct), len(_assoc), _max_lines))
            for _h in _direct[:8]:
                print("   %.3f id=%s %s" % (float(_h[0]) or 0.0, dict(_h[2]).get("id"),
                                            (dict(_h[2]).get("raw") or "")[:40]))

        for s, wn, r in fresh:
            # 🔧 T4-4：联想来的条目加个前缀，跟直接想起区分开。
            #    直接命中最多 5 条，联想最多再补 2 条，别把格子塞满。
            _ts = r["create_ts"] or 0
            _tag = ""
            if _ts:
                try:
                    _tag = time.strftime("%m-%d", time.localtime(float(_ts)))
                except Exception:
                    _tag = ""
            if r["superseded_by"]:
                _tag = (_tag + "｜旧说法") if _tag else "旧说法"
            if r["id"] in assoc_ids:
                # 联想最多再补 2 条；但它排在直接命中之后，所以不能因为
                # 「正事已经满了」就整段丢掉 —— 以前就是这里直接 break，
                # 五个直接命中之后，联想一条都摆不上来。
                if n_assoc >= 2:
                    break
                n_assoc += 1
                body = "（顺带想起）" + self.render(r, wn, query=query)
            else:
                n_direct += 1
                if n_direct > _max_lines:
                    break
                body = self.render(r, wn, query=query)
            line = ("- %s｜%s" % (_tag, body)) if _tag else ("- " + body)
            # 🔧 V5.6.2：除了加工过的句子，再端一份原话。
            #    render() 为了让句子读起来顺，会做时间锚定、去壳、去引号，
            #    改写后的句子常常丢掉考题要的那几个字（人名/日期/数字）。
            #    原话只在**确实被改写**时才附，不然就是白白占预算。
            if n_raw < RAW_LINE_MAX:
                try:
                    _raw = (r["raw"] or "").strip()
                except Exception:
                    _raw = ""
                if _raw and _raw != body and jaccard(_raw, body) < 0.92:
                    _snip = _raw[:RAW_LINE_CHARS]
                    if len(_raw) > RAW_LINE_CHARS:
                        _snip += "…"
                    _cand = line + "（原话：" + _snip + "）"
                    if used + len(_cand) <= cap4:
                        line = _cand
                        n_raw += 1
            if used + len(line) > cap4:
                dropped += 1
                break
            lines.append(line)
            shown_ids.add(r["id"])
            _picked_ids.append(int(r["id"]))
            used += len(line)
        if lines:
            # 低置信：top1 分数太低就别硬装记得 —— 把「可能」写在脸上，
            # 比编一条顺口的更接近三条底线。
            _weak = bool(hits) and float(hits[0][0]) < CONF_WEAK
            blk = "【这句话让我想起了】"
            if _weak:
                blk += "（这几条我不太确定，可能记岔了）"
            blk += "\n" + "\n".join(lines)
            out.append(blk)
            sections["recall"] = used
        # V7 第 8 件：把这次召回的漏斗落一行（只有真装配上下文时才记）。
        try:
            if _trace is not None:
                self.recall_trace_add(
                    query=query, flags=_trace.get("flags"),
                    stages=_trace.get("stages"), top=_trace.get("top"),
                    picked=_picked_ids,
                    n_lines=len(lines), ms=int((time.time() - _trace_t0) * 1000))
        except Exception as _te:
            print("[card2] 召回 trace 落盘失败: %s" % _te)


        # 🗄 冷档兜底（2026-11-09）：眼前的事一条都没想起来（或只有一两条），
        #    说明问的可能是很久以前、早被"忘掉"的事。这时去硬盘深处翻一遍旧账 ——
        #    mem 里没有它 = 平时想不起来；mem_archive 里有它 = 从来没销毁。
        #    只在想不起来时才翻，平时不让几年前的碎片挤掉眼前的事。
        #    触发条件不是「眼前一条都没有」—— 正常召回总能捞回几条不相干的，
        #    那样这道兜底永远不触发（试过，等于死代码）。改看内容：
        #    问句里若有实词只在冷档里出现过，那条旧账就该被翻出来。
        if True:
            try:
                _arch = self.archive_search(query or "", k=3)
            except Exception as _ae:
                _arch = []
                print("[card2] 冷档检索失败: %s" % _ae)
            if _arch:
                _al = []
                _aused = 0
                for _a in _arch:
                    _at = ""
                    try:
                        _at = time.strftime("%Y-%m-%d",
                                            time.localtime(float(_a["create_ts"])))
                    except Exception:
                        pass
                    _body = (_a["core"] or _a["raw"] or "").strip()[:72]
                    if not _body:
                        continue
                    _line = ("- %s｜%s" % (_at, _body)) if _at else ("- " + _body)
                    if used + _aused + len(_line) > cap4:
                        break
                    _al.append(_line)
                    _aused += len(_line)
                if _al:
                    _shown = " ".join(lines)
                    _al = [x for x in _al if x.split("｜")[-1][:12] not in _shown]
                if _al:
                    out.append("【很久以前的事（翻旧账翻到的）】\n" + "\n".join(_al))
                    sections["archive"] = _aused

        # ④b 事实格（V5.7）：谁说的 / 说的是谁 / 什么事
        #
        # 为什么单独一格：句子相似度能捞回「原话」，但捞不回「他妈生日三月五号」——
        # 那种原子事实在远期原话被磨掉之后仍然该在；而且它必须**带着主体**出现，
        # 否则就会犯第三方评测里最刺眼的那类错：把别人的事记到他头上。
        _fact_cap = max(60, int(q["recall"] * 0.35))
        _fact_used = 0
        try:
            _facts = ([] if os.environ.get("WHALE_FACTS", "1") == "0"
                      else facts_mod.recall(self.db_path, query or "", limit=6))
        except Exception as _fq:
            _facts = []
            print("[card2] 事实检索失败: %s" % _fq)
        if _facts:
            flines = []
            for _f in _facts:
                line = "- " + facts_mod.line_of(_f)
                if _fact_used + len(line) > _fact_cap:
                    break
                flines.append(line)
                _fact_used += len(line)
            if flines:
                out.insert(_fact_pos, "【事实】\n" + "\n".join(flines))
                sections["fact"] = _fact_used

        # ⑤ 技能格（F 维，V5.0 §7）
        #
        # 🔴 2026-10-31：这里以前是空的。ctxweight 给技能记忆留了整整一维权重
        #    （六维里的 F），但拼上下文时从来没往里装东西 —— 权重算了、
        #    预算分了、格子留了，最后什么都不放。那 F 维就是个空转的指针。
        #
        # 为什么技能要单独一格而不是混进「想起」：
        #    想起是「我记得这件事」，技能是「我会做这件事」。前者是内容，
        #    后者是熟练度 —— 会做的东西不该跟着记忆一起褪色。
        #    而且技能带步骤，混进印象格里会把那格撑爆。
        used = 0
        cap5 = used + q["recall"]
        try:
            _skv = to_vec(query or "")
            _sk_hits = (self.skill.search(_skv, top_k=3,
                                          hormones=getattr(self, "h", None))
                        if _skv else [])
        except Exception:
            _sk_hits = []
        if _sk_hits:
            slines = []
            for _s, _sim, _sr in _sk_hits:
                # 休眠线以下的技能不展示 —— 手生了就别装作还会
                if float(_sr["L"]) < skill.L_DORMANT:
                    continue
                line = "- " + _sr["name"] + "：" + _sr["desc"]
                if used + len(line) > cap5:
                    dropped += 1
                    break
                slines.append(line)
                used += len(line)
            if slines:
                blk = "【我会做的事】\n" + "\n".join(slines)
                out.append(blk)
                sections["skill"] = used

        # ⑥ 此刻的状态（正在情绪里 / 跟他的距离 / 说话倾向）
        #
        # 🔴 2026-11-05 从①挪到这里：这句话是**整段上下文里唯一每轮必变**的，
        #    放在最前面等于每轮都把缓存从头打断一次。挪到最后，前面
        #    人格 + 长期两格就变成稳定的前缀，缓存能从第一格一直命到
        #    「想起」之前 —— 只有尾巴要重算。
        #    这句话本身很短（二三十字），重算它几乎不花钱。
        # ⑤.5 身体（快反应层）：一句话就能动的那一层。
        #     放在【此刻】前面 —— 生理反应比情绪先来，顺序本身也是信息。
        try:
            vtxt = self.viscera_line() if touch else ""
        except Exception:
            vtxt = ""
        if vtxt:
            out.append("【身体】\n" + vtxt)
            sections["viscera"] = len(vtxt)

        # ⑤.6 印象：他（或别人）在什么情境下常做什么。
        #     放在【此刻】前面、【身体】后面 —— 它是背景知识，不是眼前这一刻。
        #     只描述、不预测、不当台词：这里进来的每一句都必须是"发生过 N 次的事"，
        #     而不是"他大概会怎样"。
        try:
            _ilines = self.impression_hints(limit=3)
        except Exception:
            _ilines = []
        if _ilines:
            _itxt = "\n".join("- " + x for x in _ilines)
            out.append("【印象】\n" + _itxt)
            sections["impression"] = len(_itxt)

        # ⑤.7 动机：此刻最想做的一两件事（欲望层）
        #     mot_state 里有七条驱力，candidates 会把超出基线的浮成「想做的事」。
        #     这里只取紧急度最高的那一条，用 motiv.describe 翻成人话。
        #     没有（全在基线附近）就不输出 —— 不编「我想干什么」。
        try:
            _mo = getattr(self, "mo", None)
            if _mo:
                # 口径跟 motiv_tick 保持一致：用「他最后一次说话」算空闲，
                # 不要用 last_talk_ts（那个每轮 /context 都会被刷新，
                # 于是「0.0 小时没开口」永远成立，括号里的解释成了噪音）。
                _lu = self._last_user_ts()
                _cands = motiv.candidates(_mo.get("d", motiv.base()), {
                    "now": now,
                    "last_spoke_ts": _lu,
                    "last_sent_ts": float(_mo.get("last_sent_ts") or 0),
                    "last_event_ts": _lu,
                    "quiet": False, "at_school": False,
                    "sent_today": 0, "learned_today": 0,
                })
                if _cands:
                    _top = _cands[0]
                    _mtxt = motiv.describe(_top)
                    if _mtxt:
                        out.append("【动机】\n" + _mtxt)
                        sections["motiv"] = len(_mtxt)
        except Exception:
            pass

        # ⑤.8 工作日志：我上一轮干到哪儿了（2026-11-10 加）。
        #     排在【此刻】之前：先知道手上这件活做到哪，再谈现在什么心情。
        #     这不是记忆，是我的笔记本 —— 换窗口/换电脑后第一眼看的就是这几行。
        try:
            _wl = self.worklog_text(n=5)
        except Exception:
            _wl = ""
        if _wl:
            out.append("【最近在做的事】\n" + _wl)
            sections["worklog"] = len(_wl)

        # ⑤.9 梦：攒够原文了就在上下文里提醒一句（2026-11-10 加）。
        #     压缩由**宿主模型**做（卡不调模型），所以「该压了」必须由卡主动说；
        #     否则原文越攒越多 —— 788 轮欠账就是这么来的：工具在那儿，没人喊。
        try:
            _dd = self.dream_due()
        except Exception:
            _dd = {}
        if _dd.get("due"):
            _dm = ("原文攒了 %d 轮没压（最近一轮 %.1f 小时前）。"
                   "该做梦了：mind_dream_pending 取原文 → 压成摘要 → mind_dream_commit。"
                   % (int(_dd.get("pending") or 0), float(_dd.get("idle_hours") or 0)))
            out.append("【该做梦了】\n" + _dm)
            sections["dream"] = len(_dm)

        # ⑤.95 时间与待办（2026-11-11）：先把「现在几点」摆出来，再摆没到点的事。
        # 为什么必须有：今早用户说「我到学校了」，我在同一轮里把它当成「今晚」——
        # 不是记错，是手上没有一行「现在是几点」。这行最短，但少了它，
        # 「今晚/明早/待会儿」全都没有参照物。
        try:
            _tp = self._tevent()
            _tb = _tp.block_text() if _tp is not None else ""
        except Exception as _te:
            soft_fail("tempora_block", _te)
            _tb = ""
        if _tb:
            out.append(_tb)
            sections["time"] = len(_tb)

        # ⑤.97 【没结清的事】承诺账本（2026-11-11 接进读侧）。
        #     为什么放这里：和 worklog / 做梦 / 时间同属「每轮可能变、只影响语气」的
        #     尾巴块，不打乱前面事实格的缓存前缀；承诺不是事实，不进【事实】。
        #     没有账时零开销；WHALE_PROMISE=0 整块消失（回滚）。
        if getattr(self, "_promises_ok", False):
            try:
                import promises as _pm
                _pb = _pm.promise_block(now=time.time(), limit=3, db=self.db_path)
            except Exception as _pe:
                soft_fail("promise_block", _pe)
                _pb = ""
            if _pb:
                out.append(_pb)
                sections["promise"] = len(_pb)

        state = self._state_line()
        if state:
            out.append("【此刻】\n" + state)
            sections["state"] = len(state)

        text = "\n\n".join(out)
        return {
            "text": text,
            "sections": sections,
            "dropped": dropped,
            "est_tokens": len(text),
            # 以前这里返回的是「传进来的 budget」，不传就是常量 6000；
            # 现在返回**真正用了多少**（可能来自卡里的 BUDGET）——
            # 否则控制台上显示的总预算和这一轮实际的计划永远差一截。
            "budget": plan["total"],
            "scene": plan["scene"],
            "weights": plan["weights"],
            "quota": q,
        }

    # ---------- 统计 ----------


    def get_dialogue(self, mem_id):
        """翻回某条记忆当时的原话（user + assistant）"""
        rows = self.db.execute(
            "SELECT role,text,ts FROM dialogue WHERE mem_id=? ORDER BY ts,id",
            (int(mem_id),)).fetchall()
        return [{"role": r["role"], "text": r["text"], "ts": r["ts"]} for r in rows]

    def close(self):
        """关库（WAL 落盘）"""
        try:
            self.db.commit()
        except Exception:
            pass
        try:
            self.db.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def stats(self):
        now = self.clock.now()
        c = self.db.cursor()
        by_pool = {}
        for p in (POOL_SHORT, POOL_SHORT_WARM, POOL_MEDIUM,
                  POOL_MEDIUM_WARM, POOL_LONG, POOL_ARCHIVE, POOL_DEEP):
            rows = c.execute(
                "SELECT pool,w,t_anchor,region,tier FROM mem WHERE pool=?",
                (p,)).fetchall()
            ws = [wn_row(r, now) for r in rows]
            by_pool[p] = {"n": len(rows),
                          "avg_w": round(sum(ws) / len(ws), 3) if ws else 0.0}
        n = c.execute("SELECT COUNT(*) FROM mem").fetchone()[0]
        d = c.execute("SELECT COUNT(*) FROM detail").fetchone()[0]
        tr = c.execute("SELECT COUNT(*), COALESCE(SUM(count),0)"
                       " FROM trace").fetchone()
        return {"total": n, "details": d, "pools": by_pool,
                "trace": {"rows": tr[0], "memories": tr[1]},
                "oxy": round(getattr(self, "oxy", 0.0), 3),
                "mood": round(getattr(self, "mood", MOOD_NEUTRAL), 3),
                "mood_label": mood_text(getattr(self, "mood", MOOD_NEUTRAL)),
                "hormones": hormone.describe(getattr(self, "h", {}))}

# ---------------------------------------------------------------- 搬出去的写路径
# 6-1 步把 remember / add_dialogue / worklog / emb_* 搬到了 cardmix_store.py，
# 但方法体里还在用这里的模块级常量和工具函数（POOL_SHORT、to_vec、DAY…）。
# 函数体里的名字是 LOAD_GLOBAL，查的是 **cardmix_store 自己的 globals**，
# 不会走它的模块 __getattr__ —— 所以在这里把本模块的名字补进去一次。
# setdefault：不覆盖 cardmix_store 自己的 json/sqlite3/MixHabit 之类。
def _free_globals(path):
    """模块里以「全局名」方式被读到的名字 —— 也就是运行期必须能解析的那些。

    为什么要在绑定之后核对一遍：搬出去的方法体是 LOAD_GLOBAL，查的是 cardmix_store
    自己的 globals。少补一个名字，import 阶段一声不响，等第一次写记忆才炸 NameError ——
    那时候磁盘上已经落了一半，很难查。所以宁可**在启动/导入时就失败**，把「什么时候炸」
    从「某次写入」提前到「启动」。这是 fail-fast，不是额外保险。
    """
    import ast as _ast
    import builtins as _bi
    src = open(path, encoding="utf-8").read()
    tree = _ast.parse(src)
    have = set(dir(_bi)) | {"__name__", "__file__", "__doc__"}
    for n in _ast.walk(tree):
        if isinstance(n, _ast.Name) and isinstance(n.ctx, _ast.Store):
            have.add(n.id)
        elif isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
            have.add(n.name)
            if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                a = n.args
                have |= {x.arg for x in list(a.args) + list(a.posonlyargs) + list(a.kwonlyargs)}
                if a.vararg:
                    have.add(a.vararg.arg)
                if a.kwarg:
                    have.add(a.kwarg.arg)
        elif isinstance(n, (_ast.Import, _ast.ImportFrom)):
            for al in n.names:
                have.add(al.asname or al.name.split(".")[0])
        elif isinstance(n, _ast.Global):
            have |= set(n.names)
        elif isinstance(n, _ast.ExceptHandler) and n.name:
            have.add(n.name)
        elif isinstance(n, (_ast.Lambda, _ast.comprehension)):
            for t in _ast.walk(n):
                if isinstance(t, _ast.arg):
                    have.add(t.arg)
                elif isinstance(t, _ast.Name) and isinstance(t.ctx, _ast.Store):
                    have.add(t.id)
    out = set()
    for n in _ast.walk(tree):
        if isinstance(n, _ast.Name) and isinstance(n.ctx, _ast.Load) and n.id not in have:
            out.add(n.id)
    return out


def _check_mixin_globals():
    """核对拆出去的 mixin 自己能把依赖都拿到 —— **只核对，不再注入**。

    历史：最早靠 `_bind_store_globals()` 把本模块的名字塞给 cardmix_store，
    那是「父模块往子模块里填名字」的反向依赖，子模块离了 card2 就跑不起来。
    2026-11-11 共享常量收进 cardcfg.py、改子模块自己 import 之后，注入不需要了 ——
    这里退化成一道闸：自己拿不到就启动失败，不靠父模块悄悄补。
    """
    import os as _os
    _here = _os.path.dirname(_os.path.abspath(__file__))
    missing = {}
    for _name in ("cardmix_store", "cardmix_habit"):
        try:
            _m = __import__(_name)
        except Exception as _e:  # noqa: BLE001
            raise ImportError("%s 导入失败：%s" % (_name, _e))
        _miss = sorted(n for n in _free_globals(_os.path.join(_here, _name + ".py"))
                       if not hasattr(_m, n))
        if _miss:
            missing[_name] = _miss
    if missing:
        raise ImportError(
            "拆出去的方法有名字没自己 import，启动即失败（别再靠父模块注入）：%s"
            % "; ".join("%s → %s" % (k, ", ".join(v)) for k, v in missing.items()))


_check_mixin_globals()