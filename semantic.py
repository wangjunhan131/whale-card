# -*- coding: utf-8 -*-
"""语义层：把一句话变成「意思向量」，让召回认意思而不是认字面。

为什么单独一个模块：card2.py 一直是纯标准库写的（能拷到任何一台装了
Python 的电脑上就跑）。语义层需要 numpy，所以把它关在这里，card2 只在
需要时 import 一次，拿不到就自动退回字面匹配 —— 功能降级，但不崩。

设计要点：
  1. **存的是 bytes 不是对象**：mem 表加一列 emb BLOB，512 个 float32
     正好 2048 字节。库能直接拷走，不依赖任何 Python 对象。
  2. **编码失败绝不返回零向量**：零向量的余弦是 0，看起来像「不相关」，
     会让召回静默变错。宁可抛异常让上层退回字面匹配。
  3. **相似度要做尺度变换**：bge 的余弦天然挤在 0.3~0.9（无关句也有
     0.3），直接当 0~1 用会让所有记忆都显得「有点像」。所以减基准再拉伸。
  4. **批量打分**：一次矩阵乘法算完所有候选，不在 Python 里逐条循环。
"""
import os
import sys

import numpy as np


class SemanticUnavailable(Exception):
    """语义层不可用：没有模型、模型坏了、或者被显式关掉。

    为什么必须定义它：这个文件里有 7 处 `raise SemanticUnavailable(...)` 和
    1 处 `except SemanticUnavailable`，但类从来没定义过 —— 平时装了模型走不到
    失败分支，一旦触发就是未捕获的 NameError，`encode_many` 的兜底也接不住。
    （2026-11-07 别人提的 issue 里唯一一条真 bug，就是它。）
    """
    pass


MODEL_DIR_NAME = os.path.join('models', 'bge-small-zh-v1.5')

# 🔧 2026-10-30：支持 bge-base-zh-v1.5（768 维）。
#    维度不能写死 —— 换模型时 emb 列宽度跟着变，
#    所以 from_blob 改成「按实际字节数反推维度」，不再校验固定 DIM。
BASE_DIR_NAME = os.path.join('models', 'bge-base-zh-v1.5')

# bge 余弦的实测尺度。**两个模型不一样，必须分开标**（2026-10-30 实测）：
#
#   bge-small-zh-v1.5（512 维）：同义 0.60~0.90、无关 0.25~0.45
#   bge-base-zh-v1.5 （768 维）：同义 0.31~0.72、无关 0.14~0.31
#
#   base 的余弦整体**比 small 低**，而且无关的上限 0.31 几乎顶到同义的下限
#   0.307 —— 两者重叠。所以按 small 的 0.32/0.45 去归一 base，
#   会把大量真相关的语义压成 0，实测 Top-1 反而从 56% 掉到 53%。
#
# base 的标法：0.28 以下当无关（压到 0），0.28~0.72 拉成 0~1。
#
# 🔴 2026-10-31：上面这行 base=0.28 是**错的**，已重新校准为 0.08/0.18。
#
#   实测（328 条主干 vs 三条查询的原始余弦分布）：
#     「今天天气怎么样啊」（完全无关）p50=0.196 p75=0.229 p90=0.260 p99=0.313
#     「我喜欢吃什么」  （真相关）  p50=0.126 p75=0.163 p99=0.318 max=0.437
#     「我QQ号是多少」  （真相关）  p50=0.179 p75=0.228 p99=0.354 max=0.435
#
#   **无关查询的 p90（0.26）只比旧 base（0.28）低一点点** —— 意味着
#   0.28 这条线卡在了「几乎全部真实信号之上」，整库记忆在大多数查询下
#   被判成 0。这就是为什么主干语义分数的中位数长期是 0.000，语义通道
#   实际上形同虚设，全靠字面 bigram 顶着（bigram 正是「暑假工」「最爱」
#   这类撞车错误的来源）。
#
#   新标法 0.08/0.18：base 下探到无关分布的 p25 以下，把整个 0.08~0.26 的
#   信号区都保留下来；span 收窄到 0.18，让 0.26 往上（真相关）就近饱和。
#   实测 Top-1 21/29 → 24/29，Top-3 保持 29/29。
#
#   平台区间：base 0.06~0.08 都是 24/29，0.05 掉到 23 → 取平台中部的 0.08。
#   换模型/换库之后必须重新跑一次评估脚本，不能直接套用这组数字。
SIM_BASE_SMALL, SIM_SPAN_SMALL = 0.32, 0.45
SIM_BASE_BASE, SIM_SPAN_BASE = 0.08, 0.18

