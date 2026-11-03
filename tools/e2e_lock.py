# -*- coding: utf-8 -*-
"""tools/e2e_lock.py —— 端到端验证「写入闸」：卡被别的机器占着时，/report 真的返回 409。

为什么单独一条：这件事要起真服务、加载模型（30~60 秒），放进 test_*.py 太慢。
手动/每天跑一次就够。用**卡的真副本**，绝不碰真卡。

用法：python tools/e2e_lock.py [--port 8857] [--db card.db]
"""
import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OTHER = "w-othermachine"


def _prep(src, dst_dir):
    dst = os.path.join(dst_dir, "card.db")
    con = sqlite3.connect(src)
    tgt = sqlite3.connect(dst)
    with tgt:
        con.backup(tgt)
    con.close()
    # 把链头改成"另一台机器写的"
    tgt.execute("UPDATE timeline SET writer=? WHERE seq=(SELECT MAX(seq) FROM timeline)",
                (OTHER,))
    tgt.commit()
    head = tgt.execute("SELECT seq, writer FROM timeline ORDER BY seq DESC LIMIT 1").fetchone()
    tgt.close()
    return dst, head


def _get(url, timeout=20):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode())


def _post(url, obj, timeout=60):
    req = urllib.request.Request(
        url, data=json.dumps(obj).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            body = json.loads(body)
        except Exception:
            pass
        return e.code, body


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(ROOT, "card.db"))
    ap.add_argument("--port", type=int, default=8857)
    ap.add_argument("--wait", type=int, default=150)
    args = ap.parse_args(argv)

    if not os.path.exists(args.db):
        print("找不到库：%s" % args.db)
        return 2
    tmp = tempfile.mkdtemp(prefix="e2elock_", dir=ROOT)
    proc = None
    try:
        db, head = _prep(args.db, tmp)
        print("临时卡：%s" % db)
        print("链头改成别人写的：seq=%s writer=%s" % head)

        env = dict(os.environ)
        env["WHALE_DB"] = db
        env["WHALE_MIND_PORT"] = str(args.port)
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", os.path.join(ROOT, "mind_server.py"),
             "--db", db, "--port", str(args.port)],
            cwd=ROOT, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        base = "http://127.0.0.1:%d" % args.port
        t0 = time.time()
        health = None
        while time.time() - t0 < args.wait:
            try:
                _, health = _get(base + "/health", timeout=5)
                break
            except Exception:
                time.sleep(2)
        if not health:
            print("❌ %d 秒内服务没起来" % args.wait)
            return 1
        tl = health.get("timeline") or {}
        w = tl.get("writer") or {}
        print("健康检查：locked_by=%s readonly=%s head=%s" % (
            w.get("locked_by"), w.get("readonly"), tl.get("head")))
        ok1 = (w.get("locked_by") == OTHER and w.get("readonly") is True
               and tl.get("chain_ok") is True)

        # 动手之前先数一遍：被拒之后必须一模一样
        con0 = sqlite3.connect(db)
        n_tl_before = con0.execute("SELECT COUNT(*) FROM timeline").fetchone()[0]
        n_mem_before = con0.execute("SELECT COUNT(*) FROM mem").fetchone()[0]
        con0.close()

        code, body = _post(base + "/report",
                           {"user_message": "端到端测试：这句不该写进卡",
                            "reply": "我不写"})
        print("POST /report -> %s %s" % (code, json.dumps(body, ensure_ascii=False)[:160]))
        ok2 = (code == 409 and isinstance(body, dict)
               and body.get("error") == "card_locked")

        con = sqlite3.connect(db)
        n_tl = con.execute("SELECT COUNT(*) FROM timeline").fetchone()[0]
        n_mem = con.execute("SELECT COUNT(*) FROM mem").fetchone()[0]
        con.close()
        print("被拒后：时间线 %d 条、记忆 %d 条（应和动手前一样）" % (n_tl, n_mem))
        # 注意：不能拿"行数"去跟"链头 seq"比 —— 删过记录之后两者本来就不相等
        # （行 647 条、最大 seq 648）。要跟"动手之前"比。
        ok3 = (n_tl == n_tl_before and n_mem == n_mem_before)

        print("\n结果：%s" % ("全过 ✅" if (ok1 and ok2 and ok3) else "有失败 ❌"))
        print("  只读标记 %s ｜ 409 语义 %s ｜ 没多写记录 %s" % (ok1, ok2, ok3))
        return 0 if (ok1 and ok2 and ok3) else 1
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
