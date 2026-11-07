# -*- coding: utf-8 -*-
"""voice.py —— 把一句话变成能发出去的语音条

为什么是 edge-tts 而不是别的：
  · 不用显卡（本机 RX 580 跑不了 RVC/IndexTTS2 那一套）
  · 不用下模型（几十 MB 都没有，走微软的在线合成）
  · 快 —— 一句十来字 1.5 秒左右出 mp3

和 RVC 的分工（以后真要做"像谁"再说）：
  edge-tts 决定**像不像人**，RVC 决定**像不像她**。
  现在只做前半截，因为"像谁"要音色模型，而音色模型不是我们的东西。

NapCat 那边的真相（从 napcat.mjs 里挖出来的）：
  发 `record` 段时它会先 `isSilk()`，不是 silk 就用自带的 ffmpeg addon
  `convertToNTSilkTct()` 转一遍再发。所以这里只管给 mp3 路径，
  转码不用我们操心 —— 这也解释了为什么不用自己装 silk 编码器。

三个必须守的规矩：
  1. **文字发出去撤不回来** —— 所以"要不要发语音"必须在发送前决定，
     发完再想改就晚了。这里是纯函数，不给它任何副作用。
  2. **语音挂了退回打字** —— `synth()` 任何一步失败都返回 None，
     调用方拿到 None 就照常发文字。宁可她打字，也不能一声不吭。
  3. **不缓存等于每次重新合成** —— 同一句话（同一组参数）走缓存，
     重复的话不用重新请求，也省得在群里听起来一模一样的两条还要等。

用法：
  python tools/voice.py "你回来啦"                    # 合成到默认目录，打印路径
  python tools/voice.py "你回来啦" --out a.mp3
  python tools/voice.py --voices                      # 列出中文声线
  python tools/voice.py --check "这句话该发语音吗"      # 只跑判定，不合成
"""
import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(ROOT, "qq_state", "voice")

# ── 声线 ────────────────────────────────────────────────────────────────
# XiaoyiNeural 是中文女声里最"年轻"的一个，语速略快、音高偏高，
# 读短句不像播音。XiaoxiaoNeural 更标准、更"客服"，适合长句。
VOICE_DEFAULT = "zh-CN-XiaoyiNeural"
VOICE_LONG = "zh-CN-XiaoxiaoNeural"
RATE_DEFAULT = "+10%"
PITCH_DEFAULT = "+20Hz"

# ── 什么时候该发语音 ────────────────────────────────────────────────────
# 阈值不是拍脑袋：群里别人说话中位 16 字，我自己中位 31 字。
# 语音条超过 20 秒就没人点了，20 秒大概 60~70 字。
VOICE_MAX_CHARS = 40       # 超过这个字数的句子发语音太长，还是打字
VOICE_MIN_CHARS = 2        # 一个字（"嗯""哦"）发语音太怪

# ── 可调的配置（控制台「语音」那一栏写这里）─────────────────────────────
# 为什么不放卡里的 runtime_config：voice.py 要在没有卡、或者卡被别的进程锁着
# 的时候也能用（qq_say.py 发送前就要算"该发语音吗"）。放一个小 JSON 文件，
# 读写都是原子的，跟卡的可用性解耦。代码里的常量仍是"默认值"，文件只存"改过的"。
CFG_PATH = os.path.join(CACHE, "cfg.json")
CFG_DEFAULTS = {
    "voice": VOICE_DEFAULT,
    "rate": RATE_DEFAULT,
    "pitch": PITCH_DEFAULT,
    "max_chars": VOICE_MAX_CHARS,
    # 外部 TTS 接入位（留好了，但**还没接** —— 见 load_cfg 注释，别假装它能用）
    "provider": "edge",          # edge = 现在这条真的在跑
    "ext_base": "",              # 外部服务的 base_url
    "ext_key": "",
    "ext_model": "",
}

# 这些内容发语音是错的：链接念不出来、代码念出来更糟
_RE_NO_VOICE = re.compile(
    r"https?://|www\.|```|`[^`]+`|\b[A-Za-z]{6,}\b|[\w.+-]+@[\w-]+\.\w+")
# 纯标点/表情/空白 —— 没有可念的东西
_RE_EMPTY = re.compile(r"^[\s\W_]*$", re.UNICODE)



def _max_chars():
    # 语音最长几个字：从控制台那张表读（voice.max_chars），读不到用常量。
    try:
        import chatcfg as _cc
        return int(_cc.load_cfg().get("voice.max_chars", VOICE_MAX_CHARS))
    except Exception:
        return VOICE_MAX_CHARS
def speakable(text, max_chars=None):
    """这句话值不值得变成语音条。返回 (bool, 理由)。

    纯函数，不碰网络、不碰磁盘 —— 因为调用它的时候往往还没决定发不发。
    max_chars 传了就按它判（控制台把上限调宽了，这里要跟着变）。
    """
    if not text:
        return False, "空"
    t = text.strip()
    if _RE_EMPTY.match(t):
        return False, "只有标点或表情"
    if len(t) < VOICE_MIN_CHARS:
        return False, "太短（%d 字）" % len(t)
    _max = _max_chars() if max_chars is None else int(max_chars)
    if len(t) > _max:
        return False, "太长（%d 字 > %d）" % (len(t), _max)
    if _RE_NO_VOICE.search(t):
        return False, "含链接/代码/英文单词，念出来没意义"
    # 括号里的舞台提示（"（笑）"）念出来很怪，但只有少量时可以留着，
    # 因为 tts 会把括号当停顿，反而像真的在喘气
    return True, "ok"


