# -*- coding: utf-8 -*-
"""T2-2 验收：身份锚 + CORE/LIVE 双区

验收标准（施工总表）：
  改一个字符 → 启动报「身份锚不匹配，可能是另一张卡」
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from identity import (IdentityAnchor, IdentityMismatch, CORE_KEYS, _sha)


def main():
    tmpdir = tempfile.mkdtemp()
    dbp = os.path.join(tmpdir, "mind.db")
    db = sqlite3.connect(dbp)
    db.row_factory = sqlite3.Row

    ident = {
        "user_qq": "100000000",
        "user_nickname": "使用者",
        "card_name": "小鲸鱼",
        "core_belief_1": "不骗主人",
        "core_belief_2": "不装成别人",
        "core_belief_3": "记得自己是记得的，忘了就承认忘了",
    }

    ok_all = True
    a = IdentityAnchor(db, card_dir=tmpdir)

    # 1) 未封锚时校验应当失败
    ok, bad = a.verify(ident)
    print(("  ✅" if bad == ["__unsealed__"] else "  ❌") +
          f" 未封锚时 verify → {bad}")
    ok_all &= (bad == ["__unsealed__"])

    # 2) 封锚
    a.seal(ident)
    print("  ✅ 封锚完成，sealed =", a.is_sealed())

    # 3) 正确值通过
    ok, bad = a.verify(ident)
    print(("  ✅" if ok else "  ❌") + f" 正确身份 verify → ok={ok} bad={bad}")
    ok_all &= ok

    # 4) 改一个字符 → 必须报错
    wrong = dict(ident)
    wrong["user_qq"] = "2080692107"   # 只改最后一位
    ok2, bad2 = a.verify(wrong)
    print(("  ✅" if (not ok2 and bad2 == ["user_qq"]) else "  ❌") +
          f" 改 QQ 一位 → ok={ok2} bad={bad2}")
    ok_all &= (not ok2 and bad2 == ["user_qq"])

    # 5) verify_or_die 抛 IdentityMismatch 且消息里有「另一张卡」
    try:
        a.verify_or_die(wrong)
        print("  ❌ 应该抛异常却没抛")
        ok_all = False
    except IdentityMismatch as e:
        has_kw = "另一张卡" in str(e)
        print(("  ✅" if has_kw else "  ❌") + f" verify_or_die 抛错：{e}")
        ok_all &= has_kw

    # 6) CORE 区物理只读：UPDATE 被触发器挡住
    try:
        db.execute("UPDATE core_zone SET val='我改主意了' WHERE key='user_qq'")
        db.commit()
        print("  ❌ CORE 区居然被改了")
        ok_all = False
    except sqlite3.DatabaseError as e:
        hit = "CORE" in str(e) or "只读" in str(e)
        print(("  ✅" if hit else "  ❌") + f" CORE 区 UPDATE 被挡：{e}")
        ok_all &= hit

    # 7) CORE 区 DELETE 也被挡
    try:
        db.execute("DELETE FROM core_zone WHERE key='user_qq'")
        db.commit()
        print("  ❌ CORE 区居然被删了")
        ok_all = False
    except sqlite3.DatabaseError as e:
        hit = "CORE" in str(e) or "只读" in str(e)
        print(("  ✅" if hit else "  ❌") + f" CORE 区 DELETE 被挡：{e}")
        ok_all &= hit

    # 8) CORE 值仍完好
    v = a.get_core("user_qq")
    print(("  ✅" if v == "100000000" else "  ❌") + f" CORE 值未变：{v}")
    ok_all &= (v == "100000000")

    # 9) LIVE 区随便写
    a.set_live("mood", "0.62")
    a.set_live("mood", "0.71")
    mv = a.get_live("mood")
    print(("  ✅" if mv == "0.71" else "  ❌") + f" LIVE 区可写且能覆盖：{mv}")
    ok_all &= (mv == "0.71")

    # 10) 卡文件落盘 + 明文可读
    p = a.save_card(ident)
    exists = os.path.exists(p)
    print(("  ✅" if exists else "  ❌") + f" 卡文件已落盘：{p}")
    ok_all &= exists
    loaded = a.load_card(p)
    print(("  ✅" if loaded.get("user_qq") == "100000000" else "  ❌") +
          f" 卡文件可读回：qq={loaded.get('user_qq')}")
    ok_all &= (loaded.get("user_qq") == "100000000")

    # 11) 重开数据库后锚还在
    db2 = sqlite3.connect(dbp)
    db2.row_factory = sqlite3.Row
    a2 = IdentityAnchor(db2, card_dir=tmpdir)
    ok3, bad3 = a2.verify(ident)
    print(("  ✅" if ok3 else "  ❌") + f" 重开库后校验通过：{ok3}")
    ok_all &= ok3

    # 12) 已封锚时不允许悄悄重封
    #     2026-10-31 改：seal() 早就不抛异常了（改成返回 (ok, bad)，
    #     和 verify() 同一个返回形状，调用方才能统一处理）。
    #     这条断言一直没过，不是产品坏了，是测试没跟着改。
    ok4, bad4 = a2.seal(ident)
    if (not ok4) and bad4 == ["__already_sealed__"]:
        print("  ✅ 已封锚拒绝重封：", bad4, "（不覆盖原锚）")
    else:
        print("  ❌ 已封锚却允许重封：ok=%s bad=%s" % (ok4, bad4))
        ok_all = False

    # 13) 性格：能写、能读、**不进身份锚**、写完触发器要装回去
    #     这是 2026-10-31 那次事故的回归测试：那天发现性格根本不在卡里，
    #     只写在 AGENTS.md，一插卡就没了。
    a2.set_persona("测试性格：傲娇、爱摸鱼。")
    got = a2.get_persona("")
    print(("  ✅" if got.startswith("测试性格") else "  ❌") + f" 性格可写可读：{got[:16]}")
    ok_all &= got.startswith("测试性格")

    anchors = {r[0] for r in db2.execute("SELECT key FROM identity_anchor")}
    in_anchor = "persona" in anchors
    print(("  ❌" if in_anchor else "  ✅") +
          f" 性格不进身份锚（锚里只有 {len(anchors)} 条）")
    ok_all &= (not in_anchor)

    try:
        db2.execute("UPDATE core_zone SET val='篡改' WHERE key='persona'")
        db2.commit()
        print("  ❌ 写完性格后触发器没装回去")
        ok_all = False
    except sqlite3.DatabaseError as e:
        hit = "CORE" in str(e) or "只读" in str(e)
        print(("  ✅" if hit else "  ❌") + f" 写完性格触发器仍只读：{str(e)[:24]}")
        ok_all &= hit

    # 性格不进锚 ⇒ 改性格不会让 verify 失败
    ok5, bad5 = a2.verify(ident)
    print(("  ✅" if ok5 else "  ❌") + f" 改性格后身份校验仍通过：{bad5}")
    ok_all &= ok5

    db.close()
    db2.close()

    print()
    print("=" * 50)
    print("T2-2 验收：" + ("全部通过 ✅" if ok_all else "有失败项 ❌"))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
