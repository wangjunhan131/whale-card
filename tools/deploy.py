# -*- coding: utf-8 -*-
"""一键部署：在新电脑上把这张卡「叫醒」并接到 DSH。

它做的是「带卡出门」的另一半：
    旧机器  go_out.py  → 停机、落盘、镜像到盘、旧卡退役（只读）
    新机器  deploy.py  → 认路、接手、起服务、试写一句、接入 DSH、开控制台

为什么不再写一套落地逻辑：
    `tools/first_run.py` 已经把 land / handover / 起服务 / 自检全做过了，
    而且它踩过的坑（假绿灯、退役标记没清、锚指向旧路径）都修在里面。
    这里只负责「前面补两件事、后面补两件事」：
        前：检查便携 Python 与依赖（新机器可能连 Python 都没有）
        后：接入 DSH、打开控制台

用法：
    runtime\\python\\python.exe tools\\deploy.py            正常跑
    ... deploy.py --dry                                     只彩排（不写文件、不起服务）
    ... deploy.py --skip-probe --no-dsh --no-console        赶时间/排错时

安全阀：
    · 任何一步失败就停，并把「已经做了什么、下一步该做什么」打清楚。
    · --dry 全程不写：land/handover 内部走只读路径。
"""
import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAR = "─" * 62


def say(msg=""):
    print(msg, flush=True)


def section(n, title):
    say()
    say(BAR)
    say("  %d. %s" % (n, title))
    say(BAR)


def _runtime_python():
    p = os.path.join(ROOT, "runtime", "python", "python.exe")
    return p if os.path.exists(p) else None


def step_env():
    """确认解释器与依赖。这一步不装东西，只判断 + 说清楚怎么办。"""
    py = _runtime_python()
    say("  解释器：%s" % (py or "（用系统 Python：%s）" % sys.executable))
    if not py:
        say("  ! 卡里没有便携 Python（runtime\\python）。")
        say("    能用系统 Python 的前提：3.13 + pip install -r requirements.txt")
    ok, detail = True, []
    try:
        import numpy  # noqa: F401
        detail.append("numpy %s" % getattr(numpy, "__version__", "?"))
    except Exception as e:
        ok = False
        detail.append("numpy 缺失（%s）" % e)
    try:
        import psutil  # noqa: F401
        detail.append("psutil %s" % getattr(psutil, "__version__", "?"))
    except Exception as e:
        detail.append("psutil 缺失（%s，不影响核心）" % e)
    say("  依赖：" + " / ".join(detail))
    if not ok:
        say("  ✗ numpy 没装，卡跑不起来。先在新机器上：")
        say("      python -m pip install -r requirements.txt")
        return False
    return True


def step_first_run(dry):
    say("  交给 tools\\first_run.py（认路 / 接手 / 起服务 / 自检）")
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    sys.path.insert(0, ROOT)
    import first_run
    argv = ["--no-browser", "--skip-probe"]
    if dry:
        # first_run 的 --dry 里 land/handover 都是只读路径，不会写盘；
        # 它仍会去看 8848——如果那上面住着另一张卡，它会明确报「另一张卡」，
        # 这正好也是彩排要暴露的问题之一。
        argv = ["--dry"] + argv
    rc = first_run.main(argv)
    if rc != 0:
        say("  ✗ first_run 失败（rc=%s）" % rc)
    return rc == 0


def step_dsh():
    p = os.path.join(ROOT, "tools", "install_to_dsh.py")
    if not os.path.exists(p):
        say("  - 没有 tools\\install_to_dsh.py，跳过")
        return True
    say("  接入 DSH（把 whale 桥插件拷进 DSH profile）")
    r = subprocess.run([sys.executable, "-X", "utf8", p],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (r.stdout or "").strip()
    for line in out.splitlines()[-6:]:
        say("    " + line)
    if r.returncode != 0:
        say("  ! 接入 DSH 失败（rc=%s）：DSH 里暂时没有 mind_* 工具" % r.returncode)
    else:
        say("  ✓ 已接入：重启 DSH 后生效")
    return r.returncode == 0


def step_console():
    p = os.path.join(ROOT, "tools", "open_console.py")
    if not os.path.exists(p):
        return True
    subprocess.run([sys.executable, "-X", "utf8", p], cwd=ROOT)
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description="一键部署：让这张卡在这台电脑上活过来")
    ap.add_argument("--dry", action="store_true", help="只彩排，不写任何文件")
    ap.add_argument("--skip-probe", action="store_true", help="跳体检（默认就跳）")
    ap.add_argument("--no-dsh", action="store_true", help="不接入 DSH")
    ap.add_argument("--no-console", action="store_true", help="最后不开控制台")
    a = ap.parse_args(argv)

    say()
    say("  数字生命卡 · 一键部署")
    say("  目录：%s" % ROOT)
    say("  时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"))

    section(1, "环境")
    if not step_env():
        return 2

    section(2, "认路与接手")
    if not step_first_run(a.dry):
        return 3

    if a.dry:
        say()
        say(BAR)
        say("  彩排结束：什么都没写、没起服务。")
        say("  去掉 --dry 就是真部署。")
        say(BAR)
        return 0

    if not a.no_dsh:
        section(3, "接入 DSH")
        step_dsh()

    if not a.no_console:
        section(4, "打开控制台")
        step_console()

    say()
    say(BAR)
    say("  部署完成。")
    say("  · 控制台：http://127.0.0.1:8848")
    say("  · DSH：重启后会有 mind_* 工具（问一句「你还记得吗」试试）")
    say("  · 离开这台电脑前，双击「带卡出门」再拔盘")
    say(BAR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
