# qq_meme.py —— 收表情包、发表情包（2026-11-12）
import argparse, hashlib, html, json, os, re, time, urllib.request

BASE = os.environ.get("WHALE_QQ_API", "http://127.0.0.1:6700")
TOKEN = os.environ.get("WHALE_QQ_TOKEN", "qqai-dsh-http-2026")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INBOX = os.path.join(ROOT, "deploy", "qq_bridge", "inbox.jsonl")
STORE = os.path.join(ROOT, "qq_state", "memes")
INDEX = os.path.join(STORE, "index.jsonl")
CQ_IMG = re.compile(r"\[CQ:image,([^\]]*)\]")


def api(name, body=None, timeout=25):
    req = urllib.request.Request(BASE + "/" + name, data=json.dumps(body or {}).encode(),
                                 headers={"Authorization": "Bearer " + TOKEN,
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _fields(raw):
    d = {}
    for part in raw.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            d[k.strip()] = v.strip()
    return d


def scan(limit=400, group=0, dry=False):
    os.makedirs(STORE, exist_ok=True)
    seen = set()
    if os.path.exists(INDEX):
        with open(INDEX, encoding="utf-8") as f:
            for line in f:
                try:
                    seen.add(json.loads(line)["hash"])
                except Exception as _e1:
                    print("[qq_meme] 索引行读不动，跳过：%s" % _e1)
                    continue
    got = 0
    rows = []
    with open(INBOX, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or "[CQ:image" not in line:
                continue
            try:
                d = json.loads(line)
            except Exception as _e2:
                print("[qq_meme] 收件行读不动，跳过：%s" % _e2)
                continue
            if group and int(d.get("peer") or 0) != int(group):
                continue
            rows.append(d)
    for d in rows[-limit:]:
        for m in CQ_IMG.finditer(d.get("text") or ""):
            fl = _fields(m.group(1))
            key = fl.get("file") or fl.get("path") or ""
            if not key:
                continue
            blob = b""
            # 先走 CQ 里自带的 url（懒加载的图缓存会过期，get_image 常报 file not found）
            _url = html.unescape(fl.get("url") or "")
            if _url.startswith("http"):
                try:
                    with urllib.request.urlopen(_url, timeout=25) as rr:
                        blob = rr.read()
                except Exception as e:
                    print("[meme] 直连下载失败：%s" % e)
            if not blob:
                try:
                    r = api("get_image", {"file": key})
                    path = (r.get("data") or {}).get("file") or ""
                    blob = open(path, "rb").read() if path and os.path.exists(path) else b""
                except Exception as e:
                    print("[meme] get_image 兜底失败：%s" % e)
            if not blob:
                continue
            ext = os.path.splitext(key)[1] or ".img"
            path = key
            h = hashlib.sha1(blob).hexdigest()[:12]
            if h in seen:
                continue
            dst = os.path.join(STORE, h + ext)
            if dry:
                print("[dry] 会收：", h, d.get("sender"), (d.get("text") or "")[:30])
                seen.add(h)
                continue
            with open(dst, "wb") as out:
                out.write(blob)
            rec = {"hash": h, "file": dst, "size": len(blob),
                   "sender": d.get("sender"), "group": d.get("peer"),
                   "ts": d.get("time"), "text": (d.get("text") or "")[:80]}
            with open(INDEX, "a", encoding="utf-8") as out:
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            seen.add(h)
            got += 1
    print("新收表情包 %d 张，库里共 %d 张" % (got, len(seen)))


def show():
    if not os.path.exists(INDEX):
        print("库里还没有")
        return
    for line in open(INDEX, encoding="utf-8"):
        try:
            r = json.loads(line)
        except Exception as _e3:
            print("[qq_meme] 索引行读不动，跳过：%s" % _e3)
            continue
        print("%-14s %5.0fKB %-10s %s" % (r["hash"], r["size"] / 1024,
                                          str(r.get("sender"))[:10], str(r.get("text"))[:40]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--group", type=int, default=0)
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    if a.list:
        show()
    else:
        scan(a.limit, a.group, a.dry)


if __name__ == "__main__":
    raise SystemExit(main())
