# -*- coding: utf-8 -*-
"""把群里收到的图收下来，按魔数改后缀，筛掉截图，只留表情包。

为什么按魔数改后缀：QQ 拉下来的图经常是「文件名 .jpg，字节其实是 GIF/WebP」。
后缀错了 `read_image` 会报格式不符，发回群里客户端也可能不认。

用法：
  python tools/qq_memes.py collect            # 扫所有群最近 60 条，收图
  python tools/qq_memes.py list               # 看收了多少、都是什么
  python tools/qq_memes.py list --kind meme   # 只看表情包类
"""
import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qq_guard as G  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "qq_state", "memes_collected")
INDEX = os.path.join(OUT, "index.json")

MAGIC = [
    (b"GIF87a", ".gif"), (b"GIF89a", ".gif"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"RIFF", ".webp"),          # 再往后 8 字节是 WEBP
    (b"BM", ".bmp"),
]


def sniff(path):
    """按字节判断真实格式，返回正确后缀。"""
    with open(path, "rb") as f:
        head = f.read(16)
    for sig, ext in MAGIC:
        if head.startswith(sig):
            if ext == ".webp" and head[8:12] != b"WEBP":
                continue
            return ext
    return os.path.splitext(path)[1].lower() or ".bin"


def sha8(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def load_index():
    if os.path.exists(INDEX):
        try:
            with open(INDEX, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_index(idx):
    os.makedirs(OUT, exist_ok=True)
    with open(INDEX, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=1)


def classify(path, w, h, nbytes):
    """粗分：表情包 vs 截图 vs 别的。

    表情包：一般不大、接近正方或偏宽、文件小（动图可以稍大）。
    截图：手机/电脑截屏，长宽比明显偏长，或者特别大。
    """
    if nbytes > 2_500_000:
        return "screenshot"
    ratio = (h / w) if w else 1.0
    if ratio > 1.75 or ratio < 0.42:
        return "screenshot"
    if w > 1400 or h > 1400:
        return "screenshot"
    return "meme"


def collect(count=60, groups=None):
    os.makedirs(OUT, exist_ok=True)
    idx = load_index()
    if groups is None:
        gl = G.call("get_group_list").get("data") or []
        groups = [g["group_id"] for g in gl]
    added = 0
    for gid in groups:
        try:
            r = G.call("get_group_msg_history", group_id=gid, count=count)
        except Exception as e:
            print("  跳过 %s：%s" % (gid, e))
            continue
        msgs = (r.get("data") or {}).get("messages") or []
        for m in msgs:
            for s in (m.get("message") or []):
                if s.get("type") != "image":
                    continue
                d = s.get("data") or {}
                f = d.get("file") or ""
                if not f:
                    continue
                try:
                    rr = G.call("get_image", file=f)
                    src = ((rr.get("data") or {}).get("file")) or ""
                except Exception:
                    continue
                if not src or not os.path.exists(src):
                    continue
                key = sha8(src)
                if key in idx:
                    continue
                ext = sniff(src)
                try:
                    from PIL import Image
                    with Image.open(src) as im:
                        w, h = im.size
                except Exception:
                    w = h = 0
                n = os.path.getsize(src)
                kind = classify(src, w, h, n)
                dst = os.path.join(OUT, key + ext)
                if not os.path.exists(dst):
                    with open(src, "rb") as fi, open(dst, "wb") as fo:
                        fo.write(fi.read())
                idx[key] = {
                    "file": os.path.basename(dst), "kind": kind,
                    "w": w, "h": h, "bytes": n,
                    "group": gid, "sender": (m.get("sender") or {}).get("card")
                    or (m.get("sender") or {}).get("nickname") or "",
                    "ts": m.get("time"), "raw_name": f,
                }
                added += 1
                print("  + %s %s %sx%s %dKB %s" % (key, kind, w, h, n // 1024,
                                                   idx[key]["sender"]))
    save_index(idx)
    print("新收 %d 张，共 %d 张 → %s" % (added, len(idx), OUT))
    return added


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["collect", "list"])
    ap.add_argument("--count", type=int, default=60)
    ap.add_argument("--kind", default="", choices=["", "meme", "screenshot"])
    a = ap.parse_args(argv)
    if a.cmd == "collect":
        collect(a.count)
        return 0
    idx = load_index()
    rows = [v for v in idx.values() if not a.kind or v.get("kind") == a.kind]
    memes = [v for v in idx.values() if v.get("kind") == "meme"]
    shots = [v for v in idx.values() if v.get("kind") == "screenshot"]
    print("共 %d 张：表情包 %d ／ 截图 %d" % (len(idx), len(memes), len(shots)))
    for v in rows[:40]:
        print("  %-8s %-10s %4dx%-4d %5dKB %s"
              % (v.get("kind"), v.get("file", "")[:8], v.get("w", 0),
                 v.get("h", 0), v.get("bytes", 0) // 1024, v.get("sender", "")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
