# -*- coding: utf-8 -*-
"""纯数学小工具（从 card2.py 搬出来的第一批）。

拆 card2 门面的第 1 步：这些函数不带状态、不碰数据库、不依赖 MemoryCard，
但它们占着 card2 里最显眼的位置，读召回逻辑时要来回翻 2000 行。
搬出来之后 card2 只 `from vmath import ...`，调用点一个不改（名字仍是模块级）。

规矩：这一层只放"输入数字、输出数字"的东西。要读库、要读卡、要用 self 的，
一律不许放进来。
"""
import json
import math


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


def _clamp01(v):
    """把权重夹回 [0,1]。脏数据不许穿透到检索排序里。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if f != f:          # nan
        return 0.0
    return 0.0 if f < 0.0 else (1.0 if f > 1.0 else f)

import re

_PUNCT = re.compile(r"[，。！？、；：“”‘’（）\s,.!?;:()\[\]{}]")

_STOP_CHARS = set("的了在是我你他她它吗呢吧啊呀哦嗯和也就都还这那个一不有"
                  "们么什怎样么着过得地很太要会能可说对好么")

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
