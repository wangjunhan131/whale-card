# -*- coding: utf-8 -*-
"""测试用：从「可能正在被服务读写的卡」里安全拷一份出来。

为什么不直接 shutil.copy2：
    服务在跑的时候 card.db 有 WAL 和内存页，copy2 会报
    `PermissionError: [WinError 33] 另一个程序已锁定文件的一部分`，
    而且就算拷成功也可能拷到半截状态（wal 里的写入没合进来）。
    sqlite 的在线备份 API 会自己处理这些 —— 拿到的一定是一致的快照。
"""
import os
import sqlite3


def copy_card_db(src_db, dst_db):
    """把 src_db 复制成 dst_db（dst 不能是已存在的库，会被覆盖）。"""
    src_db = os.path.abspath(src_db)
    dst_db = os.path.abspath(dst_db)
    os.makedirs(os.path.dirname(dst_db), exist_ok=True)
    for ext in ("", "-wal", "-shm"):
        p = dst_db + ext
        if os.path.exists(p):
            os.remove(p)
    uri = "file:" + src_db.replace("\\", "/") + "?mode=ro"
    s = sqlite3.connect(uri, uri=True, timeout=60)
    try:
        d = sqlite3.connect(dst_db)
        try:
            s.backup(d)
        finally:
            d.close()
    finally:
        s.close()
    return dst_db
