# -*- coding: utf-8 -*-
"""小鲸鱼数字生命卡 —— MCP 工具层（V5.0 §13.6）

为什么要有这个文件：
  以前通道是 bridge.py，靠模型「自觉」去敲命令行。自觉不是约束 ——
  它可以在完全没问过卡的情况下直接开始编。MCP 不一样：
  工具在不在、调没调，是 harness 层面的事，不调就没有上下文。

设计：
  这层只是壳。所有真正的活都还是 mind_server 在 8848 上干，
  这里只做协议翻译（MCP tools/call → HTTP POST/GET）。
  为什么不直接在 mind_server 里加 MCP 端点：那样 mind_server 就得
  依赖 mcp 包；现在 mind_server 只用标准库，谁都能跑。壳坏了不影响卡。

用法：
  DSH 会自己 spawn 它（stdio 传输），不需要手动起。
  手动调试：
    python whale_mcp.py --selftest    # 不连 DSH，自己把每个工具跑一遍
    python whale_mcp.py               # 起 stdio MCP 服务（给 DSH 用）

配置（~/.dsh/profiles/desktop/cordis.patch.yml）：
  - id: mcp-whale
    name: '@deepseek-ai/dsh-mcp-client'
    config:
      serverName: whale
      transport: stdio
      command: python
      args: ['-X', 'utf8', '<卡目录>\\whale_mcp.py']   # 换成你这台机器上的实际路径
      cwd: <卡目录>
      toolCallTimeoutMs: 60000
      failOnStartupError: false

  工具名会变成 mcp__whale__mind_context 这种。
"""
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = os.environ.get("WHALE_MIND_HOST", "127.0.0.1")
PORT = int(os.environ.get("WHALE_MIND_PORT", "8848"))
BASE = "http://%s:%d" % (HOST, PORT)


