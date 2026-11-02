# -*- coding: utf-8 -*-
"""媒体记忆（画面）功能自测：不碰真卡，全程临时目录。"""
import hashlib
import os
import shutil
import tempfile
import time

import sys, os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import card2

DAY = card2.DAY
d = tempfile.mkdtemp()
db = os.path.join(d, "t.db")
c = card2.MemoryCard(db)
ok = []


def chk(name, cond, extra=""):
    ok.append((name, bool(cond), extra))
    print(("  ✅ " if cond else "  ❌ ") + name + (("  " + str(extra)) if extra else ""))


# 造一个真的"帧文件"（内容随便，能算 sha 就行）
media_dir = c.media_root()
raw = b"fake-webp-bytes-for-test"
sha = hashlib.sha256(raw).hexdigest()
rel = os.path.join("media", sha[:2], sha + ".webp")
abs_p = os.path.join(d, rel)
os.makedirs(os.path.dirname(abs_p), exist_ok=True)
open(abs_p, "wb").write(raw)
rel_thumb = os.path.join("media", sha[:2], sha + "_t.webp")
open(os.path.join(d, rel_thumb), "wb").write(b"thumb")

print("① 权重曲线（幂律）")
for days, expect in [(1, 0.68), (7, 0.32), (30, 0.15), (60, 0.10)]:
    w = c.media_weight_now(1.0, 0.0, now=days * DAY)
    chk("第 %d 天 ≈ %.2f（实测 %.3f）" % (days, expect, w), abs(w - expect) < 0.02)

print("② 挂载 + 去重 + 共享")
m1 = c.remember("晚上在路边摊吃烧烤，很开心", salience=0.8)
m2 = c.remember("那次烧烤摊的辣椒特别辣", salience=0.7)
m3 = c.remember("和同学一起去的路边摊", salience=0.6)
r1 = c.attach_media(m1, sha, caption="路边摊吃烧烤，还拍了视频",
                    tags=["烧烤", "同学"], path=rel, thumb=rel_thumb, nbytes=len(raw))
chk("第一次挂载 ok", r1.get("ok") and r1.get("dup") is False, r1)
r2 = c.attach_media(m2, sha, caption="辣椒特别辣", path=rel, nbytes=len(raw))
chk("同一文件第二次挂载 → 复用（dup=True）", r2.get("ok") and r2.get("dup") is True, r2)
m_id = r1["media_id"]
lst = c.media_of(m1)
chk("m1 身上有 1 张画面", len(lst) == 1 and lst[0]["id"] == m_id, [x["id"] for x in lst])

print("③ 看一眼会加强（比文字猛）")
before = c.media_of(m1)[0]["w"]
c.db.execute("UPDATE media SET t_anchor=? WHERE id=?", (time.time() - 7 * DAY, m_id))
c.db.commit()
after_gain = c.media_touch(m_id)
chk("七天后看一眼：加强且权重回升", after_gain["ok"] and after_gain["w"] > 0.4, after_gain)

print("④ 共享引用：删两条还剩，删完进残影")
c._forget_row(c.db.cursor(), m2, "测试删除")
c.db.commit()
chk("删 m2 后画面还在", len(c.media_of(m1)) == 1)
c._forget_row(c.db.cursor(), m1, "测试删除")
c.db.commit()
row = c.db.execute("SELECT orphan_ts FROM media WHERE id=?", (m_id,)).fetchone()
chk("m1 也删了 → 进残影（orphan_ts>0）", row is not None and row["orphan_ts"] > 0, row)

print("⑤ 残影期内打开能救回")
c.media_touch(m_id)
row = c.db.execute("SELECT orphan_ts FROM media WHERE id=?", (m_id,)).fetchone()
chk("打开一次 → 残影期清零", row["orphan_ts"] == 0, row)

print("⑥ 到线删除 + 台账 + 文字不动")
c.db.execute("UPDATE media SET t_anchor=?, w0=1.0, orphan_ts=0 WHERE id=?",
             (time.time() - 70 * DAY, m_id))
c.db.commit()
ev = c._media_tick()
chk("70 天没翻 → 被删", any(e[1] == "dropped" for e in ev), ev)
chk("文件真的没了", not os.path.exists(abs_p))
tomb = c.db.execute("SELECT * FROM media_tomb WHERE sha256=?", (sha,)).fetchone()
chk("台账里有账，且留着当时那句话",
    tomb is not None and "烧烤" in (tomb["caption"] or ""), dict(tomb) if tomb else None)
chk("文字记忆 m3 完好", c.db.execute("SELECT COUNT(*) FROM mem WHERE id=?", (m3,)).fetchone()[0] == 1)

print("⑦ 锁定画面不删")
raw2 = b"pinned-frame"
sha2 = hashlib.sha256(raw2).hexdigest()
rel2 = os.path.join("media", sha2[:2], sha2 + ".webp")
os.makedirs(os.path.dirname(os.path.join(d, rel2)), exist_ok=True)
open(os.path.join(d, rel2), "wb").write(raw2)
r3 = c.attach_media(m3, sha2, caption="锁住的画面", path=rel2, nbytes=len(raw2), pinned=1)
c.db.execute("UPDATE media SET t_anchor=?, w0=1.0 WHERE id=?", (time.time() - 400 * DAY, r3["media_id"]))
c.db.commit()
ev2 = c._media_tick()
chk("pinned=1 权重掉到极低也不删",
    all(e[0] != r3["media_id"] for e in ev2) and c.media_stats()["n"] >= 0, ev2)

print("⑧ verify + stats")
v = c.verify_media()
chk("verify 通过（没有丢文件）", v["ok"], v)
chk("verify 记得删过几张", v["tomb_n"] >= 1, v["tomb_n"])
st = c.media_stats()
chk("stats 带双曲线参数", st["decay"]["b"] == card2.MEDIA_DECAY_B, st["decay"])

print("⑨ 手工删文件 → verify 报坏")
os.remove(os.path.join(d, rel2))
v2 = c.verify_media()
chk("文件被拿走 → 报坏（不是已知删除）", (not v2["ok"]) and any("不见了" in b for b in v2["bad"]), v2["bad"])

fails = [n for n, good, _ in ok if not good]
print("\n结果：%d/%d 通过" % (len(ok) - len(fails), len(ok)))
if fails:
    print("失败：", fails)
shutil.rmtree(d, ignore_errors=True)
raise SystemExit(1 if fails else 0)

