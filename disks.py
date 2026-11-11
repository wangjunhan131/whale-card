# -*- coding: utf-8 -*-
"""盘子登记 / 备份 / 同步 / 主备切换（V1）

只解决一件事：同一张卡的多个副本，谁在演「活的」，谁是冷备，
以及两块盘同时插着的时候怎么安全地同步、怎么安全地换角色。

三条硬规矩写在这里，别处不许绕过：
1. 认盘不认盘符 —— 用卷序列号 + 卡实例号（时间线第 1 条事件的哈希前 8 位）。
   今天 E: 明天 F: 是常态，盘符不能进任何持久化字段。
2. 每块盘自己带一张身份证（DISK-ROLE.json），注册表只是索引。
   只存注册表的话，备份盘拿到别的机器上就不知道自己是备份。
3. 会写盘的动作（backup/sync/switch）都要求：服务停下 + checkpoint WAL。
   热拷会静默丢掉 WAL 里还没合并的写入 —— 这事 11-02 实测过（14.1 MB）。
"""
import ctypes
import hashlib
import io
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CARD_DB = os.path.join(HERE, "card.db")
REG_NAME = "DISKS.json"
ROLE_NAME = "DISK-ROLE.json"
BUNDLE_DIR = "whale-card"
PY = sys.executable

ROLE_MAIN = "main"
ROLE_BACKUP = "backup"

MANIFEST_NAME = "PORTABLE-MANIFEST.json"
# 2 = 2026-11-10 补全字段：身份四件套 / 快照方法 / card_sha256 + files 列表。
# 旧包是 1 或无版本号（files 是 {"路径": {...}} 字典），verify_manifest 两种都认。
MANIFEST_SCHEMA_VERSION = 2
MANIFEST_REQUIRED = ("schema_version", "identity_id", "instance_id",
                     "head_seq", "head_hash", "writer_epoch",
                     "snapshot_method", "card_sha256", "card_bytes", "files")

# ---- 写者纪元 / 租约（V7 P0-4，2026-11-10）----
# 纪元跟着卡走（搬过去要留着），租约不跟（易失，靠心跳续命）。
WRITER_EPOCH_NAME = "WRITER-EPOCH.json"
WRITER_LEASE_NAME = "WRITER-LEASE.json"
TAKEOVER_DONE_NAME = "TAKEOVER-DONE.json"
LEASE_TTL = 90.0          # 秒：心跳断这么久，就认为这个写者没了
_LOCK_STALE = 10.0        # 旁路锁文件超过这么久没删 = 崩在临界区，收掉重抢
_LOCK_WAIT = 5.0          # 等锁上限（秒）


# ---------------------------------------------------------------- 盘的身份
def _volinfo(root):
    """卷序列号 / 卷标 / 文件系统。拿不到就返回 None。"""
    try:
        kernel32 = ctypes.windll.kernel32
        name = ctypes.create_unicode_buffer(261)
        fs = ctypes.create_unicode_buffer(261)
        serial = ctypes.c_ulong(0)
        maxlen = ctypes.c_ulong(0)
        flags = ctypes.c_ulong(0)
        ok = kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root), name, 261, ctypes.byref(serial),
            ctypes.byref(maxlen), ctypes.byref(flags), fs, 261)
        if not ok:
            return None
        return {"serial": "%08X" % (serial.value & 0xFFFFFFFF),
                "label": name.value or "",
                "fs": fs.value or ""}
    except Exception:
        return None


def _space(path):
    try:
        u = shutil.disk_usage(path)
        return {"total_gb": round(u.total / 1e9, 1),
                "free_gb": round(u.free / 1e9, 1)}
    except Exception:
        return {"total_gb": None, "free_gb": None}


def _disk_key(card_dir):
    """注册表的键：卷序列号 + 相对卷根的路径。

    只用卷序列号不够 —— 同一块盘上放两个副本（测试、或者"我 D 盘也留一份"）
    会互相覆盖；只用路径又会被盘符变化坑。两者拼起来才既认盘又认位置。
    """
    absd = os.path.abspath(card_dir)
    drive, rest = os.path.splitdrive(absd)
    vol = _volinfo(drive + "\\") or {}
    serial = vol.get("serial") or ("DIR:" + absd)
    rel = rest.strip("\\/").replace("\\", "/")
    return serial + ("::" + rel if rel else "")


def list_volumes():
    """枚举本机所有卷（只读）。带不带卡、是什么角色，都在这里判。"""
    out = []
    if os.name != "nt":
        return out
    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
    except Exception:
        return out
    for i in range(26):
        if not (mask >> i) & 1:
            continue
        root = "%s:\\" % chr(ord("A") + i)
        info = _volinfo(root)
        if not info:
            continue
        if not info["serial"]:
            continue
        row = {"root": root, "letter": root[0], "serial": info["serial"],
               "label": info["label"], "fs": info["fs"]}
        row.update(_space(root))
        # 这块盘里有没有我们的卡
        cand = os.path.join(root, BUNDLE_DIR)
        role = read_role(cand)
        if role is None:
            role = read_role(root)
            if role is not None:
                cand = root
        row["card_dir"] = cand if os.path.exists(os.path.join(cand, "card.db")) else None
        row["role"] = (role or {}).get("role")
        row["number"] = (role or {}).get("number")
        out.append(row)
    return out


def card_fp(db_path=CARD_DB):
    """卡的指纹：实例号（创世哈希前 8 位）、条数、链头。"""
    fp = {"db": os.path.abspath(db_path), "instance": None, "mem": 0,
          "head_seq": 0, "head_hash": None, "sealed": None}
    if not os.path.exists(db_path):
        return fp
    try:
        c = sqlite3.connect("file:%s?mode=ro" % db_path.replace("\\", "/"), uri=True)
        c.row_factory = sqlite3.Row
        r = c.execute("SELECT self_hash FROM timeline WHERE seq=1").fetchone()
        if r and r["self_hash"]:
            fp["instance"] = "WG-" + r["self_hash"][:8].upper()
        r = c.execute("SELECT seq, self_hash FROM timeline ORDER BY seq DESC "
                      "LIMIT 1").fetchone()
        if r:
            fp["head_seq"] = int(r["seq"])
            fp["head_hash"] = r["self_hash"]
        fp["mem"] = c.execute("SELECT COUNT(*) FROM mem").fetchone()[0]
        try:
            r = c.execute("SELECT val FROM card_meta WHERE key='sealed'").fetchone()
            fp["sealed"] = bool(r and r[0] == "1")
        except Exception:
            pass
        fp["head_at"] = self_hash_at(c, fp["head_seq"])
        c.close()
    except Exception as e:
        fp["error"] = str(e)[:120]
    return fp


def self_hash_at(conn, seq):
    try:
        r = conn.execute("SELECT self_hash FROM timeline WHERE seq=?", (seq,)).fetchone()
        return r[0] if r else None
    except Exception:
        return None


# ---------------------------------------------------------------- 身份证
def read_role(card_dir):
    """读某块盘上的 DISK-ROLE.json。没有就 None。"""
    if not card_dir:
        return None
    for p in (os.path.join(card_dir, ROLE_NAME), os.path.join(card_dir, BUNDLE_DIR, ROLE_NAME)):
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    d = json.load(f)
                d["_file"] = p
                return d
            except Exception:
                return None
    return None


def write_role(card_dir, role, number=None, main_instance=None, head_seq=None,
               note=""):
    card_dir = os.path.abspath(card_dir)
    vol = _volinfo(os.path.splitdrive(card_dir)[0] + "\\") or {}
    d = {"role": role, "number": number if role == ROLE_BACKUP else None,
         "main_instance": main_instance, "head_seq": head_seq,
         "volume_serial": vol.get("serial"), "volume_label": vol.get("label"),
         "card_dir": card_dir, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
         "note": note}
    p = os.path.join(card_dir, ROLE_NAME)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    d["_file"] = p
    return d


# ---------------------------------------------------------------- 注册表
# ── 退役（带卡出门） ────────────────────────────────────────────────
# 主备标签管不住「两份拷贝各自都能写」这件事：卡写的时候根本不看 role。
# 所以搬走时必须有一步，让留在家里的这一份自己认输。
# 存在单独的文件里，因为 write_role() 每次都会整份重写 DISK-ROLE.json。
RETIRED_NAME = "RETIRED.json"


def _retired_path(card_dir):
    return os.path.join(os.path.abspath(card_dir), RETIRED_NAME)


