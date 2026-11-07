# -*- coding: utf-8 -*-
"""
EmotionEngine —— RPE + 神经调质动力学核心内核（纯标准库，单轮 <1ms）。

每轮 7 步，对应文档的逻辑骨架：
  ① 安全评估   决定亲密通路开 / 关（OT 门槛，安全感优先于一切）
  ② 价值打分   评价层特征 → V_actual（0~1）
  ③ 预测取用   V_expected（我方主动发起时用"预激活"缓存）
  ④ 计算 RPE   RPE = V_actual - V_expected
  ⑤ 脉冲注入   五个调质按 增益 × 适应 × 疲劳 × 内源情绪 注入
  ⑥ 半衰期回落 各调质按自己的 HL 回落基线（制造情绪惯性/残留）
  ⑦ 学习与漂移 TD 更新预期；基线缓慢漂移后强制回归出厂值（防情绪永久塌陷）
"""

from __future__ import annotations

import json
import math
import os
import random
import time
from typing import Any, Dict, List, Optional, Tuple

from .config import DEFAULTS, EMOTION_LABELS
from .appraisal import Appraisal

MODS = ("DA", "NE", "OT", "5HT", "CORT")

SCENARIO_BIAS = {
    "jealous": {"jealous": 0.30, "loss": 0.12},
    "praise":  {"joy": 0.10, "shy": 0.08},
    "flirt":   {"shy": 0.14, "heart": 0.12},
    "reject":  {"fear": 0.12, "loss": 0.15},
    "apology": {"guilt": 0.12},
    "comfort": {"calm": 0.14},
    "neglect": {"anxious": 0.12, "loss": 0.10},
}


def clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def sig(x: float) -> float:
    if x < -30:
        return 0.0
    if x > 30:
        return 1.0
    return 1.0 / (1.0 + math.exp(-x))


