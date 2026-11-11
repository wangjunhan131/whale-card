# -*- coding: utf-8 -*-
"""T4-4 突触联想 + 电势扩散

为什么要有这一层：
    recall() 解决的是「直接想起来」——问什么像什么就翻什么。
    但人的记忆不是这样工作的：说到「烧烤」会顺带想起「那天很开心」，
    说到「学校门口」会想起「同桌」。这是**联想**，不是检索。

    检索是「找」，联想是「顺着找」。前者靠相似度，后者靠连线。

做法（V5.0 §9.6）：
    1. 建突触：两条记忆之间连一条带类型、带权重的边
         co        共激活  1.00   同一次查询被一起想起来
         temporal  时序    1.20   同一段对话里挨着发生
         tag       同标签  1.15   同一分区 + 共享实体
         ground    同地点  1.25   同一个地方
         semantic  语义    1.45~1.80  库里 emb 的余弦最近邻（2026-11-09 加）
    2. 扩散：从直接命中的种子出发，电势沿突触往外传
         pot_next = pot_cur * LAYER_DECAY * synapse_weight
         LAYER_DECAY = 0.75   （V3.4 是 0.5，第三层直接死）
         POT_MIN_ACTIVATE = 0.05
         D_MAX = 3            （最多跳三跳）
    3. 被激活的邻居以「联想加成」的形式并入召回结果，
       而不是替换掉直接命中 —— 直接命中永远排前面。

注意：这一层是**加法**，不是替代。关掉它 recall 照样能用。
"""
import json
import math
import time

DAY = 86400.0

# 扩散参数（V5.0 §11 超参表）
LAYER_DECAY = 0.75
POT_MIN_ACTIVATE = 0.05
D_MAX = 3

# 语义边（2026-11-09）：
#   病根：temporal 边只是「时间上挨着的另一句话」，沿它扩散出来的「顺带
#   想起」几乎全是上下句，不是语义相关的事。这里直接用库里已有的 emb
#   连最近邻（不重新编码），让联想网长出真正的语义边。
#
#   阈值怎么定的 —— 先跑了诊断脚本，对真卡前 200 条记忆两两算余弦
#   （768 维，19900 对）：
#     两两分布：p50=.426 p75=.486 p90=.544 p95=.583 p99=.681 max=.996
#               >=.60 占 3.69%，>=.65 占 1.62%，>=.70 占 0.75%
#     每条 top1：p5=.585 p25=.647 p50=.722
#     top3 均值：p5=.564 p25=.621 p50=.681
#   取 0.62：高于两两 p95(.583)，只收相似度前约 3% 的边；又略高于 top1
#   的 p5(.585)，约九成记忆至少连得到一条，不会把网留空。
#   评测副本（512 维，96 条）复核：两两 p95=.607、>=.60 占 5.83%、
#   top1 p5=.599 —— 同一个阈值在两种模型上落在同一分位，不用按维度分叉。
SEMANTIC_MIN_SIM = 0.62      # 余弦低于它不连边
SEMANTIC_TOP_N = 3           # 一条最多连 3 个语义近邻，防稠密
SEMANTIC_DUP_SIM = 0.95      # 两个候选彼此 >=.95 算近重复，只留一个
SEMANTIC_WEIGHT_MIN = 1.45   # 严格高于 temporal(1.20)，扩散时不被挤掉
SEMANTIC_WEIGHT_MAX = 1.80   # 越像越粗，始终高于 temporal，扩散时优先

# 每节点边数封顶时，权重相同则按类型优先级留边（语义 > 地点 > 时序 ...）
KIND_PRIORITY = {"semantic": 5, "ground": 4, "temporal": 3, "tag": 2, "co": 1}

# 关系边类型与基础权重（V5.0 §9.6）
EDGE_TYPES = {
    "co": 1.00,        # 共激活
    "temporal": 1.20,  # 时序
    "tag": 1.15,       # 同标签
    "ground": 1.25,    # 同地点
    "semantic": SEMANTIC_WEIGHT_MIN,  # 语义近邻（实际权重按相似度在区间内浮动）
}

