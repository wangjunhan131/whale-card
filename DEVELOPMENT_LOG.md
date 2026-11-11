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

## 📅 2026-11-05 傍晚｜把「上下文组合」露出来 + 修四个真 bug

> 用户 m15228：「完善一下我们的方案吧，修一修bug把现有的bug和算法全部优化好，
> 争取以后不要再出bug了，以及就是我们不是组合上下文吗？那应该有许多可以调的参数，
> 还有几种不同的模式才对啊，为什么控制台里没有呢？给他加到控制台里加一栏怎么样？」

**新增文件/模块**

- `tools/fix_sysmark.py` —— 修已存记忆里的工单壳污染（只重算 raw/core/detail/vec/emb）。
- `tests/test_t86.py`（11 项）—— 召回结算按天去重。
- `tests/test_t87.py`（23 项）—— 工单壳剥离 + 转述句拆解。
- `tests/test_t88.py`（19 项）—— 情绪弱化词分级。

**修改文件/关键函数**

- `ctxweight.py`：`BUDGET_DEFAULT=6000` / `BUDGET_MIN=1000` / `BUDGET_MAX=60000`；
  `_parse_num()`；`load_config()` 认第四种键 `BUDGET`；`effective_budget()`（显式传 > 卡里配 > 默认）；
  `compute()` 的 budget 默认改 `None`、返参加 `total` 与 `quota`；新增 `preview()`
  （拷配置套 overrides 再算，不写库、不读记忆、不动 turn_count）。
- `card2.py`：`assemble_context(budget=None)`；返回的 `"budget"` 改报 `plan["total"]`（真用了多少）；
  `ACCESS_DIMINISH_CAP=20`；`_settle_recall_events()` 按天分组、一天只加固一次；
  `strip_sysmark()` + `remember()` 入口调用；`split_predicate()` 引号优先 + 动词按位置选；
  `_RE_EMO_WEAK` + `_is_weak_emo()`，`is_chatter()` / `classify_region()` 接上。
- `mind_server.py`：`GET /config` 扩到十个字段（含 limits/defaults/cells/scenes）；
  `POST /config` 支持 `{key, reset}`、标量挡范围、不认识的键 400；新增 `POST /ctx_preview`、
  `POST /config_all`；`/remember` 支持 `pool`（`short/medium/long`，非法 400 `bad_pool`）。
- `timeline.py`：新增 `restamp_birth(mid, reason)` —— 有序修复原文后重开出生证，
  **reason 为空直接拒绝**（"没有理由的原文改动就是在抹证据"），并写一条 `mem_fix` 进时间线。
- `console.py`：加「上下文」页（四张卡：LIVE 四格 / GATES 三个闸门 / MODES 七种模式 × 六维 /
  TRY 拿一句话试算）；`refresh()` 多拉一条 `/config`；有未保存改动时不重绘、切页会问一句。

**完成了什么**

- 控制台「上下文」页落地：七个场景 × 六维权重的表格可直接改、三个闸门（总预算/每格下限/
  A 保底）可调、拿一句话试算不必真说话。`node --check` 过，服务已重启。
- 四个真 bug 全修，**回归 12 个测试文件全绿**（`test_card2` 22/22、`test_t33`、`test_t66`、
  `test_t73` 28/28、`test_t76` 42/42、`test_t81`、`test_t83`、`test_t84` 30/30、
  `test_t85` 24/24、`test_t86` 11/11、`test_t87` 23/23、`test_t88` 19/19）。
- 体检：640 条 / 链完好 / 出生证完好 / 锚 ok / 第 20 天；`usable_ratio` 0.9891 → **1.0**。

**四个 bug 的病根**

1. **召回把权重钉死**。`_settle_recall_events()` 里 `for _ in lst:` 每个事件都加一次
   `GAIN_ACCESS=0.08`，而衰减半衰期 4.46 天每天只扣 0.144 —— 每天被想起 2 次就永久顶到
   0.856，真卡实测 167 个「同日召回 ≥2 次」组合、71/623 条 w≥0.95，被钉住的全是
   #201/#202/#209 那种对话碎片。**越没用的越容易被捞出、越捞越结实。**
   修法：按 `mem_id` 分组后再按天分组，**一天只结算一次增益**；`access_count` 仍按事件数累加
   （那是"被想起过几次"的账，海马分和升长期门票读它）；增益分母加 `ACCESS_DIMINISH_CAP=20`
   的上界，否则天天被想起一年后增益只剩 0.0015，比不上一夜衰减。
   实测：同日 3 次 w=0.5800（三次全算会是 0.6920）；天天被想起一年 w=0.8674。
2. **系统工单壳混进记忆**。7 条 raw 以 `[dsh-mnemon:delegated write]` 开头、夹着
   `Execute this ... request now` 祈使句，`core` 全被拆成 `Executethissupervised-writebackreques`，
   **#298 还落在 self 区**。`strip_sysmark()` 剥壳（`_RE_EXEC_LEAD` 不能加 `$` 锚，工单有时整条
   压成一行），剥完没正文就返回 `""` → `remember()` 直接 `return None`（不写、不占去重窗口）。
   已存的 7 条用 `tools/fix_sysmark.py` 修回来（备份 → 重算 raw/core/detail/vec/emb → 重开出生证）。
3. **转述句被拆坏**。`split_predicate` 原来按"长度降序逐个 find、先命中谁算谁"，
   于是 `'他ID6F29AB在2026-11-05说过「我回来了」'` 被「来」抢了动词位，`core` 变成
   `('他ID6F29AB在2026-11-05说过「我回','来','」')`。修法三条：**引号优先**（转述壳不参与拆解）、
   **动词按出现位置选**（位置相同才比长度）、**单字动词+单字宾语视为撞进词里**（「教室」的「教」）。
   引号内容 ≤12 字直接整体当主干，不再往里拆。
4. **弱化情绪被当成强情绪**。用户原话是「除非有重要的…或者有很丰富的情绪」，而
   `is_chatter()` 只要命中情绪词就判"不是闲聊"，`remember()` 给的 emotion 又只有 1.0/0.25 两档，
   于是「我有点难过」拿到 1.0、一路落到 episode 区，一天后还剩 0.977 —— 跟「我们聊了一晚上」
   一个寿命。修法：`_RE_EMO_WEAK`（有点|稍微|略微|一点点|还算|还好|不太…），弱化的按轻微算，
   不进闪光灯、也不拦住闲聊判定。实测弱情绪一天后 0.1448 vs 强情绪 0.7704 = 5.3 倍。

**关键发现 / 教训**

- **出生证哈希锁的就是 `raw`**，所以任何一次有序修复都会让它对不上——这是它该干的事。
  但"不该悄悄改"不等于"不许改"：跟删除台账一个道理，**改可以，改了什么、为什么改必须留在链上**。
  所以 `restamp_birth()` 在 reason 为空时直接抛错。
- **`vec` 是字面 bigram 向量，`emb` 才是语义向量**，两回事。修原文时 `vec` 得按新正文重算，
  清空等于这条从此没有字面召回——第一版就是这么错的（空 vec 7 条），已补。
- **默认值也是决策**：`assemble_context(budget=TOKEN_BUDGET)` 让"总预算"这个最该露出来的参数
  反而最调不动，改成 `None` 之后才听卡里配置。
- 测召回加固必须显式 `region=R_FACT`：不带情绪的测试句会被判成 `R_CHATTER`（半衰期 8.77 小时），
  两天后就掉到 0.13，验的就不再是加固。

**什么没完成**

- 控制台「上下文」页的**浏览器实际渲染还没肉眼看过**（PAGE 是导入时求值常量，服务已重启）。
- 七问答复里提到的其余长期项：「不每句都回」进 `qq_guard`、写入时时间锚定、`/context` 加
  L0 时间线、检索补时间范围、拆句发送、`human_gap` 拉长、QQ 掉线自恢复/开机自启。

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


### 11-03 深夜｜习惯层 v1：先只挖，不注入
- 新 `habits.py`：`habits` 表结构（category/key/trigger/behavior/scope/subject_key/hits/days/first_seen/last_seen/confidence/status/weight/pinned/conflict/evidence/note）、门槛常量（候选 ≥3 次且跨 ≥2 天、稳定 ≥10 次且跨 ≥14 天、30 天不出现降权不删）、`segments()` 切段、`is_report_like()` 汇报味判定、`ngrams()`（先剔标点再切）、`head_ngram/tail_ngram`、`confidence_of()`、`status_of()`、`upsert()`、`list_habits()`
- 新 `tools/mine_habits.py`（**只读** `mode=ro`）：统计语言指纹（段数/段长/短段比例/句尾标点/emoji/段首段尾）、挖口头禅候选（命中、跨天数、用户是否也常说、区分度），输出 `docs/habits-report.md` + `docs/habits-candidates.json`
- 新 `tests/test_t76.py` **29/29**：切段、汇报味识别（代码块/列表/表格/✅）、n-gram 不带标点、段首段尾、置信度与状态机（钉住/冲突/衰减优先）、临时库 upsert 幂等与升级、evidence JSON 回读、只读挖掘不改库
- **真卡零写入**：`habits` 表不存在、mem 565、dialogue 520 不变；`test_card2.py` 22/22
- 挖出来的实话：我方 170 条回复里 146 条是开发汇报，只有 24 条像聊天；不过滤时 top 候选是「建文件夹(39)、D新建文(39)」这种噪声，过滤后才有「明天、我记、我这边、晚安、去睡吧」
- 结论：**v1 的机器是好的，语料不行**。真要挖出口癖，得喂纯聊天语料（QQ 那边 / 等日常聊出来），否则挖的是"我上班时怎么说话"
- 下一步（v2）：接 `/context` 的【我是谁】末尾最多 5 条 + 「自然用，别硬塞」，先要有人筛过的候选

### 11-03 深夜（二）｜习惯层 v1 完善：加"背景对比"，把开发词压下去
- 痛点：v1 只按"她说过几次"排序，结果 top 候选是「建文件夹(39)、D新建文(39)、新建文件(39)」——挖出来的是我上班时的口吻，不是说话习惯
- 做法（`habits.py` 新增三个纯函数，判据写死在代码里，免得以后拍脑袋）：
  - `rate_per_1k(hits, segments)`：每千段出现率，两边段数不同也能比
  - `lift_of(chat_hits, chat_segments, bg_hits, bg_segments)`：聊天出现率 ÷ 背景出现率；背景=被判定为"汇报味"的那批回复。背景为 0 且聊天有时给 99（有限大数，别让报告里出现 inf）
  - `suggest_of(distinct, lift, hits, days)` → keep / review / drop：区分度 <0.35 直接丢（那是你也在说）、天数 <2 待定、lift <1.2 丢（背景里也常见）、命中 ≥3 且 lift ≥2 才建议保留
  - 常量：`HAB_LIFT_KEEP=2.0`、`HAB_LIFT_DROP=1.2`、`HAB_DISTINCT_DROP=0.35`
- 顺带修一类噪声：`is_gram_junk(g)` 把"全是虚词拼起来的碎片"（的你 / 得我 / 了我 / 我不）剔掉——它们不是口癖，是 n-gram 切出来的渣
- `tools/mine_habits.py`：`phrase_candidates()` 改成返回 `(candidates, totals)`；候选多 `bg_hits / lift / suggest` 三个字段；报告改成三段表（建议保留 / 待定 / 建议丢弃），列里带"背景次数"和 lift，并写清 lift 是干什么用的
- 结果对比（同一份卡）：
  - 改前 45 条候选，前几名是「建文件夹/D新建文/新建文件」
  - 改后 34 条候选：**建议保留 23 / 待定 2 / 建议丢弃 9**；前几名变成「晚安(6, 97.6x)、去睡(3, 18.3x)、睡吧(3, 16.3x)、我记(6, 17.2x)、说过(3, 18.3x)、明天(8, 17.7x)、反正(3, 14.6x)」
  - 丢弃的那批：回来(5, 5.1x, 背景 48)、里面(3, 4.9x, 背景 30)、以后(3, 4.4x, 背景 33)、一句(4, 2.9x, 背景 68) —— 全是"背景里也常见"的词，正是要压掉的
- 测试：`tests/test_t76.py` 从 29 项加到 **41 项全过**（新增 lift 三种情形、rate 除零、suggest 四档边界、虚词碎片判定、候选字段齐全）
- 安全检查（很重要）：真卡 `habits` 表**仍然不存在**、mem 565 / dialogue 520 不变、`test_card2.py` 22/22 —— v1 全程只读，没有一行写进真卡
- 老实说还剩什么：真正像聊天的回复只有 24 条，样本撑不起"她一定这么说"；下一步要么导 QQ 聊天记录当语料，要么等日常多聊几周再挖。v2 才是把这批候选注入 `/context`（末尾 ≤5 条，带"自然用别硬塞"）

### 11-03 深夜（三）｜写入闸 writer_id：别再长出第二条链
- 缘起：盘子面板能备份、能换主备，但**两台机器同时跑**会各自往自己那条链上追加 —— 哈希链一分叉就再也合不回来，只能扔掉一半的"她"。这是我核对"有没有偏离最初目的"时唯一不可逆的风险
- 位置：`timeline.py` + `mind_server.py`
  - `timeline.writer_id()`：这台机器的稳定 ID（`~/.dsh/writer-id.txt`，格式 `w-xxxxxxxxxxxx`；环境变量 `WHALE_WRITER` 可覆盖，测试用）。**不从盘符推** —— 换盘不该换人设，但换电脑就该换手
  - `timeline` 表新增 `writer` 列（`ALTER TABLE ADD COLUMN`，老库自动补，默认空串）；**不进 self_hash** —— 老记录的哈希一个都不能动，所以这是保险丝不是防伪章：能发现事故，挡不住恶意改库
  - `Timeline.append(..., allow_foreign=False)`：链头是别人写的 → 抛 `ForeignWriter`，**拒写**。`allow_foreign=True` 留给人工迁移（明确知道自己在干什么时）
  - `mind_server` 启动时记 `self.writer`，比对链头：不是我就 `self.writer_lock = 链头 writer`，**跳过 catch_up 与 boot**，并把原因写进 `chain_bad`
  - `/report` 撞上锁 → 直接 `409 card_locked`，理由里带两个 writer；`/health` 的 `timeline.writer` 给出 `{mine, head, locked_by, readonly}`
  - 测试辅助：`WHALE_WRITER` 覆盖，不再需要伪造 writer 文件
- 验收：新增 `tests/test_t77.py` **14/14**（写入打标、链头 writer、加了列链仍合法、B 被拒且没多出记录、allow_foreign 可写、老卡空 writer 可被接管、writer_id 稳定且可覆盖）
- 回归：`test_card2.py` 22/22、`test_t64` 全过、`t72` 12、`t73` 19、`t74` 8、`t76` 41 全过
- 真卡实测：`/health` 里 `writer={"mine":"w-EXAMPLE00000","head":"w-EXAMPLE00000","locked_by":"","readonly":false}`，链完好、645 条事件、seq 646 两条 boot 都带 writer
- 老实说：拒写逻辑有单测；`/report` 返回 409 这条**只做了代码审查，没跑端到端**（要起临时服务、加载模型，太慢）——明天补一个带假卡的 E2E
- 另一个没做的：A｜冲突误判的人工解除（把 conflict 置 0、trust 抬回 1，记台账）。现在真卡冲突记忆 0 条，不急

### 11-03 深夜（四）｜防污误判可人工解除 + 写入闸端到端验证

**A｜`POST /mem/untrust`（能说一句"那是玩笑"）**
- 规则能看出"这句像在改写自我"，但看不出"是不是在开玩笑"。误判的代价是那条记忆永远进不了长期/底色、召回还打对折。所以补一条人工通道。
- `card2.py` 新增 `untrust(mid, reason=...)`：把原值（trust/conflict/why）写进 `history` 一笔（>8 条裁最早的），然后 `trust=1.0, conflict=0, conflict_why=''`。重复解除不重复记账；不存在返回 `no_such_mem`。
- 踩坑：第一版 SELECT 里带了 `pinned`，`mem` 表根本没这列 → `no such column: pinned`。测试先跑崩，把列删掉才对 —— **测试的价值就在这**。
- `mind_server.py` 新增 `POST /mem/untrust`（缺 id → 400 `no_id`；成功后 `timeline.append("untrust", ...)` 留痕，写链失败放进 `timeline_error` 不吞掉）。
- `tests/test_t78.py` 15 项全过：解除成功、正常记忆不动、原值回传、trust 回 1.0、conflict 清零、why 清空、history 一笔且含理由与原值、重复解除不记账、解除后具备晋升资格、从 `injection_report` 里消失。
- **默认不自动解**：只有人点了才会解。规则误判是可逆损失（点一下就回来），自动解会让"假装被灌输"变成一键洗白。

**B｜写入闸的端到端验证（`tools/e2e_lock.py`）**
- 做法：把真卡用 `sqlite3` backup 拷到临时目录 → 把链头 writer 改成 `w-othermachine` → 起 `mind_server.py --db <tmp> --port 8858` → 轮询 `/health` → 断言三件事。
- 实跑结果：`locked_by=w-othermachine readonly=True head=5a1e74c281f2bca2`；`POST /report → 409 {"ok": false, "error": "card_locked", "detail": "这张卡正被另一台机器用（w-othermachine），我这边只读"}`；被拒后时间线 647 条 / 记忆 567 条，与动手前完全一致。
- 踩坑：第三项断言一开始写成 `n_tl == head_seq`，但**删过记录之后行数和最大 seq 本来就不相等**（行 647、seq 648）→ 误报失败。改成"动手前取一次、动手后再取一次，前后相等"。教训：断言要拿同一口径的东西比。

**顺手修：`test_t52.py` 不再抢真卡的写锁**
- `test_t52.py:96` 的 `MemoryCard(old_card)` 会跑 `_migrate`（含 `UPDATE mem SET t_anchor=create_ts WHERE t_anchor=0`），服务开着时直接 `database is locked` —— 之前以为是"缺 zstandard"，其实两件事凑在一起了。
- 修法：先用 `sqlite3` backup 拷一份副本再开，跑完 `shutil.rmtree` 删副本（不然每跑一次多一个 `t52_real_*` 目录）。
- 结果：`T5-2/T5-3 验收：全部通过 (23 项)`，无残留目录；`zstandard` 确认已装（0.25.0）。

### 11-04 中午｜习惯层 v2：把稳定习惯接进【我是谁】（代码完成，等一句"放宽还是等"）

**做了什么**
- `card2.py`：新增 `habit_hints(limit=5, max_chars=220)`，从 `habits` 表只读地捞几条 `stable`/`pinned`，拼成短句挂到 `core_identity_text()` 末尾；段首写明"是参考，不是台词；没有合适的场合就别硬用"。
- 三条自我约束：①只取 stable/pinned，候选与衰减中的不进来（凭印象说不如不说）②最多 5 条、总字数 ≤220（密度刹车）③`WHALE_HABITS=0` 一键关；表不存在时返回空列表，不建表、不报错。
- `habits.py`：给 `status_of()` 加可选 `category`，`language` 类用另一套门槛（`STABLE_LANG_MIN_HITS=5`、`STABLE_LANG_MIN_DAYS=5`）—— 口头语本来就偶尔冒一句，用行为习惯的 10 次/14 天门槛等于永远进不来。不传 category 时行为完全不变。
- `upsert()` 内部也把 `category` 传进状态机（不传的话写进去永远是 candidate，我第一次就踩了这个：写完 25 条、注入 0 条）。
- `tools/mine_habits.py`：新增 `--write`（默认不写），把 keep/review 的候选 upsert 进表，note 里留 lift 与建议，可追溯。
- `tests/test_t79.py` 14 项全过：没表时返回空、最多 5 条、候选/衰减不进、钉住优先、内容是人话、字数截断、开关生效、人格锚里确实出现习惯段且不含候选词。回归 `test_card2.py` 22/22、`tests/test_t76.py` 41/41。