# 保留旧名字做兼容；真正的取值由 sim_scale() 按当前模型决定。
SIM_BASE = SIM_BASE_SMALL
SIM_SPAN = SIM_SPAN_SMALL

# 两种模型的向量宽度。新库按当前模型建，老库的 512 维和新的 768 维**不能混用**
# （cos 会算错），所以 card2 里对每条记忆都记了它编码时的维度。
DIM_SMALL = 512
DIM_BASE = 768
DIM = DIM_SMALL          # 兼容老代码引用


def current_model_dir(env=None):
    """按环境变量 WHALE_EMBED_MODEL 挑模型目录（与 embed_np.default_model_dir 一致）。"""
    import embed_np
    return embed_np.default_model_dir()


def current_dim():
    """当前模型输出多少维。"""
    import embed_np
    d = embed_np.default_model_dir()
    return DIM_BASE if 'base' in os.path.basename(d) else DIM_SMALL


def _model_dir(model_dir=None):
    if model_dir:
        return model_dir
    return current_model_dir()


def available(model_dir=None):
    d = _model_dir(model_dir)
    if not (os.path.exists(os.path.join(d, 'config.json'))
            and os.path.exists(os.path.join(d, 'vocab.txt'))):
        return False
    return any(os.path.exists(os.path.join(d, c))
               for c in ('model.safetensors', 'pytorch_model.bin'))


def to_blob(vec):
    """np.float32[N] -> bytes（落库用）"""
    return np.asarray(vec, dtype=np.float32).tobytes()


def from_blob(blob):
    """bytes -> np.float32[N]（从库里读出来用）

    也接受已经是 ndarray 的情况（内存缓存里存的就是 ndarray），
    这样调用方不用关心「这条是从库里读的还是缓存里的」。

    🔧 2026-10-30：不再写死 512 维。换 bge-base（768 维）后，
    老库里的 512 维向量和新的 768 维**不能算余弦**（形状对不上），
    所以这里改成按字节数反推维度：只要能被 float32 整除就认，
    真正「这条向量是不是用自己的模型算的」由 card2 的 vec_dim 列把关。
    """
    if blob is None:
        return None
    if isinstance(blob, np.ndarray):
        return blob if blob.size > 0 else None
    if not blob:
        return None
    if len(blob) % 4:
        return None
    return np.frombuffer(blob, dtype=np.float32)


def norm_sim(cos, base=None, span=None):
    """把 bge 的原始余弦映射到 0~1 的「像不像」。

    base/span 不给就按当前加载的模型自动选（见 sim_scale）。
    """
    if base is None or span is None:
        b, s = sim_scale()
        base = b if base is None else base
        span = s if span is None else span
    return max(0.0, min(1.0, (float(cos) - base) / span))


def sim_scale(model_dir=None):
    """当前模型的 (base, span)。base 决定「多少算无关」，必须跟模型匹配。"""
    d = _model_dir(model_dir)
    if 'base' in os.path.basename(os.path.normpath(d)).lower():
        return SIM_BASE_BASE, SIM_SPAN_BASE
    return SIM_BASE_SMALL, SIM_SPAN_SMALL


