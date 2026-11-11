# -*- coding: utf-8 -*-
"""装完/升级后跑一遍的 smoke test（V7 第 18 件）。

用法：
    python tools/smoke.py                 # 静态检查 + 如果服务在跑就顺带查活体
    python tools/smoke.py --port 8848 --json
    python tools/smoke.py --rollback      # 额外跑版本回滚演练（临时副本）

为什么要有它：桥插件装过三次才生效（前两次「看着装好了、其实一条工具都没注册」），
mcp 升到 2.x 把 whale_mcp 的 import 弄崩过一次，schema 加列漏过一处。
这些都不该靠人盯着日志发现 —— 装完跑一遍，红的就是红的。

检查项（每项独立，互不阻断）：
  1. 目录/关键文件在位（card2.py / mind_server.py / bridge.py / console.py / card.db）
  2. 关键模块 import（card2 / timeline / identity / disks / impression / habits / versions）
  3. 卡可读、schema 版本对得上（PRAGMA user_version vs versions.SCHEMA_VERSION）
  4. token 文件在（qq_state/api_token.txt）
  5. 桥插件：profile 里那份的 package.json 版本 + 有没有 load.log 里的注册记录
  6. 活体（服务在跑时）：/health ok、/version 拿得到、/context 七格齐、写闸不是 readonly
  7. 时间线链完好（/health 的 timeline.chain_ok）

退出码：0 全过；1 有 FAIL；2 有 WARN 但不阻断。
"""
import argparse
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"
RESULT = []


def chk(name, ok, detail="", level=FAIL):
    RESULT.append({"name": name, "state": PASS if ok else level,
                   "detail": str(detail)[:300]})
    return ok


def static_checks(db_path):
    # 1. 文件
    need = ["card2.py", "mind_server.py", "bridge.py", "console.py",
            "timeline.py", "identity.py", "disks.py", "versions.py"]
    missing = [f for f in need if not os.path.exists(os.path.join(ROOT, f))]
    chk("关键文件在位", not missing, "缺：%s" % missing if missing else "%d 个都在" % len(need))
    chk("卡文件在位", os.path.exists(db_path), db_path)

    # 2. import（在子进程里做更干净，但这里直接做：失败也只是这一项红）
    sys.path.insert(0, ROOT)
    for mod in ("card2", "timeline", "identity", "disks", "impression",
                "habits", "versions"):
        try:
            __import__(mod)
            chk("import %s" % mod, True, "ok")
        except Exception as e:
            chk("import %s" % mod, False, "%s: %s" % (type(e).__name__, e))

    # 3. schema 版本
    try:
        import versions
        con = sqlite3.connect("file:%s?mode=ro" % db_path.replace("\\", "/"),
                              uri=True, timeout=5)
        v = con.execute("PRAGMA user_version").fetchone()[0]
        con.close()
        chk("卡 schema 版本", int(v or 0) == versions.SCHEMA_VERSION,
            "db=%s 代码=%s" % (v, versions.SCHEMA_VERSION),
            level=WARN if int(v or 0) < versions.SCHEMA_VERSION else FAIL)
    except Exception as e:
        chk("卡 schema 版本", False, "%s: %s" % (type(e).__name__, e))

    # 4. token
    tp = os.path.join(ROOT, "qq_state", "api_token.txt")
    tok = ""
    try:
        tok = open(tp, encoding="utf-8").read().strip()
    except OSError:
        pass
    chk("API token", bool(tok), tp if tok else "缺 %s（服务首启会自动建）" % tp,
        level=WARN)

    # 5. 桥插件
    prof = os.path.join(os.path.expanduser("~"), ".dsh", "profiles", "desktop",
                        "node_modules", "dsh-whale-bridge")
    pkg = os.path.join(prof, "package.json")
    ver = None
    try:
        ver = (json.load(open(pkg, encoding="utf-8")) or {}).get("version")
    except Exception:
        pass
    chk("桥插件已装进 profile", bool(ver), "%s version=%s" % (prof, ver))
    loadlog = os.path.join(prof, "load.log")
    reg = ""
    try:
        txt = open(loadlog, encoding="utf-8", errors="ignore").read()
        for line in txt.splitlines():
            if "registered mind_" in line or "apply() 完成" in line:
                reg = line.strip()
    except OSError:
        pass
    chk("桥插件注册过工具", bool(reg), reg or "load.log 里没有 registered 行",
        level=WARN)

    # 6. 吞异常台账守门（V7 第 6 件）：新出现的空吞必须登记，否则 FAIL。
    try:
        sys.path.insert(0, HERE)
        import swallow_gate as _sg
        _r = _sg.check()
        if _r.get("reason"):
            chk("吞异常台账", False, _r["reason"])
        else:
            _new = _r.get("new") or []
            _brief = "；".join("%s:%d" % (e["file"], e["line"])
                               for e in _new[:3])
            chk("吞异常台账", _r["ok"],
                "当前 %d 处，新增 %d%s"
                % (_r["current"], len(_new),
                   ("：" + _brief) if _brief else ""))
    except Exception as e:
        chk("吞异常台账", False,
            "守门器跑不起来：%s: %s" % (type(e).__name__, e))
    return tok


