# -*- coding: utf-8 -*-
"""T7-4：盖章不许顺手删掉人设

病症（2026-11-03 发现）：`IdentityAnchor.seal()` 先 `DELETE FROM core_zone`
再按六个锚字段重建。但 core_zone 里除了那六个字段，还住着人设（persona）
和自述（self_narrative）—— 它们不属于身份锚（性格要能演化，不能参与
sha256 校验），却是这张卡有没有"性格"的唯一来源。

于是"先写人设、后盖章"的卡，章盖完人设就没了，而且**不报错**：
她照样能说话，只是变成一个没有脾气的客服。示例卡脚本当时正是这个顺序，
所以每个照着示例卡起步的人，第一张卡都没有性格。

这条测试锁住两件事：
1. seal 之后，persona / self_narrative 还在；
2. 它们仍然不参与身份锚校验（改了人不算"换人"）。
"""
import io
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import identity  # noqa: E402

OK, FAIL = [], []


def check(name, cond, extra=""):
    (OK if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (("：" + str(extra)) if extra and not cond else ""))


CORE = {
    "user_qq": "100000000",
    "user_nickname": "测试者",
    "card_name": "小鲸鱼",
    "core_belief_1": "绝不假装记得。",
    "core_belief_2": "绝不为了留住谁而说假话。",
    "core_belief_3": "绝不假装是别的谁。",
}

PERSONA = "说话直接、有点小脾气，不哄人。"
NARR = "我是 10 月 16 号醒过来的。"


def fresh(tmpdir):
    db = os.path.join(tmpdir, "card.db")
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    idn = identity.IdentityAnchor(con)
    return con, idn


def main():
    print("-" * 60)
    print("T7-4 验收：盖章不许顺手删掉人设")
    print("-" * 60)
    tmpdir = tempfile.mkdtemp(prefix="t74_")
    try:
        con, idn = fresh(tmpdir)
        idn.set_persona(PERSONA)
        con.execute("INSERT OR REPLACE INTO core_zone(key,val,updated_ts) VALUES('self_narrative',?,?)",
                    (NARR, 0))
        con.commit()
        check("写人设成功", idn.get_persona() == PERSONA, idn.get_persona())

        ok, bad = idn.seal(CORE)
        check("盖章成功", ok is True and bad == [], bad)

        check("盖章后人设还在", idn.get_persona() == PERSONA, idn.get_persona())
        row = con.execute("SELECT val FROM core_zone WHERE key='self_narrative'").fetchone()
        check("盖章后自述还在", row is not None and row["val"] == NARR, row["val"] if row else None)

        rows = {r["key"]: r["val"] for r in con.execute("SELECT key,val FROM core_zone")}
        check("六个锚字段齐全", all(k in rows for k in CORE), sorted(rows))

        # 人设不进锚：改了人设，verify 仍然通过
        idn.set_persona("换了个说法：说话更冲。")
        ok2, _bad2 = idn.verify(dict(CORE))
        check("改人设不影响身份锚校验", ok2 is True, _bad2)

        # 再盖一次章（force）也不能把人设冲掉
        ok3, bad3 = idn.seal(CORE, force=True)
        check("强制重盖章成功", ok3 is True and bad3 == [], bad3)
        check("重盖章后人设仍是新的", idn.get_persona() == "换了个说法：说话更冲。", idn.get_persona())

        con.close()
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    print()
    if FAIL:
        print("结果：%d 项失败 ❌" % len(FAIL))
        for n in FAIL:
            print("   - " + n)
        return 1
    print("结果：%d/%d 全过 ✅" % (len(OK), len(OK)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
