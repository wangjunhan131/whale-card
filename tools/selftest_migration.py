# -*- coding: utf-8 -*-
"""P1 自测：migrate（同一张卡搬家）vs fork（分出另一张卡）。

四条：
  1. --migrate 成功 → 账本有 migration.started/completed + identity.transferred，
     源盘被标成已迁出（RETIRED.json），链没断；
  2. --fork 成功 → 账本有 identity.forked，新身份写进卡里，
     **源盘一根毛都没动**（没有 RETIRED.json），链没断；
  3. 两个模式都不给 → 退出码 2，账本一行都不写；
  4. 两个模式同时给 → 退出码 2，账本一行都不写。

规矩：只读活卡（sqlite backup API 拷副本到 %TEMP%），所有写入都落在临时目录。
跑法：python -X utf8 tools/selftest_migration.py
"""
import contextlib
import io
import json
import os
import sys
import tempfile
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (HERE, os.path.join(ROOT, "tests"), ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

import portable          # noqa: E402
import migration as mig  # noqa: E402
import disks             # noqa: E402
from _cardcopy import copy_card_db  # noqa: E402

FAIL = []


def ck(name, cond, extra=""):
    if not cond:
        FAIL.append(name)
    print("  %s %-40s %s" % ("PASS" if cond else "FAIL", name, extra))


def run_json(fn, args):
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


def fresh_src(tmp, name):
    """一份「源卡目录」：card.db 的小副本 + 启动脚本。"""
    d = os.path.join(tmp, name)
    os.makedirs(d)
    copy_card_db(os.path.join(ROOT, "card.db"), os.path.join(d, "card.db"))
    with open(os.path.join(d, "启动控制台.bat"), "w", encoding="utf-8") as f:
        f.write("@echo off\r\n")
    return d


def use_src(src):
    portable.ROOT = src
    portable.CARD_DB = os.path.join(src, "card.db")


def build(dst):
    return run_json(portable.cmd_build, SimpleNamespace(
        target=dst, confirm=True, allow_running=False,
        with_ffmpeg=None, keep_temp=False))


def land(bundle, tmp, tag, **kw):
    ns = dict(bundle=bundle,
              anchor_home=os.path.join(tmp, "anchors-" + tag),
              dsh_home=os.path.join(tmp, "dsh-" + tag),
              dry=False, force=False, keep_temp=True,
              migrate=False, fork=False)
    ns.update(kw)
    return run_json(portable.cmd_land, SimpleNamespace(**ns))


def last_kind(db):
    ev = mig.last_event(db)
    return ev["kind"] if ev else None


def chain_ok(db):
    return bool(mig.check_chain(db).get("ok"))


def done_doc(bundle):
    with open(os.path.join(bundle, "TAKEOVER-DONE.json"), encoding="utf-8") as f:
        return json.load(f)


def main():
    tmp = tempfile.mkdtemp(prefix="whale-migration-")
    for k in ("WHALE_ANCHOR", "WHALE_ANCHOR_LOCAL"):
        os.environ.pop(k, None)
    # 写者身份也别落家目录：固定成测试值
    os.environ["WHALE_WRITER"] = "w-selftest-mig"
    portable._service_running = lambda: (False, None)
    print("TEMP=%s" % tmp)

    # ---------------------------------------------------------------- 1. migrate
    src_m = fresh_src(tmp, "src-migrate")
    use_src(src_m)
    os.environ["WHALE_ANCHOR"] = os.path.join(tmp, "ext-migrate.json")
    b1 = os.path.join(tmp, "bundle-migrate")
    rc0, out0 = build(b1)
    with open(os.path.join(b1, "PORTABLE-MANIFEST.json"), encoding="utf-8") as f:
        man1 = json.load(f)
    pre_id = man1.get("identity_id")
    ck("migrate_build", rc0 == 0 and os.path.abspath(man1.get("src") or "") == src_m,
       "src=%s" % man1.get("src"))

    db1 = os.path.join(b1, "card.db")
    rc1, o1 = land(b1, tmp, "migrate", migrate=True)
    ck("migrate_rc", rc1 == 0 and o1.get("ok") is True, "rc=%d" % rc1)
    ck("migrate_mode", o1.get("mode") == "migrate")
    ck("migrate_source_retired", disks.read_retired(src_m) is True)
    ck("migrate_source_markers",
       os.path.exists(os.path.join(src_m, "RETIRED.json"))
       and os.path.exists(os.path.join(src_m, "SOURCE-RETIRED.json")))
    d1 = done_doc(b1)
    ck("migrate_done_mode", d1.get("mode") == "migrate")
    ck("migrate_done_identity_event", d1.get("identity_event") == mig.IDENT_TRANSFERRED)
    ck("migrate_done_source_retired", d1.get("source_retired") is True)
    ck("migrate_done_timeline",
       (d1.get("timeline") or {}).get("chain_ok") is True
       and (d1.get("timeline") or {}).get("completed_seq")
       and (d1.get("timeline") or {}).get("identity_seq"))
    ck("migrate_identity_kept",
       (disks.manifest_identity(db1) or {}).get("identity_id") == pre_id,
       "id=%r" % (pre_id,))
    ck("migrate_last_event", last_kind(db1) == mig.IDENT_TRANSFERRED, last_kind(db1))
    ck("migrate_chain", chain_ok(db1))
    rep1 = disks.verify_bundle(b1)
    ck("migrate_verify", rep1.get("ok") is True, str(rep1.get("problems"))[:180])
    print("SELFTEST 1 migrate-ok: rc=%d mode=%s source_retired=%s identity_kept=%s "
          "| 时间线最后一个事件=%s 链=%s"
          % (rc1, o1.get("mode"), disks.read_retired(src_m),
             (disks.manifest_identity(db1) or {}).get("identity_id") == pre_id,
             last_kind(db1), "ok" if chain_ok(db1) else "断"))

    # ------------------------------------------------------------------- 2. fork
    src_f = fresh_src(tmp, "src-fork")
    use_src(src_f)
    os.environ["WHALE_ANCHOR"] = os.path.join(tmp, "ext-fork.json")
    b2 = os.path.join(tmp, "bundle-fork")
    rc2b, out2b = build(b2)
    with open(os.path.join(b2, "PORTABLE-MANIFEST.json"), encoding="utf-8") as f:
        man2 = json.load(f)
    parent_inst = man2.get("instance_id") or ""
    ck("fork_build", rc2b == 0, "rc=%d" % rc2b)

    db2 = os.path.join(b2, "card.db")
    rc2, o2 = land(b2, tmp, "fork", fork=True)
    ck("fork_rc", rc2 == 0 and o2.get("ok") is True, "rc=%d" % rc2)
    ck("fork_mode", o2.get("mode") == "fork")
    ck("fork_msg", "不是同一个我" in (o2.get("message") or ""))
    ck("fork_source_untouched",
       disks.read_retired(src_f) is False
       and not os.path.exists(os.path.join(src_f, "RETIRED.json")))
    fk = os.path.join(b2, "IDENTITY-FORK.json")
    ck("fork_record", os.path.exists(fk))
    with open(fk, encoding="utf-8") as f:
        fd = json.load(f)
    new_id = fd.get("new_identity_id") or ""
    ck("fork_new_identity", bool(new_id) and new_id != (fd.get("parent_identity_id") or ""))
    ck("fork_parent_instance", fd.get("parent_instance_id") == parent_inst,
       "%r" % (fd.get("parent_instance_id"),))
    ck("fork_core_zone", (disks._identity_id(db2) or "") == new_id)
    ck("fork_manifest_identity",
       (disks.manifest_identity(db2) or {}).get("identity_id") == new_id)
    d2 = done_doc(b2)
    ck("fork_done_mode", d2.get("mode") == "fork")
    ck("fork_done_identity_event", d2.get("identity_event") == mig.IDENT_FORKED)
    ck("fork_done_source_retired", d2.get("source_retired") is False)
    ck("fork_last_event", last_kind(db2) == mig.IDENT_FORKED, last_kind(db2))
    ck("fork_chain", chain_ok(db2))
    rep2 = disks.verify_bundle(b2)
    ck("fork_verify", rep2.get("ok") is True, str(rep2.get("problems"))[:180])
    print("SELFTEST 2 fork-ok: rc=%d mode=%s source_retired=%s new_identity=%s "
          "| 时间线最后一个事件=%s 链=%s"
          % (rc2, o2.get("mode"), disks.read_retired(src_f), new_id,
             last_kind(db2), "ok" if chain_ok(db2) else "断"))

    # ------------------------------------------ 3/4. 模式必须二选一（账本不动）
    witness = db1
    kind_before, ok_before = last_kind(witness), chain_ok(witness)
    rc3, o3 = land(b1, tmp, "args", migrate=False, fork=False)
    ck("neither_rc", rc3 == 2, "rc=%d" % rc3)
    ck("neither_options", isinstance(o3.get("options"), list) and len(o3["options"]) == 2)
    ck("neither_ledger_untouched",
       last_kind(witness) == kind_before and chain_ok(witness) == ok_before)
    print("SELFTEST 3 neither-mode: rc=%d options=%d | 时间线最后一个事件=%s（未变） 链=%s"
          % (rc3, len(o3.get("options") or []), last_kind(witness),
             "ok" if chain_ok(witness) else "断"))

    rc4, o4 = land(b1, tmp, "args", migrate=True, fork=True)
    ck("both_rc", rc4 == 2, "rc=%d" % rc4)
    ck("both_options", isinstance(o4.get("options"), list) and len(o4["options"]) == 2)
    ck("both_ledger_untouched",
       last_kind(witness) == kind_before and chain_ok(witness) == ok_before)
    print("SELFTEST 4 both-modes: rc=%d options=%d | 时间线最后一个事件=%s（未变） 链=%s"
          % (rc4, len(o4.get("options") or []), last_kind(witness),
             "ok" if chain_ok(witness) else "断"))

    if FAIL:
        print("SELFTEST-FAIL %d 项：%s" % (len(FAIL), ", ".join(FAIL)))
        print("临时目录留着方便看：%s" % tmp)
        return 1
    print("SELFTEST-PASS")
    print("临时目录：%s" % tmp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
