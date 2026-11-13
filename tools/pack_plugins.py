# -*- coding: utf-8 -*-
"""把 DSH 插件「整打包」到随身盘：能打的打包，不能打的记名字和链接。

产物（默认 H:\\_projects\\plugins\\）：
  profile\\            拉起插件所需的元数据（package.json / lock / cordis 配置）
  node_modules.tar.gz 已装好的全部依赖（离线可还原）
  packages\\           file: 装的本地 tgz/目录
  wheels\\             pip 侧的依赖轮子（scrapling 那套）
  PLUGINS.json         机器可读清单
  PLUGINS.md           人看的清单（名字 / 版本 / 来源链接 / 怎么装）
"""
import io
import json
import os
import shutil
import subprocess
import sys
import time

PROFILE = os.path.join(os.environ["USERPROFILE"], ".dsh", "profiles", "desktop")
OUT = os.environ.get("PLUGIN_OUT", r"H:\_projects\plugins")
PKG_CACHE = os.path.join(os.environ["USERPROFILE"], ".dsh", "plugin-packages")

META_FILES = ["package.json", "pnpm-lock.yaml", "pnpm-workspace.yaml",
              "cordis.patch.yml", "cordis.yml"]


def run(cmd, **kw):
    print("  $ " + " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def node_version(name):
    p = os.path.join(PROFILE, "node_modules", name, "package.json")
    try:
        with io.open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def main():
    os.makedirs(OUT, exist_ok=True)
    prof = json.load(io.open(os.path.join(PROFILE, "package.json"), encoding="utf-8"))
    deps = prof.get("dependencies", {})
    bundles = (((prof.get("dsh") or {}).get("profile") or {}).get("bundles") or [])

    # ① 元数据
    meta_dir = os.path.join(OUT, "profile")
    os.makedirs(meta_dir, exist_ok=True)
    for fn in META_FILES:
        src = os.path.join(PROFILE, fn)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(meta_dir, fn))
    print("元数据:", META_FILES)

    # ② 本地 file: 包
    pkg_dir = os.path.join(OUT, "packages")
    os.makedirs(pkg_dir, exist_ok=True)
    for name, spec in deps.items():
        if spec.startswith("file:"):
            src = spec[5:]
            src = src.replace("/", os.sep)
            if os.path.exists(src):
                dst = os.path.join(pkg_dir, os.path.basename(src.rstrip(os.sep)))
                if os.path.isdir(src):
                    if os.path.exists(dst):
                        shutil.rmtree(dst)
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
                print("  本地包:", os.path.basename(src))

    # ③ node_modules 整打
    tar_path = os.path.join(OUT, "node_modules.tar.gz")
    if not os.path.exists(tar_path) or os.path.getmtime(tar_path) < time.time() - 3600:
        r = run(["tar", "-czf", tar_path, "-C", PROFILE, "node_modules"])
        print("  tar:", "ok" if r.returncode == 0 else r.stderr[-300:])

    # ④ pip 轮子
    wheels = os.path.join(OUT, "wheels")
    os.makedirs(wheels, exist_ok=True)
    r = run([sys.executable, "-m", "pip", "download",
             "-d", wheels, "--only-binary=:all:",
             "scrapling==0.5.1", "curl_cffi", "browserforge", "tld", "playwright"])
    print("  wheels:", "ok" if r.returncode == 0 else r.stderr[-300:])

    # ⑤ 清单
    rows = []
    for name in sorted(deps):
        spec = deps[name]
        meta = node_version(name)
        ver = meta.get("version") or (spec.lstrip("^~"))
        if spec.startswith("github:"):
            kind, link = "github", "https://github.com/" + spec[len("github:"):]
            cmd = "pnpm add " + spec
        elif spec.startswith("file:"):
            kind, link = "local", os.path.basename(spec.replace("/", os.sep))
            cmd = "pnpm add ./packages/" + link
        else:
            kind = "npm"
            link = "https://www.npmjs.com/package/" + (name.split("/")[-1] if name.startswith("@") else name)
            cmd = "pnpm add %s@%s" % (name, ver)
        rows.append({"name": name, "version": ver, "spec": spec, "kind": kind,
                     "link": link, "install": cmd,
                     "desc": (meta.get("description") or "").strip()[:120]})

    data = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "profile": PROFILE,
        "dsh_bundles": bundles,
        "dependencies": rows,
        "artifacts": {
            "node_modules": "node_modules.tar.gz",
            "packages": "packages/",
            "wheels": "wheels/",
            "profile_meta": "profile/",
        },
        "how_to_install": [
            "1) 新机器先装好 DSH（自带 @deepseek-ai/dsh-base 等核心 bundle）",
            "2) 把 profile/ 下四个文件放进 %USERPROFILE%\\.dsh\\profiles\\desktop\\",
            "3) 解压 node_modules.tar.gz 到同一目录（tar -xzf node_modules.tar.gz）",
            "4) 把 packages/ 里的本地包放回 %USERPROFILE%\\.dsh\\plugin-packages\\",
            "5) 需要 scrapling 时：pip install --no-index --find-links wheels scrapling",
            "6) 重开 DSH；缺哪个插件就 pnpm add <名字>@<版本>（清单里有命令）",
        ],
    }
    with io.open(os.path.join(OUT, "PLUGINS.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    # ⑥ 人看的清单
    lines = ["# DSH 插件清单（随身包）", "",
             "生成时间：%s" % data["generated_at"], "",
             "## 怎么还原到新电脑", ""]
    lines += ["%d. %s" % (i + 1, s) for i, s in enumerate(data["how_to_install"])]
    lines += ["", "## 依赖（%d 个）" % len(rows), "",
              "| 插件 | 版本 | 来源 | 链接 | 一条命令 |", "|---|---|---|---|---|"]
    for r in rows:
        lines.append("| `%s` | %s | %s | %s | `%s` |" % (
            r["name"], r["version"], r["kind"], r["link"], r["install"]))
    lines += ["", "## profile 里注册的 bundle（%d 个）" % len(bundles), ""]
    lines += ["- `%s`" % b for b in bundles]
    with io.open(os.path.join(OUT, "PLUGINS.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print("\n产物目录:", OUT)
    for fn in ["node_modules.tar.gz", "PLUGINS.json", "PLUGINS.md"]:
        p = os.path.join(OUT, fn)
        if os.path.exists(p):
            print("  %-22s %8.1f MB" % (fn, os.path.getsize(p) / 1048576.0))


if __name__ == "__main__":
    main()
