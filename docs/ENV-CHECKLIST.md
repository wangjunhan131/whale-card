# 换机器要装什么（环境清单 + 插件清单）

> 一句话：**先看，再装，最后才装模型。**
> 到新机器上第一件事不是装东西，是跑一遍体检：

```bat
python tools\env_probe.py
```

它只读、不改任何东西。它会告诉你「有什么、缺什么、装什么版本」，还会把
**会让卡悄悄出错的坑**（时间服务没校准、Pascal 卡的 torch 没有 sm_61）单独列出来。
先看结论，再动手。

---

## 一、环境清单

按这个顺序装。每条都写了「为什么」——不写理由的清单，下次换机器还得重新猜。

### 0. 系统层（装完 Windows 就该有）

| 项 | 要求 | 为什么 |
|---|---|---|
| VC++ 运行库 | 2015-2022 x64 | Python wheel、ffmpeg、numpy 都依赖它，缺了报「找不到 VCRUNTIME140.dll」 |
| **时间服务 w32time** | **Running / 自动** | **最容易被忽略、后果最严重**：卡里所有衰减按真实流逝秒数算。时钟往未来跳 → `t_anchor` 落到未来 → 记忆被「冻结」永不衰减（2026-11-03 真出过：95 条记忆被冻 63.9 小时）；往过去跳 → 记忆被「加速遗忘」。**而且它不报错。** |

校准命令（管理员 cmd）：

```bat
sc config w32time start= auto
net start w32time
w32tm /resync
w32tm /query /status
```

### 1. Python

| 项 | 要求 | 为什么 |
|---|---|---|
| Python | **3.13.x 64 位** | 卡是 3.13 写的；32 位装不进 numpy 2.x 的大 wheel |
| 勾选 Add to PATH | 是 | 不然 `启动控制台.bat` 找不到解释器 |

> 包里如果带了 `runtime/python/python.exe`（`tools/install_runtime.py` 装的便携版），
> 那这台机器**可以不装 Python** —— bat 会优先用包里那个。
> 便携版只带 numpy/psutil，够跑卡，不够跑模型。

### 2. Python 依赖（卡本体）

```bat
python -m pip install -U pip
python -m pip install numpy Pillow psutil requests
```

| 包 | 最低版本 | 用途 | 缺了会怎样 |
|---|---|---|---|
| numpy | 2.5.4 | **向量检索**（`embed_np.py` 是纯 numpy 手写 BERT 前向） | 只能关键词召回，语义全废 |
| Pillow | 13.0.0 | 图片记忆的缩放/缩略图 | 图存不进卡 |
| psutil | 7.1.3 | 控制台的 CPU/内存曲线、看门狗判活 | 只是少个曲线 |
| requests | 2.34.2 | 少数脚本用 | 少数脚本报错 |

**不要装**（重要）：

- ❌ `torch` / `transformers` / `sentence-transformers` —— 卡的向量是纯 numpy 实现的，
  装了纯属浪费 2~3 GB，而且新 wheel 在 Pascal 卡上还会互相打架。
- ❌ 任何「模型运行框架」——模型按需单独装，见第四步。

可选（有对应功能才需要）：

```bat
python -m pip install edge-tts        :: 发语音条 / 唱歌
python -m pip install mcp zstandard   :: 只有要跑 MCP 服务时
```

### 3. ffmpeg

| 项 | 要求 | 为什么 |
|---|---|---|
| ffmpeg.exe + ffprobe.exe | 任意近期版本 | 语音转 16k wav、视频抽帧、音频拼接 |

两个来源，二选一：

- 卡包里带：`runtime\ffmpeg\ffmpeg.exe`（打包时加 `--with-ffmpeg`）
- 系统 PATH 里能 `where ffmpeg` 到

包里的优先，因为不依赖 PATH 污染。

