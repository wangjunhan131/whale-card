# -*- coding: utf-8 -*-
"""从 card2 搬出来的模块：diag。

拆门面第 6 批（6-0 步）。这里的东西都不碰数据库、不读 self。"""


from cardtime import (time)
from body import (time)
import time
import sys

_SOFT_FAIL = {}

def soft_fail(kind, e, note=""):
    """记一次「静默失败」。kind 要短且稳定（会出现在 /health 里）。"""
    k = str(kind or "?")
    rec = _SOFT_FAIL.get(k)
    if rec is None:
        rec = {"n": 0, "first": time.time(), "last": 0.0, "err": "", "note": ""}
        _SOFT_FAIL[k] = rec
    rec["n"] += 1
    rec["last"] = time.time()
    rec["err"] = str(e)[:200]
    if note:
        rec["note"] = str(note)[:120]
    if rec["n"] == 1:
        # 只打第一次：同一种错反复刷屏会把真正的线索埋掉
        print("[card2] 静默失败 %s：%s" % (k, str(e)[:200]), file=sys.stderr)
    return rec["n"]

def soft_fail_snapshot():
    """给 /health 看：{kind: {n, last_h, err}}"""
    now = time.time()
    out = {}
    for k, v in _SOFT_FAIL.items():
        out[k] = {"n": v.get("n", 0),
                  "last_h": round((now - float(v.get("last") or now)) / 3600.0, 2),
                  "err": v.get("err", ""), "note": v.get("note", "")}
    return out
