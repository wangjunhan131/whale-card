# -*- coding: utf-8 -*-
"""T6-2 可视化星云图

为什么要这个 —— 现在整套系统是黑盒：哪条记忆被翻出来、为什么、权重多少，
全都看不见。星云图是这套系统唯一的调试界面，也是唯一能「看见自己脑子里
长什么样」的东西。

产出：一个**自包含**的 HTML（不联网、不引 CDN、双击就能开），里面：
  · 左：星云图 —— 每条记忆一个点，突触一条线
  · 右：状态面板 —— 池分布 / 激素 / 心境 / 分区 / 突触 / trace
  · 底：本轮召回高亮 —— 传一个查询，命中的点亮起来，并列出分数拆解

用法：
    python export_viz.py                          # 生成 viz/nebula.html
    python export_viz.py --query "你还记得我QQ号吗"   # 顺便标出这次召回
    python export_viz.py --open                   # 生成完直接打开
"""
import argparse
import json
import math
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DEFAULT_DB = os.path.join(HERE, "card.db")
OUT_DIR = os.path.join(HERE, "viz")

REGION_CN = {
    "episode": "情景", "fact": "事实", "self": "自我",
    "habit": "习惯", "preference": "偏好", "flash": "闪光",
}
REGION_COLOR = {
    "episode": "#5b7fa6", "fact": "#4f9d69", "self": "#c9a227",
    "habit": "#3fa8a0", "preference": "#c47ba0", "flash": "#c0504d",
}
POOL_CN = {
    "short": "短期", "medium": "中期", "long": "长期",
    "archive": "归档", "deep": "深海",
}
EDGE_COLOR = {
    "temporal": "#4a5568", "tag": "#3d6b8f",
    "ground": "#4a7c59", "co": "#8a6b3f",
}


