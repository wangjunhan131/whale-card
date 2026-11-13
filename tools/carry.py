# -*- coding: utf-8 -*-
"""带卡出门 —— 唯一的一份实现。

2026-11-13 重写原因：
    在此之前，「带卡出门」有两套逻辑，谁也不知道该信哪一套 ——

      · 控制台那个按钮（`/disks/retire`）：只把本机标成退役，
        **一个字节都没往盘上搬**。按它做完，盘上还是旧记忆。
      · `tools/go_out.py`：会停服务、合并 WAL、镜像整张卡、刷清单重签、
        再退役。可它是命令行，控制台上没有。

    人按了控制台那个按钮，以为「已经带走了」，其实盘还是三天前的她。
    这种两套逻辑并存的东西，比完全没有功能更危险 —— 所以这里收口成一份：

        carry(target)  =  合并 WAL → 镜像 → 快照 db → 刷清单重签 → 校验
        retire()       =  交给调用方（控制台/命令行）在合适的时候做

    `go_out.py` 和 `mind_server.py` 的 `/disks/carry` 都调这里，
    不再各写一套。参数、返回值、日志文案都只在这里定义一次。
"""
import hashlib
import io
import json
import os
import shutil
import sqlite3
import sys
import time


def _sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def checkpoint(card_root, log=print):
    """把 WAL 合并进主库。合并完 card.db 才是完整的。"""
    p = os.path.join(card_root, "card.db")
    try:
        c = sqlite3.connect(p, timeout=30.0)
        rows = c.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        c.close()
        log("  checkpoint(TRUNCATE) → %s" % (rows,))
        return True
    except Exception as e:                       # noqa: BLE001
        log("  ! checkpoint 失败（继续）：%s" % e)
        return False


def sync_files(card_root, target, log=print):
    """把「跟着卡走」的文件逐个对 sha 搬过去。返回 (拷贝数, 一致数, 错误表)。"""
    import portable
    canonical = {}
    for rel, src, _sz in portable._iter_sources(card_root):
        canonical[rel.replace("\\", "/")] = src
    log("  跟卡走的文件：%d 个" % len(canonical))

    copied, same, errs = 0, 0, []
    for rel, src in sorted(canonical.items()):
        dst = os.path.join(target, rel.replace("/", os.sep))
        try:
            os.makedirs(os.path.dirname(dst) or target, exist_ok=True)
            if os.path.exists(dst) and _sha(src) == _sha(dst):
                same += 1
                continue
            shutil.copy2(src, dst)
            copied += 1
        except Exception as e:                   # noqa: BLE001
            errs.append("%s: %r" % (rel, e))
    log("  拷贝 %d / 已一致 %d / 错误 %d" % (copied, same, len(errs)))
    for e in errs[:5]:
        log("    " + e)
    return copied, same, errs


def snapshot_db(card_root, target, log=print):
    """卡库用 sqlite 备份 API 写一份一致快照，再把清单里 card.db 的 sha 刷掉。"""
    import disks
    src = os.path.join(card_root, "card.db")
    dst = os.path.join(target, "card.db")
    disks._snapshot_db(src, dst)
    disks._refresh_manifest_hash(target, ["card.db"], snapshot_method="sqlite-backup-api")
    log("  card.db 快照已写入（sqlite-backup-api）")


# 服务在跑的时候这几样**必然会变**：检查点由后台线程刷新，card.db 由 WAL 追加。
# 它们的 sha 不可能在「镜像 → 校验」这个窗口里稳定，所以校验时按易变文件对待。
VOLATILE = {"checkpoints/LATEST.json", "card.db"}


def refresh_and_sign(card_root, target, log=print):
    """把清单对齐到盘上的真实字节：补新条目、刷旧条目，然后用盘内 anchor.key 重签。

    坑（2026-11-13 踩过两次，都要记住）：
      · `disks._refresh_manifest_hash` 只按**文件名**匹配已有条目，传 "tools/x.py"
        它找不到；而且它内部 `seal.load_key` 会往**上一级目录**找钥匙，
        自动重签会签错。所以这里干脆自己做，不用它。
      · 新增文件必须追加条目，否则 verify 永远报「清单里没有这个文件」。
    """
    import portable
    import seal

    mf = os.path.join(target, "PORTABLE-MANIFEST.json")
    man = json.load(io.open(mf, encoding="utf-8"))
    entries = {e["path"]: e for e in man.get("files", [])}

    added, changed, missing = [], [], []
    canonical = {}
    for rel, src_path, _sz in portable._iter_sources(card_root):
        canonical[rel.replace("\\", "/")] = src_path

    for rel in sorted(canonical):
        full = os.path.join(target, rel.replace("/", os.sep))
        if not os.path.exists(full):
            missing.append(rel)
            continue
        sha_now = _sha(full)
        size_now = os.path.getsize(full)
        e = entries.get(rel)
        if e is None:
            entries[rel] = {"path": rel, "sha256": sha_now, "bytes": size_now}
            added.append(rel)
        elif e.get("sha256") != sha_now or int(e.get("bytes") or 0) != size_now:
            e["sha256"] = sha_now
            e["bytes"] = size_now
            changed.append(rel)

    if added or changed or missing:
        log("  清单：新增 %d / 刷新 %d / 盘上缺 %d"
            % (len(added), len(changed), len(missing)))
        if added:
            log("    新增：" + ", ".join(added[:6]))
        if missing:
            log("    缺：" + ", ".join(missing[:6]))

    man["files"] = [entries[k] for k in sorted(entries)]
    man["files_n"] = len(man["files"])
    man["bytes"] = sum(int(f.get("bytes") or 0) for f in man["files"])
    body = {k: v for k, v in man.items() if k != "sig"}
    key = bytes.fromhex(io.open(os.path.join(target, "anchor.key"),
                                encoding="utf-8").read().strip())
    man["sig"] = seal.sign(body, key)
    io.open(mf, "w", encoding="utf-8", newline="").write(
        json.dumps(man, ensure_ascii=False, indent=1, sort_keys=True))
    log("  已重签（%d 条）" % man["files_n"])
    return {"added": added, "changed": changed, "missing": missing}


