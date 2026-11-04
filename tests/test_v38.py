# -*- coding: utf-8 -*-
"""把 V3.8 白皮书 §12/§13 的伪代码落成真代码，验六件事。"""
import math, time, json, random
from typing import List, Dict, Optional

USER_ID = "user_syh"
T_S, T_CUT = 7*86400, 3*86400
LAM_S1, LAM_S2 = 1.8e-6, 1.5e-5
LAM_M = 0.15 * LAM_S1
W_FLOOR_MEDIUM, W_FLOOR_LONG = 0.08, 0.15
LAM_L = 0.08 * LAM_M
N_SHORT_MAX = 32
THETA_MEDIUM, THETA_LONG = 0.35, 0.60
W_MAX = 1.0

_FAKE_NOW = None
def now():
    return _FAKE_NOW if _FAKE_NOW is not None else time.time()

def lam_for_pool(pool):
    return {"medium": LAM_M, "long": LAM_L}.get(pool, LAM_S1)


class MemorySpine:
    def __init__(self, raw_text, entities, emotion, w0=None, t_create=None):
        t = now() if t_create is None else t_create
        self.t_create = t
        self.t_anchor = t
        self.t_raw_expire = t + T_S
        self.raw_text = raw_text
        self.subject = None; self.predicate = None; self.object = None
        self.emotion_vec = list(emotion)
        self.linked_entities = list(entities)
        self.c_recall = 0
        self.pool = "short"
        if w0 is None:
            e = math.sqrt(sum(x*x for x in self.emotion_vec))
            w0 = min(max(0.35 + 0.35*min(e/2.0, 1.0), 0.35), 0.70)
        self.w_now = w0

    def current_weight(self):
        dt = now() - self.t_anchor
        if self.pool == "short":
            if dt <= T_CUT:
                return self.w_now * math.exp(-LAM_S1*dt)
            w_cut = self.w_now * math.exp(-LAM_S1*T_CUT)
            return w_cut * math.exp(-LAM_S2*(dt-T_CUT))
        return self.w_now * math.exp(-lam_for_pool(self.pool)*dt)

    def reanchor(self, new_w):
        self.w_now = max(0.0, min(new_w, W_MAX))
        self.t_anchor = now()

    def move_to(self, pool):
        w = self.current_weight()
        floor = {"medium": W_FLOOR_MEDIUM, "long": W_FLOOR_LONG}.get(pool, 0.0)
        self.pool = pool
        self.reanchor(max(w, floor))

    def decay_apply(self):
        w = self.current_weight()
        p = 1.0 - w
        if p > 0.70 and self.predicate is not None and random.random() < p:
            self.predicate = None
        if p > 0.90 and self.object is not None and random.random() < p:
            self.object = None

    def recall_reinforce(self):
        w = self.current_weight()
        gain = 0.08 / (1.0 + 0.15*self.c_recall)
        self.c_recall += 1
        self.reanchor(w + gain)

    def evict_score(self):
        dt = now() - self.t_create
        return self.current_weight() * (1.0 - dt/T_S)

    def raw_expired(self):
        return now() > self.t_raw_expire


def hippocampus_score(sp, oxy, a=0.40, b=0.20, g=0.25, d=0.15):
    e = math.sqrt(sum(x*x for x in sp.emotion_vec))
    e_hat = min(e/2.0, 1.0)
    c_ent = min(len(sp.linked_entities)/5.0, 1.0)
    c_rec = min(sp.c_recall/10.0, 1.0)
    o_hat = max(0.0, min(oxy, 1.0))
    return a*e_hat + b*c_ent + g*c_rec + d*o_hat


def recall_associate(input_entities, pools, top_k=4):
    cand = []
    for sp in pools:
        inter = set(input_entities) & set(sp.linked_entities)
        if inter:
            union = set(input_entities) | set(sp.linked_entities)
            cand.append((1.0 - len(inter)/max(len(union),1), sp))
    cand.sort(key=lambda x: x[0])
    return [sp for _, sp in cand[:top_k]]