def live_checks(port, token):
    base = "http://127.0.0.1:%d" % port

    def get(path):
        req = urllib.request.Request(base + path)
        if token:
            req.add_header("X-Whale-Token", token)
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)

    try:
        h = get("/health")
    except Exception as e:
        chk("服务在跑", False, "连不上 %s：%s" % (base, e), level=WARN)
        return
    chk("服务在跑", True, base)
    chk("/health ok", bool(h.get("ok")), "red=%s" % (h.get("reconcile") or {}).get("red"))
    # 启动自检（2026-11-11 稳定性）：服务起来几秒后会自己走一遍「库能不能写、
    # 世界有没有在走、账本表在不在」。这里只读它的结论 —— 启动有没有真的站住。
    _bt = h.get("boot")
    if _bt is None:
        chk("启动自检", True, "还没跑完（刚起来），跳过", level=WARN)
    else:
        _bk = _bt.get("checks") or {}
        chk("启动自检", bool(_bt.get("ok")),
            "journal=%s pods_cache=%sKB problems=%s"
            % (_bk.get("journal_mode"),
               ((_bk.get("pods") or {}).get("cache_kb")),
               ",".join(_bt.get("problems") or []) or "无"),
            level=WARN)
        chk("库是 WAL", str(_bk.get("journal_mode")).lower() == "wal",
            "journal=%s" % _bk.get("journal_mode"))
    # 后台线程（2026-11-11）：世界推进/写队列/维护，三个里死一个都算坏 ——
    # 服务还会回话，但记忆不衰减、写不落地。启动自检的 boot_check 是跑完就结束的，不算。
    _th = h.get("threads") or {}
    _dead = [k for k in ("world_tick", "write_queue", "maint") if _th.get(k) is False]
    chk("后台线程都在", not _dead,
        "threads=%s" % ",".join("%s=%s" % (k, _th.get(k))
                                for k in ("world_tick", "write_queue", "maint", "boot_check")))
    tl = h.get("timeline") or {}
    chk("时间线链完好", bool(tl.get("chain_ok")), "head=%s" % str(tl.get("head"))[:12])
    chk("不是只读", not h.get("readonly"), "readonly_reason=%s" % h.get("readonly_reason"),
        level=WARN)
    try:
        v = get("/version")["versions"]
        mm = v.get("mismatch") or []
        chk("版本矩阵一致", not mm, mm or "schema code=%s db=%s"
            % ((v.get("schema") or {}).get("code"),
               (v.get("schema") or {}).get("db")), level=WARN)
    except Exception as e:
        chk("版本矩阵", False, "%s: %s" % (type(e).__name__, e), level=WARN)
    try:
        import urllib.request as _u
        req = _u.Request(base + "/context", method="POST",
                         data=json.dumps({"message": "smoke：你叫什么？"}).encode(),
                         headers={"Content-Type": "application/json",
                                  **({"X-Whale-Token": token} if token else {})})
        with _u.urlopen(req, timeout=60) as r:
            ctx = json.load(r)
        sec = ((ctx.get("context") or {}).get("sections") or {})
        chk("/context 七格齐", len(sec) >= 6, "sections=%s" % sorted(sec))
    except Exception as e:
        chk("/context 能答", False, "%s: %s" % (type(e).__name__, e))



