# -*- coding: utf-8 -*-
"""t103：带卡出门（退役）+ 接入 DSH。

测的是今晚最容易出事的一层：
    两份拷贝各自是完整的链、各自都能写 —— 事后合不回来。
    writer 锁只挡「同一个文件被两台机器写」，挡不住「两个文件各写各的」。
    所以需要「退役」：明确把这一份钉成只读。

跑之前不动真卡：全部在 card.db 的副本上起服务。
"""
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _cardcopy  # noqa: E402
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import disks as dk  # noqa: E402

passed = 0
failed = []


def chk(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print("  \u2705 %s" % name)
    else:
        failed.append(name)
        print("  \u274c %s  %s" % (name, detail))


def post(base, path, body, timeout=60):
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def get(base, path, timeout=30):
    with urllib.request.urlopen(base + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


print("\n== 一、RETIRED.json 本身 ==")
tmp = tempfile.mkdtemp(prefix="t103_")
try:
    chk("刚拿到手的目录没有退役标记", dk.read_retired(tmp) is False)
    chk("retired_info 也没内容", dk.retired_info(tmp) in (None, {}, False)
        or not dk.retired_info(tmp).get("on"))

    r = dk.set_retired(tmp, True, "带卡出门测试")
    chk("打上退役标记返回成功", bool(r))
    chk("文件叫 RETIRED.json", os.path.exists(os.path.join(tmp, "RETIRED.json")))
    chk("读回来是 True", dk.read_retired(tmp) is True)
    info = dk.retired_info(tmp) or {}
    chk("info 带上了原因", "带卡出门测试" in json.dumps(info, ensure_ascii=False))

    dk.set_retired(tmp, False)
    chk("解除后文件被删掉", not os.path.exists(os.path.join(tmp, "RETIRED.json")))
    chk("解除后读回来是 False", dk.read_retired(tmp) is False)

    # 坏文件不能把服务搞崩
    with io.open(os.path.join(tmp, "RETIRED.json"), "w", encoding="utf-8") as f:
        f.write("{这不是合法 json")
    chk("RETIRED.json 坏掉时当成已退役（宁可只读也不乱写）",
        dk.read_retired(tmp) is True)
    dk.set_retired(tmp, False)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n== 二、可移动盘用 DELETE 日志，不用 WAL ==")
src = io.open(os.path.join(ROOT, "card2.py"), encoding="utf-8").read()
chk("按盘类型选日志模式（PRAGMA journal_mode=%s）", "journal_mode=%s" in src)
chk("用 GetDriveTypeW 判盘（2=可移动 4=网络 → DELETE）",
    "GetDriveTypeW" in src and "_dt in (2, 4)" in src
    and 'DELETE' in src)

print("\n== 三、真起一个服务，验退役后写不进去 ==")
work = tempfile.mkdtemp(prefix="t103srv_")
proc = None
try:
    _cardcopy.copy_card_db(os.path.join(ROOT, "card.db"),
                        os.path.join(work, "card.db"))
    port = 8871
    env = dict(os.environ)
    env["WHALE_WRITER"] = ""
    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", "mind_server.py", "--port", str(port),
         "--db", os.path.join(work, "card.db")],
        cwd=ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = "http://127.0.0.1:%d" % port
    up = False
    for _ in range(90):
        time.sleep(1)
        try:
            get(base, "/health", timeout=5)
            up = True
            break
        except Exception:
            pass
    chk("服务起来了", up)
    if up:
        def _mem_id(resp):
            """报告接口的返回是嵌套的：{ok, report:{mem_id, reply_mem_id}}。"""
            rep = (resp or {}).get("report") or {}
            return rep.get("mem_id") or (resp or {}).get("mem_id")

        st, r = post(base, "/report", {"user_message": "退役前写一句", "reply": "好"})
        chk("退役前能写（200 + mem_id）", st == 200 and _mem_id(r), (st, r))

        st, r = post(base, "/disks/retire", {"on": True, "why": "测试带卡出门"})
        chk("POST /disks/retire 返回成功", st == 200 and r.get("ok"), (st, r))
        chk("立刻变成只读", r.get("readonly_now") is True, r)

        st, r = post(base, "/report", {"user_message": "退役后写一句", "reply": "好"})
        chk("退役后 /report 被拒（409）", st == 409, (st, r))
        chk("拒的理由是 card_locked", r.get("error") == "card_locked", r)
        chk("文案说的是「带卡出门」", "带卡出门" in json.dumps(r, ensure_ascii=False), r)

        st, r = post(base, "/remember", {"text": "退役后写记忆"})
        chk("退役后 /remember 也被拒（409）", st == 409, (st, r))

        h = get(base, "/health")
        chk("health 里 readonly=true", h["timeline"]["writer"]["readonly"] is True, h["timeline"]["writer"])
        chk("health 里 locked_by=retired",
            h["timeline"]["writer"].get("locked_by") == "retired", h["timeline"]["writer"])

        d = get(base, "/disks")
        chk("/disks 报 retired=true", d.get("retired") is True, d.get("retired"))
        chk("/disks 里有 retired_info", bool(d.get("retired_info")), d.get("retired_info"))

        st, r = post(base, "/disks/retire", {"on": False})
        chk("解除退役返回成功", st == 200 and r.get("ok"), (st, r))
        chk("文件真的被删了", not os.path.exists(os.path.join(work, "RETIRED.json")))

        st, r = post(base, "/report", {"user_message": "解除后再写一句", "reply": "好"})
        chk("解除后又能写了", st == 200 and _mem_id(r), (st, r))
finally:
    if proc is not None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
    shutil.rmtree(work, ignore_errors=True)

print("\n== 四、交棒会把退役标记清掉（不然搬过去还是只读）==")
p = os.path.join(ROOT, "tools", "portable.py")
src = io.open(p, encoding="utf-8").read()
chk("portable.cmd_handover 里调用了 set_retired(..., False)",
    "set_retired" in src and "read_retired" in src)
fr = io.open(os.path.join(ROOT, "tools", "first_run.py"), encoding="utf-8").read()
chk("first_run 交棒后也清退役", "set_retired" in fr or "read_retired" in fr)

print("\n== 五、接入 DSH 的脚本 ==")
inst = os.path.join(ROOT, "tools", "install_to_dsh.py")
chk("安装脚本存在", os.path.exists(inst))
isrc = io.open(inst, encoding="utf-8").read()
chk("会拷 dsh-plugin", "dsh-plugin" in isrc)
chk("会改 package.json 的 bundles", "bundles" in isrc)
chk("会写 cordis.patch.yml", "cordis.patch.yml" in isrc)
chk("cardDir 按这台机器写（不是死路径）", "cardDir" in isrc and "ROOT" in isrc)
chk("有 --dry", "--dry" in isrc)
chk("幂等：已经有就跳过", "已经有了" in isrc)

bat = os.path.join(ROOT, "接入DSH.bat")
chk("接入DSH.bat 存在", os.path.exists(bat))
raw = io.open(bat, "rb").read()
chk("接入DSH.bat 是纯 ASCII（cmd 对非 ASCII 的 bat 处理不一致）",
    all(b < 128 for b in raw), "有非 ASCII 字节")
chk("接入DSH.bat 会 pause（不然窗口一闪就没）", b"pause" in raw)

r = subprocess.run([sys.executable, "-X", "utf8", inst, "--dry"],
                   cwd=ROOT, capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=120)
chk("--dry 能跑完且不动东西", "彩排" in r.stdout or "已经有了" in r.stdout, r.stdout[-200:] + r.stderr[-200:])

print("\n== 六、包里带上了新东西 ==")
psrc = io.open(os.path.join(ROOT, "tools", "portable.py"), encoding="utf-8").read()
chk("INCLUDE_DIRS 里有 dsh-plugin", '"dsh-plugin"' in psrc)
chk("INCLUDE_FILES 里有一键落地.bat", "一键落地.bat" in psrc)
chk("INCLUDE_FILES 里有接入DSH.bat", "接入DSH.bat" in psrc)

print("\n过 %d / 挂 %d" % (passed, len(failed)))
if failed:
    print("挂的项：")
    for f in failed:
        print("  -", f)
sys.exit(1 if failed else 0)
