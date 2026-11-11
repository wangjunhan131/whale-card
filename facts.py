# -*- coding: utf-8 -*-
"""facts.py — 写入侧事实抽取（谁说的 / 说的是谁 / 什么事 / 现在什么状态）

为什么要有这个文件：第三方评测里最弱的三块是
  1) 「别人的事记到用户头上」——用户说「我妈生日是三月五号」，我们把它记成"用户的记忆"，
     下次问「我生日」时会把他妈的生日端上来；
  2) 「分散在不同聊天里的线索拼不起来」——同一个人/同一件事散在几十条记忆里，
     检索只会按句子相似度捞，捞不到「把这几条拼起来」；
  3) 45 天档——远期的原话早已被时间磨掉，但「他妈生日三月五号」这种原子事实不该跟着烂。

V5.8（2026-11-08）重做抽取：
  · 原来只认 14 个谓词、只认亲属词做主体，700 条记忆只抽出 13 条事实（覆盖率 1/6），
    而且抽出来的还多是「('', '叫', '哪几个字')」这种废条。
  · 现在：主体认 **专名**（百家姓 + 2~4 字）+ 亲属 + 我/你/他；谓词扩到 40 多条；
    客体长度放到 24 字并保留日期数字。
  · 加「状态覆盖」：约定/欠账/接送/单价这类**会变**的事，新一条自动把旧的标成 superseded，
    问的时候只端最新状态 —— 治「已经还了一件却说还欠三件」。

做法（全部确定性规则，不调模型，可解释）：
  · 写记忆时顺手抽一层「事实」：主体（谁）· 谓词（什么事）· 客体（什么）· 说话人 · 时间 · 出处 mem_id
  · 事实只是索引，不改写原文；原文仍在 mem.raw，随时能回去看
  · 抽不出就抽不出，绝不编（返回空列表比返回错的好）
"""

import os
import re
import sqlite3
import time

# ---------- 主体识别 ----------

# 亲属/关系词 → 规范名（第三方）
_OTHER_SUBJECTS = {
    "我妈": "妈", "我妈妈": "妈", "我母亲": "妈", "母亲": "妈",
    "我爸": "爸", "我爸爸": "爸", "我父亲": "爸", "父亲": "爸",
    "我哥": "哥", "我哥哥": "哥", "我姐": "姐", "我姐姐": "姐",
    "我弟": "弟", "我弟弟": "弟", "我妹": "妹", "我妹妹": "妹",
    "我爷爷": "爷爷", "我奶奶": "奶奶", "我姥姥": "姥姥", "我姥爷": "姥爷",
    "我外公": "外公", "我外婆": "外婆",
    "我朋友": "朋友", "我同学": "同学", "我同事": "同事",
    "我老师": "老师", "我室友": "室友", "我同桌": "同桌", "我老板": "老板",
    "同事": "同事", "同学": "同学", "朋友": "朋友", "老师": "老师",
    "我妈我爸": "爸妈",
}
_SELF_SUBJECTS = ("你", "小鲸鱼", "小鲸鱼", "小鲸")
_USER_SUBJECTS = ("我", "我本人", "我自己")

# 百家姓（常用部分）—— 用来认「叶楠」「梁志远」「沈知夏」这类专名
_SURNAMES = set(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐"
    "费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄"
    "和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁"
    "杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍"
    "虞万支柯昝管卢莫经房裘缪干解应宗丁宣邓郁单杭洪包诸左石崔吉龚程邢"
    "裴陆荣翁荀羊於惠甄曲家封芮羿储靳汲邴糜松井段富巫乌焦巴弓牧隗山谷"
    "车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘钭厉戎祖武符刘景詹束龙叶幸司韶"
    "郜黎蓟薄印宿白怀蒲邰从鄂索咸籍赖卓蔺屠蒙池乔阴胥能苍双闻莘党翟谭"
    "贡劳逄姬申扶堵冉宰郦雍却璩桑桂濮牛寿通边扈燕冀郏浦尚农温别庄晏柴"
    "瞿阎充慕连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡国文寇广"
    "禄阙东欧殳沃利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚那简饶空曾毋沙乜养"
    "鞠须丰巢关蒯相查后荆红游竺权逯盖益桓公"
)
_NAME_STOP = set("我你他她们的了是在有和就都也很还这那个什么谁哪怎么时候说过话上下前后里外")

