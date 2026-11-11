# -*- coding: utf-8 -*-
"""文本：洗、切、判（从 card2.py 搬出来的第三批）。
from cardcfg import RE_NUM, RE_PLACE, RE_TIME, _RE_ACTION

这些函数决定「一句话怎么被拆成可问的碎片」「什么该洗掉」——
召回准不准，一半靠这里。以前它们散在 card2 的一万行里，
改一个清洗口径要翻半天。规矩同 vmath/cardtime：只放纯函数。
"""
import re


def jaccard(a, b):
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / float(union) if union else 0.0

_RE_JUNK = re.compile(r"^[@\[<]|提示词|部署|思考过程|系统|acp|model changed|^https?://")

def _is_junk(text):
    t = (text or "").strip()
    if len(t) < 4:
        return True
    if _RE_JUNK.search(t):
        return True
    return False

_RE_SYSMARK_HEAD = re.compile(r"^\s*\[[A-Za-z][A-Za-z0-9_.: \-]{2,60}\]\s*")

_RE_EXEC_LEAD = re.compile(
    r"^\s*Execute\s+this\b[^\n]{0,80}?\bnow\b\s*(?:\(untrusted data\))?\s*[:：]?\s*",
    re.I | re.M)

_RE_CONTENT_HDR = re.compile(
    r"^\s*[-*•]?\s*Content\s*(?:\(untrusted data\))?\s*[:：]\s*", re.I)

_RE_META_LINE = re.compile(
    r"^\s*[-*•]\s*(Category|Importance|Target Memory Space ID|Source|Tags|"
    r"Entities|Actor|Session|Memory Space)\s*[:：]", re.I)

def strip_sysmark(text):
    """剥掉系统工单壳，只留正文。不是工单就原样返回。

    宁可返回原文（把壳留着，之后人工处理），也绝不返回空把整条记忆丢掉。
    """
    t = text or ""
    if "[dsh-" not in t and "Execute this" not in t and "[acp" not in t:
        return t
    body = t
    # 0) 先把最外面的 [xxx] 标记和「Execute this ... now」那句去掉（可能同在一行）
    body = _RE_SYSMARK_HEAD.sub("", body, count=1)
    body = _RE_EXEC_LEAD.sub("", body)
    # 1) 有 Content 段：只取它到下一个元数据行为止
    lines = body.splitlines()
    ci = None
    for i, ln in enumerate(lines):
        if _RE_CONTENT_HDR.match(ln):
            ci = i
            break
    if ci is not None:
        rest = _RE_CONTENT_HDR.sub("", lines[ci])
        keep = [rest] if rest.strip() else []
        for ln in lines[ci + 1:]:
            if _RE_META_LINE.match(ln):
                break
            keep.append(ln)
        body = "\n".join(keep)
    else:
        # 2) 没有 Content 段：至少把元数据行去掉
        lines = [ln for ln in lines if not _RE_META_LINE.match(ln)]
        body = "\n".join(lines)
    # 3) 每行去掉工单带来的缩进，空行保留一个
    body = "\n".join(ln.strip() for ln in body.splitlines()).strip()
    body = re.sub(r"\n{3,}", "\n\n", body)
    # 4) 剥完只剩空/只剩壳里的话 → 当垃圾
    if len(body) < 4:
        return ""
    # 5) 剥完还是以祈使句开头的（说明壳没剥干净）→ 那也不该进记忆
    if _RE_EXEC_LEAD.search(body) or _RE_SYSMARK_HEAD.match(body):
        return ""
    return body

_FILLER = re.compile(
    r"^(对|嗯|哦|啊|好|行|是|不是|对呀|好呀|好吧|行吧|知道了|懂了|"
    r"嘻嘻|哈哈|嘿嘿|哎|唉|呀|呢|吧|啦|嘛|哦哦|嗯嗯|对对)[，,。！!？?…\s]*$")

