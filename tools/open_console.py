# -*- coding: utf-8 -*-
"""打开控制台 —— 被 `启动控制台.bat` 调起来干活。

为什么不在 bat 里写中文：cmd.exe 对非 ASCII 的 .bat 处理不一致（GBK/UTF-8
之间来回转），最坏的情况是脚本直接不执行，看起来就是「双击了没反应」。
所以 bat 只当壳（纯 ASCII），中文一律在这里打。

要做四件事：
  1. 确认 8848 上的服务活着（没有就拉起来）
  2. 确认识别到的是**这个目录里的卡**（避免看到别人家的绿灯笼）
  3. 找一个真能用的浏览器（Edge → Chrome → 系统默认）
  4. 打开 /console
"""
import json
import os
import sys
import time
import urllib.request
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

URL = "http://127.0.0.1:8848/console"


def health(timeout=5):
    try:
        with urllib.request.urlopen("http://127.0.0.1:8848/health", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def ensure_service():
    """没有服务就用 bridge.py 拉起来（它自己会等到端口通）。"""
    h = health()
    if h:
        return h
    print("  服务没在跑，正在拉起来…")
    try:
        import subprocess
        subprocess.call([sys.executable, "-X", "utf8", "bridge.py", "health"],
                        cwd=ROOT)
    except Exception as e:
        print("  ✗ 拉不起来：%s" % e)
        return None
    for _ in range(30):
        time.sleep(1)
        h = health()
        if h:
            return h
    return None


def check_same_card(h):
    """8848 上如果是另一张卡，宁可不开，也别让他看错。"""
    mine = os.path.abspath(os.path.join(ROOT, "card.db"))
    theirs = h.get("db")
    if not theirs:
        return True
    try:
        same = os.path.normcase(os.path.abspath(theirs)) == os.path.normcase(mine)
    except Exception:
        same = False
    if not same:
        print()
        print("  ⚠ 8848 上跑的是**另一张卡**：")
        print("      %s" % theirs)
        print("    这个目录里的卡是：")
        print("      %s" % mine)
        print("    先关掉那个服务，再开这个控制台。")
        return False
    return True


def find_browser():
    cands = [
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                     "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                     "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     "Google", "Chrome", "Application", "chrome.exe"),
    ]
    for c in cands:
        if os.path.exists(c):
            return c
    return None


def main():
    print()
    print("  小鲸鱼 · 控制台")
    print("  目录：%s" % ROOT)

    h = ensure_service()
    if not h:
        print()
        print("  ✗ 服务起不来。手动看一眼：")
        print("      \"%s\" -X utf8 bridge.py health" % sys.executable)
        return 1

    if not check_same_card(h):
        return 1

    tl = h.get("timeline") or {}
    print("  服务：已就绪（%s 条记忆，第 %s 天）" % (h.get("memories"), tl.get("day")))

    exe = find_browser()
    if exe:
        print("  浏览器：%s" % os.path.basename(exe))
        try:
            import subprocess
            subprocess.Popen([exe, "--new-window", URL],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print("  打不开（%s），用手动地址" % e)
    else:
        print("  没找到 Edge/Chrome，交给系统默认浏览器")
        try:
            webbrowser.open(URL)
        except Exception as e:
            print("  ✗ 还是打不开（%s）" % e)

    print("  地址：%s" % URL)
    print("  这个窗口可以关，服务是独立进程。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