# ---------- 谓词表 ----------
# 顺序有意义：先长后短、先具体后笼统。一小句只取第一条命中的。
_PREDICATES = [
    # 约定 / 欠账 / 安排（会变的状态）
    (r"(?:还欠|欠着|欠下|欠了|欠)", "欠账"),
    (r"(?:还了|还给|归还|补上了|补上|还给他|还给她)", "归还"),
    (r"(?:改成|改到|推迟到|提前到|定在|安排在|约在|说好|约好|讲好|答应)", "约定"),
    (r"(?:接(?:送)?|接送)", "接送"),
    (r"(?:值日|值班|夜班|倒班|排班|加班|上班|下班|放学)", "排班"),
    # 钱 / 货
    (r"(?:单价|价格|价钱|报价|多少钱|花了|一共)", "单价"),
    (r"(?:订了|下了单|买了一|买了|入手|下单)", "买"),
    (r"(?:送了|寄了|给了|借了|借给|借走)", "送"),
    (r"(?:想借|要借|借我的|借用|借相机|借书)", "借"),
    # 基本信息
    (r"生日(?:是|在|为)?", "生日"),
    (r"(?:今年|年纪|年龄)[^\d]{0,2}(\d{1,3})", "年龄"),
    # 「今年二十四」「二十四岁」——中文数字不认的话，长档里那条年龄永远抽不出来
    (r"(?:今年|年纪|年龄)?([一二三四五六七八九十〇零两]{1,4})岁", "年龄"),
    # 「今年二十四，比我大三岁」——数字后面不一定跟「岁」，后面是逗号也得收
    (r"(?:今年|年纪|年龄)[^\u4e00-\u9fa5\d]{0,2}([一二三四五六七八九十〇零两]{1,4})(?=[，。,！？\s]|$)", "年龄"),
    (r"(?:今年?)?(\d{1,2})\s*岁", "年龄"),
    (r"(?:住在|家住|家在|老家在|搬到|搬家)", "住处"),
    (r"(?:在|于)([\u4e00-\u9fa5A-Za-z0-9]{2,12})(?:上班|工作|读书|上学|念书|开店)", "在哪"),
    (r"(?:读|上|念)(高[一二三]|初[一二三]|大[一二三四]|\d{1,2}年级)", "年级"),
    (r"(?:叫|名叫|名字是|昵称是|全名)", "叫"),
    (r"(?:考试|模考|月考|期末|期中|高考|中考)", "考试"),
    # 状态 / 身体 / 事件
    (r"(?:生病|发烧|感冒|咳嗽|住院|看病|疼|过敏|腰不好|腰疼|腰疼|腰椎|颈椎|胃不好|胃疼|血压)", "身体"),
    (r"(?:修好|修了|坏了|退掉|退货|丢了|弄丢|找回来)", "物件状态"),
    (r"(?:请假|辞职|出差|调岗|入职|面试)", "工作变动"),
    # 偏好 / 习惯
    (r"(?:喜欢|爱吃|爱喝|爱看|爱听|迷上了|粉上了|习惯)", "喜欢"),
    (r"(?:讨厌|不喜欢|受不了|反感|不吃|忌口)", "讨厌"),
    (r"(?:养了|养着|捡了|喂了)", "养"),
    (r"(?:打算|计划|准备|想要|想去|要讲|要还|要交)", "打算"),
    (r"(?:作业|功课|卷子)", "作业"),
]

# 会随时间被新状态覆盖的谓词：只认「同一件事」能被结算的那种（欠账 → 归还、单价谈定）。
# 别把排班/约定也放进来：它们每次说的都是不同的事，按谓词一律覆盖会误伤。
_STATE_PREDS = {"欠账", "归还", "单价"}

# 🔧 V5.9：抽取用的正则太「挑剔」——问句几乎不会原样重复它（问「多大年纪」不会说
#    「今年24」）。所以问句这边单独列一份「怎么问到这个谓词」的词表，两边各走各的。
_Q_PRED_KW = {
    "年龄": ("多大", "几岁", "多少岁", "年纪", "年龄", "岁数"),
    "身体": ("身体", "腰", "不舒服", "难受", "生病", "病", "疼", "忌口",
             "血压", "胃", "过敏", "住院", "颈椎"),
    "生日": ("生日",),
    "住处": ("住哪", "住哪儿", "住在", "老家"),
    "在哪": ("在哪", "在哪儿", "上班在哪", "工作在哪"),
    "接送": ("接", "送"),
    "排班": ("班", "值日", "夜班", "倒班", "加班"),
    "欠账": ("欠",),
    "归还": ("还了", "还给", "归还", "还回去"),
    "单价": ("价", "多少钱", "单价", "报价"),
    "喜欢": ("喜欢", "爱吃", "爱听", "爱看", "爱喝"),
    "讨厌": ("讨厌", "不喜欢", "受不了", "忌口", "不吃", "反感"),
    "打算": ("打算", "计划", "准备", "想要"),
    "承诺": ("答应", "承诺", "说过什么", "你答应"),
    "约定": ("约好", "说好", "定在", "改到", "约"),
    "叫": ("叫什么", "名字", "全名", "昵称"),
    "年级": ("几年级", "年级", "高几", "初几"),
    "考试": ("考试", "模考", "月考", "期末", "期中"),
    "养": ("养", "猫", "狗"),
    "作业": ("作业", "功课", "卷子"),
    "买": ("买", "下单", "订"),
    "物件状态": ("修好", "坏", "丢了", "退"),
    "工作变动": ("辞职", "请假", "出差", "调岗", "面试", "入职"),
}

