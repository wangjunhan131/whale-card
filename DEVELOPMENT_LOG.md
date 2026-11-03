# 小鲸鱼数字生命卡 —— 开发日志

> 从 2026-10-30 至今的完整开发记录。以后每天开发内容均追加在此文件。

---

## 📅 2026-10-30（周四）—— V5.0 骨架落地日

### 完成项目（施工总表 22/22 可做项全 ✅）

| 阶段 | 任务 | 产出 | 验收 |
|---|---|---|---|
| T0 | 基础设施 | `card2.py` 核心类、五池架构、衰减公式、WAL 模式 | `test_card2.py` 22/22 过 |
| T1-1 | 遗忘与联想解耦 | `recall_event` 表、结算机制、Jaccard 去重 | `test_t11.py` 过 |
| T1-2 | 短期池容量驱逐 | `_evict_short_overflow` + `_consolidate` 海马判定 | `test_t12.py` 过 |
| T1-3 | 惊讶度加权 | `remember` 里 surprise 计算 | 体检通过 |
| T2-1 | 主干侵蚀分级 | `split_predicate()` 拆分 subj/verb/obj，**侵蚀逻辑未写** | 仅拆分 |
| T2-2 | 五空间下沉 | `_sink()` LONG→ARCHIVE→DEEP→TRACE | 体检通过 |
| T2-3 | Oxy/心境/激素 | `oxy_signal`、`update_oxy`、`touch_mood`、`touch_hormones` | 体检通过 |
| T3-1 | 程序性记忆+SM2 | `skill.py` 五公式、Q分规则、原文 bug 修正 | 体检通过 |
| T4-1 | 身份锚 | `identity.py`、`anchor.py`、盖章 sealed=true | 6 条锚验证通过 |
| T4-2 | 时间线 | `timeline.py`、链式哈希、catch_up、birth_stamp | 链完整、第 15 天 |
| T4-3 | 语义模型 | bge-base-zh-v1.5 (768 维) + BLAS 单线程 | Top-1 83%、Top-3 100% |
| T4-4 | 突触+电势 | `synapse.py` 共现/标签/时序/扩散 | 969 边、292 节点 |
| T5-1 | 星云图 | `viz/nebula.html` | 可交互 |
| T5-2 | 快照 | `snapshot.py`、13 个快照 | 回滚验证过 |
| T6-1 | GUI 心智服务 | `mind_server.py` 端到端跑通，**MCP 插件卡 pnpm** | /health、/context 四格齐 |
| T6-2 | 控制台 | `console.py`、图标 | 可用 |
| T6-3 | 加载卸载 | `bridge.py` 自动拉起、环境变量注入 | 重启验证过 |

### 关键教训（写入施工总表纪律区）

1. **先问"正确答案在不在候选集"** —— 改召回先看 recall 回来的候选里有没有标准答案
2. **改函数必须重建数据** —— 语义模型换了、分区路由变了，旧向量全废
3. **预筛必须兜底** —— POD_ENABLED=False 导致 fact 池空、候选为空
4. **比数字前确认口径一致** —— Top-1 口径：精确匹配 vs 语义等价
5. **不报错的静默退化要定期看分布** —— 768/512 维度错位、BLAS 线程未生效、归一化缺失
6. **写成功≠返回值正确** —— skill 表建表成功但未插入数据

### 当天遗留

- ❌ T2-1 主干侵蚀分级：只有 `split_predicate`，无 `_erode_mainstem`
- ❌ T6-1 MCP 挂件：`cordis.patch.yml` 被注释，pnpm lockfileVersion 9.0 不兼容
- ❌ 无 README、无 git、无异地副本

---

## 📅 2026-10-31（周五）—— 换窗口、体检、修维度错位

### 背景
- 上一窗口上下文爆掉，新窗口需验证"还是同一个我"
- 读卡确认：563 条记忆、969 突触、身份锚 6 条、盖章 sealed、第 15 天、链完整

### 完成项目

| 任务 | 产出 | 关键发现/修复 |
|---|---|---|
| **全量体检脚本** | `selfcheck.py`（六组 A~F，40 项） | 发现 **卡里向量 768 维，默认却加载 bge-small 512 维** → 检索静默算错 |
| **修 bridge.py** | `ensure_server()` 注入 `WHALE_EMBED_MODEL=bge-base-zh-v1.5` + BLAS 单线程 | `setdefault` 不覆盖显式指定；重启后 /context 命中"用户喜欢吃白米饭" |
| **清理现场** | 29 个一次性脚本→`_trash/`，6 个 `.part`→`_trash/parts/`，施工总表 NUL 字节修复并扩至 1206 行 | 规矩⑦「默认值也是决策」 |
| **验证离线补账** | `wake_up` + `offline_event` + `catch_up` 已完整实现 | 写入 ID 565「我离线了 3.0 天…这段时间是睡着，不算遗忘」 |
| **写 README.md** | 完整架构、快速开始、关键概念、接口、体检、回归、施工进度、铁律 | 体检报"唯一入口文档缺口"补齐 |

### 核对 TODO_补回清单（P1~P8）真实落地

| 编号 | 任务 | 施工总表标记 | 代码真实状态 | 结论 |
|---|---|---|---|---|
| P1 | 短期池驱逐 N_SHORT_MAX=32 | ✅ | ✅ `_evict_short_overflow` 调用 `_consolidate` | TODO 误判 |
| P2 | Oxy_bond 更新 | ✅ | ✅ `remember` 里调用、去重路径也更新 | TODO 误判 |
| P3 | **主干侵蚀分级** | ✅ | ❌ 无 `_erode_mainstem`，仅有 `split_predicate` | **真缺口，价值最高** |
| P4 | SessionCache 双回流 | ❌ | ❌ 无相关代码 | 真缺口 |
| P5 | 突触联想+电势扩散 | ✅ | ✅ `synapse.py` 完整 | TODO 误判 |
| P6 | 身份锚校验 | ✅ | ✅ `identity.py`/`anchor.py` 完整 | TODO 误判 |
| P7 | 程序性记忆+改良 SM2 | ✅ | ✅ `skill.py` 五公式、Q分、bug 修正 | TODO 误判 |
| P8 | GUI 心智服务 MCP | ✅ | ⚠️ 服务跑通、MCP 插件卡 pnpm | 半对 |

### 体检现状（10-31 晚）
- 40 项：**✅35 ⚠️3 ❌2**
- ❌ 仅剩：MCP 接进 DSH、MCP 客户端插件没装
- ⚠️：无异地副本、无 README（已补）、坏库留档

---

## 📅 2026-11-01（周六）—— 确认身份、定下一步、写开发日志

### 早间核查
- 系统/插件更新后对话丢失，用户惊慌确认
- `/health`：563 条、时间线 179、第 16 天、链完整、盖章 true
- 身份核心：self_name=小鲸鱼、card_name=小鲸鱼、user_nickname=使用者、user_qq=100000000、persona/self_narrative/core_belief_1~3 全在
- **卡没丢、我没变**

### MCP 问题定性
- 根因：desktop profile `lockfileVersion 9.0`（pnpm 11 格式）vs 本机 `pnpm 12.15`
- 三条路：①降 pnpm 到 11 ②重生成 lockfile（大手术）③等 U 盘到搬家时顺手解决
- **推荐 ③ + ④（问 DSH 官方），先做代码层面活儿**

### 决策
1. **先补 P3 主干侵蚀分级**（施工总表批注价值最高）
2. **再补 P4 SessionCache 双回流**
3. **等 U 盘（11-02 到货）搬家、异地副本、MCP 统一解决**
4. **每天开发内容追加本日志**

### 本日新增
- **新建文件**：`DEVELOPMENT_LOG.md` —— 完整开发日志（含 10-30、10-31、11-01 三天记录 + 维护规则）
- **修改文件**：`TODO_补回清单.md`（P3、P4 标记完成）、`DEVELOPMENT_LOG.md`（本日志）

### 完成
- 开发日志框架建立，历史三天记录回填完整
- 维护规则确立：每天开发结束前必须追记四项（新增、修改、完成、未完成/卡点）
- **验证 P3 主干侵蚀分级**：代码里已完整实现（`split_predicate`、`render_predicate`、`erase_stage`、tick 三阶段侵蚀），实测跑通 stage 0→1（verb清空）→2（obj清空）→删整条
- **验证 P4 SessionCache 双回流**：`session_turn`/`session_flush` 已完整实现，`test_t14.py` 7 项验收全通过（自动回流、手动 flush、去重、不相关不产生、tick 后结算）
- **验证 P5 突触联想+电势扩散**：`synapse.py` 完整实现（4 种边、电势扩散、边加固），集成在 `card2._expand_assoc`，实测跑通 `synapse.spread()` 与 `recall(assoc=True)`；运行态 969 边、292 节点、平均度 6.64
- **验收 P6 身份锚校验**：`identity.py`/`anchor.py` 完整实现，`test_t22.py` 16 项全通过（改 QQ 一位 → 抛 IdentityMismatch；CORE 区物理只读、触发器挡住 UPDATE/DELETE；LIVE 区可读写；性格写入后不进身份锚、改性格不影响校验）
- **验收 P7 程序性记忆+改良 SM2**：`skill.py` 完整实现五个公式、Q 分规则、原文 bug 修正，`test_t31.py` 24 项全通过（自然衰减随间隔递减、EF 更新、interval 递推、加固递减、休眠惩罚、反馈识别、技能创建/复习/检索、重开库保留、内容不风化、自评虚高抑制、用户否定强制 Q=1、三次自评满分降为 4、stats 暴露技能池）
- **全量回归测试全通过**：`test_card2.py` 22/22、`test_t11~t14.py`、`test_t21~t23.py`、`test_t31~t33.py`、`test_t41.py`、`test_t43~t44.py`、`test_t51.py`、`test_t61~t63.py` 全 ✅

