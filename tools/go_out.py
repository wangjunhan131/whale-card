# -*- coding: utf-8 -*-
"""带卡出门：把活卡整棵镜像到「随身盘」，然后把本机这份退役成只读。

流程（每一步都能重复跑，失败就停在这一步）：
    1. 找到随身盘（默认扫 D:..Z: 里带 PORTABLE-MANIFEST.json + card.db 的盘）
    2. 停服务（POST /shutdown）—— 不停机就镜像，拿到的是半截 WAL
    3. WAL checkpoint(TRUNCATE) —— 让 card.db 变成单文件、没有旁挂日志
    4. 逐个文件比对 sha256 拷到随身盘（只拷 portable 认定的「跟卡走的东西」）
    5. card.db 用 sqlite backup API 写成一致性快照
    6. 刷新清单 sha + 用随身盘内的 anchor.key 重签 + verify
    7. 本机退役（只读）—— 从此这张卡只认随身盘那份能写

用法：
    python tools\\go_out.py --target H:\\            # 真出门
    python tools\\go_out.py --target H:\\ --dry      # 只彩排（不复制、不停机、不退役）
    python tools\\go_out.py --target H:\\ --no-retire# 镜像完但不退役本机（调试用）
"""
import argparse
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)
BAR = "─" * 62
PORT = int(os.environ.get("WHALE_CARD_PORT") or 8848)


def say(msg=""):
    print(msg, flush=True)


def section(n, title):
    say()
    say(BAR)
    say("  %d. %s" % (n, title))
    say(BAR)


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def http(path, body=None, timeout=20):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (PORT, path), data=data,
        headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except Exception as e:
        return {"ok": False, "why": str(e)}


def health():
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/health" % PORT, timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def find_target():
    """找随身盘：根上有 PORTABLE-MANIFEST.json 且 card.db 的盘，且不是本卡目录。"""
    me = os.path.abspath(ROOT).rstrip("\\").lower()
    cands = []
    for d in "DEFGHIJKLMNOPQRSTUVWXYZ":
        root = d + ":\\"
        if not os.path.isdir(root):
            continue
        if os.path.abspath(root).rstrip("\\").lower() == me[:2]:
            continue
        if os.path.exists(os.path.join(root, "PORTABLE-MANIFEST.json")) and \
                os.path.exists(os.path.join(root, "card.db")):
            cands.append(root)
    return cands


def step_shutdown(dry):
    h = health()
    if not h:
        say("  服务没在跑，跳过停机")
        return True
    say("  服务在跑（mem=%s），发 /shutdown" % h.get("memories"))
    if dry:
        say("  （彩排）不真停")
        return True
    r = http("/shutdown", {}, timeout=15)
    say("  /shutdown → %s" % r)
    for _ in range(30):
        time.sleep(1)
        if not health():
            say("  已停")
            return True
    say("  ! 15 秒内没停掉，继续镜像有拿到半截 WAL 的风险（Ctrl-C 可以中止）")
    return True


def step_checkpoint(dry):
    p = os.path.join(ROOT, "card.db")
    if dry:
        say("  （彩排）跳过 checkpoint")
        return True
    try:
        c = sqlite3.connect(p, timeout=30.0)
        rows = c.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        c.close()
        say("  checkpoint(TRUNCATE) → %s" % (rows,))
        return True
    except Exception as e:
        say("  ! checkpoint 失败：%s" % e)
        return True


def step_mirror(target, dry):
    """镜像整张卡到 target。

    2026-11-13 起这份逻辑只有一处：tools/carry.py（控制台按钮也走它）。
    这里只负责把日志讲人话。
    """
    import carry

    if dry:
        import portable
        n = len(list(portable._iter_sources(ROOT)))
        say("  （彩排）会比对 %d 个文件并覆盖有差异的；不会写盘" % n)
        return True

    out = carry.carry(ROOT, target, log=say)
    say("  校验：%s" % ("通过" if out.get("verify_ok") else "没过 —— 先别拔盘"))
    if not out.get("ok"):
        say("  ! %s" % (out.get("why") or out.get("code")))
        return False
    return True


def step_retire(dry):
    import disks
    say("  本机退役（之后本机只读，只有随身盘那份能写）")
    if dry:
        say("  （彩排）不退役")
        return True
    disks.set_retired(ROOT, True, "带卡出门：主卡随盘带走 @ %s"
                      % time.strftime("%Y-%m-%d %H:%M"))
    say("  ✓ 已退役")
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description="带卡出门：镜像到随身盘 + 本机退役")
    ap.add_argument("--target", help="随身盘根目录，如 H:\\")
    ap.add_argument("--dry", action="store_true", help="彩排：不复制、不停机、不退役")
    ap.add_argument("--no-retire", action="store_true", help="镜像完不退役本机")
    a = ap.parse_args(argv)

    say()
    say("  数字生命卡 · 带卡出门")
    say("  本机：%s" % ROOT)

    section(1, "找随身盘")
    target = a.target
    if not target:
        cands = find_target()
        say("  候选：%s" % (cands or "没有找到"))
        if len(cands) == 1:
            target = cands[0]
        else:
            say("  ✗ 没法定目标，用 --target H:\\ 指定")
            return 2
    target = os.path.abspath(target).rstrip("\\") + "\\"
    say("  目标：%s" % target)
    if not os.path.isdir(target):
        say("  ✗ 目标盘不存在")
        return 2

    section(2, "停服务")
    step_shutdown(a.dry)

    section(3, "落盘")
    step_checkpoint(a.dry)

    section(4, "镜像")
    ok = step_mirror(target, a.dry)

    if not a.no_retire:
        section(5, "退役本机")
        step_retire(a.dry)

    say()
    say(BAR)
    say("  %s" % ("镜像完成。" if ok else "镜像有失败项，先看上面。"))
    say("  下一步：拔盘带走；到新电脑双击「一键部署」。")
    say("  回到这台电脑时双击「接回来」把它接回主位。")
    say(BAR)
    return 0 if ok else 4


if __name__ == "__main__":
    sys.exit(main())
