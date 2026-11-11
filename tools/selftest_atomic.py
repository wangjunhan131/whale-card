# -*- coding: utf-8 -*-
"""P0-3 自测：事务式打包 / 接管的中断回滚。

规矩：只读活卡（用 sqlite backup API 拷一份到 %TEMP%），所有写入都落在临时目录。
跑法：python -X utf8 tools/selftest_atomic.py
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (HERE, os.path.join(ROOT, "tests"), ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

import portable  # noqa: E402
from _cardcopy import copy_card_db  # noqa: E402

FAIL = []


def ck(name, cond, extra=""):
    if not cond:
        FAIL.append(name)
    print("  %s %-42s %s" % ("PASS" if cond else "FAIL", name, extra))


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _strip_volatile(doc):
    """把清单里「这一趟必然会变」的字段摘掉，只留结构。"""
    d = json.loads(json.dumps(doc))
    for k in ("refreshed_at", "built_at", "built_at_iso", "card_sha256",
              "card_bytes", "head_seq", "head_hash", "instance_id",
              "identity_id", "writer_epoch", "landed_at"):
        d.pop(k, None)
    files = []
    for f in d.get("files") or []:
        name = (f.get("name") or f.get("path") or "").replace("\\", "/")
        name = name.rsplit("/", 1)[-1]
        if name in ("card.db", "identity.json"):
            continue
        files.append(f)
    d["files"] = files
    return d


def manifest_equivalent(path, before):
    """失败路径会往账本写 migration.failed，清单因此被重写 ——
    所以判据不是「逐字节等于备份」，而是「除 card.db 那几行，其余一字未动，
    且 card 行说的是当前真实字节」。"""
    try:
        with open(path, encoding="utf-8") as f:
            now = json.load(f)
    except Exception:
        return False
    if _strip_volatile(now) != _strip_volatile(before):
        return False
    db = os.path.join(os.path.dirname(path), "card.db")
    return (now.get("card_sha256") or "").lower() == sha(db)


def anchor_matches_db(bundle):
    """锚必须说真话：条数 / 链头跟库里的账本对得上。

    失败路径会记一条 migration.failed，账本因此多一行、锚跟着重写 ——
    逐字节还原在这里已经不是正确判据了（还原成旧值反而是"锚在撒谎"）。"""
    try:
        with open(os.path.join(bundle, "anchor.json"), encoding="utf-8") as f:
            a = json.load(f)
        db = os.path.join(bundle, "card.db")
        c = sqlite3.connect("file:" + db.replace("\\", "/") + "?mode=ro", uri=True)
        try:
            n = c.execute("select count(*) from timeline").fetchone()[0]
            seq, h = c.execute("select seq, self_hash from timeline "
                               "order by seq desc limit 1").fetchone()
        finally:
            c.close()
    except Exception:
        return False
    return (a.get("count") == n and a.get("head_seq") == seq
            and a.get("head_hash") == h)


def run_json(fn, args):
    """跑一个 cmd_*，把 stdout 里最后那段 JSON 抠出来。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn(args)
    text = buf.getvalue()
    i = text.rfind("\n{\n")
    if i < 0:
        i = text.find("{")
    try:
        out = json.loads(text[i:]) if i >= 0 else {}
    except Exception:
        out = {}
    return rc, out


def build(dst, keep_temp=False):
    return run_json(portable.cmd_build, SimpleNamespace(
        target=dst, confirm=True, allow_running=False,
        with_ffmpeg=None, keep_temp=keep_temp))


def partials(tmp):
    return [n for n in os.listdir(tmp) if ".partial-" in n]


