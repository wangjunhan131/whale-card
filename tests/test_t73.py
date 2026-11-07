# -*- coding: utf-8 -*-
"""T7-3 验收：盘子登记 / 备份 / 同步 / 主备切换（disks.py）

跑法：python tests/test_t73.py      （全部在临时目录里做，不碰真卡）
"""
import io, json, os, shutil, sqlite3, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)
import disks

PASS = FAIL = 0
tmp = tempfile.mkdtemp(prefix="t73_")


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✅ " + name + (("  " + str(extra)) if extra else ""))
    else:
        FAIL += 1
        print("  ❌ " + name + (("  " + str(extra)) if extra else ""))


def copy_db(dst):
    s = sqlite3.connect(os.path.join(ROOT, "card.db"))
    d = sqlite3.connect(dst)
    with d:
        s.backup(d)
    s.close(); d.close()


A = os.path.join(tmp, "A"); B = os.path.join(tmp, "B")
os.makedirs(A); os.makedirs(B)
copy_db(os.path.join(A, "card.db"))
copy_db(os.path.join(B, "card.db"))

# 让 B 落后一节
c = sqlite3.connect(os.path.join(B, "card.db"))
c.execute("DELETE FROM timeline WHERE seq=(SELECT MAX(seq) FROM timeline)")
c.commit(); c.close()

print("\n[1] 指纹：同一张卡的不同副本，实例号必须一样")
fa = disks.card_fp(os.path.join(A, "card.db"))
fb = disks.card_fp(os.path.join(B, "card.db"))
ok("实例号一致", fa["instance"] == fb["instance"] and fa["instance"], fa["instance"])
ok("B 落后一节", fa["head_seq"] == fb["head_seq"] + 1, "%s vs %s" % (fa["head_seq"], fb["head_seq"]))

print("\n[2] 同步：落后 → 补齐；一致 → 什么都不做")
r = disks.sync_to(A, B, confirm=True)
ok("同步成功", r.get("ok") and r.get("action") == "sync", r)
after = disks.card_fp(os.path.join(B, "card.db"))
ok("链头对上了", after["head_hash"] == fa["head_hash"])
ok("条数对上了", after["mem"] == fa["mem"])
r2 = disks.sync_to(A, B, confirm=True)
ok("再同步一次是 noop", r2.get("action") == "noop", r2.get("action"))

print("\n[3] 分叉：备份的链头不在主盘这条链上 → 必须拒绝")
C = os.path.join(tmp, "C"); os.makedirs(C)
copy_db(os.path.join(C, "card.db"))
c = sqlite3.connect(os.path.join(C, "card.db"))
c.execute("UPDATE timeline SET self_hash='deadbeef' WHERE seq=(SELECT MAX(seq) FROM timeline)")
c.commit(); c.close()
r = disks.sync_to(A, C, confirm=True)
ok("拒绝分叉", (not r.get("ok")) and "分叉" in (r.get("why") or ""), r.get("why"))

print("\n[4] 换主备：三条全中才允许")
r = disks.switch_role(A, B)
ok("一致时切换成功", r.get("ok"), r.get("old_main_now"))
ok("A 变成备份 #1", (disks.read_role(A) or {}).get("role") == "backup")
ok("B 变成主盘", (disks.read_role(B) or {}).get("role") == "main")
r = disks.switch_role(A, C)
ok("不一致时拒绝", not r.get("ok") and r.get("diff"), r.get("diff"))

print("\n[5] 注册表：备份盘自动编号，不复用（用一块干净的主卡目录 E）")
E = os.path.join(tmp, "E"); os.makedirs(E)
copy_db(os.path.join(E, "card.db"))
disks.write_role(E, "main")
if not os.path.isdir(os.path.join(tmp, "D")):
    os.makedirs(os.path.join(tmp, "D"))
    copy_db(os.path.join(tmp, "D", "card.db"))
r1 = disks.register(E, os.path.join(tmp, "B"), "backup")
r2 = disks.register(E, os.path.join(tmp, "D"), "backup")
ok("第一块备份 #1、第二块 #2", r1["number"] == 1 and r2["number"] == 2,
   "%s / %s" % (r1["number"], r2["number"]))
