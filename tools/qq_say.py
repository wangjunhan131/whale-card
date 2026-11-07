# -*- coding: utf-8 -*-
"""在群里说一句：能引用、能带图、能@。

以前只会发纯文字，别人问"你在回哪句"就得自己猜。OneBot 的
`reply` 段就是干这个的 —— 把它放在消息数组最前面，客户端会显示成引用。

用法：
  python tools/qq_say.py <群号> "文字"
  python tools/qq_say.py <群号> "文字" --quote 1234567890
  python tools/qq_say.py <群号> --image qq_state/memes/zai.png
  python tools/qq_say.py <群号> "看这个" --image <path> --quote <mid>
  python tools/qq_say.py <群号> "文字" --at 100000000
  python tools/qq_say.py <群号> --voice "哼，你回来啦"        # 合成并只发语音条
  python tools/qq_say.py <群号> "文字" --voice "哼，你回来啦"  # 语音 + 文字
  python tools/qq_say.py <群号> --voice-file a.mp3

规矩（跟 qq_guard 一致）：
  · 发送前按 human_gap() 等一个随机时长
  · 发完写回卡（POST /remember，source=qq-group）
  · 不刷屏：一轮最多一两句
  · **语音要在发送前决定**（文字发出去撤不回来）；合成失败自动退回打字
"""
import argparse
# 直接跑脚本时 sys.path[0] 是 tools/，卡根目录不在里面 → chatcfg import 失败、
# 设置被静默忽略（2026-11-06 实测：脚本模式下「上学时段」不生效）。补一句根目录。
import os as _os
import sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
import json
import os
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:6700"
TOKEN = "qqai-dsh-http-2026"
CARD_API = "http://127.0.0.1:8848/remember"

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
import qq_guard as G  # noqa: E402


def call(action, **params):
    req = urllib.request.Request(
        BASE + "/" + action,
        data=json.dumps(params).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + TOKEN})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def _talk_cfg():
    """说话那几项从控制台那张表读；读不到就按老样子发。"""
    try:
        import chatcfg as _cc
        return _cc.load_cfg()
    except Exception:
        return {}


def polish(text):
    """发出去之前按设置清一遍：句号（真人聊天不关门）、波浪号（AI 演可爱）。"""
    c = _talk_cfg()
    t = text or ""
    # 先去掉波浪号，否则「好累。～」末尾不是句号、清不掉
    if c.get("talk.no_wave", True):
        t = t.replace("～", "").replace("~", "")
    if c.get("talk.no_period", True):
        t = t.rstrip()
        while t.endswith(("。", ".")):
            t = t[:-1].rstrip()
    return t


SPLIT_MARKS = "。！？；，、…\n"


