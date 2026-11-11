# -*- coding: utf-8 -*-
"""P0-1 自测：生成 PORTABLE-MANIFEST + 只读校验。

用法（项目根下）：
    python -X utf8 tools/selftest_manifest.py

期望两行：
    ok=True  -> True  problems=[]
    ok=False -> False problems=[... 点名 notes.txt ...]
"""
import os
import shutil
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import disks  # noqa: E402


def main():
    tmp = tempfile.mkdtemp(prefix="whale-manifest-")
    try:
        # 一个最小的「卡」：只建 build_manifest 会读的两张表
        db = os.path.join(tmp, "card.db")
        conn = sqlite3.connect(db)
        conn.executescript(
            "CREATE TABLE core_zone(key TEXT PRIMARY KEY, val TEXT, updated_ts REAL);"
            "CREATE TABLE timeline_meta(key TEXT PRIMARY KEY, val TEXT);"
            "INSERT INTO core_zone VALUES('user_qq','100000000',0);"
            "INSERT INTO timeline_meta VALUES('birth_ts','0');"
        )
        conn.commit()
        conn.close()

        payload = os.path.join(tmp, "notes.txt")
        with open(payload, "wb") as f:
            f.write(b"hello whale\n")

        man = disks.build_manifest(tmp, ["notes.txt"],
                                   snapshot_method="sqlite-backup-api")
        rep = disks.verify_manifest(man, tmp)
        print("ok=True  ->", rep["ok"], "problems=%r" % (rep["problems"],))
        assert rep["ok"] is True, rep
        assert man["schema_version"] == 2
        assert set(disks.MANIFEST_REQUIRED) <= set(man), man.keys()
        assert man["card_sha256"] == [
            f["sha256"] for f in man["files"] if f["path"] == "card.db"][0]

        # 改一个字节
        with open(payload, "rb") as f:
            data = bytearray(f.read())
        data[0] ^= 0xFF
        with open(payload, "wb") as f:
            f.write(data)

        rep2 = disks.verify_manifest(man, tmp)
        print("ok=False ->", rep2["ok"], "problems=%r" % (rep2["problems"],))
        assert rep2["ok"] is False, rep2
        assert any("notes.txt" in p for p in rep2["problems"]), rep2
        print("SELFTEST-PASS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