class EmotionEngine:
    """一个角色一份状态。可持久化到 JSON，重启后继续。"""

    def __init__(self, cfg: Optional[dict] = None, name: str = "default",
                 state_dir: Optional[str] = None, autosave: bool = True):
        # ---- 配置：深合并，允许只改想改的字段 ----
        self.cfg = json.loads(json.dumps(DEFAULTS))
        if cfg:
            self._merge(self.cfg, cfg)
        self.name = name
        self.state_dir = state_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")
        os.makedirs(self.state_dir, exist_ok=True)
        self.path = os.path.join(self.state_dir, f"{name}.json")
        self.autosave = autosave

        # ---- 出厂基线（回归锚点，永不改变） ----
        self.factory: Dict[str, float] = dict(self.cfg["baseline"])
        self.base: Dict[str, float] = dict(self.factory)

        # ---- 瞬时状态 ----
        self.st: Dict[str, float] = dict(self.factory)
        self.safety: float = 0.72
        self.fatigue: float = 0.05
        self.mood: float = 0.0        # 内源情绪偏置（与对话无关的起伏）
        self.turn: int = 0
        self.last_ts: float = time.time()

        # ---- 学习记忆 ----
        self.expect_global: float = 0.52
        self.expect: Dict[str, float] = {}     # 场景 → 预期价值
        self.rep: Dict[str, float] = {}        # 场景 → 重复计数（神经适应）
        self.pending: Optional[Dict[str, Any]] = None   # 预激活缓存

        self._load()

    # ------------------------------------------------------------------ 工具
    @staticmethod
    def _merge(dst: dict, src: dict) -> None:
        for k, v in src.items():
            if isinstance(v, dict) and isinstance(dst.get(k), dict):
                EmotionEngine._merge(dst[k], v)
            else:
                dst[k] = v

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
            self.base.update(d.get("base", {}))
            self.st.update(d.get("state", {}))
            self.safety = d.get("safety", self.safety)
            self.fatigue = d.get("fatigue", self.fatigue)
            self.mood = d.get("mood", self.mood)
            self.turn = d.get("turn", 0)
            self.last_ts = d.get("last_ts", time.time())
            self.expect_global = d.get("expect_global", self.expect_global)
            self.expect = d.get("expect", {})
            self.rep = d.get("rep", {})
            self.apply_wallclock_gap()      # 关了几天再打开，情绪先自然回落
        except Exception as e:      # 状态文件损坏不应阻断运行
            print(f"[emotion] 状态读取失败，按全新开始：{e}")

    def save(self) -> None:
        d = {
            "name": self.name,
            "turn": self.turn,
            "state": self.st,
            "base": self.base,
            "safety": self.safety,
            "fatigue": self.fatigue,
            "mood": self.mood,
            "expect": self.expect,
            "expect_global": self.expect_global,
            "rep": self.rep,
            "last_ts": self.last_ts,
        }
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)
        self.last_ts = time.time()

    def reset(self) -> None:
        self.base = dict(self.factory)
        self.st = dict(self.factory)
        self.safety, self.fatigue, self.mood = 0.72, 0.05, 0.0
        self.turn = 0
        self.expect_global, self.expect, self.rep = 0.52, {}, {}
        self.pending = None
        if os.path.exists(self.path):
            os.remove(self.path)

    def apply_wallclock_gap(self) -> None:
        """真实时间流逝也应当让情绪冷却（不是只有对话轮次才算时间）。"""
        gap = time.time() - self.last_ts
        turns = min(gap / 90.0, 40.0)     # 90 秒 ≈ 1 轮
        if turns > 0.2:
            self.decay(turns)

    # ------------------------------------------------------------- ① 安全评估
    def _update_safety(self, ap: Appraisal) -> None:
        s = self.cfg["safety"]
        threat = max(ap.threat, ap.boundary) * (1.0 - 0.75 * ap.repair)
        target = clip(0.45 * self.st["OT"] + 0.45 * (1.0 - threat) + 0.10 * (1.0 - ap.cold)
                      + 0.30 * ap.repair)  # 道歉/安抚能把安全感真正拉回来
        if ap.empathy_pain > 0.3:      # 共情场景：我在意你，安全反而升高
            target = clip(target + 0.10)
        rate = s["down_rate"] if target < self.safety else s["up_rate"]
        self.safety += (target - self.safety) * rate
        self.safety = clip(self.safety)

    def gate(self) -> float:
        """亲密通路开度 0~1：安全感不足时，再强的暧昧也只会变成紧张。"""
        s = self.cfg["safety"]
        return clip((self.safety - s["gate_lo"]) / max(1e-6, s["gate_hi"] - s["gate_lo"]))

    # ------------------------------------------------------------- ② 价值打分
    def value_of(self, ap: Appraisal) -> float:
        g = self.gate()
        vy = clip(0.50
                  + 0.30 * ap.praise
                  + 0.28 * ap.care
                  + 0.14 * ap.share
                  + 0.30 * ap.intimacy * (0.25 + 0.75 * g)   # 不安全时亲密不算正分
                  + 0.28 * (ap.attention - 0.5)
                  - 0.30 * ap.cold
                  - 0.60 * ap.threat
                  - 0.50 * ap.boundary
                  - 0.12 * ap.neglect)
        return clip(vy)

    # ------------------------------------------------------------- ③ 预测取用
    def expected_for(self, scenario: str) -> float:
        if scenario in self.expect:
            return self.expect[scenario]
        return self.expect_global

    def preactivate(self, scenario: str = "generic", predicted: Optional[float] = None,
                    intensity: float = 0.5) -> None:
        """
        预激活：我方主动开口/试探之前调用。
        鼓起勇气本身就会先抬 NE、小幅抬 CORT（"还没开口就有点忐忑"），
        并把这一轮的主观预期锁进 pending，等你的回复到来再结算 RPE。
        """
        exp_v = self.expected_for(scenario) if predicted is None else clip(predicted)
        self.pending = {"scenario": scenario, "expected": exp_v}
        self.st["NE"] = clip(self.st["NE"] + 0.10 * intensity)
        self.st["CORT"] = clip(self.st["CORT"] + 0.07 * intensity)
        self.st["DA"] = clip(self.st["DA"] + 0.03 * intensity)

    # ------------------------------------------------------------- ④~⑦ 主循环
    def step(self, ap: Appraisal, dt: float = 1.0) -> "TurnResult":
        p = self.cfg
        self.turn += 1

        # 0) 先让上一轮的情绪按时间自然回落 —— 再接受这一轮的刺激。
        #    顺序很关键：若先注入再衰减，同一轮就会吃掉 1/3 的脉冲，强度永远不够。
        self.decay(dt)

        # ① 安全
        self._update_safety(ap)

        # ② 实际价值
        v_actual = self.value_of(ap)

        # ③ 预期
        if self.pending is not None:
            scenario = self.pending["scenario"]
            v_exp = self.pending["expected"]
            self.pending = None
        else:
            scenario = ap.scenario or "generic"
            v_exp = self.expected_for(scenario)
        v_exp = clip(v_exp, 0.02, 0.98)

        # ④ RPE（学习用原始 rpe；情绪反应用放大后的 rpe_eff）
        rpe = clip(v_actual - v_exp, -1.0, 1.0)
        rpe_eff = clip(rpe * p.get("rpe_gain", 1.0), -1.0, 1.0)
        pos, neg = max(0.0, rpe_eff), max(0.0, -rpe_eff)

        # ⑤ 脉冲
        rep = self.rep.get(scenario, 1.0)
        adapt = max(p["adapt_floor"], 1.0 / (1.0 + p["adapt_strength"] * max(0.0, rep - 1.0)))
        fat_f = 1.0 - p["fatigue"]["gain_penalty"] * self.fatigue
        mood_f = 1.0 + p["endogenous"]["da_gain"] * self.mood

        inj = p["inject"]
        d: Dict[str, float] = {}

        # 正/负 RPE 的主效应
        d["DA"] = (p["gain_pos"]["DA"] * pos + p["gain_neg"]["DA"] * neg) * adapt * fat_f * mood_f
        d["NE"] = (p["gain_pos"]["NE"] * pos + p["gain_neg"]["NE"] * neg)
        d["OT"] = (p["gain_pos"]["OT"] * pos + p["gain_neg"]["OT"] * neg)
        d["5HT"] = (p["gain_pos"]["5HT"] * pos + p["gain_neg"]["5HT"] * neg)
        d["CORT"] = (p["gain_pos"]["CORT"] * pos + p["gain_neg"]["CORT"] * neg)

        # 外部事件直接注入（威胁 / 越界 / 冷淡 / 亲密 / 关心 各有独立通路）
        for unit, table in (
            ("threat", inj["threat_per_unit"]),
            ("boundary", inj["boundary_per_unit"]),
            ("cold", inj["cold_per_unit"]),
            ("intimacy", inj["intimacy_per_unit"]),
            ("care", inj["care_per_unit"]),
        ):
            w = getattr(ap, unit, 0.0)
            if w <= 0:
                continue
            if unit == "intimacy":
                w *= (0.25 + 0.75 * self.gate())   # 不安全时亲密被削弱
            if unit == "threat":
                w = max(w, ap.boundary * 0.6)     # 越界同时触发威胁
            for k, v in table.items():
                d[k] += v * w

        # 共情通路（独立，优先级高于暧昧奖赏）
        if ap.empathy_pain > 0:
            d["CORT"] += inj["empathy_pain"]["CORT"] * ap.empathy_pain
            d["OT"] += inj["empathy_pain"]["OT"] * ap.empathy_pain
            d["DA"] -= 0.10 * ap.empathy_pain      # 暧昧奖赏暂时降级

        # 修复通路：道歉/安抚让应激真的降下来、依恋回来（"哄好"能起作用的机制）
        if ap.repair > 0:
            d["CORT"] -= 0.22 * ap.repair
            d["OT"] += 0.16 * ap.repair
            d["5HT"] -= 0.06 * ap.repair

        # 正向时相与 5HT 的拮抗：5HT 高会压住 NE 的冲动
        d["NE"] *= (1.0 - 0.35 * clip(self.st["5HT"] - 0.4))

        for k in MODS:
            self.st[k] = clip(self.st[k] + d[k])
        # 促性腺激素式"克制"：极强 NE + 高压 CORT 时不产生暧昧，直接进回避
        if ap.boundary > 0.5 or ap.threat > 0.7:
            self.st["DA"] = clip(self.st["DA"] - 0.20)

        # ⑦ TD 学习 + 基线漂移（衰减已在轮首完成，这里保留本轮脉冲给情绪分类）
        key = scenario
        self.expect[key] = clip(self.expect.get(key, self.expect_global) + p["td_alpha"] * rpe, 0.05, 0.95)
        self.expect_global = clip(self.expect_global + p["td_alpha_global"] * rpe, 0.10, 0.90)
        for k in list(self.rep):
            self.rep[k] *= 0.93
        self.rep[key] = self.rep.get(key, 0.0) + 1.0
        self.rep = {k: v for k, v in self.rep.items() if v > 0.05}

        self._drift(ap, rpe, v_actual)
        self._update_fatigue(dt)
        self._update_endogenous()

        # 情绪分类
        emos = self.classify(scenario, ap)
        dominant, score = emos[0] if emos else ("calm", 0.5)
        intensity = score

        res = TurnResult(
            turn=self.turn, v_actual=v_actual, v_expect=v_exp, rpe=rpe,
            scenario=scenario, emotions=emos, dominant=dominant,
            intensity=intensity, state=dict(self.st),
            safety=self.safety, fatigue=self.fatigue, gate=self.gate(),
            pulsed=d,
        )
        if self.autosave:
            self.save()
        return res

    # 半衰期回落 ------------------------------------------------------------
    def decay(self, dt: float = 1.0) -> None:
        hl = self.cfg["half_life"]
        for k in MODS:
            b = self.base[k]
            self.st[k] = b + (self.st[k] - b) * (0.5 ** (dt / hl[k]))
        self.st = {k: clip(v) for k, v in self.st.items()}

    # 基线漂移（带强制回归） ------------------------------------------------
    def _drift(self, ap: Appraisal, rpe: float, v_actual: float) -> None:
        p = self.cfg
        dr = p["drift"]
        pos, neg = max(0.0, rpe), max(0.0, -rpe)
        g = self.gate()

        ot_sig = (0.55 * ap.intimacy * g + 0.45 * ap.care + 0.35 * pos
                  - 0.50 * neg - 1.20 * ap.threat - 1.50 * ap.boundary
                  + 0.25 * ap.empathy_pain)
        cort_sig = (0.85 * neg + 1.10 * ap.threat + 1.30 * ap.boundary
                    + 0.30 * ap.cold + 0.35 * ap.empathy_pain
                    - 0.80 * pos - 0.90 * ap.care)

        self.base["OT"] = clip(self.base["OT"] + dr["OT"] * ot_sig
                               - dr["reversion"] * (self.base["OT"] - self.factory["OT"]),
                               0.10, 0.90)
        self.base["CORT"] = clip(self.base["CORT"] + dr["CORT"] * cort_sig
                                 - dr["reversion"] * (self.base["CORT"] - self.factory["CORT"]),
                                 0.08, 0.80)
        self.base["DA"] = clip(self.base["DA"] + dr["DA"] * (pos * 0.7 - neg * 0.4)
                               - dr["reversion"] * (self.base["DA"] - self.factory["DA"]),
                               0.12, 0.85)
        self.base["5HT"] = clip(self.base["5HT"] + dr["5HT"] * (neg * 0.8 + ap.threat * 0.6 - pos * 0.5)
                                - dr["reversion"] * (self.base["5HT"] - self.factory["5HT"]),
                                0.25, 0.85)
        lo, hi = p["baseline_bounds"]
        self.base["OT"] = clip(self.base["OT"], lo, hi)
        self.base["CORT"] = clip(self.base["CORT"], lo, hi)

    def _update_fatigue(self, dt: float) -> None:
        f = self.cfg["fatigue"]
        over = self.st["CORT"] - f["cort_threshold"]
        if over > 0:
            self.fatigue += f["accumulate"] * (over * 2.0 + 0.2) * dt
        else:
            self.fatigue -= f["recover"] * dt * (1.0 - 0.3 * self.st["OT"])
        self.fatigue = clip(self.fatigue)

    def _update_endogenous(self) -> None:
        e = self.cfg["endogenous"]
        self.mood = clip(e["ar"] * self.mood + random.gauss(0.0, e["noise"]),
                         -e["bound"], e["bound"])

    # ------------------------------------------------------------- 情绪分类
    def classify(self, scenario: str = "", ap: Optional[Appraisal] = None) -> List[Tuple[str, float]]:
        s = self.st
        f = self.fatigue
        g = self.gate()
        slow = 0.35 + 0.65 * g        # 不安全时羞涩/心动被压掉
        # 只有 NE 真的被抬起来，才谈得上"害羞"；否则只是普通的平静
        ne_gate = clip((s["NE"] - 0.18) / 0.30) ** 0.8
        da_gate = 0.45 + 0.55 * clip((s["DA"] - 0.28) / 0.35)
        low_da = max(0.0, 0.30 - s["DA"])     # DA 掉下去才是"失落"，不是 OT 高就叫安稳

        raw: Dict[str, float] = {
            "shy":     sig(3.2 * s["NE"] + 1.7 * s["DA"] + 0.7 * s["OT"] - 3.15) * slow * ne_gate,
            "heart":   sig(3.0 * s["DA"] + 1.2 * s["NE"] + 0.7 * s["OT"] - 3.10) * slow * da_gate,
            "joy":     sig(2.6 * s["DA"] + 1.0 * s["NE"] + 0.7 * s["OT"] - 1.3 * s["CORT"] - 2.7),
            "calm":    sig(2.8 * s["OT"] + 1.3 * s["5HT"] - 2.2 * s["CORT"] - 1.2 * s["NE"]
                           - 0.9 * low_da - 1.9),
            "loss":    sig(2.2 * s["5HT"] + 1.6 * s["CORT"] + 2.0 * low_da
                           - 0.8 * s["OT"] - 3.0),
            "fear":    sig(3.2 * s["CORT"] + 1.4 * s["NE"] - 1.5 * s["OT"] - 2.4),
            "anxious": sig(2.2 * s["CORT"] + 1.4 * s["NE"] + 0.4 * s["5HT"] - 1.1 * s["OT"] - 2.5),
            "guilt":   sig(2.0 * s["CORT"] + 1.4 * s["5HT"] - 1.2 * s["DA"] - 2.5),
            "jealous": sig(1.8 * s["CORT"] + 0.8 * s["5HT"] - 1.0 * s["DA"] - 1.9),
            "tired":   sig(1.8 * f + 1.2 * s["CORT"] + 1.0 * s["5HT"] - 1.5 * s["DA"] - 2.1),
            "numb":    sig(2.2 * f + 1.6 * s["CORT"] - 2.0 * s["DA"] - 1.5 * s["OT"] - 1.2),
        }

        # 事件线索加成：这一轮"发生了什么"必须参与分类，否则被敷衍也会算成安稳
        if ap is not None:
            cue = (
                ("shy",     0.10 * ap.intimacy + 0.06 * ap.praise),
                ("heart",   0.12 * ap.intimacy),
                ("joy",     0.10 * ap.praise + 0.08 * ap.share + 0.05 * ap.care),
                ("calm",    0.15 * ap.care),
                ("loss",    0.28 * ap.cold + 0.15 * ap.neglect + 0.06 * max(0.0, -ap.attention)),
                ("anxious", 0.20 * ap.neglect),
                ("guilt",   0.18 * ap.care * (1.0 if ap.empathy_pain > 0.3 else 0.0)),
                ("fear",    0.22 * ap.threat + 0.15 * ap.boundary),
            )
            for emo, bonus in cue:
                if emo in raw and bonus > 0:
                    raw[emo] = clip(raw[emo] + bonus)

        for emo, bonus in SCENARIO_BIAS.get(scenario, {}).items():
            if emo in raw:
                raw[emo] = clip(raw[emo] + bonus)

        ranked = sorted(raw.items(), key=lambda kv: kv[1], reverse=True)
        top_name, top_score = ranked[0]
        second_name, second_score = ranked[1]

        threshold = self.cfg["classify"]["min_score"]
        ratio = self.cfg["classify"]["secondary_ratio"]

        out: List[Tuple[str, float]] = []
        if top_score >= threshold:
            out.append((top_name, top_score))
        # 次情绪：需要确实不弱，才作为"底下压着的一点X"呈现
        if second_score >= max(threshold, 0.42) and second_score >= top_score * ratio:
            out.append((second_name, second_score))
        if not out:
            out.append(("calm", max(0.35, top_score)))

        # 真正的"五味杂陈"只在信号冲突时出现：
        #   一边有奖赏（DA/NE 高）一边有应激（CORT 高），或高依恋同时高戒备
        conflict = ((s["DA"] > 0.55 and s["CORT"] > 0.48)
                    or (s["CORT"] > 0.55 and s["OT"] > 0.55)
                    or (s["NE"] > 0.60 and s["CORT"] > 0.60))
        if conflict and top_name not in ("fear", "numb"):
            out[0] = ("mixed", max(top_score, 0.45))
        return out

    # ------------------------------------------------------------- 快照
    def snapshot(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "turn": self.turn,
            "state": {k: round(v, 4) for k, v in self.st.items()},
            "base": {k: round(v, 4) for k, v in self.base.items()},
            "safety": round(self.safety, 4),
            "gate": round(self.gate(), 4),
            "fatigue": round(self.fatigue, 4),
            "mood": round(self.mood, 4),
            "expect_global": round(self.expect_global, 4),
            "expect": {k: round(v, 3) for k, v in sorted(self.expect.items(), key=lambda kv: -kv[1])[:6]},
            "dominant": self.classify()[0][0],
        }


class TurnResult:
    """一轮结算结果。数值只给调试看，给 LLM 的只有 prompt.render 出的行为提示。"""

    __slots__ = ("turn", "v_actual", "v_expect", "rpe", "scenario", "emotions",
                 "dominant", "intensity", "state", "safety", "fatigue", "gate", "pulsed")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    @property
    def dominant_label(self) -> str:
        return EMOTION_LABELS.get(self.dominant, self.dominant)

    def to_dict(self) -> dict:
        return {
            "turn": self.turn,
            "scenario": self.scenario,
            "v_actual": round(self.v_actual, 3),
            "v_expect": round(self.v_expect, 3),
            "rpe": round(self.rpe, 3),
            "emotions": [(EMOTION_LABELS.get(k, k), round(v, 3)) for k, v in self.emotions],
            "dominant": self.dominant_label,
            "intensity": round(self.intensity, 3),
            "state": {k: round(v, 3) for k, v in self.state.items()},
            "safety": round(self.safety, 3),
            "gate": round(self.gate, 3),
            "fatigue": round(self.fatigue, 3),
            "pulsed": {k: round(v, 3) for k, v in self.pulsed.items()},
        }
