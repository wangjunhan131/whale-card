# -*- coding: utf-8 -*-
"""V7 第 1 件：备份完整性自动复核（3-2-1 的「能自己查」那半边）。

要回答的四件事（/health 的 checks.backup 和 probe/_v1_backup_verify.py 共用）：
  1. 最近一次备份是什么时候（还有几个小时/几天）
  2. 一共几份、其中几份在「另一块盘/另一块活盘」上
  3. 每一份的 sha256，以及它的链头是不是当前这条链上的某一节（同源）
  4. 最老一份几天前

口径（别把它当密码学证明）：
  · 「同源」= 备份的链头哈希 = 当前链在同一个 seq 上的哈希，或就是当前链头。
    整份文件的 sha256 用来回答「是不是一模一样的字节」，不是同源判据 ——
    旧备份的字节本来就跟现在的卡不一样。
  · 只读：本模块不移动、不删除、不写入任何备份文件；只 stat + 只读打开。
  · 卡外盘的路径来自 DISKS.json 的 role=backup 条目 + E:\\whale-backups；
    环境变量 WHALE_BACKUP_DIRS（分号分隔）可以整体覆盖，测试/排查用。
  · 哈希和链头按 (size, mtime) 缓存 600 秒：/health 会被频繁调用，
    不能每次都把几十 MB 的备份从头读一遍。WHALE_BACKUP_CACHE_SEC 可调。
"""
import hashlib
import json
import os
import time

import disks

DB_NAME = "card.db"
DEFAULT_FLAT = r"E:\whale-backups"
STALE_H = 7 * 24.0          # 最新一份超过 7 天 → 红
FRESH_WARN_H = 36.0         # 最新一份超过 36 小时 → 黄
MIN_OFFSITE = 2             # 3-2-1：至少两份在别的盘上

_CACHE = {}
_MAIN_SHA = {}


def _cache_sec():
    try:
        return max(0.0, float(os.environ.get("WHALE_BACKUP_CACHE_SEC", "600") or 0))
    except Exception:
        return 600.0


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _vol(path):
    try:
        return (os.path.splitdrive(os.path.abspath(path))[0] or "").upper()
    except Exception:
        return ""


def _disks_dirs(card_dir):
    """DISKS.json 里登记为 backup 的卡目录。读不到就空。"""
    out = []
    p = os.path.join(card_dir, "DISKS.json")
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f) or {}
    except Exception:
        return out
    rows = []
    for k, v in (d.get("disks") or {}).items():
        if not isinstance(v, dict) or v.get("role") != "backup":
            continue
        cd = (v.get("card_dir") or "").strip()
        if not cd:
            continue
        rows.append((int(v.get("number") or 999), k, cd))
    for _n, _k, cd in sorted(rows):
        out.append(cd)
    return out


def backup_roots(db_path):
    """要扫的备份根目录（去重、只留存在的）。"""
    card_dir = os.path.dirname(os.path.abspath(db_path))
    env = (os.environ.get("WHALE_BACKUP_DIRS", "") or "").strip()
    if env:
        roots = [x.strip() for x in env.split(";") if x.strip()]
    else:
        roots = _disks_dirs(card_dir)
        roots.append(DEFAULT_FLAT)
        roots.append(os.path.join(card_dir, "backups"))
        roots.append(os.path.join(card_dir, "snapshots"))
    seen, out = set(), []
    for r in roots:
        try:
            a = os.path.abspath(os.path.expanduser(r))
        except Exception:
            continue
        key = os.path.normcase(a)
        if key in seen or not os.path.isdir(a):
            continue
        seen.add(key)
        out.append(a)
    return out


