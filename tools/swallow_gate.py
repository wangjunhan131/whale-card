# -*- coding: utf-8 -*-
"""吞异常台账守门（V7 第 6 件）。

背景：docs/吞异常台账-V7.md 里那些 `except ...: pass` 是**故意**留的 ——
失败不该拖垮服务。怕的是后来有人顺手又加一个空吞，没人登记、也没人知道。

做法：AST 扫一遍，把每一处「except 体只有 pass / continue / ...」记账；
指纹只锁 文件 + 所在函数/类 + 异常类型 + 体内容 + 同作用域内第几次出现
（不含行号，重构挪行不会误报）。基线在 docs/吞异常台账-V7.json。

用法：
    python tools/swallow_gate.py                 # 查：有新空吞就 FAIL
    python tools/swallow_gate.py --json          # 机器可读
    python tools/swallow_gate.py --write-baseline  # 人工确认后重新登记
退出码：0 没有新增；1 有新增（或基线缺失）；2 扫描本身失败。
"""
import argparse
import ast
import hashlib
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BASELINE = os.path.join(ROOT, "docs", "吞异常台账-V7.json")
SCAN_DIRS = [ROOT, os.path.join(ROOT, "tools")]
SKIP_NAMES = {"__pycache__", "probe", "dev", "tests", "test", "deploy",
              "dsh-plugin", ".git", ".venv", "venv", "node_modules",
              "site-packages"}


def _rel(p):
    return os.path.relpath(p, ROOT).replace("\\", "/")


def _is_empty_body(body):
    if not body:
        return False
    for n in body:
        if isinstance(n, (ast.Pass, ast.Continue)):
            continue
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) \
                and n.value.value is Ellipsis:
            continue
        return False
    kinds = []
    for n in body:
        if isinstance(n, ast.Pass):
            kinds.append("pass")
        elif isinstance(n, ast.Continue):
            kinds.append("continue")
        else:
            kinds.append("...")
    return "+".join(kinds)


def scan_file(path):
    src = io.open(path, encoding="utf-8", errors="replace").read()
    tree = ast.parse(src, filename=path)
    out = []
    stack = []
    seen = {}

    class V(ast.NodeVisitor):
        def visit_ClassDef(self, node):
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        def visit_FunctionDef(self, node):
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        def visit_AsyncFunctionDef(self, node):
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        def visit_Try(self, node):
            for h in node.handlers:
                body = _is_empty_body(h.body)
                if not body:
                    continue
                exc = ast.unparse(h.type) if h.type is not None else ""
                scope = ".".join(stack) or "<module>"
                sig = (scope, exc, body)
                nth = seen.get(sig, 0) + 1
                seen[sig] = nth
                raw = "%s|%s|%s|%s|%d" % (_rel(path), scope, exc, body, nth)
                out.append({
                    "fp": hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16],
                    "file": _rel(path), "func": scope, "exc": exc,
                    "body": body, "nth": nth, "line": h.lineno,
                })
            self.generic_visit(node)

    V().visit(tree)
    return out


def scan():
    entries = []
    files = []
    for d in SCAN_DIRS:
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".py"):
                continue
            p = os.path.join(d, name)
            if not os.path.isfile(p):
                continue
            files.append(_rel(p))
            entries.extend(scan_file(p))
    entries.sort(key=lambda e: (e["file"], e["line"], e["fp"]))
    counts = {}
    for e in entries:
        counts[e["file"]] = counts.get(e["file"], 0) + 1
    return {"scanned_files": len(files), "current": len(entries),
            "counts": counts, "entries": entries}


def load_baseline():
    try:
        with io.open(BASELINE, encoding="utf-8") as f:
            d = json.load(f)
        return d
    except FileNotFoundError:
        return None
    except Exception as e:
        return {"_error": "%s: %s" % (type(e).__name__, e)}


def check():
    cur = scan()
    base = load_baseline()
    if base is None:
        return {"ok": False, "reason": "基线不存在：%s（先人工确认再 --write-baseline）"
                % _rel(BASELINE), "current": cur["current"],
                "counts": cur["counts"], "new": [], "resolved": []}
    if base.get("_error"):
        return {"ok": False, "reason": "基线读不了：%s" % base["_error"],
                "current": cur["current"], "counts": cur["counts"],
                "new": [], "resolved": []}
    old = {e["fp"]: e for e in base.get("entries") or []}
    new = [e for e in cur["entries"] if e["fp"] not in old]
    now = {e["fp"] for e in cur["entries"]}
    resolved = [e for e in base.get("entries") or [] if e["fp"] not in now]
    return {"ok": not new, "reason": "",
            "baseline": base.get("count"), "current": cur["current"],
            "counts": cur["counts"], "new": new, "resolved": resolved}


def write_baseline():
    cur = scan()
    os.makedirs(os.path.dirname(BASELINE), exist_ok=True)
    payload = {"version": 1, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
               "count": cur["current"], "scanned_files": cur["scanned_files"],
               "counts": cur["counts"], "entries": cur["entries"]}
    tmp = BASELINE + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    os.replace(tmp, BASELINE)
    return payload


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write-baseline", action="store_true")
    a = ap.parse_args()
    try:
        if a.write_baseline:
            p = write_baseline()
            print("已登记 %d 处 -> %s" % (p["count"], _rel(BASELINE)))
            return 0
        r = check()
        if a.json:
            print(json.dumps(r, ensure_ascii=False, indent=1))
        else:
            print("空吞：当前 %s 处（基线 %s），新增 %d 处"
                  % (r["current"], r.get("baseline"), len(r["new"])))
            for e in r["new"]:
                print("  NEW  %s:%d  %s  except %s: %s"
                      % (e["file"], e["line"], e["func"], e["exc"] or "?",
                         e["body"]))
            for e in r["resolved"][:5]:
                print("  GONE %s  %s  except %s"
                      % (e["file"], e["func"], e["exc"] or "?"))
            if r.get("reason"):
                print("  " + r["reason"])
            print("RESULT", "PASS" if r["ok"] else "FAIL")
        return 0 if r["ok"] else 1
    except Exception as e:
        print("扫描失败：%s: %s" % (type(e).__name__, e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