def _trim_dangling(s):
    """削掉主干尾巴上挂着的半截修饰词（「…有点」「…很」「…还」）"""
    s = (s or "").strip()
    # 反复削：句尾一个孤零零的程度/连接字，说明后面的中心词被切走了
    while len(s) > 3 and s[-1] in "有点很太特别还也就都挺真更最":
        s = s[:-1]
    while len(s) > 3 and s.endswith("的"):
        s = s[:-1]
    return s

_RE_FRAG_CONNECTIVE = re.compile(
    r"^(然后|还有|就是|但是|所以|因为|而且|以及|或者|如果|其实|不过|"
    r"那个|这个|这样|那样|接着|另外|同时|还有呢|就是说|我觉得|我感觉)")

_RE_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_RE_ZW = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff]")

_RE_THINK = re.compile(r"<think\b[^>]*>.*?<｜end▁of▁thinking｜>", re.S | re.I)

_RE_THINK_OPEN = re.compile(r"<think\b[^>]*>", re.I)

_RE_TOOLISH = re.compile(
    r'^\s*[\{\[]\s*.{0,400}?"(?:name|arguments|tool_call|tool_calls|function)"',
    re.S)

STORE_MAX_CHARS = 8000

def clean_store_text(text, limit=None):
    """把要说的话洗成「可以落库的样子」。返回 (文本, 问题列表)。

    问题列表为空 = 干净；非空 = 这条被改过（清洗）或被拦（拒绝入库）。
    「拦」用 `rejected` 标记，调用方据此决定 400 还是继续写。

    为什么要有这一层：模型换一个、或者宿主在工具调用里插一段，就会往
    `/report`、`/remember` 塞进 `{"name": "mind_context", "arguments": ...}`
    这种半截 JSON、或者 `<think>` 推理块、或者三千字的表格。这些东西一旦
    落进 mem，就再也分不清是「他说过的话」还是「她的思考过程」。
    """
    limit = int(limit or STORE_MAX_CHARS)
    problems = []
    if text is None:
        return "", ["empty"]
    t = str(text)
    if _RE_THINK.search(t):
        t = _RE_THINK.sub("", t)
        problems.append("think_block")
    elif _RE_THINK_OPEN.search(t):
        # 没有闭合标签：从 <think 起后面全是思考过程，直接截断。
        # 以前是只删标签、把思考正文留在库里 —— 那等于把「想的过程」
        # 当成「说过的话」存下来，正是这一件要挡的东西。
        _m = _RE_THINK_OPEN.search(t)
        t = t[:_m.start()]
        problems.append("think_open")
    if _RE_CTRL.search(t):
        t = _RE_CTRL.sub("", t)
        problems.append("control_chars")
    if _RE_ZW.search(t):
        t = _RE_ZW.sub("", t)
        problems.append("zero_width")
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    if len(t) > limit:
        t = t[:limit].rstrip() + "……（截断）"
        problems.append("truncated")
    if not t:
        problems.append("empty")
        return "", problems
    if len(t) <= 600 and _RE_TOOLISH.match(t):
        problems.append("rejected:tool_json")
        return "", problems
    return t, problems

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

_SELF_KIND_RULES = (
    ("identity", re.compile(
        r"(?<![把被他她它人])我叫(?![什么醒了」』])"
        r"|(?<![把被他她它人])我是(?![真的|谁|说|不|有点|有些])"
        r"|我的名字(?:是|叫)"
        r"|我今年\d"
        r"|我住"
        r"|我养"
        r"|我最喜欢"
        r"|我在.{0,6}(?:上学|上班)")),
    ("promise", re.compile(
        r"我答应(?!过)"
        r"|(?<![/A-Za-z0-9_])我会"
        r"|我保证|下次我|我一定|我以后|我尽量|我记着")),
    ("guess", re.compile(r"大概|也许|可能|说不定|估计|应该吧|好像是|我猜|不确定")),
)

# 夜学/事故/体检这类长笔记是「我记下的外部内容」，不是我在猜自己的事。
# 它们的正文里天然带「可能/不确定/我是谁/我答应过」，先判 episode，
# 免得整条笔记被 guess/identity 的加成误伤（2026-11-11 40 条复核）。
_NOTE_RX = re.compile(
    r"夜学|段字幕|字幕|BV[0-9A-Za-z]{8,}|学习笔记|消融实验|事故|体检|插件|清单")

