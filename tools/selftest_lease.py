# -*- coding: utf-8 -*-
"""写者租约 / 写者纪元自测（V7 搬家协议 P0-4）。

只在 %TEMP% 下建空目录跑，绝不打开活卡的 card.db。

跑法（项目根）：
    python -X utf8 tools\\selftest_lease.py

退出码 0 = 全过；1 = 有失败。
"""
from __future__ import print_function

import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import disks  # noqa: E402  （要把 ROOT 先塞进 sys.path）


def _check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name
          + (("  -- " + str(detail)) if (detail and not cond) else ""))
    return bool(cond)


def main():
    results = []
    base = tempfile.mkdtemp(prefix="whale-lease-selftest-")
    try:
        # 自证一遍：测试目录必须在 %TEMP% 里，别手滑指到活卡上。
        tmp = os.path.abspath(tempfile.gettempdir()).lower()
        if not os.path.abspath(base).lower().startswith(tmp):
            print("FAIL  测试目录不在 %%TEMP%% 里：%s" % base)
            return 1

        A = disks.default_holder("selftest-A", epoch=7, pid=os.getpid())
        B = disks.default_holder("selftest-B", epoch=7, pid=os.getpid())

        # ---- ① 两个不同 holder 抢：第二个必须失败 ----
        d1 = os.path.join(base, "card1")
        os.makedirs(d1)
        r1 = disks.acquire_lease(d1, A)
        r2 = disks.acquire_lease(d1, B)
        results.append(_check(
            "① 不同 holder 抢租约：第一个成功、第二个 lease_held",
            r1.get("ok") and not r2.get("ok")
            and r2.get("reason") == "lease_held"
            and (r2.get("holder") or {}).get("holder_id") == A["holder_id"],
            (r1, r2)))
        results.append(_check(
            "①b 被拒时带回了持有者和剩余秒数",
            isinstance(r2.get("remaining_s"), (int, float))
            and r2["remaining_s"] > 0,
            r2))

        # ---- ② TTL 过期后第二个能抢到（时间用 now 参数拨，不真等 90s）----
        t0 = float(time.time())
        r3 = disks.acquire_lease(d1, B, now=t0 + disks.LEASE_TTL + 1.0)
        results.append(_check(
            "② TTL 过期后第二个 holder 能抢到",
            r3.get("ok") and (r3.get("lease") or {}).get("holder_id") == B["holder_id"],
            r3))
        results.append(_check(
            "②b 抢到之后 lease_alive 恢复为 True",
            disks.lease_alive(d1, now=t0 + disks.LEASE_TTL + 2.0) is True,
            disks.lease_state(d1, now=t0 + disks.LEASE_TTL + 2.0)))

        # ---- ③ 心跳续租：过期前续上，过期点就整体后移 ----
        d2 = os.path.join(base, "card2")
        os.makedirs(d2)
        t1 = 1000000.0
        disks.acquire_lease(d2, A, ttl=90.0, now=t1)
        hb = disks.heartbeat_lease(d2, A["holder_id"], ttl=90.0, now=t1 + 80.0)
        results.append(_check("③ 自己的心跳续租成功", hb.get("ok"), hb))
        results.append(_check(
            "③b 续租后 t0+160 仍活着（原租约本来 t0+90 就该没了）",
            disks.lease_alive(d2, now=t1 + 160.0) is True,
            disks.lease_state(d2, now=t1 + 160.0)))
        results.append(_check(
            "③c 续租后别人仍抢不走",
            disks.acquire_lease(d2, B, now=t1 + 160.0).get("reason") == "lease_held",
            disks.acquire_lease(d2, B, now=t1 + 160.0)))
        wrong = disks.heartbeat_lease(d2, B["holder_id"], ttl=90.0, now=t1 + 161.0)
        results.append(_check(
            "③d 别人的 holder_id 续不了（不是自己的绝不抢）",
            not wrong.get("ok") and wrong.get("reason") == "not_holder",
            wrong))

        # ---- ④ release 之后别人能拿 ----
        d3 = os.path.join(base, "card3")
        os.makedirs(d3)
        disks.acquire_lease(d3, A)
        rel = disks.release_lease(d3, A["holder_id"])
        results.append(_check("④ 自己 release 成功", rel.get("ok") and rel.get("released"), rel))
        results.append(_check("④b release 之后 lease_alive 为 False",
                              disks.lease_alive(d3) is False,
                              disks.lease_state(d3)))
        taken = disks.acquire_lease(d3, B)
        results.append(_check(
            "④c release 之后 B 能拿到",
            taken.get("ok") and (taken.get("lease") or {}).get("holder_id") == B["holder_id"],
            taken))
        results.append(_check(
            "④d release 别人的租约被拒（not_holder）",
            disks.release_lease(d3, A["holder_id"]).get("reason") == "not_holder",
            disks.release_lease(d3, A["holder_id"])))

        # ---- ⑤ bump_writer_epoch 递增 ----
        d4 = os.path.join(base, "card4")
        os.makedirs(d4)
        e0 = disks.writer_epoch(d4)
        e1 = disks.bump_writer_epoch(d4, identity_id="selftest-id")
        e2 = disks.bump_writer_epoch(d4, identity_id="selftest-id")
        e3 = disks.bump_writer_epoch(d4, identity_id="selftest-id")
        doc = disks.writer_epoch_doc(d4)
        results.append(_check(
            "⑤ 纪元缺省 0，且每次 +1（0→1→2→3）",
            e0 == 0 and (e1, e2, e3) == (1, 2, 3)
            and disks.writer_epoch(d4) == 3 and int(doc.get("epoch") or -1) == 3,
            (e0, e1, e2, e3, doc)))
        results.append(_check(
            "⑤b 纪元文件记下了 identity_id 和写它的进程",
            doc.get("identity_id") == "selftest-id"
            and int(doc.get("by_pid") or 0) == os.getpid(),
            doc))
        results.append(_check(
            "⑤c 纪元写入不会顺手造出租约",
            not os.path.exists(os.path.join(d4, disks.WRITER_LEASE_NAME)),
            os.listdir(d4)))

        # ---- ⑥ 附加：epoch_check 在没搬家记录时不误报落后 ----
        led = disks.epoch_check(d4)
        results.append(_check(
            "⑥ 没有 TAKEOVER-DONE 时 epoch_check 不报 stale",
            led.get("stale") is False and led.get("epoch") == 3,
            led))

        # ---- ⑦ 搬家记录里的纪元更靠前 → stale=True（只告警，不改文件）----
        with open(os.path.join(d4, disks.TAKEOVER_DONE_NAME), "w",
                  encoding="utf-8") as f:
            json.dump({"state": "done", "writer_epoch": 9}, f)
        led2 = disks.epoch_check(d4)
        results.append(_check(
            "⑦ 盘上 epoch=3、接管记录=9 → stale，且没有偷偷改纪元",
            led2.get("stale") is True and led2.get("takeover_epoch") == 9
            and disks.writer_epoch(d4) == 3,
            led2))
    finally:
        shutil.rmtree(base, ignore_errors=True)

    bad = results.count(False)
    print("")
    print("%d/%d 通过" % (len(results) - bad, len(results)))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
