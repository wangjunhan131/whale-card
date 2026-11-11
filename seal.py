# -*- coding: utf-8 -*-
"""签名与密钥：卡自己的一把钥匙，谁都不依赖。

为什么要单独一层：`checkpoint.py`（检查点）、`disks.py`（可移植清单）、
`anchor.py`（人格锚）三处都要「签名/验签」，而它们本来互相 import 就成环
（disks → checkpoint → disks）。把签名的三件套抽到这里之后，
依赖变成 disks/anchor/checkpoint → seal → （标准库），环就没了。

钥匙跟着卡走（`anchor.key`）是设计使然：卡本身就是身份，挡的是
「运输途中文件被改了几行」，不是防能拿到整盘的人。
"""

import hashlib
import hmac
import json
import os

KEY_NAME = "anchor.key"

def load_key(db_path, create=True):
    """读（或第一次生成）签名密钥。返回 bytes；create=False 时不存在返回 None。"""
    p = key_path(db_path)
    try:
        with open(p, "r", encoding="utf-8") as f:
            k = (f.read() or "").strip()
        if k:
            return bytes.fromhex(k)
    except OSError:
        pass
    if not create:
        return None
    k = os.urandom(32)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(k.hex())
    os.replace(tmp, p)
    return k


def sign(payload, key):
    return hmac.new(key, _canon(payload), hashlib.sha256).hexdigest()


def verify_signature(doc, key):
    if not isinstance(doc, dict):
        return False, "不是一个 JSON 对象"
    sig = doc.get("sig") or ""
    body = {k: v for k, v in doc.items() if k != "sig"}
    want = sign(body, key)
    if not sig:
        return False, "没有签名"
    if not hmac.compare_digest(str(sig), want):
        return False, "签名对不上（文件被改过或密钥不是这一把）"
    return True, ""


def _card_dir(db_path):
    return os.path.dirname(os.path.abspath(db_path))


def key_path(db_path):
    return os.path.join(_card_dir(db_path), KEY_NAME)


def _canon(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")
