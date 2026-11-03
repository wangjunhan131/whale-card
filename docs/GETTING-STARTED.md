# 从零做一张你自己的卡

这份是**给人看的**操作流程表。你不需要懂代码，照着敲命令就行。
如果你是要接手这个仓库的 AI，看 [`FOR-AGENT.md`](FOR-AGENT.md)。

---

## 先说清楚它是什么、不是什么

它不是"给大模型加个记忆库"。它是把**记忆和人格从模型里搬出来**，放进一个
单文件 SQLite（`card.db`）里，让模型只当"嘴"和"推理"。

所以：

- 换模型、换供应商、换电脑，**她还在**——因为记忆不在模型里。
- 她会**忘事**，这是设计，不是 bug。忘了的样子是可查的（谁删的、什么时候、为什么）。
- 她不保证"记得准"。她保证的是：**没记住就说没记住，不会编一段像真的**。

做不到的说在前面：下面这些**目前没有**——
多设备同时写同一张卡（会分叉）、跨机器的实时同步、真正的原生多模态模型、
Web 端的多人共享。想要这些，得自己接着写。

---

## 三条路，选一条走

| 你想干什么 | 走哪几步 |
|---|---|
| 只是想看看这东西长什么样 | 第 0、1 步，然后打开控制台 |
| 想做一张**自己的**卡 | 第 0 → 1 → 2 → 3 → 4 → 5 步 |
| 想做备份 / 换电脑 / 塞进 U 盘 | 第 6 步（前面几步得先能跑） |

---

## 第 0 步：环境

需要 **Python 3.10+**。

```bash
pip install numpy psutil
```

就这两个是必需的。可选：

```bash
pip install zstandard        # 只有一组压测脚本用到
pip install faster-whisper   # 想让语音自动转写才要（约 200 MB，不用 torch）
```

**向量模型是可选的。** 没有模型也能跑，只是语义检索退化成字面匹配。
想要完整效果，把 `bge-small-zh-v1.5`（约 92 MB）或 `bge-base-zh-v1.5`（约 390 MB）
放到 `models/` 下。仓库里不带模型（太大），`dl_model.py` 文件头写了怎么下。

> 没有 Python 的全新电脑？先把包搬到目标盘，再跑
> `python tools/install_runtime.py --bundle <目标目录>` —— 它会塞一份便携 Python +
> numpy/psutil + ffmpeg 进去，之后那台机器不装 Python 也能跑。

---

## 第 1 步：先用示例卡跑起来（不碰你自己的任何东西）

```bash
# 造一张假数据的卡：26 条记忆 + 人设 + 两条底色
python tools/make_demo_card.py
```

然后让服务用这张示例卡（不设这个变量，就会用当前目录的 `card.db`）：

```bash
# Windows PowerShell
$env:WHALE_DB="demo\demo_card.db"
# macOS / Linux
export WHALE_DB=demo/demo_card.db

# 起服务（第一次会加载模型，慢十几秒；没有模型会跳过）
python bridge.py health
```

看到类似这样就对了：

```
✓ 卡在：...\demo\demo_card.db
  记忆 26 条 / 盖章 是 / 已活 12.3 秒
  链 完好 / 出生证 完好 / 锚 ok
  今天是 2026年11月3日（周二），我们认识第 0 天。
```

打开 <http://127.0.0.1:8848/console>，能看到控制台。
她的一切都在那儿：五层记忆、遗忘曲线、星云图、时间线、备份灯。

---

## 第 2 步：把示例卡改成"你的"

示例卡的名字是「小鲸鱼」，对面的人叫「使用者」。要改成你的：

