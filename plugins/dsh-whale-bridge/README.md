# dsh-whale-bridge

把 [数字生命卡](..) 接进 [DeepSeek Harness](https://github.com/wangjunhan131/whale-card) 的薄桥插件。

卡片本体（`mind_server.py`）继续只绑 `127.0.0.1:8848`；这个插件只做一件事：把卡暴露的 HTTP 接口包装成 agent 可直接调用的工具，不复制任何记忆逻辑。

## 装法

1. 先让卡跑起来（`python mind_server.py`，或 `tools/portable.py` 注入后的启动脚本）。
2. 插件目录放进 profile 的 `node_modules/`（**要真目录，不要 junction/symlink** —— Node 会 realpath 回源路径，导致 `@deepseek-ai/*` 解析不到）。
3. profile 的 `package.json` 里 `dsh.profile.bundles` 加上插件名。
4. 重启 DSH 让它加载。

## 工具（18 个）

- 读：`mind_context` `mind_recall` `mind_health` `mind_status` `mind_timeline` `mind_disks` `mind_injection` `mind_ctx_preview` `mind_skill_list` `mind_config`
- 写：`mind_report` `mind_remember` `mind_dream_commit` `mind_seal` `mind_untrust`
- 执行：`mind_dream_pending` `mind_skill_use` `mind_disks_sync`（同步必须显式 `confirm: true`）

## 一条底线

卡里没有的，就是没有。工具返回没翻到就如实说没翻到，不许顺嘴编。

## 设计取舍

- **不做**通过 HTTP 做批量迁移：`/remember` 不收 `pool` 字段（见 `docs/ARCHITECTURE.md`）。
- **不做**在插件里缓存记忆：缓存的副本会与卡分家，宁可每次多一次 HTTP 往返。
- 客户端 UI 挂载（侧边栏页签）默认关闭：DSH 没有给插件留左侧栏插槽，且控制台本体是外置页面，塞进窄侧栏只会挤。