def read_retired(card_dir):
    """这张卡是不是已经被「带卡出门」了。

    三种情况要分清（2026-11-07 修）：
      · 文件不存在       → 没退役（False），最常见
      · 文件在且读得出来 → 看 retired 字段
      · 文件在但读不出来 → **当成已退役**（True）

    第三种为什么是 True：RETIRED.json 只在「这份卡被钉成只读」时才写，
    它在那儿本身就是个信号。文件坏了却回 False，等于「读不懂就不管了」，
    结果是一条本该只读的卡突然能写 —— 那正是两份拷贝分叉的开局。
    宁可让人手动删掉这个文件，也不能自作主张放行。
    """
    p = _retired_path(card_dir)
    if not os.path.exists(p):
        return False
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return bool(d.get("retired"))
    except FileNotFoundError:
        return False
    except Exception:
        return True


def retired_info(card_dir):
    p = _retired_path(card_dir)
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def set_retired(card_dir, on=True, why=""):
    """打上 / 清掉 retired 标记。清掉时直接删文件，不留残渣。"""
    p = _retired_path(card_dir)
    if not on:
        try:
            os.remove(p)
        except OSError:
            pass
        return {"retired": False}
    d = {"retired": True, "why": why or "带卡出门：这张卡已经被带到别处",
         "at": time.strftime("%Y-%m-%d %H:%M:%S"),
         "card_dir": os.path.abspath(card_dir)}
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    return d


def reg_path(card_dir):
    return os.path.join(card_dir, REG_NAME)


def load_registry(card_dir):
    p = reg_path(card_dir)
    if not os.path.exists(p):
        return {"disks": {}, "main_instance": None, "updated_at": None}
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        d.setdefault("disks", {})
        return d
    except Exception:
        return {"disks": {}, "main_instance": None, "updated_at": None}


def save_registry(card_dir, reg):
    reg["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(reg_path(card_dir), "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=1)


def register(card_dir, target_dir, role, number=None, note=""):
    """把一块盘登记进注册表（并写它自己的身份证）。"""
    reg = load_registry(card_dir)
    fp = card_fp(os.path.join(target_dir, "card.db"))
    vol = _volinfo(os.path.splitdrive(os.path.abspath(target_dir))[0] + "\\") or {}
    serial = _disk_key(target_dir)
    main = card_fp(os.path.join(card_dir, "card.db"))
    if role == ROLE_BACKUP and number is None:
        prev = reg["disks"].get(serial) or {}
        if prev.get("role") == ROLE_BACKUP and prev.get("number"):
            number = int(prev["number"])      # 同一块盘重登记不换号
        else:
            used = {int(v.get("number") or 0) for v in reg["disks"].values()
                    if v.get("role") == ROLE_BACKUP}
            number = 1
            while number in used:
                number += 1
    d = write_role(target_dir, role, number, main.get("instance"), fp.get("head_seq"), note)
    reg["disks"][serial] = {"role": role, "number": number,
                            "instance": fp.get("instance"),
                            "card_dir": os.path.abspath(target_dir),
                            "volume_label": vol.get("label"),
                            "head_seq": fp.get("head_seq"),
                            "mem": fp.get("mem"),
                            "last_seen": time.strftime("%Y-%m-%d %H:%M:%S")}
    if role == ROLE_MAIN:
        reg["main_instance"] = fp.get("instance")
    save_registry(card_dir, reg)
    return {"ok": True, "serial": serial, "role_file": d["_file"],
            "number": number, "instance": fp.get("instance")}


# ---------------------------------------------------------------- 状态
def service_running(port=8848, timeout=0.35):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def status(card_dir=HERE):
    main = card_fp(os.path.join(card_dir, "card.db"))
    reg = load_registry(card_dir)
    vols = list_volumes()
    by_serial = {}
    for v in vols:
        if v.get("card_dir"):
            by_serial[_disk_key(v["card_dir"])] = v
        by_serial.setdefault(v["serial"], v)
    rows = []
    # 主盘
    drv = os.path.splitdrive(os.path.abspath(card_dir))[0] + "\\"
    mserial = _disk_key(card_dir)
    # 这台机器手上这张卡是主是备，得看它自己的身份证 —— 换过主备之后就未必是主盘了
    _myrole = read_role(card_dir) or {}
    _ismain = (_myrole.get("role") or ROLE_MAIN) == ROLE_MAIN
    rows.append({"serial": mserial, "role": ROLE_MAIN if _ismain else ROLE_BACKUP,
                 "number": None if _ismain else _myrole.get("number"),
                 "online": True, "card_dir": os.path.abspath(card_dir),
                 "volume_label": (_volinfo(drv) or {}).get("label"),
                 "instance": main.get("instance"), "mem": main.get("mem"),
                 "head_seq": main.get("head_seq"), "head_hash": main.get("head_hash"),
                 "same_chain": True, "behind": 0,
                 "label": "主盘（这台机器）" if _ismain
                          else "备份 #%s（这台机器）" % _myrole.get("number")})
    # 注册表里认识的备份盘
    for serial, v in reg["disks"].items():
        if v.get("role") == ROLE_MAIN:
            continue
        vol = by_serial.get(serial) or by_serial.get((serial or "").split("::")[0])
        online = bool(v.get("card_dir") and os.path.exists(
            os.path.join(v["card_dir"], "card.db")))
        fp = card_fp(os.path.join(v["card_dir"], "card.db")) if v.get("card_dir") else {}
        same = bool(fp.get("head_hash") and main.get("head_hash")
                    and _prefix_match(fp, main))
        rows.append({"serial": serial, "role": ROLE_BACKUP,
                     "number": v.get("number"), "online": online,
                     "card_dir": v.get("card_dir"),
                     "volume_label": (vol or {}).get("label") or v.get("volume_label"),
                     "instance": fp.get("instance"), "mem": fp.get("mem"),
                     "head_seq": fp.get("head_seq"),
                     "head_hash": fp.get("head_hash"),
                     "same_chain": same,
                     "behind": (main.get("head_seq") or 0) - (fp.get("head_seq") or 0),
                     "label": "备份 #%s" % v.get("number")})
    # 插着但没登记过的、里面有卡的盘
    known = set(reg["disks"].keys())
    for v in vols:
        if v["serial"] in known:
            continue
        if v.get("card_dir") and v["card_dir"] != os.path.abspath(card_dir):
            rows.append({"serial": v["serial"], "role": None, "number": None,
                         "online": True, "card_dir": v["card_dir"],
                         "volume_label": v.get("label"), "instance": None,
                         "mem": None, "head_seq": None, "head_hash": None,
                         "same_chain": False, "label": "未登记"})
    return {"ok": True, "main": main,
            "service_running": service_running(),
            "volumes": [{"root": v["root"], "label": v["label"],
                         "serial": v["serial"], "free_gb": v["free_gb"],
                         "card_dir": v["card_dir"], "role": v["role"]} for v in vols],
            "disks": rows}


def _fork_of(main_db, back_db):
    """两条链从哪儿开始不一样。返回 (共同前缀末节, 备份盘上被丢掉的条目)。

    为什么要它：分叉判断只看链头是"对不上"，但用户需要知道**丢的是什么**。
    把孤儿条目列出来，人才能判断"这是测试垃圾"还是"这是我自己的日子"。
    """
    try:
        a = sqlite3.connect("file:%s?mode=ro" % main_db.replace("\\", "/"), uri=True)
        b = sqlite3.connect("file:%s?mode=ro" % back_db.replace("\\", "/"), uri=True)
        A = {r[0]: (r[1] or "") for r in a.execute("SELECT seq, self_hash FROM timeline")}
        B = {r[0]: (r[1] or "") for r in b.execute("SELECT seq, self_hash FROM timeline")}
        meta = {r[0]: (r[1], r[2], r[3]) for r in b.execute(
            "SELECT seq, ts, kind, note FROM timeline")}
        a.close()
        b.close()
    except Exception as e:
        return None, [], str(e)
    last_ok = 0
    for seq in sorted(set(A) & set(B)):
        if A[seq] != B[seq]:
            break
        last_ok = seq
    orphans = []
    for seq in sorted(B):
        if seq > last_ok:
            ts, kind, note = meta.get(seq, (None, "", ""))
            orphans.append({"seq": seq, "ts": ts, "kind": kind,
                            "note": (note or "")[:80]})
    return last_ok, orphans, None


def _prefix_match(backup_fp, main_fp):
    """备份的链头是不是主盘这条链上的某一节（同一张卡、只是落后）。"""
    if not backup_fp.get("head_hash") or not main_fp.get("head_hash"):
        return False
    if backup_fp.get("head_hash") == main_fp["head_hash"]:
        return True
    try:
        c = sqlite3.connect("file:%s?mode=ro" % main_fp["db"].replace("\\", "/"), uri=True)
        h = self_hash_at(c, backup_fp["head_seq"])
        c.close()
        return h == backup_fp["head_hash"]
    except Exception:
        return False


