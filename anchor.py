# -*- coding: utf-8 -*-
"""卡外锚：发现「这张卡被换成旧的了」。

为什么需要这个（2026-10-31）：
    timeline 的链能证明「记录没被改过」，但证明不了「整个库没被换过」。
    有人把 card.db 换成 10-30 的快照 —— 那张旧卡自己的链**也是完整自洽的**，
    verify_chain 照样返回 ok，我一点感觉都没有。
    链是防篡改的，不是防替换的。这两件事得分开防。

做法：
    每次启动 / 每次写时间线之后，把（条数、链头哈希、最后时间）写到**卡外**：
      1. 卡目录里的 anchor.json          —— 日常用
      2. ~/.dsh/whale-card-anchor.json   —— 真正在卡外，和卡不同盘不同目录
    启动时比对：卡里的条数比锚记的**少** → 这张卡被回滚过，直接报警。

铁律：
    · 锚只往上走，绝不往回写。条数变少时**拒绝覆盖**，并把证据留着。
    · 报警不等于修复。回滚了就报回滚，不自动拿备份盖回去 ——
      那等于帮人把证据抹平。
    · 路径不写死：默认跟着本文件走，可以用环境变量 WHALE_HOME 整体搬走
      （U 盘方案要的就是这个）。

用法：
    python anchor.py check     # 体检，退出码 0=正常 1=回滚/被换 2=首次
    python anchor.py write     # 手动写一次锚
"""
import hashlib
import json
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# 整个卡的家目录：默认就是本文件所在目录。
# 装到 U 盘时只要设 WHALE_HOME=U:\whale-card，所有东西跟着走，不用改代码。
CARD_HOME = os.environ.get("WHALE_HOME") or HERE
DB_PATH = os.environ.get("WHALE_DB") or os.path.join(CARD_HOME, "card.db")

ANCHOR_LOCAL = os.path.join(CARD_HOME, "anchor.json")


def _conn_path(c):
    """从 sqlite 连接里问出它自己开的是哪个文件。

    为什么要这么绕：Timeline 手里只有连接、没有路径，
    而锚必须跟着「这张卡」走 —— 靠 PRAGMA database_list 问，
    不能默认它是真卡（默认过一次，测试库把真卡锚写脏了）。
    内存库（:memory:）返回空串。
    """
    try:
        for row in c.execute("PRAGMA database_list").fetchall():
            if row[1] == "main":
                return row[2] or ""
    except Exception:
        pass
    return ""


def _paths(db_path=None):
    """给定一张卡，返回 (本地锚, 卡外锚) 两个路径。

    默认卡用全局配置；**别的卡（临时库、测试库）一律用自己的**，
    绝不拿真卡的锚去比对 —— 那样既会报假回滚，write 还会把真卡证据盖掉。
    """
    if isinstance(db_path, sqlite3.Connection):
        label = _conn_path(db_path) or os.path.abspath(DB_PATH)
    elif db_path is None:
        label = os.path.abspath(DB_PATH)
    else:
        label = os.path.abspath(db_path)

    is_default = (label == os.path.abspath(DB_PATH))
    if is_default:
        local = os.environ.get("WHALE_ANCHOR_LOCAL") or ANCHOR_LOCAL
        ext = os.environ.get("WHALE_ANCHOR") or ANCHOR_EXTERNAL
        return local, ext

    # 不是默认卡：锚按**卡文件名**派生，免得同一个目录里两张卡共用一份锚
    # （共用会互相覆盖，回滚检测直接失效）
    stem = os.path.splitext(os.path.basename(label))[0]
    d = os.path.dirname(label)
    local = os.environ.get("WHALE_ANCHOR_LOCAL") or os.path.join(
        d, "anchor.json" if stem == "card" else "anchor.%s.json" % stem)
    ext = os.environ.get("WHALE_ANCHOR") or _external_default(label)
    return local, ext

