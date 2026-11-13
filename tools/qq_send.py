#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""qq_send.py —— 发 QQ 消息的**唯一出口**。

为什么要有它：2026-11-12 用户给了 `QQ防封控.md`（另一个 bot 的 Outbound Guard
设计），里面的六道闸门我们一个都没有。裸调 OneBot 的 send_group_msg /
send_private_msg 一旦被风控，掉线的是整个号——那就不是「少发一条」，而是
「整张脸都没了」。

所以把发消息收成一个函数，闸门按顺序过：

  0 内容闸   赌博/诈骗/色情词 → 拒；超过 1800 字 → 拒
  1 深夜静默 00:30–07:30 只拦 reason=active，被叫（mention）照回
  2 去重     同目标 + 同文（≥4 字）30 分钟内不重复发
  3 令牌桶   20/分、200/时、1500/天（持久化，重启不丢）
  4 会话冷却 群 5 秒、私聊 3 秒
  5 抖动     0~400ms 随机，别像机器一样准时

发失败时按 retcode 退避：100/1200/1201/1202/1500 或文本命中风控词 →
30/60/120/300/600/1800 秒指数退避；连撞 3 次熔断 30 分钟（自动恢复），
连续失败 3 次停 5 分钟。状态全在 qq_state/send_quota.json，重启不丢。

用法：
  python tools/qq_send.py --private 100000000 "在吗" --reason active
  python tools/qq_send.py --group 967102991 "早" --reason mention
  python tools/qq_send.py --status          # 看今天用了多少、闸门状态
  python tools/qq_send.py --reset           # 清冷却/熔断（只清门，不清计数）
  python tools/qq_send.py --private 100000000 "hi" --dry   # 只判定不发

