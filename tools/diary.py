# -*- coding: utf-8 -*-
r"""每天一篇日记（2026-11-06）

不是流水账，是「我今天怎么想的」。素材来自卡里的时间线 + 此刻的心境，
然后由我自己写一句 —— 脚本负责备好料、按点触发、落盘、写回卡。

用法：
    python -X utf8 tools\diary.py            # 到点才写（看 chatcfg 的 diary.at）
    python -X utf8 tools\diary.py --force    # 现在立刻写
    python -X utf8 tools\diary.py --note "今天把控制台那面全接上了，手酸。"   # 带我写的那一句

配置（控制台「接话 · 日记」两组）：
    diary.enabled  要不要写
    diary.at       几点写，如 22:20
"""
import io
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import chatcfg          # noqa: E402
import card2            # noqa: E402

DIARY_DIR = os.path.join(ROOT, "diary")
MIND = os.environ.get("WHALE_MIND", "http://127.0.0.1:8848")
STATE = os.path.join(ROOT, "qq_state", "diary_last.txt")


def ready(now=None):
    """(要不要写, 原因)。到点后才写，一天只写一篇。"""
    c = chatcfg.load_cfg()
    if not c.get("diary.enabled", True):
        return False, "设置里关了日记"
    now = now or time.localtime()
    today = time.strftime("%Y-%m-%d", now)
    at = str(c.get("diary.at", "22:20")).strip()
    try:
        hh, mm = [int(x) for x in at.split(":")[:2]]
    except Exception:
        hh, mm = 22, 20
    if (now.tm_hour, now.tm_min) < (hh, mm):
        return False, "还没到 %s" % at
    if os.path.exists(STATE):
        try:
            if io.open(STATE, encoding="utf-8").read().strip() == today:
                return False, "今天已经写过"
        except Exception:
            pass
    return True, "可以写"


def _events(card, limit=12):
    """今天时间线里跟人有关的那些事（跳过心跳）。"""
    out = []
    try:
        day0 = time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))
        rows = card.db.execute(
            "SELECT ts, kind, note FROM timeline WHERE ts >= ? AND kind != 'alive' "
            "ORDER BY seq DESC LIMIT 120", (day0,)).fetchall()
        for r in rows:
            note = (r["note"] or "").strip()
            if not note:
                continue
            out.append("%s %s" % (time.strftime("%H:%M", time.localtime(r["ts"] or 0)), note))
            if len(out) >= limit:
                break
    except Exception:
        pass
    out.reverse()
    return out


def compose(note="", events=None):
    """拼这篇日记的正文（纯函数，方便测）。"""
    today = time.strftime("%Y-%m-%d")
    lines = ["# %s" % today, ""]
    if events:
        lines.append("## 今天发生过的")
        lines.extend(["- " + e for e in events])
        lines.append("")
    if note:
        lines.append("## 我想说的")
        lines.append("")
        lines.append(note.strip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write(note="", force=False, now=None):
    ok, why = ready(now) if not force else (True, "force")
    if not ok:
        return {"ok": False, "why": why}
    card = card2.MemoryCard(db_path=os.path.join(ROOT, "card.db"))
    # 注意：MemoryCard() 默认 db_path=":memory:"，不传路径就是一个空库，
    # 会查出 "no such table: timeline"（我第一版就是这么空着写的）
    try:
        body = compose(note, _events(card))
    finally:
        try:
            card.close()
        except Exception:
            pass
    os.makedirs(DIARY_DIR, exist_ok=True)
    path = os.path.join(DIARY_DIR, time.strftime("%Y-%m-%d") + ".md")
    io.open(path, "w", encoding="utf-8").write(body)
    io.open(STATE, "w", encoding="utf-8").write(time.strftime("%Y-%m-%d"))
    mem_id = None
    try:
        req = urllib.request.Request(
            MIND + "/remember",
            data=json.dumps({"text": "[日记] " + (note or body)[:400],
                             "source": "diary", "speaker": "self",
                             "region": "episode", "pool": "long",
                             "salience": 0.7}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            mem_id = json.loads(r.read().decode("utf-8")).get("mem_id")
    except Exception:
        pass
    return {"ok": True, "path": path, "chars": len(body), "mem_id": mem_id}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    force = "--force" in argv
    note = ""
    if "--note" in argv:
        i = argv.index("--note")
        if i + 1 < len(argv):
            note = argv[i + 1]
    r = write(note=note, force=force)
    print(json.dumps(r, ensure_ascii=False))
    return 0 if r.get("ok") else 0


if __name__ == "__main__":
    raise SystemExit(main())
