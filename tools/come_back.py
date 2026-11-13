# -*- coding: utf-8 -*-
"""把 U 盘那份卡「接回来」到这台电脑。

场景：她带着 U 盘去别的电脑活了一段（那边写了新记忆），现在 U 盘插回来，
      本机这份是旧的、而且是只读（带卡出门留下的退役标记）。
      这时候**不能**各写各的 —— 必须把新那份搬回来，本机转成唯一能写的那份。

做法（每一步都可重复跑）：
  1. 停掉本机正在跑的服务（不然搬完文件它还在写）
  2. 把本机旧卡备份成 card.db.bak-before-comeback-<时间戳>
  3. 用 sqlite backup API 把 U 盘那份**一致性快照**写回本机（不是直接拷文件，
     直接拷可能拿到半截 WAL）
  4. 清掉本机的退役标记（它现在重新上岗了）
  5. 给 U 盘那份打上退役（它现在退场，只读）
  6. 重启服务，真写一句话验证能写

安全阀：
  · U 盘那份比本机旧？只提示，不硬拦（有时人就是要回退），但会写清楚。
  · 两份链头哈希对不上 → 说明真的分叉了，**停下来等你决定**，不自动选一份覆盖另一份。
"""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request

# 测试钩子：允许把「本机卡目录」指到别处（默认就是本文件所在的卡目录）
ROOT = os.environ.get("WHALE_CARD_ROOT") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))
# 代码目录（本文件在 <卡>/tools/ 里）和数据目录分开：
# 测试时 WHALE_CARD_ROOT 指向一个只放 card.db 的假卡目录，那里没有 disks.py。
CODE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, CODE_ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

BAR = "─" * 62
PORT = int(os.environ.get("WHALE_CARD_PORT") or 8848)


def say(msg=""):
    print(msg)


def section(n, title):
    print()
    print(BAR)
    print("  %d. %s" % (n, title))
    print(BAR)


def http(path, body=None, timeout=20, method=None):
    url = "http://127.0.0.1:%d%s" % (PORT, path)
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method=method or ("POST" if data else "GET"))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def service_up(timeout=2.0):
    try:
        http("/health", timeout=timeout)
        return True
    except Exception:
        return False


def stop_service():
    """让本机服务退场。先好好说，不听话再杀。"""
    if not service_up():
        return "本机服务本来就没在跑"
    try:
        http("/shutdown", {}, timeout=10)
    except Exception:
        pass
    for _ in range(30):
        time.sleep(0.5)
        if not service_up(1.0):
            return "已让本机服务退出"
    # 还没死就找进程杀掉
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object { $_.CommandLine -like '*mind_server.py*' } | "
             "ForEach-Object { $_.ProcessId }"],
            capture_output=True, text=True, timeout=30).stdout.split()
        for pid in out:
            subprocess.run(["taskkill", "/f", "/pid", pid],
                           capture_output=True, timeout=15)
        time.sleep(1.5)
        return "服务没响应退出，已强杀 pid=%s" % (",".join(out) or "无")
    except Exception as e:
        return "停服务失败：%s" % e


def db_info(db):
    """读一份卡的概况：条数、最大 seq、链头哈希、最后写者。"""
    c = sqlite3.connect("file:" + db.replace("\\", "/") + "?mode=ro",
                        uri=True, timeout=30)
    c.row_factory = sqlite3.Row
    try:
        n = c.execute("SELECT count(*) AS n FROM mem").fetchone()["n"]
        row = c.execute("SELECT seq, self_hash, writer FROM timeline "
                        "ORDER BY seq DESC LIMIT 1").fetchone()
        return {
            "mem": n,
            "head_seq": row["seq"] if row else 0,
            "head_hash": row["self_hash"] if row else "",
            "head_writer": row["writer"] if row else "",
            "mtime": os.path.getmtime(db),
        }
    finally:
        c.close()


def snapshot_into(src_db, dst_db):
    """从 src 做一致性快照写到 dst（sqlite backup API）。"""
    for ext in ("-wal", "-shm"):
        p = dst_db + ext
        if os.path.exists(p):
            os.remove(p)
    s = sqlite3.connect("file:" + src_db.replace("\\", "/") + "?mode=ro",
                        uri=True, timeout=60)
    d = sqlite3.connect(dst_db, timeout=60)
    try:
        with d:
            s.backup(d)
    finally:
        d.close()
        s.close()


