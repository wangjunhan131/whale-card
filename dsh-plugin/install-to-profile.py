# -*- coding: utf-8 -*-
"""把 dsh-whale-bridge 接进 profile 的 patch 层。幂等：已经接过就跳过。

2026-11-07 修：原来这里写死了三处「这台机器的」路径（profile 位置、卡目录、python 路径），
而这份脚本是打包进便携包的 —— 搬到新机器上会往人家的 DSH 里塞旧机器的路径，
症状是「插件装了、但连的是不存在的卡」。
现在全部按「本文件在哪」和「当前解释器是谁」现算。
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))          # <卡>/dsh-plugin
CARD = os.path.dirname(HERE)                                # <卡>
DSH_HOME = os.environ.get("DSH_HOME") or os.path.join(os.path.expanduser("~"), ".dsh")
PROFILE = os.environ.get("DSH_PROFILE") or "desktop"
P = os.path.join(DSH_HOME, "profiles", PROFILE, "cordis.patch.yml")

def _q(s):
    """YAML 单引号风格：'...'，内部的 ' 写成 ''。"""
    return "'" + s.replace("'", "''") + "'"


BLOCK = """
# ===== 接入 dsh-whale-bridge（卡的原生通道）=====
# 这个插件直接走 HTTP 打 8848，不碰 pnpm、不碰 MCP、不碰卡的数据库。
# cardDir / pythonPath 由 dsh-plugin/install-to-profile.py 按**这台机器**现算写入。
- id: whale-bridge
  name: "dsh-whale-bridge"
  config:
    host: 127.0.0.1
    port: 8848
    cardDir: {card}
    pythonPath: {py}
    autoStart: true
    timeoutMs: 30000
    healthTimeoutMs: 3000
""".format(card=_q(CARD), py=_q(sys.executable))


def main():
    if not os.path.exists(P):
        print("找不到 %s" % P)
        print("  → DSH 还没装过。先装 DSH，再跑这个。")
        return 1

    with io.open(P, "r", encoding="utf-8") as f:
        txt = f.read()

    if "- id: whale-bridge" in txt:
        print("已经接过，跳过（不重复写）。")
        print("  卡目录：%s" % CARD)
        print("  python：%s" % sys.executable)
        return 0

    new = txt.rstrip("\n") + "\n" + BLOCK
    with io.open(P, "w", encoding="utf-8", newline="\n") as f:
        f.write(new)
    print("已追加：%d -> %d 字节" % (len(txt.encode()), len(new.encode())))
    print("  卡目录：%s" % CARD)
    print("  python：%s" % sys.executable)

    try:
        import yaml
        with io.open(P, "r", encoding="utf-8") as f:
            doc = yaml.safe_load(f)
        hit = [e for e in (doc or [])
               if isinstance(e, dict) and e.get("id") == "whale-bridge"]
        print("YAML 解析 OK，whale-bridge 条目 %d 条" % len(hit))
        if hit:
            cfg = hit[0].get("config") or {}
            print("  cardDir   = %s" % cfg.get("cardDir"))
            print("  pythonPath= %s" % cfg.get("pythonPath"))
    except Exception as e:
        print("YAML 校验没做成（已写入，不影响）：%s" % e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
