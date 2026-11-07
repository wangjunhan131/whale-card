# -*- coding: utf-8 -*-
"""t109：时间锚定 + 身体参数进卡 + 群聊当场不记 + 语义层异常类。

对应 2026-11-07 批评文档里被采纳的几条（用户 m34734「一起开工都做包括升级」）：
  1. 相对时间锚定：同一句「昨天」在不同条目里换成不同日期（防记串），
     且 raw 一个字都不动（存的是相对、读的是绝对）
  2. SemanticUnavailable 真的有定义（以前 7 处 raise 引用一个不存在的类）
  3. 身体参数（基线/半衰期/静息/注入倍率）能进卡、能校验、脏值拒收
  4. 群聊当场不记：太短、纯表情、复读三类拦在门外；@ 我的由调用方放行
"""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import _cardcopy  # noqa: E402
import card2  # noqa: E402
import viscera as V  # noqa: E402
import semantic  # noqa: E402

try:
    import qq_guard as G
    HAVE_GUARD = True
except Exception as exc:
    HAVE_GUARD = False
    G = None
    print("  (qq_guard 不可用：%s)" % exc)

passed = 0
failed = 0


def chk(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print("  ✅ %s" % name)
    else:
        failed += 1
        print("  ❌ %s %s" % (name, extra))


def ts_of(s):
    return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M"))


print("==============================================================")
print("  t109  时间锚定 / 身体参数进卡 / 群聊当场不记")
print("==============================================================")

print("\n== 一、相对时间锚定（渲染时换算，raw 不动） ==")
D7 = ts_of("2026-11-07 17:00")
D1 = ts_of("2026-11-01 09:00")
chk("昨天 → 11月6日",
    card2.anchor_relative_time("昨天他说要剪视频", D7) == "2026年11月6日他说要剪视频")
chk("今天 → 11月7日",
    card2.anchor_relative_time("今天我考试考得还行", D7) == "2026年11月7日我考试考得还行")
chk("明天 → 11月8日",
    card2.anchor_relative_time("明天给你账号嘻嘻", D7) == "2026年11月8日给你账号嘻嘻")
chk("大前天 → 11月4日（长词优先，不被前天抢）",
    "11月4日" in card2.anchor_relative_time("大前天下午吃烧烤", D7))
chk("明早保留时段（不能只剩日期）",
    card2.anchor_relative_time("明早记得叫我", D7) == "2026年11月8日早上记得叫我")
chk("同一个「昨天」在不同条目里换出不同日期（这才是防记串）",
    card2.anchor_relative_time("昨天他来了", D1) == "2026年10月31日他来了")
chk("没有相对时间词的句子原样返回",
    card2.anchor_relative_time("使用者喜欢吃白米饭", D7) == "使用者喜欢吃白米饭")
chk("空串 / None 不炸",
    card2.anchor_relative_time("", D7) == "" and
    card2.anchor_relative_time(None, D7) == "")
chk("ts 缺失时原样返回（不知道日期就别瞎猜）",
    card2.anchor_relative_time("昨天他来了", 0) == "昨天他来了")
chk("跨年：1月1日说昨天 = 去年12月31日",
    "2025年12月31日" in card2.anchor_relative_time(
        "昨天跨年了", ts_of("2026-01-01 10:00")))

print("\n== 二、语义层异常类（老 bug） ==")
chk("SemanticUnavailable 存在且是 Exception 子类",
    hasattr(semantic, "SemanticUnavailable") and
    issubclass(semantic.SemanticUnavailable, Exception))
_caught = False
try:
    raise semantic.SemanticUnavailable("no model")
except semantic.SemanticUnavailable:
    _caught = True
chk("能被自己 except 住（不再是 NameError）", _caught)

print("\n== 三、身体参数：校验 ==")
_before = V.current_profile()
chk("current_profile 带出 11 个键（9 标量 + 2 表）", len(_before) == 11)
chk("默认值可读（REST_SYMP 在 0~1）",
    isinstance(_before.get("REST_SYMP"), float))
_ok, _why = V.validate_profile({"HR_CAP": 999})
chk("超范围拒收", not _ok and "超出范围" in _why, _why)
_ok, _why = V.validate_profile({"不存在的键": 1})
chk("未知键拒收", not _ok and "未知参数" in _why, _why)
_ok, _why = V.validate_profile({"H_BASE": {"dopa": 999}})
chk("激素基线超 0~100 拒收", not _ok, _why)
_ok, _why = V.validate_profile({"H_BASE": {"没这个激素": 20}})
chk("不存在的激素名拒收", not _ok and "没有" in _why, _why)
_ok, _why = V.validate_profile({"REST_SYMP": "abc"})
chk("非数字拒收", not _ok and "不是数字" in _why, _why)

print("\n== 四、身体参数：生效 / 进卡 / 重启后还在 ==")
_ok, _why = V.apply_profile({"HR_CAP": 150.0, "REST_SYMP": 0.2})
chk("apply_profile 生效", _ok and V.HR_CAP == 150.0 and V.REST_SYMP == 0.2, _why)
V.apply_profile(_before)          # 先还原，下面的卡测试用干净默认值

tmp = tempfile.mkdtemp(prefix="t109_")
dst = os.path.join(tmp, "card.db")
_cardcopy.copy_card_db(os.path.join(ROOT, "card.db"), dst)

c = card2.MemoryCard(dst)
_ok, _why = c.set_viscera_profile({"HR_CAP": 150.0,
                                   "HALFLIFE_H": {"cort": 30.0}})
chk("set_viscera_profile 收下", _ok, _why)
chk("写库后立刻生效", V.HR_CAP == 150.0 and V.HALFLIFE_H["cort"] == 30.0)
_ok2, _why2 = c.set_viscera_profile({"HR_CAP": 0})
chk("脏参数被拒且不落库", not _ok2 and V.HR_CAP == 150.0, _why2)

V.apply_profile(_before)          # 模拟「重启」：先把内存改回去
c2 = card2.MemoryCard(dst)        # 新连接 → 应该从卡里读回来
chk("重启后参数从卡里长回来（换机器也不变）",
    V.HR_CAP == 150.0 and V.HALFLIFE_H["cort"] == 30.0,
    "HR_CAP=%s cort_hl=%s" % (V.HR_CAP, V.HALFLIFE_H.get("cort")))
V.apply_profile(_before)

print("\n== 五、群聊当场不记 ==")
if HAVE_GUARD:
    chk("「草」太短 → 拦", G.group_noise("草")[0])
    chk("「嗯」太短 → 拦", G.group_noise("嗯")[0])
    chk("纯表情 → 拦", G.group_noise("😂😂😂")[0])
    chk("正常一句 → 放", not G.group_noise("今天海洋管理1区真热闹啊")[0])
    chk("复读 → 拦",
        G.group_noise("今天海洋管理1区真热闹啊",
                      ["今天海洋管理1区真热闹啊"])[0])
    chk("不同的两件事不算复读",
        not G.group_noise("刚才那个视频笑死我了",
                          ["今天海洋管理1区真热闹啊"])[0])
    chk("空消息 → 拦", G.group_noise("")[0])
    chk("判定理由说人话", G.group_noise("草")[1].startswith("太短"))
else:
    chk("qq_guard 可 import", False, "模块不在")

print("\n==============================================================")
print("  过 %d / 挂 %d" % (passed, failed))
print("==============================================================")
sys.exit(1 if failed else 0)