def _scan_root(root):
    """一个目录里的备份文件。

    有 card.db 的目录 = 整卡快照，只认 card.db（不会再把它旁边的测试库
    当成备份——H:\\ 根目录就躺着一个 fade_demo.db）。
    没有 card.db 的目录 = 单库快照目录，扫 *.db。
    """
    out = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    card = os.path.join(root, DB_NAME)
    if os.path.isfile(card):
        return [(card, "card")]
    for name in names:
        low = name.lower()
        if not low.endswith(".db"):
            continue
        if low.endswith(("-wal", "-shm", "-journal")):
            continue
        p = os.path.join(root, name)
        try:
            if not os.path.isfile(p):
                continue
        except OSError:
            continue
        out.append((p, "db"))
    return out


def _main_sha(db_path):
    """当前卡的字节 sha。不按 mtime 失效（活卡一直在写，那样等于每次都重算），
    只按时间缓存 1 小时；它只用来回答「是不是一模一样的字节」。"""
    now = time.time()
    key = os.path.normcase(os.path.abspath(db_path))
    hit = _MAIN_SHA.get(key)
    if hit and (now - hit["at"]) < 3600.0:
        return hit["sha"]
    try:
        sha = sha256_file(db_path)
    except Exception:
        sha = ""
    _MAIN_SHA[key] = {"at": now, "sha": sha}
    return sha


def _cached(path, st):
    now = time.time()
    key = (int(st.st_size), int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))))
    hit = _CACHE.get(path)
    if hit and hit["key"] == key and (now - hit["at"]) < _cache_sec():
        return hit["sha"], hit["fp"]
    try:
        sha = sha256_file(path)
    except Exception:
        sha = ""
    try:
        fp = disks.card_fp(path)
    except Exception as e:
        fp = {"error": str(e)[:120], "head_seq": 0, "head_hash": None,
              "instance": None}
    _CACHE[path] = {"key": key, "at": now, "sha": sha, "fp": fp}
    return sha, fp


