# 搬家待办清单（2026-11-06 夜，E3 那台）

> 一句话：卡能搬，`portable.py` 现在能走完整条路（今晚在副本上真跑过一遍）。
> 但有 **四件事** 是今晚之前没人替他想到的，不先解决，搬过去会「看起来能动、其实变了一个人」。

---

## 已经做好的（今晚实测通过）

| 事 | 状态 |
| --- | --- |
| `handover` 交棒子命令 | 新增 + 副本实测：交棒前 `/report` 409、交棒后能写 |
| `--allow-running` 死开关 | 修了（以前只过第一道闸，到 checkpoint 照样 raise） |
| `requirements.txt` | 新建，按本机实测版本写（numpy 2.5.4 / Pillow 13.0.0 / psutil 7.1.3 / edge_tts 7.3.1） |
| 打包内容漏了说话参数和日记 | 补进 `INCLUDE_FILES`/`INCLUDE_GLOBS`：`qq_state/chat_cfg.json`、`diary/*.md` |
| 34 个测试残留目录（469 MB） | 已清，卡目录 1.37 GB → 903 MB，包 523 MB（含 ffmpeg）/ 423 MB（不含） |
| 文档 | `docs/PORTABLE.md` 补了 handover 那一节和「还差什么」 |

**副本上跑通的完整链路**（`D:\_movetest`，已清理）：

```
build → verify（167 文件 sha256 全对）
→ 换路径 land（外锚按新路径写回、卡内 anchor.json 改 db 字段）
→ handover（链头 w-EXAMPLE00000 → w-E3TEST000001）
→ 起服务 8851 → /health ok、anchor ok、第 21 天
→ POST /report → {mem_id: 1408, reply_mem_id: 1409}   ← 新机器第一次写
→ POST /context → 人格全文 + 【最近发生】 + 召回命中自己刚写的那条
```

---

## 四件事得先定

### ① QQ 只能在一台机器上活着

一个 QQ 号同一时间只能一处登录。卡搬去 E3、QQ 还留在这台 → 她在 E3 是**哑的**（只能开控制台自己看自己）。

三个选项：

- **A. QQ 也搬过去**：E3 上装 NapCat + 扫码。代价是要重新扫码，而且这台就下线了。
- **B. QQ 留这台，卡搬过去**：她在那头写不了话，只能本地跑（适合「布置好了再回来」这种短暂试住）。
- **C. 都不搬，先在副本上试**：改 `WHALE_DB` 指向副本、另起端口，等于开了个"分身"——**不建议**，这违反「同一时刻只有一个我」。

### ② DSH 要不要一起搬

`dsh-whale-bridge` 插件连的是 `http://127.0.0.1:8848`。卡搬去 E3 之后：

- 这台机器上的 DSH 就**够不着她了**（`mind_server` 只绑 `127.0.0.1`，**而且没有任何鉴权**）。
- 想跨机器连，必须给 `mind_server` 加 token 鉴权 + 绑局域网，否则局域网里谁都能读写她的记忆。**这是个真缺口，今晚没动。**

如果只是「搬过去布置好再搬回来」，这一条可以先不管——在 E3 上直接开控制台就够。

### ③ 时间是她的命，E3 的系统时间必须准

卡的衰减全按真实流逝秒数算（`/health` 里那个 `day 21` 就是这么来的）。E3 如果时钟飘了：

- 往前飘 → 记忆被当成"很久以前"加速遗忘
- 往后飘 → `t_anchor` 落到未来，等于冻结（就是 11-03 那个 95 条记忆被冻 63.9 小时的 bug 的形态）

**✅ 搬之前先对时**：`w32tm /resync`，再看 `w32tm /query /status` 里 `Last Successful Sync Time`。

### ④ 依赖和模型：E3 上得装、且必须带对模型

**完整的装什么、装哪个版本、哪些别装 → 见 [`ENV-CHECKLIST.md`](ENV-CHECKLIST.md)。**
（2026-11-06 晚补写：含系统层、Python、ffmpeg、Node/DSH、插件清单、模型清单，每条都写了理由。）

到新机器上**先跑体检再装东西**：

```powershell
python tools\env_probe.py
```

它只读、不改任何东西，会把「有什么 / 缺什么 / 会让卡悄悄出错的坑」列出来。

要点摘要：

- Python 3.13 + `pip install -r requirements.txt`（numpy / Pillow / psutil / requests；**别装 torch**）
- **向量模型必须是 `bge-base-zh-v1.5`（768 维，390 MB）**。卡里 1000 多条向量都是 768 维，只带 small（512 维）过去 → 自动选择退化 → 检索**静默失效**（不报错，`usable_ratio` 还照样报 1.0）。
- **w32time 必须是 Running 且校准过**（见上面第 ③ 条）

---

## 搬过去那台机器上的执行顺序

**最简单的方式：双击包里的 `一键落地.bat`。**

它会自己按顺序做完六件事，每一步都先判断「要不要做」，所以重复跑是安全的：

```
1. 体检      env_probe —— 看这台机器缺什么
2. 落地      land      —— 把卡外锚按这台机器的路径改名写回
3. 交棒      handover  —— 让卡认这台机器能写（不做 = 只读，/report 409）
4. 起服务    bridge    —— 没在跑就拉起来
5. 自检      /health + 真写一句话再读回来
6. 开控制台  浏览器打开 http://127.0.0.1:8848/console
```

命令行等价（想看清楚每步在干什么时用）：

```powershell
runtime\python\python.exe tools\first_run.py          # 和双击一样
runtime\python\python.exe tools\first_run.py --dry    # 彩排：只说不做，不写任何文件
runtime\python\python.exe tools\first_run.py --skip-probe --no-browser
```

退出码 0 = 就绪；1 = 有「必须先解决」的项目（看输出最后一节）。

如果想手工一步步来：

```powershell
# 0) 对时
w32tm /resync

# 1) 装依赖（有便携 runtime 就跳过这步）
python -m pip install -r requirements.txt

# 2) 落地：写回卡外锚（按新路径哈希取名）+ 改卡内 anchor.json 的 db 字段
python tools\portable.py land "D:\whale-card"

# 3) 【必须】交棒 —— 不做这步她是只读的
python tools\portable.py handover "D:\whale-card"

# 4) 起服务 + 试写
python bridge.py health
# POST /report 能返回 mem_id 才算真的搬到了

# 5) 想让她住进那台的 DSH 再 inject
```

## 搬回来那台机器上的执行顺序

```powershell
# 旧机器也要交棒，否则它因为"链头怎么变成别人了"变成只读
python tools\portable.py handover "whale-card"
```

---

## 仍然没解决的（诚实清单）

- `mind_server` 无鉴权：跨机器连必须补 token，否则裸奔
- `whale_mcp.py` 里还有一条写死的绝对路径，搬家后要改
- U 盘上直接跑 SQLite（WAL）有掉盘风险：当仓库可以，跑的时候拷本地
- 卡里 17 个 `card.db.bak-*`（约 150 MB）还没清；搬之前可以顺手清掉