class LifeCardManager:
    def __init__(self, ctx_base_n=3):
        self.ctx_base_n = ctx_base_n
        self.loaded = True
        self.card_meta = {"card_id":"whalegirl_001","name":"小鲸鱼","version":"V3.8"}
        self.personality = [0.62,0.75,0.41,0.58]
        self.mood_base = [0.2,0.1,0.05,0.0]
        self.oxy_bond = {USER_ID: 0.78}
        self.short_pool, self.medium_pool, self.long_pool = [], [], []

    def unload_card(self, save_before=False, path=None):
        if save_before and self.loaded and path:
            self.save_card(path)
        self.card_meta = {}; self.personality = []; self.mood_base = []
        self.oxy_bond = {}
        self.short_pool.clear(); self.medium_pool.clear(); self.long_pool.clear()
        self.loaded = False

    def load_card(self, path):
        data = json.load(open(path, encoding="utf-8"))
        self.unload_card(False)
        self.card_meta = data["card_meta"]
        self.personality = data["personality_vector"]
        self.mood_base = data["mood_base"]
        self.oxy_bond = data.get("oxy_bond_matrix", {})
        self.medium_pool = deserialize_spines(data.get("medium_memory_spines", []))
        self.long_pool = deserialize_spines(data.get("long_memory_spines", []))
        self.short_pool.clear(); self.loaded = True

    def save_card(self, path):
        payload = {"card_meta": self.card_meta,
                   "personality_vector": self.personality,
                   "mood_base": self.mood_base,
                   "oxy_bond_matrix": self.oxy_bond,
                   "medium_memory_spines": serialize_spines(self.medium_pool),
                   "long_memory_spines": serialize_spines(self.long_pool)}
        json.dump(payload, open(path,"w",encoding="utf-8"), indent=2, ensure_ascii=False)

    def add_interaction(self, raw, entities, emotion):
        if not self.loaded: return
        self.short_pool.append(MemorySpine(raw, entities, emotion))
        if len(self.short_pool) > N_SHORT_MAX:
            self._evict_one()

    def _evict_one(self):
        self.short_pool.sort(key=lambda x: x.evict_score())
        ev = self.short_pool.pop(0)
        ev.raw_text = None
        sc = hippocampus_score(ev, self.oxy_bond.get(USER_ID, 0.0))
        if sc > THETA_MEDIUM:
            ev.move_to("medium")
            self.medium_pool.append(ev)
            return "promoted"
        return "dropped"

    def tick(self):
        oxy = self.oxy_bond.get(USER_ID, 0.0)
        for sp in list(self.short_pool):
            sp.decay_apply()
            if sp.raw_expired():
                sp.raw_text = None
                sc = hippocampus_score(sp, oxy)
                if sc > THETA_MEDIUM:
                    sp.move_to("medium")
                    self.medium_pool.append(sp); self.short_pool.remove(sp)
                elif sp.current_weight() < 0.05:
                    self.short_pool.remove(sp)
        for sp in list(self.medium_pool):
            sp.decay_apply()
            sc = hippocampus_score(sp, oxy)
            if sc > THETA_LONG and sp.c_recall >= 2:
                sp.move_to("long")
                self.long_pool.append(sp); self.medium_pool.remove(sp)
            elif sp.current_weight() < 0.02:
                self.medium_pool.remove(sp)
        for sp in list(self.long_pool):
            sp.decay_apply()
            if sp.current_weight() <= 0.01:
                self.long_pool.remove(sp)

    def assemble_context(self, input_entities, token_budget=8000):
        base = [x.raw_text for x in self.short_pool[-self.ctx_base_n:] if x.raw_text]
        recalled = recall_associate(input_entities, self.medium_pool + self.long_pool)
        def spine_str(s):
            return f"【记忆骨架】主:{s.subject} 谓:{s.predicate} 宾:{s.object} 情绪:{s.emotion_vec}"
        def rt(t): return len(t)
        parts = list(base)
        total = sum(rt(p) for p in parts)
        while total > token_budget*0.7 and len(parts) > 1:
            total -= rt(parts.pop(0))
        for s in recalled:
            t = spine_str(s)
            if total + rt(t) > token_budget: break
            parts.append(t); total += rt(t)
        return "\n".join(parts)


def serialize_spines(pool):
    return [{"raw": s.raw_text, "sub": s.subject, "pred": s.predicate,
             "obj": s.object, "emo": s.emotion_vec, "ent": s.linked_entities,
             "w": s.w_now, "ta": s.t_anchor, "tc": s.t_create,
             "cr": s.c_recall, "pool": s.pool} for s in pool]

def deserialize_spines(data):
    out = []
    for d in data:
        s = MemorySpine(d["raw"], d["ent"], d["emo"], w0=d["w"], t_create=d["tc"])
        s.subject, s.predicate, s.object = d["sub"], d["pred"], d["obj"]
        s.t_anchor, s.c_recall, s.pool = d["ta"], d["cr"], d["pool"]
        out.append(s)
    return out