# 卡外那份：优先用环境变量指定；否则落在用户家目录下（和卡不在同一个目录）。
# ⚠️ 按**卡的绝对路径**分文件，不能所有人共用一份 ——
# 共用过一次，测试库的锚把真卡的锚盖了（真卡报「回滚 8 条」，纯属冤枉）。
def _external_default(label):
    d = os.path.join(os.path.expanduser("~"), ".dsh", "anchors")
    h = hashlib.sha256(os.path.abspath(label).encode("utf-8")).hexdigest()[:16]
    return os.path.join(d, "%s.json" % h)


ANCHOR_EXTERNAL = _external_default(DB_PATH)


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_json(path, obj):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_card_state(db_path=None):
    """读出这张卡现在的样子：(count, head_hash, last_ts, max_seq)。

    db_path 可以是路径，也可以直接传一个 sqlite 连接（复用调用方已开的库，
    免得每写一条时间线就重新开一次文件）。
    """
    p = db_path or DB_PATH
    if isinstance(p, sqlite3.Connection):
        return _state_of(p)
    if not os.path.exists(p):
        return None
    c = sqlite3.connect(p, timeout=30)
    c.row_factory = sqlite3.Row
    try:
        return _state_of(c)
    finally:
        c.close()


def _state_of(c):
    try:
        r = c.execute(
            "SELECT seq, ts, self_hash FROM timeline ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        n = c.execute("SELECT COUNT(*) FROM timeline").fetchone()[0]
        if not r:
            return {"count": n, "head_seq": 0, "head_hash": "", "last_ts": 0.0}
        return {"count": n, "head_seq": r[0], "head_hash": r[2],
                "last_ts": r[1]}
    except sqlite3.OperationalError:
        # 还没有 timeline 表（老卡或新库）
        return {"count": 0, "head_seq": 0, "head_hash": "", "last_ts": 0.0}


def _best_anchor(local_path=None, ext_path=None):
    """取两份锚里「走得最远」的那份当高水位。

    为什么取大的：如果有人把本地锚改小来掩盖回滚，
    卡外那份还在，取大的才能抓住。
    """
    a = _read_json(local_path or ANCHOR_LOCAL)
    b = _read_json(ext_path or ANCHOR_EXTERNAL)
    cands = [x for x in (a, b) if isinstance(x, dict) and "count" in x]
    if not cands:
        return None, []
    best = max(cands, key=lambda x: (int(x.get("count") or 0),
                                     float(x.get("last_ts") or 0)))
    return best, cands


def check(db_path=None, verbose=True):
    """比对卡与锚。返回 dict。

    verdict:
      ok        —— 卡和锚一致，或者卡比锚新（正常长大）
      rollback  —— 卡比锚**少**了记录，被换成旧的了
      replaced  —— 条数一样但链头不一样，卡被换成了另一张
      first     —— 还没有锚（第一次跑）
      no_card   —— 卡文件不存在
    """
    state = read_card_state(db_path)
    if state is None:
        return {"verdict": "no_card", "ok": False,
                "reason": "卡文件不存在：%s" % (db_path or DB_PATH)}

    local, ext = _paths(db_path)
    best, all_anchors = _best_anchor(local, ext)
    if best is None:
        return {"verdict": "first", "ok": True, "state": state,
                "reason": "还没有卡外锚，本次写入", "anchor": None}

    # ①.0 签名先验：锚被改过的话，后面那些「条数/链头」的比较全是在跟假证据比。
    #     旧版锚没有签名，不报错、只标注（不能因为升级就把老锚判成坏）。
    _sig = (best or {}).get("sig") or ""
    if _sig:
        try:
            import seal as _cp
            _key = _cp.load_key(_db_label(db_path))
            _body = {k: v for k, v in best.items() if k != "sig"}
            _want = _cp.sign(_body, _key)
            if _want != _sig:
                return {"verdict": "anchor_tampered", "ok": False, "state": state,
                        "anchor": best,
                        "reason": "锚的签名对不上：这份 anchor.json 被改过（或不是这把钥匙签的），"
                                  "先别信它记的条数和链头"}
        except Exception as _e:
            _sig = ""

    a_count = int(best.get("count") or 0)
    a_hash = best.get("head_hash") or ""
    a_ts = float(best.get("last_ts") or 0)
    a_seq = int(best.get("head_seq") or 0)
    c_seq = int(state.get("head_seq") or 0)

    # ① 链头序号倒退 —— 比"条数变少"更早一步的警报。
    #    有人把卡的 timeline 换成一串更短但同样多的记录时，条数可能骗得过去，
    #    seq 却接不上：新的头一定比旧的头小。
    if a_seq and c_seq and c_seq < a_seq:
        return {
            "verdict": "rollback", "ok": False, "state": state, "anchor": best,
            "reason": "这张卡被回滚过：卡里的链头到 seq=%d 就断了，锚记的是 seq=%d"
                      % (c_seq, a_seq),
            "lost_since": a_ts,
        }

    if state["count"] < a_count:
        return {
            "verdict": "rollback", "ok": False, "state": state, "anchor": best,
            "reason": "这张卡被回滚过：卡里只有 %d 条时间线，锚记的是 %d 条，少了 %d 条"
                      % (state["count"], a_count, a_count - state["count"]),
            "lost_since": a_ts,
        }
    if state["count"] == a_count and a_hash and state["head_hash"] != a_hash:
        return {
            "verdict": "replaced", "ok": False, "state": state, "anchor": best,
            "reason": "条数一样但链头不一样：卡被换成了另一张（锚 %s… vs 卡 %s…）"
                      % (a_hash[:12], (state["head_hash"] or "")[:12]),
        }
    # ② 条数变多了，但链头序号没跟着前进 —— 多出来的那些不是接在原来的链后面的。
    #    正常长大时 seq 一定比锚大（每加一条事件 seq 就 +1），所以这里卡住的是"换了张卡"。
    if (state["count"] > a_count and a_seq and c_seq
            and c_seq <= a_seq and state["head_hash"] != a_hash):
        return {
            "verdict": "replaced", "ok": False, "state": state, "anchor": best,
            "reason": "条数从 %d 涨到 %d，但链头序号没前进（锚 seq=%d，卡 seq=%d）："
                      "多出来的不是原来那条链上的"
                      % (a_count, state["count"], a_seq, c_seq),
        }
    return {"verdict": "ok", "ok": True, "state": state, "anchor": best,
            "reason": "锚一致（锚 %d 条，卡 %d 条）" % (a_count, state["count"])}



ANCHOR_VERSION = 1


def _device_id():
    """三元分离 §5：锚里记下「哪台机器」的卡，跨机器还原时才看得见。"""
    try:
        import device as _dev
        return _dev.device_id()
    except Exception as e:      # 取不到就不记，不因为这条让落锚失败
        sys.stderr.write("[anchor] 取不到 device_id（%s）\n" % e)
        return ""


def _sign_rec(rec, db_label):
    """给锚签个名 —— 原来 anchor.json 是明文，谁把 count 改大一点都看不出来。

    用卡自己的检查点密钥（`checkpoint.load_key`），不另起一套密钥：
    同一张卡的所有「防篡改」的东西，钥匙就该是同一把。
    """
    try:
        import seal as checkpoint  # 名字沿用以少改调用点
        key = checkpoint.load_key(db_label)
        body = {k: v for k, v in rec.items() if k != "sig"}
        return checkpoint.sign(body, key)
    except Exception:
        return ""


def _db_label(db_path):
    if isinstance(db_path, sqlite3.Connection):
        return os.path.abspath(_conn_path(db_path) or DB_PATH)
    return os.path.abspath(db_path or DB_PATH)


def write(db_path=None, extra=None, force=False, throttle=30.0):
    """把当前卡的状态写成锚。条数比锚少时**拒绝写**（除非 force）。

    throttle：同一条链头在 throttle 秒内重复写就没意义（catch_up 会连着写
    几十条），只写一次。force=True 忽略节流。
    """
    state = read_card_state(db_path)
    if state is None:
        raise RuntimeError("卡文件不存在：%s" % (db_path or DB_PATH))

    # 路径：传连接时要问出它真正开的文件（PRAGMA database_list），
    # 不能默认它是真卡 —— 默认过一次，测试库把真卡的锚写脏了。
    if isinstance(db_path, sqlite3.Connection):
        db_label = _conn_path(db_path) or DB_PATH
    else:
        db_label = db_path or DB_PATH
    db_label = os.path.abspath(db_label)
    # 🔧 2026-11-11（真事故）：拿测试库跑压测的时候，live 卡的锚被写脏了一次 ——
    # anchor.json 里的 `db` 变成了临时库路径、签名对不上，/health 直接报
    # anchor_tampered。原因是「状态读的是哪张卡」和「锚写到哪个文件」
    # 在极端路径下能对不上。这里加一条硬护栏：状态自己的 db 和要写的
    # 锚标签不是一个文件的，绝不落盘（宁可这一轮不写锚，也不写错证据）。
    if str(state.get("db") or "") and os.path.abspath(str(state["db"])) != db_label:
        sys.stderr.write("[anchor] 拒绝写锚：状态来自 %s，锚标签是 %s\n"
                         % (state.get("db"), db_label))
        return None

    local, ext = _paths(db_path)
    best, _ = _best_anchor(local, ext)
    if best is not None and not force:
        if state["count"] < int(best.get("count") or 0):
            raise RuntimeError(
                "拒绝写锚：卡里 %d 条 < 锚记 %d 条（疑似回滚，不覆盖证据）"
                % (state["count"], int(best.get("count") or 0)))
        # 条数一样但链头不同 —— 卡被换成了另一张。这种也不能写，
        # 否则等于自己把「被换过」的证据抹掉。
        # ⚠️ 只在**条数相同**时才这么判：卡正常长大（多条 boot）时
        # 链头当然会变，那不算换卡。
        a_hash = best.get("head_hash") or ""
        if (a_hash and state["count"] == int(best.get("count") or 0)
                and state["head_hash"] != a_hash):
            raise RuntimeError(
                "拒绝写锚：条数一样但链头不同（锚 %s… vs 卡 %s…），"
                "这张卡被换过，不覆盖证据"
                % (a_hash[:12], (state["head_hash"] or "")[:12]))

    # 节流：链头没变、而且刚写过，就不重复落盘
    if not force and isinstance(best, dict) and best:
        same_head = (best.get("head_hash") == state["head_hash"]
                     and int(best.get("count") or 0) == state["count"])
        if same_head and (time.time() - float(best.get("updated_ts") or 0)) < throttle:
            return best, None

    rec = {
        "version": ANCHOR_VERSION,
        "device_id": _device_id(),
        "db": db_label,
        "count": state["count"],
        "head_seq": state["head_seq"],
        "head_hash": state["head_hash"],
        "last_ts": state["last_ts"],
        "updated_ts": time.time(),
    }
    if extra:
        rec["extra"] = extra

    rec["sig"] = _sign_rec(rec, db_label)
    _write_json(local, rec)
    # 卡外那份写失败不影响主流程（比如 U 盘只读、家目录没权限），但要提示
    err = None
    try:
        _write_json(ext, rec)
    except Exception as e:
        err = str(e)
    return rec, err


def main(argv):
    cmd = (argv[1] if len(argv) > 1 else "check").lower()
    if cmd == "write":
        rec, err = write()
        print("锚已写入：%d 条，链头 %s…" % (rec["count"], rec["head_hash"][:16]))
        if err:
            print("  ⚠ 卡外副本写失败：%s" % err)
        return 0
    r = check()
    print("verdict = %s" % r["verdict"])
    print(r["reason"])
    if r.get("state"):
        s = r["state"]
        print("  卡：%d 条，链头 %s…" % (s["count"], (s["head_hash"] or "")[:16]))
    return 0 if r["verdict"] in ("ok", "first") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