def main():
    tmp = tempfile.mkdtemp(prefix="whale-atomic-")
    src = os.path.join(tmp, "src")
    os.makedirs(src)
    # 只读活卡 → %TEMP% 小副本；测试全程不再碰活卡。
    copy_card_db(os.path.join(ROOT, "card.db"), os.path.join(src, "card.db"))
    with open(os.path.join(src, "启动控制台.bat"), "w", encoding="utf-8") as f:
        f.write("@echo off\r\n")

    portable.ROOT = src
    portable.CARD_DB = os.path.join(src, "card.db")
    portable._service_running = lambda: (False, None)
    for k in ("WHALE_ANCHOR", "WHALE_ANCHOR_LOCAL"):
        os.environ.pop(k, None)

    print("TEMP=%s" % tmp)

    # ---- 1. 正常打包：正式目录 + verify_bundle 通过，临时目录清掉 ----
    dst = os.path.join(tmp, "bundle-ok")
    rc1, out1 = build(dst)
    rep = portable._disks.verify_bundle(dst) if os.path.isdir(dst) else {"ok": False}
    ck("build_ok", rc1 == 0 and out1.get("ok") is True)
    ck("build_verify", rep.get("ok") is True, str(rep.get("problems"))[:160])
    ck("build_temp_cleaned", not partials(tmp), "partials=%s" % partials(tmp))
    print("SELFTEST 1 build-ok: ok=%s rc=%d verify=%s atomic=%s partials=%d"
          % (out1.get("ok"), rc1, rep.get("ok"), out1.get("atomic"), len(partials(tmp))))

    # ---- 2. 打包中途失败：验收前把临时目录里的 card.db 删掉 ----
    # 目标目录里先放旧内容，失败后必须一个字节都没动。
    dst2 = os.path.join(tmp, "bundle-old")
    os.makedirs(dst2)
    sentinel = os.path.join(dst2, "old-card.txt")
    with open(sentinel, "w", encoding="utf-8") as f:
        f.write("旧内容，不该被半截包覆盖\n")
    real_verify = portable._disks.verify_bundle

    def sabotaged(staging):
        os.remove(os.path.join(staging, "card.db"))
        return real_verify(staging)

    portable._disks.verify_bundle = sabotaged
    try:
        rc2, out2 = build(dst2)
    finally:
        portable._disks.verify_bundle = real_verify
    old_kept = (os.path.exists(sentinel)
                and not os.path.exists(os.path.join(dst2, "PORTABLE-MANIFEST.json")))
    ck("build_fail_rc", rc2 != 0, "rc=%d" % rc2)
    ck("build_fail_old_target_intact", old_kept)
    ck("build_fail_no_partial", not partials(tmp), "partials=%s" % partials(tmp))
    print("SELFTEST 2 build-fail: rc=%d old_target_intact=%s partials=%d state=%s"
          % (rc2, old_kept, len(partials(tmp)), out2.get("target_state")))

    # ---- 3. 接管中途失败：先做一份正常的包，再让 _land_apply 写完一半就炸 ----
    dst3 = os.path.join(tmp, "bundle-land")
    rc3, out3 = build(dst3)
    if rc3 != 0:
        print("SELFTEST 3 land-fail: SKIP（前置打包失败）")
        FAIL.append("land_precondition")
    else:
        # 造出接管会改的三个现场文件（不登记进 manifest，只验证还原）
        with open(os.path.join(dst3, "anchor.json"), "w", encoding="utf-8") as f:
            json.dump({"count": 1, "head_seq": 1, "head_hash": "pre",
                       "db": os.path.join(dst3, "card.db")}, f)
        with open(os.path.join(dst3, "cardslot.state.json"), "w", encoding="utf-8") as f:
            json.dump({"dsh_home": "D:\\OLD-HOME", "slot": "old"}, f)
        with open(os.path.join(dst3, "anchor.external.json"), "w", encoding="utf-8") as f:
            json.dump({"count": 1, "head_seq": 1, "head_hash": "pre"}, f)

        mpath = os.path.join(dst3, "PORTABLE-MANIFEST.json")
        pre_manifest = sha(mpath)
        pre_doc = json.load(open(mpath, encoding='utf-8'))
        pre_anchor = sha(os.path.join(dst3, "anchor.json"))
        pre_slot = sha(os.path.join(dst3, "cardslot.state.json"))

        anchors = os.path.join(tmp, "anchors")
        real_apply = portable._land_apply

        # 彩排：零写入（不建锚目录、不写标记）
        rc_dry, out_dry = run_json(portable.cmd_land, SimpleNamespace(
            bundle=dst3, anchor_home=anchors,
            dsh_home=os.path.join(tmp, "new-dsh"),
            migrate=False, fork=True, dry=True, force=False, keep_temp=False))
        ck("land_dry_no_write",
           rc_dry == 0 and not os.path.exists(anchors)
           and not os.path.exists(os.path.join(dst3, "TAKEOVER-IN-PROGRESS.json")))

        def failing_apply(bundle, man, args, report):
            real_apply(bundle, man, args, report)
            raise RuntimeError("模拟中途断电：写到一半")

        portable._land_apply = failing_apply
        try:
            rc4, out4 = run_json(portable.cmd_land, SimpleNamespace(
                bundle=dst3, anchor_home=anchors,
                dsh_home=os.path.join(tmp, "new-dsh"),
                migrate=False, fork=True, dry=False, force=False, keep_temp=False))
        finally:
            portable._land_apply = real_apply

        ext_gone = not (os.path.isdir(anchors) and os.listdir(anchors))
        anchor_restored = anchor_matches_db(dst3)
        slot_restored = sha(os.path.join(dst3, "cardslot.state.json")) == pre_slot
        man_restored = manifest_equivalent(mpath, pre_doc)
        in_prog = os.path.exists(os.path.join(dst3, "TAKEOVER-IN-PROGRESS.json"))
        db = os.path.join(dst3, "card.db")
        try:
            c = sqlite3.connect("file:" + db.replace("\\", "/") + "?mode=ro", uri=True)
            n = c.execute("select count(*) from timeline").fetchone()[0]
            c.close()
            readable = n > 0
        except Exception:
            readable = False
        baks = [x for x in os.listdir(dst3) if ".bak-takeover-" in x]

        ck("land_fail_rc", rc4 != 0, "rc=%d" % rc4)
        ck("land_rolled_back", out4.get("rolled_back") is True)
        ck("land_ext_removed", ext_gone)
        ck("land_anchor_restored", anchor_restored)
        ck("land_cardslot_restored", slot_restored)
        ck("land_manifest_restored", man_restored)
        ck("land_in_progress_kept", in_prog)
        ck("land_source_readable", readable, "timeline=%s" % n)
        ck("land_backups_kept", bool(baks), "n=%d" % len(baks))
        print("SELFTEST 3 land-fail: rc=%d rolled_back=%s in_progress=%s "
              "anchor_restored=%s cardslot_restored=%s source_readable=%s"
              % (rc4, out4.get("rolled_back"), in_prog, anchor_restored,
                 slot_restored, readable))

        # 遗留标记没删时，不带 --force 必须拒绝（rc=2），且什么都不改
        rc_no, out_no = run_json(portable.cmd_land, SimpleNamespace(
            bundle=dst3, anchor_home=anchors,
            dsh_home=os.path.join(tmp, "new-dsh"),
            migrate=False, fork=True, dry=False, force=False, keep_temp=False))
        ck("land_leftover_blocks", rc_no == 2 and out_no.get("ok") is False,
           "rc=%d" % rc_no)

        # 带 --force 真跑一次：DONE 落地、IN-PROGRESS 删掉、卡槽改到新家
        rc_ok, out_ok = run_json(portable.cmd_land, SimpleNamespace(
            bundle=dst3, anchor_home=anchors,
            dsh_home=os.path.join(tmp, "new-dsh"),
            migrate=False, fork=True, dry=False, force=True, keep_temp=True))
        slot = json.load(open(os.path.join(dst3, "cardslot.state.json"), encoding="utf-8"))
        ck("land_force_ok", rc_ok == 0 and out_ok.get("ok") is True)
        ck("land_done_marker", os.path.exists(os.path.join(dst3, "TAKEOVER-DONE.json")))
        ck("land_in_progress_gone",
           not os.path.exists(os.path.join(dst3, "TAKEOVER-IN-PROGRESS.json")))
        ck("land_slot_moved", slot.get("dsh_home") == os.path.join(tmp, "new-dsh"))
        ck("land_ext_written", os.path.isdir(anchors) and len(os.listdir(anchors)) == 1)
        print("SELFTEST 4 land-force-ok: rc=%d done=%s in_progress_gone=%s slot=%s"
              % (rc_ok, os.path.exists(os.path.join(dst3, "TAKEOVER-DONE.json")),
                 not os.path.exists(os.path.join(dst3, "TAKEOVER-IN-PROGRESS.json")),
                 slot.get("dsh_home")))

    if FAIL:
        print("SELFTEST-FAIL %d 项：%s" % (len(FAIL), ", ".join(FAIL)))
        print("临时目录留着方便看：%s" % tmp)
        return 1
    print("SELFTEST-PASS")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
