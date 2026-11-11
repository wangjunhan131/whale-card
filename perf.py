# -*- coding: utf-8 -*-
"""分段耗时（读写分离第一步：只观测，不改行为）

为什么要有它：2026-11-11 那份《读写分离专项评审》说得很直白——/context 慢
不是因为 SQLite，而是应用层一把 mind.lock 把读写串成了队，写路径还把
embedding / consolidate 这些重活塞在锁里。要动它，先得看得见
「等锁多久、锁里干了什么」，否则改完不知道好在哪、坏在哪。

这个模块不做业务决定：
  - begin/finish 只圈一次请求的边界；
  - phase() 只记某个函数花了多久；
  - TimedLock 只是把现成的锁包一层，语义与原锁一致（acquire/release/locked）；
  - 数据只留在内存环形缓冲（200 条），不落库、不写记忆正文。
"""

import collections
import contextlib
import threading
import time

_RING = collections.deque(maxlen=200)
_L = threading.local()


def begin(path):
    _L.req = {"path": path, "t0": time.perf_counter(), "phases": {},
              "lock_wait_ms": 0.0, "lock_hold_ms": 0.0, "lock_n": 0}


def cur():
    return getattr(_L, "req", None)


@contextlib.contextmanager
def phase(name):
    d = cur()
    if d is None:          # 维护线程/启动期没有请求上下文，静默跳过
        yield
        return
    t = time.perf_counter()
    try:
        yield
    finally:
        dt = (time.perf_counter() - t) * 1000.0
        ph = d["phases"]
        ph[name] = ph.get(name, 0.0) + dt


class TimedLock(object):
    """包一层原生锁：只记等待/持有时长，锁语义完全不变。

    另外记「这次等的是谁」：并发下 /context 一半时间在等锁，
    但不知道是谁在里面磨蹭就改不动 —— 记持有者线程名，够用来定位。
    """

    def __init__(self, lock):
        self._lock = lock
        self._held_by = None
        self._held_since = None

    def acquire(self, blocking=True, timeout=-1):
        d = cur()
        t = time.perf_counter()
        if timeout is None or timeout < 0:
            ok = self._lock.acquire(blocking)
        else:
            ok = self._lock.acquire(blocking, timeout)
        if d is not None:
            dt = (time.perf_counter() - t) * 1000.0
            d["lock_wait_ms"] += dt
            if ok:
                d["lock_n"] += 1
                d["_hold_t"] = time.perf_counter()
            elif dt >= 200.0:
                who = self._held_by or "?"
                d.setdefault("waited_for", []).append("%s(%.0fms)" % (who, dt))
        if ok:
            self._held_by = threading.current_thread().name
            self._held_since = time.perf_counter()
        return ok

    def release(self):
        d = cur()
        if d is not None and d.get("_hold_t"):
            d["lock_hold_ms"] += (
                time.perf_counter() - d.pop("_hold_t")) * 1000.0
        self._held_by = None
        self._held_since = None
        self._lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *a):
        self.release()
        return False

    def locked(self):
        return self._lock.locked()


def finish():
    d = cur()
    if d is None:
        return None
    _L.req = None
    d["total_ms"] = (time.perf_counter() - d["t0"]) * 1000.0
    _RING.append(d)
    return d


def _pct(seq, q):
    if not seq:
        return None
    s = sorted(seq)
    i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return round(s[i], 1)


def _group(path):
    if path in ("/context", "/ctx_preview"):
        return "context"
    if path == "/report":
        return "report"
    if path in ("/recall", "/recent"):
        return "recall"
    return "other"


def stats():
    """按分组给 p50/p95/max：总耗时、等锁、持锁、各阶段。样本不够就 n 小，不装。"""
    by = collections.defaultdict(list)
    for r in list(_RING):
        by[_group(r["path"])].append(r)
    out = {"n": len(_RING),
           "note": "只记元数据（毫秒），不含任何记忆正文", "groups": {}}
    for g, rows in by.items():
        tot = [r["total_ms"] for r in rows]
        wait = [r["lock_wait_ms"] for r in rows]
        hold = [r["lock_hold_ms"] for r in rows]
        ph = collections.defaultdict(list)
        for r in rows:
            for k, v in r["phases"].items():
                ph[k].append(v)
        out["groups"][g] = {
            "n": len(rows),
            "total_ms": {"p50": _pct(tot, .5), "p95": _pct(tot, .95),
                         "max": round(max(tot), 1)},
            "lock_wait_ms": {"p50": _pct(wait, .5), "p95": _pct(wait, .95),
                             "max": round(max(wait), 1)},
            "lock_hold_ms": {"p50": _pct(hold, .5), "p95": _pct(hold, .95),
                             "max": round(max(hold), 1)},
            "phases_ms": {k: {"p50": _pct(v, .5), "p95": _pct(v, .95)}
                          for k, v in ph.items()},
        }
        # 等锁时「里面那个人是谁」：定位是谁把读堵住了
        wf = collections.Counter()
        for r in rows:
            for w in r.get("waited_for", []):
                wf[w.split("(")[0]] += 1
        out["groups"][g]["waited_for"] = wf.most_common(5)
    return out


def one(d):
    """慢日志用：把一条记录压成一行。"""
    ph = " ".join("%s=%.0fms" % (k, v) for k, v in sorted(d["phases"].items()))
    return ("rid=%s total=%.0fms lock_wait=%.0fms lock_hold=%.0fms %s"
            % (d.get("rid") or "-", d["total_ms"], d["lock_wait_ms"],
               d["lock_hold_ms"], ph))


def install(mind):
    """把 mind 身上的锁与两个重活函数包一层。装不上也不抛，服务照跑。"""
    lock = getattr(mind, "lock", None)
    if lock is not None and not isinstance(lock, TimedLock):
        mind.lock = TimedLock(lock)

    sem = getattr(mind, "semantic", None)
    if sem is not None and not getattr(sem, "_perf_wrapped", False):
        orig_enc = sem.encode

        def _enc(*a, **kw):
            with phase("embed"):
                return orig_enc(*a, **kw)

        sem.encode = _enc
        sem._perf_wrapped = True

    cons = getattr(mind, "_consolidate", None)
    if cons is not None and not getattr(mind, "_perf_cons", False):
        def _cons(*a, **kw):
            with phase("consolidate"):
                return cons(*a, **kw)
        mind._consolidate = _cons
        mind._perf_cons = True

    return True


def _enc(orig, a, kw):
    with phase("embed"):
        return orig(*a, **kw)
