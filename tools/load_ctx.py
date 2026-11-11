#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""并发压测：读写在同时发生时的延迟分布（读写分离验收用）

用法（默认打本机 8899 上的副本实例，别打生产 8848）：
    python -X utf8 tools/load_ctx.py --port 8899 --readers 3 --reads 4 --writers 5 --writes 3

为什么要有：2026-11-11 那份评审的基线是「5 组写×3 + 3 组读×4 并发下
/context 中位 6881ms」。单流量的数字好看不代表并发好看——本工具就是复现那一轮，
给出 p50/p95/max，免得只看平均值自我感觉良好。
"""
import argparse
import json
import statistics
import threading
import time
import urllib.request


def _post(port, path, payload, token, timeout=180):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=body,
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("X-Whale-Token", token)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _get(port, path, token, timeout=60):
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path))
    if token:
        req.add_header("X-Whale-Token", token)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _pct(xs, q):
    xs = sorted(xs)
    i = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
    return round(xs[i], 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--token", default="")
    ap.add_argument("--readers", type=int, default=3)
    ap.add_argument("--reads", type=int, default=4)
    ap.add_argument("--writers", type=int, default=5)
    ap.add_argument("--writes", type=int, default=3)
    o = ap.parse_args()

    lat, errs = [], []
    lock = threading.Lock()
    stop = threading.Event()

    READS = ["今天有点累", "你还记得画展吗", "晚上吃什么好", "明天几点开会",
             "我最近在学做饭", "周末有空吗", "我是不是说过要早点睡", "你还记得我答应过什么",
             "学校里今天挺热的", "说说话吧"]
    WRITES = ["压测写入一：今天在食堂吃了一碗面", "压测写入二：放学路上看见一只橘猫",
              "压测写入三：数学老师又拖堂了", "压测写入四：晚上想早点睡",
              "压测写入五：周末打算去画展"]

    def reader(k):
        for i in range(o.reads):
            msg = READS[(k * o.reads + i) % len(READS)]
            t = time.time()
            try:
                _post(o.port, "/context", {"message": msg}, o.token)
                dt = (time.time() - t) * 1000
                with lock:
                    lat.append(dt)
            except Exception as e:      # noqa: BLE001
                with lock:
                    errs.append("%s: %s" % (type(e).__name__, e))

    def writer(k):
        for i in range(o.writes):
            txt = "%s（第%d次·%d）" % (WRITES[(k * o.writes + i) % len(WRITES)], i, k)
            try:
                _post(o.port, "/report",
                      {"user_message": txt,
                       "reply": "嗯，我记着了。",
                       "source": "load"},
                      o.token)
            except Exception as e:      # noqa: BLE001
                with lock:
                    errs.append("%s: %s" % (type(e).__name__, e))

    ths = [threading.Thread(target=reader, args=(k,)) for k in range(o.readers)]
    ths += [threading.Thread(target=writer, args=(k,)) for k in range(o.writers)]
    t0 = time.time()
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    wall = (time.time() - t0) * 1000

    print("并发：%d 读×%d + %d 写×%d，墙钟 %.0f ms"
          % (o.readers, o.reads, o.writers, o.writes, wall))
    if lat:
        print("/context n=%d p50=%.0fms p95=%.0fms max=%.0fms mean=%.0fms"
              % (len(lat), _pct(lat, .5), _pct(lat, .95), max(lat),
                 statistics.mean(lat)))
    else:
        print("/context 没有成功样本")
    if errs:
        print("错误 %d 条，前 3：%s" % (len(errs), errs[:3]))
    try:
        r = _get(o.port, "/perf", o.token)
        g = (r.get("perf") or {}).get("groups", {}).get("context") or {}
        if g:
            print("服务端 /perf context：n=%s total p50=%s p95=%s lock_wait p50=%s p95=%s"
                  % (g.get("n"), g["total_ms"]["p50"], g["total_ms"]["p95"],
                     g["lock_wait_ms"]["p50"], g["lock_wait_ms"]["p95"]))
    except Exception as e:              # noqa: BLE001
        print("读 /perf 失败：%s" % e)


if __name__ == "__main__":
    main()
