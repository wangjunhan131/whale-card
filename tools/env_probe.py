# -*- coding: utf-8 -*-
"""到新机器上第一件事：看环境。

为什么要有它：
    E3 那台是空机，什么都没有。凭印象装东西最容易漏，也最容易装错版本
    （比如在 Pascal 卡上装了个不带 sm_61 的 torch，一路报错还查不出来）。
    所以先跑这个，把「有什么、缺什么、装什么版本」一次问清楚，
    再决定装什么。**它只读，不改任何东西。**

用法：
    python tools/env_probe.py            # 打印给人看
    python tools/env_probe.py --json     # 只吐 JSON（给卡/给日志吃）

判读要点（写在输出里，别只看红绿）：
    - python 缺 → 卡根本起不来（除非包里带了 runtime/python）
    - torch 缺不算问题：卡的向量检索是纯 numpy，不需要 torch
    - torch 有但 sm_61 不在列表 → 模型能加载、算子会崩，白装
    - 时间服务没校准 → 记忆会「加速遗忘」或「冻结」，比缺依赖更危险
"""
import argparse
import ctypes
import json
import os
import platform
import shutil
import subprocess
import sys


def _run(args, timeout=20, shell=False):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace", shell=shell)
        return (p.stdout or "").strip()
    except Exception as e:
        return ""


def _first_line(text):
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _wmic_col(args):
    """wmic /format:csv 只取真正的值（第一行是 NODE 表头，还可能夹空行）。"""
    raw = _run(["wmic"] + args + ["/format:csv"])
    for line in raw.splitlines():
        cells = [c.strip() for c in line.split(",")]
        if not cells or not cells[0] or cells[0].upper() == "NODE":
            continue
        for c in reversed(cells):
            if c and c != cells[0]:
                return c
    return ""


def probe_os():
    return {
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "hostname": platform.node(),
        "caption": platform.platform(),
        "cpu": _wmic_col(["cpu", "get", "name"]) or platform.processor(),
    }


def probe_mem():
    """物理内存（GB）。卡跑得动的最低线是 4G，但这个数也决定能不能在本地跑模型。"""
    try:
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MEMORYSTATUSEX()
        m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return {"total_gb": round(m.ullTotalPhys / 1024 ** 3, 1),
                "avail_gb": round(m.ullAvailPhys / 1024 ** 3, 1),
                "load_pct": int(m.dwMemoryLoad)}
    except Exception as e:
        return {"error": str(e)}


def probe_disks():
    out = []
    for letter in "CDEFGH":
        root = letter + ":\\"
        if not os.path.exists(root):
            continue
        try:
            u = shutil.disk_usage(root)   # 命名元组是 (total, used, free)
            out.append({"drive": letter, "total_gb": round(u.total / 1024 ** 3, 1),
                        "free_gb": round(u.free / 1024 ** 3, 1)})
        except Exception:
            pass
    return out


def probe_python():
    info = {"version": platform.python_version(),
            "executable": sys.executable,
            "bits": 64 if sys.maxsize > 2 ** 32 else 32}
    # 卡真正要的三样：numpy（向量）、sqlite3（记忆）、psutil（监控）
    for mod in ("numpy", "sqlite3", "psutil", "PIL", "requests"):
        try:
            __import__(mod)
            info[mod] = True
        except Exception:
            info[mod] = False
    try:
        import numpy
        info["numpy_version"] = numpy.__version__
    except Exception:
        pass
    return info


def probe_node():
    """DSH 是 node 应用；插件市场也要 node/pnpm。"""
    info = {}
    for name, args, need_shell in (
            ("node", ["node", "--version"], False),
            ("npm", ["npm", "--version"], True),
            ("pnpm", ["pnpm", "--version"], True),
            ("git", ["git", "--version"], False)):
        out = _run(args, shell=need_shell)
        info[name] = out or None
    return info


def probe_ffmpeg():
    exe = shutil.which("ffmpeg")
    local = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "runtime", "ffmpeg", "ffmpeg.exe")
    return {"on_path": exe, "in_bundle": os.path.exists(local),
            "usable": bool(exe or os.path.exists(local))}


def probe_gpu():
    """nvidia-smi 拿不到就用 wmic 兜底。

    真正要看的不是「有没有显卡」，是 **compute capability**：
    Pascal（6.1，P104/GTX1080）没有张量核心，且 fp16 只有 1/64 速度；
    很多新 wheel 不带 sm_61，装上去导入就崩。
    """
    out = {"nvidia_smi": shutil.which("nvidia-smi")}
    smi = _run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version,compute_cap",
                "--format=csv,noheader"], timeout=25)
    if smi and "failed" not in smi.lower():
        cards = []
        for line in smi.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 4:
                cards.append({"name": parts[0], "vram": parts[1],
                              "driver": parts[2], "compute_cap": parts[3]})
        out["cards"] = cards
    else:
        raw = _run(["wmic", "path", "win32_videocontroller", "get",
                    "name,driverversion", "/format:csv"])
        cards = []
        for line in raw.splitlines():
            cells = [c.strip() for c in line.split(",")]
            if len(cells) < 3 or not cells[0] or cells[0].upper() == "NODE":
                continue
            cards.append({"name": cells[-1], "driver": cells[-2]})
        out["cards"] = cards
        out["note"] = "没有 nvidia-smi：要么是 A 卡/核显，要么驱动没装"
    return out