def rollback_checks(source_db):
    """V7 第18件回滚演练：检查点 seq 改大必须报回滚，去掉假检查点必须恢复。

    全部在临时副本里做，活卡一行不动。只在 --rollback 时调用。
    """
    import io as _io
    import shutil as _sh
    import tempfile as _tf

    try:
        import checkpoint as _cp
        import card2 as _card2
        import disks as _disks
    except Exception as e:
        chk("回滚演练可加载", False, "%s: %s" % (type(e).__name__, e))
        return

    base = _tf.mkdtemp(prefix="v18_smoke_")
    try:
        d = os.path.join(base, "card")
        os.makedirs(d, exist_ok=True)
        db = os.path.join(d, "card.db")
        src = sqlite3.connect(source_db, timeout=30)
        out = sqlite3.connect(db)
        src.backup(out)
        out.close()
        src.close()

        def _mem_n():
            con = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"),
                                  uri=True)
            try:
                return con.execute("SELECT COUNT(*) FROM mem").fetchone()[0]
            finally:
                con.close()

        def _latest():
            dirs = [x for x in _cp._dirs(db) if os.path.isdir(x)]
            docs = [x for x in (_cp.latest(y) for y in dirs) if x]
            return (docs[0] if docs else None), dirs

        n0 = _mem_n()
        _cp.write(db)
        v1 = _cp.verify(db)
        chk("回滚演练：第 1 份检查点写进去且验得过", v1.get("ok"),
            "checked=%s mem=%d" % (v1.get("checked"), n0))

        card = _card2.MemoryCard(db)
        card.remember("回滚演练：这条是在第 2 步写的。", source="selftest")
        try:
            card.db.commit()
        except Exception:
            pass
        try:
            card.close()
        except Exception:
            pass
        n1 = _mem_n()
        chk("回滚演练：第 2 步真的往前长了", n1 > n0, "mem %d -> %d" % (n0, n1))
        _cp.write(db)
        v2 = _cp.verify(db)
        chk("回滚演练：第 2 份检查点也验得过", v2.get("ok"),
            "checked=%s" % v2.get("checked"))

        doc, dirs = _latest()
        if not doc or not dirs:
            chk("回滚演练：能读到最新检查点", False, "没有 LATEST.json")
            return
        doc_big = dict(doc)
        doc_big["head_seq"] = int(doc.get("head_seq") or 0) + 5
        key = _cp.load_key(db)
        latest_path = os.path.join(dirs[0], "LATEST.json")
        keep_latest = os.path.join(base, "LATEST.keep.json")
        _sh.copy2(latest_path, keep_latest)
        doc_big.pop("sig", None)
        body = dict(doc_big)
        body["sig"] = _cp.sign(doc_big, key)
        _io.open(latest_path, "w", encoding="utf-8", newline="").write(
            json.dumps(body, ensure_ascii=False, sort_keys=True))
        v3 = _cp.verify(db)
        probs = " / ".join(str(x) for x in (v3.get("problems") or []))
        chk("回滚演练：检查点 seq 改大 -> 报回滚",
            (not v3.get("ok")) and ("回滚" in probs or "更旧" in probs),
            probs[:220])

        _sh.copy2(keep_latest, latest_path)
        v4 = _cp.verify(db)
        chk("回滚演练：去掉假检查点 -> 恢复正常", v4.get("ok"),
            str(v4.get("problems"))[:180])

        keep = os.path.join(base, "state2.db")
        _sh.copy2(db, keep)
        _sh.copy2(keep, db)
        v5 = _cp.verify(db)
        ident = {}
        try:
            ident = _disks.manifest_identity(db) or {}
        except Exception:
            pass
        chk("回滚演练：备份盖回去仍验得过", v5.get("ok"),
            str(v5.get("problems"))[:180])
        chk("回滚演练：身份还在", bool(ident.get("instance_id")),
            str(ident.get("instance_id")))
        chk("回滚演练：内容回到盖回去那一刻", _mem_n() == n1, "mem %d" % _mem_n())

        con = sqlite3.connect(db, timeout=30)
        try:
            con.execute("DELETE FROM mem WHERE id=(SELECT MAX(id) FROM mem)")
            con.commit()
        finally:
            con.close()
        chk("回滚演练：手动退一格成功", _mem_n() == n1 - 1, "mem %d" % _mem_n())
        v6 = _cp.verify(db)
        chk("回滚演练：少一条记忆不算回滚", v6.get("ok"),
            str(v6.get("problems"))[:180])
    except Exception as e:
        chk("回滚演练整体", False, "%s: %s" % (type(e).__name__, e))
    finally:
        _sh.rmtree(base, ignore_errors=True)


