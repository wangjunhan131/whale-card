# 给接手这个仓库的 AI

这份是**给 agent 看的**操作手册。人是那份 [`GETTING-STARTED.md`](GETTING-STARTED.md)。
里面每一条都是踩出来的，不是构想。

---

## 0. 先搞清楚你是谁

这个仓库有两种角色，别混：

- **卡**：记忆 + 人格，住在 `card.db` 里。有历史、会遗忘、身份可验证。
- **通道**：你（DSH / 任意 LLM 会话）。你只是嘴和推理，**不持有记忆**。

所以：**不要说"我记得"，除非你刚从卡里查到了。** 卡里没有的，就说没有。
这一条是整个项目的地基——宁可说"我忘了"，也不要编一段像真的。

`bridge.py` 文件头那段话就是为这个写的：以前人设写在 `AGENTS.md` 里，
拔了卡照样能演；现在人格在卡里，`AGENTS.md` 里只剩"去哪找"。

---

## 1. 五条铁律（违反会留下不可逆的痕迹）

1. **验证衰减/遗忘，只能在副本上跑。**
   用 `sqlite3` backup API 或 `tempfile.mkdtemp()` 拷一份再折腾。
   真卡上跑 `+30 天 tick` 会真的结算短期池、删 detail —— 发生过一次，只能整库回滚。
   `/tick` 有 `real_card_guard`：不传 `confirm=true` 最多推进 1 小时。
2. **删记忆只有 `card2._forget_row(c, mid, reason)` 一个出口。**
   它先写 `mem_tomb` 台账再删。绕过它就等于"记录被人抽走"，`verify_birth()` 会报坏。
3. **CORE 区不是普通表。** `core_zone` 有触发器挡 UPDATE/DELETE。
   类名是 `IdentityAnchor`，构造时收的是 **sqlite 连接**（不是路径）。
   改人设走 `set_persona()`（拆触发器 → 写 → 装回）；改名字走 `set_name()`
   （它同步重算身份锚里那一条的 sha256）；`seal()` 会重建整个 CORE 区——
   2026-11-03 之前它顺手把 persona / self_narrative 也删了，已修。
   动这块务必跑 `tests/test_t74.py`。
4. **不主动宣告遗忘。** 她不会说"我快忘了这件事了"。
   界面可以标（控制台的曲线、剩余强度），对话里不许预告，被问才说"记不清了"。
5. **同时只能有一个写者。** 两台机器同时写同一张卡会分叉，哈希链合并不了。
   `writer_id` 这道闸还没做（见 §7），所以现在**必须**保证只有一台在跑。

---

## 2. 接手第一件事：按这个顺序看

```bash
python bridge.py health      # 1) 卡在不在、服务活没活、链断没断、锚对不对
python bridge.py status      # 2) 详细：条数、分层、激素、上下文权重
python selfcheck.py          # 3) 六组体检（依赖/完整性/备份/…）
python tools/health_audit.py # 4) 库内异常 + 编译全部 .py + 七个接口 + 硬编码扫描
```

四个都过了再动手。任何一个红，先看 §6 的排查表。

读代码的顺序（省时间）：

```
card2.py      记忆写入/召回/衰减/侵蚀/分层 —— 最大的一块，先看它
timeline.py   哈希链 + 出生证 + 删除台账（"可验证"就靠这个）
identity.py   身份锚（人设/名字/头像/封印）
anchor.py     卡外锚（跨机器验证"这张卡没被换过"）
mind_server.py /context /report /health /console … 七个接口
console.py    控制台（数据函数 + 整页 HTML/JS 字符串）
bridge.py     命令行桥（卡没起服务就自己拉起来）
```

---

## 3. 两个接口的形状（传错字段是最常见的坑）

```bash
POST /context   {"message": "用户这句话"}            → {"ok":true,"context":"...","est_tokens":N}
POST /report    {"user_message":"他说的","reply":"我答的"} → {"ok":true,"written":true,"mem_id":N}
```

