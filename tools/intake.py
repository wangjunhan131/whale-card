#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""统一收件口的命令行入口：把「我看完的东西」推进卡里。

用户 2026-11-08 定的规矩：
  · 所有来源都进同一个口（这个对话框、QQ、抖音、B站、小说）
  · 收件层只装、只除杂，不判断值不值得记
  · **对话原文一个字都不许改**；但长篇文字（小说/B站字幕）可以提炼主干
    再加几处细节 —— 那是"我看完的印象"，不是谁的对话原文

用法：
  python -X utf8 tools/intake.py --text "..." --source bili
  python -X utf8 tools/intake.py --file 字幕.txt --source bili --kind subtitle
  python -X utf8 tools/intake.py --file 小说.txt --kind novel --full   # 整篇进
  python -X utf8 tools/intake.py --list 20                            # 看收件层
"""
import argparse
import io
import json
import os
import re
import sys
import urllib.request

MIND = os.environ.get("WHALE_MIND", "http://127.0.0.1:8848")
LONG_CHARS = 1200          # 超过它就不是"一句对话"，可以提炼
MAX_DETAIL = 6             # 提炼时留几处细节


def _post(path, body, timeout=30):
    req = urllib.request.Request(
        MIND + path, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _get(path, timeout=30):
    with urllib.request.urlopen(MIND + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def condense(text, max_detail=MAX_DETAIL):
    """长文提炼：主干 + 几处细节（确定性规则，不调模型）

    步骤很朴素：按段落切开 → 每段取第一句当"这段说了什么" →
    再挑几处带数字/引号/专名的句子当细节。
    它不追求像摘要，只追求"回头还能想起这篇讲了什么、哪句值得翻回去"。
    """
    text = (text or "").strip()
    if not text:
        return "", 0
    paras = [p.strip() for p in re.split(r"\n\s*\n|\r\n\r\n", text) if p.strip()]
    if not paras:
        paras = [text]
    spine = []
    for p in paras[:14]:
        one = re.split(r"(?<=[。！？；!?;])", p.strip())[0].strip()
        if one:
            spine.append(one[:160])
    # 细节：带数字、引号、书名号、专名的句子更像"具体的东西"
    sents = [s.strip() for s in re.split(r"(?<=[。！？!?])", text) if s.strip()]
    scored = []
    for i, s in enumerate(sents):
        if len(s) < 8 or len(s) > 200:
            continue
        score = 0
        if re.search(r"\d", s):
            score += 2
        if re.search(r"[「」“”《》]", s):
            score += 1
        if re.search(r"[A-Za-z]{3,}", s):
            score += 1
        if score:
            scored.append((score, i, s))
    scored.sort(key=lambda x: (-x[0], x[1]))
    details = [s for _sc, _i, s in scored[:max_detail]]
    out = "【主干】\n" + "\n".join("- " + x for x in spine)
    if details:
        out += "\n【细节】\n" + "\n".join("- " + x for x in details)
    out += "\n（原文 %d 字，已提炼）" % len(text)
    return out, len(text)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default="")
    ap.add_argument("--file", default="")
    ap.add_argument("--source", default="cli",
                    help="bili / douyin / novel / qq / cli / dsh")
    ap.add_argument("--channel", default="")
    ap.add_argument("--who", default="")
    ap.add_argument("--kind", default="text")
    ap.add_argument("--full", action="store_true",
                    help="长篇也整篇塞进去，不提炼")
    ap.add_argument("--note", default="")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--list", type=int, default=0)
    a = ap.parse_args(argv)

    if a.list:
        d = _get("/inbox?limit=%d" % a.list)
        for it in (d.get("items") or []):
            print("#%s %s %s/%s %s | %s" % (
                it.get("id"), it.get("ts_str") or "", it.get("source") or "",
                it.get("channel") or "", it.get("who") or "",
                (it.get("text") or "")[:70].replace("\n", " ")))
        print("共 %s 条（kept %s）" % (d.get("count"), d.get("kept")))
        return 0

    text = a.text
    if a.file:
        text = io.open(a.file, encoding="utf-8", errors="replace").read()
    if not (text or "").strip():
        print("没有内容")
        return 2

    orig_len = len(text)
    note = a.note
    if orig_len > LONG_CHARS and not a.full:
        text, real_len = condense(text)
        note = (note + " " if note else "") + "原文 %d 字" % real_len
        print("长文 %d 字 → 提炼成 %d 字" % (orig_len, len(text)))

    body = {"text": text, "source": a.source, "who": a.who,
            "kind": a.kind, "note": note}
    if a.channel:
        body["channel"] = a.channel
    if a.dry:
        print(json.dumps(body, ensure_ascii=False)[:600])
        return 0
    r = _post("/intake", body)
    print(json.dumps(r, ensure_ascii=False))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
