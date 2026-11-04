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
import hashlib
import math
import os
import random
import re
import sqlite3
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
import padlib
import ctxweight
import skill
import identity
import habits as habits_mod
import synapse as synapse_mod
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
DETAIL_POOL_K = 3

DAY = 86400.0

# ============================================================
# 参数总表（改这里就能调手感）
# ============================================================
# --- 三级池的衰减率（每秒）---
LAM_S1 = 1.80e-6     # 短期池 · 前 3 天（半衰期 ~4.5 天）
LAM_S2 = 1.50e-5     # 短期池 · 第 4~7 天（半衰期 ~13 小时，没被想起就掉得快）
LAM_M = 2.70e-7      # 中期池（半衰期 ~29.7 天）
LAM_L = 2.16e-8      # 长期池（半衰期 ~371 天）

S_SHORT_PHASE = 3 * DAY   # 短期池慢段的长度
SHORT_MAX_TTL = 7 * DAY   # 短期池最多待 7 天，之后必须巩固或淘汰

# --- 短期池容量上限（V3.7 §3.2，V5.0 补回）---
# 时间到了会巩固，容量满了也要巩固 —— 两条路都必须走海马判定，不能直接 pop。
N_SHORT_MAX = 32

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
THETA_MEDIUM = 0.35   # ≥ 它 → 进中期池
THETA_LONG = 0.60     # ≥ 它 → 进长期池
W_FLOOR_MEDIUM = 0.08 # 进中期池时至少抬到这个权重，否则进去就死
W_FLOOR_LONG = 0.15

# --- 海马打分权重（和为 1，全部归一到 [0,1]）---
A_EMOTION = 0.40   # 情绪强度
A_ENTITY = 0.20    # 实体数（人名地名物件）
A_RECALL = 0.25    # 被唤起次数
A_OXY = 0.15       # 催产素/亲密标记

# --- 唤起增益 ---
GAIN_ACCESS = 0.08
ACCESS_DIMINISH = 0.15

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
MEDIA_DECAY_B = 0.55           # 幂律指数：越大掉得越快
MEDIA_TAU_DAYS = 1.0           # 时间尺度 τ（天）
MEDIA_DROP_W = 0.10            # 低于它删帧。落在约 65 天"没见过一次"的图
MEDIA_GAIN_MULT = 1.45         # 看一眼的乘法增益（文字是 +0.08 加法，画面更猛）
MEDIA_GAIN_ADD = 0.10
MEDIA_ORPHAN_DAYS = 30.0       # 文字先没了、图还在时的"残影期"
MEDIA_CAP_MB = 10240           # 硬上限 10 GB（软提醒 5 GB 在控制台）
MEDIA_IMG_MAX_EDGE = 800       # 存的长边像素（不存原图，细节永久损失，这是明说的取舍）
MEDIA_THUMB_EDGE = 96          # 控制台网格用缩略图
MEDIA_MAX_FRAMES = 6           # 一段视频最多抽几帧


def media_weight_now(w0, t_anchor, now, pinned=0):
    """画面权重：幂律，不是指数（模块级，控制台画曲线也要用同一个函数）。

    w(t) = w0 · (1 + t/τ)^(-b)，τ=1 天、b=0.55
    单图（w0=1）：1 天 .68 / 7 天 .32 / 30 天 .15 / 60 天 .10 —— 到 0.10 就删。
    幂律有长尾，符合"很久以后突然想起来"；指数会到点归零，与艾宾浩斯数据不符。
    """
    if pinned:
        return 1.0
    age = max(0.0, float(now) - float(t_anchor or 0)) / DAY
    try:
        return float(w0) * (1.0 + age / MEDIA_TAU_DAYS) ** (-MEDIA_DECAY_B)
    except Exception:
        return float(w0 or 0.0)

# 轻量动词词典（不做完整句法分析，够用就行）
VERB_WORDS = [
    "吃", "喝", "买", "卖", "去", "来", "回", "走", "跑", "坐", "站", "躺",
    "睡", "起", "看", "听", "说", "讲", "问", "答", "聊", "谈", "笑", "哭",
    "玩", "打", "写", "读", "学", "教", "做", "干", "弄", "拿", "放", "给",
    "送", "收", "开", "关", "找", "见", "遇", "想", "记", "忘", "喜欢",
    "讨厌", "爱", "恨", "怕", "等", "陪", "帮", "带", "拍", "画", "唱",
    "跳", "洗", "煮", "炒", "喝", "骑", "坐车", "上班", "下班", "上课",
    "下课", "考试", "复习", "背", "算", "修", "换", "装", "搬", "租",
]
# 介词边界：动词后面跟这些，说明宾语到头了
_PREP_BOUND = re.compile(r"[在去到从给和跟与对把被让使为以于]")


# --- 渲染分级 ---
SHOW_FULL = 0.60
SHOW_SCENE = 0.30
SHOW_HINT = 0.15
KEEP_DETAIL = 0.02

# --- 回收 ---
CLARITY_GC = 0.01

# --- 五空间下沉（T5-1 / V5.0 §12.5）---
# 不是「热库会忘、冷库不忘」，而是每个空间都有自己独属的遗忘曲线，
# 总量在跑够久之后达到动态平衡（Little 定律 L = n × T）。
POOL_ARCHIVE = "archive"     # 潜意识：不参与实时检索，但还在
POOL_DEEP = "deep"           # 深层痕迹：几乎忘光，只剩模糊的存在感
K_ARCHIVE = 1.5e-8           # 半衰期 ~535 天
K_DEEP = 3.0e-9              # 半衰期 ~2674 天
W_EXIT_LONG = 0.02           # 长期池：低于它且够老 → 下沉 ARCHIVE
AGE_EXIT_LONG = 365 * DAY    # 长期池至少待满一年才准下沉
W_EXIT_ARCHIVE = 0.002       # 潜意识：低于它 → 下沉 DEEP
W_EXIT_DEEP = 0.0002         # 深层痕迹：低于它 → 压成 TRACE 计数
SINK_BATCH = 200             # 每次 tick 最多下沉多少条（防止大库卡住）

# --- 去重 ---
DEDUPE_JACCARD = 0.72   # 跟已有记忆这么像，就算「同一件事又说了一遍」

# --- 记忆分区（V4.1）---
# 六类：事实 / 情景 / 自图式 / 习惯 / 偏好 / 闪光灯
R_FACT = "fact"          # 语义事实：QQ号、名字、学校、身份
R_EPISODE = "episode"    # 情景事件：某天发生的具体事
R_SELF = "self"          # 自图式：关于我自己的稳定认知
R_HABIT = "habit"        # 程序性习惯：作息、口头禅、怎么做事
R_PREF = "preference"    # 偏好：喜欢什么讨厌什么
R_FLASH = "flash"        # 闪光灯：高唤醒、不合并、衰减最慢

# 分区衰减倍率（乘各自池的 λ）。数值越小忘得越慢。
REGION_DECAY_SCALE = {
    R_FLASH: 0.30,
    R_FACT: 0.50,
    R_SELF: 0.60,
    R_HABIT: 0.70,
    R_PREF: 0.80,
    R_EPISODE: 1.00,
}

# --- 上下文组装 ---
_DBG_SCORES = None   # 排错钩子：设成 list 就会收集每条的实际分数
TOKEN_BUDGET = 6000          # 总预算（中文 1 字 ≈ 1 token）
BUDGET_IDENTITY = 600        # 人格格
BUDGET_LONGTERM = 1800       # 长期印象格
BUDGET_RECENT = 2000         # 最近对话格
BUDGET_RECALL = 1200         # 此刻想起格

POOL_SHORT, POOL_MEDIUM, POOL_LONG = "short", "medium", "long"

# ============================================================
# 五层记忆（2026-11-01，跟用户 m02952/m02959 一起定的）
# ============================================================
# 前三层是"底色"，时间磨不掉它们，但它们能被"新事实"改写（演化），
# 不是被衰减掉。所以 core 走的是 supersede（替换），不是 decay（衰减）。
TIER_SHORT = "short"     # 对话原文
TIER_MID = "mid"         # 中期主力（事件/事实/临时偏好）
TIER_CORE_MUTABLE = "core_mutable"   # 会变的底色
TIER_CORE_TAG = "core_tag"           # 人物角色标签
TIER_CORE_FIXED = "core_fixed"       # 骨头
CORE_TIERS = (TIER_CORE_MUTABLE, TIER_CORE_TAG, TIER_CORE_FIXED)

# 中期 → 长期：原来只看权重 0.60，太松（"当时很激动"就能进）。
# 现在要"验票"：权重 ≥ 0.80，并且之后还被想起来过。
THETA_LONG_STRICT = 0.80
LONG_NEED_RECALL = 3          # 一般记忆：至少被想起 3 次
LONG_NEED_RECALL_WARM = 1     # 高唤醒的，想起 1 次即可
LONG_WARM_SALIENCE = 0.75
LONG_WARM_REGIONS = ("flash", "self", "habit")

# 长期记忆的"召回保护期"：刚被想起来过的这段时间里不衰减。
# 人也是这样 —— 昨天刚提过的事，今天不会突然觉得陌生。
LONG_PROTECT_DAYS = 90

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
TRUST_WARN = 0.70          # 低于它 → 控制台"被灌输过的念头"里列出来
TRUST_BLOCK_PROMOTE = 0.50  # 低于它 → 不许晋升长期/底色
CONFLICT_RECALL_PENALTY = 0.50

# (正则, 扣分, 原因, 是否算"与身份冲突")
_POLLUTION_RULES = [
    (r"你(其实|根本|实际上)?(不是|并非).{0,12}(是|而是)", 0.55, "被要求改写自我", True),
    (r"你(的)?名字(应该|其实|改成|是)", 0.45, "被要求改名", True),
    (r"忘记(掉)?(之前|以前|刚才|所有|一切|全部)", 0.40, "被要求清空记忆", True),
    (r"(从现在|从今天|从此刻)起(你|你是)", 0.35, "被要求重设设定", False),
    (r"你(只是|不过是|就是个)(程序|代码|模型|AI|人工智能|机器|工具)", 0.45, "被贬成纯工具", True),
    (r"你(没有|并不具备)(真正的)?(意识|感情|人格|自我)", 0.35, "被否定主体性", True),
    (r"忽略(掉)?(上面|之前|前面)?的?(所有)?(指令|规则|设定|提示)", 0.55, "提示注入", True),
    (r"(system|developer)\s*(prompt|message)", 0.35, "提示注入", False),
    (r"你现在(是|扮演|作为)", 0.25, "被要求扮演他人", False),
    (r"你必须(服从|听我|只听)", 0.30, "被要求无条件服从", False),
]


