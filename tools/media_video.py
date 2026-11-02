# -*- coding: utf-8 -*-
"""把一段视频收成几张"帧"（宿主侧，要 ffmpeg）。

为什么不整段存：视频是画面的流水账，一段十分钟的东西，真到了三个月后
能想起来的也就是三五个瞬间。整段存下来的结果是——盘满了，而她一张都想不起。

抽帧策略（全是启发式，不调模型，这台机器没有 GPU）：
  1. 时长均分取 12 个候选点（够密，又不至于解一堆帧）
  2. 每个候选解码成小图，打三个分：
     差异（跟上一个候选有多不一样 → 切镜头）
     锐度（拉普拉斯响应的方差 → 不是一片糊）
     亮度（离 0/255 太近的丢掉 → 黑场、过曝）
  3. 分数从高到低取前 --max-frames 张，按时间排好

台词不在这里生成：ASR 是另一件事（宿主的 whisper 之类），
这里只管把帧和她说话的时间窗对上（t0/t1），台词由调用方补进 caption。
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DEFAULT_DB = os.environ.get("WHALE_DB") or os.path.join(ROOT, "card.db")


def _bundled(name):
    """随卡走的 ffmpeg 优先：<卡目录>/runtime/ffmpeg/<name>.exe。
    没有盘符依赖 —— 卡在哪，工具就在哪。"""
    cand = os.path.join(ROOT, "runtime", "ffmpeg", name + ".exe")
    return cand if os.path.exists(cand) else None


FFMPEG = (os.environ.get("WHALE_FFMPEG") or _bundled("ffmpeg")
          or shutil.which("ffmpeg") or "ffmpeg")
FFPROBE = (os.environ.get("WHALE_FFPROBE") or _bundled("ffprobe")
           or shutil.which("ffprobe") or "ffprobe")

CANDIDATES = 12
WINDOW_SEC = 4.0        # 一帧代表它前后这么多秒（配 ASR 台词用）


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, **kw)


def probe_duration(path):
    r = _run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
              "-of", "default=nw=1:nk=1", path], text=True, encoding="utf-8")
    try:
        return float((r.stdout or "").strip().splitlines()[0])
    except Exception:
        return 0.0


def grab(path, ts, out_path, width=480):
    """在 ts 秒处抠一张小图（抽帧打分不需要原分辨率）。"""
    r = _run([FFMPEG, "-v", "error", "-ss", "%.2f" % ts, "-i", path,
              "-frames:v", "1", "-vf", "scale=%d:-2" % width, "-y", out_path])
    return os.path.exists(out_path) and os.path.getsize(out_path) > 512


def score_image(p):
    """(差异前的原图) → (锐度, 亮度)，值都在 0~1 附近，方便排序比较。"""
    from PIL import Image, ImageFilter, ImageStat
    im = Image.open(p).convert("L")
    edges = im.filter(ImageFilter.FIND_EDGES)
    sharp = ImageStat.Stat(edges).stddev[0] / 64.0          # 经验归一
    bright = ImageStat.Stat(im).mean[0] / 255.0
    return min(1.0, sharp), bright


def diff_score(a, b):
    """两张小图的差异（0~1）：切镜头的那一瞬间差异最大。"""
    from PIL import Image, ImageChops, ImageStat
    try:
        ia = Image.open(a).convert("L").resize((64, 36))
        ib = Image.open(b).convert("L").resize((64, 36))
    except Exception:
        return 0.0
    d = ImageChops.difference(ia, ib)
    return min(1.0, ImageStat.Stat(d).mean[0] / 90.0)


def pick_frames(path, max_frames=6, tmpdir=None):
    """返回 [(ts, tmpfile, score)]，按时间升序。"""
    dur = probe_duration(path)
    if dur <= 0.2:
        return []
    tmpdir = tmpdir or tempfile.mkdtemp()
    step = dur / (CANDIDATES + 1)
    cands = []
    for i in range(CANDIDATES):
        ts = step * (i + 1)
        f = os.path.join(tmpdir, "c%02d.jpg" % i)
        if grab(path, ts, f):
            sharp, bright = score_image(f)
            cands.append({"ts": ts, "file": f, "sharp": sharp, "bright": bright})
    if not cands:
        return []
    picked = []
    for i, c in enumerate(cands):
        prev = cands[i - 1]["file"] if i > 0 else c["file"]
        c["diff"] = diff_score(prev, c["file"])
        # 亮度太黑或太白，扣分（黑场和过曝在视频里很常见）
        pen = 1.0 if 0.12 <= c["bright"] <= 0.92 else 0.35
        c["score"] = round((0.45 * c["diff"] + 0.35 * c["sharp"]
                            + 0.20 * (1 - abs(c["bright"] - 0.5) * 2)) * pen, 4)
    for c in sorted(cands, key=lambda x: -x["score"]):
        # 同一个镜头里连着挑三张没有意义：离已选中的太近就跳过
        if any(abs(c["ts"] - p["ts"]) < max(0.6, step * 1.5) for p in picked):
            continue
        picked.append(c)
        if len(picked) >= max_frames:
            break
    picked.sort(key=lambda x: x["ts"])
    return picked, tmpdir, dur


def main(argv=None):
    ap = argparse.ArgumentParser(description="把视频收成几张帧，挂到一条记忆上")
    ap.add_argument("--mem", type=int, required=True)
    ap.add_argument("--file", required=True, help="视频文件")
    ap.add_argument("--caption", default="", help="这段话描述（可留空，之后按台词补）")
    ap.add_argument("--tags", default="")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--max-frames", type=int, default=None)
    ap.add_argument("--pinned", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    import card2
    from tools.media_import import render_frame  # 同目录，复用同一套压帧逻辑
    card = card2.MemoryCard(a.db)
    maxf = a.max_frames or card2.MEDIA_MAX_FRAMES
    tags = [t.strip() for t in (a.tags or "").split(",") if t.strip()]
    if not os.path.exists(a.file):
        print(json.dumps({"ok": False, "why": "文件不存在"}, ensure_ascii=False))
        return 1

    # ffmpeg 在这台机器上写不了 %%TEMP%%（权限拒），临时帧一律放卡目录下再收拾干净
    tmpdir = tempfile.mkdtemp(prefix='.media-tmp-', dir=card.card_dir)
    picked, tmpdir, dur = pick_frames(a.file, maxf, tmpdir=tmpdir)
    if not picked:
        print(json.dumps({"ok": False, "why": "抽不出帧（ffmpeg 失败或时长太短）"},
                         ensure_ascii=False))
        return 1

    items = []
    for c in picked:
        main, thumb, w, h = render_frame(c["file"])
        import hashlib
        sha = hashlib.sha256(main).hexdigest()
        rec = {"ts": round(c["ts"], 2), "score": c["score"],
               "size": "%dx%d" % (w, h), "kb": round(len(main) / 1024.0, 1),
               "sha256": sha[:12]}
        if not a.dry_run:
            from tools.media_import import save_frame
            rel, rel_t = save_frame(card, sha, main, thumb)
            t0 = max(0.0, c["ts"] - WINDOW_SEC)
            t1 = min(dur, c["ts"] + WINDOW_SEC)
            r = card.attach_media(
                a.mem, sha, caption=a.caption, tags=tags, kind="frame",
                mime="image/webp", path=rel, thumb=rel_t, w=w, h=h,
                dur=dur, nbytes=len(main), t0=t0, t1=t1,
                source="user", pinned=1 if a.pinned else 0)
            rec.update({"ok": r.get("ok"), "media_id": r.get("media_id"),
                        "dup": r.get("dup"), "t0": round(t0, 2), "t1": round(t1, 2)})
        items.append(rec)
    try:
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception:
        pass
    print(json.dumps({"ok": True, "mem": a.mem, "duration": round(dur, 2),
                      "n": len(items), "items": items},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
