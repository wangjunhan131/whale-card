# -*- coding: utf-8 -*-
"""在新电脑上还原 DSH 插件包（在随身盘 H:\\_projects\\plugins 里运行）。

用法：
    python tools\\install_plugins.py           # 真装
    python tools\\install_plugins.py --dry     # 只看会做什么

做的事：
  1) profile/ 里的 package.json / lock / cordis 配置 → %USERPROFILE%\\.dsh\\profiles\\desktop\\
  2) node_modules.tar.gz → 解压到 %%USERPROFILE%%\\.dsh\\profiles\\desktop\\
  3) packages\\*.tgz / 目录 → %USERPROFILE%\\.dsh\\plugin-packages\\
  4) wheels\\*.whl（scrapling 那套）→ 提示一条 pip 命令（不自动装，免得动到新机器的 Python）

原则：**不覆盖已有配置**。目标文件存在时先备份成 .bak-<时间戳>。
"""
import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PACK = os.path.join(HERE, "..", "_projects", "plugins")
if not os.path.isdir(PACK):
    PACK = os.environ.get("PLUGIN_PACK", PACK)

DST_PROFILE = os.path.join(os.environ["USERPROFILE"], ".dsh", "profiles", "desktop")
DST_PKGS = os.path.join(os.environ["USERPROFILE"], ".dsh", "plugin-packages")
STAMP = time.strftime("%Y%m%d-%H%M%S")


def backup(path, dry):
    if os.path.exists(path):
        bak = path + ".bak-install-" + STAMP
        if not dry:
            shutil.copy2(path, bak)
        print("  备份:", os.path.basename(bak))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    dry = args.dry

    print("插件包目录:", os.path.abspath(PACK))
    if not os.path.isdir(PACK):
        print("！找不到插件包目录（应该在随身盘的 _projects\\plugins）")
        return 2

    os.makedirs(DST_PROFILE, exist_ok=True)
    os.makedirs(DST_PKGS, exist_ok=True)

    # ① 元数据
    meta = os.path.join(PACK, "profile")
    if os.path.isdir(meta):
        for fn in sorted(os.listdir(meta)):
            src = os.path.join(meta, fn)
            dst = os.path.join(DST_PROFILE, fn)
            print("  配置:", fn)
            backup(dst, dry)
            if not dry:
                shutil.copy2(src, dst)

    # ② node_modules
    tar = os.path.join(PACK, "node_modules.tar.gz")
    if os.path.exists(tar):
        print("  解压 node_modules（%.1f MB）…" % (os.path.getsize(tar) / 1048576.0))
        if not dry:
            old = os.path.join(DST_PROFILE, "node_modules")
            if os.path.isdir(old):
                moved = old + ".old-" + STAMP
                os.replace(old, moved)
                print("   旧 node_modules 挪到:", os.path.basename(moved))
            r = subprocess.run(["tar", "-xzf", tar, "-C", DST_PROFILE],
                               capture_output=True, text=True)
            print("   解压:", "ok" if r.returncode == 0 else r.stderr[-300:])
    else:
        print("！没有 node_modules.tar.gz（只还原配置，插件依赖要联网 pnpm add）")

    # ③ 本地包
    pk = os.path.join(PACK, "packages")
    if os.path.isdir(pk):
        for fn in sorted(os.listdir(pk)):
            src = os.path.join(pk, fn)
            dst = os.path.join(DST_PKGS, fn)
            print("  本地包:", fn)
            if dry:
                continue
            if os.path.isdir(src):
                if os.path.isdir(dst):
                    shutil.rmtree(dst, ignore_errors=True)
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)

    # ④ Python 轮子：给提示，不自动装
    wh = os.path.join(PACK, "wheels")
    if os.path.isdir(wh):
        print("\n  Python 侧（可选，要让 scrapling 能跑再装）：")
        print('    pip install --no-index --find-links "%s" scrapling' % os.path.abspath(wh))

    print("\n完成。接下来：重开 DSH → 设置里确认插件列表 → 缺哪个按 PLUGINS.md 里的一条命令补。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