> 踩过的坑：ffmpeg 写不了 `%TEMP%` 的时候，临时帧要放到卡目录里；
> concat 列表在中文路径下要用 UTF-8 写绝对路径，否则 `Permission denied`。

### 4. Node.js（只有要在这台机器上跑 DSH 才需要）

| 项 | 要求 | 为什么 |
|---|---|---|
| Node.js | **≥ 22**（本机 24.20.0） | DSH 是 node 应用 |
| npm | 随 node | 装插件用 |
| pnpm | 12.x（可选） | 从源码 build 插件时才要 |
| git | 2.55+（可选） | 拉插件源码、推开源仓 |

```bat
node --version
npm --version
```

### 5. DSH 本体（可选）

```bat
:: 从官网/安装包装，装完确认
dir "workspace\DeepSeek Harness.exe"
```

装完 `%USERPROFILE%\.dsh\` 会出现 profile 目录。**这个目录是 DSH 的全部家当**（插件、配置、会话），
本机现在 3.6 GB。

---

## 二、插件清单

分三类：**必需**（没有就少半条命）、**常用**（日常真在用）、**可选**（装了但可以砍）。

装法：DSH 里开插件市场（`dshmarket`）搜名字装；或者直接 `npm i <包名>` 进 profile。

### 必需

| 包 | 版本 | 作用 |
|---|---|---|
| `dsh-whale-bridge` | 0.1.0 | **卡和 DSH 之间那条通道**（18 个 `mind_*` 工具）· 自建，源码在 `deploy\dsh-whale-bridge` |
| `billion-context` | 0.1.212 | 上下文压缩（省 token 靠它） |
| `dsh-mnemon` | 0.5.37 | 热记忆（USER.md / MEMORY.md 那套） |
| `dsh-context` | 0.78.2 | 上下文状态查询 |
| `dsh-web-search-pro` | 0.2.3 | 联网搜索（免 key，走 `web_call search.run`） |

### 常用

| 包 | 版本 | 作用 |
|---|---|---|
| `dsh-computer-use-win` | 0.5.3 | Windows 桌面/浏览器控制（`mcp__wincu__*`） |
| `dsh-ego-browser` | 0.9.7 | 另一个浏览器通道（截图、CDP） |
| `dsh-image-gen` | 0.10.0 | 生图/改图（**需要配 API key**） |
| `dsh-drawai` | 0.3.4 | 画布（`.drawio`，画头像/示意图） |
| `@liustack/modlens` | 3.27.2 | 看图/OCR 的视觉桥 |
| `dsh-bili-scroll` | 2.4.0 | 刷 B 站（看视频、读字幕、评论） |
| `dsh-douyin-scroll` | 0.5.8 | 刷抖音 |
| `dsh-freellmapi` | 0.7.18 | 免费模型池（`headless` 轮次靠它） |
| `@vectorize-io/hindsight-coding-agents` | 0.8.4 | 长期记忆/知识页 |
| `@michengai/dsh-im-connect` | 1.0.21 | IM 连接 |
| `dshmarket` | 1.69.0 | 插件市场本体 |
| `dsh-workspace-mover` | 2.4.2 | 工作区/会话搬迁 |
| `dsh-worktree-space` | 1.3.8 | 多仓库 worktree |
| `dsh-move-to-workspace` | 0.1.13 | 会话移到工作区 |
| `@bushizhanghan/dsh-workmate` | 1.2.7 | 工作搭子 |
| `dsh-embedded-workbench` | — | 内嵌工作台 |
| `dsh-startup-screen` | 1.1.0 | 启动画面（纯装饰） |
| `dsh-whale-widget` | 0.4.2 | 小挂件（纯装饰） |
| `dsh-connect-workbuddy` | 5.17.0 | 协同 |

### 装了但基本不用（可以砍）

`dsh-startup-screen`、`dsh-whale-widget`、`dsh-connect-workbuddy` —— 纯观感/协同，搬过去可以先不装。

### 装完自检

```bat
:: 1. 看 profile 里的 bundle 列表
type "%USERPROFILE%\.dsh\profiles\desktop\package.json"