class SemanticLayer:
    """懒加载的语义编码器。

    第一次 encode 才真正读模型（约 90MB / 1~2 秒），没用到就不占内存。
    """

    def __init__(self, model_dir=None, enabled=True):
        self.model_dir = _model_dir(model_dir)
        self.enabled = enabled
        self._enc = None
        self._failed = None
        self.encode_count = 0

    # -- 状态 -------------------------------------------------------
    def ready(self):
        if not self.enabled:
            return False
        if self._enc is not None:
            return True
        if self._failed:
            return False
        if not available(self.model_dir):
            return False
        return True

    def _load(self):
        if self._enc is not None:
            return self._enc
        if self._failed:
            raise SemanticUnavailable(self._failed)
        if not self.enabled:
            raise SemanticUnavailable('语义层被关掉了')
        try:
            import embed_np
            self._enc = embed_np.Encoder(self.model_dir)
            return self._enc
        except Exception as e:            # 模型坏了 / 内存不够 / numpy 没装
            self._failed = '%s: %s' % (type(e).__name__, e)
            raise SemanticUnavailable(self._failed)

    # -- 编码 -------------------------------------------------------
    def encode(self, text):
        """一句话 -> bytes；失败抛 SemanticUnavailable。"""
        enc = self._load()
        try:
            v = enc.encode(text)
        except Exception as e:
            raise SemanticUnavailable('%s: %s' % (type(e).__name__, e))
        self.encode_count += 1
        return to_blob(v)

    def encode_query(self, text):
        """检索用（带 bge 的查询前缀）-> np 向量。"""
        enc = self._load()
        try:
            return enc.encode_query(text)
        except Exception as e:
            raise SemanticUnavailable('%s: %s' % (type(e).__name__, e))

    def encode_many(self, texts, progress=False):
        """批量编码，返回 list[bytes|None]；单条失败不拖垮整批。"""
        out = []
        total = len(texts)
        for i, t in enumerate(texts):
            try:
                out.append(self.encode(t))
            except SemanticUnavailable:
                out.append(None)
            if progress and (i + 1) % 25 == 0:
                print('  编码 %d/%d' % (i + 1, total), flush=True)
        return out

    # -- 打分 -------------------------------------------------------
    @staticmethod
    def sims(query_vec, blobs):
        """一次算完一组候选的相似度。

        返回 list[float]，长度与 blobs 一致；blob 无效的位置给 0.0。
        已经是 L2 归一化的向量，所以余弦 = 点积。
        """
        if query_vec is None:
            return [0.0] * len(blobs)
        idx, rows = [], []
        for i, b in enumerate(blobs):
            v = from_blob(b)
            if v is None:
                continue
            idx.append(i)
            rows.append(v)
        out = [0.0] * len(blobs)
        if not rows:
            return out
        # 维度不一致的向量跳过（比如老库 512 维 + 新模型 768 维）：
        # 混着算余弦要么报错要么算出垃圾，宁可当它不存在。
        q = np.asarray(query_vec, dtype=np.float32)
        keep = [(i, v) for i, v in zip(idx, rows) if v.shape[0] == q.shape[0]]
        if not keep:
            return out
        idx = [k[0] for k in keep]
        rows = [k[1] for k in keep]
        mat = np.stack(rows)                      # (n, dim)
        cos = mat @ q
        cos = np.clip(cos, 0.0, 1.0)
        base, span = sim_scale()
        for k, i in enumerate(idx):
            out[i] = norm_sim(cos[k], base, span)
        return out

    def stats(self):
        return {
            'enabled': bool(self.enabled),
            'ready': self.ready(),
            'failed': self._failed,
            'encoded': self.encode_count,
            'model_dir': self.model_dir,
        }


# 进程级单例：一个进程只读一次模型
_LAYER = None


def get_layer(model_dir=None, enabled=True):
    global _LAYER
    if _LAYER is None:
        _LAYER = SemanticLayer(model_dir, enabled=enabled)
    return _LAYER


def main():  # noqa: D401
    """自测入口已经搬走：这里 import card2 会让语义层反向依赖整卡。

    真正跑样例的命令： python -X utf8 tools/selftest_semantic.py
    """
    print("语义层自测已移到 tools/selftest_semantic.py")
    print("（搬家的原因：这里的 main() 原来要 import card2 才能查真实库，"
          "等于让最底层的模型库反向依赖整张卡。）")
    return 0


if __name__ == '__main__':
    sys.exit(main())