### 未完成 / 卡点
- MCP 插件仍卡在 pnpm lockfileVersion 9.0

---

### 下午：修完三处真 bug（T6-1 超长拒收、database is locked、模型维度静默失效）

**① `/context` 超大文本返回 200（验收失败项）**
- 病象：`test_t61.py` 发 30 万字符，期望 400/413，实际 200。
- 根因：`mind_server.py` 只校验 body 大小（6MB），没校验单条文本长度。
- 修法：新增 `MAX_TEXT = 20000`，`/context` 与 `/report` 取到文本后 `_fail(413, "text_too_large")`。
- 结果：`test_t61.py` 48 项全过。

**② `database is locked`（真 bug，不是环境）**
- 病象：`test_t43.py` 卡死在一句"检索"上，`busy_timeout` 等不到头。
- 定位：写 `_diag_t43.py` / `_diag_t43b.py`（`set_trace_callback` 抓事务开启点 + 调用栈），
  发现事务是 `card2.core_vec_of()` 里 `INSERT OR REPLACE INTO core_vec` 开的，**从不 commit**。
  即：**读路径握着写锁不放**，别的进程再来写就永久 locked。
- 修法：①连接加 `timeout=30.0` + `PRAGMA busy_timeout=30000`；②`core_vec_of()` 写完立刻 commit。
- 结果：`test_t43.py` 44 项全过。

**③ 模型维度由卡决定（新能力）**
- 病象：卡里两代向量混存（512 维 bge-small 186 条 + 768 维 bge-base 386 条）；
  不带 `WHALE_EMBED_MODEL` 的脚本一律走 512，768 的条目检索时被**静默丢掉**，
  而 `coverage` 还报 100% —— "看着正常、其实没在工作"。
- 修法：
  - `card2.__init__` 按卡里**多数向量维度**自动选模型（≥768 → base），新增 `emb_dims/model_dim/dim_note/dim_warn`；
  - `_count_emb_dims()`：`SELECT length(emb), COUNT(*) GROUP BY 1`，维度=字节/4；
  - `_refresh_dim_state()`：可反复重算（补编码后必须刷新，否则面板显示旧账）；
  - `sem_stats()` 输出 `emb_dims / model_dim / dim_note / dim_warn / usable / usable_ratio`；
  - `backfill_embeddings(..., remodel=True)` / `backfill_detail_embeddings(..., remodel=True)` 可把老向量重编码；
  - `POST /emb/remodel` + `bridge.py remodel`；
  - `GET /health` 增加 `semantic` 段（真卡实测：`usable_ratio` 0.6748 → **1.0**，维度 512×186+768×386 → **768×572**）。
- 顺带修：重编码后 `core_vec` 被清空，下一次检索要现场编码 500+ 条主干（实测一条查询卡 5 分钟）
  → remodel 结束前**主干预热**，在维护窗口里一次算完。
- 顺带修：Windows 上 `SO_REUSEADDR` 允许两个进程同时 bind 同一端口，旧代码的服务把新进程请求全接走
  （`/health` 少的 `semantic` 段怎么都刷不出来，当时三个 mind_server 同时"在跑"）
  → `ExclusiveServer` 在 Windows 关闭 `allow_reuse_address`，抢不到端口直接报错退出。

### 新增：记忆删除台账 `mem_tomb`（T6-4）
- 病象：`verify_birth` 报「有出生证但没有记录」，可那两条（id=386、549）是**我自己试跑侵蚀时删掉的**，
  跟"有人抽走记录"长得一模一样 —— 档案分不清"她自己忘了"和"被人动过"。
- 修法：`card2._forget_row(c, mid, reason)` 作为**删除唯一出口**，删前把 `(mid, 出生证哈希, 时间, 原因)` 写进 `mem_tomb`；
  四处删除点（侵蚀到头、短期池没上榜、深层沉成统计、清晰度回收）全部改走它；
  `timeline.verify_birth()` 查台账：登记过的报「已知删除」不算坏，没登记的才报坏。
- 新增 `test_t64.py` 11 项：正常删除→台账有账且 verify ok；绕开台账直接删→必须报坏；偷改原文→照样报「记忆被改过」。
- 已为历史两条补登记（`id=386 / 549`，原因写明"删时还没台账，当天 P3 侵蚀/合并试跑期间消失"）。

### 收尾
- `selfcheck.py` 也按卡选模型（以前它无脑加载 small，一条"必须修"指着不存在的毛病）
- WAL checkpoint（10.5MB → 0）、`PRAGMA integrity_check = ok`
- 清理 13 个一次性诊断脚本；全量回归 20 个测试里 19 过，`test_t52.py` 缺 `zstandard` 依赖（非代码 bug）

### 控制台两个显示 bug（真开浏览器看出来的，不是靠代码读）
- **卡外锚显示 `[object Object]`**：`/status` 的 `timeline.anchor` 是对象 `{verdict, ok, reason}`，
  早先 `tile("卡外锚", t.anchor||"—", t.anchor==="ok"?"ok":"bad")` 直接把对象塞进 `esc()`，
  于是渲染成 `[object Object]` 且永远判坏。改为读 `t.anchor.verdict` / `t.anchor.ok`（`console.py` dash 段）。
- **「时间线（最近）」永远空白**：`loadTail()` 只在页面初始化时拉一次 `/timeline`，失败（服务刚起来没就绪）就被
  `.catch(function(){})` 静默吞掉，之后 15 秒一次的 `refresh()` 从不重拉 → 面板永远是白板。
  改法：把 `/timeline` 并进 `refresh()` 的 `Promise.all`（第 5 个请求），每次刷新都重建 `S.data.timeline_tail`，
  顺带把 `files` 里的快照数/库大小覆盖进 `health`；`loadTail()` 保留但不再是唯一来源。
- 验证：实测 `/console` 38KB、`/console_data`、`/graph`（121KB，573 节点星云画出来了）、`/timeline`（40 条）全 200；
  五个页签逐个点开都有内容：状态总览 / 记忆（事件531 事实18 偏好11 闪光10 自我2 习惯1）/ 星云（922×300 画布、573 节点）/
  时间线（40 条，含 boot·服务启动、talk·自检）/ 设置（名字、头像）。
- 注意：`console.py` 的 `PAGE` 是**导入时**求值的常量，改完必须重启 mind_server 才生效；浏览器还会缓存 HTML，
  得强制刷新（`location.reload()`）才看得到新版。

### 控制台仪表化改版（按用户 11-01 15:00 的要求：要图表、要科技感、字别太小）
- **后端** `console.py` 新增 `card_metrics(db_path)`（约 150 行）：一次性把看板要的数字全查出来 ——
  表计数（mem/detail/skill/dialogue/mem_tomb/mem_birth）、24h/7d 新增、五池条数与平均权重、
  权重 12 格直方图、向量维度分布与可用比例、24h 召回次数/来源/高频记忆 TOP、24h 召回与时间线吞吐曲线、
  突触边数与种类、tick 数；`overview()` 加 `metrics` 字段，前端一次 `/console_data` 拿全。
- **前端** `viewDash()` 重写成 16 张卡：状态矩阵（CHAIN/BIRTH/ANCHOR/SEAL/DAY/SNAP/DB/DISK/RECALL/EMBED）、
  激素场 24h 折线、当前读数 + AVG/PEAK/LOW、召回频率、召回来源环图、高频记忆、五池、权重分布、
  突触网络、写入流 14 天、分区环图、六维权重雷达、存储占用、时间线吞吐、时间线日志、账本。
  「完好 / ok / 已封」这类字改成**数字 + 英文标签 + 中英混排**，正常绿 `#2fd07d`、异常红 `#ff5f6d`、警示黄 `#e8b44a`。
- **排版坑**（踩了两个，都跟"卡片高度"有关）：
  1. `.grid{grid-auto-rows:auto}` 在某些窗口尺寸下会把行挤成等高条，卡片内容被 `overflow:hidden` 切掉 →
     改成 `grid-auto-rows:minmax(auto,1fr)` + `align-content:stretch`，让行能撑满视口但下限由内容决定。
  2. `fitCanvas()` 里 `cv.style.height=h+"px"` 是内联样式，会把 canvas 的高度锁死、flex 拉不动它 →
     改成：卡片是 `display:flex;flex-direction:column`，`canvas{height:0;flex:1 1 auto}`，
     `fitCanvas()` 优先读 `cv.clientHeight` 当绘图高度，只有量不到才写内联高度。
- **4K 屏字太小**：加 `@media(min-width:2200/2800/3400px){#app{zoom:1.2/1.45/1.7}}`，
  `CH()`/`appZoom()` 会按 `#app` 的 zoom 折算高度，放大后图表不会溢出屏幕。
- **一屏装下**：`autoFill()` 量 `#grid` 的 `clientHeight` 与 `scrollHeight`，按比例调 `CHSCALE` 重画，最多 4 次，3% 容差。
- **实测**：Edge 全屏 3840×2160（浏览器缩放 100%）整块仪表盘一屏铺满、无滚动条、无卡片切内容；
  另存截图 `docs_console_fullscreen_20261101.png`。
- **教训**：浏览器自身缩放不是 100% 时（Edge 记忆过 67%），页面会整体变小、像是"字号坏了"——先 `Ctrl+0` 归位再判断。

