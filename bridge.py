# -*- coding: utf-8 -*-
"""桥 —— 让任何一个 DSH 会话能跟卡说话（V5.0 通道层）

为什么要有这个文件：
  DSH 没有「从外部注入人格」的接口。它每次开新会话只认一个文件：
  $DSH_HOME/AGENTS.md。所以卡槽的做法是——把 AGENTS.md 换成一段
  「你只是通道，去问卡」的规矩，规矩里写明怎么调这个脚本。

  以前 DSH 里的人设是**写在 AGENTS.md 里的字**，拔了卡照样能演；
  现在人格在卡里，AGENTS.md 里只剩「去哪找」。这是两回事。

用法（规矩文件里也是这么写的）：
    python bridge.py ask "用户这句话"      → 打印该想起的提示词
    python bridge.py report "他说" "我答"  → 把这一轮写回卡
    python bridge.py health                → 卡在不在 / 链断没断 / 锚对不对
    python bridge.py status                → 详细状态
    python bridge.py recall "关键词"        → 只翻某件事

设计取舍：
  - 一律走 mind_server 的 HTTP，不自己开库。
    两个进程同时开 WAL 库，一边写一边读会互相踩；服务端有锁，让它一个人管。
  - 服务没起来就**自己拉起来**，等它 /health 通。DSH 会话里没人会记得先起服务。
  - 输出一律 UTF-8。Windows 控制台默认 GBK，中文会 UnicodeEncodeError。
"""
import io
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = os.environ.get("WHALE_MIND_HOST", "127.0.0.1")
PORT = int(os.environ.get("WHALE_MIND_PORT", "8848"))
BASE = "http://%s:%d" % (HOST, PORT)
START_TIMEOUT = 25.0


# ──────────────────────────────────────────────────────
# 控制台编码：Windows 上不设这个，打印中文会炸
# ──────────────────────────────────────────────────────
def _fix_stdout():
    for name in ("stdout", "stderr"):
        s = getattr(sys, name, None)
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _api_token():
    """读本机 API token（V7 第 17 件）。

    为什么要它：服务端「没带 token 的调用方，自报 source 一律降级成
    unverified:xxx」。bridge 是本地可信进程，带上 token 才不至于让
    「他自己说的话」在库里被标成来路不明。
    token 由服务端首启自动生成：<卡目录>/qq_state/api_token.txt。
    """
    cand = []
    if os.environ.get("WHALE_CARD_DIR"):
        cand.append(os.environ["WHALE_CARD_DIR"])
    cand.append(os.getcwd())
    cand.append(os.path.dirname(os.path.abspath(__file__)))
    for root in cand:
        p = os.path.join(root, "qq_state", "api_token.txt")
        try:
            t = io.open(p, encoding="utf-8").read().strip()
        except OSError:
            continue
        if t:
            return t
    return ""


def _headers(extra=None):
    h = {"Content-Type": "application/json; charset=utf-8"}
    t = _api_token()
    if t:
        h["X-Whale-Token"] = t
    if extra:
        h.update(extra)
    return h


def _get(path, timeout=5.0):
    req = urllib.request.Request(BASE + path, method="GET",
                                 headers=_headers())
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(path, obj, timeout=30.0):
    raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        BASE + path, data=raw, method="POST", headers=_headers())
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def alive():
    """服务在不在。返回 /health 的 dict，不在返回 None。"""
    try:
        return _get("/health", timeout=2.0)
    except Exception:
        return None


def ensure_server(quiet=False):
    """确保服务在跑。已经通了就直接返回；不通就拉起来再等。

    返回 (ok, health_or_None, note)
    """
    h = alive()
    if h:
        return True, h, "已在运行"

    db = os.environ.get("WHALE_DB", os.path.join(HERE, "card.db"))
    if not os.path.exists(db):
        return False, None, "卡文件不存在：%s" % db

    # ⚠ 模型必须跟卡里向量一致：卡里存的是 768 维（bge-base）。
    # 不显式指定的话 embed_np 默认加载 bge-small（512 维），
    # 查出来的向量和存的对不上，余弦相似度会静默算错 —— 不报错、只是变差。
    env = dict(os.environ)
    env.setdefault("WHALE_EMBED_MODEL", "bge-base-zh-v1.5")
    env.setdefault("OPENBLAS_NUM_THREADS", "1")
    env.setdefault("OMP_NUM_THREADS", "1")
    env.setdefault("MKL_NUM_THREADS", "1")

    if not quiet:
        print("[bridge] 服务没在跑，正在拉起…", file=sys.stderr)
    try:
        # 分离启动：DSH 会话退出不该把服务带走
        kw = {}
        if os.name == "nt":
            kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED | NEW_GROUP
        else:
            kw["start_new_session"] = True
        subprocess.Popen(
            [sys.executable, os.path.join(HERE, "mind_server.py"),
             "--db", db, "--port", str(PORT)],
            cwd=HERE, env=env, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, **kw)
    except Exception as e:
        return False, None, "拉起失败：%s" % e

    t0 = time.time()
    while time.time() - t0 < START_TIMEOUT:
        time.sleep(0.4)
        h = alive()
        if h:
            return True, h, "刚拉起（%.1fs）" % (time.time() - t0)
    return False, None, "拉起后 %.0fs 内 /health 仍不通" % START_TIMEOUT


