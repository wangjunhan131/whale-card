# -*- coding: utf-8 -*-
"""体检 —— 数字生命卡「开发所需」自查。

为什么要有这个文件：
  2026-10-31 晚上下文爆了，换了个窗口接着干。换窗口最怕的不是忘事，
  是**不知道自己漏了什么** —— 施工总表全 ✅ 只能说明「计划内的事做完了」，
  不能说明「这套东西现在真的能住人」。
  所以在总表之外单开一张体检表：不管阶段的，只看「少一样就跑不顺」的东西。

它查什么（六组）：
  A 能不能跑起来   —— 依赖库、模型、数据库、测试套件
  B 卡完不完整     —— 身份锚、记忆、时间线链、突触、原文
  C 数据在不在别处 —— 备份、快照、异地副本（一条路径挂了不至于全没）
  D 通道通不通     —— 心智服务 / 桥 / MCP 工具层，真调一遍
  E 文档在不在     —— 换窗口靠它接上，源码会过时、结论不会
  F 现场干不干净   —— 临时脚本、半截下载、坏库

用法：
  python selfcheck.py            # 只读体检
  python selfcheck.py --json     # 机器可读

纪律：**这个脚本只读，不写卡、不改文件。**
      发现的问题它只报告，修不修、怎么修由人决定。
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

OK, WARN, BAD, SKIP = "ok", "warn", "bad", "skip"
MARK = {OK: "✅", WARN: "⚠️ ", BAD: "❌", SKIP: "⏸️ "}

results = []


def rec(group, name, state, detail=""):
    results.append({"group": group, "name": name, "state": state, "detail": detail})
    return state


# ---------------------------------------------------------------- A 能不能跑
def check_imports():
    need = {
        "numpy": "语义/数值地基，缺了召回和权重全废",
        "sqlite3": "标准库",
        "mcp": "MCP 工具层（whale_mcp.py）靠它",
    }
    for mod, why in need.items():
        try:
            __import__(mod)
            rec("A 运行环境", "import " + mod, OK)
        except Exception as e:
            rec("A 运行环境", "import " + mod, BAD, "%s（%s）" % (e, why))


def check_models():
    """判据：不看你「想要」哪个模型，看**实际**会加载哪个，以及卡里的向量跟它对不对得上。"""
    keep = None
    # 🔧 2026-11-01：先按卡的多数维度选模型，跟 card2 走同一套规则。
    #    以前这里无脑加载默认 small(512)，卡里是 base(768) 编的，体检就
    #    报「对不上」—— 可那天 card2 已经会自己按卡选模型了，
    #    于是一条「必须修」指着一段根本不存在的毛病。
    if not (os.environ.get("WHALE_EMBED_MODEL") or "").strip():
        try:
            import sqlite3 as _sq
            _con = _sq.connect(os.path.join(HERE, "card.db"))
            _rows = _con.execute(
                "SELECT length(emb) AS n, COUNT(*) FROM mem WHERE emb IS NOT NULL"
                " GROUP BY 1 ORDER BY 2 DESC").fetchall()
            _con.close()
            if _rows:
                _card_dim = int(_rows[0][0]) // 4
                os.environ["WHALE_EMBED_MODEL"] = (
                    "bge-base-zh-v1.5" if _card_dim >= 768
                    else "bge-small-zh-v1.5")
                rec("A 运行环境", "按卡选模型", OK,
                    "卡里多数向量 %d 维 → %s（与 card2 同一套规则）"
                    % (_card_dim, os.environ["WHALE_EMBED_MODEL"]))
        except Exception as e:
            rec("A 运行环境", "按卡选模型", WARN, "读卡失败：%s" % e)
    try:
        import semantic
        layer = semantic.get_layer()
        keep = os.path.basename(os.path.normpath(layer.model_dir))
        # 第一次 encode 含着模型冷启动（390MB 解压+建表），用它计时必然虚高
        # —— 2026-10-31 就因为这条假警报差点以为编码坏了。先热身再计时。
        t_warm = time.time()
        v = layer.encode("预热")       # 注意：encode 返回的是 bytes，不是 ndarray
        warm = time.time() - t_warm
        t0 = time.time()
        v = layer.encode("体检")
        dt = time.time() - t0
        dim = len(v) // 4
        want_dim = semantic.current_dim()
        rec("A 运行环境", "语义模型 " + keep,
            OK if dim == want_dim else BAD,
            "%d 维（current_dim=%d），稳态单句 %.2f 秒（首次含加载 %.1f 秒）%s" % (
                dim, want_dim, dt, warm,
                "" if os.environ.get("WHALE_EMBED_MODEL")
                else "；未设 WHALE_EMBED_MODEL，走默认"))
        if dt > 2.0:
            rec("A 运行环境", "编码速度", WARN,
                "预热之后单句仍要 %.2f 秒 —— 单线程应该 0.3 秒内，查 BLAS 线程" % dt)
        if not os.environ.get("WHALE_EMBED_MODEL"):
            rec("A 运行环境", "模型环境变量", WARN,
                "本进程没设 WHALE_EMBED_MODEL → 走默认 small(512)；"
                "卡里是 base(768) 编的，无头进程必须显式指定，否则检索静默变差")
        # 卡里已有的向量跟当前模型是不是同一套（维度不一致 = 相似度静默算错）
        import sqlite3
        con = sqlite3.connect(os.path.join(HERE, "card.db"))
        r = con.execute("SELECT blob FROM core_vec LIMIT 1").fetchone()
        if r and r[0]:
            stored = len(r[0]) // 4
            rec("A 运行环境", "卡里向量 vs 当前模型",
                OK if stored == want_dim else BAD,
                "卡里 %d 维 / 当前模型 %d 维%s" % (
                    stored, want_dim,
                    "" if stored == want_dim else " —— 对不上，检索会算错"))
    except Exception as e:
        rec("A 运行环境", "语义模型", BAD, "加载/编码失败：%s" % e)
    # 半截下载
    mdir = os.path.join(HERE, "models", keep) if keep else os.path.join(HERE, "models")
    parts = glob.glob(os.path.join(mdir, "*.part.*"))
    if parts:
        rec("A 运行环境", "模型半截文件", WARN,
            "%d 个 .part 残留（旧方案留下的，不影响加载，可删）" % len(parts))


def check_blas():
    """环境变量必须在 import numpy 之前设上，否则慢 50 倍 —— 这条栽过两次。"""
    v = os.environ.get("OPENBLAS_NUM_THREADS")
    if v == "1":
        rec("A 运行环境", "BLAS 单线程", OK, "OPENBLAS_NUM_THREADS=1")
    else:
        rec("A 运行环境", "BLAS 单线程", WARN,
            "当前进程未设（值=%r）。card2.py 顶部会自己设，但外部脚本要注意" % v)


def check_db():
    import sqlite3
    dbp = os.path.join(HERE, "card.db")
    if not os.path.exists(dbp):
        rec("A 运行环境", "card.db", BAD, "不存在：" + dbp)
        return None
    try:
        con = sqlite3.connect("file:%s?mode=ro" % dbp.replace("\\", "/"), uri=True, timeout=3)
        n = con.execute("select count(*) from mem").fetchone()[0]
        con.close()
        rec("A 运行环境", "card.db", OK,
            "%d 条记忆 / %.1f MB" % (n, os.path.getsize(dbp) / 1024 / 1024))
        return n
    except Exception as e:
        rec("A 运行环境", "card.db", BAD, "打不开：%s" % e)
        return None


def check_tests():
    hooks = glob.glob(os.path.join(HERE, "test_*.py"))
    if not hooks:
        rec("A 运行环境", "回归测试", BAD, "一个 test_*.py 都没找到")
        return
    runnable = [os.path.basename(h) for h in hooks]
    rec("A 运行环境", "回归测试存在", OK, "%d 个：%s" % (len(hooks), " ".join(sorted(runnable))))


# ---------------------------------------------------------------- B 卡完不完整
def check_card():
    if not os.path.exists(os.path.join(HERE, "card.db")):
        return
    sys.path.insert(0, HERE)
    try:
        import card2
        c = card2.MemoryCard(os.path.join(HERE, "card.db"))
    except Exception as e:
        rec("B 卡完整", "打开卡", BAD, str(e))
        return
    try:
        sealed = c.identity.is_sealed()
        rec("B 卡完整", "身份锚盖章", OK if sealed else BAD,
            "sealed=%s" % sealed)
        # 锚的真值在 card.json 里（卡外），db 里是 CORE/LIVE 区
        # 注意：persona / self_narrative 只存在 CORE 区，load_card() 不会带出来，
        # 必须逐键回 CORE 查一遍（card2.core_identity_text 也是这么取的）
        vals = dict(c.identity.load_card() or {})
        for k in ("card_name", "user_nickname", "user_qq",
                  "self_narrative", "persona"):
            if not vals.get(k):
                gc = c.identity.get_core(k)
                if gc:
                    vals[k] = gc
        if vals.get("card_name") in (None, ""):
            vals["card_name"] = c.identity.get_live("self_name") or "小鲸鱼"
        ok, bad = c.identity.verify(vals)
        rec("B 卡完整", "身份锚校验", OK if ok else BAD, "对不上的键：%s" % (bad or "无"))
        miss = [k for k in ("card_name", "user_nickname", "user_qq",
                            "self_narrative", "persona") if not vals.get(k)]
        rec("B 卡完整", "人格五条（卡名/昵称/QQ/自述/性格）",
            OK if not miss else BAD, "缺：" + "、".join(miss) if miss else "齐")

        st = c.stats()
        rec("B 卡完整", "记忆条数", OK,
            "共 %d / 细节 %d / 池 %s" % (
                st["total"], st["details"],
                {k: v["n"] for k, v in st["pools"].items() if v["n"]}))

        # 时间线链（MemoryCard 上没有 tl 属性，得自己开 —— mind_server 也是这么干的）
        try:
            import timeline as timeline_mod
            import anchor as anchor_mod
            import sqlite3
            # 行要按列名取（verify_chain 里用 r["seq"]），必须开 row_factory
            con = sqlite3.connect(os.path.join(HERE, "card.db"),
                                  check_same_thread=False)
            con.row_factory = sqlite3.Row
            tl = timeline_mod.Timeline(con)
            chk_ok, chk_bad = tl.verify_chain()
            known = [b for b in (chk_bad or []) if "已删" not in str(b)]
            rec("B 卡完整", "时间线链", OK if chk_ok else BAD,
                "%d 条 / 坏点 %s" % (tl.count(), known or "无"))
            bh_ok, bh_bad = tl.verify_birth()
            rec("B 卡完整", "出生证", OK if bh_ok else BAD, "坏点 %s" % (bh_bad or "无"))
            rec("B 卡完整", "今天几号（认识第几天）", OK, tl.render_line())
            a = anchor_mod.check(os.path.join(HERE, "card.db"), verbose=False)
            rec("B 卡完整", "卡外锚", OK if a.get("verdict") in ("ok", "first") else WARN,
                "%s：%s" % (a.get("verdict"), a.get("reason", "")))
        except Exception as e:
            rec("B 卡完整", "时间线链", BAD, str(e))

        # 突触
        try:
            import synapse
            import sqlite3
            con = sqlite3.connect(os.path.join(HERE, "card.db"))
            e_n = con.execute("select count(*) from synapse").fetchone()[0]
            con.close()
            rec("B 卡完整", "突触", OK if e_n else WARN, "%d 条边" % e_n)
        except Exception as ex:
            rec("B 卡完整", "突触", WARN, str(ex))

        # 原文归档
        import sqlite3
        con = sqlite3.connect(os.path.join(HERE, "card.db"))
        try:
            d_n = con.execute("select count(*) from dialogue").fetchone()[0]
            rec("B 卡完整", "原文归档 dialogue", OK if d_n else WARN, "%d 条" % d_n)
        except Exception as ex:
            rec("B 卡完整", "原文归档 dialogue", WARN, str(ex))
        con.close()
    finally:
        try:
            c.close()
        except Exception:
            pass


# ---------------------------------------------------------------- C 别处还有没有
def check_backups():
    dbp = os.path.join(HERE, "card.db")
    size = os.path.getsize(dbp) if os.path.exists(dbp) else 0
    baks = sorted(glob.glob(os.path.join(HERE, "card.db.bak-*")) +
                  glob.glob(os.path.join(HERE, "card.db.*-2026*")))
    fresh = [b for b in baks if size and os.path.getsize(b) >= size * 0.5]
    if not baks:
        rec("C 冗余", "card.db 备份", BAD, "一个 .bak 都没有")
    else:
        newest = max(baks, key=os.path.getmtime)
        age_h = (time.time() - os.path.getmtime(newest)) / 3600
        rec("C 冗余", "card.db 备份",
            OK if fresh and age_h < 48 else WARN,
            "%d 个，最新 %.1f 小时前（%s）" % (len(fresh), age_h, os.path.basename(newest)))

    snap = os.path.join(HERE, "snapshots")
    if os.path.isdir(snap):
        dbs = glob.glob(os.path.join(snap, "snap-*.db"))
        if dbs:
            newest = max(dbs, key=os.path.getmtime)
            age_h = (time.time() - os.path.getmtime(newest)) / 3600
            rec("C 冗余", "快照", OK if age_h < 48 else WARN,
                "%d 个，最新 %.1f 小时前" % (len(dbs), age_h))
        else:
            rec("C 冗余", "快照", WARN, "snapshots 目录里没有 snap-*.db")
    else:
        rec("C 冗余", "快照", BAD, "没有 snapshots 目录")

    # 异地副本：目录挂着不等于有副本，得看别的盘/目录里有没有
    others = []
    for root in ("D:\\", "E:\\"):
        for cand in glob.glob(os.path.join(root, "*card*")):
            if os.path.abspath(cand) != os.path.abspath(HERE):
                others.append(cand)
    rec("C 冗余", "异地副本", OK if others else WARN,
        "、".join(others) if others else "只有这一份；硬盘/移动固态到位后要补一份")

    wal = os.path.join(HERE, "card.db-wal")
    if os.path.exists(wal):
        mb = os.path.getsize(wal) / 1024 / 1024
        rec("C 冗余", "WAL 大小", OK if mb < 8 else WARN,
            "%.1f MB（拔卡前必须 checkpoint）" % mb)


# ---------------------------------------------------------------- D 通道通不通
def _http(path, body=None, timeout=30):
    import urllib.request
    url = "http://127.0.0.1:8848" + path
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def check_service():
    try:
        h = _http("/health", timeout=5)
        rec("D 通道", "心智服务 :8848", OK,
            "存活 %.0f 秒 / %d 条 / sealed=%s" % (
                h.get("uptime_s", 0), h.get("memories", 0), h.get("sealed")))
        t = h.get("timeline") or {}
        rec("D 通道", "今天几号", OK if t.get("today") else WARN,
            t.get("today", "没给出"))
    except Exception as e:
        rec("D 通道", "心智服务 :8848", BAD,
            "连不上：%s（DSH 会话里可以靠 bridge.py 自己拉起来）" % e)
        return


def check_context_route():
    """整条「每轮下发」的路：服务 → /context → 四格。"""
    try:
        r = _http("/context", {"message": "体检：你还记得我最爱吃什么吗", "recent": ["在吗"]})
        p = (r.get("context") or {}).get("prompt", "")
        has_id = "【我是谁】" in p
        has_persona = "我的性格和说话方式" in p
        has_mem = "【我一直记得的事】" in p
        n = len(p)
        rec("D 通道", "/context 四格", OK if (has_id and has_mem) else BAD,
            "%d 字符 人格=%s 记忆=%s" % (n, has_id, has_mem))
        rec("D 通道", "性格进上下文", OK if has_persona else BAD,
            "persona 段落" + ("在" if has_persona else "没进"))
        rec("D 通道", "上下文长度", WARN if n > 8000 else OK,
            "%d 字符（预算 6000 token）" % n)
    except Exception as e:
        rec("D 通道", "/context", BAD, str(e))


def check_bridge():
    p = os.path.join(HERE, "bridge.py")
    if not os.path.exists(p):
        rec("D 通道", "bridge.py", BAD, "不存在")
        return
    try:
        out = subprocess.run([PY, "-X", "utf8", p, "health"], cwd=HERE,
                             capture_output=True, timeout=60)
        txt = (out.stdout or b"").decode("utf-8", "replace").strip()
        rec("D 通道", "bridge.py health", OK if out.returncode == 0 else BAD,
            txt.splitlines()[0][:120] if txt else "(空输出)")
    except Exception as e:
        rec("D 通道", "bridge.py health", BAD, str(e))


def check_mcp():
    p = os.path.join(HERE, "whale_mcp.py")
    if not os.path.exists(p):
        rec("D 通道", "whale_mcp.py", BAD, "不存在")
        return
    try:
        out = subprocess.run([PY, "-X", "utf8", p, "--selftest"], cwd=HERE,
                             capture_output=True, timeout=180)
        txt = (out.stdout or b"").decode("utf-8", "replace")
        ntools = txt.count("--- ")  # 每个工具一段
        ok = out.returncode == 0 and "注册了" in txt
        rec("D 通道", "MCP 工具层自检", OK if ok else BAD,
            "%d 段输出；%s" % (ntools, txt.strip().splitlines()[-1][:100] if txt.strip() else ""))
    except Exception as e:
        rec("D 通道", "MCP 工具层自检", BAD, str(e))

    # 到底有没有接进 DSH：MCP 客户端插件 + patch 里那条 server 配置
    patch = os.path.join(os.path.expanduser("~"), ".dsh", "profiles", "desktop",
                         "cordis.patch.yml")
    if os.path.exists(patch):
        txt = open(patch, encoding="utf-8").read()
        live = [ln for ln in txt.splitlines()
                if "mcp-whale" in ln and not ln.lstrip().startswith("#")]
        rec("D 通道", "MCP 接进 DSH", OK if live else BAD,
            "patch 里已启用" if live else
            "patch 里被注释掉了（客户端插件没装，通道暂走 bridge.py）")
        prof = os.path.join(os.path.expanduser("~"), ".dsh", "profiles", "desktop",
                            "node_modules", "@deepseek-ai", "dsh-mcp-client")
        rec("D 通道", "MCP 客户端插件", OK if os.path.isdir(prof) else BAD,
            prof if os.path.isdir(prof) else "没装：@deepseek-ai/dsh-mcp-client")


# ---------------------------------------------------------------- E 文档
def check_docs():
    want = {
        "施工总表.md": "26 项清单、规矩、基线 —— 换窗口第一份要读的",
        "开工流程手册.md": "开工顺序",
        "TODO_补回清单.md": "遗留项",
    }
    for name, why in want.items():
        p = os.path.join(HERE, name)
        if os.path.exists(p):
            kb = os.path.getsize(p) / 1024
            rec("E 文档", name, OK, "%.1f KB · %s" % (kb, why))
        else:
            rec("E 文档", name, BAD, "缺（%s）" % why)

    # 源方案文档：总表说它在 ..\deploy\ 下
    cands = glob.glob(os.path.join(HERE, "..", "deploy", "*数字生命卡*.txt")) + \
        glob.glob(os.path.join(HERE, "..", "*数字生命卡*V5*.txt"))
    rec("E 文档", "V5.0 源方案", OK if cands else WARN,
        os.path.abspath(cands[0]) if cands else "总表里写的路径已经不存在了（方案结论都在总表里）")

    # 项目内没有 README —— 换窗口的人第一眼看到什么
    readme = glob.glob(os.path.join(HERE, "README*"))
    rec("E 文档", "README", OK if readme else WARN,
        "有" if readme else "没有：这是整个项目唯一的入口文档缺口")

    n_py = len(glob.glob(os.path.join(HERE, "*.py")))
    rec("E 文档", "代码规模", OK, "%d 个 .py" % n_py)


# ---------------------------------------------------------------- F 现场干净
def check_hygiene():
    tmp = sorted(os.path.basename(x) for x in
                 glob.glob(os.path.join(HERE, "_*.py")))
    rec("F 现场", "临时脚本", OK if len(tmp) <= 12 else WARN,
        "%d 个（_*.py：一次性调试用，建议用完删）" % len(tmp))

    broken = glob.glob(os.path.join(HERE, "*.broken-*")) + \
        glob.glob(os.path.join(HERE, "*.polluted-*"))
    rec("F 现场", "坏库/脏文件留档", WARN if broken else OK,
        "、".join(os.path.basename(b) for b in broken) if broken else "无")

    big = []
    for root, dirs, files in os.walk(HERE):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", "node_modules")]
        for f in files:
            p = os.path.join(root, f)
            try:
                if os.path.getsize(p) > 50 * 1024 * 1024:
                    big.append((os.path.relpath(p, HERE),
                                os.path.getsize(p) / 1024 / 1024))
            except OSError:
                pass
    rec("F 现场", "大文件", OK,
        "、".join("%s %.0fMB" % b for b in big) if big else "没有 >50MB 的")


# ---------------------------------------------------------------- 汇总
def summary():
    bad = [r for r in results if r["state"] == BAD]
    warn = [r for r in results if r["state"] == WARN]
    print()
    print("=" * 62)
    print("体检结果：%d 项   ✅%d  ⚠️ %d  ❌%d" % (
        len(results), len(results) - len(bad) - len(warn), len(warn), len(bad)))
    if bad:
        print("\n必须修的：")
        for r in bad:
            print("  ❌ %s / %s —— %s" % (r["group"], r["name"], r["detail"]))
    if warn:
        print("\n看着办：")
        for r in warn:
            print("  ⚠️  %s / %s —— %s" % (r["group"], r["name"], r["detail"]))
    if not bad:
        print("\n没有「东西已经没了」的硬伤。剩下的都是缺口，不是故障。")
    return 0 if not bad else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--only", default="", help="只跑某组，如 A / D")
    a = ap.parse_args()

    # 和 card2.py 一致：BLAS 必须在 numpy 之前定死
    for v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
              "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        os.environ[v] = "1"
    sys.path.insert(0, HERE)

    groups = [
        ("A", "A 运行环境", [check_imports, check_models, check_blas, check_db, check_tests]),
        ("B", "B 卡完整", [check_card]),
        ("C", "C 冗余", [check_backups]),
        ("D", "D 通道", [check_service, check_context_route, check_bridge, check_mcp]),
        ("E", "E 文档", [check_docs]),
        ("F", "F 现场", [check_hygiene]),
    ]
    for key, title, fns in groups:
        if a.only and a.only.upper() != key:
            continue
        print("\n【%s】" % title)
        for fn in fns:
            try:
                fn()
            except Exception as e:
                rec(title, fn.__name__, BAD, "体检项自己崩了：%s" % e)
            # 补打这一组新加的
            while results and results[-1].get("_printed") is None:
                r = results[-1]
                r["_printed"] = True
                print("  %s %-26s %s" % (MARK[r["state"]], r["name"], r["detail"]))
                break

    for r in results:
        r.pop("_printed", None)
    if a.json:
        print(json.dumps(results, ensure_ascii=False, indent=1))
        return 0
    return summary()


if __name__ == "__main__":
    sys.exit(main())