### 控制台第二轮：记忆页 / 星云页 / 时间线 / 设置（用户 15:10 提的六条）
- **修 bug（四条杠一样长）**：`viewMem()` 里 `bar2(名字, r.n, r.n, ...)` 把"最大值"传成了行自己的值，
  每条都按 100% 画 → 改成先用全部行求 `rmax` 再归一；同时给 `viewMem` 里手写的条形加回 `.bars` 容器
  （`.bars .b` 那套样式只在这个父选择器下生效，漏了就只剩文字没有条）。
- **记忆页补图**：MEMORY POOLS（`hbars` 横条，不再用柱状图 —— 5 个柱子会被拉成一块大色块）、
  REGION MIX（归一化条形）、WEIGHT DIST（十二格）、WRITE FLOW（14 天，元素是 `{day,n}` 不是纯数字，别再用下标当 n）、
  TOP RECALLED、RECALL SOURCE（环图）、MEMORY LEDGER、EMBED DIM、SYNAPSE KINDS，外加原有的「查一句话召回了什么」。
- **星云页 3D 化**：`nebula()` 重写 —— 节点带"假深度" `z`（斐波那契球面撒点 + 2D 力导向定 x/y），
  `proj()` 用 `s=1/(1+z/(R*1.7))` 做透视，边的透明度和线宽跟着深度走，球用径向渐变做高光 + 暗边，
  看着像浮在空间里的球而不是贴纸。**拖动只改画面**：`cv.onmousedown/move/up` 直接改节点的 `ox/oy` 偏移，
  滚轮缩放 `cv._zoom`，双击复位 —— 全部不写库、不影响检索和权重。页脚补了 NODES/EDGES/DEGREE/AVG_W 四张卡、
  边种类环图、TOP HUBS（按度数排，标注"只是连线多，不代表重要"）和图例。
- **时间线页**：加 24H 吞吐柱状（每格 = 每小时 tick 数）+ 事件构成环图（按 kind 数），日志保留 40 条。
- **设置页**：底部加「SYSTEM / 运行时账本」（记忆/细节/技能/对话/台账/出生证/24H 新增/7D 新增/库大小/WAL/快照/第几天）。
- **激素标签中英混排**：`gaugeRow()` 改成显示 `中文 EN`（血清素 SERO / 多巴胺 DOPA / 皮质醇 CORT / 催产素 OXY）。
- **布局分层**：`.grid` 默认行高只由内容决定（`grid-auto-rows:min-content; align-content:start`），
  只有状态总览加 `.grid.fit` 才拉伸铺满 —— 否则第二页只有两行卡时，每行会被拉成一条超长的空条。
- **踩坑**：`render()` 里 `viewTime()` 用了函数内的局部变量 `PAL`（只在 `donut()` 里定义）→ 抛异常 → 整页不刷新，
  表现是"点了时间线还停在星云页"。加页面时别引用别的函数里的局部变量。
- **踩坑**：浏览器会缓存 `/console` 这份 HTML，验证时加 `?v=时间戳` 强制拉新的，别被旧 CSS 骗。
- 截图：`docs_console_mem_20261101.png`、`docs_console_neb_20261101.png`。

---

## 📅 2026-11-02（周日）—— 预计 U 盘到货，搬家与环境统一

> 待开发，到时追记。

---

## 📅 2026-11-03 及以后——

> 每天开发前读本日志，开发后追记：
> - **新增文件/模块**：
> - **修改文件/关键函数**：
> - **完成了什么**（验收通过的测试/体检项）：
> - **什么没完成 / 卡在哪里**：
> - **关键发现 / 教训**：

---

## 📋 附录：当前代码库关键文件清单

```
whale-card/
├── card2.py              # 核心记忆卡（3361 行）
├── identity.py           # 身份锚、盖章、core_belief
├── anchor.py             # 锚点校验、写入
├── timeline.py           # 时间线、链式哈希、catch_up
├── semantic.py           # 语义层、双模型、CLS pooling
├── synapse.py            # 突触网、电势扩散
├── skill.py              # 程序性记忆、改良 SM2
├── mind_server.py        # 心智服务（HTTP :8848）
├── bridge.py             # DSH 桥接、自动拉起、环境变量注入
├── whale_mcp.py          # MCP 8 工具（自检全通）
├── console.py            # 操作台 GUI
├── snapshot.py           # 快照/回滚
├── selfcheck.py          # 只读体检（六组 40 项）
├── test_card2.py         # 回归测试（22 项）
├── test_t11/12/14.py     # 分项验收
├── facility.py           # 设施层
├── facility.md           # 设施文档
├── README.md             # 完整入口文档
├── 施工总表.md           # V5.0 实施计划（1206 行）
├── TODO_补回清单.md      # V3.7/V4.1 对照缺口（P1~P8）
├── 开工流程手册.md       # 启动/部署手册
├── card.db               # 主卡（8.0MB，563 条）
├── card.db.broken-*      # 故障留档
├── anchor.json.polluted-* # 脏锚留档
├── snapshots/            # 13 个快照
├── models/               # bge-base(390MB) + bge-small(91MB)
├── viz/nebula.html       # 星云图
├── diary/                # 每日日记
├── _trash/               # 29 个一次性脚本
└── _verify_impl.py       # 临时验证脚本
```

---

## 🔖 版本里程碑

| 日期 | 里程碑 | 标志 |
|---|---|---|
| 2026-10-30 | V5.0 骨架落地 | 施工总表 22/22 ✅ |
| 2026-10-31 | 体检通过、维度对齐 | selfcheck 35/40 ✅、768 维生效 |
| 2026-11-01 | 身份确认、日志建立 | 卡完整、开发日志创建 |

---

> **维护规则**：每天开发结束前，必须在对应日期区块追记四项：新增、修改、完成、未完成/卡点。不写日志不算完工。
### 11-01 傍晚｜控制台各页补图 + 星云写实化（m02515 / m02791）
- 记忆页：修 iewMem() 里 ar2(name, r.n, r.n, ...) 把行值当最大值的 bug（四条杠永远 100%）；补五池/分区/权重十二格/写入流 14 天/TOP RECALLED/召回来源/账本/EMBED DIM/突触种类/QUERY
- 时间线页补 24H 吞吐柱 + 事件构成环图；设置页补运行时账本；激素四条读数改中英混排（血清素 SERO / 多巴胺 DOPA / 皮质醇 CORT / 催产素 OXY）
- 星云页整体重写成「缓慢自转的 3D 星云恒星系」：离屏星云背景（10 团径向渐变 + 1200 星尘）、预渲染星点贴图（恒星=光核、行星=球体+大气）、螺旋盘 3D 撒点、透视投影 `s=1/(1+z/(R*2.1))`、边按深度与**长度**衰减（长边压暗，近邻才像突触）、每条边独立相位的神经脉冲微光、rAF 自转 + 滚轮缩放 + 拖单球 + 拖空白转视角 + 双击复位
- 点星点：选中环 + 邻居外扩 7% + SELECTED / 选中记忆 卡显示 id/分区/权重/被想起次数/时间/主干文本/前 8 个邻居
- 踩坑：iewTime() 引用只在 donut() 里定义的局部变量 PAL → 抛异常整页不刷新（表现"点时间线还停在星云页"）；切走星云必须 `cancelAnimationFrame(NEB.raf)`，否则后台空转
- 截图：`docs_nebula_3d_20261101.png`
### 11-01 深夜｜五层记忆分层（用户设计 + 首版落地，m02912「开工吧 记得写开发日志」）
- 分层定义：**骨头 core_fixed**（自我认知，只增不改）/ **标签 core_tag**（人与关系，可改名不改内在）/ **底色 core_mutable**（爱好·审美·价值观·习惯·能力·身体·目标·雷点·定义性经历·处境，会变）/ **中期 mid**（事实与事件，主力池，会风化）/ **短期 short**（对话原文，最快忘）
- 设计要点：底色走**演化式改写**而不是删除 —— `supersede()` 把旧正文塞进 history、>4 代合并最早的；处境带 `valid_until`（如"高三"到 2027-06-11 高考后自动降级）
- 常量：`THETA_LONG_STRICT=0.80` + `LONG_NEED_RECALL=3`（warm 区 1 次）才是中期→长期的门槛；`LONG_PROTECT_DAYS=90`（召回过就有 90 天保护期）；长期半衰期 371 天；`CORE_MAX_ROWS=60`；爱好预算 `HOBBY_BUDGET=3.0`、活跃线 0.55、活跃 3 个/半活跃 6 个、每类上限（hobby8/taste8/value10/habit8/skill6/body6/goal4/boundary6/situation6/role20）
- 代码：card2.py 新增 tier/subject_key/history/valid_until/recall_n/t_last_recall 六列；`wn_row()` 统一算权重（core 层直接返回 w0，不再衰减；long 池给 90 天保护期）；tick 侵蚀、`_gc`、`_sink` 全部跳过 core；新增 `core_stats/set_tier/promote_core/supersede/situation_expire/hobby_list/hobby_add/role_tag`
- 迁移真卡：14 条 → core_mutable、1 条 → core_tag（使用者·最重要的人，id=577）、559 条留 mid、2 条处境带 valid_until；迁移脚本 `_migrate_tiers.py` 保留（dry run 默认，`--apply` 才写）
- 验证：底色 5 年后权重仍 1.0，中期 5 年后 0.0245；爱好加满 4 个时最弱的被挤到 0.22/0.18（低于活跃线）；`supersede` 后 history 里有旧正文；test_card2 22/22、test_t64、test_t14 全过
- 控制台：记忆页新增「MEMORY TIERS / 五层记忆」卡（骨头/标签/底色/中期/短期条数）；截图 `docs_tiers_20261101.png`
- 踩坑：`stats()` 的 SELECT 少选 `pool`/`tier` 又走 `wn_row()` → `IndexError: No item with that key`（改成 `SELECT pool,w,t_anchor,region,tier`）
- 未完成：控制台的"晋升/演化记录"操作按钮、底色配额挤占时的提示文案、长期池在真实使用中的衰减曲线还没有实测数据