**真卡数据上的诚实结论**
- 副本上跑一遍：候选 34 条、写入 25 条，但**全部停在 candidate**，注入 0 条。
- 原因不是代码坏：我方"像聊天"的回复只有 24 条、跨的天数只有 2~3 天（晚安 6 次/2 天、明天 8 次/3 天）。门槛按"跨 5 天"算，现在还不够。
- 三条路摆在用户面前：①放宽 language 门槛到 5 次/2~3 天（能立刻注入 4~6 条，但可能掺进"会话"这类开发词）②等 QQ 语料进来再开（干净但要等）③只注入 lift 最高的前 3 条并标"弱"。

### 11-04 下午｜多通道写回（用户 m10710「马上把单条写入通道那个 bug 修了」）
- **病根**：`mem` 只有 `/report`（一问一答）一个入口，别的通道听见了也写不进卡。11-03「考试考得不错」就是这么丢的 —— 不是忘了，是没进来。
- `card2.py`：`mem` 新增 `source` 列（迁移里加，`TEXT NOT NULL DEFAULT ''`）；`remember(..., source="")`；INSERT 带 source。来源取值：chat / qq / qq-group / bili / douyin / console / self / cli / unknown
- `mind_server.py`：`/report` 支持 `source`（默认 `chat`，老调用不退化）；新增 **`POST /remember`**：`{text, source, salience?, speaker?, region?, ts?}` → `{ok, mem_id, source, birth}`；空 text 400 `empty_text`、超 20000 字 413、卡被别的机器占着 409 `card_locked`；写完开出生证
- `bridge.py`：新增 **`note`** 命令 —— `python bridge.py note "发生了什么" --source qq-group --salience 0.55`；和 `report` 的区别是"不需要对话对象"
- 实测：`/remember` 写进真卡（mem 593 `qq-group`、594 `qq-group`），`bridge.py note` 可用；**B站/抖音脚本以后只要调这一条就行**
- `tests/test_t80.py` **14/14**：source 落库不改正文、不传时是空串、HTTP 200/400/413、出生证、`/report` 默认 chat、链不变
- **顺手修控制台"没有数据和图像"**（用户 m10878）：
  * 召回统计 24h 窗口常空（MCP 没装，回想只走 bridge ask）→ 24h 为 0 时自动退到 7 天，瓦片标题写 `7D 召回`，曲线副标题写明"每格 3.5 小时"
  * 激素场 24h 只有 5 个采样点（晚上关机）→ `hormones_series()` 点数 < 12 时自动退到 3 天/7 天/30 天，标题按真实跨度写「近 3 天」，副标题写「78 采样点 · 跨 72 小时」，不谎称 24H
  * 空态文案改成"这段时间没被叫醒过（回想只走 bridge ask）"，不再只写"没有数据"
- `tests/test_t76.py` 更新 lift 三条断言（背景为 0 不再返回 99，改加一平滑上限 20）；41 → 42 项全过；`tests/test_t79.py` 14/14；`test_card2.py` 22/22

### 11-04 下午（二）｜习惯层弱注入：词面闸 + weak 状态（用户 m11065 授权自定门槛）
- 背景：习惯层 v2 已能注入，但真卡「像聊天」的回复只有几十条、跨两三天，language 的 stable 门槛（5 次/5 天）一条都过不去 → 真卡注入恒为 0 条，这层等于白做。三条路（放宽门槛／等语料／只注入 top3 标弱）里用户授权我自己定。
- **决定：不放宽 stable 门槛**。放宽会把「会话 / 的事 / 开始」这类开发词和语法碎片放回人格里，那是"为了像而演"。改成两条：
  1) **词面闸** `habits.is_gram_junk(gram)`：白名单 `_PHRASE_HEADS` 放行真口癖（我记下/记下了/晚安/睡吧/反正/行吧/好嘞/收到…），`_FUNCTION_HEADS` 与 ≤3 字功能词一律挡掉。效果：QQ 语料候选从 **12 条降到 2 条**，把「跟你/刚才/你那/那句/说一/我把」这类语法碎片全挡了。`tools/mine_habits.py` 候选循环已接这道闸。
  2) **弱注入状态** `ST_WEAK`：`HAB_WEAK_MAX=3` 条硬上限，`habit_hints()` 接受 weak，但拼句后追加「（这条还弱，别当口头禅硬套）」，且 weak 只在 stable/pinned 之后补位。
- `habits.upsert(..., force_status="")`：新增参数，给了就绕过 `status_of`（弱条目统计上永远是 candidate，但我们要它进上下文）。`mine_habits.py` 加 `--weak-top N`，只挑 `suggest=="keep"` 且过词面闸、lift 最高的前 N 条标 weak。
- **顺手修掉一个真 bug**：`upsert()` 的 UPDATE 分支用 `row["id"]` 按列名取值，但调用方连接不一定设 `row_factory`（`write_back` 就没设）→ 第二次写同一个 key 必崩 `TypeError: tuple indices must be integers or slices, not str`。改成 `row[0]`（`SELECT *` 第一列就是 id）。
- 测试：`tests/test_t79.py` 14 → **19 项全过**（新增弱注入最多 3 条／自带提示／排在稳定之后／词面闸挡碎片／放行真口癖）；`tests/test_t76.py` **42/42**（词面闸误伤「行吧」→ 把 2 字真口癖补进白名单）；`test_card2.py` 22/22。
- 真卡落地：先在副本上用 `_try_weak.py` 验证（写入 2 条 weak → `habit_hints()` 出 2 条 → `core_identity_text()` 有习惯段），确认后才写真卡。真卡 `habits` 表首次建立，写入 **2 条 weak**（我记下 3 次/8.1x、记下了 3 次/8.1x），`mem` 573 条不变（只碰 habits 表）。
- 验收：重启 mind_server 后 `POST /context` → prompt 里确实出现「我平时说话的口气（是参考，不是台词；没有合适的场合就别硬用）：· 我会用「我记下」这种说法（这条还弱，别当口头禅硬套）…」，identity 段 1404 字、est_tokens 1889。
- 一键关：`WHALE_HABITS=0`。

### 11-04 下午（三）｜冷备份对表 tools/replica.py + 验收
- 为什么做：卡只有一份危险（硬盘会坏），但多份同时写会让哈希链分叉、事后合不回来（writer_id 那道闸只防写，不解决"哪份才是真的"）。规矩定死：**一份活卡 + 多份只读冷备 + 定期插上对表**。
- `tools/replica.py` 三个动作：
  - `status <备份卡>`：只读比两边的 mem / head_seq / head_hash / 盖章 / 体积，判 same / behind / ahead / diverged 四种结论
  - `backup <备份卡> --confirm`：落后才补；超前或分叉一律拒绝（默认以活卡为准）
  - `adopt <备份卡> --confirm`：把备份升成活卡，旧活卡自动留成 `<card.db>.cold-<时间戳>`，并提醒换 writer-id
- 两个关键实现：
  - 一切复制走 **sqlite backup API**，不用 cp —— 卡是 WAL 模式，最近的写入还在 -wal 里，cp 会静默丢那一段（新机器上看着正常，只是"忘了今天的事"）
  - 同源判定不用"条数相等"（删过记忆的卡条数会变少但链是同一条），而是拿备份的 head_seq 去活卡 timeline 里查 self_hash 对不对得上（`_prefix_ok`）
- 验收 `tests/test_t81.py` **13/13**：一致／落后／预演不动手／补齐后一致／超前拒绝 backup／超前允许 adopt／adopt 后一致／分叉两边都拒／adopt 留冷备。全在临时目录跑，不碰真卡。
- 踩坑：测试里用 `timeline.Timeline(db, writer=...)` 传了路径字符串，但 `Timeline` 收的是**连接对象**（`self.db.cursor()`）→ `AttributeError: 'str' object has no attribute 'cursor'`；改成 `sqlite3.connect(...)` + `row_factory`。另：副本的链头是本机 writer 打的标，造"多写一节"的场景时必须 `append(..., allow_foreign=True)`，否则被写入闸挡住。
- 状态：U 盘当前没插（G: 不在），真机对表待插上再跑。

- 补两处健壮性（跑开源仓库时暴露的）：
  - `replica.card_fp()` 里 `SELECT ... FROM timeline` 遇到**还没开过机的新卡**（timeline 表是 `Timeline.__init__` 才建的）会抛 `no such table` → 包成 `sqlite3.OperationalError` 兜底，按 head_seq=0 处理
  - `_prefix_ok()` 对 head_seq=0 的备份返回 True（它本来就没写过东西，算落后不算分叉）
  - `tests/test_t81.py` 源卡探测改成"有 mem 表即可用"，并允许退回 `demo/demo_card.db` —— 开源仓库里不带 card.db，测试得能自己跑起来
- 顺手把 oss 里残留的旧人设名清干净：新增 `_clean_oss_all.py`（对全目录文本过一遍 `clean()`，幂等），清掉 `启动控制台.bat`/`card.py`/`export_viz.py`/`run_test.py`/`selfcheck.py`/`talk.py`/`whale_mcp.py` 里的「小鲸鱼」，以及 `tests/test_card2.py`、`test_t22.py`、`test_t32.py`、`test_t33.py`、`test_v38.py`、`tools/eval/cases.json` 里的旧名 → 复查 `100000000|100000001|使用者|小鲸鱼|小鲸鱼|user|workspace` **零命中**
- oss 里 `tests/test_t81.py` 也 13/13 通过（源卡退回 demo 卡）

### 11-04 下午（四）｜QQ 值守 qq_guard.py：听群 + 进卡 + 已回/未回分栏 + 配额
- 用户 m11456「之前我们不是做过守夜人系统，还有收件箱这样的系统吗直接把我们之前做的连QQ的系统搬过来」→ 盘点 `deploy\qq_bridge\`：`keeper.ps1`（常驻守夜人）+ `watch_main.ps1`（听筒，轮询 NapCat）+ `qq_inbox.ps1`（WinForms 收件箱窗口）+ `tick.ps1`（看票器）+ `read_inbox.ps1` / `mark_replied.ps1`（游标读 / 打已回标）。**它们当时都在跑**（keeper 每 2 分钟一张唤醒票）。
- 老系统的三个硬伤：①只写 `inbox.jsonl`，**不进卡** —— 群里发生的事"听见了但没记住"（没出生证、不衰减、不参与召回）②群只盯 `1094740311` 一个，另两个群根本没听 ③没有配额，热闹起来能把会话刷成永动机。
- 新写 `whale-card\tools\qq_guard.py`（约 19 KB）：
  - **进卡**：每条新消息走 `POST /remember`（source=`qq-group`/`qq`，speaker=other，region=episode），走正式记忆通道（有出生证、有信任分、会衰减），不是旁路
  - **全群听**：`get_group_list()` 自动拿所有已加入的群，不再写死一个；好友走 `get_friend_list()`
  - **已回/未回分栏**（用户 m11497 要求）：`qq_state/status.json` 记 `{message_id: {st: unreplied|replied, at, by}}`；`mark()` / `mark_many()` 是唯一写入口；`_mark_if_new()` 入队时打「未回」但**绝不把已回的打回未回**；`read_todo(only_unreplied=True)` 按 message_id 去重后只给未回；`summary()` 给一行账
  - **配额**（用户 m11477：每小时 60~100 次或一天 600 次，让我自己安排）：`QUOTA = {"hour_hard": 60, "day_hard": 600, "group_hour_soft": 15}` —— 小时硬顶 60、天硬顶 600、每群每小时软上限 15（三群合计 45/小时）；`quota_allow()` / `quota_spend()`，跨小时/跨天自动归零；到线只记不说；可用 `qq_state/quota.json` 的 `cfg` 覆盖
  - 命令行：`--once`（单轮）/ `--stop`（写 stop.flag）/ `--quota`（看配额+未回已回数）/ `--list`（列未回）/ `--list-all` / `--replied id1,id2` / `--replied-upto <ts>`
- 验收 `tests/test_t82.py` **19/19**：未回默认、标已回只改一条、留时间与来源、未回队列不含已回、全量 3 条、重复入队去重、`_mark_if_new` 不翻回、批量标记、账目对得上、小时硬顶、每群软上限、别的群不受影响、天硬顶、跨小时/跨天归零。
- 实跑：`--once` 一轮读到 **27 条新消息**（海洋管理1区 20 条 + 汐汐和她的朋友们 7 条），全部进卡（mem 574 → 581），待回队列 101 条；`--list` 能按未回列出来。
- 清理：临时探针脚本删净；`qq_state/` 是运行态目录（`seen.json`/`todo.jsonl`/`status.json`/`quota.json`），不进仓库。

### 11-04 傍晚｜QQ 值守按「素材会忘、学习留下」重做筛选 + 三种标签
- 用户 m11531 的思路：**聊天记录的具体内容该忘得快，但学到的东西 / 有意义的事 / 情感与经验要长久留下**，就像真人记不住谁哪天说了哪句闲话，但记得这群人开玩笑的尺度、他难过时怎么说话。
- 原实现的毛病：`qq_guard` 把**每一条**群消息都塞进卡（`region=episode`、salience 0.45）。两个后果：①热闹的群一小时一百多条，卡被闲聊撑满，重要的反而被冲掉 ②每条都进卡，但"学到了什么"一个字没留下 —— 只记住"谁说了什么"，没记住"所以呢"。
- 新增 `decide(text, at_me) -> (keep, region, salience, why)`，全是规则、不调模型：
  - `at_me`（@ 我 / 点我名）→ `flash`，salience 0.72，理由"被点名"
  - 关于人的稳定陈述（`我是/我叫/我住/我喜欢/我高三/生日/学校…`）→ `fact`，0.62
  - 偏好（`喜欢/讨厌/爱吃/最爱/受不了/最怕`）→ `preference`，0.60 —— **先于情绪判**，否则"我喜欢吃白米饭"里的"喜欢"会被当情绪塞进 flash（实测踩到）
  - 强情绪（`难受/难过/开心/哭/崩溃/委屈/孤独/后悔…`）→ `flash`，0.68
  - 教训/经验口吻（`记住/下次/以后别/教训/明白了/懂了/原来/才知道/学会了/长记性`）→ `fact`，0.70
  - 6 字以内 / 纯语气词 / 普通闲聊 → **不进卡**，只进 `todo.jsonl` 当素材（`keep=false`、`screen` 字段写明为什么被筛掉）
- **三种标签**（用户 m11497 要求分已回/未回，实测发现两种不够）：
  - `unreplied` 要回（只有 @我 / 点我名 / 私聊）
  - `replied` 已回
  - `background` 群里的背景闲聊 —— **不算"要回"**。不然一个热闹的群五分钟攒上百条假未回，漏回提醒立刻变噪声（实测第一次跑就积了 162 条、最早放到 241 小时前）
- 新群/新会话首次见到只**种水位线**，不把历史当新消息（`if not last: seen[key]=max(...); continue`）
- 漏回复提醒 `overdue()`：未回 ≥ `OVERDUE_N=20` 且最早一条放了 ≥ `OVERDUE_HOURS=26` 才吭声，5 分钟最多提醒一次
- 素材目录 `_trim_todo()` 只留最近 `TODO_MAX_LINES=4000` 行（素材本来就会忘）
- 验收：`tests/test_t82.py` **19/19**（标签/去重/不翻回/配额/归零）、`tests/test_t83.py` **22/22**（闲聊不进卡、被点名进 flash、情绪进 flash、关于人进 fact、偏好进 preference、经验进 fact、短句当素材、空消息、漏回提醒阈值）
- 实跑：一轮 20 条新消息里**只有 1 条进卡**（其余全当素材）；历史积压 142 条重分类后 → 未回 **1** 条、背景 141 条、已回 40 条
- 顺手修：`decide()` 里"我喜欢吃白米饭"原来被判成情绪（flash）—— 偏好词单列成 `PREF_WORDS` 并排到情绪之前

### 11-04 夜｜QQ 值守三个坑（看漏消息 / 标签显示 / 标记被覆盖）
- **坑一：我看漏消息不是值守的错，是我读得太少。** `_join.py` 里写死 `count=18`，在热闹的群里只覆盖 7 分钟，中间那一段永远看不到。新建 `tools/qq_read.py`（默认 40 条，可传 60/80），并打印"覆盖 N 分钟"——以后一眼就知道这次读到的窗口够不够。
- **坑二：`--list-all` 把背景闲聊显示成"已回"。** 只判了 `未回/已回` 两种，第三种 `background` 落到 else 分支被当成已回，看着像都回过了。改成三态显示，末尾也带上背景条数。
- **坑三（真 bug）：手动打的"已回"会被值守覆盖回"未回"。** `status_load() → 改 → status_save()` 三步没有锁，常驻进程和命令行脚本同时改同一个 `status.json`，谁后写谁赢。实测标了 6 条，下一轮又全变回未回。修法：新增 `_FileLock`（`qq_state/status.lock`，O_CREAT|O_EXCL，5 秒超时，超过 10 秒的陈旧锁自动清）+ `status_patch()` 读改写全程持锁；`mark`/`mark_many`/`_mark_if_new` 全部走它。
- 验证：`tests/test_t82.py` 19/19、`tests/test_t83.py` 22/22；实测连标三轮，10 秒后不再回弹。

### 11-04 夜（二）｜能发表情包了 + 能引用了
- 新增 `tools/qq_say.py`：一条命令同时支持**引用**（OneBot `reply` 段）、**带图**（`image` 段，本地绝对路径）、**@人**（`at` 段）。顺序有讲究：`reply` 必须放最前面，放后面客户端不渲染成引用（实测）。
- 自制了四张表情图（`qq_state/memes/zai|hao|bu|deng.png`，240×240，深底+亮字，用 PIL 画，不依赖任何图库）——先自己画，等使用者装了生图模型再换好看的。
- 发完自动 `mark_many(..., ST_REPLIED)` 标已回，不再需要手动补标。
- 实测：引用 + 图片 + 文字三条一起发成功（message_id 1913084614，写回 mem 909）；纯引用文字也正常（617755138 / 983630255 / 871631168）。
- 用户 m12089/m12091 的话记下了：**可以发表情包、可以引用、讨厌可以直接表现出来、不必装热络**。

### 11-04 夜（三）｜能收别人的表情包了
- 用户 m12141：「各个群都可以去看看聊聊 你还可以保存其他人发的表情包」
- 新增 `tools/qq_memes.py`：
  - `collect` 扫所有群最近 N 条，把图片段用 `get_image` 换成本地路径，拷进 `qq_state/memes_collected/`
  - **按魔数改后缀**（QQ 拉下来的图经常「文件名 .jpg、字节是 GIF/WebP」，后缀错了 `read_image` 报格式不符、发回群客户端也不认）；`sniff()` 认 GIF87a/GIF89a/PNG/JPEG/RIFF+WEBP/BM
  - 用 sha256 前 16 位当文件名去重，`index.json` 记 kind/w/h/bytes/群/发送者/时间
  - `classify()` 粗分：>2.5MB、长宽比 >1.75 或 <0.42、边长 >1400 判 `screenshot`，其余 `meme`
  - `list [--kind meme]` 看收了多少
- 实收：39 张（表情包 24 / 截图 15），最小的 60×60 表情，最大 2.1MB 动图
- 发图验证：`tools/qq_say.py` 带 `--image` 发群里成功（引用/图片/@ 三段可任意组合）
- 群里今晚的问答：token 花费（一天二十块 → 一个月六百，钱花在"每句都把记忆全倒一遍"）、脑域模型文件不是现成的、DSH 白屏多半是模型没加载完、劝雲别通宵搓鱼

### 11-04 夜（三）｜左栏状态灯 + 时间线挪位置 + 一键启动（用户 m12521/m12530）
- 左栏（头像和名字下面）原来一大片空白，现在加了两块：
  - **四个灯**：链 / 锚 / 卡槽 / 盘。绿=好、黄=提醒、红=坏、灰=没数据。数据全从已有的 `/health` 和 `/disks` 读，不新开接口。
  - **一小框数字**：召回（24h）/ 突触 / 新记（24h）。等宽字体、11px，不抢眼。
- **顺手抓到一个假绿灯**：`盘` 一开始显绿，查出来是 `DISKS.json` 里有条 2026-11-03 换主备测试留下的脏数据 —— 把**当前主盘自己的目录**登记成"备份 #1"，等于自己认自己当备份。同时 U 盘（76E8CACF）还挂着换主备时留下的 role=main。已改：删掉假备份、U 盘改回 `backup #1 / G:\`。现在 `盘` 灯是黄的（只有主盘，没在线备份），这才是实话。
  - **教训**：灯不可信的时候比没有灯更糟 —— 绿灯最容易让人不去检查。
