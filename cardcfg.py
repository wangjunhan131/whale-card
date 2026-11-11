# -*- coding: utf-8 -*-
"""卡的共享常量：池名 / 池子门槛 / 权重 / 正则。

为什么单独一个文件：这些常量既被 `card2` 用，也被拆出去的 `cardmix_store` /
`cardmix_habit` / `cardtext` / `poolmath` 用。原来住在 card2 里靠运行期注入，
子模块离了父模块跑不起来 —— 那是反向依赖。搬到这儿以后谁要谁自己 import。

规矩：这里只放**纯值**（数字/字符串/编译好的正则）和 `DAY`、池名这种最底层的标量，
不 import 本项目任何模块 —— 它必须是依赖图的最底，否则就会成环（v1 就栽在这）。
"""
import os
import re

# 一天的秒数。放最底层：短期池 TTL、时间窗口换算都要它。
DAY = 86400.0

# 池名。原来是 poolmath 里的，但它排在依赖链更上面，得先进来。
POOL_SHORT, POOL_MEDIUM, POOL_LONG = "short", "medium", "long"

EMB_ADAPTER_VERSION = 1
SHORT_MAX_TTL = 7 * DAY   # 短期池最多待 7 天，之后必须巩固或淘汰
THETA_MEDIUM = 0.35   # ≥ 它 → 进中期池
W_FLOOR_MEDIUM = 0.08 # 进中期池时至少抬到这个权重，否则进去就死
W_FLOOR_LONG = 0.15
GAIN_REPEAT = 0.10
KEEP_DETAIL = 0.02
CLARITY_GC = 0.01
DEDUPE_JACCARD = 0.92   # 跟已有记忆这么像，才算「同一件事又说了一遍」
W_CEIL_RECALL = {POOL_SHORT: 0.95, POOL_MEDIUM: 0.90, POOL_LONG: 0.95}
TIER_MID = "mid"         # 中期主力（事件/事实/临时偏好）
THETA_LONG_STRICT = float(os.environ.get("WHALE_THETA_LONG", "0.60") or 0.60)
TRUST_WARN = 0.70          # 低于它 → 控制台"被灌输过的念头"里列出来
TRUST_BLOCK_PROMOTE = 0.50  # 低于它 → 不许晋升长期/底色
RE_NUM = re.compile(r"(\d+(?:\.\d+)?)")
RE_TIME = re.compile(
    r"(\d{1,2}\s*月\s*\d{1,2}\s*[日号]|[上下]午\s*\d{1,2}\s*[点时]|"
    r"晚上\s*\d{1,2}\s*[点时]|昨天|今天|前天|后天|明天|上周|这周|那年|去年|"
    r"\d{4}\s*年|\d{1,2}\s*[点时]\s*\d{0,2}\s*分?)"
)
RE_PLACE = re.compile(
    r"((?:在|去|到|从)[^\s，。！？、；：]{1,12}"
    r"(?:门口|路边摊|路边|学校|教室|宿舍|食堂|超市|公园|医院|车站|"
    r"公司|店里|楼上|楼下|操场|图书馆|家))"
)
_RE_ACTION = re.compile(
    r"^(还|又|再|也|顺便|然后|接着|后来|之后)?\s*"
    r"(拍|吃|喝|去|来|买|看|听|玩|打|聊|走|跑|写|读|做|见|找|送|带|拿|"
    r"收|寄|洗|做|学|练|唱|跳|坐|骑|开|关|睡|醒)")
GROUP_SOURCE_PREFIX = "qq-group"
GROUP_SAL_MAX = 0.35             # 群聊写入时 salience 上限
