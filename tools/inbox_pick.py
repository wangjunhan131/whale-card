#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""收件层 → 短期池：除杂去噪那一遍（手动入口）

用户 2026-11-08 定的分工，一句话：
    收件箱把所有原文装进去 → 除杂去噪 → 推短期池

所以这个脚本只干"去噪"这一段：纯符号、一两个字、哈哈哈、和最近重复的、
别家 AI 的话 —— 这些留下等收件层自己过期；其余整条原文推进短期池。

  python -X utf8 tools/inbox_pick.py            # 只看会挑哪些（不写卡，默认）
  python -X utf8 tools/inbox_pick.py --commit   # 真推
  python -X utf8 tools/inbox_pick.py --limit 40 --min 0.30
"""
import argparse
import json
import os
import sys
import urllib.request

MIND = os.environ.get("WHALE_MIND", "http://127.0.0.1:8848")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--hours", type=float, default=None)
    ap.add_argument("--min", dest="min_score", type=float, default=None)
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args(argv)

    body = {"limit": a.limit, "commit": bool(a.commit)}
    if a.hours:
        body["hours"] = a.hours
    if a.min_score is not None:
        body["min_score"] = a.min_score
    req = urllib.request.Request(
        MIND + "/inbox/pick", data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read().decode("utf-8"))

    if d.get("committed"):
        print("推进短期池 %d 条（阈值 %.2f）" % (d.get("n_wrote", 0),
                                            d.get("threshold", 0)))
        for w in d.get("wrote") or []:
            print("  inbox #%s → mem #%s  score %.2f" % (w.get("id"),
                                                         w.get("mid"),
                                                         w.get("score")))
        return 0

    print("阈值 %.2f：会挑 %d 条，留 %d 条在收件层等过期"
          % (d.get("threshold", 0), d.get("n_picked", 0), d.get("n_dropped", 0)))
    print("—— 会挑 ——")
    for x in d.get("picked") or []:
        print("  [%.2f] %s | %s" % (x.get("score"), x.get("why"),
                                    (x.get("text") or "")[:50].replace("\n", " ")))
    print("—— 当噪声留下 ——")
    for x in d.get("dropped") or []:
        print("  [%.2f] %s | %s" % (x.get("score"), x.get("why"),
                                    (x.get("text") or "")[:30].replace("\n", " ")))
    if not a.commit:
        print("\n（这次没写卡。要真推就加 --commit）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
