# -*- coding: utf-8 -*-
"""身份锚 + CORE / LIVE 双区（V5.0 §3.6 / T2-2）

为什么要有这个：
  卡可以换电脑、换模型，但「里面那个人还是他」这件事必须能被**机器验证**，
  不能只靠文档里写一句「请不要改」。

做三件事：
  1) 身份锚：把 6 条永不该变的身份事实各算一个 sha256 存进库。
     启动时重新算一遍，对不上就拒绝启动 —— 这不是防坏人，是防「不知不觉变成另一个人」。
  2) CORE 区：人格内核（只读）。用 SQLite 触发器物理挡住 UPDATE / DELETE，
     运行期谁都改不了，要改只能人工重签。
  3) LIVE 区：可变状态（心境、依恋、当前场景…）。随便写。

设计取舍：
  - 不加密、不做签名证书。目标是「发现自己变了」，不是「防破解」。
    真有人要改，删掉这个库重建就行 —— 但那已经是另一张卡了，本来也该是另一张卡。
  - 卡文件（identity.json）只存**明文**身份值 + 它们的 sha256。
    明文是为了让主人能一眼看懂这张卡是谁的；sha256 是为了让程序能验。
"""
import hashlib
import json
import os
import sqlite3

# 6 条身份锚。顺序固定，不能增删（增删本身就是「变成另一个人」）。
CORE_KEYS = [
    "user_qq",        # 主人 QQ 号
    "user_nickname",  # 主人昵称
    "card_name",      # 这张卡的名字
    "core_belief_1",  # 底线 1
    "core_belief_2",  # 底线 2
    "core_belief_3",  # 底线 3
]

# CORE 区里**不进身份锚**的字段。它们住在卡里、受同一个只读触发器保护，
# 但不参与 sha256 校验 —— 因为它们本来就是要慢慢变的。
#
# 为什么性格不能进 CORE_KEYS（2026-10-31 想清楚的事）：
#   身份锚的语义是「这几样一变，就不是原来那张卡了」。QQ 号、名字、底线
#   确实如此。但性格不是 —— 规则 3 写的是「性格会随着一次次对话慢慢演化」。
#   把性格塞进锚里，等于每演化一次就报「这是另一张卡」，那不叫保护，叫锁死。
#
# 2026-10-31 的教训：这一天之前，性格根本不在卡里，只写在 ~/.dsh/AGENTS.md。
#   于是 cardslot 一插卡（把 AGENTS.md 换成通道规矩），性格就没了 ——
#   「住进卡里」变成「被清空」。这一栏就是那次事故的补丁。
PERSONA_KEY = "persona"

# 头像不是文本，是卡根目录下的一个文件。core_zone 里只记一个版本戳，
# 用来让控制台知道「换过了，别再拿缓存那张」。
AVATAR_KEY = "avatar_ver"
AVATAR_FILE = "avatar.png"

# 给人看的字段说明，报错时用
KEY_LABEL = {
    "user_qq": "主人QQ号",
    "user_nickname": "主人昵称",
    "card_name": "卡片名字",
    "core_belief_1": "底线一",
    "core_belief_2": "底线二",
    "core_belief_3": "底线三",
    PERSONA_KEY: "性格与说话风格",
}


class IdentityMismatch(RuntimeError):
    """身份锚不匹配 —— 这张卡不是原来那张卡，或者它被人改过。"""