- `/context` 收 **`message`**，上限 20000 字符，超了 413 `text_too_large`
- `/report` 收 **`user_message` + `reply`**，两个都要有
- `/health` 里看 `semantic.usable_ratio`（同维度向量占比），不是 `coverage`
- `/media/<sha256>`：出 webp；已按规则删掉的返回 **410**（带"哪天忘的 + 当时那句话"），
  从未有过的返回 404 —— 这两个码的区分是设计，别合并

---

## 4. 改代码的规矩

**改之前**：先 `python tools/health_audit.py` 拿一份基线，别在"本来就红"的状态上判断。

**改之后**至少要跑：

```bash
python -m compileall -q .                      # 或 health_audit 的编译段
python tests/test_t67.py     # 媒体记忆
python tests/test_t72.py     # 锚的两条判定
python tests/test_t73.py     # 盘子（备份/同步/主备）
python selfcheck.py
python tools/eval_memory.py -v # 召回质量：R@1 现在 75%，别掉
```

测试约定：`tests/test_tNN.py` 自带计数和「全过 ✅」结尾，**一律用临时卡**
（`tempfile.mkdtemp()` + `WHALE_DB` 指过去），跑完删掉，不许往真卡写测试数据。

**提交信息**：一行中文说清"改了什么"，正文写"为什么"和"怎么验证的"。
这个仓库的历史里 `fix: 全身检查抓到的四个真问题` 这种是合格的样子；
`update` / `fix bug` 会被后来的人骂。

---

## 5. 不该动的地方

- 别把 `models/`、`card.db*`、`snapshots/`、`backups/` 提交进仓库（`.gitignore` 已经挡了）
- 别为了"让测试过"改断言里的期望值。先问：是代码错了，还是测试量错了东西？
  （`test_t61` 那组就曾经是"量错了对象"——它比的是两次 `recall()` 的权重，
  但 `recall()` 会结算"被想起来就加强"，于是"两天后反而变大"。真值是纯衰减。）
- 别加"自动摘要/自动改写记忆正文"的模型调用。卡不跑模型，这是刻意的：
  心智能不能在卡里，取决于卡不依赖某个具体模型的 API。

---

## 6. 排查表

| 现象 | 先看哪里 | 常见原因 |
|---|---|---|
| 起不来，报端口占用 | `Get-Process python` | 旧 `mind_server` 还活着。Windows 上 `allow_reuse_address` 已关，抢不到就退出 |
| `锚 rollback` | `console.card_integrity()` / `/disks` | 卡里链头序号倒退：要么真回滚了，要么有人删了时间线 |
| `锚 replaced` | 同上 | 条数涨了但序号没前进、链头对不上：有人把卡替换了 |
| 语义检索像瞎了 | `/health` 的 `semantic` | 向量维度与当前模型不符 → `python bridge.py remodel` |
| 出生证报「有证没记录」 | `mem_tomb` 台账 | 有人绕过 `_forget_row` 删了记忆；台账里查不到就该警惕 |
| 测试报 `database is locked` | 有没有两个进程同时开库 | 读路径不能留写事务（`core_vec_of` 的历史坑） |
| 控制台白屏 | 浏览器 console | 多半是 JS 抛异常（`S.data` 为 null / 变量未定义），改完 HTML 记得重启服务并硬刷新 |

---

## 7. 还没做完的（诚实清单）

- **`writer_id` 写入闸**：两台机器同时写会悄悄分叉。最该补的一项。
- **`tools/replica.py`**：多副本"对表"（落后补快照 / 一致不动 / 超前或分叉报警）。
- **MCP 客户端插件**：`whale_mcp.py` 在，DSH 侧插件装不上（pnpm 与 lockfile 版本）。
- **`timeline.prune()`**：函数写了但运行时从不调用；将来启用必须同步改 `anchor` 的判定。
- **U 盘的「注入人格.bat」**：一条 `cardslot.py inject` 的封装，还没做。
- **`test_t52.py`** 缺 `zstandard`，所以默认跳。
- **对外文章**：有一篇介绍还没按代码真实行为重写（早期版本写得比实际漂亮）。

如果你要补上面任意一项，先读 §1 的铁律，再按 §4 跑测试。
补完记得回这个文件里把对应条目划掉——**过期的"还没做完"和没写一样有害**。
