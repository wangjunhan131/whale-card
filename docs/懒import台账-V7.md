# 懒 import 台账（V7 · 2026-11-11 实测）

一句话结论：**懒 import 不是坏味道，是这套代码用来「不把整张卡拖起来」和「破环」的工具。留。**
但只有一种懒 import 是错的 —— **下层模块在函数里 import 上层模块**（反向依赖）。现在这种是 0 处。

## 现在的数字（`ast` 扫全部根目录 `.py`）

- 函数内 `import` 合计 **142 处**
- 分布：`mind_server.py` 64、`card2.py` 13、`console.py` 8、`disks.py` 8、`whale_mcp.py` 6、`dl_base.py` 5、`identity.py` 5、`semantic.py` 3、`timeline.py` 3、`versions.py` 3、`anchor.py` 2、`bridge.py` 2
- 被懒加载最多的目标：`urllib`(15)、`time`(12)、`disks`(12)、`json`(6)、`re`(5)、`ctxweight`(5)、`console`(5)、`mcp`(5)、`seal`(4)、`sqlite3`(4)、`anchor`(4)、`card2`(4)
- **函数内 `import card2` 只剩 4 处，全在 `mind_server.py`**（`_route` / `_fail` / `_reconcile`）。mind_server 是最上层入口，往下 import 天经地义；`card2` **从不** import `mind_server`。

## 三类，各自为什么留

**第一类：可选的重件，不能启动就加载**
`semantic`（sentence-transformers / torch，几百 MB 内存）、`mcp`、`urllib`。这些只在真要用的时候加载 —— 起卡时不需要模型，等你第一次问「还记得吗」才加载。

**第二类：破环**
`disks ↔ checkpoint ↔ anchor` 这三个互相要签名/要身份，签名的代码在 `seal.py`（唯一底层），剩下必须互调的少数几处放函数里。`card2` 里 13 处也是同类：结算要用到 `disks` 的指纹、导出要用到 `console` 的曲线计算，模块级 import 就会成环（`card2 → console → poolmath → card2`），所以留在函数里。

**第三类：顶层入口往下拿「边缘件」**
`mind_server` 的 64 处大多是这个：`/disks/*` 路由里才 import `disks`、`/console_data` 才 import `console`。好处是某个边缘件坏了（比如 `mcp` 没装），不影响整张卡起来。

## 一条硬规矩（新增空吞/懒 import 都按这个判）

| 写法 | 允许吗 | 理由 |
| --- | --- | --- |
| 上层 → 下层的函数内 import | ✅ | 延迟加载、隔离可选项 |
| 同层之间为了破环的函数内 import | ✅ | 但要在注释里写「为什么成环」 |
| **下层 → 上层**（比如 `card2` 在函数里 `import mind_server`） | ❌ | 这是真的反向依赖，说明模块边界画错了 |
| 用懒 import 掩盖「本来该显式声明的常量」 | ❌ | 这类在 2026-11-11 已清过一次（`_PUNCT`/`_STOP_CHARS` 那种「引用却从未定义」的陷阱） |

## 顺带记一笔：根目录的一次性脚本

41 个（`test_t*.py`、`eval_*.py`、`import_*.py`、`_repartition_pools.py`…）已收进 `dev/`，见 `dev/README.md`。
它们以前在根目录，只是让「模块列表」看着乱，**没有一个被生产代码 import**（生产里只有注释提到名字）。
