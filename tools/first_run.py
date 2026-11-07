# -*- coding: utf-8 -*-
"""一键落地 —— 卡搬到一个新地方之后，跑这一条就够了。

    runtime\\python\\python.exe tools\\first_run.py

它会按顺序做完六件事，每一步都会自己判断「要不要做」，所以重复跑是安全的：

    1. 体检        看这台机器缺什么（时间/依赖/显卡/卡文件）
    2. 落地        land：把包里的卡外锚按**这台机器的路径**改名写回
    3. 交棒        handover：让这张卡认这台机器能写
                   （不做这一步，卡到了新家是只读的，/report 直接 409）
    4. 起服务      bridge.py 那套，服务没在跑就拉起来
    5. 自检        读 /health，报记忆条数、第几天、链、锚、能不能写
    6. 开控制台    浏览器打开 http://127.0.0.1:8848/console

为什么写成脚本而不是一长串命令：
  搬家最容易出事的不是技术，是手忙脚乱漏一步。漏 land → 锚对不上（看起来"锚 ok"，
  其实是自己跟自己一致）；漏 handover → 卡是只读的；漏对时 → 记忆被冻住或加速遗忘。
  这三件都不报错。所以让机器来记顺序。

退出码：0 = 一切就绪；1 = 有必须解决的问题（看输出最后一节的「必须先解决」）。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _fix_stdout():
    for name in ("stdout", "stderr"):
        s = getattr(sys, name, None)
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_fix_stdout()

BAR = "─" * 62


def _drive_type(path):
    """返回盘类型：2=可移动 3=本地 4=网络 5=光驱 6=内存盘。"""
    try:
        import ctypes
        drive = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
        return ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(drive))
    except Exception:
        return 0


def _dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def pick_local_target(need_bytes):
    """挑一个本地硬盘放卡：空间够、且不是当前这块（U 盘/网络盘）自己。"""
    best = None
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        root = letter + ":\\"
        if not os.path.exists(root):
            continue
        if _drive_type(root) != 3:          # 只要本地固定盘
            continue
        try:
            u = shutil.disk_usage(root)
        except OSError:
            continue
        if u.free < need_bytes + (300 << 20):
            continue
        if best is None or u.free > best[1]:
            best = (root, u.free)
    return best[0] if best else None


def auto_copy_to_local():
    """在 U 盘上双击时，自己把整个卡拷到本地硬盘，再在那边接着跑。

    为什么必须这样：WAL 模式的 SQLite 不适合在 exFAT/可移动盘上跑，
    而且 U 盘速度慢、随时可能被拔。用户要的是「双击就能用」，
    所以「拒绝执行」不如「我帮你搬过去再跑」。
    """
    need = _dir_size(ROOT)
    target = pick_local_target(need)
    if not target:
        print("  ✗ 找不到能放下这张卡的本地硬盘（需要 %.0f MB 空闲）。"
              % (need / 1048576.0))
        print("    手动把整个文件夹拷到某个本地硬盘，再从那里双击。")
        return None

    dst = os.path.join(target, "whale-card-%s" % time.strftime("%Y%m%d-%H%M%S"))

    print("  → 拷到本地硬盘：%s" % dst)
    print("    （%.0f MB，U 盘读得慢，请等一会儿，别拔）" % (need / 1048576.0))
    t0 = time.time()
    try:
        shutil.copytree(ROOT, dst, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("card.db-wal", "card.db-shm"))
    except Exception as e:
        print("  ✗ 拷贝失败：%s" % e)
        return None
    print("  ✓ 拷贝完成，用了 %.0f 秒" % (time.time() - t0))
    return dst


def rerun_in(dst, argv):
    """在拷贝后的目录里，用那边的 python 再跑一次自己。"""
    py = os.path.join(dst, "runtime", "python", "python.exe")
    if not os.path.exists(py):
        py = sys.executable
    script = os.path.join(dst, "tools", "first_run.py")
    print()
    print("  ── 下面在本地硬盘上正式跑 ──")
    print()
    return subprocess.call([py, "-X", "utf8", script] + list(argv))


def warn_if_removable():
    """在可移动/网络盘上跑卡：警告，但**不再拒绝、也不再偷偷拷走**。

    2026-11-07 改动（用户实测踩到）：
      原来在 U 盘上双击会静默把整张卡拷到 D:\\whale-card 再在那边跑。
      结果是「他点了 U 盘的『一键落地』，清的是 D 盘那份的退役标记，
      U 盘原地那份纹丝不动，控制台还让他点解除退役」。
      而且拷一次生成一个 whale-card-N，几次就成了三份卡。
    现在：原地跑。卡在可移动盘上会自动用 DELETE 日志（不是 WAL），
    代价是慢一点，好处是拔盘/掉电不会把库写坏。
    """
    t = _drive_type(ROOT)
    if t == 2:
        print()
        print("  ⚠ 这份卡在**可移动磁盘**上（U 盘）。")
        print("    我会原地跑，卡的日志模式会自动切成 DELETE（不用 WAL）——")
        print("    拔盘/掉电不会写坏库，代价是慢一点。")
        print("    ⚠ 拔盘之前一定先关服务、关控制台。")
        print("    想拷到本机硬盘再跑：加参数 --copy-to-local。")
        return True
    if t == 4:
        print()
        print("  ⚠ 这份卡在**网络盘**上，SQLite 走网络盘锁不住，建议先拷到本地硬盘。")
        return True
    return False


def section(n, title):
    print()
    print(BAR)
    print("  %d. %s" % (n, title))
    print(BAR)


# ──────────────────────────────────────────────────────
# 1. 体检
# ──────────────────────────────────────────────────────
def step_probe():
    """跑 env_probe，只把结论和「必须先解决」的部分打出来。"""
    try:
        import env_probe as ep
    except Exception as e:
        # 没有这个文件不是致命错误 —— 老包里可能确实没有
        print("  （没有 env_probe.py，跳过详细体检：%s）" % e)
        return []
    try:
        data = {
            "os": ep.probe_os(), "mem": ep.probe_mem(), "disks": ep.probe_disks(),
            "python": ep.probe_python(), "node": ep.probe_node(),
            "ffmpeg": ep.probe_ffmpeg(), "gpu": ep.probe_gpu(),
            "torch": ep.probe_torch(), "time": ep.probe_time(),
            "card": ep.probe_card(), "dsh": ep.probe_dsh(),
        }
        data["summary"] = ep.summarize(data)
    except Exception as e:
        print("  （体检自己出错了：%s）" % e)
        return []

    s = data.get("summary") or {}
    blocking = s.get("blocking") or []
    optional = s.get("optional") or []

    o = data.get("os") or {}
    print("  系统：%s" % (o.get("caption") or o.get("version") or "?"))
    print("  CPU ：%s" % (o.get("cpu") or "?"))
    mem = data.get("mem") or {}
    if mem.get("total_gb"):
        print("  内存：%.1f GB（可用 %.1f GB）" % (mem["total_gb"], mem.get("avail_gb") or 0))
    py = data.get("python") or {}
    print("  Python：%s（%s 位）" % (py.get("version") or "未装", py.get("bits") or "?"))
    gpu = data.get("gpu") or {}
    cards = gpu.get("cards") or []
    if cards:
        c0 = cards[0]
        print("  显卡：%s（%s MB，算力 %s）" % (c0.get("name"), c0.get("memory_mb"),
                                          c0.get("compute_cap", "?")))
    else:
        print("  显卡：没认出来（核显或驱动没装）")
    t = data.get("time") or {}
    print("  对时：w32time %s" % (t.get("service_state") or "?"))

    print()
    if blocking:
        print("  ⚠ 必须先解决：")
        for x in blocking:
            print("     · %s" % x)
    else:
        print("  ✓ 没有「必须先解决」的项目")
    if optional:
        print("  可选/提醒：")
        for x in optional:
            print("     · %s" % x)
    return blocking


# ──────────────────────────────────────────────────────
# 2. 落地
# ──────────────────────────────────────────────────────
def step_land(dry=False):
    """把卡外锚按这台机器的路径写回。"""
    import types
    import portable as P

    man_path = os.path.join(ROOT, "PORTABLE-MANIFEST.json")
    if not os.path.exists(man_path):
        print("  这已经是落好地的卡（没有 PORTABLE-MANIFEST.json），不用再 land。")
        return True

    ns = types.SimpleNamespace(
        bundle=ROOT,
        dry=dry,
        anchor_home=os.environ.get("WHALE_ANCHOR_HOME") or None,
        dsh_home=os.environ.get("DSH_HOME") or None,
    )
    try:
        rc = P.cmd_land(ns)
        return rc == 0
    except Exception as e:
        print("  ! land 出错：%s" % e)
        return False


# ──────────────────────────────────────────────────────
# 3. 交棒
# ──────────────────────────────────────────────────────
def step_handover(dry=False):
    import types
    import portable as P

    ns = types.SimpleNamespace(bundle=ROOT, dry=dry)
    try:
        rc = P.cmd_handover(ns)
        return rc == 0
    except Exception as e:
        print("  ! handover 出错：%s" % e)
        return False


# ──────────────────────────────────────────────────────
# 4+5. 起服务 + 自检
# ──────────────────────────────────────────────────────
def step_start():
    import bridge

    ok, h, note = bridge.ensure_server()
    print("  服务：%s" % note)
    if not ok:
        print("  ✗ 服务起不来：%s" % note)
        return None

    # 防「假绿灯」：8848 上可能住着**另一张卡**（比如旧机器那份还在跑）。
    # 那样 /health 明明通，读的却不是这个目录的卡 —— 搬家时最容易被骗的一次。
    want = os.path.abspath(os.path.join(ROOT, "card.db"))
    got = h.get("db")
    if got and os.path.abspath(got) != want:
        print("  ✗ 8848 上是**另一张卡**：%s" % got)
        print("    这个目录的卡是：%s" % want)
        print("    → 先关掉那个服务，再重跑。不然你看到的是别人家的绿灯笼。")
        return None
    return h


def print_health(h):
    if not h:
        return
    tl = h.get("timeline") or {}
    se = h.get("semantic") or {}
    w = tl.get("writer") or {}
    print()
    print("  记忆：%s 条" % h.get("memories"))
    print("  第  ：%s 天" % tl.get("day"))
    print("  链  ：%s" % ("完好" if tl.get("chain_ok") else "断了（%s）" % tl.get("chain_bad")))
    print("  锚  ：%s —— %s" % (tl.get("anchor_verdict"), tl.get("anchor_reason")))
    print("  可写：%s%s" % ("是" if not w.get("readonly") else "否",
                          "" if not w.get("readonly") else "（链头是 %s，需要 handover）"
                          % (w.get("locked_by") or w.get("head"))))
    print("  向量：%s 维 / 可用率 %s%s" % (se.get("model_dim"), se.get("usable_ratio"),
                                    ("  —— " + se["note"]) if se.get("note") else ""))
    if se.get("warn"):
        print("  ⚠ 向量警告：%s" % se["warn"])
    print("  人格：%s" % ("已封存" if h.get("sealed") else "没封"))


def step_selftest():
    """真写一句话再读回来 —— 只有真能写，才算搬成功。"""
    import bridge

    try:
        r = bridge._post("/report", {
            "user_message": "（换卡自检：我刚到这个新地方）",
            "reply": "（换卡自检：能读能写，链没断。）",
            "source": "first-run-selftest",
        })
        print("  试写：成功  mem_id=%s" % r.get("mem_id"))
        return True
    except Exception as e:
        print("  试写：失败  %s" % e)
        return False


# ──────────────────────────────────────────────────────
# 6. 开控制台
# ──────────────────────────────────────────────────────
def step_console(port=8848):
    url = "http://127.0.0.1:%d/console" % port
    try:
        import webbrowser
        webbrowser.open(url)
        print("  已打开：%s" % url)
    except Exception as e:
        print("  打不开浏览器（%s），手动访问：%s" % (e, url))


def main(argv=None):
    ap = argparse.ArgumentParser(description="卡搬到新地方之后跑这一条")
    ap.add_argument("--dry", action="store_true", help="只看会做什么，不写任何文件")
    ap.add_argument("--no-browser", action="store_true", help="最后不自动开控制台")
    ap.add_argument("--skip-probe", action="store_true", help="跳体检（赶时间时用）")
    ap.add_argument("--copy-to-local", action="store_true",
                    help="在 U 盘/网络盘上时，先拷到本机硬盘再跑（默认原地跑）")
    a = ap.parse_args(argv)

    print()
    print("  数字生命卡 · 一键落地")
    print("  目录：%s" % ROOT)
    print("  Python：%s" % sys.version.split()[0])

    if _drive_type(ROOT) in (2, 4) and a.copy_to_local:
        # 只有明确说了「拷到本地」才拷。默认原地跑 —— 见 warn_if_removable 的注释。
        print()
        print(BAR)
        print("  这个目录不在本地硬盘上，按你说的先拷到本地硬盘再跑。")
        print(BAR)
        if a.dry:
            need = _dir_size(ROOT)
            tgt = pick_local_target(need)
            print("  （彩排）会拷到：%s" % os.path.join(tgt or "?", "whale-card-<时间戳>"))
            return 0
        dst = auto_copy_to_local()
        if not dst:
            return 1
        passthrough = [x for x in list(argv or sys.argv[1:]) if x != "--copy-to-local"]
        return rerun_in(dst, passthrough)

    on_removable = warn_if_removable()

    blocking = []
    if not a.skip_probe:
        section(1, "体检")
        blocking = step_probe()

    section(2, "落地（把锚按这台机器的路径写回）")
    ok_land = step_land(dry=a.dry)

    section(3, "交棒（让这张卡认这台机器能写）")
    ok_ho = step_handover(dry=a.dry)

    # 交棒之后这张卡就归本机了：退役标记必须清掉，否则服务起来还是只读的。
    try:
        import disks as _dk
        if _dk.read_retired(ROOT):
            if a.dry:
                print("  （彩排）会清掉退役标记")
            else:
                _dk.set_retired(ROOT, False)
                print("  已清掉退役标记：这张卡重新上岗")
    except Exception as e:
        print("  ⚠ 清退役标记失败：%s" % e)

    section(4, "起服务")
    h = step_start()

    section(5, "自检")
    print_health(h)
    writable = bool(h) and not ((h.get("timeline") or {}).get("writer") or {}).get("readonly")
    ok_write = step_selftest() if (writable and not a.dry) else False
    if h and not writable:
        print("  → 卡是只读的：链头属于另一台机器。跑 handover 或确认你搬的是最新那份。")

    if not a.no_browser:
        section(6, "打开控制台")
        step_console()

    print()
    print(BAR)
    if a.dry:
        print("  这是一次彩排（--dry），什么都没写。")
    elif on_removable and blocking:
        print("  上面有必须先解决的问题；另外记住：拔 U 盘前先停服务。")
    elif blocking:
        print("  有必须先解决的问题，先处理上面的「必须先解决」再重跑。")
    elif not (ok_land and ok_ho and h and ok_write):
        print("  没全通过：落地 %s / 交棒 %s / 服务 %s / 试写 %s"
              % (ok_land, ok_ho, bool(h), ok_write))
    else:
        print("  就绪。卡在这台机器上能读能写，链和锚都对。")
    print(BAR)
    print()

    if a.dry:
        return 0
    return 0 if (ok_land and ok_ho and h and ok_write and not blocking) else 1


if __name__ == "__main__":
    sys.exit(main())
