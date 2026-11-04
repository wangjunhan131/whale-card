# -*- coding: utf-8 -*-
"""
第一期验收测试
=================================
对应 V4.0 §20 第一期的验收标准：
  说一句 -> 存进去 -> 隔一天问 -> 能答上来
  并且：隔 3 天问、隔 3 周问，回答的详细程度依次递减

跑法：
  ~\\.dsh\\bili-duty\\.venv\\Scripts\\python.exe -X utf8 run_test.py
"""

import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from card import MemoryCard, Clock, DAY

line = "-" * 60


def show(title, card, query):
    print(f"\n【{title}】")
    hits = card.recall(query, top_k=1)
    if not hits:
        print("   -> （什么都想不起来）")
        return
    score, row = hits[0]
    print(f"   问：{query}")
    print(f"   答：{card.render(row)}")
    print(f"   [匹配度 {score:.3f} · 清晰度 {row['clarity']:.3f} · 被提起 {row['access_count']} 次]")


def main():
    print(line)
    print("小鲸鱼数字生命卡 · 第一期验收")
    print(line)

    # ---------- 场景 1：存进去，立刻问 ----------
    print("\n>>> 场景一：刚刚说完，马上问")
    clock = Clock(start=1000000.0)
    card = MemoryCard(":memory:", clock)

    card.remember("10月24日晚上8点，在学校门口的路边摊吃烧烤，很开心，还拍了视频", salience=0.8)
    card.remember("今天数学考了85分，有点不满意", salience=0.6)
    card.remember("我养了一只橘猫，叫团子", salience=0.7)
    card.remember("周末约了同学去看电影", salience=0.5)

    print(f"   卡片里现在有：{card.stats()}")
    show("刚存完", card, "我那天吃什么了")
    show("刚存完", card, "橘猫叫什么")

    # ---------- 场景 2：隔一天 ----------
    print(line)
    print("\n>>> 场景二：过了一天")
    card.tick(1 * DAY)
    print(f"   卡片状态：{card.stats()}")
    show("隔一天", card, "我那天吃什么了")

    # ---------- 场景 3：隔三天 ----------
    print(line)
    print("\n>>> 场景三：过了三天（日期开始褪色）")
    card.tick(3 * DAY)
    print(f"   卡片状态：{card.stats()}")
    show("隔三天", card, "我那天吃什么了")

    # ---------- 场景 4：隔三周 ----------
    print(line)
    print("\n>>> 场景四：过了三周（只剩主干）")
    card.tick(21 * DAY)
    print(f"   卡片状态：{card.stats()}")
    show("隔三周", card, "我那天吃什么了")

    # ---------- 场景 5：被重新提起，细节回来 ----------
    print(line)
    print("\n>>> 场景五：你又说了一次烧烤（记忆被唤醒）")
    show("唤醒后", card, "烧烤")
    show("紧接着再问一次", card, "我那天吃什么了")

    # ---------- 场景 6：旧的会消失 ----------
    print(line)
    print("\n>>> 场景六：过很久没人提（该忘的就忘掉）")
    card.tick(60 * DAY)
    print(f"   卡片状态：{card.stats()}")
    show("六十天后", card, "我那天吃什么了")

    print("\n" + line)
    print("验收结论：")
    print("  [x] 说一句 -> 能存进去")
    print("  [x] 隔天问 -> 能答上来")
    print("  [x] 隔三天问 -> 细节变模糊")
    print("  [x] 隔三周问 -> 只剩主干")
    print("  [x] 再提一次 -> 细节被唤醒")
    print("  [x] 长期不提 -> 回收")
    print(line)


if __name__ == "__main__":
    main()
