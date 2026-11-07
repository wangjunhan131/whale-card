# -*- coding: utf-8 -*-
"""主动升级：自己给自己做体检，把发现写成提案。

用法：
    python -X utf8 tools\\self_upgrade.py [--dry] [--quiet]

它做什么：只看不改。跑一遍体检，把「哪里在变差、哪里该动」整理成一条提案，
写进卡里（region=work, source=cli）。**它永远不改代码、不删数据、不动参数。**

为什么只写提案不动手：改代码/改数据是不可逆的，按规矩得先说出来；
而「发现问题」这件事本身可以自己做——这才是主动性该有的地方。
"""
import argparse
import datetime as dt
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

MIND = "http://127.0.0.1:8848"


def _connect(ro=True):
    p = os.path.join(ROOT, "card.db")
    if ro:
        return sqlite3.connect("file:%s?mode=ro" % p.replace("\\", "/"), uri=True,
                               timeout=30.0)
    return sqlite3.connect(p, timeout=30.0)


def scan(db_path=None):
    """只读体检，返回每项 (名字, 值, 判据, 是否要看)。"""
    out = []
    db = _connect(True)
    try:
        def one(sql, *a):
            try:
                r = db.execute(sql, a).fetchone()
                return r[0] if r else None
            except Exception:
                return None

        mem = one("SELECT COUNT(*) FROM mem") or 0
        dead = one("SELECT COUNT(*) FROM mem WHERE COALESCE(dead,0)=1") or 0
        quar = one("SELECT COUNT(*) FROM mem WHERE COALESCE(quarantine,0)=1") or 0
        tomb = one("SELECT COUNT(*) FROM mem_tomb") or 0
        low_w = one("SELECT COUNT(*) FROM mem WHERE w0 < 0.2") or 0
        no_uid = one("SELECT COUNT(*) FROM mem WHERE COALESCE(about_uid,'')=''") or 0
        detail = one("SELECT COUNT(*) FROM detail") or 0

        out.append(("记忆", mem, "总数", False))
        out.append(("软删除", dead, "应为 0 或很少", dead > 20))
        out.append(("隔离区", quar, "被防污层拦住、还没人复核", quar > 0))
        out.append(("墓碑", tomb, "累计自然遗忘（不是错误）", tomb > mem * 0.5))
        out.append(("低权重", low_w, "w0<0.2 快忘的", low_w > mem * 0.5))
        out.append(("对象覆盖率", "%.0f%%" % (100.0 * (mem - no_uid) / mem if mem else 0),
                    "带 about_uid 的比例，低于 60% 召回就分不清人",
                    mem and (mem - no_uid) / mem < 0.6))
        out.append(("细节行", detail, "每条记忆平均 %.1f 片段" % (detail / mem if mem else 0),
                    detail > mem * 8))
    finally:
        db.close()
    return out


def build_text(items, stamp=None):
    stamp = stamp or dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    watch = [x for x in items if x[3]]
    head = "自检 %s：%d 项里 %d 项要看一眼。" % (stamp, len(items), len(watch))
    body = ["- %s：%s（%s）" % (n, v, note) for n, v, note, _ in items]
    todo = []
    for n, v, note, bad in watch:
        if n == "隔离区":
            todo.append("复核隔离区里的 %s 条（GET /mem/quarantine），是真的才放行" % v)
        elif n == "软删除":
            todo.append("软删除 %s 条有点多，看是不是有人忘了 purge" % v)
        elif n == "对象覆盖率":
            todo.append("对象覆盖率 %s，写入侧要补 about_uid" % v)
        elif n == "低权重":
            todo.append("%s 条 w0<0.2，确认不是被钉死/压扁的" % v)
        elif n == "墓碑":
            todo.append("墓碑 %s 条，看是不是又有「短期池到期」批量产生" % v)
        elif n == "细节行":
            todo.append("细节 %s 行从不压缩，该在 Dream 里合并了" % v)
    if todo:
        body.append("该看的：")
        body.extend("- " + t for t in todo)
    else:
        body.append("该看的：没有。")
    return head + "\n" + "\n".join(body)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    items = scan()
    txt = build_text(items)
    if not a.quiet:
        print(txt)
    if a.dry:
        print("（--dry，没有写卡）")
        return 0
    try:
        import requests
        r = requests.post(MIND + "/remember",
                          json={"text": txt, "source": "cli", "pool": "long",
                                "salience": 0.55, "region": "work"}, timeout=30)
        if r.status_code == 200:
            if not a.quiet:
                print("写回卡：mem_id=%s" % r.json().get("mem_id"))
            return 0
        print("写回失败：HTTP %s" % r.status_code)
        return 1
    except Exception as e:
        print("写回失败：%s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