# ══════════════════════════════════════════════════════
# 布局：弹簧 + 网格斥力（纯 Python，300 点以内够快）
# ══════════════════════════════════════════════════════
def layout(nodes, edges, iters=180, width=1200.0, height=900.0, seed=20261030):
    """给每个节点算一个坐标

    没用 Fruchterman-Reingold 的全局斥力 —— 300 点会变成 O(n²) 的十几亿次运算。
    改成：边当弹簧互相拉，点之间用网格分桶算近邻斥力，再加一个朝中心的弱引力
    防止整体散开。视觉上够用，速度快两个数量级。
    """
    rnd = random.Random(seed)
    n = len(nodes)
    if n == 0:
        return {}
    idx = {nd["id"]: i for i, nd in enumerate(nodes)}
    # 初始：按分区散在圆上，同区聚在一起（一眼能看出分区）
    by_region = {}
    for i, nd in enumerate(nodes):
        by_region.setdefault(nd["region"], []).append(i)
    regions = sorted(by_region)
    pos = [(0.0, 0.0)] * n
    for ri, rg in enumerate(regions):
        ang = 2 * math.pi * ri / max(1, len(regions))
        cx = width / 2 + math.cos(ang) * width * 0.26
        cy = height / 2 + math.sin(ang) * height * 0.26
        for i in by_region[rg]:
            pos[i] = (cx + rnd.uniform(-90, 90), cy + rnd.uniform(-90, 90))

    # 边表（只保留两端都存在的）
    links = []
    for e in edges:
        a, b = idx.get(e["a"]), idx.get(e["b"])
        if a is None or b is None or a == b:
            continue
        links.append((a, b, float(e.get("weight") or 1.0)))

    CELL = 110.0
    for it in range(iters):
        t = 1.0 - it / float(iters)          # 温度递减
        disp = [[0.0, 0.0] for _ in range(n)]

        # ① 弹簧（边）
        for a, b, w in links:
            ax, ay = pos[a]
            bx, by = pos[b]
            dx, dy = bx - ax, by - ay
            d = math.hypot(dx, dy) or 0.01
            target = 70.0 / (0.6 + 0.4 * w)   # 边越粗越近
            f = (d - target) * 0.06 * t
            ux, uy = dx / d, dy / d
            disp[a][0] += ux * f
            disp[a][1] += uy * f
            disp[b][0] -= ux * f
            disp[b][1] -= uy * f

        # ② 网格斥力（只看同格 + 邻格）
        grid = {}
        for i, (x, y) in enumerate(pos):
            grid.setdefault((int(x // CELL), int(y // CELL)), []).append(i)
        for (gx, gy), bucket in grid.items():
            near = []
            for ox in (-1, 0, 1):
                for oy in (-1, 0, 1):
                    near.extend(grid.get((gx + ox, gy + oy), ()))
            for i in bucket:
                xi, yi = pos[i]
                for j in near:
                    if j <= i:
                        continue
                    xj, yj = pos[j]
                    dx, dy = xi - xj, yi - yj
                    d2 = dx * dx + dy * dy
                    if d2 < 1e-6:
                        dx, dy = rnd.uniform(-1, 1), rnd.uniform(-1, 1)
                        d2 = dx * dx + dy * dy or 1.0
                    if d2 > (CELL * 2.2) ** 2:
                        continue
                    d = math.sqrt(d2)
                    f = (55.0 / d) * 0.05 * t
                    ux, uy = dx / d, dy / d
                    disp[i][0] += ux * f
                    disp[i][1] += uy * f
                    disp[j][0] -= ux * f
                    disp[j][1] -= uy * f

        # ③ 朝中心弱引力 + 边界夹紧
        cx0, cy0 = width / 2, height / 2
        for i, (x, y) in enumerate(pos):
            disp[i][0] += (cx0 - x) * 0.0012 * t
            disp[i][1] += (cy0 - y) * 0.0012 * t
            nx = x + max(-30.0, min(30.0, disp[i][0]))
            ny = y + max(-30.0, min(30.0, disp[i][1]))
            pos[i] = (max(40.0, min(width - 40.0, nx)),
                      max(40.0, min(height - 40.0, ny)))

    return {nodes[i]["id"]: (round(pos[i][0], 1), round(pos[i][1], 1))
            for i in range(n)}


# ══════════════════════════════════════════════════════
# 数据收集
# ══════════════════════════════════════════════════════
def collect(db_path, query=None):
    from card2 import MemoryCard, weight_now
    import hormone as hormone_mod

    c = MemoryCard(db_path)
    now = c.clock.now()
    rows = c.db.execute("SELECT * FROM mem").fetchall()

    nodes = []
    for r in rows:
        w = weight_now(r["w"], r["pool"], r["t_anchor"], now, r["region"])
        nodes.append({
            "id": r["id"],
            "core": (r["core"] or r["raw"] or "")[:60],
            "raw": (r["raw"] or "")[:200],
            "region": r["region"] or "episode",
            "pool": r["pool"],
            "w": round(float(w), 4),
            "salience": round(float(r["salience"] or 0), 3),
            "access": int(r["access_count"] or 0),
            "ts": float(r["create_ts"] or 0),
            "subj": r["subj"] or "", "verb": r["verb"] or "", "obj": r["obj"] or "",
            "erase_stage": int(r["erase_stage"] or 0),
        })

    edges = []
    try:
        # 星云图要的是「现在这张网长什么样」，所以先确保突触建过 ——
        # 不建的话新库第一次打开会是一张只有点、没有线的图。
        c.synapse.ensure()
        for e in c.db.execute(
                "SELECT a_id,b_id,kind,weight FROM synapse"):
            edges.append({"a": e["a_id"], "b": e["b_id"],
                          "kind": e["kind"], "weight": float(e["weight"] or 1.0)})
    except Exception:
        pass

    # 本轮召回
    hit_ids, hit_detail = [], []
    if query:
        hits = c.recall(query, top_k=8, access=False)
        assoc = getattr(c, "_last_assoc_ids", set())
        for s, w, r in hits:
            hit_ids.append(r["id"])
            hit_detail.append({
                "id": r["id"],
                "score": round(float(s), 4),
                "w_now": round(float(w), 4),
                "assoc": r["id"] in assoc,
                "text": c.render(r, w),
            })

    st = c.stats()
    mood = c.mood_label()
    h = hormone_mod.describe(c.h)
    try:
        syn = c.synapse.stats()
    except Exception:
        syn = {}
    sections = None
    ctx_text = ""
    if query:
        ctx = c.assemble_context(query)
        sections = ctx.get("sections")
        ctx_text = ctx.get("text") or ""
    c.close()

    return {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "db": os.path.abspath(db_path),
        "nodes": nodes,
        "edges": edges,
        "stats": st,
        "mood": mood,
        "hormones": h,
        "synapse": syn,
        "query": query or "",
        "hits": hit_detail,
        "hit_ids": hit_ids,
        "sections": sections,
        "ctx_text": ctx_text,
    }


# ══════════════════════════════════════════════════════
# HTML
# ══════════════════════════════════════════════════════
HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>小鲸鱼 · 记忆星云</title>
<style>
  :root{
    --bg:#0b0f16; --panel:#131a24; --line:#1f2b3a;
    --fg:#c8d4e0; --dim:#6b7c8f; --accent:#7fb3d5;
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;background:var(--bg);color:var(--fg);
    font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
    font-size:13px;overflow:hidden}
  #app{display:flex;height:100vh}
  #left{flex:1;position:relative;min-width:0}
  #cv{display:block;width:100%;height:100%;cursor:grab}
  #cv.drag{cursor:grabbing}
  #panel{width:330px;border-left:1px solid var(--line);background:var(--panel);
    overflow-y:auto;padding:14px 16px 40px}
  h1{font-size:15px;margin:0 0 4px;font-weight:600;letter-spacing:.5px}
  .sub{color:var(--dim);font-size:11px;margin-bottom:14px;line-height:1.6}
  .sec{margin:16px 0 8px;font-size:11px;letter-spacing:1.5px;color:var(--accent);
    text-transform:uppercase;border-bottom:1px solid var(--line);padding-bottom:5px}
  .row{display:flex;justify-content:space-between;padding:3px 0;font-size:12px}
  .row .k{color:var(--dim)}
  .row .v{font-variant-numeric:tabular-nums}
  .bar{height:5px;background:#1b2634;border-radius:3px;overflow:hidden;margin-top:3px}
  .bar>i{display:block;height:100%;border-radius:3px}
  .legend{display:flex;flex-wrap:wrap;gap:8px 12px;margin-top:6px}
  .legend span{display:flex;align-items:center;gap:5px;font-size:11px;color:var(--dim)}
  .dot{width:8px;height:8px;border-radius:50%;display:inline-block}
  #hits{margin-top:6px}
  .hit{padding:7px 9px;border-left:2px solid var(--line);margin-bottom:6px;
    background:#0f1620;border-radius:0 4px 4px 0;font-size:11.5px;line-height:1.55}
  .hit .sc{color:var(--accent);font-variant-numeric:tabular-nums}
  .hit .tag{font-size:10px;padding:1px 5px;border-radius:8px;background:#24344a;
    color:#9fb8cc;margin-left:5px}
  .hit .tag.assoc{background:#3a2b40;color:#c9a0d0}
  #tip{position:absolute;pointer-events:none;background:#0e1520ee;
    border:1px solid var(--line);border-radius:6px;padding:8px 10px;
    font-size:11.5px;line-height:1.6;max-width:340px;display:none;z-index:9;
    box-shadow:0 6px 24px #0009}
  #tip b{color:#fff;font-weight:600}
  #tip .m{color:var(--dim);font-size:10.5px}
  #hud{position:absolute;left:14px;top:12px;font-size:11px;color:var(--dim);
    line-height:1.7;pointer-events:none}
  #hud b{color:var(--fg);font-weight:600}
  #ctxbox{margin-top:6px;background:#0f1620;border:1px solid var(--line);
    border-radius:5px;padding:9px;font-size:11px;line-height:1.7;
    white-space:pre-wrap;max-height:220px;overflow-y:auto;color:#9fb0c2}
  .empty{color:var(--dim);font-size:11px;padding:6px 0}
</style>
</head>
<body>
<div id="app">
  <div id="left">
    <canvas id="cv"></canvas>
    <div id="hud"></div>
    <div id="tip"></div>
  </div>
  <div id="panel"></div>
</div>
<script>
const DATA = __DATA__;

const cv = document.getElementById('cv');
const ctx2d = cv.getContext('2d');
const panel = document.getElementById('panel');
const tip = document.getElementById('tip');
const hud = document.getElementById('hud');

const RC = __RC__;
const PC = __PC__;
const EC = __EC__;

const hits = new Set(DATA.hit_ids || []);
const hitMap = {};
(DATA.hits || []).forEach(h => hitMap[h.id] = h);

let view = {x:0, y:0, k:1};
let dragging = false, dragStart = null, moved = false;
let hoverId = null;
let highlight = new Set();

function fit() {
  const r = cv.parentElement.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  cv.width = Math.floor(r.width * dpr);
  cv.height = Math.floor(r.height * dpr);
  cv.style.width = r.width + 'px';
  cv.style.height = r.height + 'px';
  ctx2d.setTransform(dpr, 0, 0, dpr, 0, 0);
  draw();
}

function nodeR(n) {
  return 2.2 + Math.max(0.15, n.w) * 9 + Math.min(3, n.access * 0.12);
}

function draw() {
  const r = cv.parentElement.getBoundingClientRect();
  const W = r.width, H = r.height;
  ctx2d.clearRect(0, 0, W, H);

  ctx2d.save();
  ctx2d.translate(view.x, view.y);
  ctx2d.scale(view.k, view.k);

  // 边
  const nodeById = {};
  DATA.nodes.forEach(n => nodeById[n.id] = n);
  DATA.edges.forEach(e => {
    const a = nodeById[e.a], b = nodeById[e.b];
    if (!a || !b) return;
    const hot = (hoverId && (e.a === hoverId || e.b === hoverId)) ||
                (highlight.has(e.a) && highlight.has(e.b));
    ctx2d.strokeStyle = EC[e.kind] || '#334';
    ctx2d.globalAlpha = hot ? 0.85 : 0.14;
    ctx2d.lineWidth = hot ? Math.min(2.2, 0.5 + e.weight) : 0.55;
    ctx2d.beginPath();
    ctx2d.moveTo(a.x, a.y);
    ctx2d.lineTo(b.x, b.y);
    ctx2d.stroke();
  });
  ctx2d.globalAlpha = 1;

  // 点
  DATA.nodes.forEach(n => {
    const rad = nodeR(n);
    const isHit = hits.has(n.id);
    const isHover = n.id === hoverId;
    const dimmed = highlight.size > 0 && !highlight.has(n.id);

    if (isHit) {  // 召回命中：外圈光晕
      ctx2d.beginPath();
      ctx2d.arc(n.x, n.y, rad + 7, 0, 7);
      ctx2d.fillStyle = 'rgba(127,179,213,0.13)';
      ctx2d.fill();
      ctx2d.beginPath();
      ctx2d.arc(n.x, n.y, rad + 3.5, 0, 7);
      ctx2d.strokeStyle = '#7fb3d5';
      ctx2d.lineWidth = 1.2;
      ctx2d.stroke();
    }
    ctx2d.beginPath();
    ctx2d.arc(n.x, n.y, rad, 0, 7);
    ctx2d.fillStyle = RC[n.region] || '#556';
    ctx2d.globalAlpha = dimmed ? 0.18 : (0.30 + Math.min(0.70, n.w));
    ctx2d.fill();
    ctx2d.globalAlpha = 1;
    if (isHover) {
      ctx2d.strokeStyle = '#fff';
      ctx2d.lineWidth = 1.6;
      ctx2d.stroke();
    }
  });

  // 标签（只给命中的和悬停的，否则太乱）
  ctx2d.font = '11px -apple-system,"Microsoft YaHei",sans-serif';
  DATA.nodes.forEach(n => {
    if (!hits.has(n.id) && n.id !== hoverId) return;
    const label = n.core.length > 18 ? n.core.slice(0, 18) + '…' : n.core;
    const w = ctx2d.measureText(label).width;
    const x = n.x + nodeR(n) + 5, y = n.y + 4;
    ctx2d.fillStyle = '#0b0f16cc';
    ctx2d.fillRect(x - 3, y - 12, w + 6, 16);
    ctx2d.fillStyle = '#dce8f2';
    ctx2d.fillText(label, x, y);
  });

  ctx2d.restore();

  // HUD
  const shown = DATA.nodes.filter(n => n.w > 0.02).length;
  hud.innerHTML = '<b>' + DATA.nodes.length + '</b> 条记忆 · <b>' +
    DATA.edges.length + '</b> 条突触 · 可见 <b>' + shown + '</b> 条<br>' +
    '滚轮缩放 · 拖动平移 · 悬停看详情';
}

// ---- 坐标变换 ----
function toWorld(mx, my) {
  return {x: (mx - view.x) / view.k, y: (my - view.y) / view.k};
}
function hitTest(mx, my) {
  const p = toWorld(mx, my);
  let best = null, bestD = 1e9;
  DATA.nodes.forEach(n => {
    const d = Math.hypot(n.x - p.x, n.y - p.y);
    const rad = nodeR(n) + 4;
    if (d < rad && d < bestD) { bestD = d; best = n; }
  });
  return best;
}

// ---- 交互 ----
cv.addEventListener('mousedown', e => {
  dragging = true; moved = false;
  dragStart = {x: e.offsetX - view.x, y: e.offsetY - view.y};
  cv.classList.add('drag');
});
cv.addEventListener('mousemove', e => {
  if (dragging) {
    view.x = e.offsetX - dragStart.x;
    view.y = e.offsetY - dragStart.y;
    moved = true;
    draw();
    return;
  }
  const n = hitTest(e.offsetX, e.offsetY);
  if ((n && n.id) !== hoverId) {
    hoverId = n ? n.id : null;
    draw();
  }
  if (n) {
    const h = hitMap[n.id];
    tip.style.display = 'block';
    tip.style.left = Math.min(e.offsetX + 14, cv.clientWidth - 350) + 'px';
    tip.style.top = (e.offsetY + 14) + 'px';
    tip.innerHTML = '<b>' + esc(n.core) + '</b>' +
      '<div class="m">' + (RC[n.region] ? '' : '') +
      (REGION_CN[n.region] || n.region) + ' · ' + (POOL_CN[n.pool] || n.pool) +
      ' · 权重 ' + n.w.toFixed(3) + ' · 想过 ' + n.access + ' 次' +
      (n.erase_stage ? ' · 侵蚀第' + n.erase_stage + '级' : '') + '</div>' +
      (h ? '<div style="margin-top:4px;color:#7fb3d5">本轮命中 score=' +
           h.score.toFixed(3) + (h.assoc ? '（联想）' : '') + '</div>' : '') +
      (n.raw && n.raw !== n.core ?
        '<div class="m" style="margin-top:4px">原文：' + esc(n.raw) + '</div>' : '');
  } else {
    tip.style.display = 'none';
  }
});
window.addEventListener('mouseup', () => {
  dragging = false;
  cv.classList.remove('drag');
});
cv.addEventListener('mouseleave', () => {
  tip.style.display = 'none';
  hoverId = null;
  draw();
});
cv.addEventListener('wheel', e => {
  e.preventDefault();
  const mx = e.offsetX, my = e.offsetY;
  const p = toWorld(mx, my);
  const k2 = Math.max(0.25, Math.min(4, view.k * (e.deltaY < 0 ? 1.12 : 0.89)));
  view.k = k2;
  view.x = mx - p.x * k2;
  view.y = my - p.y * k2;
  draw();
}, {passive: false});

// 点击：高亮这个点的邻居
cv.addEventListener('click', e => {
  if (moved) return;
  const n = hitTest(e.offsetX, e.offsetY);
  if (!n) { highlight = new Set(); draw(); return; }
  const s = new Set([n.id]);
  DATA.edges.forEach(ed => {
    if (ed.a === n.id) s.add(ed.b);
    if (ed.b === n.id) s.add(ed.a);
  });
  highlight = s;
  draw();
});

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

const REGION_CN = __RCN__;
const POOL_CN = __PCN__;

// ---- 面板 ----
function pct(x) { return (x * 100).toFixed(0) + '%'; }

function bar(v, color) {
  return '<div class="bar"><i style="width:' + Math.max(0, Math.min(100, v * 100)) +
    '%;background:' + color + '"></i></div>';
}

function renderPanel() {
  let h = '';
  h += '<h1>记忆星云</h1>';
  h += '<div class="sub">生成于 ' + esc(DATA.generated) + '<br>' +
       esc(DATA.db) + '</div>';

  // 概览
  const st = DATA.stats || {};
  h += '<div class="sec">概览</div>';
  h += row('记忆条数', st.total);
  h += row('细节条数', st.details);
  h += row('心情', DATA.mood);
  h += row('突触', (DATA.synapse || {}).edges || 0);
  h += row('节点', (DATA.synapse || {}).nodes || 0);
  h += row('平均度', ((DATA.synapse || {}).avg_degree || 0).toFixed(2));

  // 池分布
  h += '<div class="sec">记忆池</div>';
  const pools = (st.pools || {});
  const maxN = Math.max(1, ...Object.values(pools).map(p => p.n));
  Object.keys(pools).forEach(k => {
    const p = pools[k];
    h += '<div class="row"><span class="k">' + (POOL_CN[k] || k) +
         '</span><span class="v">' + p.n + ' · ' + (p.avg_w || 0).toFixed(2) + '</span></div>';
    h += bar(p.n / maxN, '#4a6b8a');
  });

  // 分区
  h += '<div class="sec">分区</div>';
  const byRegion = {};
  DATA.nodes.forEach(n => byRegion[n.region] = (byRegion[n.region] || 0) + 1);
  Object.keys(byRegion).sort((a, b) => byRegion[b] - byRegion[a]).forEach(k => {
    h += '<div class="row"><span class="k"><span class="dot" style="background:' +
         (RC[k] || '#556') + '"></span> ' + (REGION_CN[k] || k) +
         '</span><span class="v">' + byRegion[k] + '</span></div>';
  });

  // 激素
  h += '<div class="sec">内分泌</div>';
  const hh = DATA.hormones || {};
  Object.keys(hh).forEach(k => {
    const v = hh[k];
    h += '<div class="row"><span class="k">' + k + '</span><span class="v">' +
         v.toFixed(3) + '</span></div>';
    h += bar(v, v > 0.7 ? '#c0504d' : (v < 0.3 ? '#3fa8a0' : '#5b7fa6'));
  });

  // 本轮召回
  if (DATA.query) {
    h += '<div class="sec">本轮召回</div>';
    h += '<div class="sub" style="margin:0 0 8px">问：' + esc(DATA.query) + '</div>';
    if (!DATA.hits || !DATA.hits.length) {
      h += '<div class="empty">没有命中</div>';
    } else {
      h += '<div id="hits">';
      DATA.hits.forEach(x => {
        h += '<div class="hit"><span class="sc">' + x.score.toFixed(3) + '</span>' +
             '<span class="tag' + (x.assoc ? ' assoc' : '') + '">' +
             (x.assoc ? '联想' : '命中') + '</span><br>' + esc(x.text) + '</div>';
      });
      h += '</div>';
    }
    if (DATA.sections) {
      h += '<div class="sec">四格配额</div>';
      Object.keys(DATA.sections).forEach(k => {
        h += row(k, DATA.sections[k]);
      });
    }
    if (DATA.ctx_text) {
      h += '<div class="sec">组装出的上下文</div>';
      h += '<div id="ctxbox">' + esc(DATA.ctx_text) + '</div>';
    }
  }

  // 图例
  h += '<div class="sec">图例</div>';
  h += '<div class="legend">';
  Object.keys(RC).forEach(k => {
    h += '<span><i class="dot" style="background:' + RC[k] + '"></i>' +
         (REGION_CN[k] || k) + '</span>';
  });
  h += '</div>';
  h += '<div class="legend" style="margin-top:8px">';
  Object.keys(EC).forEach(k => {
    h += '<span><i class="dot" style="background:' + EC[k] + '"></i>' + k + '</span>';
  });
  h += '</div>';

  panel.innerHTML = h;
}

function row(k, v) {
  return '<div class="row"><span class="k">' + k + '</span><span class="v">' +
         v + '</span></div>';
}

// ---- 启动 ----
// 把 Python 算好的坐标贴到节点上
DATA.nodes.forEach(n => {
  n.x = n.px; n.y = n.py;
});
renderPanel();
fit();
window.addEventListener('resize', fit);

// 自适应视图：把所有点装进画布
function autoFit() {
  const r = cv.parentElement.getBoundingClientRect();
  let minX = 1e9, minY = 1e9, maxX = -1e9, maxY = -1e9;
  DATA.nodes.forEach(n => {
    minX = Math.min(minX, n.x); maxX = Math.max(maxX, n.x);
    minY = Math.min(minY, n.y); maxY = Math.max(maxY, n.y);
  });
  const pad = 40;
  const k = Math.min((r.width - pad * 2) / Math.max(1, maxX - minX),
                     (r.height - pad * 2) / Math.max(1, maxY - minY));
  view.k = Math.max(0.2, Math.min(2.5, k));
  view.x = (r.width - (maxX - minX) * view.k) / 2 - minX * view.k;
  view.y = (r.height - (maxY - minY) * view.k) / 2 - minY * view.k;
  draw();
}
autoFit();
</script>
</body>
</html>
"""


def build_html(payload):
    import json as _j
    nodes = payload["nodes"]
    pos = layout(nodes, payload["edges"])
    for n in nodes:
        p = pos.get(n["id"], (600.0, 450.0))
        n["px"], n["py"] = p
    blob = _j.dumps(payload, ensure_ascii=False)
    # 把 < 全部写成 \u003c —— JSON 解析出来还是 <，但 HTML 解析器
    # 再也不会在里面看见 </script> 或 <script> 而提前闭合脚本块。
    blob = blob.replace("<", "\\u003c")
    html = HTML.replace("__DATA__", blob)
    html = html.replace("__RC__", _j.dumps(REGION_COLOR, ensure_ascii=False))
    html = html.replace("__PC__", _j.dumps(POOL_CN, ensure_ascii=False))
    html = html.replace("__EC__", _j.dumps(EDGE_COLOR, ensure_ascii=False))
    html = html.replace("__RCN__", _j.dumps(REGION_CN, ensure_ascii=False))
    html = html.replace("__PCN__", _j.dumps(POOL_CN, ensure_ascii=False))
    return html


def main(argv=None):
    ap = argparse.ArgumentParser(description="生成记忆星云图")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--out", default=os.path.join(OUT_DIR, "nebula.html"))
    ap.add_argument("--query", default="")
    ap.add_argument("--open", action="store_true")
    a = ap.parse_args(argv)

    if not os.path.exists(a.db):
        print("找不到卡：%s" % a.db, file=sys.stderr)
        return 2

    t0 = time.time()
    payload = collect(a.db, a.query or None)
    t1 = time.time()
    html = build_html(payload)
    t2 = time.time()

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(html)

    print("✅ 星云图已生成：%s" % os.path.abspath(a.out))
    print("   %d 条记忆 / %d 条突触 / %.0f KB"
          % (len(payload["nodes"]), len(payload["edges"]),
             os.path.getsize(a.out) / 1024.0))
    print("   取数 %.0f ms，布局+写文件 %.0f ms" % ((t1 - t0) * 1000, (t2 - t1) * 1000))
    if a.query:
        print("   查询「%s」命中 %d 条" % (a.query, len(payload["hits"])))

    if a.open:
        os.startfile(a.out)          # noqa: 只在 Windows 上用
    return 0


if __name__ == "__main__":
    sys.exit(main())