def probe_torch():
    """torch 在不在、支不支持这张卡的算力。

    这是最容易被坑的一条：装了个新 wheel，`import torch` 成功、
    `torch.cuda.is_available()` 也 True，但跑算子才报
    「no kernel image is available for execution on the device」。
    所以直接问它编进去的架构列表。
    """
    try:
        import torch
    except Exception as e:
        return {"installed": False, "why": str(e).splitlines()[0][:120]}
    info = {"installed": True, "version": torch.__version__,
            "cuda_build": getattr(torch.version, "cuda", None),
            "cuda_available": bool(getattr(torch.cuda, "is_available", lambda: False)())}
    try:
        arch = torch.cuda.get_arch_list()
        info["arch_list"] = arch
        info["has_sm61"] = any(a in ("sm_61", "sm_60") for a in arch)
    except Exception:
        pass
    try:
        if info.get("cuda_available"):
            info["device_name"] = torch.cuda.get_device_name(0)
            info["device_capability"] = "%d.%d" % torch.cuda.get_device_capability(0)
    except Exception:
        pass
    return info


def probe_time():
    """时间服务。

    卡里所有衰减都按真实流逝秒数算：
      - 系统时间往未来跳 → t_anchor 落到未来 → 记忆被「冻结」（永不衰减）
      - 系统时间往过去跳 → 记忆被「加速遗忘」
    比缺个依赖严重得多，而且不会报错。
    """
    info = {}
    try:
        p = subprocess.run(["sc", "query", "w32time"], capture_output=True,
                           text=True, timeout=15, encoding="utf-8", errors="replace")
        txt = (p.stdout or "").upper()
        info["service_state"] = ("RUNNING" if "RUNNING" in txt
                                 else "STOPPED" if "STOPPED" in txt else "UNKNOWN")
    except Exception as e:
        info["service_state"] = "err:%s" % e
    try:
        p = subprocess.run(["w32tm", "/query", "/status"], capture_output=True,
                           text=True, timeout=15, encoding="gbk", errors="replace")
        txt = p.stdout or ""
        for line in txt.splitlines():
            if "源" in line or "Source" in line:
                info["source"] = line.strip()
        info["queried"] = True
    except Exception:
        info["queried"] = False
    return info


