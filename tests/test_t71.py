# -*- coding: utf-8 -*-
"""语音转写落地自测：走 --transcript 这条路（本机没装 ASR），验记忆真的写进去了。"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import card2  # noqa: E402

d = tempfile.mkdtemp(prefix="_t71_")
db = os.path.join(d, "t.db")
card = card2.MemoryCard(db)

txt = os.path.join(d, "asr.txt")
open(txt, "w", encoding="utf-8").write(
    "[00:03] 那天晚上我们在路边摊吃烧烤\n"
    "[00:12] 辣椒特别辣，我喝了两瓶水\n"
    "[00:25] 后来还去看了海\n")
jsonl = os.path.join(d, "asr.jsonl")
open(jsonl, "w", encoding="utf-8").write(
    json.dumps({"t": 3.2, "text": "语音里的第一句"}, ensure_ascii=False) + "\n" +
    json.dumps({"t": 9.0, "text": "语音里的第二句"}, ensure_ascii=False) + "\n")

TOOL = [sys.executable, "-X", "utf8", os.path.join(ROOT, "tools", "media_audio.py")]
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONPATH=ROOT)
ok = []


def chk(name, cond, extra=""):
    ok.append(bool(cond))
    print(("  ✅ " if cond else "  ❌ ") + name + ("  " + str(extra) if extra else ""))


r = subprocess.run(TOOL + ["--db", db, "--transcript", txt],
                   capture_output=True, text=True, encoding="utf-8", env=ENV)
res = json.loads(r.stdout) if r.stdout.strip().startswith("{") else {}
chk("txt 路线成功", r.returncode == 0 and res.get("ok"), res.get("via"))
chk("三句都落成记忆", res.get("n") == 3, res.get("n"))
chk("时间戳解析对了", [i["t"] for i in res.get("items", [])] == [3.0, 12.0, 25.0],
    [i["t"] for i in res.get("items", [])])

n_mem = card.db.execute("SELECT COUNT(*) FROM mem").fetchone()[0]
chk("库里真有三条记忆", n_mem == 3, n_mem)
row = card.db.execute("SELECT raw FROM mem WHERE raw LIKE '%辣椒%'").fetchone()
chk("内容对得上（原文完整留着）",
    row is not None and "辣椒特别辣" in row["raw"] and "两瓶水" in row["raw"],
    row["raw"] if row else None)

r2 = subprocess.run(TOOL + ["--db", db, "--transcript", jsonl, "--note", "语音"],
                    capture_output=True, text=True, encoding="utf-8", env=ENV)
res2 = json.loads(r2.stdout) if r2.stdout.strip().startswith("{") else {}
chk("jsonl 路线成功", r2.returncode == 0 and res2.get("ok"), res2.get("n"))
chk("--note 会拼在句首", all(i["text"].startswith("语音：") for i in res2.get("items", [])),
    res2.get("items", [{}])[0].get("text"))
chk("库里一共五条", card.db.execute("SELECT COUNT(*) FROM mem").fetchone()[0] == 5)

# 没有转写也没有 ASR → 要明确说清楚怎么办，不能静默
r3 = subprocess.run(TOOL + ["--db", db, "--file", "x.m4a"],
                    capture_output=True, text=True, encoding="utf-8", env=ENV)
res3 = json.loads(r3.stdout) if r3.stdout.strip().startswith("{") else {}
chk("缺参数时报错清楚", r3.returncode == 1 and "asr" in (res3.get("why") or "").lower(),
    res3.get("why"))

fails = [x for x in ok if not x]
print("\n结果：%d/%d 通过" % (len(ok) - len(fails), len(ok)))
shutil.rmtree(d, ignore_errors=True)
raise SystemExit(1 if fails else 0)
