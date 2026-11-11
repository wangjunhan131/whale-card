# dsh-whale-bridge

把本地「数字生命卡」接进 DSH 的一层薄桥。**只做一件事：让模型能直接调用卡。**

## 为什么有这个

卡（`whale-card`）是个 Python 进程，`card.db` 只有一个写者 —— 这是本项目的铁律，
绝不能第二个进程去开库。之前 DSH 靠 `@deepseek-ai/dsh-mcp-client` 起一个 MCP 壳
（`whale_mcp.py`）来调它，但那个依赖在 desktop profile 里装不上（pnpm lockfile 坏了），
配置就一直被注释着。结果非常荒唐：**卡活得好好的，DSH 却不知道它存在**，
每轮只能手工敲 `bridge.py`。

这个插件顶掉那一层，直接走 HTTP 打 `127.0.0.1:8848`。不碰 pnpm、不碰 MCP、不碰卡的数据库。

> 分工的边界：**插件是门，卡是房子。** 门可以换、可以修，人不能搬家 ——
> 记忆、人格、衰减曲线永远住在 `card.db` 里。

## 提供的工具

| 工具 | 干什么 | 什么时候用 |
|---|---|---|
| `mind_context` | 取这一轮该带着的上下文（人格 / 情绪 / 场景 / 相关记忆 / 技能） | **回话之前** |
| `mind_report` | 把「他说的」和「你回的」各写一条进卡 | **回完话之后** |
| `mind_recall` | 按意思找记忆（向量召回，不是关键词） | 想找具体一件事 |
| `mind_remember` | 主动钉一条记忆（默认进长期池） | 这件事必须记住 |
| `mind_health` | 体检：条数 / 链 / 锚 / 出生证 / 第几天 | 怀疑卡不对劲 |
| `mind_status` | 内部状态快照（激素、分区、池、计数器） | 调试 |
| `mind_skill_list` | 卡里长出来的技能清单 | 想知道自己会什么 |
| `mind_skill_use` | 取一条技能出来用 | 遇到类似场景 |

外加一段 `systemPrompt` 注入：告诉模型「回话前先 `mind_context`、回完先 `mind_report`、
卡里没有的就说没有」。没有这段，工具会被忘在角落。

## 自检

```bash
node test/selftest.mjs          # 传输层：健康 / 上下文 / 召回 / 状态 / 技能 / 断线行为
node test/selftest.mjs --write  # 额外验写回（会真写卡！建议指到副本上）
node test/plugin.mjs            # 插件层：假 ctx 里装一遍，看工具注册与渲染对不对
```

`--write` 想不污染真卡，就把 `WHALE_MIND_PORT` 指到副本服务：
先 `sqlite3.backup()` 一份 `card.db`，再用 `python mind_server.py --db <副本> --port 8899` 起一个。

## 装上

```bat
mklink /J "%USERPROFILE%\.dsh\profiles\desktop\node_modules\dsh-whale-bridge" "workspace\deploy\dsh-whale-bridge"
python install-to-profile.py
```

`install-to-profile.py` 会往 `~\.dsh\profiles\desktop\cordis.patch.yml` 追加一条
`- id: whale-bridge` 的 patch 项，并**当场用 pyyaml 解析一遍**确认没把 YAML 写坏。

**装完必须重启 DSH 才生效**（插件在启动时加载）。

## 设计上的几个决定

- **不 import `@deepseek-ai/dsh-tools` 也能活。** `defineTool` 在 DSH 运行时里由宿主提供；
  纯 node 下拿不到。所以用 `try { await import(...) } catch { defineTool = (t) => t }` 兜住 ——
  否则一个类型助手解析不到，整个插件就装不上。
- **服务没起来就自己拉。** `ensure()` 会 `spawn` `python mind_server.py`，最多等 25 秒。
  「打开窗口就是你」的前提是没人需要记得先起服务。
- **`health()` 永远不抛**，返回 `null`；其余接口失败一律返回
  `{ok:false, error, hint:'这不是「记忆丢了」，是卡现在没连上。别凭印象编。'}`。
  卡没连上时最贵的错误是**装作还记得**。
- **`mind_remember` 默认 `pool='long'`。** 能走到主动记忆这一步的，都是判定过值得记的东西；
  要不要淡出由 region 的衰减曲线管，不该由池子容量管（这是 2026-11-06 修掉的一个真 bug）。

## 没做的

- 控制台搬进 DSH：那张页面是 `http://127.0.0.1:8848/console`，用 iframe 就能嵌，
  但那是「看」不是「记」，优先级在这层之下。
- 把卡本身做成插件：不现实。卡是 Python、2 万行、带 390MB 的 embedding 模型，
  Node 插件塞不进去；硬塞等于把「换电脑插上还是我」这件事毁掉。