_CN_DIGIT = {"〇": 0, "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _cn2num(s):
    """二十四 → 24，五 → 5，十二 → 12。只处理 1~99，年龄和日子够用了。"""
    if not s:
        return None
    if "十" not in s:
        return _CN_DIGIT.get(s) if len(s) == 1 else None
    if s.count("十") != 1:
        return None
    a, b = s.split("十")
    if a and a not in _CN_DIGIT:
        return None
    if b and b not in _CN_DIGIT:
        return None
    return (_CN_DIGIT.get(a, 1) if a else 1) * 10 + (_CN_DIGIT.get(b, 0) if b else 0)


def _q_grams(text, n=2):
    t = _PUNCT.sub("", text or "")
    if len(t) < n:
        return set(t) if t else set()
    return {t[i:i + n] for i in range(len(t) - n + 1)}

_CL_SENT = re.compile(r"[。！？!?；;\n]+")
_CL_CLAUSE = re.compile(r"[，,、：:]+")
_PUNCT = re.compile(r"[\s，。！？、；：…~—\-（）()《》·「」【】\"'’“”]+")
_BAD_OBJECT = re.compile(r"(吗|呢|吧|就行|啥子|怎样|怎么|好吗|行吗|时间|搞清|告诉你|算了|哈)")
_STRIP_LEAD = re.compile(r"^(已经|都|也|还|又|就|先|再)+")
_STRIP_TAIL = re.compile(r"(已经|都|也|还|又|就)$")
_STATE_HINT = ("还欠", "欠", "已经", "上一件", "下一件", "还有", "我们")
_NAME_TAIL = re.compile(r"^[\u4e00-\u9fa5A-Za-z·]{2,6}$")
_DATE = re.compile(r"(\d{1,2}月\d{1,2}日|\d{1,2}月\d{1,2}号|周[一二三四五六日天]|星期[一二三四五六日天]|明天|后天|下周[一二三四五六日天]?|昨天|今天)")
# 问句在问「哪两天/什么时候/几号」的时候，宾语里带日子的事实才是答案
_TIME_Q = re.compile(r"(哪(?:两|几)?天|哪(?:一)?(?:日|号)|什么时候|啥时候|多久|几点|周几|星期几)")
_TIME_OBJ = re.compile(r"(周[一二三四五六日天]|星期[一二三四五六日天]|\d{1,2}\s*[月号日]|\d)")


def _clean(s):
    return _PUNCT.sub("", s or "")


def _is_name(tok):
    """2~4 个汉字、首字是姓、不是虚字 —— 当一个专名。"""
    if not (2 <= len(tok) <= 4):
        return False
    if tok[0] not in _SURNAMES:
        return False
    if any(c in _NAME_STOP for c in tok):
        return False
    # 🔴 2026-11-08：光看「首字是姓」还会放过「钟到校」「米米周三」这种
    #    分词切出来的假名字。凡是夹着虚字/时间字的，一律不当人名。
    if any(c in "到在了的是和与就要把被给还也都才又周号" for c in tok):
        return False
    # 问句里「沈知夏多大年纪」这种，四字滑窗会切出「沈知夏多」——
    # 结尾挂个疑问/程度字，多半是切出来的，不是名字。
    if tok[-1] in "多哪些怎甚啥几什吗呢吧啊不用赶过":
        return False
    return all("\u4e00" <= c <= "\u9fa5" for c in tok)


_PERSON_VERB = ("想来要把给说在是还接借发欠约叫改送买养帮问让拿找谈聊倒值"
                # V6.0：光认上面这些动词，会把「她考了/他住在/我给他打」里的
                # 主语整条丢掉——而这类句子恰恰是第三方事实的主产地。
                "去到回打考学住吃喝睡起做干挣花用看听玩走跑骑开抽戒病疼累忙"
                "忘记等陪带教骂夸嫌怕爱收洗卖存交租搬换修遛喂抱哄管盯催劝怪"
                "谢烦气穿戴饿渴")
_NAME_NEXT_STOP = "的了是和就都也吗呢吧啊呀哦嗯"
_NAME_NEXT_OK = set(_PERSON_VERB) | set("今年明昨刚正已都还也要本上这那每下把给")
_DATE_PREFIX = re.compile(r"^(周|星期|第|\d|今|明|昨|上|下|这|那)")


def _name_like(tok):
    """专名 / 小X / 阿X / 老X / 叠字（米米）都算人名；「周二」「第三」这类日期不算。"""
    if _DATE_PREFIX.match(tok):
        return False
    if _is_name(tok):
        return True
    if re.fullmatch(r"(小|阿|老)[\u4e00-\u9fa5]{1,2}", tok):
        return True
    if len(tok) == 2 and tok[0] == tok[1]:
        return True
    return False


def _persons(text):
    """句子里所有「人」的位置：[(start, end, kind, name)]。"""

    spans = []

    def _covered(i, j):
        return any(s <= i and j <= e for s, e, _, _ in spans)

    for word in sorted(_OTHER_SUBJECTS, key=len, reverse=True):
        for m in re.finditer(re.escape(word), text):
            spans.append((m.start(), m.end(), "other", _OTHER_SUBJECTS[word]))

    n = len(text)
    i = 0
    while i < n:
        if any(s <= i < e for s, e, _, _ in spans):
            i += 1
            continue
        picked = None
        # 短的先试：先「叶楠」再「叶楠想借」——长的容易把动词一起吃进去
        for L in (2, 3, 4):
            tok = text[i:i + L]
            if len(tok) < L or not _name_like(tok) or _covered(i, i + L):
                continue
            nxt = text[i + L:i + L + 1]
            if nxt and nxt not in _NAME_NEXT_OK:
                continue
            picked = (i, i + L, "other", tok)
            break
        if picked:
            spans.append(picked)
            i = picked[1]
        else:
            i += 1

    for m in re.finditer(r"[我你他她]", text):
        if any(s <= m.start() < e for s, e, _, _ in spans):
            continue
        if m.group(0) in "他她" and m.start() > 0 and text[m.start() - 1] == "其":
            continue
        nxt = text[m.end():m.end() + 1]
        # 「我妈」「你的」里的代词是定语；「我接」「她说」才是主语
        if nxt in _NAME_NEXT_OK or nxt == "":
            kind = {"我": "user", "你": "self"}.get(m.group(0), "other")
            spans.append((m.start(), m.end(), kind, ""))
    spans.sort()
    return spans


def _object_ok(pred, obj):
    """客体质量闸：宁可抽不出，也不要把「他修车那手才叫稳」当成事实。"""
    if not obj or len(obj.strip()) < 2:
        return False
    if obj.lstrip()[0] in "了的前后得要把在还也就都这那":
        return False
    if re.search(r"(提醒|记着|说好要|别忘了|哈哈)", obj):
        return False
    if len(obj) > 24:
        return False
    if _BAD_OBJECT.search(obj):
        return False
    if pred == "叫" and not _NAME_TAIL.match(obj):
        return False
    # 只有「了/的/是」这种光杆尾巴的，不要
    if _clean(obj) in ("了", "的", "是", "了。", ""):
        return False
    return True


def extract(text, ts=None):
    """从一段话里抽事实。返回 [dict]；抽不出返回 []。

    主语不必在句首：谓语前面最近的那个人就是主语（「周二周四梁志远接」→ 梁志远）。
    主语还可以从上一小句继承；实在没有主语的「状态类」句子（还欠三件 / 上一件已经还了）
    默认算在他头上 —— 因为是他在说话。
    """
    out = []
    if not text or len(text) < 4:
        return out
    for sent in _CL_SENT.split(text):
        sent = sent.strip()
        if len(sent) < 4:
            continue
        sent_persons = _persons(_clean(sent))
        sent_subj = sent_persons[0] if sent_persons else None
        for clause in _CL_CLAUSE.split(sent):
            clause = _clean(clause)
            if len(clause) < 3:
                continue
            persons = _persons(clause)
            for pat, pred in _PREDICATES:
                emitted = False
                for m in re.finditer(pat, clause):
                    before = [p for p in persons if p[1] <= m.start()]
                    subj = before[-1] if before else None
                    if subj is None and sent_subj is not None and sent_subj[1] <= m.start() and pred in _STATE_PREDS:
                        subj = sent_subj
                    if subj is None and (pred in _STATE_PREDS or clause.startswith(_STATE_HINT)):
                        subj = (0, 0, "user", "")
                    # 偏好/身体/作息这种，句子里没出现主语也基本是在说他自己
                    #（「不吃芹菜」「腰不好」），别整句丢掉。
                    if subj is None and pred in ("喜欢", "讨厌", "养", "打算",
                                                 "身体", "作业", "考试", "排班",
                                                 "生日", "住处"):
                        subj = (0, 0, "user", "")
                    # 岁数这条最容易串：「米米十二岁」「她十九岁」都别记成他。
                    if subj is None and pred == "年龄":
                        mm = re.match(r"([\u4e00-\u9fa5]{2,3}?)(?:今年|才|已经|刚)?"
                                      r"(?:[一二三四五六七八九十]{1,3}|\d{1,3})岁", clause)
                        if mm and mm.group(1) not in ("我们", "咱们", "我"):
                            subj = (0, 0, "other", mm.group(1))
                        elif re.match(r"[她他]", clause):
                            subj = (0, 0, "other", "")
                        elif "我" in clause or "咱" in clause:
                            subj = (0, 0, "user", "")
                    if subj is None:
                        continue
                    tail = clause[m.end():].strip()
                    head = clause[:subj[0]].strip()      # 主语前面那段（「周二周四」）
                    between = clause[subj[1]:m.start()].strip()
                    obj = tail
                    if not obj or len(obj.strip()) < 2:
                        obj = between or head
                    obj = _STRIP_TAIL.sub("", _STRIP_LEAD.sub("", obj)) if obj else obj
                    if pred == "生日":
                        obj = obj.lstrip("是")
                    if pred == "在哪":
                        obj = (m.group(1) if m.groups() else "") + obj
                    if pred == "年级":
                        obj = m.group(1) if m.groups() else obj
                    if pred == "年龄":
                        # 「比我大三岁」「小我两岁」是相对年龄，不是他的岁数
                        if re.search(r"比[他她你我]?[大小]|[大小][他她我]", clause):
                            continue
                        g = (m.group(1) if m.groups() else "") or ""
                        if g and not re.search(r"\d", g):
                            n = _cn2num(g)
                            if n:
                                obj = "%d岁" % n
                            else:
                                obj = (g or obj) + "岁"
                        else:
                            obj = (g or obj) + "岁"
                    # 「我发烧了」「腰不好」这种，谓语后面没有宾语，光剩个「了」；
                    # 状态类就直接拿谓词那几个字本身当内容，别丢掉。
                    if (not obj or not _object_ok(pred, obj)) and pred in (
                            "身体", "物件状态", "工作变动", "排班"):
                        obj = clause[m.start():m.start() + 10].strip()
                    if not obj or not _object_ok(pred, obj):
                        continue  # 这一处说法不干净，换下一处匹配，别整句放弃
                    out.append({
                        "subject_kind": subj[2],
                        "subject": subj[3],
                        "predicate": pred,
                        "object": obj[:80],
                        "ts": ts if ts is not None else time.time(),
                    })
                    # 「我叫沈知夏，知了的知，夏天的夏，24」——岁数是个光秃秃的数字，
                    # 没有「岁」字可抓。既然这一句已经在自报姓名了，顺手的数字就是年龄。
                    if pred == "叫":
                        # 数字常在下一小句（「我叫沈知夏，…，24」），所以在整句里找
                        mm = (re.search(r"[，,]\s*(\d{1,3})\s*(?:[，,。]|$)", sent)
                              or re.search(r"(\d{1,3})\s*$", sent))
                        if mm:
                            out.append({
                                "subject_kind": subj[2],
                                "subject": subj[3],
                                "predicate": "年龄",
                                "object": mm.group(1) + "岁",
                                "ts": ts if ts is not None else time.time(),
                            })
                    emitted = True
                    break
                if emitted:
                    break  # 一小句只算一个谓词，避免「喜欢」和「讨厌」都中
    return out


# ---------- 永久区的内容边界（V5.9，2026-11-08 定调）----------------------
# 不衰减的那一层只收「以后还会被问、而且答错会出事」的结构化事实；
# 其它一律留在事件记忆里，让它按衰减曲线自然淡掉。
# 判据：①说清楚是谁的 ②换个说法也还是同一件事 ③答错了会真的伤到人
_CATEGORY = {
    "叫": ("identity", 1), "年龄": ("identity", 1), "生日": ("identity", 1),
    "住处": ("identity", 1), "年级": ("identity", 1),
    "喜欢": ("preference", 1), "讨厌": ("preference", 1),
    "身体": ("health", 1),
    "欠账": ("obligation", 1), "归还": ("obligation", 1),
    "约定": ("commitment", 1),
    "接送": ("schedule", 1), "排班": ("schedule", 1), "考试": ("schedule", 1),
    "单价": ("transaction", 1), "买": ("transaction", 1),
    "物件状态": ("possession", 1),
    "打算": ("plan", 1), "工作变动": ("plan", 1),
    # 今天在哪、作业写没写——过两天就没意义了，别进永久区
    "在哪": ("transient", 0), "作业": ("transient", 0),
}

# 转述/猜测不落户：她说可能、我听说是、大概也许——这类写进永久区就是给未来埋错。
_HEARSAY = re.compile(r"(听说|据说|可能|也许|好像|大概|不确定|是不是|说是)")

# 再收一道：真正允许进永久区的谓词。接送、排班、单价、约定、打算这些时间性太强，
# 换天就过期，留在事件记忆里让它自己褪掉反而更诚实。
_PERMANENT_OK = {"叫", "年龄", "生日"}

# 2026-11-09：我自己写的工程记录不算「生活」。
#    这些字眼出现在原文里，基本就是当天施工/评测的汇报，抽出来的三元组
#    一个都不该进永久区（实测混进过「讨厌｜多少」「接送｜替了她对面」）。
_WORKLOG = re.compile(r"施工|测试|评测|部署|提交|回归|编译|推送|跑分|自测|bench|probe|packet|review", re.I)
# 只有「人和人真说过的话」才配不衰减：空来源（老数据/manual）、QQ 私聊/群聊、chat、cli、日记
_LIFE_SOURCE = re.compile(r"^(|qq.*|chat|cli|diary)$", re.I)


def _perm_ok(it, text, source):
    pred = it.get("predicate")
    obj = (it.get("object") or "").strip()
    if not _CATEGORY.get(pred, ("other", 0))[1]:
        return False
    if pred not in _PERMANENT_OK:
        return False
    if _HEARSAY.search(text or "") or not _source_ok(source):
        return False
    if _WORKLOG.search(text or ""):
        return False
    if not _LIFE_SOURCE.match((source or "").strip()):
        return False
    if not (2 <= len(obj) <= 10):
        return False
    if any(ch in obj for ch in "的了是我你他她它在把给被就都还得了着吧呢啊嘛呀"):
        return False
    if len(text or "") > 90:
        return False
    if obj[0] in "替进这上端把让给被不没都很就到":
        return False
    if it.get("subject_kind") == "other" and not (it.get("subject") or "").strip():
        return False
    if not re.search(r"[\u4e00-\u9fa5]", obj):
        return False
    return True


# 哪些来源配进永久区：人和人的对话（QQ/DSH/正常聊天）。
# 施工汇报、评测卡、群聊扫描是「记录」不是「生活」，抽出来的三元组一旦永久化，
# 以后每次问「我讨厌什么」都可能把「45天档掉了」端上来。
# 我自己说过的话：答应、说好、下次讲——这些是「欠他的」，跟他说的话一样要能查。
_SELF_COMMIT = re.compile(r"(答应|说好|承诺|下次讲|明天给你|会跟你说|改天)")


_NON_LIFE_SOURCE = re.compile(r"(report|bench|probe|scan|digest|^log)", re.I)


def _source_ok(source):
    return not _NON_LIFE_SOURCE.search(source or "")


# ---------- 存储 ----------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fact(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mem_id INTEGER,
    subject_kind TEXT,      -- user / other / self / unknown
    subject TEXT,           -- 第三方时的名字（妈/爸/同学/专名…）
    predicate TEXT,
    object TEXT,
    speaker TEXT,           -- user / self
    ts REAL,
    text TEXT,              -- 原句（截断），方便回看
    status TEXT DEFAULT 'active',   -- active / superseded
    category TEXT,          -- identity / preference / health / obligation …
    permanent INTEGER DEFAULT 1     -- 1 = 不衰减的永久区
);
CREATE INDEX IF NOT EXISTS idx_fact_ts ON fact(ts);
CREATE INDEX IF NOT EXISTS idx_fact_subject ON fact(subject_kind, subject);
CREATE INDEX IF NOT EXISTS idx_fact_pred ON fact(predicate);
CREATE TABLE IF NOT EXISTS fact_name(name TEXT PRIMARY KEY, n INTEGER DEFAULT 0);
"""


def ensure(db):
    c = sqlite3.connect(db, timeout=30.0)
    try:
        c.executescript(_SCHEMA)
        # 老卡没有 status 列：补上（幂等）
        cols = {r[1] for r in c.execute("PRAGMA table_info(fact)").fetchall()}
        if "status" not in cols:
            c.execute("ALTER TABLE fact ADD COLUMN status TEXT DEFAULT 'active'")
        if "category" not in cols:
            c.execute("ALTER TABLE fact ADD COLUMN category TEXT")
        if "permanent" not in cols:
            c.execute("ALTER TABLE fact ADD COLUMN permanent INTEGER DEFAULT 1")
        c.commit()
    finally:
        c.close()


def _common_run(a, b):
    """两个宾语里最长公共片段的长度（2~6 字扫一遍）。

    还账的时候要判断「还的是哪一件」：句子层面「还了」和「欠着」几乎不像，
    但宾语里那两三个字（关东煮、两包辣油、夜班见闻）是能对上的。
    """
    a, b = a or "", b or ""
    for n in range(6, 1, -1):
        for i in range(0, max(0, len(a) - n) + 1):
            if a[i:i + n] in b:
                return n
    return 0


def save(db, mem_id, items, speaker="user", text="", source=""):
    """把抽出来的事实写进表。返回写入条数。

    「会变的状态」谓词：同一主体同一谓词有了新的一条，旧的自动标 superseded。
    但**还账不能整条覆盖**：欠三件、还了一件，如果直接把「欠账」全标旧，
    等于把剩下两件也一起抹了（评测里就是这么错的——答成「一件未讲」）。
    所以先看宾语对不对得上：对得上的是那一条，对不上就在欠账后面记「（已还：…）」。
    """
    if not items:
        return 0
    c = sqlite3.connect(db, timeout=30.0)
    try:
        n = 0
        for it in items:
            # 专名门槛：两字名很容易是分词切错的碎词（「钟去」「别到」），要见第二回才认；
            # 三字以上的名字（梁志远、沈知夏、罗杏子）歧义小，第一回就让进。
            if (it["subject_kind"] == "other" and it.get("subject")
                    and it["subject"] not in _OTHER_SUBJECTS.values()):
                c.execute("INSERT INTO fact_name(name,n) VALUES(?,1)"
                          " ON CONFLICT(name) DO UPDATE SET n=n+1", (it["subject"],))
                seen = c.execute("SELECT n FROM fact_name WHERE name=?", (it["subject"],)).fetchone()[0]
                if seen < 2 and len(it["subject"]) < 3:
                    continue
            # 结算关系：还了一条 → 记到对应的那条欠账后面（**不整条标旧**）。
            #
            # 为什么不用 supersede：欠三件、还一件，把整条欠账标旧等于把剩下两件
            # 也一起抹了（评测里就是答成「一件未讲」）。所以只在宾语后面追加
            # 「（已还：…）」，剩下的件数仍然留在眼前。
            if it["predicate"] == "归还":
                debts = c.execute(
                    "SELECT rowid, COALESCE(object,'') FROM fact"
                    " WHERE subject_kind=? AND COALESCE(subject,'')=? AND predicate='欠账'"
                    "   AND COALESCE(status,'active')='active'",
                    (it["subject_kind"], it.get("subject") or ""),
                ).fetchall()
                best, hit = 0, (debts[0] if debts else None)
                for _rid, _obj in debts:
                    _run = _common_run(_obj, it["object"])
                    if _run > best:
                        best, hit = _run, (_rid, _obj)
                _note = (it["object"] or "")[:24]
                # 「等休息日再」「明天吧」「04月15日」这些都是改期/日期，不是还了东西；
                # 当成已还记下去会把欠账越抹越乱。
                if (not _note or re.match(r"^(等|再|下次|明天|后天|以后|改天)", _note)
                        or re.fullmatch(r"[\d年月日号\s:：\-—]+", _note)):
                    continue
                if hit and _note not in hit[1]:
                    c.execute("UPDATE fact SET object=? WHERE rowid=?",
                              ((hit[1] + "（已还：" + _note + "）")[:80], hit[0]))
            elif it["predicate"] == "单价":
                c.execute(
                    "UPDATE fact SET status='superseded'"
                    " WHERE subject_kind=? AND COALESCE(subject,'')=? AND predicate='单价'"
                    "   AND COALESCE(status,'active')='active'",
                    (it["subject_kind"], it.get("subject") or ""),
                )
            c.execute(
                "INSERT INTO fact(mem_id,subject_kind,subject,predicate,object,speaker,ts,text,status,"
                "category,permanent) VALUES(?,?,?,?,?,?,?,?,'active',?,?)",
                (mem_id, it["subject_kind"], it.get("subject") or "", it["predicate"],
                 it["object"], speaker, it["ts"], (text or "")[:160],
                 _CATEGORY.get(it["predicate"], ("other", 0))[0],
                 (1 if _perm_ok(it, text, source) else 0)),
            )
            n += 1
        c.commit()
        return n
    finally:
        c.close()


def _prev_subject(db, mem_id, ts=None, window=900.0):
    """上一条对话里说的是谁。

    「腰不好\\n久坐得垫个小枕头」这种句子没有主语，之前一律记成用户本人的，
    可实际上它是跟着上一句「我爸……」说的。只在本人没出现、且真的从上一两条
    里找到一个第三方名字时才继承 —— 宁可漏，不要错怪。
    """
    try:
        c = sqlite3.connect("file:%s?mode=ro" % str(db).replace("\\", "/"),
                            uri=True, timeout=20.0)
        rows = c.execute(
            "SELECT raw, t_anchor FROM mem WHERE id < ? ORDER BY id DESC LIMIT 4",
            (mem_id,)).fetchall()
        c.close()
    except Exception:
        return None
    names = []
    for raw, ta in rows:
        if ts is not None and ta:
            try:
                if abs(float(ta) - float(ts)) > window:
                    continue
            except Exception:
                pass
        for kind, name in _q_subjects(raw or ""):
            if kind == "other" and name:
                names.append(name)
        if names:
            break
    uniq = list(dict.fromkeys(names))
    return uniq[0] if len(uniq) == 1 else None


def record(db, mem_id, text, ts=None, speaker="user", source=""):
    """一步到位：抽 + 存。返回 (抽到的条数, 事实列表)。

    source 决定这条能不能进永久区：只有"人真的说过的话"才配不衰减。
    施工汇报、评测卡、群聊扫描这些是记录，不是生活，抽错了会一直错下去。
    """
    items = extract(text, ts=ts)
    # 没主语的「腰不好 / 不吃芹菜」，跟着上一句的主语走（见 _prev_subject）
    if items and "我" not in (text or ""):
        subj = _prev_subject(db, mem_id, ts)
        if subj:
            for it in items:
                if (it.get("subject_kind") == "user" and not it.get("subject")
                        and it.get("predicate") in ("身体", "喜欢", "讨厌")):
                    it["subject_kind"] = "other"
                    it["subject"] = subj
    # 助手自己说出去的话也要落一条：他问「你答应过我什么」时，得答得出来。
    if (speaker or "") == "self" and _SELF_COMMIT.search(text or ""):
        m = _SELF_COMMIT.search(text)
        snippet = (text[max(0, m.start() - 6):m.start() + 40] or "").strip()
        items.append({
            "subject_kind": "self", "subject": "", "predicate": "承诺",
            "object": snippet[:60], "ts": ts if ts is not None else __import__("time").time(),
        })
    if not items:
        return 0, []
    return save(db, mem_id, items, speaker=speaker, text=text, source=source), items


# ---------- 查询 ----------

def _q_subjects(query):
    hits = []
    for word, name in _OTHER_SUBJECTS.items():
        if word in query or (name and name in query):
            hits.append(("other", name))
    # 专名：问句里出现「梁志远/沈知夏」这种。
    # 🔴 不能只用 {2,4} 的贪心滑窗——「沈知夏多大」会被切成四字，名字反而漏掉。
    #    按 3、2、4 的顺序去试每个滑窗里的片段，命中一个就够。
    for m in re.finditer(r"[\u4e00-\u9fa5]{2,5}", query or ""):
        seg = m.group(0)
        found = None
        for L in (3, 2, 4):
            for i in range(0, len(seg) - L + 1):
                tok = seg[i:i + L]
                if _is_name(tok):
                    found = tok
                    break
            if found:
                break
        if found:
            hits.append(("other", found))
    if "我" in query:
        hits.append(("user", ""))
    if any(w in query for w in _SELF_SUBJECTS):
        hits.append(("self", ""))
    # 「我妈生日」里那个「我」是别人的定语，不是提问的人本人。
    if any(k == "other" for k, _ in hits):
        hits = [(k, n) for k, n in hits if k != "user"]
    hits = list(dict.fromkeys(hits))   # 同一个「爸」别叠四遍
    return hits


def _q_predicates(query):
    q = query or ""
    out = []
    for pat, pred in _PREDICATES:
        if re.search(pat, q) or pred in q:
            out.append(pred)
    # 问句的「问法」和抽取的「说法」不是一套词，单独查一遍问法表
    for pred, kws in _Q_PRED_KW.items():
        if pred in out:
            continue
        if any(k in q for k in kws):
            out.append(pred)
    # 问句里没有现成谓词时，靠「词面」兜一下：问「欠几件」也要能挂到欠账
    for kw, pred in (("欠", "欠账"), ("约", "约定"), ("接", "接送"), ("价", "单价"),
                     ("还", "归还"), ("生日", "生日"), ("岁", "年龄")):
        if kw in q and pred not in out:
            out.append(pred)
    return out


def recall(db, query, limit=5):
    """按问句里的「主体 + 谓词」找事实。找不到就返回空，不猜。"""
    subs = _q_subjects(query or "")
    preds = _q_predicates(query or "")
    if not subs and not preds:
        return []
    c = sqlite3.connect("file:%s?mode=ro" % str(db).replace("\\", "/"), uri=True, timeout=30.0)
    try:
        rows = c.execute(
            "SELECT id,mem_id,subject_kind,subject,predicate,object,speaker,ts,category,permanent"
            " FROM fact WHERE COALESCE(status,'active')!='superseded'").fetchall()
    except sqlite3.Error:
        return []
    finally:
        c.close()
    scored = []
    time_q = bool(_TIME_Q.search(query or ""))
    q_grams = _q_grams(query or "")
    # 他自报的名字（「我叫沈知夏」）——记成 user 的主体是空名字，可问句里写的
    # 偏偏是这个名字；不把别名翻回 user，这条事实永远对不上号。
    aliases = set()
    for r in rows:
        if r[2] == "user" and r[4] == "叫" and r[5] and r[5] in (query or ""):
            aliases.add(r[5])
    if aliases and not any(k == "user" for k, _ in subs):
        subs = list(subs) + [("user", "")]
    for r in rows:
        sub_hit = 0.0
        sub_weight = 1.0
        if subs:
            for kind, name in subs:
                if r[2] == kind and (not name or r[3] == name):
                    sub_hit = 1.0
                    # 有名字的主体（梁志远）比光秃秃的「他/我」结实，分数给它实权
                    sub_weight = 2.0 if (kind == "other" and name) else 1.0
                    break
        pred_hit = 0.0
        if preds:
            pred_hit = 1.2 if r[4] in preds else 0.2
        # 主体对上 or 谓词对上，都算相关：问「哪两天接米米」时，答案的主语是梁志远，
        # 不能因为题面里出现的是米米就把这条挡掉。
        score = sub_hit * sub_weight + pred_hit * 1.5
        # 词面对上也算一分：问「腰怎么样」时这条记的是「腰不好」，谓词表对不上，
        # 但字面上明明撞上了 —— 没有这一条，长档的毛病就永远捞不出来。
        if q_grams and r[5]:
            og = _q_grams(r[5])
            if og:
                ov = len(q_grams & og) / float(len(q_grams))
                if ov >= 0.15:
                    score += min(1.2, ov * 2.0)
        if r[2] == "other" and r[3]:
            score += 0.3
        # 问的是「哪两天」，答案就得是日子：宾语里带周X/几号的那条往上抬
        if time_q and _TIME_OBJ.search(r[5] or ""):
            score += 1.3
        # 永久区的条目更可信、更该先摆出来；临时性的往后排
        if len(r) > 9 and r[9]:
            score += 0.4
        if score < 1.0:
            continue
        scored.append((score, r))
    scored.sort(key=lambda x: (-x[0], -(x[1][7] or 0)))
    out, seen = [], set()
    for _, r in scored:
        key = (r[2], r[3] or "", r[4], r[5])
        if key in seen:
            continue
        seen.add(key)
        d = dict(zip(("id", "mem_id", "subject_kind", "subject", "predicate",
                      "object", "speaker", "ts", "category", "permanent"), r))
        out.append(d)
        if len(out) >= limit:
            break
    return out


def line_of(f, now=None):
    """给上下文用的一行人话。说清「说的是谁」，防止把别人的事记到他头上。"""
    who = {"user": "他", "self": "我"}.get(f["subject_kind"], "")
    if f["subject_kind"] == "other":
        who = f["subject"] or "别人"
    elif not who:
        who = "不明"
    day = ""
    if f.get("ts"):
        day = time.strftime("%m-%d", time.localtime(f["ts"]))
    s = "%s｜%s｜%s" % (who, f["predicate"], f["object"])
    if f["subject_kind"] == "other":
        s += "（说的是别人，不是他本人）"
    if day:
        s += " · " + day
    return s