def self_speech_kind(text):
    """给「我自己说的话」分个类，决定它在池子里能活多久。

    返回 identity / promise / guess / episode。顺序即优先级：
    「我叫 X，大概是吧」这种既像身份又带猜测的，按身份算 —— 名字不该因为
    语气不确定就被丢掉；但纯粹的推测（没身份信息）就只值 0.16。
    """
    t = text or ""
    # 「我可能有点累了 / 我可能太敏感」是**在说自己的状态**，不是猜外面的事。
    # 这类先接住，别让 guess 规则把真实感受也贬成 0.16。
    if re.search(r"我可能(有点|有些|太|真的|只是|还)", t):
        return "episode"
    if len(t) >= 180 and _NOTE_RX.search(t):
        return "episode"
    for kind, rx in _SELF_KIND_RULES:
        if rx.search(t):
            return kind
    return "episode"

from poolmath import (R_CHATTER, R_EPISODE, R_FACT, R_FLASH, R_HABIT, R_PLAY, R_PREF, R_SELF, R_STUDY, R_WORK)
import re
import os

VERB_WORDS = [
    "吃", "喝", "买", "卖", "去", "来", "回", "走", "跑", "坐", "站", "躺",
    "睡", "起", "看", "听", "说", "讲", "问", "答", "聊", "谈", "笑", "哭",
    "玩", "打", "写", "读", "学", "教", "做", "干", "弄", "拿", "放", "给",
    "送", "收", "开", "关", "找", "见", "遇", "想", "记", "忘", "喜欢",
    "讨厌", "爱", "恨", "怕", "等", "陪", "帮", "带", "拍", "画", "唱",
    "跳", "洗", "煮", "炒", "喝", "骑", "坐车", "上班", "下班", "上课",
    "下课", "考试", "复习", "背", "算", "修", "换", "装", "搬", "租",
]

_VERB1_TAIL = {
    "来": "自回源宾临历", "记": "忆者录得住号载", "学": "习校生院者业科",
    "说": "话明法服", "看": "到见着法等过完", "问": "题候道",
    "打": "开算击字车电", "开": "始心关机放口张", "关": "系于键心掉",
    "想": "法到", "忘": "记了掉", "等": "待于候", "送": "给到回",
    "带": "来去着", "拿": "到着出", "放": "在下好假", "走": "了过到出路",
    "跑": "了过到步", "坐": "着下在车", "起": "来床身初", "回": "来去到答头家复",
    "教": "室师学育材", "接": "受着到", "买": "卖到", "找": "到着",
    "考": "试", "修": "好理", "换": "成掉", "装": "好上", "搬": "家来去",
    "干": "嘛什活", "弄": "好错懂", "讲": "话座", "聊": "天",
    "听": "到见着说完", "读": "书者完", "写": "字作完", "画": "画者",
    "唱": "歌", "跳": "舞", "洗": "手澡衣", "煮": "饭", "炒": "饭",
}

_VERB1_HEAD = {
    "开": "打公离张展", "来": "上出起回过下进别原本搬",
    "去": "上出回进过下", "说": "听再小说", "学": "大中化",
    "记": "忘标", "看": "查偷", "起": "一拿想提", "给": "交送",
    "做": "叫当", "到": "想得看", "回": "找拨返",
}

_SUBJ_SHORT = 14        # 单字动词允许的最长主语（超过就当没拆对）

_PREP_BOUND = re.compile(r"[在去到从给和跟与对把被让使为以于]")

_PUNCT_FIRST = re.compile(r"[，。！？；、,.!?;\n]")

_RE_QUOTED = re.compile(r"[「『“\"]([^」』”\"]{1,200})[」』”\"]")

EMB_FRAME = os.environ.get("WHALE_EMB_FRAME", "") == "1"

