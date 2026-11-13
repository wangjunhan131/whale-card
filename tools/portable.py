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
    build   <目标目录> [--confirm]     真拷（P0-3：先写 .partial-*，验收过了才换正式目录）
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

import disks as _disks  # noqa: E402  （清单的字段/校验只有一套，在 disks.py）
import migration as _mig  # noqa: E402  （V7-P1：迁移事件 + migrate/fork 的分界）

CARD_DB = os.environ.get("WHALE_DB") or os.path.join(ROOT, "card.db")
MANIFEST = "PORTABLE-MANIFEST.json"
WHEREAMI = "WHEREAMI.json"

# 跟着卡走的东西：目录 / 文件 / 通配
INCLUDE_DIRS = ["models", "media", "snapshots", "dsh-plugin", "checkpoints", "runtime"]   # dsh-plugin：新机器装 DSH 后靠它接上
INCLUDE_FILES = ["card.db", "anchor.json", "anchor.key", "cardslot.state.json",
                 "dsh_channel_rule.txt", "requirements.txt", "README.md",
                 "启动控制台.bat", "一键落地.bat", "接入DSH.bat",
                 # 2026-11-13 补：随身卡的三件套 ——
                 #   带卡出门（镜像到盘 + 本机退役）、一键部署（新机器上叫醒）、
                 #   接回来（旧机器重新上岗）。少一个，流程就断在半路。
                 "带卡出门.bat", "一键部署.bat", "接回来.bat",
                 # 2026-11-06 补：这两样以前不在包里 ——
                 #   chat_cfg.json 是她那 30 项说话参数（接话频率、上学时段、拆句开关…），
                 #   丢了等于搬到新家变成一个陌生人，设回默认值；
                 #   diary/ 是她自己写的日记，是"作品"不是缓存。
                 "qq_state/chat_cfg.json"]
INCLUDE_GLOBS = ["*.py", "docs/*.md", "tools/*.py", "tests/test_*.py",
                 "diary/*.md", "qq_state/chat_cfg.json"]
# 明确不带走的：运行期垃圾、临时、以及"搬过去也没意义"的
EXCLUDE_DIRS = ["__pycache__", "_trash", "_diag_archive", ".git",
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
    """只做体检，不杀进程。先看 8848 端口有没有人 listen，再看 /health。

    不能只靠 HTTP：服务忙时 /health 可能超过 2 秒，超时会被误判成「已经停了」，
    而显式 --source-dir 活卡根正是靠这个判断放行的 —— 会重演 2026-11-11。
    端口有人接就先算活着；HTTP 只用来取 mtime，取不到也保持活着。
    """
    import socket
    import urllib.request
    for port in (8848,):
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=0.8)
            sock.close()
        except OSError:
            sock = None
            continue
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/health" % port, timeout=2) as r:
                return True, json.loads(r.read().decode("utf-8")).get("mtime") or True
        except Exception:
            return True, "port-open"
    return False, None


def _mb(n):
    return round(n / 1048576.0, 1)


# ---------------------------------------------------------------- P0-3 原子写盘
# 规矩（写给以后手滑的自己）：
#   1. 正式目录只由「已经验收通过的临时目录」一次换名得到；绝不往里逐文件写。
#   2. 落地（接管）先把要改的文件备份成 *.bak-takeover-<ts>，中途失败按备份还原。
#   3. 遗留的临时目录 / 接管标记只**报告**，不自动删 —— 那可能是别人正在跑的一步。
PARTIAL_MARK = ".partial-"
TAKEOVER_IN = "TAKEOVER-IN-PROGRESS.json"
TAKEOVER_DONE = "TAKEOVER-DONE.json"
TAKEOVER_BAK = ".bak-takeover-"


def _ts_stamp():
    return time.strftime("%Y%m%d-%H%M%S")


def _tag():
    """临时名后缀：秒 + pid + 微秒，同一秒并发也不会撞。"""
    return "%s-%d-%d" % (_ts_stamp(), os.getpid(), time.time_ns() % 1000000)


def _cleanup_cmd(path):
    """给用户抄的清理命令（只打印，不执行）。"""
    if os.name == "nt":
        return 'Remove-Item -LiteralPath "%s" -Recurse -Force' % path
    return 'rm -rf "%s"' % path


def _scan_partials(dst):
    """找 <dst>.partial-* / <dst>.old-*，只报告，不删。"""
    dst = os.path.abspath(dst)
    parent = os.path.dirname(dst) or "."
    base = os.path.basename(dst)
    found = []
    try:
        names = sorted(os.listdir(parent))
    except OSError:
        return found
    for n in names:
        if not (n.startswith(base + PARTIAL_MARK)
                or n.startswith(base + ".old-")):
            continue
        p = os.path.join(parent, n)
        if not os.path.isdir(p):
            continue
        partial = n.startswith(base + PARTIAL_MARK)
        found.append({
            "path": p,
            "kind": "partial" if partial else "old",
            "hint": ("上一次打包没完成留下的临时目录；确认不要了再删"
                     if partial else
                     "换包时留下的旧目录；如果正式目录不见了，可以把它改名回去"),
            "cleanup": _cleanup_cmd(p),
        })
    return found


def _scan_takeover(bundle):
    """找 TAKEOVER-IN-PROGRESS.json，只报告，不删。"""
    bundle = os.path.abspath(bundle)
    p = os.path.join(bundle, TAKEOVER_IN)
    if not os.path.exists(p):
        return []
    info = {}
    try:
        with open(p, encoding="utf-8") as f:
            info = json.load(f)
    except Exception:
        info = {}
    done = {}
    dp = os.path.join(bundle, TAKEOVER_DONE)
    if os.path.exists(dp):
        try:
            with open(dp, encoding="utf-8") as f:
                done = json.load(f)
        except Exception:
            done = {}
    finished = bool(info and done
                    and info.get("ts") == done.get("ts")
                    and info.get("manifest_sha256") == done.get("manifest_sha256"))
    return [{
        "path": p,
        "kind": "takeover-in-progress",
        "possibly_finished": finished,
        "info": info or None,
        "hint": ("上一次接管已经写了 TAKEOVER-DONE，只是标记没删干净；确认后可删"
                 if finished else
                 "上一次接管没走完（已回滚，或还在跑）；源盘没被这里动过，先看状态再决定重跑"),
        "cleanup": _cleanup_cmd(p),
    }]


def _atomic_write_json(path, obj):
    """同目录临时文件 + os.replace：读到的一半 JSON 不可能出现。"""
    tmp = "%s.tmp-takeover-%s" % (path, _tag())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _move_tree(src, dst):
    """同盘 rename；跨盘/目录占位失败时退回 shutil.move。"""
    try:
        os.replace(src, dst)
        return "replace"
    except OSError as e:
        reason = getattr(e, "winerror", None) or e.errno or str(e)
        if os.path.exists(dst):
            if os.path.isdir(dst):
                shutil.rmtree(dst)
            else:
                os.remove(dst)
        shutil.move(src, dst)
        return "move(replace failed: %s)" % reason