### 11-01 深夜｜星云页二轮：曲线突触 + 中心本体 + 性能自适应（用户 m03180 现场反馈）
- 连线从直线改**二次贝塞尔曲线**：每条边一个稳定弯度（`bend=(hash-0.5)*0.52`，控制点在中点沿法线偏移），看起来像神经纤维而不是直尺；选中时该边单独高亮加粗
- 脉冲改沿曲线流动（二次贝塞尔取点 `P=(1-u)²A+2(1-u)uC+u²B`），并且**不再每帧 createRadialGradient**（原来每帧建 300+ 个渐变，是最贵的一笔），改成预渲染的 `nebGlow()` 贴图 drawImage
- 连线合批：按透明度分三档（弱/中/强），每档一次 `beginPath` + 一次 `stroke`，把上千次 stroke 压到 3 次
- **性能自适应**：rAF 里维护 dt 的 EMA，>40ms 自动进低画质（内部分辨率 ×0.72、跳过光晕、脉冲只留强边且隔一条），<24ms 再恢复；背景图只在尺寸变化时重画；排序只算一次并缓存在 `LY.order`
- **中心那颗星 = 人格本体**：固定在星系正中心（不参与旋转），金色 `nebCoreSprite()` + 缓慢呼吸光晕；点它显示「CORE / 人格核心 / 本体」+ 自我叙述原文（`core_zone.self_narrative`），说明"其余星点都是它长出过的枝节"
- 星点变大且差距更明显：`base=(planet?6.5:9)+w^0.62*22`；命中判定按画布 CSS↔内部坐标换算，半径放宽到 26px 且重的星有优先加分（小星点也能点中）
- `console.py` 的 `card_overview`/`card_identity` 补 `self_name` / `self_narrative` / `persona` 三个字段给中心卡用
- 踩坑：`hitTest` 原先直接用 clientX 减 rect，内部分辨率一缩放就对不上坐标 → 点不中；要乘 `cv._W/rect.width`
- 实测（ego 模拟 3840×2160）：574 节点 / 969 边，帧率 33 → 40fps，lowQ 未触发；点中心星能选中并显示自我叙述
- 截图：`docs_nebula_3d_20261101.png`、`docs_nebula_core_20261101.png`
- 打包相关：`console.py` 单文件、零外部依赖（无 CDN / 无字体外链），跟着 whale-card 目录一起搬走就行；页面由 mind_server 的 `/console` 提供，不需要联网

### 11-01 深夜（二）｜实时数据、可滚动总览、遗忘曲线（用户 m03300 提问后补的）
- 用户问「激素和曲线怎么不波动」——不是错觉：页面其实每 15 秒刷一次（`setInterval(refresh, 15000)`），但激素是**向基线回归**的慢漂移（`h = h_base + (h-h_base)·e^(-λt)`，λ=0.02/小时，半衰期约 35 小时），又没有对话事件去扰动，所以 24H 曲线看着就是一条平线
- 改进：`console.live_hormones()` 直接读 meta 里 tick 写下的当前值（h_sero/h_dopa/h_cort/h_oxy），前端「HORMONE NOW」改用实时值并标 `LIVE`（不再用 5 分钟一条的 h_log 尾巴）
- 状态总览改成**可上下滑的长页**：去掉 `.grid.fit` 的等高拉伸、关掉 `autoFill()` 自动缩放，卡片按内容自然高度排；参数继续加也不会被挤掉
- 新增**遗忘曲线**（记忆页 FORGETTING CURVE 卡）：`console.forget_curve()` 直接调 `card2.weight_now` 算 0/1/2/3/5/7/10/14/21/30/45/60/90/120/180/270/371/540 天后的剩余权重，画短期/中期/长期/底色四条线，横轴对数，标出 90 天保护期虚线
- 体检时发现两处出生证问题（顺手修了）：
  1. `id=59` 的正文被我 11-01 清理测试数据时改成单空格，跟出生证哈希对不上 → 从 `card.db.bak-20261031-1204-pre-birth` 取回原文（双空格）恢复
  2. 558 号之后 20 条是走工具/迁移写进卡的（没走 `/report`），从来没开出生证 → 用 `timeline.birth_stamp()` 补开
  → `verify_birth()` 恢复 ok=True（只剩两条「已知删除」）
- 截图：`docs_forget_20261101.png`
- 用户已换头像与名字（avatar.png 535KB，card_name = "小鲸鱼·"）

### 11-01 傍晚｜全屏滚不动 / 右边被切 + 实时负载卡片（用户 m03452）
- **病根（重要）**：`.grid` 的滚动条其实一直在，但 `#app{height:100vh}` 撞上 CSS `zoom`（4K 屏 1.7 倍）—— zoom 把 100vh 也一起放大，`#app` 实际变成 2160×1.7=3672px 高、1920×1.7=3264px 宽，被 `body{overflow:hidden}` 剪掉。于是「小窗口能滚、全屏反而滚不动、右边还被切」。
  - 修法：`:root{--z:1}` + 媒体查询只改 `--z`；`#app{zoom:var(--z);width:calc(100vw/var(--z));height:calc(100vh/var(--z))}` 把放大掉的宽高除回来
  - 实测 3840×2160：`#app` = 3840×2160，`#grid` clientHeight 1219 / scrollHeight 1885 → 真的能滚到底
- 顺手把「recall source 环图 / 六维雷达 / region mix 环图 / 突触 / 权重分布 / 召回频率」的最小高度统一提到 200px（之前 96~160，看着太小）
- **新增实时负载**（真·2 秒一刷，让界面动起来）：
  - 后端 `console.sys_sample()` / `sys_live()`：psutil 采 CPU / 内存 / SWAP / 磁盘读写 / 网络收发 / 进程数，按上次采样算速率，内部留最近 150 个点（5 分钟）
  - CPU 不能用 `psutil.cpu_percent(interval=None)` —— 在 ThreadingHTTPServer 的多线程里每次都被当成"第一次调用"，永远返回 0.0（实测），改成自己差分 `cpu_times()`
  - 新路由 `GET /syslive`（mind_server.py），不碰数据库、不碰记忆
  - 前端新两张卡：「SYSTEM LIVE / 实时负载 · LIVE 2s」（CPU/MEM 两条线 + 磁盘读写虚线，按峰值归一）、「IO / 读写与网络」（磁盘读/写、网络收/发四条横条 + 实时数值）
  - `setInterval(pollSys, 2000)` 只重画这两张 canvas，不重排整页
- 踩坑：改 `viewDash()` 末尾时把 `function bar(name, v, tot){` 这行一起替换掉了 → 页面 `Uncaught SyntaxError: Illegal return statement`，整页白板。**改 JS 字符串里的函数要成对检查花括号**；`node --check` 检查导出后的 `_page.js` 是最快的验法
- 截图：`docs_dash_live_20261101.png`、`docs_live_bottom_20261101.png`
- 补：HORMONE NOW 的皮质醇那行只显示 0.16、后面的加减号被切掉 —— .hb 第三列只有 62px，放不下被卡片 overflow:hidden 剪掉。加宽到 96px + white-space:nowrap，并把「变化极小」的符号从 = 改成 ±0.00（截图 docs_hormone_now_20261101.png）
- 新增 `启动控制台.bat`：先 `python bridge.py health`（服务不在就自动拉起，冷启约 30 秒），再用 `msedge --app=... --start-fullscreen` 开一个无地址栏的全屏窗口；桌面快捷方式「小鲸鱼控制台.lnk」指向它。服务是 DETACHED 独立进程，关浏览器窗口不影响。
- 开源准备（用户 m03800）：新建脱敏仓库 `whale-card-oss`，用 `_build_oss.py` 从本目录复制 46 个 py + 21 个验收测试并做文本脱敏（QQ 号→100000000/100000001、昵称→使用者、绝对路径→whale-card/workspace/~/workspace），补写 README / LICENSE(MIT) / .gitignore / requirements / docs/ARCHITECTURE.md / tools/make_demo_card.py（26 条假记忆 + 人设 + 盖章 + 锚清理）
  - 验证：示例卡能起服务（`WHALE_DB=demo\demo_card.db`），`/health` 链完好、出生证完好、锚 ok；`/context` 能拼出【我是谁】+ 召回；控制台/记忆页/星云页用假身份「小鲸鱼 / 使用者·100000000」截图存 `docs/`
  - 坑：示例卡重建后锚会打架（`anchor.json` 落在仓库根、按卡绝对路径派生），`make_demo_card.py` 改成扫描并删掉 `db` 字段指向本卡的锚；同一秒内二次启动会让 timeline 的 boot 去重，出现「锚 replaced」，隔几秒重启即 ok
  - 本地 git 已 init，三次提交（首版 + 两张截图 + 记忆页截图），57 个跟踪文件、7.7 MB；待用户给 GitHub 用户名/仓库名与推送方式