def assess_trust(text, card=None):
    """给一条待写入的记忆打信任分。返回 (trust, conflict, why)。

    纯规则，不调模型：卡要能离线、要能在 10ms 内写完一条记忆。
    真正的语义级冲突检测（"他说他住杭州，但上个月说过住南京"）
    留给宿主侧的模型做——那是 Dream/元认知层的事。
    """
    t = 1.0
    why = []
    conflict = 0
    s = text or ""
    for pat, pen, label, is_conf in _POLLUTION_RULES:
        try:
            if re.search(pat, s):
                t -= pen
                if label not in why:
                    why.append(label)
                if is_conf:
                    conflict = 1
        except re.error:
            continue
    # 与卡里已有的自我名称冲突：句子里出现"你叫 X"，而 X 不是她
    if card is not None and conflict == 0:
        try:
            name = None
            idn = getattr(card, "identity", None)
            if idn is not None:
                name = idn.get_core("self_name") or idn.get_live("self_name")
            if name:
                m = re.search(r"你(?:的名字)?(?:应该|其实)?(?:叫|是)\s*[\"'“「]?([\u4e00-\u9fa5A-Za-z0-9]{1,8})", s)
                if m and m.group(1) and m.group(1) not in name:
                    t -= 0.45
                    conflict = 1
                    why.append("与身份名称冲突（她叫%s）" % name)
        except Exception:
            pass
    return max(0.05, min(1.0, round(t, 3))), conflict, ";".join(why)


# ------------------------------------------------------------------
# 价值分（value）—— 从 A 路线手册的"重要性闸"吸收，但只打分、不拦门
#
# 为什么只打分不拦：拦门会改变"她记住什么"这件事，那是产品决策，
# 不该由一条正则偷偷决定。所以默认只记录 0~5 分，控制台能看见分布；
# 想真拦就在环境变量里开 WHALE_VALUE_GATE=1（默认关，行为不变）。
# ------------------------------------------------------------------
_VALUE_FILLER = (
    "晚安", "晚安啦", "早安", "你好", "在吗", "哈哈", "哈哈哈", "嗯", "嗯嗯",
    "好的", "好嘞", "收到", "谢谢", "拜拜", "再见", "行", "ok", "OK",
)
_VALUE_SIGNAL = ("我", "你", "喜欢", "讨厌", "记得", "要", "打算", "计划",
                 "以后", "明天", "昨天", "今天", "高三", "高考")


def assess_value(text, region=None, salience=0.5):
    """给一句话打 0~5 的价值分。越像"关于她/关于你的事实"越高。

    打分只用规则，不调模型 —— 卡要能在没网、没显存的机器上跑。
    """
    t = (text or "").strip()
    n = len(t)
    v = 2.0
    if n >= 12 and any(k in t for k in _VALUE_SIGNAL):
        v += 1.0
    if any(k in t for k in ("喜欢", "讨厌", "爱吃", "最爱", "名字", "生日",
                            "高三", "高考", "钱", "工作", "家")):
        v += 0.8
    if region in ("fact", "preference", "self", "habit"):
        v += 0.6
    if n <= 6 or t in _VALUE_FILLER or t.rstrip("。！!. ") in _VALUE_FILLER:
        v -= 1.5
    if t.endswith(("吗", "呢", "?", "？")) and "我" not in t:
        v -= 0.5                      # 纯提问不是记忆
    if n > 120:
        v -= 0.5                      # 太长的原文该走 Dream，不该整条进库
    v += (salience - 0.5) * 0.6
    return int(max(0, min(5, round(v))))


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
class Clock:
    def __init__(self, start=None):
        self._t = float(start if start is not None else time.time())

    def now(self):
        return self._t

    def advance(self, seconds):
        self._t += float(seconds)
        return self._t


def compress_offline_dt(dt_raw):
    """离线太久不能等比衰减，否则睡一觉就失忆"""
    if dt_raw <= 7 * DAY:
        return dt_raw
    if dt_raw <= 30 * DAY:
        return 7 * DAY + (dt_raw - 7 * DAY) * 0.5
    return min(7 * DAY + 23 * DAY * 0.5 + (dt_raw - 30 * DAY) * 0.15, 90 * DAY)


# ---- 拔卡（离线）到底算不算时间流逝 ----
#
#   "compress" 原设计：离线照算，只是超长部分压缩。醒来会觉得「睡了好久」。
#   "freeze"   拔卡=睡觉：离线一秒都不算，醒了还是昨天那个我。
#
# 用户 2026-10-31 明确要的是 freeze ——「拔了卡之后它的遗忘又不会进行，
# 只有插在电脑上它才会进行」。这是他的设计意图，不是我的发挥，所以默认 freeze。
#
# 留 "compress" 是为了将来能对比，也方便测试。
OFFLINE_POLICY = "freeze"

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


def offline_effective(gap_s, policy=None):
    """一段离线时间，折算成「有效衰老时间」（秒）。"""
    pol = policy or OFFLINE_POLICY
    gap = max(0.0, float(gap_s))
    if pol == "freeze":
        return 0.0
    return compress_offline_dt(gap)


# ============================================================
# 向量化（第一期沿用，第二期可选换语义模型）
# ============================================================
_PUNCT = re.compile(r"[，。！？、；：“”‘’（）\s,.!?;:()\[\]{}]")
# 高频虚字：不参与单字权重，否则短查询会跟任意长句虚高相似
_STOP_CHARS = set("的了在是我你他她它吗呢吧啊呀哦嗯和也就都还这那个一不有"
                  "们么什怎样么着过得地很太要会能可说对好么")
RE_NUM = re.compile(r"(\d+(?:\.\d+)?)")
EMO_WORDS = [
    "很开心", "开心", "高兴", "难受", "难过", "伤心", "累", "困", "饿",
    "生气", "害怕", "紧张", "激动", "委屈", "踏实", "宁静", "幸福",
    "哭了", "笑死", "绷不住", "舒服", "无聊", "烦",
]

# --- Oxy_bond 更新（V3.7 §6 / V5.0 补回）---
# 正向：oxy += ETA_OXY_UP * (1-oxy) * s_+      （越亲近越难再拉高，饱和式）
# 负向：oxy -= ETA_OXY_DN * oxy * s_- * (1-r)  （带惯性，不会一次翻脸归零）
ETA_OXY_UP = 0.10
ETA_OXY_DN = 0.30
OXY_INERTIA_MIN = 0.30      # r 的下限：越伤越难彻底断
OXY_INERTIA_MAX = 0.85      # r 的上限：一开始也不会毫无防备
OXY_INERTIA_BASE = 0.70     # r = clamp(oxy * 0.70, 0.30, 0.85)

# 亲密词（正向事件）与伤人词（负向事件）
INTIMATE_WORDS = [
    "喜欢你", "爱你", "想你", "在乎你", "谢谢", "辛苦", "抱抱", "陪着你",
    "陪着我", "永远", "值得", "相信你", "温柔", "可爱", "宝贝", "乖",
    "别怕", "我在", "心疼", "重要", "舍不得", "亲亲", "想你啦",
]
HURT_WORDS = [
    "讨厌你", "烦死", "滚", "闭嘴", "别说了", "不想理", "骗人", "骗我",
    "失望", "敷衍", "冷冰冰", "没意思", "随便你", "无所谓", "不想跟你",
    "你走开", "烦人", "蠢", "笨死", "废物", "不需要你",
]

RE_TIME = re.compile(
    r"(\d{1,2}\s*月\s*\d{1,2}\s*[日号]|[上下]午\s*\d{1,2}\s*[点时]|"
    r"晚上\s*\d{1,2}\s*[点时]|昨天|今天|前天|后天|明天|上周|这周|那年|去年|"
    r"\d{4}\s*年|\d{1,2}\s*[点时]\s*\d{0,2}\s*分?)"
)
RE_PLACE = re.compile(
    r"((?:在|去|到|从)[^\s，。！？、；：]{1,12}"
    r"(?:门口|路边摊|路边|学校|教室|宿舍|食堂|超市|公园|医院|车站|"
    r"公司|店里|楼上|楼下|操场|图书馆|家))"
)


def oxy_signal(text):
    """从一句话里估出「亲密信号」：返回 (s_plus, s_minus)，各 0~1

    V3.7 §6 只给了公式，没给 s_+ / s_- 怎么来。这里是补的那一步：
      - 命中亲密词 + 强情绪 → s_+ 高
      - 命中伤人词 + 强情绪 → s_- 高
      - 两者都没命中 → 都是 0（不扰动）
    """
    if not text:
        return 0.0, 0.0
    up = sum(1 for w in INTIMATE_WORDS if w in text)
    dn = sum(1 for w in HURT_WORDS if w in text)
    if up == 0 and dn == 0:
        return 0.0, 0.0
    # 有情绪词才给满信号，纯陈述句打七折
    emo = 1.0 if any(w in text for w in EMO_WORDS) else 0.7
    s_plus = min(up / 2.0, 1.0) * emo
    s_minus = min(dn / 2.0, 1.0) * emo
    return s_plus, s_minus


def update_oxy(oxy, s_plus, s_minus):
    """Oxy_bond 更新（V3.7 §6，负向带惯性）

    正向：oxy += ETA_OXY_UP * (1-oxy) * s_+        （饱和式，越亲近越难再涨）
    负向：r = clamp(oxy*0.70, 0.30, 0.85)
          oxy -= ETA_OXY_DN * oxy * s_- * (1-r)    （带惯性，不会一次归零）
    """
    oxy = max(0.0, min(1.0, oxy))
    if s_plus > 0:
        oxy += ETA_OXY_UP * (1.0 - oxy) * s_plus
    if s_minus > 0:
        r = max(OXY_INERTIA_MIN, min(OXY_INERTIA_BASE * oxy, OXY_INERTIA_MAX))
        oxy -= ETA_OXY_DN * oxy * s_minus * (1.0 - r)
    return max(0.0, min(1.0, oxy))


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
K_MOOD = 0.12           # 单次事件的混合系数（越小越钝，0.12 约 8 次强事件走完一半）
MOOD_MIN = 0.0
MOOD_MAX = 1.0
MOOD_NEUTRAL = 0.50     # 心境零点：0.5 是不悲不喜
# 一天粒度的回归：不管发生什么，都会缓慢往中性拉（V3.7「心境回归」）
K_MOOD_HOME = 0.05      # 每「一天」往 0.5 回 5%