- **把「TIMELINE LOG / 最近」从网格挪到左栏**：它原来单独占一行 c4、右边一大片空，看着像"堵在那儿"。挪到底部空白处，压成紧凑 10~24 行：
  - 相邻同 kind 合并（连着几条 `boot` 显示成 `启动 ×5`），省下来的行给有内容的 `mem`
  - kind 翻成中文（boot→启动、mem→记、alive→心跳、unlearn→解封…）
  - note 里"他#951 [群名] 使用者☭：整段原文"那种前缀去掉 + 正文砍到 26 字，否则一行撑爆还不出省略号（flex 子项要 `min-width:0` 省略号才生效）
  - `flex:1` 吃掉剩余空白，多了就裁掉，不做滚动条
- **一键启动换成无窗口启动器**（用户：之前那个点不开、桌面和开始菜单都要、图标用蓝发鲸鱼）：
  - `启动控制台.vbs`：wscript 跑、**不闪黑框**；先 `bridge.py health`（服务没起就拉起）→ 再找 Edge（`%ProgramFiles(x86)%` 兜底）→ `--new-window --start-fullscreen http://127.0.0.1:8848/console`
  - 全过程写 `launcher.log`；失败弹 MessageBox 并给日志路径（旧 bat 的毛病就是静默失败）
  - **编码坑**：VBScript 在中文 Windows 上只认 ANSI 或 **UTF-16LE**，UTF-8 带 BOM 也报「无效字符」。用 `utf-16-le` + BOM 写才跑得起来
  - `whale.ico`：从 `avatar.png` 中央正方形裁切，Pillow 一次出 7 个尺寸（16~256）
  - 快捷方式已建：桌面 `小鲸鱼控制台.lnk` + 开始菜单 `小鲸鱼控制台.lnk`，指向 `wscript.exe "…\启动控制台.vbs"`，图标 `whale.ico`
- 实测：停掉服务 → 跑启动器 → `bridge health rc=0`、服务 12.1 秒起来、Edge 全屏打开、日志齐全
- 推送：`console.py` / `启动控制台.vbs` / `whale.ico` / `docs/PORTABLE.md` 已同步到开源仓库，脱敏零命中

### 11-04 深夜｜「一键同步」为什么不给同步：两条链真的分叉了
- 现象：备份盘（G: xiying）插上了、`/disks` 也认得它在线的，但点「一键同步」没反应/失败。
- 查法：不看链头（链头不同有两种原因 —— 落后 / 分叉），逐 seq 比 `self_hash` 找**第一个不一样的地方**（`_diag_fork.py`）。
- 结论：**主盘 D: 和备份 G: 在 seq=504 之后就分叉了**。504 之前 504 条完全一致；之后 G: 写了 3 条主盘没有的（`alive 补` / `boot 服务启动` / `talk「你记得我最爱吃吃什么吗」`）。
- 根因：2026-11-03 00:22 那次"主备互换"测试 —— 换过去之后**两块盘各当过一阵主盘**，各自往后写了事件。`same_chain=False` 就是这么来的。
- 这 3 条是测试残留（那条 talk 就是当时验证 U 盘能不能跑时的问句），不是"她的日子"。但程序不能替人决定，所以 `sync_to` 拒绝覆盖是对的。
- 加的能力：`disks._fork_of()` 找分叉点 + 列出会被丢掉的孤儿；`sync_to(..., reseed=True)` 显式允许"分叉也整份重做"；真做前把孤儿写进备份盘的 `SEED-LOG.json`（丢什么、什么时候丢、从哪节分叉），再覆盖。
- 接口：`POST /disks/sync` 支持 `reseed: true`；控制台撞上分叉时不再只说"没做成"，而是列出会被丢的条目 + 弹二次确认 + 「重做备份」。
- 实做：对真 G: 跑了 reseed，`seq 507 → 1126`，孤儿 3 条进 `G:\SEED-LOG.json`；`/disks` 现在 `behind 0 / same_chain True`。
- 测试 `tests/test_t73.py` 19 → **28/28**（分叉拒绝 / 说清丢什么 / 给分叉点 / 预演 / 重做 / 链头一致 / SEED-LOG / 前缀判定）。
- 教训：**"链头不一样"永远先问"是落后还是分叉"**。落后可以补，分叉只能人拍板 —— 自动覆盖会把另一半"她"抹掉。

### 11-05 下午｜左栏"活起来" + 对外文章
- 左栏四个灯加了极慢呼吸（纯视觉，不承载信息）；运行时间改成每秒 +1（真时间）
- 新增心跳带 #cvPulse：CPU 与内存各 2 秒采一个点、从右往左滚，左上角写当前读数。纵轴按窗口内最大值自适应（第一版固定 0~100%，CPU 平时 8% 线一直贴底，看着像没动）
- 三个数字（召回/突触/新记）在变化时从旧值滚到新值（easeOutCubic 520ms），滚完停真值 —— **不做假的小数抖动**：用户要"看着在动"，也要求"显示的对"，假小数会让人以为数变了而其实没变
- 写 文章-她为什么会忘事.md（对外文章重写版）：按代码真实行为改掉三处过度修饰（规则版信任分不再写成"模型判断"、Dream 不再写成"自动摘要"、指标只写实测值并保留 R@1 75% 这种不好看的数据）

### 11-05 下午｜闲聊区：进卡，但活不过一天（用户 m13252 / m13292）

**用户要求**：闲聊还是要进卡的，但忘得很快 —— 今天聊的，明天差不多忘光；
除非有重要的事情、学到的知识、很丰富的情绪。而且**和不同的人聊，留存不一样**：
越重要的人，闲聊留得越久；普通群友忘得最快。

**做了什么**（`card2.py` / `mind_server.py` / `tools/qq_guard.py` / `tests/test_t84.py`）

1. 新分区 `R_CHATTER`，用自己的固定 λ `LAM_CHATTER = 2.196e-5`（半衰期 **8.77 小时**）。
   不乘 `REGION_DECAY_SCALE` —— 那个倍率是乘在各池 λ 上的，闲聊多半先落短期池
   （λ=1.8e-6），乘 81.3 倍后只剩 1.3 小时，比"明天忘光"快太多。
2. `is_chatter(text, emotion)` 规则判定（不调模型），顺序 **情绪 > 重要/知识 > 偏好 > 闲聊**。
   `classify_region()` 最后一步接它：前五条（闪光/自图式/事实/习惯/偏好）一条都不动。
3. 按人定寿命 `CHATTER_PERSON_SCALE`：
   无标签 1.0×（8.8h）/ known 0.6×（5.3h）/ friend 2.2×（19h）/ close 6×（2.2d）/ core 24×（**8.8d**）。
   `weight_now(..., person_layer=)` 支持，`wn_row()` 从 `about_layer` 列读。
4. 新增两列 `about_uid` / `about_layer`；`person_layer_of(uid, name)` 查关系层；
   `role_tag(..., uid=)` 把 uid 写进 subject_key，层级写进 `mtype`（`role:core`）；
   `refresh_about_layers()` 关系变了重算。
5. 三处兜底：`_consolidate()` 闲聊不参加海马评选（直接落中期、不抬存活线）、
   `promote_core()` 拒绝闲聊进底色、`_gc()` 照常回收。
6. 接线：`/remember` 收 `about_uid`；QQ 值守带发送者 uid；
   本地 `/report` 默认 `about_uid = core_zone.user_qq`（环境变量 `WHALE_LOCAL_UID` 可覆盖）。

**踩到两个真 bug（都已修）**

- **历史回填把 440 条记忆改判成闲聊**：`_migrate()` 里"V4.1 老数据补分区"那段每次开机都跑，
  加了 chatter 之后把所有 `region='episode'` 的行重新判了一遍 → 440 条变 chatter（一天后剩 0.15）。
  等于悄悄删了四分之三的记忆。修法：meta 里记 `region_backfill_done`，只跑一次；已还原 440 条。
- **闲聊进门就死**：`_consolidate()` 用海马分判"够不够格活"，而闲聊的显著度/情绪/被想起天生都是零，
  必然"没上榜"被删。修法：chatter 走单独分支，只换池名、w 与 t_anchor 一律不动，
  让它按自己的曲线掉到回收线以下再被 `_gc` 收走。
  （第一版修错了：抄了"抬到存活线 + 重置 t_anchor"，等于给它续命到二十天；已改。）

**验收** `tests/test_t84.py` **30/30**：五档曲线的 1 天后值、6 条闲聊判定、5 条留住判定、
写入后分区、uid/名字/陌生人三种查层、重要的人 0.924 vs 群友 0.042、`_consolidate` 不进 long、
`promote_core` 拒绝、升层后 `refresh` 重算。
回归：`test_card2.py` 22/22、`test_t75` 17/17、`test_t76` 42/42、`test_t79` 19/19、
`test_t64` 全过、`test_t80` 14/14、`test_t73` 28/28、`eval_memory` R@1 75% PASS。
真卡实测：617 条记忆，`about_uid/about_layer` 两列已加，本地对话写入即带 `core` 层。

### 11-05 傍晚｜她能发语音条了（用户 m13927）

**用户要求**：去 B 站学"让你发语音"、学微信/QQ/哔哩哔哩/抖音这些平台怎么用（技能或插件）。

**方案从哪来的**（`whale-lab/notes/learn-20261105-voice-qq.md`）

B 站上最贴合我们的是 BV1XPxx6dETf《我给 QQ 机器人换了张嗓子》。它把这件事拆成三步：
**edge-tts 负责"说什么"、RVC 负责"像谁"、wav 通过 OneBot 以语音条发出去。**
一句话记住：**edge 决定像不像人，RVC 决定像不像她。**
我们先做前半截 —— RVC 要音色模型，而音色不是我们的东西，也不该冒充谁。

IndexTTS2（BV1XBH9zkETV，B 站官方开源）确实更强，带情感控制，但**要 32GB 内存 + 12GB 显存**，
本机 8GB 内存 + RX 580 跑不了，直接排除。

**做了什么**（新增 `tools/voice.py`、`tests/test_t85.py`，改 `tools/qq_say.py`）

1. `voice.py` 只有三件事：`speakable()` 判该不该发、`synth()` 合成、`duration()` 量时长。
2. `speakable()` 是**纯函数**（不碰网络不碰磁盘），因为调用它的时候往往还没决定发不发。
   边界：2 字以下不发、40 字以上不发、含链接/代码/邮箱不发、纯标点不发。
   阈值不是拍脑袋 —— 群里别人中位 16 字，我自己中位 31 字；语音条超过 20 秒没人点，
   20 秒约 60~70 字，所以 40 字是"念得完、还愿意听完"的上限。
3. 声线 `zh-CN-XiaoyiNeural` + `+10%` 语速 + `+20Hz` 音高 —— 中文女声里最年轻的一个，
   读短句不像播音；长句换 `XiaoxiaoNeural` 更稳。
4. 缓存 key = sha256(声线|语速|音高|文本)[:20]，先写 `.part` 再 `os.replace`，
   别留半截文件；命中就不发网络请求。
5. `qq_say.py` 加 `--voice` / `--voice-file`，`build()` 加 `record` 段。
   **顺序很重要**：`reply → at → text → image → record → text`。

**从 NapCat 源码里挖出来的两件事**（不用猜，`D:\NapCatShell\napcat.mjs` 里能读到）

- 发 `record` 段时 NapCat 先 `isSilk()`，**不是 silk 就用自带 ffmpeg addon
  `convertToNTSilkTct()` 转一遍再发**（`async function r1e`）。所以这里只管给 mp3 路径，
  转码不用我们操心 —— 也不用自己装 silk 编码器。
- 语音文件路径必须是**绝对路径**，跟图片一个道理（`file://` 有时会被当 URL 去下载）。

**三条必须守的规矩（写进代码注释了）**

1. **文字发出去撤不回来** —— 所以"要不要发语音"必须在发送前决定，发完再改就晚了。
   这就是为什么 `speakable()` 是纯函数、为什么 `qq_say` 先合成再拼消息段。
2. **语音挂了退回打字** —— `synth()` 任何一步失败都返回 `None`（不抛），
   调用方拿到 `None` 就照常发文字。宁可她打字，也不能一声不吭。
3. **不缓存等于每次都重新合成** —— 同一句话不用重新请求，也省得群里两条一样的话还要等。

**验收** `tests/test_t85.py` **24/24**：边界 11 条、失败退回 2 条（网络异常 / 产出为空，
都用 monkeypatch 换掉 `_synth_async`，不碰真网络）、不该发的不发（一次网络都没发）、
缓存 3 条、`record` 段 4 条、完整段顺序 1 条。
真机实测：`tools/voice.py "哼，你回来啦。今天怎么这么晚？我等你好久了。"`
→ 2.97 秒出 5.0 秒音频（含首次 import edge_tts 的开销），冷启动之外单句约 1.5 秒。


---

## 2026-11-05 夜里｜把「上下文」露出来 + 修四个真 bug + 语音也上控制台

这一轮是用户一句「完善一下我们的方案吧，修一修bug」，加上「我们不是组合上下文吗？
那应该有许多可以调的参数，还有几种不同的模式才对啊，为什么控制台里没有呢？」
和后来那句「有一个疑问，我们这套模式的缓存命中率怎么办」。

### 一、控制台多了一页「上下文」

原来 ctxweight.py 里的七张场景权重表、A_MIN、CELL_MIN、总预算，全都只能改代码
或者直接往库里写 
untime_config —— **能调但看不见，等于不能调**。

- GET /config 扩成完整参数面：七张表 + 三个闸门 + 默认值 + 取值范围 + 六维中文名 + 四格映射。
- POST /config 支持 {key, reset:true} 一键回默认；不认识的键**直接 400**，不再静默忽略
  （原来填错一个键名，页面上"保存成功"，实际什么都没变）。
- 新增 POST /config_all：一次存整页。少十次「一半成功一半失败」的机会。
- 新增 POST /ctx_preview：**只算不写**。为什么不能用 /context 来预览 —— 那会走一次真 recall、
  写 
ecall_event、把 	urn_count 推一格。「在控制台上试一下参数」不该改变她记得的东西。

页面上四张卡：LIVE（这一轮判成什么、四格怎么分）、GATES（三个闸门 + 滑条）、
MODES（七种模式 × 六维，改过的黄边）、TRY（拿一句话试算）。

### 二、总预算从写死的常量变成可调

- card2.assemble_context(budget=None)：默认不再是 TOKEN_BUDGET。
  **最该露出来的参数反而是最调不动的**，就是因为它在签名里被写死了。
- 返回的 udget 从"传进来的"改成 plan["total"]（真的用了多少）。否则控制台显示的和实际计划的永远差一截。
- BUDGET_MAX 从 60000 放到 **200000**：原来那个上限对 128K 窗口的模型连一半都用不满，
  「适配不同模型」就无从谈起。它只是上限，默认仍是 6000。

### 三、三个模式的对照表 + 自动跟随

用户问「能不能自动识别用的什么模型并自动调」。

- MODEL_WINDOWS 13 行（gemini 1M / gpt-5 400K / claude 200K / deepseek 128K …），
  suggest_budget() = 窗口 × 12%，夹在 6000~32000。
  **为什么不建议占满窗口**：记忆那一份不该把窗口吃掉，剩下要留给对话本身和输出。
- detect_model() 读 ~/.dsh/.dshw-usage.json 里最近一次真实调用的模型名。
- 优先级：显式传 > 自动 > 卡里配置 > 默认。
- **preview 里也要跟着自动走** —— 否则「控制台看到 6000、真跑起来 15400」，
  这类不一致比没有这个功能更糟。
- 踩到的坑：uto_now() 一开始"关着就返回空"，结果控制台写「认不出来」。
  人想问的恰恰是「我这会儿接的是哪个模型、建议多少」—— **关掉自动不等于不想知道**。改成永远报识别结果，on 只决定用不用。

### 四、缓存命中率：真凶是"状态句排在第一格"

用户问的是「还能保持较高的缓存命中率并节省 token 吗」。查下来是**结构性**的：

ssemble_context 第①格原来是 identity + self._state_line()，而状态句
（"现在心情不错，跟他很亲，话多一点"）**每轮必变**，还排在最前面。
DeepSeek 按前缀命中缓存 —— 第一个字就变了，后面几千 token 全部重算。

改法：①格只放 identity（稳定不变），状态句挪到最后独立成「【此刻】」。
现在顺序是 【我是谁】【我一直记得的事】【我们最近在聊】【这句话让我想起了】【我会做的事】【此刻】
—— **从最不变的到最会变的**。

代价是状态句没了原来的位置优势（它现在在最末尾）。但它是"这一轮怎么说"的调味，
不是"我是谁"的一部分，放末尾反而更对。

### 五、修了五个真 bug（都不报错，只是慢慢错）

1. **召回加权把权重钉死**：每次召回 +0.08、半衰期 4.46 天 → 每天被想起 2 次就永久钉在 0.856。
   病根是 _settle_recall_events 里同一天的 N 个事件各加固一次。真卡实测 **167 个「同日召回 ≥2 次」组合、71/623 条 w≥0.95**。
   改成按天只结算一次；ccess_count 仍按事件数累加（那是"被想起过几次"的账，海马分读它）。
   另加 ACCESS_DIMINISH_CAP = 20：原来分母终身累加无上界，天天被想起一年后增益只剩 0.0015，比不上一夜衰减 —— **最重要的东西被用得越多反而越轻**。