def _need_server():
    ok, h, note = ensure_server()
    if not ok:
        print("卡没插好：%s" % note, file=sys.stderr)
        print("（不是记忆丢了，是卡现在没连上。别凭印象编。）", file=sys.stderr)
        sys.exit(2)
    return h


# ──────────────────────────────────────────────────────
# 四条命令
# ──────────────────────────────────────────────────────
def cmd_ask(argv):
    if not argv:
        print("用法：bridge.py ask \"用户这句话\" [--norecent]", file=sys.stderr)
        return 2
    use_recent = "--norecent" not in argv
    argv = [a for a in argv if a != "--norecent"]
    msg = " ".join(argv).strip()
    if not msg:
        print("消息为空", file=sys.stderr)
        return 2
    _need_server()
    body = {"message": msg}
    # 短期对话原文从卡里取 —— 不是从调用方带。
    # 换台电脑、换个会话，卡里那段还是同一段。
    n_recent = 0
    if use_recent:
        try:
            rec = _post("/recent", {"n": 12, "max_chars": 2000}).get("recent") or []
            if rec:
                body["recent"] = rec
                n_recent = len(rec)
        except Exception:
            pass
    out = _post("/context", body)
    c = out.get("context") or {}
    prompt = c.get("prompt") or ""
    print(prompt)
    # 一行小字到 stderr，不污染提示词本体
    hits = c.get("hits") or []
    print("[bridge] 翻出 %d 条 / 卡里近 %d 句 / 心境 %s / %d tokens"
          % (len(hits), n_recent, c.get("mood") or "?", c.get("est_tokens") or 0),
          file=sys.stderr)
    return 0


def cmd_report(argv):
    if len(argv) < 2:
        print("用法：bridge.py report \"他说的话\" \"我答的话\"", file=sys.stderr)
        return 2
    user_msg, reply = argv[0].strip(), argv[1].strip()
    if not user_msg:
        print("他说的话不能为空", file=sys.stderr)
        return 2
    _need_server()
    out = _post("/report", {"user_message": user_msg, "reply": reply})
    rep = out.get("report") or {}
    print("[bridge] 已写回卡：mem_id=%s" % rep.get("mem_id"), file=sys.stderr)
    return 0


def cmd_health(argv):
    ok, h, note = ensure_server()
    if not ok:
        print("✗ 卡没连上：%s" % note)
        return 2
    tl = h.get("timeline") or {}
    print("✓ 卡在：%s" % h.get("db"))
    print("  记忆 %s 条 / 盖章 %s / 已活 %s 秒"
          % (h.get("memories"), "是" if h.get("sealed") else "否",
             h.get("uptime_s")))
    print("  链 %s / 出生证 %s / 锚 %s"
          % ("完好" if tl.get("chain_ok") else "断了",
             "完好" if tl.get("birth_ok") else "断了",
             tl.get("anchor_verdict")))
    print("  %s" % (tl.get("today") or ""))
    if note != "已在运行":
        print("  （%s）" % note)
    return 0


def cmd_status(argv):
    _need_server()
    s = _get("/status").get("status") or {}
    st = s.get("stats") or {}
    tl = s.get("timeline") or {}
    print("记忆 %s / 细节 %s / 心境 %s"
          % (st.get("total"), st.get("details"), s.get("mood")))
    print("激素：%s" % json.dumps(s.get("hormones") or {}, ensure_ascii=False))
    print("时间线：%s 条 / 第 %s 天" % (tl.get("ticks"), tl.get("day")))
    print("锚：%s — %s" % ((tl.get("anchor") or {}).get("verdict"),
                           (tl.get("anchor") or {}).get("reason")))
    bad = (tl.get("chain_bad") or []) + (tl.get("birth_bad") or [])
    for b in bad:
        print("  ! %s" % b)
    return 0


def cmd_recall(argv):
    if not argv:
        print("用法：bridge.py recall \"关键词\"", file=sys.stderr)
        return 2
    q = " ".join(argv).strip()
    _need_server()
    hits = _post("/recall", {"query": q, "top_k": 8}).get("hits") or []
    if not hits:
        print("卡里没翻到相关的（这是「没有」，不是「忘了」）")
        return 0
    for h in hits:
        print("%.4f  w=%.3f  #%s  %s"
              % (h.get("score") or 0, h.get("w_now") or 0,
                 h.get("id"), h.get("text")))
    return 0


