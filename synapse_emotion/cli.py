# -*- coding: utf-8 -*-
"""
本地命令行 / 本地 HTTP 服务。给 DSH、llama.cpp、任意前端调用。

常用：
  python -m synapse_emotion.cli step --text "你今天好可爱" --scenario flirt
  python -m synapse_emotion.cli preactivate --scenario flirt --predicted 0.75
  python -m synapse_emotion.cli state
  python -m synapse_emotion.cli demo
  python -m synapse_emotion.cli serve --port 8765
  python -m synapse_emotion.cli reset
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .engine import EmotionEngine
from .appraisal import estimate_appraisal, Appraisal
from .prompt import render_behavior_block, render_compact

DEFAULT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state")


def _engine(name: str) -> EmotionEngine:
    return EmotionEngine(name=name, state_dir=DEFAULT_DIR)


def cmd_step(a) -> None:
    eng = _engine(a.name)
    if a.preactivate:
        eng.preactivate(a.scenario, a.predicted, a.probe_intensity)
    ap = Appraisal.from_llm_dict(json.loads(a.json)) if a.json else \
        estimate_appraisal(a.text, scenario=a.scenario, is_probe=a.preactivate, predicted=a.predicted)
    res = eng.step(ap)
    if a.json_out:
        print(json.dumps({"result": res.to_dict(), "block": render_behavior_block(res, a.subject)},
                         ensure_ascii=False, indent=2))
    else:
        print(f"\n[第{res.turn}轮] 场景={res.scenario}  V实际={res.v_actual:.2f}  "
              f"V预期={res.v_expect:.2f}  RPE={res.rpe:+.2f}")
        print("情绪：" + " / ".join(f"{k}{v:.2f}" for k, v in
                                  [(res.dominant_label, res.intensity)]))
        print("调质：" + "  ".join(f"{k}={v:.2f}" for k, v in res.state.items()))
        print()
        print(render_behavior_block(res, a.subject))
    if a.compact:
        print("\n[极简版]\n" + render_compact(res, a.subject))


def cmd_pre(a) -> None:
    eng = _engine(a.name)
    eng.preactivate(a.scenario, a.predicted, a.probe_intensity)
    eng.save()
    print(f"已预激活：场景={a.scenario} 预期V={eng.pending['expected']:.2f} "
          f"NE={eng.st['NE']:.2f} CORT={eng.st['CORT']:.2f}")


def cmd_state(a) -> None:
    print(json.dumps(_engine(a.name).snapshot(), ensure_ascii=False, indent=2))


def cmd_reset(a) -> None:
    _engine(a.name).reset()
    print("已重置为该角色的出厂状态")


def cmd_demo(a) -> None:
    from .demo import run_demo
    run_demo(name=a.name)


# ---------------------------------------------------------------- HTTP 服务
class _Handler(BaseHTTPRequestHandler):
    engine: EmotionEngine = None   # type: ignore

    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):      # 静默，别刷屏
        pass

    def do_GET(self) -> None:
        if self.path.startswith("/health"):
            self._send(200, {"ok": True, "turn": self.engine.turn})
        elif self.path.startswith("/state"):
            self._send(200, self.engine.snapshot())
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        try:
            n = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            self._send(400, {"error": f"bad json: {e}"})
            return

        try:
            if self.path.startswith("/preactivate"):
                self.engine.preactivate(payload.get("scenario", "generic"),
                                        payload.get("predicted"), payload.get("intensity", 0.5))
                self.engine.save()
                self._send(200, {"ok": True, "pending": self.engine.pending})

            elif self.path.startswith("/step"):
                if payload.get("json"):
                    ap = Appraisal.from_llm_dict(payload["json"])
                else:
                    ap = estimate_appraisal(payload.get("text", ""),
                                            scenario=payload.get("scenario", "generic"),
                                            is_probe=payload.get("is_probe", False),
                                            predicted=payload.get("predicted"))
                res = self.engine.step(ap)
                self._send(200, {
                    "ok": True,
                    "result": res.to_dict(),
                    "block": render_behavior_block(res, payload.get("subject", "她")),
                    "compact": render_compact(res, payload.get("subject", "她")),
                })

            elif self.path.startswith("/reset"):
                self.engine.reset()
                self._send(200, {"ok": True})

            else:
                self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"error": str(e)})

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


def cmd_serve(a) -> None:
    _Handler.engine = _engine(a.name)
    srv = ThreadingHTTPServer((a.host, a.port), _Handler)
    print(f"情绪引擎已启动： http://{a.host}:{a.port}")
    print("  GET  /state      查看内部状态（仅调试）")
    print("  POST /preactivate 主动开口前预激活")
    print("  POST /step        传一句用户回复，返回行为提示")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="SYNAPSE-1024 情绪引擎")
    p.add_argument("--name", default="default", help="角色存档名")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("step", help="输入一句对方的话，结算一轮情绪")
    s.add_argument("--text", default="")
    s.add_argument("--json", default="", help="直接传评价 JSON（接小模型打分用）")
    s.add_argument("--scenario", default="generic")
    s.add_argument("--preactivate", action="store_true", help="本轮是我方主动发起")
    s.add_argument("--predicted", type=float, default=None)
    s.add_argument("--probe-intensity", type=float, default=0.5)
    s.add_argument("--subject", default="她")
    s.add_argument("--json-out", action="store_true")
    s.add_argument("--compact", action="store_true")
    s.set_defaults(func=cmd_step)

    q = sub.add_parser("preactivate", help="主动开口前调用，产生鼓起勇气的忐忑")
    q.add_argument("--scenario", default="generic")
    q.add_argument("--predicted", type=float, default=None)
    q.add_argument("--probe-intensity", type=float, default=0.5)
    q.set_defaults(func=cmd_pre)

    st = sub.add_parser("state", help="打印内部状态")
    st.set_defaults(func=cmd_state)

    rs = sub.add_parser("reset", help="重置为该角色出厂状态")
    rs.set_defaults(func=cmd_reset)

    dm = sub.add_parser("demo", help="跑 60 轮脚本演示")
    dm.set_defaults(func=cmd_demo)

    sv = sub.add_parser("serve", help="启动本地 HTTP 服务")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8765)
    sv.set_defaults(func=cmd_serve)

    a = p.parse_args(argv)
    a.func(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
