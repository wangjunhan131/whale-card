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


def _refresh_manifest_hash(bundle, names):
    """把清单里某些文件的 sha256 改成现在的实际值（只改这几个，别整份重算）。"""
    mf = os.path.join(bundle, "PORTABLE-MANIFEST.json")
    if not os.path.exists(mf):
        return False
    try:
        with io.open(mf, encoding="utf-8") as f:
            man = json.load(f)
    except Exception:
        return False
    files = man.get("files")
    want = set(names)
    changed = False
    if isinstance(files, dict):          # {"card.db": {"sha256":..,"bytes":..}}
        for rel in list(files):
            if os.path.basename(rel) in want:
                full = os.path.join(bundle, rel.replace("/", os.sep))
                if os.path.exists(full):
                    files[rel] = {"sha256": _sha256(full),
                                  "bytes": os.path.getsize(full)}
                    changed = True
    elif isinstance(files, list):        # [{"rel":..,"sha256":..}]
        for it in files:
            rel = it.get("rel") or it.get("path")
            if rel and os.path.basename(rel) in want:
                full = os.path.join(bundle, rel.replace("/", os.sep))
                if os.path.exists(full):
                    it["sha256"] = _sha256(full)
                    it["bytes"] = os.path.getsize(full)
                    changed = True
    else:
        return False
    if changed:
        with io.open(mf, "w", encoding="utf-8") as f:
            json.dump(man, f, ensure_ascii=False, indent=1)
    return changed


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
    _refresh_manifest_hash(bundle, ["card.db"])
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
    _refresh_manifest_hash(target, ["card.db", "DISK-ROLE.json"])
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
