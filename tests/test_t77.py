# -*- coding: utf-8 -*-
"""test_t77.py —— 写入闸：谁能写这张卡（writer_id + 拒写）

设计意图（2026-11-03 深夜）：
  两台机器同时往一张卡上写，会各自长出一条链，事后**合不回去**。
  所以链上每条记一个 writer；发现链头不是我写的 → 拒写、报错、只读。
  这是保险丝，不是防伪章：能发现事故，但挡不住恶意改库。
"""
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import timeline  # noqa: E402

OK = 0
BAD = []


def ck(name, cond, extra=""):
    global OK
    if cond:
        OK += 1
        print("  ✅ %s" % name)
    else:
        BAD.append(name)
        print("  ❌ %s %s" % (name, extra))


def _con(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def main():
    tmp = tempfile.mkdtemp(prefix="t77_")
    try:
        db = os.path.join(tmp, "card.db")

        # ---- 1. 写入时打上 writer ----
        con = _con(db)
        tl = timeline.Timeline(con, writer="A")
        tl.append("boot", "第一次启动")
        tl.append("talk", "说了句话")
        rows = con.execute("SELECT seq, kind, writer FROM timeline ORDER BY seq").fetchall()
        ck("两条都写了 writer", [r["writer"] for r in rows] == ["A", "A"],
           [r["writer"] for r in rows])
        ck("链头 writer 是 A", tl.head_writer() == "A", tl.head_writer())
        ok, bad = tl.verify_chain()
        ck("加了 writer 列，链仍然合法（哈希没动）", ok, bad[:2])

        # ---- 2. 换人写：默认拒 ----
        con2 = _con(db)
        tl_b = timeline.Timeline(con2, writer="B")
        ck("B 看到链头是 A", tl_b.head_writer() == "A", tl_b.head_writer())
        raised = False
        try:
            tl_b.append("boot", "B 想写")
        except timeline.ForeignWriter as e:
            raised = True
            ck("拒写理由里带两个 writer", "A" in str(e) and "B" in str(e), str(e))
        ck("ForeignWriter 被抛出", raised)
        n = con2.execute("SELECT COUNT(*) FROM timeline").fetchone()[0]
        ck("被拒后没多出记录", n == 2, n)

        # ---- 3. 明确要覆盖时能写（给人工迁移用）----
        tl_b.append("boot", "强行写", allow_foreign=True)
        ck("allow_foreign 后可写", tl_b.head_writer() == "B", tl_b.head_writer())
        ok, bad = tl_b.verify_chain()
        ck("强写之后链仍完整", ok, bad[:2])

        # ---- 4. 老卡没有 writer 列时不当成别人 ----
        db2 = os.path.join(tmp, "old.db")
        con3 = _con(db2)
        con3.execute("CREATE TABLE timeline(seq INTEGER PRIMARY KEY AUTOINCREMENT,"
                     " ts REAL NOT NULL, kind TEXT NOT NULL,"
                     " note TEXT NOT NULL DEFAULT '', prev_hash TEXT NOT NULL DEFAULT '',"
                     " self_hash TEXT NOT NULL DEFAULT '')")
        con3.commit()
        tl3 = timeline.Timeline(con3, writer="C")
        ck("老卡链头 writer 为空", tl3.head_writer() == "", tl3.head_writer())
        tl3.append("boot", "老卡第一次被 C 写")
        ck("老卡可以被新 writer 接管", tl3.head_writer() == "C", tl3.head_writer())

        # ---- 5. writer_id：稳定、可被环境变量覆盖 ----
        old = os.environ.pop("WHALE_WRITER", None)
        w1 = timeline.writer_id()
        w2 = timeline.writer_id()
        ck("writer_id 稳定且非空", w1 and w1 == w2, (w1, w2))
        ck("writer_id 带前缀", w1.startswith("w-"), w1)
        os.environ["WHALE_WRITER"] = "w-forced"
        ck("环境变量可覆盖", timeline.writer_id() == "w-forced", timeline.writer_id())
        if old is None:
            os.environ.pop("WHALE_WRITER", None)
        else:
            os.environ["WHALE_WRITER"] = old

        con.close()
        con2.close()
        con3.close()
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n结果：%d 通过，%d 失败" % (OK, len(BAD)))
    if BAD:
        print("失败项：", BAD)
    return 0 if not BAD else 1


if __name__ == "__main__":
    raise SystemExit(main())
