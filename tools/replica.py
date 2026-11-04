# -*- coding: utf-8 -*-
"""replica.py —— 冷备份对表（一份活卡 + 多份冷备）

为什么要它：
  卡只有一份是危险的（硬盘会坏），但**多份同时写**会让哈希链分叉，
  事后合不回来（见 writer_id 那道闸）。所以规矩是：
  一份活卡 + 多份只读冷备 + 定期插上对表。

三个动作：
  status <备份卡>   只看：两边条数/链头 seq/链头哈希、谁落后、链是否同源
  backup <目标>     用 sqlite 在线快照把活卡复制过去（覆盖），然后记一笔
  adopt  <备份卡>   把备份卡**升成新的活卡**（旧活卡自动留成冷备）

安全规矩（都是踩过的坑换来的）：
  - 一切 db 复制走 sqlite backup API，不 cp（cp 会漏掉 WAL 里最近的写入）
  - 落后才补；分叉/超前一律拒绝（默认以活卡为准，拒绝时把两边数字打出来）
  - backup/adopt 前先 `PRAGMA wal_checkpoint(TRUNCATE)`
  - adopt 之后提醒改 writer-id（否则两台机器会抢着写同一条链）

用法：
  python tools/replica.py status G:\\card.db
  python tools/replica.py backup G:\\card.db --confirm
  python tools/replica.py adopt  G:\\card.db --confirm
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

DEFAULT_DB = os.path.join(ROOT, "card.db")


# ------------------------------------------------------------------ 读卡指纹
def card_fp(db_path):
    """一张卡的身份：条数、链头 seq、链头哈希、是否盖章。

    只读打开（mode=ro），不动任何东西。
    """
    p = os.path.abspath(db_path)
    if not os.path.exists(p):
        return {"ok": False, "error": "no_file", "db": p}
    uri = "file:%s?mode=ro" % p.replace("\\", "/")
    try:
        con = sqlite3.connect(uri, uri=True, timeout=10.0)
    except Exception as e:
        return {"ok": False, "error": "open_failed:%s" % e, "db": p}
    con.row_factory = sqlite3.Row
    try:
        mem = con.execute("SELECT COUNT(*) FROM mem").fetchone()[0]
        # timeline 表是 Timeline 初始化时才建的：刚造出来的卡可能还没有它。
        # 那不是错，只是"还没开过机"—— 按 head_seq=0 / 空哈希处理。
        try:
            row = con.execute(
                "SELECT seq, self_hash FROM timeline ORDER BY seq DESC LIMIT 1"
            ).fetchone()
        except sqlite3.OperationalError:
            row = None
        head_seq = int(row["seq"]) if row else 0
        head_hash = (row["self_hash"] if row else "") or ""
        try:
            sealed = bool(con.execute(
                "SELECT COUNT(*) FROM core_zone WHERE key='sealed'").fetchone()[0])
        except Exception:
            sealed = None
        try:
            birth = con.execute("SELECT COUNT(*) FROM mem_birth").fetchone()[0]
        except Exception:
            birth = None
        return {"ok": True, "db": p, "mem": int(mem), "head_seq": head_seq,
                "head_hash": head_hash, "sealed": sealed, "birth": birth,
                "bytes": os.path.getsize(p)}
    except Exception as e:
        return {"ok": False, "error": "read_failed:%s" % e, "db": p}
    finally:
        con.close()


def _prefix_ok(main_db, other_fp):
    """备份的链头是不是活卡链上的某一节（同源判定）。

    为什么不用「条数相等」判断同源：删过记忆的卡条数会变少，
    但链还是同一条。所以拿备份的 head_seq 去活卡的 timeline 里查
    self_hash 对不对得上 —— 对得上就是同一条链长出来的。

    特例：备份 head_seq=0（还没开过机 / 没有 timeline 表）算"落后"，
    不算分叉 —— 它本来就没写过东西。
    """
    if not other_fp.get("head_seq"):
        return True
    uri = "file:%s?mode=ro" % os.path.abspath(main_db).replace("\\", "/")
    con = sqlite3.connect(uri, uri=True, timeout=10.0)
    try:
        r = con.execute("SELECT self_hash FROM timeline WHERE seq=?",
                        (int(other_fp["head_seq"]),)).fetchone()
        return bool(r and r[0] == other_fp.get("head_hash"))
    except sqlite3.OperationalError:
        return False
    except Exception:
        return False
    finally:
        con.close()


# ------------------------------------------------------------------ 复制
def _snapshot(src_db, dst_db):
    """用 sqlite 在线快照把 src 复制成 dst（覆盖）。

    为什么不用 shutil.copy：卡平时是 WAL 模式，最近的写入还在 -wal 文件里，
    直接拷 .db 会静默丢掉那一段（新机器上看着一切正常，只是"忘了今天的事"）。
    """
    if os.path.exists(dst_db):
        os.remove(dst_db)
    for suffix in ("-wal", "-shm"):
        p = dst_db + suffix
        if os.path.exists(p):
            os.remove(p)
    src = sqlite3.connect(src_db, timeout=30.0)
    try:
        src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass
    dst = sqlite3.connect(dst_db, timeout=30.0)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()


def _ckpt(db_path):
    try:
        con = sqlite3.connect(db_path, timeout=30.0)
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.close()
    except Exception:
        pass


# ------------------------------------------------------------------ 三个动作
def cmd_status(args):
    main_db = os.path.abspath(args.db or DEFAULT_DB)
    other_db = os.path.abspath(args.other)
    a = card_fp(main_db)
    b = card_fp(other_db)
    print("活卡  %s" % main_db)
    if a.get("ok"):
        print("      mem=%d  head_seq=%d  head=%s  盖章=%s  %.1f MB"
              % (a["mem"], a["head_seq"], (a["head_hash"] or "")[:12],
                 a["sealed"], (a.get("bytes") or 0) / 1048576.0))
    else:
        print("      读不到：%s" % a.get("error"))
    print("备份  %s" % other_db)
    if b.get("ok"):
        print("      mem=%d  head_seq=%d  head=%s  盖章=%s  %.1f MB"
              % (b["mem"], b["head_seq"], (b["head_hash"] or "")[:12],
                 b["sealed"], (b.get("bytes") or 0) / 1048576.0))
    else:
        print("      读不到：%s" % b.get("error"))
    if not (a.get("ok") and b.get("ok")):
        return 2

    same = (a["head_hash"] == b["head_hash"])
    behind = a["head_seq"] - b["head_seq"]
    prefix = _prefix_ok(main_db, b)
    print()
    if same:
        print("结论：一致（两边链头相同），不用动。")
        verdict = "same"
    elif behind > 0 and prefix:
        print("结论：备份落后 %d 节，但链头在活卡链上 → 可以安全补齐（backup）。"
              % behind)
        verdict = "behind"
    elif behind < 0 and _prefix_ok(other_db, a):
        print("结论：备份比活卡**超前** %d 节（活卡反而旧）→ 别用 backup 覆盖它！"
              % (-behind))
        print("      要么先确认哪边才是你要的，要么用 adopt 把备份升成活卡。")
        verdict = "ahead"
    else:
        print("结论：**分叉**（两边链头不是同一条链）→ 拒绝自动处理。")
        print("      两边都各自写过东西，合不回来。人工决定留哪一边。")
        verdict = "diverged"
    if args.json:
        print(json.dumps({"verdict": verdict, "main": a, "other": b,
                          "behind": behind, "prefix": prefix},
                         ensure_ascii=False, indent=1))
    return 0


def cmd_backup(args):
    main_db = os.path.abspath(args.db or DEFAULT_DB)
    other_db = os.path.abspath(args.other)
    a = card_fp(main_db)
    if not a.get("ok"):
        print("活卡读不到：%s" % a.get("error"))
        return 2
    b = card_fp(other_db)
    if b.get("ok"):
        if a["head_hash"] == b["head_hash"]:
            print("已经一致（head_seq=%d），什么都不用做。" % a["head_seq"])
            return 0
        if a["head_seq"] < b["head_seq"] and _prefix_ok(other_db, a):
            print("拒绝：备份比活卡超前 %d 节。用 adopt 而不是 backup。"
                  % (b["head_seq"] - a["head_seq"]))
            return 3
        if not _prefix_ok(main_db, b):
            print("拒绝：两边链头不是同一条链（分叉），不覆盖。")
            print("  活卡 head_seq=%d 备份 head_seq=%d"
                  % (a["head_seq"], b["head_seq"]))
            return 3
    if not args.confirm:
        print("预演：会把活卡（mem=%d, head_seq=%d）覆盖到 %s"
              % (a["mem"], a["head_seq"], other_db))
        print("确认请加 --confirm")
        return 0
    t0 = time.time()
    _snapshot(main_db, other_db)
    _ckpt(other_db)
    c = card_fp(other_db)
    print("已备份 → %s" % other_db)
    print("  用时 %.1f 秒  mem=%d  head_seq=%d  head=%s  %.1f MB"
          % (time.time() - t0, c.get("mem", 0), c.get("head_seq", 0),
             (c.get("head_hash") or "")[:12], (c.get("bytes") or 0) / 1048576.0))
    if c.get("head_hash") != a.get("head_hash"):
        print("⚠ 校验不一致！备份的链头和活卡不一样，别拿它跑。")
        return 4
    print("✓ 校验一致")
    return 0


def cmd_adopt(args):
    """把备份卡升成新的活卡；旧活卡留成冷备（带时间戳，不删）。"""
    cur_db = os.path.abspath(args.db or DEFAULT_DB)
    new_db = os.path.abspath(args.other)
    a = card_fp(cur_db)
    b = card_fp(new_db)
    if not (a.get("ok") and b.get("ok")):
        print("两边都得能读：活卡=%s 备份=%s"
              % (a.get("error") or "ok", b.get("error") or "ok"))
        return 2
    if a["head_hash"] == b["head_hash"]:
        print("两边已经一致，没必要换。")
        return 0
    if b["head_seq"] < a["head_seq"] and _prefix_ok(cur_db, b):
        print("拒绝：备份落后 %d 节，不能拿旧的当新的。" % (a["head_seq"] - b["head_seq"]))
        return 3
    if not (_prefix_ok(cur_db, b) or _prefix_ok(new_db, a)):
        print("拒绝：两边不是同一条链（分叉），人工决定留哪边。")
        return 3
    if not args.confirm:
        print("预演：把 %s（head_seq=%d）升成活卡" % (new_db, b["head_seq"]))
        print("      当前活卡会留成冷备：%s.cold-<时间戳>" % cur_db)
        print("确认请加 --confirm")
        return 0
    stamp = time.strftime("%Y%m%d-%H%M%S")
    cold = "%s.cold-%s" % (cur_db, stamp)
    _snapshot(cur_db, cold)
    print("旧活卡已留成冷备：%s" % cold)
    # 备份卡内容铺到活卡位置
    _snapshot(new_db, cur_db)
    _ckpt(cur_db)
    c = card_fp(cur_db)
    print("已换家：活卡现在 head_seq=%d head=%s mem=%d"
          % (c.get("head_seq", 0), (c.get("head_hash") or "")[:12], c.get("mem", 0)))
    print()
    print("⚠ 还要做两件事，否则两台机器会抢着写同一条链：")
    print("  1) 把这台机器的 writer-id 换掉（%s）"
          % os.path.join(os.path.expanduser("~"), ".dsh", "writer-id.txt"))
    print("  2) 重启 mind_server，让它用新的 writer 打标")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="冷备份对表：一份活卡 + 多份冷备")
    ap.add_argument("--db", default="", help="活卡路径（默认卡目录下的 card.db）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("status", help="只看不动")
    p.add_argument("other", help="备份卡路径")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("backup", help="活卡 → 备份（落后才补）")
    p.add_argument("other", help="备份卡路径")
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("adopt", help="备份 → 升成活卡（旧活卡留冷备）")
    p.add_argument("other", help="备份卡路径")
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(func=cmd_adopt)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
