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

## 四步

```bash
# 1) 体检（只读，不碰任何东西）
python tools/portable.py plan "E:\whale-card"

# 2) 停服务，再造包（会先 PRAGMA wal_checkpoint(TRUNCATE) 把 WAL 合并进主库）
python tools/portable.py build "E:\whale-card" --confirm \
       --with-ffmpeg "D:\...\ffmpeg.exe"

# 3) 验包（120 个文件的 sha256 逐个对）
python tools/portable.py verify "E:\whale-card"

# 4) 到了新机器再落地：写回卡外锚 + 改写卡槽状态
python tools/portable.py land "E:\whale-card"
python tools/portable.py inject          # 人格注入
```

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
- **ffmpeg 不在卡里**：视频抽帧、语音转写靠它，`--with-ffmpeg` 会一起打包（约 100 MB）。
- **U 盘上跑 SQLite（WAL）有掉盘风险**：建议"U 盘当仓库、跑的时候拷到本地"，
  或者每次结束时做一次 checkpoint。
- `whale_mcp.py` 里还写死了一条绝对路径（MCP 插件用），搬家后要改。