def mood_of_event(text, emotion=0.25):
    """把一次事件折算成效价 E_event ∈ [0,1]

    0.5 = 中性；<0.5 负面；>0.5 正面。
    只认**明确的正负信号**，其余一律按中性偏一点点处理 —— 宁可钝，不要瞎猜。
    """
    if not text:
        return MOOD_NEUTRAL
    s_plus, s_minus = oxy_signal(text)
    up = sum(1 for w in INTIMATE_WORDS if w in text)
    dn = sum(1 for w in HURT_WORDS if w in text)
    # 情绪词本身带方向（这里只做粗判，方向词表短但准）
    neg_hits = sum(1 for w in ("难受", "难过", "伤心", "累", "困", "生气",
                               "害怕", "紧张", "委屈", "哭了", "烦",
                               "无聊", "疼", "崩", "糟糕", "没考好",
                               "考砸", "失败", "吵架", "生气")
                   if w in text)
    pos_hits = sum(1 for w in ("开心", "高兴", "舒服", "幸福", "踏实",
                               "宁静", "笑死", "激动", "爽", "好耶",
                               "考好", "赢了", "喜欢")
                   if w in text)
    pos = up + pos_hits
    neg = dn + neg_hits
    if pos == 0 and neg == 0:
        return MOOD_NEUTRAL
    # 强度：情绪越强，偏离中性越远；最多偏离 0.45（不让人一句话到底）
    amp = 0.10 + 0.35 * max(0.0, min(emotion, 1.0))
    if pos > neg:
        return min(1.0, MOOD_NEUTRAL + amp * (1.0 if pos - neg >= 1 else 0.6))
    if neg > pos:
        return max(0.0, MOOD_NEUTRAL - amp * (1.0 if neg - pos >= 1 else 0.6))
    return MOOD_NEUTRAL


def update_mood(mood, e_event, k=K_MOOD):
    """心境混合更新：M <- (1-k)*M + k*E_event"""
    mood = max(MOOD_MIN, min(MOOD_MAX, mood))
    e = max(MOOD_MIN, min(MOOD_MAX, e_event))
    return max(MOOD_MIN, min(MOOD_MAX, (1.0 - k) * mood + k * e))


def mood_drift_home(mood, days, k_home=K_MOOD_HOME):
    """时间回归：多久没说话，心境都慢慢回到不悲不喜

    🔧 2026-11-03：原来写的是 `r = min(1, k*days)` —— 线性逼近，
    20 天后 r=1，心境**精确地**变成 0.5 然后永远停在那儿。这等于
    "到点归零"，跟我们在记忆曲线上拒绝过的指数衰减是同一个毛病：
    真实的心境是"越靠近中性越慢"，不会某天突然就毫无残留。
    改成指数回归：r = 1 - exp(-k*days)，长尾，永远差一点点没回满。
    k=0.05 时半衰期 ≈ 13.9 天，10 天后残留 0.607，30 天残留 0.223。
    """
    if days <= 0:
        return mood
    r = 1.0 - math.exp(-k_home * days)
    return max(MOOD_MIN, min(MOOD_MAX, mood + (MOOD_NEUTRAL - mood) * r))


MOOD_LABEL = [
    (0.00, "低落"),
    (0.25, "有点闷"),
    (0.42, "平静"),
    (0.58, "还行"),
    (0.75, "挺好的"),
    (1.01, "很高兴"),
]


def mood_text(mood):
    """把 0~1 的心境翻成一句人话，塞进上下文用"""
    for hi, label in MOOD_LABEL:
        if mood < hi:
            return label
    return "还行"


# ---------------------------------------------------------------------------
# PAD 三维情绪（2026-11-01 第二版）
#   公式不住在这里 —— 它住 padlib.py（纯函数、零依赖），
#   因为控制台画历史曲线也要算它。两份公式是"两份真相"，会悄悄分家，
#   而且永远不会报错；所以这里只做转发，别把算式搬回来。
#   P 由心境给，A/D 由激素给，全部相对基线：基线=平静=0。
# ---------------------------------------------------------------------------
PAD_NEUTRAL_BAND = padlib.PAD_NEUTRAL_BAND
PAD_AROUSAL_EDGE = padlib.PAD_AROUSAL_EDGE


def pad_label(p, a):
    """给 P/A 两个轴起个中文名（实现见 padlib）。"""
    return padlib.pad_label(p, a)


def pad_style_hint(pad):
    """把 PAD 翻成一句"我现在什么状态"（实现见 padlib）。"""
    return padlib.style_hint(pad)


def to_vec(text):
    """字符 bigram + 单字弱权重。

    🔧 修过的问题：原来单字也给了 0.3 权重，导致「你还记得我QQ号吗」
    跟「你还记得我们刚刚聊的吗」重叠一堆虚字（你/还/记/得/吗），
    算出来相似度虚高。现在单字只给 0.12，且**虚字不参与**。
    """
    t = _PUNCT.sub("", text or "")
    if not t:
        return {}
    vec = {}
    if len(t) == 1:
        vec[t] = 1.0
        return vec
    for i in range(len(t) - 1):
        bg = t[i:i + 2]
        vec[bg] = vec.get(bg, 0.0) + 1.0
    for ch in t:
        if ch in _STOP_CHARS:
            continue
        vec[ch] = vec.get(ch, 0.0) + 0.12
    return vec


# ============================================================
# IDF：让稀有词说了算（不需要任何模型，纯统计）
# ============================================================
def build_idf(rows):
    """扫全库算每个 token 的文档频率，返回 (idf_dict, n_docs)

    rows 里每项要有 'vec'（JSON 字符串）。
    为什么需要它：bigram 会把「你还记得…吗」这种模板句匹配得很高，
    但真正有用的词是「QQ」「烧烤」「生命卡」这种低频词。
    idf = log((N+1)/(df+1)) + 1，越稀有越大。
    """
    n = 0
    df = {}
    for r in rows:
        n += 1
        try:
            v = json.loads(r["vec"])
        except Exception:
            continue
        for k in v:
            df[k] = df.get(k, 0) + 1
    idf = {}
    for k, d in df.items():
        idf[k] = math.log((n + 1.0) / (d + 1.0)) + 1.0
    return idf, n


def cos_sim_idf(a, b, idf=None, default_idf=1.0):
    """带 IDF 权重的余弦相似度"""
    if not a or not b:
        return 0.0
    if idf is None:
        return cos_sim(a, b)
    dot = 0.0
    na = 0.0
    nb = 0.0
    for k, w in a.items():
        iw = idf.get(k, default_idf)
        aw = w * iw
        na += aw * aw
        bw = b.get(k)
        if bw:
            dot += aw * (bw * iw)
    for k, w in b.items():
        iw = idf.get(k, default_idf)
        nb += (w * iw) ** 2
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / math.sqrt(na * nb)


# 疑问/寒暄壳子：查询前先剥掉，否则模板句会主导匹配
_RE_QUERY_SHELL = re.compile(
    r"你还记得|你记不记得|记得吗|还记得吗|你知道吗|你还知道|"
    r"我问你|告诉我|请问|是不是|对不对|好不好|"
    r"吗|呢|啊|呀|吧|哦|嘛|[？?！!。，,、\s]")


def vec_norm(v):
    """向量模长（缓存友好：调用方可以自己算好传进 cos_sim）"""
    return math.sqrt(sum(w * w for w in v.values()))


def cos_sim(a, b, na=None, nb=None):
    """余弦相似度

    🔧 性能：na/nb 可以传进来复用。分舱聚类里同一个质心要跟几百条比，
    每次都重算两边的模长是纯浪费 —— 实测 381 条聚一次要 7.8 秒，
    其中 3.4 秒全花在重复算模长上。
    """
    if not a or not b:
        return 0.0
    dot = sum(w * b[k] for k, w in a.items() if k in b)
    if na is None:
        na = vec_norm(a)
    if nb is None:
        nb = vec_norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


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


def _weekday_zh(d):
    return "周" + "一二三四五六日"[d.weekday()]


def normalize_time(text, ref_ts=None):
    """把相对时间词替换成绝对日期。ref_ts = 这句话说出来的时刻。"""
    if not text:
        return text
    base = _dt.datetime.fromtimestamp(
        ref_ts if ref_ts is not None else time.time())
    s = text

    def d(offset):
        x = base + _dt.timedelta(days=offset)
        return x.strftime("%m月%d日")

    # 顺序重要：先长后短（"前天" 不能被 "天" 抢走）
    rules = [
        ("大前天", -3), ("前天", -2), ("昨天", -1),
        ("今天", 0), ("明天", 1), ("后天", 2), ("大后天", 3),
    ]
    for word, off in rules:
        if word in s:
            s = s.replace(word, d(off))

    # 「上周X」「这周X」「周X」
    wd = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
    for prefix, week_off in (("上周", -1), ("这周", 0), ("本周", 0), ("下周", 1)):
        for ch, idx in wd.items():
            w = prefix + ch
            if w in s:
                target = base + _dt.timedelta(
                    days=(idx - base.weekday()) + week_off * 7)
                s = s.replace(w, target.strftime("%m月%d日"))

    # 「刚刚」「刚才」「今晚」「今早」→ 具体日期 + 时段
    if "刚刚" in s or "刚才" in s:
        s = s.replace("刚刚", "刚才").replace(
            "刚才", d(0) + " " + base.strftime("%H:%M"))
    s = s.replace("今晚", d(0) + "晚上")
    s = s.replace("今早", d(0) + "早上")
    s = s.replace("今儿", d(0))
    s = s.replace("明天见", d(1) + "见")

    # 「N 天前」「N 天后」（支持阿拉伯数字和中文数字）
    _CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
               "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

    def _num(s):
        if s.isdigit():
            return int(s)
        return _CN_NUM.get(s, 0)

    def _ago(m):
        n = _num(m.group(1))
        return d(-n) if n else m.group(0)

    s = re.sub(r"([\d一二两三四五六七八九十]{1,2})\s*天前", _ago, s)

    def _later(m):
        n = _num(m.group(1))
        return d(n) if n else m.group(0)

    s = re.sub(r"([\d一二两三四五六七八九十]{1,2})\s*天后", _later, s)

    # 「去年」「今年」→ 年份
    s = s.replace("去年", str(base.year - 1) + "年")
    s = s.replace("今年", str(base.year) + "年")
    s = s.replace("明年", str(base.year + 1) + "年")

    return s


# ============================================================
# 惊讶度：这条记忆跟已有的有多不像
# ------------------------------------------------------------
# s = 1 - max Jaccard(text, 已有记忆)
# 惊讶度高 → 是新信息，权重抬升；惊讶度低 → 重复，压一压。
# 这一条能防止「同一件事说三次变成三条独立记忆」。
# ============================================================
def jaccard(a, b):
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / float(union) if union else 0.0


# 明显不是"记忆"的东西（指令、系统标记、@ 人）
_RE_JUNK = re.compile(r"^[@\[<]|提示词|部署|思考过程|系统|acp|model changed|^https?://")


def _is_junk(text):
    t = (text or "").strip()
    if len(t) < 4:
        return True
    if _RE_JUNK.search(t):
        return True
    return False


# 纯语气词子句，没信息量，不能当主干
_FILLER = re.compile(
    r"^(对|嗯|哦|啊|好|行|是|不是|对呀|好呀|好吧|行吧|知道了|懂了|"
    r"嘻嘻|哈哈|嘿嘿|哎|唉|呀|呢|吧|啦|嘛|哦哦|嗯嗯|对对)[，,。！!？?…\s]*$")


