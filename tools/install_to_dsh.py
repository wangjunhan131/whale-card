# -*- coding: utf-8 -*-
"""把卡的原生通道（dsh-whale-bridge 插件）接进 DSH。

为什么需要这一步：
    「卡」是记忆和状态，「DSH」是嘴和脑子。卡搬到了新机器，如果 DSH 里没有
    这个插件，DSH 就不知道卡存在 —— 每轮只能手工敲命令行，等于白搬。

它做三件事，全部幂等（重复跑安全）：
    1. 把卡目录里的 dsh-plugin/ 拷进 <DSH>/profiles/<profile>/node_modules/dsh-whale-bridge
    2. 在 profile 的 package.json 的 dsh.profile.bundles 里加上 dsh-whale-bridge
    3. 在 profile 的 cordis.patch.yml 里加一条 whale-bridge（路径按**这台机器**写）

用法：
    python tools\\install_to_dsh.py            # 自动找 profile
    python tools\\install_to_dsh.py --profile desktop
    python tools\\install_to_dsh.py --dry      # 只看会做什么
"""
import argparse
import io
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PLUGIN_NAME = "dsh-whale-bridge"
SRC = os.path.join(ROOT, "dsh-plugin")


def find_dsh_home():
    for p in (os.path.join(os.path.expanduser("~"), ".dsh"),
              r"~\.dsh"):
        if os.path.isdir(p):
            return p
    return None


def list_profiles(dsh_home):
    base = os.path.join(dsh_home, "profiles")
    if not os.path.isdir(base):
        return []
    return [d for d in sorted(os.listdir(base))
            if os.path.isdir(os.path.join(base, d))]


def which_python():
    return sys.executable


def copy_plugin(profile_dir, dry=False):
    dst = os.path.join(profile_dir, "node_modules", PLUGIN_NAME)
    if not os.path.isdir(SRC):
        return False, "卡目录里没有 dsh-plugin/（这个包不是完整的）"
    if dry:
        return True, "会把 dsh-plugin/ 拷到 %s" % dst
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.isdir(dst):
            shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(SRC, dst,
                        ignore=shutil.ignore_patterns("load.log", "*.bak-*",
                                                      "__pycache__"))
        return True, "插件已拷到 %s" % dst
    except Exception as e:
        return False, "拷贝失败：%s" % e


def patch_package_json(profile_dir, dry=False):
    p = os.path.join(profile_dir, "package.json")
    if not os.path.exists(p):
        return False, "没有 %s（这个 profile 不完整）" % p
    try:
        with io.open(p, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception as e:
        return False, "读 package.json 失败：%s" % e

    dsh = doc.setdefault("dsh", {})
    prof = dsh.setdefault("profile", {})
    bundles = prof.setdefault("bundles", [])
    if PLUGIN_NAME in bundles:
        return True, "package.json 里已经有了"
    if dry:
        return True, "会在 dsh.profile.bundles 里加 %s" % PLUGIN_NAME
    bundles.append(PLUGIN_NAME)
    try:
        with io.open(p, "w", encoding="utf-8", newline="\n") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
            f.write("\n")
        return True, "package.json 已加进 bundles"
    except Exception as e:
        return False, "写 package.json 失败：%s" % e


def patch_cordis(profile_dir, dry=False):
    p = os.path.join(profile_dir, "cordis.patch.yml")
    if not os.path.exists(p):
        return False, "没有 %s（这个 profile 不完整）" % p
    with io.open(p, encoding="utf-8") as f:
        txt = f.read()
    if "- id: whale-bridge" in txt:
        return True, "cordis.patch.yml 里已经有了"

    block = "\n".join([
        "",
        "# ===== 小鲸鱼：卡的原生通道（由 tools/install_to_dsh.py 写入）=====",
        "- id: whale-bridge",
        '  name: "%s"' % PLUGIN_NAME,
        "  config:",
        "    host: 127.0.0.1",
        "    port: 8848",
        "    cardDir: '%s'" % ROOT.replace("\\", "\\\\"),
        "    pythonPath: '%s'" % which_python().replace("\\", "\\\\"),
        "    autoStart: true",
        "    timeoutMs: 30000",
        "    healthTimeoutMs: 3000",
        "",
    ])
    if dry:
        return True, "会在 cordis.patch.yml 末尾加一条 whale-bridge"
    try:
        with io.open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(txt.rstrip("\n") + "\n" + block)
        return True, "cordis.patch.yml 已加 whale-bridge"
    except Exception as e:
        return False, "写 cordis.patch.yml 失败：%s" % e


def main(argv=None):
    ap = argparse.ArgumentParser(description="把卡接进 DSH")
    ap.add_argument("--profile", default="", help="profile 名字，默认自动挑")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args(argv)

    print()
    print("  把「卡」接进 DSH")
    print("  卡目录：%s" % ROOT)

    dsh_home = find_dsh_home()
    if not dsh_home:
        print()
        print("  ✗ 没找到 DSH（%USERPROFILE%\\.dsh）。先把 DSH 装上再来。")
        return 1
    print("  DSH 家目录：%s" % dsh_home)

    profiles = list_profiles(dsh_home)
    if not profiles:
        print("  ✗ .dsh\\profiles 下没有任何 profile。先启动一次 DSH 让它建好。")
        return 1
    name = a.profile or ("desktop" if "desktop" in profiles else profiles[0])
    if name not in profiles:
        print("  ✗ 没有这个 profile：%s（现有：%s）" % (name, ", ".join(profiles)))
        return 1
    profile_dir = os.path.join(dsh_home, "profiles", name)
    print("  profile：%s" % name)

    ok_all = True
    for label, fn in (("拷插件", copy_plugin),
                      ("改 package.json", patch_package_json),
                      ("改 cordis.patch.yml", patch_cordis)):
        ok, msg = fn(profile_dir, dry=a.dry)
        print("  %s %s：%s" % ("✓" if ok else "✗", label, msg))
        ok_all = ok_all and ok

    print()
    if ok_all and not a.dry:
        print("  好了。**重启一次 DSH**，新会话里就会有 18 个 mind_* 工具。")
        print("  自检：重启后看 %s" % os.path.join(profile_dir, "node_modules",
                                                PLUGIN_NAME, "load.log"))
        print("        最后一行应该是「apply() 完成，共注册 18 个工具」。")
    elif a.dry:
        print("  这是彩排，什么都没写。")
    else:
        print("  有没做成的步骤，看上面的 ✗。")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
