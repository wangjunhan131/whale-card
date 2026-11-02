# -*- coding: utf-8 -*-
"""语音 → 文字（宿主侧）。声音从来不进卡，进卡的只有它说的话。

为什么不留音频：声音里真正有用的信息是"说了什么"，那部分适合文字记忆
（能被复述、能被召回、能参与遗忘）；剩下的语调、环境声，存了也用不上，
还会把卡撑胖。所以这个工具只做一件事：把话变成文字写进记忆。

两条路：
  1) 已经有转写了：--transcript 给一个 txt（每行一句）或 jsonl（{"t":12.3,"text":"..."}）
     —— 推荐这条路：转写质量可以人工过一眼，也方便以后换 ASR
  2) 没有转写：自动找可用的 ASR（faster-whisper / whisper CLI / vosk），
     抽 16k 单声道音频到卡目录临时文件 → 转写 → 立刻删音频
     国内下载模型慢的话先设 HF_ENDPOINT=https://hf-mirror.com；
     模型用 --asr-model 选（tiny 最快但中文糙，small 是默认）

用法：
    python tools/media_audio.py --file 语音.m4a --captions 语音.txt
    python tools/media_audio.py --file 视频.mp4 --asr        # 需要装 faster-whisper

参数 --speaker 用来标谁说的（默认 user）；--tags 会挂到每条记忆上（现在只写进 --note）。
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
    """随卡走的 ffmpeg 优先：<卡目录>/runtime/ffmpeg/<name>.exe。"""
    cand = os.path.join(ROOT, "runtime", "ffmpeg", name + ".exe")
    return cand if os.path.exists(cand) else None


FFMPEG = (os.environ.get("WHALE_FFMPEG") or _bundled("ffmpeg")
          or shutil.which("ffmpeg") or "ffmpeg")


def extract_wav(src, dst):
    """抽成 16k 单声道 wav（ASR 基本都吃这个）。"""
    r = subprocess.run([FFMPEG, "-v", "error", "-y", "-i", src,
                        "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", dst],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return os.path.exists(dst) and os.path.getsize(dst) > 1024, (r.stderr or "")[-300:]


def load_transcript(path):
    """支持三种：jsonl（{"t":秒,"text":"..."}）、纯文本每行一句、带 [00:12] 前缀的文本。"""
    out = []
    if path.lower().endswith(".jsonl") or path.lower().endswith(".json"):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            txt = (o.get("text") or "").strip()
            if txt:
                out.append({"t": float(o.get("t") or 0), "text": txt})
        return out
    import re
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line.strip():
            continue
        m = re.match(r"^\[?(\d{1,2}):(\d{2})(?::(\d{2}))?\]?\s*(.+)$", line.strip())
        if m:
            a, b, c, txt = m.groups()
            sec = int(a) * 60 + int(b) if c is None else int(a) * 3600 + int(b) * 60 + int(c)
            out.append({"t": float(sec), "text": txt.strip()})
        else:
            out.append({"t": 0.0, "text": line.strip()})
    return out


def _read_wav_16k(path):
    """自己读 wav 成 float32 数组：绕开 av/ffmpeg 解码（av 19 与 faster-whisper
    1.2 的 metadata_errors 参数不兼容，直接喂数组最省事）。"""
    import numpy as np
    import wave
    with wave.open(path, "rb") as w:
        assert w.getnchannels() == 1, "要单声道"
        assert w.getframerate() == 16000, "要 16k"
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def try_asr(wav):
    """按可用性依次尝试。返回 (segments, backend) 或 (None, 原因)。"""
    last = ""
    try:
        from faster_whisper import WhisperModel
        audio = _read_wav_16k(wav)
        model = WhisperModel(os.environ.get("WHALE_ASR_MODEL", "small"),
                             device="cpu", compute_type="int8")
        segs, _info = model.transcribe(audio, language="zh", vad_filter=True)
        return ([{"t": float(s.start), "text": (s.text or "").strip()}
                 for s in segs if (s.text or "").strip()], "faster-whisper")
    except Exception as e:
        last = repr(e)[:160]
    cli = shutil.which("whisper")
    if cli:
        try:
            outdir = tempfile.mkdtemp(prefix=".asr-", dir=os.path.dirname(wav))
            subprocess.run([cli, wav, "--language", "zh", "--output_format", "json",
                            "--output_dir", outdir], capture_output=True, timeout=1800)
            for f in os.listdir(outdir):
                if f.endswith(".json"):
                    data = json.load(open(os.path.join(outdir, f), encoding="utf-8"))
                    segs = [{"t": float(s.get("start") or 0),
                             "text": (s.get("text") or "").strip()}
                            for s in data.get("segments") or []]
                    return ([s for s in segs if s["text"]], "whisper-cli")
        except Exception:
            pass
    return None, ("没有可用的 ASR（pip install faster-whisper，或先用 --transcript 给转写）"
                  + ("；最后一次错误：%s" % last if last else ""))


def main(argv=None):
    ap = argparse.ArgumentParser(description="把语音/视频里的话变成文字记忆")
    ap.add_argument("--file", default="", help="音频或视频文件（--asr 时才需要）")
    ap.add_argument("--transcript", default="", help="已有转写（txt / jsonl）")
    ap.add_argument("--asr", action="store_true", help="自动找 ASR 转写（要先装）")
    ap.add_argument("--asr-model", default="",
                    help="faster-whisper 模型：tiny/base/small…（默认 small；tiny 更快但中文糙）")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--speaker", default="user")
    ap.add_argument("--note", default="", help="给这批记忆加一句上下文（拼在每条前面）")
    ap.add_argument("--min-chars", type=int, default=2)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    import card2
    card = card2.MemoryCard(a.db)

    if a.transcript:
        if not os.path.exists(a.transcript):
            print(json.dumps({"ok": False, "why": "转写文件不存在"}, ensure_ascii=False))
            return 1
        segs = load_transcript(a.transcript)
        src = "transcript"
    elif a.asr:
        if not os.path.exists(a.file):
            print(json.dumps({"ok": False, "why": "文件不存在"}, ensure_ascii=False))
            return 1
        tmpdir = tempfile.mkdtemp(prefix=".asr-", dir=card.card_dir)
        wav = os.path.join(tmpdir, "a.wav")
        ok, err = extract_wav(a.file, wav)
        if not ok:
            print(json.dumps({"ok": False, "why": "抽音频失败", "detail": err},
                             ensure_ascii=False))
            return 1
        if a.asr_model:
            os.environ["WHALE_ASR_MODEL"] = a.asr_model
        segs, src = try_asr(wav)
        shutil.rmtree(tmpdir, ignore_errors=True)   # 声音不落盘
        if segs is None:
            print(json.dumps({"ok": False, "why": src}, ensure_ascii=False))
            return 1
    else:
        print(json.dumps({"ok": False,
                          "why": "要么给 --transcript，要么加 --asr（需要先装 ASR）"},
                         ensure_ascii=False))
        return 1

    segs = [s for s in segs if len((s.get("text") or "").strip()) >= a.min_chars]
    if not segs:
        print(json.dumps({"ok": False, "why": "转写里没有有效句子"}, ensure_ascii=False))
        return 1

    made = []
    for s in segs:
        txt = s["text"].strip()
        if a.note:
            txt = a.note + "：" + txt
        rec = {"t": s.get("t", 0), "text": txt}
        if not a.dry_run:
            mid = card.remember(txt, salience=0.62, speaker=a.speaker)
            rec["mem_id"] = mid
        made.append(rec)

    print(json.dumps({"ok": True, "via": src, "n": len(made),
                      "dry_run": bool(a.dry_run), "items": made},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