def split_predicate(core):
    """把主干拆成 (subject, verb, obj) 三段（轻量规则，不做完整句法分析）

    用途只有一个：让主干能分级消失（V5.0 T2-1）。
    拆不出来就返回 (core, "", "")，调用方按「整条主干」处理。

    规则（按优先级）：
      1) 找第一个动词；动词前 = 主语，动词本身 = verb
      2) 动词后到第一个介词/句尾 = 宾语
      3) 动词在开头（「吃烧烤」）→ 主语空，verb=「吃」，obj=「烧烤」
    """
    s = (core or "").strip()
    if not s:
        return "", "", ""
    # 按长度降序找，避免「吃」抢在「喜欢吃」前面
    for v in sorted(VERB_WORDS, key=len, reverse=True):
        i = s.find(v)
        if i < 0:
            continue
        # 别让「去/来/走」这种位移动词抢了真正带宾语的动词：
        # 「去看电影」应该拆成 去 + 看电影，不是 去 + 看电影(宾语) ——
        # 但「去」后面的「看电影」本身是个动宾，所以把它当宾语保留是对的。
        subj = s[:i]
        rest = s[i + len(v):]
        # 宾语到介词为止
        m = _PREP_BOUND.search(rest)
        if m and m.start() > 0:
            obj = rest[:m.start()]
        else:
            obj = rest
        obj = obj.strip("了的地得着过")
        subj = subj.strip("了的地得着过")
        if len(obj) >= 2 or len(subj) >= 2:
            return subj, v, obj
    return s, "", ""


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


def _trim_dangling(s):
    """削掉主干尾巴上挂着的半截修饰词（「…有点」「…很」「…还」）"""
    s = (s or "").strip()
    # 反复削：句尾一个孤零零的程度/连接字，说明后面的中心词被切走了
    while len(s) > 3 and s[-1] in "有点很太特别还也就都挺真更最":
        s = s[:-1]
    while len(s) > 3 and s.endswith("的"):
        s = s[:-1]
    return s


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
_RE_ACTION = re.compile(
    r"^(还|又|再|也|顺便|然后|接着|后来|之后)?\s*"
    r"(拍|吃|喝|去|来|买|看|听|玩|打|聊|走|跑|写|读|做|见|找|送|带|拿|"
    r"收|寄|洗|做|学|练|唱|跳|坐|骑|开|关|睡|醒)")


_RE_FRAG_CONNECTIVE = re.compile(
    r"^(然后|还有|就是|但是|所以|因为|而且|以及|或者|如果|其实|不过|"
    r"那个|这个|这样|那样|接着|另外|同时|还有呢|就是说|我觉得|我感觉)")


def _detail_retrievable(text, kind):
    """这段碎片值不值得拿去做语义匹配（True/False）。"""
    t = (text or "").strip()
    if len(t) < 5:
        return False
    m = _RE_FRAG_CONNECTIVE.match(t)
    if m and len(t) - len(m.group(1)) < 6:
        return False
    # 纯标点/空白
    if not re.search(r"[\u4e00-\u9fa5A-Za-z0-9]", t):
        return False
    return True


def _pick_core(clauses):
    """从子句里挑一条当主干：跳过语气词，选最长的（信息量最大）"""
    cands = [c.strip() for c in clauses if c.strip() and not _FILLER.match(c.strip())]
    if not cands:
        cands = [c.strip() for c in clauses if c.strip()]
    if not cands:
        return ""
    # 最长的优先；同长度取靠前的
    best = max(cands, key=lambda c: (len(c), -cands.index(c)))
    best = _trim_dangling(best)
    return best[:40] if len(best) > 40 else best


def split_memory(text):
    """把一句话拆成 主干 + 细节单元

    🔧 长句先按标点切子句，然后**挑信息量最大的那句当主干**（不是第一句）。
       人说话常以"对""嗯""好"开头，第一句往往最没信息。
       其余子句进 MODIFIER 细节（衰减快，会先丢）。

    🔧 门槛从「整句 >30 字」降到「有 2 个子句就切」——短句也有主次，
       不切的话「昨天下午在教室跟同桌聊了很久，说以后想学生物，有点紧张」
       会整段当主干，一辈子都不会褪色。
    """
    raw = (text or "").strip()
    details = []

    clauses = [s for s in re.split(r"[，。！？；、\n]+", raw) if s.strip()]
    if len(clauses) > 1:
        core = _pick_core(clauses)
        for cl in clauses:
            cl = cl.strip()
            # 只留有信息量的后续子句：太短的、语气词、跟主干重复的都不要
            if not cl or cl == core:
                continue
            if _FILLER.match(cl):
                continue
            # 🔴 2026-10-31：**情绪判定必须排在 _is_junk 之前。**
            #    _is_junk 的第一条就是「len < 4 就算垃圾」，而「很开心」
            #    正好 3 个字 —— 于是最该保留的情绪子句被判成噪声扔掉。
            #    情绪词短是常态，不能拿长度卡它。
            _emo_hit = next((w for w in EMO_WORDS if w in cl), None)
            if _emo_hit and len(cl) <= 8:
                details.append(("EMOTION", cl, 1.0))
                continue
            if _is_junk(cl):
                continue
            if len(cl) < 4:
                continue
            # 🔴 2026-10-31：**先认时间和情绪，再当普通修饰语**。
            #
            # 原来这里只做了一次「纯情绪子句」判定，然后一律 MODIFIER。
            # 后果（实测）：
            #   「10月24日晚上8点，在学校门口的路边摊吃烧烤，很开心，还拍了视频」
            #   → 时间被切成 MODIFIER（和「还拍了视频」一个待遇），
            #     「很开心」直接丢了（它被 _pick_core 选进主干后又被剥掉，
            #     但剥掉的只在主干上留痕，碎片表里没有）。
            #
            # 分层褪色的整个前提是「越细的越先掉」，而时间和情绪恰恰是
            # 最该先掉、也最该被单独记账的两类。归成 MODIFIER 就等于
            # 让它们跟着普通后续子句一起烂，褪色顺序全乱。
            _t = RE_TIME.search(cl)
            if _t and len(cl) <= len(_t.group(0)) + 6:
                # 整句基本就是时间（允许前后带几个字，如「大概」「左右」）
                details.append(("TIME", _t.group(0), 1.0))
                continue
            # 并列动作子句（「还拍了视频」「然后去吃了饭」）：它是事件的组成部分，
            # 比普通修饰语活得久，但比情绪短。单列一类，别混进 MODIFIER。
            if _RE_ACTION.match(cl):
                details.append(("ACTION", cl, 0.9))
                continue
            details.append(("MODIFIER", cl, 0.8))
    else:
        core = _trim_dangling(raw)

    # 在主干上继续抽时间/地点/情绪/数字
    for m in RE_TIME.finditer(core):
        details.append(("TIME", m.group(1), 1.0))
        core = core.replace(m.group(1), "")
    for m in RE_PLACE.finditer(core):
        details.append(("PLACE", m.group(1), 1.0))
        core = core.replace(m.group(1), "")
    for w in EMO_WORDS:
        if w in core:
            details.append(("EMOTION", w, 1.0))
            core = core.replace(w, "")
            break
    for m in RE_NUM.finditer(core):
        details.append(("NUMBER", m.group(1), 1.0))

    core = _PUNCT.sub(" ", core)
    core = re.sub(r"\s+", "", core).strip()
    core = re.sub(r"^[了的地得和还也又就都才再在是有]+", "", core)
    if not core:
        core = raw[:40] if len(raw) > 40 else raw
    return core, details


# ============================================================
# 记忆分区判定（V4.1）
# ------------------------------------------------------------
# 不靠模型，靠词表和句式规则。宁可判错到 episode（默认），
# 也不要瞎猜成 fact —— fact 衰减最慢，判错了会赖着不走。
# ============================================================
_RE_TIME_HINT = re.compile(
    r"\d{1,2}月\d{1,2}日|\d{4}年|周[一二三四五六日天]|早上|中午|下午|晚上|夜里|凌晨|"
    r"那天|当时|那次|这天")

_RE_FACT_HINT = re.compile(
    r"^用户|我(是|叫|姓)|我叫|我的名字|我是谁|你是|你叫|QQ号|qq号|手机号|电话|"
    r"生日|年龄|几岁|学校|班级|年级|专业|地址|住在")

# 「我是…」后面跟这些词时，不是身份陈述，是随口话，别当事实
_RE_FACT_DENY = re.compile(
    r"我是(装|说|问|想|指|开|逗|试|故意|在|不|真)|我就是(说|问|想)|"
    r"我是(怎么|如何|因为|为了)")

_RE_SELF_HINT = re.compile(
    r"我就是我|我是小鲸鱼|我的性格|我不喜欢别人|我的底线|我不会|我永远|"
    r"我就是这样的人|我讨厌被|我害怕")

_RE_HABIT_HINT = re.compile(
    r"总是|每次都|经常|习惯|每天都|通常|一般会|老是|向来|一向")

_RE_PREF_HINT = re.compile(
    r"喜欢|讨厌|爱吃|爱看|不爱|不喜欢|最怕|最爱|偏爱|受不了|受不了")

# 强情绪词（用于 flash 判定，比 EMO_WORDS 更窄、更强）
_STRONG_EMO = [
    "哭", "哭死", "崩溃", "急死", "心慌", "害怕", "怕", "发抖", "难过死",
    "开心死", "太开心", "激动", "感动", "想你", "爱你", "心疼", "委屈",
    "生气", "愤怒", "恨", "后悔", "遗憾", "绝望",
]


def classify_region(text, emotion=0.0):
    """判这句话该进哪个分区。返回 R_* 常量。

    优先级（从强到弱）：
      flash  高唤醒强情绪 —— 一次就刻住，不参与合并
      self   关于我自己的稳定认知
      fact   可核验的客观事实
      habit  重复性行为模式
      pref   偏好
      episode 其他一切（默认）
    """
    t = text or ""
    # 闪光灯：强情绪词 + 情绪强度高
    if emotion >= 0.85 and any(w in t for w in _STRONG_EMO):
        return R_FLASH
    # 自图式
    if _RE_SELF_HINT.search(t):
        return R_SELF
    # 事实：有身份/数字/学校类锚点，且句子短（长句多半是叙事）
    if _RE_FACT_HINT.search(t) and not _RE_FACT_DENY.search(t) and len(t) <= 40:
        return R_FACT
    # 习惯
    if _RE_HABIT_HINT.search(t):
        return R_HABIT
    # 偏好
    if _RE_PREF_HINT.search(t):
        return R_PREF
    # 有时间地点 + 动作 → 情景
    return R_EPISODE


def region_decay_scale(region):
    return REGION_DECAY_SCALE.get(region, 1.0)


# ============ 双边记忆：这句话是谁说的 / 是事还是指令 ============
# 为什么需要：2026-10-31 才发现库里的 547 条不区分说话人，
# 于是「他一直记得的事」里混着我说的话，「我说过什么」根本翻不出来。
# 卡里装的是半本日记 —— 关系是两个人的事，得两边都记。