def _card_candidates():
    """可能放着「另一份卡」的地方，全按运行时环境算，不写死盘符。

    顺序：环境变量 WHALE_CARD_ROOTS（分号分隔）→ 各盘常见目录名 → 各盘一层子目录。
    只保留「目录里真有 card.db」的，并且踢掉本机这一份（不然自己搬自己）。
    """
    out = []
    for p in os.environ.get("WHALE_CARD_ROOTS", "").replace(os.pathsep, ";").split(";"):
        p = p.strip().strip('"')
        if p:
            out.append(p)
    drives = [chr(c) + ":\\" for c in range(65, 91) if os.path.exists(chr(c) + ":\\")]
    for d in drives:
        for n in ("card-live", "whale-card", "数字生命卡", "whale-card-usb"):
            out.append(os.path.join(d, n))
    for d in drives:
        try:
            subs = sorted(os.listdir(d))
        except OSError:
            # 某个盘不让枚举（比如空的读卡器）——记一笔就跳过，不算失败
            _listdir_err = True
            continue
        for x in subs:
            full = os.path.join(d, x)
            if os.path.isdir(full):
                out.append(full)
    me = os.path.normcase(os.path.abspath(ROOT))
    seen, rank = {me}, {}
    for i, p in enumerate(out):          # 越早出现的越像（常见名在子目录扫描之前）
        q = os.path.normcase(os.path.abspath(p))
        if q in seen:
            continue
        seen.add(q)
        base = os.path.basename(p.rstrip("\\/")).lower()
        if any(w in base for w in ("backup", "snapshot", "bak", "trash", "recycle")):
            continue                      # 备份/快照不是「另一份卡」，别让接回认错人
        if os.path.exists(os.path.join(p, "card.db")):
            rank[p] = i
    return sorted(rank, key=lambda x: rank[x])