def cmd_remodel(argv):
    """把维度对不上的老向量用当前模型重编码（换过模型之后跑一次）"""
    _need_server()
    out = _post("/emb/remodel", {}, timeout=3600.0)
    if not out.get("ok"):
        print("重编码失败：%s" % out, file=sys.stderr)
        return 1
    print("记忆：编码 %d 条，跳过 %d，失败 %d"
          % (out["mem"]["done"], out["mem"]["skip"], out["mem"]["fail"]))
    print("片段：编码 %d 条，跳过 %d，失败 %d"
          % (out["detail"]["done"], out["detail"]["skip"],
             out["detail"]["fail"]))
    print("主干预热：%s 条" % out.get("core_warm"))
    b, a = out.get("before") or {}, out.get("after") or {}
    print("可用比例：%s → %s" % (b.get("usable_ratio"), a.get("usable_ratio")))
    print("维度分布：%s → %s" % (b.get("emb_dims"), a.get("emb_dims")))
    if a.get("warn"):
        print("⚠️  %s" % a["warn"])
    return 0


def cmd_note(argv):
    """任意通道写一条 —— QQ/B站/抖音/控制台都能用。

    和 report 的区别：report 是「一问一答」，note 是「发生了什么/我说了什么」。
    以前没有这个入口，别的通道听见了也写不进卡。
    """
    import argparse
    ap = argparse.ArgumentParser(prog="bridge.py note")
    ap.add_argument("text", nargs="+", help="要记下来的那句话")
    ap.add_argument("--source", default="cli",
                    help="从哪条通道来的（qq/qq-group/bili/douyin/console…）")
    ap.add_argument("--salience", type=float, default=0.55)
    ap.add_argument("--speaker", default="self")
    ap.add_argument("--region", default=None)
    args = ap.parse_args(argv)
    tx = " ".join(args.text).strip()
    if not tx:
        print("要记的话不能为空", file=sys.stderr)
        return 2
    _need_server()
    body = {"text": tx, "source": args.source, "salience": args.salience,
            "speaker": args.speaker}
    if args.region:
        body["region"] = args.region
    out = _post("/remember", body)
    print("[bridge] 已写回卡：mem_id=%s source=%s"
          % (out.get("mem_id"), out.get("source")), file=sys.stderr)
    return 0


def cmd_log(argv):
    """工作日志（那张「桌子」）：不带参数看最近几条，带参数就写一条。

    用法：
        python bridge.py log                      → 最近 8 条
        python bridge.py log "干了什么" "怎么干的/看到什么数" "下一步"
        python bridge.py log --kind bug "标题" "正文"

    为什么要它：卡记的是「我们之间发生过什么」，记不住「我上一轮改到第几行」。
    换窗口/换电脑之后，新开的那一轮先看这张表，就知道上一轮干到哪、为什么这么干。
    """
    import argparse
    ap = argparse.ArgumentParser(prog="bridge.py log")
    ap.add_argument("title", nargs="?", default=None, help="一句话：干了什么")
    ap.add_argument("body", nargs="?", default="", help="怎么干的 / 看到什么数")
    ap.add_argument("next_step", nargs="?", default="", help="下一步")
    ap.add_argument("--kind", default="work", help="work / bug / decision / note")
    ap.add_argument("--n", type=int, default=8, help="不带参数时列几条")
    args = ap.parse_args(argv)
    _need_server()
    if not args.title:
        out = _get("/worklog?n=%d" % max(1, args.n))
        items = out.get("items") or []
        if not items:
            print("工作日志还是空的。")
            return 0
        for it in items:
            print("%s  [%s] %s" % (time.strftime("%m-%d %H:%M",
                                                 time.localtime(float(it.get("ts") or 0))),
                                   it.get("kind") or "work", it.get("title") or ""))
            if it.get("body"):
                print("      %s" % it["body"])
            if it.get("next_step"):
                print("      → %s" % it["next_step"])
        return 0
    out = _post("/worklog", {"title": args.title, "body": args.body,
                             "next_step": args.next_step, "kind": args.kind})
    print("[bridge] 日志已记：id=%s" % out.get("id"), file=sys.stderr)
    return 0


CMDS = {
    "ask": cmd_ask,
    "report": cmd_report,
    "note": cmd_note,
    "log": cmd_log,
    "health": cmd_health,
    "status": cmd_status,
    "recall": cmd_recall,
    "remodel": cmd_remodel,
}


def main(argv=None):
    _fix_stdout()
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    fn = CMDS.get(argv[0])
    if fn is None:
        print("不认识的命令：%s" % argv[0], file=sys.stderr)
        print("可用：%s" % " / ".join(CMDS), file=sys.stderr)
        return 2
    try:
        return fn(argv[1:])
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")
        except Exception:
            pass
        print("卡返回错误 %s：%s" % (e.code, body), file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print("连不上卡：%s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