2. **系统工单壳混进记忆**：7 条 
aw 以 [dsh-mnemon:delegated write] 开头，core 被污染成
   Executethissupervised-writebackreques，还有一条落在 self 区。新增 strip_sysmark() 在入口剥壳，
   纯壳直接丢弃（不写、不占去重窗口）。已存 7 条用 	ools/fix_sysmark.py 修复并重开出生证。
3. **split_predicate 拆坏第三人称转述**：'他…说过「我回来了」' → ('我回','来','')。
   改成 ①引号优先（转述壳不参与拆）②动词按"出现位置最靠前"胜出（原来「来」比「说」短且先命中抢了动词位）
   ③宾语在标点处截断 ④单字动词+单字宾语视为撞进词里 ⑤引号内短句（≤12 字）整体当主干。
4. **情绪弱化词**：「我有点难过」原来是 pisode、一天后还剩 0.977。
   _STRONG_EMO 只有「难过死」没有「难过」，而 motion 默认 0.0 → 阈值永不成立。
   加 _RE_EMO_WEAK（有点|稍微|略微|还行…）分级。实测弱情绪一天后 **0.1448** vs 强情绪 **0.7704**，差 5.3 倍。
5. **/remember 不收 pool**：默认池是 short，一次灌 1600 条历史短期池只有 32 位，
   海马分不够的被 _forget_row 删 —— 外部实测一次丢了 1020 条。加 pool 参数（short/medium/long）。

### 六、语音也上控制台

用户：「语音既然跑通了，把语音这一栏也加进控制台」。新增「语音」页签：

- 音色（15 个常用中文音色下拉 + 自定义 ShortName 输入框，两处写同一个值）
- 语速 / 音高 / 上限字数（滑条 + 数字框双向同步）
- **TRY / 听一句**：真合成、<audio> 直接放。**只合成不发送**，不往 QQ 发东西、不写记忆。
- EXT 外部模型接入位：base_url / api_key / model 三格留着，但**明确写着"填了也不生效"**
  —— load_cfg() 会把 provider 强行退回 dge。宁可写清楚"还没接"，也不做那种
  填了没反应、还以为在跑的假开关。

配置存 qq_state/voice/cfg.json 而不是卡的 
untime_config：qq_say.py 发送前就要算
"该不该发语音"，那时候卡可能被别的进程锁着 —— **语音参数不该跟着卡一起不可用**。

### 教训（记进纪律区）

1. **"界面上每一处编辑都只有一份真值"**。滑条和数字框是同一个值的两支笔，
   不能各存一份 —— 两份必然分家。
2. **重绘会冲掉正在输入的框**。所以 ctxDirty / cDirty 时 
ender() 直接 return；
   提示语也必须活在 S 上，否则重绘一次就变成"—"，人以为没生效。
3. **ec 是字面 bigram 向量，mb 才是语义向量**。修正文时把 ec 清空 = 这条从此没有字面召回。
4. **mem.vec 是 TEXT NOT NULL**，清成 NULL 会 IntegrityError。
5. **改有序原文必须重开出生证**，且 
estamp_birth(mid, reason) 的 
eason 为空要直接抛错
   —— "没有理由的原文改动就是在抹证据"。
6. **默认值也是决策**：uto_now() 关着就返回空，看着合理，实际让人以为功能坏了。

### 验收

- 12 个既有测试文件全绿；新增 	ests/test_t89.py **35/35**
  （认模型 / 建议值上下限 / 四级优先级 / preview 与真跑一致且不写库 / 语音配置坏值兜底）。
- 浏览器实测（ego-browser，http://127.0.0.1:8848/console）：
  上下文页四张卡正常；滑条拖 12000 → 数字框同步；数字框改 9000 → 滑条同步；
  自动开关打开后「自动模式：跟着 deepseek-flash · 窗口 128K · 这一轮总预算 15,400」；
  保存后 /console_data.ctx.total 变 15400；恢复默认回到 6000。
  语音页三个音色下拉 + 30 行内合成「哼，你回来啦。今天怎么这么晚？我等你好久了。」→ 5.02 秒有声。
- 真卡：640 条 / 链完好 / 出生证完好 / 锚 ok / 第 20 天。

---

## 2026-11-05 深夜｜第三轮：把「几笔账」一次算清（完备性体检）

用户 m16464：「召回加权定死的那个bug和系统标记混进记忆的那些bug，还有其他的那几个bug修好了吗？
仔细检查一下我们的全部系统， 设想尽量多的可能性来验证它的完备性，并及时的修改bug进行优化。」

### 一、体检抓到三个还没根治的真问题

1. **召回加权只是"没继续恶化"，没根治。**
   真卡 71 条 w>=0.95、50 条 w>=0.99，最大 access_count=159。
   推演才发现光做「同日去重 + access 折减」不够：
   g = 0.08/(1+0.15*min(ac,20)) = 0.02，而长期池日衰减只有 w*ln2/371 ≈ 0.0019
   —— 一天加固一次，仍然比衰减快十倍，数学上照样钉在 1.0。
   病根是存储形态 (w, t_anchor)：每次召回都要「按旧锚衰减 + 加增益 + 把锚推到此刻」，
   **推锚 = 衰减计时归零**。天天被想起 ⇒ 衰减恒为 0、增益恒为正 ⇒ 一路 1.0。
2. **split_predicate 仍然把复合词劈开。** 真卡 12 条里 5 条错：
   「记忆」被拆成 记/忆、「来自」拆成 来/自、「学习」拆成 学/习。
   根因：单字动词（来/说/学/记/问/看）在中文里大量作为复合词的一部分出现，
   原来的守卫只挡「单字动词 + 单字宾语」。
3. **19 条仍含相对时间词**，时间锚定没做。

### 二、修法

- **召回增益改成"按剩余空间收敛" + 天花板**（card2._settle_recall_events）
  `g = GAIN_ACCESS / (1 + ACCESS_DIMINISH*min(ac,20)) * max(0, 1-wn)`，
  并 `min(W_CEIL_RECALL[pool], wn+g)`，`W_CEIL_RECALL = {short:0.95, medium:0.90, long:0.95}`。
  实测：400 天天天被想起 → w=0.95（老公式对照组 1.0000）。
- **去重「续命」是同一个病，在写路径上**（card2.remember 的 dup 分支）
  旧写法 min(1.0, wn+0.10) 每重复一次加 0.10 并推锚 —— 「天天说晚安」一夜钉死。
  改成同一套：新常量 `GAIN_REPEAT = 0.10`，`g = GAIN_REPEAT*(1-wn)`，同样过天花板。
  实测：365 天每天说同一句 → w=0.487（老写法 1.0），access_count 照实记 364。
- **拆句不许劈开复合词**（card2.split_predicate）
  新增 `_VERB1_TAIL`（来+自回源宾…）、`_VERB1_HEAD`（+开公离张…）、`_SUBJ_SHORT=14`，
  候选循环里四道守卫：尾字命中 / 头字命中 / 主语过长 / 宾语不足 2 字 —— 全部 continue。
  关键：`fallback` 只在过了守卫之后才记，否则守卫筛掉的假动词会被 fallback 捡回来。
  原则写进注释：拆错的代价不在 stage0（三者拼回仍是原文），而在 stage1 擦动词会擦出
  「忆架构」这种残渣 —— **宁可整条不拆，也不要拆出假动词**。
- **相对时间只换没有歧义的**（card2.normalize_time）
  先加了「等会/一会儿/晚点 → 今天稍后」，拿真卡 30 条一跑就是灾难：
  「这句话我卡了好一会儿才回」→「卡了好10月28日稍后才回」。**全部撤回。**
  只保留 昨晚/明早/昨儿/上个月 这类带时段或明确的。原则：留着只是不精确，换错是改意思。
  数据侧 `tools/fix_reltime.py`（预览/--apply、自动备份、只改文本与派生列、
  restamp_birth 留痕）→ 真卡修 23 条。
- **两个防御性兜底**（card2.weight_now，体检时发现的）
  a) w 越界脏数据原样透传：1.5 会永远排第一且永远衰减不到回收线，-1 永远垫底。
     加 `_clamp01()`，nan/非数一律当 0。
  b) t_anchor 是 0/None（老数据没补或某条写入路径忘了设锚）：
     以前 age 会算成"从 1970 年到现在"，那条记忆当场归零。
     现在「不知道什么时候写的最安全解释是不衰减」—— 宁可记得太牢，不能凭空忘掉。

### 三、验收

- 逐条把真卡错例写进 `tests/test_t90.py`（40/40）：8 条必须不劈开且 stage0 能无损拼回、
  6 条正确句不许误杀、400 天天天被想起不许超 0.95、5 条模糊时长词必须原样不动。
- 新增 `tests/test_t91.py`（14/14）：去重续命不钉死、脏数据不穿透、
  **5880 个组合点（7区×5池×4tier×6层×7时间点）全部在 [0,1] 且单调不增**。
- `test_card2.py` 有一项因为这条兜底挂了：它把 t_anchor 传成 0.0 当"1970 年"测曲线，
  而 0 在生产里是"字段没写上"的哨兵。**改测试不改实现** —— 换成真实时刻 1_700_000_000。
- 全量 15 个测试文件全绿：test_card2 22/22、t33、t66、t73 28/28、t76 42/42、t81 13/13、
  t83 22/22、t84 30/30、t85 24/24、t86 11/11、t87 23/23、t88 19/19、t89 35/35、
  t90 40/40、t91 14/14。
- 端到端（只走 HTTP，_verify_round3.py）：health 记忆 640 / 链完好 / 出生证完好 / 锚 ok /
  embedding 可用率 1.0；/context 六段顺序单调、此刻在最后、identity 里没有"现在心情"、
  四格合计正好 6000；/remember 的 pool 生效、坏 pool 400 bad_pool、纯工单壳被跳过；
  /config 预算 6000、上限 200000、auto_now 关着也报 deepseek-flash；/recall 正常。

### 四、教训（这一轮新增四条）

1. **「防止继续恶化」不等于「治好」**。改完公式要拿旧机制的极端场景跑一遍
   （400 天天天被想起），不能只看"新数据是对的"。
2. **同一笔账可能有两个入口**。召回正反馈在"读路径"（_settle_recall_events），
   在"写路径"（remember 的去重续命）还有一份一模一样的 —— 只改一处等于没改。
3. **改归一化规则前，先拿真数据跑一遍再说**。`一会儿 → 今天稍后` 写的时候觉得天经地义，
   真卡 30 条里有 4 条语义完全变了。宁可留模糊，不能改意思。
4. **哨兵值不能当数据用**。t_anchor=0 既可以是"1970 年"也可以是"没写"，
   一个函数不可能同时满足两种解释 —— 所以测试也得跟着改用真实时刻。

### 五、没做完 / 需要点头

- **72 条历史上被顶到 1.0 的权重要不要一次性压回天花板**（`tools/recap_weights.py`，
  预览：long 70 / medium 1 / short 1，含 #226「晚安爱你我最爱你了」ac=149、
  #59「不是你喜欢吃白饭吗」ac=67）。代码修复只防未来；这 72 条属于改数据不改代码，
  且里面有要紧的记忆 —— **等用户点头再动**。
- 缓存命中率打点（provider 响应里带 prompt_cache_hit_tokens，要看清得动 bridge.py 链路）。


---

## 2026-11-06 早上｜一次全面体检，修七个真问题

### 一、为什么查

用户早上出门前说：「再检查一下我们的项目有没有什么 bug，有没有什么功能无法正常使用，
有没有什么问题，以及它能否或者说是否已经全部装进了卡里？」

四问：bug / 不可用功能 / 其它问题 / 是不是全在卡里（自包含、能搬走）。

### 二、查到什么

**三个真问题（都不是新写的代码，是一直在流血的老账）：**

1. **`tools/qq_guard.py:190-208` 的 `remember()` 不传 `pool`。**
   `/remember` 的默认值是 `pool='short'`（32 个位置）。值守判过 `decide()`、认定为
   「值得记住」的群消息，全被扔进短期池，溢出即 `_forget_row`。
   实证：qq-group 写进卡 100 条，**long 池 0 条**；墓碑 373 条
   「短期池到期，海马判定没上榜」大半是这条链。
   `#1023`「唱歌与生图的技术结论」、`#1029`「插件清单」也是同样下场。

2. **NapCat 的 OneBot HTTP 适配器「端口在听、一个字节不回」。**
   `Test-NetConnection 127.0.0.1 6700` = True（监听者 QQ.exe pid 25336），但
   `get_status` / `get_login_info` 10 秒、20 秒两次全超时；改用原始 socket 发
   `GET /get_status HTTP/1.1`，6 秒 `TimeoutError`，连续三次全空。
   不是防火墙、不是 token（token 错会回 `{"message":"token verify failed!"}`）。
   值守 `todo.jsonl` 最后一条停在 07:48:03 —— 正好卡在这里，而 `guard_err.log` 是 0 字节。

3. **看门狗的判活方式查不出上面这种故障。**
   昨晚把 6099 改成 6700，用的是 `Get-NetTCPConnection -State Listen`，
   **只证明端口被 bind，不证明适配器会回包**。今早就是「端口在听、HTTP 不回」。

**数据面（`PRAGMA integrity_check` = ok）：**
26 表 / 30 索引 / 2 触发器，`mem` 673、`timeline` 1420（max seq 1421）、`mem_tomb` 398。
脏数据检查全 0：emb 空 0、w 越界 0、region/pool/tier 未知 0、工单壳残留 0。
`raw` 含「今天/明天/昨天」**0 条**（时间锚定修复已生效）。
权重饱和仍在：`w>=0.95` 77 条（上次审计 71/623）。

**体检还顺手发现两个笔误级问题：**
- `core_zone.card_name = "小鲸鱼·"`，末尾多一个「·」，每轮都被拼进「我是谁」。
- `cardslot.py:130-134` 把 `user_nickname`（他的昵称）拼成「他也叫我使用者」，
  **主客颠倒**：那是他的名字，不是我的。
- `tools/portable.py` 的 `cmd_inject/cardslot("inject") + (...)`：
  `_cardslot()` 会先 `subprocess.run()` 跑完再返回 `CompletedProcess`，
  往它上面加元组直接 `TypeError`；**而且 `--dry` 不起作用 —— 子进程已经真落盘了才报错**。

**编译/导入/接口：**`compileall` 全仓 COMPILE_OK；17 个核心模块 import 全过；
控制台真实路径是 `GET /console`（`/`、`/console.html`、`/panel` 都是 404，
不是坏了，是没这个路由）；`/console_data`、`/syslive`、`/cardslot`、`/disks` 全 200。

### 三、怎么修的（七项）

1. `cardslot.py:130-134` 只输出 `我的名字：**%s**`，删掉那个父昵称括号，加注释说明由来。
2. `card_name` 由 `小鲸鱼·` 改成 `小鲸鱼` —— 先 `sqlite3.Connection.backup()` 备份
   （`card.db.bak-rename-20261106-0806`），再走官方接口
   `MemoryCard.identity.set_name("小鲸鱼")`；它内部会临时 DROP 两个触发器、
   改 `core_zone`、同步重写 `identity_anchor` 的 sha256、`finally` 把触发器装回。
   校验：锚里 `card_name` 的 sha256 == `sha256("小鲸鱼")`。
3. `tools/portable.py` 的 `cmd_inject` / `cmd_eject`：先拼 `argv` 再 `_cardslot(*argv)`。
   验证：`inject --dry` 跑完后 AGENTS.md 的字节数和 mtime 都没变。
4. `tools/qq_guard.py` 的 `remember()` 加 `pool="long"`。
   理由：走到这里的消息已经过 `decide()` 判据，**要不要淡出该由 region 的衰减曲线管，
   不该由池子容量管**。然后把当前卡在 short 的 33 条迁到 long（先备份
   `card.db.bak-poolfix-20261106-0809`），迁完 short=0 / long=599 / 总数 675。