# 建边阈值
TEMPORAL_GAP = 600.0        # 10 分钟内算同一段对话
TEMPORAL_MAX = 6            # 一条最多连 6 个邻居，避免稠密
TAG_MIN_SHARED = 2          # 至少共享 2 个实体才算同标签
MAX_EDGES_PER_NODE = 24     # 每个节点最多 24 条边（防稠密图）
REBUILD_GROWTH = 0.05       # 节点数涨 5% 就重建

# 联想加成上限：再强的联想也不能盖过直接命中
ASSOC_BONUS_MAX = 0.55
# 联想结果的最低分数（相对种子最高分）
ASSOC_KEEP_RATIO = 0.25


def _norm_entities(raw):
    """mem.entities 存的是个数，不是集合。这里用 detail 里的实体做替代。"""
    try:
        n = int(raw or 0)
    except (TypeError, ValueError):
        n = 0
    return n


_PREPS = ("在", "去", "到", "从", "往", "于")


def _strip_prep(s):
    """去掉地点前的介词：「在学校门口」→「学校门口」"""
    t = (s or "").strip()
    while t and t[0] in _PREPS:
        t = t[1:]
    return t


def _common_prefix(a, b):
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


class SynapseNet(object):
    """突触网络：建边 + 电势扩散

    用一张独立的表存边，不污染 mem 表。
    """

    def __init__(self, db):
        self.db = db
        self._adj = None          # {mem_id: [(other_id, kind, weight)]}
        self._n_at_build = 0
        # 语义边用的向量缓存：dim -> [ids, 已 L2 归一化的矩阵]
        # 增量 remember 时只追加新行，避免每写一条就把整库 1300×768 读一遍。
        self._vec = {}
        self._vec_count = -1
        self._vec_max_id = 0
        self._sem_checked = False
        self._schema_ok = False

    # ---------- 建表 ----------
    def ensure_schema(self):
        c = self.db.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS synapse (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            a_id        INTEGER NOT NULL,
            b_id        INTEGER NOT NULL,
            kind        TEXT NOT NULL,
            weight      REAL NOT NULL DEFAULT 1.0,
            fire_count  INTEGER NOT NULL DEFAULT 0,
            last_fire_ts REAL NOT NULL DEFAULT 0,
            create_ts   REAL NOT NULL DEFAULT 0
        )""")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_syn_pair"
                  " ON synapse(a_id, b_id, kind)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_syn_a ON synapse(a_id)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_syn_b ON synapse(b_id)")
        self.db.commit()
        self._schema_ok = True

    # ---------- 惰性建边 ----------
    def _stale(self):
        """只有「记忆条数明显涨了」才算过期。

        🔧 注意：`_adj is None` 不等于过期 —— 那只是还没载入内存。
           早期版本把这两件事混在一起，导致每次新建 SynapseNet 都会
           全量重建，手工插进去的边被 DELETE 掉。载入 ≠ 重建。
        """
        if self._adj is None:
            return False
        n = self.db.execute("SELECT COUNT(*) FROM mem").fetchone()[0]
        return n >= self._n_at_build * (1.0 + REBUILD_GROWTH) + 5

    def ensure(self):
        if self._adj is None:
            self.ensure_schema()
            self._load_adj()
            # 库里一条边都没有、但记忆够多 → 说明还没建过，建一次
            if not self._adj:
                n = self.db.execute("SELECT COUNT(*) FROM mem").fetchone()[0]
                if n >= 2:
                    return self.rebuild()
            # 老库兼容：已有 temporal/tag，却几乎没有 semantic → 补一次。
            # 只在本进程第一次 ensure 时检查，别把 COUNT 挂到每次 recall 上。
            # 用覆盖率而不是「一条都没有」：万一新记忆先写了几条语义边，
            # 老记忆仍然是空的，不能因此跳过整库补建。
            if not self._sem_checked:
                self._sem_checked = True
                try:
                    mem_n = self.db.execute(
                        "SELECT COUNT(*) FROM mem WHERE COALESCE(dead,0)=0"
                    ).fetchone()[0]
                    if (not self._has_kind("semantic")
                            or self._semantic_covered() < 0.6 * mem_n):
                        self.backfill_semantic_edges()
                except Exception as e:
                    print("[synapse] 语义边批量补建失败: %s" % e)
            return self._adj
        if self._stale():
            return self.rebuild()
        return self._adj

    def _has_kind(self, kind):
        row = self.db.execute(
            "SELECT 1 FROM synapse WHERE kind=? LIMIT 1", (kind,)).fetchone()
        return row is not None

    def _semantic_covered(self):
        row = self.db.execute(
            "SELECT COUNT(*) FROM (SELECT a_id AS mid FROM synapse"
            " WHERE kind='semantic' UNION SELECT b_id FROM synapse"
            " WHERE kind='semantic')").fetchone()
        return row[0] or 0

    def _load_adj(self):
        """从 synapse 表读进内存，不重建"""
        adj = {}
        for s in self.db.execute("SELECT a_id,b_id,kind,weight FROM synapse"):
            a, b, k, w = s["a_id"], s["b_id"], s["kind"], s["weight"]
            adj.setdefault(a, []).append((b, k, w))
            adj.setdefault(b, []).append((a, k, w))
        for mid in list(adj):
            if len(adj[mid]) > MAX_EDGES_PER_NODE:
                # 权重相同按类型优先级留（语义边先留），别让时序边把
                # 语义边挤出 24 条的上限。
                adj[mid] = sorted(
                    adj[mid],
                    key=lambda x: (-x[2], -KIND_PRIORITY.get(x[1], 0)))[
                        :MAX_EDGES_PER_NODE]
        self._adj = adj
        self._n_at_build = self.db.execute(
            "SELECT COUNT(*) FROM mem").fetchone()[0]
        return adj

    # ---------- 语义边：直接用库里存的 emb，不重新编码 ----------
    @staticmethod
    def _numpy():
        try:
            import numpy as np
            return np
        except Exception:
            return None

    @staticmethod
    def _l2(np, mat):
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0
        return (mat / norms).astype(np.float32)

    def _load_all_vectors(self, np):
        """一次读全库 emb，按维度分组并 L2 归一化。

        老库可能混着 512 维和 768 维（换过模型），不同维度不能算余弦，
        所以按 dims 分组，各算各的。
        """
        rows = self.db.execute(
            "SELECT id, emb FROM mem WHERE emb IS NOT NULL"
            " AND COALESCE(dead,0)=0").fetchall()
        groups = {}
        for r in rows:
            blob = r["emb"]
            if not blob or len(blob) % 4:
                continue
            dim = len(blob) // 4
            v = np.frombuffer(blob, dtype=np.float32)
            if v.size != dim:
                continue
            ids, vs = groups.setdefault(dim, ([], []))
            ids.append(r["id"])
            vs.append(v)
        out = {}
        for dim, (ids, vs) in groups.items():
            out[dim] = [ids, self._l2(np, np.stack(vs))]
        self._vec = out
        cnt = self.db.execute(
            "SELECT COUNT(*) AS n, COALESCE(MAX(id),0) AS m FROM mem"
            " WHERE emb IS NOT NULL AND COALESCE(dead,0)=0").fetchone()
        self._vec_count = cnt["n"] or 0
        self._vec_max_id = cnt["m"] or 0
        return bool(out)

    def _refresh_vectors(self, np):
        """缓存有效就直接用；新写了记忆只把 id 更大的那几行追加进来。"""
        cnt = self.db.execute(
            "SELECT COUNT(*) AS n, COALESCE(MAX(id),0) AS m FROM mem"
            " WHERE emb IS NOT NULL AND COALESCE(dead,0)=0").fetchone()
        n, m = cnt["n"] or 0, cnt["m"] or 0
        if self._vec and n == self._vec_count and m == self._vec_max_id:
            return bool(self._vec)
        if self._vec and n > self._vec_count and m > self._vec_max_id:
            rows = self.db.execute(
                "SELECT id, emb FROM mem WHERE id>? AND emb IS NOT NULL"
                " AND COALESCE(dead,0)=0", (self._vec_max_id,)).fetchall()
            new = {}
            for r in rows:
                blob = r["emb"]
                if not blob or len(blob) % 4:
                    continue
                dim = len(blob) // 4
                v = np.frombuffer(blob, dtype=np.float32)
                if v.size != dim:
                    continue
                ids, vs = new.setdefault(dim, ([], []))
                ids.append(r["id"])
                vs.append(v)
            for dim, (ids, vs) in new.items():
                fresh = self._l2(np, np.stack(vs))
                if dim in self._vec:
                    old_ids, old_mat = self._vec[dim]
                    self._vec[dim] = [old_ids + ids,
                                      np.vstack([old_mat, fresh])]
                else:
                    self._vec[dim] = [ids, fresh]
            self._vec_count = n
            self._vec_max_id = m
            return bool(self._vec)
        return self._load_all_vectors(np)

    @staticmethod
    def _sem_weight(sim):
        span = max(1e-9, 1.0 - SEMANTIC_MIN_SIM)
        t = min(1.0, max(0.0, (sim - SEMANTIC_MIN_SIM) / span))
        return SEMANTIC_WEIGHT_MIN + (
            SEMANTIC_WEIGHT_MAX - SEMANTIC_WEIGHT_MIN) * t

    def _pick_for(self, np, mid, vec, group):
        """给一条记忆挑最多 3 个语义近邻 -> [(other_id, sim, weight)]"""
        ids, mat = group
        v = np.asarray(vec, dtype=np.float32).reshape(-1)
        if v.shape[0] != mat.shape[1]:
            return []
        norm = float(np.linalg.norm(v))
        if norm <= 0.0:
            return []
        sims = mat @ (v / norm)
        order = np.argsort(-sims)
        out, chosen = [], []
        for idx in order:
            if len(out) >= SEMANTIC_TOP_N:
                break
            sim = float(sims[idx])
            if sim < SEMANTIC_MIN_SIM:
                break
            oid = ids[idx]
            if oid == mid:
                continue
            # 两个候选彼此太像（同一句话的两个版本）→ 只留一条，
            # 否则「顺带想起」又变成翻出三条几乎一样的句子。
            if any(float(np.dot(mat[idx], mat[c])) >= SEMANTIC_DUP_SIM
                   for c in chosen):
                continue
            chosen.append(idx)
            out.append((oid, sim, self._sem_weight(sim)))
        return out

    def _all_semantic_edges(self, np):
        """全库语义边 -> {(lo, hi): weight}，分块算，避免 n² 内存爆掉。"""
        edges = {}
        for _dim, (ids, mat) in self._vec.items():
            n = len(ids)
            for start in range(0, n, 256):
                block = mat[start:start + 256]
                sims = block @ mat.T
                for r in range(sims.shape[0]):
                    i = start + r
                    row = sims[r]
                    order = np.argsort(-row)
                    picks, chosen = [], []
                    for idx in order:
                        if len(picks) >= SEMANTIC_TOP_N:
                            break
                        sim = float(row[idx])
                        if sim < SEMANTIC_MIN_SIM:
                            break
                        if idx == i:
                            continue
                        if any(float(np.dot(mat[idx], mat[c]))
                               >= SEMANTIC_DUP_SIM for c in chosen):
                            continue
                        chosen.append(idx)
                        picks.append((ids[idx], sim))
                    for oid, sim in picks:
                        a, b = (ids[i], oid) if ids[i] < oid else (oid, ids[i])
                        w = self._sem_weight(sim)
                        if w > edges.get((a, b), 0.0):
                            edges[(a, b)] = w
        return edges

    def backfill_semantic_edges(self):
        """老库兼容：一次算完全库近邻并写库（1300×768 约几十毫秒）。"""
        np = self._numpy()
        if np is None or not self._load_all_vectors(np):
            return 0
        edges = self._all_semantic_edges(np)
        if not edges:
            return 0
        now = time.time()
        self.db.cursor().executemany(
            "INSERT OR IGNORE INTO synapse(a_id,b_id,kind,weight,create_ts)"
            " VALUES(?,?,?,?,?)",
            [(a, b, "semantic", w, now) for (a, b), w in edges.items()])
        self.db.commit()
        self._load_adj()
        return len(edges)

    def add_semantic_edges(self, mem_ids, vecs=None):
        """新记忆落库后增量补语义边（复用传入向量，没有再查库）。

        vecs 与 mem_ids 对齐；元素可以是 ndarray / float32 bytes / None。
        返回真正插入的边数。numpy 不可用就静默跳过（功能降级，不崩）。
        """
        np = self._numpy()
        if np is None or not mem_ids:
            return 0
        if not self._schema_ok:
            self.ensure_schema()
        if not self._refresh_vectors(np):
            return 0
        now = time.time()
        c = self.db.cursor()
        inserted = 0
        for i, mid in enumerate(mem_ids):
            vec = vecs[i] if vecs and i < len(vecs) else None
            if vec is None:
                row = self.db.execute(
                    "SELECT emb FROM mem WHERE id=?", (mid,)).fetchone()
                vec = row["emb"] if row else None
            if vec is None:
                continue
            if isinstance(vec, (bytes, bytearray, memoryview)):
                vec = bytes(vec)
                if not vec or len(vec) % 4:
                    continue
                vec = np.frombuffer(vec, dtype=np.float32)
            vec = np.asarray(vec, dtype=np.float32).reshape(-1)
            group = self._vec.get(int(vec.shape[0]))
            if not group:
                continue
            for oid, _sim, w in self._pick_for(np, mid, vec, group):
                a, b = (mid, oid) if mid < oid else (oid, mid)
                cur = c.execute(
                    "INSERT OR IGNORE INTO synapse"
                    "(a_id,b_id,kind,weight,create_ts) VALUES(?,?,?,?,?)",
                    (a, b, "semantic", w, now))
                if cur.rowcount:
                    inserted += 1
                    self._adj_add(a, b, w)
        self.db.commit()
        return inserted

    def _adj_add(self, a, b, w):
        """内存邻接表跟着长一条；不重置 _n_at_build，原有重建节奏不变。"""
        if self._adj is None:
            return
        for x, y in ((a, b), (b, a)):
            lst = self._adj.setdefault(x, [])
            lst.append((y, "semantic", w))
            if len(lst) > MAX_EDGES_PER_NODE:
                self._adj[x] = sorted(
                    lst, key=lambda t: (-t[2], -KIND_PRIORITY.get(t[1], 0)))[
                        :MAX_EDGES_PER_NODE]

    def rebuild(self):
        """从 mem / detail / recall_event 重建整张突触网

        每次全量重建（几百到几千条，几十毫秒），比增量维护简单且不会错。
        """
        self.ensure_schema()
        rows = self.db.execute(
            "SELECT id, region, create_ts, entities, pool FROM mem"
        ).fetchall()
        n = len(rows)
        self._n_at_build = n
        if n < 2:
            self._adj = {}
            return self._adj

        # 1) temporal：时间上挨着的两条
        by_time = sorted(rows, key=lambda r: r["create_ts"] or 0)
        temporal_pairs = set()
        for i, r in enumerate(by_time):
            cnt = 0
            for j in range(i + 1, min(i + 1 + TEMPORAL_MAX, n)):
                q = by_time[j]
                gap = (q["create_ts"] or 0) - (r["create_ts"] or 0)
                if gap > TEMPORAL_GAP:
                    break
                if gap >= 0:
                    temporal_pairs.add((r["id"], q["id"]))
                    cnt += 1
                if cnt >= TEMPORAL_MAX:
                    break

        # 2) tag：同分区 + 共享实体（用 detail 的 PLACE/NUMBER 词做代理）
        place_of = {}
        for d in self.db.execute(
                "SELECT parent_id, text FROM detail WHERE kind='PLACE'"):
            place_of.setdefault(d["parent_id"], set()).add(
                (d["text"] or "").strip())
        by_region = {}
        for r in rows:
            by_region.setdefault(r["region"] or "episode", []).append(r)
        tag_pairs = set()
        ground_pairs = set()
        for _reg, lst in by_region.items():
            # 同分区里按实体数分组：实体多的和实体多的连（有内容才值得连）
            rich = [r for r in lst if _norm_entities(r["entities"]) >= 2]
            for i in range(len(rich)):
                a = rich[i]
                for j in range(i + 1, min(i + 1 + 4, len(rich))):
                    b = rich[j]
                    if abs((a["create_ts"] or 0) - (b["create_ts"] or 0)) < 3 * DAY:
                        tag_pairs.add((a["id"], b["id"]))
        # ground：同一个地方
        # 🔧 不能要求地点字符串完全相等 —— 「在学校门口的路边摊」和
        #    「在学校门口」是同一个地方，只是说法长短不同。
        #    改成：一个地点是另一个的子串，或者两者去掉介词后共享 ≥3 字前缀。
        place_keys = []
        for mid, ps in place_of.items():
            for p in ps:
                q = _strip_prep(p)
                if len(q) >= 2:
                    place_keys.append((q, mid))
        by_place = {}
        for q, mid in place_keys:
            by_place.setdefault(q, []).append(mid)
        ground_pairs = set()
        keys = list(by_place)
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                a, b = keys[i], keys[j]
                short, long_ = (a, b) if len(a) <= len(b) else (b, a)
                if short in long_ or _common_prefix(a, b) >= 3:
                    for ma in by_place[a]:
                        for mb in by_place[b]:
                            if ma != mb:
                                ground_pairs.add((ma, mb))

        # 3) co：同一次查询被一起想起来的（recall_event 同 query_hash）
        co_pairs = set()
        groups = self.db.execute(
            "SELECT query_hash, GROUP_CONCAT(mem_id) AS ids FROM recall_event"
            " WHERE query_hash IS NOT NULL GROUP BY query_hash"
            " HAVING COUNT(*) >= 2 LIMIT 500").fetchall()
        for g in groups:
            ids = [int(x) for x in (g["ids"] or "").split(",") if x]
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    co_pairs.add((ids[i], ids[j]))

        # 写库
        now = time.time()
        c = self.db.cursor()
        c.execute("DELETE FROM synapse")
        add = []
        for kind, pairs in (("temporal", temporal_pairs),
                            ("tag", tag_pairs),
                            ("ground", ground_pairs),
                            ("co", co_pairs)):
            w = EDGE_TYPES[kind]
            for a, b in pairs:
                if a == b:
                    continue
                lo, hi = (a, b) if a < b else (b, a)
                add.append((lo, hi, kind, w, now))

        # 5) semantic：emb 余弦最近邻。重建必须一起重建 —— 上面那条
        #    DELETE FROM synapse 会把老库补好的语义边一起冲掉。
        np = self._numpy()
        if np is not None and self._load_all_vectors(np):
            for (a, b), w in self._all_semantic_edges(np).items():
                add.append((a, b, "semantic", w, now))
        # 每节点边数封顶（按 id 顺序截，简单可复现）
        if add:
            c.executemany(
                "INSERT OR IGNORE INTO synapse(a_id,b_id,kind,weight,create_ts)"
                " VALUES(?,?,?,?,?)", add)
        self.db.commit()

        # 建邻接表（直接复用 _load_adj，逻辑只写一处）
        self._adj = None
        self._load_adj()
        return self._adj

    # ---------- 电势扩散 ----------
    def spread(self, seeds, top_k=5):
        """从种子出发沿突触扩散电势

        seeds: [(mem_id, pot), ...]  pot 已归一化到 0~1
        返回：[(mem_id, pot, path_kind), ...] —— 不含种子本身
        """
        adj = self.ensure()
        if not adj or not seeds:
            return []
        pot = {}
        for mid, p in seeds:
            if p > pot.get(mid, 0.0):
                pot[mid] = p
        seed_ids = set(pot)
        frontier = dict(pot)
        acc = {}
        for _layer in range(D_MAX):
            nxt = {}
            for mid, p in frontier.items():
                for other, kind, w in adj.get(mid, ()):
                    if other in seed_ids:
                        continue          # 不回灌种子，避免自我加强
                    np_ = p * LAYER_DECAY * w
                    if np_ < POT_MIN_ACTIVATE:
                        continue
                    pri = KIND_PRIORITY.get(kind, 0)
                    cur = nxt.get(other)
                    # 电势相同时优先语义边：不能让先遍历到的时序边
                    # 仅靠插入顺序把语义路径顶掉。
                    if cur is None or (np_, pri) > (cur[0], cur[1]):
                        nxt[other] = (np_, kind)
            if not nxt:
                break
            # 同一层内合并（取最大），不累加 —— 累加会让稠密节点爆炸
            for mid, (p, kind) in nxt.items():
                cur = pot.get(mid, 0.0)
                if p > cur or (p == cur and
                               KIND_PRIORITY.get(kind, 0) >
                               KIND_PRIORITY.get(acc.get(mid), 0)):
                    pot[mid] = p
                    acc[mid] = kind
            frontier = {m: p for m, (p, _k) in nxt.items()}
        out = [(mid, p, acc.get(mid, "co")) for mid, p in pot.items()
               if mid not in seed_ids]
        out.sort(key=lambda x: -x[1])
        return out[:top_k]

    # ---------- 加固突触（被一起想起的边变粗） ----------
    def reinforce(self, mem_ids, now=None):
        """一次召回里同时出现的记忆，它们之间的边 +1"""
        if len(mem_ids) < 2:
            return 0
        now = now or time.time()
        c = self.db.cursor()
        n = 0
        for i in range(len(mem_ids)):
            for j in range(i + 1, len(mem_ids)):
                a, b = mem_ids[i], mem_ids[j]
                lo, hi = (a, b) if a < b else (b, a)
                c.execute(
                    "UPDATE synapse SET fire_count=fire_count+1, last_fire_ts=?"
                    " WHERE a_id=? AND b_id=?", (now, lo, hi))
                n += c.rowcount
        self.db.commit()
        return n

    def stats(self):
        self.ensure_schema()
        total = self.db.execute("SELECT COUNT(*) FROM synapse").fetchone()[0]
        kinds = {}
        for r in self.db.execute(
                "SELECT kind, COUNT(*) AS n FROM synapse GROUP BY kind"):
            kinds[r["kind"]] = r["n"]
        nodes = self.db.execute(
            "SELECT COUNT(DISTINCT a_id) FROM synapse").fetchone()[0]
        covered = self.db.execute(
            "SELECT COUNT(*) FROM (SELECT a_id AS mid FROM synapse"
            " UNION SELECT b_id FROM synapse)").fetchone()[0]
        memories = self.db.execute(
            "SELECT COUNT(*) FROM mem WHERE COALESCE(dead,0)=0").fetchone()[0]
        return {"edges": total, "kinds": kinds, "nodes": nodes,
                "covered": covered, "memories": memories,
                "coverage": round(covered * 1.0 / memories, 4)
                if memories else 0.0,
                "avg_degree": round(total * 2.0 / nodes, 2) if nodes else 0.0}
