# -*- coding: utf-8 -*-
"""情绪 / 身体 / 离线时钟：从 card2 搬出来的纯模块。

拆门面第 5 批。这里的函数都不碰数据库、不读 self，只做数值与世界模型层面的计算，
所以可以被单测直接调用。"""
import math
import re

import padlib

from cardtime import DAY
import time

_POLLUTION_RULES = [
    (r"你(其实|根本|实际上)?(不是|并非).{0,12}(是|而是)", 0.55, "被要求改写自我", True),
    # 🔧 2026-11-06（体检修）：原来这条是
    #    (r"你(的)?名字(应该|其实|改成|是)", 0.45, "被要求改名", True)
    # 裸「是」让它对「你的名字是小鲸鱼」也判「被要求改名」—— 说的明明是她自己。
    # 名字对不对这种事，下面那段拿卡里的自我名字比对的收尾判断已经做得更准，
    # 这里只留「在下命令 / 在改」的语气，不再单独认「名字是」。
    (r"你(的)?名字(应该|其实|改成|必须)", 0.45, "被要求改名", True),
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
                # 🔧 2026-11-06（体检修）：原来写的是
                #    r"你(?:的名字)?(?:应该|其实)?(?:叫|是)…"
                # 那个裸 `是` 会把任何一句「你是…」都当成改名。真卡实测误伤 5 条：
                #    「你是我的骄傲我要上学」→ 抓成「你是我的骄傲我要上学」
                #    「你是否清醒」「你是想砸上跑道还是砸」「你是怎么走到那里的」
                #    「你是11月06日」
                # 误判的代价不是好不好看：trust 掉到 0.55 → 权重乘 0.55、
                # 不许晋升长期、召回再打对折（CONFLICT_RECALL_PENALTY）。
                # 所以只认「明确在介绍名字」的两种句式：
                #   ① 你的名字是/叫 X   ② 你叫 X（必须紧跟「叫」，不接受裸「是」）
                # 再把代词/量词/否定开头的一律当误匹配扔掉（"你是谁""你这个""是不是"）。
                m = re.search(
                    r"(?:你的名字(?:是|叫)|你(?:应该|其实)?叫)\s*[\"'“「]?"
                    r"([\u4e00-\u9fa5A-Za-z0-9]{1,8})", s)
                cand = (m.group(1) if m else "") or ""
                _BAD_HEAD = ("的", "个", "我", "你", "他", "她", "它", "们",
                             "这", "那", "哪", "谁", "不", "很", "太", "想",
                             "怎", "是", "有", "会", "能", "要", "把", "被",
                             "也", "还", "就", "都", "又", "在", "给", "让",
                             "一", "二", "三", "四", "五", "六", "七", "八",
                             "九", "十", "0", "1", "2", "3", "4", "5",
                             "6", "7", "8", "9")
                if cand and not cand.startswith(_BAD_HEAD) and cand not in name:
                    t -= 0.45
                    conflict = 1
                    why.append("与身份名称冲突（她叫%s）" % name)
        except Exception:
            pass
    return max(0.05, min(1.0, round(t, 3))), conflict, ";".join(why)

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

OFFLINE_POLICY = "freeze"

def offline_effective(gap_s, policy=None):
    """一段离线时间，折算成「有效衰老时间」（秒）。"""
    pol = policy or OFFLINE_POLICY
    gap = max(0.0, float(gap_s))
    if pol == "freeze":
        return 0.0
    return compress_offline_dt(gap)

EMO_WORDS = [
    "很开心", "开心", "高兴", "难受", "难过", "伤心", "累", "困", "饿",
    "生气", "害怕", "紧张", "激动", "委屈", "踏实", "宁静", "幸福",
    "哭了", "笑死", "绷不住", "舒服", "无聊", "烦",
]

ETA_OXY_UP = 0.10

ETA_OXY_DN = 0.30

OXY_INERTIA_MIN = 0.30      # r 的下限：越伤越难彻底断

OXY_INERTIA_MAX = 0.85      # r 的上限：一开始也不会毫无防备

OXY_INERTIA_BASE = 0.70     # r = clamp(oxy * 0.70, 0.30, 0.85)

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

K_MOOD = 0.20           # 单次事件的混合系数（2026-11-10 由 .12 调到 .20）

MOOD_SPAN = 0.35        # 软限：不管叠加多少，一次会话内 |mood-0.5| 不超过这个值

MOOD_MIN = 0.0

MOOD_MAX = 1.0

MOOD_NEUTRAL = 0.50     # 心境零点：0.5 是不悲不喜

K_MOOD_HOME = 0.10      # 每「一天」往 0.5 回 10%（.05 时追不上白天的推动）

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
    # 情绪词表在 2026-11-10 扩过一遍：原来只有二十来个词，
    # 「真好/厉害/哈哈/谢谢/焦虑/压力/孤独」这些最常见的说法一个都不认，
    # 于是绝大多数句子都被当成中性。
    NEG_WORDS = ("难受", "难过", "伤心", "累", "困", "生气", "害怕", "紧张",
                 "委屈", "哭了", "烦", "烦死", "无聊", "疼", "崩", "撑不住",
                 "糟糕", "没考好", "考砸", "失败", "吵架", "焦虑", "压力",
                 "孤独", "想哭", "丧", "不舒服", "头疼", "不想活")
    POS_WORDS = ("开心", "高兴", "舒服", "幸福", "踏实", "宁静", "笑死", "激动",
                 "爽", "好耶", "考好", "赢了", "喜欢", "真好", "厉害", "不错",
                 "哈哈", "嘿嘿", "太好了", "棒", "可爱", "温柔", "谢谢", "辛苦",
                 "爱你", "想你", "抱抱", "好玩", "有趣")
    neg_hits = sum(1 for w in NEG_WORDS if w in text)
    pos_hits = sum(1 for w in POS_WORDS if w in text)
    pos = up + pos_hits
    neg = dn + neg_hits
    if pos == 0 and neg == 0:
        # 一句没有正负信号的话，只轻擦一下（不是"把心境按回 0.5"）
        return MOOD_NEUTRAL + (0.0 if MOOD_SPAN <= 0 else 0.0)
    # 强度按「命中几个词」算，而不是只看调用方给的 emotion ——
    # 评价值对「难受/撑不住」给 1.0、对「你真好」只给 0.25，
    # 结果同样 0.32 的 amp，负向位移是正向两倍（负面比正面敏感就是这么来的）。
    amp = (0.16 + 0.05 * min(pos + neg, 4)) * (0.9 + 0.2 * max(0.0, min(emotion, 1.0)))
    if pos > neg:
        return min(1.0, MOOD_NEUTRAL + amp * 1.10)
    if neg > pos:
        return max(0.0, MOOD_NEUTRAL - amp * 0.80)
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
    (0.00, "很低落"),
    (0.20, "有点闷"),
    (0.38, "平静"),
    (0.56, "心情不错"),
    (0.74, "挺开心"),
    (0.90, "特别高兴"),
]

def mood_text(mood):
    """把 0~1 的心境翻成一句人话，塞进上下文用"""
    try:
        m = float(mood)
    except Exception:
        m = MOOD_NEUTRAL
    label = MOOD_LABEL[0][1]
    for lo, name in MOOD_LABEL:
        if m >= lo:
            label = name
        else:
            break
    return label

def pad_label(p, a):
    """给 P/A 两个轴起个中文名（实现见 padlib）。"""
    return padlib.pad_label(p, a)
