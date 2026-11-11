# -*- coding: utf-8 -*-
"""语义层自测：真实库里的检索样例，看它是不是真的认意思。

从 `semantic.py` 搬出来的（原来它 main() 里 import card2，让语义层反向依赖整卡）。
用法：python -X utf8 tools/selftest_semantic.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from semantic import SemanticLayer  # noqa: E402

def main():
    """自测：真实库里的检索样例，看语义层是不是真的认意思。"""
    import card2
    db = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'card.db')
    if not os.path.exists(db):
        print('找不到 card.db')
        return 2
    layer = SemanticLayer()
    print('语义层状态：', layer.stats())
    if not layer.ready():
        print('模型不可用')
        return 1

    cases = [
        ('你还记得我QQ号吗', '用户是高三学生主号QQ号是100000000昵称叫使用者'),
        ('我喜欢吃什么', '用户喜欢吃白米饭'),
        ('我暑假靠什么挣钱', '用户暑假靠拍婚礼挣钱买硬盘'),
        ('你要做记忆卡吗', '我们要做一张数字生命卡'),
        ('你要做记忆卡吗', '你永远值得我付出'),
    ]
    for q, t in cases:
        qv = layer.encode_query(q)
        tb = layer.encode(t)
        s = SemanticLayer.sims(qv, [tb])[0]
        print('  %.3f  %s  ||  %s' % (s, q, t))
    return 0


if __name__ == "__main__":
    sys.exit(main())
