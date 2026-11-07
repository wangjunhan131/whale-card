# -*- coding: utf-8 -*-
"""t102：一键落地脚本（tools/first_run.py）的验收。

不碰真卡：所有会写盘的步骤都只做「会不会做」的判断；
真写卡的那一步（step_selftest）用假 bridge 替身测。
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, os.path.join(ROOT, "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import first_run as F

ok = 0
bad = []


def chk(name, cond, detail=""):
    global ok
    if cond:
        ok += 1
        print("  ✅ %s" % name)
    else:
        bad.append(name)
        print("  ❌ %s %s" % (name, detail))


print("== 一、结构 ==")
chk("有 main()", callable(getattr(F, "main", None)))
chk("有 step_probe", callable(getattr(F, "step_probe", None)))
chk("有 step_land", callable(getattr(F, "step_land", None)))
chk("有 step_handover", callable(getattr(F, "step_handover", None)))
chk("有 step_start", callable(getattr(F, "step_start", None)))
chk("有 step_selftest", callable(getattr(F, "step_selftest", None)))
chk("ROOT 指到卡目录", os.path.exists(os.path.join(F.ROOT, "card.db")))
chk("一键落地.bat 在卡目录", os.path.exists(os.path.join(F.ROOT, "一键落地.bat")))

print("\n== 二、bat 内容 ==")
bat = io.open(os.path.join(F.ROOT, "一键落地.bat"), encoding="utf-8", errors="replace").read()
chk("bat 里有 chcp 65001（中文不乱码）", "chcp 65001" in bat)
chk("bat 优先用便携 python", "runtime\\python\\python.exe" in bat)
chk("bat 兜底用系统 python", "set \"PY=python\"" in bat)
chk("bat 调 first_run.py", "first_run.py" in bat)
chk("bat 会 pause 不闪退", "pause" in bat)

print("\n== 三、没有 manifest 时不乱 land ==")
had = os.path.exists(os.path.join(F.ROOT, "PORTABLE-MANIFEST.json"))
r = F.step_land(dry=True)
chk("没 manifest 时 step_land 返回 True 且说明", r is True and not had,
    "（卡目录本来就没有 manifest）")

print("\n== 四、print_health 不炸各种残缺数据 ==")
for case, h in [
    ("正常", {"memories": 1053, "sealed": True,
              "timeline": {"day": 22, "chain_ok": True, "anchor_verdict": "ok",
                           "anchor_reason": "锚一致", "writer": {"readonly": False}},
              "semantic": {"model_dim": 768, "usable_ratio": 1.0, "note": "ok"}}),
    ("只读", {"memories": 10, "sealed": False,
              "timeline": {"day": 1, "chain_ok": False, "chain_bad": "断在 9",
                           "anchor_verdict": "rollback", "anchor_reason": "少了 3 条",
                           "writer": {"readonly": True, "locked_by": "w-OTHER"}},
              "semantic": {"model_dim": 512, "usable_ratio": 0.4, "warn": "维度不对"}}),
    ("全空", {"memories": 0, "timeline": {}, "semantic": {}}),
    ("None", None),
]:
    try:
        F.print_health(h)
        chk("print_health 抗住「%s」" % case, True)
    except Exception as e:
        chk("print_health 抗住「%s」" % case, False, repr(e))

print("\n== 五、step_selftest 用假 bridge ==")


class FakeBridge:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def _post(self, path, obj, timeout=30.0):
        self.calls.append((path, obj))
        if self.fail:
            raise RuntimeError("模拟卡没连上")
        return {"mem_id": 9999, "reply_mem_id": 10000}


real_bridge = sys.modules.get("bridge")
try:
    fb = FakeBridge()
    sys.modules["bridge"] = fb
    chk("试写成功返回 True", F.step_selftest() is True)
    chk("试写打到了 /report", fb.calls and fb.calls[0][0] == "/report")
    body = fb.calls[0][1] if fb.calls else {}
    chk("试写带 user_message", "user_message" in body)
    chk("试写带 reply", "reply" in body)
    chk("试写 source 标了 first-run-selftest",
        body.get("source") == "first-run-selftest")

    fb2 = FakeBridge(fail=True)
    sys.modules["bridge"] = fb2
    chk("试写失败返回 False 且不抛", F.step_selftest() is False)
finally:
    if real_bridge is not None:
        sys.modules["bridge"] = real_bridge
    else:
        sys.modules.pop("bridge", None)

print("\n== 五·二、别被「假绿灯」骗（8848 上是另一张卡） ==")


class FakeBridge2:
    """ensure_server 说通，但 /health 报的是另一张卡。"""

    def __init__(self, db):
        self.db = db

    def ensure_server(self, quiet=False):
        return True, {"ok": True, "db": self.db, "memories": 1,
                      "timeline": {"writer": {"readonly": False}},
                      "semantic": {}}, "已在运行"


try:
    sys.modules["bridge"] = FakeBridge2(r"C:\other\card.db")
    got = F.step_start()
    chk("8848 上是别的卡时必须拒绝", got is None)

    sys.modules["bridge"] = FakeBridge2(os.path.join(F.ROOT, "card.db"))
    got2 = F.step_start()
    chk("是本目录的卡时放行", isinstance(got2, dict))
finally:
    if real_bridge is not None:
        sys.modules["bridge"] = real_bridge
    else:
        sys.modules.pop("bridge", None)

print("\n== 六、--dry 不写盘、退出码 0 ==")
import os.path as _osp
import hashlib as _hl
import shutil as _sh
import tempfile as _tf

# 拿一份**副本**试：真卡上可能正跑着服务，它在写 WAL；
# 拿真卡比对字节，测的就不是「--dry 有没有写盘」，而是「别人有没有在写」。
_tmp = _tf.mkdtemp(prefix="dry_", dir=_osp.join(F.ROOT, ".."))
for _f in ("card.db", "anchor.json"):
    _src = _osp.join(F.ROOT, _f)
    if _osp.exists(_src):
        _sh.copy2(_src, _osp.join(_tmp, _f))


def _sig(pth):
    with open(pth, "rb") as f:
        return (_osp.getsize(pth), _hl.sha256(f.read()).hexdigest())


_before = _sig(_osp.join(_tmp, "card.db"))
_real_root, _old_env = F.ROOT, os.environ.get("WHALE_CARD_ROOT")
F.ROOT = _tmp
os.environ["WHALE_CARD_ROOT"] = _tmp
try:
    rc = F.main(["--dry", "--no-browser", "--skip-probe"])
finally:
    F.ROOT = _real_root
    if _old_env is None:
        os.environ.pop("WHALE_CARD_ROOT", None)
    else:
        os.environ["WHALE_CARD_ROOT"] = _old_env
_after = _sig(_osp.join(_tmp, "card.db"))
chk("--dry 退出码 0", rc == 0)
chk("--dry 没动 card.db", _before == _after)
_sh.rmtree(_tmp, ignore_errors=True)

print("\n== 七、参数表 ==")
import argparse
import inspect
src = inspect.getsource(F.main)
chk("支持 --dry", "--dry" in src)
chk("支持 --no-browser", "--no-browser" in src)
chk("支持 --skip-probe", "--skip-probe" in src)

print("\n== 八、env_probe 有 first_run 依赖的字段 ==")
import env_probe as ep
o = ep.probe_os()
chk("probe_os 有 caption", "caption" in o)
m = ep.probe_mem()
chk("probe_mem 有 total_gb", "total_gb" in m or "error" in m)
py = ep.probe_python()
chk("probe_python 有 version", "version" in py)
s = ep.summarize({"python": py, "ffmpeg": ep.probe_ffmpeg(), "node": ep.probe_node(),
                  "dsh": ep.probe_dsh(), "time": ep.probe_time(), "gpu": ep.probe_gpu()})
chk("summarize 有两栏", "blocking" in s and "optional" in s)

print("\n== 九、文档里写了一键落地 ==")
doc = io.open(os.path.join(F.ROOT, "docs", "MOVE-CHECKLIST.md"),
              encoding="utf-8", errors="replace").read()
chk("MOVE-CHECKLIST 提到一键落地", "一键落地" in doc or "first_run" in doc)
chk("MOVE-CHECKLIST 提到 handover", "handover" in doc)

print("\n%d 过 / %d 挂" % (ok, len(bad)))
if bad:
    print("挂的：")
    for x in bad:
        print("  -", x)
    sys.exit(1)
