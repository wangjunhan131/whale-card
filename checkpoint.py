# -*- coding: utf-8 -*-
"""签名检查点：定期给「这张卡走到哪儿了」盖一个带签名的章。

为什么还要它（V7 第 5 件，GPT 评审 5.1/5.2）：
    anchor.py 已经有锚了，能抓「卡被换成旧的那份」。但锚是**明文 JSON**：
      · 谁都能改小它来掩盖回滚（改完看着就自洽了）；
      · 锚被写坏/删掉时，现在的代码会「下次写入时重建」——
        那等于把证据冲掉，坏了和「从没写过」分不出来。
    检查点是它的补丁：带 HMAC 签名，改一个字就对不上；
    而且**坏了就报坏，不自动重建**（anchor 的 write 保持原样不动）。

签名的边界，先说清楚（别把它当密码学保证）：
    · 密钥存在卡目录的 anchor.key 里 —— 能读卡目录的人也能读密钥，
      所以它挡的是「意外损坏 + 随手改」，不是挡root。
    · 真正的防篡改要靠「检查点写到卡外介质」（另一个盘/另一台机），
      本模块支持 mirror dir：卡内一份、卡外一份，两边都得对得上。

文件布局：
    <card>/checkpoints/LATEST.json            最新一份（方便快查）
    <card>/checkpoints/CKPT-<seq>-<ts>.json   历史（保留最近 40 份）
    <mirror>/checkpoints/...                  卡外镜像（可选）
"""
import disks  # 环已经断了（disks 只依赖 seal）：manifest_identity 是这里要的口径
import hashlib
import hmac
import json
import os
import sqlite3
import time

from seal import (  # noqa: F401  （对外仍是 checkpoint.sign / key_path 这些名字）
    KEY_NAME, _card_dir, key_path, load_key, sign, verify_signature)

SCHEMA = 1
KEEP = 40
DIR_NAME = "checkpoints"


# ---------------------------------------------------------------- 密钥






# ---------------------------------------------------------------- 签名






# ---------------------------------------------------------------- 快照
def snapshot(db_path, writer=None, extra=None):
    """把「这张卡现在走到哪儿」读成一个可签名的字典。

    字段按评审要求：schema 版本 / 身份 / 链头 / 各类条数 / 写者 / 时间。
    读不到的东西写 None，不猜。
    """
    import sqlite3
    doc = {"schema": SCHEMA, "created_at": round(time.time(), 3),
           "card": os.path.abspath(db_path)}
    try:
        ident = disks.manifest_identity(db_path) or {}
    except Exception:
        ident = {}
    doc["identity_id"] = ident.get("identity_id") or ""
    doc["instance_id"] = ident.get("instance_id") or ""
    doc["writer_epoch"] = int(ident.get("writer_epoch") or 0)
    doc["head_seq"] = int(ident.get("head_seq") or 0)
    doc["head_hash"] = ident.get("head_hash") or ""
    if writer:
        doc["writer"] = writer

    def _one(con, sql):
        try:
            return con.execute(sql).fetchone()[0]
        except Exception:
            return None

    try:
        con = sqlite3.connect("file:%s?mode=ro" % os.path.abspath(db_path),
                              uri=True, timeout=10)
        try:
            doc["timeline_n"] = _one(con, "SELECT COUNT(*) FROM timeline")
            doc["mem_n"] = _one(con, "SELECT COUNT(*) FROM mem")
            doc["birth_n"] = _one(con, "SELECT COUNT(*) FROM mem_birth")
            doc["tomb_n"] = _one(con, "SELECT COUNT(*) FROM mem_tomb")
            doc["dialogue_n"] = _one(con, "SELECT COUNT(*) FROM dialogue")
        finally:
            con.close()
    except Exception as e:
        doc["read_error"] = str(e)
    if extra:
        doc.update(extra)
    return doc


# ---------------------------------------------------------------- 写
def _dirs(db_path, mirror=None):
    """写/验的目录列表：只有「卡目录」和「显式给的镜像目录」。

    ⚠️ 一开始自动把 anchor 的卡外目录也加进来了，结果在临时副本上跑测试时
    把一份**副本的**检查点写进了真卡的卡外目录 —— 副本和真卡 instance_id
    一样（都是那张卡的拷贝），事后验签就会判「卡被回滚过」。所以卡外那一份
    必须由调用方显式指定（`WHALE_CHECKPOINT_MIRROR`），不能猜。
    """
    out = [os.path.join(_card_dir(db_path), DIR_NAME)]
    if mirror:
        out.append(os.path.join(mirror, DIR_NAME))
    seen, uniq = set(), []
    for d in out:
        a = os.path.abspath(d)
        if a not in seen:
            seen.add(a)
            uniq.append(a)
    return uniq


