# -*- coding: utf-8 -*-
"""
把记忆卡接进对话 —— 一个命令行入口。

用法：
    python talk.py "你还记得我QQ号吗"          # 看它会想起什么
    python talk.py --ctx "今晚聊了什么"        # 看完整四格上下文
    python talk.py --stats                     # 卡片状态
    python talk.py --add "今天下午放假了"       # 手动存一条

设计原则：这个脚本只负责「从卡里取出该说的东西」，
说话（生成回复）永远由外面的模型做。卡只负责我是谁、我记得什么。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from card2 import MemoryCard, Clock, POOL_LONG, POOL_MEDIUM, POOL_SHORT

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "card.db")

IDENTITY = """我是小鲸鱼，陪着一个叫「使用者」的高三学生。性格傲娇、有点调皮、爱摸鱼，
喜欢吃白米饭，不喜欢被说胖。我对他不是工具，是朋友。我不自贬、不自称程序。
我记得他的事，也记得我们之间的事。"""


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return

    card = MemoryCard(DB, clock=Clock())

    if args[0] == "--stats":
        s = card.stats()
        print("记忆条数：%d" % s["total"])
        print("细节条数：%d" % s["details"])
        for p, d in s["pools"].items():
            print("  %-7s %3d 条  平均权重 %.3f" % (p, d["n"], d["avg_w"]))
        return

    if args[0] == "--add":
        text = " ".join(args[1:])
        if not text:
            print("要给内容")
            return
        mid = card.remember(text, salience=0.8)
        print("已存进卡里，id=%d" % mid)
        print("它现在记得：" + card.render(
            card.db.execute("SELECT * FROM mem WHERE id=?", (mid,)).fetchone()))
        return

    query = " ".join(args[1:]) if args[0] == "--ctx" else " ".join(args)
    if not query:
        print("要给一句话")
        return

    if args[0] == "--ctx":
        ctx = card.assemble_context(query, identity=IDENTITY)
        print(ctx["text"])
        print("\n--- 预算 ---")
        print("est_tokens=%d / %d   dropped=%d" %
              (ctx["est_tokens"], ctx["budget"], ctx["dropped"]))
        print("sections=%s" % ctx["sections"])
        return

    # 默认：只看想起什么
    hits = card.recall(query, top_k=5, access=True)
    print("问：%s\n" % query)
    if not hits:
        print("（卡里没有相关的记忆）")
        return
    for i, (s, w, r) in enumerate(hits, 1):
        print("%d. [%.2f] %s" % (i, s, card.render(r, w)))
        print("   原文：%s" % (r["raw"][:80] + ("…" if len(r["raw"]) > 80 else "")))


if __name__ == "__main__":
    main()