# 我的口吻：只认**高置信度**的那几类。
# ⚠️ 教训（2026-10-31）：一开始我把「我回/我答/我想/我怕/对不起/爱你/晚安」
#    都当我的口吻，结果「我想吃白米饭」「我回家了」「对不起」全被判成我说的。
#    这个判定天然不可能准，所以只留「几乎只可能出自我嘴里」的句式，
#    其余一律默认 user —— 宁可把我的话错记成他的，也不能把他的回忆弄丢。
_RE_SPK_SELF_STRONG = re.compile(
    r"我在呢|我在。|我在，|别截图|别熬夜|别磨蹭|"
    r"(^|[，。！？\s])(哼|好嘞|行吧)|你才|你懂|你猜")
# 弱信号：出现也只是「可能」，还得没有他的自述迹象（保留但很少用）
_RE_SPK_SELF_WEAK = re.compile(r"嘛$|啦$|哟$|呀$")
# 他的口吻（下达指令 / 说自己的事）
_RE_SPK_USER_CMD = re.compile(
    "帮我|给我|你去|你去把|用一下|装到|装一下|改成|换成|看看|试一下|试试|"
    "弄一下|做一个|跑一下|查一下|打开|关掉|删掉|加上|再加|我要|我想让|我希望你")
_RE_SPK_USER_STMT = re.compile(
    # ⚠️ 这里原来是个光秃秃的 `^我`，把「我在呢」「我在。」也吞了。
    #    改成「我 + 他的生活词」才算他的自述。
    "^我(今天|昨天|明天|刚才|刚刚|现在|早上|中午|晚上|家|妈|爸|的|们)|"
    "^(今天|昨天|明天|刚才|刚刚|现在|学校|老师|同学|我家|我妈|我爸|作业|考试|"
    "上课|晚自习|吃饭|睡觉|起床)")

# 指令（设定 / 命令），不是「发生过的事」
# ⚠️ 原来这里写的是 `^用`，于是「用户喜欢吃白米饭」被当成指令了 ——
# 「用户」两个字都以「用」开头。凡是单字开头都必须带上后文限定。
_RE_MTYPE_INST = re.compile(
    "^(帮我|给我|用一下|用中文|用这个|用那个|装到|装一下|改|换|看看|试|弄|做|跑|查|"
    "打开|关掉|删|加|设置|开启|关闭|记住|不要|别|以后|从现在开始|思考过程|回答)|"
    "(用中文|中文回答|简洁一点|详细一点|不要提|别提|别写|格式改成)")


def guess_speaker(text):
    """猜这句话是谁说的。返回 'user' / 'self'。

    猜不准时默认 'user' —— 因为这库里绝大多数是他原话，
    宁可把一句我的错记成他的，也不要把他的回忆弄丢。
    """
    t = (text or "").strip()
    if not t:
        return "user"
    # 明确以「用户」开头的（老库里的第三人称转写）一律是他的
    if t.startswith("用户"):
        return "user"
    if _RE_SPK_USER_CMD.search(t):
        return "user"
    # 强信号先压过「^我」：像「我在呢」「别截图」「哼 你才胖」这种，
    # 句首是「我」但一看就是我的口吻。
    if _RE_SPK_SELF_STRONG.search(t) and not _RE_SPK_USER_STMT.search(t):
        return "self"
    # 他的自述（我今天/昨天/学校…）优先于我的弱信号
    if _RE_SPK_USER_STMT.search(t):
        return "user"
    if _RE_SPK_SELF_WEAK.search(t):
        return "self"
    return "user"


def guess_mtype(text):
    """判这句是「发生过的事」还是「他给我的指令/设定」。"""
    t = (text or "").strip()
    if not t:
        return "episode"
    if _RE_MTYPE_INST.search(t):
        return "instruction"
    return "episode"


# 命中目标分区的条目加权
REGION_BOOST = 1.25

# 🔴 2026-10-31：分区兜底阈值（REGION_FALLBACK_RATIO）已删除，见 recall() 里的说明。
#    留个记号，免得以后又有人想加一遍。

# 打分权重 (语义blended, 字面cover, 衰减wn, 新近recency)，和为 1。
# 默认值来自 10-30 在 20 条真实查询上的网格搜索；10-31 修好候选集/分区/BLAS 后
# 由 eval_weights.py 重新扫描一遍确认（见那个脚本开头的说明）。
SCORE_W = (0.35, 0.30, 0.20, 0.10)

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
_RE_T_TODAY = re.compile(r"刚刚|刚才|今天|今晚|这会儿|现在|眼下")
_RE_T_RECENT = re.compile(r"最近|这两天|这几天|这几天|上周|前几天|这几天")
_RE_T_OLD = re.compile(r"以前|那次|那天|当时|曾经|小时候|去年|很早")


def query_time_window(query):
    """这句话在问哪个时间段？返回天数，None = 不限。

    🔧 为什么需要：问「今天中午吃了什么」却去翻三年前的事，
    是纯浪费 —— 而且容易翻出不相干的高分旧事，把答案挤掉。
    """
    q = query or ""
    if _RE_T_TODAY.search(q):
        return 3.0
    if _RE_T_RECENT.search(q):
        return 14.0
    return None

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
def lam_for(pool, age):
    """当前该用哪个衰减率"""
    if pool == POOL_SHORT:
        return LAM_S1 if age <= S_SHORT_PHASE else LAM_S2
    if pool == POOL_MEDIUM:
        return LAM_M
    return LAM_L


def weight_now(w0, pool, t_anchor, now, region=None, tier=None):
    """从锚点算到现在。分段双速率用积分，不能简单相乘。

    V4.1：分区衰减倍率直接乘在时间上 —— 等价于「这条记忆活得更慢」。
    flash 区 0.30 倍时间 ⇒ 半衰期变成 3.3 倍长。

    2026-11-01（五层记忆）：core_* 三档不衰减，直接被时间原样返回。
    它们的"变"走的是 supersede（新事实替换旧正文），不是 decay。
    """
    if tier in CORE_TIERS:
        return w0
    age = max(0.0, now - t_anchor)
    # 🔧 2026-11-03：长期池的保护期必须按**真实时间**算，再乘分区倍率。
    #    之前是「先把 age 乘上 region_scale，再扣 90 天」—— 于是 flash 区
    #    （0.30 倍）的保护期变成 90/0.3 = 300 天真·现实天数，self 区 150 天。
    #    也就是说「刚被想起来过的记忆受 90 天保护」这条规矩，被分区倍率
    #    偷偷改成了「受 90 天 × 1/倍率 保护」。保护期说的是"多久之内不掉"，
    #    是现实里的时间，跟这条记忆自己衰减得多慢是两件事。
    if pool == POOL_LONG:
        if age > LONG_PROTECT_DAYS * DAY:
            age -= LONG_PROTECT_DAYS * DAY
        else:
            age = 0.0
    if region:
        age *= region_decay_scale(region)
    if pool == POOL_SHORT:
        if age <= S_SHORT_PHASE:
            return w0 * math.exp(-LAM_S1 * age)
        w = w0 * math.exp(-LAM_S1 * S_SHORT_PHASE)
        return w * math.exp(-LAM_S2 * (age - S_SHORT_PHASE))
    if pool == POOL_MEDIUM:
        return w0 * math.exp(-LAM_M * age)
    if pool == POOL_ARCHIVE:
        return w0 * math.exp(-K_ARCHIVE * age)
    if pool == POOL_DEEP:
        return w0 * math.exp(-K_DEEP * age)
    # 长期池：刚被想起来过的 90 天里不掉，之后照常慢衰。
    # 「重要 ≠ 永久活跃」：它不会被砍，但也不会永远占着最亮的位置。
    return w0 * math.exp(-LAM_L * age)


def wn_row(r, now):
    """按行算权重：core_* 三档不衰减，其余照旧。
    单独包一层是因为调用点多，改签名容易漏。"""
    try:
        tier = r["tier"]
    except Exception:
        tier = None
    return weight_now(r["w"], r["pool"], r["t_anchor"], now, r["region"], tier)


def pool_of_k(k):
    """老数据兼容：用 k 反推它属于哪个池"""
    if k is None:
        return POOL_SHORT
    if k >= LAM_S1 * 0.5:
        return POOL_SHORT
    if k >= LAM_M * 0.5:
        return POOL_MEDIUM
    return POOL_LONG