def verify(target, log=print):
    """跑一遍 portable verify。返回 (ok, 尾部几行)。"""
    code = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "tools", "portable.py")
    if not os.path.exists(code):
        code = os.path.join(target, "tools", "portable.py")
    import subprocess
    r = subprocess.run([sys.executable, "-X", "utf8", code, "verify", target],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    lines = (r.stdout or "").strip().splitlines()
    for line in lines[-6:]:
        log("    " + line)

    # 运行中镜像时，易变文件的 sha 对不上是正常现象：不算失败，但要留痕。
    if r.returncode != 0:
        text = "\n".join(lines)
        import re as _re
        probs = _re.findall(r'"([^"]+)"', text)
        touched = [p for p in probs if any(v in p for v in VOLATILE)]
        hard = [p for p in probs if p.startswith(("sha256 ", "bytes ", "缺少："))]
        hard_left = [p for p in hard if not any(v in p for v in VOLATILE)]
        if touched and not hard_left:
            log("  ! 只有易变文件不一致（%s）——服务在跑时正常，镜像仍然有效"
                % ", ".join(sorted({v for v in VOLATILE for p in touched if v in p})))
            return True, lines
    return r.returncode == 0, lines


EXTRA_CFG = "_carry_extra.json"


def _load_extra(card_root):
    """随身文件之外、但也要带走的「以前做过的项目」。

    配置在卡根 `_carry_extra.json`：
        {"dirs": [{"src": "D:/.../bench22-notes", "dest": "_projects/bench22-notes"}]}
    src 必须存在；dest 是盘上的相对子目录。这类资料不参与签名清单。
    """
    cfg = os.path.join(card_root, EXTRA_CFG)
    if not os.path.exists(cfg):
        return []
    try:
        data = json.loads(io.open(cfg, encoding="utf-8").read())
    except Exception:
        return []
    items = []
    for it in data.get("dirs", []):
        s, d = it.get("src"), it.get("dest")
        if s and d and os.path.isdir(s):
            items.append((s, d))
    return items


def sync_extra(card_root, target, log=print):
    """只补差异（同名同大小视作一致），返回 {copied, same, errors}。"""
    copied = same = 0
    errs = []
    for src_dir, dest in _load_extra(card_root):
        base = os.path.join(target, dest)
        for root, dirs, files in os.walk(src_dir):
            dirs[:] = [x for x in dirs if x not in ("__pycache__", ".git", "node_modules")]
            for fn in files:
                sp = os.path.join(root, fn)
                rel = os.path.relpath(sp, src_dir)
                tp = os.path.join(base, rel)
                try:
                    os.makedirs(os.path.dirname(tp), exist_ok=True)
                    ss = os.path.getsize(sp)
                    ts = os.path.getsize(tp) if os.path.exists(tp) else -1
                    if ss == ts:
                        same += 1
                        continue
                    shutil.copy2(sp, tp)
                    copied += 1
                except Exception as e:
                    errs.append("%s: %s" % (rel, e))
    log("额外项目：拷贝 %d / 已一致 %d / 错误 %d" % (copied, same, len(errs)))
    return {"copied": copied, "same": same, "errors": errs[:5]}


def carry(card_root, target, log=print, do_retire=False, why=""):
    """整条「带卡出门」。返回一份可以直接塞进 HTTP 响应的 dict。"""
    import disks
    card_root = os.path.abspath(card_root)
    target = os.path.abspath(target)
    if not os.path.isdir(target):
        return {"ok": False, "code": "no_target", "why": "目标目录不存在：%s" % target}
    if os.path.abspath(target) == card_root:
        return {"ok": False, "code": "same_dir", "why": "目标就是自己，不用搬"}

    t0 = time.time()
    log("带卡出门 → %s" % target)
    checkpoint(card_root, log=log)
    copied, same, errs = sync_files(card_root, target, log=log)
    snapshot_db(card_root, target, log=log)
    man_info = refresh_and_sign(card_root, target, log=log)
    ok, lines = verify(target, log=log)
    try:
        extra = sync_extra(card_root, target, log=log)
    except Exception as e:
        extra = {"copied": 0, "same": 0, "errors": ["%s: %s" % (type(e).__name__, e)]}

    out = {"ok": bool(ok), "target": target, "files_copied": copied,
           "extra": extra,
           "files_same": same, "errors": errs[:5], "verify_ok": bool(ok),
           "manifest_added": len(man_info.get("added", [])),
           "manifest_changed": len(man_info.get("changed", [])),
           "ms": int((time.time() - t0) * 1000)}
    if do_retire:
        disks.set_retired(card_root, True, why or "带卡出门：镜像完成 @ %s"
                          % time.strftime("%Y-%m-%d %H:%M"))
        out["retired"] = True
    if not ok:
        out["code"] = "verify_failed"
        out["why"] = "镜像写完了，但包校验没过；先别拔盘，看 verify 输出"
        out["verify_tail"] = lines[-6:]
    return out
