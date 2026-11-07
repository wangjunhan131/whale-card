# -*- coding: utf-8 -*-
"""t104：接回来（come_back）的验收。

不碰真卡：整个流程跑在临时目录里的两份「假卡」上（都是真 card.db 的副本）。
覆盖：
  1. db_info 读得出条数/链头
  2. U 盘那份更新 → 搬回来，本机条数与链头变成 U 盘的
  3. 本机旧卡先被备份成 card.db.bak-before-comeback-*
  4. 本机退役标记被清掉、U 盘被打上退役
  5. U 盘那份更旧 → 拒绝（不覆盖），除非 --force
  6. 两份同 seq 不同哈希（真分叉）→ 拒绝
  7. --dry 什么都不动
  8. 找 U 盘的默认路径逻辑（找不到就明确报错，不瞎猜）
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REAL_DB = os.path.join(ROOT, "card.db")
TOOL = os.path.join(ROOT, "tools", "come_back.py")

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

passed = 0
failed = 0


def chk(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print("  ✅ %s" % name)
    else:
        failed += 1
        print("  ❌ %s %s" % (name, extra))


def copy_card(src_dir, dst_dir):
    os.makedirs(dst_dir, exist_ok=True)
    shutil.copy2(os.path.join(src_dir, "card.db"), os.path.join(dst_dir, "card.db"))
    for ext in ("-wal", "-shm"):
        p = os.path.join(src_dir, "card.db" + ext)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(dst_dir, "card.db" + ext))


def db_stat(d):
    c = sqlite3.connect("file:" + os.path.join(d, "card.db").replace("\\", "/")
                        + "?mode=ro", uri=True, timeout=30)
    try:
        n = c.execute("SELECT count(*) FROM mem").fetchone()[0]
        s = c.execute("SELECT max(seq) FROM timeline").fetchone()[0]
        return n, s
    finally:
        c.close()


def bump(db_dir, note):
    """往 copy 里追加一条 timeline 事件，让它「更新」。"""
    c = sqlite3.connect(os.path.join(db_dir, "card.db"), timeout=30)
    try:
        row = c.execute("SELECT seq, self_hash FROM timeline ORDER BY seq DESC LIMIT 1").fetchone()
        seq = (row[0] if row else 0) + 1
        prev = row[1] if row else ""
        import hashlib
        payload = json.dumps({"kind": "talk", "note": note, "prev": prev},
                             ensure_ascii=False, sort_keys=True)
        h = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        c.execute("INSERT INTO timeline (seq, ts, kind, note, prev_hash, self_hash) "
                  "VALUES (?,?,?,?,?,?)",
                  (seq, 1794000000 + seq, "talk", note, prev, h))
        c.commit()
    finally:
        c.close()


def run(args, env_extra=None, timeout=120):
    env = dict(os.environ)
    env.update(env_extra or {})
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run([sys.executable, "-X", "utf8", TOOL] + args,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, timeout=timeout)


print("==============================================================")
print("  t104  接回来")
print("==============================================================")

tmp = tempfile.mkdtemp(prefix="t104_")
try:
    main = os.path.join(tmp, "main")
    usb = os.path.join(tmp, "usb")
    copy_card(ROOT, main)
    copy_card(ROOT, usb)

    print("\n== 一、db_info / 基本读数 ==")
    n0, s0 = db_stat(main)
    chk("真卡副本读得出来", n0 > 500 and s0 > 100, "(%s, %s)" % (n0, s0))
    r = run(["--dry", usb], {"WHALE_CARD_ROOT": main})
    chk("--dry 能跑完", r.returncode == 0, r.stderr[-200:])
    chk("--dry 里看得到两边条数", "本机" in r.stdout and "U盘" in r.stdout)
    n1, s1 = db_stat(main)
    chk("--dry 没动本机", (n0, s0) == (n1, s1))

    print("\n== 二、U 盘更新 → 搬回来 ==")
    bump(usb, "在外面写的这一条")
    bump(usb, "再写一条")
    _, s_usb = db_stat(usb)
    chk("U 盘确实比本机新", s_usb > s0, "(%s vs %s)" % (s_usb, s0))
    # 本机造一个退役标记（模拟「带卡出门」过）
    open(os.path.join(main, "RETIRED.json"), "w", encoding="utf-8").write(
        json.dumps({"retired": True, "why": "测试"}, ensure_ascii=False))
    r = run(["--no-start", usb], {"WHALE_CARD_ROOT": main})
    chk("搬回来了（退出码 0）", r.returncode == 0, r.stdout[-400:] + r.stderr[-200:])
    n2, s2 = db_stat(main)
    chk("本机链头跟着走", s2 == s_usb, "(%s vs %s)" % (s2, s_usb))
    chk("本机条数跟 U 盘一致", n2 == db_stat(usb)[0])
    baks = [f for f in os.listdir(main)
            if f.startswith("card.db.bak-before-comeback-")
            and not f.endswith(("-wal", "-shm"))]
    chk("旧卡备份留下来了", len(baks) == 1, str(baks))
    chk("本机退役标记被清掉", not os.path.exists(os.path.join(main, "RETIRED.json")))
    chk("U 盘被打上退役", os.path.exists(os.path.join(usb, "RETIRED.json")))
    if os.path.exists(os.path.join(usb, "RETIRED.json")):
        info = json.load(open(os.path.join(usb, "RETIRED.json"), encoding="utf-8"))
        chk("U 盘退役原因写得清楚", "接回来" in (info.get("why") or ""), str(info))

    print("\n== 三、U 盘更旧 → 拒绝 ==")
    tmp2 = tempfile.mkdtemp(prefix="t104b_")
    try:
        main2 = os.path.join(tmp2, "main")
        usb2 = os.path.join(tmp2, "usb")
        copy_card(ROOT, main2)
        copy_card(ROOT, usb2)
        bump(main2, "本机更新")
        n_before, s_before = db_stat(main2)
        r = run(["--no-start", usb2], {"WHALE_CARD_ROOT": main2})
        chk("旧的那份不许覆盖", r.returncode == 2, "rc=%s" % r.returncode)
        chk("本机一个字没动", db_stat(main2) == (n_before, s_before))
        chk("说清了「比本机旧」", "比本机旧" in r.stdout)
        chk("没留备份（没动就不用备份）",
            not any(f.startswith("card.db.bak-before-comeback-")
                    for f in os.listdir(main2)))
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)

    print("\n== 四、同 seq 不同哈希（真分叉）→ 拒绝 ==")
    tmp3 = tempfile.mkdtemp(prefix="t104c_")
    try:
        main3 = os.path.join(tmp3, "main")
        usb3 = os.path.join(tmp3, "usb")
        copy_card(ROOT, main3)
        copy_card(ROOT, usb3)
        # 两边各自追加一条不同内容：seq 一样，哈希不同 = 真分叉
        bump(main3, "本机这条")
        bump(usb3, "U 盘那条")
        chk("造出了同 seq 不同哈希",
            db_stat(main3)[1] == db_stat(usb3)[1], "%s vs %s"
            % (db_stat(main3)[1], db_stat(usb3)[1]))
        r = run(["--no-start", usb3], {"WHALE_CARD_ROOT": main3})
        chk("真分叉会停下来", r.returncode == 2, "rc=%s" % r.returncode)
        chk("说了这是真分叉", "真分叉" in r.stdout or "对不上" in r.stdout)
    finally:
        shutil.rmtree(tmp3, ignore_errors=True)

    print("\n== 五、找不到 U 盘 → 明确报错 ==")
    r = run([os.path.join(tmp, "nowhere")], {"WHALE_CARD_ROOT": main})
    chk("找不到就报错不瞎猜", r.returncode == 1, "rc=%s" % r.returncode)
    chk("告诉怎么给路径", "接回来.bat" in r.stdout)

    print("\n== 六、脚本与 bat 的约定 ==")
    src = open(TOOL, encoding="utf-8").read()
    chk("用了 backup API 而不是直接拷", "backup(" in src)
    chk("搬之前先备份本机", "bak-before-comeback" in src)
    chk("有 WHALE_CARD_ROOT 测试钩子", "WHALE_CARD_ROOT" in src)
    bat = os.path.join(ROOT, "接回来.bat")
    chk("接回来.bat 存在", os.path.exists(bat))
    if os.path.exists(bat):
        raw = open(bat, "rb").read()
        chk("bat 里没有非 ASCII 字节（cmd 会整段不执行）",
            all(b < 128 for b in raw), "非 ASCII %d 个" % sum(1 for b in raw if b >= 128))
        chk("bat 最后有 pause", "pause" in raw.decode("ascii", "ignore"))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("==============================================================")
print("  过 %d / 挂 %d" % (passed, failed))
print("==============================================================")
sys.exit(1 if failed else 0)