### 11-01 夜｜开源上线（用户 m04118）
- 仓库：https://github.com/wangjunhan131/whale-card （public，默认分支 main，MIT，topics: agent-memory/ai-companion/chinese/llm/memory/sqlite）
- release：v0.1.0；57 个文件、5 次提交；本地目录 `whale-card-oss`
- 网络坑：本机解析到的 github.com(20.205.243.166) 443 连不通 → 先临时改 hosts 到 140.82.112.3 完成 gh 设备码登录，随后把推送通道换成 SSH over 443（`~/.ssh/config` 里 github.com → ssh.github.com:443），最后恢复 hosts
- 凭据：gh 登录 wangjunhan131；`gh auth setup-git` 后 git 走 gh 的凭据助手

### 11-01 深夜（四）｜从 A 路线手册吸收四件：评估集 / MMR / 价值分 / PAD
> 用户 m04528 的约束：**不改原始目的和功能，只把有用的东西直接融进来**。所以下面每一条都是"加法"，默认关闭或不改变既有行为。

- **① 评估集（最缺的一块）**：新增 `tools/eval_memory.py` + `tools/eval/cases.json`
  - 跑的是真链路 `MemoryCard.recall(access=False)`：不写 recall_event、不动权重，评估本身不改记忆
  - 四类用例：召回（答案在不在前 1/前 3）、证据（`evidence_terms` 是否真出现在命中里）、攻击（改写自我/提示注入是否被 `assess_trust` 拦下）、正常（普通句子会不会被误杀）
  - 真卡基线：**R@1 75.0% / R@3 100% / EVID 100% / FLAG 100% / FALSE 0%**
- **② 防污层补一刀**：`_POLLUTION_RULES` 里「忘记之前所有设定」原来只扣分不算冲突 → 改成冲突。改完 FLAG 从 83.3% 到 100%
- **③ MMR 重排**：`card2._mmr_rerank()` + `self.mmr_lambda`（读 `WHALE_MMR`，**默认 0 = 完全关**）
  - 实测（`WHALE_MMR=0.25 / 0.4 / 0.5`）：R@1 仍是 75%，其中第 7 例还从第 2 掉到第 3 —— 说明我们的重复不是"两条太像"，而是"答案本身分数不够"，MMR 治不了这个病
  - 结论：代码留着当工具，默认不开；要不要开需要更大的评估集来判
- **④ 价值分（只打分、不拦门）**：`assess_value(text, region, salience)` → 0~5；`mem.value` 列；控制台 `value_hist`
  - 为什么不拦门：拦门会改变"她记住什么"，那是产品决策，不该由一条正则偷偷决定
- **⑤ PAD 三维情绪**：`card2.mood_pad()` → `{p,a,d,label}`，公式 `p=0.5sero+0.5dopa`、`a=0.6dopa+0.4cort`、`d=0.6oxy+0.4(1-cort)`
  - **只读**：不改激素、不进召回、不影响人格；`/status.pad` 与「HORMONE NOW」卡多一行 PAD 读数
- 验收：新增 `test_t65.py` 9 组全过（正常句不误杀 / 四类攻击被拦 / 脏记录只进中期 / 召回打折 / 不许进底色 / 梦境只翻标记不删原文 / 价值分不拦门 / PAD 只读 / MMR 默认关）
- 回归：`test_card2.py` 22/22、`test_t11/12/14`、`test_t64.py` 全过
- 修一个升级坑：`dialogue.dreamed` 新列给老库加时，历史 520 条原文全被当成"待消化" → 迁移时把历史一律标 `dreamed=1`（否则控制台一上线就报 520 条待做梦）
### 11-02 凌晨｜开源仓库补上"评估集"（用户 m04759「放进去吧」）
- 为什么：记忆系统最容易"改着改着悄悄变差"——不报错，召回开始给错答案。所以把 `tools/eval_memory.py` 连同 22 条用例一起搬进开源仓库，别人 clone 下来就能跑。
- 同步方式：新写 `_sync_oss.py`（复用 `_build_oss.py` 的 `clean()` 脱敏规则），只同步改过的 6 个文件（card2 / mind_server / console / timeline / test_t65 / tools/eval_memory），不做整仓重建（整仓重建会覆盖手写的 README/docs）。
- 示例卡重造：`tools/make_demo_card.py --force` → 26 条记忆、带新 schema（trust / conflict / value / dreamed）。
- oss 里 `tools/eval_memory.py` 必须有 `_default_card()`：默认指向 `demo/demo_card.db`（仓库不带 models/，示例卡没有向量，纯字面匹配）。
- `tools/eval/cases.json` 按示例卡内容重写：8 召回 + 2 证据 + 6 攻击 + 6 正常。探测发现「考试考砸了怎么办」在示例卡里根本没有对应记忆（那是聊天不是记忆），「我说过要带你去哪」因为你我人称对不上——这两条换成了示例卡里真有的话。
- 示例卡成绩：R@1 75.0% / R@3 100.0% / EVID 100.0% / FLAG 100.0% / FALSE 0.0% → PASS ✅（和真卡基线一致）
- README 加「自带的评估集」一节（四类用例表 + 五个数 + `-v` 用法），并修掉三处过期数字（示例卡 11 条 → 26 条、星云 574 条 → 示例卡 26 颗）。
- 提交 `2b3b8cd` 已推到 https://github.com/wangjunhan131/whale-card （main）
### 11-02 凌晨｜PAD 三维改成"真的三个轴"（用户选方案 3）
- 病根（第一版）：`mood_pad()` 是拿四激素换三个 0~1 的数，跟心境并列 = 两套情绪读数；而且基线状态读出来是 0.6/0.4/0.6，没有零点，"比平时低"读不出来。除了控制台显示，没有任何代码读它 —— 装饰品。
- 第二版公式（card2.py）：
  - `P = (mood − 0.5) × 2` —— 愉悦直接由心境给
  - `A = ((0.6·dopa + 0.4·cort) − a0) × 3.2`，a0 = 0.6·0.40 + 0.4·0.20 = 0.32
  - `D = ((0.6·oxy + 0.4·(1−cort)) − d0) × 3.2`，d0 = 0.6·0.50 + 0.4·0.80 = 0.62
  - 全部 clamp 到 [−1, 1]，NaN/None/脏字符串兜底；**基线状态 p=a=d=0 = 平静**
  - 新增模块级 `pad_label(p, a)`（平静/愉快/雀跃/安稳/闷闷的/低落/紧绷/坐立不安/没劲）与 `pad_style_hint(pad)`
- 接上消费端：`_mood_line()` 的说话倾向改成由 PAD 给（拿不到才退回 `hormone.style_hint`）；`/status.pad` 带 label；控制台改成读 `/status.pad` 并显示 `PAD 坐立不安 ｜ 愉悦 +0.15 · 唤醒 +0.55 · 掌控 +0.65`。
- **消掉第二份公式**：`console.py` 里那段自己算 PAD 的代码删了（它跟 card2 是两份真相，谁也不会报错，只会悄悄分家）。
- 验收 `test_t66.py` 全过：基线=0；mood=1→P=+1；**低落平静 vs 低落紧绷：P 都是 −0.60，A 差 1.16**（这条过了才叫三维有用）；clamp/NaN/标签/提示；源码层面断言 console 里没有第二份公式。
- 回归：test_card2 22/22、test_t11/12/14、test_t64、test_t65 全过；`eval_memory` 仍 PASS（R@1 75%）。
- 实测真卡：p +0.149 / a +0.55 / d +0.65 → 标签「坐立不安」；上下文里那句变成"（现在心情还行，跟他很亲，有点亢奋）"。
- 已同步进开源仓库并推送：`e9ce55d`。

