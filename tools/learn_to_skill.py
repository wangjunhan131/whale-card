# -*- coding: utf-8 -*-
"""把学到的整理成技能。

用法：
    python -X utf8 tools\\learn_to_skill.py --note <笔记.md> [--name 名字] [--dry]

为什么要有这个：笔记是「我读过什么」，技能是「下次遇到这类事我该怎么做」。
两者混在一起，笔记越多、检索越吵。这里把一份笔记压成一条可复用的做法。

设计上的克制：
- 只认「笔记里真写了的」，抽不出「什么时候用」就如实写「场景：未标注」，
  不替它编一个场景（编出来的场景下次会误导我）。
- 技能是覆盖写（name 唯一），改主意了就再跑一次，不留同名两条。
"""
import argparse
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

SKIP_PREFIX = ("```", "---", "> ")


def extract(note_text, name=None):
    """从笔记里抽 (名字, 描述)。抽不出来就返回 (None, 原因)。"""
    if not note_text or not note_text.strip():
        return None, "笔记是空的"
    lines = [l.rstrip("\n") for l in note_text.split("\n")]
    title = ""
    for l in lines:
        m = re.match(r"^\s*#{1,3}\s+(.+?)\s*$", l)
        if m:
            title = m.group(1).strip()
            break
    if not title:
        for l in lines:
            if l.strip() and not l.startswith(SKIP_PREFIX):
                title = l.strip()[:60]
                break
    nm = (name or title or "").strip()
    if not nm:
        return None, "找不到标题，也没给 --name"

    when = ""
    for l in lines:
        m = re.match(r"^\s*[-*]?\s*(什么时候用|场景|适用|用在|何时用)\s*[:：]\s*(.+)$", l)
        if m:
            when = m.group(2).strip()
            break

    bullets = []
    for l in lines:
        m = re.match(r"^\s*[-*]\s+(.+)$", l)
        if not m:
            m2 = re.match(r"^\s*\d+[.、]\s+(.+)$", l)
            m = m2
        if not m:
            continue
        t = m.group(1).strip()
        if len(t) < 6 or t.startswith("<"):
            continue
        bullets.append(t)
        if len(bullets) >= 6:
            break

    body = ["场景：%s" % (when or "未标注")]
    if bullets:
        body.append("做法：")
        body.extend("- " + b for b in bullets)
    else:
        head = " ".join(x.strip() for x in lines if x.strip() and not x.startswith("#"))[:300]
        body.append("原文要点：%s" % (head or "（这份笔记没有可抽的要点）"))
    return (nm[:80], "\n".join(body)), None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--note", required=True)
    ap.add_argument("--name", default="")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args(argv)

    s = io.open(a.note, encoding="utf-8").read()
    got, why = extract(s, a.name or None)
    if not got:
        print("抽不出来：%s" % why)
        return 2
    nm, desc = got
    print("名字：%s" % nm)
    print(desc)
    if a.dry:
        print("（--dry，没有写卡）")
        return 0

    import skill as skill_mod
    import sqlite3
    db = sqlite3.connect(os.path.join(ROOT, "card.db"), timeout=30.0)
    try:
        sm = skill_mod.SkillMemory(db)
        r = sm.add(nm, desc)
        print("写入结果：%r" % (r,))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