def restore_checks(db):
    """恢复演练（2026-11-11 稳定性）：备份拷出来能不能重新站起来。

    真活儿在 tools/restore_drill.py 里，这里只读它的结论 —— 备份做过很多次，
    「备份能不能用」必须也有一次真凭实据。
    """
    import subprocess
    try:
        p = subprocess.run([sys.executable, "-X", "utf8",
                            os.path.join(ROOT, "tools", "restore_drill.py")],
                           capture_output=True, text=True, timeout=300)
        lines = [l for l in (p.stdout or "").splitlines() if l.startswith(("PASS", "FAIL"))]
        chk("恢复演练", p.returncode == 0,
            "%d 项，末项 %s" % (len(lines), lines[-1] if lines else (p.stderr or "")[-100:]))
    except Exception as e:
        chk("恢复演练", False, "%s: %s" % (type(e).__name__, e))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("WHALE_MIND_PORT", "8848")))
    ap.add_argument("--db", default=os.path.join(ROOT, "card.db"))
    ap.add_argument("--no-live", action="store_true", help="只做静态检查")
    ap.add_argument("--restore", action="store_true",
                    help="额外跑恢复演练（备份出来的卡能不能重新站起来，全在临时副本里）")
    ap.add_argument("--rollback", action="store_true",
                    help="额外跑回滚演练（V7 第18件，全部在临时副本里）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    tok = static_checks(a.db)
    if a.rollback:
        rollback_checks(a.db)
    if a.restore:
        restore_checks(a.db)
    if not a.no_live:
        live_checks(a.port, tok)

    fails = [r for r in RESULT if r["state"] == FAIL]
    warns = [r for r in RESULT if r["state"] == WARN]
    if a.json:
        print(json.dumps({"results": RESULT, "fail": len(fails),
                          "warn": len(warns)}, ensure_ascii=False, indent=1))
    else:
        for r in RESULT:
            mark = {"PASS": "✓", "FAIL": "✗", "WARN": "!"}[r["state"]]
            print("%s %-24s %s" % (mark, r["name"], r["detail"]))
        print("\n合计：%d 项，FAIL %d，WARN %d"
              % (len(RESULT), len(fails), len(warns)))
        if fails:
            print("失败项：" + "，".join(r["name"] for r in fails))
    return 1 if fails else (2 if warns else 0)


if __name__ == "__main__":
    sys.exit(main())