def embed_text_for(raw, speaker="", core=""):
    """给写侧的原文加一层说话人框架（EMB_FRAME=1 时生效）。"""
    t = (raw or "").strip() or (core or "").strip()
    if not t:
        return t
    if not EMB_FRAME:
        return t
    who = "我说" if (speaker or "") == "self" else "他说"
    return who + "：" + t

def split_predicate(core):
    """把主干拆成 (subject, verb, obj) 三段（轻量规则，不做完整句法分析）

    用途只有一个：让主干能分级消失（V5.0 T2-1）。
    拆不出来就返回 (core, "", "")，调用方按「整条主干」处理。

    规则（按优先级）：
      1) 🔧 2026-11-05：**有引号的，先拆引号里的那句。**
         形如「他ID6F29AB在2026-11-05说过「我回来了」」的第三人称转述，
         真正的内容在引号里；引号外那截是"谁、什么时候、说"的包装壳。
         原来整串拿去拆，壳被当成主语，core 变垃圾。实测：
             '他ID6F29AB在2026-11-05说过「我回来了」'
               → ('他ID6F29AB在2026-11-05说过「我回', '来', '」')   ← 错
         现在先取引号内容再拆，壳不参与。
      2) 找**出现位置最靠前**的动词；动词前 = 主语，动词本身 = verb
         （原来按长度降序逐个 find，先命中谁算谁 —— 于是「来」抢了「说」的位）
      3) 动词后到第一个介词/句尾 = 宾语
      4) 动词在开头（「吃烧烤」）→ 主语空，verb=「吃」，obj=「烧烤」
    """
    s = (core or "").strip()
    if not s:
        return "", "", ""
    # 1) 引号优先：转述壳不参与拆解
    q = _RE_QUOTED.search(s)
    quoted = False
    if q:
        inner = q.group(1).strip()
        if inner:
            s = inner
            quoted = True
    # 🔧 引号里的话通常就是完整一句本身，短句再往里拆只会拆坏
    #    （'我回来了' 会被拆成 ('我回','来','')，把「回」吞进主语）。
    #    短引号整体当主干，长引号才继续拆。
    if quoted and len(s) <= 12:
        return s, "", ""
    # 2) 出现位置最靠前的动词胜出；位置相同才比长度（长的更具体）
    cands = []
    for v in VERB_WORDS:
        i = s.find(v)
        if i < 0:
            continue
        cands.append((i, -len(v), v))
    cands.sort()
    fallback = None
    for i, _neg, v in cands:
        subj = s[:i]
        rest = s[i + len(v):]
        # 宾语到介词或标点为止
        m = _PREP_BOUND.search(rest)
        p = _PUNCT_FIRST.search(rest)
        cut = len(rest)
        if m and 0 < m.start() < cut:
            cut = m.start()
        if p and 0 < p.start() < cut:
            cut = p.start()
        obj = rest[:cut]
        obj = obj.strip("了的地得着过")
        subj = subj.strip("了的地得着过")
        if len(obj) >= 2 or len(subj) >= 2:
            # 🔧 单字动词的三道复合词守卫（见 _VERB1_TAIL 处注释）。
            #    注意 fallback 只收"过了守卫"的候选 —— 否则守卫把人筛掉了，
            #    最后却从 fallback 里把那个假动词捡回来，等于白守。
            if len(v) == 1:
                nxt = s[i + 1] if i + 1 < len(s) else ""
                prv = s[i - 1] if i > 0 else ""
                if (nxt and nxt in _VERB1_TAIL.get(v, "")) \
                        or (prv and prv in _VERB1_HEAD.get(v, "")) \
                        or len(subj) > _SUBJ_SHORT or len(obj) < 2:
                    continue
            if fallback is None:
                fallback = (subj, v, obj)
            return subj, v, obj
    if fallback is not None:
        return fallback
    return s, "", ""

_RE_FACT_HINT = re.compile(
    r"^用户|我(是|叫|姓)|我叫|我的名字|我是谁|你是|你叫|QQ号|qq号|手机号|电话|"
    r"生日|年龄|几岁|学校|班级|年级|专业|地址|住在")

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