# ---------------------------------------------------------------- 写操作
def _snapshot_db(src_db, dst_db):
    """用 sqlite 自己的 backup API 拷库 —— 服务跑着也是**一致快照**。

    早先想用"停服务 + 拷文件"，但控制台的按钮就在服务里，
    等于自己把自己锁死。backup API 会透过 WAL 读到最新已提交状态，
    所以热同步是安全的；不安全的是直接 cp card.db（会丢 WAL 里的写入）。
    """
    src = sqlite3.connect(src_db, timeout=30)
    dst = sqlite3.connect(dst_db, timeout=30)
    try:
        with dst:
            src.backup(dst)
    finally:
        src.close()
        dst.close()


def io_open_seedlog(path):
    """SEED-LOG.json 用追加写文件句柄包一层 —— 只记"什么时候丢过什么"。"""
    import io as _io
    return _io.open(path, "a", encoding="utf-8")


def _guard(card_dir, what):
    """写操作前的检查：现在只报告状态，不再拦。

    因为 db 走 sqlite 快照（一致性有保证），服务在跑也能同步；
    但"整包 build"仍然要服务停（那一步是拷文件字节）。
    """
    wal = os.path.join(card_dir, "card.db") + "-wal"
    pre = os.path.getsize(wal) if os.path.exists(wal) else 0
    return {"ok": True, "wal_mb": round(pre / 1e6, 1),
            "service_running": service_running()}


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest_files(manifest):
    """把清单里的 files 归一成 [{"path","sha256","bytes"}]。

    新清单是列表；老清单（H:\\PORTABLE-MANIFEST.json 那种）是
    {"card.db": {"sha256":..,"bytes":..}} 的字典。两种都认，免得老包直接判死。
    """
    files = (manifest or {}).get("files")
    out = []
    if isinstance(files, dict):
        for rel, meta in files.items():
            meta = meta if isinstance(meta, dict) else {}
            out.append({"path": str(rel).replace("\\", "/"),
                        "sha256": meta.get("sha256") or "",
                        "bytes": meta.get("bytes")})
    elif isinstance(files, list):
        for it in files:
            if not isinstance(it, dict):
                continue
            path = it.get("path") or it.get("rel") or ""
            out.append({"path": str(path).replace("\\", "/"),
                        "sha256": it.get("sha256") or "",
                        "bytes": it.get("bytes", it.get("size"))})
    return out


def _identity_id(db_path, conn=None):
    """卡的身份 ID（只读）。

    identity.py 现在只封了 6 条锚的 sha256（user_qq / user_nickname / card_name /
    三条底线），**卡里还没有独立的 identity_id 字段**。所以先找 core_zone.identity_id
    （V7 以后若补上就直接用），再找 identity.json 的 identity_id，再没有就返回 ""。
    空串表示「卡里还没有这个概念」，不是这里算不出来。
    """
    own = conn is None
    try:
        if conn is None:
            conn = sqlite3.connect("file:%s?mode=ro" % db_path.replace("\\", "/"),
                                   uri=True)
        try:
            row = conn.execute(
                "SELECT val FROM core_zone WHERE key='identity_id'").fetchone()
            if row and row[0]:
                return str(row[0])
        except Exception:
            pass
    except Exception:
        pass
    finally:
        if own and conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    ident = os.path.join(os.path.dirname(os.path.abspath(db_path)), "identity.json")
    if os.path.exists(ident):
        try:
            with io.open(ident, encoding="utf-8") as f:
                doc = json.load(f)
            v = doc.get("identity_id")
            if v:
                return str(v)
        except Exception:
            pass
    return ""


def _writer_epoch(card_dir, conn=None):
    """写者纪元（单调递增的「第几任写者」）。

    优先级（V7 P0-4 起）：
      · WRITER-EPOCH.json → 权威值（bump_writer_epoch 写它）
      · DISK-ROLE.json 里若已有 writer_epoch → 兼容旧盘
      · 卡里 timeline_meta.writer_epoch → 兼容更旧的盘
      · 都没有 → 0（= 未启用，不是「第一个写者」）
    """
    v = writer_epoch(card_dir)
    if v:
        return v
    role = read_role(card_dir) or {}
    v = role.get("writer_epoch")
    if v is not None:
        try:
            return int(v)
        except Exception:
            pass
    if conn is not None:
        try:
            row = conn.execute(
                "SELECT val FROM timeline_meta WHERE key='writer_epoch'").fetchone()
            if row and row[0] is not None:
                return int(row[0])
        except Exception:
            pass
    return 0


# ---------------------------------------------------------------- 写者纪元 / 写者租约
# 解决一件事：同一张卡同一时刻只允许一个进程写。
# 双开、搬家时新旧盘同时跑，没有这层的时候两边各写各的 —— 时间线分叉、
# 锚互相覆盖、记忆条数对不上，事后只能靠备份回滚。
#
# 设计取舍（改之前先读完）：
# 1. 纪元（WRITER-EPOCH.json）是单调的「第几任写者」，跟着卡走；租约
#    （WRITER-LEASE.json）是易失的「现在谁在写」，靠心跳续命。两者分开：
#    搬家时纪元要留在新盘上，租约则不许被当成遗产复制过去。
# 2. 单文件而不是 LEASES/ 目录：一次读改写 + 原子替换就够，目录只会多出
#    一堆要清理的碎文件。
# 3. 互斥用的是旁路锁文件（不是租约本身）：「读 → 判断 → 写」不是原子的，
#    两个进程能同时读到空租约再同时写。锁文件只在毫秒级持有；
#    超过 _LOCK_STALE 秒视为崩溃残留，删掉重抢。
# 4. holder_id 必须同时含 主机名 + pid + 卡实例号。只拿 instance_id 当
#    holder 的话，同一张卡上的两个进程会被认成同一个写者、互相续租 ——
#    那正好是本模块要防的那个 bug。
# 5. pid 存活判断是三态 True/False/None：只有拿到明确的「没这个进程」才判死，
#    权限不足 / 接口失败一律当未知、回落 TTL。反过来（未知当死）会让第二个
#    进程抢走活租约，又回到本 bug。
def _hostname():
    try:
        return socket.gethostname() or ""
    except Exception:
        return ""


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _atomic_json(path, obj):
    """原子写 JSON：同目录 tmp + os.replace。读的人永远看不到半个文件。"""
    d = os.path.dirname(os.path.abspath(path)) or "."
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    tmp = "%s.tmp-%d-%d" % (path, os.getpid(), int(time.time() * 1000))
    data = json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(tmp, path)
    return path


def _lock_acquire(path, wait=_LOCK_WAIT):
    """拿旁路锁文件，返回 fd；超时返回 None。"""
    deadline = time.time() + max(0.0, float(wait or 0))
    while True:
        try:
            return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                age = time.time() - os.path.getmtime(path)
            except OSError:
                age = 0.0
            if age > _LOCK_STALE:
                try:
                    os.remove(path)
                except OSError:
                    pass
                continue
            if time.time() >= deadline:
                return None
            time.sleep(0.01)
        except OSError:
            return None


def _lock_release(fd, path):
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.remove(path)
    except OSError:
        pass


def _epoch_lock_path(card_dir):
    return os.path.join(os.path.abspath(card_dir), WRITER_EPOCH_NAME + ".lock")


def _lease_lock_path(card_dir):
    return os.path.join(os.path.abspath(card_dir), WRITER_LEASE_NAME + ".lock")


def writer_epoch_doc(card_dir):
    return _read_json(os.path.join(os.path.abspath(card_dir), WRITER_EPOCH_NAME))


def writer_epoch(card_dir):
    """从 WRITER-EPOCH.json 读当前纪元；没有 / 坏了 → 0（= 未启用）。"""
    try:
        return int(writer_epoch_doc(card_dir).get("epoch") or 0)
    except (TypeError, ValueError):
        return 0


def read_takeover_done(card_dir):
    return _read_json(os.path.join(os.path.abspath(card_dir), TAKEOVER_DONE_NAME))


