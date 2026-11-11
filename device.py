"""这台机器是哪一份：三元分离里的 device_id（V7 验收规格 §5）。

规格要求 identity_id / instance_id / device_id 各管一件事：
  identity_id —— 「谁」（同一个角色，跨卡、跨机器都认这个人）
  instance_id —— 「这一次加载的哪张卡」（WG-XXXX，换卡就换）
  device_id   —— 「哪台机器」（同一台机器上重启不变）
之前只有前两个：把卡拷到另一台机器上，谁也看不出「这不是原来那台机器」。

device_id 用 Windows 的 MachineGuid（重装系统才会变）派生，不采集任何
硬件序列号；拿不到就退回 hostname + 安装目录的哈希 —— 宁可换一个不稳的
id，也不要因为读不到注册表就让启动失败。
"""
import hashlib
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(ROOT, "device.json")


def _machine_seed():
    """能稳定代表「这台机器」的种子，以及它的来源（写进文件方便排查）。"""
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           r"SOFTWARE\Microsoft\Cryptography")
        v, _ = winreg.QueryValueEx(k, "MachineGuid")
        return str(v), "machine-guid"
    except Exception:
        _guid_err = True      # 读不到注册表就往下走，不因为这条让启动失败
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            with open(p, "r", encoding="utf-8") as f:
                s = f.read().strip()
            if s:
                return s, "machine-id"
        except Exception:
            _id_err = True
    return "%s|%s" % (os.environ.get("COMPUTERNAME") or "host", ROOT), "hostname"


def _load():
    try:
        with open(PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and d.get("device_id"):
            return d
    except Exception:
        _load_err = True
    return None


def device_id(refresh=False):
    """这台机器的稳定 id（同一台机器上重启不变，换机器才变）。"""
    d = None if refresh else _load()
    if d is not None:
        return str(d["device_id"])
    seed, src = _machine_seed()
    did = "dev-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    rec = {"device_id": did, "source": src,
           "host": os.environ.get("COMPUTERNAME") or "unknown",
           "first_seen": time.time()}
    try:
        tmp = PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=1)
        os.replace(tmp, PATH)
    except Exception as e:
        sys.stderr.write("[device] 写不了 device.json（%s），本次只放在内存里\n" % e)
    return did


if __name__ == "__main__":
    print(device_id(), _load())
