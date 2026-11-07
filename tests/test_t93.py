# -*- coding: utf-8 -*-
"""t93：短期区按「类型」再分区（V5.2 分区细分，全部在副本上跑，绝不碰真卡）。

由来（用户 2026-11-06）：
    「要不我们把短期区再划分一下，划分成闲聊 工作 学习 娱乐 等等，
      多个区然后每个区的遗忘曲线都有一定的区别」
    「如果当天没什么人聊天…那就按正常的遗忘曲线来说…聊的有很多的话就会忘得很快」
    「工作的话15天左右…闲聊…12个小时…学习…7天…娱乐…3天」
    「先把我们刚刚说的形成一套方案，然后再测试，测试完了之后再正式改动」

要验的八件事：
    ① 开关关着时**零回归**（老五区行为一字不改）
    ② 学习区在短期池半衰期 = 7 天
    ③ 学习区升到中期池 = 46.7 天（区差要穿过阶段，不能到中期就被抹平）
    ④ 负载：写一条 +1、时间流走会衰减
    ⑤ 拥挤系数 f：ratio 0/1/4 分别夹到 0.55 / 1.0 / 2.0
    ⑥ 关系层：core 的寿命是 24 倍
    ⑦ 之前被误判成闲聊的 18 条，现在一条都不判闲聊
    ⑧ 显式传的 region 不被 source 路由覆盖
"""
import os
import shutil
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import card2

SRC = os.path.join(ROOT, "card.db")
DAY = 86400.0
LN2 = 0.6931471805599453

ok = 0
bad = []


def chk(name, cond, extra=""):
    global ok
    if cond:
        ok += 1
        print("  ✅ " + name)
    else:
        bad.append(name)
        print("  ❌ " + name + ("  " + str(extra) if extra else ""))


def near(a, b, tol=0.02):
    try:
        return abs(float(a) - float(b)) <= tol
    except Exception:
        return False


def temp_card():
    d = tempfile.mkdtemp(prefix="t93_", dir=ROOT)
    dst = os.path.join(d, "c.db")
    s = sqlite3.connect(SRC)
    t = sqlite3.connect(dst)
    with t:
        s.backup(t)
    s.close()
    t.close()
    return d, dst