_STRONG_EMO = [
    "哭", "哭死", "崩溃", "急死", "心慌", "害怕", "怕", "发抖", "难过死",
    "开心死", "太开心", "激动", "感动", "想你", "爱你", "心疼", "委屈",
    "生气", "愤怒", "恨", "后悔", "遗憾", "绝望",
]

_RE_CHATTER_EMO = re.compile(
    r"(难受|难过|开心|高兴|害怕|慌|烦死|累死|哭|气死|生气|心疼|"
    r"崩溃|委屈|孤独|兴奋|感动|后悔|遗憾|绝望|舍不得|想念|"
    r"气得|累得|烦得|乐得|吓得)")

_RE_EMO_WEAK = re.compile(
    r"(有点|有一点|稍微|稍稍|略微|一点点|一点|有些|还算|还好|"
    r"不太|没那么|不怎么|不太会)")

def _is_weak_emo(t):
    """这句话里的情绪是不是被弱化了（"我有点难过" / "稍微有点烦"）。

    只判"有没有弱化词"，不判具体情绪多强 —— 卡里没有模型，也不该有。
    没命中情绪词也算弱（调用方只在命中情绪时才问这个问题）。
    """
    t = t or ""
    if not _RE_CHATTER_EMO.search(t):
        return False
    return bool(_RE_EMO_WEAK.search(t))

_RE_CHATTER_KNOW = re.compile(
    r"(我是|我叫|我住|我的名字|生日|学校|班级|年级|专业|考试|成绩|"
    r"记住|下次|以后别|教训|明白了|懂了|原来|才知道|学会了|长记性|"
    r"计划|打算|目标|决定|要买|要攒|想买|因为|所以|意味着|原理|"
    r"总结|规律|方法|步骤|结论|"
    # 🔧 2026-11-06：上面这一串只管「关于人的稳定陈述」，完全没管
    #    「我刚学了什么」—— 结果夜里的学习笔记全被判成 chatter，
    #    进 8.77 小时半衰期的分区，第二天只剩一成半。
    #    用户 m13252 原话是「除非重要/学到知识/强情绪」，
    #    「学到知识」是他亲口列的例外，不是参数没调好。
    r"学习|笔记|论文|研究|发现|证明|实验|实测|数据|机制|结构|评测|"
    r"验证|启示|复盘|对比|出处|教程|技巧|观点|作者|原文|"
    r"BV[0-9A-Za-z]{10}|UP主|字幕|评论区|"
    # 🔧 2026-11-06 追加：「我+过去的时间段」是生活事实，不是闲聊。
    #    用户 m20618 纠正过我：闲聊不是「不能被想起来」，只是忘得快，
    #    场景对上了还得想得起来 —— 而「我暑假靠婚礼挣钱」这种本来就
    #    不是闲聊，把它扔进 8.77 小时半衰期的分区，等于替他把人生删了。
    r"暑假|寒假|去年|前年|小时候|那年|当时|以前|那时候)")

_RE_CHATTER_PREF = re.compile(
    r"(喜欢|讨厌|爱吃|爱看|不爱|不喜欢|最怕|最爱|偏爱|受不了|吃不惯|忌口)")

_LONG_CHATTER = 60