def bump_writer_epoch(card_dir, identity_id=""):
    """纪元 +1（旁路锁 + tmp + os.replace），返回新纪元。"""
    card_dir = os.path.abspath(card_dir)
    if not os.path.isdir(card_dir):
        raise FileNotFoundError(card_dir)
    lk = _epoch_lock_path(card_dir)
    fd = _lock_acquire(lk)
    if fd is None:
        raise TimeoutError("writer_epoch 锁超时：%s" % lk)
    try:
        new = writer_epoch(card_dir) + 1
        _atomic_json(os.path.join(card_dir, WRITER_EPOCH_NAME),
                     {"schema": 1, "epoch": new,
                      "identity_id": identity_id or "",
                      "updated_ts": time.time(),
                      "by_pid": os.getpid(), "by_host": _hostname()})
        return new
    finally:
        _lock_release(fd, lk)


def epoch_check(card_dir):
    """纪元体检：文件里的纪元比搬运记录（TAKEOVER-DONE.json）的旧 → 只告警。

    不自动修：纪元倒退说明有人拿旧包落地 / 盘被换过，直接改数字
    等于把证据抹掉；该让人知道。
    """
    file_epoch = writer_epoch(card_dir)
    done = read_takeover_done(card_dir)
    want = 0
    for src in (done, done.get("manifest") if isinstance(done.get("manifest"), dict) else {}):
        if not isinstance(src, dict):
            continue
        for key in ("writer_epoch", "epoch"):
            try:
                want = max(want, int(src.get(key) or 0))
            except (TypeError, ValueError):
                pass
    return {"epoch": file_epoch, "takeover_epoch": want,
            "stale": bool(want > file_epoch),
            "takeover_done": bool(done)}


def _lease_path(card_dir):
    return os.path.join(os.path.abspath(card_dir), WRITER_LEASE_NAME)


def read_lease(card_dir):
    return _read_json(_lease_path(card_dir))


def default_holder(instance_id="", epoch=0, pid=None):
    """造一个 holder。holder_id = 主机名:pid:卡实例号（见上面取舍 4）。"""
    pid = os.getpid() if pid is None else int(pid)
    host = _hostname()
    inst = str(instance_id or "")
    return {"holder_id": "%s:%d:%s" % (host or "?", pid, inst or "-"),
            "pid": pid, "host": host, "instance_id": inst,
            "epoch": int(epoch or 0)}


def _lease_remaining(doc, now):
    try:
        hb = float(doc.get("heartbeat_ts") or 0.0)
        ttl = float(doc.get("ttl") or LEASE_TTL)
    except (TypeError, ValueError):
        return 0.0
    return (hb + max(1.0, ttl)) - now