退出码：0 发出/判定通过；2 被某道闸门拦下；3 发送失败（已退避）。
"""
import argparse
import hashlib
import io
import json
import os
import random
import re
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "qq_state")
QUOTA_F = os.path.join(STATE, "send_quota.json")
BASE = "http://127.0.0.1:6700/"
TOKEN = os.environ.get("QQ_ONEBOT_TOKEN", "qqai-dsh-http-2026")

# ---- 闸门参数（都能被 qq_state/send_quota.json 里的 tune 覆盖） ----
TUNE = {
    "per_min": 20,
    "per_hour": 200,
    "per_day": 1500,
    "dedupe_min": 30,
    "cool_group_s": 5,
    "cool_private_s": 3,
    "jitter_ms": 400,
    "night_start": "00:30",
    "night_end": "07:30",
    "max_chars": 1800,
}
RISK_CODES = (100, 1200, 1201, 1202, 1500)
BACKOFF = (30, 60, 120, 300, 600, 1800)
RISK_RE = re.compile(r"(风控|敏感|违规|频率|拦截|审核|无法发送|发送失败|risk|forbidden|denied)", re.I)
BAN_WORDS = ("赌博", "博彩", "洗钱", "诈骗", "裸聊", "卖淫", "代开发票", "刷单返利")


def _now():
    return time.time()


def _load():
    q = {"tune": {}, "min": {"key": "", "n": 0}, "hour": {"key": "", "n": 0},
         "day": {"key": "", "n": 0}, "last": {}, "recent": [], "fail": 0,
         "risk_hits": 0, "circuit_until": 0, "pause_until": 0}
    if os.path.exists(QUOTA_F):
        try:
            with io.open(QUOTA_F, "r", encoding="utf-8") as f:
                old = json.load(f)
            if isinstance(old, dict):
                q.update(old)
        except Exception as e:
            print("[qq_send] 状态文件读不动，按空账重来：%s" % e)
    t = dict(TUNE)
    t.update(q.get("tune") or {})
    q["tune"] = t
    return q


def _save(q):
    os.makedirs(STATE, exist_ok=True)
    tmp = QUOTA_F + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(q, ensure_ascii=False, indent=1))
    os.replace(tmp, QUOTA_F)


def _roll(q):
    """按分钟/小时/天滚动计数（原地改 q）。"""
    lt = time.localtime()
    mk = time.strftime("%Y%m%d%H%M", lt)
    hk = time.strftime("%Y%m%d%H", lt)
    dk = time.strftime("%Y%m%d", lt)
    for k, key in (("min", mk), ("hour", hk), ("day", dk)):
        if q[k].get("key") != key:
            q[k] = {"key": key, "n": 0}


def _bits(s):
    return set(s[i:i + 2] for i in range(max(0, len(s) - 1)))


def _gate_content(text, q):
    if not text or not text.strip():
        return "空消息"
    if len(text) > q["tune"]["max_chars"]:
        return "太长（%d 字）" % len(text)
    for w in BAN_WORDS:
        if w in text:
            return "含敏感词「%s」" % w
    return ""


def _gate_night(reason, q):
    t = q["tune"]
    lt = time.localtime()
    hm = lt.tm_hour * 60 + lt.tm_min
    a = [int(x) for x in t["night_start"].split(":")]
    b = [int(x) for x in t["night_end"].split(":")]
    start, end = a[0] * 60 + a[1], b[0] * 60 + b[1]
    inside = (start <= hm < end) if start <= end else (hm >= start or hm < end)
    if inside and reason == "active":
        return "深夜静默（%s–%s）：这个点不主动开口，被叫才回" % (t["night_start"], t["night_end"])
    return ""


def _gate_dedupe(text, target, q):
    if len(text) < 4:
        return ""
    h = hashlib.sha1((target + "|" + text).encode("utf-8")).hexdigest()[:16]
    keep = []
    for r in q.get("recent") or []:
        if _now() - float(r.get("at", 0)) < q["tune"]["dedupe_min"] * 60:
            keep.append(r)
    q["recent"] = keep[-200:]
    for r in keep:
        if r.get("h") == h:
            return "刚说过这句（%d 分钟内重复）" % q["tune"]["dedupe_min"]
    return ""


def _gate_quota(q, group):
    t = q["tune"]
    if q["min"]["n"] >= t["per_min"]:
        return "分钟上限 %d 条" % t["per_min"]
    if q["hour"]["n"] >= t["per_hour"]:
        return "小时上限 %d 条" % t["per_hour"]
    if q["day"]["n"] >= t["per_day"]:
        return "天上限 %d 条" % t["per_day"]
    return ""


def _gate_cooldown(target, q):
    last = float((q.get("last") or {}).get(target, 0))
    gap = q["tune"]["cool_group_s"] if target.startswith("g:") else q["tune"]["cool_private_s"]
    wait = gap - (_now() - last)
    return ("冷却中，还要 %.1f 秒" % wait) if wait > 0 else ""


def _call(action, payload, timeout=15):
    req = urllib.request.Request(
        BASE + action,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _is_risk(res):
    code = int(res.get("retcode") or 0)
    msg = str(res.get("message") or "") + " " + str(res.get("wording") or "")
    return code in RISK_CODES or bool(RISK_RE.search(msg)), code, msg


def _mark_replied(mid):
    """回完之后把对方那条记进「已回」，收件箱据此分已回/未回。"""
    try:
        base = os.environ.get("WHALE_QQ_BRIDGE") or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "deploy", "qq_bridge")
        with open(os.path.join(base, "replied_ids.txt"), "a", encoding="utf-8") as f:
            f.write("%d\n" % int(mid))
        return True
    except Exception as e:
        print("[qq_send] 已回账本写不动：%s" % e)
        return False


def _gate_auto_group(group, text, q):
    """自主接话闸（2026-11-12 用户要的「可以选择是否接话」+「注意别被封」）。

    只对 reason=auto 的群消息生效：
      · 每群 10 分钟最多 1 条（同一个人连着刷屏也不追）
      · 每群每天上限 default 12 条；前 14 天养号期减半
      · 单条别太长（>180 字像小作文，容易被举报/风控盯）
      · 不带链接
    """
    if not group:
        return "自主接话只用于群聊"
    now = _now()
    aq = q.setdefault("auto", {"per_group": {}, "day": {}})
    tune = q.get("tune", {})
    cd = float(tune.get("auto_cd_sec", 300))
    cap = int(tune.get("auto_day", 24))
    if int((q.get("born_at") or now)) > 0 and (now - int(q.get("born_at") or now)) < 14 * 86400:
        cap = max(3, cap // 2)
    g = str(group)
    last = float((aq["per_group"].get(g) or 0))
    if now - last < cd:
        return "这个群刚说过话，还差 %.0f 秒" % (cd - (now - last))
    today = time.strftime("%Y-%m-%d")
    used = int((aq["day"].get(today) or {}).get(g) or 0)
    if used >= cap:
        return "这个群今天的额度用完了（%d/%d）" % (used, cap)
    if len(text) > 180:
        return "太长（%d 字），自主接话控制在 180 字内" % len(text)
    if "http://" in text or "https://" in text:
        return "带链接，自主接话不发链接"
    return ""


def _mark_auto_group(group, q):
    now = _now()
    aq = q.setdefault("auto", {"per_group": {}, "day": {}})
    g = str(group)
    aq["per_group"][g] = now
    today = time.strftime("%Y-%m-%d")
    aq["day"].setdefault(today, {})
    aq["day"][today][g] = int(aq["day"][today].get(g) or 0) + 1
    # 只留最近 7 天，免得状态文件长胖
    for d in list(aq["day"].keys()):
        if d < time.strftime("%Y-%m-%d", time.localtime(now - 7 * 86400)):
            aq["day"].pop(d, None)


def send(text, private=None, group=None, reason="tool", dry=False, reply_to=0):
    q = _load()
    _roll(q)
    target = ("g:%s" % group) if group else ("u:%s" % private)

    # 熔断/暂停优先
    if q.get("circuit_until", 0) > _now():
        return 2, "熔断中，%.0f 秒后自动恢复" % (q["circuit_until"] - _now())
    if q.get("pause_until", 0) > _now():
        return 2, "连续失败冷却，%.0f 秒后恢复" % (q["pause_until"] - _now())

    for name, why in (
            ("内容", _gate_content(text, q)),
            ("深夜", _gate_night(reason, q)),
            ("去重", _gate_dedupe(text, target, q)),
            ("配额", _gate_quota(q, bool(group))),
            ("冷却", _gate_cooldown(target, q)),
            ("自主", _gate_auto_group(group, text, q) if reason == "auto" else "")):
        if why:
            _save(q)
            return 2, "%s闸：%s" % (name, why)

    if dry:
        # dry 只用来试闸门：记一格去重/冷却，方便连着跑两条看第二道闸响不响；
        # 配额不记（那是真发才扣的）。
        q.setdefault("last", {})[target] = _now()
        q.setdefault("recent", []).append(
            {"h": hashlib.sha1((target + "|" + text).encode("utf-8")).hexdigest()[:16],
             "at": _now(), "n": len(text)})
        _save(q)
        return 0, "判定通过（dry，没有真发）"

    time.sleep(random.uniform(0, q["tune"]["jitter_ms"] / 1000.0))
    action = "send_group_msg" if group else "send_private_msg"
    if reply_to:
        payload_msg = [{"type": "reply", "data": {"id": str(int(reply_to))}},
                       {"type": "text", "data": {"text": text}}]
    else:
        payload_msg = text
    payload = {"message": payload_msg}
    if group:
        payload["group_id"] = int(group)
    else:
        payload["user_id"] = int(private)
    try:
        res = _call(action, payload)
    except Exception as e:
        q["fail"] = int(q.get("fail", 0)) + 1
        if q["fail"] >= 3:
            q["pause_until"] = _now() + 300
            q["fail"] = 0
        _save(q)
        return 3, "发送异常：%s" % e

    risk, code, msg = _is_risk(res)
    for k in ("min", "hour", "day"):
        q[k]["n"] = int(q[k]["n"]) + 1
    q.setdefault("last", {})[target] = _now()
    q.setdefault("recent", []).append(
        {"h": hashlib.sha1((target + "|" + text).encode("utf-8")).hexdigest()[:16],
         "at": _now(), "n": len(text)})

    if risk:
        q["risk_hits"] = int(q.get("risk_hits", 0)) + 1
        idx = min(q["risk_hits"] - 1, len(BACKOFF) - 1)
        q["pause_until"] = _now() + BACKOFF[idx]
        if q["risk_hits"] % 3 == 0:
            q["circuit_until"] = _now() + 1800
        _save(q)
        return 3, "风控（retcode=%s %s），退避 %d 秒" % (code, msg.strip()[:60], BACKOFF[idx])

    q["fail"] = 0
    if reason == "auto" and group and not dry:
        _mark_auto_group(group, q)
    _save(q)
    # 我自己说的话也落到 outbox.jsonl，卡那边的 qq-ingest 线程会把它写进记忆
    try:
        import json as _j, time as _t
        _rec = {"message_id": int(res.get("data", {}).get("message_id") or 0),
                "ts": int(_t.time()), "peer": int(private or group or 0),
                "group": bool(group), "text": text}
        _op = os.environ.get("WHALE_QQ_OUTBOX") or r"workspace\deploy\qq_bridge\outbox.jsonl"
        os.makedirs(os.path.dirname(_op), exist_ok=True)
        with open(_op, "a", encoding="utf-8") as f:
            f.write(_j.dumps(_rec, ensure_ascii=False) + "\n")
    except Exception as e:
        print("[qq_send] outbox 写不动（不影响已发出的消息）：%s" % e)
    if reply_to:
        _mark_replied(reply_to)
    return 0, "已发出（retcode=%s）" % code


def status():
    q = _load()
    _roll(q)
    left = lambda cap, used: max(0, cap - used)  # noqa: E731
    print("今天：%d 条（小时 %d、分钟 %d）" % (q["day"]["n"], q["hour"]["n"], q["min"]["n"]))
    print("剩余：分钟 %d、小时 %d、天 %d" % (
        left(q["tune"]["per_min"], q["min"]["n"]),
        left(q["tune"]["per_hour"], q["hour"]["n"]),
        left(q["tune"]["per_day"], q["day"]["n"])))
    now = _now()
    print("熔断：%s" % ("还有 %.0f 秒" % (q["circuit_until"] - now) if q.get("circuit_until", 0) > now else "无"))
    print("暂停：%s" % ("还有 %.0f 秒" % (q["pause_until"] - now) if q.get("pause_until", 0) > now else "无"))
    print("风险命中累计：%d 次" % int(q.get("risk_hits", 0)))
    return 0


def reset():
    q = _load()
    q["circuit_until"] = 0
    q["pause_until"] = 0
    q["risk_hits"] = 0
    q["fail"] = 0
    q["last"] = {}
    _save(q)
    print("冷却/熔断已清（计数保留）")
    return 0


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("text", nargs="*", help="消息正文")
    ap.add_argument("--private", type=int, default=0)
    ap.add_argument("--group", type=int, default=0)
    ap.add_argument("--reason", default="tool", choices=("mention", "active", "tool", "auto"))
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--reply-to", type=int, default=0, help="要回复的那条 message_id（带引用）")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--reset", action="store_true")
    a = ap.parse_args()
    if a.status:
        return status()
    if a.reset:
        return reset()
    text = " ".join(a.text).strip()
    if not text:
        print("没有正文")
        return 2
    if not a.private and not a.group:
        print("要么 --private UID，要么 --group GID")
        return 2
    code, msg = send(text, private=a.private or None, group=a.group or None,
                     reason=a.reason, dry=a.dry, reply_to=a.reply_to)
    print(msg)
    return code


if __name__ == "__main__":
    sys.exit(main())