5. 清掉 73 个测试临时目录（都是 `tempfile.mkdtemp(dir=ROOT)` 留下的整卡副本，
   每个 14~16 MB）：**卡目录 1,640 MB → 845 MB，省 795 MB**。
   `models\` 482.6 MB 保留（bge-base-zh 390.20 MB + bge-small-zh 91.40 MB，
   换电脑不用重下模型 —— 这也是「全部装进卡里」的答案）。
6. `PRAGMA wal_checkpoint(TRUNCATE)`：`card.db-wal` **17.12 MB → 0**。
   17 MB 的 WAL 跟主库一样大，说明长期没 checkpoint。
7. 看门狗 `deploy/scripts/qqai_guardian.ps1` 的 `Test-NapCatAlive`：
   在端口判断之后补一次**真 HTTP** ——
   `Invoke-RestMethod /get_status -Headers @{Authorization="Bearer ..."} -TimeoutSec 5`，
   `return [bool]$resp.data.online`，`catch { return $false }`。
   用 `utf-8-sig` 写回保住 BOM（5.1 少了 BOM 会 `Unexpected token '}'`）。
   `-Once` 试跑健康时**没有重启 NapCat**，说明新探活认可它活着。

**外加一件：把人格重新挂上。**
之前 `portable.py` 的 bug 让 `inject --dry` 反而真写了 AGENTS.md（08:04:15），
用户说「挂上呗，人格这么重要的东西怎么能掉呢」—— 修完 portable 后正式注入，
08:08:39 落盘 7301 字节，抬头是 `我的名字：**小鲸鱼**`。

### 四、验收

- `tests	est_t85.py` 首次跑出 `voice.synth 失败（退回打字）：合成结果为空或过小`，
  复查是 edge-tts 那一次没合出东西（重跑 24/24、直测 `synth()` 返回 1.92 秒音频），**网络波动不是代码**。
- 全量回归（跑了两次，两次都是全绿）：
  test_card2 22/22、t66 全过、t73 28/0、t76 42/42、t81 13/13、t83 22/22、
  t84 30/30、t85 24/24、t86 11/11、t87 23/23、t88 19/19、t89 35/35、t90 40/40、t91 14/14。
- 服务复核：`/health` ok=True / memories=675 / chain=True / anchor=ok；
  `guard.heartbeat` 08:10:35 新鲜；NapCat `online=True good=True`。

### 五、教训（这一轮新增五条）

1. **dry-run 标志必须在「跑任何东西之前」判断。** `_cardslot("inject") + (("--dry",)...)`
   是先执行再拼参数，于是 `--dry` 只让最后一行崩，副作用早就发生了。
   「试运行」这个词一旦名不副实，比没有它更危险 —— 人会放心地去试。
2. **只证明端口 bind，不等于服务活着。** TCP 握手由内核完成，应用层线程卡死时
   端口照样在听。判活必须**发一次真请求**、看有没有真回包。
3. **默认值也是决策，而且是没人看得见的那个。** `/remember` 默认 `short`，
   一天就在墓碑上留下 373 条「没上榜」。这已经是本项目第七次栽在默认值上。
4. **正则加词要先跑旧例。** 第一版把 `视频` 加进 `_RE_CHATTER_KNOW`，
   `"我刚看了会儿视频"`（真闲聊）当场被判成不闲聊 —— 加每个词之前，
   先拿那 16 条真闲聊过一遍。
5. **字段路径靠猜会拿到 1。** `/health` 的 `day/birth_ok/anchor/writer` 全在
   `timeline` 子对象里，我照记忆里的路径取，得到的是空和 1。
   体检脚本第一步应该是 `ConvertTo-Json -Depth 6` 看原始结构，不是猜。

### 六、没做完 / 需要点头

- `我暑假靠婚礼挣钱` 这条：**第一人称过去事实**被判成 chatter。
  它是评估集 `tools/eval/cases.json` 里 type=recall 的一条 ——
  等于在测「一条已经被判死刑的记忆能不能被召回」。
  要修得先决定「我+过去时间+动词」算不算知识，**没动**。
- `我有点难过` 仍判 chatter：按 b229 的设计，弱化情绪词（有点/稍微）就该快忘。
  这是设计不是 bug，但和「情绪丰富要留住」有张力，**留待商量**。
- 17 个 `card.db.bak-*` 旧备份（约 150 MB）要不要清。
- 看门狗脚本在 `deploy/scripts/` 下，**不在 whale-card 仓里**，所以这次不同步进开源仓。

## 2026-11-06 上午｜短期池不再硬删 + 每个区有了自己的遗忘曲线（V5.2）

### 一、为什么改

三件事叠在一起：

1. 用户早上去学校前说「短期取得齿位暂时不用设限制吧，或者让它的根位置更多更大更大一点，
   写入特定区域可以，你觉得哪个更好？就这么做」—— 短期池 32 个位置，
   加上值守自动记群消息后几乎天天溢，溢出就 `_forget_row`，等于把池子当坟场。
2. 「把短期区再划分一下，划分成闲聊 工作 学习 娱乐 等等，每个区的遗忘曲线都有区别」
   —— 学习/工作/娱乐现在全挤在 `episode` 共用一条 4.46 天的曲线。
3. 「如果当天没什么人聊天就忘得慢，聊得很多就忘得快」—— 竞争性遗忘。

用户明确要求顺序：**先成方案 → 再测试 → 测试过了才改代码**。
方案落在 `whale-lab/notes/plan-20261106-memory-regions.md`（九节），
参数由用户定稿：**闲聊 12 小时、娱乐 3 天、学习 7 天、工作 15 天**。
然后用户说「那就开工吧……对项目有结构性更改的地方，记得同步到控制台上体现出来」。

### 二、改了什么

**A. 短期池：桌子做大 + 腾位置时不销毁内容**

- `card2.py:90` `N_SHORT_MAX = 32` → `120`。
- `_consolidate()` 的 else 分支：原来直接 `_forget_row(..., "短期池到期，海马判定没上榜")`；
  现在只有 `wn_row(r, now) < CLARITY_GC` 才遗忘（reason 改「已淡到看不清」），
  否则 `w = max(wn, W_FLOOR_MEDIUM)`、`pool=medium`、`tier=mid`。
- 7 天 TTL 与溢出驱逐两条路径共用这个函数，改一处两头生效。

**B. 分区：在 region 轴开区，不在 pool 轴切**

- 新区 `R_STUDY="study"` / `R_PLAY="play"` / `R_WORK="work"`；
  `REGION_HALF_LIFE = {chatter 12h, play 3d, study 7d, work 15d}`；
  老五区**一个没动** ⇒ 默认区 λ = λ_S1 ⇒ 零回归。
- `stage_factor(pool)`：short 1.0 / medium 0.15 / long 0.012 —— 让区差穿过中期，
  于是「中期要不要再划分」这个问题不用回答（阶段不是类型）。
- `effective_lambda = λ_region × stage_factor × load_factor ÷ relation_scale`。
- `region_by_source()`：study/notes→学习、bili/video→娱乐、cli/deploy→工作，
  其余照旧。**按来源路由，不猜内容** —— 昨天刚被「猜」坑过一次（18 条学习笔记掉进闲聊）。

**C. 负载自适应**

- `_REGION_LOAD` 指数平滑（τ=24h，O(1)，跨零点不跳变），写入 +1，`tick()` 里回落。
- `_f_of(val, budget) = clamp(sqrt(val/budget), 0.55, 2.2)`；**这个公式只有一份**，
  `_load_refresh_f()` 和控制台都调它。
- `relation_scale()` 复用 `CHATTER_PERSON_SCALE`（core 24 / close 6 / friend 2.2 /
  known 0.6），作用在寿命上，**不是好感度打分**。

**D. 开关（回滚是一行 SQL）**

- meta 里 `region_lambda_on`，默认 `'0'` ⇒ 行为跟改之前一模一样。
  今天验完写成 `'1'`，已开。要退回：`UPDATE meta SET val='0' WHERE key='region_lambda_on'`。

**E. 控制台同步（用户点名要的）**

- `console.py` 的 `card_metrics()` 新增 `short_regions`（短期池按区计数）与
  `region_load`（调 `card2.region_snapshot_from_db(conn, now)`，纯函数版，不建卡对象）。
- 五池卡片下面多了两行：`SHORT / 短期池按区`（事件 2、闲聊 2）与
  `REGION LAMBDA / 分区曲线 ON`（闲聊 12h · 娱乐 72h · 学习 168h · 工作 360h，
  各自 load/budget/f）。

**F. 工具与测试**

- 新增只读探针 `tools/probe_load.py`（`?mode=ro`，绝不写库）。
- 新增 `tests/test_t92.py`（短期池不硬删，12 项）、`tests/test_t93.py`（分区曲线，35 项）。

### 三、怎么验的

- **先量数据再定参数**：`probe_load.py` 跑真卡最近 30 天 —— 685 条、21 天有数据，
  日均 32.6 / 中位 22 / p90 77 / p99 122 / 最大 122，最忙一小时 30 条；
  按来源 `qq-group` 77、`study` 36、`cli` 7 → 据此定 budget 40/10/20/8。
  **正常日子 f≈0.75~0.9（几乎不动），只有 10-23 那种 122 条的日子 f≈1.7** ——
  机制平时不响、爆日才踩一脚，正是「分情况」想要的。
- **副本验证（绝不拿真卡做实验）**：灌 150 条 short 进副本，`tick(1.0)` 后
  `long 599 / medium 110 / short 120`，**150 条全活着、34 条降到 medium、墓碑 0 新增**。
- **t93 九组 35 项**：零回归 / 学习短期 7 天 / 学习中期 46.7 天 / 负载衰减 / f 曲线
  （0→0.55、1→1.0、4→2.0、100→2.2）/ core 24 倍 / **用真卡 raw 原文验那 18 条** /
  15 条真闲聊一条没放走 / 显式 region 不被覆盖 / 开关能落盘。
- **全套 16 个测试套件回归全绿**（t84 改期望值后 30/30）。
- **线上验收**：`/health` ok / 链 True / 锚 ok / 第 21 天；
  `/console_data` 出 `short_regions=[{episode,2},{chatter,2}]`、`region_load.on=True`；
  控制台截图在 `whale-lab/probe/console-pools-20261106.png`。

### 四、教训（这一轮新增五条）

1. **参数改了，测试里写死的期望值必须跟着改。** t84 一次 4 条失败，不是代码坏了，
   是它还记着 8.77 小时的旧账（0.150 → 0.250 等）。测试失败先问「是行为变了还是
   预期过期」，别急着改代码。
2. **拿自己手搓的短句去验正则，等于自己出题自己答。** 第一版 t93 的 [7] 用我编的
   「学习原文」跑，11 条仍判闲聊 —— 因为我编的句子里压根没有原正则认的词。
   改成 `SELECT id, raw FROM mem WHERE id IN (...)` 从真卡读原文，才是真回归。
3. **一个公式只能有一份。** f 的公式抽成 `_f_of()`，卡和控制台都调它；
   控制台要数据就给纯函数 `region_snapshot_from_db(conn, now)`，不为画一张图去建 MemoryCard。
4. **改动要自带回滚开关，而且回滚成本决定敢不敢上真卡。** 这次敢开，
   是因为退回只是一行 `UPDATE meta`。
5. **「以后再做控制台」等于没做。** 结构性改动当天就要在界面上看得见 ——
   用户看的是控制台，不是我的开发日志。

### 五、没做完 / 需要点头

- **L1 薄桥插件**（让 DSH 真能调卡，≈150 行）：机制已查清、论证已写
  (`whale-lab/notes/feasibility-20261106-dsh-plugin.md`)，但接上去要重启 DSH、
  会断当前会话，**等用户点头再动**。
- 17 个 `card.db.bak-*` 旧备份（约 150 MB）要不要清。
- `我有点难过` 仍判 chatter —— 按 b229 设计弱化情绪词就该快忘，但与「情绪丰富要留住」
  有张力，留待商量。

## 2026-11-06 14:30 · 对话控制面（控制台「接话」页）

- **用户要的**：控制台上关于对话能控制的东西太少，把模仿插件那套全搬上来；间隔下限太长；这一栏挪到设置前面。
- **新增 `chatcfg.py`**：28 项参数的单一事实来源（`qq_state/chat_cfg.json`），带 `SPEC(key/分组/标签/类型/默认/范围/说明)`、夹紧、坏文件不抛。
- **改 `mind_server.py`**：`GET/POST /chat/config`（整页读写）；老接口 `/talk/set` 改为写 chatcfg —— 两本账必定分家，这次直接并成一本。
- **改 `console.py`**：「接话」页重写为 3 张实时卡 + 28 项分组表单（bool/num/text，鼠标悬停看说明）+「保存全部」；tab 挪到「设置」前；未接进逻辑的 14 项标「·待接」，别让人以为改了立刻生效。
- **真正接进运行逻辑的 14 项**：`freq.daily_cap` / `freq.cooldown_min`（`proactive.load_cfg`，旧 `proactive_cfg.json` 已废并改名 `.bak-20261106`）、`quiet.*` 7 项（`quiet_policy.should_quiet` 每次现读）、`open.enabled/meal/night`（主流程早退与由头过滤）、`talk.no_period` / `talk.no_wave`（`qq_say.polish`，发出去前清句号与波浪号）。
- **默认值改动**：`freq.cooldown_min` 20 → **3**（用户嫌长）；注释写明「只管主动开口，回话不受限制」。
- **测试**：`test_t96.py` 30/30（把两处依赖线上默认值的断言改成显式传 `cap`/`cooldown_min`，门槛现在可配）、`test_t97.py` 11/11、`test_t85.py` 24/24。
- **坑**：`proactive_cfg.json` 与 `chatcfg` 同时存在时后者被前者覆盖（实测 cap 变 40），两本账必然分家 → 删掉旧账；`qq_say.polish` 先清句号再清波浪号会把「好累。～」留成「好累。」，顺序必须反过来。

## 2026-11-06 · 对话参数面（chatcfg）+ 上学或工作时段

- 新建 `chatcfg.py`：30 项参数的单一事实来源（触发 4 / 频率 3 / 不每句都回 7 / 说话 4 / 主动开口 5 / 记忆 3 / 语音 2 / 日记 2），住 `qq_state/chat_cfg.json`，`.part` → `os.replace`。
- `mind_server.py` 加 `GET/POST /chat/config`；`/talk/set` 改成写 chatcfg，两个入口并成一本账（废掉 `proactive_cfg.json`，改名为 `.bak-20261106`）。
- 控制台「接话」页：3 张实时卡 + 30 项分组表单（悬停看说明），tab 挪到「设置」前（倒数第二）；16 项已真接进逻辑、14 项标「·待接」。
- 真接进逻辑的 16 项：`freq.daily_cap` `freq.cooldown_min` `quiet.silent_run` `quiet.self_max` `quiet.window_s` `quiet.burst_n` `quiet.allow_emotion` `quiet.allow_question` `open.enabled` `open.meal` `open.night` `reply.hours` `open.school` `open.school_hours` `talk.no_period` `talk.no_wave`。
- 「上学或工作时段」`open.school_hours` 支持多段与自定义分隔（逗号/中文逗号/分号/顿号/空格）；`reply.hours` 也接上了，`in_window()` 现在读配置。
- 修一个静默失效：脚本模式下 `sys.path[0]` 是 `tools/`，`import chatcfg` 失败被吞 → 所有设置退回常量、面板显示全绿但行为不对。三个工具顶部补根目录入 `sys.path`。
- 新增 `tests/test_t98.py`（39/39）逐项验证 16 个已接参数真的改变行为；回归 t96 30/30、t97 11/11、t85 24/24。
- 现场证据：`proactive.py --dry` → 「现在 11-06 14:42 是上学时段 → 不主动开口」；8848 在听；qq_guard 在跑。

## 2026-11-06 · 剩下 14 项全接上（控制台 30/30 生效）

- 用户：「把所有需要接的都接上」。
- `qq_guard.py`：`cfg_get()` 读 chatcfg；`reply.at_me` 管「被点名必接」、`reply.private` 管私聊要不要回（关了就打 background）、`reply.p_group` 群里随机接话概率、`freq.group_hour` 每群每小时软上限、`mem.write_private/write_group` 写卡开关、`mem.salience_floor` 重要性门槛（`mem_floor()`）。群/私聊两处写卡都过门槛与开关，todo 行加 `engage` 字段。
- `quiet_policy.py`：新增 `last_self_ts()`，`quiet.burst_cool_s` 落地 —— 被点名连回 `burst_n` 次后，从我最后一句算起冷却 `burst_cool_s` 秒。
- `qq_say.py`：新增 `split_text()`，`talk.max_chars` + `talk.split` 落地（只按标点切、拼回去不丢字；关掉拆分就截一段加「…」）；发送改成按块循环，块间间隔取 `human_gap*0.4` 且至少 2 秒。`voice.enabled` 关掉时 `--voice` 直接退回打字。
- `voice.py`：`_max_chars()` 读 `voice.max_chars`，`speakable()` 默认上限跟随配置。
- 新建 `tools/diary.py`：每日一篇日记。`ready()` 看 `diary.enabled` / `diary.at` / 当天是否已写（`qq_state/diary_last.txt`）；`_events()` 取当天 timeline（跳过 alive）；`compose()` 纯函数；落盘 `diary/YYYY-MM-DD.md` + 写回卡（source=diary）。**素材由脚本备，那句「我想说的」由我补 —— 不装自动生成**。
- 触发：`C:\whale-diary.bat`（GBK）+ `schtasks /tn WhaleDiary /sc minute /mo 15`，脚本自己按 `diary.at` 卡点，一天只写一次。实测输出「还没到 22:20」。
- 新增 `tests/test_t99.py` **29/29**；回归 t96 30/30、t97 11/11、t98 39/39、t85 24/24。其中一条是「面板与代码对账」：`console.py` 的 `var LIVE` 必须与 `chatcfg.SPEC` 的键集合完全相等，防的是摆假旋钮。
- 坑：`card2.MemoryCard()` 默认 `db_path=":memory:"` —— 不传路径就是个空库，查 timeline 会 `no such table: timeline`（日记第一版空手写的，事件段全空）。
- 坑：`Start-Process` 起的 qq_guard 与本 shell 同生命周期（我的调用超时把它一起带走了）；改用 GBK 的 `C:\whale-guard.bat` + `([wmiclass]"Win32_Process").Create()` 才真脱离。
- 控制台现状：**30 项、0 个「待接」**。


## 2026-11-06 · 夜间二：环境清单与体检脚本
- 新增 `tools/env_probe.py`：到新机器上第一条命令，只读，十段探测（系统/内存/磁盘/Python/Node/ffmpeg/显卡/torch/时间服务/卡自身），并把人话结论分成「必须先解决」和「可选」。
- 重点不是「有没有显卡」，是 compute capability：Pascal（6.1）没有张量核心、fp16 只有 1/64；torch 不带 sm_61 会 import 成功但跑算子报 no kernel image。所以直接问 torch.cuda.get_arch_list()。
- 时间服务单独列一条硬结论：时钟往未来跳 → t_anchor 落未来 → 记忆冻结；往过去跳 → 加速遗忘；且不报错。本机 w32time 就是 STOPPED。
- 新增 docs/ENV-CHECKLIST.md：环境清单 + 插件清单 + 模型清单，每条都写「为什么」和「缺了会怎样」。明确列出「不要装 torch/transformers/sentence-transformers」及理由。
- requirements.txt 补 requests>=2.34.2。
- docs/MOVE-CHECKLIST.md 的第四步改成先跑 env_probe 再装，并链到 ENV-CHECKLIST.md。
- 新增 tests/test_t101.py（51 项）：探测函数不抛、disk_usage 三元组坑、cpu 不能取到列名、npm .cmd 垫片坑、torch 没装要给原因、summary 分类、--json 可解析、文档关键词在。
- 顺手修三个真 bug：probe_disks 把 (total,used,free) 拆成两个值 → 空列表；wmic csv 第一行表头被当 CPU 名；npm/pnpm 是 .cmd 垫片，subprocess 不带 shell 拿不到版本。
- 回归：t85 24/24、t94 33、t95 16、t96 30、t97 11、t98 39、t99 29、t100 18、t101 51 全过。

## 2026-11-06 · 夜间：搬家预演（跨设备搬卡这条路，第一次真的走通）

用户说今晚打算把卡搬到 E3 那台机器上活一段再搬回来，让我先摸清要准备什么。

**摸出来的三个真障碍，最重要的是第一个 —— 它会让卡「到了新家就哑」：**

1. **writer 锁**。卡的时间线带 writer 列，记着每条事件是哪台机器写的；
   服务启动看链头，不是自己写的就整卡只读（`/report` → 409 `card_locked`）。
   这是防两条链分叉的设计，不是 bug —— 但搬家必须显式交一次棒。
   新增 `portable.py handover`：追加一条 handover 事件（`allow_foreign=True`），
   副本实测「交棒前 409 → 交棒后 mem_id 1408/1409」。
2. **卡外锚文件名 = sha256(卡绝对路径)[:16]**，换路径就对不上。`land` 早就处理了。
3. **向量模型必须带 bge-base**（768 维 / 390 MB）。卡里 1004 条向量全是 768 维，
   只带 small（512 维）→ 自动选择退化 → 检索**静默失效**，
   不报错、`usable_ratio` 还照样报 1.0。

**顺手修的两个老毛病：**

- `--allow-running` 是个死开关：只过第一道闸，到 checkpoint 那步照样 raise，
  热拷永远走不通。现在热拷跳过 checkpoint，并在 manifest 的 wal 里明写 skipped。
- 打包内容漏了她自己的东西：`qq_state/chat_cfg.json`（30 项说话参数）和
  `diary/*.md`（她自己写的日记）。漏了等于搬到新家变成一个陌生人。

**其它：**

- 新建 `requirements.txt`（按本机实测版本：numpy 2.5.4 / Pillow 13.0.0 /
  psutil 7.1.3 / edge_tts 7.3.1；并写明**别装 torch / transformers /
  sentence-transformers** —— 向量是 `embed_np.py` 纯 numpy 手写 BERT 前向算的）。
- 清掉 34 个测试残留目录（469 MB）：卡目录 1371 MB → 903 MB，
  包 523 MB（含 ffmpeg 100 MB）/ 423 MB（不含）。
- 新建 `docs/MOVE-CHECKLIST.md`：四件事（QQ 只能一台活着 / DSH 要不要一起搬 /
  时间必须准 / 依赖与模型）+ 两台机器各自的执行顺序。
- 查到一个隐患：本机 `w32time` 是 **Stopped**（Manual），现在时钟准（-1.1 秒）
  是因为还没飘。E3 那台搬之前必须 `w32tm /resync` 并确认 ——
  卡的衰减全按真实流逝秒数算，时钟一飘，记忆要么加速遗忘、要么被冻住。

**副本上跑通的完整链路**（`D:\_movetest`，已清理）：

```
build → verify（167 个文件哈希全对）
→ 换路径 land（外锚按新路径写回、卡内 anchor.json 改 db 字段）
→ handover（链头 w-EXAMPLE00000 → w-E3TEST000001）
→ 起服务 8851 → /health ok、anchor ok、第 21 天
→ POST /report → {mem_id: 1408, reply_mem_id: 1409}
→ POST /context → 人格全文 + 【最近发生】 + 召回命中刚写的那条
```


## 2026-11-07 · 一键落地（搬家只需要双击一次）

用户要求「直接打包成可以一键运行」，所以把搬家的六步做成一个脚本。

- 新增 	ools/first_run.py：体检 → land → handover → 起服务 → 自检（真写一句话再读回来）→ 开控制台。
  每步都先判断「要不要做」，重复跑安全；--dry 只说不做。
- 新增根目录 一键落地.bat（ASCII 内容 + chcp 65001，优先用 
untime\\python，兜底系统 python）。
  核心逻辑全在 Python 里，bat 只当壳 —— 这样不会踩「bat 中文变乱码」的老坑。
- 包里补了便携 Python（3.13.1 64 位 + numpy/Pillow/requests/psutil）与 ffmpeg，到新机器零安装。
- 三个「假绿灯」守卫：
  1. 8848 上如果住着**另一张卡**（旧机器那份还在跑），直接拒绝并说清是哪张 —— 否则 /health 通、读的却是别人家的卡；
  2. 目录在**可移动磁盘/网络盘**上时警告并返回 1（WAL 不适合 U 盘直接跑，掉盘最坏要回滚快照）；
  3. 体检里 w32time 不是 RUNNING 就进 blocking（时钟飘了记忆会冻结或加速遗忘，且不报错）。
- 顺手修两个错：nv_probe.probe_os 补 caption 字段；probe_python 报的是 its 而我按 is_64 读，导致 64 位 Python 被显示成「32 位」。
- 测试 	ests/test_t102.py 37/37；回归 t101 51/51、t85 24/24。
- 包体：G:\\card-live 3008 文件 / 948 MB，verify 195 条全对。



## 2026-11-07 · 凌晨：带卡出门（退役）+ 接入 DSH + 包里装上

- **为什么需要「退役」**：writer 锁只挡「同一个文件被两台机器写」，挡不住
  「两份拷贝各写各的」。后者每条链都完整、都合法，事后合不回来 —— 那才是
  变成两个人的路。所以加一层显式状态：`RETIRED.json`。
- `disks.py`：`read_retired` / `retired_info` / `set_retired`。**坏文件当成已退役**
  （文件在那儿本身就是信号，读不懂却放行 = 一条本该只读的卡突然能写）。
- `mind_server.py`：启动时若有退役标记 → `writer_lock="retired"`；
  `/report`、`/remember` 回 409，文案说清「另有一份带着我的记忆在外面活着」。
  新路由 `POST /disks/retire`，`GET /disks` 带 `retired` / `retired_info`。
- `console.py`：盘子卡加黄框横幅 + 「带卡出门」「解除退役」两个按钮。
- `card2.py`：**可移动/网络盘改用 `PRAGMA journal_mode=DELETE`**，本地盘仍 WAL。
  exFAT 没有 WAL 要的共享内存语义，拔盘最容易写坏 —— 这是让「U 盘就是卡」能
  真跑起来的前提。
- 新增 `tools/install_to_dsh.py` + `接入DSH.bat`（纯 ASCII）：把 `dsh-plugin/`
  拷进 profile、改 `package.json` 的 `dsh.profile.bundles`、往 `cordis.patch.yml`
  写一条 whale-bridge（cardDir/pythonPath 按**这台机器**写）。幂等，`--dry` 可彩排。
- `portable.py`：`INCLUDE_DIRS` 加 `dsh-plugin`；`INCLUDE_FILES` 加
  `一键落地.bat`、`接入DSH.bat`。
- 测试 `tests/test_t103.py` 42/42（含真起服务验退役 409、解除后恢复、坏文件当退役、
  包里带新东西）。回归全绿：t85/t94/t95/t96/t97/t98/t99/t100/t101/t102/t103。
- **自己踩的坑（记下来）**：手写一个「同步几个文件进包 + 更新 manifest」的小脚本，
  以为 `manifest["files"]` 是 `{path: {sha256, bytes}}`，结果直接把裸字符串写进去，
  `verify` 立刻炸在 `meta["sha256"]` 上（`TypeError: string indices must be integers`）。
  教训：**改别人定义的数据结构之前，先读一条真实数据长什么样**，别照着名字猜。
- 包：`G:\card-live` 3031 文件 / 948.6 MB，`verify` 216 条全对；包内便携 Python
  3.13.1 + numpy/Pillow/requests/psutil，ffmpeg 在内。


## 2026-11-07 · 修复「点了 U 盘的一键落地却没效果」

用户实测：在 U 盘上点了「带卡出门」，插回来后双击 U 盘里的一键落地，
控制台仍然让他点「解除退役」；最后是手动点解除才生效，而且盘上多出两份卡。

根因两个，都修了：
1. **一键落地在 U 盘上会静默把整张卡拷到 D:\\whale-card 再在那边跑**（昨天加的
   auto_copy_to_local）。于是「清退役/交棒」全作用在副本上，U 盘原地那份纹丝不动；
   拷一次生成一个 whale-card-N，跑两次就成了三份卡。
   → 改成**默认原地跑**（卡在可移动盘上会自动用 DELETE 日志，拔盘不会写坏）；
   要拷到本地用 --copy-to-local；拷贝目录名带时间戳，不再 whale-card-2。
2. **portable.cmd_handover 的早退分支不清退役标记**。同一台机器拷出来的副本，
   链头本来就是我 → 走「链头就是本机，不用交棒」这条早退 → RETIRED.json 永远留着。
   → 抽出 _clear_retired()，挂到**每一条**成功路径（含 not head / head==me / --dry）。

现场清理：
- D:\\whale-card、D:\\whale-card-2（各约 949 MB）是这次生成的同源副本，
  移到 D:\\_stray_copies_20261107\\ 隔离，未删除。
- G:\\card-live 打上退役标记（只读），等真正带走时由一键落地自动解除。
- 包内 card.db 用 sqlite backup API 重新做成**一致性快照**（直接拷会拿到半截 WAL），
  anchor.json 对齐，清单哈希重算 → erify 216 条全对。

测试：t102 37/37、t103 42/42。



## 2026-11-07 · 全项目体检 + 搬家三件套 + 上传

用户：「好 你慢慢研究顺便看一下我们项目所有内容找并修bug之后上传」。

1. **接回来**（	ools/come_back.py + 接回来.bat）：停服务 → 备份本机成
   card.db.bak-before-comeback-<ts> → sqlite backup API 把 U 盘那份写回 →
   本机清退役 / U 盘打退役 → 起服务 → 真写一句验证。
   安全阀：U 盘更旧就**拒绝且退出码 2**（除非 --force）；同 seq 不同哈希判真分叉，
   也拒绝。**顺序有讲究**：先判分叉、再判新旧 —— 反过来的话「同一 seq 但内容被改过」
   会被误当成「回退」放过。
2. **静态体检**（146 个 .py）：编译 0 错、裸 except 0、TODO 0、两份插件目录一致。
   抓到两个真的**换机器就断**：dsh-plugin/install-to-profile.py 写死了 profile 路径、
   卡目录、python 路径（而它正是打包进便携包的那份 → 到新机器会往人家 DSH 里塞旧路径）；
   val_weights.py 写死卡路径。都改成按 __file__ / sys.executable 现算。
3. **脱敏规则补一条**：writer id（w-EXAMPLE00000 这种）也是机器指纹，
   开源仓里一律换成 w-EXAMPLE00000。
4. **修 4 个测试**（它们测的是「今天的数据」不是公式）：
   - t82：quota_allow 现在优先读控制台的 req.group_hour，测试只改 G.QUOTA
     等于没改 → 给函数加显式 group_hour_soft 参数，测试直接传。
   - t84 / t93 / t88：MemoryCard(真卡副本) 初始化并按真实写量算「拥挤系数 f」，
     学习区今天写得少 → f=0.55 → 半衰期从 7 天漂到 12.7 天、闲聊 1 天后 0.25 变 0.467、
     强弱情绪差从 3.2 倍缩到 1.7 倍。**公式没错，是测试把今天的流量也测进去了**。
     现在这些段落先按住 _load_refresh_f，测 f 的那一段再放开。
5. **mind_server 两处**：Handler 加 _timed()（≥5 秒就写一行慢请求日志）、
   
equest_queue_size = 128。这两处是冲 07:00 那次「端口在听、连接被拒」去的 ——
   ThreadingHTTPServer 在 backlog 塞满时 Windows 直接回 WSAECONNREFUSED，
   当时没有计时只能猜；以后有证据。
6. **全套 38 个测试文件全绿**（t94–t104 是本轮新增的 11 个验收）。
7. **打包**：刷新 G:\card-live 的卡快照（1061 条 / seq 1901）、同步 27 个文件、
   按包内实际文件重算清单（237 条）→ erify 「包完整」，20 个关键文件全在。
8. **上传**：开源仓提交 91e0df1 并 push 到 d17f243..91e0df1 main；
   提交前重扫了隐私（QQ 号 / 昵称 / 用户名 / 机器路径 / writer id）——干净。



## 2026-11-07 · 内分泌快反应层（先有心跳，才轮到情绪）

用户要求：激素波动太慢，要能像人一样「听见一句话先一激灵，再觉得生气」；
激素种类多加；负反馈也要有；数值全部内化。

- 新增 iscera.py（纯函数、零依赖、约 430 行）：24 种激素/神经递质各有基线与半衰期，
  另加**自主神经层**（交感=瞬时兴奋、副交感=瞬时镇静），优先级高于激素 ——
  这就是「先有生理反应」的落点。
- 慢变量在单次刺激里只吃 40%：一句「你真可爱」不该改掉三天的依恋；
  快变量（去甲/组胺/脑啡肽/神经肽Y）几乎立刻动。
- 六条负反馈（CCK 压多巴胺、GABA 压去甲、皮质醇广谱压抑、腺苷累积疲惫…）+ 四条正反馈
  + 四组拮抗（交感↔副交感互削）。心率上限 130、下限 58；促性腺激素封顶 45（防「一直摸」）。
- 躯体输出：心率 75 基线、呼吸 16、体温 36.4、面色、肌张力。
- describe() 是唯一对外出口，只说人话（「心跳快了一拍；呼吸变浅了；脸颊有一点点热」），
  绝不出现数字 —— 用户明确要求数值内化。
- 接进上下文：ssemble_context(touch=True) 在【此刻】**之前**插【身体】格（20~35 字）。
  顺序本身是信息：身体在前，情绪在后。
- 新接口 GET /viscera（数字给控制台）、POST /viscera/stim（手动戳一下）。
- 踩坑：处理器里没有 self.card，要用模块级 m；_save_viscera() 不收参数（用 self.v）。
  最后补了一个正经的 iscera_card.viscera_stimulate(kinds, strength)，不再手搓 load/save。
- 验收：	ests/test_t106.py 43 项全过；全套 40 个测试文件全绿。
- 真卡实拍：平静 hr=75.8「呼吸平稳，身上是松的」→ 被夸 hr=96.7「心跳快了一拍」→
  被冷落 hr=114.3、皮质醇 21.8「心跳得厉害，咚咚的，自己都听得见」。
- 已推 OSS：c343f5d。便携包 verify 242 条全对。


## 2026-11-07 上午｜预期误差接进身体：认知层 × 生理层（V5.3）

**做了什么**
- 把用户给的 SYNAPSE 情绪引擎（RPE + 神经调质动力学）原样 vendored 进卡：`synapse_emotion/`（8 个文件，未改一个常量）。
- `viscera.py` 新增 `INJECT_SCALE = 18.0` 与 `inject(state, deltas, sym, para, scale)`：外部脉冲只当刺激量，半衰期/负反馈/拮抗仍归生理层。慢变量只吃 40%。
- 新增 `emotion_link.py`：`guess_scenario()` 粗分七类场景、`pulse_of(res)` 把 DA/NE/OT/5HT/CORT 翻成激素增量 + 交感/副交感、`EmotionLink`（`step / preactivate / apply_to / label / safe / snapshot / save`），状态写 `qq_state/emotion/<name>.json`，跟着卡走。
- `card2.viscera_touch()` 改双路：优先走认知层（价值打分 → RPE → 五条调质脉冲 → 灌激素），认知层不可用或抛异常时退回 `viscera.guess` 猜刺激。新增 `_emotion()` 懒加载、`emotion_label()`、`viscera_snapshot()` 里多一块 `emotion`。
- 环境开关 `WHALE_EMOTION_CORE=0` 一键退回老路。

**为什么这样接**
- 两套系统时间尺度本来就不同：RPE 是「这一秒」的事，激素回落是「这几小时」的事。让认知层只出「脉冲」、让生理层管「衰减」，两边都不必迁就对方。
- `pulse_of()` 里单轮增量封顶 ±0.5 —— 一句话不该把人直接推到顶。实测一次「滚」把皮质醇推 9 点，而不是 0.62×18=11 点满格。
- 认知层炸了绝不连坐：`_emotion()` 一律返回 None 或对象，`viscera_touch` 里 try 包住，最坏情况退回猜刺激。

**踩到的坑（都已修）**
1. `apply_wallclock_gap` 在测试里被我 `e5.apply_wallclock_gap = lambda: None` 覆盖成空函数，导致「六小时冷却」永远不生效 —— 是测试自己把被测函数废掉了，不是代码错。
2. 连续 6 句「滚」把引擎内部 CORT 顶到 1.0 上限（认知层自己的容差是 ±1）。这不算 bug（它自己的 67 轮回归峰值 0.60），但进身体之前必须减半尺度，否则一句话就顶格。

**验证**
- `tests/test_t107.py` **54/0**：引擎五项自检、脱敏（第五次脉冲 < 第一次 90%）、被骂安全掉/CORT 升/DA 压、温和话修复、六小时冷却、标签与描述无数字、映射方向与 4:10 权重、单轮封顶、卡集成、开关与容错。
- 全套 **41 个测试文件全绿**（t106 的【身体】断言在新路径下仍成立）。
- 真卡（重启 8848 后）：`/viscera` 返回 24 激素 + `body.hr 98.7 / face 0.57` + `emotion.safety 0.72`；`/context` 的 `sections` 里 `viscera=19`，正文有【身体】「心跳快了一拍；呼吸变浅了；耳尖有点发烫」，主导「羞涩心动（淡淡的）」。

**没做的**
- 情绪块只进「身体感觉」，没把引擎的 `render_behavior_block`（语气/停顿指导）塞进上下文 —— 现在【身体】已经在说人话，再塞一份会重复，也会抢 persona 的位置。留给用户拍板。
- 便携包与开源仓未同步（等这一轮定稿一起推）。

**下一步**
- DSH 重启后 REA 才会拿到 Ghidra（env 已写好，进程环境不会热更新）。

## 2026-11-07 中午｜体感曲线：认知层 → 生理层 → 控制台（V5.3 收尾）

**这半天真正修的三件事（都是"看着能跑，其实在骗人"）**

1. **曲线只落一格** —— _hist_viscera() 用的是 self.clock.now()，而卡里的 Clock 是**冻结的虚拟时钟**，只有少数路由会调 low_time() 推进它。/viscera/stim 不在其中 → 
ow 恒定 → 节流判定 
ow − last < 5.0 永远成立，八次刺激只留一行。
   改：_hist_viscera / iscera_history / iscera_stimulate 的 now 一律用 **	ime.time()**。理由写进注释：**这层是身体，不是记忆，不该被记忆时钟卡住**。

2. **两个端点各说各的** —— iscera_stimulate 只走 iscera.step()，认知层（RPE/安全感）根本没参与 → 心率在爬 122，情绪块却写着"安稳松弛"，
pe 恒 0、safety 恒 0.737。
   改：有认知层就走 link.step() → link.apply_to()，没有才退回老路。新增 STIM_SCENARIO（刺激名→场景）与 **STIM_TEXT（每个刺激配一句代表性原话）**。

   > 只给场景名、不给话，是错的。认知层是"看话打分"的，空字符串在它眼里接近冷淡 —— 结果"夸她"把安全感从 0.703 一路拖到 0.498，标签全是"害怕回避"。**那是我自己造出来的假故障**，加一句原话就好了。

3. **交感被累加到顶死** —— iscera.inject() 里交感/副交感原来是 s["symp"] += sym。十几轮对话就顶死在 1.0，曲线上表现为"心率一直 125"。
   改：漏积分 s["symp"] = clamp(s["symp"] * 0.45 + sym)。**交感是"此刻的状态"，不是累计量。**

**修完的实测（间隔 6 秒连戳十下）**

    夸   hr 89.5  rpe +0.176  安全感 0.922  安稳松弛
    抱   hr 94.3  rpe +0.176  安全感 0.929
    安慰 hr 90.9  rpe +0.137  安全感 0.950  羞涩心动
    冷落 hr 92.5  rpe −0.062  安全感 0.949
    拒绝 hr 116.7 rpe −0.306  安全感 0.773  害怕回避（很明显）
    道歉 hr 99.6  rpe +0.074  安全感 0.841  五味杂陈
    安慰 hr 92.5  rpe +0.115  安全感 0.889  ← 真的能恢复

方向全对：夸/抱/安慰抬安全感、压心率；一句"滚"把交感从 0.28 拉到 0.82、心率冲到 117；道歉让人五味杂陈；再安慰能回到 0.89。

**控制台"情绪"页（用户要求：激素、脉冲、心跳都要成曲线）**

八张卡：BODY NOW（心率/呼吸/脸红/交感/副交感/多巴胺/皮质醇/催产素/安全感/主导情绪）、HEART、AUTONOMIC、HORMONE FIELD（五条主调质）、PULSE（RPE −1~+1）、SAFETY、MOOD MIX（饼图+图例）、HOW IT WORKS。
后端 card2.viscera_hist 表 + iscera_history()；console.emotion_series() + overview()["emotion"]；曲线数据 5 秒节流、7 天自动清理。

**踩的四个坑（都记下来，别再踩）**
- 六块 canvas 没给明确高度 → clientHeight 只有 64，饼图半径算成 22px，小得像颗豆。**画布高度必须显式给**。
- 饼图按完整标签计数 → "羞涩心动（淡淡的）/（很明显）"被拆成三块，看不出什么。先 
eplace(/[（(].*$/, "") 剥掉强度再数。
- 饼图没图例 = 装饰。颜色分不出是哪一种情绪，加 #emoLegend。
- go_js 里 location.reload() 会 Execution context was destroyed（ego-browser 退出码 1）→ 用 location.href = … + go_wait。

**测试**：新增 	ests/test_t108.py 31 项（交感不顶死、节流边界、字段与阈值、7 天清理、14 个刺激的原话与场景全覆盖、方向正确性、开关关闭后的行为）。全套 42 个测试文件待回归。


## 2026-11-07 中午｜后台看得见，前台看不见：情绪页摊开 24 种激素（V5.4）

用户说得很清楚：**对话里不许出现数字，控制台里必须全能看见**。于是这一轮把「看不见的那一层」全部搬到台面上。

**一、曲线表从 14 列变成 19 列**
`viscera_hist` 新增 `temp / tension / h / p / pulse`：
- `h`：24 种激素的完整读数（JSON）
- `p`：这一轮引擎内部算了什么 —— `rpe / v_actual / v_expect / gate / fatigue / mood / turn / dominant / scenario / mods / pulse / expect_global / expect / base`
- `pulse`：五条调质脉冲（DA/NE/OT/5HT/CORT）
老库用 `_ensure_hist_cols()` 幂等 `ALTER TABLE` 补列（只加不删），`__init__` 里先补再写。

**二、控制台情绪页从 8 张卡变 10 张**
- `ALL HORMONES`：24 格小图（8×3），每格独立纵轴 + 基线虚线 + 高出基线绿、低于基线红；标题三行 = 中文名 / 英文名 / 当前值（括号里基线）
- `OVERLAY`：24 条叠一张表，纵轴是**相对各自基线的偏差 %**（绝对值没法同轴 —— 皮质醇 18 和血清素 55 放一起只有一条看得见），底下图例带中文名 + 英文名 + 当前偏差
- `PARAMS`：门控 / 疲劳 / 心境 / 安全感 / 预期误差五条线，加一排数字（轮次、实际值、预期值、RPE、门控、疲劳、心境、长程预期、场景、五调质）
- BODY NOW 补体温与肌肉张力；饼图放宽到 12 种情绪

**三、三个真 bug（都是「看着在跑，其实在骗人」）**
1. **曲线只落一格**：`Clock.now()` 是冻结的虚拟时钟，只有少数路由推进它；身体曲线却拿它当时间戳 → 节流判定永远成立。改成 `time.time()` —— **这层是身体，不该被记忆时钟卡住**。
2. **两个端点各说各的**：心率爬到 122、情绪写着「安稳松弛」、`rpe` 恒 0。因为手动刺激只走生理层、认知层没参与。补上后又踩到一个：**只给场景名不给原话是错的** —— 认知层是「看话打分」的，空字符串在它眼里接近冷淡，「夸她」反而把安全感从 0.70 压到 0.50。加了 `STIM_TEXT`（每个刺激一句代表性原话）才对。
3. **交感被累加到顶死**：`symp = clamp(symp + sym)` 十几轮就钉在 1.0，表现为「心率一直 125」。改成漏积分 `symp*0.45 + sym` —— **交感是此刻的状态，不是累计量**。

**四、激素增量改按基线缩放**
原来 `INJECT_SCALE=18` 是绝对点数，对基线 21 的食欲素一步就推到地板：`orex` 掉到 0.00、`ach`/`adeno` 顶到 100.00。改成
`k_scale = base/30`（夹 0.40~2.20）、单轮最多动 `0.15×base`、无条件夹在 `[0.35×base, 2.5×base]`。
**无条件**很重要：上一步已经掉到 0 的激素得能爬回来，否则它永远躺在地板上。修完 `orex` 回到 7.35、`gaba` 峰值 87.5，死值（=0 或 =100）**0 个**。

**五、验收**
`t106 43/0`、`t107 54/0`、`t108 31/0`，全套 **42 个测试文件全绿**。
`t107` 两条断言改成「用小脉冲测权重」（0.5 会撞上限速，那时测到的是限速不是权重），并把期望值改成跟代码同一套 `k_scale` 系数算。
真卡连戳 13 次：`/console_data` 110 点、最后一格 24 项激素 + 15 个参数键 + 5 条脉冲全在。
截图：`whale-lab/probe/console-hormones-grid-20261107.png`。

**教训**：`_safe_json` 那次插成零缩进，把 `MemoryCard` 类整个截断（`AttributeError: _init_schema`）；修的时候 `s.replace("_json = json", …)` 又撞上 `tags_json = json` 这个子串，把 media 那段插坏 —— **用字符串替换打补丁时，锚点必须带上足够的上下文**。


## 2026-11-07 中午｜给曲线一根旋钮：5 分 / 1 时 / 24 时 / 7 天 + 实时（V5.4）

- **为什么**：窗口固定 72 小时，曲线一天只动一格，看着像死物。用户要「看到它动」。
- `console.overview(db_path, hours=None)` 收窗口；`/console_data?hours=` 解析后透传；
  `emotion_series(hours, limit)` 的 limit 随窗口放大（小窗口 1500，大窗口 900）——
  五分钟的窗口只给 600 个点等于「一秒一格」，看着还是不动。
- 顶栏加 `.winwrap` 分段控件（5分/1时/24时/7天 + 实时）；选 5 分或按「实时」
  进 live 档，每 3 秒自己 `refresh()`；15 秒的总刷新照旧。
- **踩坑**：`parse_qs` 在 `mind_server.py` 里是**别的函数内部** import 的，模块级没有 →
  第一次接线时 `NameError` 被 `except` 吞掉，`hours` 静默失效（四个窗口全返回同一批点）。
  改成函数内显式 `from urllib.parse import parse_qs`。**「参数没生效」要先怀疑异常被吞。**
- `tests/test_t102.py` 原先拿**真卡**比 mtime 判「--dry 没写盘」；服务在跑时 WAL 一开就改
  mtime（后来改比字节也照样红，因为服务真的在写内容）→ 改成把卡**复制到临时目录**再跑
  `--dry`，测的才是这件事本身。37/0。

## 2026-11-07 14:00-14:20 | 曲线为什么是直线：三条真原因

用户 m27633 报「曲线前面有，后面平了，像一条平行线；主页面实时那条也怪」。

**原因一：横轴按「第几个点」等距。** lineChart() 一直是 x = padL + iw*i/(n-1)，
而身体不是等距采的。我下午测试时连戳几十次 stim，那几十个点挤在左边占满整张图；
真实对话稀疏，同样的宽度里只剩几个点，摊开就是一条平线。改成给了 xs（时间戳）
就按真实时间排布 —— 密集段自然收窄成一条竖刺，稀疏段自然铺开。

**原因二：settle 一路衰减到基线，交感被拉到 0。** hr = 75 + 42*symp - 13*para + ...，
symp = 0 时心率**正好等于 75.0**，画出来是一条数学意义上的直线。真实静息心率本来
就有 1~3 拍的起伏（呼吸性窦性心律不齐）。新增 iscera.restless()：交感围绕静息张力
REST_SYMP=0.08 做 OU + 高斯扰动，副交感同理，几条慢激素做小随机游走。
实测心率在 73.8~78 之间跳。**注意**：写成  .08 + (x-0.08)*0.94 是错的 —— x 已经被
settle 拉到 0，这样只会得到 0.005，永远回不来；要写成 x*0.40 + 0.08*0.60 + 噪声。

**原因三：心率纵轴固定 55~130。** 窗口里只有静息时，±2 拍的起伏在 75 像素里看不出来。
改成跟数据走：跨度小于 18 拍就放大到中位 ±9，跨度大才用全量程。

顺手两件：①采样点沿用最近一次真实计算的 params/pulse（_last_emotion_res()），
PARAMS 卡在实时档不再是一排「—」②把我调试时重复采到的 52 个完全相同点合并掉
（409 → 357 行，只留每个平段第一格）—— 那是我自己制造的噪声，不是历史。

测试：t106 43/0、t107 54/0、t108 31/0；
ode --check 通过 PAGE 抽出的 143K 脚本。
提交 4a525e0（restless）+ 9708b66（时间轴/纵轴），已 push，便携包 253 条 verify 通过。

## 2026-11-07 傍晚｜时间锚定 / 身体参数进卡 / 群聊当场不记（V5.3.1）

### 新增
- card2.anchor_relative_time(text, ts)：渲染时把「昨天/今天/明天/大前天…」
  按**这条记忆自己的 create_ts** 换成绝对日期。存的是相对、读的是绝对，
  
aw 一个字不动。跨年也会算对（1 月 1 日说「昨天」= 去年 12 月 31 日）。
- iscera.current_profile() / validate_profile() / apply_profile()：
  身体参数（9 个标量 + H_BASE/HALFLIFE_H 两张表）可外置；白名单 + 范围校验。
- card2.set_viscera_profile(patch)：验证 → 生效 → 写 meta['viscera_profile']；
  __init__ 里 _load_viscera_profile() 读回来 —— 换机器/换代码版本，身体不变。
- mind_server：GET /viscera/profile、POST /viscera/profile。
- mind_server._start_viscera_sampler()：后台每 120 秒记一次静息采样。
- qq_guard.group_noise(text, recent)：群聊当场不记（太短 / 纯表情 / 复读）。
- 	ests/test_t109.py：32 项验收。

### 修改
- semantic.py：补 class SemanticUnavailable(Exception) —— 这个文件 7 处
  
aise 和 1 处 xcept 引用它，但**从来没定义过**；平时装了模型走不到失败
  分支，一触发就是未捕获 NameError。别人提的 issue 里唯一一条真 bug。
- card2.render()：末尾对渲染出的整行做锚定（连 detail 里的 TIME 一起）。
- console.py：情绪页 OVERLAY → **激素总览**；AUTONOMIC / HORMONE FIELD /
  激素总览三张图的纵轴从写死（0~1 / 0~100 / -90~200）改成**按窗口真实幅度
  自适应**（p95×1.35、正负分开取 p90×1.3），刻度步长按范围自动选。
- iscera.py：REST_SYMP 0.08→0.10、REST_PARA 0.12→0.10，新增
  REST_SIG_SYMP=0.065 / REST_SIG_PARA=0.055 / REST_SIG_HORMONE=0.030。
  原来静息抖动只有 ±2%（心率不到 1 拍），控制台上就是一条直线。
- qq_guard.poll_once()：群消息进卡前过 group_noise，@ 我的直接放行；
  每个群维护本轮最近 20 条用于判复读（跨轮不保留）。

### 完成
- 全套 **43 个测试文件全绿**（t109 32/32 新增）。
- 真卡：8848 重启后 /health ok、1101→1106 条、第 22 天、链 ok、usable_ratio 1.0。
- /viscera/profile 读出 11 个键；POST 脏值（HR_CAP=999）返回 400
  ad_viscera_profile；把当前参数写进卡后重启仍从卡里读回。
- 采样实测：心率 73.8~79.2 在跳（原来恒定 75.0），交感/副交感也各自有起伏。
- 开源仓提交 cfb0f51 已 push（6 files, +549/−42）。

### 未完成
- 便携包 G:\card-live **没同步**：U 盘当前没插（G: 不存在），下次插上补。
- 待做：condition_expected 共现表、召回对象过滤、记忆四元组 / mem 绑 seq /
  召回置信阈值 / 多对象关系卡 / 群里别的 AI 打标 / 日记进卡。
- 时间锚定目前只作用于 
ender()（长期印象格与召回行）；「最近在聊」那格
  用的是原话，没锚（本来就是最近几轮，风险低）。

## 2026-11-07 傍晚｜动机层 + 统一时间线 + 曲线看得见（V5.3.2）

**新增**
- `motiv.py`：七条驱力（好奇/依恋/胜任/好玩/关心/休息/秩序）的池子 + 六类欲望候选 + 仲裁（硬否决写理由）。
  设计上只做「内在天气」：影响语气、看什么、学什么、闲时自演化；**外发消息要过 quiet/上课/间隔/额度四道门**。
- `tests/test_t110.py` 29 项（静息不生欲望、四道否决、风险刹车、settle 不穿基线、bump 不抬驱力）。
- `mind_server` 三个路由：`GET /motiv`、`POST /motiv/tick`、`POST /motiv/done`（tick 顺手往时间线写一条）。

**修改**
- `card2.recall()`：`SELECT m.*, (SELECT seq FROM mem_birth WHERE mid=m.id) AS tseq`，
  想起的东西**按时间线序号排序**，行首挂短日期（`11-05｜…`），被 supersede 的标「旧说法」。
- `card2._hist_viscera()`：没事件的采样点原 label 是空串（攒了 258 条「没名字」的点），
  改成沿用六小时内上一个标签、都没有才记「静息」；**只在认知层开着时生效**。
- `console.py`：新增 `autoY()` —— 5%~95% 分位当主体、两侧各留 0.27 倍跨度，主体占画面 **65%**；
  心率/交感副交感/五调质三张图改用；激素总览叠加图 ×1.30→×1.45。

**完成**
- 44 个测试文件全绿（t108 31/31、t107 54/54、t110 29/29）。
- 真卡：258 条空标签补齐，`/console_data` 597 点无空标签。

**未完成**
- 「冲突按 seq 裁决」只做了排序与标注（`superseded_by` 目前 0 条），等真出现新旧说法再验。
- 自主学习→skill 库、主动外发消息的落地通道还没做。
## 2026-11-07 傍晚｜V5.3.3：把"可信度"补上（软删除 / 隔离区 / 来源校验 / 一层装饰）

**新增**
- `forget(mid, mode)` 三档删除：logical（dead=1，可恢复）/ natural（原遗忘路径，台账留
  mid+哈希+理由）/ purge（再清 detail、recall_event、mem_birth、向量缓存 + WAL）。
  purge 的返回体带 `boundary`，明说 `card.db.bak-*`、便携包、外部锚没动。
- `quarantine` 列 + `quarantine_rows()` / `release_quarantine()`：防污层判死的记忆
  现在**真的不进检索**（SQL 级 `COALESCE(dead,0)=0 AND COALESCE(quarantine,0)=0`）。
- `_origin_ok()`：看 Origin / Referer，剥端口后必须本机；OPTIONS 一律 405。
  可选 `WHALE_REQUIRE_TOKEN=1` + `X-Whale-Token`（存 `qq_state/api_token.txt`）。
- 路由：`/forget`、`/mem/quarantine`、`/mem/release`；成功都写时间线。
- `HORMONE_NOTE`：24 条激素各一句体感，控制台多一张「激素总览 · 体感」卡。
- `tests/test_t111.py` 26 项：三档语义 / 隔离区进出 / 来源校验 / SQL 级排除。
- `docs/数字生命卡-技术说明-V5.3.3.txt`（42928 字节）：新增第 8 章，并订正三处措辞
  （三因子→四因子 + W_CEIL_RECALL 说明；防篡改→可检测部分篡改；时序真实顺序）。

**修改**
- `remember()` 写入时判 `trust < 0.50 或 conflict=1` → `quarantine=1`（INSERT 28 个占位符）。
- `console.py` 的 `API()` 自动带令牌，19 处裸 fetch 统一补 header。

**完成**
- 全套 45 个测试文件 0 挂；真卡重启后 /health ok（1124 条）、跨站 POST 403、
  本机 POST 200、logical 删完 `recall` 确认 False、bad id 404 / bad mode 400。

**未完成 / 已知边界**
- purge 不清磁盘备份与便携包副本（设计如此，返回体已点名）。
- 隔离判别仍靠规则版信任分：0.50~0.70 那批误判（如"你是我的骄傲"）只是压低分，不隔离。
- 19 条装饰激素没有任何行为影响 —— 按用户要求只当观感，别拿它们当机制。

**教训**
- 补丁脚本里用 `'''...'''` 写 `"\n"` 会被当真实换行 → `SyntaxError`，改 `chr(10)`。
- `MemoryCard` 没有 `self.lock`（锁在 `mind.lock`），照抄旧写法会连删三处缩进。
- `json.dumps` 产出双引号字符串，HTML 属性里的 `"` 会变成 `\"` 撑坏整行 → 用 `repr()`。
- `recall()` 返回 tuple 不是 dict，测试里 `h.get("id")` 会 `AttributeError`。

## 2026-11-07 夜｜V5.3.4：别把猜的当成记得（召回带对象 / 低置信 / 共现表 / 别家 AI / 主动开口）

**新增**
- ``recall(..., about_uid=)`` 软加权：同一个人的记忆 ×1.10、明确属于别人 ×0.92。
  不做硬过滤 —— about_uid 覆盖率只有三成，硬过滤会把七成记忆挡死。
- ``CONF_WEAK = 0.34``：top1 低于它时，【这句话让我想起了】前加
  「（这几条我不太确定，可能记岔了）」。
- ``condition_expected(a,b,n,last_ts)`` 共现表 + ``expected_after(region,k)``：
  remember() 里跟「上一条同一个人（没标人则全局）」连一笔，回答「A 之后常跟 B」。
  不做 SNN：事件标签仍要 LLM 生成，共现表几十行给同一信号且解释得清。
- ``qq_guard.looks_like_bot()``：14 个短语 + 「200 字以上且含首先/其次」→ source=qq-bot、
  salience ≤0.45（群里 49% 的消息来自另一个 AI，不标以后会把它的口吻当群友）。
- ``tools/qq_proactive.py``：动机引擎的出口。问卡 → 卡否决（安静/上课/间隔/额度）→
  只在 reach_out / care_check 组一句 → 自己再核时间窗 → 发 → POST /motiv/done。
  **默认不发**，``--send`` 才真发。
- ``tests/test_t112.py`` 17 项；``docs/数字生命卡-技术说明-V5.3.4.txt``（第 9 章）。

**修改**
- ``assemble_context(..., about_uid=None)``：显式参数（上一版误写成 kwargs.get，NameError）。

**完成**
- 全套 **46 个测试文件 0 挂**；真卡重启后 /health ok（1124 条 / 第 22 天）。
- 推送 ``463a8f2``；顺手清掉 3 条自己冒烟写进真卡的 probe 记录（purge）。

**未完成 / 已知边界**
- 评估集 R@1 62.5%（5/8，样本太小没有统计意义）；比上次 75% 少一条，
  不能排除是这一轮反复跑 eval 把 access 权重抬起来造成的漂移 —— 需要扩样本再看。
- 多对象关系卡、自主学习→skill 库、主动升级的落地形式，还没做。

**教训**
- ``python -X utf8 -c`` 里套三引号/多层引号必翻车（今天四次），一律写临时 .py 再跑。
- ``recall()`` 对外返回的是候选元组 (score, wn, row, sim, cover)，取 id 要 ``[2]["id"]``。

## 2026-11-07 夜｜V5.3.5：关系卡 / 学到的整理成技能 / 主动升级提案

**新增**
- ``person`` 表 + ``person_touch/person_card/people``：一个人一行（关系层、第一次、最后一次、
  出现次数、各分区分布）。remember() 带 about_uid 时自动挂一笔。
  **只记关系与频次，不记评价** —— 好感度打分与「不用装热络」冲突。
- ``GET /people``、``GET /person?uid=``（没有就 404 not_found，不编）。
- ``tools/learn_to_skill.py``：把一份笔记压成一条技能（场景 / 做法）。
  抽不出「什么时候用」就如实写「未标注」，不替它编场景；同名覆盖，不留两条。
- ``tools/self_upgrade.py``：只读体检 → 一条提案写回卡（region=work）。
  **只写提案，不改代码、不删数据、不动参数** —— 发现问题可以自己做，动手得先说出来。
- ``tests/test_t113.py`` 24 项。

**修改**
- ``mind_server`` 两个新路由的 query 解析：``_qs`` 是另一个方法的局部变量，
  在 ``_route`` 里 UnboundLocalError → 各自就地 ``parse_qs``。

**完成**
- t109 32 / t110 29 / t111 26 / t112 17 / t113 24 全绿；真卡重启 /health ok（1127 条）。
- 推送 ``b8a8780``。真跑一次 ``self_upgrade``：7 项里只有「对象覆盖率 33%」要看一眼。

**未完成 / 已知边界**
- 关系卡目前只有基础频次（没有「上次聊了什么」「他最近在忙什么」），要等写入侧
  真带上 about_uid 才有内容 —— 现在库里 33% 有对象。
- 主动开口通道默认不发；要不要真开（以及发什么）还没定。

**教训**
- ``ON CONFLICT ... layer=excluded.layer OR person.layer`` 里 ``OR`` 是逻辑或，
  不是拼接 —— 写成了会把 layer 变成 0。要合并字符串用 ``CASE WHEN``/``||``。

## 2026-11-07 深夜｜V5.4：一张卡开始收下所有发生过的事（统一收件口 + 池子不设上限）

**依据**：用户 m36184 拍板五条 —— ①我的记忆不只属于他一个人，收件层收所有人说的话 ②所有池都不设容量限制，「满了往中期沉淀」这个机制拿掉 ③时间线加成只作用在最近一个月、越近加得越多、超一个月归零，加在原衰减曲线之上 ④长篇文字（小说/长视频字幕）允许提炼主干+留几处细节 ⑤收件层保留 60 小时（m36190 又定短期窗口 = 7 天）。

**新增**
- `card2.py`：`inbox` 表（id/ts/source/channel/who/uid/gid/kind/text/kept/kept_mid/note + idx_inbox_ts）、`inbox_add / inbox_list / inbox_mark / inbox_gc / inbox_stats / _ensure_inbox`；`remember()` 写完立刻往收件层留一份 `kept=1`（私聊与本窗口「两条腿」，不再依赖我每轮记得调 /report）。
- `card2.py`：`WINDOW_BONUS_DAYS=30`、`WINDOW_BONUS_MAX=0.06`、`WHALE_WINDOW_BONUS`（默认开）。加成只给 `speaker='user'`，`age<30 天` 时 `s *= 1 + 0.06*(1-age/span)`。
- `mind_server.py`：`POST /intake`（统一收件口，任何来源都往这写）、`POST /inbox/mark`、`GET /inbox?limit=&hours=&unkept=&full=`；启动守卫：卡里 0 条记忆且没设 `WHALE_ALLOW_EMPTY=1` 就拒绝启动（盘没挂上时 SQLite 会替你新建一个空库，/health 还报 ok，这是最骗人的假绿灯）。
- `tests/test_t114.py` 31/31。

**修改**
- `_evict_short_overflow()` 变成空操作（容量驱逐整个拿掉）；`tick()` 里换成 `inbox_gc()`。
- 修掉一个我自己埋的 bug：`about_uid` 分支的 `elif` 让 `(0.85+0.30*salience)` 只作用于『别人』那一支，salience 乘子对同人/未标注的记忆一直没生效。
- `tests/test_t92.py`「短期池容量腾退」改成「不再被削回去 + 7 天窗到期离开」。

**评估（卡副本，eval_memory.py）** 关加成 R@1 75.0/R@3 75.0 → 12%/30天 62.5/87.5（太猛）→ **6%/30天 75.0/87.5（采用）**；EVID 100%。结论：加成 ≤6% 时第一命中不掉、前三多捞回一条。

**验证** 全套 48 个测试文件 0 挂；真卡 1163 条 / 第 23 天 / chain_ok / anchor ok；`/intake` 200、`/inbox` 200、跨站 Origin 仍 403；冒烟写进真卡的 4 行 `source='smoke'` 已清。

**教训**
- `remember()` 里要先 `_ensure_inbox(c)` 再 INSERT，否则每次写入都刷 `no such table: inbox`。
- 测试里灌 200 条短期记忆不能用 `remember()`（Jaccard 去重会把数字变化当重复并成十几条），得直接 INSERT `mem`（别忘了 `vec` 列 NOT NULL）。
- `c.tick()` 要传 `dt_seconds`。
- 交接时最容易漏的是「谁去调这个新接口」：`/intake` 建好了，但 `qq_guard`、B站、抖音三个出口还没改走它。

**未完成**：①`qq_guard` / B站 / 抖音 三个 adapter 接到 `/intake` ②长文「提炼主干+细节」的工具 ③256G M.2 主卡迁移（文件系统未定）④重启 DSH 让 REA 拿到 Ghidra。
## 2026-11-08 凌晨｜V5.5 池子阶梯修回原样 + 印象层

用户核对出两件事：①"满了往中期沉"这个容量机制不该存在（池子不设限）②短期池应该
**实时筛选**，够格的当场进中期，不够格的留到 7 天忘干净删掉。第①条 V5.4 已做；
第②条查出来是我 11-06 改错的：	ick() 只在 7 天到期时结算，else 分支还把不够格的
UPDATE pool=medium（等于续命 6 倍）。

**改（card2.py）**：	ick() 短期循环改每轮实时调 _consolidate()；_consolidate()
的 else 分支恢复 7 天忘光（_forget_row(..., "短期池到期，没进中期")）；
USER_SPEECH_BONUS=0.12（他说的话海马分 +0.12，**不是免筛**——免筛会让 133 条里
103 条全进中期）；REPEAT_SHORT_PENALTY=0.5 + _is_repeat_short()（≤12 字且 7 天内
同句 ≥3 次，或该条 access_count≥3；去重会把重复句合成一条，所以次数记在 access_count）。

**印象层（新文件 impression.py，不调 LLM）**：主体×情境（时间桶/事件桶/我的状态）×反应；
w = min(0.98, 1-0.5^(n/6))；覆盖要件是"比被看到的次数更多"——
max(3, ceil(n_old*1.5)) 次新反应才打折（*0.6），跌到 0.06 退休留档；
90 天半衰期淡出；每主体 40 条；近义反应按 difflib 0.72 合并（"我回来了/我回来啦"）。
接进 ssemble_context 的【印象】格（只描述、不预测、不当台词）+ GET /impression。

**教训**：①覆盖公式第一版写成 
_old*0.6（方向反了），回灌一次把 593 条印象打死；
②lt_n 曾用来记"当前攒到多少"，结果永远差一点推不翻，改成只记"上次推翻时的证据数"；
③回灌不能把 mem 全量塞进印象——时间桶太粗，半夜每句话都会进同一个筐，
必须有事件桶且跳过群消息（[群名] 前缀），1177 条里只留下 19 条真的算印象。
测试：t115 36/36、t92 12/12、t107 54/54、全套 **49 个测试文件 0 挂**。
提交 c2ab77a / 1d57994。

## 2026-11-08 上午｜V5.6 两个小池 + 统一收件口打通三个出口

用户要的是：「在短期池加一个**小池**，被反复召回的东西进去，遗忘时间更长；在小池里
又被经常提起就转中期。中期同理也加一个。」我第一遍理解成"给池子挂个 warm 标记"，
被纠正：**是真加一个独立池**，有自己的寿命和进出条件。

**新结构**：
短期池 7 天 ──被想起≥2 次且跨≥2 天──▶ 短期小池 21 天 ──再跨 3 天被想起──▶ 中期池
（半衰期 29.7 天）──被想起≥3 次且跨≥3 天──▶ 中期小池 90 天 ──再跨 7 天被想起──▶ 长期池

**改（card2.py，260420 → 265218 字节）**：新增池常量 POOL_SHORT_WARM / POOL_MEDIUM_WARM
与参数 SHORT_WARM_TTL=21d、MEDIUM_WARM_TTL=90d、WARM_SLOW=1/3、WARM_SHORT_DAYS=2、
WARM_SHORT_RECALL=2、WARM_UP_DAYS=3、WARM_UP_RECALL=3、WARM_MEDIUM_DAYS=3、
WARM_LONG_DAYS=7；weight_now() 加两个小池分支（λ 乘 WARM_SLOW，因为小池 TTL 21 天而
短期 λ 半衰期只有 4.5 天，不乘等于"寿命更长"是空话）；stage_factor 补两项；
tick() 扩成三段流转；新增 _warm_ready / _to_warm / _promote_warm；stats() 补两个池。

**V5.6 最关键的一课：权重基准与寿命基准必须分开记。**
第一版为了"想起不续命"把 t_anchor 的推进删了 → 权重失去基准，每次都从原始锚点重算，
w 越用越轻（test_t90 的 [B] 直接挂：400 天后 w=0.0757）。正确拆法：
t_anchor = 权重的参考点（被想起就推到此刻）；**新增列 pool_in_ts = 进当前这个池的时刻**，
只在换池时改，tick() 里 age = now - pool_in_ts。于是"想起只涨权重、不延长池内寿命"
由 pool_in_ts 保证。_migrate() 加幂等回填 UPDATE mem SET pool_in_ts=t_anchor
WHERE COALESCE(pool_in_ts,0)=0。

**一步跨两级**：tick() 里 short_warm→medium 之后同一轮 medium 循环又把它抓进
medium_warm。加 moved_ids 集合，每处搬家登记，后续循环跳过。

**验收**：tests/test_t116.py 22/22（小池衰减更慢、21 天还看得见、召回不延长池内年龄
但会推 t_anchor、同日两条只加固一次、跨日 +1 天、短期→小池、小池→中期、21/90 天到期
被忘且墓碑写明"小池"、中期也有自己的小池、stats 两个键）；test_t90.py 40/40 回到全绿；
全套 ALL TESTS OK。

**三个出口接收件口**：tools/qq_guard.py 新增 intake()（POST /intake，失败不抛）；
群消息在写入判定**之前**落一份（channel=group、带 gid、note=bot 标别家 AI），
私聊也在写入判定之前落一份（channel=private）——不管最后记不记，**原文先进收件层**。

**长文提炼**：tools/intake.py（--text/--file/--source/--kind/--full/--list/--dry）。
condense() 是确定性规则：按段落切→每段第一句当主干（最多 14 行）→再挑带数字/引号/
英文词的最多 6 句当细节→末尾标原文字数。1200 字以上自动提炼，--full 强制整篇。
对话原文永不提炼（用户 11-08 明确），只有"我看完的长文"可以。

**控制台**：POOLN 补 short_warm:"短期小池"、medium_warm:"中期小池"，五池水位给两个
小池配暖色；记忆页尾部新增 IMPRESSION / 印象 卡（async 拉 GET /impression，公式不复制
到前端）。

**真卡**：重启后 /health ok、1188 条、第 23 天、chain_ok；/console_data?hours=24 的池子
变 long 889 / medium 250 / short 49 —— 实时筛选生效，够格的当场搬去中期，且没有删除。
## 2026-11-08 上午（续）｜V5.6.1 收件层 → 短期池：除杂去噪那一道工序

用户把这个口子的分工说得非常清楚：
「收件箱是把所有的原文都装进去，然后进行除杂去噪，然后推短期池就行。」
也就是说 —— **不判断价值，只除噪**。我第一版写成"打分挑精华"（阈值 0.52），
方向就是错的：那等于在收件层又做了一次"值不值得记"的判断。

**改（card2.py）**：新增 inbox_score() / inbox_pick()。
  · 噪声 = 纯符号表情 / ≤2 字 / 重复字（哈哈哈）/ 碎片 / 与最近 20 条 Jaccard≥0.80 /
    标了 note=bot 的别家 AI 话 —— 这些留在收件层等 60 小时过期。
  · 其余整条原文推进短期池，阈值降到 0.30（噪声才拦）。
  · 推进去的时候 `normalize=False`：**原文一字不改**。这一条是真 bug：
    remember(normalize=True) 会把「今天」改写成「11月08日」再落库，我在 t117 里
    直接抓到（raw 变成「我妈11月08日说周末带我去买键盘」）。时间锚定只保留渲染侧。
  · 回填 kept / kept_mid，挑过的不会被挑第二次。
  · commit=False 是默认（只看不写），commit=True 才真落。

**接口**：mind_server 新增 POST /inbox/pick；后台线程 _start_inbox_picker 每 30 分钟
（WHALE_INBOX_PICK_SEC，0 关）自动跑一次，每次最多 20 条（WHALE_INBOX_PICK_N）。
**手动入口**：tools/inbox_pick.py（--limit/--hours/--min/--commit），默认不写卡。

**踩的两个真坑**：
  1. 路由里我又写了一遍 `body = self._body()` —— 外层已经读过 body，第二次读会
     一直等字节，请求 40 秒超时。现象是"接口卡死"，实际是"重复读同一个请求体"。
  2. 第一次插路由时把 `with m.lock:` 插到了行中间，缩进错乱；按行号整块重写才干净。

**验收**：tests/test_t117.py 全绿（噪声四条规则 / 三条实内容过线 / 重复降权 /
bot 降权 / commit 回填 kept / 进的是 short 池 / raw 一字不改 / 挑过不重挑 / gc 只清
没挑中的）。全套测试 ALL TESTS OK。

---

## 2026-11-08 下午｜评测复测与 V5.6.3：把「原话」端到桌上（R@1 之外第一次有外部尺子）

外部评测 `github.com/Ornn8/chat-memory-benchmark`：714 题、六个人物、跨半年，公开了我们旧版的逐题卷子
（`results/2026-11-08-whale-card-summary.json`）。旧版整题通过 217/711 = 30.5%，成本 ¥0.51。
分档看得很清楚：0 天 73.5%、3 天 23.9%、15 天 9.8%、45 天 10.2% —— 不是「忘得慢」，是「过了当天就找不回来」。

在这个仓库里搭了一个零模型调用的检索适配器（`whale-lab/bench/adapt_retrieval.py`），
每个 persona 建全新空卡、逐轮 remember、到题目时间点取上下文，量三件事：
`ret5/10/20`（按记忆 id 的召回）、`hit12`（上下文里有没有锚点原话 12 字）、
`cov`（判分 required 的证据有没有到眼前）。四类「该说不知道」的题单独剔除，否则尺子会把正确拒答算成没记住。

### 试过但更差的（全部回滚，数字留在这里）

| 改动 | P01 事实类 | P12 事实类 |
|---|---|---|
| 基线 V5.6.1 | 40.9% | 19.0% |
| 放宽晋升门槛（θ_medium 0.18 / θ_long 0.50 / 长期票 2） | 36.4% | 16.5% |
| 只加召回条数（top_k 10 + 预算 2000） | 30.6% | 25.5% |
| 邻居扩展（36 小时窗、每条 2 个） | 47.2% | — |
| 想起格内去重（jaccard 0.72） | 66.7% | — |
| 问句类型加宽（WIDE_TOP_K 8） | 68.1% | 42.6% |
| 短期池 7→14 天 | 66.7% | 42.6% |
| 多跳（剥虚词再召回一次） | 61.1% | 29.8% |
| 本人话语加分 0.30→0.55 | 66.7% | 42.6% |

结论：**「多留」「多端」不等于「记得更多」**。门槛一松，噪音进中期池，召回反而把真东西挤下去。

### 真正有效的两处

1. **原话进上下文**（`RAW_LINE_CHARS=110`、`RAW_LINE_MAX=8`）：想起格每条后面附
   「（原话：…）」，条件是 `raw != body` 且 jaccard < 0.92。根因是 `render()` 做时间锚定与去壳时
   会丢掉考题要的人名、日期、数字。→ P01 40.9% → **63.9%**，P12 19.0% → **40.4%**。
2. **本人话语加分**（`USER_SPEECH_BONUS` 0.12 → 0.30）：他亲口说的东西海马分抬一点。
   → P01 **68.1%**，P12 **44.7%**。

### V5.6.3：到期不硬删 + 去重放宽（用户 m37518「听你的…AB 同时做也可以」）

- A：`_consolidate()` 的 else 分支不再 `_forget_row`，到期改成 `pool=medium, pool_in_ts=now`
  （w 与 t_anchor 都不动，接着按中期 29.7 天半衰期走）。理由：缺证据的 304 题里
  **278 题证据在库里根本没有**（只有 3 题是「有但没召到」），而运行卡上写进去的 ¼~½ 被 7 天结算删掉；
  会忘该发生在中期→长期那段，不该发生在第七天那一刻。
- B：`DEDUPE_JACCARD 0.72 → 0.92`，0.72 会把「同一件事的细节不同」揉成一条。
- 顺手修：`_consolidate()` 的「权重磨到看不见就遗忘」提到晋升判断之前，否则 w=0 的行会被海马分抬回中期续命。

### 六 persona 终版（V5.6.3，ladder）

| persona | 整题 | 事实类 |
|---|---|---|
| P01 | 47/88 = 53.4% | 47/72 = 65.3% |
| P12 | 21/79 = 26.6% | 21/47 = 44.7% |
| P14 | 31/82 = 37.8% | 31/54 = 57.4% |
| P16 | 31/123 = 25.2% | 30/87 = 34.5% |
| P17 | 53/172 = 30.8% | 48/130 = 36.9% |
| P20 | 47/170 = 27.6% | 47/123 = 38.2% |
| **合计** | **230/714 = 32.2%** | **224/513 = 43.7%** |

对比：V5.6.2 是 31.8% / 42.9%，基线 V5.6.1 事实类 25.0%。A+B 对总分只 +0.4pp，
但 P20 从 34.1% 抬到 38.2%（+4.1pp）——说明「到期沉中期」对最长的那批对话有用。
注意尺子不同：官方 30.5% 判的是「整题作答对不对」，我们量的是「证据有没有到眼前」，所以 32.2% 不等于最终分数。

### 仍然最弱的三处（需要动写入侧，不是参数）

1. 45 天档：P12 5%、P14 19%、P17 23%——长期池的票据条件（w≥0.60 且召回≥3）在评测卡上几乎没人达标。
2. 别人的事记到用户头上 2/29：`remember()` 里凡 `speaker=user` 就挂 `about_uid=user`，
   「我妈生日」这种说的是别人，也被挂到他头上。
3. 分散线索 3/56：要跨多条拼，靠一次 top-5 召回做不到。

### 测试

`tests/test_t114.py`（收件层）31/31、`tests/test_t115.py` 35/35、`tests/test_t92.py` 12/12、`tests/test_t116.py` 22/22，
全套 **51 个测试文件 0 挂**。两处断言跟着语义改：
t114 的 `inbox_list(limit=50)` 是测试自己截断了历史行（改 500）；
t115 的墓碑理由与「到期沉中期」都按 V5.6.3 重写。

### 教训

- 评测分数要先确认「尺子量的是什么」：我们量证据到眼前，官方量整题作答，两个数不能直接比。
- 参数只能小修；真正的提升来自「把原话端上来」这一处代码（+23pp），而它一开始被我漏了。
- 复测要用空卡副本，绝不能在真卡上跑（跑一次会写进上千条假记忆，还要靠墓碑清）。