def _pid_alive(pid):
    """三态：True 活 / False 确定没了 / None 不知道（见上面取舍 5）。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    if os.name == "nt":
        try:
            k32 = ctypes.windll.kernel32
            k32.OpenProcess.restype = ctypes.c_void_p
            k32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
            h = k32.OpenProcess(0x1000, 0, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
            if h:
                k32.CloseHandle(ctypes.c_void_p(h))
                return True
            if k32.GetLastError() == 87:          # ERROR_INVALID_PARAMETER = 没这个 pid
                return False
            return None
        except Exception:
            return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None


def lease_state(card_dir, now=None):
    """租约的完整判断结果。alive=False 的 reason：no_lease / expired / pid_gone。"""
    now = time.time() if now is None else float(now)
    doc = read_lease(card_dir)
    if not doc:
        return {"alive": False, "reason": "no_lease", "lease": {},
                "remaining_s": 0.0}
    rem = _lease_remaining(doc, now)
    if rem <= 0:
        return {"alive": False, "reason": "expired", "lease": doc,
                "remaining_s": 0.0}
    # 跨机的 pid 没有可比性，只有同机 + 明确「没这个进程」才提前作废。
    if (doc.get("host") or "") == _hostname() and _pid_alive(doc.get("pid")) is False:
        return {"alive": False, "reason": "pid_gone", "lease": doc,
                "remaining_s": max(0.0, rem)}
    return {"alive": True, "reason": "ok", "lease": doc,
            "remaining_s": max(0.0, rem)}


def lease_alive(card_dir, now=None):
    return bool(lease_state(card_dir, now=now)["alive"])


def acquire_lease(card_dir, holder, ttl=LEASE_TTL, now=None):
    """抢写者租约。

    已有未过期租约且 holder_id 不同 → {"ok": False, "reason": "lease_held",
    "holder": {...}}。同一个 holder 再调 = 续租（刷新 heartbeat，保留 started_ts）。
    """
    card_dir = os.path.abspath(card_dir)
    if not os.path.isdir(card_dir):
        return {"ok": False, "reason": "no_card_dir", "card_dir": card_dir}
    if not isinstance(holder, dict):
        holder = {"holder_id": str(holder)}
    hid = str(holder.get("holder_id") or "").strip()
    if not hid:
        holder = dict(holder, **default_holder(holder.get("instance_id") or "",
                                               holder.get("epoch") or 0,
                                               holder.get("pid")))
        hid = holder["holder_id"]
    now = time.time() if now is None else float(now)
    ttl = max(1.0, float(ttl or LEASE_TTL))
    lk = _lease_lock_path(card_dir)
    fd = _lock_acquire(lk)
    if fd is None:
        return {"ok": False, "reason": "busy", "holder": read_lease(card_dir)}
    try:
        cur = read_lease(card_dir)
        st = lease_state(card_dir, now=now)
        if st["alive"] and str(cur.get("holder_id") or "") != hid:
            # 🔧 2026-11-11（真事故）：TTL 90 秒没到，但持有者已经被 kill -9 了 ——
            # 旧逻辑照样判「有人在写」，新进程就永远只读，重启也起不来可写的。
            # 同主机 + 那个 pid 已经不在了 = 它不会再写任何一个字节，直接接手。
            _cur_pid = cur.get("pid")
            _same_host = str(cur.get("host") or "") == _hostname()
            if not (_same_host and _cur_pid and not _pid_alive(int(_cur_pid))):
                return {"ok": False, "reason": "lease_held", "holder": cur,
                        "remaining_s": st["remaining_s"]}
        renew = bool(cur) and str(cur.get("holder_id") or "") == hid
        try:
            started = float(cur.get("started_ts") or now) if renew else now
        except (TypeError, ValueError):
            started = now
        doc = {"schema": 1, "holder_id": hid,
               "epoch": int(holder.get("epoch") or 0),
               "pid": int(holder.get("pid") or os.getpid()),
               "started_ts": started, "heartbeat_ts": now, "ttl": ttl,
               "instance_id": str(holder.get("instance_id") or ""),
               "host": _hostname(), "card_dir": card_dir,
               "note": str(holder.get("note") or "")}
        _atomic_json(_lease_path(card_dir), doc)
        return {"ok": True, "renewed": renew, "lease": doc, "holder": doc}
    finally:
        _lock_release(fd, lk)


def heartbeat_lease(card_dir, holder_id, ttl=LEASE_TTL, now=None):
    """续租；不是自己的租约就失败（绝不抢）。"""
    card_dir = os.path.abspath(card_dir)
    now = time.time() if now is None else float(now)
    ttl = max(1.0, float(ttl or LEASE_TTL))
    lk = _lease_lock_path(card_dir)
    fd = _lock_acquire(lk, wait=2.0)
    if fd is None:
        return {"ok": False, "reason": "busy"}
    try:
        cur = read_lease(card_dir)
        if not cur:
            return {"ok": False, "reason": "no_lease"}
        if str(cur.get("holder_id") or "") != str(holder_id or ""):
            return {"ok": False, "reason": "not_holder", "holder": cur}
        # 文件里还是我 → 说明没人抢走（抢走会换 holder_id），过期了也续上：
        # 让一个卡住的进程续上租约，比让整台服务莫名其妙变只读要好。
        cur = dict(cur)
        cur["heartbeat_ts"] = now
        cur["ttl"] = ttl
        _atomic_json(_lease_path(card_dir), cur)
        return {"ok": True, "lease": cur,
                "remaining_s": _lease_remaining(cur, now)}
    finally:
        _lock_release(fd, lk)


def release_lease(card_dir, holder_id):
    """交还租约；只删自己的那张。"""
    card_dir = os.path.abspath(card_dir)
    lk = _lease_lock_path(card_dir)
    fd = _lock_acquire(lk, wait=2.0)
    if fd is None:
        return {"ok": False, "reason": "busy"}
    try:
        cur = read_lease(card_dir)
        if not cur:
            return {"ok": True, "released": False, "reason": "no_lease"}
        if str(cur.get("holder_id") or "") != str(holder_id or ""):
            return {"ok": False, "reason": "not_holder", "holder": cur}
        try:
            os.remove(_lease_path(card_dir))
        except FileNotFoundError:
            pass
        return {"ok": True, "released": True}
    finally:
        _lock_release(fd, lk)


def lease_summary(card_dir, now=None):
    """/health 用的一行租约摘要（不判对错，只描述现场）。"""
    now = time.time() if now is None else float(now)
    st = lease_state(card_dir, now=now)
    doc = st.get("lease") or {}
    return {"alive": bool(st["alive"]), "reason": st.get("reason"),
            "holder_id": doc.get("holder_id"),
            "epoch": doc.get("epoch"),
            "pid": doc.get("pid"), "host": doc.get("host"),
            "instance_id": doc.get("instance_id"),
            "heartbeat_age_s": (round(now - float(doc.get("heartbeat_ts") or now), 1)
                                if doc else None),
            "remaining_s": round(float(st.get("remaining_s") or 0.0), 1),
            "ttl": doc.get("ttl")}


def manifest_identity(db_path=CARD_DB, card_dir=None):
    """清单里「这是哪张卡、哪一刻」的一组字段（只读）。"""
    db_path = os.path.abspath(db_path)
    card_dir = os.path.abspath(card_dir or os.path.dirname(db_path))
    fp = card_fp(db_path)
    conn = None
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % db_path.replace("\\", "/"),
                               uri=True)
    except Exception:
        conn = None
    try:
        ident = _identity_id(db_path, conn)
        epoch = _writer_epoch(card_dir, conn)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return {"identity_id": ident,
            "instance_id": fp.get("instance") or "",
            "head_seq": int(fp.get("head_seq") or 0),
            "head_hash": fp.get("head_hash") or "",
            "writer_epoch": epoch}


def manifest_file_entry(card_dir, rel):
    """算一个文件的清单项；文件不在会抛 FileNotFoundError，由调用方决定怎么报。"""
    rel = str(rel).replace("\\", "/")
    full = os.path.join(os.path.abspath(card_dir), rel.replace("/", os.sep))
    return {"path": rel, "sha256": _sha256(full),
            "bytes": os.path.getsize(full)}


def build_manifest(card_dir, entries, snapshot_method="sqlite-backup-api",
                   extra=None, src=None):
    """造一份完整的 PORTABLE-MANIFEST（只读源目录，不写盘；写盘由调用方做）。

    entries 收 [{"path","sha256","bytes"}] 或路径字符串（字符串现算哈希）。
    card.db 不管传没传都会按**实际字节**补/覆盖一行 —— 保证顶层 card_sha256
    和 files 里 card.db 那一行是同一个值（旧包就是这里不一致）。
    """
    card_dir = os.path.abspath(card_dir)
    db = os.path.join(card_dir, "card.db")
    rows = {}
    for it in entries or []:
        if isinstance(it, str):
            try:
                e = manifest_file_entry(card_dir, it)
            except OSError:
                continue
        elif isinstance(it, dict):
            rel = it.get("path") or it.get("rel")
            if not rel:
                continue
            rel = str(rel).replace("\\", "/")
            full = os.path.join(card_dir, rel.replace("/", os.sep))
            if not os.path.exists(full):
                continue
            e = {"path": rel,
                 "sha256": it.get("sha256") or _sha256(full),
                 "bytes": int(it["bytes"]) if it.get("bytes") is not None
                 else os.path.getsize(full)}
        else:
            continue
        rows[e["path"]] = e
    if os.path.exists(db):
        rows["card.db"] = manifest_file_entry(card_dir, "card.db")
    files = [rows[k] for k in sorted(rows)]
    card = rows.get("card.db") or {"sha256": "", "bytes": 0}
    ident = manifest_identity(db, card_dir)
    man = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "built_at": time.time(),
        "built_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "src": os.path.abspath(src or card_dir),
        # identity_id 为空串时见 _identity_id：卡里还没有独立身份 ID
        "identity_id": ident["identity_id"],
        "instance_id": ident["instance_id"],
        "head_seq": ident["head_seq"],
        "head_hash": ident["head_hash"],
        # 0 = writer_epoch/租约未启用（V7 P0-4）
        "writer_epoch": ident["writer_epoch"],
        "snapshot_method": snapshot_method,
        "card_sha256": card.get("sha256") or "",
        "card_bytes": int(card.get("bytes") or 0),
        "files": files,
        "files_n": len(files),
        "bytes": sum(int(f.get("bytes") or 0) for f in files),
    }
    for k, v in (extra or {}).items():
        if k not in man:
            man[k] = v
    # 签名：清单是整个包的「装箱单」，没有签名就等于能被随手改。
    # 用卡自己的签名密钥（checkpoint.load_key）签，包里带着钥匙 —— 卡本身就是身份，
    # 钥匙跟着卡走是设计使然，不是泄露；挡的是「运输途中清单被改了几行」。
    try:
        import seal as _cp
        _key = _cp.load_key(card_dir)
        if _key:
            man["sig"] = _cp.sign({k: v for k, v in man.items() if k != "sig"}, _key)
    except Exception as e:  # noqa: BLE001
        man["sig"] = ""
        man["sig_note"] = "签名失败：%s" % e
    return man


def verify_manifest(manifest, root):
    """只读校验清单：字段齐不齐 + 文件字节对不对。

    返回 {"ok": bool, "problems": [...], "checked": n}。
    只读 —— 不改清单、不接管、不覆盖。problems 里点名的路径可直接去查。
    """
    problems = []
    if not isinstance(manifest, dict):
        return {"ok": False, "problems": ["manifest 不是 JSON 对象"], "checked": 0}
    for k in MANIFEST_REQUIRED:
        if k not in manifest:
            problems.append("缺少必需字段：%s" % k)
    root = os.path.abspath(root)
    cache = {}
    # 清单自身的完整性：签名（有才验，老包没签名不算错）+ 文件条数自洽
    _sig = manifest.get("sig") or ""
    if _sig:
        try:
            import seal as _cp
            _key = _cp.load_key(root, create=False)
            if _key:
                _ok_sig, _why = _cp.verify_signature(manifest, _key)
                if not _ok_sig:
                    problems.append("清单签名对不上：%s" % _why)
            else:
                problems.append("清单有签名，但包里没有签名密钥（anchor.key），无法验证")
        except Exception as e:  # noqa: BLE001
            problems.append("校验清单签名时出错：%s" % e)
    _fn = manifest.get("files_n")
    _fl = manifest.get("files")
    if isinstance(_fl, list) and _fn is not None and int(_fn) != len(_fl):
        problems.append("清单自相矛盾：files_n=%s 但 files 里有 %d 条" % (_fn, len(_fl)))

    def actual(rel):
        """(sha256, bytes)；不存在 → (None, None)，路径跑出包 → ("__escape__", None)。"""
        rel = str(rel).replace("\\", "/")
        if rel in cache:
            return cache[rel]
        full = os.path.join(root, rel.replace("/", os.sep))
        try:
            if os.path.commonpath([root, os.path.abspath(full)]) != root:
                cache[rel] = ("__escape__", None)
                return cache[rel]
        except ValueError:
            cache[rel] = ("__escape__", None)
            return cache[rel]
        if not os.path.exists(full) or not os.path.isfile(full):
            cache[rel] = (None, None)
        else:
            cache[rel] = (_sha256(full), os.path.getsize(full))
        return cache[rel]

    checked = 0
    sha, size = actual("card.db")
    if sha == "__escape__" or sha is None:
        problems.append("card.db 不存在：%s" % os.path.join(root, "card.db"))
    else:
        checked += 1
        want = manifest.get("card_sha256")
        if want and want != sha:
            problems.append("card_sha256 不一致：清单 %s…，实际 %s…"
                            % (str(want)[:12], sha[:12]))
        wb = manifest.get("card_bytes")
        if isinstance(wb, int) and wb != size:
            problems.append("card_bytes 不一致：清单 %s，实际 %s" % (wb, size))

    entries = manifest_files(manifest)
    if "files" in manifest and not entries:
        problems.append("files 是空的")
    for i, e in enumerate(entries):
        rel = e.get("path") or ""
        if not rel:
            problems.append("files[%d] 缺 path" % i)
            continue
        sha, size = actual(rel)
        if sha == "__escape__":
            problems.append("files[%d] 路径跑出包目录：%s" % (i, rel))
            continue
        if sha is None:
            problems.append("文件缺失：%s" % rel)
            continue
        checked += 1
        want = str(e.get("sha256") or "")
        if not want:
            problems.append("files[%d] %s 缺 sha256" % (i, rel))
        elif want != sha:
            problems.append("sha256 不一致：%s（清单 %s…，实际 %s…）"
                            % (rel, want[:12], sha[:12]))
        wb = e.get("bytes")
        if isinstance(wb, int) and wb != size:
            problems.append("bytes 不一致：%s（清单 %s，实际 %s）" % (rel, wb, size))
    return {"ok": not problems, "problems": problems, "checked": checked}


def _ro_connect(db_path):
    """只读打开 card.db：file:...?mode=ro，不建文件、不 checkpoint、不写一个字节。

    包里没有非空 -wal 时再加 immutable=1 —— 货是静态的，这样连 -shm/-wal
    都不会在包目录里生成，U 盘只读也能验。
    """
    p = os.path.abspath(db_path).replace("\\", "/")
    uri = "file:%s?mode=ro" % p
    wal = os.path.abspath(db_path) + "-wal"
    try:
        has_wal = os.path.exists(wal) and os.path.getsize(wal) > 0
    except OSError:
        has_wal = True          # 判断不了就保守走普通只读（会读到 WAL）
    if not has_wal:
        uri += "&immutable=1"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _bundle_chain_problems(conn, max_report=5):
    """只读验链：返回 (problems, 行数, 已登记删除数)。

    哈希公式与 timeline.Timeline.verify_chain 完全一致；
    但**不实例化 Timeline** —— 它 __init__ 会 CREATE/ALTER，等于写库。
    """
    pruned = {}
    try:
        for k, v in conn.execute(
                "SELECT key,val FROM timeline_meta WHERE key LIKE 'prune_%'"):
            try:
                pruned[int(str(k).split("_", 1)[1])] = (
                    json.loads(v) or {}).get("hash", "")
            except Exception:
                continue
    except sqlite3.DatabaseError:
        pruned = {}
    try:
        rows = conn.execute(
            "SELECT seq,ts,kind,note,prev_hash,self_hash FROM timeline"
            " ORDER BY seq ASC").fetchall()
    except sqlite3.DatabaseError as e:
        return ["读不了 timeline 表：%s" % e], 0, len(pruned)
    bad = []
    prev_hash = ""
    expect_seq = 1
    for r in rows:
        seq = int(r["seq"])
        if seq != expect_seq:
            missing = list(range(expect_seq, seq))
            if not (missing and all(s in pruned for s in missing)):
                bad.append("跳号：期望 seq=%d，实际 %d" % (expect_seq, seq))
        if r["prev_hash"] != prev_hash:
            known = any(pruned.get(s) == r["prev_hash"] for s in pruned)
            if not known:
                bad.append("断链：seq=%d 的 prev 对不上" % seq)
        payload = json.dumps(
            {"seq": seq, "ts": round(r["ts"], 3), "kind": r["kind"],
             "note": r["note"], "prev": r["prev_hash"]},
            ensure_ascii=False, sort_keys=True)
        if hashlib.sha256(payload.encode("utf-8")).hexdigest() != r["self_hash"]:
            bad.append("被改过：seq=%d 的哈希对不上" % seq)
        prev_hash = r["self_hash"]
        expect_seq = seq + 1
        if len(bad) >= max_report:
            break
    return bad, len(rows), len(pruned)


def verify_bundle(root: str) -> dict:
    """到货验收（全程只读）：清单 / 库完整性 / 外键 / 时间线链 / 锚 / 盘角色。

    返回 {"ok": bool, "problems": [...], "warnings": [...], "checks": {...}}。
    card.db 只用 file:...?mode=ro 打开；不改清单、不改库、不写任何文件。
    读不到 / 表不存在 → 记 warning，不抛异常。
    """
    root = os.path.abspath(root)
    problems = []
    warnings = []
    checks = {}

    # ---- 1. 清单 ----
    man = None
    mpath = os.path.join(root, MANIFEST_NAME)
    if not os.path.isfile(mpath):
        problems.append("manifest: 没有 %s（不是便携包）" % MANIFEST_NAME)
        checks["manifest"] = {"ok": False, "checked": 0,
                              "problems": ["清单文件不存在"]}
    else:
        try:
            with io.open(mpath, encoding="utf-8") as f:
                man = json.load(f)
        except Exception as e:
            problems.append("manifest: 读不了 %s：%s" % (MANIFEST_NAME, e))
            checks["manifest"] = {"ok": False, "checked": 0,
                                  "problems": ["读不了：%s" % e]}
            man = None
        if man is not None:
            rep = verify_manifest(man, root)
            checks["manifest"] = {"ok": rep["ok"], "checked": rep["checked"],
                                  "problems": list(rep["problems"])}
            for p in rep["problems"]:
                problems.append("manifest: %s" % p)
    if not isinstance(man, dict):
        man = None
    m_head_seq = man.get("head_seq") if man else None
    m_head_hash = man.get("head_hash") if man else None
    m_instance = man.get("instance_id") if man else None

    # ---- 2~5. 库内部：一个只读连接跑完 ----
    dbp = os.path.join(root, "card.db")
    db_instance = None
    conn = None
    if not os.path.isfile(dbp):
        # verify_manifest 已经点过名，这里只放 check 占位，不重复报
        checks["sqlite_integrity"] = {"ok": False, "error": "card.db 不存在"}
        checks["foreign_keys"] = {"ok": None, "error": "card.db 不存在"}
        checks["timeline"] = {"ok": None, "skipped": "card.db 不存在"}
        checks["anchor"] = {"ok": None, "skipped": "card.db 不存在"}
    else:
        try:
            conn = _ro_connect(dbp)
        except Exception as e:
            problems.append("sqlite: card.db 打不开：%s" % e)
            checks["sqlite_integrity"] = {"ok": False, "error": str(e)}
            checks["foreign_keys"] = {"ok": None, "error": str(e)}
            checks["timeline"] = {"ok": None, "skipped": "card.db 打不开"}
            checks["anchor"] = {"ok": None, "skipped": "card.db 打不开"}

    if conn is not None:
        try:
            # 2. integrity_check
            try:
                irows = [r[0] for r in conn.execute(
                    "PRAGMA integrity_check").fetchall()]
                iok = (irows == ["ok"])
                checks["sqlite_integrity"] = {"ok": iok, "result": irows[:10]}
                if not iok:
                    problems.append(
                        "sqlite_integrity: integrity_check 不通过：%s"
                        % "；".join(str(x) for x in irows[:5]))
            except sqlite3.DatabaseError as e:
                checks["sqlite_integrity"] = {"ok": False, "error": str(e)}
                problems.append("sqlite_integrity: 检查失败：%s" % e)

            # 3. foreign_key_check
            try:
                frows = conn.execute("PRAGMA foreign_key_check").fetchall()
                checks["foreign_keys"] = {"ok": not frows,
                                          "count": len(frows)}
                if frows:
                    problems.append(
                        "foreign_keys: %d 行违反外键（首个 table=%s rowid=%s"
                        " parent=%s）"
                        % (len(frows), frows[0][0], frows[0][1], frows[0][2]))
            except sqlite3.DatabaseError as e:
                checks["foreign_keys"] = {"ok": None, "error": str(e)}
                warnings.append("foreign_keys: 检查不了（%s）" % e)

            # 4. timeline：链头对清单 + 整条链重算
            try:
                has_tl = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table'"
                    " AND name='timeline'").fetchone() is not None
            except sqlite3.DatabaseError as e:
                has_tl = False
                warnings.append("timeline: 查不到表（%s）" % e)
            if not has_tl:
                checks["timeline"] = {"ok": None,
                                      "skipped": "timeline 表不存在"}
                warnings.append("timeline: 表不存在，跳过链头与链完整性核对")
            else:
                chain_bad = []
                n_rows = n_prune = 0
                head = None
                try:
                    head = conn.execute(
                        "SELECT seq,self_hash FROM timeline"
                        " ORDER BY seq DESC LIMIT 1").fetchone()
                    seq1 = conn.execute(
                        "SELECT self_hash FROM timeline WHERE seq=1").fetchone()
                    if seq1 and seq1["self_hash"]:
                        db_instance = ("WG-"
                                       + str(seq1["self_hash"])[:8].upper())
                    chain_bad, n_rows, n_prune = _bundle_chain_problems(conn)
                except Exception as e:
                    # 旧库/怪行也不许把验收搞崩
                    warnings.append("timeline: 读表失败（%s）" % e)
                actual_seq = int(head["seq"]) if head else 0
                actual_hash = (head["self_hash"] or "") if head else ""
                tl = []
                if man is not None:
                    try:
                        if (m_head_seq is not None
                                and int(m_head_seq) != actual_seq):
                            tl.append("head_seq 不一致：清单 %s，库里 %s"
                                      % (m_head_seq, actual_seq))
                    except (TypeError, ValueError):
                        tl.append("清单 head_seq 不是整数：%r" % (m_head_seq,))
                    if m_head_hash and m_head_hash != actual_hash:
                        tl.append("head_hash 不一致：清单 %s…，库里 %s…"
                                  % (str(m_head_hash)[:12], actual_hash[:12]))
                    if (m_instance and db_instance
                            and m_instance != db_instance):
                        tl.append("instance_id 不一致：清单 %s，库里 %s"
                                  % (m_instance, db_instance))
                tl.extend(chain_bad)
                checks["timeline"] = {
                    "ok": not tl, "head_seq": actual_seq,
                    "head_hash": actual_hash,
                    "manifest_head_seq": m_head_seq,
                    "manifest_head_hash": m_head_hash,
                    "instance": db_instance,
                    "rows": n_rows, "pruned": n_prune,
                    "problems": tl[:10],
                }
                for p in tl:
                    problems.append("timeline: %s" % p)

            # 5. anchor：有锚文件才比；用只读连接喂 anchor.check，
            #    避免它自己以读写模式开库触发 WAL checkpoint。
            try:
                names = os.listdir(root)
            except OSError as e:
                names = []
                warnings.append("anchor: 列不了包目录（%s）" % e)
            afiles = sorted(n for n in names
                            if n == "anchor.json"
                            or (n.startswith("anchor.")
                                and n.endswith(".json")))
            if not afiles:
                checks["anchor"] = {"ok": None, "files": []}
                warnings.append(
                    "anchor: 无锚文件（没有 anchor.json / anchor.*.json）")
            elif not os.path.exists(os.path.join(root, "anchor.key")):
                # 包里带了锚文件却没带钥匙：签名根本验不了。
                # 迁移包本来就不该靠「源机的锚」证明自己是好的 —— 新机落地会重新写锚。
                # 这里判「无锚」，不当成篡改（否则每个合法的包都会被自己挡下来）。
                checks["anchor"] = {"ok": None, "files": afiles,
                                    "skipped": "包里没有 anchor.key，签名验不了"}
                warnings.append(
                    "anchor: 包里有锚文件但没有 anchor.key，按无锚处理（不判错）")
            else:
                try:
                    import anchor as _anchor
                    arep = _anchor.check(conn)
                except Exception as e:
                    checks["anchor"] = {"ok": None, "files": afiles,
                                        "error": str(e)}
                    warnings.append("anchor: 校验失败（%s），锚文件在：%s"
                                    % (e, ", ".join(afiles)))
                else:
                    aok = bool(arep.get("ok"))
                    checks["anchor"] = {
                        "ok": aok, "verdict": arep.get("verdict"),
                        "reason": arep.get("reason"), "files": afiles,
                        "anchor": arep.get("anchor"),
                    }
                    if not aok:
                        problems.append("anchor: %s（verdict=%s）"
                                        % (arep.get("reason"),
                                           arep.get("verdict")))
                    elif arep.get("verdict") == "first":
                        warnings.append(
                            "anchor: 锚文件读不出有效内容，按无锚处理")
        finally:
            conn.close()

    # ---- 6. 盘角色 ----
    try:
        role = read_role(root)
    except Exception as e:
        role = None
        warnings.append("role: 读 %s 失败（%s）" % (ROLE_NAME, e))
    if not role:
        checks["role"] = {"ok": None, "present": False}
        warnings.append("role: 没有 %s，跳过实例比对" % ROLE_NAME)
    else:
        want = m_instance or db_instance
        got = role.get("instance_id") or role.get("main_instance")
        rok = not (got and want and got != want)
        checks["role"] = {"ok": rok, "role": role.get("role"),
                          "instance_id": got, "manifest_instance_id": want,
                          "file": role.get("_file")}
        if not rok:
            problems.append(
                "role: %s 的 instance_id=%s 与清单/库里的 %s 不一致"
                % (ROLE_NAME, got, want))
        if role.get("role") not in (ROLE_MAIN, ROLE_BACKUP):
            warnings.append("role: role=%r 不认识" % role.get("role"))

    return {"ok": not problems, "problems": problems,
            "warnings": warnings, "checks": checks}


def _refresh_manifest_hash(bundle, names, snapshot_method=None):
    """把清单里某些文件的 sha256 改成现在的实际值（只改这几个，别整份重算）。

    card.db 被换掉时，除了 files 里那一行，顶层 card_sha256 / card_bytes 和身份
    四件套（identity_id / instance_id / head_seq / head_hash / writer_epoch）也一起
    更新 —— 不然新清单的 card_sha256 指的仍是旧字节（P0-1 里最要命的那个坑）。
    """
    mf = os.path.join(bundle, MANIFEST_NAME)
    if not os.path.exists(mf):
        return False
    try:
        with io.open(mf, encoding="utf-8") as f:
            man = json.load(f)
    except Exception:
        return False
    if not isinstance(man, dict):
        return False
    want = set(names)
    rows = manifest_files(man)
    changed = False
    for e in rows:
        if os.path.basename(e["path"]) in want:
            full = os.path.join(bundle, e["path"].replace("/", os.sep))
            if os.path.exists(full):
                e["sha256"] = _sha256(full)
                e["bytes"] = os.path.getsize(full)
                changed = True
    if not changed:
        return False
    man["files"] = [{"path": e["path"], "sha256": e["sha256"],
                     "bytes": e["bytes"]} for e in rows]
    man.setdefault("schema_version", MANIFEST_SCHEMA_VERSION)
    man["files_n"] = len(man["files"])
    man["bytes"] = sum(int(f.get("bytes") or 0) for f in man["files"])
    if "card.db" in want:
        db = os.path.join(bundle, "card.db")
        if os.path.exists(db):
            for e in man["files"]:
                if e["path"] == "card.db":
                    man["card_sha256"] = e["sha256"]
                    man["card_bytes"] = e["bytes"]
                    break
            ident = manifest_identity(db, bundle)
            for k, v in ident.items():
                man[k] = v
            if snapshot_method:
                man["snapshot_method"] = snapshot_method
            man["refreshed_at"] = time.time()
    # 刷新 files/card_sha256 也改了 body：有密钥就重签，否则旧 sig 立刻失效。
    try:
        import seal as _seal
        _key = _seal.load_key(bundle, create=False)
        if _key:
            man["sig"] = _seal.sign(
                {k: v for k, v in man.items() if k != "sig"}, _key)
    except Exception:
        _key = None
    with io.open(mf, "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    return True


def backup_to(card_dir, target, confirm=False):
    """把活卡整包拷到目标盘（走 portable.build），并给它发"备份 #n"的身份证。"""
    target = os.path.abspath(target)
    if not confirm:
        return {"ok": True, "dry_run": True, "target": target,
                "note": "确认要写盘请传 confirm=true"}
    plan = {"ok": True}
    sys.path.insert(0, os.path.join(card_dir, "tools"))
    try:
        import portable
    finally:
        sys.path.pop(0)
    # portable.build 把卡直接铺在目标根目录（不是目标下再建一层 whale-card/）
    bundle = target
    if os.path.exists(os.path.join(bundle, "card.db")):
        return {"ok": False, "why": "目标里已经有一份卡了（card.db 已存在）",
                "how": "换一块空盘，或者先确认那块盘上的旧副本不要了"}
    os.makedirs(bundle, exist_ok=True)
    from types import SimpleNamespace
    args = SimpleNamespace(target=target, confirm=True, allow_running=True,
                           with_ffmpeg=None)
    rc = portable.cmd_build(args)
    if rc not in (0, None):
        return {"ok": False, "why": "portable.build 返回 %s" % rc}
    # build 拷的是文件字节，可能落后于 WAL —— 用一致性快照覆盖它
    _snapshot_db(os.path.join(card_dir, "card.db"), os.path.join(bundle, "card.db"))
    # 覆盖之后 manifest 里 card.db 那一行的哈希就过期了 —— 不补，
    # portable verify 会报「包有问题，别拿去跑」（2026-11-03 实测踩到）。
    _refresh_manifest_hash(bundle, ["card.db"], snapshot_method="sqlite-backup-api")
    info = register(card_dir, bundle, ROLE_BACKUP,
                    note="一键备份 @ %s" % time.strftime("%Y-%m-%d %H:%M"))
    return {"ok": True, "wal": info.get("wal_before_mb") if isinstance(info, dict) else None,
            "backup": info, "bundle": bundle}


def sync_to(card_dir, target, confirm=False, reseed=False):
    """把活卡的最新状态同步到一块落后于它的备份盘。

    只允许"备份落后"这一种情况：
      一致 → 什么都不做
      落后且链头对得上 → 直接覆盖 card.db（先 checkpoint）
      超前 / 分叉 → 拒绝
    """
    target = os.path.abspath(target)
    main = card_fp(os.path.join(card_dir, "card.db"))
    back = card_fp(os.path.join(target, "card.db"))
    if not back.get("head_hash"):
        return {"ok": False, "why": "目标里没有卡（或读不出来）", "target": target}
    if back["head_hash"] == main["head_hash"]:
        return {"ok": True, "action": "noop", "why": "两边链头一样，不用同步",
                "head_seq": main["head_seq"]}
    if (back.get("head_seq") or 0) > (main.get("head_seq") or 0):
        return {"ok": False, "why": "备份比主盘还新（%s > %s），拒绝用主盘覆盖它"
                % (back.get("head_seq"), main.get("head_seq"))}
    if not _prefix_match(back, main):
        last_ok, orphans, err = _fork_of(
            os.path.join(card_dir, "card.db"), os.path.join(target, "card.db"))
        if not reseed:
            return {"ok": False,
                    "why": "两条链在 seq=%s 之后就分叉了：备份盘上有 %d 条主盘没有的记录。"
                           "分叉时不敢自动覆盖 —— 万一那几条才是真的呢。"
                           % (last_ok, len(orphans)),
                    "need_reseed": True,
                    "fork_at": last_ok,
                    "orphans": orphans[:8],
                    "backup_head": (back.get("head_hash") or "")[:12],
                    "main_seq": main.get("head_seq")}
        if not confirm:
            return {"ok": True, "dry_run": True, "action": "reseed",
                    "fork_at": last_ok, "orphans": orphans[:8],
                    "n_orphans": len(orphans),
                    "from_seq": back.get("head_seq"), "to_seq": main.get("head_seq"),
                    "note": "重做备份会丢掉上面这些孤儿记录（主盘上没有它们）。"
                            "确认请传 confirm=true 且 reseed=true"}
        # 真做：先把丢掉的东西记成证据，再整份覆盖
        try:
            import json as _json
            import time as _time
            log = {"at": _time.time(),
                   "when": _time.strftime("%Y-%m-%d %H:%M:%S"),
                   "main_db": os.path.join(card_dir, "card.db"),
                   "backup_db": os.path.join(target, "card.db"),
                   "fork_at": last_ok,
                   "main_seq": main.get("head_seq"),
                   "backup_seq": back.get("head_seq"),
                   "orphans": orphans}
            with io_open_seedlog(os.path.join(target, "SEED-LOG.json")) as fh:
                fh.write(_json.dumps(log, ensure_ascii=False, indent=1))
        except Exception:
            pass
    if not confirm:
        return {"ok": True, "dry_run": True, "action": "sync",
                "from_seq": back.get("head_seq"), "to_seq": main.get("head_seq"),
                "files_mb": round(os.path.getsize(os.path.join(card_dir, "card.db")) / 1e6, 1),
                "note": "确认要写盘请传 confirm=true"}
    g = _guard(card_dir, "同步")
    if not g.get("ok"):
        return g
    _snapshot_db(os.path.join(card_dir, "card.db"), os.path.join(target, "card.db"))
    # 覆盖之后清单里那一行就过期了 —— 顺手刷新，否则 verify 会误报「包有问题」
    _refresh_manifest_hash(target, ["card.db", "DISK-ROLE.json"],
                           snapshot_method="sqlite-backup-api")
    # 媒体目录增量拷（备份盘上多出来的文件不删，只补缺）
    msrc = os.path.join(card_dir, "media")
    mdst = os.path.join(target, "media")
    n_media = 0
    if os.path.isdir(msrc):
        for dirpath, _dirnames, names in os.walk(msrc):
            for fn in names:
                sp = os.path.join(dirpath, fn)
                rel = os.path.relpath(sp, msrc)
                tp = os.path.join(mdst, rel)
                if not os.path.exists(tp):
                    os.makedirs(os.path.dirname(tp), exist_ok=True)
                    shutil.copy2(sp, tp)
                    n_media += 1
    after = card_fp(os.path.join(target, "card.db"))
    oknow = after.get("head_hash") == main.get("head_hash")
    if oknow:
        role = read_role(target) or {}
        write_role(target, ROLE_BACKUP, role.get("number"),
                   main.get("instance"), after.get("head_seq"), "同步 @ %s" % time.strftime("%Y-%m-%d %H:%M"))
        reg = load_registry(card_dir)
        for serial, v in reg["disks"].items():
            if v.get("card_dir") and os.path.abspath(v["card_dir"]) == os.path.abspath(target):
                v["head_seq"] = after.get("head_seq")
                v["mem"] = after.get("mem")
                v["last_seen"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_registry(card_dir, reg)
    return {"ok": oknow, "action": "sync", "wal_before_mb": g.get("wal_before_mb"),
            "from_seq": back.get("head_seq"), "to_seq": after.get("head_seq"),
            "media_copied": n_media,
            "why": None if oknow else "同步后链头仍不一致，别用这块盘"}


def switch_role(card_dir, target):
    """主备互换。三条全中才允许：条数、链头序号、链头哈希。"""
    main = card_fp(os.path.join(card_dir, "card.db"))
    back = card_fp(os.path.join(target, "card.db"))
    diff = []
    if main.get("mem") != back.get("mem"):
        diff.append("条数 %s ≠ %s" % (main.get("mem"), back.get("mem")))
    if main.get("head_seq") != back.get("head_seq"):
        diff.append("链头序号 %s ≠ %s" % (main.get("head_seq"), back.get("head_seq")))
    if main.get("head_hash") != back.get("head_hash"):
        diff.append("链头哈希不同")
    if diff:
        return {"ok": False, "why": "两份卡还没完全一致，不能换主备",
                "diff": diff, "how": "先点一次「一键同步」再换"}
    role_t = read_role(target) or {}
    if role_t.get("role") != ROLE_BACKUP:
        return {"ok": False, "why": "目标不是备份盘（role=%s）" % role_t.get("role")}
    # 写身份证：目标升主，旧的这块降成备份 #n
    old_number = role_t.get("number") or 1
    write_role(target, ROLE_MAIN, None, back.get("instance"), back.get("head_seq"),
               "从备份 #%s 升为主盘 @ %s" % (old_number, time.strftime("%Y-%m-%d %H:%M")))
    write_role(card_dir, ROLE_BACKUP, old_number, back.get("instance"),
               main.get("head_seq"), "降为备份 #%s @ %s" % (old_number, time.strftime("%Y-%m-%d %H:%M")))
    # 注册表换向
    reg = load_registry(card_dir)
    tserial = _disk_key(target)
    mserial = _disk_key(card_dir)
    if tserial:
        reg["disks"][tserial] = {"role": ROLE_MAIN, "number": None,
                                 "instance": back.get("instance"),
                                 "card_dir": os.path.abspath(target),
                                 "head_seq": back.get("head_seq"), "mem": back.get("mem"),
                                 "last_seen": time.strftime("%Y-%m-%d %H:%M:%S")}
    if mserial:
        reg["disks"][mserial] = {"role": ROLE_BACKUP, "number": old_number,
                                 "instance": main.get("instance"),
                                 "card_dir": os.path.abspath(card_dir),
                                 "head_seq": main.get("head_seq"), "mem": main.get("mem"),
                                 "last_seen": time.strftime("%Y-%m-%d %H:%M:%S")}
    reg["main_instance"] = back.get("instance")
    save_registry(card_dir, reg)
    return {"ok": True, "switched": True,
            "new_main": os.path.abspath(target),
            "old_main_now": "备份 #%s" % old_number,
            "how": "下次启动请从新主盘目录打开；“启动控制台.bat”在主盘里"}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] == "status":
        print(json.dumps(status(HERE), ensure_ascii=False, indent=1))
        return 0
    if argv[0] == "volumes":
        print(json.dumps(list_volumes(), ensure_ascii=False, indent=1))
        return 0
    if argv[0] == "register" and len(argv) >= 3:
        print(json.dumps(register(HERE, argv[1], argv[2]), ensure_ascii=False, indent=1))
        return 0
    if argv[0] in ("backup", "sync", "switch") and len(argv) >= 2:
        fn = {"backup": backup_to, "sync": sync_to, "switch": switch_role}[argv[0]]
        confirm = "--confirm" in argv
        if argv[0] == "switch":
            print(json.dumps(fn(HERE, argv[1]), ensure_ascii=False, indent=1))
        else:
            print(json.dumps(fn(HERE, argv[1], confirm=confirm), ensure_ascii=False, indent=1))
        return 0
    print("用法: python disks.py status|volumes | register <目录> main|backup | "
          "backup <目录> [--confirm] | sync <目录> [--confirm] | switch <目录>")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
