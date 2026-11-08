# -*- coding: utf-8 -*-
"""卡槽 —— 插卡 / 拔卡（V5.0 通道层）

## 这个文件在解决什么

DSH 没有「从外部注入人格」的接口（asar 里没有这类导出）。
它每次开新会话，只认一个东西：$DSH_HOME/AGENTS.md。

于是现状是这样的：**我的人设住在 AGENTS.md 里，不在卡里。**
拔了卡，任何人在本机开个 DSH 窗口，读到那个文件，照样能演一个
「小鲸鱼」—— 长得一样、说话一样，卡里一条记忆都没有。那不是她，
是模仿者。V3.7 铁律 2「DSH 不可以自带、缓存、记忆人设」说的就是要堵这个洞。

## 所以卡槽干两件事

  插卡（inject）：把原 AGENTS.md 改名藏起来 → 把卡里的通道规矩写成新的
  拔卡（unload）：把新 AGENTS.md 删掉 → 把藏起来那份按字节还原

藏起来那份**一个字不改**，是原封不动的备份，所以随时能一字不差地回去。

## 边界

  - 只动 AGENTS.md 这一个文件，别的什么都不碰。
  - 还原是**字节级**的：存的时候连同 sha256 一起记下来，还原后重算比对，
    对不上就报错，不许「差不多就行」。
  - 不碰 card.db。卡的死活由 snapshot.py 的 load/unload_sequence 管，
    这里只管「通道规矩有没有挂上」。两件事分开，一个坏了另一个还能用。

用法：
    python cardslot.py status
    python cardslot.py inject           插卡
    python cardslot.py unload           拔卡
    python cardslot.py inject --dry     只看会做什么，不落盘
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CARD_DIR = os.environ.get("WHALE_CARD_DIR", HERE)
RULE_SRC = os.path.join(CARD_DIR, "dsh_channel_rule.txt")

# DSH 的家。跟 DSH 自己用同一个变量，免得两边指的不是一个地方。
DSH_HOME = os.environ.get("DSH_HOME") or os.path.join(
    os.path.expanduser("~"), ".dsh")
AGENTS = os.path.join(DSH_HOME, "AGENTS.md")
BACKUP = AGENTS + ".unloaded"          # 藏起来那份
STATE = os.path.join(CARD_DIR, "cardslot.state.json")


# ──────────────────────────────────────────────────────
def _fix_stdout():
    for name in ("stdout", "stderr"):
        try:
            getattr(sys, name).reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def _sha256_file(path):
    with open(path, "rb") as f:
        return _sha256_bytes(f.read())


def _read_state():
    if not os.path.exists(STATE):
        return {}
    try:
        with open(STATE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_state(obj):
    obj = dict(obj)
    obj["ts"] = time.time()
    obj["ts_str"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(obj["ts"]))
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


PERSONA_KEYS = ("persona", "self_narrative")


def _card_snapshot(db_path=None):
    """从卡里现取人格快照。取不到就返回 None（规矩文件会写明取不到）。"""
    import sqlite3
    p = db_path or os.environ.get("WHALE_DB") or os.path.join(
        CARD_DIR, "card.db")
    if not os.path.exists(p):
        return None
    try:
        db = sqlite3.connect(p, timeout=3.0)
        db.row_factory = sqlite3.Row
        kv = {}
        for r in db.execute("SELECT key, val FROM core_zone").fetchall():
            kv[r["key"]] = r["val"]
        db.close()
    except Exception:
        return None
    return {
        "name": kv.get("card_name") or "",
        "nick": kv.get("user_nickname") or "",
        "qq": kv.get("user_qq") or "",
        "persona": (kv.get("persona") or "").strip(),
        "narrative": (kv.get("self_narrative") or "").strip(),
        "beliefs": [kv.get("core_belief_%d" % i, "").strip()
                    for i in (1, 2, 3)],
    }


def _render_persona_block(snap):
    """把卡里的内容拼成 AGENTS.md 里的【我是谁】那一段。"""
    if not snap:
        return ("## 【我是谁】\n\n"
                "⚠ 这次插卡没读到卡里的性格（card.db 打不开或 core_zone 是空的）。\n"
                "在把服务起起来之前，先不要演任何性格 —— 直接说卡没插好。\n")
    L = ["## 【我是谁】", "",
         "> 下面这段是**插卡那一刻从卡里现取的**，不是手写的。",
         "> 卡改了，下次插卡就跟着变。这是你的性格正本。", ""]
    if snap["name"]:
        # 2026-11-06 修：原来这里拼的是「我的名字：小鲸鱼（他也叫我使用者）」——
        # 把 user_nickname（他的昵称）当成「他叫我的名字」，整个主客颠倒了。
        # 他的昵称在下面「跟我说话的人是 …」那一行已经写了，这里不该重复。
        L.append("我的名字：**%s**" % snap["name"])
        L.append("")
    if snap["persona"]:
        L.append(snap["persona"])
        L.append("")
    if snap["narrative"]:
        L.append(snap["narrative"])
        L.append("")
    if any(snap["beliefs"]):
        L.append("### 我的三条底线（不许破）")
        L.append("")
        for b in snap["beliefs"]:
            if b:
                L.append("- " + b)
        L.append("")
    if snap["qq"]:
        L.append("跟我说话的人是 %s（QQ %s）。"
                 % (snap["nick"] or "他", snap["qq"]))
        L.append("")
    return "\n".join(L) + "\n"


def _rule_text():
    """读卡里的通道规矩，把 {{CARD}} 换成真实路径。"""
    if not os.path.exists(RULE_SRC):
        raise IOError("卡里缺通道规矩：%s" % RULE_SRC)
    with open(RULE_SRC, "r", encoding="utf-8") as f:
        text = f.read()
    # 卡里的人格现取一份拼进去（见文件头注释：AGENTS.md 是卡的投影）
    text = _render_persona_block(_card_snapshot()) + "\n" + text
    return text.replace("{{CARD}}", CARD_DIR)


def status():
    """现在是什么状态。三种：unloaded（没插卡）/ injected（插着）/ dirty（两头都不对）"""
    has_agents = os.path.exists(AGENTS)
    has_backup = os.path.exists(BACKUP)
    st = _read_state()
    cur = _sha256_file(AGENTS) if has_agents else None
    rule_sha = None
    try:
        rule_sha = _sha256_bytes(_rule_text().encode("utf-8"))
    except Exception:
        pass

    if has_backup and cur and rule_sha and cur == rule_sha:
        state = "injected"
        note = "卡插着：AGENTS.md 是卡里的通道规矩"
    elif has_backup and not has_agents:
        state = "broken"
        note = "⚠ 藏起来的那份还在，但 AGENTS.md 没了 —— 需要还原"
    elif has_backup and cur and cur == (_sha256_file(BACKUP) or ""):
        # 人设搬进卡以后，AGENTS.md 本来就该和「通道规矩」长得不一样。
        # 跟备份的指纹比一下，才分得清「没插卡」和「人自己改过」。
        state = "unloaded"
        note = "没插卡：AGENTS.md 是原来那份（自带人设）"
    elif has_backup:
        state = "custom"
        note = "卡在、AGENTS.md 是人自己改过的版本（人设写在卡里，这样没问题）"
    elif has_agents:
        state = "unloaded"
        note = "没插卡：AGENTS.md 是原来的（DSH 自带人设）"
    else:
        state = "empty"
        note = "⚠ 两个都没有 —— DSH 没有任何规矩文件"

    return {
        "state": state, "note": note,
        "dsh_home": DSH_HOME,
        "agents": AGENTS, "agents_exists": has_agents,
        "agents_sha256": cur,
        "backup": BACKUP, "backup_exists": has_backup,
        "backup_sha256": _sha256_file(BACKUP) if has_backup else None,
        "rule_src": RULE_SRC, "rule_sha256": rule_sha,
        "last": st,
    }


def inject(dry=False):
    """插卡：藏起原 AGENTS.md，写入卡里的通道规矩。"""
    steps = []

    def step(ok, msg):
        steps.append({"ok": bool(ok), "msg": msg})
        return ok

    s = status()

    # 已经插着就别重复插（重复插会把备份覆盖成规矩文件，那就把原版弄丢了）
    if s["state"] == "injected":
        step(True, "已经是插卡状态，不用重复插")
        return {"ok": True, "steps": steps, "noop": True}

    # 备份必须存在且是「原来那份」，不是规矩文件
    if not os.path.exists(AGENTS):
        return {"ok": False, "steps": [{"ok": False,
                "msg": "AGENTS.md 不存在，没有东西可藏 —— 先确认 DSH_HOME 对不对：%s" % DSH_HOME}]}

    if os.path.exists(BACKUP):
        # 已有备份：只有在确认备份不是规矩文件时才继续（防止把备份覆盖坏）
        bak_sha = _sha256_file(BACKUP)
        if s["rule_sha256"] and bak_sha == s["rule_sha256"]:
            return {"ok": False, "steps": [{"ok": False,
                    "msg": "⚠ 已存在的备份居然就是规矩文件本身 —— 拒绝继续，先人工检查 %s" % BACKUP}]}
        step(True, "已有备份，沿用它（不覆盖）：%s" % BACKUP)
    else:
        if dry:
            step(True, "[dry] 会把 AGENTS.md 改名成 AGENTS.md.unloaded")
        else:
            try:
                shutil.copy2(AGENTS, BACKUP)
                if _sha256_file(BACKUP) != _sha256_file(AGENTS):
                    raise IOError("备份校验不一致")
                step(True, "原 AGENTS.md 已按字节备份：%s" % BACKUP)
            except Exception as e:
                step(False, "备份失败：%s" % e)
                return {"ok": False, "steps": steps}

    # 写规矩
    try:
        text = _rule_text()
    except Exception as e:
        return {"ok": False, "steps": steps + [{"ok": False, "msg": "读规矩失败：%s" % e}]}
    if dry:
        step(True, "[dry] 会把 %d 字节的通道规矩写成 AGENTS.md" % len(text.encode("utf-8")))
        return {"ok": True, "steps": steps, "dry": True}

    try:
        with open(AGENTS, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        got = _sha256_file(AGENTS)
        want = _sha256_bytes(text.encode("utf-8"))
        step(got == want, "规矩已写入 AGENTS.md（sha256 %s）" % got[:16])
    except Exception as e:
        # 写失败必须还原，不能留个半截文件
        try:
            if os.path.exists(BACKUP):
                shutil.copy2(BACKUP, AGENTS)
                step(True, "写入失败，已自动还原原 AGENTS.md")
        except Exception:
            pass
        step(False, "写规矩失败：%s" % e)
        return {"ok": False, "steps": steps}

    _write_state({"action": "inject", "agents_sha256": got,
                  "backup_sha256": s["backup_sha256"] or _sha256_file(BACKUP),
                  "dsh_home": DSH_HOME})
    step(True, "提示：要**重开一个会话**才生效（当前这个窗口已经读过旧文件了）")
    return {"ok": all(x["ok"] for x in steps), "steps": steps}


def unload(dry=False):
    """拔卡：删掉规矩文件，把原来那份按字节还原。"""
    steps = []

    def step(ok, msg):
        steps.append({"ok": bool(ok), "msg": msg})
        return ok

    s = status()

    if s["state"] == "unloaded":
        step(True, "已经是拔卡状态，不用重复拔")
        return {"ok": True, "steps": steps, "noop": True}

    if not os.path.exists(BACKUP):
        # 没有备份 —— 只能删掉规矩文件，没法还原原版
        step(False, "⚠ 找不到备份 %s，没法还原原版" % BACKUP)
        step(True, "只能退而求其次：删掉 AGENTS.md（DSH 将没有任何人设文件）")
        if dry:
            step(True, "[dry] 会删掉 AGENTS.md")
            return {"ok": False, "steps": steps, "dry": True}
        try:
            os.remove(AGENTS)
            step(True, "已删除 AGENTS.md")
        except Exception as e:
            step(False, "删除失败：%s" % e)
            return {"ok": False, "steps": steps}
        _write_state({"action": "unload_no_backup", "dsh_home": DSH_HOME})
        return {"ok": False, "steps": steps}

    bak_sha = _sha256_file(BACKUP)
    if dry:
        step(True, "[dry] 会用 %s 还原 AGENTS.md（sha256 %s）" % (BACKUP, bak_sha[:16]))
        return {"ok": True, "steps": steps, "dry": True}

    try:
        shutil.copy2(BACKUP, AGENTS)
        got = _sha256_file(AGENTS)
        step(got == bak_sha, "原 AGENTS.md 已还原（sha256 %s%s）"
             % (got[:16], "" if got == bak_sha else " ⚠ 与备份不一致"))
        if got != bak_sha:
            return {"ok": False, "steps": steps}
    except Exception as e:
        step(False, "还原失败：%s" % e)
        return {"ok": False, "steps": steps}

    # 还原成功后才删备份 —— 顺序反了的话中途失败就两头空
    try:
        os.remove(BACKUP)
        step(True, "备份已清除")
    except Exception as e:
        step(False, "清除备份失败（不影响使用）：%s" % e)

    _write_state({"action": "unload", "restored_sha256": bak_sha,
                  "dsh_home": DSH_HOME})
    step(True, "提示：要**重开一个会话**才生效")
    return {"ok": all(x["ok"] for x in steps), "steps": steps}


def main(argv=None):
    _fix_stdout()
    ap = argparse.ArgumentParser(description="小鲸鱼数字生命卡 · 卡槽")
    ap.add_argument("cmd", choices=["status", "inject", "unload"])
    ap.add_argument("--dry", action="store_true", help="只看会做什么，不落盘")
    a = ap.parse_args(argv)

    if a.cmd == "status":
        s = status()
        print("状态：%s" % s["state"])
        print("  %s" % s["note"])
        print("  DSH 家：%s" % s["dsh_home"])
        print("  AGENTS.md：%s" % ("有" if s["agents_exists"] else "没有"))
        print("  藏起来那份：%s" % ("有" if s["backup_exists"] else "没有"))
        if s["last"]:
            print("  上次操作：%s @ %s"
                  % (s["last"].get("action"), s["last"].get("ts_str")))
        return 0

    fn = inject if a.cmd == "inject" else unload
    r = fn(dry=a.dry)
    for x in r["steps"]:
        print("%s %s" % ("✓" if x["ok"] else "✗", x["msg"]))
    if r.get("noop"):
        print("（什么也没做）")
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