def _write_dir(dir_path, doc, key):
    os.makedirs(dir_path, exist_ok=True)
    body = dict(doc)
    body["sig"] = sign(doc, key)
    name = "CKPT-%06d-%d.json" % (int(doc.get("head_seq") or 0),
                                  int(doc.get("created_at") or 0))
    tmp = os.path.join(dir_path, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, os.path.join(dir_path, name))
    # LATEST 也原子换名：崩在中间只会留下旧的那份，不会留半截
    lp = os.path.join(dir_path, "LATEST.json")
    ltmp = lp + ".tmp"
    with open(ltmp, "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(ltmp, lp)
    # 只留最近 KEEP 份
    try:
        files = sorted(x for x in os.listdir(dir_path)
                       if x.startswith("CKPT-") and x.endswith(".json"))
        for old in files[:-KEEP]:
            os.remove(os.path.join(dir_path, old))
    except Exception:
        pass
    return os.path.join(dir_path, name)


def write(db_path, writer=None, mirror=None, extra=None):
    """写一份签名检查点。返回落盘路径列表。"""
    key = load_key(db_path)
    doc = snapshot(db_path, writer=writer, extra=extra)
    out = []
    for d in _dirs(db_path, mirror=mirror):
        try:
            out.append(_write_dir(d, doc, key))
        except Exception:
            pass
    return out


# ---------------------------------------------------------------- 验
def latest(dir_path):
    p = os.path.join(dir_path, "LATEST.json")
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None



def live_head_seq(db_path):
    """卡**现在**的时间线链头 seq（只读，不信清单文件）。

    为什么要这个：`disks.manifest_identity()` 读的是 PORTABLE-MANIFEST.json 里
    记下的 head_seq，那份清单只有打包/落地时才刷新。拿它跟检查点比，
    等于拿「上次打包时的序号」比「上次打包时的序号」——真回滚了也看不出来。
    回滚判据必须读**活链**：timeline 表里最大的 seq。
    """
    p = db_path.replace("\\", "/")
    try:
        con = sqlite3.connect("file:%s?mode=ro" % p, uri=True, timeout=10)
    except Exception:
        return 0
    try:
        row = con.execute("SELECT MAX(seq) FROM timeline").fetchone()
        return int(row[0] or 0)
    except Exception:
        return 0
    finally:
        con.close()


def verify(db_path, mirror=None):
    """验所有检查点：签名对不对，以及卡有没有比检查点**更旧**（回滚）。

    ⚠️ 坏掉的检查点只报告，**不重建** —— 重建等于把证据冲掉。
    返回 {"ok","checked","problems","dirs","newest"}。
    """
    key = load_key(db_path, create=False)
    res = {"ok": True, "checked": 0, "problems": [], "dirs": [],
           "newest": None, "newest_path": None, "cur_seq": 0,
           "key": key_path(db_path)}
    if key is None:
        res["ok"] = False
        res["problems"].append("没有签名密钥（%s 不存在）：这张卡还没写过检查点" % KEY_NAME)
        return res
    try:
        ident = disks.manifest_identity(db_path) or {}
    except Exception:
        ident = {}
    _live = live_head_seq(db_path)
    cur_seq = int(_live or ident.get("head_seq") or 0)
    res["cur_seq"] = cur_seq
    cur_inst = ident.get("instance_id") or ""
    newest = None
    for d in _dirs(db_path, mirror=mirror):
        if not os.path.isdir(d):
            continue
        res["dirs"].append(d)
        doc = latest(d)
        if not doc:
            res["ok"] = False
            res["problems"].append("%s 里没有 LATEST.json" % d)
            continue
        res["checked"] += 1
        good, why = verify_signature(doc, key)
        if not good:
            res["ok"] = False
            res["problems"].append("%s：%s" % (d, why))
            continue
        seq = int(doc.get("head_seq") or 0)
        if newest is None or seq > int(newest.get("head_seq") or 0):
            newest = doc
            res["newest_path"] = os.path.join(d, "LATEST.json")
        if cur_inst and doc.get("instance_id") and doc["instance_id"] != cur_inst:
            res["ok"] = False
            res["problems"].append(
                "%s：检查点记的是另一张卡（%s），现在是 %s"
                % (d, doc["instance_id"], cur_inst))
        if seq and cur_seq and cur_seq < seq:
            res["ok"] = False
            res["problems"].append(
                "%s：卡被回滚过 —— 检查点已经到 seq=%d，卡里只到 %d"
                % (d, seq, cur_seq))
    res["newest"] = newest
    return res


def summary(db_path, mirror=None):
    """给 /health 用的一行摘要：有几份、最近一份多久前、验得对不对、
    坏了的话人应该怎么办（recover 只给建议，不重建）。"""
    r = verify(db_path, mirror=mirror)
    newest = r.get("newest") or {}
    age = None
    if newest.get("created_at"):
        age = round((time.time() - float(newest["created_at"])) / 3600.0, 1)
    h = _hint_from(r, db_path, mirror)
    return {"ok": r["ok"], "checked": r["checked"], "age_h": age,
            "head_seq": newest.get("head_seq"), "problems": r["problems"][:3],
            "dirs": r.get("dirs") or [], "newest_path": r.get("newest_path"),
            "recover": {"needed": h["needed"], "kind": h["kind"],
                        "steps": h["steps"], "rollback": h["rollback"],
                        "target": h["target"],
                        "also": [x["kind"] for x in h["all"][1:]]}}


# ---------------------------------------------------------------- 坏了怎么办
# 设计边界：这里只产出「人应该怎么做」的三步建议；任何一步都要人点头，
# 本模块不复制、不回滚、不重签、不删除。自动重建等于把「走到过哪」的证据冲掉。
def _kinds(r):
    """把 verify 的 problems 归类。一条问题可能同时命中两类。"""
    probs = r.get("problems") or []
    kinds = []
    if any("没有签名密钥" in p for p in probs):
        kinds.append("key_missing")
    if any(("签名对不上" in p or "没有签名" in p or "不是一个 JSON" in p)
           for p in probs):
        kinds.append("sig_bad")
    if any("另一张卡" in p for p in probs):
        kinds.append("foreign")
    if any("卡被回滚过" in p for p in probs):
        kinds.append("rollback")
    if any("没有 LATEST.json" in p for p in probs):
        kinds.append("no_latest")
    return kinds


def _backup_items(db_path):
    try:
        import backup_verify as _bv
        return _bv.inspect_backups(db_path).get("items") or []
    except Exception:
        return []


def _backup_candidates(db_path, min_seq):
    out = []
    for it in _backup_items(db_path):
        try:
            if (it.get("readable") and it.get("same_source")
                    and int(it.get("head_seq") or 0) >= int(min_seq)):
                out.append(it["path"])
        except Exception:
            continue
    return out


def _hint_steps(kind, db_path, mirror, r):
    """返回 (要不要建议, 三步, 要不要回滚, 目标)。"""
    card = os.path.dirname(os.path.abspath(db_path))
    keyf = key_path(db_path)
    newest = r.get("newest") or {}
    nseq = int(newest.get("head_seq") or 0)
    newest_file = r.get("newest_path") or os.path.join(
        card, DIR_NAME, "LATEST.json")
    cur = int(r.get("cur_seq") or 0)
    mir = os.path.join(mirror, DIR_NAME) if mirror else ""

    if kind == "key_missing":
        cands = [os.path.join(os.path.dirname(it["path"]), KEY_NAME)
                 for it in _backup_items(db_path)]
        cands = [p for p in cands if os.path.isfile(p)]
        where = cands[0] if cands else r"G:\whale-backups\anchor.key"
        return True, [
            "文件：%s 不存在。从整卡备份里找回同一把密钥（例如 %s），复制回卡目录。"
            % (keyf, where),
            "不要重新生成：新密钥会让所有旧检查点全部验不过，等于把「走到哪」"
            "的证据一次勾掉。",
            "回滚：不用动 card.db。密钥复原后重跑 `python checkpoint.py verify "
            "--db %s%s`；还红再按「签名不符」处理。"
            % (db_path, (" --mirror " + mirror) if mirror else ""),
        ], False, {"key": keyf, "candidates": cands[:5]}
    if kind == "sig_bad":
        return True, [
            "文件：%s（同目录 CKPT-*.json 一并看）。先把坏的那份复制成 "
            ".bad-<时间> 留证，别删。" % newest_file,
            ("对比卡外镜像 %s/LATEST.json：镜像验得过就从镜像复制回来"
             "（本地坏件留证）。" % mir) if mir else
            "没有卡外镜像（WHALE_CHECKPOINT_MIRROR 没设）：只能靠整卡备份和"
            "密钥复原来判断。",
            "两边都验不过 = 卡目录被改过或换过密钥：回滚到最近一份整卡备份"
            "（见 /health 的 checks.backup），不要在坏文件上重签。",
        ], False, {"file": newest_file}
    if kind == "rollback":
        cands = _backup_candidates(db_path, nseq or cur)
        if cands:
            step2 = ("回滚：要。回滚到 head_seq≥%d 的整卡备份：%s%s。"
                     % (nseq, cands[0],
                        "（共 %d 份候选）" % len(cands) if len(cands) > 1 else ""))
        else:
            step2 = ("回滚：没有 head_seq≥%d 的同源备份，暂时不能自动回；"
                     "保留红灯，人工确认用哪台机器的副本。" % nseq)
        return True, [
            "文件：card.db。活链只到 seq=%d，检查点已到 seq=%d；先停写，把"
            "当前 card.db 改名 card.db.rollback-<时间> 留证。" % (cur, nseq),
            step2,
            "不要删检查点、不要重签：那是「走到过 %d」的唯一证据；回滚后让"
            "维护线程自然写新检查点。" % nseq,
        ], True, {"seq": nseq, "checkpoint_file": newest_file,
                  "candidates": cands[:5]}
    if kind == "foreign":
        doc_inst = newest.get("instance_id") or "?"
        return True, [
            "文件：WHEREAMI.json / DISK-ROLE.json / 当前卡目录。检查点记的是 "
            "%s，现在这张卡对不上。" % doc_inst,
            "回滚：不要。先确认是不是插错卡 / 开错 db；换回原卡再验。",
            "若确实迁移到了新实例：把旧 checkpoints/ 改名归档"
            "（checkpoints-<旧实例>），别拿旧章判新卡回滚。",
        ], False, {"doc_instance": doc_inst}
    if kind == "no_latest":
        return True, [
            "文件：%s 缺失。" % newest_file,
            "回滚：不用。同目录还有 CKPT-*.json 的话，把序号最大的复制成 "
            "LATEST.json；有镜像就从镜像取。",
            "一份 CKPT 都没有：接受「这段没盖过章」，让维护线程从下一跳重新"
            "开始，不要补造历史。",
        ], False, {"file": newest_file}
    return False, [], False, {}


def _hint_from(r, db_path, mirror):
    if r.get("ok"):
        return {"needed": False, "kind": "", "steps": [], "rollback": False,
                "target": {}, "all": []}
    hints = []
    for k in _kinds(r):
        need, steps, rollback, target = _hint_steps(k, db_path, mirror, r)
        if need:
            hints.append({"kind": k, "steps": steps,
                          "rollback": rollback, "target": target})
    if not hints:
        hints = [{"kind": "unknown",
                  "steps": ["先看 /health 的 checkpoints.problems；这一条还没"
                            "归类，别自动重建任何东西。"],
                  "rollback": False, "target": {}}]
    first = hints[0]
    return {"needed": True, "kind": first["kind"], "steps": first["steps"],
            "rollback": first["rollback"], "target": first["target"],
            "all": hints}


def recover_hint(db_path, mirror=None):
    """verify 失败时给人看的三步建议：哪个文件 / 要不要回滚 / 回滚到哪一份。

    只读、只建议；不会自动重建检查点或密钥。
    """
    return _hint_from(verify(db_path, mirror=mirror), db_path, mirror)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="签名检查点")
    ap.add_argument("cmd", choices=["write", "verify", "snapshot"])
    ap.add_argument("--db", default="card.db")
    ap.add_argument("--mirror")
    a = ap.parse_args()
    if a.cmd == "write":
        print("\n".join(write(a.db, mirror=a.mirror)))
    elif a.cmd == "verify":
        print(json.dumps(verify(a.db, mirror=a.mirror), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(snapshot(a.db), ensure_ascii=False, indent=2))