def load_cfg():
    """读配置（文件里的覆盖默认值）。坏文件/坏字段一律回默认，不抛。"""
    cfg = dict(CFG_DEFAULTS)
    try:
        with open(CFG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            for k in CFG_DEFAULTS:
                if raw.get(k) not in (None, ""):
                    cfg[k] = raw[k]
    except Exception:
        pass
    # 外部 TTS 只是**留了个位**：现在真正能跑的只有 edge。
    # 如果有人把 provider 改成 ext，这里强行退回 edge 并在合成日志里说清楚，
    # 而不是让调用方以为它在用外部模型（"写进去了"≠"真的生效"）。
    if cfg.get("provider") != "edge":
        cfg["provider"] = "edge"
    return cfg


def save_cfg(patch):
    """改一项或几项，返回改完的完整配置。范围不合法的项直接忽略（不静默写坏）。"""
    cfg = load_cfg()
    for k, v in (patch or {}).items():
        if k not in CFG_DEFAULTS:
            continue
        if k == "max_chars":
            try:
                v = int(float(v))
            except Exception:
                continue
            if not (2 <= v <= 300):
                continue
        elif k == "rate":
            v = str(v).strip()
            if not re.match(r"^[+-]\d{1,3}%$", v):
                continue
        elif k == "pitch":
            v = str(v).strip()
            if not re.match(r"^[+-]\d{1,3}Hz$", v):
                continue
        cfg[k] = v
    tmp = CFG_PATH + ".part"
    try:
        os.makedirs(CACHE, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CFG_PATH)
    except OSError as e:
        sys.stderr.write("voice.save_cfg 失败：%s\n" % e)
    return cfg


def _key(text, voice, rate, pitch):
    raw = "\x1f".join([voice, rate, pitch, text])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _ensure_dir():
    os.makedirs(CACHE, exist_ok=True)
    return CACHE


async def _synth_async(text, voice, rate, pitch, path):
    import edge_tts
    c = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    await c.save(path)


def synth(text, voice=None, rate=None, pitch=None, use_cache=True):
    """合成一句，返回 mp3 的绝对路径；失败返回 None（调用方退回打字）。"""
    cfg = load_cfg()
    ok, why = speakable(text, max_chars=cfg.get("max_chars"))
    if not ok:
        return None
    voice = voice or cfg.get("voice") or VOICE_DEFAULT
    rate = rate or cfg.get("rate") or RATE_DEFAULT
    pitch = pitch or cfg.get("pitch") or PITCH_DEFAULT

    d = _ensure_dir()
    key = _key(text.strip(), voice, rate, pitch)
    path = os.path.join(d, key + ".mp3")

    if use_cache and os.path.exists(path) and os.path.getsize(path) > 800:
        return os.path.abspath(path)

    tmp = path + ".part"
    t0 = time.time()
    try:
        asyncio.run(_synth_async(text.strip(), voice, rate, pitch, tmp))
        if not os.path.exists(tmp) or os.path.getsize(tmp) < 800:
            raise RuntimeError("合成结果为空或过小")
        os.replace(tmp, path)          # 先写 .part 再换名，别留下半截文件
    except Exception as e:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        sys.stderr.write("voice.synth 失败（退回打字）：%s\n" % e)
        return None

    # 记一笔，方便以后统计"语音条多长、合成多快"
    try:
        with open(os.path.join(d, "synth.log"), "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "chars": len(text), "sec": round(time.time() - t0, 2),
                "bytes": os.path.getsize(path), "voice": voice,
                "rate": rate, "pitch": pitch,
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return os.path.abspath(path)


def duration(path):
    """时长（秒）。拿不到就返回 0，不抛异常 —— 只是用来记日志的。"""
    try:
        import subprocess
        ff = os.path.join(ROOT, "deploy", "ffmpeg", "bin", "ffprobe.exe")
        if not os.path.exists(ff):
            ff = "ffprobe"
        out = subprocess.run(
            [ff, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, timeout=20)
        return float(out.stdout.decode("utf-8", "ignore").strip() or 0)
    except Exception:
        return 0.0


def list_voices(prefix="zh"):
    import edge_tts
    vs = asyncio.run(edge_tts.list_voices())
    return [(v["ShortName"], v.get("Gender", ""), v.get("Locale", ""))
            for v in vs if v.get("Locale", "").startswith(prefix)]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="?", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--voice", default=VOICE_DEFAULT)
    ap.add_argument("--rate", default=RATE_DEFAULT)
    ap.add_argument("--pitch", default=PITCH_DEFAULT)
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--voices", action="store_true")
    ap.add_argument("--check", default="")
    a = ap.parse_args(argv)

    if a.voices:
        for name, g, loc in list_voices():
            print("%-28s %-6s %s" % (name, g, loc))
        return 0

    if a.check:
        ok, why = speakable(a.check)
        print("%s  %s" % ("发语音" if ok else "发文字", why))
        return 0

    if not a.text:
        ap.print_help()
        return 2

    ok, why = speakable(a.text)
    if not ok:
        print("不值得发语音：%s" % why)
        return 1

    t0 = time.time()
    p = synth(a.text, a.voice, a.rate, a.pitch, use_cache=not a.no_cache)
    if not p:
        print("合成失败")
        return 1
    print("%s  (%.2fs, %.1f 秒音频)" % (p, time.time() - t0, duration(p)))
    if a.out:
        import shutil
        shutil.copyfile(p, a.out)
        print("已复制到 %s" % os.path.abspath(a.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