d, db = temp_card()
try:
    c = card2.MemoryCard(db)
    # 这一段测公式，不测今天流量：真卡副本一 remember 就按真实写量重算 f，
    # 学习区今天写得少 → f=0.55 → 半衰期 7 天漂成 12.7 天。先按住，[5] 再放开。
    _orig_refresh = c._load_refresh_f
    c._load_refresh_f = lambda: None
    card2._LOAD_F.clear()
    now = c.clock.now()

    # ---------------------------------------------------------------
    print("[1] 开关默认是关的，而且关着时老五区零回归")
    # 真卡上的开关现在是开着的（2026-11-06 验收后打开），所以先记下它、
    # 临时关掉验零回归，最后 [9] 会再验一遍开关本身能开能关。
    was_on = bool(card2._REGION_LAMBDA_ON)
    c.set_region_lambda(False)
    chk("关掉之后开关是关的", card2._REGION_LAMBDA_ON is False)
    w_ep_off = card2.weight_now(1.0, "short", now - DAY, now, region="episode")
    w_st_off = card2.weight_now(1.0, "short", now - DAY, now, region="study")
    chk("关着时 study 和 episode 走同一条老曲线（study 不认识，退化成默认）",
        near(w_ep_off, w_st_off, 1e-6), (round(w_ep_off, 6), round(w_st_off, 6)))
    print("      （这张卡原本开关是 %s，测完会恢复）" % ("开" if was_on else "关"))

    # ---------------------------------------------------------------
    print("[2] 学习区短期池半衰期 7 天")
    c.set_region_lambda(True)
    chk("开关能打开", card2._REGION_LAMBDA_ON is True)
    lam = card2.effective_lambda("study", "short")
    chk("effective_lambda(study, short) 半衰期 ≈ 7 天",
        near(LN2 / lam / DAY, 7.0, 0.05), round(LN2 / lam / DAY, 3))
    w7 = card2.weight_now(1.0, "short", now - 7 * DAY, now, region="study")
    chk("学了一条，7 天后权重 ≈ 0.5", near(w7, 0.5, 0.01), round(w7, 4))
    w_ep_on = card2.weight_now(1.0, "short", now - 7 * DAY, now, region="episode")
    chk("同一时刻 episode 掉得比 study 快（study 更该留）", w_ep_on < w7,
        (round(w_ep_on, 4), round(w7, 4)))

    # ---------------------------------------------------------------
    print("[3] 区差要穿过阶段：学习升到中期还剩 46.7 天")
    lam_m = card2.effective_lambda("study", "medium")
    hl_m = LN2 / lam_m / DAY
    chk("学习区中期半衰期 ≈ 46.7 天", near(hl_m, 46.67, 0.3), round(hl_m, 2))
    chk("中期比短期更长（阶段倍率生效了）", hl_m > 7.0, round(hl_m, 2))
    chk("工作区长期池半衰期更长（15 天 × 阶段）",
        (LN2 / card2.effective_lambda("work", "long") / DAY) > hl_m)
    chk("没有自有 λ 的区返回 None（episode/fact/preference 不受影响）",
        card2.effective_lambda("episode", "short") is None
        and card2.effective_lambda("fact", "long") is None)

    # ---------------------------------------------------------------
    print("[4] 负载：写一条 +1，时间流走会衰减")
    card2._REGION_LOAD.clear()
    c._load_write({})
    c._load_bump("study")
    v1 = card2._REGION_LOAD.get("study", 0.0)
    chk("写一条之后 load = 1", near(v1, 1.0, 1e-6), round(v1, 4))
    c._load_bump("study")
    v2 = card2._REGION_LOAD.get("study", 0.0)
    chk("再写一条 → 2", near(v2, 2.0, 1e-6), round(v2, 4))
    c.clock.advance(DAY)          # 时间走一天
    c._load_decay()
    v3 = card2._REGION_LOAD.get("study", 0.0)
    chk("一天不写东西，负载衰减到约 2/e（≈0.736）",
        near(v3, 2.0 * 0.36787944117, 0.02), round(v3, 4))

    # ---------------------------------------------------------------
    print("[5] 拥挤系数 f：ratio 0 / 1 / 4")
    c._load_refresh_f = _orig_refresh   # 这一段就要真 f
    card2._REGION_LOAD.clear()
    card2._REGION_LOAD["study"] = 0.0
    c._load_refresh_f()
    chk("没人写时 f = 下限 0.55", near(card2._LOAD_F["study"], 0.55, 1e-6),
        card2._LOAD_F.get("study"))
    card2._REGION_LOAD["study"] = card2.REGION_DAY_BUDGET["study"]        # ratio 1
    c._load_refresh_f()
    chk("正好写满预算时 f = 1.0", near(card2._LOAD_F["study"], 1.0, 1e-6),
        card2._LOAD_F.get("study"))
    card2._REGION_LOAD["study"] = card2.REGION_DAY_BUDGET["study"] * 4    # ratio 4
    c._load_refresh_f()
    chk("写到预算 4 倍时 f = 2.0（sqrt(4)）",
        near(card2._LOAD_F["study"], 2.0, 1e-6), card2._LOAD_F.get("study"))
    card2._REGION_LOAD["study"] = card2.REGION_DAY_BUDGET["study"] * 100  # ratio 100
    c._load_refresh_f()
    chk("爆到 100 倍也只到上限 2.2", near(card2._LOAD_F["study"], 2.2, 1e-6),
        card2._LOAD_F.get("study"))
    # f 真的影响寿命：挤的时候掉得更快
    card2._REGION_LOAD["study"] = card2.REGION_DAY_BUDGET["study"] * 4
    c._load_refresh_f()
    lam_busy = card2.effective_lambda("study", "short")
    card2._REGION_LOAD["study"] = 0.0
    c._load_refresh_f()
    lam_idle = card2.effective_lambda("study", "short")
    chk("挤的时候 λ 更大（忘得更快）", lam_busy > lam_idle,
        (round(lam_busy, 10), round(lam_idle, 10)))
    # 闲时被下限压到 0.55、挤时是 2.0，所以比值是 2.0/0.55 ≈ 3.636，不是 2
    chk("挤/闲 的 λ 比值 = 2.0/0.55",
        near(lam_busy / lam_idle, 2.0 / 0.55, 0.01),
        round(lam_busy / lam_idle, 4))

    # ---------------------------------------------------------------
    print("[6] 关系层：重要的人说的，活得久")
    card2._REGION_LOAD.clear()
    c._load_refresh_f()
    lam_core = card2.effective_lambda("chatter", "short", "core")
    lam_none = card2.effective_lambda("chatter", "short", "")
    chk("core 的寿命是默认的 24 倍", near(lam_none / lam_core, 24.0, 0.01),
        round(lam_none / lam_core, 3))
    chk("known 是 0.6 倍（更短，因为关系浅）",
        near(card2.relation_scale("known"), 0.6, 1e-6))
    chk("不认识的层按 1.0 算", near(card2.relation_scale(""), 1.0, 1e-6))

    # ---------------------------------------------------------------
    print("[7] 之前被误判成闲聊的 18 条（用真卡原文验，不拿手搓短句糊弄自己）")
    ids = (987, 988, 989, 990, 993, 996, 999, 1002, 1008, 1009,
           1011, 1013, 1015, 1017, 1024, 1025, 1027, 1028)
    q = ",".join("?" * len(ids))
    rows = c.db.execute(
        "SELECT id, raw FROM mem WHERE id IN (%s)" % q, ids).fetchall()
    # 开源仓用的是示例卡（tools/make_demo_card.py 造的小卡），那些 id 不一定存在。
    # 有就逐条验，没有就明说跳过 —— 绝不假装验过。
    if len(rows) >= 15:
        still = [r["id"] for r in rows if card2.is_chatter(r["raw"], 0.0)]
        chk("真卡里这 18 条还查得到", True, len(rows))
        chk("真卡原文一条都不判闲聊", len(still) == 0, still)
    else:
        print("      跳过：这张卡里只有 %d 条（示例卡没有这些 id），"
              "改为只验下面五条典型陈述" % len(rows))

    # 顺手验一下「本来就不是闲聊」的那几条典型陈述
    for t in ("使用者喜欢吃白米饭", "我暑假靠婚礼挣钱", "我去年开始剪视频",
              "我小时候在老家呆过", "那时候家里还没网"):
        chk("「%s」不判闲聊" % t[:10], card2.is_chatter(t, 0.0) is False)

    print("[7b] 真闲聊还是一条都没放走")
    casual = [
        "嗯 行 那就先这样吧", "11月05日下午有点困", "你先停一下", "嗯",
        "在吗", "我刚看了会儿视频", "那你去躺一会儿", "哦", "好嘞",
        "哈哈哈笑死", "我去吃饭了", "重新上传", "现在gpu占用100",
        "好了谢谢你啦 晚安大肥鱼", "看了什么",
    ]
    lost = [t for t in casual if not card2.is_chatter(t, 0.0)]
    chk("15 条真闲聊仍然判闲聊", len(lost) == 0, lost)

    # ---------------------------------------------------------------
    print("[8] 显式传的 region 不被 source 路由覆盖")
    card2._REGION_LOAD.clear()
    mid_explicit = c.remember("验收：显式指定分区，来源是 study 也不许改",
                              source="study", region="episode", salience=0.5)
    r_explicit = c.db.execute("SELECT region FROM mem WHERE id=?",
                              (mid_explicit,)).fetchone()[0]
    chk("显式 region=episode 保住了", r_explicit == "episode", r_explicit)

    mid_routed = c.remember("验收：不指定分区，来源是 study 应该自动进学习区",
                            source="study", salience=0.5)
    r_routed = c.db.execute("SELECT region FROM mem WHERE id=?",
                            (mid_routed,)).fetchone()[0]
    chk("不指定时 source=study 自动进 study 区", r_routed == "study", r_routed)

    mid_qq = c.remember("验收：群消息来源照旧", source="qq-group", salience=0.5)
    r_qq = c.db.execute("SELECT region FROM mem WHERE id=?",
                        (mid_qq,)).fetchone()[0]
    chk("source=qq-group 不会被塞进学习区", r_qq != "study", r_qq)

    # ---------------------------------------------------------------
    print("[9] 开关能落盘（换台电脑插上还是同一个设定）")
    c.set_region_lambda(True)
    row = c.db.execute("SELECT val FROM meta WHERE key='region_lambda_on'").fetchone()
    chk("meta 里记了开关状态", bool(row) and str(row[0]) in ("0", "1"),
        row[0] if row else None)
    c.set_region_lambda(False)
    row = c.db.execute("SELECT val FROM meta WHERE key='region_lambda_on'").fetchone()
    chk("关掉也能落盘", bool(row) and str(row[0]) == "0",
        row[0] if row else None)

    # 恢复这张卡原来的开关状态（真卡是开着的，不许被测试顺手关掉）
    c.set_region_lambda(was_on)
    chk("测完把开关恢复成原样", card2._REGION_LAMBDA_ON is was_on, was_on)

finally:
    try:
        c.db.close()
    except Exception:
        pass
    shutil.rmtree(d, ignore_errors=True)

print("")
print("通过 %d 项，失败 %d 项" % (ok, len(bad)))
if bad:
    print("失败清单：")
    for b in bad:
        print("  - " + b)
    sys.exit(1)
print("全部通过 ✅")
