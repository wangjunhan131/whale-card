# -*- coding: utf-8 -*-
"""T7-2 验收：锚的"长大也要查"那条腿

11-02 模拟搬家时发现的弱点：
    把锚里的 head_hash 改坏、条数不动，服务启动会先追加一条 boot 事件，
    卡就"比锚多一条"→ check() 走「卡比锚新＝正常长大」分支 → 直接报 ok。
    也就是说：锚能抓"换了一张旧卡"，抓不住"卡长大以后链头被动过手脚"。

这一组钉住四条判定（前三条本来就有，后两条是这次补的）：
    1. 没锚                     → first（且不算坏）
    2. 正常长大（条数+、seq+）   → ok
    3. 卡比锚少                 → rollback
    4. 条数变多但链头 seq 倒退   → rollback（新规则①）
    5. 条数变多、seq 没前进      → replaced（新规则②）
    6. 条数一样、链头不同        → replaced
    7. 完全一致                 → ok
    8. 锚被改成"未来"（条数更多）→ rollback
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import anchor as A

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✅ %s" % name)
    else:
        FAIL += 1
        print("  ❌ %s  %s" % (name, extra))


def make_card(path, seqs, head_hash=None):
    """造一张只有 timeline 的假卡：事件序号由 seqs 给（可以故意留空档）。

    head 的 self_hash 默认 h<seq>，可以用 head_hash 覆盖 —— 用来模拟"链头被动过"。
    """
    if os.path.exists(path):
        os.remove(path)
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE IF NOT EXISTS timeline(seq INTEGER PRIMARY KEY, ts REAL,"
              " kind TEXT, note TEXT, prev_hash TEXT, self_hash TEXT)")
    for s in seqs:
        c.execute("INSERT INTO timeline(seq,ts,kind,note,prev_hash,self_hash)"
                  " VALUES(?,?,?,?,?,?)", (s, 1000.0 + s, "alive", "", "", "h%s" % s))
    top = max(seqs)
    c.execute("UPDATE timeline SET self_hash=? WHERE seq=?",
              (head_hash if head_hash is not None else "h%s" % top, top))
    c.commit()
    c.close()


def bump_anchor(ext, local, d_seq=0, d_count=0):
    """手动把两份锚都改成"走得比卡远"，模拟被篡改/未来的锚。"""
    for p in (ext, local):
        if not os.path.exists(p):
            continue
        a = json.load(open(p, encoding="utf-8"))
        a["head_seq"] = int(a.get("head_seq") or 0) + d_seq
        a["count"] = int(a.get("count") or 0) + d_count
        with open(p, "w", encoding="utf-8") as f:
            json.dump(a, f, ensure_ascii=False)


def main():
    tmp = tempfile.mkdtemp(prefix="t72-")
    db = os.path.join(tmp, "card.db")
    local = os.path.join(tmp, "anchor.json")
    ext = os.path.join(tmp, "ext-anchor.json")
    # 两份锚都指到临时目录，别碰真卡那两根钉子
    os.environ["WHALE_ANCHOR_LOCAL"] = local
    os.environ["WHALE_ANCHOR"] = ext

    print("\n=== 1) 还没有锚 → first ===")
    make_card(db, list(range(1, 11)))
    r = A.check(db)
    check("首次判定 first", r["verdict"] == "first", r)
    check("first 不算坏", r["ok"] is True, r)

    print("\n=== 2) 正常长大 → ok ===")
    A.write(db, force=True)
    make_card(db, list(range(1, 14)))
    r = A.check(db)
    check("条数变多、seq 前进 → ok", r["verdict"] == "ok", r)

    print("\n=== 3) 卡比锚少 → rollback ===")
    A.write(db, force=True)
    make_card(db, list(range(1, 10)))
    r = A.check(db)
    check("卡比锚少 → rollback", r["verdict"] == "rollback", r)
    check("理由说得清（回滚）", "回滚" in r["reason"], r["reason"])

    print("\n=== 4) 条数变多、链头 seq 倒退 → rollback（新规则①）===")
    make_card(db, list(range(91, 101)))       # 10 条，头 seq=100
    A.write(db, force=True)
    make_card(db, list(range(1, 13)))         # 12 条（更多），头 seq=12（更小）
    r = A.check(db)
    check("条数涨但 seq 倒退 → rollback", r["verdict"] == "rollback", r)
    check("理由里点了 seq", "seq=" in r["reason"], r["reason"])

    print("\n=== 5) 条数变多、seq 没前进 → replaced（新规则②）===")
    # 锚：10 条，最大的 seq 跳到 100（头 seq=100）
    make_card(db, list(range(1, 10)) + [100])
    A.write(db, force=True)
    # 新卡：12 条（更多），但最大 seq 还是 100 —— 多出来的塞在中间，不是接在链尾
    make_card(db, list(range(1, 10)) + [11, 12, 100], head_hash="other-head")
    r = A.check(db)
    check("条数涨、seq 没前进 → replaced", r["verdict"] == "replaced", r)
    check("理由里说明白了", "没前进" in r["reason"], r["reason"])

    print("\n=== 6) 条数一样、链头不同 → replaced ===")
    make_card(db, list(range(1, 11)), head_hash="aaa")
    A.write(db, force=True)
    make_card(db, list(range(1, 11)), head_hash="bbb")
    r = A.check(db)
    check("同条数异链头 → replaced", r["verdict"] == "replaced", r)

    print("\n=== 7) 完全一致 → ok ===")
    A.write(db, force=True)
    r = A.check(db)
    check("一致 → ok", r["verdict"] == "ok", r)

    print("\n=== 8) 锚被改成「未来」（条数比卡多）→ rollback ===")
    bump_anchor(ext, local, d_seq=5, d_count=5)
    r = A.check(db)
    check("锚走得更远 → rollback", r["verdict"] == "rollback", r)

    for k in ("WHALE_ANCHOR_LOCAL", "WHALE_ANCHOR"):
        os.environ.pop(k, None)
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
