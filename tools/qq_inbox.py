# qq_inbox.py —— 收件箱：列出「还没回」的消息、标记已回（2026-11-12）
import argparse, json, os, time

BRIDGE = (os.environ.get("WHALE_QQ_BRIDGE")
          or os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                          "deploy", "qq_bridge"))
INBOX = os.path.join(BRIDGE, "inbox.jsonl")
REPLIED = os.path.join(BRIDGE, "replied_ids.txt")


def _replied():
    try:
        with open(REPLIED, "r", encoding="utf-8") as f:
            return {int(x.strip()) for x in f if x.strip().isdigit()}
    except FileNotFoundError:
        return set()
    except Exception as e:
        print("[qq_inbox] 已回账本读不动，按空：%s" % e)
        return set()


def mark(mid):
    try:
        with open(REPLIED, "a", encoding="utf-8") as f:
            f.write("%d\n" % int(mid))
        return True
    except Exception as e:
        print("[qq_inbox] 标记失败：%s" % e)
        return False


def load(limit=0):
    out, done = [], _replied()
    try:
        with open(INBOX, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception as e:
                    print("[qq_inbox] 坏行跳过：%s" % e)
                    continue
                mid = int(d.get("message_id") or 0)
                d["_mid"] = mid
                d["_replied"] = mid in done
                out.append(d)
    except FileNotFoundError:
        return []
    out.sort(key=lambda d: float(d.get("time") or 0))   # inbox 是追加写，但补拉历史时顺序会乱
    if limit:
        out = out[-limit:]
    return out


def fmt(d):
    t = time.strftime("%m-%d %H:%M", time.localtime(float(d.get("time") or 0)))
    where = "群%s" % d.get("peer") if d.get("group") else "私聊"
    flag = "✓" if d["_replied"] else "·"
    txt = (d.get("text") or "").replace("\n", " ")[:70]
    q = ""
    if d.get("quote_text"):
        q = " ↪「%s」" % str(d["quote_text"])[:24]
    return "%s %s %-10s %s %s%s  #%d" % (flag, t, (d.get("sender") or "")[:10], where, txt, q, d["_mid"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="列出未回的（默认）")
    ap.add_argument("--all", action="store_true", help="连已回的也列")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--mark", type=int, nargs="+", help="把这些 message_id 标成已回")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()
    if a.mark:
        ok = [m for m in a.mark if mark(m)]
        print("已标记已回：%s" % ok)
        return 0
    rows = load()
    if a.stats:
        n = len(rows)
        r = sum(1 for x in rows if x["_replied"])
        print("收件箱 %d 条：已回 %d / 未回 %d" % (n, r, n - r))
        return 0
    rows = [x for x in rows if a.all or not x["_replied"]][-a.limit:]
    rows.sort(key=lambda d: float(d.get("time") or 0))
    if not rows:
        print("（没有待回的）")
        return 0
    for d in rows:
        print(fmt(d))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
