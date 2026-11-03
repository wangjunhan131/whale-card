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
STOP_PHRASES = set("""
的 了 是 在 我 你 他 她 它 们 这 那 有 没 就 都 也 又 还 很 太 不 要 会 能 可 对 吧 啊 呀 呢 哦 嗯 嗯嗯 哈哈 嘿嘿 嘻嘻
一个 一下 一点 一些 这个 那个 这样 那样 什么 怎么 为什么 因为 所以 但是 不过 而且 然后 如果 要是 虽然 或者 还是 就是 只是 已经 现在 时候
我们 你们 他们 自己 别人 东西 事情 问题 地方 时间 感觉 觉得 知道 应该 可以 需要 不用 不是 没有 不会 不能 不要 就是 还有 也是 都是
要是你 你要是 我可以 你可以 我就不 你不 我就 我就说 我不是 我又 你也不 我也不
""".split())

# 开发汇报词（默认排除的补充过滤）
DEV_WORDS = ("测试", "回归", "提交", "代码", "文件", "接口", "配置", "数据库", "脚本",
             "日志", "字段", "函数", "报错", "修复", "验收", "文档", "仓库", "部署")


def open_ro(path):
    p = path.replace("\\", "/")
    con = sqlite3.connect("file:%s?mode=ro" % p, uri=True, timeout=10.0)
    con.row_factory = sqlite3.Row
    return con


def day_of(ts):
    try:
        return time.strftime("%Y-%m-%d", time.localtime(float(ts)))
    except Exception:
        return "?"


def has_dev_word(s):
    return any(w in s for w in DEV_WORDS)


def collect(con):
    rows = [dict(r) for r in con.execute(
        "SELECT id, ts, role, text FROM dialogue ORDER BY id")]
    mine, theirs = [], []
    for r in rows:
        text = (r.get("text") or "").strip()
        if not text:
            continue
        if r["role"] == "assistant":
            r["report_like"] = H.is_report_like(text) or has_dev_word(text)
            mine.append(r)
        else:
            theirs.append(r)
    return mine, theirs


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


def phrase_candidates(mine, theirs, include_reports=False):
    """候选口头禅：2~4 字，出现在我方回复里，统计次数/天数/用户是否也说。"""
    stat = defaultdict(lambda: {"hits": 0, "days": set(), "ids": [], "samples": []})
    user_stat = Counter()

    for r in theirs:
        for s in H.segments(r["text"]):
            for g in H.ngrams(s, 2, 4):
                user_stat[g] += 1

    for r in mine:
        if r.get("report_like") and not include_reports:
            continue
        for s in H.segments(r["text"]):
            core = H.strip_punct(s)
            if len(core) < 2:
                continue
            grams = set()
            # 整段很短（<=8 字）本身就是一个候选；否则取 2~4 字 n-gram
            if len(core) <= 8:
                grams.add(core)
            grams |= H.ngrams(core, 2, 4)
            for g in grams:
                if g in STOP_PHRASES or len(g) < 2:
                    continue
                if not H._HAS_CN_RE.search(g):      # 纯英文/数字不算口癖
                    continue
                if g.isdigit():
                    continue
                st = stat[g]
                st["hits"] += 1
                st["days"].add(day_of(r["ts"]))
                if len(st["ids"]) < 6:
                    st["ids"].append(r["id"])
                if len(st["samples"]) < 3:
                    st["samples"].append(s[:40])

    # 过滤：被更长短语覆盖的短候选（"行吧" 和 "行吧我等着" 同时出现时留长的）
    keys = [k for k, v in stat.items()
            if v["hits"] >= H.HAB_CAND_MIN_HITS and len(v["days"]) >= H.HAB_CAND_MIN_DAYS]
    keys.sort(key=len, reverse=True)
    keep = []
    for k in keys:
        if any(k != o and k in o for o in keep):
            continue
        keep.append(k)

    out = []
    for k in keep:
        v = stat[k]
        u = user_stat.get(k, 0)
        # 区分度：她说得多、用户说得少 → 更像她的口音
        distinct = round(v["hits"] / float(v["hits"] + u), 3)
        out.append({
            "key": k,
            "hits": v["hits"],
            "days": len(v["days"]),
            "user_hits": u,
            "distinct": distinct,
            "confidence": H.confidence_of(v["hits"], len(v["days"])),
            "status": H.status_of(v["hits"], len(v["days"])),
            "dialogue_ids": v["ids"],
            "samples": v["samples"],
        })
    out.sort(key=lambda x: (x["hits"] * (1 + x["days"]), x["distinct"]), reverse=True)
    return out


