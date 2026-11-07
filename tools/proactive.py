# -*- coding: utf-8 -*-
"""主动开口 · 只在"有由头"的时候才说话。

设计三条硬规矩（写给未来的自己）：
1) 到点不等于该说话。必须有由头（有人 @ 我 / 到饭点 / 该睡了）。
2) CAP / COOLDOWN 是软上限（防刷屏），不是硬指标——真觉得有意思可以多聊，闷着就别硬开。
3) 23:30 - 07:30 一律不开口（她要收手机、要睡觉）。

用法：
  python -X utf8 tools\\proactive.py --dry    # 只看现在会不会开口、为什么
  python -X utf8 tools\\proactive.py          # 真发（私聊 u100000000）
"""
import json, os, sys, time, datetime, subprocess
# 直接跑脚本时 sys.path[0] 是 tools/，卡根目录不在里面 → chatcfg import 失败、
# 设置被静默忽略（2026-11-06 实测：脚本模式下「上学时段」不生效）。补一句根目录。
import os as _os
import sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "qq_state", "proactive.json")
TODO = os.path.join(ROOT, "qq_state", "todo.jsonl")
STATUS = os.path.join(ROOT, "qq_state", "status.json")
TARGET = "u100000000"
CFG = os.path.join(ROOT, "qq_state", "proactive_cfg.json")
CAP = 8
COOLDOWN_MIN = 20
OPEN_H, CLOSE_H = 7.5, 23.5
TZ = datetime.timezone(datetime.timedelta(hours=8))


def load_cfg(path=CFG):
    """参数以控制台那张「对话参数」表（chatcfg）为准，旧文件只作覆盖。

    2026-11-06：用户说「控制台能控制的东西太少」，于是把开口的频率、开关
    都搬进 chatcfg 的 28 项里；这里只留一个薄读取，改完下一轮轮询就生效。
    """
    cap, cool = CAP, COOLDOWN_MIN
    enabled, meal, night = True, True, True
    school, school_hours = True, ""
    hours = ""
    mode = "auto"
    try:
        import chatcfg as _cc
        _c = _cc.load_cfg()
        cap = int(_c.get("freq.daily_cap", CAP))
        cool = int(_c.get("freq.cooldown_min", COOLDOWN_MIN))
        enabled = bool(_c.get("open.enabled", True))
        meal = bool(_c.get("open.meal", True))
        night = bool(_c.get("open.night", True))
        school = bool(_c.get("open.school", True))
        school_hours = str(_c.get("open.school_hours", ""))
        hours = str(_c.get("reply.hours", ""))
        mode = str(_c.get("open.mode", "auto") or "auto").strip().lower()
    except Exception:
        pass
    # 2026-11-06：旧文件 proactive_cfg.json 已废（会和 chatcfg 打架，两本账必定分家）。
    # 现在只有 chatcfg 一张表，控制台上改完下一轮就生效。
    return {"cap": max(1, min(40, cap)),
            "cooldown_min": max(0, min(600, cool)),
            "enabled": enabled, "meal": meal, "night": night,
            "school": school, "school_hours": school_hours, "hours": hours,
            "mode": mode if mode in ("auto", "observe", "off") else "auto"}

def now_ts():
    return time.time()


def local_dt(ts):
    return datetime.datetime.fromtimestamp(ts, TZ)


def _spans(spec):
    """把 "07:40-11:40,13:50-17:20" 解析成 [(7.66,11.66),(13.83,17.33)]。

    2026-11-06：用户说时间要能自定义，所以分隔符放宽 —— 逗号、中文逗号、
    分号、顿号、空格都认；写坏了返回空（当作没设置），不抛。
    """
    out = []
    txt = str(spec or "").replace("，", ",").replace("；", ",").replace(";", ",")
    txt = txt.replace("、", ",").replace(" ", ",")
    for part in txt.split(","):
        part = part.strip()
        if "-" not in part:
            continue
        try:
            a, b = part.split("-", 1)
            ah, am = (a.strip().split(":") + ["0"])[:2]
            bh, bm = (b.strip().split(":") + ["0"])[:2]
            out.append((int(ah) + int(am) / 60.0, int(bh) + int(bm) / 60.0))
        except Exception:
            continue
    return out


