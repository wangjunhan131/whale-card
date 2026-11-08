# -*- coding: utf-8 -*-
"""facts.py — 写入侧事实抽取（谁说的 / 说的是谁 / 什么事 / 什么时候）

为什么要有这个文件：第三方评测里最弱的三块是
  1) 「别人的事记到用户头上」——用户说「我妈生日是三月五号」，我们把它记成"用户的记忆"，
     下次问「我生日」时会把他妈的生日端上来；
  2) 「分散在不同聊天里的线索拼不起来」——同一个人/同一件事散在几十条记忆里，
     检索只会按句子相似度捞，捞不到「把这几条拼起来」；
  3) 45 天档——远期的原话早已被时间磨掉，但「他妈生日三月五号」这种原子事实不该跟着烂。

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
}
_SELF_SUBJECTS = ("你", "汐音", "鲸鱼娘", "小鲸")
_USER_SUBJECTS = ("我", "我本人", "我自己")

# 谓词表：模式 → 规范谓词
_PREDICATES = [
    (r"生日(?:是|在|为)?", "生日"),
    (r"今年?(\d{1,2})\s*岁", "年龄"),
    (r"(?:住在|家住|家在|老家在)", "住处"),
    (r"(?:在|于)([\u4e00-\u9fa5A-Za-z0-9]{2,12})(?:上班|工作|读书|上学|念书)", "在哪"),
    (r"(?:读|上|念)(高[一二三]|初[一二三]|大[一二三四]|\d{1,2}年级)", "年级"),
    (r"(?:喜欢|爱吃|爱喝|爱看|爱听|迷上了|粉上了)", "喜欢"),
    (r"(?:讨厌|不喜欢|受不了|反感)", "讨厌"),
    (r"(?:养了|养着|捡了)", "养"),
    (r"(?:买了|买了个|入手了|换了)", "买"),
    (r"(?:叫|名叫|名字是|昵称是)", "叫"),
    (r"(?:打算|计划|准备|想要|想去)", "打算"),
    (r"(?:答应|约好|说好|约定)", "约定"),
    (r"(?:考试|模考|月考|期末|期中)", "考试"),
    (r"(?:作业|功课)", "作业"),
]

_SPLIT = re.compile(r"[。！？!?；;\n]+")
_CLAUSE = re.compile(r"[，,、]+")
_PUNCT = re.compile(r"[\s，。！？、；：…~—\-（）()《》·「」【】\"']+")

# 客体里出现这些词，说明抽到的是「他说/他叫」这种元叙述，不是事实本身
_BAD_OBJECT = re.compile(r"(叫|吗|呢|吧|就行|啥子|怎样|怎么)")
# 「我叫 X」里的名字只可能是 2~6 个字，长的一定是断错了
_NAME_TAIL = re.compile(r"^[\u4e00-\u9fa5A-Za-z·]{2,6}$")


def _object_ok(pred, obj):
    """客体质量闸：宁可抽不出，也不要把「他修车那手才叫稳」当成事实。"""
    if not obj:
        return False
    if len(obj) > 20:
        return False
    if _BAD_OBJECT.search(obj):
        return False
    if pred == "叫" and not _NAME_TAIL.match(obj):
        return False
    return True


def _clean(s):
    return _PUNCT.sub("", s or "")


def _subject_of(clause):
    """返回 (kind, name, 剩余文本)。kind ∈ user / other / self / unknown。"""
    # 先长后短，避免「我妈妈」被「我妈」截断
    for word in sorted(_OTHER_SUBJECTS, key=len, reverse=True):
        if clause.startswith(word):
            return "other", _OTHER_SUBJECTS[word], clause[len(word):]
        idx = clause.find("的" + word)
        if 0 <= idx <= 6:
            return "other", _OTHER_SUBJECTS[word], clause[idx + 1 + len(word):]
    for word in _USER_SUBJECTS:
        if clause.startswith(word):
            return "user", "", clause[len(word):]
    for word in _SELF_SUBJECTS:
        if clause.startswith(word):
            return "self", "", clause[len(word):]
    if clause[:1] in ("他", "她"):
        return "other", "", clause[1:]
    return None, "", clause


def extract(text, ts=None):
    """从一段话里抽事实。返回 [dict]；抽不出返回 []。

    只抽「主语 + 谓词 + 客体」都在同一小句里的情况，宁缺勿编。
    """
    out = []
    if not text or len(text) < 4:
        return out
    for sent in _SPLIT.split(text):
        sent = sent.strip()
        if len(sent) < 4:
            continue
        for clause in _CLAUSE.split(sent):
            clause = _clean(clause)
            if len(clause) < 3:
                continue
            kind, name, rest = _subject_of(clause)
            if kind is None:
                continue
            for pat, pred in _PREDICATES:
                m = re.search(pat, rest)
                if not m:
                    continue
                obj = rest[m.end():].strip()
                if pred == "生日":
                    obj = obj.lstrip("是") or obj
                if pred == "在哪":
                    obj = (m.group(1) if m.groups() else "") + obj
                if pred == "年级":
                    obj = m.group(1) if m.groups() else obj
                if not obj and pred not in ("生日", "年龄"):
                    continue
                if pred in ("生日", "年龄") and not obj:
                    continue
                if not _object_ok(pred, obj):
                    continue
                out.append({
                    "subject_kind": kind,
                    "subject": name,
                    "predicate": pred,
                    "object": obj[:80],
                    "ts": ts if ts is not None else time.time(),
                })
                break  # 一小句只算一个谓词，避免「喜欢」和「讨厌」都中
    return out


# ---------- 存储 ----------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fact(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mem_id INTEGER,
    subject_kind TEXT,      -- user / other / self / unknown
    subject TEXT,           -- 第三方时的名字（妈/爸/同学…）
    predicate TEXT,
    object TEXT,
    speaker TEXT,           -- user / self
    ts REAL,
    text TEXT               -- 原句（截断），方便回看
);
CREATE INDEX IF NOT EXISTS idx_fact_ts ON fact(ts);
CREATE INDEX IF NOT EXISTS idx_fact_subject ON fact(subject_kind, subject);
CREATE INDEX IF NOT EXISTS idx_fact_pred ON fact(predicate);
"""