def probe_card():
    """卡自己的东西：包里的 runtime、模型、card.db。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    info = {"root": root}
    info["card_db"] = os.path.exists(os.path.join(root, "card.db"))
    if info["card_db"]:
        info["card_db_mb"] = round(os.path.getsize(os.path.join(root, "card.db")) / 1024 ** 2, 1)
        wal = os.path.join(root, "card.db-wal")
        info["wal_mb"] = round(os.path.getsize(wal) / 1024 ** 2, 1) if os.path.exists(wal) else 0
    info["portable_python"] = os.path.exists(os.path.join(root, "runtime", "python", "python.exe"))
    models = os.path.join(root, "models")
    info["models"] = []
    if os.path.isdir(models):
        for name in sorted(os.listdir(models)):
            d = os.path.join(models, name)
            if os.path.isdir(d):
                size = sum(os.path.getsize(os.path.join(dp, f))
                           for dp, _, fs in os.walk(d) for f in fs)
                info["models"].append({"name": name, "mb": round(size / 1024 ** 2, 1)})
    return info


def probe_dsh():
    """DSH 装没装、在哪。"""
    info = {"found": False}
    for p in (r"workspace\DeepSeek Harness.exe",
              os.path.expandvars(r"%LOCALAPPDATA%\Programs\DeepSeek Harness\DeepSeek Harness.exe"),
              os.path.expandvars(r"%PROGRAMFILES%\DeepSeek Harness\DeepSeek Harness.exe")):
        if os.path.exists(p):
            info["found"] = True
            info["path"] = p
            break
    home = os.path.expanduser("~/.dsh")
    info["profile_dir"] = home if os.path.isdir(home) else None
    return info


def summarize(r):
    """把「缺什么」翻成人话，按「卡起不起得来」排优先级。"""
    must, nice = [], []
    py = r["python"]
    if not py.get("numpy"):
        must.append("numpy —— 向量检索全靠它，缺了卡只能关键词召回")
    if not py.get("sqlite3"):
        must.append("sqlite3 —— 记忆本体就是 sqlite，缺了开不了卡")
    if not py.get("psutil"):
        nice.append("psutil —— 只影响控制台的 CPU/内存曲线")
    if not r["ffmpeg"]["usable"]:
        nice.append("ffmpeg —— 语音转 wav、视频抽帧用；没有就只是这两样不可用")
    if not r["node"]["node"]:
        nice.append("node —— 只有要装 DSH 才需要")
    if not r["dsh"]["found"]:
        nice.append("DSH —— 想在这台机器上跟我说话就得装")
    t = r["time"].get("service_state")
    if t != "RUNNING":
        must.append("时间服务（w32time %s）—— 时钟一飘，记忆会加速遗忘或冻结，且不报错" % t)
    gpus = r["gpu"].get("cards") or []
    if gpus and "compute_cap" in gpus[0]:
        cap = gpus[0]["compute_cap"]
        if cap.startswith("6."):
            must.append("显卡是 Pascal（算力 %s，没有张量核心，fp16 只有 1/64）："
                        "torch 必须自己带 sm_61，否则装完能 import 但跑不了" % cap)
    return {"blocking": must, "optional": nice}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="只吐 JSON")
    a = ap.parse_args(argv)

    r = {"os": probe_os(), "mem": probe_mem(), "disks": probe_disks(),
         "python": probe_python(), "node": probe_node(), "ffmpeg": probe_ffmpeg(),
         "gpu": probe_gpu(), "torch": probe_torch(), "time": probe_time(),
         "card": probe_card(), "dsh": probe_dsh()}
    r["summary"] = summarize(r)

    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0

    o = r["os"]
    print("=" * 60)
    print("环境体检 · %s" % o.get("hostname"))
    print("=" * 60)
    print("系统    %s %s (%s)" % (o.get("system"), o.get("release"), o.get("machine")))
    print("CPU     %s" % o.get("cpu"))
    m = r["mem"]
    print("内存    %s GB（可用 %s GB，已用 %s%%）"
          % (m.get("total_gb"), m.get("avail_gb"), m.get("load_pct")))
    print("磁盘    " + "  ".join("%s: 剩 %.1f / %.1f GB"
                                % (d["drive"], d["free_gb"], d["total_gb"])
                                for d in r["disks"]))
    py = r["python"]
    print("\nPython  %s (%s 位)  %s" % (py["version"], py["bits"], py["executable"]))
    print("        numpy=%s sqlite3=%s psutil=%s PIL=%s requests=%s"
          % (py.get("numpy"), py.get("sqlite3"), py.get("psutil"),
             py.get("PIL"), py.get("requests")))
    n = r["node"]
    print("\nNode    %s / npm %s / pnpm %s / git %s"
          % (n.get("node"), n.get("npm"), n.get("pnpm"), n.get("git")))
    f = r["ffmpeg"]
    print("ffmpeg  %s" % ("ok (%s)" % (f["on_path"] or "包里") if f["usable"] else "缺"))
    print("\n显卡")
    for c in (r["gpu"].get("cards") or []):
        print("        %s  显存 %s  驱动 %s  算力 %s"
              % (c.get("name"), c.get("vram", "?"), c.get("driver", "?"),
                 c.get("compute_cap", "?")))
    if r["gpu"].get("note"):
        print("        (%s)" % r["gpu"]["note"])
    t = r["torch"]
    if t.get("installed"):
        print("\ntorch   %s  cuda=%s  可用=%s  arch=%s  sm_61=%s"
              % (t.get("version"), t.get("cuda_build"), t.get("cuda_available"),
                 ",".join(t.get("arch_list") or []) or "?", t.get("has_sm61")))
    else:
        print("\ntorch   没装（正常：卡的向量检索是纯 numpy，不需要它）")
    print("\n时间服务  %s" % r["time"].get("service_state"))
    if r["time"].get("source"):
        print("          %s" % r["time"]["source"])
    c = r["card"]
    print("\n卡      %s" % c["root"])
    print("        card.db=%s (%s MB)  wal=%s MB  便携python=%s"
          % (c["card_db"], c.get("card_db_mb"), c.get("wal_mb"), c["portable_python"]))
    print("        模型：%s" % (", ".join("%s(%.0fMB)" % (x["name"], x["mb"])
                                          for x in c["models"]) or "无"))
    d = r["dsh"]
    print("\nDSH     %s" % (d.get("path") or "没装"))
    print("        profile: %s" % (d.get("profile_dir") or "无"))

    s = r["summary"]
    print("\n" + "=" * 60)
    if s["blocking"]:
        print("必须先解决（不然卡起不来或会悄悄出错）：")
        for x in s["blocking"]:
            print("  ✗ " + x)
    else:
        print("没有阻塞项。")
    if s["optional"]:
        print("\n可选（缺了只是少功能）：")
        for x in s["optional"]:
            print("  · " + x)
    print("=" * 60)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