def split_text(text, max_chars=30, do_split=True):
    """一条最多几个字 / 太长就拆成几条（控制台那两项）。

    2026-11-06 两处修：①别把英文单词劈成两半（优先在空格处断）
    ②短块并回上一块时，前一块已经有标点就不再补逗号（原来会出「，，」）。
    """
    t = (text or "").strip()
    if not t:
        return []
    try:
        max_chars = int(max_chars)
    except Exception:
        max_chars = 30
    if max_chars < 1:
        max_chars = 1
    if not do_split:
        return [t] if len(t) <= max_chars else [t[:max_chars] + "…"]

    out, buf = [], ""
    for ch in t:
        buf += ch
        hard = len(buf) >= max_chars
        soft = ch in SPLIT_MARKS and len(buf) >= max(12, int(max_chars * 0.55))
        if not (hard or soft):
            continue
        piece = buf
        # 撞到上限时，先在最近 10 个字里找个空格断开，别把单词劈开
        if hard and ch not in SPLIT_MARKS:
            cut = max(piece.rfind(" "), piece.rfind("，"), piece.rfind("、"))
            if cut >= max(10, len(piece) - 10):
                piece, buf = piece[:cut + 1], piece[cut + 1:]
            else:
                buf = ""
        else:
            buf = ""
        if piece.strip():
            out.append(piece.strip())
    if buf.strip():
        out.append(buf.strip())

    merged = []
    for piece in out:
        if merged and len(piece) < max(8, max_chars // 2) and len(merged[-1]) + len(piece) <= max_chars:
            tail = merged[-1]
            join = "" if tail[-1] in SPLIT_MARKS else "，"
            merged[-1] = tail + join + piece
        else:
            merged.append(piece)
    return merged


def build(text="", quote=None, image=None, at=None, voice=None):
    """拼 OneBot 消息数组。顺序有讲究：reply 必须在最前面，
    不然客户端不会渲染成引用（实测放后面就只是一段文字）。

    voice 给了就发 `record` 段。NapCat 收到非 silk 文件时会用自带的
    ffmpeg addon 转成 silk 再发（`convertToNTSilkTct`），所以这里
    直接给 mp3 路径就行，不用自己转码。
    """
    segs = []
    if quote:
        segs.append({"type": "reply", "data": {"id": str(quote)}})
    if at:
        segs.append({"type": "at", "data": {"qq": str(at)}})
        segs.append({"type": "text", "data": {"text": " "}})
    if image:
        # NapCat 认本地绝对路径；用 file:// 有时会被当 URL 去下载
        segs.append({"type": "image", "data": {"file": os.path.abspath(image)}})
    if voice:
        segs.append({"type": "record", "data": {"file": os.path.abspath(voice)}})
    if text:
        segs.append({"type": "text", "data": {"text": polish(text)}})
    return segs


def remember(text, kind="text"):
    try:
        req = urllib.request.Request(
            CARD_API,
            data=json.dumps({"text": "在群里说：「%s」" % text,
                             "source": "qq-group", "speaker": "self",
                             "region": "episode"}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8")).get("mem_id")
    except Exception as e:
        print("  ⚠ 写回失败（不阻断发送）：%s" % e)
        return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("gid", type=str, help="群号；私聊写 u<QQ号>（如 u100000000）")
    ap.add_argument("text", nargs="?", default="")
    ap.add_argument("--quote", default="", help="要引用的 message_id")
    ap.add_argument("--image", default="", help="本地图片路径")
    ap.add_argument("--at", default="", help="@ 谁（QQ 号）")
    ap.add_argument("--voice", default="", help="要发语音的那句话（自己合成）；"
                    "给了 --voice 时 text 只作为语音失败时的兜底文字")
    ap.add_argument("--voice-file", default="", help="直接发这个音频文件")
    ap.add_argument("--no-wait", action="store_true", help="不等随机间隔（测试用）")
    a = ap.parse_args(argv)

    if not a.text and not a.image and not a.voice and not a.voice_file:
        print("要么给文字，要么给 --image / --voice / --voice-file")
        return 2

    # ── 先决定发不发语音，再发 ────────────────────────────────────
    # 顺序很重要：文字发出去撤不回来，所以"改主意"必须在发送之前。
    # 语音任何一步失败都退回打字（V 会保持 None）。
    vpath = None
    _c = _talk_cfg()
    _voice_on = bool(_c.get("voice.enabled", True))
    if (a.voice or a.voice_file) and not _voice_on:
        print("→ 设置里关了语音（voice.enabled=false），这次只发文字")
        a.voice, a.voice_file = "", ""
    if a.voice_file:
        if not os.path.exists(a.voice_file):
            print("⚠ 语音文件不存在，退回打字：%s" % a.voice_file)
        else:
            vpath = os.path.abspath(a.voice_file)
    elif a.voice:
        import voice as V
        ok, why = V.speakable(a.voice)
        if not ok:
            print("→ 发文字（%s）" % why)
        else:
            t0 = time.time()
            vpath = V.synth(a.voice)
            if vpath:
                print("→ 发语音 %.2fs 合成：%s" % (time.time() - t0, vpath))
            else:
                print("→ 合成失败，退回打字")

    if vpath and not a.text:
        a.text = ""          # 只发语音条，不补文字
    # 一条最多几个字 / 太长就拆成几条：文字才拆，图片语音不拆
    _c = _talk_cfg()
    chunks = split_text(a.text,
                        _c.get("talk.max_chars", 30),
                        bool(_c.get("talk.split", True))) if a.text else []
    if not chunks:
        chunks = [""]

    _to = str(a.gid).strip()
    _uid = int(_to[1:]) if _to[:1].lower() in ("u", "p") else None
    mids = []
    for _i, _chunk in enumerate(chunks):
        segs = build(_chunk, a.quote if _i == 0 else None, a.image if _i == 0 else None,
                     a.at if _i == 0 else None, vpath if _i == 0 else None)
        print("消息段：%s" % json.dumps(segs, ensure_ascii=False))
        gap = 0 if a.no_wait else G.human_gap(_chunk or "语音")
        if _i > 0 and not a.no_wait:
            gap = max(2.0, gap * 0.4)   # 拆开的两条之间别等太久
        if gap:
            print("等 %.1f 秒…" % gap)
            time.sleep(gap)
        if _uid:
            r = call("send_private_msg", user_id=_uid, message=segs)
        else:
            r = call("send_group_msg", group_id=int(_to), message=segs)
        _mid = (r.get("data") or {}).get("message_id")
        if not _mid:
            print("⚠ 发送失败，没拿到 message_id：%s" % json.dumps(r, ensure_ascii=False)[:300])
            return 1
        print("发出 message_id =", _mid)
        mids.append(_mid)
    mid = mids[0] if mids else None

    if a.text:
        print("写回 mem_id =", remember(a.text))
    elif vpath:
        print("写回 mem_id =", remember("[语音] %s" % (a.voice or os.path.basename(vpath))))
    else:
        print("写回 mem_id =", remember("[图片] %s" % os.path.basename(a.image)))

    # 自己发的也算已回，别让它挂在未回里
    if mid:
        try:
            G.mark_many([mid], G.ST_REPLIED, by="self")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