def _publish_dir(staging, dst):
    """把验收过的 staging 换成正式目录 dst。

    正式目录已存在时：先整目录改名成 <dst>.old-*，再把 staging 换上来；
    换失败就把旧目录原样放回。返回 (方式, 旧目录路径或 None)。
    """
    if not os.path.exists(dst):
        return _move_tree(staging, dst), None
    if os.path.isdir(dst) and not os.listdir(dst):
        os.rmdir(dst)
        return _move_tree(staging, dst), None
    old = "%s.old-%s" % (dst, _tag())
    os.replace(dst, old)          # 失败则正式目录原封不动
    try:
        method = _move_tree(staging, dst)
    except Exception as e:
        try:
            if os.path.exists(dst):
                if os.path.isdir(dst):
                    shutil.rmtree(dst, ignore_errors=True)
                else:
                    os.remove(dst)
            os.replace(old, dst)  # 旧内容放回去
        except Exception as e2:
            raise RuntimeError(
                "换包失败，且旧目录没能放回 %s（旧内容在 %s）：%s / %s"
                % (dst, old, e, e2))
        raise
    return method, old


class _BuildVerifyError(RuntimeError):
    def __init__(self, problems):
        super().__init__("临时目录 verify_bundle 未通过")
        self.problems = list(problems or [])


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
    # 原地/互相嵌套都不许：换名那一步会把「自己」搬走，比半截包更糟。
    root = os.path.abspath(ROOT)
    try:
        common = os.path.commonpath([root, dst])
    except ValueError:
        common = ""
    if os.path.normcase(common) == os.path.normcase(root):
        print(json.dumps({"ok": False,
                          "why": "目标在卡目录里面（或就是卡目录自己），拒绝原地打包",
                          "dst": dst, "card_dir": root,
                          "how": "换一个卡目录之外的目录，例如 D:\\backup\\whale-card"},
                         ensure_ascii=False, indent=1))
        return 2
    if os.path.normcase(common) == os.path.normcase(dst):
        print(json.dumps({"ok": False,
                          "why": "卡目录在目标里面：换名会把装着活卡的外层目录整个搬走，拒绝",
                          "dst": dst, "card_dir": root,
                          "how": "目标要选一个和卡目录不相交的目录"},
                         ensure_ascii=False, indent=1))
        return 2
    leftovers = _scan_partials(dst)

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
                          "leftovers": leftovers,
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

    # 真拷：先写进 <dst>.partial-<pid>-<ts>，全部落盘 + verify_bundle 通过，
    # 才一次换名成正式目录。中途断电最多留个 .partial-*，正式目录绝不出半个。
    staging = "%s%s%s" % (dst, PARTIAL_MARK, _tag())
    existed_before = os.path.exists(dst)
    t0 = time.time()
    old_dir = None
    publish_note = ""
    try:
        os.makedirs(staging)
        entries = []
        extra = {"wal": wal_info}
        for rel, abs_src, size in items:
            out = os.path.join(staging, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            shutil.copy2(abs_src, out)
            h = _sha256(out)
            entries.append({"path": rel, "sha256": h, "bytes": size})
            print("  %-52s %8.1f MB  %s" % (rel, size / 1048576.0, h[:12]))
        anchor_src = os.path.join(ROOT, "anchor.json")
        if os.path.exists(anchor_src):
            carried = os.path.join(staging, "anchor.carried.json")
            shutil.copy2(anchor_src, carried)
            extra["carried_anchor"] = {"path": "anchor.carried.json",
                                       "sha256": _sha256(carried)}
        # 卡外锚也必须带走：它是唯一"不在卡里"的那根钉子。
        # 落地时按新路径算新文件名再写回去（见 inject/land 的说明），否则新机器会安静地重建一份。
        ext = _ext_anchor_path()
        if os.path.exists(ext):
            out = os.path.join(staging, "anchor.external.json")
            shutil.copy2(ext, out)
            try:
                with open(out, encoding="utf-8") as f:
                    _a = json.load(f)
                _seq = _a.get("head_seq")
            except Exception:
                _seq = None
            extra["carried_external_anchor"] = {
                "path": "anchor.external.json", "sha256": _sha256(out),
                "from_db": CARD_DB, "head_seq": _seq}
        # 可选：把 ffmpeg 一起带走（视频抽帧/语音转写靠它，缺了那两条路直接废）
        if getattr(args, "with_ffmpeg", None):
            src_ff = args.with_ffmpeg
            if os.path.isfile(src_ff):
                outdir = os.path.join(staging, "tools", "ffmpeg")
                os.makedirs(outdir, exist_ok=True)
                out = os.path.join(outdir, os.path.basename(src_ff))
                shutil.copy2(src_ff, out)
                extra.setdefault("extra", {})["ffmpeg"] = {
                    "path": os.path.relpath(out, staging).replace("\\", "/"),
                    "sha256": _sha256(out), "bytes": os.path.getsize(out)}
                print("  ffmpeg -> %s (%.1f MB)" % (
                    extra["extra"]["ffmpeg"]["path"],
                    os.path.getsize(out) / 1048576.0))
            else:
                print("  [!] 找不到 ffmpeg：%s（跳过，视频/语音转写会不可用）" % src_ff)

        # 写者纪元（P0-4）跟着卡走：纪元文件必须进包，否则新盘 land 时
        # bump 会从 0 起算，反而比旧写者还低 —— 纪元就不再是「第几任」。
        # 故意不进 entries：它会在 land 时被 +1，进清单的话一落地就
        # 变成「sha256 不一致」的假问题。清单里只抄 epoch 数值。
        _ep_src = os.path.join(ROOT, _disks.WRITER_EPOCH_NAME)
        if os.path.exists(_ep_src):
            shutil.copy2(_ep_src,
                         os.path.join(staging, _disks.WRITER_EPOCH_NAME))
            extra["writer_epoch_carried"] = {
                "path": _disks.WRITER_EPOCH_NAME,
                "epoch": _disks.writer_epoch(ROOT)}

        # build 是「服务停 → checkpoint → 按文件拷字节」，不是 sqlite backup API；
        # backup_to 后来用 backup API 覆盖 card.db 时会把 snapshot_method 改成
        # "sqlite-backup-api"（disks._refresh_manifest_hash 里做）。
        # --allow-running 时 checkpoint 被跳过，就别记成 checkpoint-file-copy（那是谎话）。
        method = ("hot-file-copy-wal-unmerged" if wal_info.get("skipped")
                  else "checkpoint-file-copy")
        manifest = _disks.build_manifest(
            staging, entries, snapshot_method=method, extra=extra, src=ROOT)

        with open(os.path.join(staging, MANIFEST), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=1)
        with open(os.path.join(staging, WHEREAMI), "w", encoding="utf-8") as f:
            json.dump({"from": ROOT, "to": dst, "at": time.time(),
                       "files": len(manifest["files"]), "bytes": manifest["bytes"]},
                      f, ensure_ascii=False, indent=1)

        # 验收闸：临时目录自己先过一遍完整验收，过了才有资格换名。
        rep = _disks.verify_bundle(staging)
        if not rep.get("ok"):
            raise _BuildVerifyError(rep.get("problems"))

        publish, old_dir = _publish_dir(staging, dst)
        if old_dir and not getattr(args, "keep_temp", False):
            try:
                shutil.rmtree(old_dir)
                old_dir = None
            except OSError as e:
                publish_note = "旧目录没删掉：%s（%s）" % (old_dir, e)
        elif old_dir:
            publish_note = "旧目录保留（--keep-temp）：%s" % old_dir
    except Exception as e:
        problems = getattr(e, "problems", None)
        note = "临时目录已清理"
        if os.path.isdir(staging):
            if getattr(args, "keep_temp", False):
                note = "临时目录保留（--keep-temp）：%s" % staging
            else:
                shutil.rmtree(staging, ignore_errors=True)
        elif not os.path.exists(staging):
            note = "临时目录已不在（未创建 / 已清理）"
        if existed_before and os.path.isdir(dst):
            target_state = "正式目录是旧的，原样保留"
        elif not existed_before and not os.path.exists(dst):
            target_state = "正式目录未创建（没有半个包）"
        else:
            target_state = "需要人工检查：%s" % dst
        print(json.dumps({
            "ok": False, "dst": dst,
            "why": "打包失败：%s" % e,
            "problems": (problems or [])[:20],
            "target_state": target_state,
            "temp": note,
            "leftovers": _scan_partials(dst),
            "next": "修好后重跑 build；确认没有旧内容要留，再按 leftovers[].cleanup 清临时目录",
        }, ensure_ascii=False, indent=1))
        return 5

    out = {"ok": True, "dst": dst, "files": len(manifest["files"]),
           "mb": _mb(manifest["bytes"]),
           "seconds": round(time.time() - t0, 1),
           "atomic": True, "publish": publish,
           "verified": "verify_bundle ok",
           "leftovers": leftovers,
           "next": "verify %s" % dst}
    if getattr(args, "keep_temp", False):
        out["temp_kept"] = staging
    if old_dir:
        out["old_dir_kept"] = old_dir
    if publish_note:
        out["warning"] = publish_note
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


def _writer_epoch_check(bundle, man):
    """manifest 里记的写者纪元 vs 目标目录 WRITER-EPOCH.json（只报警告）。

    正常的卡落地时 land 已经 bump 过并同步进 manifest，所以两者应当相等；
    对不上说明包被换过、纪元文件丢了、或者落地那头被人手动改过 ——
    「现在谁有写权」就说不清了，但这不毁数据，所以是 warning 不是 problem。
    """
    want = man.get("writer_epoch")
    have = _disks.writer_epoch(bundle)
    out = {"manifest": want, "disk": have, "ok": None, "warning": None}
    if want is None:
        out["warning"] = ("manifest 没记 writer_epoch（老包或打包时没抄），"
                          "目标目录当前是 %s，无法对账" % have)
        return out
    try:
        same = int(want) == int(have)
    except (TypeError, ValueError):
        out["warning"] = ("manifest.writer_epoch 不是整数：%r，目标目录 %s"
                          % (want, have))
        return out
    out["ok"] = same
    if not same:
        out["warning"] = ("writer_epoch 不一致：manifest=%s，目标目录=%s"
                          % (want, have))
    return out


def cmd_verify(args):
    bundle = os.path.abspath(args.bundle)
    mpath = os.path.join(bundle, MANIFEST)
    if not os.path.exists(mpath):
        print(json.dumps({"ok": False, "why": "没有 %s，不是便携包" % MANIFEST},
                         ensure_ascii=False))
        return 1
    if getattr(args, "deep", False):
        rep = _disks.verify_bundle(bundle)
        man = json.load(open(mpath, encoding="utf-8"))
        epoch = _writer_epoch_check(bundle, man)
        if epoch.get("warning"):
            rep["warnings"].append(epoch["warning"])
        print(json.dumps({"ok": rep["ok"], "deep": True,
                          "writer_epoch": epoch,
                          "problems": rep["problems"][:40],
                          "warnings": rep["warnings"][:40],
                          "checks": rep["checks"],
                          "verdict": "包完整（深度验收）" if rep["ok"]
                                     else "包有问题，别拿去跑"},
                         ensure_ascii=False, indent=1))
        return 0 if rep["ok"] else 2
    man = json.load(open(mpath, encoding="utf-8"))
    # 校验只有一套：字段齐不齐 / card_sha256 / 每个文件 sha —— 都在 disks.verify_manifest
    rep = _disks.verify_manifest(man, bundle)
    problems = list(rep["problems"])
    # 这两样不在 files 里也必须存在（老包可能没登记）
    for n in ("card.db", "启动控制台.bat"):
        if not os.path.exists(os.path.join(bundle, n)) and \
                not any(n in p for p in problems):
            problems.append("文件缺失：%s" % n)
    miss = [p.split("：", 1)[-1] for p in problems if p.startswith("文件缺失：")]
    bad = [p.split("：", 1)[1].split("（", 1)[0]
           for p in problems if p.startswith("sha256 不一致：")]
    ok = rep["ok"] and not problems
    print(json.dumps({"ok": ok,
                      "files_checked": len(_disks.manifest_files(man)),
                      "missing": miss[:10], "hash_mismatch": bad[:10],
                      "problems": problems[:20],
                      "schema_version": man.get("schema_version"),
                      "snapshot_method": man.get("snapshot_method"),
                      "head_seq": man.get("head_seq"),
                      "writer_epoch": man.get("writer_epoch"),
                      "card_sha256_ok": not any(
                          p.startswith("card_sha256") for p in problems),
                      "carried_anchor": man.get("carried_anchor") is not None,
                      "verdict": "包完整" if ok else "包有问题，别拿去跑"},
                     ensure_ascii=False, indent=1))
    return 0 if ok else 2


def _backup_for_takeover(paths, suffix):
    """接管前把会被改的文件各留一份 *.bak-takeover-<ts>。

    原来不存在的也记下来 —— 回滚时要把"新造出来的半成品"删掉。
    """
    backups, missing = [], []
    for p in paths:
        if os.path.exists(p):
            b = p + suffix
            shutil.copy2(p, b)
            backups.append({"path": p, "backup": b})
        else:
            missing.append(p)
    return backups, missing


def _restore_takeover(backups, missing):
    """按备份还原；先写临时文件再 os.replace，避免还原到一半又断电。"""
    failures = []
    for b in reversed(backups):
        try:
            tmp = b["path"] + ".restore-" + _tag()
            shutil.copy2(b["backup"], tmp)
            os.replace(tmp, b["path"])
        except Exception as e:
            failures.append("还原失败 %s：%s" % (b["path"], e))
    for p in missing:
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception as e:
            failures.append("删掉半成品 %s 失败：%s" % (p, e))
    return failures


def _land_apply(bundle, man, args, report):
    """真正改状态的四步。全部写完才算成功；任何一步抛异常由 cmd_land 回滚。"""
    new_db = report["new_db"]
    src_ext = os.path.join(bundle, "anchor.external.json")
    if os.path.exists(src_ext):
        with open(src_ext, encoding="utf-8") as f:
            a = json.load(f)
        a["db"] = new_db                     # 锚里记的"我钉的是哪张卡"要改成新路径
        a["landed_from"] = man.get("src")
        a["landed_at"] = time.time()
        _atomic_write_json(report["anchor_target"], a)
        report["did"].append("卡外锚已按新路径写回：head_seq=%s head=%s"
                             % (a.get("head_seq"), str(a.get("head_hash"))[:12]))
        report["anchor_head_seq"] = a.get("head_seq")
    else:
        report["warn"] = "包里没有 anchor.external.json（旧包或导出时没有卡外锚）"

    # 卡内锚：db 字段改成新路径（代码不读它，但别留谎话）
    local = os.path.join(bundle, "anchor.json")
    if os.path.exists(local):
        with open(local, encoding="utf-8") as f:
            a = json.load(f)
        a["db"] = new_db
        _atomic_write_json(local, a)
        report["did"].append("卡内 anchor.json 的 db 字段已改指新路径")

    # 卡槽状态：dsh_home 是旧机器的，改写成本机（否则 status 会骗人）
    st_path = os.path.join(bundle, "cardslot.state.json")
    if os.path.exists(st_path):
        with open(st_path, encoding="utf-8") as f:
            st = json.load(f)
        old = st.get("dsh_home")
        st["dsh_home"] = args.dsh_home or os.path.join(os.path.expanduser("~"), ".dsh")
        st["landed_at"] = time.time()
        st["landed_from"] = old
        _atomic_write_json(st_path, st)
        report["did"].append("卡槽状态 dsh_home：%s → %s" % (old, st["dsh_home"]))

    # 落地改过文件，manifest 里对应哈希要更新，否则 verify 会说"被篡改"
    rows = _disks.manifest_files(man)
    changed = [r for r in ("anchor.json", "cardslot.state.json")
               if any(e.get("path") == r for e in rows)]
    for e in rows:
        if e.get("path") in changed:
            p = os.path.join(bundle, e["path"].replace("/", os.sep))
            e["sha256"] = _sha256(p)
            e["bytes"] = os.path.getsize(p)
    if changed:
        man["files"] = [{"path": e["path"], "sha256": e["sha256"],
                         "bytes": e["bytes"]} for e in rows]
        man["files_n"] = len(man["files"])
        man["bytes"] = sum(int(f.get("bytes") or 0) for f in man["files"])
    man["landed_at"] = time.time()
    # 落地本来就会改几个文件（锚/卡槽），所以清单要重签一次：
    # 不重签的话，下一次 verify 会把「我们自己改的」判成篡改。
    try:
        import checkpoint as _cp
        _key = _cp.load_key(bundle, create=False)
        if _key:
            man["sig"] = _cp.sign({k: v for k, v in man.items() if k != "sig"}, _key)
    except Exception:  # noqa: BLE001
        pass
    _atomic_write_json(os.path.join(bundle, MANIFEST), man)
    if changed:
        report["did"].append("manifest 已更新：%s" % ", ".join(changed))
    return report


# ---------------------------------------------------------------- P1 迁移 / 分叉
def _land_mode(args):
    """--migrate / --fork 必须选一个。返回 (mode, None) 或 (None, 报错)。

    为什么不在代码里给个默认值：搬家（同一张卡）和分叉（另一张卡）后果差太远 ——
    一个要把源盘锁成只读，一个绝不能碰源盘。让程序替用户猜，就是在含糊。
    """
    m = bool(getattr(args, "migrate", False))
    f = bool(getattr(args, "fork", False))
    if m and f:
        return None, {"ok": False,
                      "why": "--migrate 和 --fork 是两件事，只能选一个",
                      "options": list(_mig.MODE_HELP),
                      "how": "同一张卡换地方 = --migrate；从这张卡分出另一张 = --fork"}
    if not (m or f):
        return None, {"ok": False,
                      "why": "没说这是搬家还是分叉：必须给 --migrate 或 --fork",
                      "options": list(_mig.MODE_HELP),
                      "how": "两个都不给、或者同时给，都会被拒绝（退出码 2）"}
    return (_mig.MODE_MIGRATE if m else _mig.MODE_FORK), None


def _land_event(bundle, kind, mode=None, allow_foreign=False, **payload):
    """往包里的卡库追加一条迁移事件（写完自己会只读复核哈希链）。"""
    db = os.path.join(bundle, "card.db")
    if mode:
        payload["mode"] = mode
    return _mig.append_migration_event(db, kind, allow_foreign=allow_foreign, **payload)


def _resync_anchor(bundle, new_db, dst_ext, landed_from, report):
    """事件写完后把锚重新写准。

    Timeline.append 每次都会 _touch_anchor，而锚是**整条重建**的 —— 它会把
    _land_apply 刚写进去的 landed_from/landed_at 抹掉。所以所有事件写完之后
    在这里补一次，顺手让锚的条数/链头跟库对上。
    """
    try:
        import anchor as _a
        state = _a.read_card_state(new_db)
        if not state:
            report.setdefault("warnings", []).append("锚重新同步失败：读不到卡状态")
            return None
        rec = {"version": 1, "db": os.path.abspath(new_db),
               "count": state["count"], "head_seq": state["head_seq"],
               "head_hash": state["head_hash"], "last_ts": state["last_ts"],
               "updated_ts": time.time(),
               "landed_from": landed_from, "landed_at": time.time()}
        _atomic_write_json(os.path.join(bundle, "anchor.json"), rec)
        _atomic_write_json(dst_ext, rec)
        report["anchor_head_seq"] = rec["head_seq"]
        report["did"].append("锚已按迁移后的链头重写：%d 条，head_seq=%s"
                             % (rec["count"], rec["head_seq"]))
        return rec
    except Exception as e:
        report.setdefault("warnings", []).append("锚重新同步失败：%s" % e)
        return None


RETIRE_REFUSED = 7  # --migrate 拒绝退休源目录（本次不写任何文件）


def _path_inside(a, b):
    """a 是否等于 b，或位于 b 里面。不同盘符返回 False（commonpath 会抛 ValueError）。"""
    a = os.path.normcase(os.path.realpath(os.path.abspath(a)))
    b = os.path.normcase(os.path.realpath(os.path.abspath(b)))
    try:
        return os.path.commonpath([a, b]) == b
    except ValueError:
        return False


def _resolve_retire_source(args, man, bundle):
    """决定 --migrate 要退休哪个目录，并在写任何东西之前验明正身。

    返回 {"ok","dir","from","reason","instance_id","identity_id","live_busy"}。
    ok=False 时调用方必须拒绝本次 land（退出码 7），一个字节都不写。

    为什么不能只信 manifest.src：2026-11-11 的包是在活卡根 build 的，
    manifest.src 就是活卡根，identity 自然也一致 —— 只比 instance_id 拦不住。
    所以「隐式 manifest 指向当前代码根」必须单独拒绝；活卡在跑时更是一律拒绝。
    """
    explicit = str(getattr(args, "source_dir", "") or "").strip()
    raw = man.get("src")
    out = {"ok": False, "dir": "", "from": "", "reason": "",
           "instance_id": "", "identity_id": "", "live_busy": False}

    if explicit:
        src = os.path.abspath(explicit)
        out["from"] = "--source-dir"
    elif raw:
        if not os.path.isabs(str(raw)):
            out["reason"] = (
                "包内 manifest.src 是相对路径（%r），会按当前工作目录解析 —— "
                "这正是 2026-11-11 活卡被误退休的根因；拒绝，请用 --source-dir 显式指定"
                % raw)
            return out
        src = os.path.abspath(str(raw))
        out["from"] = "manifest.src"
    else:
        out.update(ok=True, reason="清单里没有源目录（老包），本次不退休任何目录")
        return out

    out["dir"] = src
    if not os.path.isdir(src):
        out["reason"] = "退休目标不是本机上的目录：%s" % src
        return out

    bundle = os.path.abspath(bundle)
    if os.path.normcase(os.path.realpath(src)) == os.path.normcase(os.path.realpath(bundle)):
        out["reason"] = "退休目标就是目标包目录本身（%s）" % src
        return out
    if _path_inside(bundle, src) or _path_inside(src, bundle):
        out["reason"] = "退休目标和包目录互相包含，拒绝：%s ↔ %s" % (src, bundle)
        return out

    sdb = os.path.join(src, "card.db")
    if not os.path.isfile(sdb):
        out["reason"] = "退休目标里没有 card.db，无法确认它是刚被迁走的那张卡：%s" % src
        return out
    try:
        ident = _disks.manifest_identity(sdb, src)
    except Exception as e:  # noqa: BLE001
        out["reason"] = "读源卡身份失败（%s）：%s" % (src, e)
        return out
    got = str(ident.get("instance_id") or "")
    want = str(man.get("instance_id") or "")
    out["instance_id"] = got
    out["identity_id"] = str(ident.get("identity_id") or "")
    if not want:
        out["reason"] = "包里没有 instance_id，无法校验源卡身份"
        return out
    if not got:
        out["reason"] = "源卡读不出 instance_id，无法确认它就是这个包的源"
        return out
    if got != want:
        out["reason"] = ("源卡 instance_id=%s 与包内 instance_id=%s 不一致 —— "
                         "它不是这个包的源，拒绝退休" % (got, want))
        return out
    wid = str(man.get("identity_id") or "")
    if wid and out["identity_id"] and wid != out["identity_id"]:
        out["reason"] = ("源卡 identity_id=%s 与包内 identity_id=%s 不一致，拒绝退休"
                         % (out["identity_id"], wid))
        return out

    live_busy, _ = _service_running()
    lease_busy = False
    try:
        lease_busy = bool(_disks.lease_state(ROOT).get("alive"))
    except Exception:
        lease_busy = False
    out["live_busy"] = bool(live_busy or lease_busy)
    if os.path.normcase(os.path.realpath(src)) == os.path.normcase(os.path.realpath(ROOT)):
        if live_busy or lease_busy:
            out["reason"] = (
                "退休目标就是当前活卡根（%s），8848 还在服务或写者租约仍在 —— "
                "这正是 2026-11-11 误盖「已迁出」章的现场；拒绝" % ROOT)
            return out
        if out["from"] != "--source-dir":
            out["reason"] = (
                "包内 manifest.src 自动指向当前代码根（%s），服务虽已停也不替它盖章；"
                "真要把这张卡搬走，请停服务后用 --source-dir 显式指定" % ROOT)
            return out
        out["reason"] = "显式指定当前代码根且服务已停：允许退休（请确认这是最后一次用本机）"
    else:
        out["reason"] = "源卡身份与包内一致：instance_id=%s" % got
    out["ok"] = True
    return out

def cmd_land(args):
    """在"新机器"上落地：把包里带的锚写回**新路径**的名字，改写卡槽状态。

    为什么必须做这一步：卡外锚的文件名 = sha256(卡绝对路径)[:16]。
    路径一变，文件名就变；不改名写回，新机器会认为"没有卡外锚"，
    然后按卡内容重建一份 —— 于是"锚 ok"，但其实丢了"这卡是从哪来的"这层证据。

    P0-3：先写 TAKEOVER-IN-PROGRESS.json，改动前把每个目标文件备份成
    *.bak-takeover-<ts>；中途失败按备份还原，已写的 IN-PROGRESS 保留（不清尸灭迹）。

    P1：这里也是「迁移」和「分叉」**分开**的地方 ——
      --migrate  同一张卡换地方：写完事件后把源盘标成已迁出（再启动就只读）；
      --fork     从这张卡分出另一张：源盘一根毛都不动，新卡拿新身份。
    """
    # 模式先定，再碰任何文件：参数不对时连 bundle 存不存在都不用管。
    mode, bad = _land_mode(args)
    if bad is not None:
        print(json.dumps(bad, ensure_ascii=False, indent=1))
        return 2

    if getattr(args, "source_dir", None) and mode != _mig.MODE_MIGRATE:
        print(json.dumps({
            "ok": False,
            "why": "--source-dir 只跟 --migrate 一起用；--fork 不能退休源盘",
        }, ensure_ascii=False, indent=1))
        return 2

    bundle = os.path.abspath(args.bundle)
    mpath = os.path.join(bundle, MANIFEST)
    if not os.path.exists(mpath):
        print(json.dumps({"ok": False, "why": "没有 %s，不是便携包" % MANIFEST},
                         ensure_ascii=False, indent=1))
        return 1
    leftovers = _scan_takeover(bundle)
    if leftovers and not getattr(args, "force", False):
        print(json.dumps({
            "ok": False,
            "why": "这里还有上一次接手留下的 %s —— 不确定它当时写到了哪一步，先别接着跑"
                   % TAKEOVER_IN,
            "leftovers": leftovers,
            "how": "先看 TAKEOVER-IN-PROGRESS.json 和 *.bak-takeover-*；"
                   "确认要重跑就加 --force（会先备份当前状态再接管）",
        }, ensure_ascii=False, indent=1))
        return 2

    man = json.load(open(mpath, encoding="utf-8"))
    new_db = os.path.join(bundle, "card.db")
    if not os.path.exists(new_db):
        print(json.dumps({"ok": False, "why": "包里没有 card.db"}, ensure_ascii=False))
        return 1

    # 落地前先验包：签名对不上 / 文件被改 / 清单自相矛盾，一律不接管。
    # 为什么必须放在碰任何文件之前：land 会就地改写 manifest 和锚，
    # 一个被改过的包一旦开始接管，事后连「原始长什么样」都说不清。
    _pre = _disks.verify_bundle(bundle)
    if not _pre.get("ok"):
        print(json.dumps({
            "ok": False,
            "why": "这个包没通过落地前验收，不接管",
            "problems": _pre.get("problems"),
            "warnings": _pre.get("warnings"),
            "how": "先跑 verify <包> --deep 看是哪一条；改动过的包请重新 build",
        }, ensure_ascii=False, indent=1))
        return 5

    anchor_home = args.anchor_home or os.path.join(os.path.expanduser("~"), ".dsh", "anchors")
    h = hashlib.sha256(os.path.abspath(new_db).encode("utf-8")).hexdigest()[:16]
    dst_ext = os.path.join(anchor_home, "%s.json" % h)

    retire = {"ok": True, "dir": "", "from": "", "reason": "",
              "instance_id": "", "identity_id": "", "live_busy": False}
    if mode == _mig.MODE_MIGRATE:
        retire = _resolve_retire_source(args, man, bundle)
        src_dir = retire["dir"]
    else:
        src_dir = (os.path.abspath(str(man.get("src")))
                   if man.get("src") and os.path.isabs(str(man.get("src"))) else "")
    report = {"bundle": bundle, "new_db": new_db, "anchor_target": dst_ext,
              "did": [], "mode": mode, "retire_check": retire}


    if args.dry:
        # 彩排：只读，不建目录、不写标记、不碰备份。
        src_ext = os.path.join(bundle, "anchor.external.json")
        if os.path.exists(src_ext):
            report["did"].append("（彩排）会把卡外锚写到 %s" % dst_ext)
        else:
            report["warn"] = "包里没有 anchor.external.json（旧包或导出时没有卡外锚）"
        if os.path.exists(os.path.join(bundle, "anchor.json")):
            report["did"].append("（彩排）会改写卡内 anchor.json 的 db 字段")
        if os.path.exists(os.path.join(bundle, "cardslot.state.json")):
            report["did"].append("（彩排）会改写 cardslot.state.json 的 dsh_home")
        report["leftovers"] = leftovers
        ident_kind = (_mig.IDENT_TRANSFERRED if mode == _mig.MODE_MIGRATE
                      else _mig.IDENT_FORKED)
        report["plan"] = {
            "mode": mode,
            "events": [_mig.KIND_STARTED, _mig.KIND_COMPLETED, ident_kind],
            "source_retire": mode == _mig.MODE_MIGRATE,
            "identity": ("同一张卡：identity_id 不变" if mode == _mig.MODE_MIGRATE
                         else "新身份：写 IDENTITY-FORK.json，新卡不是同一个我"),
            "source_dir": src_dir or None,
            "source_reachable": bool(src_dir and os.path.isdir(src_dir)
                                     and src_dir != bundle),
            "retire_check": retire,
        }
        report["next"] = ["去掉 --dry 才真落地",
                          "落地失败会自动回滚，状态留在 %s" % TAKEOVER_IN]
        return 0

    if mode == _mig.MODE_MIGRATE and not retire.get("ok"):
        print(json.dumps({
            "ok": False,
            "why": "拒绝退休源目录：%s" % retire.get("reason"),
            "refused": True,
            "exit": RETIRE_REFUSED,
            "mode": mode,
            "bundle": bundle,
            "retire_target": retire,
            "how": ("退休目标只能是：①--source-dir 显式给的目录；"
                    "②包里 manifest.src（必须是绝对路径）。写之前会比对 instance_id，"
                    "并要求目录不是包本身、不是正在运行的活卡。改好后重跑；"
                    "本次一个字节都没写。"),
        }, ensure_ascii=False, indent=1))
        return RETIRE_REFUSED

    os.makedirs(anchor_home, exist_ok=True)
    stamp = _tag()
    suffix = TAKEOVER_BAK + stamp
    watch = [dst_ext,
             os.path.join(bundle, "anchor.json"),
             os.path.join(bundle, "cardslot.state.json"),
             os.path.join(bundle, "DISK-ROLE.json"),
             mpath]
    # 退休标记 / 分叉凭证也是「这次新建出来的东西」：进了 watch，失败时才会被清掉。
    if mode == _mig.MODE_MIGRATE and retire.get("ok") and src_dir and src_dir != bundle \
            and os.path.isdir(src_dir):
        watch += [os.path.join(src_dir, "RETIRED.json"),
                  os.path.join(src_dir, "SOURCE-RETIRED.json")]
    if mode == _mig.MODE_FORK:
        watch += [os.path.join(bundle, "IDENTITY-FORK.json"),
                  os.path.join(bundle, "identity.json"),
                  os.path.join(bundle, "SOURCE-RETIRED.json")]
    backups, missing = _backup_for_takeover(watch, suffix)
    journal = {
        "state": "in-progress",
        "ts": time.time(),
        "stamp": stamp,
        "pid": os.getpid(),
        "bundle": bundle,
        "new_db": new_db,
        "mode": mode,
        "identity_event": (_mig.IDENT_TRANSFERRED if mode == _mig.MODE_MIGRATE
                           else _mig.IDENT_FORKED),
        "source": man.get("src"),
        "source_dir": src_dir or None,
        "source_manifest": mpath,
        "manifest_sha256": _sha256(mpath),
        "head_seq": man.get("head_seq"),
        "head_hash": man.get("head_hash"),
        "backup_suffix": suffix,
        "backed_up": [b["backup"] for b in backups],
        "created_if_absent": missing,
        "started_at_iso": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _atomic_write_json(os.path.join(bundle, TAKEOVER_IN), journal)
    mig = {"mode": mode, "started_seq": None, "completed_seq": None,
           "identity_seq": None, "chain_ok": None}
    src_retire = {"enforced": False, "retired": False, "note": ""}
    ident = {}
    try:
        # ① 先在账本上写「开始了」。allow_foreign：链头多半还是**旧机器**写的，
        #    这是整套流程里唯一该放行的一条；之后链头换成本机，默认就锁上了。
        started = _land_event(bundle, _mig.KIND_STARTED, mode, allow_foreign=True,
                              source=man.get("src") or "",
                              source_dir=src_dir or None,
                              dest=bundle,
                              manifest_sha256=journal["manifest_sha256"],
                              head_seq=man.get("head_seq"),
                              head_hash=man.get("head_hash"),
                              writer_epoch=man.get("writer_epoch"))
        mig["started_seq"] = started["seq"]

        _land_apply(bundle, man, args, report)

        # ② 两种模式从这里分开 —— 搬家会锁源盘，分叉一根毛都不碰源盘
        if mode == _mig.MODE_MIGRATE:
            if not src_dir:
                src_retire = {"enforced": False, "retired": False, "refused": False,
                              "note": "清单里没有源目录（老包），本次不退休任何目录"}
            else:
                src_retire = _mig.retire_source(
                    src_dir, bundle, identity_id=str(man.get("identity_id") or ""),
                    why="migrate",
                    expected_instance=str(man.get("instance_id") or ""),
                    live_root=ROOT, live_busy=bool(retire.get("live_busy")))
            if src_retire.get("enforced"):
                report["did"].append("源盘已标成「已迁出」：%s（它再启动就是只读）"
                                     % src_dir)
            elif src_retire.get("retired"):
                report["did"].append("源盘已标成「已迁出」（RETIRED.json）")
            else:
                # 源盘不在手边：凭证留在新包里，但别假装退休成功了
                _atomic_write_json(os.path.join(bundle, "SOURCE-RETIRED.json"), {
                    "schema": _mig.SCHEMA, "mode": mode, "retired": True,
                    "enforced": False, "source_dir": src_dir or None,
                    "dest": bundle,
                    "identity_id": man.get("identity_id") or "",
                    "at": time.time(),
                    "note": "源盘没能就地退休：%s" % (src_retire.get("note") or "原因不明"),
                })
                report.setdefault("warnings", []).append(
                    "源盘没退休（%s）。要让原处只读，得在那台机器上写 RETIRED.json："
                    "控制台「带卡出门/退役」或 portable.py handover"
                    % (src_retire.get("note") or "原因不明"))
        else:
            parent = _mig._card_identity(new_db, bundle)
            ident = _mig.fork_identity(bundle, new_db, parent["identity_id"],
                                       parent["instance_id"])
            for w in ident.get("warnings") or []:
                report.setdefault("warnings", []).append(w)
            report["identity"] = {
                "mode": mode,
                "new_identity_id": ident["new_identity_id"],
                "parent_identity_id": ident["parent_identity_id"],
                "parent_instance_id": ident["parent_instance_id"],
                "message": "这是一张新卡，不是同一个我",
            }
            report["did"].append("新身份 %s（从 %s 分出来；源卡没动）"
                                 % (ident["new_identity_id"],
                                    ident["parent_identity_id"]
                                    or ident["parent_instance_id"] or "?"))

        # ③ 落 DONE：模式 + 身份事件都写进去，事后一眼看出这是搬家还是分叉
        _atomic_write_json(os.path.join(bundle, TAKEOVER_DONE), {
            "state": "done",
            "ts": journal["ts"],
            "stamp": stamp,
            "pid": os.getpid(),
            "mode": mode,
            "identity_event": journal["identity_event"],
            "source_retired": bool(src_retire.get("retired")),
            "identity": report.get("identity"),
            "manifest_sha256": journal["manifest_sha256"],
            "did": report["did"],
            "finished_at_iso": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
    except _mig.ChainBroken as e:
        # 账本写不进去（或写完发现链断）：接管不做，按备份还原，退出码 6。
        failures = _restore_takeover(backups, missing)
        print(json.dumps({
            "ok": False,
            "why": "时间线账本没写进去，接管中止：%s" % e,
            "chain": e.as_dict(),
            "mode": mode,
            "rolled_back": not failures,
            "rollback_failures": failures[:10],
            "in_progress": os.path.join(bundle, TAKEOVER_IN),
            "source_untouched": True,
            "next": "时间线的哈希链断了，需要人处理：先跑 "
                    "python -c \"import migration;print(migration.check_chain(r'%s'))\" "
                    "看断在哪，别硬接管" % os.path.join(bundle, "card.db"),
        }, ensure_ascii=False, indent=1))
        return 6
    except Exception as e:
        failures = _restore_takeover(backups, missing)
        rolled = not failures
        msg = ("已回滚，源盘未受影响" if rolled
               else "回滚失败，源盘未受影响（可以按 *.bak-takeover-* 手动还原）")
        # 失败也是一次迁移：带原因记进账本。账本自己也写不进去就只报告，不递归。
        fail = {"ok": False, "seq": None}
        try:
            fail = _land_event(bundle, _mig.KIND_FAILED, mode, allow_foreign=True,
                               reason="%s: %s" % (type(e).__name__, str(e)[:300]),
                               source=man.get("src") or "",
                               source_dir=src_dir or None,
                               dest=bundle,
                               manifest_sha256=journal["manifest_sha256"],
                               applied=list(report.get("did") or []),
                               started_seq=mig.get("started_seq"),
                               identity_started=bool(ident))
            # 事件改了库，清单不能继续指着旧字节，否则 verify --deep 会喊被篡改
            _disks._refresh_manifest_hash(bundle, ["card.db", "identity.json"])
        except Exception as e2:
            fail = {"ok": False, "why": "%s: %s" % (type(e2).__name__, str(e2)[:300])}
            if isinstance(e2, _mig.ChainBroken):
                report["chain_broken"] = e2.as_dict()
        print(json.dumps({
            "ok": False,
            "why": "接手失败：%s" % e,
            "mode": mode,
            "rolled_back": rolled,
            "message": msg,
            "rollback_failures": failures[:10],
            "in_progress": os.path.join(bundle, TAKEOVER_IN),
            "in_progress_kept": os.path.exists(os.path.join(bundle, TAKEOVER_IN)),
            "source_card_readable": os.path.exists(new_db),
            "applied_before_failure": report.get("did"),
            "timeline": {"started_seq": mig.get("started_seq"),
                         "failed_seq": fail.get("seq"),
                         "chain_ok": fail.get("chain_ok")},
            "chain_broken": report.get("chain_broken"),
            "backups": [b["backup"] for b in backups],
            "next": ("先看 IN-PROGRESS、账本里的 migration.failed 和 .bak-takeover；"
                     "修好后加 --force 重跑，或按备份手动恢复"),
        }, ensure_ascii=False, indent=1))
        return 5

    # 接管完成 = 换了一任写者：纪元必须 +1（P0-4 的 B 口）。
    # 不 +1 的话，旧机器上还开着的进程会拿着同一个纪元继续写「同一张卡」。
    # bump 失败不回滚已经完成的接管（回滚会把新盘改回去），只在报告里留 warning。
    epoch_id = ident.get("new_identity_id") or str(man.get("identity_id") or "")
    _epoch = man.get("writer_epoch")
    try:
        _epoch = _disks.bump_writer_epoch(bundle, identity_id=epoch_id)
        man["writer_epoch"] = _epoch
        # 改完 body 必须重签；否则 sign 还是 bump 之前那一版的，verify 会喊被篡改。
        try:
            import seal as _seal
            _key = _seal.load_key(bundle, create=False)
            if _key:
                man["sig"] = _seal.sign(
                    {k: v for k, v in man.items() if k != "sig"}, _key)
        except Exception:
            _key = None
        _atomic_write_json(mpath, man)
        report["writer_epoch"] = _epoch
        report["did"].append("写者纪元 +1 → %d（接管后换任）" % _epoch)
    except Exception as e:
        report["warning"] = "接管已完成，但写者纪元 bump 失败：%s" % e

    # 账本：完成 + 身份事件。先取一次链头 —— completed 记的是「完成时」的链头。
    try:
        head_before = _mig.check_chain(new_db)
        done_ev = _land_event(bundle, _mig.KIND_COMPLETED, mode,
                              head_seq=head_before.get("head_seq"),
                              head_hash=head_before.get("head_hash"),
                              writer_epoch=_epoch,
                              source=man.get("src") or "",
                              dest=bundle,
                              source_retired=bool(src_retire.get("retired")))
        mig["completed_seq"] = done_ev["seq"]

        if mode == _mig.MODE_MIGRATE:
            ident_ev = _land_event(
                bundle, _mig.IDENT_TRANSFERRED, mode,
                identity_id=str(man.get("identity_id") or ""),
                instance_id=str(man.get("instance_id") or ""),
                source=man.get("src") or "", dest=bundle,
                source_retired=bool(src_retire.get("retired")))
        else:
            ident_ev = _land_event(
                bundle, _mig.IDENT_FORKED, mode,
                parent_identity_id=ident.get("parent_identity_id") or "",
                parent_instance_id=ident.get("parent_instance_id") or "",
                new_identity_id=ident.get("new_identity_id") or "",
                source=man.get("src") or "", dest=bundle,
                source_retired=False)
        mig["identity_seq"] = ident_ev["seq"]

        # 每次 append 都会把锚整条重建（landed_from 被抹掉）→ 补写一次
        _resync_anchor(bundle, new_db, dst_ext, man.get("src") or "", report)
        _disks._refresh_manifest_hash(bundle, ["card.db", "anchor.json",
                                               "identity.json"])
        _after = _mig.check_chain(new_db)
        mig["chain_ok"] = bool(_after["ok"])
        mig["head_seq_after"] = _after.get("head_seq")
    except _mig.ChainBroken as e:
        # 物理接管已经完成，不撤；但账本断了必须让人看见。
        mig["chain_ok"] = False
        report["chain_broken"] = e.as_dict()
        report.setdefault("warnings", []).append(
            "物理接管已完成，但时间线账本写不进去 / 链断了：%s" % e)

    # 一次性把 DONE 补全（模式、身份事件、写者纪元、账本 seq 都在这里）
    done_path = os.path.join(bundle, TAKEOVER_DONE)
    try:
        with open(done_path, encoding="utf-8") as f:
            done_doc = json.load(f)
    except Exception:
        done_doc = {"state": "done"}
    done_doc.update({
        "writer_epoch": _epoch,
        "bumped_at_iso": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": mode,
        "identity_event": journal["identity_event"],
        "source_retired": bool(src_retire.get("retired")),
        "identity": report.get("identity"),
        "timeline": {"mode": mode,
                     "started_seq": mig.get("started_seq"),
                     "completed_seq": mig.get("completed_seq"),
                     "identity_seq": mig.get("identity_seq"),
                     "head_seq_after": mig.get("head_seq_after"),
                     "chain_ok": mig.get("chain_ok")},
    })
    _atomic_write_json(done_path, done_doc)

    marker = os.path.join(bundle, TAKEOVER_IN)
    try:
        os.remove(marker)
        removed = True
    except OSError as e:
        removed = False
        report["warning"] = "接管成功，但 IN-PROGRESS 没删掉（%s）；下次跑会要求 --force" % e

    if not getattr(args, "keep_temp", False):
        for b in backups:
            try:
                os.remove(b["backup"])
            except OSError:
                pass

    report["next"] = [
        "验证一遍：python tools/portable.py verify \"%s\"" % bundle,
        "起服务：python bridge.py health（会自己拉起 mind_server）",
        "注入人格：python tools/portable.py inject  → 之后开 DSH 新会话，她就在卡里说话",
        "注意：新机器要先有 Python + numpy/psutil（图片还要 Pillow）",
    ]
    if mode == _mig.MODE_MIGRATE:
        report["next"].insert(0, "源盘 %s 已标成「已迁出」：别再从那台机器上写这张卡"
                              % (src_dir or "（清单没记源目录）"))
    else:
        report["next"].insert(0, "这是一张新卡，不是同一个我：新身份 %s；源卡照旧能写，"
                                 "两边从此各写各的链" % ident.get("new_identity_id"))
    report["takeover"] = {
        "done": os.path.join(bundle, TAKEOVER_DONE),
        "in_progress_removed": removed,
        "backups_kept": bool(getattr(args, "keep_temp", False)),
        "mode": mode,
        "identity_event": journal["identity_event"],
        "source_retired": bool(src_retire.get("retired")),
    }
    if mode == _mig.MODE_FORK:
        # 显式说一次：这不是「同一个我换了个地方」
        report["message"] = "这是一张新卡，不是同一个我"
        report["identity"] = report.get("identity") or {}
        report["identity"]["message"] = "这是一张新卡，不是同一个我"
    if leftovers:
        report["overrode_leftover"] = leftovers
    if mig.get("chain_ok") is False:
        # 接管做完了，账本却断了 —— 不装作成功，让人来处理
        report["ok"] = False
        report["why"] = "接管已完成，但时间线账本断了，需要人处理"
        report["takeover_done"] = True
        report["next"].insert(0, "时间线链断了：先跑 python -c "
                                 "\"import migration;print(migration.check_chain(r'%s'))\""
                              % os.path.join(bundle, "card.db"))
        print(json.dumps({"ok": False, "dry_run": False, **report},
                         ensure_ascii=False, indent=1))
        return 7
    print(json.dumps({"ok": True, "dry_run": False, **report},
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
    p2.add_argument("--keep-temp", action="store_true",
                    help="失败/成功后保留 .partial-* 临时目录（测试用；默认清理）")
    p2.set_defaults(fn=cmd_build)
    p3 = sub.add_parser("verify"); p3.add_argument("bundle")
    p3.add_argument("--deep", action="store_true",
                    help="深度验收：库完整性 / 外键 / 时间线链 / 锚 / 盘角色（只读）")
    p3.set_defaults(fn=cmd_verify)
    pl = sub.add_parser("land", help="新机器落地：写回卡外锚 + 改写卡槽状态")
    pl.add_argument("bundle")
    pl.add_argument("--anchor-home", default=None, help="锚目录（默认 ~/.dsh/anchors）")
    pl.add_argument("--dsh-home", default=None, help="DSH 家目录（默认 ~/.dsh）")
    pl.add_argument("--dry", action="store_true")
    pl.add_argument("--force", action="store_true",
                    help="有遗留的 TAKEOVER-IN-PROGRESS.json 也继续（先确认状态）")
    pl.add_argument("--keep-temp", action="store_true",
                    help="成功后保留 *.bak-takeover-* 备份（测试用；默认清理）")
    # P1：搬家还是分叉，必须明说（两个都不给 / 同时给 → 退出码 2）
    pl.add_argument("--migrate", action="store_true",
                    help="同一张卡换地方：落地后把源盘标成已迁出（再启动只读）")
    pl.add_argument("--fork", action="store_true",
                    help="从这张卡分出另一张：新身份，源盘不动（不是同一个我）")
    pl.add_argument("--source-dir", default=None, metavar="DIR",
                    help="--migrate 要退休的源卡目录（不填则只认包内绝对路径 src；"
                         "写前会比对 instance_id）")
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
