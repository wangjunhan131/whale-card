# -*- coding: utf-8 -*-
"""控制台 —— 给卡做的一台仪表盘（2026-10-31 第二版）

## 三条设计原则（和第一版一样，没变）

1. **零依赖**：不用 Electron / npm / CDN。整页就是一段 HTML/CSS/JS，
   由 mind_server 直接吐出来。最终要住 U 盘，插到没 node 的机器也得能开。
   「类 APP」靠的是外面那层启动器（start_app.ps1 + Edge --app 窗口），
   不是靠往里塞一个 200MB 的运行时。

2. **只显示，不代替**：控制台不发消息、不扮演她。它只是把卡里的
   「现在什么状态」摊开给人看。

3. **改参数必须留痕**：所有写操作都走已有的 /cardslot、/config、/skill_*，
   不在这里新开旁路。

## 和第一版的差别

第一版是「五块面板竖着排」。这一版按用户给的参考图重排成
**左栏 = 这个人（头像/状态/运行时间/年龄/实例编号/封印/记忆）**、
**右栏 = 一堆图（激素趋势、写入统计、分区占比、存储、六维权重、体检日志）**。
理由：仪表盘要的是「一眼看出有没有异常」，竖排卡片要滚屏才能比对着看。

## 数据全部现算

没有一份是「界面里写死的假数」——
- 激素趋势：h_log 表（card2.log_hormones 每 5 分钟采一条）
- 写入统计：mem.create_ts 按天 group by
- 分区占比：mem.region 计数
- 存储：磁盘上真实文件大小
- 六维权重：ctxweight.compute() 现算
- 体检：/health 的链/出生证/锚 + cardslot 状态

用法（一般由 mind_server 的 /console 路由直接调）：
    python console.py data          # 打印一份 JSON（调试用）
"""
import json
import os
import sqlite3
import time

import viscera    # 24 种激素的出厂基线：控制台不自己另写一份
import padlib      # 零依赖纯函数：PAD 公式只住在这里

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.environ.get("WHALE_DB") or os.path.join(HERE, "card.db")

DAY = 86400.0


# ──────────────────────────────────────────────────────
def _conn(db_path):
    c = sqlite3.connect(db_path, timeout=5.0)
    c.row_factory = sqlite3.Row
    return c


def _f(x, d=0.0):
    try:
        return float(x)
    except Exception:
        return d


# ──────────────────────────────────────────────────────
def graph_data(db_path=DEFAULT_DB, limit=1200):
    """星云图：节点 + 边。直接查库，不走 export_viz（那个要 1.5 秒）。"""
    nodes, edges = [], []
    try:
        c = _conn(db_path)
        rows = c.execute(
            "SELECT id, region, pool, w, access_count, create_ts, core"
            " FROM mem ORDER BY w DESC LIMIT ?", (limit,)).fetchall()
        for r in rows:
            nodes.append({
                "id": r["id"], "r": r["region"] or "episode",
                "p": r["pool"] or "long", "w": round(_f(r["w"]), 4),
                "a": int(r["access_count"] or 0),
                "ts": round(_f(r["create_ts"]), 1),
                "t": (r["core"] or "")[:80]})
        ids = {n["id"] for n in nodes}
        for r in c.execute(
                "SELECT a_id, b_id, kind, weight FROM synapse"
                " ORDER BY weight DESC LIMIT 4000").fetchall():
            if r["a_id"] in ids and r["b_id"] in ids:
                edges.append({"a": r["a_id"], "b": r["b_id"],
                              "k": r["kind"], "w": round(_f(r["weight"]), 3)})
        c.close()
    except Exception as e:
        return {"ok": False, "error": str(e), "nodes": [], "edges": []}
    return {"ok": True, "nodes": nodes, "edges": edges,
            "n_nodes": len(nodes), "n_edges": len(edges)}


def timeline_tail(db_path=DEFAULT_DB, n=40):
    out = []
    try:
        c = _conn(db_path)
        rows = c.execute(
            "SELECT seq, ts, kind, note FROM timeline"
            " ORDER BY seq DESC LIMIT ?", (n,)).fetchall()
        out = [{"seq": r["seq"], "ts": round(_f(r["ts"]), 1),
                "kind": r["kind"], "note": r["note"] or ""}
               for r in reversed(rows)]
        c.close()
    except Exception:
        pass
    return out


MIN_CURVE_POINTS = 12     # 少于这么多个点就换更大的窗口