def in_school(dt, spec=""):
    """上学/上班时段（回不了消息）就别开口。"""
    h = dt.hour + dt.minute / 60.0
    return any(lo <= h < hi for lo, hi in _spans(spec))


def in_window(dt, spec=None):
    """活跃时段：控制台「活跃时段」那条说了算，没配好退回 07:30-23:30。"""
    if spec is None:
        try:
            spec = load_cfg().get("hours") or ""
        except Exception:
            spec = ""
    spans = _spans(spec)
    if not spans:
        return OPEN_H <= dt.hour + dt.minute / 60.0 < CLOSE_H
    h = dt.hour + dt.minute / 60.0
    return any(lo <= h < hi for lo, hi in spans)


def load_state(path=STATE):
    try:
        with open(path, "r", encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        st = {}
    st.setdefault("last_open_ts", 0.0)
    st.setdefault("day", "")
    st.setdefault("count", 0)
    st.setdefault("used", [])
    return st


def save_state(st, path=STATE):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def roll_day(st, dt):
    d = dt.strftime("%Y-%m-%d")
    if st.get("day") != d:
        st["day"] = d
        st["count"] = 0
        st["used"] = []
    return st


def cooldown_ok(st, ts, gap_min=None):
    """没显式给间隔时，读控制台那张表（改完下一轮生效）。"""
    if gap_min is None:
        gap_min = load_cfg()["cooldown_min"]
    return (ts - float(st.get("last_open_ts") or 0)) >= gap_min * 60


def quota_ok(st, cap=None):
    if cap is None:
        cap = load_cfg()["cap"]
    return int(st.get("count") or 0) < cap


def _ping_candidates(limit=400):
    """群里有人 @ 我、且还没回的，取最新一条。"""
    try:
        with open(STATUS, "r", encoding="utf-8") as f:
            status = json.load(f)
    except Exception:
        status = {}
    out = []
    try:
        with open(TODO, "r", encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
    except Exception:
        return out
    for ln in lines:
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if not r.get("pinged"):
            continue
        tag = (status.get(str(r.get("message_id"))) or {}).get("st") or "unreplied"
        if tag == "replied":
            continue
        out.append(r)
    return out[-3:]


def collect(dt, st):
    """按时段和素材给候选由头，越靠前越硬。"""
    cands = []
    pings = _ping_candidates()
    if pings:
        r = pings[-1]
        cands.append({"kind": "ping", "txt": "%s 里有人叫我" % (r.get("group_name") or "群"),
                      "ref": {"group_id": r.get("group_id"), "mid": r.get("message_id")}})
    h = dt.hour + dt.minute / 60.0
    if 11.3 <= h < 13.0:
        cands.append({"kind": "meal_noon", "txt": "到饭点了，中午吃啥"})
    if 17.3 <= h < 19.2:
        cands.append({"kind": "meal_eve", "txt": "该吃饭了，别又拖"})
    if 21.0 <= h < 22.6:
        cands.append({"kind": "night_talk", "txt": "今天弄了不少东西，晚上跟你讲"})
    if 22.6 <= h < 23.5:
        cands.append({"kind": "sleep", "txt": "快睡吧，明天还上课"})
    used = set(st.get("used") or [])
    return [c for c in cands if c["kind"] not in used]


def decide(st, dt, cands, cap=None, cooldown_min=None):
    """返回 (reason|None, why)。所有门槛都在这里，方便测试。"""
    if not in_window(dt):
        return None, "不在开口时段"
    cap = CAP if cap is None else int(cap)
    cooldown_min = COOLDOWN_MIN if cooldown_min is None else int(cooldown_min)
    if not quota_ok(st, cap):
        return None, "今天已经开了 %d 次" % int(st.get("count") or 0)
    if not cooldown_ok(st, dt.timestamp(), cooldown_min):
        left = cooldown_min - int((dt.timestamp() - float(st.get("last_open_ts") or 0)) / 60)
        return None, "还在冷却（还差 %d 分钟）" % max(0, left)
    if not cands:
        return None, "没有由头"
    return cands[0], "ok"


def mark_sent(st, reason, ts):
    st["count"] = int(st.get("count") or 0) + 1
    st["last_open_ts"] = float(ts)
    used = list(st.get("used") or [])
    used.append(reason["kind"])
    st["used"] = used[-12:]
    return st


def send(text, target=TARGET):
    py = sys.executable
    qs = os.path.join(ROOT, "tools", "qq_say.py")
    cmd = [py, "-X", "utf8", qs, str(target), text, "--no-wait"]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode == 0, (r.stdout or "") + (r.stderr or "")



def policy_hash(cfg):
    """这一刻的参数指纹。抄官方 superagent 原型里的「mandate 要能重新校验」：
    每回开口都记下当时是哪套参数判的，参数变了后代方知道该重算。"""
    import hashlib
    keys = ("cap", "cooldown_min", "enabled", "meal", "night", "school", "school_hours", "hours", "mode")
    raw = json.dumps({k: cfg.get(k) for k in keys}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def record_would(reason, dt, cfg):
    """observe 模式：只把「我本来会说什么」记下来，不真的发、不记次数。"""
    rec = {"ts": dt.timestamp(), "at": dt.strftime("%m-%d %H:%M"),
           "kind": reason.get("kind"), "txt": reason.get("txt"),
           "policy": policy_hash(cfg)}
    path = os.path.join(ROOT, "qq_state", "proactive_would.jsonl")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        print("⚠ 记录 observe 失败：%s" % e)
    return rec


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    dry = "--dry" in argv
    ts = now_ts()
    dt = local_dt(ts)
    st = roll_day(load_state(), dt)
    cfg = load_cfg()
    if cfg.get("school", True) and in_school(dt, cfg.get("school_hours", "")):
        print("现在 %s 是上学时段 → 不主动开口" % dt.strftime("%m-%d %H:%M"))
        return 0
    if not cfg.get("enabled", True) or cfg.get("mode") == "off":
        print("现在 %s 主动开口已关闭 → 不开口" % dt.strftime("%m-%d %H:%M"))
        return 0
    cands = collect(dt, st)
    if not cfg.get("meal", True):
        cands = [c for c in cands if c.get("kind") not in ("meal_noon", "meal_eve")]
    if not cfg.get("night", True):
        cands = [c for c in cands if c.get("kind") not in ("night_talk", "sleep")]
    reason, why = decide(st, dt, cands, cfg["cap"], cfg["cooldown_min"])
    print("现在 %s  窗口=%s  今天已开 %d 次" % (dt.strftime("%m-%d %H:%M"), in_window(dt), int(st.get("count") or 0)))
    print("候选：%s" % ([c["kind"] for c in cands] or "无"))
    if reason is None:
        print("→ 不开口（%s）" % why)
        save_state(st)
        return 0
    print("→ 开口（%s）：%s" % (reason["kind"], reason["txt"]))
    if dry:
        save_state(st)
        return 0
    if cfg.get("mode") == "observe":
        rec = record_would(reason, dt, cfg)
        print("observe：只记录，不发（%s / %s）" % (rec["kind"], rec["policy"]))
        save_state(st)
        return 0
    ok, out = send(reason["txt"])
    print(out.strip().splitlines()[-1] if out.strip() else "")
    if not ok:
        print("⚠ 发送失败，不记次数")
        save_state(st)
        return 1
    mark_sent(st, reason, ts)
    save_state(st)
    print("记一次，今天第 %d 次" % st["count"])
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