def ensure(db):
    c = sqlite3.connect(db, timeout=30.0)
    try:
        c.executescript(_SCHEMA)
        c.commit()
    finally:
        c.close()


def save(db, mem_id, items, speaker="user", text=""):
    """把抽出来的事实写进表。返回写入条数。"""
    if not items:
        return 0
    c = sqlite3.connect(db, timeout=30.0)
    try:
        n = 0
        for it in items:
            c.execute(
                "INSERT INTO fact(mem_id,subject_kind,subject,predicate,object,speaker,ts,text)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (mem_id, it["subject_kind"], it["subject"], it["predicate"],
                 it["object"], speaker, it["ts"], (text or "")[:160]),
            )
            n += 1
        c.commit()
        return n
    finally:
        c.close()


def record(db, mem_id, text, ts=None, speaker="user"):
    """一步到位：抽 + 存。返回 (抽到的条数, 事实列表)。"""
    items = extract(text, ts=ts)
    if not items:
        return 0, []
    return save(db, mem_id, items, speaker=speaker, text=text), items


# ---------- 查询 ----------

def _q_subjects(query):
    hits = []
    for word, name in _OTHER_SUBJECTS.items():
        if word in query or (name and name in query):
            hits.append(("other", name))
    if "我" in query:
        hits.append(("user", ""))
    if any(w in query for w in _SELF_SUBJECTS):
        hits.append(("self", ""))
    # 「我妈生日」里那个「我」是别人的定语，不是提问的人本人。
    # 只要句子里出现了明确的第三方，就把光秃秃的「我」剔掉 ——
    # 否则问「我妈生日」会先端上「我生日」。
    if any(k == "other" for k, _ in hits):
        hits = [(k, n) for k, n in hits if k != "user"]
    return hits


def _q_predicates(query):
    out = []
    for pat, pred in _PREDICATES:
        if re.search(pat, query) or pred in query:
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
        rows = c.execute("SELECT id,mem_id,subject_kind,subject,predicate,object,speaker,ts FROM fact").fetchall()
    except sqlite3.Error:
        # 表还没建（老卡/被删）就当没有事实，别把上下文整炸
        return []
    finally:
        c.close()
    scored = []
    for r in rows:
        score = 0.0
        if subs:
            for kind, name in subs:
                if r[2] == kind and (not name or r[3] == name):
                    score += 1.0
                    break
            else:
                continue
        if preds and r[4] in preds:
            score += 1.2
        elif preds:
            score += 0.2
        if score <= 0:
            continue
        scored.append((score, r))
    scored.sort(key=lambda x: (-x[0], -(x[1][7] or 0)))
    return [dict(zip(("id", "mem_id", "subject_kind", "subject", "predicate", "object", "speaker", "ts"), r))
            for _, r in scored[:limit]]


def line_of(f, now=None):
    """给上下文用的一行人话。说清「说的是谁」，防止把别人的事记到他头上。"""
    who = {"user": "他", "self": "我"}.get(f["subject_kind"], "")
    if f["subject_kind"] == "other":
        who = ("他" + f["subject"]) if f["subject"] else "别人"
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