:: 2. 起 DSH，看插件有没有真加载（关键：18 个工具）
type "%USERPROFILE%\.dsh\profiles\desktop\node_modules\dsh-whale-bridge\load.log"
:: 最后一行要是：apply() 完成，共注册 18 个工具

:: 3. 试一个真调用
:: 让它调一次 mind_health，能返回「第 N 天 / 链完好」就算通了
```

> **两条硬教训（血换的）**：
> 1. `defineTool` 的 `parameters` 是**扁平属性表**，不是 JSON Schema；对象型参数
>    必须显式写 `additionalProperties: true/false`。写错一个字 → 整段 `apply()` 抛错 →
>    **18 个工具一个都不注册**，而且插件看着是装好的。
> 2. DSH 只加载 **profile 里那份拷贝**。改了源码目录要跑
>    `deploy\dsh-whale-bridge\sync-to-profile.ps1`（脚本必须存成带 BOM 的 UTF-8）。

---

## 三、模型清单（最后装）

前提：**8 GB 显存，同一时刻只能装一个**。

| 模型 | 大小 | 显存 | 结论 |
|---|---|---|---|
| Qwen2.5-7B-Instruct（Q4） | ~4.7 GB | ~5 GB | ✅ 主力。离线兜底的嘴 |
| Qwen2.5-Coder-7B-Instruct（Q4） | ~4.7 GB | ~5 GB | ⚠️ 和上面抢显存，二选一 |
| Qwen2.5-14B-Instruct（Q4） | ~9 GB | **装不下** | ❌ 只能切到 16G DDR3 跑，每秒两三字。只当离线打标 |
| CosyVoice2-0.5B | ~2 GB | ~3 GB | ✅ 留这个（Apache-2.0，中文强） |
| FishSpeech-1.5-0.5B | ~1.5 GB | ~2 GB | ⚠️ **非商用许可**（禁止任何商业用途），且和 CosyVoice2 重复 |
| SD1.5 + LCM-LoRA | ~2 GB | ~4 GB | ✅ 4 步出图，P104 能吃。画质上限低，画头像够 |

**建议的最小线**：7B-Instruct（Q4）+ CosyVoice2 + SD1.5。

**按需加载**需要个「模型管家」：加载前先卸上一个（8 GB 装不下两个），
还要管显存碎片。等机器到了我写。

### Pascal 卡的死穴

P104-100 是 GP104、**算力 6.1、没有张量核心、fp16 只有 1/64 速度**。

- 装 torch 必须自己带 `sm_61`，否则 `import torch` 成功、`cuda.is_available()` 也 True，
  跑算子才报 `no kernel image is available for execution on the device`。
  验证：`python -c "import torch; print(torch.cuda.get_arch_list())"`，里面要有 `sm_61`。
- 老的 cu121/cu124 wheel 还带 sm_61；越新的越可能没有。**装之前先跑 `env_probe.py` 看 `has_sm61`。**
- SD 和 TTS 都要注意：fp16 在 Pascal 上是 1/64 速率，能用但别指望快。

---

## 四、执行顺序（照这个来，别跳）

```
1. python tools\env_probe.py           ← 先看
2. 系统层：VC++ 运行库、w32time 校准    ← 再补地基
3. Python + numpy/Pillow/psutil/requests
4. ffmpeg（包里带 or PATH）
5. （要跑 DSH 才装）Node + DSH + 插件
6. 试跑卡：python bridge.py health      ← 卡能起来再谈模型
7. 装模型（一次一个，装完验显存）
8. 再跑一次 env_probe.py 对账
```

**别跳第 2 步。** 时间服务没校准，一天后你会发现记忆「忘得莫名其妙快」，
或者「该忘的忘不掉」，而且查不出原因——它不报错。