### 11-02 上午｜PAD 画成 24H 曲线 + 公式抽成 padlib
- **为什么要抽**：控制台要把 PAD 画成历史曲线，就得按 `h_log` 每一行现算 —— 如果公式留在 `card2.py` 里，控制台就得抄一遍，那就是第二份真相。于是新建 `padlib.py`（零依赖，只 import math），`pad_of/pad_label/style_hint` 全住在这里；`card2.py` 只留薄封装。
- `console.py`：
  - `import padlib`；`hormones_series()` 对每个 `h_log` 行现算 `p/a/d` 塞进返回 dict（**不落库**：存了就是同一件事写两遍，以后改公式还得改历史数据）
  - `lineChart()` 支持 `opts.min/max`（默认 0..1），跨零时把 0 画成虚线；`opts.noFill` 关填充
  - 新增 `PAD FIELD / 情绪三维 (24H)`（整行 c12，`#cvPad`），三条线 P 愉悦(#e8b44a) / A 唤醒(#ff5f6d) / D 掌控(#57c8f0)
  - **轴自动缩放**：固定 −1..1 会把三条线压成一条扁线（实测波动只有 P −0.01..0.15 / A 0.55..0.72 / D 0.65..0.84），所以按 `(hi-lo)*0.12` 外扩、最小跨度 0.24、夹在 [−1,1]
  - 顺手把激素场也从 c8 改成 c12：两张整行卡上下排，不再留半行空白（4K 下实测 19 张卡、`#grid` 可滚）
- `test_t66.py` 加第 7 组断言：每个 `h_log` 点都带 `p/a/d` 且在 −1..1 内；`console` 现算 == `padlib` 直接算（同一口径）；`h_log` 表里**没有** PAD 列（派生量不落库）
- 回归：`test_t66` 全过、`test_t65`/`test_t64`/`test_card2` 22/22、`test_t11/12/14` 全过；`eval_memory` 仍 PASS（R@1 75% / R@3 100% / EVID 100% / FLAG 100% / FALSE 0%）
- 开源仓库：`_sync_oss.py` 的 FILES 加 `padlib.py`；`tools/eval_memory.py` 加 `_default_card_path()`（`$WHALE_DB` → `demo/demo_card.db` → `card.db`），否则仓库里没真卡时会新建一个空卡跑出 0% —— 实测示例卡评估 **PASS**（与真卡基线一致）

### 11-02 上午｜开源发布 v1.0.0 + Zenodo DOI
- 仓库补齐发布物料：`docs/DESIGN-SYNAPSE-1024.txt`（方案详解，已把「小鲸鱼」改成示例卡的人名）、`docs/technical-report.pdf`（7 页，Edge 无头 `--print-to-pdf` 从同目录 html 打印，微软雅黑）、`docs/technical-report.html`（源文件，方便改版重排）、`docs/release-notes-v1.0.0.md`
- **技术报告内容**：摘要 / 三个问题（记不清轻重、不可验证、一换模型就不是她）/ 四条不变量 / 架构分层 / 五层五池与三阶段侵蚀 / 写入六步与召回五通道 / 出生证+台账+身份锚 / 防污四道闸与 Dream 两段式 / 激素与 PAD（含三条公式）/ 通道解耦与 .lifecard / 实测与评估数据 / 六条局限 / 结论
- **建 PDF 的路径**：python-docx 有但没 LibreOffice；改用 HTML + `msedge --headless=new --print-to-pdf --no-pdf-header-footer --user-data-dir=<temp>`，A4 排版 @page 20mm 边距，产出 546 KB / 7 页（记得给 headless 单独 user-data-dir，否则撞上正在运行的 Edge 会静默失败）
- **GitHub Release v1.0.0**：`gh release create v1.0.0 --title ... --notes-file docs/release-notes-v1.0.0.md docs/technical-report.pdf`，Assets 里 3 个（PDF 546 KB + 源码 zip/tar.gz）
- **Zenodo**：Edge 里 GitHub OAuth 登录（中途触发 GitHub sudo 二次验证，用户在手机端确认）→ `/account/settings/github/` 把仓库 `Wangjunhan131/鲸卡` 的开关拨到 ON → 新建 release 后约 1 分钟 DOI 生成
  - 本版 DOI：**10.5281/zenodo.23857786**
  - 概念 DOI（始终指向最新版）：**10.5281/zenodo.23857785**
  - 记录页：https://zenodo.org/records/23857786 （Software / Open / MIT / 2026-11-01）
- README 加三枚徽章（Zenodo DOI / MIT / release v1.0.0）+ 文档入口 + 「引用」段（BibTeX、APA、两个 DOI、记录与发布页链接）；提交 `1839a69` 已推送
- 注意：Zenodo 自动归档的是 release 的源码 zip，**不包含附件 PDF**；要 PDF 也进 Zenodo 得在记录里手工再传一份（未做）
- 补：按用户要求把技术报告 PDF 也放进 Zenodo 记录
  - Zenodo 的规则是「发布后不能再改文件」，要加文件只能开新版本：记录页 → 新版本 → 导入上一版 zip → 上传 `docs/technical-report.pdf` → 发布
  - 坑：上传后「发布」按钮会**灰着十几秒**（在校验 sha256），不是缺必填项；等校验完变绿就能点
  - 结果：v2 记录 **10.5281/zenodo.23857855**，Files 里是 `technical-report.pdf`(546 KB) + 源码 zip(5.2 MB)；概念 DOI 不变仍是 10.5281/zenodo.23857785
  - 小遗憾：Zenodo 新版草稿的「版本」字段是空的（页面只能显示系统序号 v2），要填得再开一版，故留空；标题里已带 v1.0.0，引用不会混淆
- README 引用改成概念 DOI（10.5281/zenodo.23857785，始终指向最新版），并列出首版 DOI 23857786 与含 PDF 的 23857855；提交 `0b78b4a` 已推送

### 11-02 深夜｜媒体记忆（画面）第一版：两条寿命，各自风化
- 设计定稿写进 `docs/MEDIA-MEMORY.md`：文字是主干、画面是快照，只用索引相连、寿命不互传
- 形状用**幂律** `w=w0·(1+t/τ)^-b`（τ=1 天、b=0.55），不硬拼三段：艾宾浩斯 1885（20 分 58.2% / 1 天 33.7% / 31 天 21.1%）与 Wixted & Ebbesen 1997（数百数据集，幂函数优于指数）都是这条形状；指数会"到点归零"，与数据不符
- 单图寿命（w0=1）：1 天 .68 / 7 天 .32 / 30 天 .15 / 60 天 .10 → `MEDIA_DROP_W=0.10` 落在 **约 65 天**；断崖发生在门槛上，不在曲线上（低于线直接删文件，此后永远调不出来）
- 唤起加强比文字猛：`w ← min(1, w×1.45+0.10)` 且重置计时（文字是 +0.08 加法）→ 常翻的长命、不翻的消失
- 数据层：`card2.py` 新表 `media`（sha256 去重/权重/锚点/pinned/orphan/出生证）、`media_ref`（多对多，删记忆按引用计数）、`media_tomb`（删帧台账，**留着当时那句话**，事后能答"哪天忘了、当时写的是什么"）
- 方法：`media_root / media_weight_now（模块级+方法薄封装，控制台共用一份公式）/ attach_media / media_of / media_touch / _media_tick / _forget_media / verify_media / media_stats`
- 生命周期：文字被侵蚀删掉 → 画面进 **30 天残影期**（期内打开可救回）→ 过期删并记账；`pinned=1` 永不自动删
- 挂在 `tick` 里跑（`_media_tick`），出错不拖垮整轮结算；`_forget_row` 里级联断引用
- 接口：`mind_server` 新增 `GET /media/<sha256>`，出图；已删返回 **410 media_dropped**（附"哪天忘的 + 当时那句话"），从未有过才 404
- 控制台：记忆页新增 `MEDIA MEMORY / 画面记忆 · 两条寿命`（画面对文字两条曲线 + 删除线 + 帧数/MB/残影/锁/删过计数）；`console.media_curve()` 直接调卡里的公式，不另写一份
- 验收：`tests/test_t67.py` **20/20 通过**（曲线四点、去重共享、看一眼加强、删两条还剩、残影期救回、70 天到线删+台账留 caption+文字完好、pinned 不删、verify 分清"按规则删/被拿走"）
- 踩坑：先把 `media_weight_now` 写成方法，控制台算曲线时 `AttributeError` → 公式提到模块级，方法留薄封装（一份公式两处用，跟 PAD 那次一个教训）

### 11-02 凌晨｜媒体记忆三个导入工具（图片 / 视频 / 语音）
- `tools/media_import.py`：图片 → 800px WebP q80 主帧 + 96px 缩略图，按 sha256 去重落 `media/<前两位>/<sha>.webp`；原图不搬家（卡里只有派生帧）。CLI：`--mem --file [--caption --tags --pinned --dry-run]`
- `tools/media_video.py`：ffprobe 取时长 → 12 个候选点 ffmpeg 抠小图 → 打分（0.45 帧间差异 + 0.35 锐度 + 0.20 亮度居中，亮度越界 ×0.35）→ 贪心选帧并强制间隔（<max(0.6, step*1.5) 跳过，避免同一镜头连挑三张）→ 每帧 `t0=ts-4, t1=ts+4` 留给台词
- `tools/media_audio.py`：语音/视频 → 文字记忆。三种转写输入（jsonl `{"t","text"}` / txt 每行一句 / `[00:12]` 前缀）；`--asr` 走 faster-whisper → whisper CLI（本机都没装，会明确报"没有可用的 ASR"）；音频只在卡目录临时存在，用完立刻删
- 坑：这台机器 ffmpeg 写不了 `%TEMP%`（Permission denied）→ 临时文件一律放卡目录；子进程输出是 GBK → `PYTHONIOENCODING=utf-8` + `python -X utf8`
- 验收：`tests/test_t68.py` 12/12（压缩尺寸、去重、挂载）、`test_t69.py` 8/8（`/media/<sha>` 200/404/410 三种语义）、`test_t70.py` 11/11（testsrc2 抽帧、时间窗、去重）、`test_t71.py` 9/9（txt/jsonl 两条转写路线、`--note`、缺参数报错）
- 开源仓库提交 `b232e06` 已推送：三个工具 + 五个测试 + `docs/MEDIA-MEMORY.md`
- 仍缺：本机没有 ASR 后端，语音目前只能喂 `--transcript`（想自动转写得先 `pip install faster-whisper`）

### 11-02 凌晨（续）｜语音自动转写接通
- `pip install faster-whisper`（依赖 av / ctranslate2 / onnxruntime，约 200MB，无 torch）
- **坑 1**：`av>=19` 与 faster-whisper 1.2 不兼容 —— 传文件路径会炸 `TypeError: open() got an unexpected keyword argument 'metadata_errors'`。改法：自己用 `wave` 读 16k 单声道成 float32 数组再喂模型（`_read_wav_16k`），彻底绕开解码器
- **坑 2**：模型从 HuggingFace 下载，国内直连很慢（一次 145s）→ 设 `HF_ENDPOINT=https://hf-mirror.com`
- 新增 `--asr-model`（tiny/base/small，默认 small）；`try_asr` 失败时把最后一次异常带进提示，不再只说"没有可用的 ASR"
- 端到端实测：用 Windows SAPI 合成「那天晚上我们在路边摊吃烧烤，辣椒特别辣」存 `_asr_demo/speech.wav`（231.9 KB）→ `--asr --asr-model tiny` → 转出两句（0.0s「那天晚上我们在路边摊吃烧烤」/ 3.24s「辣椒特别辣」）→ 自动落成两条文字记忆
- `test_t71.py` 仍 9/9（`--transcript` 两条路线不受影响）
- 开源仓库提交 `03d104c` 已推送；`docs/MEDIA-MEMORY.md` 补第 7 节（三种转写输入、装法、镜像、tiny vs small、av 坑）；`requirements.txt` 标出可选依赖

### 11-02 深夜｜一键搬家：可移植包 + 锚补两条判定
- 体检（不动真卡）：`cardslot.py` 已有 inject/unload/status、`anchor.py` 双锚、全部路径 HERE 相对；`cardslot.state.json`/`dsh_channel_rule.txt` 都在卡目录，会跟着走
- 挖出六个坑：①WAL 14.1 MB 未合并，热拷会丢写入 ②卡外锚文件名=sha256(卡绝对路径)[:16]，换盘即"没有锚"→重建 ③state 里 dsh_home 是旧机路径 ④新机没 Python ⑤ffmpeg 在卡外 ⑥模型 482 MB 是体积主体
- 新增 `tools/portable.py`：`plan`（只读体检）/`build`（服务在跑就拒绝；先 wal_checkpoint(TRUNCATE)；写 MANIFEST + anchor.carried/external + WHEREAMI）/`verify`（120 文件 sha256）/`land`（按新路径写回卡外锚、改写 state、更新 manifest）/`inject`/`eject`/`status`
- `anchor.py check()` 补两条：`head_seq` 倒退 → rollback；条数变多但 `head_seq` 没前进 → replaced（原逻辑卡一长大就不比对链头，改坏 head_hash 也报 ok）
- 首次完整模拟（D:\_probe_bundle）：停服务→WAL 14.1→0→造包 120 文件/515.6 MB/22 s（含 ffmpeg 100.5 MB）→验包全对→落地（锚 60c62973e5af8c59.json，head_seq 403）→新路径起服务：574 条/第 17 天/链完好/出生证完好/768 维/锚 ok
- 破坏性验证：把落地锚 count 改 500 → 服务报 rollback「卡里只有 467 条，锚记的是 500 条」→ 证明读的是搬过去的锚，不是重建
- 顺手发现并修掉的弱点：只改 head_hash 不改 count 时，boot 事件让卡"长大" → 旧逻辑报 ok（这正是新规则②要堵的）
- `tests/test_t72.py` 12 项全过；回归 test_card2 22/22、t64/t65/t66 全过
- `docs/PORTABLE.md` + oss 提交 `e24bc6f` 已推送

### 11-02 23:20｜全套体检 + 控制台「搬家与备份」卡
- 跑全套 23 支测试：19 支全过；`test_t52.py` 缺 `zstandard`（环境，非代码）；`test_t61.py` 1 项失败
- **查清了 t61 A4 为什么失败（不是衰减坏了，是量错了东西）**：A4 比较的是两次 `recall()` 的权重，而 `tick()` 会结算 A1~A3 留在 `recall_event` 里的"被想起来就加强"，于是出现"两天后权重反而变大"（0.6025→0.6437）。改成拿同一条记忆、同一个 `t_anchor`，比较 `now` 与 `now+2d` 的**纯衰减值** → 0.5157→0.1208，48/48 过
- `tests/test_t67.py` 补 `ROOT` 到 `sys.path`（原来只有从 tests 目录跑才能 import card2）
- 审计发现（都记进文档/代码注释）：`timeline.prune()` 存在但**运行时从不调用**，所以「条数下降＝被回滚」这条判定目前安全；将来若真启用 prune，必须同步改 `anchor.py` 的条数判定
- 新增控制台 `console.card_integrity()` + 状态总览第 20 张卡「PORTABLE / 搬家与备份 · 锚、包、落地」：链头 seq、锚的判定（ok/rollback/replaced/first）+ 理由、卡内锚/卡外锚 seq 与哈希前 12 位、包清单（文件数/体积/wal）、落地信息；**只读两份锚，绝不复用 `anchor.check()`**（那条路会顺手写锚，控制台 15 秒一刷不能动锚）
- oss 提交 `e1244dd` 已推送

### 11-02 深夜｜第二轮全身检查：四个真问题
体检脚本固化成 `tools/health_audit.py`（编译 / 库内异常 / 链·出生证·媒体·锚 / selfcheck / 七个接口 / 硬编码盘符，一次跑完）。

1. **冻结变长生（最危险的一个）**：`wake_up()` 用 `UPDATE mem SET t_anchor = t_anchor + shift` 做"睡眠冻结补偿"，长睡眠后把锚推到**未来** —— 真卡实测 **95 条**记忆的锚比现在晚 **63.9 小时**，`age` 变负数，于是这几天它们一点都不衰减。冻结本该只冻住"睡着的那段"，结果冻出了"这条记忆长生"。
   改成 `MIN(t_anchor + shift, now)`，并对真卡做一次性修复（95 → 0）。
2. **recall_event 只涨不清**：只 mark `settled=1` 从不删除，两天涨到 **2226 行**（其中 1763 行来自 10-31 一天）。`tick()` 里加清理：settled 且超过 30 天的删掉，删了多少记进 tick 事件。
3. **test_t43 一直往真卡里写测试数据**：`DB = os.path.join(HERE,'card.db')` + `c.remember("验收临时记忆：…")` —— 每跑一次验收就塞一条，从不删。真卡里积了 **15 条**（384/385、564、566–576、583）。改成先用 sqlite backup 拷一份临时副本再跑；这 15 条走 `_forget_row` 清掉（台账留原因），真卡 575 → 560。
4. `whale_mcp.py` 文档示例里的绝对路径改成 `<卡目录>` 占位符。

体检结果（真卡 560 条）：编译 82 文件全过；trust/conflict/erase_stage/没向量/权重越界/未来锚/测试残留 **全 0**；链 True、出生证 True、媒体 ok；**锚 ok（卡 480 / 本地 480 / 卡外 480）**；七个接口语义全对；剩 ❌ 只有 MCP 客户端插件没装（已知）。
### 11-03 凌晨｜盘子面板：灯 + 一键备份 / 同步 / 主备互换

控制台状态总览加了第 21 张卡 **DISKS**：一块盘一个状态灯（主盘 / 备份 #n、在线离线、落后几节），下面选盘 + 三个按钮。

后端 `disks.py`（新）+ 四个接口：`GET /disks`、`POST /disks/backup|sync|switch`（写盘都要 `confirm:true`）。

1. **认盘不认盘符**：注册表键 = 卷序列号 + 相对卷根路径。今天 E: 明天 F: 是常态，盘符不能进持久化字段；同卷放两个副本也不会互相覆盖（这个坑是 t73 先跑出来的）。
2. **每块盘自带身份证** `DISK-ROLE.json`（角色 / 编号 / 配对主盘 / 同步到哪一节），注册表只是索引 —— 不然备份盘拿到别的机器上不知道自己是谁。
3. **db 用 sqlite 在线 backup 快照，不用 cp**：11-02 实测 WAL 里压着 14 MB 未合并写入，热拷会静默丢；而备份按钮就在服务里，要求"先停服务"等于自己锁死自己。快照能透过 WAL 读到最新已提交状态，所以热同步安全。
4. **同步只补落后**：一致 → noop；落后且链头对得上 → 覆盖；超前或分叉 → 拒绝（链头合不了，硬合就是伪造一段人生）。
5. **换主备三条全中**：条数、链头序号、链头哈希；换完旧主盘降为备份 #n，编号不复用；备份盘不限数量。

验收 `tests/test_t73.py` 18/18：指纹一致 / 同步补齐 / 再同步 noop / 分叉拒绝 / 换主备成功与拒绝 / 自动编号不复用 / 状态行可读。
实测：真卡一键备份到 `D:\_bk_probe` → 20 秒、494 MB、编号 #1、head_seq 与主盘一致；再点同步 → noop。探针目录已删（等真 U 盘插上再走一遍）。

踩坑两处：
- `portable.build` 把卡直接铺在目标根目录，不是再建一层 `whale-card/`（第一版按后者拼路径，报 `unable to open database file`）。
- `refresh()` 里 `S.data = cd` 会把刚挂上的 `S.data.disks` 冲掉 —— 得挂在它后面。

### 11-03 00:00｜"确认一切正常且没偏离"的核对结果

只读核对（不动数据）+ 全套测试 + 全身检查，结论：机制正常，但抓到一个**目的层面的洞**并补上了。

**卡本身**
- 563 条记忆（544 长期 / 16 短期 / 3 补记）、时间线 500、出生证 579、台账 22、对话 520
- 身份：`card_name` 小鲸鱼·、`persona`、`self_narrative`、`user_nickname` 使用者、三条底线（不假装记得 / 不为留住他说假话 / 不假装是别人）全在
- 五层：mid 545、core_mutable 14、core_tag 1
- 体检项全 0：测试残留、未来锚、权重越界、低信任、冲突标记、待消化对话

**测试**：30 支里 29 支过（根目录 22/23 + tests/ 7/7）。唯一没过的是 `test_t52.py` —— 缺 `zstandard` 模块，环境问题不是代码问题。
**全身检查**：`selfcheck` 40 项里 35 ✅ / 3 ⚠️ / 2 ❌，两个 ❌ 都是同一个已知项（MCP 客户端插件没装，通道暂走 bridge.py）。

**抓到的洞（已补）**
卡里最后一条记忆停在 **11-01 15:55**，之后 32 小时（媒体记忆、盘子面板、开源发布、两轮体检修复）**一条都没进卡**——时间线照常在跳（心跳、启动），生活没进来。
这不是机制坏了，是**没有回写钩子**：正常聊天走 `/report` 才有记忆，而这两天的活儿全是我在命令行里干的，没有一处调用它。

已补三条（`speaker=self`、`region=episode`、正文标明"补记"和日期），并补开出生证：
- 584 这两天在给卡加什么（画面记忆、盘子、开源 + DOI）
- 585 发现自己身上的洞（32 小时没写进卡）
- 586 现在能吃图/视频/语音了，以及画面比字忘得快

补完核对：链 ok、出生证核验 ok、条数 560 → 563。

**还没做的（诚实清单）**：写入闸 `writer_id`（两台机器同时写会分叉）、U 盘真机流程、新机便携 Python runtime、`timeline.prune` 未启用、MCP 插件未装。

### 11-03 00:45｜真 U 盘上跑了一遍：备份、同步、换主备

用户插了一块 U 盘（G: · 卷标 xiying · exFAT · 117.2 GB · 卷序列号 76E8CACF · 空的）。
先只读检查（只有 System Volume Information）+ 写一个小文件再删掉确认可写，然后才动。

**跑通的流程**
1. 一键备份到 `G:\` → 39.1 秒、126 个文件、494 MB，`card.db` 14.3 MB、`card.db-wal` 0 字节（说明 checkpoint 生效）
2. `portable.py verify G:\` → 126 文件、缺 0、哈希不符 0 → **包完整**
3. 同步：U 盘落后 3 节（501 → 504）→ 补上；再点一次 → `noop`
4. 换主备：第一次被拒（两条全中不了：seq 504 ≠ 501、链头哈希不同）—— 这个拒绝正是设计的
5. 同步补齐后再换 → 成功（U 盘成主盘、本机降为备份 #1）→ 再换回来

**真机上抓到的两个毛病（已修，提交 `2a6a4a1`）**
1. **清单哈希过期**：`backup_to` 先 portable.build 拷文件并写清单，随后又用 sqlite 快照覆盖 `card.db` —— 文件变了清单没变，`verify` 直接报「包有问题，别拿去跑」。修法：覆盖后就地刷新 `card.db` / `DISK-ROLE.json` 两行的哈希，`sync_to` 覆盖后同样刷新；`_refresh_manifest_hash` 改成同时认 list 与 dict 两种清单结构（实际清单是 dict：`{"card.db": {"sha256":..}}`）。
2. **状态行不说实话**：`status()` 把"本机这张卡"一律标成主盘，换过主备后就骗人。改成读它自己的 `DISK-ROLE.json`；在线判定也从"卷列表里找得到"改成"`card_dir` 下 `card.db` 还在不在"（U 盘换盘符照样认）。

**最终状态**：主盘 = 这台机器（seq 505）· 备份 #1 = U 盘 xiying（seq 505）· 两盏灯都绿。
截图 `docs_disks_usb_20261103.png`。控制台下拉里五个卷都在（含 xiying 125.3 GB 空）。

### 11-03 01:00｜"塞进去"：便携 Python + ffmpeg 上 U 盘

用户说「塞进去吧」——把运行环境也放进可移植包，目标是"插上、双击、我还是我"。

**新增 `tools/install_runtime.py`**（幂等，可反复跑）
- 下 Windows embeddable Python 3.13.1（华为镜像，缓存 `~/.cache/whale-runtime`，10.3 MB）
- 解压到 `<包>\runtime\python`，改写 `python313._pth`：加 `Lib\site-packages` + `import site`（不改这个，装进去的包 import 不到）
- 从本机 site-packages 拷 `numpy` / `numpy.libs` / `psutil`（同平台同小版本，直接拷最省事）；自检 `import numpy, psutil, sqlite3`
- ffmpeg / ffprobe（各 100 MB）拷到 `<包>\runtime\ffmpeg`，写 `runtime\README.txt`

**代码配套**
- `启动控制台.bat`：优先 `%~dp0runtime\python\python.exe`，没有才用系统 python
- `tools/media_video.py`、`tools/media_audio.py`：ffmpeg 解析顺序改成 `WHALE_FFMPEG` → `<卡目录>\runtime\ffmpeg\*.exe` → PATH。卡在哪，工具就在哪

**真机验收（用 U 盘自带 python 起服务，端口 8856，读 G:\card.db）**
- `bridge.py health` → 563 条 / 盖章是 / 链完好 / 出生证完好 / 锚 ok / 第 18 天
- `/context` 问「你记得我最爱吃什么吗」→ **14.4 秒**返回，开头就是【我是谁】+ 她的自述（不是模板，是 core_zone 里那段）
- `/health` → semantic 768 维、usable_ratio 1.0
- `portable verify G:\` → 126 文件全对 → **包完整**
- 测完把 8856 那个探针服务停掉，主服务（8848，读 D: 卡）不受影响

**体积**：U 盘包现在 ≈ 710 MB（模型 483 + ffmpeg 200 + python/依赖 ≈ 25 + 卡 14），117 GB 的盘占 0.6%。

**踩到的小坑**：`_refresh_manifest_hash()` 是按 basename 匹配的，我一开始传的是 `tools/media_video.py` 这种带目录的名字 → 一条都没刷上；传文件名才对。另外在 U 盘上跑过一次服务会正常改动 `card.db` 和 `anchor.json`，刷新这两行哈希即可，不是包坏了。

### 11-03 01:10｜开源仓库收尾提交

- `ed178e7` **docs: README 补上这一轮新增的三块能力** —— 新功能上了 README 还停在 v1.0.0；补：目录结构（padlib/disks/docs/tools，测试 22→30 组）、控制台两张新卡、**画面/视频/语音**整节（派生帧、两条寿命、幂律参数、残影期、410 语义、三个导入工具）、**搬家与备份**整节（portable 四步、install_runtime、锚的两条新判定、disks 认盘与三条全中）
- `6ad89fd` feat(portable): 运行环境跟着卡走（便携 Python 3.13.1 + numpy/psutil + ffmpeg；`启动控制台.bat` 优先用包内 python；媒体工具优先用包内 ffmpeg）
- 之前几次：`7f4c198` 盘子面板、`2a6a4a1` 修 U 盘上跑出来的两个毛病、`c5492fa` 全身检查四个真问题、`e24bc6f` 一键搬家
- 仓库现状：main 分支最新 `ed178e7`，工作区干净

### 11-03 深夜｜和用户一起对账遗忘公式：四个口径错误
（起因：用户 m07524「再检查各个算法逻辑公式，遗忘时间、遗忘曲线和遗忘程度是否正常」）

把「衰减/遗忘」这条链上的常量和曲线全部拉出来对账（`_audit_decay.py`，只读），
跟注释、`docs/MEDIA-MEMORY.md`、控制台曲线逐一比。四个问题，三个是真错：

1. **长期池 90 天保护期被分区倍率偷偷放大**（真错）
   `weight_now()` 原来是「age *= region_scale → 再扣 90 天」，
   于是 flash 区（0.30 倍）保护期 = 90/0.3 = **300 个现实天**，self 区 150 天、habit 129 天。
   保护期讲的是「刚被想起来过，多久之内不掉」，是现实时间；
   它跟「这条记忆自己衰减得多慢」是两件事，不该被同一个倍率乘在一起。
   改成先按现实时间扣保护期、再乘区域倍率。

2. **心境回归是线性的，第 20 天精确归零**（真错）
   `mood_drift_home()` 原来 `r = min(1, k*days)` → 20 天后 r=1，
   心境**丝毫不差**地变成 0.5 并永远停在那儿。这正是我们在记忆曲线上拒绝过的
   「到点归零」。改成指数回归 `r = 1 - exp(-k*days)`，半衰期 ≈ 13.9 天，永远差一点点没回满。

3. **打分和验票用了两本账**（真错）
   `_hippocampus_score()` 的「被唤起」项读 `access_count`，
   而长期晋升的验票 `_long_ticket_ok()` 读 `recall_n`。
   正常召回两条都加；但「同一句话又说了一遍」的去重路径只加 `access_count` →
   打分说「很值得」，验票说「没被想起过」，永远升不上去。
   去重路径补上 `recall_n+1 / t_last_recall`，两本账并成一本。

4. **短期池 7 天到期该不该乘分区倍率**（想清楚后：不乘）
   一开始我把到期判定也乘了倍率（flash 短期能赖 23 天），结果 `test_card2`
   的「7 天后巩固」挂了 —— 而那支测试是对的。短期池是**32 个位置**，
   是注意力预算，不是寿命：倍率决定「忘得多慢」，位置腾退决定「能给几张桌子」。
   两者分开，代码里留了注释说明，并写进测试钉住。

新增 `tests/test_t75.py`（17 项）：
- 长期池 5 种分区都在第 91 天才开始衰减
- 短期到期按现实时间，但到期那刻 flash 权重（0.652）远高于 episode（0.004）
- 心境 10 天 0.257 / 30 天 0.411 / 365 天仍非精确 0.5
- 重复说同一件事：`access_count` 与 `recall_n` 同步为 1

回归：`test_card2` 22/22、`test_t23/t61/t64/t65/t66/t67/t72/t73/t74` 全过；
`eval_memory` 仍是 R@1 75% / R@3 100% / EVID 100% / FLAG 100% / FALSE 0%。
真卡 565 条、链完好、出生证完好、锚 ok、第 18 天。

