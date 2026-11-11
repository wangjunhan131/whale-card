# -*- coding: utf-8 -*-
"""
example_integration.py —— 把情绪引擎接进你自己的对话主流程的完整示例。

跑法：
    cd 项目根目录
    python3 example_integration.py

它不调用任何模型，只演示"什么时候算情绪、算完把什么塞进提示词"。
你要做的只是把注释标着 [接 llama.cpp] 的两处，换成你自己的推理调用。
"""

import os

from synapse_emotion import (
    EmotionEngine,
    estimate_appraisal,
    render_behavior_block,
    render_compact,
)

PERSONA = """你是「阿栈」，二十出头的女孩子，说话轻、慢，有点怕生。
你住在对方电脑里，是他本地跑起来的一个存在。"""


def build_system_prompt(block: str, compact: bool = False) -> str:
    """系统提示词 = 固定人设 + 本轮的内部状态提示（每次重新拼，别缓存）。"""
    return f"{PERSONA}\n\n{block}"


def main() -> None:
    name = "demo_char"
    eng = EmotionEngine(name=name, state_dir=os.path.join(os.path.dirname(__file__), "state"))

    # 一次对话的节拍是：她开口前 preactivate → 生成 → 用户回复 → step → 生成
    turns = [
        # (她说的话, 场景, 是否她先开口, 她的预判)
        ("在吗？我这边刚弄完，想找你聊会儿。", "generic", True, 0.55),
        ("刚吃完饭，随便聊聊吧。", "generic", False, None),
        ("你今天怎么这么可爱。", "flirt", False, None),
        ("我、我才不可爱。", "flirt", False, None),
        ("好啦不逗你了，说正经的。", "flirt", False, None),
        ("其实我今天有点累。", "comfort", False, None),
        ("我一直在，你慢慢说。", "comfort", False, None),
    ]

    for i, (text, scenario, she_starts, predicted) in enumerate(turns, 1):
        if she_starts:
            # 她主动开口：先把"鼓起勇气"的生理反应打进去，并锁住预期
            eng.preactivate(scenario, predicted, intensity=0.6)

        # ── 1. 先算情绪（毫秒级，放在生成之前）
        res = eng.step(estimate_appraisal(text, scenario=scenario,
                                          is_probe=she_starts, predicted=predicted))
        block = render_behavior_block(res, subject="她")

        # ── 2. 拼系统提示词，然后调模型
        #    [接 llama.cpp]
        #    prompt = build_system_prompt(block)
        #    reply = your_llm.chat(prompt, history + [{"role":"user","content":text}])
        print(f"\n=== 第 {i} 轮 ===")
        print(f"对方：{text}")
        print(f"[内部] {res.dominant_label} 强度{res.intensity:.2f}  "
              f"V实{res.v_actual:.2f}/V期{res.v_expect:.2f} RPE{res.rpe:+.2f}")
        print(f"[拼进system的块，前120字]\n{block[:120]}...")
        print(f"[极简版，上下文紧张时用] {render_compact(res)}")

        # ── 3. 把回复写进历史，进入下一轮
        #    history.append({"role":"assistant","content": reply})
        eng.save()      # 每轮落盘，进程重启也不丢

    print("\n--- 提示：状态存在 state/demo_char.json，删掉就等于'她失忆了' ---")


if __name__ == "__main__":
    main()