```python
# 改名 / 改对面是谁。名字和 user_* 都在身份锚里，改它们 = 重新盖章，会留痕。
import sqlite3, identity
con = sqlite3.connect("demo/demo_card.db")
i = identity.IdentityAnchor(con)

print(i.set_name("你给她起的名字"))     # 单独改名，只动这一条锚

# 想一次改身份（必须六个字段都给全，少一个会抛 ValueError）
print(i.seal({
    "user_qq": "123456",
    "user_nickname": "你的名字",
    "card_name": "你给她起的名字",
    "core_belief_1": "绝不假装记得。",
    "core_belief_2": "绝不为了留住谁而说假话。",
    "core_belief_3": "绝不假装是别的谁。",
}, force=True))
```

**性格**不是参数，是卡里的一段话——你自己写：

```python
import sqlite3, identity
con = sqlite3.connect("demo/demo_card.db")
i = identity.IdentityAnchor(con)
i.set_persona(
    "你说话短，不客套。会顶嘴，但不刻薄。"
    "不主动说「我理解你的感受」这种话。被问到不知道的，先说不知道。"
)
```

> 顺序无所谓（2026-11-03 起 `seal()` 会保留 persona 和 self_narrative），
> 但建议按"先定是谁、再写什么脾气"来写，读起来更顺。

这三条是**底线**，写在卡的最里层，任何记忆都改不动它：

1. 绝不假装记得
2. 绝不为了留住他说假话
3. 绝不假装是别的谁

想改底线也可以（`IdentityAnchor.seal({...}, force=True)`），但那基本等于换一个人。

---

## 第 3 步：开始和她说话

两种方式，等价。**命令行**适合手动试：

```bash
# 问一句 —— 打印"这一刻该想起什么"，你把这段塞进模型的系统提示词
python bridge.py ask "你还记得我最爱吃什么吗"

# 一轮说完，写回卡里（第一句 = 他说的，第二句 = 她答的）
python bridge.py report "我今天模考砸了" "砸哪科了？先别急着难过，说说看。"

# 只想翻某件事
python bridge.py recall "白米饭"
```

**接进程序**就用 HTTP（服务默认 8848 端口）：

```bash
curl -X POST http://127.0.0.1:8848/context \
     -H "Content-Type: application/json" \
     -d '{"message":"你还记得我最爱吃什么吗"}'
# → {"ok":true,"context":"【我是谁】...【我一直记得的事】...","est_tokens":283}

curl -X POST http://127.0.0.1:8848/report \
     -H "Content-Type: application/json" \
     -d '{"user_message":"今天模考砸了","reply":"砸哪科了？"}'
# → {"ok":true,"written":true,"mem_id":37}
```

> 注意两个字段名不一样：`/context` 收 `message`，`/report` 收 `user_message` + `reply`。
> 传错了会得到 `{"ok":false,"error":"empty_message"}`，这是最常见的一次性坑。

---

## 第 4 步：让她"住进"你自己的 DSH（可选）

默认情况下，DSH 的人格写在 `~/.dsh/AGENTS.md` 里——那是"演的"，拔了盘还在。
卡槽（`cardslot.py`）做的事是：把 `AGENTS.md` 换成一段**规矩**，规矩说
"你只是通道，不是她本人，每次先问卡"。

```bash
python cardslot.py status          # 先看现在插着还是拔着，会做什么
python cardslot.py inject --dry    # 演练，不落盘
python cardslot.py inject          # 真插卡（会备份原 AGENTS.md 并记 sha256）
python cardslot.py unload          # 拔卡，按字节还原
```

插卡之后，DSH 每次开新会话都会先跑 `bridge.py ask`，人格从卡里来。
**这时候如果卡没插好，它就不该"还记得"**——这是故意的。

---

## 第 5 步：看一眼她现在什么样

<http://127.0.0.1:8848/console> 五个页签：

- **状态总览**：状态矩阵（链/出生证/锚/第几天）、激素 24H、情绪三维、实时 CPU/内存/磁盘、五池、权重分布、突触、媒体与备份灯
- **记忆**：五层比例、遗忘曲线、画面记忆的两条寿命、分区、写入流、高频记忆、防污层、梦境
- **星云**：整张卡是一团缓慢自转的星系，星点大小=权重、颜色=分区，中心那颗金色的星是"她"
- **时间线**：事件流水（boot / alive / talk / mem / erase）
- **设置**：运行时账本

