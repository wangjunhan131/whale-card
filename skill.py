# -*- coding: utf-8 -*-
"""程序性记忆（技能记忆）+ 改良 SM2  ——  V5.0 §7 / T3-1

## 为什么技能要和「事件」分开记

事件记忆风化的是**内容**：
  「10月24日晚上在学校门口吃烧烤，很开心，还拍了视频」
  → 「某天在某个地方吃了烧烤」
  → 「我记得好像有这么回事」
  → 没了

技能记忆风化的是**参数**，内容一个字都不能变：
  「怎么煮白米饭：米水比 1:1.2，泡 20 分钟，大火烧开转小火 12 分钟，关火焖 10 分钟」
  → 这条描述永远原样保留，会变的只有「我有多熟练」（L）和「多久复习一次」（I、EF）。

这是两套完全不同的机制，混在一张表里必然互相污染。
所以：`mem` 表管事件，`skill` 表管技能，两张表、两套公式、两个入口。

## 五个公式（对应 V5.0 §7）

1. 自然衰减      L(t+dt) = L(t) * exp(-LAM_EFF * dt)
                 LAM_EFF = LAM_B * (1 / max(I,1)) ** 0.3
                 ↑ 原文 bug 修在这里：原文 lambda 是常数，I 和 EF 都不进公式，
                   等于「间隔越大越记得牢」这句话根本没实现。
2. EF 更新       EF_new = clamp(EF + (0.1-(5-Q)*(0.08+(5-Q)*0.02)), 1.3, 2.5)
3. interval 递推  Q>=3 时：I==0 -> 1；I==1 -> 6；否则 I * EF_new
                 Q<3  时：I -> 1（答错了，重头来）
4. 熟练度加固    L_new = min(1, L + k_q*(Q/5)*(1-L)/(1+0.05*I))
5. 检索打分      Score = Sim * sqrt(L) * (1+0.15*(EF-1.3)) * (0.2 if L<0.05)

## Q 分从哪来（原文没定，这里定）

心智服务看不见对话结果，所以：
  - 默认：模型自评（调用时显式传 q）
  - 用户显式否定（「不对」「不是这样」「错了」）-> 强制 Q=1
  - 用户显式肯定（「对」「就是这样」「好厉害」）-> 强制 Q=5
  - 连续 3 次自评 5 分但用户完全没反馈 -> 第 3 次起降为 4（防止自我陶醉）
"""
import json
import math
import sqlite3
import time

DAY = 86400.0
HOUR = 3600.0

# ---- 参数（V5.0 §7）----
LAM_B = 0.0009          # 基础衰减率（每小时）
# 🔧 对文档的修正：原文 LAM_B = 0.0003，算下来半衰期 96 天 ——
#    意味着一条技能五年不用才淡到休眠线。那不是遗忘，那是硬盘。
#    真人三个月不练手就生了。0.0009 半衰期 32 天：
#      30 天不用 → 0.76 掉到 0.40（手生）
#      150 天不用 → 掉到 0.03（休眠，但条目还在、desc 一字未变）
#    而练熟的（I=30）衰减只有 0.36 倍，150 天还剩 0.24 —— 老手艺丢得慢。
L_DORMANT = 0.05        # 休眠线：低于这个值，检索时 ×0.2，但条目**不删**
K_Q = 0.55              # 熟练度加固系数
# 🔧 对文档公式的工程修正：分母里的 I 要封顶。
#    文档原文 `L + k_q*(Q/5)*(1-L)/(1+0.05*I)` 里 I 是指数增长的，
#    练到第 5 次 I 就到 37 天、第 6 次 93 天，分母越来越大，
#    结果是「练了二十年还是不熟」—— 这不是熟练度有天花板，这是学不会。
#    封顶 10 天后，分母最大 1.5，熟练度能真正收敛。
I_CAP_REINFORCE = 10.0
EF_MIN, EF_MAX = 1.3, 2.5
EF_INIT = 2.5
I_MAX_DAYS = 365.0      # interval 上限，别算出天文数字

# 自评虚高抑制：连续几次「自评满分但用户没反应」
SELF_PRAISE_LIMIT = 3

# 用户反馈识别（短、准，宁缺勿滥）
_RE_NEG = ("不对", "不是这样", "错了", "错了吧", "你说错", "搞错",
           "不是这个", "不行", "重来", "答错")
_RE_POS = ("对的", "就是这样", "说对了", "好厉害", "正是", "没错",
           "答对了", "就是这个", "对呀对呀")


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def lam_eff(interval_days):
    """有效衰减率 —— 原文致命 bug 的修正点。

    原文：lambda 是常数 0.0003，I 和 EF 一个都没进公式，
          于是「间隔越大衰减越弱」「EF 越高越不容易忘」全是空话。
    这里：间隔越长，衰减越慢（1/I^0.3）。
          I=1 天  -> 1.00x
          I=6 天  -> 0.58x
          I=30 天 -> 0.36x
          I=365 天-> 0.17x
    """
    i = max(float(interval_days or 1.0), 1.0)
    return LAM_B * (1.0 / i) ** 0.3