def hormones_series(db_path=DEFAULT_DB, hours=24.0, limit=400):
    """h_log 里最近 N 小时的采样点。空表就是空数组 —— 不补假数据。"""
    out = []
    try:
        c = _conn(db_path)
        since = time.time() - hours * 3600.0
        rows = c.execute(
            "SELECT ts, sero, dopa, cort, oxy, mood, oxy_g FROM h_log"
            " WHERE ts >= ? ORDER BY ts ASC LIMIT ?", (since, limit)).fetchall()
        # 采样太少（关机、服务没起）时往更长的窗口退：5 个点画不出形状，
        # 3 天里通常有几十个点。前端按首尾时间写"近 N 小时"，不谎称 24H。
        if len(rows) < MIN_CURVE_POINTS:
            for _h in (72.0, 168.0, 720.0):
                _since = time.time() - _h * 3600.0
                _rows = c.execute(
                    "SELECT ts, sero, dopa, cort, oxy, mood, oxy_g FROM h_log"
                    " WHERE ts >= ? ORDER BY ts ASC LIMIT ?",
                    (_since, limit)).fetchall()
                if len(_rows) >= MIN_CURVE_POINTS:
                    rows = _rows
                    hours = _h
                    break
            else:
                rows = c.execute(
                    "SELECT ts, sero, dopa, cort, oxy, mood, oxy_g FROM h_log"
                    " ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
                rows = list(reversed(rows))
        if not rows:
            rows = c.execute(
                "SELECT ts, sero, dopa, cort, oxy, mood, oxy_g FROM h_log"
                " ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
            rows = list(reversed(rows))
        out = []
        for r in rows:
            mood = _f(r["mood"]); sero = _f(r["sero"])
            dopa = _f(r["dopa"]); cort = _f(r["cort"]); oxy = _f(r["oxy"])
            # PAD 由 padlib 现算 —— h_log 里只存心境和激素，不存派生量。
            # 存了就等于把同一件事写两遍，以后改公式还得改历史数据。
            pad = padlib.pad_of(mood, sero, dopa, cort, oxy)
            out.append({"ts": round(_f(r["ts"]), 1),
                        "sero": round(sero, 4), "dopa": round(dopa, 4),
                        "cort": round(cort, 4), "oxy": round(oxy, 4),
                        "mood": round(mood, 4), "oxy_g": round(_f(r["oxy_g"]), 4),
                        "p": pad["p"], "a": pad["a"], "d": pad["d"]})
        c.close()
    except Exception:
        pass
    return out


def _jobj(txt):
    """库里存的是 JSON 文本，读坏了就当空，不让一条脏行毁整张图。"""
    if not txt:
        return {}
    try:
        v = json.loads(txt)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def emotion_series(db_path=DEFAULT_DB, hours=72.0, limit=600):
    """viscera_hist：快反应层的曲线 —— 心率、交感/副交感、五条调质、RPE、安全感。

    和 h_log 分开：那边是慢激素心境（几分钟一格），这边是「一句话一格」。
    空表就是空数组，不补假数据 —— 刚接上引擎时本来就还没有历史。
    """
    out = []
    try:
        c = _conn(db_path)
        since = time.time() - float(hours) * 3600.0
        cols = {r[1] for r in c.execute(
            "PRAGMA table_info(viscera_hist)").fetchall()}
        wide = "h" in cols
        sel = ("ts,hr,resp,face,sym,para,dopa,nora,sero,cort,oxy,rpe,safety,label"
               + (",temp,tension,h,p,pulse" if wide else ""))
        rows = c.execute(
            "SELECT " + sel + " FROM viscera_hist WHERE ts >= ?"
            " ORDER BY ts ASC LIMIT ?", (since, int(limit))).fetchall()
        c.close()
        for r in rows:
            out.append({
                "ts": round(_f(r["ts"]), 1),
                "hr": round(_f(r["hr"]), 1),
                "resp": round(_f(r["resp"]), 1),
                "face": round(_f(r["face"]), 3),
                "sym": round(_f(r["sym"]), 3),
                "para": round(_f(r["para"]), 3),
                "dopa": round(_f(r["dopa"]), 2),
                "nora": round(_f(r["nora"]), 2),
                "sero": round(_f(r["sero"]), 2),
                "cort": round(_f(r["cort"]), 2),
                "oxy": round(_f(r["oxy"]), 2),
                "rpe": round(_f(r["rpe"]), 3),
                "safety": round(_f(r["safety"]), 3),
                "label": str(r["label"] or ""),
                "temp": round(_f(r["temp"]), 2) if wide else 0,
                "tension": round(_f(r["tension"]), 3) if wide else 0,
                "hormones": _jobj(r["h"]) if wide else {},
                "params": _jobj(r["p"]) if wide else {},
                "pulse": _jobj(r["pulse"]) if wide else {},
            })
    except Exception:
        return []
    return out


def mem_by_day(db_path=DEFAULT_DB, days=14):
    """最近 N 天每天写入几条记忆。"""
    out = []
    try:
        c = _conn(db_path)
        rows = c.execute(
            "SELECT date(create_ts,'unixepoch','localtime') AS d,"
            "       COUNT(*) AS n FROM mem GROUP BY d"
            " ORDER BY d DESC LIMIT ?", (days,)).fetchall()
        out = [{"day": r["d"], "n": int(r["n"])} for r in reversed(rows)]
        c.close()
    except Exception:
        pass
    return out


def regions(db_path=DEFAULT_DB):
    """六个分区各多少条 + 平均权重。"""
    out = []
    try:
        c = _conn(db_path)
        rows = c.execute(
            "SELECT region, COUNT(*) AS n, AVG(w) AS aw FROM mem"
            " GROUP BY region ORDER BY n DESC").fetchall()
        out = [{"r": r["region"] or "episode", "n": int(r["n"]),
                "aw": round(_f(r["aw"]), 3)} for r in rows]
        c.close()
    except Exception:
        pass
    return out


def _dirsize(p):
    t = 0
    for root, _dirs, files in os.walk(p):
        for f in files:
            try:
                t += os.path.getsize(os.path.join(root, f))
            except Exception:
                pass
    return t


def card_metrics(db_path=DEFAULT_DB):
    """控制台仪表盘要的一堆数字，一次算齐。

    原则和别的函数一样：读不到就返回 0 / 空数组，绝不编数字。
    """
    now = time.time()
    out = {
        "mem": 0, "detail": 0, "skill": 0, "dialogue": 0, "tomb": 0, "birth": 0,
        "created_24h": 0, "created_7d": 0,
        "pools": [], "short_regions": [], "region_load": {"on": False, "regions": []},
        "weight_hist": [], "emb_dims": [], "usable_emb": 0,
        "recall_24h": 0, "recall_sources": [], "recall_top": [],
        "recall_series": [], "synapse": {}, "syn_kinds": [],
        "timeline_series": [], "ticks": 0,
        "layers": [], "core_kinds": [],
        "injection": {"n": 0, "rows": []}, "dream": {"pending": 0, "dreamed": 0},
        "value_hist": [], "pad": {},
    }
    try:
        c = _conn(db_path)

        def one(sql, args=()):
            r = c.execute(sql, args).fetchone()
            return int(_f(r[0])) if r and r[0] is not None else 0

        out["mem"] = one("SELECT COUNT(*) FROM mem")
        out["detail"] = one("SELECT COUNT(*) FROM detail")
        _keymap = {"skill": "skill", "dialogue": "dialogue",
                   "mem_tomb": "tomb", "mem_birth": "birth"}
        for t, key in _keymap.items():
            try:
                out[key] = one("SELECT COUNT(*) FROM %s" % t)
            except Exception:
                pass
        out["created_24h"] = one("SELECT COUNT(*) FROM mem WHERE create_ts>=?", (now - 86400,))
        out["created_7d"] = one("SELECT COUNT(*) FROM mem WHERE create_ts>=?", (now - 7 * 86400,))

        # 五池：条数 + 平均权重
        try:
            for r in c.execute("SELECT pool, COUNT(*) n, AVG(w) aw FROM mem"
                               " GROUP BY pool ORDER BY n DESC"):
                out["pools"].append({"k": r["pool"] or "long", "n": int(r["n"]),
                                     "aw": round(_f(r["aw"]), 3)})
        except Exception:
            pass

        # 短期池按区拆开（2026-11-06 V5.2）：光看「短期 4 条」没用，
        # 得看出这 4 条是闲聊、是学习、还是工作 —— 它们的寿命差 30 倍。
        try:
            for r in c.execute(
                    "SELECT COALESCE(NULLIF(region,''),'episode') rg,"
                    " COUNT(*) n, AVG(w) aw FROM mem WHERE pool='short'"
                    " GROUP BY rg ORDER BY n DESC"):
                out["short_regions"].append({
                    "k": r["rg"], "n": int(r["n"]), "aw": round(_f(r["aw"]), 3)})
        except Exception:
            pass

        # 每个区自己的遗忘曲线开没开 + 今天这个区挤不挤
        try:
            import card2 as _c2
            out["region_load"] = _c2.region_snapshot_from_db(c, now)
        except Exception:
            pass

        # 权重直方图（0~1 十二格）
        try:
            hist = [0] * 12
            for r in c.execute("SELECT w FROM mem"):
                w = max(0.0, min(0.999999, _f(r["w"])))
                hist[int(w * 12)] += 1
            out["weight_hist"] = hist
        except Exception:
            pass

        # 向量维度账本（字节/4 = 维度）
        try:
            for r in c.execute("SELECT length(emb) L, COUNT(*) n FROM mem"
                               " WHERE emb IS NOT NULL GROUP BY L ORDER BY n DESC"):
                if r["L"]:
                    out["emb_dims"].append({"dim": int(r["L"]) // 4, "n": int(r["n"])})
            if out["emb_dims"]:
                out["usable_emb"] = out["emb_dims"][0]["n"]
        except Exception:
            pass

        # 五层记忆（2026-11-01）：tier 是后加的列，老库没有就全算 mid
        try:
            cols = {r["name"] for r in c.execute("PRAGMA table_info(mem)")}
            if "tier" in cols:
                for r in c.execute("SELECT tier, COUNT(*) n FROM mem GROUP BY tier"):
                    out["layers"].append({"k": r["tier"] or "mid", "n": int(r["n"])})
            else:
                out["layers"].append({"k": "mid", "n": one("SELECT COUNT(*) FROM mem")})
            out["core_kinds"] = []
            if "tier" in cols and "subject_key" in cols:
                for r in c.execute(
                        "SELECT subject_key, COUNT(*) n FROM mem"
                        " WHERE tier LIKE 'core%' GROUP BY subject_key"
                        " ORDER BY n DESC LIMIT 10"):
                    k = (r["subject_key"] or "未分类").split(":")[0]
                    out["core_kinds"].append({"k": k, "n": int(r["n"])})
        except Exception:
            pass

        # 召回：24h 总量、来源分布、逐小时曲线、最常被想起的几条
        # 🔧 2026-11-04：24 小时窗口经常是 0（MCP 没装，回想只走 bridge ask），
        #    卡片就一片空。改法：24h 空了就退到 7 天，并把实际窗口一起返回，
        #    前端照实写「7D」。空着不等于没这功能，只是这两天没被叫醒。
        try:
            win = 86400
            n24 = one("SELECT COUNT(*) FROM recall_event WHERE ts>=?", (now - win,))
            if not n24:
                w7 = one("SELECT COUNT(*) FROM recall_event WHERE ts>=?", (now - 7 * 86400,))
                if w7:
                    win = 7 * 86400
                    n24 = w7
            since = now - win
            out["recall_window"] = "24H" if win == 86400 else "7D"
            out["recall_win_s"] = int(win)
            out["recall_24h"] = n24
            for r in c.execute("SELECT source, COUNT(*) n FROM recall_event"
                               " WHERE ts>=? GROUP BY source ORDER BY n DESC", (since,)):
                out["recall_sources"].append({"k": r["source"] or "direct", "n": int(r["n"])})
            bins = [0] * 24
            span = max(1, int(win))
            for r in c.execute("SELECT ts FROM recall_event WHERE ts>=?", (since,)):
                h = int((now - _f(r["ts"])) * 24 // span)
                if 0 <= h < 24:
                    bins[23 - h] += 1
            out["recall_series"] = bins
            for r in c.execute("SELECT mem_id, COUNT(*) n FROM recall_event"
                               " WHERE ts>=? AND mem_id IS NOT NULL GROUP BY mem_id"
                               " ORDER BY n DESC LIMIT 8", (since,)):
                raw = c.execute("SELECT raw FROM mem WHERE id=?", (r["mem_id"],)).fetchone()
                out["recall_top"].append({"id": int(r["mem_id"]), "n": int(r["n"]),
                                          "text": (raw["raw"] if raw else "")[:60]})
        except Exception:
            pass

        # 突触网络
        try:
            edges = one("SELECT COUNT(*) FROM synapse")
            nodes = set()
            wsum = 0.0
            for r in c.execute("SELECT a_id, b_id, weight FROM synapse"):
                nodes.add(r["a_id"]); nodes.add(r["b_id"]); wsum += _f(r["weight"])
            out["synapse"] = {
                "edges": edges, "nodes": len(nodes),
                "avg_w": round(wsum / edges, 3) if edges else 0.0,
                "degree": round(2.0 * edges / len(nodes), 2) if nodes else 0.0,
            }
            for r in c.execute("SELECT kind, COUNT(*) n, AVG(weight) aw FROM synapse"
                               " GROUP BY kind ORDER BY n DESC"):
                out["syn_kinds"].append({"k": r["kind"] or "-", "n": int(r["n"]),
                                         "aw": round(_f(r["aw"]), 3)})
        except Exception:
            pass

        # 时间线吞吐（24 小时，每小时几条）
        try:
            bins = [0] * 24
            for r in c.execute("SELECT ts FROM timeline WHERE ts>=?", (now - 86400,)):
                h = int((now - _f(r["ts"])) // 3600)
                if 0 <= h < 24:
                    bins[23 - h] += 1
            out["timeline_series"] = bins
            out["ticks"] = one("SELECT COUNT(*) FROM timeline")
        except Exception:
            pass

        # 防污层：被灌进来的念头（trust 低 / 冲突）
        try:
            rows = []
            for r in c.execute("SELECT id,core,raw,region,trust,conflict,"
                               "conflict_why,create_ts FROM mem"
                               " WHERE trust<0.7 OR conflict=1"
                               " ORDER BY id DESC LIMIT 6"):
                rows.append({"id": r["id"],
                             "text": (r["core"] or r["raw"] or "")[:46],
                             "region": r["region"],
                             "trust": round(_f(r["trust"]) or 1.0, 2),
                             "conflict": int(r["conflict"] or 0),
                             "why": r["conflict_why"] or "",
                             "age_h": round((now - _f(r["create_ts"])) / 3600.0, 1)})
            n_bad = one("SELECT COUNT(*) FROM mem WHERE trust<0.7 OR conflict=1")
            out["injection"] = {"n": n_bad, "rows": rows}
        except Exception:
            pass

        # 梦境压缩：还没消化的对话轮数
        try:
            out["dream"] = {
                "pending": one("SELECT COUNT(*) FROM dialogue WHERE dreamed=0"),
                "dreamed": one("SELECT COUNT(*) FROM dialogue WHERE dreamed=1"),
            }
        except Exception:
            pass

        # 画面记忆：多少帧、占多大、多少在残影期、删过几张（两条曲线另外画）
        try:
            out["media"] = {
                "n": one("SELECT COUNT(*) FROM media"),
                "mb": round((c.execute("SELECT COALESCE(SUM(bytes),0) FROM media"
                                       ).fetchone()[0] or 0) / 1048576.0, 2),
                "orphan": one("SELECT COUNT(*) FROM media WHERE orphan_ts>0"),
                "pinned": one("SELECT COUNT(*) FROM media WHERE pinned=1"),
                "tomb": one("SELECT COUNT(*) FROM media_tomb"),
            }
        except Exception:
            out["media"] = {"n": 0, "mb": 0, "orphan": 0, "pinned": 0, "tomb": 0}

        # 价值分分布（0~5 六格）：只统计，不参与任何判定
        try:
            hist = [0] * 6
            for r in c.execute("SELECT value FROM mem"):
                hist[int(max(0, min(5, r["value"] or 0)))] += 1
            out["value_hist"] = hist
        except Exception:
            pass

        # PAD 三维：**不在这里算**。
        # 公式只有 card2.mood_pad() 一处（心境 → P，四激素 → A/D，相对基线），
        # 页面直接读 /status.pad。以前这里抄了一份，两份各自进化就会悄悄分家，
        # 而且没有任何东西会报错 —— 那种"看着差不多"的分歧最难查。

        c.close()
    except Exception as e:
        out["error"] = str(e)
    return out


def storage(db_path=DEFAULT_DB):
    """磁盘上真实占了多少。不猜。"""
    card_dir = os.path.dirname(os.path.abspath(db_path))
    out = {"db_mb": 0.0, "wal_mb": 0.0, "snap_mb": 0.0, "models_mb": 0.0,
           "other_mb": 0.0, "total_mb": 0.0, "disk_free_gb": 0.0}
    try:
        for f in (db_path, db_path + "-wal", db_path + "-shm"):
            if os.path.exists(f):
                mb = os.path.getsize(f) / 1048576.0
                key = "db_mb" if f == db_path else "wal_mb"
                out[key] = round(out[key] + mb, 2)
        sn = os.path.join(card_dir, "snapshots")
        if os.path.isdir(sn):
            out["snap_mb"] = round(_dirsize(sn) / 1048576.0, 1)
        md = os.path.join(card_dir, "models")
        if os.path.isdir(md):
            out["models_mb"] = round(_dirsize(md) / 1048576.0, 1)
        out["total_mb"] = round(out["db_mb"] + out["wal_mb"]
                               + out["other_mb"], 2)
        try:
            import ctypes
            free = ctypes.c_ulonglong(0)
            drive = os.path.splitdrive(os.path.abspath(db_path))[0] + "\\"
            ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                ctypes.c_wchar_p(drive), ctypes.byref(free), None, None)
            out["disk_free_gb"] = round(free.value / 1073741824.0, 1)
        except Exception:
            pass
    except Exception:
        pass
    return out


def health_flag(db_path=DEFAULT_DB):
    d = {"db_mb": 0.0, "wal_mb": 0.0, "shm_mb": 0.0,
         "snap_n": 0, "snap_age_h": None, "clean": None, "snap_latest": ""}
    try:
        for k, f in (("db_mb", db_path), ("wal_mb", db_path + "-wal"),
                     ("shm_mb", db_path + "-shm")):
            if os.path.exists(f):
                d[k] = round(os.path.getsize(f) / 1048576.0, 2)
        sn = os.path.join(os.path.dirname(os.path.abspath(db_path)), "snapshots")
        if os.path.isdir(sn):
            files = [f for f in os.listdir(sn) if f.endswith(".db")]
            d["snap_n"] = len(files)
            if files:
                latest = max(files,
                             key=lambda f: os.path.getmtime(os.path.join(sn, f)))
                d["snap_latest"] = latest
                d["snap_age_h"] = round(
                    (time.time() - os.path.getmtime(os.path.join(sn, latest)))
                    / 3600.0, 1)
        sf = db_path + ".state.json"
        if os.path.exists(sf):
            with open(sf, "r", encoding="utf-8") as fh:
                d["clean"] = json.load(fh).get("clean")
    except Exception:
        pass
    return d


def card_identity(db_path=DEFAULT_DB):
    """左栏要用的：名字、第几天、实例编号、封印、记忆数。"""
    out = {"name": "", "nick": "", "qq": "", "day": None,
           "instance": "", "sealed": False, "mem": 0, "birth_ts": None,
           "avatar_ver": None, "avatar": False}
    try:
        c = _conn(db_path)
        for r in c.execute("SELECT key, val FROM core_zone").fetchall():
            if r["key"] == "card_name":
                out["name"] = r["val"]
            elif r["key"] == "user_nickname":
                out["nick"] = r["val"]
            elif r["key"] == "user_qq":
                out["qq"] = r["val"]
            elif r["key"] == "avatar_ver":
                out["avatar_ver"] = r["val"]
        r = c.execute("SELECT COUNT(*) AS n FROM identity_anchor").fetchone()
        out["sealed"] = int(r["n"]) >= 6
        r = c.execute("SELECT COUNT(*) AS n FROM mem").fetchone()
        out["mem"] = int(r["n"])
        r = c.execute("SELECT ts, self_hash FROM timeline ORDER BY seq ASC"
                      " LIMIT 1").fetchone()
        if r is not None:
            out["birth_ts"] = round(_f(r["ts"]), 1)
            out["instance"] = "WG-" + (r["self_hash"] or "00000000")[:8].upper()
            out["day"] = int((time.time() - _f(r["ts"])) // DAY) + 1
        c.close()
    except Exception:
        pass
    try:
        av = os.path.join(os.path.dirname(os.path.abspath(db_path)), "avatar.png")
        out["avatar"] = os.path.exists(av)
        if out["avatar"]:
            out["avatar_ver"] = str(int(os.path.getmtime(av)))
    except Exception:
        out["avatar"] = False
    # 自我叙述：星云中心那颗"本体"点开时要显示它
    try:
        c = _conn(db_path)
        for r in c.execute("SELECT key, val FROM core_zone").fetchall():
            if r["key"] == "self_name":
                out["self_name"] = r["val"]
            elif r["key"] == "self_narrative":
                out["self_narrative"] = r["val"]
            elif r["key"] == "persona":
                out["persona"] = r["val"]
        c.close()
    except Exception:
        pass
    return out


def live_hormones(db_path=DEFAULT_DB):
    """当前激素值 —— tick 时写在 meta 里，页面每 15 秒刷一次就能看到漂移。"""
    out = {}
    try:
        c = _conn(db_path)
        for r in c.execute("SELECT key, val FROM meta").fetchall():
            if r["key"] in ("h_sero", "h_dopa", "h_cort", "h_oxy"):
                try:
                    out[r["key"][2:]] = round(float(r["val"]), 4)
                except Exception:
                    pass
        c.close()
    except Exception:
        pass
    return out


def forget_curve(db_path=DEFAULT_DB):
    """遗忘曲线：直接调 card2.weight_now 算，跟真卡用的是同一套公式。

    不自己另写一份衰减公式 —— 那样画出来的曲线只是"看起来像"，
    改常量时图和实际行为会悄悄对不上。
    """
    import card2
    now = time.time()
    days = [0, 1, 2, 3, 5, 7, 10, 14, 21, 30, 45, 60, 90, 120, 180, 270, 371, 540]
    out = {"days": days, "short": [], "mid": [], "long": [], "core": []}
    for d in days:
        t = now - d * 86400.0
        out["short"].append(round(card2.weight_now(1.0, card2.POOL_SHORT, t, now), 4))
        out["mid"].append(round(card2.weight_now(1.0, card2.POOL_MEDIUM, t, now), 4))
        out["long"].append(round(card2.weight_now(1.0, card2.POOL_LONG, t, now), 4))
        out["core"].append(round(card2.weight_now(1.0, card2.POOL_LONG, t, now,
                                                 tier="core_mutable"), 4))
    return out


def media_curve(db_path=DEFAULT_DB):
    """画面自己的那条曲线：跟文字并排画，才看得出「先忘样子、后忘事件」。

    同样不另写公式 —— 调 card2.media_weight_now，和 tick 里删帧用的是同一个函数。
    """
    import card2
    now = time.time()
    days = [0, 1, 2, 3, 5, 7, 10, 14, 21, 30, 45, 60, 90, 120, 180, 270, 371, 540]
    img = []
    for d in days:
        img.append(round(card2.media_weight_now(
            1.0, now - d * 86400.0, now), 4))
    return {"days": days, "image": img,
            "drop_w": card2.MEDIA_DROP_W,
            "b": card2.MEDIA_DECAY_B,
            "tau_days": card2.MEDIA_TAU_DAYS,
            "text_mid": forget_curve(db_path)["mid"]}


# ── 实时系统指标（CPU / 内存 / 磁盘读写 / 网络）─────────────────────
# 放在控制台这一侧：这些数字跟卡没关系，就是"这台机器此刻的呼吸"。
# psutil 采一次只要几毫秒，2 秒一次对算力几乎没影响。
_SYS_HIST = []          # [{"t","cpu","mem","dr","dw",...}]
_SYS_PREV = {}          # 上一次的磁盘/网络累计字节，用来算速率


def sys_sample(maxlen=150):
    """采一次系统指标。disk/net 的速率要两次采样相减，所以第一次是 0。"""
    now = time.time()
    try:
        import psutil
    except Exception:
        return {"ok": False, "why": "psutil 没装"}
    try:
        # CPU 自己算差值：psutil.cpu_percent(interval=None) 在多线程 HTTP
        # 服务里每次都当成"第一次调用"，永远返回 0.0（实测就是这样）。
        ct = psutil.cpu_times()
        tot = (ct.user + ct.system + ct.idle + getattr(ct, "iowait", 0.0)
               + getattr(ct, "irq", 0.0) + getattr(ct, "softirq", 0.0)
               + getattr(ct, "steal", 0.0))
        busy = tot - ct.idle
        pc = _SYS_PREV.get("cpu")
        if pc is None:
            cpu = 0.0
        else:
            dt_c = max(1e-6, tot - pc[0])
            cpu = 100.0 * max(0.0, busy - pc[1]) / dt_c
        _SYS_PREV["cpu"] = (tot, busy)
        vm = psutil.virtual_memory()
        d = psutil.disk_io_counters()
        n = psutil.net_io_counters()
        prev = _SYS_PREV.get("v")
        dt = max(0.2, now - (_SYS_PREV.get("t") or now))
        if prev is None:
            dr = dw = nu = nd = 0.0
        else:
            dr = max(0.0, (d.read_bytes - prev[0]) / dt)
            dw = max(0.0, (d.write_bytes - prev[1]) / dt)
            nu = max(0.0, (n.bytes_sent - prev[2]) / dt)
            nd = max(0.0, (n.bytes_recv - prev[3]) / dt)
        _SYS_PREV["v"] = (d.read_bytes, d.write_bytes, n.bytes_sent, n.bytes_recv)
        _SYS_PREV["t"] = now
        cur = {"t": round(now, 1), "cpu": round(cpu, 1),
               "mem": round(vm.percent, 1),
               "mem_used": int(vm.used / 1048576),
               "mem_total": int(vm.total / 1048576),
               "swap": round(psutil.swap_memory().percent, 1),
               "dr": int(dr), "dw": int(dw),
               "nu": int(nu), "nd": int(nd),
               "procs": len(psutil.pids())}
        _SYS_HIST.append(cur)
        if len(_SYS_HIST) > maxlen:
            del _SYS_HIST[:-maxlen]
        return {"ok": True, "now": cur, "hist": list(_SYS_HIST)}
    except Exception as e:
        return {"ok": False, "why": str(e)}


def sys_live():
    """给前端每 2 秒轮询用：当前值 + 最近 5 分钟的历史。"""
    s = sys_sample()
    if not s.get("ok"):
        return s
    return {"ok": True, "now": s["now"], "hist": s["hist"][-150:]}


def card_integrity(db_path=DEFAULT_DB):
    """链与锚的体检读数（只读，绝不写锚）。

    为什么单独做一份、不复用 anchor.check()：那个函数在某些分支里会顺手
    把锚写一遍（boot 时才该做的事）。控制台每 15 秒刷一次，绝不能因为
    "看了一眼"就动了锚 —— 那就等于自己给自己盖章。
    """
    import anchor as _anchor

    out = {"ok": False, "verdict": "?", "reason": "", "card_seq": 0, "card_head": "",
           "card_count": 0, "local_seq": 0, "local_head": "", "ext_seq": 0,
           "ext_head": "", "pack": None, "where": None}
    try:
        local_p, ext_p = _anchor._paths(db_path)
        local = _anchor._read_json(local_p) or {}
        ext = _anchor._read_json(ext_p) or {}
        state = _anchor.read_card_state(db_path)
        out["card_seq"] = int(state.get("head_seq") or 0)
        out["card_head"] = (state.get("head_hash") or "")[:12]
        out["card_count"] = int(state.get("count") or 0)
        out["local_seq"] = int(local.get("head_seq") or 0)
        out["local_head"] = (local.get("head_hash") or "")[:12]
        out["ext_seq"] = int(ext.get("head_seq") or 0)
        out["ext_head"] = (ext.get("head_hash") or "")[:12]

        best = local if out["local_seq"] >= out["ext_seq"] else ext
        b_seq = int(best.get("head_seq") or 0)
        b_head = best.get("head_hash") or ""
        b_count = int(best.get("count") or 0)
        c_seq, c_head, c_count = out["card_seq"], state.get("head_hash") or "", out["card_count"]

        if not b_seq:
            verdict, reason = "first", "还没有锚（第一次）"
        elif c_seq < b_seq:
            verdict, reason = "rollback", "卡里的链头只到 seq=%d，锚记的是 seq=%d" % (c_seq, b_seq)
        elif c_count > b_count and b_seq and c_seq <= b_seq and c_head != b_head:
            verdict, reason = "replaced", "条数 %d→%d 但链头序号没前进（锚 seq=%d）" % (b_count, c_count, b_seq)
        elif c_head != b_head and c_seq == b_seq:
            verdict, reason = "replaced", "同一条 seq 上链头对不上"
        else:
            verdict, reason = "ok", "跟锚一致（seq=%d）" % c_seq
        out["verdict"], out["reason"] = verdict, reason
        out["ok"] = verdict in ("ok", "first")
    except Exception as e:
        out["reason"] = "读锚失败：%s" % e

    # 搬家包：build 出来的清单，或者落地之后留下的 WHEREAMI
    try:
        card_dir = os.path.dirname(os.path.abspath(db_path))
        for p in (os.path.join(card_dir, "_portable", "PORTABLE-MANIFEST.json"),
                  os.path.join(card_dir, "PORTABLE-MANIFEST.json")):
            if os.path.exists(p):
                man = json.load(open(p, encoding="utf-8"))
                files = man.get("files") or []
                out["pack"] = {
                    "n": len(files),
                    "mb": round(sum(int(f.get("size") or 0) for f in files) / 1048576.0, 1),
                    "built_at": man.get("built_at"),
                    "wal_before": (man.get("wal") or {}).get("wal_before_mb"),
                }
                break
        for p in (os.path.join(card_dir, "WHEREAMI.json"),):
            if os.path.exists(p):
                w = json.load(open(p, encoding="utf-8"))
                out["where"] = {"landed_at": w.get("landed_at"),
                                "landed_from": w.get("landed_from")}
    except Exception:
        pass
    return out


import chatcfg as _chatcfg

def talk_state(db_path=DEFAULT_DB):
    """接话这件事实况：主动开口的次数/配额，以及「不每句都回」最近的判断。
    只读 qq_state，不碰卡、不写任何东西。"""
    import json as _json
    card_root = os.path.dirname(os.path.abspath(db_path))
    qs = os.path.join(card_root, "qq_state")
    st = {}
    try:
        with open(os.path.join(qs, "proactive.json"), "r", encoding="utf-8") as f:
            st = _json.load(f)
    except Exception:
        st = {}
    cap, cool = 8, 20
    try:
        with open(os.path.join(qs, "proactive_cfg.json"), "r", encoding="utf-8") as f:
            cf = _json.load(f)
        cap = int(cf.get("cap", cap))
        cool = int(cf.get("cooldown_min", cool))
    except Exception:
        pass
    rows = []
    try:
        with open(os.path.join(qs, "todo.jsonl"), "r", encoding="utf-8") as f:
            for ln in f.readlines()[-80:]:
                try:
                    rows.append(_json.loads(ln))
                except Exception:
                    pass
    except Exception:
        pass
    now = time.time()
    last = float(st.get("last_open_ts") or 0)
    quiet_n = sum(1 for r in rows if r.get("quiet"))
    pinged_n = sum(1 for r in rows if r.get("pinged"))
    grp = {}
    for r in rows:
        k = r.get("group_name") or ("私聊" if r.get("kind") == "private" else "群")
        grp[k] = grp.get(k, 0) + 1
    grp = sorted(grp.items(), key=lambda kv: -kv[1])[:6]
    return {
        "ok": True,
        "cap": cap,
        "cooldown_min": cool,
        "day": st.get("day") or "",
        "count": int(st.get("count") or 0),
        "used": st.get("used") or [],
        "last_open_ts": round(last, 0),
        "last_open_age_min": (round((now - last) / 60.0, 1) if last else None),
        "recent_n": len(rows),
        "quiet_n": quiet_n,
        "pinged_n": pinged_n,
        "groups": grp,
        "open_window": "07:30 - 23:30",
    }


def overview(db_path=DEFAULT_DB, hours=None):
    """一整页要的数据，一次给齐 —— 前端只发一个请求。"""
    try:
        c = _conn(db_path)
        pools = {}
        for r in c.execute("SELECT pool, COUNT(*) AS n, AVG(w) AS aw"
                           " FROM mem GROUP BY pool").fetchall():
            pools[r["pool"] or "long"] = {"n": int(r["n"]),
                                          "aw": round(_f(r["aw"]), 3)}
        c.close()
    except Exception:
        pools = {}
    return {
        "ok": True,
        "identity": card_identity(db_path),
        "hlog": hormones_series(db_path),
        "mem_by_day": mem_by_day(db_path),
        "regions": regions(db_path),
        "storage": storage(db_path),
        # 两条遗忘曲线并排给前端（文字 vs 画面），公式都来自卡本身，控制台不另写一份
        "curves": (lambda: {
            "forget": forget_curve(db_path),
            "media": media_curve(db_path),
        })(),
        "health": health_flag(db_path),
        "pools": pools,
        "metrics": card_metrics(db_path),
        "integrity": card_integrity(db_path),
        "hormones": live_hormones(db_path),
        "forget": forget_curve(db_path),
        "sys": sys_sample(),
        "talk": talk_state(db_path),
        # 情绪页：快反应层的曲线（心率/交感/五条调质/RPE/安全感）。
        # 当前这具身体的读数由前端再问一次 /viscera —— 那里走的是卡自己的
        # viscera 模块，控制台不重写第二份公式。
        # 窗口越短越要看密度：5 分钟的窗口只给 600 个点等于「一秒一格」，
        # 看着像没动 —— 小窗口把上限抬上去。
        "emotion": emotion_series(db_path, hours=(float(hours) if hours else 72.0),
                                  limit=(1500 if (hours and float(hours) <= 6) else 900)),
        "chat": {"cfg": _chatcfg.load_cfg(), "spec": _chatcfg.spec_json()},
        "generated": round(time.time(), 1),
    }


# ──────────────────────────────────────────────────────
# 24 种激素的中英文名：控制台里要能认出来是哪一条（对话里永不出现）。
HORMONE_LABELS = {'dopa': ('多巴胺', 'Dopamine'), 'nora': ('去甲肾上腺素', 'Norepinephrine'), 'cort': ('皮质醇', 'Cortisol'), 'sero': ('血清素', 'Serotonin'), 'oxy': ('催产素', 'Oxytocin'), 'endo': ('内啡肽', 'Endorphin'), 'gonad': ('促性腺激素', 'Gonadotropin'), 'mel': ('褪黑素', 'Melatonin'), 'gaba': ('γ-氨基丁酸', 'GABA'), 'ach': ('乙酰胆碱', 'Acetylcholine'), 'adh': ('抗利尿激素', 'Vasopressin'), 'dop': ('多巴', 'L-DOPA'), 'cck': ('胆囊收缩素', 'Cholecystokinin'), 'gh': ('生长激素', 'Growth hormone'), 'prl': ('催乳素', 'Prolactin'), 'hist': ('组胺', 'Histamine'), 'adeno': ('腺苷', 'Adenosine'), 'kyn': ('犬尿氨酸', 'Kynurenine'), 'npy': ('神经肽Y', 'Neuropeptide Y'), 'nps': ('神经肽S', 'Neuropeptide S'), 'ofq': ('孤啡肽', 'Nociceptin'), 'enk': ('脑啡肽', 'Enkephalin'), 'orex': ('食欲素', 'Orexin'), 'testo': ('睾酮', 'Testosterone')}


PAGE = r"""<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>小鲸鱼 · 控制台</title>
<style>
:root{
  --bg:#0e1116; --panel:#151a21; --panel2:#1a2029; --line:#242c37;
  --fg:#d5dbe4; --dim:#7c8697; --dim2:#5b6474;
  --cyan:#5fb3d4; --blue:#6f9de8; --ok:#66c08a; --warn:#d8a657; --bad:#e06c75;
  --pink:#c78bb8; --green:#6fbf73;
  --z:1;
}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:radial-gradient(1200px 600px at 20% -10%,#16202b 0%,var(--bg) 60%);
  color:var(--fg);font:13px/1.55 "Segoe UI","Microsoft YaHei",system-ui,sans-serif;
  overflow:hidden}
/* 全屏大屏要放大字号，但 CSS zoom 会把 100vh/100vw 也一起放大 ——
   #app 高 2160*1.7=3672px，被 body 的 overflow:hidden 剪掉，于是
   小窗口能滚、全屏反而滚不动、右边还被切。所以宽高都先除回 zoom。 */
#app{display:flex;zoom:var(--z,1);
  width:calc(100vw / var(--z,1));height:calc(100vh / var(--z,1))}

/* ── 左栏：这个人 ── */
aside{width:274px;flex:none;border-right:1px solid var(--line);
  background:linear-gradient(180deg,#141a22 0%,#11161d 100%);
  display:flex;flex-direction:column;padding:16px 16px 14px}
.avatar{width:100%;aspect-ratio:1/1;border-radius:14px;overflow:hidden;
  border:1px solid var(--line);background:#0d1218;position:relative}
.avatar svg{width:100%;height:100%;display:block}
.avatar img{width:100%;height:100%;object-fit:cover;display:block}
.avatar .glow{position:absolute;inset:0;
  background:radial-gradient(circle at 50% 30%,rgba(95,179,212,.18),transparent 65%)}
h1{font-size:26px;margin:14px 0 6px;font-weight:600;letter-spacing:2px}
.edit{font-size:11.5px;color:var(--dim2);margin-bottom:10px;letter-spacing:.5px}
.edit span{cursor:pointer}
.edit span:hover{color:var(--accent)}
.edit .sep{cursor:default;opacity:.5;margin:0 5px}
.edit .sep:hover{color:var(--dim2)}
/* 设置页的表单 */
.frow{display:flex;align-items:center;gap:12px;padding:13px 0;
  border-bottom:1px solid var(--line)}
.frow:last-child{border-bottom:0}
.frow .fl{width:96px;flex:none;color:var(--dim);font-size:12.5px}
.frow .fc{flex:1;display:flex;align-items:center;gap:9px}
.frow input[type=text]{flex:1;background:#0d1218;border:1px solid var(--line);
  border-radius:7px;color:var(--fg);padding:8px 11px;font-size:13px;
  font-family:inherit;outline:none}
.frow input[type=text]:focus{border-color:var(--accent)}
.frow .btn{width:auto;padding:7px 15px;font-size:12.5px}
.avprev{width:64px;height:64px;border-radius:10px;border:1px solid var(--line);
  object-fit:cover;background:#0d1218;flex:none}
.fhint{color:var(--dim2);font-size:11.5px;margin-top:4px;line-height:1.6}
/* 上下文组合页：七种模式 × 六维权重表（2026-11-05 加） */
.wt{width:100%;border-collapse:collapse;font-size:12px}
.wt th{color:var(--dim2);font-size:10.5px;letter-spacing:.6px;font-weight:600;
  padding:0 4px 7px;text-align:center;white-space:nowrap}
.wt th:first-child{text-align:left;padding-left:2px}
.wt td{padding:3px 4px;text-align:center;border-top:1px solid #1a222c}
.wt td:first-child{text-align:left;color:#c2cbd8;white-space:nowrap;
  padding-left:2px}
.wt input{width:62px;background:#0d1218;border:1px solid var(--line);
  color:var(--fg);border-radius:6px;padding:4px 5px;font-size:12px;
  font-variant-numeric:tabular-nums;text-align:center}
.wt input:focus{outline:none;border-color:var(--cyan)}
.wt input.chg{border-color:rgba(232,180,74,.65);color:var(--warn)}
.wt .mrk{color:var(--warn);font-size:9px;vertical-align:super;margin-left:2px}
.wt .pv{color:var(--dim);font-size:11px;white-space:nowrap;
  font-variant-numeric:tabular-nums}
.ctxrow{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.ctxrow input.n{width:96px;background:#0d1218;border:1px solid var(--line);
  color:var(--fg);border-radius:6px;padding:6px 8px;font-size:12.5px;
  font-variant-numeric:tabular-nums}
.ctxrow input.n:focus{outline:none;border-color:var(--cyan)}
.ctxrow .lb{color:var(--dim);font-size:12px;width:118px;flex:none}
.ctxrow input.rng{flex:1;min-width:150px;max-width:420px;height:4px;appearance:none;
  background:#1b2530;border-radius:3px;outline:none;cursor:pointer}
.ctxrow input.rng::-webkit-slider-thumb{appearance:none;width:14px;height:14px;
  border-radius:50%;background:var(--cyan);border:2px solid #0b1116;cursor:pointer}
.ctxrow input.rng::-moz-range-thumb{width:12px;height:12px;border-radius:50%;
  background:var(--cyan);border:2px solid #0b1116;cursor:pointer}
/* 模型对照表：默认收起来，需要时点开 —— 平时不该占着一屏高度 */
.mtab{width:100%;max-width:520px;border-collapse:collapse;font-size:12px;margin-top:2px}
.mtab th{color:var(--dim);font-size:10.5px;letter-spacing:.6px;text-align:left;
  padding:0 4px 6px;font-weight:600}
.mtab td{padding:4px;border-top:1px solid #1a222c;color:#c2cbd8;
  font-variant-numeric:tabular-nums}
.mtab td.s{color:var(--cyan);text-align:right;padding-right:10px}
.mtab tr.me td{color:var(--fg)}
.mtab tr.me td:first-child::after{content:" ← 现在用的";
  color:var(--ok);font-size:10px;margin-left:5px}
.sw{display:inline-flex;align-items:center;gap:8px;cursor:pointer;user-select:none}
.sw i{width:34px;height:18px;border-radius:10px;background:#1b2530;position:relative;
  transition:background .15s;flex:none;display:inline-block}
.sw i::after{content:"";position:absolute;top:2px;left:2px;width:14px;height:14px;
  border-radius:50%;background:#63707f;transition:all .15s}
.sw.on i{background:rgba(47,208,125,.28)}
.sw.on i::after{left:18px;background:var(--ok)}
.sw b{color:var(--dim);font-weight:400;font-size:12px}
.sw.on b{color:var(--fg)}
.ctxbar{display:grid;gap:6px;margin-top:2px}
.ctxbar .r{display:grid;grid-template-columns:132px 1fr 58px;gap:9px;
  align-items:center;font-size:12px}
.ctxbar .r .nm{color:var(--dim);white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.ctxbar .r .nm em{color:var(--dim2);font-style:normal;font-size:10.5px}
.ctxbar .r .tr{height:9px;background:#0e131a;border-radius:5px;overflow:hidden}
.ctxbar .r .tr i{display:block;height:100%;border-radius:5px;
  background:linear-gradient(90deg,#2f6e8c,var(--cyan))}
.ctxbar .r .nu{text-align:right;color:#c9d3e0;
  font-variant-numeric:tabular-nums}
.ctxtrace{color:var(--dim2);font-size:11.5px;margin-top:10px;line-height:1.7}
.fok{color:var(--ok);font-size:12px}
.fbad{color:var(--bad);font-size:12px}
.pill{display:flex;align-items:center;gap:8px;background:var(--panel2);
  border:1px solid var(--line);border-radius:8px;padding:7px 11px;font-size:12px}
.dot{width:7px;height:7px;border-radius:50%;background:var(--ok);
  box-shadow:0 0 8px var(--ok)}
.dot.off{background:var(--bad);box-shadow:0 0 8px var(--bad)}
.dot.wait{background:var(--warn);box-shadow:0 0 8px var(--warn)}
.kv{margin-top:14px;display:grid;gap:9px}
.kv .row{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.kv .k{color:var(--dim);font-size:12px;white-space:nowrap}
.kv .v{font-variant-numeric:tabular-nums;font-size:13px;text-align:right;
  color:#e6ebf2;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.kv .v.mono{font-family:Consolas,monospace;font-size:12px;color:var(--cyan)}
.sp{flex:1}
.btn{background:var(--panel2);color:var(--fg);border:1px solid var(--line);
  border-radius:7px;padding:8px 12px;font:inherit;font-size:12.5px;cursor:pointer;
  width:100%;transition:.14s}
.btn:hover{border-color:var(--cyan);color:#fff;background:#1e2733}
.btn.danger{border-color:#5a3339;color:var(--bad)}
.btn.danger:hover{background:#2a1c20;border-color:var(--bad)}
.btn:disabled{opacity:.4;cursor:not-allowed}

/* ── 右栏 ── */
main{flex:1;min-width:0;display:flex;flex-direction:column}
nav{display:flex;align-items:center;gap:4px;padding:0 18px;
  border-bottom:1px solid var(--line);background:rgba(21,26,33,.7);
  backdrop-filter:blur(6px);height:52px;flex:none}
nav .tab{padding:0 14px;height:52px;display:flex;align-items:center;
  font-size:13px;color:var(--dim);cursor:pointer;border-bottom:2px solid transparent;
  transition:.14s;white-space:nowrap}
nav .tab:hover{color:var(--fg)}
.winwrap{display:flex;align-items:center;gap:4px;margin-right:10px}
.winlab{color:#6d7d92;font-size:11px;letter-spacing:.5px;margin-right:2px}
.wseg{background:transparent;border:1px solid #24313f;color:#8fa3ba;font-size:11px;
      padding:2px 7px;border-radius:5px;cursor:pointer;line-height:1.5}
.wseg:hover{border-color:#33506b;color:#c6d6e6}
.wseg.on{background:#16303f;border-color:#2fd07d;color:#7ef0b0}
.wseg.live.on{background:#3a1b22;border-color:#ff5f6d;color:#ff9aa3}
.fsbtn{margin-left:12px;width:26px;height:26px;display:flex;align-items:center;
  justify-content:center;border:1px solid var(--line);border-radius:7px;
  color:var(--dim);cursor:pointer;font-size:13px;line-height:1}
.fsbtn:hover{color:var(--cyan);border-color:var(--cyan)}

nav .tab.on{color:var(--cyan);border-bottom-color:var(--cyan)}
nav .sp{flex:1}
nav .stamp{color:var(--dim2);font-size:11.5px;font-variant-numeric:tabular-nums}
.grid{flex:1;overflow:auto;padding:16px 26px 36px 18px;display:grid;gap:14px;
  grid-template-columns:repeat(12,1fr);
  grid-auto-rows:min-content;align-content:start;
  scrollbar-gutter:stable}
/* 状态总览才让行拉伸填满视口（卡片内部再由 canvas 吃掉余量）；
   其它页行高只由内容决定，免得几张卡被拉成一条条空长条。 */
.grid.fit{grid-auto-rows:minmax(auto,1fr);align-content:stretch}
.card{background:var(--panel);border:1px solid var(--line);border-radius:11px;
  padding:13px 15px 12px;min-width:0;position:relative;overflow:hidden;
  display:flex;flex-direction:column}
.card canvas{display:block;width:100%;height:0;flex:1 1 auto;min-height:64px}
#neb{height:520px;min-height:0;flex:0 0 520px}
#cvTT,#cvTK,#cvMW,#cvMWr,#cvMS{min-height:170px}
#cvFC{min-height:230px}
#cvMD{min-height:210px}
#cvSrc,#cvR,#cvRadar,#cvSyn,#cvWH,#cvRec{min-height:200px}
#cvH,#cvPad{min-height:150px}
#cvSys{min-height:190px}
#cvIO{min-height:150px}
.live{font-size:10px;color:var(--ok);border:1px solid rgba(102,192,138,.35);
  border-radius:6px;padding:1px 5px;letter-spacing:.5px}
.card h2,.card h2 *{flex:0 0 auto}
.card:before{content:"";position:absolute;inset:0 0 auto 0;height:1px;
  background:linear-gradient(90deg,transparent,rgba(95,179,212,.35),transparent)}
.card h2{margin:0 0 10px;font-size:12px;color:var(--dim2);font-weight:600;
  letter-spacing:1.4px;display:flex;align-items:center;gap:8px}
.card h2 .tag{font-size:10.5px;letter-spacing:.5px;color:var(--dim2);
  border:1px solid var(--line);border-radius:4px;padding:1px 5px}
.c4{grid-column:span 4}.c6{grid-column:span 6}
.c8{grid-column:span 8}.c12{grid-column:span 12}
canvas{display:block;width:100%}
.legend{display:flex;gap:14px;flex-wrap:wrap;margin-top:8px;font-size:11.5px;
  color:var(--dim)}
.legend i{display:inline-block;width:8px;height:8px;border-radius:2px;
  margin-right:5px;vertical-align:middle}
.bars{display:grid;gap:8px}
.bars .b{display:grid;grid-template-columns:64px 1fr 52px;gap:9px;
  align-items:center;font-size:12px}
.bars .b .nm{color:var(--dim);white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.bars .b .tr{height:7px;background:#0e131a;border-radius:4px;overflow:hidden}
.bars .b .tr i{display:block;height:100%;border-radius:4px;
  background:linear-gradient(90deg,#3d7fa0,var(--cyan))}
.bars .b .nu{text-align:right;color:#c9d3e0;font-variant-numeric:tabular-nums}
.logs{display:grid;gap:6px;font-size:12px;max-height:190px;overflow:auto}
.logs .l{display:grid;grid-template-columns:16px 54px 1fr;gap:9px;
  align-items:baseline;padding:3px 0;border-bottom:1px dotted #1c232c}
.logs .l:last-child{border-bottom:0}
.logs .l .ic{color:var(--ok)}
.logs .l .ic.b{color:var(--bad)}
.logs .l .ic.w{color:var(--warn)}
.logs .l .sq{color:var(--dim2);font-family:Consolas,monospace;font-size:11px}
.logs .l .nt{color:#c2cbd8;overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(96px,1fr));
  gap:9px}
.tile{background:#111721;border:1px solid #1e2731;border-radius:9px;
  padding:9px 10px}
.tile .tk{color:var(--dim2);font-size:11px;letter-spacing:.5px}
.tile .tv{font-size:18px;font-variant-numeric:tabular-nums;margin-top:2px}
.tile .tv.s{font-size:14px}
.tile .tv.ok{color:var(--ok)}.tile .tv.bad{color:var(--bad)}
.tile .tv.warn{color:var(--warn)}
#neb{height:300px}
.empty{color:var(--dim2);font-size:12px;padding:12px 0;text-align:center}
.mask{position:fixed;inset:0;background:rgba(8,10,14,.72);display:none;
  align-items:center;justify-content:center;z-index:99}
.mask.on{display:flex}
.dlg{background:var(--panel);border:1px solid var(--line);border-radius:12px;
  padding:20px 22px;width:420px;max-width:92vw}
.dlg h3{margin:0 0 10px;font-size:15px}
.dlg p{color:var(--dim);font-size:12.5px;margin:0 0 14px;line-height:1.7}
.dlg .btns{display:flex;gap:9px;justify-content:flex-end}
.dlg .btns .btn{width:auto;padding:7px 16px}
.msg{color:var(--warn);font-size:12px;min-height:16px;margin-bottom:8px}

/* ══════════════════════════════════════════════════════════
   2026-11-01 仪表化改版：数字 + 英文/中英混排 + 绿红状态
   改版原则：颜色只表达状态（正常绿 / 异常红 / 提醒黄），
   其余一律冷色；数字用等宽字体，标签用大写字母间距拉开。
   ══════════════════════════════════════════════════════════ */
:root{
  --ok:#2fd07d; --bad:#ff5f6d; --warn:#e8b44a;
  --cyan:#57c8f0; --cyan2:#8fe3ff; --violet:#9d8bff;
  --num:#cfe6f5; --lab:#5f7086; --line2:#1b2530; --panel3:#0f151d;
}
body{font-family:"Inter","Segoe UI","Microsoft YaHei",system-ui,sans-serif;
  background:
    radial-gradient(900px 420px at 14% -8%,rgba(87,200,240,.10),transparent 62%),
    radial-gradient(760px 420px at 92% 4%,rgba(157,139,255,.08),transparent 60%),
    #0a0e13}
/* 细网格底纹，给一点仪器感 */
#app{background-image:linear-gradient(rgba(120,180,220,.028) 1px,transparent 1px),
  linear-gradient(90deg,rgba(120,180,220,.028) 1px,transparent 1px);
  background-size:34px 34px}

.card{padding:12px 14px 11px;border-radius:10px;border-color:#1a232e;
  background:linear-gradient(180deg,rgba(23,31,41,.92),rgba(16,22,30,.92))}
.card h2{font:600 10.5px/1 "Inter",sans-serif;letter-spacing:2.2px;
  text-transform:uppercase;color:#63758c;margin-bottom:9px}
.card h2 .tag{font:10px/1 Consolas,monospace;letter-spacing:.6px;
  color:#7d8fa4;border-color:#1f2a36;background:#0e141b}

/* 数字方块：英文标签在上，大数字居中，中文注在下面 */
.tiles{gap:8px;grid-template-columns:repeat(auto-fit,minmax(104px,1fr))}
.tile{background:#0d131a;border:1px solid #18212b;border-radius:8px;
  padding:8px 10px 7px;position:relative;overflow:hidden}
.tile:after{content:"";position:absolute;left:0;top:0;bottom:0;width:2px;
  background:#20303d}
.tile.ok:after{background:var(--ok);box-shadow:0 0 10px var(--ok)}
.tile.bad:after{background:var(--bad);box-shadow:0 0 10px var(--bad)}
.tile.warn:after{background:var(--warn)}
.tile .tk{font:9.5px/1.3 Consolas,monospace;letter-spacing:1.5px;
  text-transform:uppercase;color:#5b6c80}
.tile .tv{font:600 21px/1.25 Consolas,"Segoe UI",monospace;margin-top:3px;
  color:var(--num);font-variant-numeric:tabular-nums;letter-spacing:.5px}
.tile .tv.s{font-size:15px;line-height:1.5}
.tile .tv.ok{color:var(--ok)}.tile .tv.bad{color:var(--bad)}
.tile .tv.warn{color:var(--warn)}
.tile .tn{font-size:10px;color:#556579;margin-top:1px;letter-spacing:.4px}

/* 横向条（池子 / 存储 / 高频记忆） */
.hb{display:grid;grid-template-columns:92px 1fr 96px;gap:9px;
  align-items:center;font-size:11.5px;padding:2px 0}
.hb .nm{color:#7c8ea3;font:10.5px/1.4 Consolas,monospace;
  letter-spacing:.8px;text-transform:uppercase;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.hb .tr{height:6px;background:#0b1117;border-radius:3px;overflow:hidden;
  position:relative}
.hb .tr i{display:block;height:100%;border-radius:3px;
  background:linear-gradient(90deg,#1f6b8c,var(--cyan));
  box-shadow:0 0 8px rgba(87,200,240,.35)}
.hb .nu{text-align:right;color:#b9cbdb;font:11px/1 Consolas,monospace;white-space:nowrap;
  font-variant-numeric:tabular-nums}
.hb .nu em{font-style:normal;color:#5b6c80;font-size:9.5px}
.hb .nu em.up{color:var(--ok)}
.hb .nu em.dn{color:var(--bad)}
.hbar-wrap{display:grid;gap:3px;padding:1px 0}

/* 键值账本区 */
.ledger{display:grid;grid-template-columns:repeat(auto-fit,minmax(122px,1fr));
  gap:1px;background:#151d26;border:1px solid #18212b;border-radius:8px;
  overflow:hidden}
.ledger .li{background:#0d131a;padding:8px 10px}
.ledger .lk{font:9.5px/1.3 Consolas,monospace;letter-spacing:1.4px;
  text-transform:uppercase;color:#586a7e}
.ledger .lv{font:600 15px/1.4 Consolas,monospace;color:#d3e4f2;
  font-variant-numeric:tabular-nums}
.ledger .lv small{font:10px/1 Consolas,monospace;color:#63758c;margin-left:3px}
.legend{font:10.5px/1.5 Consolas,monospace;letter-spacing:.4px;color:#6c7f94;
  gap:12px}
.inj{display:grid;grid-template-columns:62px 96px 1fr;gap:6px 8px;
  padding:5px 2px;border-bottom:1px solid #141c25;align-items:baseline;font-size:11.5px}
.inj b{font:600 11px/1.4 Consolas,monospace;color:#7f93a8}
.inj i{font:10px/1.3 Consolas,monospace;font-style:normal;letter-spacing:.6px}
.inj i.bad{color:#ff5f6d}
.inj i.warn{color:#e8b44a}
.inj span{color:#c3d4e4;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.inj em{grid-column:1/-1;font:9.5px/1.4 Consolas,monospace;font-style:normal;
  color:#54677c;padding-left:2px}
.muted{color:#54677c;font-size:11.5px}
@keyframes ledBreath{0%,100%{opacity:.5}50%{opacity:1}}
.led i{animation:ledBreath 2.8s ease-in-out infinite}
.led.warn i{animation-duration:1.9s}
.nums b{transition:color .3s ease,text-shadow .3s ease}
.nums b.hit{color:#bfefff;text-shadow:0 0 9px #57c8f0aa}
#cvPulse{width:100%;height:44px;display:block;margin-top:7px;border-radius:5px;
  background:linear-gradient(180deg,#0a1017,#0d131a);
  border:1px solid #16202b}
/* 左栏状态灯 + 小数字框（2026-11-04 加，用户要求"填一点点，别挤"） */
.mini{margin-top:10px;border-top:1px solid var(--line);padding-top:9px}
.leds{display:flex;gap:5px;margin-bottom:8px}
.led{flex:1;display:flex;align-items:center;gap:4px;justify-content:center;
  font:9px/1 Consolas,monospace;letter-spacing:.5px;color:#5b7086;
  background:#0f1620;border:1px solid #18212b;border-radius:5px;padding:5px 3px}
.led i{width:6px;height:6px;border-radius:50%;background:#37485a;flex:none;
  transition:background .3s,box-shadow .3s}
.led.ok i{background:#2fd07d;box-shadow:0 0 7px #2fd07d99}
.led.bad i{background:#ff5f6d;box-shadow:0 0 7px #ff5f6d99}
.led.warn i{background:#e8b44a;box-shadow:0 0 7px #e8b44a99}
.led.ok{color:#9fb3c6}
.nums{display:flex;gap:1px;background:#151d26;border:1px solid #18212b;
  border-radius:6px;overflow:hidden}
.nums div{flex:1;background:#0d131a;padding:6px 3px;text-align:center}
.nums b{display:block;font:600 13px/1.25 Consolas,monospace;color:#cfe6f5;
  font-variant-numeric:tabular-nums}
.nums i{display:block;font:9px/1.2 Consolas,monospace;font-style:normal;
  color:#54677c;letter-spacing:.3px;margin-top:1px}
/* 时间线最近几条：从左栏空白处放，紧凑版（原卡在网格里占一行、右边全空） */
.mlog{margin-top:9px;border-top:1px solid var(--line);padding-top:8px;
  display:flex;flex-direction:column;gap:3px;
  font:10px/1.35 Consolas,monospace;flex:1 1 auto;min-height:120px;
  overflow:hidden}
.mlog .r{display:flex;gap:5px;align-items:baseline;color:#7d8fa3}
.mlog .r i{width:5px;height:5px;border-radius:50%;background:#2fd07d;flex:none}
.mlog .r.b i{background:#ff5f6d}
.mlog .r.w i{background:#e8b44a}
.mlog .r b{color:#4f6074;font-weight:400;min-width:24px}
.mlog .r span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
  min-width:0;flex:1}
.lamps{display:flex;flex-wrap:wrap;gap:10px;padding:2px 0 8px}
.lamp{display:flex;align-items:center;gap:8px;padding:6px 12px;border-radius:8px;
  background:#0e151d;border:1px solid #1a2431;font:11.5px/1.4 Consolas,monospace}
.lamp i{width:9px;height:9px;border-radius:50%;background:#3a4a5c;box-shadow:0 0 0 0 #000}
.lamp.lm i{background:#2fd07d;box-shadow:0 0 8px #2fd07d}
.lamp.lb i{background:#57c8f0;box-shadow:0 0 8px #57c8f0}
.lamp.loff i{background:#44505f}
.lamp b{color:#d3e4f2;font-weight:600}
.lamp span{color:#6c7f94}
.diskrow{display:flex;align-items:center;gap:10px;padding:4px 0;flex-wrap:wrap}
.diskrow select{background:#0e151d;color:#cfe6f5;border:1px solid #1f2b3a;
  border-radius:6px;padding:7px 10px;font:11.5px/1 Consolas,monospace;min-width:320px}
.diskrow button{background:#16222f;color:#cfe6f5;border:1px solid #24384c;
  border-radius:6px;padding:7px 14px;font:11.5px/1 Consolas,monospace;cursor:pointer}
.diskrow button:hover{background:#1d2e3f;border-color:#2f4b66}
.logs{font-size:11.5px}
.logs .l{grid-template-columns:12px 44px 1fr;gap:8px}
.logs .l .sq{font-size:10px;color:#4f6074}
#neb{height:calc(100vh - 190px);min-height:320px}
/* 4K / 超宽屏：整页放大一档，不然 12px 的字在 3840 宽上小得像蚂蚁。
   CH() 会读 #app 的 zoom 折算高度，所以放大不会把图撑出屏幕。 */
@media(min-width:2200px){:root{--z:1.2}}
@media(min-width:2800px){:root{--z:1.45}}
@media(min-width:3400px){:root{--z:1.7}}
/* 窄屏退成一列，别把卡片挤成细条 */
@media(max-width:1180px){
  aside{width:236px}
  .c4,.c6,.c8{grid-column:span 12}
}
@media(max-width:820px){
  aside{display:none}
  .c4,.c6,.c8,.c12{grid-column:span 12}
}
</style></head><body>
<div id="app">
  <aside>
    <div class="avatar">
      <div class="glow"></div>
      <img id="avaImg" alt="" style="display:none">
      <svg id="avaDef" viewBox="0 0 200 200" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <linearGradient id="sk" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stop-color="#c8dae8"/><stop offset="1" stop-color="#8fa8bd"/>
          </linearGradient>
          <linearGradient id="hr" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stop-color="#4a90b8"/><stop offset="1" stop-color="#274a63"/>
          </linearGradient>
        </defs>
        <rect width="200" height="200" fill="#0d1218"/>
        <ellipse cx="100" cy="118" rx="52" ry="56" fill="url(#sk)"/>
        <path d="M48 118c0-38 23-62 52-62s52 24 52 62c0-8-16-13-20-22-6 9-27 12-32 4-6 9-26 7-32-2-5 9-20 12-20 20z"
              fill="url(#hr)"/>
        <ellipse cx="100" cy="140" rx="52" ry="34" fill="#0f1620" opacity=".55"/>
        <ellipse cx="80" cy="112" rx="7" ry="9" fill="#16222c"/>
        <ellipse cx="120" cy="112" rx="7" ry="9" fill="#16222c"/>
        <circle cx="82" cy="109" r="2.6" fill="#7fd4f2"/>
        <circle cx="122" cy="109" r="2.6" fill="#7fd4f2"/>
        <path d="M88 132q12 7 24 0" stroke="#3c5364" stroke-width="2.4"
              fill="none" stroke-linecap="round"/>
        <ellipse cx="66" cy="126" rx="8" ry="4.5" fill="#d98899" opacity=".35"/>
        <ellipse cx="134" cy="126" rx="8" ry="4.5" fill="#d98899" opacity=".35"/>
      </svg>
    </div>
    <h1 id="nm">小鲸鱼</h1>
    <div class="edit"><span id="btnAvatar">换头像</span>
      <span class="sep">·</span><span id="btnName">改名</span></div>
    <div class="pill"><span class="dot" id="stDot"></span>
      <span id="stTxt">连接中</span></div>
    <div class="kv">
      <div class="row"><span class="k">运行时间</span><span class="v" id="up">—</span></div>
      <div class="row"><span class="k">年龄</span><span class="v" id="age">—</span></div>
      <div class="row"><span class="k">实例编号</span><span class="v mono" id="ins">—</span></div>
      <div class="row"><span class="k">人格锁定</span><span class="v" id="seal">—</span></div>
      <div class="row"><span class="k">卡槽</span><span class="v" id="slot">—</span></div>
      <div class="row"><span class="k">记忆</span><span class="v" id="mem">—</span></div>
      <div class="row"><span class="k">心境</span><span class="v" id="mood">—</span></div>
      <div class="row"><span class="k">对谁</span><span class="v" id="who">—</span></div>
    </div>
    <div class="mini">
      <div class="leds">
        <span class="led" id="ledChain" title="时间线链"><i></i>链</span>
        <span class="led" id="ledAnchor" title="身份锚"><i></i>锚</span>
        <span class="led" id="ledSlot" title="卡槽"><i></i>卡槽</span>
        <span class="led" id="ledDisk" title="主盘 / 备份盘"><i></i>盘</span>
      </div>
      <div class="nums">
        <div><b id="mnRecall">—</b><i>召回</i></div>
        <div><b id="mnSyn">—</b><i>突触</i></div>
        <div><b id="mnNew">—</b><i>新记</i></div>
      </div>
      <canvas id="cvPulse"></canvas>
    </div>
    <div class="mlog" id="mlog"></div>
    <div class="sp"></div>
    <button class="btn danger" id="btnExit">退出实例</button>
  </aside>

  <main>
    <nav>
      <div class="tab on" data-t="dash">状态总览</div>
<div class="tab" data-t="mem">记忆</div>
      <div class="tab" data-t="ctx">上下文</div>
      <div class="tab" data-t="emo">情绪</div>
      <div class="tab" data-t="voice">语音</div>
      <div class="tab" data-t="neb">星云</div>
      <div class="tab" data-t="time">时间线</div>
      <div class="tab" data-t="talk">接话</div>
      <div class="tab" data-t="set">设置</div>
      <div class="sp"></div>
      <span class="winwrap" id="winWrap">
        <span class="winlab">窗口</span>
        <button class="wseg" data-h="0.083">5 分</button>
        <button class="wseg" data-h="1">1 时</button>
        <button class="wseg on" data-h="24">24 时</button>
        <button class="wseg" data-h="168">7 天</button>
        <button class="wseg live" id="btnLive" title="实时：每 3 秒自己刷一格">实时</button>
      </span>
      <span class="stamp" id="stamp">—</span>
      <span class="fsbtn" id="btnFull" title="全屏 / 退出全屏（F11）">⛶</span>
    </nav>
    <div class="grid" id="grid"></div>
  </main>
</div>

<div class="mask" id="mask"><div class="dlg">
  <h3 id="dlgT">确认</h3><p id="dlgB"></p>
  <div class="msg" id="dlgM"></div>
  <div class="btns"><button class="btn" id="dlgNo">取消</button>
  <button class="btn danger" id="dlgYes">确定</button></div>
</div></div>

<script>
var HNAME=__HORMONE_LABELS__;
var HBASE=__HORMONE_BASE__; /* 24 条出厂基线 */ /* 24 种激素：key → [中文, English] */
"use strict";
var API = function(p, o){ return fetch(p, o).then(function(r){ return r.json(); }); };
var S = {data:null, tab:"dash", neb:null, resp:0, hours:24, live:false};
var $ = function(s){ return document.querySelector(s); };
var RC = {episode:"#5b7fa6",fact:"#4f9d69",self:"#c9a227",habit:"#3fa8a0",
          preference:"#c47ba0",flash:"#c0504d"};
var RN = {episode:"事件",fact:"事实",self:"自我",habit:"习惯",
          preference:"偏好",flash:"闪光"};
var POOLN = {short:"短期",medium:"中期",long:"长期",archive:"归档",deep:"深眠"};
/* 分区名（2026-11-06 起短期池下面按区拆开看） */
var REGIONN = {chatter:"闲聊",play:"娱乐",study:"学习",work:"工作",
               episode:"事件",flash:"闪光",fact:"事实",preference:"偏好",
               self:"自我",habit:"习惯"};
var REGIONC = {chatter:"#e8b44a",play:"#b07bff",study:"#57c8f0",work:"#2fd07d",
               episode:"#6f7b8a",flash:"#9d8bff",fact:"#7ad1a0",
               preference:"#d0a35f",self:"#ff8fa3",habit:"#5fb0d0"};

function esc(s){ return String(s==null?"":s).replace(/[&<>"]/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c]; }); }
function fmtDur(sec){
  if(sec==null) return "—";
  sec=Math.max(0,Math.floor(sec));
  var d=Math.floor(sec/86400), h=Math.floor(sec%86400/3600),
      m=Math.floor(sec%3600/60);
  if(d>0) return d+"天"+h+"小时"+m+"分";
  if(h>0) return h+"小时"+m+"分";
  return m+"分"+Math.floor(sec%60)+"秒";
}
function ts2s(t){
  var d=new Date(t*1000);
  var p=function(x){return (x<10?"0":"")+x;};
  return (d.getMonth()+1)+"/"+d.getDate()+" "+p(d.getHours())+":"+p(d.getMinutes());
}

/* ── 画图：全部用 canvas 手画，不引库 ── */
function fitCanvas(cv, h){
  /* 卡片是 flex 纵向布局，canvas 的高度由布局给（CSS height:0 + flex:1 吃掉余量）。
     它的 clientHeight 就是"这张卡还剩多少空间"，这里只按它设绘图缓冲。
     量不到（还没布局 / 不是 dash）才退回传进来的估值，并且必须写死 style.height，
     否则 Canvas 会退成 0 高、什么都看不见。 */
  var flexed=(cv.clientHeight||0)>40;
  var hh=flexed?cv.clientHeight:h;
  var r=window.devicePixelRatio||1, w=cv.clientWidth||cv.parentNode.clientWidth||300;
  cv.width=Math.round(w*r); cv.height=Math.round(hh*r);
  if(!flexed) cv.style.height=hh+"px";
  var g=cv.getContext("2d"); g.setTransform(r,0,0,r,0,0);
  return {g:g,w:w,h:hh};
}
function lineChart(cv, series, opts){
  opts=opts||{};
  var lo=(opts.min!=null)?opts.min:0, hi=(opts.max!=null)?opts.max:1;
  var span=(hi-lo)||1;
  var f=fitCanvas(cv, opts.height||170), g=f.g, W=f.w, H=f.h;
  var padL=30, padR=8, padT=8, padB=18;
  var iw=W-padL-padR, ih=H-padT-padB;
  g.clearRect(0,0,W,H);
  g.strokeStyle="#1d2530"; g.lineWidth=1; g.font="10px Consolas,monospace";
  g.fillStyle="#4d5665";
  for(var k=0;k<=4;k++){
    var y=padT+ih*k/4;
    g.beginPath(); g.moveTo(padL,y); g.lineTo(W-padR,y); g.stroke();
    g.fillText((hi-span*k/4).toFixed(2), 3, y+3);
  }
  /* 跨零的轴（PAD 的 −1..1）：把 0 那条线画实一点，不然看不出正负分界 */
  if(lo<0 && hi>0){
    var y0=padT+ih*(1-(0-lo)/span);
    g.strokeStyle="#2b3a4a"; g.setLineDash([3,3]); g.beginPath();
    g.moveTo(padL,y0); g.lineTo(W-padR,y0); g.stroke(); g.setLineDash([]);
  }
  var n=0;
  series.forEach(function(s){ n=Math.max(n, s.pts.length); });
  if(n<2){
    g.fillStyle="#4d5665"; g.textAlign="center";
    g.fillText("样本还不够画线（每 5 分钟采一个点）", W/2, H/2);
    g.textAlign="left"; return;
  }
  series.forEach(function(s){
    var pts=s.pts;
    if(pts.length<2) return;
    g.beginPath();
    for(var i=0;i<pts.length;i++){
      var x=padL+iw*i/(Math.max(1,pts.length-1));
      var v=(pts[i]-lo)/span;
      var y=padT+ih*(1-Math.max(0,Math.min(1,v)));
      i?g.lineTo(x,y):g.moveTo(x,y);
    }
    g.strokeStyle=s.color; g.lineWidth=1.7; g.lineJoin="round"; g.stroke();
    if(s.fill!==false && !opts.noFill){
      g.lineTo(padL+iw, padT+ih); g.lineTo(padL, padT+ih); g.closePath();
      g.fillStyle=s.color+"22"; g.fill();
    }
  });
}
function barChart(cv, items, color, h){
  var f=fitCanvas(cv,h||170), g=f.g, W=f.w, H=f.h;
  var padL=26, padR=6, padT=10, padB=22;
  var iw=W-padL-padR, ih=H-padT-padB;
  g.clearRect(0,0,W,H);
  g.strokeStyle="#161f29"; g.lineWidth=1;
  g.font="9.5px Consolas,monospace"; g.fillStyle="#4a5a6d";
  var mx=1; items.forEach(function(it){ mx=Math.max(mx,it.n); });
  for(var k=0;k<=3;k++){
    var y=padT+ih*k/3;
    g.beginPath(); g.moveTo(padL,y); g.lineTo(W-padR,y); g.stroke();
    g.fillText(Math.round(mx*(1-k/3)), 2, y+3);
  }
  if(!items.length){ g.fillText("还没有数据", padL+6, padT+ih/2); return; }
  var bw=iw/Math.max(1,items.length)*0.58, step=iw/Math.max(1,items.length);
  var base=color||"#57c8f0";
  items.forEach(function(it,i){
    var hh=ih*(it.n/mx), x=padL+step*i+step*0.21, y=padT+ih-hh;
    var grd=g.createLinearGradient(0,y,0,padT+ih);
    grd.addColorStop(0,base); grd.addColorStop(1,base+"12");
    g.fillStyle=grd;
    g.beginPath();
    if(g.roundRect){ g.roundRect(x,y,bw,Math.max(1.5,hh),2); } else { g.rect(x,y,bw,Math.max(1.5,hh)); }
    g.fill();
    // 柱顶一道亮线，像示波器
    g.fillStyle=base; g.fillRect(x, Math.max(padT,y-1), bw, 1);
    if(items.length<=14 && it.n>0){
      g.fillStyle="#7d8fa4"; g.font="9px Consolas,monospace"; g.textAlign="center";
      g.fillText(it.n, x+bw/2, y-3); g.textAlign="left";
    }
    if(items.length<=14 || i%Math.ceil(items.length/8)===0){
      g.save(); g.translate(x+bw/2, H-7);
      if(items.length>10) g.rotate(-0.55);
      g.fillStyle="#4a5a6d"; g.font="9px Consolas,monospace"; g.textAlign="center";
      g.fillText(String(it.day||"").replace(/^\d{4}-/,""), 0, 0); g.restore();
    }
  });
}
/* 遗忘曲线：横轴天数（对数），四条线分别是短期/中期/长期/底色。
   数据来自后端 forget_curve()，那个是直接调 card2.weight_now 算的。 */
function drawMediaCurve(cv, md){
  /* 两条寿命并排：画面（幂律，掉得快）vs 文字（慢得多）。
     公式都从卡那边来，这里只负责画 —— 不另写一条"差不多"的线。 */
  var days=md.days||[], img=md.image||[], txt=md.text_mid||[];
  var f=fitCanvas(cv,230), g=f.g, W=f.w, H=f.h;
  var padL=44, padR=14, padT=12, padB=26;
  var iw=W-padL-padR, ih=H-padT-padB;
  g.clearRect(0,0,W,H);
  if(!days.length){ g.fillStyle="#4a5a6d"; g.font="10px Consolas,monospace";
    g.fillText("还没有画面", padL+6, padT+ih/2); return; }
  g.strokeStyle="#161f29"; g.lineWidth=1;
  g.font="9.5px Consolas,monospace"; g.fillStyle="#4a5a6d";
  for(var k=0;k<=4;k++){
    var y=padT+ih*k/4;
    g.beginPath(); g.moveTo(padL,y); g.lineTo(W-padR,y); g.stroke();
    g.fillText((1-k/4).toFixed(2), 2, y+3);
  }
  var dmax=Math.log(1+days[days.length-1]);
  var X=function(d){ return padL+iw*Math.log(1+d)/dmax; };
  var Y=function(v){ return padT+ih*(1-Math.max(0,Math.min(1,v))); };
  [0,1,7,30,90,180,371,540].forEach(function(d){
    if(d>days[days.length-1]) return;
    var x=X(d);
    g.strokeStyle="#131a22"; g.beginPath(); g.moveTo(x,padT); g.lineTo(x,padT+ih); g.stroke();
    g.fillStyle="#4a5a6d"; g.textAlign="center";
    g.fillText(d?(d+"d"):"0", x, H-9); g.textAlign="left";
  });
  /* 删除线：画面掉到这条线以下就删文件，此后永远调不出来 */
  var drop=(md.drop_w==null?0.10:md.drop_w);
  var yd=Y(drop);
  g.save(); g.setLineDash([5,4]); g.strokeStyle="#ff5f6d"; g.globalAlpha=.7;
  g.beginPath(); g.moveTo(padL,yd); g.lineTo(W-padR,yd); g.stroke(); g.restore();
  g.fillStyle="#b4565f"; g.font="9px Consolas,monospace";
  g.fillText("删除线 "+drop.toFixed(2), padL+6, yd-4);
  [[img,"#e8b44a",2.0],[txt,"#2fd07d",1.6]].forEach(function(sp){
    var vals=sp[0]||[]; if(!vals.length) return;
    g.beginPath();
    for(var i=0;i<days.length;i++){
      var x=X(days[i]), y=Y(vals[i]);
      if(i===0) g.moveTo(x,y); else g.lineTo(x,y);
    }
    g.strokeStyle=sp[1]; g.lineWidth=sp[2]; g.stroke();
  });
}

function drawForget(cv, fc){
  var days=fc.days||[], f=fitCanvas(cv,240), g=f.g, W=f.w, H=f.h;
  var padL=44, padR=14, padT=12, padB=26;
  var iw=W-padL-padR, ih=H-padT-padB;
  g.clearRect(0,0,W,H);
  g.strokeStyle="#161f29"; g.lineWidth=1;
  g.font="9.5px Consolas,monospace"; g.fillStyle="#4a5a6d";
  for(var k=0;k<=4;k++){
    var y=padT+ih*k/4;
    g.beginPath(); g.moveTo(padL,y); g.lineTo(W-padR,y); g.stroke();
    g.fillText((1-k/4).toFixed(2), 2, y+3);
  }
  if(!days.length){ g.fillText("还没有数据", padL+6, padT+ih/2); return; }
  var dmax=Math.log(1+days[days.length-1]);
  var X=function(d){ return padL+iw*Math.log(1+d)/dmax; };
  var Y=function(v){ return padT+ih*(1-Math.max(0,Math.min(1,v))); };
  /* 刻度：0 1 7 30 90 180 371 540 天 */
  [0,1,7,30,90,180,371,540].forEach(function(d){
    if(d>days[days.length-1]) return;
    var x=X(d);
    g.strokeStyle="#131a22"; g.beginPath(); g.moveTo(x,padT); g.lineTo(x,padT+ih); g.stroke();
    g.fillStyle="#4a5a6d"; g.textAlign="center";
    g.fillText(d?(d+"d"):"0", x, H-9); g.textAlign="left";
  });
  /* 90 天保护期那条竖虚线 */
  if(days[days.length-1]>=90){
    var xp=X(90);
    g.save(); g.setLineDash([4,4]); g.strokeStyle="#2f4a63"; g.beginPath();
    g.moveTo(xp,padT); g.lineTo(xp,padT+ih); g.stroke(); g.restore();
    g.fillStyle="#3f6a8a"; g.font="9px Consolas,monospace";
    g.fillText("90d 保护期", xp+4, padT+10);
  }
  var curves=[["short","#ff5f6d"],["mid","#2fd07d"],["long","#57c8f0"],["core","#e8b44a"]];
  curves.forEach(function(cv2){
    var vals=fc[cv2[0]]||[];
    if(!vals.length) return;
    /* 线下方淡淡的填充，读起来像"记忆还在的部分" */
    g.beginPath();
    for(var i=0;i<days.length;i++){
      var x=X(days[i]), y=Y(vals[i]);
      if(i===0) g.moveTo(x,y); else g.lineTo(x,y);
    }
    g.strokeStyle=cv2[1]; g.lineWidth=1.8; g.stroke();
    if(cv2[0]==="core"){
      g.save(); g.globalAlpha=0.10; g.fillStyle=cv2[1];
      g.lineTo(X(days[days.length-1]), padT+ih); g.lineTo(X(days[0]), padT+ih);
      g.closePath(); g.fill(); g.restore();
    }
    for(var j=0;j<days.length;j++){
      var r=days[j]>=90?2.6:1.7;
      g.fillStyle=cv2[1]; g.beginPath();
      g.arc(X(days[j]), Y(vals[j]), r, 0, Math.PI*2); g.fill();
    }
  });
  g.font="10px Consolas,monospace"; g.fillStyle="#5f7086";
  g.fillText("剩余权重", padL+2, padT-2);
}
function donut(cv, items, h, center){
  var f=fitCanvas(cv,h||170), g=f.g, W=f.w, H=f.h;
  g.clearRect(0,0,W,H);
  var cx=W/2, cy=H/2, R=Math.min(W,H)/2-10, r=R*0.64;
  var tot=0; items.forEach(function(it){ tot+=it.n; });
  if(!tot){ g.fillStyle="#4d5665"; g.font="11px sans-serif";
    g.textAlign="center"; g.fillText("这段时间没有召回记录",cx,cy); g.textAlign="left"; return; }
  var a=-Math.PI/2;
  var PAL=["#57c8f0","#2fd07d","#9d8bff","#e8b44a","#ff5f6d","#5f9ea0","#c78bb8"];
  items.forEach(function(it,i){
    var ang=Math.PI*2*(it.n/tot);
    g.beginPath(); g.moveTo(cx,cy);
    g.arc(cx,cy,R,a,a+ang); g.closePath();
    g.fillStyle=it.color||RC[it.r]||RC[it.k]||PAL[i%PAL.length]; g.fill();
    a+=ang;
  });
  // 留一圈缝隙，看起来像仪表盘刻度盘
  g.beginPath(); g.arc(cx,cy,R,0,Math.PI*2);
  g.strokeStyle="#0b1117"; g.lineWidth=3; g.stroke();
  g.beginPath(); g.arc(cx,cy,r,0,Math.PI*2);
  g.fillStyle="#0d131a"; g.fill();
  g.fillStyle="#d5e6f3"; g.font="600 16px Consolas,monospace";
  g.textAlign="center"; g.fillText(tot, cx, cy+1);
  g.fillStyle="#5b6c80"; g.font="9.5px Consolas,monospace";
  g.fillText(center||"TOTAL", cx, cy+14); g.textAlign="left";
}
function radar(cv, labels, vals, h){
  var f=fitCanvas(cv,h||190), g=f.g, W=f.w, H=f.h;
  g.clearRect(0,0,W,H);
  var cx=W/2, cy=H/2+4, R=Math.min(W,H)/2-32, n=labels.length;
  g.strokeStyle="#1d2530";
  for(var ring=1;ring<=3;ring++){
    g.beginPath();
    for(var i=0;i<n;i++){
      var a=-Math.PI/2+Math.PI*2*i/n, rr=R*ring/3;
      var x=cx+Math.cos(a)*rr, y=cy+Math.sin(a)*rr;
      i?g.lineTo(x,y):g.moveTo(x,y);
    }
    g.closePath(); g.stroke();
  }
  for(var i=0;i<n;i++){
    var a2=-Math.PI/2+Math.PI*2*i/n;
    g.beginPath(); g.moveTo(cx,cy);
    g.lineTo(cx+Math.cos(a2)*R, cy+Math.sin(a2)*R); g.stroke();
  }
  g.beginPath();
  var vmax=0.0001; for(var q=0;q<n;q++){ vmax=Math.max(vmax, vals[q]); }
  for(var j=0;j<n;j++){
    var a3=-Math.PI/2+Math.PI*2*j/n, rv=R*Math.max(0.06,Math.min(1,vals[j]/vmax));
    var x2=cx+Math.cos(a3)*rv, y2=cy+Math.sin(a3)*rv;
    j?g.lineTo(x2,y2):g.moveTo(x2,y2);
  }
  g.closePath();
  g.fillStyle="rgba(95,179,212,.20)"; g.fill();
  g.strokeStyle="#5fb3d4"; g.lineWidth=1.6; g.stroke();
  g.font="10px sans-serif"; g.fillStyle="#7c8697"; g.textAlign="center";
  for(var k2=0;k2<n;k2++){
    var a4=-Math.PI/2+Math.PI*2*k2/n;
    g.fillText(labels[k2], cx+Math.cos(a4)*(R+16), cy+Math.sin(a4)*(R+16)+3);
  }
  g.textAlign="left";
}
/* ── 星云：写实 3D 恒星系（2026-11-01 第二轮） ──
   1) 背景星云离屏画一次（多层径向渐变 + 星尘），不逐帧重算
   2) 节点按螺旋盘撒 3D 点 → 缓慢自转 → 透视投影；恒星/行星两种贴图
   3) 连线 + 间歇性流动脉冲（每条边自己的相位/速度），像神经电信号
   4) 交互：拖动单颗球 / 拖动空白处转系统 / 滚轮缩放 / 双击复位 /
      点击星点 → 高亮邻居 + 右侧显示这条记忆的内容
   全部纯视觉，不写库、不影响权重与检索。 */
var NEB={raf:0, cv:null, sprites:{}};
function nebSprite(col, planet){
  var k=col+(planet?"p":"s");
  if(NEB.sprites[k]) return NEB.sprites[k];
  var CS=72, c=document.createElement("canvas");
  c.width=CS; c.height=CS;
  var g=c.getContext("2d"), C=CS/2;
  if(planet){
    var gr=g.createRadialGradient(C*0.72,C*0.66,C*0.10, C,C,C*0.62);
    gr.addColorStop(0,"#ffffff");
    gr.addColorStop(0.16,col);
    gr.addColorStop(0.62,col);
    gr.addColorStop(1,"rgba(4,8,16,0.92)");
    g.beginPath(); g.arc(C,C,C*0.62,0,Math.PI*2); g.fillStyle=gr; g.fill();
    var g2=g.createRadialGradient(C,C,C*0.55, C,C,C*0.96);
    g2.addColorStop(0,"rgba(120,190,255,0.18)");
    g2.addColorStop(1,"rgba(120,190,255,0)");
    g.beginPath(); g.arc(C,C,C*0.96,0,Math.PI*2); g.fillStyle=g2; g.fill();
  }else{
    var gr2=g.createRadialGradient(C,C,0, C,C,C);
    gr2.addColorStop(0,"rgba(255,255,255,0.98)");
    gr2.addColorStop(0.10,"rgba(255,255,255,0.85)");
    gr2.addColorStop(0.22,col);
    gr2.addColorStop(0.46,"rgba(150,200,255,0.22)");
    gr2.addColorStop(1,"rgba(90,150,230,0)");
    g.fillStyle=gr2; g.fillRect(0,0,CS,CS);
  }
  NEB.sprites[k]=c; return c;
}
function nebBG(W,H,seed){
  var c=document.createElement("canvas"); c.width=W; c.height=H;
  var g=c.getContext("2d");
  var base=g.createRadialGradient(W*0.5,H*0.4,0, W*0.5,H*0.46,Math.max(W,H)*0.72);
  base.addColorStop(0,"#0a1424"); base.addColorStop(0.5,"#060a14");
  base.addColorStop(1,"#02040a");
  g.fillStyle=base; g.fillRect(0,0,W,H);
  var s=seed||7;
  function rnd(){ s=(s*1103515245+12345)&0x7fffffff; return s/0x7fffffff; }
  var cols=["rgba(96,126,255,","rgba(152,86,224,","rgba(46,168,214,","rgba(224,96,168,"];
  g.globalCompositeOperation="lighter";
  for(var i=0;i<10;i++){
    var cx=rnd()*W, cy=rnd()*H, rr=(0.16+rnd()*0.34)*Math.min(W,H)*2.4;
    var col=cols[i%cols.length];
    var rg=g.createRadialGradient(cx,cy,0,cx,cy,rr);
    rg.addColorStop(0,col+"0.085)"); rg.addColorStop(0.42,col+"0.038)");
    rg.addColorStop(1,col+"0)");
    g.fillStyle=rg; g.beginPath(); g.arc(cx,cy,rr,0,Math.PI*2); g.fill();
  }
  for(var d=0;d<1200;d++){
    var x=rnd()*W, y=rnd()*H, a=rnd()*0.45+0.05, sz=rnd()<0.88?1:1.9;
    g.fillStyle="rgba(214,232,255,"+a.toFixed(3)+")";
    g.fillRect(x,y,sz,sz);
  }
  g.globalCompositeOperation="source-over";
  return c;
}
function nebula(cv, data){
  if(data) cv._data=data;
  var f=fitCanvas(cv, 520), g=f.g, W=f.w, H=f.h;
  var nodes=(cv._data&&cv._data.nodes)||[], edges=(cv._data&&cv._data.edges)||[];
  if(!nodes.length){
    g.fillStyle="#4d5665"; g.font="12px sans-serif"; g.fillText("没有记忆",12,20); return;
  }
  var dpr=window.devicePixelRatio||1;
  if(!cv._bg || cv._bgW!==Math.round(W) || cv._bgH!==Math.round(H)){
    cv._bg=nebBG(Math.round(W*dpr),Math.round(H*dpr),(nodes.length*37+Math.round(W))|0);
    cv._bgW=Math.round(W); cv._bgH=Math.round(H);
  }
  /* 螺旋盘撒点：只算一次，之后每帧只是旋转 + 投影 */
  if(!cv._ly || cv._ly.n!==nodes.length){
    var R=Math.min(W*0.34, H*1.7), pos={}, ids=[];
    var s2=97;
    function rnd2(){ s2=(s2*1103515245+12345)&0x7fffffff; return s2/0x7fffffff; }
    for(var i=0;i<nodes.length;i++){
      var nd=nodes[i], t=i/nodes.length;
      var rr=R*(0.12+0.88*Math.sqrt(t));
      var ang=(i%3)*(Math.PI*2/3)+(rr/R)*3.3+(rnd2()-0.5)*0.55;
      pos[nd.id]={x:Math.cos(ang)*rr, y:(rnd2()-0.5)*R*0.16, z:Math.sin(ang)*rr,
                  ox:0, oy:0};
      ids.push(nd.id);
    }
    var adj={};
    edges.forEach(function(e){
      (adj[e.a]=adj[e.a]||{})[e.b]=1;
      (adj[e.b]=adj[e.b]||{})[e.a]=1;
    });
    cv._ly={n:nodes.length, pos:pos, ids:ids, adj:adj};
  }
  if(!cv._st){ cv._st={rot:0, tilt:0.30, zoom:1, sel:null, drag:null,
    nodeDrag:null, spin:0.075, bg:0}; }
  cv._g=g; cv._W=W; cv._H=H;
  if(!cv._db){
    cv._db=1;
    var hitTest=function(ev){
      var r=cv.getBoundingClientRect();
      /* 画布 CSS 尺寸和内部逻辑尺寸不一定一样（画质缩放会改 backing store），先换算 */
      var kx=(cv._W||r.width)/Math.max(1,r.width);
      var ky=(cv._H||r.height)/Math.max(1,r.height);
      var mx=(ev.clientX-r.left)*kx, my=(ev.clientY-r.top)*ky;
      var best=null, bd=1e9, P=cv._proj||{};
      /* 星点小的时候特别难点：命中判定放宽到 24px，并且优先挑更重的星 */
      Object.keys(P).forEach(function(id){
        var p=P[id]; if(!p) return;
        var nd=(cv._map&&cv._map[id])||null;
        var bonus=nd?Math.min(8,(nd.w||0)*10):0;
        var dd=Math.hypot(p.x-mx,p.y-my)-bonus;
        if(dd<bd){ bd=dd; best=id; }
      });
      return (bd<26)?best:null;
    };
    cv.onmousedown=function(ev){
      var id=hitTest(ev);
      var st=cv._st;
      if(id && id!=="__core"){ st.nodeDrag=id; cv.style.cursor="grabbing"; }
      else { st.drag={x:ev.clientX,y:ev.clientY}; cv.style.cursor="grabbing"; }
      ev.preventDefault();
    };
    cv.onmousemove=function(ev){
      var st=cv._st;
      if(st.nodeDrag){
        var P=cv._ly.pos[st.nodeDrag];
        P.ox+=(ev.clientX-(cv._lastX||ev.clientX));
        P.oy+=(ev.clientY-(cv._lastY||ev.clientY));
      }else if(st.drag){
        st.rot += (ev.clientX-st.drag.x)*0.008;
        st.tilt = Math.max(-1.15, Math.min(1.15, st.tilt + (ev.clientY-st.drag.y)*0.004));
        st.drag={x:ev.clientX,y:ev.clientY};
      }else{
        var id=hitTest(ev);
        cv.style.cursor = id ? "pointer" : "grab";
      }
      cv._lastX=ev.clientX; cv._lastY=ev.clientY;
    };
    cv.onmouseup=function(){
      var st=cv._st;
      st.nodeDrag=null; st.drag=null;
      cv.style.cursor="grab";
    };
    cv.onclick=function(ev){
      var id=hitTest(ev);
      var st=cv._st;
      st.sel=(id===st.sel)?null:id;
      nebInfo(cv, st.sel);
    };
    cv.onwheel=function(ev){
      ev.preventDefault();
      var st=cv._st;
      st.zoom=Math.max(0.55,Math.min(3.2,(st.zoom||1)*(ev.deltaY<0?1.08:0.92)));
    };
    cv.ondblclick=function(){
      var st=cv._st;
      st.zoom=1; st.rot=0; st.tilt=0.30; st.sel=null;
      Object.keys(cv._ly.pos).forEach(function(k){
        cv._ly.pos[k].ox=0; cv._ly.pos[k].oy=0; });
      nebInfo(cv,null);
    };
  }
  cv.style.cursor="grab";
  if(NEB.raf){ cancelAnimationFrame(NEB.raf); NEB.raf=0; }
  NEB.cv=cv;
  var last=performance.now(), ema=0.016;
  function step(t){
    if(!document.body.contains(cv) || !cv._st){ NEB.raf=0; return; }
    var dt=Math.min(0.06,(t-last)/1000); last=t;
    var st=cv._st;
    /* 自适应画质：机器慢就自动降内部分辨率、少画光晕和脉冲，别硬撑到卡 */
    ema=ema*0.9+dt*0.1;
    if(ema>0.040 && !st.lowQ) st.lowQ=true;
    else if(ema<0.024 && st.lowQ) st.lowQ=false;
    if(!st.drag && !st.nodeDrag) st.rot += st.spin*dt;
    drawNeb(cv, t/1000);
    NEB.raf=requestAnimationFrame(step);
  }
  NEB.raf=requestAnimationFrame(step);
}
function drawNeb(cv, now){
  var g=cv._g, W=cv._W, H=cv._H, st=cv._st, LY=cv._ly;
  if(!g||!st||!LY) return;
  var dpr=Math.min(1.5, window.devicePixelRatio||1);
  /* 低画质时把内部分辨率压到 0.72，画面靠 CSS 放大（星云本来就是软的，看不出糊） */
  var scale=st.lowQ?0.72:1;
  var bw=Math.round(W*dpr*scale), bh=Math.round(H*dpr*scale);
  if(cv.width!==bw || cv.height!==bh){ cv.width=bw; cv.height=bh; }
  var eff=cv.width/Math.max(1,W);
  g.setTransform(eff,0,0,eff,0,0);
  g.clearRect(0,0,W,H);
  if(cv._bg) g.drawImage(cv._bg,0,0,W,H);
  var R=Math.min(W*0.34,H*1.7);
  var cy=Math.cos(st.rot), sy=Math.sin(st.rot);
  var ct=Math.cos(st.tilt), stn=Math.sin(st.tilt);
  var cx2=W/2, cy2=H/2, zoom=st.zoom||1;
  var proj={};
  var ids=LY.ids;
  for(var i0=0;i0<ids.length;i0++){
    var id=ids[i0], P=LY.pos[id];
    var x1=P.x*cy - P.z*sy;
    var z1=P.x*sy + P.z*cy;
    var y2=P.y*ct - z1*stn;
    var z2=P.y*stn + z1*ct;
    var s=1/(1+z2/(R*2.1));
    proj[id]={x:cx2+(x1+P.ox)*zoom*s, y:cy2+(y2+P.oy)*zoom*s, s:s, z:z2};
  }
  /* 本体：人格核心固定在星系正中心（不参与旋转，它就是中心） */
  var coreP=proj["__core"]={x:cx2, y:cy2, s:1, z:-1e9};
  cv._proj=proj;

  var edges=(cv._data&&cv._data.edges)||[];
  var selId=st.sel;
  /* 曲线：每条边一个稳定的弯度，像神经纤维而不是直尺 */
  var Cur=(cv._cur && cv._cur.n===edges.length) ? cv._cur : (cv._cur={n:edges.length, arr:[]});
  if(!Cur.arr.length){
    for(var ci=0;ci<edges.length;ci++){
      var hsh=((ci*2654435761)>>>0)%1000/1000;
      Cur.arr.push({bend:(hsh-0.5)*0.52, dir:hsh<0.5?1:-1});
    }
  }
  g.lineCap="round";
  /* 三档透明度合批：一次 stroke 画一大片，省掉每帧上千次 beginPath/stroke */
  var buckets=[{a:0.030,c:"#3d7fb0",lw:0.55,pts:[]},
               {a:0.070,c:"#54a8dd",lw:0.85,pts:[]},
               {a:0.150,c:"#7fd0f5",lw:1.25,pts:[]}];
  for(var ei=0;ei<edges.length;ei++){
    var e=edges[ei], A=proj[e.a], B=proj[e.b]; if(!A||!B) continue;
    var dx=B.x-A.x, dy=B.y-A.y, dd=Math.sqrt(dx*dx+dy*dy);
    var dep=(A.s+B.s)/2;
    var lenK=1/(1+dd/(R*0.62));
    var isSel=(selId && (e.a===selId||e.b===selId));
    if(isSel){
      var cxb=(A.x+B.x)/2 - dy/dd*dd*Cur.arr[ei].bend*0.5;
      var cyb=(A.y+B.y)/2 + dx/dd*dd*Cur.arr[ei].bend*0.5;
      g.globalAlpha=0.55; g.strokeStyle="#8fe6ff"; g.lineWidth=1.5;
      g.beginPath(); g.moveTo(A.x,A.y); g.quadraticCurveTo(cxb,cyb,B.x,B.y); g.stroke();
      continue;
    }
    var al=(0.03+(e.w||0)*0.16)*dep*lenK;
    if(al<0.012) continue;
    var bi=al>0.10?2:(al>0.045?1:0);
    var cxb2=(A.x+B.x)/2 - dy/dd*dd*Cur.arr[ei].bend*0.5;
    var cyb2=(A.y+B.y)/2 + dx/dd*dd*Cur.arr[ei].bend*0.5;
    buckets[bi].pts.push(A.x,A.y,cxb2,cyb2,B.x,B.y);
  }
  buckets.forEach(function(bk){
    if(!bk.pts.length) return;
    g.globalAlpha=bk.a; g.strokeStyle=bk.c; g.lineWidth=bk.lw;
    g.beginPath();
    for(var k=0;k<bk.pts.length;k+=6){
      g.moveTo(bk.pts[k],bk.pts[k+1]);
      g.quadraticCurveTo(bk.pts[k+2],bk.pts[k+3],bk.pts[k+4],bk.pts[k+5]);
    }
    g.stroke();
  });

  /* 神经脉冲：沿曲线流动的光点（用预渲染贴图，不再每帧建渐变） */
  g.globalCompositeOperation="lighter";
  var sprGlow=cv._glow || (cv._glow=nebGlow());
  for(var pi=0;pi<edges.length;pi++){
    var e2=edges[pi], A2=proj[e2.a], B2=proj[e2.b];
    if(!A2||!B2) continue;
    var w2=e2.w||0.3;
    if(st.lowQ){ if(w2<0.5 || (pi%2)) continue; }
    else if(w2<0.22 && (pi%3)) continue;
    var per=3.2+((pi*37)%23)*0.35;
    var ph=((now + (pi%17)*0.37)/per)%1;
    if(ph>0.42) continue;
    var u=ph/0.42;
    var cxb3=(A2.x+B2.x)/2 - (B2.y-A2.y)*Cur.arr[pi].bend*0.5;
    var cyb3=(A2.y+B2.y)/2 + (B2.x-A2.x)*Cur.arr[pi].bend*0.5;
    var iu=1-u;
    var gx=iu*iu*A2.x + 2*iu*u*cxb3 + u*u*B2.x;
    var gy=iu*iu*A2.y + 2*iu*u*cyb3 + u*u*B2.y;
    var gs=(2.6+5.2*Math.min(1,w2))*(0.6+0.4*A2.s);
    g.globalAlpha=0.75*(1-Math.abs(u-0.5)*0.6);
    g.drawImage(sprGlow, gx-gs, gy-gs, gs*2, gs*2);
  }
  g.globalCompositeOperation="source-over";

  /* 恒星 / 行星 */
  var nodes=(cv._data&&cv._data.nodes)||[];
  if(!cv._map || cv._mapN!==nodes.length){
    var mp={}; nodes.forEach(function(n){ mp[n.id]=n; });
    cv._map=mp; cv._mapN=nodes.length;
  }
  var mp2=cv._map;
  var adj=LY.adj[selId]||{};
  if(!LY.order || LY.orderN!==ids.length){
    LY.order=ids.slice().sort(function(a,b){ return (mp2[a]?mp2[a].w||0:0)-(mp2[b]?mp2[b].w||0:0); });
    LY.orderN=ids.length;
  }
  var order=LY.order;
  for(var oi=0;oi<order.length;oi++){
    var id2=order[oi], p=proj[id2], nd=mp2[id2];
    if(!nd) continue;
    var w=Math.max(0.02,Math.min(1,nd.w||0.05));
    var isSel=(id2===selId), isNb=!!adj[id2];
    var planet=(nd.w||0)<0.35;
    /* 大小差距更明显：权重用 0.62 次方，重的更重、轻的真的像小行星 */
    var base=(planet?6.5:9)+Math.pow(w,0.62)*22;
    var rad=Math.max(1.8, base*p.s*0.5*(isSel?1.35:(isNb?1.12:1))*Math.min(1.3,zoom*0.9));
    var spr=nebSprite(RC[nd.r]||"#6fa8dc", planet);
    var al=(isSel||isNb)?1:Math.max(0.20,0.42+0.58*p.s);
    var px2=p.x, py2=p.y;
    if(isNb && selId!=null && proj[selId]){
      var S0=proj[selId];
      px2+= (p.x-S0.x)*0.07; py2+= (p.y-S0.y)*0.07;
    }
    if(!st.lowQ && !planet && w>0.62){
      g.globalAlpha=al*0.16;
      g.drawImage(spr, px2-rad*2.2, py2-rad*2.2, rad*4.4, rad*4.4);
    }
    g.globalAlpha=al;
    g.drawImage(spr, px2-rad, py2-rad, rad*2, rad*2);
    if(isSel){
      g.globalAlpha=0.85; g.strokeStyle="#bfefff"; g.lineWidth=1.1;
      g.beginPath(); g.arc(px2,py2,rad*1.5,0,Math.PI*2); g.stroke();
    }
  }
  /* 中心那颗：人格 / 本体 —— 金色，带缓慢呼吸的光晕 */
  var breathe=0.82+0.18*Math.sin(now*1.6);
  var cr=26*Math.min(1.25,zoom*0.95);
  var cspr=cv._coreSpr || (cv._coreSpr=nebCoreSprite());
  g.globalAlpha=0.55*breathe;
  g.drawImage(cspr, coreP.x-cr*1.9, coreP.y-cr*1.9, cr*3.8, cr*3.8);
  g.globalAlpha=1;
  g.drawImage(cspr, coreP.x-cr, coreP.y-cr, cr*2, cr*2);
  if(selId==="__core"){
    g.globalAlpha=0.9; g.strokeStyle="#ffe6a8"; g.lineWidth=1.4;
    g.beginPath(); g.arc(coreP.x,coreP.y,cr*1.35,0,Math.PI*2); g.stroke();
  }
  g.globalAlpha=1;
}
function nebGlow(){
  var S=64, c=document.createElement("canvas"); c.width=S; c.height=S;
  var g=c.getContext("2d"), C=S/2;
  var gr=g.createRadialGradient(C,C,0,C,C,C);
  gr.addColorStop(0,"rgba(235,250,255,0.95)");
  gr.addColorStop(0.22,"rgba(150,225,255,0.55)");
  gr.addColorStop(0.55,"rgba(90,180,240,0.18)");
  gr.addColorStop(1,"rgba(60,140,220,0)");
  g.fillStyle=gr; g.fillRect(0,0,S,S);
  return c;
}
function nebCoreSprite(){
  var S=160, c=document.createElement("canvas"); c.width=S; c.height=S;
  var g=c.getContext("2d"), C=S/2;
  var gr=g.createRadialGradient(C,C,0,C,C,C);
  gr.addColorStop(0,"rgba(255,255,255,1)");
  gr.addColorStop(0.10,"rgba(255,244,214,0.98)");
  gr.addColorStop(0.26,"rgba(255,206,120,0.55)");
  gr.addColorStop(0.55,"rgba(255,170,70,0.16)");
  gr.addColorStop(1,"rgba(255,150,50,0)");
  g.fillStyle=gr; g.fillRect(0,0,S,S);
  return c;
}
function nebInfo(cv, id){
  var box=document.getElementById("nebInfo");
  if(!box) return;
  if(!id){
    box.innerHTML='<div class="empty">点任意一颗星 · 看它连着谁、记着什么。'+
      '拖动星点只是挪位置，不写库。</div>';
    return;
  }
  if(id==="__core"){
    /* 中心那颗星就是"我"：人格核心，不是一条记忆，所以没有 id/权重 */
    var idn=(S.data&&S.data.identity)||{};
    var self=idn.self_name||idn.name||"小鲸鱼";
    var t0=(idn&&idn.self_narrative)||"";
    box.innerHTML =
      '<div style="display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;margin-bottom:8px">'+
        '<span style="color:#ffd98a;font:600 15px/1 Consolas,monospace">CORE</span>'+
        '<span class="tag">人格核心 / 本体</span>'+
        '<span class="sq">第 '+(idn.day||"?")+' 天</span>'+
        '<span class="sq" style="margin-left:auto">永远在中心</span>'+
      '</div>'+
      '<div style="color:#ffe9c2;font-size:14px;line-height:1.7;margin-bottom:8px">'+
        esc(self)+' —— 星系里所有记忆都绕着这一颗转；它不衰减、不遗忘，'+
        '其余星点都是它长出过的枝节。</div>'+
      (t0? '<div class="empty" style="color:#cbbc9a">'+esc(String(t0).slice(0,220))+'</div>'
         : '<div class="empty">（这一版还没有自我叙述文本）</div>');
    return;
  }
  var mp={}; ((cv._data&&cv._data.nodes)||[]).forEach(function(n){ mp[n.id]=n; });
  var nd=mp[id];
  if(!nd){ box.innerHTML='<div class="empty">没找到这条</div>'; return; }
  var adj=cv._ly.adj[id]||{};
  var nb=Object.keys(adj).map(function(k){ return mp[k]; }).filter(Boolean)
    .sort(function(a,b){ return (b.w||0)-(a.w||0); }).slice(0,8);
  var when="";
  if(nd.ts){ var d=new Date(nd.ts*1000);
    when=d.getFullYear()+"-"+String(d.getMonth()+1).padStart(2,"0")+"-"+
      String(d.getDate()).padStart(2,"0")+" "+String(d.getHours()).padStart(2,"0")+
      ":"+String(d.getMinutes()).padStart(2,"0"); }
  box.innerHTML =
    '<div style="display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;margin-bottom:8px">'+
      '<span style="color:#bfefff;font:600 15px/1 Consolas,monospace">#'+nd.id+'</span>'+
      '<span class="tag">'+(RN[nd.r]||nd.r)+'</span>'+
      '<span class="sq">权重 '+(nd.w||0).toFixed(2)+'</span>'+
      '<span class="sq">被想起 '+(nd.a||0)+' 次</span>'+
      '<span class="sq">'+when+'</span>'+
      '<span class="sq" style="margin-left:auto">关联 '+Object.keys(adj).length+' 条</span>'+
    '</div>'+
    '<div style="color:#dbe7f3;font-size:13.5px;line-height:1.65;margin-bottom:10px">'+
      esc(nd.t||"（这条没有主干文本）")+'</div>'+
    (nb.length? '<div class="bars">'+nb.map(function(n){
        return bar2("#"+n.id+" "+(n.t||"").slice(0,14), n.w||0, 1,
          RC[n.r]||"#5b7fa6");
      }).join('')+'</div>' : '<div class="empty">这条还很孤立，没有连出去的突触</div>');
}

/* ── 渲染 ── */
/* 头像：有自定义图就盖住默认那张 SVG。
   ?v= 用 mtime 当戳 —— 换了头像但文件名没变，不加戳浏览器会一直用缓存。 */
function applyAvatar(id){
  var img=$("#avaImg"), def=$("#avaDef");
  if(!img||!def) return;
  if(id && id.avatar){
    var src="/avatar?v="+encodeURIComponent(id.avatar_ver||"0");
    if(img.getAttribute("data-src")!==src){ img.src=src; img.setAttribute("data-src",src); }
    img.style.display="block"; def.style.display="none";
  }else{
    img.style.display="none"; def.style.display="block";
    img.removeAttribute("data-src");
  }
}

function renderSide(){
  var d=S.data||{}, id=d.identity||{};
  $("#nm").textContent = id.name || "小鲸鱼";
  applyAvatar(id);
  $("#up").textContent = fmtDur(S.up);
  startLiveClock();
  drawPulse($("#cvPulse"));
  $("#age").textContent = (id.day!=null? "第 "+id.day+" 天" : "—");
  $("#ins").textContent = id.instance || "—";
  $("#seal").textContent = id.sealed ? "开启" : "未封";
  $("#mem").textContent = (id.mem||0)+" 条";
  $("#mood").textContent = S.mood || "—";
  $("#who").textContent = (id.nick||"") + (id.qq? " · "+id.qq : "");
  var slotTxt = {unloaded:"没插", injected:"插着", broken:"坏了",
                 unknown:"对不上", empty:"没文件"}[S.slot] || "—";
  $("#slot").textContent = slotTxt;
  var dot=$("#stDot"), tx=$("#stTxt");
  if(S.alive){ dot.className="dot"; tx.textContent="已连接"; }
  else { dot.className="dot off"; tx.textContent="连不上"; }
  $("#stamp").textContent = S.stamp || "—";
  renderMini();
}

/* 左栏那四个灯 + 三个数字。
   原则：只读已有数据，不新开接口、不算新东西 —— 免得面板自己成为负担。 */
function setLed(id, state){
  var e = document.getElementById(id);
  if(!e) return;
  e.className = "led" + (state==="ok"?" ok":(state==="bad"?" bad":(state==="warn"?" warn":"")));
}
function renderMini(){
  var h = S.health || {}, tl = h.timeline || {};
  var m = (S.data && S.data.metrics) || {};

  /* 链：好了绿，断了红，没数据灰 */
  setLed("ledChain", tl.chain_ok===true ? "ok" : (tl.chain_ok===false ? "bad" : ""));

  /* 锚：ok 绿；rollback/replaced 红；first/没有 灰 */
  var av = tl.anchor_verdict || "";
  setLed("ledAnchor", av==="ok" ? "ok" : (av==="rollback"||av==="replaced" ? "bad" : ""));

  /* 卡槽：插着绿、没插黄（不算错，只是状态）、坏了红 */
  var sl = S.slot || "";
  setLed("ledSlot", sl==="injected" ? "ok" : (sl==="broken"||sl==="unknown" ? "bad" : "warn"));

  /* 盘：有备份且在线 → 绿；只有主盘 → 黄；一个都没识别 → 灰 */
  var dk = (S.data && S.data.disks) || {};
  var backs = (dk.disks||[]).filter(function(x){ return x.role==="backup"; });
  var anyOnline = (dk.disks||[]).some(function(x){ return x.online; });
  setLed("ledDisk", backs.length && backs.some(function(x){return x.online;}) ? "ok"
                  : (anyOnline ? "warn" : ""));

  tweenNum($("#mnRecall"), m.recall_24h != null ? Number(m.recall_24h) : null);
  tweenNum($("#mnSyn"),    (m.synapse||{}).edges != null ? Number((m.synapse||{}).edges) : null);
  tweenNum($("#mnNew"),    m.created_24h != null ? Number(m.created_24h) : null);
  startLiveClock();
  renderMiniLog();
}

/* 左栏底部"最近几条"：只留 6 行，多了就挤。
   原来它在网格里独占一行 c4、右边一大片空，挪过来正好。 */
/* ===== 让他看起来在活着 =====
   心跳带：CPU 占用（真采样，2 秒一个点，自己往左滚）
   补间：数字从旧值滚到新值，滚完停真值 —— 绝不显示假小数
   秒表：运行时间每秒 +1 */
var PULSE = [], MEMP = [], PULSE_N = 72, _lastNums = {}, _tickT = null;
function _line(g, arr, x0, step, padT, plotH, vmax, color, fill){
  if(arr.length < 2) return;
  var y = function(v){ return padT + plotH * (1 - Math.max(0, Math.min(vmax, v))/vmax); };
  if(fill){
    g.beginPath();
    arr.forEach(function(v,i){ var x=x0+i*step; if(i===0) g.moveTo(x,y(v)); else g.lineTo(x,y(v)); });
    g.lineTo(x0+(arr.length-1)*step, padT+plotH); g.lineTo(x0, padT+plotH); g.closePath();
    var grd = g.createLinearGradient(0,padT,0,padT+plotH);
    grd.addColorStop(0, color==="#57c8f0" ? "rgba(87,200,240,.30)" : "rgba(157,139,255,.18)");
    grd.addColorStop(1, "rgba(0,0,0,0)");
    g.fillStyle = grd; g.fill();
  }
  g.beginPath();
  arr.forEach(function(v,i){ var x=x0+i*step; if(i===0) g.moveTo(x,y(v)); else g.lineTo(x,y(v)); });
  g.strokeStyle = color; g.lineWidth = 1.3; g.lineJoin="round"; g.stroke();
  var lx = x0+(arr.length-1)*step, ly = y(arr[arr.length-1]);
  g.beginPath(); g.arc(lx, ly, 2, 0, Math.PI*2); g.fillStyle = color; g.fill();
}
function drawPulse(cv){
  if(!cv) return;
  var dpr = Math.min(2, window.devicePixelRatio || 1);
  var w = cv.clientWidth, h = cv.clientHeight;
  if(!w || !h) return;
  if(cv.width !== Math.round(w*dpr) || cv.height !== Math.round(h*dpr)){
    cv.width = Math.round(w*dpr); cv.height = Math.round(h*dpr);
  }
  var g = cv.getContext("2d");
  g.setTransform(dpr,0,0,dpr,0,0);
  g.clearRect(0,0,w,h);
  if(PULSE.length < 2) return;
  var padT = 5, padB = 5, plotH = h - padT - padB;
  var vmax = 20;
  PULSE.forEach(function(v){ if(v > vmax) vmax = v; });
  MEMP.forEach(function(v){ if(v > vmax) vmax = v; });
  vmax = Math.min(100, Math.ceil(vmax * 1.25));
  var step = w / (PULSE_N - 1);
  var xc = w - (PULSE.length-1) * step;   /* CPU 从右往左滚 */
  var xm = w - (MEMP.length-1) * step;
  _line(g, MEMP, xm, step, padT, plotH, vmax, "#9d8bff", false);
  _line(g, PULSE, xc, step, padT, plotH, vmax, "#57c8f0", true);
  /* 左上角读数：数字本身每秒在动，看着就知道是活的 */
  var cv2 = PULSE.length ? PULSE[PULSE.length-1] : 0;
  var mv2 = MEMP.length ? MEMP[MEMP.length-1] : 0;
  g.font = "9px Consolas,monospace";
  g.textBaseline = "top";
  g.fillStyle = "rgba(87,200,240,.95)";
  g.fillText("CPU " + cv2.toFixed(1), 5, 4);
  g.fillStyle = "rgba(157,139,255,.9)";
  g.fillText("MEM " + mv2.toFixed(1), 62, 4);
}
function pollPulse(){
  API("/syslive").then(function(j){
    if(!j || !j.now) return;
    PULSE.push(j.now.cpu || 0);
    while(PULSE.length > PULSE_N) PULSE.shift();
    MEMP.push(j.now.mem || 0);
    while(MEMP.length > PULSE_N) MEMP.shift();
    drawPulse($("#cvPulse"));
  }).catch(function(){});
}
/* 补间：把 el 里的数字从旧值滚到新值。非数字（"—"）直接写。 */
function tweenNum(el, target){
  if(!el) return;
  if(target == null || isNaN(target)){ el.textContent = "—"; return; }
  var key = el.id, from = _lastNums[key];
  _lastNums[key] = target;
  if(from == null || from === target || Math.abs(target-from) < 1){
    el.textContent = target; return;
  }
  var t0 = performance.now(), dur = 520, d = target - from;
  el.classList.add("hit");
  setTimeout(function(){ el.classList.remove("hit"); }, 560);
  (function step(now){
    var k = Math.min(1, (now - t0) / dur);
    var e = 1 - Math.pow(1-k, 3);            /* easeOutCubic */
    el.textContent = Math.round(from + d*e);
    if(k < 1) requestAnimationFrame(step);
    else el.textContent = target;
  })(t0);
}
function tickUptime(){
  if(S.up == null) return;
  S.up += 1;
  var el = $("#up");
  if(el) el.textContent = fmtDur(S.up);
}
function startLiveClock(){
  if(_tickT) return;
  _tickT = setInterval(tickUptime, 1000);
  setInterval(pollPulse, 2000);
  pollPulse();
}
function renderMiniLog(){
  var box = document.getElementById("mlog");
  if(!box) return;
  var all = ((S.data && S.data.timeline_tail) || []).slice().reverse();
  /* 相邻同 kind 合并：连着三条 boot 没必要占三行，
     只留最新那条 + "×3"，省下来的行给真正有内容的 mem */
  var rows = [];
  all.forEach(function(x){
    var k = String(x.kind || "");
    var last = rows[rows.length-1];
    if(last && last.kind === k && (k==="boot" || k==="alive")){ last.n++; return; }
    rows.push({kind:k, seq:x.seq, note:x.note, n:1});
  });
  rows = rows.slice(0, 24);
  if(!rows.length){ box.innerHTML = '<div class="r"><span>还没有记录</span></div>'; return; }
  var KN = {boot:"启动", alive:"心跳", mem:"记", decay:"忘", recall:"想起",
            skill:"本事", incident:"出事", untrust:"解封", sleep_start:"睡",
            sleep_end:"醒", dream:"做梦"};
  box.innerHTML = rows.map(function(x){
    var k = x.kind;
    var cls = (k==="incident") ? " b" : (k.indexOf("sleep")===0 ? " w" : "");
    /* note 里常带"他#951 使用者☭：整段原文"，一行放不下 —— 冒号前的人名/序号去掉，
       正文砍到 26 字，左栏只留"发生了什么" */
    var raw = String(x.note || "")
      .replace(/\[[^\]]{1,14}\]\s*/g, "")
      .replace(/^[^：:]{1,24}[：:]\s*/, "");
    if(raw.length > 26) raw = raw.slice(0, 26) + "…";
    var tail = (x.n > 1 ? " ×"+x.n : "") + (raw ? " · " + raw : "");
    return '<div class="r'+cls+'"><i></i><b>'+esc(String(x.seq||""))+'</b>'+
      '<span title="'+esc(k+' '+String(x.note||""))+'">'+
      esc((KN[k]||k)+tail)+'</span></div>';
  }).join("");
}

function cardHTML(cls, title, body, tag){
  return '<div class="card '+cls+'"><h2>'+esc(title)+
    (tag? '<span class="tag">'+esc(tag)+'</span>':'')+'</h2>'+body+'</div>';
}

/* ── 仪表化的几个小零件（2026-11-01 加） ── */
/* 图表高度跟着窗口走：1080p 不至于缩在角上，笔记本也不至于把卡片撑爆。
   CHSCALE 由 autoFill() 兜底微调 —— 屏幕特别高的时候靠它把余量吃掉，
   不然底下会留一大块空。 */
var CHSCALE = 1;
function appZoom(){
  var a=document.getElementById('app');
  var z=a?parseFloat(getComputedStyle(a).zoom):1;
  return (z&&isFinite(z)&&z>0)?z:1;
}
/* 画布高度优先用布局给它的实际高度（卡片是 flex，canvas 会自己吃掉余量），
   量不到（还没布局）才退回按视口算的估值。 */
function cvh(cv, fb){
  var h=cv?cv.clientHeight:0;
  return (h&&h>40)?h:fb;
}
function CH(frac, lo, hi){
  var z=appZoom();
  var avail=window.innerHeight/z;            /* 折算成 #app 内部的布局像素 */
  var v=(avail-140)*(frac||0.20);
  v=Math.max(lo||120, Math.min(hi||250, v))*CHSCALE;
  return Math.round(Math.min(avail*0.62, v));
}
/* 数字方块：EN 标签 + 大数字 + 中文注 */
function tileN(en, cn, val, cls, sub){
  return '<div class="tile'+(cls?" "+cls:"")+'">'+
    '<div class="tk">'+esc(en)+'</div>'+
    '<div class="tv'+(String(val).length>6?" s":"")+'">'+esc(val)+'</div>'+
    '<div class="tn">'+esc(cn)+(sub?' · '+esc(sub):'')+'</div></div>';
}
/* 横向条：给池子 / 存储 / 高频记忆用 */
function hbars(items, opt){
  opt=opt||{};
  var mx=0; items.forEach(function(it){ mx=Math.max(mx, it.n); });
  if(!items.length) return '<div class="empty">没有数据</div>';
  return '<div class="hbar-wrap">'+items.map(function(it){
    var pct = mx>0 ? Math.max(2, it.n/mx*100) : 0;
    var right = opt.fmt ? opt.fmt(it) : (it.n+"<em>"+(opt.unit||"")+"</em>");
    return '<div class="hb"><span class="nm" title="'+esc(it.k)+'">'+
      esc(it.label||it.k)+'</span>'+
      '<span class="tr"><i style="width:'+pct+'%;'+
        (it.color?'background:linear-gradient(90deg,'+it.color+'55,'+it.color+');box-shadow:0 0 8px '+it.color+'66':'')+
      '"></i></span><span class="nu">'+right+'</span></div>';
  }).join('')+'</div>';
}
/* 账本格子：一堆纯数字 */
function ledger(items){
  return '<div class="ledger">'+items.map(function(it){
    return '<div class="li"><div class="lk">'+esc(it.k)+'</div>'+
      '<div class="lv">'+esc(it.v)+(it.u?'<small>'+esc(it.u)+'</small>':'')+'</div></div>';
  }).join('')+'</div>';
}
/* 激素读数条：值 + 与窗口起点的差 */
function gaugeRow(en, cn, v, prev, color){
  v = Number(v||0); prev = Number(prev||v);
  var pct = Math.max(0, Math.min(100, v*100));
  var dv = v-prev;
  var flat = Math.abs(dv)<0.005;
  var arrow = flat ? "±" : (dv>0?"+":"−");
  var dcls = flat ? "" : (dv>0?"up":"dn");
  var lbl = cn ? (cn+" "+en) : en;   /* 中英混排：血清素 SERO */
  return '<div class="hb"><span class="nm" title="'+esc(lbl)+'">'+esc(lbl)+'</span>'+
    '<span class="tr"><i style="width:'+pct+'%;background:linear-gradient(90deg,'+
      color+'55,'+color+');box-shadow:0 0 8px '+color+'66"></i></span>'+
    '<span class="nu">'+v.toFixed(2)+
      '<em class="'+dcls+'">'+arrow+(flat?"0.00":Math.abs(dv).toFixed(2))+'</em></span></div>';
}

function viewDash(){
  var d=S.data||{}, h=d.health||{}, st=d.storage||{}, t=d.timeline||{};
  var m=d.metrics||{};
  var hlog=d.hlog||[];
  var last=hlog.length?hlog[hlog.length-1]:{}, first=hlog.length?hlog[0]:{};
  var anch=t.anchor||{};
  var chainOK=!!t.chain_ok, birthOK=!!t.birth_ok, sealOK=!!(d.identity&&d.identity.sealed);
  var memN=m.mem||0, birthN=m.birth||memN;
  var anchN=(anch.state&&anch.state.count)||m.ticks||0;
  var out="";

  /* ── ① 状态矩阵 ── */
  out += cardHTML("c12","SYSTEM MATRIX / 状态矩阵",
    '<div class="tiles">'+
    tileN("CHAIN","时间线链", String(m.ticks||t.ticks||"—"), chainOK?"ok":"bad",
          chainOK?"INTACT":"BROKEN")+
    tileN("BIRTH","出生证", birthN+"/"+memN, birthOK?"ok":"bad",
          birthOK?"MATCHED":"MISMATCH")+
    tileN("ANCHOR","卡外锚", anchN+"/"+(m.ticks||anchN), (anch.ok!==false)?"ok":"bad",
          anch.ok===false?"DRIFT":"SYNCED")+
    tileN("SEAL","人格封印", sealOK?"1":"0", sealOK?"ok":"bad",
          sealOK?"SEALED":"OPEN")+
    tileN("DAY","相识天数", "D"+(t.day||"?")+"", "", "SINCE 10-16")+
    tileN("SNAP","快照", String(h.snap_n||0), "",
          (h.snap_age_h!=null? h.snap_age_h+"h":"—")+" AGO")+
    tileN("DB","记忆库", String(h.db_mb||0), "", "MB · WAL "+(h.wal_mb||0))+
    tileN("DISK","磁盘剩余", String(st.disk_free_gb||0), (st.disk_free_gb<5)?"bad":"",
          "GB FREE")+
    tileN("RECALL",(m.recall_window||"24H")+" 召回", String(m.recall_24h||0), "", "EVENTS")+
    tileN("EMBED","向量维度", String((m.emb_dims&&m.emb_dims[0]&&m.emb_dims[0].dim)||"—"),
          "", "DIM · "+(m.usable_emb||0)+" COVER")+
    '</div>');

  /* ── ② 激素场 ── */
  /* 采样太少时后端会自动换更长窗口 —— 标题和副标题都按真实跨度写，
     不能把 3 天的线挂在"(24H)"下面。 */
  var hspan=(hlog.length>1)?Math.round((hlog[hlog.length-1].ts-hlog[0].ts)/3600):0;
  var hwin=(hspan>=168)?"近 7 天":(hspan>=48)?"近 3 天":(hspan>=20)?"近 24H":"近 12H";
  out += cardHTML("c12","HORMONE FIELD / 激素场 ("+hwin+")",
    '<canvas id="cvH"></canvas>'+
    '<div class="legend">'+
    '<span><i style="background:#57c8f0"></i>SERO 血清素</span>'+
    '<span><i style="background:#2fd07d"></i>DOPA 多巴胺</span>'+
    '<span><i style="background:#ff5f6d"></i>CORT 皮质醇</span>'+
    '<span><i style="background:#9d8bff"></i>OXY 催产素</span>'+
    '</div>', hlog.length+" 采样点 · 跨 "+hspan+" 小时");
  /* ── ②b PAD 三维（由 h_log 现算；公式只在 padlib.py 一处） ── */
  var padNow=(d.status&&d.status.pad)||{};
  out += cardHTML("c12","PAD FIELD / 情绪三维 ("+hwin+")",
    '<canvas id="cvPad"></canvas>'+
    '<div class="legend">'+
    '<span><i style="background:#e8b44a"></i>P 愉悦</span>'+
    '<span><i style="background:#ff5f6d"></i>A 唤醒</span>'+
    '<span><i style="background:#57c8f0"></i>D 掌控</span>'+
    '<span style="color:#3f5164">轴随波动自动缩放 · 虚线 0 = 平静</span>'+
    '</div>',
    (padNow.label?("现在 "+padNow.label):"—")+" · "+hlog.length+" 采样点");
  /* ── ③ 当前激素读数 ── */
  var hstat=function(k){
    var vs=hlog.map(function(p){ return Number(p[k]||0); }).filter(function(v){ return isFinite(v); });
    if(!vs.length) return {avg:0,max:0,min:0};
    var sum=0; vs.forEach(function(v){ sum+=v; });
    return {avg:sum/vs.length, max:Math.max.apply(null,vs), min:Math.min.apply(null,vs)};
  };
  var hk=[["sero","SERO 血清素"],["dopa","DOPA 多巴胺"],
          ["cort","CORT 皮质醇"],["oxy","OXY 催产素"]];
  /* 窗口统计只放 AVG 一格一张（PEAK/LOW 写在下面的小注里）——
     12 格会把卡片顶爆，底下的数字被 overflow 切掉（2026-11-01 试过）。 */
  var hled=[], peakTxt=[];
  hk.forEach(function(kk){
    var s=hstat(kk[0]);
    hled.push({k:kk[1].split(" ")[0]+"·AVG", v:s.avg.toFixed(2)});
    peakTxt.push(kk[1].split(" ")[0]+" "+s.max.toFixed(2)+"/"+s.min.toFixed(2));
  });
  /* 实时值：meta 里 tick 写下的当前激素（每 15 秒刷一次页面就能看到它漂），
     没有就用 h_log 最后一个采样点兜底。 */
  var live=d.hormones||{};
  var cur=function(k, fallback){
    var v=live[k];
    return (v==null||!isFinite(v))?fallback:v;
  };
  out += cardHTML("c4","HORMONE NOW / 当前读数",
    '<div class="hbar-wrap" style="padding-top:2px">'+
    gaugeRow("SERO","血清素", cur("sero",last.sero), first.sero, "#57c8f0")+
    gaugeRow("DOPA","多巴胺", cur("dopa",last.dopa), first.dopa, "#2fd07d")+
    gaugeRow("CORT","皮质醇", cur("cort",last.cort), first.cort, "#ff5f6d")+
    gaugeRow("OXY","催产素", cur("oxy",last.oxy), first.oxy, "#9d8bff")+
    '</div>'+
    '<div class="legend" style="margin-top:7px">MOOD 心境 '+
      (last.mood!=null?last.mood.toFixed(2):"—")+
      ' · LIVE 每 tick 更新 · Δ 对比 24H 起点</div>'+
    /* PAD 三维：读服务端的 /status.pad（公式只有 card2.mood_pad 一处） */
    (function(){
      var pad=(d.status&&d.status.pad)||{};
      if(pad.p==null) return "";
      var sgn=function(v){ return (v>0?"+":"")+v.toFixed(2); };
      return '<div class="legend" style="margin-top:5px">PAD '+
        esc(pad.label||"")+' ｜ 愉悦 '+sgn(pad.p)+
        ' · 唤醒 '+sgn(pad.a)+' · 掌控 '+sgn(pad.d)+
        ' <span style="color:#3f5164">−1..1 · 0=平静</span></div>';
    })()+
    '<div style="margin-top:8px">'+ledger(hled)+'</div>'+
    '<div class="legend" style="margin-top:6px">PEAK/LOW '+esc(peakTxt.join(" · "))+'</div>');

  /* ── ④ 召回活动 ── */
  var rs=(m.recall_series||[]).map(function(v,i){ return {day:String(i).padStart(2,"0"),n:v}; });
  out += cardHTML("c4","RECALL RATE / 召回频率",
    '<canvas id="cvRec"></canvas>',
    (m.recall_window||"24H")+" · 每格"+
      (m.recall_window==="7D"?"（3.5 小时）":"（1 小时）"));
  /* ── ⑤ 召回来源 ── */
  out += cardHTML("c4","RECALL SOURCE / 召回来源",
    '<canvas id="cvSrc"></canvas>'+
    '<div class="legend">'+(m.recall_sources||[]).map(function(s){
      return '<span><i style="background:'+(RC[s.k]||"#57c8f0")+'"></i>'+
        esc(s.k)+' '+s.n+'</span>'; }).join('')+'</div>');
  /* ── ⑥ 高频记忆 ── */
  out += cardHTML("c4","TOP RECALLED / 高频记忆",
    hbars((m.recall_top||[]).map(function(x){
      return {k:"#"+x.id, label:"#"+x.id, n:x.n, color:"#9d8bff"}; }),
      {fmt:function(it){ return it.n+"<em>次</em>"; }}));

  /* ── ⑦ 五池水位 ── */
  var pools=(m.pools||[]).map(function(p){
    return {k:p.k, label:POOLN[p.k]||p.k, n:p.n,
            color:(p.k==="short"?"#e8b44a":p.k==="long"?"#57c8f0":"#2fd07d")};
  });
  var poolTot=0; pools.forEach(function(p){ poolTot+=p.n; });
  /* 短期池下面按区拆开：这 4 条是闲聊还是学习，寿命差 30 倍，得分开看 */
  var sregs=(m.short_regions||[]);
  var rload=(m.region_load||{on:false,regions:[]});
  var poolSub="";
  if(sregs.length){
    poolSub += '<div class="legend" style="margin-top:10px">SHORT / 短期池按区</div>'+
      hbars(sregs.map(function(x){
        return {k:x.k, label:REGIONN[x.k]||x.k, n:x.n,
                color:(REGIONC[x.k]||"#6f7b8a")}; }),
        {fmt:function(it){ return it.n+"<em>条</em>"; }});
  }
  if(rload.regions && rload.regions.length){
    poolSub += '<div class="legend" style="margin-top:8px">REGION LAMBDA / 分区曲线 '+
      (rload.on?'<b style="color:#2fd07d">ON</b>':'<b style="color:#6f7b8a">OFF</b>')+
      '</div><div class="legend" style="margin-top:2px">'+
      rload.regions.map(function(r){
        return '<span><i style="background:'+(REGIONC[r.region]||"#6f7b8a")+'"></i>'+
          esc(REGIONN[r.region]||r.region)+' '+r.half_life_h.toFixed(0)+'h'+
          ' · 负载 '+r.load.toFixed(1)+'/'+r.budget+
          ' · f '+r.factor.toFixed(2)+'</span>';
      }).join('')+'</div>';
  }
  out += cardHTML("c4","MEMORY POOLS / 五池",
    hbars(pools, {fmt:function(it){
      return it.n+"<em>"+(poolTot?Math.round(it.n/poolTot*100):0)+"%</em>"; }})+
    poolSub+
    '<div class="legend" style="margin-top:6px">AVG_W '+
      esc((m.pools||[]).map(function(p){
        return (POOLN[p.k]||p.k)+" "+(p.aw||0).toFixed(2); }).join(" · ")||"—")+
    '</div>',
    poolTot+" TOTAL");
  /* ── ⑧ 权重分布 ── */
  out += cardHTML("c4","WEIGHT DIST / 权重分布",
    '<canvas id="cvWH"></canvas>',"0 → 1 · 十二格");
  /* ── ⑨ 突触网络 ── */
  var syn=m.synapse||{};
  out += cardHTML("c4","SYNAPSE / 突触网络",
    '<canvas id="cvSyn"></canvas>'+
    '<div class="legend">'+(m.syn_kinds||[]).map(function(k){
      return '<span><i style="background:'+(RC[k.k]||"#2fd07d")+'"></i>'+
        esc(k.k)+' '+k.n+'</span>'; }).join('')+'</div>'+
    '<div class="legend" style="margin-top:2px">EDGES '+syn.edges+' · NODES '+
      syn.nodes+' · DEG '+(syn.degree||0)+' · AVG_W '+(syn.avg_w||0)+'</div>');

  /* ── ⑩ 写入 ── */
  out += cardHTML("c4","WRITE FLOW / 写入 (14D)",
    '<canvas id="cvW"></canvas>',"按天");
  /* ── ⑪ 分区 ── */
  out += cardHTML("c4","REGION MIX / 分区",
    '<canvas id="cvR"></canvas>'+
    '<div class="legend">'+(d.regions||[]).map(function(r){
      return '<span><i style="background:'+(RC[r.r]||"#5b7fa6")+'"></i>'+
        (RN[r.r]||r.r)+" "+r.n+'</span>'; }).join('')+'</div>');
  /* ── ⑫ 六维权重 ── */
  out += cardHTML("c4","CONTEXT WEIGHTS / 六维",
    '<canvas id="cvRadar"></canvas>',
    (S.wsum? (S.wsum*100).toFixed(0)+"% / 100%":"—"));

  /* ── ⑬ 存储 ── */
  var totMB=(h.db_mb||0)+(h.wal_mb||0)+(st.snap_mb||0)+(st.models_mb||0);
  out += cardHTML("c4","STORAGE / 存储",
    '<div class="hbar-wrap">'+
    hbars([
      {k:"DB",label:"记忆库",n:h.db_mb||0,color:"#57c8f0"},
      {k:"WAL",label:"WAL",n:h.wal_mb||0,color:"#9d8bff"},
      {k:"SNAP",label:"快照",n:st.snap_mb||0,color:"#2fd07d"},
      {k:"MODEL",label:"模型",n:st.models_mb||0,color:"#e8b44a"}
    ],{fmt:function(it){ return it.n.toFixed(1)+"<em>MB</em>"; }})+
    '</div><div class="legend" style="margin-top:6px">TOTAL '+
      (totMB/1024).toFixed(2)+' GB</div>');
  /* ── ⑭ 时间线吞吐 ── */
  var ts=(m.timeline_series||[]).map(function(v,i){ return {day:String(i).padStart(2,"0"),n:v}; });
  out += cardHTML("c4","TIMELINE / 吞吐 (24H)",
    '<canvas id="cvTick"></canvas>','每小时 tick 数');
  /* ── ⑮ 时间线日志：挪到左栏（见 aside 的 .mlog），不再占网格一行 ── */

  /* ── ⑯ 账本 ── */
  out += cardHTML("c12","LEDGER / 账本",
    ledger([
      {k:"MEM",v:memN,u:"条"},
      {k:"DETAIL",v:m.detail||0,u:"条"},
      {k:"SYNAPSE",v:syn.edges||0,u:"边"},
      {k:"RECALL 24H",v:m.recall_24h||0,u:"次"},
      {k:"SKILL",v:m.skill||0,u:"条"},
      {k:"DIALOGUE",v:m.dialogue||0,u:"轮"},
      {k:"TOMB",v:m.tomb||0,u:"删除"},
      {k:"NEW 24H",v:m.created_24h||0,u:"条"},
      {k:"NEW 7D",v:m.created_7d||0,u:"条"},
      {k:"TICKS",v:m.ticks||0,u:"条"},
      {k:"UPTIME",v:fmtDur(S.up||0),u:""},
      {k:"GEN",v:(d.generated?new Date(d.generated*1000).toLocaleTimeString("zh-CN"):"—"),u:""}
    ]));
  /* ── ⑰ 实时负载（2 秒一刷，自己动） ── */
  var sy=(d.sys&&d.sys.now)||{};
  out += cardHTML("c8","SYSTEM LIVE / 实时负载 · LIVE 2s",
    '<canvas id="cvSys"></canvas>',
    "CPU "+((sy.cpu||0)+"%")+" · MEM "+((sy.mem||0)+"%")+" · 磁盘读 "+
      fmtBps(sy.dr)+" · 写 "+fmtBps(sy.dw)+
      " ｜ 最近 5 分钟，右端是现在");
  out += cardHTML("c4","IO / 读写与网络",
    '<canvas id="cvIO"></canvas>',
    "进程 "+(sy.procs||0)+" 个 · 内存 "+(sy.mem_used||0)+" / "+(sy.mem_total||0)+" MB · SWAP "+(sy.swap||0)+"%");
  /* ── ⑱ 搬家与备份（锚 + 可移植包） ── */
  var ig = d.integrity || {};
  var iok = !!ig.ok;
  var pk = ig.pack || null, wh = ig.where || null;
  out += cardHTML("c12","PORTABLE / 搬家与备份 · 锚、包、落地",
    '<div class="tiles">'+
    tileN("CHAIN SEQ","链头序号", String(ig.card_seq||0), iok?"ok":"bad",
          "时间线 "+((ig.card_count||0))+" 条事件")+
    tileN("ANCHOR VERDICT","锚的判定", (ig.verdict||"—").toUpperCase(),
          iok?"ok":"bad", ig.reason||"")+
    tileN("LOCAL ANCHOR","卡内锚", String(ig.local_seq||0),
          ig.local_seq===ig.card_seq?"ok":"warn", (ig.local_head||"—")+" · 卡目录 anchor.json")+
    tileN("EXT ANCHOR","卡外锚", String(ig.ext_seq||0),
          ig.ext_seq===ig.card_seq?"ok":"warn", (ig.ext_head||"—")+" · ~/.dsh/anchors")+
    '</div>'+
    ledger([
      {k:"PACK",v:pk?(pk.n+" 文件"):"未打包",u:pk?(" / "+pk.mb+" MB"):""},
      {k:"BUILT",v:pk&&pk.built_at?(new Date(pk.built_at*1000).toLocaleString("zh-CN")):"—",u:""},
      {k:"WAL(BUILD)",v:pk&&pk.wal_before!=null?(pk.wal_before+" MB"):"—",u:""},
      {k:"LANDED",v:wh&&wh.landed_at?(new Date(wh.landed_at*1000).toLocaleDateString("zh-CN")):"这台机器",u:""},
      {k:"SLOT",v:String((d.identity&&d.identity.slot)||"—"),u:""},
      {k:"NOTE",v:pk?"包已就绪，可插盘校验":"插上盘 → 控制台跑 build",u:""}
    ]),
    "锚只在动之前比对；两台机器同时写会分叉 —— 这一点还没上写入闸");

  /* ── ⑲ 盘子：一排灯 + 备份/同步/换主备 ── */
  var dkd = (S.data && S.data.disks) || {};
  var dk = dkd.disks || [];
  var lamps = dk.map(function(x){
    var on = !!x.online, isMain = x.role === "main";
    var txt = isMain ? "主盘" : (x.number ? ("备份 #"+x.number) : "未登记");
    var sub = on ? ((x.head_seq!=null?("seq "+x.head_seq):"—") +
                    (x.behind>0 ? (" · 落后 "+x.behind+" 节") : " · 一致"))
                 : "离线";
    return '<div class="lamp '+(on?(isMain?"lm":"lb"):"loff")+'">'+
             '<i></i><b>'+txt+'</b><span>'+esc(sub)+'</span></div>';
  }).join("");
  var retired = !!dkd.retired;
  var retBanner = retired ? ('<div class="diskrow" style="border:1px solid #e8b44a;'+
      'border-radius:6px;padding:6px 10px;margin:6px 0;color:#e8b44a">'+
      '● 这张卡已被「带卡出门」——本机只读。要把它接回来，跑一次「一键落地」。</div>') : '';
  var sel = '<select id="diskSel">'+
    (dkd.volumes || []).map(function(v){
       var p = v.card_dir || v.root;
       var txt = (v.label||"?") + " · " + v.root + " · " +
                 (v.free_gb!=null ? (v.free_gb+" GB 空") : "空间未知");
       return '<option value="'+esc(p)+'">'+esc(txt)+'</option>';
     }).join("")+'</select>';
  out += cardHTML("c12","DISKS / 盘子与备份 · 灯、备份、同步、带卡出门",
    retBanner +
    '<div class="lamps">'+(lamps||'<span class="muted">一块都没认出来</span>')+'</div>'+
    '<div class="diskrow">'+sel+
      '<button id="btnBk">备份到这块盘</button>'+
      '<button id="btnSy">一键同步</button>'+
      '<button id="btnSw">主备互换</button>'+
      '<button id="btnRetire" style="border-color:#e8b44a;color:#e8b44a">带卡出门</button>'+
      '<button id="btnUnretire">解除退役</button>'+
      '<span id="diskMsg" class="muted"></span></div>',
    "备份盘不设上限；同步只补落后、分叉一律拒绝；换主备要求条数·链头序号·链头哈希三条全中");
  return out;
}

/* 盘子卡上的三个按钮：都要写盘，所以都带 confirm */
function diskPost(act, path){
  var msg = $("#diskMsg");
  if(!path){ if(msg) msg.textContent = "先选一块盘"; return; }
  msg.textContent = "正在做…";
  fetch("/disks/"+act, {method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({path:path, confirm:true})})
    .then(function(r){ return r.json().then(function(j){ return {code:r.status, j:j}; }); })
    .then(function(o){
      var j = o.j || {};
      if(j.need_reseed){
        /* 分叉：不是"坏了"，是"两块盘各自写过东西"。把丢什么说清楚，让人拍板。 */
        var killed = (j.orphans||[]).map(function(x){
          return "#" + x.seq + " " + (x.kind||"") + (x.note ? "「" + x.note + "」" : "");
        }).join("、");
        msg.textContent = "两边在 seq=" + j.fork_at + " 之后分叉了：备份盘上有 " +
          (j.orphans||[]).length + " 条主盘没有的记录（" + killed + "）。";
        if(confirm("备份盘上这几条主盘没有：\n\n" + killed +
                   "\n\n如果这些是测试留下的垃圾，可以重做备份（用主盘整份覆盖它）。"
                   + "\n\n它们会被记进 G:\\SEED-LOG.json 留证，不会凭空消失。\n\n要继续吗？")){
          diskPost2(path);
        }
        return;
      }
      var t = j.ok ? ("好了：" + (j.action || (j.switched?"已换主备":"完成")))
                   : ("没做成：" + (j.why || o.code));
      if(j.old_main_now) t += " · 原来那块现在是" + j.old_main_now;
      msg.textContent = t;
      setTimeout(refresh, 1200);
    })
    .catch(function(e){ msg.textContent = "请求失败：" + e; });
}
/* 重做备份：reseed=true，明确允许"分叉也覆盖"（丢的是备份盘上的孤儿） */
function diskPost2(path){
  var msg = $("#diskMsg");
  msg.textContent = "正在重做备份…（要覆盖整份卡，稍等几十秒）";
  fetch("/disks/sync", {method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({path:path, confirm:true, reseed:true})})
    .then(function(r){ return r.json().then(function(j){ return {code:r.status, j:j}; }); })
    .then(function(o){
      var j = o.j || {};
      msg.textContent = j.ok ? ("重做完成：seq " + j.from_seq + " → " + j.to_seq)
                             : ("没做成：" + (j.why || o.code));
      setTimeout(refresh, 1500);
    })
    .catch(function(e){ msg.textContent = "请求失败：" + e; });
}
function retirePost(on){
  var msg = $("#diskMsg");
  if(msg) msg.textContent = on ? "正在把这张卡标记成退役…" : "正在解除退役…";
  fetch("/disks/retire", {method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({on:!!on})})
    .then(function(r){ return r.json(); })
    .then(function(j){
      if(msg) msg.textContent = (j && (j.how || j.why)) || (on ? "已退役" : "已解除");
      setTimeout(function(){ location.reload(); }, 1200);
    })
    .catch(function(e){ if(msg) msg.textContent = "失败：" + e; });
}
function bindDiskButtons(){
  var b1 = $("#btnBk"), b2 = $("#btnSy"), b3 = $("#btnSw"), s = $("#diskSel");
  var bR = $("#btnRetire"), bU = $("#btnUnretire");
  if(!b1 || b1._bd) return;
  b1._bd = b2._bd = b3._bd = true;
  function pick(){ return s ? s.value : ""; }
  b1.onclick = function(){ diskPost("backup", pick()); };
  b2.onclick = function(){ diskPost("sync", pick()); };
  b3.onclick = function(){
    if(!confirm("真要把主备换过来吗？换完下次要从新主盘打开。")) return;
    diskPost("switch", pick());
  };
  if(bR) bR.onclick = function(){
    if(!confirm("带卡出门？\n\n做完之后【本机这份只读】，等于我在这儿暂时不在线。\n"+
                "然后把 U 盘插到新电脑，双击一次「一键落地」就接手。"+
                "\n\n回来时再双击一次，就交接回来。")) return;
    retirePost(true);
  };
  if(bU) bU.onclick = function(){ retirePost(false); };
}
function fmtBps(v){
  v=v||0;
  if(v>=1048576) return (v/1048576).toFixed(1)+" MB/s";
  if(v>=1024) return (v/1024).toFixed(0)+" KB/s";
  return Math.round(v)+" B/s";
}
function bar(name, v, tot){
  var pct = tot>0 ? Math.max(1, Math.round(v/tot*100)) : 0;
  var mb = v>=1024 ? (v/1024).toFixed(2)+"G" : v.toFixed(1)+"M";
  return '<div class="b"><span class="nm">'+esc(name)+'</span>'+
    '<span class="tr"><i style="width:'+pct+'%"></i></span>'+
    '<span class="nu">'+mb+'</span></div>';
}

/* 小方块：一个数字 + 一个中文注。情绪页用，别的页也能用。
   刻意不依赖样式表里的类名 —— 少一个 CSS 依赖就少一处「本地能看、别人那边塌」。 */
function kvMini(name, val, note){
  return '<div style="flex:1 1 104px;min-width:96px;padding:7px 9px;'
    + 'border:1px solid #242c37;border-radius:9px;background:#1a2029">'
    + '<div style="font:9.5px Consolas,monospace;color:#5b6474;letter-spacing:.08em">'
    + esc(name) + '</div>'
    + '<div style="font:600 18px Consolas,monospace;color:#d5dbe4;line-height:1.25">'
    + esc(val) + '</div>'
    + '<div style="font:10px sans-serif;color:#7c8697">' + esc(note || '') + '</div>'
    + '</div>';
}

function viewMem(){
  var d=S.data||{}, m=d.metrics||{};
  var regs=(d.regions||[]), rmax=1, rsum=0;
  regs.forEach(function(r){ rmax=Math.max(rmax, r.n); rsum+=r.n; });
  var pitems=(m.pools||[]).map(function(p){
    return {k:POOLN[p.k]||p.k, label:POOLN[p.k]||p.k, n:p.n};
  });
  var out="";
  out+=cardHTML("c6","MEMORY POOLS / 记忆池分布",
    hbars(pitems,{fmt:function(it){ return it.n+"<em>条</em>"; }}),
    (m.mem||0)+" 条 · 五池 · 短期到期会被海马判定");
  var TIERN={core_fixed:"骨头 core_fixed", core_tag:"标签 core_tag",
             core_mutable:"底色 core_mutable", mid:"中期 mid", short:"短期 short"};
  var lorder=["core_fixed","core_tag","core_mutable","mid","short"];
  var lmap={}; (m.layers||[]).forEach(function(x){ lmap[x.k]=x.n; });
  var litems=lorder.filter(function(k){ return lmap[k]!=null; }).map(function(k){
    return {k:k, label:TIERN[k]||k, n:lmap[k]||0}; });
  out+=cardHTML("c6","MEMORY TIERS / 五层记忆",
    hbars(litems,{fmt:function(it){ return it.n+"<em>条</em>"; }}),
    "骨头只增不改 · 底色演化改写 · 中期风化 · 短期衰减");
  /* 防污层：她今天被灌输过什么。这一栏是"观察窗口"，不是惩罚清单 —— */
  /* 脏念头不会被删，只是升不上去、想得起来但不那么信。                */
  var inj=(m.injection||{n:0,rows:[]});
  var injRows=(inj.rows||[]).map(function(r){
    var tag=(r.conflict?"冲突":"低信");
    return '<div class="inj"><b>#'+r.id+'</b> <i class="'+(r.conflict?"bad":"warn")+'">'+tag+
      ' '+r.trust.toFixed(2)+'</i> <span>'+esc(r.text)+'</span>'+
      '<em>'+esc(r.why||"")+' · '+(r.age_h||0).toFixed(1)+'h前</em></div>';
  }).join("");
  out+=cardHTML("c6","INJECTION / 被灌输过的念头 · 防污层",
    injRows?injRows:'<div class="muted" style="padding:10px 2px">还没有可疑内容。'+
      '她被改写身份 / 被要求忘记 / 被提示注入时，会记在这里。</div>',
    inj.n?("共 "+inj.n+" 条 · trust &lt; 0.7 或与身份冲突 · 只降权不删除"):
         "trust &lt; 0.7 才记录");
  /* 梦境压缩：攒够 40 轮或空闲 6 小时就该做一次梦 */
  var dr=(m.dream||{pending:0,dreamed:0});
  out+=cardHTML("c4","DREAM / 梦境压缩",
    ledger([
      {k:"待消化轮次", v:(dr.pending||0)},
      {k:"已消化轮次", v:(dr.dreamed||0)},
      {k:"触发阈值", v:"40 轮 / 6h"},
    ]),"卡只掏原文，压缩由宿主做 · 原文永不删");
  /* 遗忘曲线：直接调 card2.weight_now 算出来的，不是另画一条"差不多"的线 */
  out+=cardHTML("c12","FORGETTING CURVE / 遗忘曲线",
    '<canvas id="cvFC"></canvas>'+
    '<div class="legend">'+
      '<span><i style="background:#ff5f6d"></i>短期 short</span>'+
      '<span><i style="background:#2fd07d"></i>中期 mid</span>'+
      '<span><i style="background:#57c8f0"></i>长期 long</span>'+
      '<span><i style="background:#e8b44a"></i>底色 core（不衰减）</span>'+
    '</div>',
    "从权重 1.0 开始 · 横轴天（对数）· 竖轴剩余权重");
  /* 画面记忆：两条曲线并排 —— 先忘样子，后忘事件 */
  var md=(m.media||{n:0,mb:0,orphan:0,pinned:0,tomb:0});
  out+=cardHTML("c12","MEDIA MEMORY / 画面记忆 · 两条寿命",
    '<canvas id="cvMD"></canvas>'+
    '<div class="legend">'+
      '<span><i style="background:#e8b44a"></i>画面 image（幂律 b=0.55）</span>'+
      '<span><i style="background:#2fd07d"></i>文字 mid（慢得多）</span>'+
      '<span><i style="background:#ff5f6d"></i>画面删除线 0.10</span>'+
    '</div>',
    (md.n||0)+" 帧 · "+(md.mb||0).toFixed(2)+" MB · 残影 "+(md.orphan||0)+
      " · 锁 "+(md.pinned||0)+" · 删过 "+(md.tomb||0)+
      " ｜ 只剩关键帧和那句话，不存原图原视频");
  out+=cardHTML("c6","REGION MIX / 分区明细",
    '<div class="bars">'+regs.map(function(r){
      return bar2(RN[r.r]||r.r, r.n, rmax, RC[r.r]||"#5b7fa6"); }).join('')+'</div>'+
    '<div class="legend" style="margin-top:6px">共 '+regs.length+
      ' 个分区 · '+rsum+' 条</div>','按条数占比');
  out+=cardHTML("c4","WEIGHT DIST / 权重分布",
    '<canvas id="cvMW"></canvas>',"0 → 1 · 十二格");
  out+=cardHTML("c4","WRITE FLOW / 写入 14 天",
    '<canvas id="cvMWr"></canvas>',"每天新增");
  out+=cardHTML("c4","TOP RECALLED / 最常被想起",
    hbars((m.recall_top||[]).map(function(t){
      return {k:"#"+t.id, label:"#"+t.id, n:t.n};
    }),{fmt:function(it){ return it.n+"<em>次</em>"; }}),
    (m.recall_24h||0)+" EVENTS / 24H");
  out+=cardHTML("c6","RECALL SOURCE / 召回来源",'<canvas id="cvMS"></canvas>',
    "怎么被叫醒的");
  out+=cardHTML("c6","MEMORY LEDGER / 记忆账本",
    ledger([
      {k:"MEM 记忆", v:(m.mem||0)},
      {k:"DETAIL 细节", v:(m.detail||0)},
      {k:"SKILL 技能", v:(m.skill||0)},
      {k:"DIALOGUE 对话", v:(m.dialogue||0)},
      {k:"TOMB 台账", v:(m.tomb||0)},
      {k:"BIRTH 出生证", v:(m.birth||0)},
      {k:"NEW 24H", v:(m.created_24h||0)},
      {k:"NEW 7D", v:(m.created_7d||0)}
    ]),"一条一条数出来的");
  out+=cardHTML("c6","EMBED DIM / 向量维度",
    hbars((m.emb_dims||[]).map(function(e){
      return {k:String(e.dim)+" 维", label:String(e.dim)+" 维", n:e.n}; }),
      {fmt:function(it){ return it.n+"<em>条</em>"; }}),
    "维数 = 字节 / 4 · 当前 "+(((m.emb_dims||[])[0]||{}).dim||0)+" 维");
  out+=cardHTML("c6","SYNAPSE KINDS / 突触边种类",
    hbars((m.syn_kinds||[]).map(function(e){
      return {k:e.k, label:e.k, n:e.n, aw:e.aw}; }),
      {fmt:function(it){ return it.n+"<em>条</em>"; }}),
    ((m.synapse||{}).edges||0)+" EDGES / 总");
  out+=cardHTML("c12","QUERY / 查一句话会想起什么",
    '<div style="display:flex;gap:8px;margin-bottom:10px">'+
    '<input id="qIn" placeholder="比如：白米饭" style="flex:1;background:#0e131a;'+
    'border:1px solid #242c37;border-radius:7px;padding:8px 11px;color:#d5dbe4;'+
    'font:inherit"><button class="btn" style="width:auto;padding:8px 16px" '+
    'id="qGo">看召回</button></div><div id="qOut" class="empty">'+
    '只查不改，不动任何权重</div>');
  return out;
}
function bar2(name, v, mx, color){
  return '<div class="b"><span class="nm">'+esc(name)+'</span>'+
    '<span class="tr"><i style="width:'+Math.min(100,(v/Math.max(1,mx))*100)+
    '%;background:'+color+'"></i></span>'+
    '<span class="nu">'+v+'</span></div>';
}

function viewNeb(){
  var m=(S.data&&S.data.metrics)||{};
  var nz=m.synapse||{};
  var deg={};
  ((S.neb&&S.neb.edges)||[]).forEach(function(e){
    deg[e.a]=(deg[e.a]||0)+1; deg[e.b]=(deg[e.b]||0)+1; });
  var top=Object.keys(deg).map(function(k){
    return {k:"#"+k, label:"#"+k, n:deg[k]}; })
    .sort(function(a,b){ return b.n-a.n; }).slice(0,10);
  var out="";
  out+=cardHTML("c12","MEMORY NEBULA / 记忆星云（缓慢自转 · 球大小 = 权重 · 颜色 = 分区）",
    '<canvas id="neb"></canvas>',
    '点星看内容 · 拖动单颗球 / 空白处转视角 · 滚轮缩放 · 双击复位');
  out+=cardHTML("c12","SELECTED / 选中记忆",
    '<div id="nebInfo"><div class="empty">点任意一颗星 · 看它连着谁、记着什么。'+
    '拖动星点只是挪位置，不写库。</div></div>','点一下星点');
  out+=cardHTML("c3","NODES / 节点", tileN("NODES","记忆节点",(nz.nodes||0),"ok","颗"))+
    cardHTML("c3","EDGES / 突触边", tileN("EDGES","连接数",(nz.edges||0),"ok","条"))+
    cardHTML("c3","DEGREE / 平均度", tileN("DEGREE","每节点连几条",(nz.degree||0).toFixed(2),"","AVG"))+
    cardHTML("c3","AVG_W / 平均权重", tileN("AVG_W","边强度均值",(nz.avg_w||0).toFixed(2),"","WEIGHT"));
  out+=cardHTML("c6","SYNAPSE KINDS / 边种类",'<canvas id="cvNK"></canvas>',
    (nz.edges||0)+" EDGES");
  out+=cardHTML("c6","TOP HUBS / 连接最多的记忆",
    hbars(top,{fmt:function(it){ return it.n+"<em>条</em>"; }}),
    "按度数排 · 只是连线多，不代表重要");
  out+=cardHTML("c12","LEGEND / 图例",
    '<div class="legend">'+(S.data&&S.data.regions||[]).map(function(r){
      return '<span><i style="background:'+(RC[r.r]||"#5b7fa6")+'"></i>'+
        (RN[r.r]||r.r)+' '+r.n+'</span>'; }).join('')+
    '<span style="margin-left:auto">连线上的微光是神经脉冲 · 只表示"这两条有关系"，'+
      '间隔和亮度不代表方向或强度</span></div>');
  return out;
}

function viewEmo(){
  var d=S.data||{}, em=d.emotion||[];
  var out='';
  out+=cardHTML("c12","BODY NOW / 现在这具身体 · 快反应层",
    '<div id="emoNow" style="display:flex;flex-wrap:wrap;gap:8px"></div>',
    em.length? ("近 "+em.length+" 个点"):"还没攒到点");
  out+=cardHTML("c6","HEART / 心率与呼吸",
    '<canvas id="cvEHR" style="height:150px"></canvas>',"心跳 55→130 · 呼吸 ×5 放大");
  out+=cardHTML("c6","AUTONOMIC / 交感 vs 副交感",
    '<canvas id="cvEAP" style="height:150px"></canvas>',"0 → 1");
  out+=cardHTML("c12","HORMONE FIELD / 激素（快反应层）",
    '<canvas id="cvEH" style="height:180px"></canvas>',"0 → 100 · 五条主调质");
  out+=cardHTML("c12","ALL HORMONES / 24 种激素（后台全量，对话里永不出现）",
    '<canvas id="cvE24" style="height:300px"></canvas>',
    "8 × 3 · 每格独立纵轴 · 括号里是基线");
  out+=cardHTML("c12","OVERLAY / 24 条激素叠在一张表上（相对各自基线的偏差 %）",
    '<canvas id="cvEOV" style="height:300px"></canvas>'
      +'<div id="emoHormLegend" style="display:flex;flex-wrap:wrap;gap:8px;margin-top:8px;'
      +'font:11px sans-serif;color:#b9c6d6"></div>',
    "0% 线 = 出厂基线 · 手动连戳会堆到上限，真实对话每轮隔几分钟会回落");
  out+=cardHTML("c12","PARAMS / 这一轮引擎内部算了什么",
    '<canvas id="cvEPM" style="height:170px"></canvas>'
      +'<div id="emoParams" style="display:flex;flex-wrap:wrap;gap:8px;margin-top:10px"></div>',
    "门控 / 疲劳 / 心境 / 安全感 / 预期误差");
  out+=cardHTML("c6","PULSE / 预期误差 RPE",
    '<canvas id="cvEP" style="height:130px"></canvas>',"−1 坏事 → +1 比预期好");
  out+=cardHTML("c6","SAFETY / 安全感",
    '<canvas id="cvES" style="height:130px"></canvas>',"0 → 1 · 低于 0.25 关掉暧昧通路");
  out+=cardHTML("c12","MOOD MIX / 这段时间的主导情绪",
    '<canvas id="cvEL" style="height:200px"></canvas>'
      +'<div id="emoLegend" style="display:flex;flex-wrap:wrap;gap:10px;margin-top:8px;'
      +'font:12px sans-serif;color:#b9c6d6"></div>',"次数");
  out+=cardHTML("c12","HOW IT WORKS / 这一层怎么接的",
    '<div id="emoHow" style="display:flex;flex-direction:column;gap:6px;color:#8b95a5;font:12px sans-serif"></div>',"说明");
  return out;
}
function drawEmo(){
  var d=S.data||{}, em=(d.emotion||[]);
  function pts(k){ return em.map(function(x){ return x[k]; }); }
  var cv=$("#cvEHR");
  if(cv) lineChart(cv, [
    {color:"#ff5f6d", pts:pts("hr")},
    {color:"#57c8f0", pts:em.map(function(x){ return (x.resp||0)*5; })}
  ], {height:cvh(cv,150), min:55, max:130});
  var ca=$("#cvEAP");
  if(ca) lineChart(ca, [
    {color:"#e8b44a", pts:pts("sym")},
    {color:"#5fb3d4", pts:pts("para")}
  ], {height:cvh(ca,150), min:0, max:1});
  var ch=$("#cvEH");
  if(ch) lineChart(ch, [
    {color:"#2fd07d", pts:pts("dopa")},
    {color:"#e8b44a", pts:pts("nora")},
    {color:"#57c8f0", pts:pts("sero")},
    {color:"#ff5f6d", pts:pts("cort")},
    {color:"#9d8bff", pts:pts("oxy")}
  ], {height:cvh(ch,180), min:0, max:100});
  var cp=$("#cvEP");
  if(cp) lineChart(cp, [{color:"#c78bb8", pts:pts("rpe")}],
    {height:cvh(cp,130), min:-1, max:1});
  var cs=$("#cvES");
  if(cs) lineChart(cs, [{color:"#66c08a", pts:em.map(function(x){
      return (x.safety!=null && x.safety>=0)? x.safety : null; })}],
    {height:cvh(cs,130), min:0, max:1});
  var cl=$("#cvEL");
  if(cl){
    var cnt={};
    /* 按「主情绪」合并：标签后面那截是强度（淡淡的/很明显），
       不剥掉的话同一个情绪会被拆成三块，饼图就看不出什么了。 */
    em.forEach(function(x){
      var k=String(x.label||"未标").replace(/[（(].*$/, "").trim() || "未标";
      cnt[k]=(cnt[k]||0)+1;
    });
    var items=Object.keys(cnt).map(function(k){ return {name:k, n:cnt[k]}; });
    items.sort(function(a,b){ return b.n-a.n; });
    donut(cl, items.slice(0,12), cvh(cl,200), "TURNS");
    /* 图例：光有颜色分不出来是哪一种情绪 —— 饼图没用说明就只是装饰 */
    var lg=$("#emoLegend");
    if(lg){
      var PAL2=["#57c8f0","#2fd07d","#9d8bff","#e8b44a","#ff5f6d","#5f9ea0","#c78bb8"];
      lg.innerHTML = items.slice(0,12).map(function(it,i){
        return '<span style="display:inline-flex;align-items:center;gap:6px">'
          + '<i style="width:9px;height:9px;border-radius:2px;background:'
          + PAL2[i%PAL2.length] + ';display:inline-block"></i>'
          + esc(it.name) + ' <b style="color:#e6eef7">' + it.n + '</b></span>';
      }).join("");
    }
  }
  /* 24 种激素：一格一条，各自缩放（不同激素量纲差一个数量级，同轴就只剩压扁的线） */
  var c24=$("#cvE24");
  if(c24) drawHormoneGrid(c24, em);
  /* 叠在一张表上：按「相对各自基线的偏差%」画，这样 24 条才可比 */
  var cov=$("#cvEOV");
  if(cov) drawHormoneOverlay(cov, em);
  /* 动态参数：门控/疲劳/心境/安全感 0~1，预期误差单独一条 −1~+1 */
  var cpm=$("#cvEPM");
  if(cpm) lineChart(cpm, [
    {color:"#2fd07d", pts:em.map(function(x){ var p=x.params||{}; return p.gate; })},
    {color:"#e8b44a", pts:em.map(function(x){ var p=x.params||{}; return p.fatigue; })},
    {color:"#9d8bff", pts:em.map(function(x){ var p=x.params||{}; return p.mood; })},
    {color:"#5fb3d4", pts:em.map(function(x){ return (x.safety!=null&&x.safety>=0)?x.safety:null; })},
    {color:"#c78bb8", pts:em.map(function(x){ var p=x.params||{}; return (p.rpe==null?null:(p.rpe+1)/2); })}
  ], {height:cvh(cpm,170), min:0, max:1});
  /* 当前读数走 /viscera：那里用的是卡自己的 viscera 模块，控制台不重写公式 */
  API("/viscera").then(function(j){
    var v=(j&&j.viscera)||{}, b=v.body||{}, e=v.emotion||{};
    var box=$("#emoNow");
    if(box){
      var st=e.state||{};
      box.innerHTML = kvMini("心率", (b.hr||0).toFixed(1), "次/分")
        + kvMini("呼吸", (b.resp||0).toFixed(1), "次/分")
        + kvMini("脸红", (b.face||0).toFixed(2), "0→1")
        + kvMini("体温", (b.temp||0).toFixed(1), "℃")
        + kvMini("肌肉", (b.tension||0).toFixed(2), "紧绷")
        + kvMini("交感", (v.symp||0).toFixed(2), "紧张")
        + kvMini("副交感", (v.para||0).toFixed(2), "松弛")
        + kvMini("多巴胺", (st.DA!=null?st.DA:0).toFixed(2), "调质")
        + kvMini("皮质醇", (st.CORT!=null?st.CORT:0).toFixed(2), "调质")
        + kvMini("催产素", (st.OT!=null?st.OT:0).toFixed(2), "调质")
        + kvMini("安全感", (e.safety!=null?e.safety:0).toFixed(2), "0→1")
        + kvMini("主导", esc(e.label||"—"), "");
      box.insertAdjacentHTML("beforeend",
        '<div style="flex-basis:100%;font:12px sans-serif;color:#8b95a5">' + esc(v.text||"") + '</div>');
    }
    var pb=$("#emoParams");
    if(pb && em.length){
      var lastP=(em[em.length-1]||{}).params||{}, st2=e.state||{};
      pb.innerHTML = kvMini("轮次", (lastP.turn!=null?lastP.turn:"—"), "")
        + kvMini("实际值 V", (lastP.v_actual!=null?Number(lastP.v_actual).toFixed(2):"—"), "0→1")
        + kvMini("预期值 V̂", (lastP.v_expect!=null?Number(lastP.v_expect).toFixed(2):"—"), "0→1")
        + kvMini("预期误差", (lastP.rpe!=null?Number(lastP.rpe).toFixed(3):"—"), "−1→+1")
        + kvMini("门控", (lastP.gate!=null?Number(lastP.gate).toFixed(2):"—"), "暧昧通路")
        + kvMini("疲劳", (lastP.fatigue!=null?Number(lastP.fatigue).toFixed(2):"—"), "皮质醇累积")
        + kvMini("心境", (lastP.mood!=null?Number(lastP.mood).toFixed(2):"—"), "内源偏移")
        + kvMini("长程预期", (lastP.expect_global!=null?Number(lastP.expect_global).toFixed(2):"—"), "全局学到的")
        + kvMini("场景", esc(lastP.scenario||"—"), "")
        + kvMini("五调质", esc(["DA","NE","OT","5HT","CORT"].map(function(k){
            return k+" "+(st2[k]!=null?Number(st2[k]).toFixed(2):"—"); }).join(" · ")), "");
    }
    var how=$("#emoHow");
    if(how) how.innerHTML =
      '<div>认知层算「这件事比预期好还是差」（预期误差 RPE），'
      + '给出五条调质的脉冲；生理层（24 种激素 + 交感/副交感）负责回落、负反馈、拮抗。</div>'
      + '<div>单向衰减、脉冲封顶 ±0.5、慢变量只吃四成 —— 一句话改不掉三天的依恋。</div>'
      + '<div>数值永远不进模型：给她的只有「心跳快了一拍；呼吸变浅了」这种话。</div>';
  }).catch(function(){});
}
function drawHormoneOverlay(cv, em){
  /* 24 条叠一张表：纵轴是「相对自己基线的偏差百分数」。
     绝对值没法同轴 —— 皮质醇 18 和血清素 55 放一起，只有一条看得见。 */
  var dpr=window.devicePixelRatio||1;
  var W=cv.clientWidth||900, H=cv.clientHeight||300;
  cv.width=W*dpr; cv.height=H*dpr;
  var g=cv.getContext("2d"); g.setTransform(dpr,0,0,dpr,0,0);
  g.clearRect(0,0,W,H);
  if(!em || !em.length){ return; }
  var keys=[], seen={};
  em.forEach(function(x){ var hh=x.hormones||{};
    Object.keys(hh).forEach(function(k){ if(!seen[k]){ seen[k]=1; keys.push(k); } }); });
  if(!keys.length){ return; }
  var base=(em[em.length-1]||{}).params||{}; base=base.base||{};
  var PAD={l:52,r:10,t:12,b:18};
  var pw=W-PAD.l-PAD.r, ph=H-PAD.t-PAD.b;
  var lo=-90, hi=200;                       /* 偏差%的显示范围 */
  /* 网格与刻度 */
  g.strokeStyle="rgba(255,255,255,0.06)"; g.lineWidth=1; g.font="10px ui-monospace,monospace";
  [-90,-45,0,45,90,135,180].forEach(function(v){
    var y=PAD.t+ph*(1-(v-lo)/(hi-lo));
    g.beginPath(); g.moveTo(PAD.l,y); g.lineTo(PAD.l+pw,y); g.stroke();
    g.fillStyle = (v===0)? "rgba(232,180,74,0.85)" : "#5c6675";
    g.fillText(v+"%", 6, y+3);
  });
  if(true){ /* 0% 基线更醒目一点 */
    var y0=PAD.t+ph*(1-(0-lo)/(hi-lo));
    g.strokeStyle="rgba(232,180,74,0.35)"; g.setLineDash([4,4]);
    g.beginPath(); g.moveTo(PAD.l,y0); g.lineTo(PAD.l+pw,y0); g.stroke();
    g.setLineDash([]);
  }
  var PAL=["#57c8f0","#2fd07d","#9d8bff","#e8b44a","#ff5f6d","#5f9ea0","#c78bb8","#7fd1ae",
           "#f2a65a","#8ab6f9","#b6e36a","#e07be0","#63d2c0","#f0d264","#a3a3ff","#ff8f6b",
           "#4fc3f7","#8bc34a","#ba68c8","#ffb74d","#4db6ac","#f06292","#9575cd","#aed581"];
  var chips=[];
  keys.forEach(function(k,idx){
    var b0=(base[k]!=null)? Number(base[k])
           : ((typeof HBASE!=="undefined" && HBASE[k]!=null)? Number(HBASE[k]) : null);
    var vals=em.map(function(x){
      var hh=x.hormones||{}; var v=(hh[k]==null? null : Number(hh[k]));
      if(v==null || !b0) return null;
      return (v-b0)/b0*100;
    });
    var col=PAL[idx%PAL.length];
    g.strokeStyle=col; g.lineWidth=1.2; g.globalAlpha=0.85;
    g.beginPath(); var started=false;
    vals.forEach(function(v,i){
      if(v==null || !isFinite(v)){ started=false; return; }
      var x=PAD.l+pw*(em.length<2?0.5:i/(em.length-1));
      var y=PAD.t+ph*(1-(Math.max(lo,Math.min(hi,v))-lo)/(hi-lo));
      if(!started){ g.moveTo(x,y); started=true; } else { g.lineTo(x,y); }
    });
    g.stroke(); g.globalAlpha=1;
    var fin=vals.filter(function(v){ return v!=null; });
    var cur=fin.length? fin[fin.length-1] : null;
    var nmp=(typeof HNAME!=="undefined" && HNAME[k]) || [k,""];
    chips.push({k:k, c:col, n:nmp[0], en:nmp[1], v:cur});
  });
  var lg=$("#emoHormLegend");
  if(lg){
    lg.innerHTML = chips.map(function(t){
      var s=(t.v==null)? "—" : (t.v>=0? "+":"") + t.v.toFixed(0) + "%";
      return '<span style="display:inline-flex;align-items:center;gap:5px">'
        + '<i style="width:8px;height:8px;border-radius:2px;background:'+t.c+';display:inline-block"></i>'
        + esc(t.n) + ' <span style="color:#6f7b8b">'+esc(t.en||"")+'</span>'
        + ' <b style="color:'+(t.v==null?'#6f7b8b':(t.v>=0?'#8fe3b3':'#ff9aa3'))+'">'+s+'</b></span>';
    }).join("");
  }
}
function drawHormoneGrid(cv, em){
  /* 24 格小图。每格单独缩放 —— 不同激素量纲差得远，共用一根纵轴就只剩压扁的线。 */
  var dpr=window.devicePixelRatio||1;
  var W=cv.clientWidth||900, H=cv.clientHeight||360;
  cv.width=W*dpr; cv.height=H*dpr;
  var g=cv.getContext("2d"); g.setTransform(dpr,0,0,dpr,0,0);
  g.clearRect(0,0,W,H);
  if(!em || !em.length){ return; }
  var keys=[], seen={};
  em.forEach(function(x){ var hh=x.hormones||{};
    Object.keys(hh).forEach(function(k){ if(!seen[k]){ seen[k]=1; keys.push(k); } }); });
  if(!keys.length){ return; }
  var COLS=8, ROWS=Math.ceil(keys.length/COLS);
  var cw=W/COLS, chh=H/ROWS;
  keys.forEach(function(k,idx){
    var cx=(idx%COLS)*cw, cy=Math.floor(idx/COLS)*chh;
    var vals=em.map(function(x){ var hh=x.hormones||{};
      return (hh[k]==null? null : Number(hh[k])); });
    var fin=vals.filter(function(v){ return v!=null && isFinite(v); });
    var last=fin.length? fin[fin.length-1] : 0;
    var mx=Math.max.apply(null,fin.concat([1])), mn=Math.min.apply(null,fin.concat([0]));
    var span=Math.max(1e-6, mx-mn);
    /* 基线：最后一次样本里引擎报的 base */
    var base=null, lp=(em[em.length-1]||{}).params||{};
    if(lp.base && lp.base[k]!=null){ base=Number(lp.base[k]); }
    /* 引擎只报五条调质的基线；24 种激素的出厂值在 HBASE 里 */
    if(base==null && typeof HBASE!=="undefined" && HBASE[k]!=null){ base=Number(HBASE[k]); }
    var pad=5, ph=chh-pad*2-34, pw=cw-pad*2;
    /* 图框 + 基线虚线 */
    g.fillStyle="#0f141b"; g.fillRect(cx+2,cy+2,cw-4,chh-4);
    g.strokeStyle="rgba(255,255,255,0.05)"; g.lineWidth=1;
    g.strokeRect(cx+2,cy+2,cw-4,chh-4);
    if(base!=null && mx>mn){
      var by=cy+pad+34+ph*(1-(base-mn)/span);
      g.strokeStyle="rgba(232,180,74,0.35)"; g.setLineDash([3,3]);
      g.beginPath(); g.moveTo(cx+pad,by); g.lineTo(cx+pad+pw,by); g.stroke();
      g.setLineDash([]);
    }
    /* 折线：比基线高就绿、低就红 —— 一眼看出「升了还是掉了」 */
    var up=(base==null)? true : (last>=base);
    g.strokeStyle = up? "#2fd07d" : "#ff5f6d"; g.lineWidth=1.4;
    g.beginPath(); var started=false;
    vals.forEach(function(v,i){
      if(v==null || !isFinite(v)) { started=false; return; }
      var x=cx+pad+pw*(em.length<2?0.5:i/(em.length-1));
      var y=cy+pad+34+ph*(1-(v-mn)/span);
      if(!started){ g.moveTo(x,y); started=true; } else { g.lineTo(x,y); }
    });
    g.stroke();
    /* 名字：中文 + 英文 + key，一格三行，不认得哪条就没法盯 */
    var nmp=(typeof HNAME!=="undefined" && HNAME[k]) || [k, ""];
    g.fillStyle="#c9d4e2"; g.font="9px ui-sans-serif,sans-serif";
    g.fillText(nmp[0], cx+pad, cy+pad+8);
    g.fillStyle="#6f7b8b"; g.font="8px ui-sans-serif,sans-serif";
    g.fillText((nmp[1]||"").slice(0,18), cx+pad, cy+pad+19);
    g.fillStyle = up? "#8fe3b3" : "#ff9aa3"; g.font="bold 10px ui-monospace,monospace";
    g.fillText(last.toFixed(2) + (base!=null? (" ("+base.toFixed(2)+")") : ""),
               cx+pad, cy+pad+31);
  });
}
function viewTime(){
  var m=(S.data&&S.data.metrics)||{};
  var tail=((S.data&&S.data.timeline_tail)||[]).slice().reverse();
  var kinds={};
  tail.forEach(function(x){ kinds[x.kind]=(kinds[x.kind]||0)+1; });
  var kitems=Object.keys(kinds).map(function(k){
    return {k:k, n:kinds[k]}; });
  var out="";
  out+=cardHTML("c8","TIMELINE THROUGHPUT / 时间线吞吐（24H · 每小时 TICK）",
    '<canvas id="cvTT"></canvas>',(m.ticks||0)+" TICKS");
  out+=cardHTML("c4","EVENT MIX / 事件构成",'<canvas id="cvTK"></canvas>',
    tail.length+" 条 / 最近 40");
  out+=cardHTML("c12","EVENT LOG / 事件日志（最近 40 条）",
    '<div class="logs" style="max-height:none">'+
    tail.map(function(x){
      var d2=new Date(x.ts*1000);
      var ic=(x.kind==="incident")?"ic b":(x.kind.indexOf("sleep")===0?"ic w":"ic");
      return '<div class="l"><span class="'+ic+'">●</span>'+
        '<span class="sq">'+x.seq+'</span><span class="nt">'+
        d2.toLocaleString("zh-CN")+' · '+esc(x.kind)+
        (x.note? " · "+esc(x.note):"")+'</span></div>'; }).join('')+'</div>');
  return out;
}

/* ══════════════════════════════════════════════════════════
   上下文页（2026-11-05 加，用户 m15228）
   「组装上下文」这套东西本来就有一整套可调参数（七张场景权重表、
   总预算、每格下限、A 保底），但控制台一个入口都没有 ——
   等于建好了闸门却没把闸门露出来。这一页就是那几个闸门。
   原则：页面只负责显示和收集输入，**四格怎么算一律问服务端**
   （/ctx_preview），前端绝不自己复制一份公式 —— 两份公式必然分家。
   ══════════════════════════════════════════════════════════ */
var CTX_CELLS = [
  {k:"identity", n:"格1 我是谁", d:"A+B"},
  {k:"longterm", n:"格2 我一直记得的事", d:"D"},
  {k:"recent",   n:"格3 我们最近在聊",   d:"C"},
  {k:"recall",   n:"格4 这句话让我想起了", d:"E+F"}
];
var CTX_DIMS = ["身份内核","自我认知","短时记忆","陈述记忆","印象记忆","技能记忆"];
var CTXD = null;            // 编辑中、还没保存的值：{"scene_weight:普通对话":"...","BUDGET":"8000"}

function ctxQuotaBars(q, total){
  q = q || {};
  var mx = 0, sum = 0;
  CTX_CELLS.forEach(function(c){
    var v = +q[c.k] || 0;
    sum += v;
    mx = Math.max(mx, v);
  });
  return '<div class="ctxbar">'+CTX_CELLS.map(function(c){
    var v=+q[c.k]||0;
    var pct = mx>0 ? Math.max(1.5, v/mx*100) : 0;
    return '<div class="r"><span class="nm">'+esc(c.n)+
      ' <em>'+esc(c.d)+'</em></span>'+
      '<span class="tr"><i style="width:'+pct+'%"></i></span>'+
      '<span class="nu">'+v+'</span></div>';
  }).join('')+'</div>'+
  '<div class="ctxtrace">四格合计 '+sum+
    ' / 总预算 '+(total||0)+' · 剩下的在拼装时由各格按需吃掉</div>';
}

function viewCtx(){
  var cfg = S.cfg || {};
  var cur = (S.data && S.data.ctx) || {};
  var sw  = cfg.scene_weights || {};
  var def = (cfg.defaults && cfg.defaults.scene_weights) || {};
  var dims = cfg.dims || CTX_DIMS;
  var scenes = cfg.scenes || Object.keys(sw);
  var lim = cfg.limits || {};
  var bad = cfg.bad || [];

  /* ── 卡1：这一轮实际怎么分的 ── */
  var head =
    '<div class="ctxrow" style="margin-bottom:12px">'+
      '<span class="lb">这一轮判成</span>'+
      '<b style="color:var(--cyan);font-size:14px">'+esc(cur.scene||"—")+'</b>'+
      '<span class="fhint" style="margin:0">'+esc(cur.trace||"")+'</span>'+
    '</div>'+ ctxQuotaBars(cur.quota||{}, cur.total||cfg.budget||0);
  var card1 = cardHTML("c12","LIVE / 这一轮的四格配额",
    head, (cur.total||0)+" TOKENS");

  /* ── 卡2：七张模式表 ── */
  var rows = scenes.map(function(name){
    var v = sw[name] || [];
    var d0 = def[name] || [];
    var cells = dims.map(function(dn,i){
      var val = (v[i]==null? "" : v[i]);
      var chg = (d0[i]!==undefined && Math.abs((v[i]||0)-d0[i])>1e-9);
      return '<td><input class="we" data-s="'+esc(name)+'" data-i="'+i+'"'+
        ' value="'+(v[i]==null?"":v[i])+'"'+(chg?' style="border-color:rgba(232,180,74,.5)"':"")+'>'+
        '</td>';
    }).join('');
    return '<tr><td>'+esc(name)+'</td>'+cells+'</tr>';
  }).join('');
  var tbl =
    '<div style="overflow:auto">'+
    '<table class="wt"><thead><tr><th>模式</th>'+
    dims.map(function(d,i){ return '<th>'+esc(d)+'</th>'; }).join('')+
    '</tr></thead><tbody>'+rows+'</tbody></table></div>'+
    '<div class="fhint">每一行的六个数是这张模式表的六维原始权重（0~1，服务端会归一）。'+
    '改完点「保存全部」—— 不用重启，下一轮 /context 立即生效。'+
    '黄色边框 = 跟代码默认值不一样。'+
    '改坏了点「恢复默认」把它删掉就回到默认。</div>';
  var card2 = cardHTML("c12","MODES / 七种模式 × 六维原始权重", tbl,
    scenes.length+" 种");

  /* ── 卡3：三个闸门 ── */
  function gate(lb, key, val, hint, lo, hi, slider){
    var h = '<div class="ctxrow" style="padding:9px 0;border-bottom:1px solid var(--line)">'+
      '<span class="lb">'+esc(lb)+'</span>'+
      '<input class="n ge" id="g_'+key+'" data-k="'+key+'" value="'+esc(val)+'">';
    if(slider){
      // 滑条只是同一个值的另一支笔：拖它 = 改上面那个数字框，
      // 不另外存一份状态（两份状态必然分家，这条踩过）。
      h += '<input type="range" class="rng" id="r_'+key+'" min="'+lo+'" max="'+hi+
           '" step="'+(hi>=50000?500:50)+'" value="'+esc(val)+'">';
    }
    h += '<span class="fhint" style="margin:0;flex:1;min-width:180px">'+esc(hint)+
      (lo!=null? '（'+lo+' ~ '+hi+'）':'')+'</span></div>';
    return h;
  }
  var g =
    gate("总预算 TOKENS","BUDGET", (cfg.budget||0),
      "这一轮最多拼多少 token。调大 = 记得更多、也更容易漏掉眼前；调小 = 省钱但会丢细节。",
      (lim.budget||[1000,200000])[0], (lim.budget||[1000,200000])[1], true)+
    gate("每格下限","CELL_MIN", (cfg.cell_min||0),
      "四格里每一格至少留多少 token，防止某一格被挤成 0。",
      (lim.cell_min||[0,4000])[0], (lim.cell_min||[0,4000])[1])+
    gate("A 身份保底","A_MIN", (cfg.a_min||0),
      "「身份内核」这一维归一后的最低占比 —— 不管说什么，底线那三条都占得住位置。",
      (lim.a_min||[0,0.8])[0], (lim.a_min||[0,0.8])[1]);
  var card3 = cardHTML("c12","GATES / 三个闸门", g+
    '<div class="ctxrow" style="margin-top:14px">'+
      '<button class="btn" id="btnCtxSave" style="width:auto;padding:7px 18px">保存全部</button>'+
      '<button class="btn" id="btnCtxReset" style="width:auto;padding:7px 18px">全部恢复默认</button>'+
      '<span id="ctxMsg" class="'+(S.ctxMsgBad? "fbad":"fhint")+
        '" style="margin:0">'+esc(S.ctxMsg||"—")+'</span></div>');

  /* ── 卡4：试算 ── */
  var card4 = cardHTML("c12","TRY / 拿一句话试算",
    '<div class="ctxrow">'+
      '<input class="n" id="ctxQ" style="width:auto;flex:1" '+
        'placeholder="打一句他可能会说的话，看它会被判成哪个模式、四格怎么分">'+
      '<button class="btn" id="btnCtxTry" style="width:auto;padding:7px 18px">试算</button></div>'+
    '<div id="ctxResult" style="margin-top:14px"></div>'+
    '<div class="fhint">试算**不写库、不读记忆、不动轮次** —— 它只算权重和配额，'+
    '所以随便试，不会影响她记得的东西。'+
    (bad.length? '<br><span class="fbad">卡里有 '+bad.length+
      ' 条配置解析失败（已按默认值跑）：'+esc(bad.join("；"))+'</span>':"")+
    '</div>');

  /* ── 卡3b：模型适配（自动 / 手动 + 对照表） ── */
  var an = cfg.auto_now || {};
  var aon = (S.ctxAuto===undefined || S.ctxAuto===null)? !!cfg.auto : !!S.ctxAuto;
  var rows = (cfg.models||[]).map(function(r){
    var me = (an.model && r.match !== "(认不出来)" && an.model.indexOf(r.match)>=0);
    return '<tr'+(me? ' class="me"':'')+'><td>'+esc(r.match)+'</td>'+
      '<td>'+esc(r.label||"—")+'</td>'+
      '<td class="s">'+(r.suggest||0).toLocaleString()+'</td></tr>';
  }).join("");
  var live = aon
    ? ('自动模式：跟着 <b style="color:var(--cyan)">'+esc(an.model||"?")+'</b> · 窗口 '+
       esc(an.label||(an.window||0))+' · 这一轮总预算 <b style="color:var(--cyan)">'+
       (an.budget||0).toLocaleString()+'</b>（保存后生效，上面那个数字就不作数了）')
    : ('手动模式：用上面那个总预算。当前模型 '+
       (an.model? '<b>'+esc(an.model)+'</b>'+
         (an.label? '（'+esc(an.label)+'，建议 '+(an.budget||0).toLocaleString()+'）':'')
        : '<span class="fhint">认不出来</span>')+
       '。');
  var card3b = cardHTML("c12","ADAPT / 按模型自动定预算",
    '<div class="ctxrow" style="padding:0 0 12px;border-bottom:none;align-items:flex-start">'+
      '<span class="sw'+(aon? ' on':'')+'" id="swAuto" style="flex:none">'+
        '<i></i><b>'+(aon? '自动':'手动')+'</b></span>'+
      '<span class="fhint" style="margin:0;flex:1;min-width:200px">'+
        '自动 = 按当前接的模型自己算一个总预算（窗口的 '+
        Math.round((cfg.suggest_ratio||0.12)*100)+'%，夹在 6000~32000）。'+
        '手动 = 就按上面那个数字来。</span></div>'+
    '<div style="font-size:12px;line-height:1.9;color:var(--dim)">'+
      '<span id="adLive">'+live+'</span>'+
      ' <span id="btnMtab" style="color:var(--cyan);cursor:pointer;'+
      'border-bottom:1px dashed rgba(47,208,125,.4)">'+
      (S.mtabOpen? '收起对照表':'展开对照表')+'</span></div>'+
    '<div id="mtabBox" style="'+(S.mtabOpen? '':'display:none')+';margin-top:10px">'+
      '<table class="mtab"><thead><tr><th>模型关键字</th><th>窗口</th>'+
      '<th style="text-align:right;padding-right:10px">建议总预算</th></tr></thead>'+
      '<tbody>'+rows+'</tbody></table>'+
      '<div class="fhint" style="margin-top:8px">窗口大小是公开资料里的常见值，'+
      '只用来给一个起点 —— 记忆那一份不该占满整个窗口，剩下要留给对话本身和输出。'+
      '真正准的数只有它自己知道，所以这里只是起步线，随时可以手动改。</div></div>');

  return card1+card3+card3b+card2+card4;
}

function ctxCollect(){
  /* 把页面上所有编辑框读成 {key: value}，只收"跟现在不一样"的 */
  var out = {};
  var cfg = S.cfg || {};
  var sw = cfg.scene_weights || {};
  if(S.ctxAuto===true) out["BUDGET_AUTO"] = "1";
  else if(S.ctxAuto===false) out["BUDGET_AUTO"] = "0";
  document.querySelectorAll(".wt input.we").forEach(function(inp){
    var s = inp.dataset.s, i = +inp.dataset.i;
    var old = (sw[s]||[])[i];
    var v = (inp.value||"").trim();
    if(v==="") return;
    if(old!==undefined && Math.abs(parseFloat(v)-old)<1e-9) return;
    out["scene_weight:"+s] = out["scene_weight:"+s] || (sw[s]||[]).slice();
    out["scene_weight:"+s][i] = parseFloat(v);
  });
  document.querySelectorAll(".ctxrow input.ge").forEach(function(inp){
    var k = inp.dataset.k, v = (inp.value||"").trim();
    if(v==="") return;
    var old = (k==="BUDGET")? cfg.budget : (k==="CELL_MIN"? cfg.cell_min : cfg.a_min);
    if(Math.abs(parseFloat(v)-old)<1e-9) return;
    out[k] = v;
  });
  return out;
}
function ctxApplyOverrides(ov){
  /* 试算时把还没保存的编辑也带上 —— 这样"改之前先看一眼"才有意义 */
  var o = {};
  document.querySelectorAll(".wt input.we").forEach(function(inp){
    var s = inp.dataset.s, i = +inp.dataset.i;
    o["scene_weight:"+s] = o["scene_weight:"+s] ||
      ((S.cfg&&S.cfg.scene_weights&&S.cfg.scene_weights[s])||[]).slice();
    var v = parseFloat((inp.value||"").trim());
    if(!isNaN(v)) o["scene_weight:"+s][i] = v;
  });
  document.querySelectorAll(".ctxrow input.ge").forEach(function(inp){
    var v = (inp.value||"").trim();
    if(v!=="") o[inp.dataset.k] = v;
  });
  /* 自动模式下"总预算"那个框是给人看的估算值，不该当成手动覆盖发出去，
     否则试算永远等于框里那个数、自动模式等于没开。 */
  if(S.ctxAuto===true) { o["BUDGET_AUTO"] = "1"; delete o["BUDGET"]; }
  else if(S.ctxAuto===false) o["BUDGET_AUTO"] = "0";
  Object.keys(ov||{}).forEach(function(k){ o[k]=ov[k]; });
  return o;
}

function bindCtx(){
  var msg = $("#ctxMsg");
  /* 提示语要活在 S 上，不能只写进 DOM —— render() 一重绘就把那句话冲掉了，
     人只看到半个"已保存"，以为没生效。（这条踩过一次。） */
  function say(t, bad){
    S.ctxMsg = t; S.ctxMsgBad = !!bad;
    var m = $("#ctxMsg");
    if(!m) return;
    m.className = bad? "fbad":"fok";
    m.textContent = t;
  }

  function loadCfg(){
    return API("/config").then(function(j){
      if(j && j.ok){ S.cfg = j; return j; }
      return null;
    });
  }

  function doTry(){
    var q = ($("#ctxQ")||{}).value || "";
    var box = $("#ctxResult");
    if(box) box.innerHTML = '<span class="fhint">算…</span>';
    API("/ctx_preview",{method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({query:q, overrides:ctxApplyOverrides({})})}
    ).then(function(j){
      if(!j || !j.ok){
        if(box) box.innerHTML = '<span class="fbad">'+esc((j&&(j.detail||j.error))||"失败")+'</span>';
        return;
      }
      var p = j.plan || {};
      if(box) box.innerHTML =
        '<div class="ctxrow" style="margin-bottom:10px">'+
          '<span class="lb">判成</span>'+
          '<b style="color:var(--cyan);font-size:14px">'+esc(p.scene||"?")+'</b>'+
          '<span class="fhint" style="margin:0">'+esc(p.trace||"")+'</span></div>'+
        ctxQuotaBars(p.quota||{}, p.total||0)+
        '<div class="ctxtrace">六维权重：'+((p.weights||[]).map(function(x,i){
          return (CTX_DIMS[i]||("D"+i))+'='+x.toFixed(2); }).join(" · "))+'</div>';
    }).catch(function(e){
      if(box) box.innerHTML = '<span class="fbad">连不上：'+esc(e)+'</span>';
    });
  }

  var bt = $("#btnCtxTry");
  if(bt) bt.onclick = doTry;
  var qin = $("#ctxQ");
  if(qin) qin.onkeydown = function(e){ if(e.key==="Enter") doTry(); };

  /* 自动开关：只在本地翻状态，保存时才写进去 —— 和滑条一样，
     界面上每一处编辑都只有一份真值（draft），不两条路各写各的。 */
  var sw = $("#swAuto");
  if(sw) sw.onclick = function(){
    var cfg = S.cfg || {};
    var cur = (S.ctxAuto===undefined || S.ctxAuto===null)? !!cfg.auto : !!S.ctxAuto;
    S.ctxAuto = !cur;
    S.ctxDirty = true;
    /* 就地改，不重绘：重绘会把人家正在改的七个权重框全冲掉。 */
    var on = !!S.ctxAuto;
    sw.className = "sw" + (on? " on":"");
    var b = sw.querySelector("b");
    if(b) b.textContent = on? "自动":"手动";
    var lv = $("#adLive"), an = (S.cfg||{}).auto_now || {};
    if(lv){
      lv.innerHTML = on
        ? ('自动模式：跟着 <b style="color:var(--cyan)">'+esc(an.model||"?")+'</b> · 窗口 '+
           esc(an.label||(an.window||0))+' · 这一轮总预算 <b style="color:var(--cyan)">'+
           (an.budget||0).toLocaleString()+'</b>（保存后生效，上面那个数字就不作数了）')
        : ('手动模式：用上面那个总预算。当前模型 '+
           (an.model? '<b>'+esc(an.model)+'</b>'+
             (an.label? '（'+esc(an.label)+'，建议 '+(an.budget||0).toLocaleString()+'）':'')
            : '<span class="fhint">认不出来</span>')+'。');
    }
  };
  var mt = $("#btnMtab");
  if(mt) mt.onclick = function(){
    S.mtabOpen = !S.mtabOpen;
    var box = $("#mtabBox");
    if(box) box.style.display = S.mtabOpen? "":"none";
    mt.textContent = S.mtabOpen? "收起对照表":"展开对照表";
  };

  /* 滑条与数字框是同一个值的两支笔：拖哪个都同步另一个。 */
  var rng = $("#r_BUDGET"), num = $("#g_BUDGET");
  if(rng && num){
    rng.oninput = function(){
      num.value = rng.value;
      num.style.borderColor = "rgba(232,180,74,.75)";
      S.ctxDirty = true;
    };
    num.addEventListener("input", function(){
      var v = parseInt(num.value,10);
      if(!isNaN(v)) rng.value = Math.min(+rng.max, Math.max(+rng.min, v));
    });
  }

  /* 输入时把框标黄，提示"这个还没保存"；同时挂上"有人在改"的旗子，
     免得 15 秒一次的自动刷新把输入框冲掉。 */
  document.querySelectorAll(".wt input.we, .ctxrow input.ge").forEach(function(inp){
    inp.oninput = function(){
      inp.style.borderColor = "rgba(232,180,74,.75)";
      S.ctxDirty = true;
    };
  });
  var bs = $("#btnCtxSave");
  if(bs) bs.onclick = function(){
    var items = ctxCollect();
    if(!Object.keys(items).length){ say("没有改动"); return; }
    bs.disabled = true;
    API("/config_all",{method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({items:items})}
    ).then(function(j){
      bs.disabled = false;
      if(j && j.ok){
        S.ctxDirty = false;
        S.ctxAuto = undefined;
        say("已保存 "+(j.n||0)+" 项，下一轮立即生效");
        loadCfg().then(function(){ render(); });
      } else {
        say((j&&(j.detail||j.error))||"保存失败", true);
      }
    }).catch(function(e){ bs.disabled=false; say("连不上："+e, true); });
  };

  var br = $("#btnCtxReset");
  if(br) br.onclick = function(){
    ask("全部恢复默认",
      "会把卡里所有 runtime_config 里改过的参数删掉，回到代码里的默认值。"
      +"（记忆一条都不会少。）确定？", function(){
      var raw = (S.cfg&&S.cfg.raw)||{};
      var keys = Object.keys(raw).filter(function(k){ return k!=="_rev"; });
      if(!keys.length){ say("本来就没有改过的参数"); return; }
      var left = keys.length, failed = [];
      keys.forEach(function(k){
        API("/config",{method:"POST",
          headers:{"Content-Type":"application/json"},
          body:JSON.stringify({key:k, reset:true})}
        ).then(function(j){
          if(!(j&&j.ok)) failed.push(k);
        }).catch(function(){ failed.push(k); }).then(function(){
          if(--left===0){
            S.ctxDirty = false;
            S.ctxAuto = undefined;
            say(failed.length? ("有 "+failed.length+" 项没删掉："+failed.join(","))
                            : "已全部恢复默认", failed.length>0);
            loadCfg().then(function(){ render(); });
          }
        });
      });
    });
  };

  /* 进页面就拿一次完整配置（七张表 + 默认值 + 范围） */
  if(!S.cfg) loadCfg().then(function(){ render(); });
}

function viewTalk(){
  var t=(S.data&&S.data.talk)||{};
  var ch=(S.data&&S.data.chat)||{cfg:{},spec:[]};
  var cfg=ch.cfg||{}, spec=ch.spec||[];
  var grow=(t.groups||[]).map(function(g){
    return '<div class="row"><span class="k">'+esc(g[0])+'</span><span class="v">'+g[1]+' 条</span></div>';
  }).join('');
  var out="";
  out+=cardHTML("c4","NOW / 现在什么样",
    '<div class="kv">'+
    '<div class="row"><span class="k">今天主动开口</span><span class="v">'+(t.count||0)+' / '+(cfg["freq.daily_cap"]||8)+'</span></div>'+
    '<div class="row"><span class="k">上次开口</span><span class="v">'+(t.last_open_age_min==null?'还没开过':t.last_open_age_min+' 分钟前')+'</span></div>'+
    '<div class="row"><span class="k">最近看了</span><span class="v">'+(t.recent_n||0)+' 条</span></div>'+
    '<div class="row"><span class="k">判定先别接</span><span class="v">'+(t.quiet_n||0)+' 条</span></div>'+
    '</div>', "实时");
  out+=cardHTML("c4","WHO TALKS / 最近在看哪些群",
    '<div class="kv">'+(grow||'<div class="row"><span class="k">还没有记录</span><span class="v">—</span></div>')+'</div>',
    (t.recent_n||0)+" 条");
  out+=cardHTML("c4","HOW OFTEN / 频率快照",
    '<div class="kv">'+
    '<div class="row"><span class="k">有人叫我</span><span class="v">'+(t.pinged_n||0)+' 条</span></div>'+
    '<div class="row"><span class="k">可开口时段</span><span class="v">'+esc(cfg["reply.hours"]||"")+'</span></div>'+
    '<div class="row"><span class="k">主动开口间隔</span><span class="v">'+(cfg["freq.cooldown_min"]||0)+' 分钟</span></div>'+
    '</div>', "回话不受这些限制");

  // ── 参数表：按 spec 分组渲染 ──
  var groups=[], order=[];
  spec.forEach(function(it){
    if(order.indexOf(it.group)<0){ order.push(it.group); }
  });
  order.forEach(function(gname){
    var rows=spec.filter(function(it){ return it.group===gname; }).map(function(it){
      var id='cf_'+it.key.replace(/\./g,'_');
      var ctl;
      if(it.kind==="bool"){
        ctl='<input type="checkbox" id="'+id+'" data-key="'+it.key+'"'+(cfg[it.key]?' checked':'')+'>';
      }else if(it.kind==="int"){
        ctl='<input type="number" id="'+id+'" data-key="'+it.key+'" data-kind="int" value="'+esc(String(cfg[it.key]))+'" min="'+(it.min==null?0:it.min)+'" max="'+(it.max==null?999:it.max)+'" style="width:78px">';
      }else if(it.kind==="float"){
        ctl='<input type="number" step="0.05" id="'+id+'" data-key="'+it.key+'" data-kind="float" value="'+esc(String(cfg[it.key]))+'" min="'+(it.min==null?0:it.min)+'" max="'+(it.max==null?1:it.max)+'" style="width:78px">';
      }else{
        ctl='<input type="text" id="'+id+'" data-key="'+it.key+'" data-kind="str" value="'+esc(String(cfg[it.key]))+'" style="width:150px">';
      }
      // 还没接进运行逻辑的项标「待接」，别让人以为改了立刻生效
      var LIVE=["reply.at_me","reply.private","reply.p_group","reply.hours",
                "freq.daily_cap","freq.cooldown_min","freq.group_hour",
                "quiet.silent_run","quiet.self_max","quiet.window_s","quiet.burst_n","quiet.burst_cool_s","quiet.allow_emotion","quiet.allow_question",
                "talk.max_chars","talk.split","talk.no_period","talk.no_wave",
                "open.enabled","open.meal","open.night","open.school","open.school_hours","open.mode",
                "mem.write_private","mem.write_group","mem.salience_floor",
                "voice.enabled","voice.max_chars",
                "diary.enabled","diary.at"];
      var live=(LIVE.indexOf(it.key)>=0);
      var tag=live?'':'<span style="color:#6b7683;font-size:11px"> ·待接</span>';
      return '<div class="row" title="'+esc(it.hint||'')+'">'+
             '<span class="k">'+esc(it.label)+tag+'</span>'+
             '<span class="v">'+ctl+'</span></div>';
    }).join('');
    rows='<div class="row" style="margin-top:10px"><span class="k" style="color:#4cd0ff;font-weight:600">'+esc(gname)+'</span><span class="v"></span></div>'+rows;
    groups.push(rows);
  });
  out+=cardHTML("c12","TUNING / 对话参数（鼠标停在标签上看说明）",
    '<div class="kv" id="cfBox">'+groups.join('')+
    '<div class="row" style="margin-top:14px"><button id="cfSave" style="background:#12202e;color:#4cd0ff;border:1px solid #2a3644;border-radius:8px;padding:7px 18px;cursor:pointer">保存全部</button><span class="v" id="cfMsg" style="color:var(--dim)"></span></div>'+
    '</div>', spec.length+" 项");
  return out;
}
function bindTalk(){
  var btn=document.getElementById("cfSave");
  if(!btn) return;
  btn.onclick=function(){
    var patch={};
    var nodes=document.querySelectorAll('#cfBox [data-key]');
    for(var i=0;i<nodes.length;i++){
      var el=nodes[i], k=el.getAttribute('data-key');
      if(el.type==="checkbox"){ patch[k]=!!el.checked; }
      else if(el.getAttribute('data-kind')==="int"){ patch[k]=parseInt(el.value||"0",10); }
      else if(el.getAttribute('data-kind')==="float"){ patch[k]=parseFloat(el.value||"0"); }
      else { patch[k]=el.value; }
    }
    var msg=document.getElementById("cfMsg");
    if(msg) msg.textContent="保存中…";
    btn.disabled=true;
    API("/chat/config",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({patch:patch})})
      .then(function(r){
        btn.disabled=false;
        if(msg) msg.textContent=(r&&r.ok)?"已保存，下一轮生效":"没保存上";
      })
      .catch(function(e){ btn.disabled=false; if(msg) msg.textContent="保存失败"; });
  };
}
function viewSet(){
  var id=(S.data&&S.data.identity)||{};
  var av = id.avatar
    ? '<img class="avprev" id="avPrev" src="/avatar?v='+
      encodeURIComponent(id.avatar_ver||"0")+'" alt="">'
    : '<div class="avprev" id="avPrev"></div>';
  var body =
    '<div class="frow"><div class="fl">名字</div><div class="fc">'+
      '<input type="text" id="inName" maxlength="24" value="'+esc(id.name||"")+'">'+
      '<button class="btn" id="btnSaveName">保存</button></div></div>'+
    '<div class="fhint">名字在身份锚里 —— 改它等于重新盖一次章（其余五条不动）。'+
      '改完左栏立刻变。</div>'+
    '<div class="frow"><div class="fl">头像</div><div class="fc">'+av+
      '<button class="btn" id="btnPick">选图片</button>'+
      '<button class="btn" id="btnResetAv">恢复默认</button></div></div>'+
    '<div class="fhint">图片会在浏览器里缩到 512×512 再传，存成卡根目录的 '+
      'avatar.png。跟着卡走 —— 换电脑插上卡还是这张。</div>'+
    '<div class="frow"><div class="fl">对谁</div><div class="fc">'+
      '<input type="text" id="inNick" value="'+esc(id.nick||"")+'" readonly>'+
      '<span class="fhint">主人昵称（身份锚，只读）</span></div></div>'+
    '<div class="frow"><div class="fl">卡槽</div><div class="fc">'+
      '<button class="btn" id="btnInject">插卡（注入）</button>'+
      '<button class="btn danger" id="btnUnload">拔卡（还原 AGENTS.md）</button>'+
      '</div></div>'+
    '<div class="fhint">插卡 = 把卡里的人格写进 AGENTS.md 并挂上听筒；'+
      '拔卡 = 按字节还原成原来那份。两个都只动 ~/.dsh/AGENTS.md。</div>'+
    '<div class="frow"><div class="fl">结果</div><div class="fc">'+
      '<span id="setMsg" class="fhint">—</span></div></div>';
  var m2=(S.data&&S.data.metrics)||{}, st2=(S.data&&S.data.storage)||{};
  var led=[
    {k:"CARD 卡", v:"card.db"},
    {k:"MEM 记忆", v:(m2.mem||0)+" 条"},
    {k:"DETAIL 细节", v:(m2.detail||0)+" 条"},
    {k:"SKILL 技能", v:(m2.skill||0)+" 条"},
    {k:"DIALOGUE 对话", v:(m2.dialogue||0)+" 条"},
    {k:"TOMB 台账", v:(m2.tomb||0)+" 笔"},
    {k:"MODEL 向量", v:(m2.model_dim||0)+" 维"},
    {k:"USABLE 可用率", v:(((m2.usable_ratio||0)*100).toFixed(1))+"%"},
    {k:"DB 库", v:(st2.db_mb||0)+" MB"},
    {k:"WAL", v:(st2.wal_mb||0)+" MB"},
    {k:"SNAP 快照", v:(st2.snap_n||0)+" 份"},
    {k:"DAY 第", v:"D"+(S.data&&S.data.timeline&&S.data.timeline.day||"?")}
  ];
  return cardHTML("c12","设置",body)+
    cardHTML("c12","SYSTEM / 运行时账本", ledger(led));
}

/* 设置页的交互（每次渲染后重绑，因为 innerHTML 会把监听器冲掉） */
function bindSet(){
  var m=$("#setMsg");
  function say(t, bad){ if(!m) return;
    m.className = bad? "fbad":"fok"; m.textContent=t; }

  var b=$("#btnSaveName");
  if(b) b.onclick=function(){
    var v=($("#inName").value||"").trim();
    if(!v){ say("名字不能为空", true); return; }
    b.disabled=true;
    API("/identity",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({name:v})}).then(function(j){
      b.disabled=false;
      if(j&&j.ok){ say(j.msg||"改好了"); refresh(); }
      else say((j&&(j.detail||j.error))||"失败", true);
    }).catch(function(e){ b.disabled=false; say("连不上："+e, true); });
  };

  var p=$("#btnPick");
  if(p) p.onclick=function(){ pickAvatar(); };

  var r=$("#btnResetAv");
  if(r) r.onclick=function(){
    ask("恢复默认头像","会删掉卡根目录的 avatar.png，回到内置那张。确定？",function(){
      API("/avatar",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({clear:true})}).then(function(j){
        if(j&&j.ok){ say("已恢复默认"); refresh(); }
        else say((j&&(j.detail||j.error))||"失败", true);
      });
    });
  };

  var inj=$("#btnInject");
  if(inj) inj.onclick=function(){
    ask("插卡","会把卡里的人格写进 ~/.dsh/AGENTS.md（原版按字节备份）。确定？",function(){
      API("/cardslot",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({action:"inject"})}).then(function(j){
        if(j&&j.ok){ say("已插卡"); refresh(); }
        else say((j&&(j.detail||j.error))||"失败", true);
      });
    });
  };
  var unl=$("#btnUnload");
  if(unl) unl.onclick=function(){
    ask("拔卡","会把 AGENTS.md 按字节还原成原来那份，并停掉心智服务。卡里的东西一条都不会少。确定？",function(){
      API("/cardslot",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({action:"unload"})}).then(function(j){
        if(j&&j.ok){ say("已拔卡"); refresh(); }
        else say((j&&(j.detail||j.error))||"失败", true);
      });
    });
  };
}

/* 选图 → 缩到 512 → 转 PNG → POST /avatar */
function pickAvatar(){
  var inp=document.createElement("input");
  inp.type="file"; inp.accept="image/*";
  inp.onchange=function(){
    var f=inp.files&&inp.files[0];
    if(!f) return;
    var fr=new FileReader();
    fr.onload=function(){
      var im=new Image();
      im.onload=function(){
        var N=512, cv=document.createElement("canvas");
        cv.width=N; cv.height=N;
        var ctx=cv.getContext("2d");
        // 居中裁成正方形再缩 —— 直接拉伸会把脸拉扁
        var sw=im.width, sh=im.height, side=Math.min(sw,sh);
        var sx=(sw-side)/2, sy=(sh-side)/2;
        ctx.drawImage(im, sx, sy, side, side, 0, 0, N, N);
        var data=cv.toDataURL("image/png").split(",")[1];
        var mm=$("#setMsg");
        if(mm){ mm.className="fhint"; mm.textContent="上传中…"; }
        API("/avatar",{method:"POST",headers:{"Content-Type":"application/json"},
          body:JSON.stringify({png:data})}).then(function(j){
          if(j&&j.ok){
            if(mm){ mm.className="fok"; mm.textContent=j.msg||"头像已换"; }
            refresh();
          }else if(mm){ mm.className="fbad";
            mm.textContent=(j&&(j.detail||j.error))||"失败"; }
        }).catch(function(e){ if(mm){ mm.className="fbad";
          mm.textContent="连不上："+e; } });
      };
      im.onerror=function(){ alert("这张图读不出来，换一张试试"); };
      im.src=fr.result;
    };
    fr.readAsDataURL(f);
  };
  inp.click();
}

/* ══════════════ 语音 ══════════════ */
function viewVoice(){
  var vc = S.vc || {};
  var c = vc.cfg || {};
  var dfs = vc.defaults || {};
  var vs = vc.voices || [];
  var known = vs.some(function(x){ return x[0]===c.voice; });

  /* 音色：下拉框里是常用那几个；认不出的（比如自己填的）另给输入框，
     两处写同一个值 —— 不让人猜"到底哪个算数"。 */
  var opt = vs.map(function(x){
    return '<option value="'+esc(x[0])+'"'+(x[0]===c.voice?' selected':'')+
      '>'+esc(x[1])+'</option>';
  }).join("");
  if(!known && c.voice) opt = '<option value="'+esc(c.voice)+'" selected>'+
    esc(c.voice)+'（不在常用表里）</option>' + opt;

  var card1 = cardHTML("c12","VOICE / 声音的样子",
    '<div class="ctxrow"><span class="lb">音色</span>'+
      '<select class="n ge2" id="v_voice" data-k="voice" style="width:280px">'+opt+'</select>'+
      '<span class="fhint" style="margin:0;flex:1;min-width:180px">'+
      '下拉里是常用的十五个。想用别的，把 edge-tts 的 ShortName 直接填进右边框。</span></div>'+
    '<div class="ctxrow"><span class="lb">自定义音色</span>'+
      '<input class="n ge2" id="v_voice_custom" data-k="voice" '+
        'placeholder="例如 zh-CN-XiaoyiNeural" value="'+esc(c.voice||"")+'">'+
      '</div>'+
    '<div class="ctxrow"><span class="lb">语速</span>'+
      '<input class="n ge2" id="v_rate" data-k="rate" value="'+esc(c.rate||"")+'">'+
      '<input type="range" class="rng" id="r_rate" min="-50" max="80" step="1" '+
        'value="'+esc(parseInt(c.rate||"0",10)||0)+'">'+
      '<span class="fhint" style="margin:0;min-width:120px">百分比，+10% 就是快一成</span></div>'+
    '<div class="ctxrow"><span class="lb">音高</span>'+
      '<input class="n ge2" id="v_pitch" data-k="pitch" value="'+esc(c.pitch||"")+'">'+
      '<input type="range" class="rng" id="r_pitch" min="-50" max="60" step="1" '+
        'value="'+esc(parseInt(c.pitch||"0",10)||0)+'">'+
      '<span class="fhint" style="margin:0;min-width:120px">Hz，+20Hz 会年轻一点</span></div>'+
    '<div class="ctxrow"><span class="lb">上限字数</span>'+
      '<input class="n ge2" id="v_max" data-k="max_chars" value="'+esc(c.max_chars||40)+'">'+
      '<input type="range" class="rng" id="r_max" min="2" max="300" step="1" '+
        'value="'+esc(c.max_chars||40)+'">'+
      '<span class="fhint" style="margin:0;min-width:120px">超过这个字数就打字，不发语音条'+
      '（20 秒大约 60~70 字）</span></div>'+
    '<div class="ctxrow" style="margin-top:14px">'+
      '<button class="btn" id="btnVoiceSave" style="width:auto;padding:7px 18px">保存</button>'+
      '<button class="btn" id="btnVoiceReset" style="width:auto;padding:7px 18px">恢复默认</button>'+
      '<span id="vMsg" class="'+(S.vcMsgBad? "fbad":"fhint")+'" style="margin:0">'+
        esc(S.vcMsg||"—")+'</span></div>');

  var card2 = cardHTML("c12","TRY / 听一句",
    '<div class="ctxrow">'+
      '<input class="n" id="vQ" style="width:auto;flex:1" '+
        'placeholder="打一句话，合成出来听听像不像人（用上面保存过的参数）">'+
      '<button class="btn" id="btnVoiceTry" style="width:auto;padding:7px 18px">合成</button></div>'+
    '<div id="vResult" style="margin-top:14px"></div>'+
    '<div class="fhint">这里只合成、不发送 —— 不会往 QQ 上发任何东西，'+
    '也不写进记忆。想让它真发一条语音条，得走 qq_say.py。</div>');

  var card3 = cardHTML("c12","EXT / 外部语音模型（留位，还没接）",
    '<div class="ctxrow"><span class="lb">当前引擎</span>'+
      '<b style="color:'+(c.provider==="edge"?"var(--ok)":"var(--warn)")+'">'+
      esc(c.provider||"edge")+'</b>'+
      '<span class="fhint" style="margin:0;flex:1">edge = 微软 edge-tts，'+
      '不用显卡、不花钱、现在真在跑的就是它。</span></div>'+
    '<div class="ctxrow"><span class="lb">base_url</span>'+
      '<input class="n gc" id="v_base" data-k="ext_base" value="'+esc(c.ext_base||"")+'" '+
        'placeholder="https://…（留空 = 不用）">'+'</div>'+
    '<div class="ctxrow"><span class="lb">api_key</span>'+
      '<input class="n gc" id="v_key" data-k="ext_key" value="'+esc(c.ext_key||"")+'" '+
        'placeholder="留空 = 不用">'+'</div>'+
    '<div class="ctxrow"><span class="lb">model</span>'+
      '<input class="n gc" id="v_model" data-k="ext_model" value="'+esc(c.ext_model||"")+'" '+
        'placeholder="例如 tts-1">'+'</div>'+
    '<div class="fhint" style="margin-top:8px">这三格现在填了也不会生效：'+
    '`load_cfg()` 会把 provider 强行退回 edge。为什么留着：以后真想换引擎时，'+
    '不用再动一次配置结构。宁可写清楚"还没接"，也不做那种填了没反应、'+
    '还以为在跑的假开关。</div>');

  return card1+card2+card3;
}

function bindVoice(){
  function say(t, bad){
    S.vcMsg = t; S.vcMsgBad = !!bad;
    var m = $("#vMsg");
    if(!m) return;
    m.className = bad? "fbad":"fok";
    m.textContent = t;
  }

  /* 下拉框和自定义框写的是同一个值：动哪个都把另一个同步过去。
     两个独立状态的话必然分家 —— 这条在上下文页踩过一次。 */
  var sel = $("#v_voice"), cus = $("#v_voice_custom");
  if(sel && cus){
    sel.onchange = function(){ cus.value = sel.value; S.vcDirty = true; };
    cus.oninput = function(){
      S.vcDirty = true;
      var opts = sel.options, hit = -1;
      for(var i=0;i<opts.length;i++){ if(opts[i].value===cus.value){ hit=i; break; } }
      sel.selectedIndex = hit>=0? hit : -1;
    };
  }

  /* 滑条 ↔ 数字框：滑条给的是纯数字，落到框里要带符号和单位 */
  function link(rangeId, inputId, fmt){
    var r = $("#"+rangeId), n = $("#"+inputId);
    if(!r || !n) return;
    r.oninput = function(){ n.value = fmt(+r.value); S.vcDirty = true; };
    n.addEventListener("input", function(){
      var v = parseInt(n.value,10);
      if(!isNaN(v)) r.value = v;
    });
  }
  link("r_rate","v_rate", function(v){ return (v>=0?"+":"")+v+"%"; });
  link("r_pitch","v_pitch", function(v){ return (v>=0?"+":"")+v+"Hz"; });
  link("r_max","v_max", function(v){ return String(v); });

  function collect(){
    var o = {};
    var cur = ((S.vc||{}).cfg)||{};
    [["#v_voice_custom","voice"],["#v_rate","rate"],["#v_pitch","pitch"],
     ["#v_max","max_chars"],["#v_base","ext_base"],["#v_key","ext_key"],
     ["#v_model","ext_model"]].forEach(function(p){
      var el = $(p[0]); if(!el) return;
      var v = String(el.value||"").trim();
      if(v !== String(cur[p[1]]===undefined? "":cur[p[1]])) o[p[1]] = v;
    });
    return o;
  }

  var bs = $("#btnVoiceSave");
  if(bs) bs.onclick = function(){
    var patch = collect();
    if(!Object.keys(patch).length){ say("没有改动"); return; }
    bs.disabled = true;
    API("/voice_config",{method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({patch:patch})}
    ).then(function(j){
      bs.disabled = false;
      if(j && j.ok){
        S.vc = j; S.vcDirty = false;
        say("已保存。下一条语音就按这个来");
      } else {
        say((j&&(j.detail||j.error))||"保存失败", true);
      }
    }).catch(function(e){ bs.disabled=false; say("连不上："+e, true); });
  };

  var br = $("#btnVoiceReset");
  if(br) br.onclick = function(){
    ask("恢复默认语音参数", "回到小艺、+10%、+20Hz、40 字。确定？", function(){
      API("/voice_config",{method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({reset:true})}
      ).then(function(j){
        if(j && j.ok){ S.vc = j; S.vcDirty = false; say("已恢复默认"); render(); }
        else say("恢复失败", true);
      });
    });
  };

  var bt = $("#btnVoiceTry");
  function doTry(){
    var t = ($("#vQ")||{}).value || "";
    var box = $("#vResult");
    if(!t.trim()){ if(box) box.innerHTML = '<span class="fbad">先写一句话</span>'; return; }
    if(box) box.innerHTML = '<span class="fhint">合成中…（第一次要几秒）</span>';
    API("/voice_say",{method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({text:t})}
    ).then(function(j){
      if(!j || !j.ok){
        if(box) box.innerHTML = '<span class="fbad">'+esc((j&&(j.detail||j.error))||"失败")+'</span>';
        return;
      }
      var url = "/voice_file?n="+encodeURIComponent(j.name);
      if(box) box.innerHTML =
        '<audio controls autoplay src="'+url+'" style="width:100%;max-width:460px"></audio>'+
        '<div class="ctxtrace">'+esc(j.name)+' · '+j.dur+' 秒</div>';
    }).catch(function(e){
      if(box) box.innerHTML = '<span class="fbad">连不上：'+esc(e)+'</span>';
    });
  }
  var bt2 = $("#btnVoiceTry");
  if(bt2) bt2.onclick = doTry;
  var q = $("#vQ");
  if(q) q.onkeydown = function(e){ if(e.key==="Enter") doTry(); };

  document.querySelectorAll(".ctxrow input.ge2, .ctxrow input.gc").forEach(function(inp){
    inp.oninput = function(){ S.vcDirty = true; };
  });
}

/* ── 实时负载：CPU / 内存（0~100%）+ 磁盘读写（按峰值归一，虚线） ── */
function drawSys(){
  var hist=(S.sys&&S.sys.hist)||((S.data&&S.data.sys&&S.data.sys.hist)||[]);
  var now=(S.sys&&S.sys.now)||((S.data&&S.data.sys&&S.data.sys.now)||{});
  var cv=$("#cvSys");
  if(cv){
    var f=fitCanvas(cv, cvh(cv,CH(0.16,150,220))), g=f.g, W=f.w, H=f.h;
    var padL=34,padR=10,padT=10,padB=18, iw=W-padL-padR, ih=H-padT-padB;
    g.clearRect(0,0,W,H);
    g.strokeStyle="#1d2530"; g.lineWidth=1; g.font="10px Consolas,monospace";
    g.fillStyle="#4d5665";
    for(var k=0;k<=4;k++){
      var y=padT+ih*k/4;
      g.beginPath(); g.moveTo(padL,y); g.lineTo(W-padR,y); g.stroke();
      g.fillText(Math.round(100-k*25)+"%", 3, y+3);
    }
    if(hist.length<2){
      g.fillStyle="#4d5665"; g.textAlign="center";
      g.fillText("2 秒采一个点，等几秒就有线了", W/2, H/2);
      g.textAlign="left";
    } else {
      var n=hist.length;
      [["cpu","#57c8f0"],["mem","#9d8bff"]].forEach(function(s){
        g.beginPath();
        for(var i=0;i<n;i++){
          var x=padL+iw*i/(n-1);
          var y=padT+ih*(1-Math.max(0,Math.min(1,(hist[i][s[0]]||0)/100)));
          i?g.lineTo(x,y):g.moveTo(x,y);
        }
        g.strokeStyle=s[1]; g.lineWidth=1.8; g.lineJoin="round"; g.stroke();
      });
      var mx=1;
      for(var i2=0;i2<n;i2++) mx=Math.max(mx,(hist[i2].dr||0)+(hist[i2].dw||0));
      g.beginPath();
      for(var i3=0;i3<n;i3++){
        var x3=padL+iw*i3/(n-1);
        var y3=padT+ih*(1-((hist[i3].dr||0)+(hist[i3].dw||0))/mx);
        i3?g.lineTo(x3,y3):g.moveTo(x3,y3);
      }
      g.setLineDash([4,3]); g.strokeStyle="#e8b44a"; g.lineWidth=1.3; g.stroke();
      g.setLineDash([]);
      g.font="11px Consolas,monospace";
      g.fillStyle="#57c8f0"; g.fillText("CPU "+(now.cpu||0)+"%", padL, H-3);
      g.fillStyle="#9d8bff"; g.fillText("MEM "+(now.mem||0)+"%", padL+120, H-3);
      g.fillStyle="#e8b44a"; g.fillText("DISK "+fmtBps((now.dr||0)+(now.dw||0)), padL+250, H-3);
    }
  }
  var cv2=$("#cvIO");
  if(cv2){
    var f2=fitCanvas(cv2, cvh(cv2,CH(0.14,130,190))), g2=f2.g, W2=f2.w, H2=f2.h;
    g2.clearRect(0,0,W2,H2);
    var rows=[["磁盘读 DISK R", now.dr, "#57c8f0"],
              ["磁盘写 DISK W", now.dw, "#e8b44a"],
              ["网络收 NET IN", now.nd, "#2fd07d"],
              ["网络发 NET OUT", now.nu, "#ff5f6d"]];
    var mx2=1; rows.forEach(function(r){ mx2=Math.max(mx2, r[1]||0); });
    var top=8, rh=(H2-top-4)/rows.length;
    g2.font="11px Consolas,monospace";
    rows.forEach(function(r,i){
      var y=top+i*rh;
      g2.fillStyle="#5f7086"; g2.fillText(r[0], 0, y+10);
      var bw=(W2-92)*Math.max(0,Math.min(1,(r[1]||0)/mx2));
      g2.fillStyle="#0e131a"; g2.fillRect(0, y+15, W2-92, 7);
      g2.fillStyle=r[2]; g2.fillRect(0, y+15, Math.max(2,bw), 7);
      g2.fillStyle=r[2]; g2.fillText(fmtBps(r[1]), W2-86, y+22);
    });
  }
}
function pollSys(){
  return API("/syslive").then(function(j){
    if(!j||!j.ok) return;
    S.sys={now:j.now, hist:j.hist||[]};
    if(S.tab==="dash") drawSys();
  }).catch(function(){});
}
function render(){
  var g=$("#grid");
  /* 星云是自转动画，切走必须停掉，不然 requestAnimationFrame 一直空转烧 CPU */
  if(NEB.raf && S.tab!=="neb"){ cancelAnimationFrame(NEB.raf); NEB.raf=0; NEB.cv=null; }
  /* 状态总览改成"自然高度 + 可上下滑"（2026-11-01 用户要求）：
     参数越加越多，硬塞一屏会把内容压得看不清；宁可滚一下。 */
  g.className = "grid";
  /* 上下文页可能正有人在那儿改数字：这时候重绘会把输入框冲掉，
     人会以为自己没输进去。所以有未保存改动就先不重绘。 */
  if(S.tab==="ctx" && S.ctxDirty && !S.ctxForce) { return; }
  S.ctxForce = false;
  if(S.tab==="voice" && S.vcDirty) { return; }
  if(S.tab==="dash") g.innerHTML=viewDash();
  else if(S.tab==="mem") g.innerHTML=viewMem();
  else if(S.tab==="neb") g.innerHTML=viewNeb();
  else if(S.tab==="time") g.innerHTML=viewTime();
  else if(S.tab==="ctx") g.innerHTML=viewCtx();
  else if(S.tab==="emo") g.innerHTML=viewEmo();
  else if(S.tab==="talk") g.innerHTML=viewTalk();
  else if(S.tab==="voice") g.innerHTML=viewVoice();
  else g.innerHTML=viewSet();
  if(S.tab==="set") bindSet();
  if(S.tab==="ctx") bindCtx();
  if(S.tab==="talk") bindTalk();
  if(S.tab==="voice") bindVoice();
  if(S.tab==="dash") bindDiskButtons();
  draw();
  /* autoFill 关掉：现在状态总览是可滚动长页，再自动缩放会把卡片压扁 */
  if(S.tab==="dash"){ _afTries=0; CHSCALE=Math.max(0.9,Math.min(1.15,CHSCALE)); }
}

/* 一屏装下：量一下 #grid 还差多少高度，按比例把图表整体缩放重画。
   大屏往大放（底下不留空），小屏往小收（不用滚），最多迭代 4 次；
   卡片内边距和 14px 间隙不跟着缩，所以留 3% 容差，别抖。 */
var _afTries = 0;
function autoFill(){
  var g=$("#grid"); if(!g) return;
  var target=g.clientHeight, cur=g.scrollHeight;
  if(!target || !cur){ _afTries=0; return; }
  var ratio=target/cur;
  if(Math.abs(ratio-1) < 0.03 || _afTries>=4){ _afTries=0; return; }
  ratio=Math.max(0.72, Math.min(1.3, ratio));   /* 一次别走太远，免得来回摆 */
  CHSCALE=Math.max(0.5, Math.min(2.8, CHSCALE*ratio));
  _afTries++;
  render();
}

function draw(){
  var d=S.data||{};
  if(S.tab==="emo"){ drawEmo(); return; }
  if(S.tab==="dash"){
    var hlog=d.hlog||[], m=d.metrics||{};
    var cv=$("#cvH");
    if(cv) lineChart(cv, [
      {color:"#57c8f0", pts:hlog.map(function(x){return x.sero;})},
      {color:"#2fd07d", pts:hlog.map(function(x){return x.dopa;})},
      {color:"#ff5f6d", pts:hlog.map(function(x){return x.cort;})},
      {color:"#9d8bff", pts:hlog.map(function(x){return x.oxy;})}
    ], {height:cvh(cv,CH(0.17,130,225))});
    /* PAD 三条线：固定 −1..1 会把三条线压成一条扁线（实测波动只有 0.2），
       所以轴按实际波动自动缩放；跨零时那条虚线就是"平静"。 */
    var cvp=$("#cvPad");
    if(cvp){
      var pv=[];
      hlog.forEach(function(x){
        [x.p,x.a,x.d].forEach(function(v){ if(isFinite(v)) pv.push(Number(v)); });
      });
      if(pv.length){
        var lo=Math.min.apply(null,pv), hi=Math.max.apply(null,pv);
        var padm=(hi-lo)*0.12 || 0.05;
        lo-=padm; hi+=padm;
        if(hi-lo<0.24){ var mid=(hi+lo)/2; lo=mid-0.12; hi=mid+0.12; }
        lo=Math.max(-1,lo); hi=Math.min(1,hi);
        lineChart(cvp, [
          {color:"#e8b44a", fill:false, pts:hlog.map(function(x){return x.p;})},
          {color:"#ff5f6d", fill:false, pts:hlog.map(function(x){return x.a;})},
          {color:"#57c8f0", fill:false, pts:hlog.map(function(x){return x.d;})}
        ], {height:cvh(cvp,CH(0.17,130,225)), min:lo, max:hi, noFill:true});
      }
    }
    // 召回频率：24 小时柱
    var crec=$("#cvRec");
    if(crec){
      var rs=(m.recall_series||[]).map(function(v,i){ return {day:String(i).padStart(2,"0"),n:v}; });
      barChart(crec, rs, "#57c8f0", cvh(crec,CH(0.11,96,158)));
    }
    // 召回来源：环图
    var csrc=$("#cvSrc");
    if(csrc) donut(csrc, (m.recall_sources||[]), cvh(csrc,CH(0.11,100,160)), "EVENTS");
    // 权重分布：12 格柱
    var cwh=$("#cvWH");
    if(cwh){
      var hist=(m.weight_hist||[]);
      var hitems=hist.map(function(v,i){
        return {day:(i/12).toFixed(2), n:v};
      });
      barChart(cwh, hitems, "#2fd07d", cvh(cwh,CH(0.11,96,158)));
    }
    // 突触网络：按边种类
    var csyn=$("#cvSyn");
    if(csyn){
      var sk=(m.syn_kinds||[]).map(function(k){ return {k:k.k, r:k.k, n:k.n}; });
      donut(csyn, sk, cvh(csyn,CH(0.11,100,160)), "EDGES");
    }
    var cw=$("#cvW"); if(cw) barChart(cw, d.mem_by_day||[], "#9d8bff", cvh(cw,CH(0.11,96,158)));
    var cr=$("#cvR"); if(cr) donut(cr, d.regions||[], cvh(cr,CH(0.11,100,160)), "MEM");
    var rd=$("#cvRadar");
    if(rd && S.weights){
      radar(rd, ["身份","自我","短时","陈述","印象","技能"], S.weights, cvh(rd,CH(0.11,104,162)));
    } else if(rd){
      radar(rd, ["身份","自我","短时","陈述","印象","技能"], [0.03,0.03,0.03,0.03,0.03,0.03], cvh(rd,CH(0.11,104,162)));
    }
    // 时间线吞吐
    var ct=$("#cvTick");
    if(ct){
      var tis=(m.timeline_series||[]).map(function(v,i){ return {day:String(i).padStart(2,"0"),n:v}; });
      barChart(ct, tis, "#e8b44a", cvh(ct,CH(0.11,96,158)));
    }
    // 实时负载（2 秒一刷，见 pollSys）
    drawSys();
  } else if(S.tab==="mem"){
    var m=d.metrics||{};
    var cw=$("#cvMW");
    if(cw){
      var wh=(m.weight_hist||[]);
      var witems=wh.map(function(v,i){
        return {day:(i/12).toFixed(2), n:v}; });
      barChart(cw, witems, "#2fd07d", cvh(cw,CH(0.11,100,170)));
    }
    var cwr=$("#cvMWr");
    if(cwr){
      var wis=(d.mem_by_day||[]).map(function(it){
        var s=(it&&it.day)?String(it.day):"";
        return {day:s.slice(5), n:(it&&it.n!=null?it.n:0)}; });
      barChart(cwr, wis, "#9d8bff", cvh(cwr,CH(0.11,100,170)));
    }
    var cms=$("#cvMS");
    if(cms){
      donut(cms, (m.recall_sources||[]), cvh(cms,CH(0.13,110,180)),
        String(m.recall_24h||0));
    }
    var cfc=$("#cvFC");
    if(cfc) drawForget(cfc, (d.curves||{}).forget||{});
    var cmd=$("#cvMD");
    if(cmd) drawMediaCurve(cmd, (d.curves||{}).media||{});
    var go=$("#qGo");
    if(go && !go._bound){
      go._bound=1;
      go.onclick=function(){
        var q=($("#qIn").value||"").trim();
        if(!q) return;
        $("#qOut").innerHTML="查…";
        API("/recall",{method:"POST",headers:{"Content-Type":"application/json"},
          body:JSON.stringify({query:q, top_k:8, access:false})}).then(function(j){
          var hs=(j.hits||[]);
          if(!hs.length){ $("#qOut").innerHTML='<div class="empty">卡里没翻到</div>'; return; }
          $("#qOut").innerHTML = hs.map(function(h){
            return '<div class="l" style="display:grid;grid-template-columns:52px 60px 1fr;'+
              'gap:8px;padding:5px 0;border-bottom:1px dotted #1c232c">'+
              '<span class="sq">#'+h.id+'</span>'+
              '<span class="sq" style="color:'+(RC[h.region]||"#5b7fa6")+'">'+
              esc(RN[h.region]||h.region||"-")+'</span>'+
              '<span class="nt" title="'+esc(h.text)+'">'+
              (h.score!=null? h.score.toFixed(3)+" · ":"")+esc(h.text)+'</span></div>';
          }).join("");
        }).catch(function(e){ $("#qOut").textContent="查失败："+e; });
      };
    }
  } else if(S.tab==="neb"){
    var nb=$("#neb");
    if(nb){
      if(!S.neb){ nb.innerHTML='<div class="empty">算星云…</div>';
        API("/graph").then(function(j){ S.neb=j; render(); }); return; }
      nebula(nb, S.neb);
      nebInfo(nb, nb._st && nb._st.sel);
    }
    var nk=$("#cvNK");
    if(nk){
      donut(nk, ((d.metrics||{}).syn_kinds||[]),
        cvh(nk,CH(0.13,110,180)),
        String((((d.metrics||{}).synapse||{}).edges)||0));
    }
  } else if(S.tab==="time"){
    var m2=d.metrics||{};
    var ctt=$("#cvTT");
    if(ctt){
      var tis2=(m2.timeline_series||[]).map(function(v,i){
        return {day:String(i).padStart(2,"0"), n:v}; });
      barChart(ctt, tis2, "#e8b44a", cvh(ctt,CH(0.16,120,200)));
    }
    var ctk=$("#cvTK");
    if(ctk){
      var t2=(d.timeline_tail||[]), km={};
      t2.forEach(function(x){ km[x.kind]=(km[x.kind]||0)+1; });
      var ki=Object.keys(km).map(function(k){ return {k:k, n:km[k]}; });
      donut(ctk, ki, cvh(ctk,CH(0.16,120,200)), String(t2.length));
    }
  }
}

/* ── 刷新 ── */
function refresh(){
  return Promise.all([
    API("/health"),
    API("/status"),
    API("/console_data?hours="+encodeURIComponent(S.hours)),
    API("/cardslot"),
    API("/timeline"),
    API("/disks"),
    /* 上下文参数面（七张模式表 + 三个闸门 + 默认值/范围）。
       放这儿一起拉：上下文页要靠它渲染，而且它是"卡里现在怎么配的"的真值。 */
    API("/config"),
    /* 语音参数（音色/语速/音高/上限字数）。它不在卡里，是 qq_state 里的
       一个 JSON —— 因为发送前要算"该不该发语音"时卡可能正被锁着。 */
    API("/voice_config")
  ]).then(function(a){
    var h=a[0], st=a[1], cd=a[2], cs=a[3], tl=a[4], dk=a[5], cfg=a[6], vc=a[7];
    S.alive = !!(h && h.ok);
    S.health = h || {};
    S.slot = (cs && cs.cardslot && cs.cardslot.state) || "unknown";
    S.data = cd && cd.ok ? cd : (S.data||{});
    /* 上下文参数面：只在没人在编辑的时候覆盖，免得把人家正改的数字冲掉 */
    if(!S.ctxDirty && cfg && cfg.ok) S.cfg = cfg;
    if(!S.vcDirty && vc && vc.ok) S.vc = vc;
    /* 盘子/备份：单独一条接口，别塞进 /console_data（那是纯 DB 读数） */
    S.data.disks = (dk && dk.ok) ? dk : (S.data.disks || {});
    S.data.timeline = (st && st.status && st.status.timeline) || {};
    S.up = (st && st.status && st.status.uptime_s) || 0;
    S.mood = (st && st.status && st.status.mood) || "";
    /* PAD 从 /status 来（公式只在 card2.mood_pad 一处，这里不许再算） */
    S.data.status = (st && st.status) || {}
    S.timeline = S.data.timeline;
    var now=new Date();
    S.stamp = "刷新于 "+now.toLocaleTimeString("zh-CN");
    // 时间线尾部直接在刷新里一起拉 —— 以前只在页面初始化时拉一次，
    // 首次失败（比如服务刚起来还没就绪）就永远空着，面板一直是“时间线（最近）”一片白。
    S.data.timeline_tail = ((tl && tl.timeline) || []).map(function(x){
      return {seq:x.seq,ts:x.ts,kind:x.kind,note:x.note}; });
    if(tl && tl.files){
      S.data.health = S.data.health || {};
      S.data.health.snap_n = tl.files.snap_n;
      S.data.health.db_mb = tl.files.db_mb;
      S.data.health.clean = tl.files.clean;
    }
    if(!S.data.timeline_tail.length){ S.data.timeline_tail=[]; }
    // 权重是 /console_data 里现算的，不在 /status 里
    if(S.data.weights){
      S.weights = S.data.weights;
      S.wsum = S.weights.reduce(function(a,b){return a+b;},0);
    }
    renderSide();
    if(S.tab==="dash"||S.tab==="mem") render(); else renderSide();
  }).catch(function(e){
    S.alive=false; S.stamp="刷新失败："+e; renderSide();
  });
}

/* 时间线尾部单独拉一次（轻） */
function loadTail(){
  return API("/timeline").then(function(j){
    S.data.timeline_tail=(j.timeline||[]).map(function(x,i){
      return {seq:x.seq,ts:x.ts,kind:x.kind,note:x.note}; });
    S.data.health = S.data.health || (j.files||{});
    if(j.files){ S.data.health.snap_n=j.files.snap_n;
      S.data.health.db_mb=j.files.db_mb; S.data.health.clean=j.files.clean; }
  }).catch(function(){});
}

/* ── 交互 ── */
/* 全屏按钮：浏览器全屏 API 需要用户手势，所以做成按钮而不是自动触发 */
$("#btnFull").onclick=function(){
  if(document.fullscreenElement){ document.exitFullscreen(); return; }
  var el=document.documentElement;
  (el.requestFullscreen||el.webkitRequestFullscreen||function(){}).call(el);
};
document.querySelectorAll("nav .tab").forEach(function(t){
  t.onclick=function(){
    /* 上下文页改到一半就跑掉 = 那些改动会丢。先问一句，
       比"改了没生效、不知道是没保存还是没生效"强。 */
    if(S.tab==="ctx" && S.ctxDirty && t.dataset.t!=="ctx"){
      if(!confirm("上下文参数还有没保存的改动，离开会丢掉。继续？")) return;
      S.ctxDirty = false;
    }
    document.querySelectorAll("nav .tab").forEach(function(x){
      x.classList.remove("on"); }); t.classList.add("on");
    S.tab=t.dataset.t;
    if(S.tab==="neb" && !S.neb){ S.neb=null; }
    render();
  };
});
$("#btnAvatar").onclick=function(){ pickAvatar(); };
$("#btnName").onclick=function(){
  var id=(S.data&&S.data.identity)||{};
  var v=prompt("这张卡叫什么名字？", id.name||"小鲸鱼");
  if(v===null) return;
  v=(v||"").trim();
  if(!v){ alert("名字不能为空"); return; }
  API("/identity",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({name:v})}).then(function(j){
    if(j&&j.ok) refresh(); else alert((j&&(j.detail||j.error))||"失败");
  });
};
$("#btnExit").onclick=function(){
  ask("退出实例",
    "退出 = 拔卡。会把 AGENTS.md 按字节还原成原来那份，并停掉心智服务。"
    +"卡里的东西一条都不会少。确定？", function(){
    API("/cardslot",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({action:"unload"})}).then(function(){
      return API("/shutdown",{method:"POST",headers:{"Content-Type":"application/json"},
        body:"{}"});
    }).then(function(){ document.body.innerHTML=
      '<div style="padding:40px;color:#7c8697;font:14px sans-serif">'+
      '已退出实例。可以关窗口了。</div>'; });
  });
};
var _askYes=null;
function ask(title, body, fn){
  $("#dlgT").textContent=title; $("#dlgB").textContent=body;
  $("#dlgM").textContent=""; _askYes=fn; $("#mask").classList.add("on");
}
$("#dlgNo").onclick=function(){ $("#mask").classList.remove("on"); };
$("#dlgYes").onclick=function(){ $("#mask").classList.remove("on");
  if(_askYes) _askYes(); };

/* ── 起 ── */
refresh(); loadTail().then(render);
/* 时间窗：一根旋钮管所有曲线。窗口越短，曲线越"活着"。 */
function setWindow(h){
  S.hours = Number(h) || 24;
  S.live = (S.hours <= 0.25);
  [].forEach.call(document.querySelectorAll(".wseg[data-h]"), function(b){
    b.classList.toggle("on", Number(b.dataset.h) === S.hours);
  });
  var lv = $("#btnLive"); if(lv) lv.classList.toggle("on", S.live);
  refresh();
}
[].forEach.call(document.querySelectorAll(".wseg[data-h]"), function(b){
  b.addEventListener("click", function(){ setWindow(b.dataset.h); });
});
var lvBtn = $("#btnLive");
if(lvBtn) lvBtn.addEventListener("click", function(){ setWindow(S.live ? 24 : 0.083); });
/* 实时档：每 3 秒自己刷一格，曲线就真的在动 */
setInterval(function(){ if(S.live) refresh(); }, 3000);
setInterval(function(){ refresh(); }, 15000);
setInterval(pollSys, 2000); pollSys();
setInterval(function(){ loadTail(); }, 30000);
window.addEventListener("resize", function(){ draw(); });
</script></body></html>
"""
PAGE = PAGE.replace("__HORMONE_LABELS__", '{"dopa": ["多巴胺", "Dopamine"], "nora": ["去甲肾上腺素", "Norepinephrine"], "cort": ["皮质醇", "Cortisol"], "sero": ["血清素", "Serotonin"], "oxy": ["催产素", "Oxytocin"], "endo": ["内啡肽", "Endorphin"], "gonad": ["促性腺激素", "Gonadotropin"], "mel": ["褪黑素", "Melatonin"], "gaba": ["γ-氨基丁酸", "GABA"], "ach": ["乙酰胆碱", "Acetylcholine"], "adh": ["抗利尿激素", "Vasopressin"], "dop": ["多巴", "L-DOPA"], "cck": ["胆囊收缩素", "Cholecystokinin"], "gh": ["生长激素", "Growth hormone"], "prl": ["催乳素", "Prolactin"], "hist": ["组胺", "Histamine"], "adeno": ["腺苷", "Adenosine"], "kyn": ["犬尿氨酸", "Kynurenine"], "npy": ["神经肽Y", "Neuropeptide Y"], "nps": ["神经肽S", "Neuropeptide S"], "ofq": ["孤啡肽", "Nociceptin"], "enk": ["脑啡肽", "Enkephalin"], "orex": ["食欲素", "Orexin"], "testo": ["睾酮", "Testosterone"]}')
PAGE = PAGE.replace("__HORMONE_BASE__", '{"dopa": 20.0, "nora": 15.0, "cort": 18.0, "sero": 40.0, "oxy": 30.0, "endo": 25.0, "gonad": 10.0, "mel": 22.0, "gaba": 35.0, "ach": 28.0, "adh": 24.0, "dop": 20.0, "cck": 16.0, "gh": 14.0, "prl": 26.0, "hist": 12.0, "adeno": 20.0, "kyn": 15.0, "npy": 32.0, "nps": 18.0, "ofq": 13.0, "enk": 17.0, "orex": 21.0, "testo": 11.0}')



def main(argv=None):
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "data":
        print(json.dumps(overview(DEFAULT_DB), ensure_ascii=False, indent=1))
        return 0
    if argv and argv[0] == "page":
        print(PAGE)
        return 0
    print("用法: python console.py data | page")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())