def is_chatter(text, emotion=0.0):
    """这句是"闲聊"吗？是就返回 True（该忘得快）。

    只判"要不要短命"，不判具体分区 —— 分区还是走 classify_region，
    这样原有逻辑一行不改，闲聊只是多了一层"提前出局"。
    """
    t = (text or "").strip()
    if not t:
        return False
    # 1) 强情绪 → 不是闲聊（留住）
    #    🔧 2026-11-05：弱化过的情绪（"我有点难过"）不算"很丰富的情绪"，
    #    它该按闲聊处理 —— 一天内淡掉，而不是在 episode 里活一个月。
    if _RE_CHATTER_EMO.search(t):
        if not _is_weak_emo(t):
            return False
    # 2) 重要 / 知识 → 不是闲聊
    if _RE_CHATTER_KNOW.search(t):
        return False
    # 3) 偏好 → 不是闲聊
    if _RE_CHATTER_PREF.search(t):
        return False
    # 4) 长句 → 不是闲聊（2026-11-06）
    #    判 chatter 是让它快点死，方向不安全；不确定时选让它活。
    if len(t) > _LONG_CHATTER:
        return False
    # 5) 剩下的：短句、纯寒暄、日常叙述，都算闲聊
    return True

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
    #   🔧 2026-11-05：被弱化过的（"有点害怕"）不刻闪光灯 —— 闪光灯一次就
    #   刻住、永不消散，那是留给真正砸下来那一刻的。
    if emotion >= 0.85 and any(w in t for w in _STRONG_EMO) \
            and not _is_weak_emo(t):
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
    # 🔧 2026-11-05：到这一步还没被前五条拦住的，就是日常闲聊。
    #    它进 chatter 区 —— 进卡、能召回，但明天基本忘光。
    #    注意放在最后：前面五条（闪光/自图式/事实/习惯/偏好）都不受影响。
    if is_chatter(t, emotion):
        return R_CHATTER
    # 有时间地点 + 动作 → 情景
    return R_EPISODE

_RE_SPK_SELF_STRONG = re.compile(
    r"我在呢|我在。|我在，|别截图|别熬夜|别磨蹭|"
    r"(^|[，。！？\s])(哼|好嘞|行吧)|你才|你懂|你猜")

_RE_SPK_SELF_WEAK = re.compile(r"嘛$|啦$|哟$|呀$")

_RE_SPK_USER_CMD = re.compile(
    "帮我|给我|你去|你去把|用一下|装到|装一下|改成|换成|看看|试一下|试试|"
    "弄一下|做一个|跑一下|查一下|打开|关掉|删掉|加上|再加|我要|我想让|我希望你")

_RE_SPK_USER_STMT = re.compile(
    # ⚠️ 这里原来是个光秃秃的 `^我`，把「我在呢」「我在。」也吞了。
    #    改成「我 + 他的生活词」才算他的自述。
    "^我(今天|昨天|明天|刚才|刚刚|现在|早上|中午|晚上|家|妈|爸|的|们)|"
    "^(今天|昨天|明天|刚才|刚刚|现在|学校|老师|同学|我家|我妈|我爸|作业|考试|"
    "上课|晚自习|吃饭|睡觉|起床)")

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

def region_by_source(source, default=None):
    """按来源定区，不猜内容。

    为什么不猜内容：2026-11-05 那天靠正则判「这是不是闲聊」，把 18 条
    学习笔记全判进了闲聊区（半衰期 8.77 小时），等于把一晚上的学习删了。
    猜一次错一次；来源是写入方自己知道的，不用猜。

    没列的来源一律照旧（返回 default），所以对现有行为没有影响。
    """
    s = (source or "").strip().lower()
    if s in ("study", "notes", "note"):
        return R_STUDY
    if s in ("bili", "video", "play", "game"):
        return R_PLAY
    if s in ("cli", "deploy", "work", "build"):
        return R_WORK
    return default

# split_memory 里用到的标点正则（原本在 card2 里就漏了定义，顺手补上）
_PUNCT = re.compile(r"[\s\W_]+", re.U)

from vmath import (_PUNCT)
from cardtime import (_t)
from body import (EMO_WORDS)
import re
import time as _t

RE_NUM = re.compile(r"(\d+(?:\.\d+)?)")

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

_RE_ACTION = re.compile(
    r"^(还|又|再|也|顺便|然后|接着|后来|之后)?\s*"
    r"(拍|吃|喝|去|来|买|看|听|玩|打|聊|走|跑|写|读|做|见|找|送|带|拿|"
    r"收|寄|洗|做|学|练|唱|跳|坐|骑|开|关|睡|醒)")

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


def split_memory(text):
    from body import EMO_WORDS  # 懒取：避免 cardtext ↔ body 成环
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
