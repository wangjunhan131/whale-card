# -*- coding: utf-8 -*-
"""tools/mine_habits.py —— 从卡里的对话原文里挖"她说话的习惯"（只读）

用法：
    python tools/mine_habits.py                      # 默认读 card.db
    python tools/mine_habits.py --db D:\\path\\card.db
    python tools/mine_habits.py --include-reports     # 连开发汇报一起统计（看看差别）
    python tools/mine_habits.py --out docs/habits-report.md

它做什么：
    1. 只读打开库（mode=ro），一个字节都不写；
    2. 把我方回复分成"像聊天"和"像工作汇报"两类（汇报默认排除，否则挖出来的是"已完成/测试/✅"）；
    3. 统计语言指纹：段数、段长、短段比例、句尾标点、emoji、段首/段尾 n-gram；
    4. 出口头禅候选：出现次数、跨天数、用户是不是也常说（后者要降权）；
    5. 输出 markdown 报告 + JSON（给 v2 的注入层用）。

**这一版不写入 habits 表、不改 /context。** 先看候选质量，人筛过再接。
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import habits as H  # noqa: E402

DEFAULT_DB = os.environ.get("WHALE_DB") or os.path.join(ROOT, "card.db")
DEFAULT_OUT = os.path.join(ROOT, "docs", "habits-report.md")
DEFAULT_JSON = os.path.join(ROOT, "docs", "habits-candidates.json")

# 停用：虚词、连词、代词开头这类"谁都会说"的东西
# 挖矿逻辑已经搬进 habits.py（线上维护线程直接调它）。
# 这里只留别名，免得命令行报告和线上各用一套口径、越走越远。
STOP_PHRASES = H._STOP_PHRASES
GRAM_STOP_CHARS = H._GRAM_STOP_CHARS
DEV_WORDS = H._DEV_WORDS
is_gram_junk = H._gram_all_function
day_of = H._day_of
has_dev_word = H.has_dev_word


def open_ro(path):
    p = path.replace("\\", "/")
    con = sqlite3.connect("file:%s?mode=ro" % p, uri=True, timeout=10.0)
    con.row_factory = sqlite3.Row
    return con


def load_corpus(path):
    """读 pull_qq_corpus.py 落盘的 jsonl：role=self 是我方，其它是背景。

    为什么要这条路：卡里的 dialogue 只有"正经对话"，
    群里/私聊里那些零碎的短句才是口音真正待的地方。
    """
    mine, theirs = [], []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            text = (r.get("text") or "").strip()
            if not text:
                continue
            item = {"id": r.get("ts"), "ts": r.get("ts"), "text": text}
            if r.get("role") == "self":
                item["report_like"] = H.is_report_like(text) or has_dev_word(text)
                mine.append(item)
            else:
                theirs.append(item)
    return mine, theirs


def collect(con):
    """她自己说的 / 别人说的（实现见 habits.collect_dialogue）。"""
    return H.collect_dialogue(con)



def shape_stats(texts):
    seg_counts, seg_lens, short = [], [], 0
    tail_punct = Counter()
    emoji = 0
    total_seg = 0
    for t in texts:
        segs = H.segments(t)
        seg_counts.append(len(segs))
        for s in segs:
            total_seg += 1
            core = H.strip_punct(s)
            seg_lens.append(len(core))
            if len(core) <= 10:
                short += 1
            if s and s[-1] in "。！？…!?":
                tail_punct[s[-1]] += 1
        if any(ord(ch) > 0x1F000 for ch in t):
            emoji += 1

    def med(xs):
        xs = sorted(xs)
        return xs[len(xs) // 2] if xs else 0

    return {
        "replies": len(texts),
        "segments": total_seg,
        "seg_per_reply_median": med(seg_counts),
        "seg_len_median": med(seg_lens),
        "short_ratio": round(short / total_seg, 3) if total_seg else 0.0,
        "tail_punct": dict(tail_punct.most_common()),
        "emoji_replies": emoji,
    }


def ngram_stats(texts, lo=1, hi=3, top=20):
    head, tail = Counter(), Counter()
    for t in texts:
        for s in H.segments(t):
            if len(H.strip_punct(s)) < 2:
                continue
            h, tl = H.head_ngram(s, 3), H.tail_ngram(s, 3)
            if H._HAS_CN_RE.search(h):
                head[h] += 1
            if H._HAS_CN_RE.search(tl):
                tail[tl] += 1
    return head.most_common(top), tail.most_common(top)


def phrase_candidates(mine, theirs, include_reports=False, bg_rows=None):
    """候选口头禅（实现见 habits.mine_phrases）。"""
    return H.mine_phrases(mine, theirs, include_reports=include_reports,
                          bg_rows=bg_rows)



def report_md(meta, shape_chat, shape_all, head_chat, tail_chat, cands,
              include_reports, tot=None):
    tot = tot or {"chat_segments": 0, "bg_segments": 0}
    L = []
    L.append("# 习惯挖掘报告（v1，只读）\n")
    L.append("> 生成时间：%s ｜ 库：%s ｜ 模式：%s\n" % (
        time.strftime("%Y-%m-%d %H:%M:%S"),
        meta["db"], "含开发汇报" if include_reports else "只算像聊天的回复"))
    L.append("## 一、数据面\n")
    L.append("| 项 | 值 |\n|---|---|")
    L.append("| 我方回复总数 | %d |" % meta["mine_total"])
    L.append("| 其中像聊天 | %d |" % meta["mine_chat"])
    L.append("| 被当成工作汇报排除 | %d |" % meta["mine_report"])
    L.append("| 用户消息 | %d |" % meta["theirs_total"])
    L.append("")
    L.append("## 二、语言指纹（像聊天的那部分）\n")
    L.append("| 项 | 值 |\n|---|---|")
    L.append("| 回复数 | %d |" % shape_chat["replies"])
    L.append("| 段数合计 | %d |" % shape_chat["segments"])
    L.append("| 每条回复段数（中位） | %d |" % shape_chat["seg_per_reply_median"])
    L.append("| 每段字数（中位） | %d |" % shape_chat["seg_len_median"])
    L.append("| ≤10 字的段占比 | %.1f%% |" % (shape_chat["short_ratio"] * 100))
    L.append("| 句尾标点 | %s |" % json.dumps(shape_chat["tail_punct"], ensure_ascii=False))
    L.append("| 带 emoji 的回复 | %d |" % shape_chat["emoji_replies"])
    L.append("")
    L.append("## 三、段首 / 段尾高频（前 15）\n")
    L.append("**段首**：" + "、".join("%s(%d)" % (k, n) for k, n in head_chat[:15]))
    L.append("")
    L.append("**段尾**：" + "、".join("%s(%d)" % (k, n) for k, n in tail_chat[:15]))
    L.append("")
    L.append("## 四、口头禅候选（≥%d 次、跨 ≥%d 天）\n" % (
        H.HAB_CAND_MIN_HITS, H.HAB_CAND_MIN_DAYS))
    L.append("> 背景段数 %d ｜ 聊天段数 %d ｜ lift = 聊天里的千段出现率 ÷ 背景里的千段出现率\n"
             % (tot["bg_segments"], tot["chat_segments"]))
    if not cands:
        L.append("_没有候选。样本太少，或者全被过滤了。_")
    else:
        groups = [("keep", "建议保留"), ("review", "待定（证据还不够，或介于两档之间）"),
                  ("drop", "建议丢弃")]
        for key, title in groups:
            rows = [c for c in cands if c["suggest"] == key]
            L.append("### %s（%d 条）\n" % (title, len(rows)))
            if not rows:
                L.append("_（空）_\n")
                continue
            L.append("| 候选 | 命中 | 跨天 | 用户也说 | 背景 | 区分度 | lift | 置信 | 样例 |")
            L.append("|---|---|---|---|---|---|---|---|---|")
            for c in rows[:25]:
                L.append("| `%s` | %d | %d | %d | %d | %.2f | %.2f | %.2f | %s |" % (
                    c["key"], c["hits"], c["days"], c["user_hits"], c["bg_hits"],
                    c["distinct"], c["lift"], c["confidence"],
                    " / ".join(s.replace("|", "\\|") for s in c["samples"][:1])))
            L.append("")
    L.append("## 五、老实说\n")
    L.append("- 这张表**只是候选**。数据里大半是开发阶段的进度汇报，"
             "真正像聊天的回复只有 %d 条，样本不足以定「她一定这么说」。\n" % meta["mine_chat"])
    L.append("- 「建议保留」的判据写死在 `habits.py`：区分度 ≥ %.2f、跨 ≥%d 天、"
             "lift ≥ %.1f（聊天里出现率至少是背景的 %.0f 倍）。\n"
             % (H.HAB_DISTINCT_DROP, H.HAB_CAND_MIN_DAYS, H.HAB_LIFT_KEEP,
                H.HAB_LIFT_KEEP))
    L.append("- lift 就是拿来压「开发词」的：像「建文件夹」「环境变量」这种在汇报里满地都是、"
             "在聊天里几乎没有的词，lift 会小于 1，直接归入丢弃。\n")
    L.append("- 下一步（v2）：把「建议保留」里你点头的写进 `habits` 表，"
             "再在 `/context` 的【我是谁】末尾挂最多 5 条，带「自然用，别硬塞」。\n")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--json", default=DEFAULT_JSON)
    ap.add_argument("--include-reports", action="store_true")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--write", action="store_true",
                    help="把 keep/review 的候选写进 habits 表（默认只出报告）")
    ap.add_argument("--weak-top", type=int, default=0,
                    help="额外把 lift 最高的前 N 条标成弱注入（配合 --write）")
    ap.add_argument("--corpus", default="",
                    help="外部语料 jsonl（tools/pull_qq_corpus.py 产出）；给了就只用它")
    args = ap.parse_args(argv)

    if not os.path.exists(args.db):
        print("找不到库：%s" % args.db)
        return 2
    con = open_ro(args.db)
    if args.corpus:
        mine, theirs = load_corpus(args.corpus)
        print("语料来源：%s（我方 %d ／ 别人 %d）"
              % (args.corpus, len(mine), len(theirs)))
    else:
        mine, theirs = collect(con)
    chat = [r for r in mine if not r.get("report_like") or args.include_reports]

    shape_chat = shape_stats([r["text"] for r in chat])
    shape_all = shape_stats([r["text"] for r in mine])
    head_chat, tail_chat = ngram_stats([r["text"] for r in chat])
    cands, tot = phrase_candidates(
        mine, theirs, include_reports=args.include_reports,
        bg_rows=(theirs if args.corpus else None))

    meta = {
        "db": args.db,
        "mine_total": len(mine),
        "mine_chat": len(chat),
        "mine_report": len(mine) - len(chat),
        "theirs_total": len(theirs),
        "chat_segments": tot["chat_segments"],
        "bg_segments": tot["bg_segments"],
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    md = report_md(meta, shape_chat, shape_all, head_chat, tail_chat,
                   cands[:args.top], args.include_reports, tot)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(md)
    with open(args.json, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "shape": shape_chat,
                   "head": head_chat, "tail": tail_chat,
                   "totals": tot,
                   "candidates": cands[:args.top]},
                  f, ensure_ascii=False, indent=1)

    print("我方回复 %d 条（像聊天 %d / 报告味 %d），用户 %d 条"
          % (meta["mine_total"], meta["mine_chat"], meta["mine_report"], meta["theirs_total"]))
    print("段数中位 %d ｜ 段长中位 %d 字 ｜ ≤10 字段 %.1f%%"
          % (shape_chat["seg_per_reply_median"], shape_chat["seg_len_median"],
             shape_chat["short_ratio"] * 100))
    nk = len([c for c in cands if c["suggest"] == "keep"])
    nd = len([c for c in cands if c["suggest"] == "drop"])
    nr = len(cands) - nk - nd
    print("候选 %d 条：建议保留 %d ／ 待定 %d ／ 建议丢弃 %d"
          % (len(cands), nk, nr, nd))
    print("前 10：%s" % "、".join(
        "%s(%d,%.1fx,%s)" % (c["key"], c["hits"], c["lift"], c["suggest"])
        for c in cands[:10]))
    print("报告：%s" % args.out)
    print("JSON：%s" % args.json)

    # ---- 可选：写进 habits 表（默认不写） ----
    # 为什么默认不写：挖出来的是统计，写进去的是"她的习惯"。
    # 前者错了顶多报告难看，后者错了她会开始演。所以这一步得人点头。
    if args.write:
        n = write_back(args.db, cands, weak_top=args.weak_top)
        print("已写入 habits 表：%d 条（keep + review）" % n)
        if args.weak_top > 0:
            print("其中弱注入候选：前 %d 条（lift 最高、词面干净）" % args.weak_top)
    return 0


def write_back(db_path, cands, weak_top=0):
    """写进 habits 表（实现见 habits.write_candidates）。"""
    con = sqlite3.connect(db_path, timeout=30.0)
    try:
        return H.write_candidates(con, cands, weak_top=weak_top)
    finally:
        con.close()



if __name__ == "__main__":
    raise SystemExit(main())
