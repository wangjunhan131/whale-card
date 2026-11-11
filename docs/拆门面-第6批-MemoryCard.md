# 拆门面第 6 批：`MemoryCard` 按域拆成 mixin

> 前 5 批已经把「纯函数」搬完（`vmath` / `cardtime` / `cardtext` / `poolmath` / `body`），
> card2.py 466288 → 422949 字节，调用点一个没改，两批做了逐字等价验证。
> 这一批动类结构，是风险最高的一步，所以先把切法和验收写死，再动手。

## 一、现状

- `card2.py`：8310 行，`MemoryCard` 一个类占约 6676 行。
- 类的构造顺序（`__init__`）本身就有依赖：打开库 → 建表/迁移 → 选语义层 → 载入记忆 → 结账（tick）→ 写锚。
- 全类的隐含耦合点只有三个：`self.db`（连接）、`self._emb_cache`（向量缓存）、`self.lock` **不存在**（串行化在 `mind_server.mind.lock`，别再被这个坑绊一次）。

## 二、切法（按域，不按行数）

每个 mixin 独立成 `cardmix_<域>.py`，只放方法，不放状态；`MemoryCard` 改成多继承拼装：

```python
class MemoryCard(MixStore, MixRecall, MixPool, MixMood, MixTimeline, MixDream, MixHabit, MixApi):
    def __init__(self, db_path, ...): ...   # 仍然只有这一个 __init__
```

| mixin | 域 | 大致方法 | 依赖 |
|---|---|---|---|
| `MixStore` | 写入与清洗 | `remember` / `add_dialogue` / `log_dialogue` / `_consolidate` / `_archive_add` / `_tomb_add` | `self.db`、`cardtext`、`poolmath` |
| `MixRecall` | 召回 | `recall` / `_score_rows` / `_detail_sims` / `_self_rows` / `_pod_candidates` / `recall_trace_add` | `self.db`、`self._emb_cache`、`self.semantic` |
| `MixPool` | 池子结算 | `tick` / `weight_now` 相关结算 / `medium_exit_*` / `_sleep_decay` / `_forget_row` | `poolmath`、`timeline` |
| `MixMood` | 情绪与身体 | `touch_mood` / `hormone_regress` / `viscera_sample` / `pad_*` / `motiv_*` | `body`、`hormone`、`viscera`、`motiv` |
| `MixTimeline` | 时间线 | `birth_stamp` / `seal` / 链头读写 / `log_hormones` | `timeline` |
| `MixDream` | 梦层 | `dream_pending` / `dream_due` / `dream_commit` / `dream_batch_*` | `dialogue`、`mem` |
| `MixHabit` | 习惯与印象 | `habit_hints` / `impression_hints` / 维护入口 | `habits`、`impression` |
| `MixApi` | 给服务层看的面 | `context` / `state` / `render` / `assemble_context` / `snapshot` | 前面所有的只读组合 |

## 三、三条硬规矩（前 5 批踩出来的）

1. **一次只搬一个 mixin**，搬完立刻 `ast.parse` + `import card2` + 起临时实例（`--port 8865` 副本）+ 逐字等价比对（新 vs 生产，prompt 只许差启动时间戳）。
2. 搬之前 `shutil.copy2 card2.py card2.py.bak-mix<N>-<时间>`；**补丁脚本一律写成 `.py` 文件跑**，不用内联 heredoc（`newline="\\n"` 被当字面量会把源码截成 0 字节，这坑真踩过）。
3. 发现「方法里引用了类级常量」不要顺手复制，先确认它在哪个模块：`DAY` 在 `cardtime`、`CORE_TIERS` 在 `poolmath`、`_clamp01` 在 `vmath`。
   用「子串判断已定义」会误判（`if "CORE_TIERS" not in pm` 把函数里的引用当成已定义），要判 `"\nCORE_TIERS = "` 这种完整赋值行。

## 四、验收（缺一不可）

1. `python -c "import card2"` + `ast.parse` 全过。
2. 临时副本（`sqlite3.backup` 拷一份真卡）起 `--port 8865`，`/health` `ok True`、`mem` 条数一致、`red []`。
3. **逐字等价**：同一句问话，新实例与生产实例的 `/context` prompt 只差「服务启动」那一行时间戳。
4. `tools/smoke.py` 19 项 `FAIL 0`。
5. `/report` → `/recall` → `/dream/pending` → `/motiv` 四个接口各打一次，都不 5xx。
6. 每搬一个 mixin 写一条 worklog，全绿之后总表第 20 件才从 🟡 改 ✅。

## 五、不建议做的事

- 不要顺手「优化」逻辑（改一个 `or 0` 就是一个行为差异，等价验证会立刻炸，但你分不清是搬家错还是改动错）。
- 不要动 `__init__` 里的顺序（结账 → 写锚 → 检查点这三步有先后依赖，第 4/5 件就是为了它们）。
- 不要在这一步接新功能；这一批的唯一目标是「搬完还一样」。
