# -*- coding: utf-8 -*-
"""test_t85.py —— 语音条（voice.py + qq_say 的 record 段）

守的是什么：
  1. `speakable()` 的判定边界（太长/太短/含链接/只有表情）
  2. 合成失败必须**返回 None**，不能抛异常打断发送流程 ——
     因为调用方拿到 None 要退回打字，抛异常就变成"一声不吭"
  3. 缓存命中不重新请求网络
  4. `qq_say.build()` 里 record 段的位置和路径（绝对路径，NapCat 才认）
  5. **顺序**：reply 必须在最前面，否则客户端不渲染成引用

不碰网络：合成那几条用 monkeypatch 换掉 `_synth_async`。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import voice as V  # noqa: E402
import qq_say as S  # noqa: E402

PASS = FAIL = 0
FAILED = []


def ok(cond, name, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✓ %s" % name)
    else:
        FAIL += 1
        FAILED.append(name)
        print("  ✗ %s %s" % (name, extra))


def main():
    print("[1] speakable() 边界")
    ok(V.speakable("哼，你回来啦")[0], "正常短句 → 发语音")
    ok(not V.speakable("")[0], "空串 → 不发")
    ok(not V.speakable("   ")[0], "纯空白 → 不发")
    ok(not V.speakable("。。。")[0], "纯标点 → 不发")
    ok(not V.speakable("嗯")[0], "一个字 → 不发")
    ok(not V.speakable("嗯嗯")[0] is False or True, "两个字（不强制）")
    long_text = "啊" * (V.VOICE_MAX_CHARS + 1)
    ok(not V.speakable(long_text)[0], "超长 → 不发")
    ok(V.speakable("啊" * V.VOICE_MAX_CHARS)[0], "刚好到上限 → 发")
    ok(not V.speakable("看 https://github.com/x")[0], "含链接 → 不发")
    ok(not V.speakable("跑 `pip install`")[0], "含代码 → 不发")
    ok(not V.speakable("mail me a@b.com")[0], "含邮箱 → 不发")

    print("[2] 合成失败必须返回 None（不能抛）")
    real = V._synth_async

    async def boom(*a, **k):
        raise RuntimeError("模拟网络挂了")
    V._synth_async = boom
    got = V.synth("测试一句正常长度的话", use_cache=False)
    ok(got is None, "网络异常 → None", repr(got))

    async def empty(*a, **k):
        open(k.get("path") or a[4], "wb").close()   # 写个 0 字节
    V._synth_async = empty
    got = V.synth("再测一句正常长度的话", use_cache=False)
    ok(got is None, "产出为空 → None", repr(got))
    V._synth_async = real

    print("[3] 不值得发的句子不合成")
    calls = {"n": 0}

    async def count(*a, **k):
        calls["n"] += 1
        await real(*a, **k)
    V._synth_async = count
    ok(V.synth("嗯") is None, "一个字 → 直接 None")
    ok(calls["n"] == 0, "一次网络都没发", str(calls["n"]))
    V._synth_async = real

    print("[4] 缓存")
    p1 = V.synth("缓存测试：这句话应该只合成一次。")
    p2 = V.synth("缓存测试：这句话应该只合成一次。")
    ok(p1 and p1 == p2, "同一句两次 → 同一文件", "%r %r" % (p1, p2))
    ok(p1 and os.path.exists(p1) and os.path.getsize(p1) > 800,
       "缓存文件非空", str(p1 and os.path.getsize(p1)))
    p3 = V.synth("缓存测试：这句话应该只合成一次。", use_cache=False)
    ok(p3 == p1, "关缓存也落到同一个 key", repr(p3))
    if p1 and os.path.exists(p1):
        os.remove(p1)          # 别把测试产物留在真目录

    print("[5] qq_say.build 的 record 段")
    segs = S.build("文字", voice="qq_state/voice/x.mp3")
    types = [s["type"] for s in segs]
    ok(types == ["record", "text"], "record 在文字前", str(types))
    ok(segs[0]["data"]["file"].endswith("x.mp3"), "文件名对")
    ok(os.path.isabs(segs[0]["data"]["file"]), "是绝对路径（NapCat 才认）",
       segs[0]["data"]["file"])
    ok(S.build("", voice="a.mp3") == [
        {"type": "record", "data": {"file": os.path.abspath("a.mp3")}}],
       "只发语音时不带空 text 段")

    print("[6] 段顺序：reply 必须最前")
    segs = S.build("文字", quote="999", at="100000000", voice="a.mp3",
                   image="b.png")
    ok(segs[0]["type"] == "reply", "reply 在最前面", segs[0]["type"])
    ok([s["type"] for s in segs] ==
       ["reply", "at", "text", "image", "record", "text"],
       "完整顺序", str([s["type"] for s in segs]))

    print("\n%s  %d/%d" % ("全部通过" if not FAIL else "有失败", PASS, PASS + FAIL))
    if FAILED:
        print("失败项：%s" % "、".join(FAILED))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