def decay_l(l, interval_days, dt_hours):
    """自然衰减：L(t+dt) = L(t) * exp(-LAM_EFF * dt)"""
    if l <= 0 or dt_hours <= 0:
        return l
    return l * math.exp(-lam_eff(interval_days) * dt_hours)


def update_ef(ef, q):
    """EF 更新：EF_new = clamp(EF + (0.1-(5-Q)*(0.08+(5-Q)*0.02)), 1.3, 2.5)"""
    ef = float(ef or EF_INIT)
    q = float(q)
    delta = 0.1 - (5.0 - q) * (0.08 + (5.0 - q) * 0.02)
    return _clamp(ef + delta, EF_MIN, EF_MAX)


def next_interval(i, ef, q):
    """interval 递推（原文缺的就是这一段）"""
    i = float(i or 0)
    if q < 3:
        return 1.0
    if i == 0:
        return 1.0
    if i == 1:
        return 6.0
    return min(i * float(ef or EF_INIT), I_MAX_DAYS)


def reinforce_l(l, q, interval_days, k_q=K_Q):
    """熟练度加固：L_new = min(1, L + k_q*(Q/5)*(1-L)/(1+0.05*I))

    🔧 I 封顶在 I_CAP_REINFORCE（20 天），理由见常量注释：
       不封顶的话分母随 I 无限增长，练多少次都收敛不了。
    """
    l = _clamp(float(l or 0), 0.0, 1.0)
    if q <= 0:
        return l
    i_eff = min(float(interval_days or 0), I_CAP_REINFORCE)
    gain = k_q * (float(q) / 5.0) * (1.0 - l) / (1.0 + 0.05 * i_eff)
    return _clamp(l + gain, 0.0, 1.0)


def score_skill(sim, l, ef):
    """检索打分：Score = Sim * sqrt(L) * (1+0.15*(EF-1.3)) * (0.2 if L<0.05)"""
    s = float(sim) * math.sqrt(max(0.0, float(l)))
    s *= (1.0 + 0.15 * (float(ef or EF_MIN) - EF_MIN))
    if l < L_DORMANT:
        s *= 0.2
    return s


def parse_feedback(user_text):
    """从用户这句话里读反馈：返回 +1 / -1 / 0"""
    t = user_text or ""
    if any(w in t for w in _RE_NEG):
        return -1
    if any(w in t for w in _RE_POS):
        return 1
    return 0


