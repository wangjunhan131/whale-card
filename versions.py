# -*- coding: utf-8 -*-
"""版本矩阵（V7 第 18 件）。

为什么需要它：桥插件装过三次才生效、`mcp` 升到 2.x 把 `FastMCP` 改名弄崩过一次、
schema 改过一版（event_ts/react_key/archive）却没有「这张卡是哪一版」的记录。
出问题时第一句话永远是「你哪个版本」—— 这个模块就是那个答案。

设计：
  · **只读**，不改任何东西（除了 card2 打开时会写一次 PRAGMA user_version）；
  · 缺什么就报 `None` + `notes`，不抛异常 —— 版本查询挂掉会让 /health 也挂；
  · 矩阵里既有「代码版本」也有「数据 schema 版本」，两者不一致时给 `mismatch`。
"""
import os
import platform
import sqlite3
import sys

# 数据 schema 版本：卡里的表结构版本，跟着迁移一起加。
#   1 = 2026-11-10（event_ts/event_precision、react_key、mem_archive、worklog、
#       recall_trace、mem_birth 补齐、checkpoint 表在卡外不看）
# 加新迁移时：+1，并在 card2._init_schema 里写清「升到这一版要做什么」。
SCHEMA_VERSION = 1

MATRIX_DOC = "docs/版本矩阵-V7.md"


def _pkg_version(path):
    """读 package.json 的 version（桥插件）。读不到给 None。"""
    try:
        import json
        with open(path, encoding="utf-8") as f:
            return (json.load(f) or {}).get("version")
    except Exception:
        return None


def _bridge_plugin():
    """找 DSH profile 里的桥插件版本。

    路径有两处：本仓库 deploy/dsh-whale-bridge（源码）与
    ~/.dsh/profiles/desktop/node_modules/dsh-whale-bridge（生效的那份）。
    生效的那份更重要 —— 源码新、profile 旧是真实发生过的事。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    cands = [
        ("profile", os.path.join(os.path.expanduser("~"), ".dsh", "profiles",
                                 "desktop", "node_modules", "dsh-whale-bridge",
                                 "package.json")),
        ("source", os.path.join(here, "deploy", "dsh-whale-bridge",
                                "package.json")),
    ]
    out = {}
    for kind, p in cands:
        v = _pkg_version(p)
        if v or os.path.exists(p):
            out[kind] = {"version": v, "path": p,
                         "exists": os.path.exists(p)}
    return out


def _db_schema(db_path):
    """卡里的 user_version（只读打开，不改库）。"""
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db_path.replace("\\", "/"),
                              uri=True, timeout=5)
        try:
            v = con.execute("PRAGMA user_version").fetchone()[0]
        finally:
            con.close()
        return int(v or 0)
    except Exception as e:
        return {"error": str(e)[:120]}


def collect(db_path=None, mind_version=None):
    """收集版本矩阵。任何一项拿不到都写 None，不抛。"""
    notes = []
    python_v = "%d.%d.%d" % sys.version_info[:3]
    sqlite_v = sqlite3.sqlite_version
    code_v = mind_version
    db_v = _db_schema(db_path) if db_path else None

    mismatch = []
    if isinstance(db_v, int) and code_v is not None:
        if db_v > SCHEMA_VERSION:
            mismatch.append("卡比代码新（db user_version=%d > 代码 %d）："
                            "这张卡是更新版本的代码写的，用旧代码跑会读不懂新列"
                            % (db_v, SCHEMA_VERSION))
        elif db_v < SCHEMA_VERSION:
            mismatch.append("卡比代码旧（db user_version=%d < 代码 %d）："
                            "下次启动会自动补列，补之前不要双开"
                            % (db_v, SCHEMA_VERSION))

    sem = None
    try:
        import semantic
        model = None
        for name in ("current_model_dir",):
            fn = getattr(semantic, name, None)
            if callable(fn):
                try:
                    model = os.path.basename(str(fn()))
                except Exception:
                    model = None
        if not model:
            model = os.path.basename(str(getattr(semantic, "MODEL_DIR_NAME", "") or "")) or None
        dim = getattr(semantic, "DIM", None)
        try:
            cur = getattr(semantic, "current_dim", None)
            if callable(cur):
                dim = cur()
        except Exception:
            pass
        sem = {"model": model, "dim": dim}
    except Exception as e:
        sem = {"error": str(e)[:120]}
        notes.append("语义层拿不到版本：%s" % str(e)[:80])

    try:
        import mcp  # 桥走 HTTP，不依赖 mcp；这里只报它装没装
        mcp_v = getattr(mcp, "__version__", "unknown")
    except Exception:
        mcp_v = None

    return {
        "mind": code_v,
        "schema": {"code": SCHEMA_VERSION, "db": db_v},
        "bridge": _bridge_plugin(),
        "python": python_v,
        "sqlite": sqlite_v,
        "mcp": mcp_v,
        "semantic": sem,
        "platform": platform.platform(),
        "db_path": db_path,
        "mismatch": mismatch,
        "notes": notes,
        "doc": MATRIX_DOC,
    }