def inspect_backups(db_path, roots=None, use_cache=True):
    """跑一遍备份复核。返回 dict（见模块头四件事 + ok/pass/red/warn）。"""
    db_path = os.path.abspath(db_path)
    now = time.time()
    main_vol = _vol(db_path)
    try:
        main_fp = disks.card_fp(db_path)
    except Exception as e:
        main_fp = {"error": str(e)[:120], "head_seq": 0, "head_hash": None,
                   "instance": None}
    main_hash = main_fp.get("head_hash") or ""
    main_seq = int(main_fp.get("head_seq") or 0)
    main_inst = main_fp.get("instance") or ""

    if roots is None:
        roots = backup_roots(db_path)
    main_sha = _main_sha(db_path)

    seen, items, skipped = set(), [], []
    for root in roots:
        for path, kind in _scan_root(root):
            key = os.path.normcase(os.path.abspath(path))
            if key in seen:
                continue
            seen.add(key)
            try:
                st = os.stat(path)
            except OSError:
                continue
            if use_cache:
                sha, fp = _cached(path, st)
            else:
                try:
                    sha = sha256_file(path)
                except Exception:
                    sha = ""
                try:
                    fp = disks.card_fp(path)
                except Exception as e:
                    fp = {"error": str(e)[:120], "head_seq": 0,
                          "head_hash": None, "instance": None}
            err = fp.get("error") or ""
            if "no such table: timeline" in err:
                # 没有 timeline 表 = 不是这张卡的库（测试库/旧快照），
                # 不参与「备份完整性」判断，但要让人看得见它被跳过了。
                skipped.append({"path": path, "why": "not_a_card_db"})
                continue
            head_seq = int(fp.get("head_seq") or 0)
            head_hash = fp.get("head_hash") or ""
            inst = fp.get("instance") or ""
            readable = bool(head_hash) and not err
            same_source = bool(readable and main_hash and
                               (head_hash == main_hash or
                                disks._prefix_match(fp, main_fp)))
            ahead = bool(readable and main_seq and head_seq > main_seq)
            vol = _vol(path)
            items.append({
                "path": path,
                "kind": kind,
                "volume": vol,
                "offsite": bool(main_vol and vol and vol != main_vol),
                "bytes": int(st.st_size),
                "mtime": round(st.st_mtime, 3),
                "age_h": round((now - st.st_mtime) / 3600.0, 1),
                "sha256": sha,
                "same_bytes": bool(sha and main_sha and sha == main_sha),
                "head_seq": head_seq,
                "head_hash8": head_hash[:8],
                "instance": inst,
                "instance_match": (not inst or not main_inst or inst == main_inst),
                "ahead": ahead,
                "readable": readable,
                "same_source": same_source,
                "error": err[:120],
            })

    items.sort(key=lambda x: x["mtime"], reverse=True)
    red, warn, problems = [], [], []
    if not items:
        red.append("backup_none")
        problems.append("一份备份都没找到（扫过：%s）" % "；".join(roots))
    offsite_n = sum(1 for x in items if x["offsite"])
    foreign = [x for x in items if x["readable"] and not x["same_source"]]
    unreadable = [x for x in items if not x["readable"]]

    newest = items[0] if items else None
    oldest = items[-1] if items else None
    newest_age_h = newest["age_h"] if newest else None
    oldest_age_d = round(oldest["age_h"] / 24.0, 1) if oldest else None
    off_items = [x for x in items if x["offsite"]]
    oldest_offsite_d = (round(off_items[-1]["age_h"] / 24.0, 1)
                        if off_items else None)

    if newest_age_h is not None:
        if newest_age_h > STALE_H:
            red.append("backup_stale:%.1fd" % (newest_age_h / 24.0))
            problems.append("最新一份备份已经 %.1f 天前（>7 天）"
                            % (newest_age_h / 24.0))
        elif newest_age_h > FRESH_WARN_H:
            warn.append("backup_newest_old:%.1fh" % newest_age_h)
    if unreadable:
        red.append("backup_unreadable:%d" % len(unreadable))
        problems.append("读不出链头的备份：%s"
                        % "；".join(x["path"] for x in unreadable[:3]))
    if foreign:
        red.append("backup_foreign:%d" % len(foreign))
        problems.append("不同源的备份（链头不在当前链上）：%s"
                        % "；".join("%s%s" % (x["path"],
                                             "（比当前卡新）" if x["ahead"] else "")
                                   for x in foreign[:3]))
    if offsite_n < MIN_OFFSITE:
        warn.append("backup_offsite_low:%d" % offsite_n)

    ok = not red
    res = {
        "ok": ok,
        "pass": bool(ok and offsite_n >= MIN_OFFSITE),
        "count": len(items),
        "offsite_n": offsite_n,
        "newest_age_h": newest_age_h,
        "newest_at": (time.strftime("%Y-%m-%d %H:%M", time.localtime(newest["mtime"]))
                      if newest else None),
        "newest_path": newest["path"] if newest else None,
        "oldest_age_d": oldest_age_d,
        "oldest_offsite_d": oldest_offsite_d,
        "same_source_n": sum(1 for x in items if x["same_source"]),
        "main_head_seq": main_seq,
        "main_instance": main_inst,
        "checked_at": round(now, 3),
        "roots": list(roots),
        "skipped": skipped,
        "red": red,
        "warn": warn,
        "problems": problems,
        "items": items,
    }
    return res


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="备份完整性复核（只读）")
    ap.add_argument("--db", default=os.path.join(os.path.dirname(
        os.path.abspath(__file__)), "card.db"))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = inspect_backups(a.db, use_cache=False)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
    else:
        print("份数 %d（异地 %d） 最近 %s 前（%s） 最老 %.1f 天"
              % (r["count"], r["offsite_n"], r["newest_age_h"],
                 r["newest_at"], r["oldest_age_d"] or 0))
        for it in r["items"]:
            print("  %-58s %s %s" % (it["path"], it["sha256"][:12],
                                     "同源" if it["same_source"] else "不同源"))
        print("PASS" if r["pass"] else "FAIL", r["red"], r["warn"])