class SkillMemory(object):
    """技能记忆：内容不风化，只风化熟练度"""

    def __init__(self, db):
        self.db = db
        self._init_schema()

    def _init_schema(self):
        c = self.db.cursor()
        c.execute("""
        CREATE TABLE IF NOT EXISTS skill (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            desc          TEXT NOT NULL,       -- 🔴 原文永不风化，只读
            vec           TEXT NOT NULL,
            L             REAL NOT NULL DEFAULT 0.6,   -- 熟练度
            EF            REAL NOT NULL DEFAULT 2.5,   -- 难度因子
            I             REAL NOT NULL DEFAULT 0.0,   -- 间隔（天）
            last_call_ts  REAL NOT NULL,
            last_review_ts REAL NOT NULL,
            call_count    INTEGER NOT NULL DEFAULT 0,
            tag           TEXT NOT NULL DEFAULT '',
            create_ts     REAL NOT NULL,
            is_attach     INTEGER NOT NULL DEFAULT 0   -- 陪伴专属技能（§4.4(3)）
        )""")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_skill_name"
                  " ON skill(name)")
        # 🔴 2026-10-31 15:00 迁移：老库的 skill 表建于 is_attach 之前，
        #    CREATE TABLE IF NOT EXISTS 不会补列，直接 SELECT 会报
        #    "no such column: is_attach"。所以老库要单独 ALTER 一次。
        cols = [r[1] for r in c.execute("PRAGMA table_info(skill)")]
        if "is_attach" not in cols:
            c.execute("ALTER TABLE skill ADD COLUMN is_attach"
                      " INTEGER NOT NULL DEFAULT 0")
        c.execute("""
        CREATE TABLE IF NOT EXISTS skill_log (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            skill_id INTEGER NOT NULL,
            ts      REAL NOT NULL,
            q       REAL NOT NULL,
            l_before REAL NOT NULL,
            l_after  REAL NOT NULL,
            i_after  REAL NOT NULL,
            ef_after REAL NOT NULL,
            source  TEXT NOT NULL DEFAULT 'self'
        )""")
        self.db.commit()

    # ---------- 写 ----------
    def add(self, name, desc, vec=None, l=0.6, tag="", now=None,
            interval_days=0.0, ef=EF_INIT, is_attach=False):
        """建一条技能。desc 一旦写进去，就再也不改。

        is_attach=True 表示「陪伴专属技能」（V5.0 §4.4(3)）：
        检索时吃催产素加成，也就是她越依恋越顺手的那种事。
        """
        now = time.time() if now is None else float(now)
        if not name or not desc:
            raise ValueError("技能必须有名字和描述")
        vec = vec or {}
        self.db.execute(
            "INSERT OR IGNORE INTO skill(name,desc,vec,L,EF,I,last_call_ts,"
            "last_review_ts,call_count,tag,create_ts,is_attach)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (name, desc, json.dumps(vec, ensure_ascii=False),
             _clamp(l, 0.0, 1.0), _clamp(float(ef), EF_MIN, EF_MAX),
             max(0.0, float(interval_days)), now, now, 0, tag, now,
             1 if is_attach else 0))
        self.db.commit()
        row = self.db.execute("SELECT id FROM skill WHERE name=?",
                              (name,)).fetchone()
        return row[0] if row else None

    def get(self, name):
        return self.db.execute("SELECT * FROM skill WHERE name=?",
                               (name,)).fetchone()

    def all(self):
        return self.db.execute("SELECT * FROM skill ORDER BY L DESC").fetchall()

    def ensure_vecs(self, to_vec_fn):
        """给 vec 为空的技能补一条向量（name + desc 现算）。

        为什么需要（2026-11-09）：mind_server 的 /skill_add 只在**建技能那一刻**
        算 vec；早先经别的通道写进来的技能（真实卡里的 4、5 号）vec 是 {}。
        search() 里 `if not mv or not query_vec: continue` 会把它们直接跳过 ——
        学得再熟，也永远检索不到，更谈不上用。
        只补空行，已有向量一个字不动（那是当时建卡时的语义指纹）。
        返回补了几条。
        """
        n = 0
        for r in self.all():
            try:
                mv = json.loads(r["vec"] or "{}")
            except Exception:
                mv = {}
            if mv:
                continue
            v = to_vec_fn((r["name"] or "") + " " + (r["desc"] or ""))
            if not v:
                continue
            self.db.execute(
                "UPDATE skill SET vec=? WHERE id=?",
                (json.dumps(v, ensure_ascii=False), r["id"]))
            n += 1
        if n:
            self.db.commit()
        return n

    def tick_all(self, now=None):
        """按各自 interval 衰减所有技能的熟练度（内容不动）

        🔧 关键：结算完必须把 last_review_ts 推到现在，否则同一次衰减
        会被反复重算（每调一次就再衰减 30 天），等于衰减被重复叠加。
        """
        now = time.time() if now is None else float(now)
        c = self.db.cursor()
        rows = c.execute("SELECT * FROM skill").fetchall()
        changed = 0
        for r in rows:
            dt_h = (now - r["last_review_ts"]) / HOUR
            if dt_h <= 0:
                continue
            nl = decay_l(r["L"], r["I"], dt_h)
            c.execute("UPDATE skill SET L=?, last_review_ts=?"
                      " WHERE id=?", (nl, now, r["id"]))
            changed += 1
        self.db.commit()
        return changed

    # ---------- 用 ----------
    def use(self, name, q=None, user_text="", source="self", now=None,
            hormones=None):
        """用一次技能，然后按改良 SM2 结算。

        q 的优先级：用户显式反馈 > 传进来的自评 > 默认 4

        hormones 传进来（四元组 dict）才会吃 V5.0 §4.4 的两条内分泌联动：
          (2) Q 分上限压制 Q_max = 5*(1-0.60*max(0,h_cort-0.20)/0.80)
              压力高时天生发挥差 —— 熟练度再高也会生硬出错，
              所以是「这一次的 Q 拿不到满分」，不是「熟练度被扣」。
          (3) 陪伴专属技能加成 bonus = 1+0.25*(h_oxy-0.50)*2 ∈ [0.75,1.25]
              只作用于 is_attach=1 的技能：越亲近，这类事越顺手。
        不传 hormones 就完全退回老行为，老调用方零影响。
        """
        now = time.time() if now is None else float(now)
        row = self.get(name)
        if row is None:
            return None
        self.tick_all(now)

        # tick_all 会把行更新，重新读一次拿最新值
        row = self.get(name)
        l_before = float(row["L"])
        ef = float(row["EF"])
        i = float(row["I"])
        q_cap = 5.0
        bonus = 1.0
        if hormones:
            try:
                import hormone as _hormone
                q_cap = _hormone.q_max(hormones)
                if int(row["is_attach"] or 0):
                    bonus = _hormone.attach_bonus(hormones)
            except Exception:
                q_cap, bonus = 5.0, 1.0

        # 1) Q 分：用户反馈压过自评
        fb = parse_feedback(user_text)
        if fb < 0:
            q = 1.0
            source = "user_neg"
            self._praise_streak = 0
        elif fb > 0:
            q = 5.0
            source = "user_pos"
            self._praise_streak = 0
        else:
            if q is None:
                q = 4.0
            # 自评虚高抑制：连续 3 次自评满分但用户没反应 -> 第 4 次起降为 4
            if q >= 5.0:
                streak = getattr(self, "_praise_streak", 0) + 1
                self._praise_streak = streak
                if streak > SELF_PRAISE_LIMIT:
                    q = 4.0
            else:
                self._praise_streak = 0
        q = _clamp(q, 0.0, 5.0)

        # 1b) 内分泌：压力压 Q 上限（§4.4(2)）
        #     注意作用点 —— 压的是「这一次发挥」，不是熟练度本身。
        #     皮质醇 1.0 时 Q 最多 2.0，等于这一次练了也白练（还会掉 L）。
        if q_cap < 5.0 and q > q_cap:
            q = q_cap

        # 2) 五个公式依次跑
        ef_new = update_ef(ef, q)
        i_new = next_interval(i, ef_new, q)
        l_new = reinforce_l(l_before, q, i)
        if q < 3:
            # 答错了一次，熟练度也要真的掉一点（不是只把 interval 归 1）
            l_new = _clamp(l_new * 0.85, 0.0, 1.0)

        # 3) 陪伴专属技能加成（§4.4(3)）：加在熟练度上，封顶 1.0
        if bonus != 1.0:
            l_new = _clamp(l_new * bonus, 0.0, 1.0)

        c = self.db.cursor()
        c.execute("UPDATE skill SET L=?, EF=?, I=?, last_call_ts=?,"
                  " last_review_ts=?, call_count=call_count+1 WHERE id=?",
                  (l_new, ef_new, i_new, now, now, row["id"]))
        c.execute("INSERT INTO skill_log(skill_id,ts,q,l_before,l_after,"
                  "i_after,ef_after,source) VALUES(?,?,?,?,?,?,?,?)",
                  (row["id"], now, q, l_before, l_new, i_new, ef_new, source))
        self.db.commit()
        return {
            "name": name, "q": q, "L": round(l_new, 4),
            "EF": round(ef_new, 4), "I": round(i_new, 2),
            "source": source, "l_before": round(l_before, 4),
            "q_cap": round(q_cap, 3), "attach_bonus": round(bonus, 3),
            "is_attach": int(row["is_attach"] or 0),
        }

    # ---------- 找 ----------
    def search(self, query_vec, top_k=3, hormones=None):
        """按语义相似度 × 熟练度找技能。

        hormones 传进来时，陪伴专属技能吃催产素加成（§4.4(3)）。
        为什么要放在检索这一侧也算一遍：因为「顺手」有两个表现 ——
        一是用的时候做得更好（use 里），二是更容易被想起来（这里）。
        """
        bonus = 1.0
        if hormones:
            try:
                import hormone as _hormone
                bonus = _hormone.attach_bonus(hormones)
            except Exception:
                bonus = 1.0
        rows = self.all()
        out = []
        for r in rows:
            try:
                mv = json.loads(r["vec"])
            except Exception:
                mv = {}
            if not mv or not query_vec:
                continue
            # 简单余弦（和 card2 的 bigram 向量同构）
            num = sum(query_vec.get(k, 0.0) * mv.get(k, 0.0) for k in mv)
            na = math.sqrt(sum(v * v for v in query_vec.values()))
            nb = math.sqrt(sum(v * v for v in mv.values()))
            sim = num / (na * nb) if na and nb else 0.0
            if sim <= 0:
                continue
            s = score_skill(sim, r["L"], r["EF"])
            if bonus != 1.0 and int(r["is_attach"] or 0):
                s *= bonus
            out.append((s, sim, r))
        out.sort(key=lambda x: -x[0])
        return out[:top_k]

    def stats(self):
        rows = self.all()
        if not rows:
            return {"skills": 0, "avg_L": 0.0, "dormant": 0, "attach": 0}
        ls = [float(r["L"]) for r in rows]
        return {
            "skills": len(rows),
            "avg_L": round(sum(ls) / len(ls), 3),
            "dormant": sum(1 for x in ls if x < L_DORMANT),
            "attach": sum(1 for r in rows if int(r["is_attach"] or 0)),
        }
