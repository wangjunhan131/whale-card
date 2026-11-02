# -*- coding: utf-8 -*-
"""往可移植包里装运行环境：便携 Python + numpy/psutil + ffmpeg。

为什么要有这一步：
    文件全了不等于能跑。新电脑可能没装 Python，视频/语音要 ffmpeg ——
    这两样都不在卡里，得跟卡一起走。

用法：
    python tools/install_runtime.py --bundle G:\\ 
    python tools/install_runtime.py --bundle G:\\ --ffmpeg-src "D:\\...\\ffmpeg\\bin"
    python tools/install_runtime.py --bundle D:\\_probe --skip-download   （复用已缓存的 zip）

装完目录长这样：
    <bundle>/runtime/python/python.exe        ← 便携解释器（含 numpy / psutil）
    <bundle>/runtime/ffmpeg/ffmpeg.exe         ← 视频抽帧、语音转 16k wav
    <bundle>/runtime/ffmpeg/ffprobe.exe
    <bundle>/runtime/README.txt

注意：numpy 是从**当前这台机器**的 site-packages 直接拷过去的，
      所以要求同平台同 Python 小版本（这里固定 3.13 + win_amd64）。
      跨版本就要改成 pip download 对应 cpXX 的 wheel。
"""
import argparse
import io
import os
import shutil
import subprocess
import sys
import zipfile

PYVER = "3.13.1"
PY_URL = "https://mirrors.huaweicloud.com/python/%(v)s/python-%(v)s-embed-amd64.zip"
CACHE = os.path.join(os.path.expanduser("~"), ".cache", "whale-runtime")


def log(msg):
    print(msg, flush=True)


def _download(url, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst) and os.path.getsize(dst) > 1024 * 1024:
        log("  用缓存的 %s" % os.path.basename(dst))
        return dst
    log("  下载 %s" % url)
    import urllib.request
    with urllib.request.urlopen(url, timeout=300) as r, open(dst, "wb") as f:
        shutil.copyfileobj(r, f)
    return dst


def install_python(bundle, skip_download=False):
    py_root = os.path.join(bundle, "runtime", "python")
    if os.path.exists(os.path.join(py_root, "python.exe")):
        log("便携 Python 已在（%s），跳过解压" % py_root)
    else:
        zpath = os.path.join(CACHE, "python-%s-embed-amd64.zip" % PYVER)
        if not skip_download:
            _download(PY_URL % {"v": PYVER}, zpath)
        elif not os.path.exists(zpath):
            raise SystemExit("没有缓存的 zip，去掉 --skip-download 再跑")
        os.makedirs(py_root, exist_ok=True)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(py_root)
        log("  解压到 %s" % py_root)

    # 让 embeddable 认 Lib\site-packages，否则装进去的包 import 不到
    pth = os.path.join(py_root, "python%s._pth" % "".join(PYVER.split(".")[:2]))
    if not os.path.exists(pth):
        cand = [f for f in os.listdir(py_root) if f.endswith("._pth")]
        pth = os.path.join(py_root, cand[0]) if cand else None
    if pth:
        with io.open(pth, "w", encoding="ascii") as f:
            f.write("python%s.zip\n" % ("".join(PYVER.split(".")[:2])))
            f.write(".\nLib\\site-packages\nimport site\n")
        log("  写好 _pth：%s" % os.path.basename(pth))

    site = os.path.join(py_root, "Lib", "site-packages")
    os.makedirs(site, exist_ok=True)
    import sysconfig
    src_site = sysconfig.get_paths()["purelib"]
    for pkg in ("numpy", "numpy.libs", "psutil"):
        s = os.path.join(src_site, pkg)
        d = os.path.join(site, pkg)
        if not os.path.exists(s):
            log("  ⚠ 本机没有 %s，跳过（装上再跑一次这个脚本）" % pkg)
            continue
        if os.path.exists(d):
            shutil.rmtree(d, ignore_errors=True)
        shutil.copytree(s, d)
        log("  拷依赖 %s" % pkg)

    exe = os.path.join(py_root, "python.exe")
    out = subprocess.run([exe, "-c", "import numpy, psutil, sqlite3; print('ok')"],
                         capture_output=True,
                         env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    ok = b"ok" in (out.stdout or b"")
    log("  自检 import numpy/psutil/sqlite3 → %s" % ("通过" if ok else "失败"))
    if not ok:
        log((out.stderr or b"").decode("utf-8", "replace")[:400])
    return ok


def install_ffmpeg(bundle, src_dir=None):
    dst = os.path.join(bundle, "runtime", "ffmpeg")
    os.makedirs(dst, exist_ok=True)
    got = []
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        if os.path.exists(os.path.join(dst, name)):
            got.append(name)
            continue
        cand = None
        if src_dir:
            cand = os.path.join(src_dir, name)
        if not cand or not os.path.exists(cand):
            cand = shutil.which(name)
        if cand and os.path.exists(cand):
            shutil.copy2(cand, os.path.join(dst, name))
            got.append(name)
    log("  ffmpeg 就位：%s" % (", ".join(got) if got else "（没找到，视频/语音会不可用）"))
    return bool(got)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", required=True, help="可移植包根目录（U 盘根）")
    ap.add_argument("--ffmpeg-src", default=None, help="ffmpeg.exe 所在目录")
    ap.add_argument("--skip-download", action="store_true")
    a = ap.parse_args(argv)

    bundle = os.path.abspath(a.bundle)
    if not os.path.exists(os.path.join(bundle, "card.db")):
        raise SystemExit("%s 里没有 card.db —— 这是卡包根目录吗？" % bundle)

    log("== 便携 Python ==")
    ok_py = install_python(bundle, a.skip_download)
    log("== ffmpeg ==")
    ok_ff = install_ffmpeg(bundle, a.ffmpeg_src)

    note = [
        "运行环境（随卡一起走）",
        "",
        "python/python.exe    便携解释器（%s，已含 numpy / psutil）" % PYVER,
        "ffmpeg/ffmpeg.exe    视频抽帧、语音转 wav",
        "",
        "用法：双击上层的「启动控制台.bat」，它会优先用这里的 python。",
        "换一台新电脑时：插上盘 → 双击 bat → 浏览器打开控制台。",
    ]
    with io.open(os.path.join(bundle, "runtime", "README.txt"), "w",
                 encoding="utf-8") as f:
        f.write("\n".join(note))

    log("")
    log("结果：python=%s ffmpeg=%s" % (ok_py, ok_ff))
    return 0 if (ok_py and ok_ff) else 2


if __name__ == "__main__":
    sys.exit(main())
