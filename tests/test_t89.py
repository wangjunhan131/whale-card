# -*- coding: utf-8 -*-
"""T89：上下文预算的「可调 + 按模型自适应」这一层。

验的不是"数字好不好看"，是四件容易悄悄错的事：
  1) 认模型：认得出来 / 认不出来都要有个交代，不能静默给个 0
  2) 建议值：夹在上下限里，不能让 1M 窗口算出个 12 万的预算
  3) 优先级：显式传 > 自动 > 卡里配置 > 默认（顺序反了就是"设置了不生效"）
  4) 语音参数：坏值不许写进去，`provider` 不许假装能切换

跑法：python -X utf8 tests/test_t89.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import ctxweight as cw          # noqa: E402
import card2                    # noqa: E402

OK = 0
BAD = 0


def chk(name, cond, extra=""):
    global OK, BAD
    if cond:
        OK += 1
        print("  ✓ " + name)
    else:
        BAD += 1
        print("  ✗ " + name + ("  " + str(extra) if extra else ""))


def close(a, b, tol=1e-6):
    return abs(float(a) - float(b)) <= tol


# ── 1. 认模型 ──────────────────────────────────────────────
print("\n[1] window_of / detect_model")
chk("deepseek 认成 128K", cw.window_of("deepseek-flash")[0] == 128000)
chk("1M 窗口认得出来", cw.window_of("gemini-3.1-flash")[0] == 1000000)
chk("claude 200K", cw.window_of("claude-sonnet-4")[0] == 200000)
chk("认不出来的给保守值", cw.window_of("某个没见过的模型")[0] == cw.WINDOW_FALLBACK)
chk("空串也给保守值，不抛", cw.window_of("")[0] == cw.WINDOW_FALLBACK)
# detect_model 读真实用量文件；读不到必须是空串而不是抛异常
m = cw.detect_model(usage_path=os.path.join(tempfile.gettempdir(), "绝不存在.json"))
chk("读不到用量文件 → 空串", m == "")

# ── 2. 建议值 ──────────────────────────────────────────────
print("\n[2] suggest_budget 上下限")
chk("deepseek → 15400", cw.suggest_budget("deepseek-flash") == 15400,
    cw.suggest_budget("deepseek-flash"))
chk("1M 窗口也不许超过 SUGGEST_MAX",
    cw.suggest_budget("gemini-pro") == cw.SUGGEST_MAX,
    cw.suggest_budget("gemini-pro"))
chk("认不出来的不比 SUGGEST_MIN 低",
    cw.suggest_budget("???") >= min(cw.SUGGEST_MIN, cw.BUDGET_MIN))
chk("建议值永不超过 BUDGET_MAX", cw.suggest_budget("gemini") <= cw.BUDGET_MAX)
chk("只会给到百位（不出现 15397 这种）",
    cw.suggest_budget("deepseek-flash") % 100 == 0)

# ── 3. 优先级 ──────────────────────────────────────────────
print("\n[3] effective_budget 的先后顺序")
tmp = tempfile.mkdtemp(prefix="t89_")
db_path = os.path.join(tmp, "card.db")
card = card2.MemoryCard(db_path, card2.Clock(start=1700000000))
db = card.db
try:
    # 默认：没有任何配置
    chk("没配过 → 默认值", cw.effective_budget(None, db) == cw.BUDGET_DEFAULT,
        cw.effective_budget(None, db))

    # 卡里配了 7000
    cw.rc_set(db, "BUDGET", "7000", ts=0)
    cw.load_config(db, force=True)
    chk("卡里配 7000 → 7000", cw.effective_budget(None, db) == 7000,
        cw.effective_budget(None, db))

    # 显式传的要压过卡里的
    chk("显式 3000 压过卡里 7000", cw.effective_budget(3000, db) == 3000,
        cw.effective_budget(3000, db))
    chk("显式超上限会被夹住",
        cw.effective_budget(999999, db) == cw.BUDGET_MAX,
        cw.effective_budget(999999, db))

    # 打开自动：自动值要压过卡里那个数
    cw.rc_set(db, "BUDGET_AUTO", "1", ts=0)
    cw.load_config(db, force=True)
    auto = cw.auto_now(db)
    chk("自动开着时 on=True", auto["on"] is True)
    chk("自动值就是建议值", cw.effective_budget(None, db) == auto["budget"],
        (cw.effective_budget(None, db), auto["budget"]))
    chk("自动也算不过显式传的值", cw.effective_budget(5000, db) == 5000)

    # 关掉自动：必须回到卡里那个数，而不是"记住了上次自动值"
    cw.rc_set(db, "BUDGET_AUTO", "0", ts=0)
    cw.load_config(db, force=True)
    chk("关掉自动 → 回到卡里 7000", cw.effective_budget(None, db) == 7000,
        cw.effective_budget(None, db))
    chk("关掉自动时 auto_now 仍报识别的模型",
        cw.auto_now(db)["model"] == cw.detect_model(),
        cw.auto_now(db))

    # ── 4. preview 与真跑必须一致 ──────────────────────────
    print("\n[4] preview 不能和真跑分家")
    cw.load_config(db, force=True)
    p1 = cw.preview("你回来啦", db=db)
    chk("手动：preview 总预算 = 卡里配置", p1["total"] == 7000, p1["total"])

    # 预览里打开自动，total 必须跟着变（这就是"控制台看到 6000、真跑 15400"那个坑）
    p2 = cw.preview("你回来啦", db=db, overrides={"BUDGET_AUTO": "1"})
    chk("预览里打开自动 → total 变建议值",
        p2["total"] == cw.suggest_budget(cw.detect_model()),
        (p2["total"], cw.suggest_budget(cw.detect_model())))

    # 预览里同时给了 BUDGET 和 BUDGET_AUTO：手填的那个该赢
    p3 = cw.preview("你回来啦", db=db,
                    overrides={"BUDGET_AUTO": "1", "BUDGET": "9000"})
    chk("自动开着但手填了 9000 → 9000", p3["total"] == 9000, p3["total"])

    # preview 不许写库
    before = dict(cw.rc_all(db))
    cw.preview("随便说句话", db=db, overrides={"BUDGET": "11000"})
    chk("preview 不写库", dict(cw.rc_all(db)) == before)
finally:
    try:
        card.close()
    except Exception:
        pass

# ── 5. 语音参数 ────────────────────────────────────────────
print("\n[5] voice 配置读写与兜底")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import voice as vc            # noqa: E402

# 换到一个临时缓存目录，别动真卡旁边的 cfg.json
old_cache, old_path = vc.CACHE, vc.CFG_PATH
vc.CACHE = tmp
vc.CFG_PATH = os.path.join(tmp, "cfg.json")
try:
    d = vc.load_cfg()
    chk("没有文件 → 全默认", d == vc.CFG_DEFAULTS, d)

    vc.save_cfg({"rate": "+30%", "max_chars": 80})
    d = vc.load_cfg()
    chk("存进去能读回来", d["rate"] == "+30%" and d["max_chars"] == 80, d)
    chk("没提到的字段还是默认", d["voice"] == vc.VOICE_DEFAULT)

    # 坏值不许进
    vc.save_cfg({"rate": "快一点"})
    chk("坏语速被丢掉", vc.load_cfg()["rate"] == "+30%", vc.load_cfg()["rate"])
    vc.save_cfg({"pitch": "abc"})
    chk("坏音高被丢掉", vc.load_cfg()["pitch"] == vc.PITCH_DEFAULT)
    vc.save_cfg({"max_chars": 9999})
    chk("超范围字数被夹住", 2 <= vc.load_cfg()["max_chars"] <= 300,
        vc.load_cfg()["max_chars"])
    vc.save_cfg({"我不认识这个键": "1"})
    chk("不认识的键不落盘", "我不认识这个键" not in vc.load_cfg())

    # provider 不许假装能切
    vc.save_cfg({"provider": "openai"})
    chk("provider 强制退回 edge", vc.load_cfg()["provider"] == "edge",
        vc.load_cfg()["provider"])

    # 坏 JSON 不许炸
    with open(vc.CFG_PATH, "w", encoding="utf-8") as f:
        f.write("{这不是 json")
    chk("坏文件 → 回默认，不抛", vc.load_cfg() == vc.CFG_DEFAULTS)

    # speakable 跟着 max_chars 走
    vc.save_cfg({"max_chars": 10})
    chk("超上限的不发语音", vc.speakable("这句话确实有点太长了吧", max_chars=10)[0] is False)
    chk("上限放宽后同一句就可以发",
        vc.speakable("这句话确实有点太长了吧", max_chars=40)[0] is True)
finally:
    vc.CACHE, vc.CFG_PATH = old_cache, old_path

print("\n" + "=" * 50)
print("通过 %d 项，失败 %d 项" % (OK, BAD))
if BAD == 0:
    print("全部通过 ✅")
sys.exit(1 if BAD else 0)
