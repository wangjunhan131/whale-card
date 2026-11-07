# 搬家：把整张卡搬到另一个盘 / 另一台机器

> 一句话：卡目录就是全部。带着它走，代码不用改、路径不用改。
> 但有两根钉子必须一起带走，否则「看起来能动」和「还是她」会分家。

## 为什么不是"拷个文件夹"

卡里有三样东西是绑定在一台机器上的：

| 东西 | 绑在哪 | 搬错的后果 |
| --- | --- | --- |
| `card.db-wal` | 和 `card.db` 同目录 | 热拷只拷主库，最近几小时的写入静默丢失（11-02 实测 WAL 有 14.1 MB） |
| 卡外锚 `~/.dsh/anchors/<哈希>.json` | 文件名 = sha256(**卡的绝对路径**)[:16] | 换路径 → 文件名对不上 → 程序认为没有锚 → 按卡内容重建一份 → 「锚 ok」但只是自己跟自己一致 |
| `cardslot.state.json` 里的 `dsh_home` | 旧机器的家目录 | 显示骗人（功能不受影响，注入走当前 `DSH_HOME`） |

## 步骤

```bash
# 1) 体检（只读，不碰任何东西）
python tools/portable.py plan "E:\whale-card"

# 2) 停服务，再造包（会先 PRAGMA wal_checkpoint(TRUNCATE) 把 WAL 合并进主库）
python tools/portable.py build "E:\whale-card" --confirm \
       --with-ffmpeg "D:\...\ffmpeg.exe"

# 3) 验包（每个文件的 sha256 逐个对）
python tools/portable.py verify "E:\whale-card"

# 4) 到了新机器再落地：写回卡外锚 + 改写卡槽状态
python tools/portable.py land "E:\whale-card"

# 5) 【必须】交棒 —— 不做这步，她一到新家就是哑的（只能读不能写）
python tools/portable.py handover "E:\whale-card"

# 6) 起服务，试写一句话
python bridge.py health
# 然后 POST /report，返回 ok 且没有 409 card_locked，才算真的搬到了

# 7) 人格注入（要不要让她住进 DSH）
python tools/portable.py inject
```

## 第五步为什么不能省：writer 锁

卡的时间线（`timeline` 表）带一列 `writer`，记着**每条事件是哪台机器写的**。
`writer_id()` 按机器生成，存在 `~/.dsh/writer-id.txt`。

服务启动时会看链头是谁写的：

```
head_writer != 我的 writer  →  writer_lock = head_writer  →  整张卡只读
/report    → 409 card_locked
/remember  → 同样拒绝
```

这不是 bug，是**故意**的：防止两台机器同时写，把哈希链分叉成两条再也合不回来的链。
代价就是搬家时必须显式交一次棒。

`handover` 做的事：往链上追加一条 `handover` 事件（用它自己的 `allow_foreign=True`
越过链头检查），之后链头就是新机器，正常写。

**搬回去那次也要在旧机器上跑一次 handover**，否则旧机器会因为「链头怎么变成别人了」
变成只读。这条在 2026-11-06 的临时副本上实测过：交棒前 `WRITE BLOCKED`，
交棒后 `POST /report` 正常返回 `{mem_id, reply_mem_id}`。

`build` 会在包里留三样凭据：`PORTABLE-MANIFEST.json`（全部文件哈希）、
`anchor.carried.json`（卡内锚）、`anchor.external.json`（卡外锚）。
`land` 把 `anchor.external.json` 按**新路径**算出的哈希名写回去，
并把 `landed_from` 记进去 —— 这样"这张卡是从哪儿来的"有据可查。

## 锚是怎么被验证真的生效的

不是看它说 ok，而是**故意弄坏再看它认不认**：

```
把落地锚的 count 改成 500（卡只有 467 条）
→ verdict: rollback
→ 理由：这张卡被回滚过：卡里只有 467 条时间线，锚记的是 500 条，少了 33 条
```

这说明服务读的确实是搬过去的那份锚，不是自己重建的。

## 11-02 补上的两条判定

原来的 `check()` 只在「卡比锚少」和「条数相同但链头不同」时报警。
服务启动会先追加一条 `boot` 事件，卡一"长大"，就落进「卡比锚新＝正常长大」的分支，
**链头不再比对** —— 于是把锚里的 `head_hash` 改坏也照样报 ok。

补的两条（`anchor.py` 的 `check()`）：

1. `head_seq` 倒退 → `rollback`：新的链头比旧的还小，说明链接不上了。
2. 条数变多、但 `head_seq` 没前进 → `replaced`：多出来的事件不是接在原来的链尾。

对应验收 `tests/test_t72.py`（12 项全过）。

## 还差什么（诚实清单）

- **新机器没有 Python**：`启动控制台.bat` 直接调 `python`，会挂。要么自带嵌入式
  Python + numpy/psutil（图片还要 Pillow），要么先手动装。
  依赖版本见 `requirements.txt`（2026-11-06 按本机实测写的：numpy 2.5.4 / Pillow 13.0.0 /
  psutil 7.1.3 / edge_tts 7.3.1）。**别装 torch / transformers / sentence-transformers** ——
  向量是 `embed_np.py` 用纯 numpy 手写 BERT 前向算的，本机从来没装过那三个。
- **向量模型必须带对**：卡里是 768 维（`bge-base-zh-v1.5`，`pytorch_model.bin` 390 MB）。
  只带 small（512 维）过去，`card2.py` 的自动选择会退回 small，长度对不上 → 检索**静默失效**
  （不报错，`usable_ratio` 还照样报覆盖率）。
- **ffmpeg 不在卡里**：视频抽帧、语音转写靠它，`--with-ffmpeg` 会一起打包（约 100 MB）。
- **U 盘上跑 SQLite（WAL）有掉盘风险**：建议"U 盘当仓库、跑的时候拷到本地"，
  或者每次结束时做一次 checkpoint。
- `whale_mcp.py` 里还写死了一条绝对路径（MCP 插件用），搬家后要改。
- **`--allow-running` 以前是个死开关**（2026-11-06 修）：它只过了第一道闸，
  到 checkpoint 那步照样 raise，热拷永远走不通。现在热拷会跳过 checkpoint 并
  在 manifest 的 `wal` 里明写 `skipped` —— 宁可写明"WAL 没合并"，也不假装干净。