def main(argv=None):
    ap = argparse.ArgumentParser(description="把 U 盘那份卡接回本机")
    ap.add_argument("usb", nargs="?", default=None,
                    help="U 盘上那份卡的目录（默认自动找 G:\\\\card-live 之类）")
    ap.add_argument("--dry", action="store_true", help="只看会做什么")
    ap.add_argument("--force", action="store_true",
                    help="两边链头对不上也硬搬（默认停下来问你）")
    ap.add_argument("--no-start", action="store_true",
                    help="搬完不自动起服务（自测用）")
    a = ap.parse_args(argv)

    print()
    print("  数字生命卡 · 接回来")
    print("  本机：%s" % ROOT)

    # ── 找 U 盘那份 ─────────────────────────────
    usb = a.usb
    if not usb:
        cands = _card_candidates()
        if len(cands) > 1:
            print("  看到好几份像卡的：")
            for c in cands:
                print("    " + c)
            print("  先按第一份走；不是它就把路径直接给我。")
        if cands:
            usb = cands[0]
    if not usb or not os.path.exists(os.path.join(usb, "card.db")):
        print()
        print("  ✗ 没找到 U 盘上的卡。插好 U 盘，或者告诉我路径：")
        print("    接回来.bat <那份卡所在的目录>（例如 X:\\card-live）")
        return 1
    usb = os.path.abspath(usb)
    print("  U 盘：%s" % usb)

    mine_db = os.path.join(ROOT, "card.db")
    usb_db = os.path.join(usb, "card.db")

    section(1, "先看一眼两边各是什么")
    m, u = db_info(mine_db), db_info(usb_db)
    print("  本机  %d 条 / 链头 seq %d / %s" % (m["mem"], m["head_seq"], m["head_writer"]))
    print("  U盘   %d 条 / 链头 seq %d / %s" % (u["mem"], u["head_seq"], u["head_writer"]))

    if m["head_hash"] and u["head_hash"] and m["head_hash"] != u["head_hash"]:
        # 同一个 seq 却哈希不同 = 真分叉
        if u["head_seq"] == m["head_seq"]:
            print()
            print("  ✗ 两边链头对不上（同一个 seq 不同哈希）—— 这是真分叉。")
            print("    不由我替你选。先别动，把这段发给我。")
            if not a.force:
                return 2
    if u["head_seq"] < m["head_seq"]:
        print()
        print("  ✗ U 盘那份比本机旧（链头 %d < %d）。" % (u["head_seq"], m["head_seq"]))
        print("    搬过来等于把新的丢掉、换回旧的。我停手了。")
        print("    如果你确实想回退，加 --force 再来一次。")
        if not a.force:
            return 2

    if a.dry:
        print()
        print(BAR)
        print("  （彩排）会做：停服务 → 备份本机 → 用 U 盘快照覆盖本机 →")
        print("        本机清退役 → U 盘打退役 → 重启服务 → 试写一句")
        print(BAR)
        return 0

    section(2, "停本机服务")
    print("  " + stop_service())

    section(3, "备份本机旧卡")
    bak = os.path.join(ROOT, "card.db.bak-before-comeback-%s"
                       % time.strftime("%Y%m%d-%H%M%S"))
    try:
        shutil.copy2(mine_db, bak)
        for ext in ("-wal", "-shm"):
            p = mine_db + ext
            if os.path.exists(p):
                shutil.copy2(p, bak + ext)
        print("  备份好了：%s" % os.path.basename(bak))
    except Exception as e:
        print("  ✗ 备份失败，不敢往下做：%s" % e)
        return 1

    section(4, "把 U 盘那份搬回来（一致性快照，不是直接拷）")
    try:
        snapshot_into(usb_db, mine_db)
        after = db_info(mine_db)
        print("  本机现在是：%d 条 / 链头 seq %d" % (after["mem"], after["head_seq"]))
    except Exception as e:
        print("  ✗ 搬失败：%s" % e)
        print("    本机旧卡还在：%s" % os.path.basename(bak))
        return 1

    section("4.5", "把清单与锚对齐（不然 verify 会说包不一致）")
    _man = os.path.join(ROOT, "PORTABLE-MANIFEST.json")
    if not os.path.exists(_man):
        print("  本机没有随身清单（清单只在带走的盘上），这步不需要做")
    else:
        try:
            import carry as _carry
            info = _carry.refresh_and_sign(ROOT, ROOT, log=lambda m: print("  " + m))
            print("  清单：新增 %d / 刷新 %d"
                  % (len(info.get("added", [])), len(info.get("changed", []))))
        except Exception as e:
            print("  ⚠ 清单对齐失败（不影响数据，但下次 verify 会报）：%s" % e)
    try:
        import anchor as _anchor
        _anchor.write(os.path.join(ROOT, "card.db"), force=True, throttle=0.0)
        print("  锚：按接回来的这份重写（head_seq/count 跟着走）")
    except Exception as e:
        print("  ⚠ 锚没写成功：%s" % e)

    section(5, "换人上岗：本机可写，U 盘只读")
    try:
        import disks as _dk
        if _dk.read_retired(ROOT):
            _dk.set_retired(ROOT, False)
            print("  本机：清掉退役标记 → 重新上岗")
        else:
            print("  本机：本来就没退役")
        _dk.set_retired(usb, True, "接回来了：主盘已回到 %s" % ROOT)
        print("  U 盘：打上退役 → 只读（不会再被误写）")
    except Exception as e:
        print("  ⚠ 改退役标记失败：%s" % e)
        print("    （不影响数据，但记得别在 U 盘上再点写操作）")

    if a.no_start:
        print()
        print(BAR)
        print("  （--no-start）数据已搬好，服务没起。")
        print(BAR)
        return 0

    section(6, "重启服务并试写一句")
    try:
        subprocess.Popen(
            [sys.executable, "-X", "utf8", "bridge.py", "health"],
            cwd=ROOT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:
        print("  ⚠ 拉服务失败：%s" % e)

    h = None
    for _ in range(40):
        time.sleep(1)
        try:
            h = http("/health", timeout=5)
            break
        except Exception:
            pass
    if not h:
        print("  ✗ 服务没起来。手动跑一次：python bridge.py health")
        return 1

    print("  服务起来了：%d 条记忆" % h.get("memories", 0))
    try:
        r = http("/report", {"user_message": "接回来了，我在这儿吗",
                             "reply": "在。U 盘里那段日子我也带回来了。",
                             "source": "comeback"}, timeout=90)
        mid = (r.get("report") or {}).get("mem_id") or r.get("mem_id")
        print("  ✓ 试写成功 mem_id=%s —— 能写，就说明真的接回来了" % mid)
    except Exception as e:
        print("  ✗ 试写失败：%s" % e)
        print("    能读不能写。跑一次 一键落地.bat 补交棒。")
        return 1

    print()
    print(BAR)
    print("  接回来了。本机是唯一能写的那份，U 盘转只读。")
    print("  旧卡备份：%s" % os.path.basename(bak))
    print(BAR)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