# ================= 验收 =================
def main():
    global _FAKE_NOW
    T0 = 1761700000.0
    ok = 0; fail = 0
    def check(name, cond, detail=""):
        nonlocal ok, fail
        if cond: ok += 1; print(f"  [OK] {name} {detail}")
        else: fail += 1; print(f"  [FAIL] {name} {detail}")

    print("\n=== 1. 新记忆初始权重不再是 0 ===")
    _FAKE_NOW = T0
    sp = MemorySpine("今天吃了烧烤", ["烧烤"], [0.8,0.0,0.0,0.0])
    check("w0 非零", sp.w_now > 0.35, f"w0={sp.w_now:.3f}")
    check("权重非零", sp.current_weight() > 0.35, f"w={sp.current_weight():.3f}")
    sp_low = MemorySpine("随手记", [], [0.0,0.0,0.0,0.0])
    check("低情绪也有底线", sp_low.w_now >= 0.35, f"w0={sp_low.w_now:.3f}")

    print("\n=== 2. 三级池衰减真的分级 ===")
    _FAKE_NOW = T0
    a = MemorySpine("x", ["e"], [0.6,0,0,0]); a.pool="short"
    b = MemorySpine("x", ["e"], [0.6,0,0,0]); b.pool="medium"
    c = MemorySpine("x", ["e"], [0.6,0,0,0]); c.pool="long"
    _FAKE_NOW = T0 + 10*86400
    ws, wm, wl = a.current_weight(), b.current_weight(), c.current_weight()
    check("短期忘最快", ws < wm < wl, f"short={ws:.4f} < medium={wm:.4f} < long={wl:.4f}")
    check("长期十天后还活着", wl > 0.3, f"long={wl:.4f}")

    print("\n=== 3. 分段双速率：第4天起确实变快 ===")
    _FAKE_NOW = T0
    s = MemorySpine("x", ["e"], [0.6,0,0,0])
    _FAKE_NOW = T0 + 2*86400
    w2 = s.current_weight()
    _FAKE_NOW = T0 + 6*86400
    w6 = s.current_weight()
    ratio = (w2 - w6) / w2
    check("6天时掉了八成以上", ratio > 0.8, f"2天={w2:.4f} 6天={w6:.4f} 掉{ratio*100:.1f}%")

    print("\n=== 4. 唤起能续命（锚点模型） ===")
    _FAKE_NOW = T0
    s2 = MemorySpine("x", ["e"], [0.6,0,0,0])
    _FAKE_NOW = T0 + 6*86400
    before = s2.current_weight()
    s2.recall_reinforce()
    after = s2.current_weight()
    check("想起一次权重回升", after > before, f"{before:.4f} -> {after:.4f}")
    _FAKE_NOW = T0 + 6*86400 + 1*86400
    after2 = s2.current_weight()
    check("续命后第二天仍高于原轨迹", after2 > before, f"{after2:.4f} > {before:.4f}")

    print("\n=== 5. 海马打分封顶在 [0,1] ===")
    _FAKE_NOW = T0
    big = MemorySpine("x", [f"e{i}" for i in range(50)], [1,1,1,1])
    big.c_recall = 100
    sc = hippocampus_score(big, 1.0)
    check("满分不超 1", sc <= 1.0 + 1e-9, f"score={sc:.4f}")
    small = MemorySpine("x", [], [0,0,0,0])
    sc2 = hippocampus_score(small, 0.0)
    check("零分不低于 0", sc2 >= 0.0, f"score={sc2:.4f}")
    check("高情绪能过中期门槛", hippocampus_score(
        MemorySpine("x", [], [1.5,0,0,0]), 0.5) > THETA_MEDIUM,
        f"score={hippocampus_score(MemorySpine('x',[], [1.5,0,0,0]),0.5):.3f}")

    print("\n=== 6. 满池驱逐走海马流转（不再凭空消失） ===")
    _FAKE_NOW = T0
    m = LifeCardManager()
    for i in range(N_SHORT_MAX + 1):
        m.add_interaction(f"消息{i}", ["测试"], [1.2,0,0,0])  # 高情绪，够格进中期
    promoted = len(m.medium_pool)
    check("驱逐后有条目进中期", promoted >= 1, f"medium={promoted}, short={len(m.short_pool)}")
    check("短期池不超上限", len(m.short_pool) <= N_SHORT_MAX, f"short={len(m.short_pool)}")

    print("\n=== 7. 到期流转 + Token 截断 ===")
    _FAKE_NOW = T0
    m2 = LifeCardManager()
    m2.add_interaction("很久以前的事", ["旧事"], [1.2,0,0,0])
    _FAKE_NOW = T0 + 7*86400 + 10
    m2.tick()
    check("7天到期后离开短期池", len(m2.short_pool) == 0, f"short={len(m2.short_pool)} medium={len(m2.medium_pool)}")

    _FAKE_NOW = T0 + 100
    m3 = LifeCardManager()
    for i in range(6):
        m3.add_interaction("长" * 3000, ["话题"], [0.5,0,0,0])
    ctx = m3.assemble_context(["话题"], token_budget=8000)
    check("截断生效", len(ctx) <= 8000, f"len={len(ctx)}")

    print(f"\n结果：{ok} 通过 / {fail} 失败")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
