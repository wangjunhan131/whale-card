# -*- coding: utf-8 -*-
"""replica.py 的验收：全在临时目录里跑，不碰真卡。"""
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# 源卡：本机有真卡就用真卡（只读），没有就退回示例卡 ——
# 开源仓库里不带 card.db，测试要能在示例卡上跑起来。
# 注意：只认「有 mem 表」的卡。timeline 表是开机时才建的，
# 刚造出来的示例卡本来就没有它，那不是坏卡。
def _usable(p):
    if not os.path.exists(p):
        return False
    try:
        con = sqlite3.connect("file:%s?mode=ro" % p.replace("\\", "/"), uri=True)
        try:
            names = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            return "mem" in names
        finally:
            con.close()
    except Exception:
        return False


_CANDS = [os.path.join(ROOT, "card.db"),
          os.path.join(ROOT, "demo", "demo_card.db")]
REAL = next((p for p in _CANDS if _usable(p)), "")
TMP = tempfile.mkdtemp(prefix="replica_", dir=ROOT)
MAIN = os.path.join(TMP, "main.db")
BK = os.path.join(TMP, "backup.db")

ok = 0
bad = []


def t(name, cond, extra=""):
    global ok
    if cond:
        ok += 1
        print("  ✅ %s" % name)
    else:
        bad.append(name)
        print("  ❌ %s %s" % (name, extra))


def snap(src, dst):
    s = sqlite3.connect(src)
    d = sqlite3.connect(dst)
    s.backup(d)
    d.commit()
    d.close()
    s.close()


def run(*args):
    p = subprocess.run([sys.executable, "-X", "utf8",
                        os.path.join(ROOT, "tools", "replica.py")] + list(args),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def append_event(db, note):
    """往某张卡的链上追加一条事件（模拟"这台机器又写了东西"）。"""
    import timeline
    con = sqlite3.connect(db, timeout=30.0)
    con.row_factory = sqlite3.Row
    try:
        tl = timeline.Timeline(con, writer="w-test")
        # 副本的链头是本机 writer 打的标，这里只是在造"多写了一节"的场景，
        # 所以显式放行（allow_foreign），否则会被写入闸挡住。
        tl.append("test", note, allow_foreign=True)
        return tl.head_hash()
    finally:
        con.close()


try:
    if not REAL:
        print("找不到可用的源卡（card.db 或 demo/demo_card.db），"
              "先跑 python tools/make_demo_card.py")
        sys.exit(2)
    # 造两张同源卡
    snap(REAL, MAIN)
    snap(REAL, BK)
    print("源卡 %s" % REAL)
    print("临时目录 %s" % TMP)

    # 1) 一致
    rc, out = run("--db", MAIN, "status", BK)
    t("两边一致时 status 报 same", rc == 0 and "一致" in out, out[-200:])

    # 2) 备份落后 → status 说 behind，backup 能补
    append_event(MAIN, "main 又写了一条")
    rc, out = run("--db", MAIN, "status", BK)
    t("备份落后时 status 报 behind", "落后" in out and "可以安全补齐" in out, out[-300:])

    # 2b) 不带 --confirm 只预演，不动手
    before = os.path.getsize(BK)
    rc, out = run("--db", MAIN, "backup", BK)
    t("不带 --confirm 只预演",
      "确认请加 --confirm" in out and os.path.getsize(BK) == before, out[-200:])

    rc, out = run("--db", MAIN, "backup", BK, "--confirm")
    t("落后时 backup 成功且校验一致", rc == 0 and "校验一致" in out, out[-300:])
    rc, out = run("--db", MAIN, "status", BK)
    t("补完之后两边一致", "一致" in out, out[-200:])

    # 3) 备份超前 → 拒绝 backup，提示 adopt
    append_event(BK, "备份那边多写了一条")
    rc, out = run("--db", MAIN, "status", BK)
    t("备份超前时 status 报 ahead", "超前" in out, out[-300:])
    rc, out = run("--db", MAIN, "backup", BK, "--confirm")
    t("备份超前时 backup 拒绝", rc == 3 and "拒绝" in out, out[-300:])
    rc, out = run("--db", MAIN, "adopt", BK, "--confirm")
    t("备份超前时 adopt 允许（把新的升成活卡）",
      rc == 0 and "已换家" in out, out[-400:])
    rc, out = run("--db", MAIN, "status", BK)
    t("adopt 之后活卡与备份一致", "一致" in out, out[-200:])

    # 4) 分叉 → 两边都拒绝
    append_event(MAIN, "分叉 A")
    append_event(BK, "分叉 B")
    rc, out = run("--db", MAIN, "status", BK)
    t("分叉时 status 报 diverged", "分叉" in out, out[-300:])
    rc, out = run("--db", MAIN, "backup", BK, "--confirm")
    t("分叉时 backup 拒绝", rc == 3, out[-200:])
    rc, out = run("--db", MAIN, "adopt", BK, "--confirm")
    t("分叉时 adopt 拒绝", rc == 3, out[-200:])

    # 5) adopt 留了冷备
    cold = [f for f in os.listdir(TMP) if ".cold-" in f]
    t("adopt 把旧活卡留成冷备", len(cold) >= 1, str(cold))

    print()
    print("结果：%d/%d 通过" % (ok, ok + len(bad)))
    if bad:
        print("失败：%s" % "、".join(bad))
        sys.exit(1)
finally:
    try:
        shutil.rmtree(TMP, ignore_errors=True)
    except Exception:
        pass
