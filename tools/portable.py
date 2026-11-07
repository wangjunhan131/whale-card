# -*- coding: utf-8 -*-
"""可移植包：造包 / 验包 / 人格注入（一键搬家的安全那一半）

设计原则（写给三个月后的自己）：
    1. 这个脚本**只读**源目录，只往目标目录写。真卡的停服务与切换由人决定，
       本脚本最多给"要不要跑"的建议，绝不自己动手 kill 服务。
    2. 一切以 manifest（sha256 清单）为准：拷完先对清单，再谈能不能跑。
    3. 锚（anchor）不能就地重建。搬家时把卡内锚导出成 carried 锚一起带走，
       新机器第一次启动用它比对 —— 否则"锚 ok"只是自己跟自己一致（见 11-02 探针实验）。

子命令：
    plan    <目标目录>                 dry-run：要拷什么、多大、目标够不够、有什么风险
    build   <目标目录> [--confirm]     真拷（不带 --confirm 只打印将要做什么）
    verify  <包目录>                   对 manifest 校验 sha256 + 必需文件检查
    inject  [--dry]                    人格注入（转调 cardslot.py）
    eject   [--dry]                    人格撤回（转调 cardslot.py）
    status                             当前卡 / 注入状态 / 锚位置一览
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE) if os.path.basename(HERE) == "tools" else HERE
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

CARD_DB = os.environ.get("WHALE_DB") or os.path.join(ROOT, "card.db")
MANIFEST = "PORTABLE-MANIFEST.json"
WHEREAMI = "WHEREAMI.json"

# 跟着卡走的东西：目录 / 文件 / 通配
INCLUDE_DIRS = ["models", "media", "snapshots", "dsh-plugin"]   # dsh-plugin：新机器装 DSH 后靠它接上
INCLUDE_FILES = ["card.db", "anchor.json", "cardslot.state.json",
                 "dsh_channel_rule.txt", "requirements.txt", "README.md",
                 "启动控制台.bat", "一键落地.bat", "接入DSH.bat",
                 # 2026-11-06 补：这两样以前不在包里 ——
                 #   chat_cfg.json 是她那 30 项说话参数（接话频率、上学时段、拆句开关…），
                 #   丢了等于搬到新家变成一个陌生人，设回默认值；
                 #   diary/ 是她自己写的日记，是"作品"不是缓存。
                 "qq_state/chat_cfg.json"]
INCLUDE_GLOBS = ["*.py", "docs/*.md", "tools/*.py", "tests/test_*.py",
                 "diary/*.md", "qq_state/chat_cfg.json"]
# 明确不带走的：运行期垃圾、临时、以及"搬过去也没意义"的
EXCLUDE_DIRS = ["__pycache__", "_trash", "_diag_archive", ".git", "runtime",
                "_portable", "backups"]
EXCLUDE_SUFFIX = [".pyc", ".log", ".tmp"]


def _sha256(path, bufsize=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(bufsize)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _iter_sources(root):
    """产出 (相对路径, 绝对路径, 字节数)。顺序稳定，方便复现。"""
    seen = set()

    def add(p):
        rel = os.path.relpath(p, root).replace("\\", "/")
        if rel in seen:
            return
        if any(part in EXCLUDE_DIRS for part in rel.split("/")):
            return
        if any(rel.endswith(s) for s in EXCLUDE_SUFFIX):
            return
        seen.add(rel)
        yield rel, p, os.path.getsize(p)

    for name in INCLUDE_FILES:
        p = os.path.join(root, name)
        if os.path.exists(p):
            yield from add(p)
    for name in INCLUDE_GLOBS:
        import glob as _g
        for p in sorted(_g.glob(os.path.join(root, name))):
            if os.path.isfile(p):
                yield from add(p)
    for name in INCLUDE_DIRS:
        base = os.path.join(root, name)
        if not os.path.isdir(base):
            continue
        for cur, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for fn in sorted(files):
                yield from add(os.path.join(cur, fn))


def _free_bytes(path):
    try:
        import shutil as _s
        return _s.disk_usage(path).free
    except Exception:
        return None


def _service_running():
    """只做体检，不杀进程。看 8848 端口有没有人应。"""
    import urllib.request
    for port in (8848,):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/health" % port, timeout=2) as r:
                return True, json.loads(r.read().decode("utf-8")).get("mtime") or True
        except Exception:
            pass
    return False, None


def _mb(n):
    return round(n / 1048576.0, 1)


def _ext_anchor_path(db_path=None):
    """卡外锚的真实路径。

    ⚠️ 它不是固定文件名：`anchor._external_default()` 拿**卡的绝对路径**做 sha256
    取前 16 位当文件名。所以路径一变（换盘、换目录、换机器），文件名就变，
    看起来"卡外锚不存在" —— 这正是 11-02 探针实验里"锚 ok 但其实重建了"的根因。
    搬家时必须把旧锚改名带过去，见 build 里的 anchor.external.json。
    """
    try:
        import anchor as _a
        return _a._external_default(db_path or CARD_DB)
    except Exception:
        d = os.path.join(os.path.expanduser("~"), ".dsh", "anchors")
        h = hashlib.sha256(os.path.abspath(db_path or CARD_DB).encode("utf-8")).hexdigest()[:16]
        return os.path.join(d, "%s.json" % h)


def cmd_plan(args):
    src = ROOT
    dst = os.path.abspath(args.target)
    items = list(_iter_sources(src))
    total = sum(i[2] for i in items)
    free = _free_bytes(dst if os.path.isdir(dst) else os.path.dirname(dst) or ".")
    running, _ = _service_running()

    problems, warns = [], []
    if os.path.isdir(dst):
        try:
            if os.path.commonpath([os.path.abspath(src), dst]) == os.path.abspath(src):
                problems.append("目标在源目录里面：搬完会自己套自己")
            elif os.path.commonpath([os.path.abspath(src), dst]) == dst:
                problems.append("源在目标里面：会把家搬进自己肚子里")
        except ValueError:
            pass
    else:
        warns.append("目标目录还不存在（build 时会创建父目录）")
    if free is not None and free < total * 1.15:
        problems.append("目标剩余 %.1f MB，不够 %.1f MB + 15%% 余量" % (_mb(free), _mb(total)))
    if running:
        warns.append("服务正在跑（8848 有响应）：真搬之前必须先停，否则可能拷到写了一半的卡")

    anchor_outside = _ext_anchor_path()
    if not os.path.exists(anchor_outside):
        warns.append("卡外锚 %s 不存在：新机器上没法证明'还是同一张卡'" % anchor_outside)
    else:
        try:
            a = json.load(open(anchor_outside, encoding="utf-8"))
            warns.append("卡外锚 head_seq=%s head=%s（搬家时要一起改名带走，否则新机器会重建）"
                         % (a.get("head_seq"), str(a.get("head_hash"))[:12]))
        except Exception:
            pass

    big = sorted(items, key=lambda x: -x[2])[:5]
    print(json.dumps({
        "ok": not problems,
        "src": src, "dst": dst,
        "files": len(items), "bytes": total, "mb": _mb(total),
        "free_mb": _mb(free) if free else None,
        "service_running": running,
        "problems": problems, "warnings": warns,
        "biggest": [{"file": r, "mb": _mb(s)} for r, _p, s in big],
        "next": "plan 通过 → build <目标> --confirm → verify <目标>" +
                ("（先停服务！）" if running else ""),
    }, ensure_ascii=False, indent=1))
    return 0 if not problems else 2


def cmd_build(args):
    dst = os.path.abspath(args.target)

    # ---- 安全闸 1：服务在跑就不许拷 ----
    # WAL 里可能压着十几 MB 还没合并的写入（11-02 实测 14.1 MB），
    # 这时拷 card.db 会静默丢掉最近发生过的事 —— 而新机器上看起来一切正常。
    running, _ = _service_running()
    if running and not args.allow_running:
        print(json.dumps({
            "ok": False, "why": "服务正在跑（8848 有响应）。先停掉再拷：",
            "how": "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                   "Where-Object { $_.CommandLine -like '*mind_server*' } | Stop-Process -Force",
            "why_it_matters": "卡的 WAL 里可能还有没合并的写入，热拷会丢数据",
            "override": "确实要热拷就加 --allow-running（不建议）",
        }, ensure_ascii=False, indent=1))
        return 3

    items = list(_iter_sources(ROOT))
    total = sum(i[2] for i in items)
    if not args.confirm:
        print(json.dumps({"ok": True, "dry_run": True, "files": len(items),
                          "mb": _mb(total), "dst": dst,
                          "note": "加 --confirm 才真拷"}, ensure_ascii=False, indent=1))
        return 0

    # ---- 安全闸 2：拷卡之前，把 WAL 合并进主库 ----
    # 只碰"这张卡的数据库文件"，且必须是服务已停的状态。
    wal_info = {}
    try:
        alive, _ = _service_running()
        if alive and args.allow_running:
            # 2026-11-06 修：以前 --allow-running 只过了第一道闸，
            # 到这里照样 raise，等于这个开关根本没用（热拷永远走不通）。
            # 热拷时跳过 checkpoint、明确记下"WAL 没合并"，别假装干净。
            wal_info = {"skipped": "服务在跑，跳过 checkpoint（热拷，WAL 里的写入可能没进包）"}
            raise_skip = True
        else:
            raise_skip = False
        if alive and not args.allow_running:
            raise RuntimeError("服务又起来了，拒绝 checkpoint")
        db = CARD_DB
        if not raise_skip and os.path.exists(db):
            pre = os.path.getsize(db) if os.path.exists(db) else 0
            wal = db + "-wal"
            pre_wal = os.path.getsize(wal) if os.path.exists(wal) else 0
            import sqlite3
            c = sqlite3.connect(db)
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            c.commit()
            c.close()
            wal_info = {"db_before_mb": _mb(pre),
                        "wal_before_mb": _mb(pre_wal),
                        "wal_after_mb": _mb(os.path.getsize(wal)) if os.path.exists(wal) else 0}
    except Exception as e:
        print(json.dumps({"ok": False, "why": "checkpoint 失败，不敢继续拷：%s" % e},
                         ensure_ascii=False))
        return 4

    # 真拷：先清出一份空的包目录，再逐个写，同时算 manifest
    os.makedirs(dst, exist_ok=True)
    manifest = {"built_at": time.time(), "src": ROOT, "files": {}, "bytes": 0,
                "wal": wal_info}
    t0 = time.time()
    for rel, abs_src, size in items:
        out = os.path.join(dst, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        shutil.copy2(abs_src, out)
        h = _sha256(out)
        manifest["files"][rel] = {"sha256": h, "bytes": size}
        manifest["bytes"] += size
        print("  %-52s %8.1f MB  %s" % (rel, size / 1048576.0, h[:12]))
    anchor_src = os.path.join(ROOT, "anchor.json")
    if os.path.exists(anchor_src):
        carried = os.path.join(dst, "anchor.carried.json")
        shutil.copy2(anchor_src, carried)
        manifest["carried_anchor"] = {"path": "anchor.carried.json",
                                     "sha256": _sha256(carried)}
    # 卡外锚也必须带走：它是唯一"不在卡里"的那根钉子。
    # 落地时按新路径算新文件名再写回去（见 inject/land 的说明），否则新机器会安静地重建一份。
    ext = _ext_anchor_path()
    if os.path.exists(ext):
        out = os.path.join(dst, "anchor.external.json")
        shutil.copy2(ext, out)
        try:
            _a = json.load(open(out, encoding="utf-8"))
            _seq = _a.get("head_seq")
        except Exception:
            _seq = None
        manifest["carried_external_anchor"] = {
            "path": "anchor.external.json", "sha256": _sha256(out),
            "from_db": CARD_DB, "head_seq": _seq}
    # 可选：把 ffmpeg 一起带走（视频抽帧/语音转写靠它，缺了那两条路直接废）
    if getattr(args, "with_ffmpeg", None):
        src_ff = args.with_ffmpeg
        if os.path.isfile(src_ff):
            outdir = os.path.join(dst, "tools", "ffmpeg")
            os.makedirs(outdir, exist_ok=True)
            out = os.path.join(outdir, os.path.basename(src_ff))
            shutil.copy2(src_ff, out)
            manifest.setdefault("extra", {})["ffmpeg"] = {
                "path": os.path.relpath(out, dst).replace("\\", "/"),
                "sha256": _sha256(out), "bytes": os.path.getsize(out)}
            print("  ffmpeg -> %s (%.1f MB)" % (manifest["extra"]["ffmpeg"]["path"],
                                                os.path.getsize(out) / 1048576.0))
        else:
            print("  [!] 找不到 ffmpeg：%s（跳过，视频/语音转写会不可用）" % src_ff)

    with open(os.path.join(dst, MANIFEST), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    with open(os.path.join(dst, WHEREAMI), "w", encoding="utf-8") as f:
        json.dump({"from": ROOT, "to": dst, "at": time.time(),
                   "files": len(manifest["files"]), "bytes": manifest["bytes"]},
                  f, ensure_ascii=False, indent=1)
    print(json.dumps({"ok": True, "dst": dst, "files": len(manifest["files"]),
                      "mb": _mb(manifest["bytes"]),
                      "seconds": round(time.time() - t0, 1),
                      "next": "verify %s" % dst}, ensure_ascii=False, indent=1))
    return 0


def cmd_verify(args):
    bundle = os.path.abspath(args.bundle)
    mpath = os.path.join(bundle, MANIFEST)
    if not os.path.exists(mpath):
        print(json.dumps({"ok": False, "why": "没有 %s，不是便携包" % MANIFEST},
                         ensure_ascii=False))
        return 1
    man = json.load(open(mpath, encoding="utf-8"))
    bad, miss = [], []
    for rel, meta in man["files"].items():
        p = os.path.join(bundle, rel.replace("/", os.sep))
        if not os.path.exists(p):
            miss.append(rel)
            continue
        if _sha256(p) != meta["sha256"]:
            bad.append(rel)
    need = ["card.db", "启动控制台.bat"]
    miss += [n for n in need if not os.path.exists(os.path.join(bundle, n))]
    ok = not bad and not miss
    print(json.dumps({"ok": ok, "files_checked": len(man["files"]),
                      "missing": miss[:10], "hash_mismatch": bad[:10],
                      "carried_anchor": man.get("carried_anchor") is not None,
                      "verdict": "包完整" if ok else "包有问题，别拿去跑"},
                     ensure_ascii=False, indent=1))
    return 0 if ok else 2


def cmd_land(args):
    """在"新机器"上落地：把包里带的锚写回**新路径**的名字，改写卡槽状态。

    为什么必须做这一步：卡外锚的文件名 = sha256(卡绝对路径)[:16]。
    路径一变，文件名就变；不改名写回，新机器会认为"没有卡外锚"，
    然后按卡内容重建一份 —— 于是"锚 ok"，但其实丢了"这卡是从哪来的"这层证据。
    """
    bundle = os.path.abspath(args.bundle)
    mpath = os.path.join(bundle, MANIFEST)
    if not os.path.exists(mpath):
        print(json.dumps({"ok": False, "why": "没有 %s，不是便携包" % MANIFEST},
                         ensure_ascii=False, indent=1))
        return 1
    man = json.load(open(mpath, encoding="utf-8"))
    new_db = os.path.join(bundle, "card.db")
    if not os.path.exists(new_db):
        print(json.dumps({"ok": False, "why": "包里没有 card.db"}, ensure_ascii=False))
        return 1

    anchor_home = args.anchor_home or os.path.join(os.path.expanduser("~"), ".dsh", "anchors")
    os.makedirs(anchor_home, exist_ok=True)
    h = hashlib.sha256(os.path.abspath(new_db).encode("utf-8")).hexdigest()[:16]
    dst_ext = os.path.join(anchor_home, "%s.json" % h)

    report = {"bundle": bundle, "new_db": new_db, "anchor_target": dst_ext, "did": []}

    src_ext = os.path.join(bundle, "anchor.external.json")
    if os.path.exists(src_ext) and not args.dry:
        shutil.copy2(src_ext, dst_ext)
        a = json.load(open(dst_ext, encoding="utf-8"))
        a["db"] = new_db                     # 锚里记的"我钉的是哪张卡"要改成新路径
        a["landed_from"] = man.get("src")
        a["landed_at"] = time.time()
        with open(dst_ext, "w", encoding="utf-8") as f:
            json.dump(a, f, ensure_ascii=False, indent=1)
        report["did"].append("卡外锚已按新路径写回：head_seq=%s head=%s"
                             % (a.get("head_seq"), str(a.get("head_hash"))[:12]))
        report["anchor_head_seq"] = a.get("head_seq")
    elif not os.path.exists(src_ext):
        report["warn"] = "包里没有 anchor.external.json（旧包或导出时没有卡外锚）"

    # 卡内锚：db 字段改成新路径（代码不读它，但别留谎话）
    local = os.path.join(bundle, "anchor.json")
    if os.path.exists(local) and not args.dry:
        a = json.load(open(local, encoding="utf-8"))
        a["db"] = new_db
        with open(local, "w", encoding="utf-8") as f:
            json.dump(a, f, ensure_ascii=False, indent=1)
        report["did"].append("卡内 anchor.json 的 db 字段已改指新路径")

    # 卡槽状态：dsh_home 是旧机器的，改写成本机（否则 status 会骗人）
    st_path = os.path.join(bundle, "cardslot.state.json")
    if os.path.exists(st_path) and not args.dry:
        st = json.load(open(st_path, encoding="utf-8"))
        old = st.get("dsh_home")
        st["dsh_home"] = args.dsh_home or os.path.join(os.path.expanduser("~"), ".dsh")
        st["landed_at"] = time.time()
        st["landed_from"] = old
        with open(st_path, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
        report["did"].append("卡槽状态 dsh_home：%s → %s" % (old, st["dsh_home"]))

    # 落地改过文件，manifest 里对应哈希要更新，否则 verify 会说"被篡改"
    if not args.dry:
        changed = [r for r in ("anchor.json", "cardslot.state.json")
                   if r in man["files"]]
        for rel in changed:
            p = os.path.join(bundle, rel.replace("/", os.sep))
            man["files"][rel]["sha256"] = _sha256(p)
        man["landed_at"] = time.time()
        with open(mpath, "w", encoding="utf-8") as f:
            json.dump(man, f, ensure_ascii=False, indent=1)
        if changed:
            report["did"].append("manifest 已更新：%s" % ", ".join(changed))

    report["next"] = [
        "验证一遍：python tools/portable.py verify \"%s\"" % bundle,
        "起服务：python bridge.py health（会自己拉起 mind_server）",
        "注入人格：python tools/portable.py inject  → 之后开 DSH 新会话，她就在卡里说话",
        "注意：新机器要先有 Python + numpy/psutil（图片还要 Pillow）",
    ]
    print(json.dumps({"ok": True, "dry_run": bool(args.dry), **report},
                     ensure_ascii=False, indent=1))
    return 0


def _clear_retired(bundle, out, args=None):
    """把退役标记清掉（「这张卡重新上岗」）。

    必须挂**每一条**成功路径上：早退的「不用交棒」也是成功。
    2026-11-07 用户实测：U 盘上双击一键落地，链头本来就是本机（同一台机器
    拷出来的副本），于是走早退分支、退役标记留着，控制台一直显示只读。
    """
    if args is not None and getattr(args, "dry", False):
        try:
            import disks as _dk
            if _dk.read_retired(bundle):
                out["did"].append("（彩排）会清掉退役标记")
        except Exception:
            pass
        return
    try:
        import disks as _dk
        if _dk.read_retired(bundle):
            _dk.set_retired(bundle, False)
            out["did"].append("清掉了退役标记（这张卡重新上岗）")
    except Exception as e:
        out["did"].append("清退役标记失败（不影响交棒）：%s" % e)


def cmd_handover(args):
    """在新机器上给链条「交棒」：让这张卡认新机器能写。

    为什么必须有这一步（2026-11-06 实测）：
        卡的 timeline 带 writer 列，链头记着「哪台机器写的」。
        搬到新机器后 writer-id 变了，服务启动发现链头是别人写的
        → writer_lock 非空 → 整张卡变只读（/report 返回 409 card_locked）。
        这不是 bug，是防两条链分叉的设计；但搬家时必须显式交棒一次，
        否则「搬过去能读不能写」= 她一到新家就哑了。

    做法：追加一条 handover 事件（allow_foreign=True 越过链头检查），
    之后链头就是本机，正常写。
    """
    import sqlite3
    import timeline as _tl

    bundle = os.path.abspath(args.bundle)
    db = os.path.join(bundle, "card.db")
    if not os.path.exists(db):
        print(json.dumps({"ok": False, "why": "包里没有 card.db"}, ensure_ascii=False))
        return 1

    me = _tl.writer_id()
    # --dry 必须真的什么都不写：用只读连接，否则 sqlite 关闭时可能把 WAL 合并回
    # 主库（内容不变但 mtime 变，测试会判「动了」）。
    if args.dry:
        c = sqlite3.connect("file:" + db.replace("\\", "/") + "?mode=ro",
                            uri=True, timeout=30.0)
    else:
        c = sqlite3.connect(db, timeout=30.0)
    c.row_factory = sqlite3.Row
    tl = _tl.Timeline(c, writer=me)
    head = tl.head_writer()
    out = {"db": db, "my_writer": me, "head_writer": head, "did": []}

    if not head:
        out["ok"] = True
        out["note"] = "链头没记 writer（老卡），不需要交棒"
        _clear_retired(bundle, out, args)
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    if head == me:
        out["ok"] = True
        out["note"] = "链头就是本机，不用交棒"
        # 2026-11-07 修：这条早退路上原来**不清退役标记**。
        # 而「在同一台机器上做的副本」链头本来就是我 —— 一键落地走的正是这条，
        # 于是退役标记永远清不掉、控制台一直让点「解除退役」。用户实测踩到了。
        _clear_retired(bundle, out, args)
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    if args.dry:
        out["ok"] = True
        out["dry_run"] = True
        out["note"] = "会把链头从 %s 交给 %s" % (head, me)
        _clear_retired(bundle, out, args)
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0

    seq = tl.append("handover",
                    "从 %s 交棒给 %s（搬机器）" % (head, me),
                    allow_foreign=True)
    out["did"].append("已写 handover 事件 seq=%d" % seq)
    # 交棒成功 = 这张卡现在归本机了，退役标记必须清掉，否则起来还是只读。
    _clear_retired(bundle, out, args)
    out["head_writer_now"] = tl.head_writer()
    out["ok"] = True
    out["next"] = [
        "起服务：python bridge.py health",
        "写一句话试试：POST /report，能写就是交棒成功",
        "回旧机器前，旧机器也要跑一次 handover 才能重新写",
    ]
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


def _cardslot(*argv):
    return subprocess.run([sys.executable, "-X", "utf8",
                           os.path.join(ROOT, "cardslot.py")] + list(argv),
                          capture_output=True, text=True, encoding="utf-8",
                          env=dict(os.environ, PYTHONIOENCODING="utf-8"))


def cmd_inject(args):
    # 2026-11-06 修：原来写的是 _cardslot("inject") + (("--dry",) if args.dry else ())
    # —— _cardslot() 会立刻把子进程跑完再返回 CompletedProcess，往它上面加元组
    # 直接 TypeError；更糟的是 --dry 也没用，子进程已经真的落盘了才报错。
    # 正确做法：先把 argv 拼好，再调一次。
    argv = ("inject",) + (("--dry",) if args.dry else ())
    r = _cardslot(*argv)
    print((r.stdout or "").strip() or (r.stderr or "").strip())
    return r.returncode


def cmd_eject(args):
    # 同上（cmd_inject 的同一个 bug，eject 这边一模一样）
    argv = ("unload",) + (("--dry",) if args.dry else ())
    r = _cardslot(*argv)
    print((r.stdout or "").strip() or (r.stderr or "").strip())
    return r.returncode


def cmd_status(args):
    r = _cardslot("status")
    print((r.stdout or "").strip())
    print("\n卡：%s" % CARD_DB)
    print("卡目录：%s" % ROOT)
    anchor_local = os.path.join(ROOT, "anchor.json")
    anchor_outside = _ext_anchor_path()
    for p in (anchor_local, anchor_outside):
        if os.path.exists(p):
            try:
                a = json.load(open(p, encoding="utf-8"))
                print("锚 %-52s head_seq=%s head=%s" %
                      (p, a.get("head_seq"), str(a.get("head_hash"))[:12]))
            except Exception as e:
                print("锚 %s 读不了：%s" % (p, e))
        else:
            print("锚 %-52s 不存在" % p)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="可移植包：造包 / 验包 / 注入人格")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("plan"); p1.add_argument("target"); p1.set_defaults(fn=cmd_plan)
    p2 = sub.add_parser("build"); p2.add_argument("target")
    p2.add_argument("--confirm", action="store_true")
    p2.add_argument("--allow-running", action="store_true",
                    help="服务在跑也照拷（不建议，会丢 WAL 里的写入）")
    p2.add_argument("--with-ffmpeg", default=None, metavar="FFMPEG_EXE",
                    help="把 ffmpeg.exe 一起打包（视频抽帧/语音转写要用）")
    p2.set_defaults(fn=cmd_build)
    p3 = sub.add_parser("verify"); p3.add_argument("bundle"); p3.set_defaults(fn=cmd_verify)
    pl = sub.add_parser("land", help="新机器落地：写回卡外锚 + 改写卡槽状态")
    pl.add_argument("bundle")
    pl.add_argument("--anchor-home", default=None, help="锚目录（默认 ~/.dsh/anchors）")
    pl.add_argument("--dsh-home", default=None, help="DSH 家目录（默认 ~/.dsh）")
    pl.add_argument("--dry", action="store_true")
    pl.set_defaults(fn=cmd_land)
    ph = sub.add_parser("handover", help="新机器落地后交棒：让这张卡认本机能写")
    ph.add_argument("bundle")
    ph.add_argument("--dry", action="store_true")
    ph.set_defaults(fn=cmd_handover)
    p4 = sub.add_parser("inject"); p4.add_argument("--dry", action="store_true")
    p4.set_defaults(fn=cmd_inject)
    p5 = sub.add_parser("eject"); p5.add_argument("--dry", action="store_true")
    p5.set_defaults(fn=cmd_eject)
    p6 = sub.add_parser("status"); p6.set_defaults(fn=cmd_status)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
