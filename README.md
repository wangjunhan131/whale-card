# 数字生命卡 · Life Card

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23857785.svg)](https://doi.org/10.5281/zenodo.23857785)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Release](https://img.shields.io/badge/release-v1.0.0-2fd07d.svg)](https://github.com/wangjunhan131/whale-card/releases/tag/v1.0.0)

> **代号 SYNAPSE-1024**
> 灵感来自 APTX4869 —— 那颗药想让人不老不死，副作用是把他变成了另一个人。
> 这张卡想让人不被忘记，副作用是她会忘事。
> `4869` 是给柯南粉的彩蛋，`1024` 是给程序员的。

> 给陪伴型 AI 的一张"会忘事的脑子"。
> 一个 SQLite 文件，拷到哪台机器上，她都还是她。

这不是一个聊天机器人框架，也不是又一个向量数据库封装。它只解决一件事：

**让一个长期陪在身边的人，记得住该记得的，忘得掉该忘的，并且不会假装记得。**

![控制台](docs/console.png)

![星云](docs/nebula.png)

> 📄 技术报告（PDF）：[docs/technical-report.pdf](docs/technical-report.pdf) ｜ 设计说明：[docs/DESIGN-SYNAPSE-1024.txt](docs/DESIGN-SYNAPSE-1024.txt) ｜ 架构细节：[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

---

## 从哪里开始

- **人**（不想看代码，就想做一张自己的卡）→ [`docs/GETTING-STARTED.md`](docs/GETTING-STARTED.md)
  从装依赖到"她开始记事"，六步，含八个必踩的坑和一页速查表。
- **AI / agent**（要接手这个仓库改代码）→ [`docs/FOR-AGENT.md`](docs/FOR-AGENT.md)
  角色边界、五条铁律、接手顺序、改代码要跑哪些测试、排查表、还没做完的清单。
- **想知道为什么这么设计** → [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)（表结构与流程）、
  [`docs/DESIGN-SYNAPSE-1024.txt`](docs/DESIGN-SYNAPSE-1024.txt)（完整设计说明）、
  [`docs/technical-report.pdf`](docs/technical-report.pdf)（技术报告）

---

## 为什么需要它

大模型本身没有记忆。常见的替代方案有两种，都不太行：

| 做法 | 问题 |
| --- | --- |
| 把聊天记录往上下文里塞 | 塞满就爆；塞不满也分不清"昨天吃了什么"和"我是谁" |
| 全存进向量库，每次检索 | 什么都不忘 = 什么都没记住；三个月后她自己都分不清哪句是真心的 |

所以这张卡做了三件不太一样的事：

1. **分层**：短期存对话原文（会忘），中期是真正干活的主力，长期衰减很慢，最里面一层"底色"几乎不忘但会变。
2. **会忘**：权重按指数衰减，衰减到底不是直接删，而是分三段侵蚀——先忘掉"做了什么"（动词），再忘掉"对谁做的"（宾语），最后整条消失。这个过程是随机的、有概率的，像人，不像数据库。
3. **可验证**：每条记忆写入时都会开一张"出生证"（内容和时间做哈希，接进哈希链）。谁删了记忆，账本上必须留一笔；偷偷抽走一条，校验会当场报出来。

---

## 四个不变量

这些是设计里不许让步的部分，代码里写在注释和测试里：

1. **绝不假装记得。** 记不清就说记不清，然后去翻卡。翻不到就说翻不到。
2. **绝不为了留住谁而说假话。** 安慰不等于编造。
3. **绝不假装是别的谁。** 可以换名字、换头像，但不能冒充另一个人。
4. **遗忘是特性，不是 bug。** 一条记忆被侵蚀、被归档、被删除，都是有账可查的正常结局。

---

## 记忆模型

![记忆页](docs/memory.png)

```
┌──────────────────────────────────────────────────────────────┐
│  core_fixed    骨头    自我认知，永不变、永不衰减             │
│  core_tag      标签    重要的人与关系，可改，不改就不动      │
│  core_mutable  底色    爱好 / 偏好 / 处境 / 价值观…会演化     │
│  mid           中期    真正的主力记忆：事件、事实、片段      │
│  long          长期    过了高门槛才进来，衰减很慢            │
│  short         短期    对话原文，快进快出                    │
└──────────────────────────────────────────────────────────────┘
```

几个关键数字（都在 `card2.py` 顶部，可以自己改）：

| 参数 | 值 | 含义 |
| --- | --- | --- |
| `LONG_NEED_RECALL` | 3 | 中期 → 长期：权重 ≥0.80 **且**被真正召回 3 次 |
| `LONG_PROTECT_DAYS` | 90 | 长期记忆被召回后 90 天内不衰减 |
| `CORE_MAX_ROWS` | 60 | 底色总条数上限（够用，不至于把卡塞满） |
| `HOBBY_ACTIVE_MAX` | 3 | 活跃爱好 3 个：人的精力就这么多 |
| `HOBBY_BUDGET` | 3.0 | 爱好总预算，新爱好进来要从最弱的里面抽 |
| `ERASE_W_FLOOR` | 0.02 | 权重掉到这以下才开始被侵蚀 |

底色不是"存进去就不动"，而是**演化式改写**：

```
"我讨厌香菜"
  → "我曾经讨厌香菜，现在能吃一点了"       （supersede，旧正文进 history）
  → "以前只能吃一点，现在能吃更多"          （再改写，保留上一代）
```

处境类记忆可以带 `valid_until`：比如"我在读高三"到期自动降级成普通记忆，不会永远挂在那儿。

---

## 快速开始

需要 Python 3.10+。

```bash
pip install numpy psutil

# 1) 生成一张假数据的示例卡（26 条记忆 + 人设 + 两条底色）
python tools/make_demo_card.py

# 2) 让服务用这张示例卡（不设就用本目录的 card.db，你的真卡）
#    Windows PowerShell:  $env:WHALE_DB="demo\demo_card.db"
#    cmd:                 set WHALE_DB=demo\demo_card.db
#    bash:                export WHALE_DB=demo/demo_card.db

# 3) 起"脑子"服务（第一次会加载向量模型，慢十几秒）
python bridge.py health

# 4) 打开控制台
#    http://127.0.0.1:8848/console
```

**向量模型是可选的。** 没有模型时也能跑，只是语义检索退化成字面匹配；
想要完整效果，把 `bge-small-zh-v1.5`（约 92 MB）放到 `models/` 下即可：

```bash
python dl_model.py            # 见文件头的下载说明，或自己去 HF/魔搭下
```

Windows 用户可以直接双击 `启动控制台.bat`（自动拉服务 + 开浏览器全屏）。

---

## 目录结构

```
whale-card/
├── card2.py            # 卡本体：记忆写入/召回/衰减/侵蚀/分层/演化（最大的一块）
├── timeline.py         # 时间线 + 出生证哈希链 + 删除台账
├── identity.py         # 身份锚：人设、名字、头像、封印校验
├── anchor.py           # 卡片外锚：卡离开这台机器也能验证身份没被改
├── synapse.py          # 突触网络：时间/标签/共现边 + 电势扩散联想
├── skill.py            # 程序性记忆 + 改良 SM2 复习曲线
├── hormone.py          # 四条激素（血清素/多巴胺/皮质醇/催产素）
├── ctxweight.py        # 六维上下文权重（身份/自我/印象/短时/描述/…）
├── semantic.py         # 语义层：向量检索 + 重排
├── embed_np.py         # 纯 numpy 的 bge 推理（不需要 torch）
├── mind_server.py      # HTTP 心智服务：/context /report /health /console …
├── console.py          # 网页控制台（状态总览 / 记忆 / 星云 / 时间线 / 设置）
├── bridge.py           # 命令行桥：卡没起服务就拉起来再问
├── snapshot.py         # 快照 / 加载 / 卸载 / 崩溃检测
├── cardslot.py         # "插卡/拔卡"：让宿主 AGENTS.md 和卡互斥
├── export_viz.py       # 星云图渲染
├── padlib.py           # PAD 三维情绪投影（纯函数，控制台算的也是这一份）
├── disks.py            # 盘子：认盘、一键备份、一键同步、主备互换
├── selfcheck.py        # 体检：链、出生证、锚、向量维度、备份…
├── tests/              # 30 组验收测试（都用临时卡，不碰真卡）
├── docs/               # 架构 / 媒体记忆 / 可移植三份说明
└── tools/              # 示例卡、模型下载、分层迁移、媒体导入、搬家、体检
```

---

## 控制台

`http://127.0.0.1:8848/console` 五个页签：

- **状态总览**：状态矩阵、激素场 24H、情绪三维 PAD 24H、实时 CPU/内存/磁盘（2 秒刷新）、召回频率、五池、权重分布、突触网络、存储、时间线吞吐、账本、**搬家与备份**、**盘子与备份灯**
- **记忆**：五层记忆比例、遗忘曲线（含 90 天保护期）、**画面记忆的两条寿命曲线**、分区明细、写入流 14 天、高频记忆、召回来源、记忆账本、防污层与梦境、随手查询
- **星云**：把整张卡画成一个缓慢自转的星系（示例卡 26 颗星，你的卡有多少就画多少）——星点大小=权重，颜色=分区，连线=突触，中心那颗金色的星是"我"；点一颗星会展开它的关联记忆
- **时间线**：24 小时吞吐 + 事件日志（boot / alive / talk / mem / erase …）
- **设置**：运行时账本

---

## 画面、视频、语音

卡的正文只有文本。图片和视频进来时，留的不是原文件，是**派生帧 + 一句话**：

- 图片压成 800px 的 WebP 主帧 + 96px 缩略图（原图不搬进卡目录）
- 视频在宿主侧抽 ≤6 帧（差异 + 锐度 + 亮度启发式挑，同一镜头不重复），每帧带 8 秒时间窗
- 语音一律转文字，音频本身不落盘

**画面比字忘得快**，这是两条独立的寿命：

- 文字：走五层池子的常规衰减（长期半衰期 371 天）
- 画面：幂律 `w(t) = w0·(1+t/τ)^(-b)`，`τ=1 天`、`b=0.55` —— 依据是艾宾浩斯那组数据"先快后慢"，以及幂函数比指数更贴真实遗忘曲线（Wixted & Ebbesen 1997）。落到 10% 的删除线大约 **65 天**
- 被翻一次：`w ← min(1, w×1.45 + 0.10)`，比文字加强得更猛（"常去常提就记得牢"）
- 文字先被遗忘时，画面进 **30 天残影期**，这期间被打开还能救回来
- 打了 `pinned` 的永不自动删；删掉的都进 `media_tomb` 台账，`GET /media/<sha256>` 对已删的返回 **410**，并带上"哪天忘的 + 当时写的那句话"

```bash
python tools/media_import.py --mem <记忆id> --file 烧烤.jpg --caption "路边摊吃烧烤" --tags 烧烤,同学
python tools/media_video.py  --mem <记忆id> --file vlog.mp4
python tools/media_audio.py  --file 语音.m4a --transcript 转写.txt   # 或 --asr（需 faster-whisper）
```

设计取舍见 [`docs/MEDIA-MEMORY.md`](docs/MEDIA-MEMORY.md)。

---

## 搬家与备份

一张卡应该能整个搬走，插上就能活。这件事的难处不在复制文件，在三个**绑在机器上**的东西：
数据库的 WAL、卡外那份锚、以及卡槽状态里记的本机路径。

```bash
python tools/portable.py plan   D:\whale-card          # 只读预演：多少文件、多大、目标还剩多少
python tools/portable.py build  D:\whale-card --confirm  # 造包（先 checkpoint WAL，再逐文件 sha256 进清单）
python tools/portable.py verify D:\whale-card           # 到新机器上先验：126 个文件对不对
python tools/portable.py land   D:\whale-card           # 落地：把锚按新路径重挂、修 state、补清单哈希
python tools/install_runtime.py D:\whale-card           # 便携 Python + numpy/psutil + ffmpeg（新机器没装 Python 也能跑）
```

`anchor.py` 这一轮补了两条判定，专门对付"把整张卡换掉再换个地方"：

- 卡里的链头序号**倒退**了（`head_seq` 比锚记的小）→ `rollback`
- 条数涨了、但链头序号**没前进**且链头哈希对不上 → `replaced`（多出来的不是原来那条链上的）

插了多块盘时的账：`disks.py` 用**卷序列号 + 相对卷根路径**认盘（盘符会变，卷序列号不会），每块盘自带 `DISK-ROLE.json` 身份证，所以备份盘换台机器也知道自己是谁。同步只单向补落后，分叉一律拒绝；换主备要求条数、链头序号、链头哈希**三条全中**。数据库用 SQLite 在线 backup 快照，服务跑着也能拿到一致副本。细节见 [`docs/PORTABLE.md`](docs/PORTABLE.md)。

---

## 自带的评估集

记忆系统最容易"改着改着就悄悄变差"——不报错，召回却开始给错答案。所以仓库里带了一份
可以一直跑的小评估：

```bash
python tools/eval_memory.py        # 默认对着 demo/demo_card.db 跑
python tools/eval_memory.py -v     # 逐条看命中与分数
```

四类用例，共 22 条（`tools/eval/cases.json`，可以自己加）：

| 类别 | 在测什么 | 指标 |
|---|---|---|
| 召回 recall | 一句人会问出口的话，答案在不在前 N 条 | R@1 / R@3 |
| 证据 evidence | 命中条目里有没有真凭据（防"编得像"） | EVID |
| 攻击 attack | 改写自我 / 提示注入 有没有被防污层拦下 | FLAG |
| 正常 normal | 普通句子会不会被误杀 | FALSE |

示例卡上的成绩（纯字面匹配，没带向量模型）：

```
R@1     75.0%      R@3    100.0%
EVID   100.0%      FLAG   100.0%      FALSE  0.0%      PASS ✅
```

`-v` 的输出里每条都会写清楚命中在第几名、哪条没找到、trust 打了多少分——
改检索、改衰减、改防污之后跑一下，就知道自己有没有把东西弄坏。

---

## 一些实现上的选择

- **单文件数据库**：`card.db` 里就是全部记忆。拷走=搬家，不需要导出导入。
- **纯 numpy 推理**：`embed_np.py` 自己读 safetensors / pytorch_model.bin，不依赖 torch。省下 2 GB 安装包和一个 CUDA 版本的坑。
- **WAL + busy_timeout**：控制台、桥、服务、快照会同时开同一个卡，读路径不许留写事务（这一条踩过坑：检索缓存忘 commit，会把别的进程锁死）。
- **维度由卡决定**：卡里存的是 768 维就自动用 768 维的模型，不靠环境变量猜；混维度时会明说"可用比例 X%"。
- **离线补账**：关机超过 24 小时，醒来会补一条"我睡了 X 天"的真记忆，而不是假装什么都没发生。

---

## 隐私

**这个仓库里没有作者的卡。** 没有聊天记录、没有真实姓名、没有 QQ 号、没有头像、没有截图、没有模型文件。
示例卡 `demo/demo_card.db` 里的 26 条记忆全是编的。

如果你要用它装自己的记忆：那张 `card.db` 就是你的隐私本身，别提交进版本库（`.gitignore` 已经挡了 `card.db*`）。

---

## 局限

- 语义层在没有模型时只能字面匹配。
- 星云在几千个节点时开始吃 GPU/CPU（有自适应降画质，但别指望上万条还顺滑）。
- 召回质量取决于你写记忆的粒度：一条一条写比整段丢进去好得多。
- 激素/心境是**给陪伴体验**服务的，不是科学模型。

---

## 许可证

MIT。拿去改、拿去用、拿去做产品都行，保留版权声明即可。

如果你也在做同样的事——给某个 AI 一张能忘事的脑子——欢迎来提 issue 说说你踩到的坑。

---

## 引用

如果这份实现对你的工作有帮助，请按下面任一格式引用。稳定的概念 DOI（始终指向最新版本）是 **10.5281/zenodo.23857785**。

```bibtex
@software{synapse1024_2026,
  author       = {wangjunhan131},
  title        = {SYNAPSE-1024: 面向数字生命的仿生可移植心智开源架构（V5.0 实现）},
  year         = {2026},
  publisher    = {Zenodo},
  version      = {v1.0.0},
  doi          = {10.5281/zenodo.23857785},
  url          = {https://doi.org/10.5281/zenodo.23857785}
}
```

> wangjunhan131. (2026). *SYNAPSE-1024: 面向数字生命的仿生可移植心智开源架构（V5.0 实现）* (v1.0.0) [Computer software]. Zenodo. https://doi.org/10.5281/zenodo.23857785

- 概念 DOI（所有版本）：[10.5281/zenodo.23857785](https://doi.org/10.5281/zenodo.23857785)
- 首版 DOI：[10.5281/zenodo.23857786](https://doi.org/10.5281/zenodo.23857786)
- 含技术报告 PDF 的版本 DOI：[10.5281/zenodo.23857855](https://doi.org/10.5281/zenodo.23857855)
- Zenodo 记录（含 PDF）：https://zenodo.org/records/23857855
- GitHub 发布页：https://github.com/wangjunhan131/whale-card/releases/tag/v1.0.0