r3 = disks.register(E, os.path.join(tmp, "D"), "backup")
ok("同一块盘重登记不换号", r3["number"] == 2, r3["number"])
reg = disks.load_registry(E)
ok("注册表记了两块盘", len(reg["disks"]) == 2, len(reg["disks"]))
ok("注册表文件在卡目录里", os.path.exists(os.path.join(E, "DISKS.json")))

print("\n[6] 状态：能列出主盘、备份、在线与否")
st = disks.status(os.path.join(tmp, "A"))
roles = [d["role"] for d in st["disks"]]
# A 在这一步之前被换过主备，它现在可能是"备份（这台机器）"——
# 真正要保证的是：本机那张卡一定在列表里，且角色和它自己的身份证一致。
me = st["disks"][0]
ok("有本机行", me.get("card_dir") == os.path.abspath(os.path.join(tmp, "A")),
   me.get("card_dir"))
ok("本机角色跟着身份证",
   me.get("role") == (disks.read_role(os.path.join(tmp, "A")) or {}).get("role", "main"),
   me.get("role"))
ok("有备份行", "backup" in roles, roles)
ok("每行都有 serial", all(d.get("serial") is not None for d in st["disks"]), 
   [d.get("serial") for d in st["disks"]])

# ---- [7] reseed：分叉先拒、预演、再重做，并留证 ----
print("\n[7] 分叉与重做备份（reseed）")
import timeline  # noqa: E402

A = os.path.join(tmp, "A")
B = os.path.join(tmp, "B")
# 造真分叉：A 和 B 各写一条不同的，长度一样但哈希不同
# （只给 B 加一条的话，会先撞上"备份比主盘新"那条分支，测不到分叉）
_con_a = sqlite3.connect(os.path.join(A, "card.db"))
_con_a.row_factory = sqlite3.Row
ta = timeline.Timeline(_con_a)
ta.append("talk", "只有A有的一条", ts=time.time() + 1)
_con_b = sqlite3.connect(os.path.join(B, "card.db"))
_con_b.row_factory = sqlite3.Row
tb = timeline.Timeline(_con_b)
tb.append("talk", "只有B有的一条", ts=time.time() + 1)

r = disks.sync_to(A, B, confirm=True)
ok("分叉时 sync 拒绝", r.get("ok") is False, r.get("why"))
ok("拒绝时说清丢什么", r.get("need_reseed") is True and len(r.get("orphans") or []) >= 1,
   r.get("orphans"))
ok("给得出分叉点", r.get("fork_at") is not None, r.get("fork_at"))

r = disks.sync_to(A, B, confirm=False, reseed=True)
ok("reseed 不确认时只预演", r.get("dry_run") is True and r.get("action") == "reseed", r.get("action"))

r = disks.sync_to(A, B, confirm=True, reseed=True)
ok("reseed 成功", r.get("ok") is True, r.get("why"))
ok("reseed 后两边链头一致",
   disks.card_fp(os.path.join(A, "card.db"))["head_hash"]
   == disks.card_fp(os.path.join(B, "card.db"))["head_hash"])
seedlog = os.path.join(B, "SEED-LOG.json")
ok("丢掉的孤儿记进 SEED-LOG", os.path.exists(seedlog))
if os.path.exists(seedlog):
    note = json.load(open(seedlog, encoding="utf-8"))
    ok("SEED-LOG 里有孤儿", isinstance(note.get("orphans"), list) and len(note["orphans"]) >= 1,
       len(note.get("orphans") or []))

fa = disks.card_fp(os.path.join(A, "card.db"))
fb = disks.card_fp(os.path.join(B, "card.db"))
ok("reseed 后前缀判定为真", disks._prefix_match(fb, fa) is True)

# Windows：连接没关时 rmtree 会失败（文件被占用）
for _c in ("_con_a", "_con_b"):
    try:
        globals()[_c].close()
    except Exception:
        pass
shutil.rmtree(tmp, ignore_errors=True)
print("\n临时目录已删:", not os.path.exists(tmp))
print("\n结果：%d 过 / %d 挂" % (PASS, FAIL))
raise SystemExit(0 if FAIL == 0 else 1)