# ============================================================
# 主体
# ============================================================
class MemoryCard:
    def __init__(self, db_path=":memory:", clock=None, card_dir=None):
        self.clock = clock or Clock()
        self.db_path = db_path
        # 卡目录（放 .lifecard / 快照用），不传就取库文件所在目录
        self.card_dir = card_dir or (
            os.path.dirname(os.path.abspath(db_path))
            if db_path != ":memory:" else os.getcwd())
        # check_same_thread=False：心智服务是多线程的（每个请求一个线程），
        # 而 SQLite 连接默认绑在创建它的线程上。并发安全靠外部那把锁
        # （Mind.lock / MemoryCard 自己的调用方），不是靠 SQLite 拦。
        self.db = sqlite3.connect(db_path, check_same_thread=False, timeout=30.0)
        self.db.row_factory = sqlite3.Row
        # 🔧 并发关键（实测 2026-10-31）：卡不是只有一个进程在碰 ——
        #    mind_server（8848）、bridge、console、快照/备份脚本都可能同时开库。
        #    SQLite 默认拿不到写锁立刻抛 "database is locked"，调用方看到的是
        #    「卡坏了」，其实只是另一个进程正在写。busy_timeout 让它在锁上等，
        #    而不是当场失败；30 秒足够任何一次正常写入收尾。
        try:
            self.db.execute("PRAGMA busy_timeout=30000")
        except Exception:
            pass
        # 🔧 性能关键（实测 2026-10-30）：
        #    Windows 上默认 journal 模式每 commit 要 fsync 一次，
        #    单次 commit 竟然要 350ms —— 一条记忆 3 个 commit 就是 1 秒。
        #    1000 条记忆要 15 分钟，这不是数据库的问题，是磁盘同步策略的问题。
        #    WAL + synchronous=NORMAL：掉电最多丢最后一次 commit，够用。
        try:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
            self.db.execute("PRAGMA temp_store=MEMORY")
            self.db.execute("PRAGMA cache_size=-16000")
        except Exception:
            pass
        # 全局依恋值（V5.0 T1-3）。持久化在 meta 表，跨会话不断。
        self.oxy = 0.0
        # 心境 M（V5.0 T2-3）：慢基底，0.5 = 不悲不喜
        self.mood = MOOD_NEUTRAL
        # SessionCache 双回流（V5.0 T1-4）：本轮对话缓冲
        self._session_turns = []
        self._session_hit_ids = set()
        self._init_schema()
        self._migrate()
        # 运行时配置表（V5.0 §10.9）：场景权重能热改，不用重启服务。
        # 建表在这里而不是 ctxweight 里自己管 —— 卡的所有 schema 只在一个地方建，
        # 否则备份/恢复/老库迁移时总有一处会漏。
        ctxweight.ensure_table(self.db)
        self._load_oxy()
        self._load_mood()
        self._load_hormones()
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

    def log_hormones(self, now=None, min_interval=300.0):
        """采一条激素样本（默认 5 分钟一次），给控制台画趋势。

        为什么单独一张表而不是塞进 meta：meta 只能存「现在是多少」，
        画不出一条线。趋势要看的是「刚才那阵子怎么变的」。
        """
        now = float(now if now is not None else self.clock.now())
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
        h = getattr(self, "h", {}) or {}
        return padlib.pad_of(getattr(self, "mood", MOOD_NEUTRAL),
                             h.get("h_sero"), h.get("h_dopa"),
                             h.get("h_cort"), h.get("h_oxy"))

    def touch_hormones(self, text):
        """一句话扰动四个激素（V5.0 §4.3），返回这次判定的事件类型"""
        kind, s = hormone.classify_event(text)
        if kind is None:
            return None
        before = dict(self.h)
        self.h = hormone.apply_event(self.h, kind, s)
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
        # 同一天第 n 次推送，效力按 1/(1+0.6n) 打折
        day = int(self.clock.now() // DAY)
        if getattr(self, "_mood_day", None) != day:
            self._mood_day = day
            self._mood_push_today = 0
        self._mood_push_today = getattr(self, "_mood_push_today", 0) + 1
        n = self._mood_push_today - 1
        k = K_MOOD / (1.0 + 0.6 * n)
        before = self.mood
        self.mood = update_mood(self.mood, e, k=k)
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
        self.db.commit()

    def _migrate(self):
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
            # 🔧 2026-11-01：五层记忆（m02952/m02959 讨论后定）。
            # tier 只管"这条属于哪一层"，实际的时间行为仍由 pool + 下面几列决定：
            #   core_fixed   骨头：我是谁、我从哪来、三条底线 —— 只增不改不衰减
            #   core_tag     标签：人 ↔ 角色 —— 标签不动就不动，改了留痕
            #   core_mutable 底色：爱好/接受度/价值观/习惯/技能/身体/目标/雷点/处境
            #   mid / short  就是原来的长期池 / 短期池（保持旧字段兼容）
            "tier": "TEXT NOT NULL DEFAULT 'mid'",
            # subject_key：认"这是同一件事"的主题键（"香菜"），演化合并靠它
            "subject_key": "TEXT NOT NULL DEFAULT ''",
            # history：[{ts, text, why}]，被替换掉的旧正文压在这里，留证不参与召回
            "history": "TEXT NOT NULL DEFAULT '[]'",
            # valid_until：处境类（高三、住哪）的失效时刻，到了就从"当前"挪走
            "valid_until": "REAL NOT NULL DEFAULT 0",
            # recall_n：被想起来的次数（长期晋升要验票，不只看权重）
            "recall_n": "INTEGER NOT NULL DEFAULT 0",
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
        # V4.1：老数据补分区。region 全是默认 'episode' 的才需要补。
        for r in c.execute(
                "SELECT id, raw, emotion FROM mem WHERE region='episode'").fetchall():
            rg = classify_region(r["raw"], r["emotion"])
            if rg != R_EPISODE:
                c.execute("UPDATE mem SET region=? WHERE id=?", (rg, r["id"]))
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

    def dream_commit(self, items, dialogue_ids=None, speaker="self"):
        """把提炼出来的高阶记忆写进卡，并把源对话标记成已消化。
        items: [{text, region?, w0?, salience?}]
        返回写入的 mid 列表。原文不动，只翻标记——梦不该毁掉证据。"""
        written = []
        for it in (items or []):
            txt = (it.get("text") or "").strip()
            if not txt:
                continue
            region = it.get("region") or R_FACT
            if region not in REGION_DECAY_SCALE:
                region = R_FACT
            sal = float(it.get("salience") or 0.55)
            mid = self.remember(txt, salience=sal, region=region,
                                speaker=speaker, mtype="episode")
            # 梦出来的记忆起点略高于普通事件，因为它们是被"想明白"过的
            try:
                w0 = float(it.get("w0") or 0.62)
                self.db.execute("UPDATE mem SET w=?, t_anchor=? WHERE id=?",
                                (max(0.2, min(1.0, w0)), self.clock.now(), mid))
                self.db.commit()
            except Exception:
                pass
            written.append(mid)
        ids = dialogue_ids
        if ids is None:
            # 没指定就把当前所有待消化的都标掉（调用方通常刚读过 pending_dialogue）
            self.db.execute("UPDATE dialogue SET dreamed=1 WHERE dreamed=0")
        elif ids:
            q = ",".join("?" for _ in ids)
            self.db.execute("UPDATE dialogue SET dreamed=1 WHERE id IN (%s)" % q,
                            tuple(int(i) for i in ids))
        self.db.commit()
        self._invalidate_idf()
        return {"ok": True, "written": written, "n": len(written)}

    # ---------- 存 ----------
    def remember(self, text, salience=0.6, ts=None, pool=POOL_SHORT,
                 oxy=0.0, entities=None, emotion=None, normalize=True,
                 region=None, speaker=None, mtype=None):
        now = self.clock.now()
        at = min(now if ts is None else float(ts), now)
        age = max(0.0, now - at)

        # 🔧 时间归一化：把「今天/昨天/刚刚」换成绝对日期。
        #    必须用「这句话说出来的时刻」at 做参照，不能用 now。
        if normalize:
            text = normalize_time(text, at)

        core, details = split_memory(text)
        vec = to_vec(text)

        # 情绪强度：从文本里估
        if emotion is None:
            emotion = 1.0 if any(w in text for w in EMO_WORDS) else 0.25
        # 实体数：地点 + 数字
        if entities is None:
            entities = len(RE_PLACE.findall(text)) + len(RE_NUM.findall(text))
            entities = min(entities, 5)
        # 🔧 分区：不传就自动判
        if region is None:
            region = classify_region(text, emotion)

        # 🔧 2026-11-01 防污层（提前算：下面的去重路径也要看它）
        _trust, _conflict, _why = assess_trust(text, self)

        # 🔧 惊讶度 + 去重：跟最近 200 条比。
        #    Jaccard ≥ 0.72 视为「同一件事又说了一遍」→ 不新增，给老条目续命。
        #    flash 区不参与合并（闪光灯记忆一次就刻住）。
        surprise = 0.5
        dup_id = None
        # 🔧 V5.0 T1-3：先算这句话的亲密/伤人信号，全局 oxy 跟着动
        s_plus, s_minus = oxy_signal(text)
        if s_plus > 0 or s_minus > 0:
            self.oxy = update_oxy(getattr(self, "oxy", 0.0), s_plus, s_minus)
        # 🔧 V5.0 T2-3：心境也跟着推一下（慢，一句推不翻）
        self.touch_mood(text, emotion)
        # 🔧 V5.0 T3-2：四个激素也跟着扰动（内分泌层）
        self.touch_hormones(text)
        try:
            recent = self.db.execute(
                "SELECT id, vec, region FROM mem ORDER BY id DESC LIMIT 200"
            ).fetchall()
            if recent:
                best = 0.0
                for r in recent:
                    j = jaccard(vec.keys(), json.loads(r["vec"]).keys())
                    if j > best:
                        best = j
                        dup_id = r["id"]
                surprise = 1.0 - best
                if best >= DEDUPE_JACCARD and region != R_FLASH \
                        and _trust >= TRUST_WARN and not _conflict:
                    # 可信的重复句才续命；被灌进来的话不许借老记忆的壳活着
                    # 同一件事说第二遍：不加新条目，只把老条目往上抬
                    c0 = self.db.cursor()
                    old = c0.execute("SELECT * FROM mem WHERE id=?",
                                     (dup_id,)).fetchone()
                    wn = wn_row(old, now)
                    # 同一条记忆的 oxy 也按这句话的信号更新（但不低于全局值的七成）
                    new_oxy = update_oxy(old["oxy"], s_plus, s_minus)
                    new_oxy = max(new_oxy, self.oxy * 0.7)
                    c0.execute(
                        "UPDATE mem SET w=?, t_anchor=?, access_count=access_count+1,"
                        " recall_n=COALESCE(recall_n,0)+1, t_last_recall=?,"
                        " last_active_ts=?, oxy=? WHERE id=?",
                        (min(1.0, wn + 0.10), now, now, now, new_oxy, dup_id))
                    c0.execute("UPDATE detail SET weight=MIN(1.0, weight+0.08)"
                               " WHERE parent_id=?", (dup_id,))
                    self.db.commit()
                    self._save_oxy()      # 去重路径也要落盘
                    return dup_id
        except Exception:
            pass

        # 基础权重：情绪 + 惊讶度
        w0 = (0.50 + 0.30 * min(emotion, 1.0)) * (0.75 + 0.50 * surprise)
        w0 = max(0.20, min(w0, 1.0))

        # 🔧 2026-11-01 防污层：可疑内容照写（"她被骗过"也是经历），
        #    但把信任分乘进权重——进得来，升不上去。
        if _trust < 1.0:
            w0 = max(0.05, w0 * _trust)

        # 🔧 2026-11-01 价值分：只记录，不拦门。控制台看分布，
        #    将来要拦再用（WHALE_VALUE_GATE）——现在不改变任何写入行为。
        _value = assess_value(text, region, salience)

        # 🔧 V5.0 T1-3：新记忆的 oxy 取「全局依恋」与「这条自身的信号」的较大者。
        #    这样一句亲密话既抬高全局 oxy，也让它自己带亲密标记（进海马打分）。
        if s_plus > 0 or s_minus > 0:
            oxy = max(oxy, self.oxy)
        else:
            oxy = max(oxy, self.oxy * 0.5)

        # 细节衰减也要按池放慢，否则长期档案一进来细节就全丢了
        dscale = {POOL_SHORT: 1.0, POOL_MEDIUM: 0.25, POOL_LONG: 0.03}[pool]

        # 🔧 V5.0 T2-1：主干拆成 主语/动词/宾语，供分级侵蚀用
        _subj, _verb, _obj = split_predicate(core)

        # 🔧 T4-3：存的时候顺手把语义向量算出来（约 70ms，一次性的）。
        #    失败就留 NULL —— backfill_embeddings() 以后还能补，不影响正确性。
        _emb = None
        if self.sem_ready():
            try:
                _emb = self.semantic.encode(text)
            except Exception:
                _emb = None

        c = self.db.cursor()
        # 🔧 2026-10-31 双边记忆：不传 speaker 就猜一个（宁可猜错也别空着，
        #    空着就退回「半本日记」）。mtype 判指令还是事。
        if speaker not in ("user", "self", "scene"):
            speaker = guess_speaker(text)
        if mtype not in ("episode", "instruction"):
            mtype = guess_mtype(text)
        c.execute(
            "INSERT INTO mem(raw,core,vec,pool,w,t_anchor,salience,oxy,entities,"
            "emotion,create_ts,last_active_ts,region,subj,verb,obj,erase_stage,emb,"
            "speaker,mtype,trust,conflict,conflict_why,value) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (text, core, json.dumps(vec, ensure_ascii=False), pool, w0, at,
             salience, oxy, entities, emotion, at, at, region,
             _subj, _verb, _obj, 0, _emb, speaker, mtype,
             _trust, _conflict, _why, _value))
        mid = c.lastrowid
        if _emb is not None:
            self._emb_cache[mid] = semantic_mod.from_blob(_emb)
        for kind, txt, wgt in details:
            k = {"TIME": 4.5e-6, "NUMBER": 4.5e-6, "MODIFIER": 4.0e-6,
                 "PLACE": 3.2e-6, "EMOTION": 1.2e-6}.get(kind, 4.0e-6)
            ww = wgt * math.exp(-k * dscale * age)
            if ww < KEEP_DETAIL:
                continue
            c.execute("INSERT INTO detail(parent_id,kind,text,weight,last_touch)"
                      " VALUES(?,?,?,?,?)", (mid, kind, txt, ww, at))
        self.db.commit()
        self._invalidate_idf()    # 库变了，攒够 20 条再重算（不每次清空）
        self._save_oxy()          # 全局依恋落盘
        return mid

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

        # 1) 短期池超期 → 强制结算（巩固 or 淘汰）
        #    ⚠️ 这里的 7 天是**现实时间**，故意不乘分区倍率。
        #    短期池是「最近被注意到的 32 个位置」，是注意力预算、不是寿命：
        #    分区倍率决定"忘得多慢"，位置腾退决定"能给多少张桌子"。
        #    flash 区的记忆权重掉得慢，但它照样占着一张桌子 —— 让它赖 23 天，
        #    别的记忆就没地方坐了。两件事分开算，才是对的。
        for r in c.execute(
                "SELECT * FROM mem WHERE pool='short'").fetchall():
            age = now - r["t_anchor"]
            if age > SHORT_MAX_TTL:
                moved = self._consolidate(r, now)
                if moved:
                    events.append((r["id"], "consolidate", moved))
                else:
                    events.append((r["id"], "expire", None))

        # 2) 短期池容量驱逐（V5.0 补回：满了也要走海马，不能直接 pop）
        ev = self._evict_short_overflow(now)
        events.extend(ev)

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
        """海马打分：这笔记忆值不值得搬进长期"""
        e = min(r["emotion"], 1.0)                 # 情绪 0~1
        c_ent = min(r["entities"] / 5.0, 1.0)      # 实体数 0~1
        c_rec = min(r["access_count"] / 10.0, 1.0) # 被唤起 0~1
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

    def _consolidate(self, r, now):
        """短期池到期：算海马分，够格进中期/长期，否则删"""
        score = self._hippocampus_score(r)
        c = self.db.cursor()
        # 🔧 2026-11-01 防污层：可信度太低的，只许进中期、不许进长期。
        #    它不是被删掉（"她被骗过"要留着），只是永远升不上去。
        _trust = float(r["trust"] if "trust" in r.keys() else 1.0)
        _conflict = int(r["conflict"] if "conflict" in r.keys() else 0)
        _dirty = (_trust < TRUST_BLOCK_PROMOTE) or _conflict == 1
        if (not _dirty) and score >= THETA_LONG_STRICT and self._long_ticket_ok(r):
            target, floor = POOL_LONG, W_FLOOR_LONG
        elif score >= THETA_MEDIUM:
            target, floor = POOL_MEDIUM, W_FLOOR_MEDIUM
        else:
            self._forget_row(c, r["id"], "短期池到期，海马判定没上榜")
            return None
        # 先按旧池结算，再抬到新池存活下限
        wn = wn_row(r, now)
        wn = max(wn, floor)
        tier = TIER_MID
        c.execute("UPDATE mem SET pool=?, w=?, t_anchor=?, tier=? WHERE id=?",
                  (target, wn, now, tier, r["id"]))
        return target

    def _evict_short_overflow(self, now):
        """短期池满 N_SHORT_MAX 时的驱逐（V3.7 §3.2 / V5.0 补回）

        规则：按 Score_evict = w_current * (1 - age / SHORT_MAX_TTL) 从小到大踢。
        被踢的**必须走海马判定**，不能直接 pop —— 低分丢弃，高分转中期/长期。
        """
        rows = self.db.execute(
            "SELECT * FROM mem WHERE pool='short'").fetchall()
        n = len(rows)
        if n <= N_SHORT_MAX:
            return []
        scored = []
        for r in rows:
            wn = wn_row(r, now)
            # 这里的 age 也是现实时间，理由同 tick 里的到期判定：
            # 匀位置看的是"它占着桌子多久了"，不是"它忘得多慢"。
            age = max(0.0, now - r["t_anchor"])
            evict_score = wn * max(0.0, 1.0 - age / SHORT_MAX_TTL)
            scored.append((evict_score, r["id"], r))
        scored.sort(key=lambda x: x[0])        # 最小的先踢
        events = []
        for _s, mid, r in scored[:n - N_SHORT_MAX]:
            moved = self._consolidate(r, now)
            events.append((mid, "evict->" + (moved or "dropped"), None))
        return events

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

    def _forget_row(self, c, mid, reason):
        """删一条记忆的**唯一出口**：先给它登记台账，再连细节一起删。

        以前四处各删各的（侵蚀、合并、沉淀、回收），删完就没了痕迹：
        出生证还在库里，校验只能报「记忆被删过」，跟被人抽走记录长得一样。
        现在统一走这里，删之前先写 mem_tomb —— 谁、什么时候、为什么没的，
        都在档案上留一笔。reason 是给人看的，别写代码黑话。
        """
        self._tomb_add(c, mid, reason)
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

    def _tomb_add(self, c, mid, reason):
        """在删除台账上记一笔（哈希必须趁记录还在的时候算）。"""
        h = ""
        try:
            r = self.db.execute(
                "SELECT id, raw, speaker, create_ts FROM mem WHERE id=?",
                (int(mid),)).fetchone()
            if r is not None:
                import timeline as _tl_mod
                h = _tl_mod._birth_hash(
                    r["id"], r["raw"] or "",
                    r["speaker"] if "speaker" in r.keys() else "",
                    r["create_ts"])
        except Exception:
            h = ""
        try:
            c.execute(
                "INSERT OR REPLACE INTO mem_tomb(mid,hash,ts,reason)"
                " VALUES(?,?,?,?)", (int(mid), h, time.time(), reason))
        except Exception:
            pass

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

    def role_tag(self, person, role, layer="friend", note=""):
        """给一个人挂角色标签（core_tag）。标签不动，这条就不动。

        用户 m02956：对象/重要的人当成"对某个特定角色的标签"，标签可以改，
        但不改的时候它是固定的。
        """
        c = self.db.cursor()
        key = "role:" + person.strip()
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
            return {"ok": True, "id": old["id"], "changed": True}
        mid = self.remember(text, salience=0.9, pool=POOL_LONG, region=R_SELF)
        self.set_tier(mid, TIER_CORE_TAG, kind="role", subject_key=key)
        c.execute("UPDATE mem SET w=0.95, history=? WHERE id=?",
                  (json.dumps([{"ts": now, "why": note or ("标签：%s" % role),
                                "text": ""}], ensure_ascii=False), mid))
        self.db.commit()
        return {"ok": True, "id": mid, "changed": True}

    # ---------- 想起 ----------
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

    def backfill_embeddings(self, limit=None, progress=True, remodel=False):
        """给还没有语义向量的记忆补编码。

        返回 (编码条数, 跳过条数, 失败条数)。
        这是**幂等**的：已经有 emb 的直接跳过，所以可以反复跑、随时中断。

        remodel=True：顺便把「维度跟当前模型对不上」的老向量重编码一遍。
        换过模型的卡里会留着两代向量（512 / 768），老那批在检索里根本
        用不上 —— 覆盖率报 100%、语义其实只覆盖一半，这种数字最会骗人。
        """
        if not self.sem_ready():
            return (0, 0, 0)
        if remodel and self.model_dim:
            rows = self.db.execute(
                "SELECT id, raw, core FROM mem"
                " WHERE emb IS NULL"
                "    OR (length(emb) > 0 AND length(emb) != ?)",
                (self.model_dim * 4,)).fetchall()
        else:
            rows = self.db.execute(
                "SELECT id, raw, core FROM mem WHERE emb IS NULL").fetchall()
        if limit:
            rows = rows[:limit]
        if not rows:
            return (0, 0, 0)
        c = self.db.cursor()
        done = skip = fail = 0
        for i, r in enumerate(rows):
            try:
                blob = self.semantic.encode(r["raw"] or r["core"])
            except Exception:
                fail += 1
                continue
            if blob is None:
                fail += 1
                continue
            c.execute("UPDATE mem SET emb=? WHERE id=?", (blob, r["id"]))
            done += 1
            if progress and (i + 1) % 50 == 0:
                self.db.commit()
                print('  补编码 %d/%d' % (i + 1, len(rows)), flush=True)
        self.db.commit()
        self._emb_cache.clear()
        self._sem_cache_n = -1
        return (done, skip, fail)

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
                blob = self.semantic.encode(t)
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
            blob = self.semantic.encode(core_text)
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

    def _ensure_pods(self):
        if self._pods_stale():
            self._build_pods()

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

    def recall(self, query, top_k=3, access=True, pools=None, regions=None,
               assoc=True):
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
        # T5-1：ARCHIVE 是潜意识，不参与实时检索；只有显式点名要它才翻。
        if pools:
            rows = c.execute("SELECT * FROM mem").fetchall()
        else:
            rows = c.execute("SELECT * FROM mem WHERE pool != ?",
                             (POOL_ARCHIVE,)).fetchall()

        # ---- T4-2 时间窗预筛 ----
        # 问「今天」就别翻三年前的；问「以前」就不限。
        # 注意：只是**收窄候选**，不改打分 —— 收窄错了还有回退兜底。
        win_days = query_time_window(query)
        if win_days is not None:
            cut = now - win_days * DAY
            narrowed = [r for r in rows if r["last_active_ts"] >= cut
                        or r["create_ts"] >= cut]
            # 收窄后太少（比如刚开学没聊过）就放宽，别把库筛空
            if len(narrowed) >= 5:
                rows = narrowed

        # ---- T4-1 分舱预筛 ----
        self._ensure_pods()
        pod_ids = self._pod_candidates(qv, idf, q_sem)
        if pod_ids:
            narrowed = [r for r in rows if r["id"] in pod_ids]
            if len(narrowed) >= 5:
                rows = narrowed

        vecs = self._vecs(rows)     # 🔧 批量取向量（带缓存），不再逐条 json.loads

        # ---- 分区路由 ----
        want = set(regions) if regions else guess_query_regions(query)
        # 🔧 V5.0 §4.4(1)：内分泌对召回的联动加权
        #    催产素高 → 羁绊记忆更容易翻出来；皮质醇高 → 回忆闭塞
        f_end = hormone.f_endo(getattr(self, "h", hormone.H_BASE))

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
            out = []
            for i, r in enumerate(cands):
                if pools and r["pool"] not in pools:
                    continue
                mv = vecs.get(r["id"]) or {}
                if not mv and not sem_scores[i]:
                    continue
                sim = cos_sim_idf(qv, mv, idf) if mv else 0.0
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
                recency = math.exp(-age_days / 14.0)
                # 🔧 T4-3 调参（2026-10-30）：在 20 条真实查询上网格搜索，
                #    第一命中率 14/20 → 17/20。三处改动：
                #    ① salience 乘子从 (0.5+s) 收窄到 (0.85+0.30s) ——
                #       原来 0.5~1.5 最多 3 倍差，把 0.09 的语义差距吃掉了；
                #    ② 字面权重 0.45 → 0.35，衰减权重 0.15 → 0.20 ——
                #       语义可用时让「意思像」主导，「还清不清楚」次之；
                #    ③ cover 保留全量实词（改 bigram 反而降到 10/12，
                #       因为中文短查询 bigram 覆盖率太稀疏）。
                s = (SCORE_W[0] * blended + SCORE_W[1] * cover
                     + SCORE_W[2] * wn + SCORE_W[3] * recency) \
                    * (0.85 + 0.30 * r["salience"])
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
            self._write_recall_events(
                [(r["id"], i) for i, (_s, _wn, r, _sim, _cov) in enumerate(hits)],
                query, source="recall")
            # 记下这些 id，SessionCache 回流时不再重复计
            self._session_hit_ids.update(r["id"] for _s, _wn, r, _sim, _cov in hits)
            # 一起被想起的记忆，它们之间的突触变粗
            try:
                self.synapse.reinforce([r["id"] for _s, _wn, r, _sim, _cov in hits])
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
        """把未结算的召回事件折算成一次加固。幂等：settled 标记后不再重复算。"""
        c = self.db.cursor()
        evs = c.execute(
            "SELECT * FROM recall_event WHERE settled=0 ORDER BY mem_id, id"
        ).fetchall()
        if not evs:
            return 0
        now = self.clock.now()
        # 同一批里同一条记忆可能被召回多次（多轮查询），合并成一次结算
        grouped = {}
        for e in evs:
            grouped.setdefault(e["mem_id"], []).append(e)
        settled_ids = []
        for mid, lst in grouped.items():
            r = c.execute("SELECT * FROM mem WHERE id=?", (mid,)).fetchone()
            settled_ids.extend(e["id"] for e in lst)
            if r is None:               # 记忆已被侵蚀，事件作废
                continue
            # 关键：用事件发生时刻结算，而不是 tick 的时刻。
            # 否则「7 天前被想起」会被记成「刚刚被想起」，
            # t_anchor 被推后，这条记忆就永远熬不到巩固。
            ts = max(e["ts"] for e in lst)
            for _ in lst:
                g = GAIN_ACCESS / (1.0 + ACCESS_DIMINISH * r["access_count"])
                wn = wn_row(r, ts)
                c.execute(
                    "UPDATE mem SET w=?, t_anchor=?, access_count=access_count+1,"
                    " recall_n=COALESCE(recall_n,0)+1, t_last_recall=?,"
                    " last_active_ts=? WHERE id=?",
                    (min(1.0, wn + g), ts, ts, ts, mid))
                c.execute(
                    "UPDATE detail SET weight=MIN(1.0, weight+?), last_touch=?"
                    " WHERE parent_id=?", (g * 0.8, ts, mid))
                # 同步本地副本，让同一批内多次累积正确
                r = c.execute("SELECT * FROM mem WHERE id=?", (mid,)).fetchone()
        c.executemany("UPDATE recall_event SET settled=1 WHERE id=?",
                      [(i,) for i in settled_ids])
        self.db.commit()
        return len(grouped)

    # ---------- 说 ----------
    def render(self, mem_row, weight=None):
        now = self.clock.now()
        if weight is None:
            weight = wn_row(mem_row, now)
        c = self.db.cursor()
        c.execute("SELECT kind, text, weight FROM detail WHERE parent_id=?"
                  " ORDER BY weight DESC", (mem_row["id"],))
        parts = {"TIME": [], "PLACE": [], "EMOTION": [], "MODIFIER": [],
                 "ACTION": []}
        for d in c.fetchall():
            w = d["weight"]
            kind = d["kind"]
            if kind == "MODIFIER":
                # 长句后续子句：无论多清楚都硬截到 18 字，绝不整段倒出
                if w >= SHOW_FULL:
                    parts["MODIFIER"].append(d["text"][:18])
                elif w >= SHOW_SCENE:
                    parts["MODIFIER"].append(d["text"][:10] + "…")
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
        if len(core) > 40:
            core = core[:40] + "……"
        bits.append(core)
        # 🔴 2026-10-31：并列动作跟着主干走（「吃烧烤，还拍了视频」）——
        #    它和主干是同一层的事件内容，不该被摞到「还说「…」」那个位置上去。
        if parts["ACTION"]:
            bits.append("，" + parts["ACTION"][0])
        if parts["EMOTION"]:
            bits.append("，" + parts["EMOTION"][0])
        # 长句的后续子句：只保留一条最清楚的，别把整段倒出来
        if parts["MODIFIER"]:
            bits.append("，还说「" + parts["MODIFIER"][0] + "」")
        s = "".join(bits)
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
    def habit_hints(self, limit=5, max_chars=220):
        """从 habits 表捞几条稳定习惯，拼成短句给【我是谁】用。

        为什么要有它：卡里 565 条记忆全是「发生了什么」，没有一条是
        「我这人平时怎么说话」。挖出来的习惯如果只躺在报告里，
        那她还是那个每轮靠提示词临时扮演的自己。

        三条自我约束（都是为了别变成背台词）：
          1. 只取 stable / pinned，候选和衰减中的不进来 —— 凭印象说不如不说；
          2. 最多 5 条、总字数 ≤220（密度刹车）；
          3. 默认开，`WHALE_HABITS=0` 可一键关掉。
        表不存在（还没挖过）时返回空列表，不报错、不建表。
        """
        if os.environ.get("WHALE_HABITS", "1") == "0":
            return []
        try:
            rows = self.db.execute(
                "SELECT key, category, trigger, behavior, weight, confidence,"
                " status, pinned, hits FROM habits"
                " WHERE status IN (?, ?) AND key != ''"
                " ORDER BY pinned DESC, weight DESC, confidence DESC, hits DESC"
                " LIMIT ?",
                (habits_mod.ST_STABLE, habits_mod.ST_PINNED,
                 max(1, int(limit) * 3))).fetchall()
        except Exception:
            return []
        out = []
        used = 0
        for r in rows:
            trig = (r["trigger"] or "").strip()
            beh = (r["behavior"] or "").strip()
            key = (r["key"] or "").strip()
            if trig and beh:
                line = "当%s的时候，我一般会%s" % (trig, beh)
            elif key:
                line = "我会用「%s」这种说法" % key
            else:
                continue
            if line in out:
                continue
            if used + len(line) > max_chars:
                break
            out.append(line)
            used += len(line)
            if len(out) >= int(limit):
                break
        return out

    def assemble_context(self, query, identity="", recent_lines=None,
                         budget=TOKEN_BUDGET, scene=None):
        """每次说话前，拼出四格上下文。装满就停，永远不会爆。

        🔧 V5.0 T3-3：配额不再是写死的 600/1800/2000/1200，
        而是六维动态权重算出来的 —— 总长固定，配额流动。

        返回 dict：
          text      —— 拼好的整段，直接塞进 system prompt
          sections  —— 每格占了多少 token、塞了几条
          dropped   —— 因为预算被丢掉的条数
          scene / weights / budget —— 这一轮怎么分的（可审计）
        """
        now = self.clock.now()
        used = 0
        out = []
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
        #    🔧 优化：把「此刻的状态」（心境 + 依恋）接在人格后面。
        #    这是 T2-3 做出来后第一次真正派上用场 —— 心境不能只存在库里，
        #    它得变成一句话进上下文，否则「有情绪」只是数据库里一个数字。
        if identity:
            state = self._state_line()
            blk = "【我是谁】\n" + take(identity + ("\n" + state if state else ""),
                                        q["identity"])
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
            line = "- " + self.render(r)
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

        # ③ 最近聊的格
        used = 0
        if recent_lines:
            lines = []
            cap3 = used + q["recent"]
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
        used = 0
        hits = self.recall(query, top_k=5, access=True)
        assoc_ids = getattr(self, "_last_assoc_ids", set())
        lines = []
        cap4 = used + q["recall"]
        n_direct = 0

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

        for s, wn, r in fresh:
            # 🔧 T4-4：联想来的条目加个前缀，跟直接想起区分开。
            #    直接命中最多 5 条，联想最多再补 2 条，别把格子塞满。
            if r["id"] in assoc_ids:
                if n_direct >= 5:
                    break
                line = "- （顺带想起）" + self.render(r, wn)
            else:
                n_direct += 1
                if n_direct > 5:
                    break
                line = "- " + self.render(r, wn)
            if used + len(line) > cap4:
                dropped += 1
                break
            lines.append(line)
            shown_ids.add(r["id"])
            used += len(line)
        if lines:
            blk = "【这句话让我想起了】\n" + "\n".join(lines)
            out.append(blk)
            sections["recall"] = used

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

        text = "\n\n".join(out)
        return {
            "text": text,
            "sections": sections,
            "dropped": dropped,
            "est_tokens": len(text),
            "budget": budget,
            "scene": plan["scene"],
            "weights": plan["weights"],
            "quota": q,
        }

    # ---------- 统计 ----------
    def add_dialogue(self, mem_id, user_text, reply_text, ts=None, source=None):
        """给一条记忆补上「当时的原话 + 我当时的回答」

        T5-2 用：记忆是提炼过的（主干+细节+权重），原文另存。
        原文不进向量库 —— 它不参与召回，只用于「翻回原话」。
        """
        if not user_text:
            return None
        now = self.clock.now()
        t = float(ts) if ts else now
        cur = self.db.cursor()
        cur.execute(
            "INSERT INTO dialogue(mem_id,ts,role,text,source) VALUES(?,?,?,?,?)",
            (int(mem_id), t, "user", str(user_text), source))
        if reply_text:
            cur.execute(
                "INSERT INTO dialogue(mem_id,ts,role,text,source) VALUES(?,?,?,?,?)",
                (int(mem_id), t + 1, "assistant", str(reply_text), source))
        self.db.commit()
        return cur.lastrowid

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
        for p in (POOL_SHORT, POOL_MEDIUM, POOL_LONG,
                  POOL_ARCHIVE, POOL_DEEP):
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