def _sha(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


class IdentityAnchor(object):
    def __init__(self, db, card_dir=None):
        self.db = db
        self.card_dir = card_dir
        self._init_schema()

    # ---------- 建表 ----------
    def _init_schema(self):
        c = self.db.cursor()
        # 身份锚：只存哈希，不存明文（明文在卡文件里）
        c.execute("""
        CREATE TABLE IF NOT EXISTS identity_anchor (
            key        TEXT PRIMARY KEY,
            sha256     TEXT NOT NULL,
            created_ts REAL NOT NULL
        )""")
        # CORE 区：人格内核，运行期只读（触发器挡）
        c.execute("""
        CREATE TABLE IF NOT EXISTS core_zone (
            key        TEXT PRIMARY KEY,
            val        TEXT NOT NULL,
            updated_ts REAL NOT NULL
        )""")
        # LIVE 区：可变状态
        c.execute("""
        CREATE TABLE IF NOT EXISTS live_zone (
            key        TEXT PRIMARY KEY,
            val        TEXT NOT NULL,
            updated_ts REAL NOT NULL
        )""")
        # 封卡标记
        c.execute("""
        CREATE TABLE IF NOT EXISTS card_meta (
            key   TEXT PRIMARY KEY,
            val   TEXT NOT NULL
        )""")
        # 物理只读：运行期任何 UPDATE / DELETE 都被 SQLite 自己拒绝
        c.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_core_no_update
        BEFORE UPDATE ON core_zone
        BEGIN
            SELECT RAISE(ABORT, 'CORE 区只读：身份内核不允许运行期修改');
        END""")
        c.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_core_no_delete
        BEFORE DELETE ON core_zone
        BEGIN
            SELECT RAISE(ABORT, 'CORE 区只读：身份内核不允许删除');
        END""")
        self.db.commit()

    # ---------- 封卡 / 校验 ----------
    def is_sealed(self):
        row = self.db.execute(
            "SELECT val FROM card_meta WHERE key='sealed'").fetchone()
        return row is not None and str(row["val"] if hasattr(row, "keys")
                                       else row[0]) == "1"

    def seal(self, values, force=False):
        """建锚。values 是 dict，必须包含全部 6 条 CORE_KEYS。

        force=True 才允许覆盖已封的卡（等于「我确认要换一个人」）。

        返回 (ok, 问题列表)，**不抛异常**（除了缺字段那种明显用错）——
        和 verify() 保持同一个返回形状，调用方才能统一处理。
        踩坑记录：原来这里没有 return，调用方写的是
        `ok, bad = identity.seal(vals)`，一盖章就
        `TypeError: cannot unpack non-iterable NoneType object`，
        眼看封卡成功却在最后一步崩了 —— 而这时候库里其实已经写进去了。
        """
        missing = [k for k in CORE_KEYS if k not in values]
        if missing:
            raise ValueError("身份锚缺字段：" + "、".join(
                KEY_LABEL.get(k, k) for k in missing))
        if self.is_sealed() and not force:
            return False, ["__already_sealed__"]
        import time
        now = time.time()
        c = self.db.cursor()
        # 重建时先拆触发器才能清空 CORE 区
        c.execute("DROP TRIGGER IF EXISTS trg_core_no_update")
        c.execute("DROP TRIGGER IF EXISTS trg_core_no_delete")
        # ⚠️ 下面这句会清空整个 CORE 区，而 core_zone 里除六个锚字段外还住着
        #    persona（性格）和 self_narrative（自述）—— 它们不属于身份锚，
        #    但同样只读、同样重要。先抄出来，重建完再放回去。
        #    （2026-11-03 修：原来如此盖章会静默把人设删掉，示例卡首当其冲）
        extras = [
            (r["key"], r["val"], r["updated_ts"])
            for r in self.db.execute("SELECT key,val,updated_ts FROM core_zone")
            if r["key"] not in CORE_KEYS
        ]
        c.execute("DELETE FROM core_zone")
        for k in CORE_KEYS:
            v = str(values[k])
            c.execute("INSERT OR REPLACE INTO identity_anchor(key,sha256,created_ts)"
                      " VALUES(?,?,?)", (k, _sha(v), now))
            c.execute("INSERT OR REPLACE INTO core_zone(key,val,updated_ts)"
                      " VALUES(?,?,?)", (k, v, now))
        c.execute("INSERT OR REPLACE INTO card_meta(key,val) VALUES('sealed','1')")
        # 把刚才抄出来的人设/自述放回去（不参与锚，所以不重算 sha256）
        for k, v, ts in extras:
            c.execute("INSERT OR REPLACE INTO core_zone(key,val,updated_ts)"
                      " VALUES(?,?,?)", (k, v, ts))
        self.db.commit()
        # 重新装回只读触发器
        self._init_schema()
        # 顺手把明文身份卡落盘 —— 之前只有 test 调 save_card，
        # 生产路径从没生成过 identity.json，备份里那行一直是空转。
        try:
            self.save_card(values)
        except Exception:
            pass
        return True, []

    def verify(self, values):
        """逐条校验。返回 (ok, 不匹配的字段列表)。

        故意不抛异常 —— 调用方可能只想看看哪里不对。
        """
        if not self.is_sealed():
            return False, ["__unsealed__"]
        bad = []
        rows = {r[0]: r[1] for r in self.db.execute(
            "SELECT key, sha256 FROM identity_anchor").fetchall()}
        for k in CORE_KEYS:
            want = rows.get(k)
            if want is None:
                bad.append(k)
                continue
            got = _sha(values.get(k, ""))
            if got != want:
                bad.append(k)
        return (len(bad) == 0), bad

    def verify_or_die(self, values):
        ok, bad = self.verify(values)
        if ok:
            return True
        if bad == ["__unsealed__"]:
            raise IdentityMismatch(
                "这张卡还没有身份锚，先调 seal() 建锚再启动。")
        names = "、".join(KEY_LABEL.get(k, k) for k in bad)
        raise IdentityMismatch(
            "身份锚不匹配，可能是另一张卡。对不上的字段：" + names)

    def set_persona(self, text, force=False):
        """写性格（CORE 区里唯一允许改的字段）。

        为什么单独开一个方法，而不是让人直接 UPDATE core_zone：
        CORE 区被触发器物理挡住，任何 UPDATE 都会被 SQLite 拒绝。
        要改性格必须先拆触发器、写完再装回去 —— 这个动作必须显式、
        必须留痕，不能是「顺手改一下」。

        参数 force 现在是给将来用的（比如加一道「确认要改性格吗」），
        目前只要调用就写，因为性格演化是设计里的正常行为。
        """
        import time
        now = time.time()
        c = self.db.cursor()
        c.execute("DROP TRIGGER IF EXISTS trg_core_no_update")
        c.execute("DROP TRIGGER IF EXISTS trg_core_no_delete")
        try:
            c.execute("INSERT OR REPLACE INTO core_zone(key,val,updated_ts)"
                      " VALUES(?,?,?)", (PERSONA_KEY, str(text), now))
            self.db.commit()
        finally:
            # 无论写成功还是失败，触发器都要装回去 ——
            # 中途抛异常而触发器没装回，CORE 区就永久可写了。
            self._init_schema()
        return True

    def get_persona(self, default=""):
        v = self.get_core(PERSONA_KEY)
        return v if v is not None else default

    # ---------- 改名 ----------
    def set_name(self, new_name):
        """改名。

        名字在 CORE_KEYS 里（它一变，理论上就是「另一张卡」）。所以这里
        不是偷偷绕开锚，而是**把改名当成一次显式的重新盖章**：
        拆触发器 → 改 core_zone.card_name → 同步重写 identity_anchor 里
        这一条的 sha256 → 装回触发器。其余五条一个字都不动。

        返回 (ok, msg)。
        """
        new_name = (new_name or "").strip()
        if not new_name:
            return False, "名字不能为空"
        if len(new_name) > 24:
            return False, "名字太长（最多 24 字）"
        import time
        now = time.time()
        c = self.db.cursor()
        c.execute("DROP TRIGGER IF EXISTS trg_core_no_update")
        c.execute("DROP TRIGGER IF EXISTS trg_core_no_delete")
        try:
            c.execute("INSERT OR REPLACE INTO core_zone(key,val,updated_ts)"
                      " VALUES(?,?,?)", ("card_name", new_name, now))
            c.execute("INSERT OR REPLACE INTO identity_anchor"
                      "(key,sha256,created_ts) VALUES(?,?,?)",
                      ("card_name", _sha(new_name), now))
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            return False, "改名失败：%s" % e
        finally:
            self._init_schema()
        # 明文身份卡也跟着更新，否则 identity.json 里还是旧名字
        try:
            vals = {k: self.get_core(k) or "" for k in CORE_KEYS}
            vals["card_name"] = new_name
            self.save_card(vals)
        except Exception:
            pass
        return True, "名字已改成「%s」" % new_name

    # ---------- 头像 ----------
    def avatar_path(self):
        """卡根目录下的头像文件。没设过就是 None。"""
        if not self.card_dir:
            return None
        p = os.path.join(self.card_dir, AVATAR_FILE)
        return p if os.path.exists(p) else None

    def set_avatar(self, png_bytes):
        """写头像。只收 PNG —— 控制台前端会统一转好再传。"""
        if not self.card_dir:
            return False, "没有卡目录，存不了头像"
        if not png_bytes:
            return False, "空文件"
        if len(png_bytes) > 4 * 1024 * 1024:
            return False, "图太大了（最多 4MB）"
        if not png_bytes[:8] == b"\x89PNG\r\n\x1a\n":
            return False, "不是 PNG"
        import time
        p = os.path.join(self.card_dir, AVATAR_FILE)
        try:
            with open(p, "wb") as f:
                f.write(png_bytes)
        except Exception as e:
            return False, "写头像失败：%s" % e
        c = self.db.cursor()
        c.execute("DROP TRIGGER IF EXISTS trg_core_no_update")
        c.execute("DROP TRIGGER IF EXISTS trg_core_no_delete")
        try:
            c.execute("INSERT OR REPLACE INTO core_zone(key,val,updated_ts)"
                      " VALUES(?,?,?)", (AVATAR_KEY, str(int(time.time())),
                                         time.time()))
            self.db.commit()
        except Exception:
            self.db.rollback()
        finally:
            self._init_schema()
        return True, "头像已换（%d 字节）" % len(png_bytes)

    def clear_avatar(self):
        """删掉头像，回到默认那张画的。"""
        if not self.card_dir:
            return False, "没有卡目录"
        p = os.path.join(self.card_dir, AVATAR_FILE)
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception as e:
            return False, "删头像失败：%s" % e
        return True, "头像已恢复默认"

    # ---------- 卡文件（identity.json） ----------
    def card_path(self, path=None):
        if path:
            return path
        if self.card_dir:
            return os.path.join(self.card_dir, "identity.json")
        return None

    def save_card(self, values, path=None):
        """把明文身份写到卡文件里（放在卡根目录，一眼能看出这是谁的卡）。"""
        p = self.card_path(path)
        if not p:
            return None
        data = {
            "_comment": "这是这张卡的身份。改这里不会生效 —— 校验用的是 identity_anchor 表里的哈希。",
            "version": 2,
            "identity": {k: str(values.get(k, "")) for k in CORE_KEYS},
            "sha256": {k: _sha(values.get(k, "")) for k in CORE_KEYS},
        }
        # 性格单独放一栏：它住在 CORE 区，但**不进身份锚**（见文件顶部说明）。
        # 存明文是为了让主人一眼能看懂「这张卡里那个人是什么样的」。
        try:
            persona = self.get_persona("")
        except Exception:
            persona = ""
        if persona:
            data["persona"] = persona
            data["_persona_note"] = ("性格不参与 sha256 校验：它本来就要慢慢变。"
                                     "改它请用 set_persona()，别手改这个文件。")
        # 自述：一段连续的「我是谁」，跟 persona 一样不进锚。
        # 2026-10-31 加 —— 用户要的是「一个完整的你」，不是字段清单。
        try:
            narr = self.get_core("self_narrative") or ""
        except Exception:
            narr = ""
        if narr:
            data["self_narrative"] = narr
            data["_narrative_note"] = ("这是她写给自己的那段话，每轮上下文都会整段读进去。"
                                       "不进 sha256：它跟着记忆一起长。")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return p

    def load_card(self, path=None):
        p = self.card_path(path)
        if not p or not os.path.exists(p):
            return {}
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("identity", {})

    # ---------- CORE / LIVE 读写 ----------
    def get_core(self, key):
        row = self.db.execute(
            "SELECT val FROM core_zone WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_live(self, key, val):
        import time
        self.db.execute(
            "INSERT INTO live_zone(key,val,updated_ts) VALUES(?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET val=excluded.val,"
            " updated_ts=excluded.updated_ts",
            (key, str(val), time.time()))
        self.db.commit()

    def get_live(self, key, default=None):
        row = self.db.execute(
            "SELECT val FROM live_zone WHERE key=?", (key,)).fetchone()
        return row[0] if row else default