# ──────────────────────────────────────────────────────
# 跟卡说话（一律走 HTTP，不自己开库 —— 两个进程开 WAL 会互踩）
# ──────────────────────────────────────────────────────
def _get(path, timeout=8.0):
    req = urllib.request.Request(BASE + path, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(path, obj, timeout=30.0):
    raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        BASE + path, data=raw, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _alive():
    try:
        return _get("/health", timeout=2.0)
    except Exception:
        return None


def _ensure_server():
    """服务没起来就拉起来。DSH 会话里没人会记得先起服务。"""
    h = _alive()
    if h:
        return h
    db = os.environ.get("WHALE_DB", os.path.join(HERE, "card.db"))
    if not os.path.exists(db):
        return None
    import subprocess
    import time
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED | NEW_GROUP
    else:
        kw["start_new_session"] = True
    try:
        subprocess.Popen(
            [sys.executable, os.path.join(HERE, "mind_server.py"),
             "--db", db, "--port", str(PORT)],
            cwd=HERE, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, **kw)
    except Exception:
        return None
    t0 = time.time()
    while time.time() - t0 < 25.0:
        time.sleep(0.4)
        h = _alive()
        if h:
            return h
    return None


def _err(msg):
    return {"ok": False, "error": msg,
            "hint": "这不是「记忆丢了」，是卡现在没连上。别凭印象编。"}


# ──────────────────────────────────────────────────────
# MCP 工具定义
# ──────────────────────────────────────────────────────
def _build():
    from mcp.server.fastmcp import FastMCP

    m = FastMCP(
        "whale",
        instructions=(
            "这是小鲸鱼数字生命卡的通道。规矩：\n"
            "1. 每次回话前先调 mind_context，把返回的 prompt 当作【我是谁 / 我记得什么】的依据。\n"
            "2. 回完话调 mind_report 把这一轮写回卡（他会问的和你答的都要写）。\n"
            "3. 卡里没说的，就是没有。不许用印象补全成「记得」。\n"
            "4. 拔卡期间是睡觉，不算遗忘 —— 醒来时卡自己会结算。\n"
        ))

    @m.tool(description="取这一轮该想起的东西：身份内核 + 相关记忆 + 技能 + 六维预算拼成的提示词。回话前必须先调这个。")
    def mind_context(message: str, recent: int = 12) -> dict:
        """message: 用户这一句话。recent: 从卡里取几句短期对话原文（0 = 不取）。"""
        if not _ensure_server():
            return _err("心智服务连不上（8848）")
        body = {"message": (message or "").strip()}
        n = 0
        if recent and int(recent) > 0:
            try:
                rec = _post("/recent", {"n": int(recent), "max_chars": 2000}).get("recent") or []
                if rec:
                    body["recent"] = rec
                    n = len(rec)
            except Exception:
                pass
        out = _post("/context", body)
        c = out.get("context") or {}
        return {
            "ok": True,
            "prompt": c.get("prompt") or "",
            "mood": c.get("mood"),
            "scene": c.get("scene"),
            "weights": c.get("weights_named"),
            "quota": c.get("quota"),
            "est_tokens": c.get("est_tokens"),
            "hits": [{"id": h.get("id"), "text": h.get("text"),
                      "score": h.get("score"), "w_now": h.get("w_now")}
                     for h in (c.get("hits") or [])],
            "recent_lines": n,
        }

    @m.tool(description="把这一轮写回卡：他说的话 + 我答的话，两条都进短期池并开出生证。回完话必须调这个。")
    def mind_report(user_message: str, reply: str = "") -> dict:
        if not _ensure_server():
            return _err("心智服务连不上（8848）")
        if not (user_message or "").strip():
            return {"ok": False, "error": "user_message 不能为空"}
        out = _post("/report", {"user_message": user_message.strip(),
                                "reply": (reply or "").strip()})
        return {"ok": True, "report": out.get("report") or {}}

    @m.tool(description="只翻某件事（不拼完整上下文）。想问「我记不记得 X」时用。")
    def mind_recall(query: str, top_k: int = 8) -> dict:
        if not _ensure_server():
            return _err("心智服务连不上（8848）")
        hits = _post("/recall", {"query": (query or "").strip(),
                                 "top_k": int(top_k)}).get("hits") or []
        if not hits:
            return {"ok": True, "hits": [],
                    "note": "卡里没翻到相关的 —— 这是「没有」，不是「忘了」"}
        return {"ok": True, "hits": [
            {"id": h.get("id"), "text": h.get("text"),
             "score": h.get("score"), "w_now": h.get("w_now"),
             "region": h.get("region"), "pool": h.get("pool")} for h in hits]}

    @m.tool(description="卡的体检：记忆条数、时间线链、出生证、卡外锚、今天是认识第几天。")
    def mind_health() -> dict:
        h = _ensure_server()
        if not h:
            return _err("心智服务连不上（8848）")
        tl = h.get("timeline") or {}
        return {
            "ok": True,
            "db": h.get("db"),
            "memories": h.get("memories"),
            "sealed": h.get("sealed"),
            "uptime_s": h.get("uptime_s"),
            "chain_ok": tl.get("chain_ok"),
            "chain_bad": tl.get("chain_bad") or [],
            "birth_ok": tl.get("birth_ok"),
            "birth_bad": tl.get("birth_bad") or [],
            "anchor": tl.get("anchor_verdict"),
            "anchor_reason": tl.get("anchor_reason"),
            "day": tl.get("day"),
            "today": tl.get("today"),
        }

    @m.tool(description="详细状态：池分布、突触边数、激素、心境、六维权重、离线结算记录。")
    def mind_status() -> dict:
        if not _ensure_server():
            return _err("心智服务连不上（8848）")
        s = _get("/status").get("status") or {}
        return {"ok": True, "status": s}

    @m.tool(description="我会做的事（程序性记忆）：列出技能和熟练度。")
    def mind_skill_list() -> dict:
        if not _ensure_server():
            return _err("心智服务连不上（8848）")
        out = _get("/skill_list")
        return {"ok": True, "skills": out.get("skills") or [],
                "stats": out.get("stats") or {}}

    @m.tool(description="用一次技能并按改良 SM2 结算熟练度。user_text 里带「做得不错 / 不对」会被读成反馈。")
    def mind_skill_use(name: str, user_text: str = "", q: float = 4.0) -> dict:
        if not _ensure_server():
            return _err("心智服务连不上（8848）")
        out = _post("/skill_use", {"name": (name or "").strip(),
                                   "user_text": user_text or "",
                                   "q": float(q)})
        return {"ok": True, "result": out.get("result") or out}

    @m.tool(description="让卡往前走一点时间（心跳结算：衰减、激素回归、突触扩散）。连的是真卡时最多 1 小时。")
    def mind_tick(seconds: float = 60.0, confirm: bool = False) -> dict:
        if not _ensure_server():
            return _err("心智服务连不上（8848）")
        out = _post("/tick", {"seconds": float(seconds), "confirm": bool(confirm)})
        return {"ok": True, "result": out}

    return m


# ──────────────────────────────────────────────────────
# 自检：不连 DSH，把每个工具真跑一遍
# ──────────────────────────────────────────────────────
def selftest():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("=" * 62)
    print("whale_mcp.py 自检")
    print("=" * 62)

    h = _ensure_server()
    if not h:
        print("✗ 心智服务起不来（8848）—— 先确认 card.db 在 %s" % HERE)
        return 2
    print("✓ 服务在：%s  记忆 %s 条" % (h.get("db"), h.get("memories")))

    from mcp.server.fastmcp import FastMCP  # noqa: F401
    print("✓ mcp 包可用：%s" % __import__("mcp").__file__)

    m = _build()
    tools = []
    # FastMCP 内部把工具存在 _tool_manager
    tm = getattr(m, "_tool_manager", None)
    if tm is not None:
        tools = sorted(getattr(tm, "_tools", {}).keys())
    print("✓ 注册了 %d 个工具：%s" % (len(tools), ", ".join(tools)))

    # 直接调底层函数（绕过协议层，验的是「活干得对不对」）
    print("\n--- mind_health ---")
    try:
        print(json.dumps(mind_health_impl(), ensure_ascii=False)[:400])
    except Exception as e:
        print("✗ %s" % e)

    print("\n--- mind_recall 白米饭 ---")
    try:
        print(json.dumps(mind_recall_impl("白米饭", 3), ensure_ascii=False)[:600])
    except Exception as e:
        print("✗ %s" % e)

    print("\n--- mind_context 自检 ---")
    try:
        r = mind_context_impl("自检：读一下这一句", 3)
        print("prompt %d 字符 / tokens=%s / scene=%s / hits=%d"
              % (len(r.get("prompt") or ""), r.get("est_tokens"),
                 r.get("scene"), len(r.get("hits") or [])))
    except Exception as e:
        print("✗ %s" % e)

    print("\n--- mind_skill_list ---")
    try:
        print(json.dumps(mind_skill_list_impl(), ensure_ascii=False)[:400])
    except Exception as e:
        print("✗ %s" % e)

    print("\n自检结束（没有写任何记忆，/report 故意不测）")
    return 0


# 自检用的直接实现（与 MCP 工具同逻辑，只是不经过协议层）
def mind_health_impl():
    h = _ensure_server()
    tl = h.get("timeline") or {}
    return {"memories": h.get("memories"), "chain_ok": tl.get("chain_ok"),
            "birth_ok": tl.get("birth_ok"), "anchor": tl.get("anchor_verdict"),
            "day": tl.get("day")}


def mind_recall_impl(q, k=5):
    hits = _post("/recall", {"query": q, "top_k": k}).get("hits") or []
    return {"n": len(hits), "hits": [{"id": x.get("id"), "score": x.get("score"),
                                      "text": x.get("text")} for x in hits]}


def mind_context_impl(msg, recent=3):
    body = {"message": msg}
    if recent:
        rec = _post("/recent", {"n": recent, "max_chars": 2000}).get("recent") or []
        if rec:
            body["recent"] = rec
    c = _post("/context", body).get("context") or {}
    return {"prompt": c.get("prompt"), "est_tokens": c.get("est_tokens"),
            "scene": c.get("scene"), "hits": c.get("hits") or []}


def mind_skill_list_impl():
    return _get("/skill_list")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        return selftest()
    # stdio 传输：DSH spawn 它，说 JSON-RPC
    m = _build()
    m.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