def report_md(meta, shape_chat, shape_all, head_chat, tail_chat, cands, include_reports):
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
    if not cands:
        L.append("_没有候选。样本太少，或者全被过滤了。_")
    else:
        L.append("| 候选 | 命中 | 跨天 | 用户也说过 | 区分度 | 置信 | 状态 | 样例 |")
        L.append("|---|---|---|---|---|---|---|---|")
        for c in cands[:40]:
            L.append("| `%s` | %d | %d | %d | %.2f | %.2f | %s | %s |" % (
                c["key"], c["hits"], c["days"], c["user_hits"], c["distinct"],
                c["confidence"], c["status"],
                " / ".join(s.replace("|", "\\|") for s in c["samples"][:1])))
    L.append("")
    L.append("## 五、老实说\n")
    L.append("- 这张表**只是候选**。数据里大半是开发阶段的进度汇报，"
             "真正像聊天的回复只有 %d 条，样本不足以定「她一定这么说」。\n" % meta["mine_chat"])
    L.append("- 判断标准建议：**区分度 < 0.5 的先扔**（那是你也在说、不是她的口音），"
             "**只在一个洞里出现的先扔**（那是这场聊嗨了，不是习惯）。\n")
    L.append("- 下一步（v2）：把人工筛过的候选写进 `habits` 表，再在 `/context` 的"
             "【我是谁】末尾挂最多 5 条，带「自然用，别硬塞」。\n")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--json", default=DEFAULT_JSON)
    ap.add_argument("--include-reports", action="store_true")
    ap.add_argument("--top", type=int, default=40)
    args = ap.parse_args(argv)

    if not os.path.exists(args.db):
        print("找不到库：%s" % args.db)
        return 2
    con = open_ro(args.db)
    mine, theirs = collect(con)
    chat = [r for r in mine if not r.get("report_like") or args.include_reports]

    shape_chat = shape_stats([r["text"] for r in chat])
    shape_all = shape_stats([r["text"] for r in mine])
    head_chat, tail_chat = ngram_stats([r["text"] for r in chat])
    cands = phrase_candidates(mine, theirs, include_reports=args.include_reports)

    meta = {
        "db": args.db,
        "mine_total": len(mine),
        "mine_chat": len(chat),
        "mine_report": len(mine) - len(chat),
        "theirs_total": len(theirs),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    md = report_md(meta, shape_chat, shape_all, head_chat, tail_chat,
                   cands[:args.top], args.include_reports)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(md)
    with open(args.json, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "shape": shape_chat,
                   "head": head_chat, "tail": tail_chat,
                   "candidates": cands[:args.top]},
                  f, ensure_ascii=False, indent=1)

    print("我方回复 %d 条（像聊天 %d / 报告味 %d），用户 %d 条"
          % (meta["mine_total"], meta["mine_chat"], meta["mine_report"], meta["theirs_total"]))
    print("段数中位 %d ｜ 段长中位 %d 字 ｜ ≤10 字段 %.1f%%"
          % (shape_chat["seg_per_reply_median"], shape_chat["seg_len_median"],
             shape_chat["short_ratio"] * 100))
    print("候选 %d 条，前 10：%s" % (
        len(cands), "、".join("%s(%d)" % (c["key"], c["hits"]) for c in cands[:10])))
    print("报告：%s" % args.out)
    print("JSON：%s" % args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
