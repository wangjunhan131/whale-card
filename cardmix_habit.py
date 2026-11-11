# -*- coding: utf-8 -*-
"""习惯与印象：`MemoryCard` 的一块。

拆门面第 6 批的第 1 块。这里只放方法，状态仍然在 MemoryCard 上
（`self.db` 之类的属性由 MemoryCard.__init__ 提供），所以这个类
不能单独实例化。"""

import habits as habits_mod
import impression as impression_mod
import os
from cardcfg import (CLARITY_GC, DEDUPE_JACCARD, EMB_ADAPTER_VERSION, GAIN_REPEAT,
                     GROUP_SAL_MAX, GROUP_SOURCE_PREFIX, KEEP_DETAIL, RE_NUM, RE_PLACE,
                     SHORT_MAX_TTL, THETA_LONG_STRICT, THETA_MEDIUM, TIER_MID,
                     TRUST_BLOCK_PROMOTE, TRUST_WARN, W_CEIL_RECALL, W_FLOOR_LONG,
                     W_FLOOR_MEDIUM)
from poolmath import (POOL_LONG, POOL_MEDIUM, POOL_SHORT, R_CHATTER, R_FLASH,
                      TIER_CORE_MUTABLE, _REGION_LAMBDA_ON, wn_row)
from cardtext import (classify_region, embed_text_for, guess_mtype, guess_speaker, jaccard,
                      region_by_source, self_speech_kind, split_memory, split_predicate,
                      strip_sysmark)
from cardtime import normalize_time, parse_event_time
from body import EMO_WORDS, assess_trust, assess_value, oxy_signal, update_oxy
from vmath import to_vec
from diag import soft_fail
import semantic as semantic_mod
import impression as impression_mod
import facts as facts_mod
import math
import os
import re


class MixHabit(object):
    """习惯 / 印象的读取与维护。方法原样搬来，一行没改。"""

    def impression_top(self, subject=None, limit=8, min_w=0.0):
        return impression_mod.top(self.db, subject=subject, limit=limit, min_w=min_w)

    def impression_hints(self, subject=None, limit=3, min_w=None):
        return impression_mod.hint_lines(self.db, subject=subject, limit=limit, min_w=min_w)

    def impression_snapshot(self):
        return impression_mod.snapshot(self.db)

    def impression_fade(self, now=None):
        return impression_mod.fade(self.db, now=now if now is not None else self.clock.now())

    def habit_hints(self, limit=5, max_chars=220):
        """从 habits 表捞几条稳定习惯，拼成短句给【我是谁】用。

        为什么要有它：卡里 565 条记忆全是「发生了什么」，没有一条是
        「我这人平时怎么说话」。挖出来的习惯如果只躺在报告里，
        那她还是那个每轮靠提示词临时扮演的自己。

        三条自我约束（都是为了别变成背台词）：
          1. 只取 stable / pinned，候选和衰减中的不进来 —— 凭印象说不如不说；
          2. 最多 5 条、总字数 ≤220（密度刹车）；
          3. 默认开，`WHALE_HABITS=0` 可一键关掉。
        表不存在（还没挖过）时返回空列表，不报错、不建表。
        """
        if os.environ.get("WHALE_HABITS", "1") == "0":
            return []
        try:
            rows = self.db.execute(
                "SELECT key, category, trigger, behavior, weight, confidence,"
                " status, pinned, hits FROM habits"
                " WHERE status IN (?, ?, ?) AND key != ''"
                " ORDER BY pinned DESC, weight DESC, confidence DESC, hits DESC"
                " LIMIT ?",
                (habits_mod.ST_STABLE, habits_mod.ST_PINNED, habits_mod.ST_WEAK,
                 max(1, int(limit) * 3))).fetchall()
        except Exception:
            return []
        out = []
        used = 0
        weak_n = 0
        for r in rows:
            st = str(r["status"] or "")
            if st == habits_mod.ST_WEAK:
                if weak_n >= habits_mod.HAB_WEAK_MAX:
                    continue
            trig = (r["trigger"] or "").strip()
            beh = (r["behavior"] or "").strip()
            key = (r["key"] or "").strip()
            if trig and beh:
                line = "当%s的时候，我一般会%s" % (trig, beh)
            elif key:
                line = "我会用「%s」这种说法" % key
            else:
                continue
            if st == habits_mod.ST_WEAK:
                line += "（这条还弱，别当口头禅硬套）"
                weak_n += 1
            if line in out:
                continue
            if used + len(line) > max_chars:
                break
            out.append(line)
            used += len(line)
            if len(out) >= int(limit):
                break
        return out