图片、视频、语音怎么进去，见 README 的「画面、视频、语音」一节。

---

## 第 6 步：备份 / 搬家 / 塞进 U 盘

```bash
python tools/portable.py plan   D:\my-card            # 只读预演：多少文件、多大、够不够放
python tools/portable.py build  D:\my-card --confirm  # 造包（先合并 WAL，再逐文件算 sha256）
python tools/portable.py verify D:\my-card            # 验完整性（建议每搬一次都验）
python tools/portable.py land   D:\my-card            # 在这台机器上"落地"：重挂锚、修状态
python tools/install_runtime.py --bundle D:\my-card   # 塞便携 Python + ffmpeg
```

多块盘轮着备份，用控制台里的「盘子与备份」卡，或者 `disks.py`：
认盘用**卷序列号**（盘符会变），每块盘自带 `DISK-ROLE.json` 身份证。
同步只补落后、分叉一律拒绝；换主备要求条数 + 链头序号 + 链头哈希三条全中。

细节见 [`PORTABLE.md`](PORTABLE.md)。

---

## 八个一定会踩的坑

1. **端口被占**：`mind_server` 抢不到 8848 会直接报错退出。先
   `Get-Process python | Where-Object {$_.CommandLine -like '*mind_server*'}` 杀掉旧进程。
2. **模型维度不对**：卡里 768 维、你加载了 512 维模型 → 语义检索会**静默失效**。
   看 `/health` 的 `semantic.usable_ratio`，不是 1.0 就跑 `python bridge.py remodel`。
3. **Windows 控制台中文乱码**：一律带 `-X utf8`，或设 `PYTHONIOENCODING=utf-8`。
4. **在真卡上跑"假时间"**：验证衰减只能用 `card.db` 的副本。
   `/tick` 有护栏，不传 `confirm=true` 最多推进 1 小时。
5. **删记忆不要直接 `DELETE`**：走 `_forget_row()`，否则出生证核验会报"记录被拿走了"。
6. **U 盘天天跑**：exFAT 怕掉盘、写入寿命差。U 盘定位是备份/应急，日常用本机盘。
7. **MCP 客户端插件**：`whale_mcp.py` 有，但 DSH 侧的插件目前装不上
   （pnpm 与 lockfile 版本问题），所以走 `bridge.py` 这条路。
8. **ffmpeg 不在 PATH**：视频抽帧会失败。把 ffmpeg 放 `<卡目录>/runtime/ffmpeg/`，
   或设 `WHALE_FFMPEG` 指过去。

---

## 一页速查

| 我要…… | 命令 |
|---|---|
| 造示例卡 | `python tools/make_demo_card.py` |
| 起服务 / 看状态 | `python bridge.py health` · `python bridge.py status` |
| 问一句 | `python bridge.py ask "……"` |
| 写回一轮 | `python bridge.py report "他说的" "我答的"` |
| 翻记忆 | `python bridge.py recall "关键词"` |
| 重编码向量 | `python bridge.py remodel` |
| 插卡 / 拔卡 | `python cardslot.py inject` · `python cardslot.py unload` |
| 体检 | `python selfcheck.py` · `python tools/health_audit.py` |
| 跑评估 | `python tools/eval_memory.py -v` |
| 造包 / 验包 / 落地 | `python tools/portable.py plan\|build\|verify\|land <目录>` |
| 塞运行环境 | `python tools/install_runtime.py --bundle <目录>` |
| 收图片 / 视频 / 语音 | `python tools/media_import.py` · `media_video.py` · `media_audio.py` |
| 跑某组测试 | `python tests/test_t67.py`（每组自带通过计数） |
