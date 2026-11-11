# -*- coding: utf-8 -*-
"""卡的「写路径」：记忆怎么落库、原文怎么记、工作日志、向量元数据。

从 card2.py 拆出来（V7 第 20 件 6-1 步）。这里只放**写**：读的那一半还在 card2。

为什么下面有个模块级 __getattr__：这些方法是从一个巨大模块里原样搬出来的，方法体里
还会引用 card2 里的模块级常量和工具函数（`to_vec`、`split_memory`、`DAY` 之类）。
一个个解耦要动到几十处、每处都要回归，风险比收益大；惰性代理让它们在**运行期**
回 card2 要 —— import 期不成环，行为与搬之前逐字一致。以后哪个名字彻底独立了，
再从 card2 挪到叶子模块，代理会自动少用一条。
"""

import os
import json
import time
import sqlite3
import time

from cardmix_habit import MixHabit  # noqa: F401  （仅为保持「搬家前后同一个基类链」）
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




def _pid_alive(pid):
    """这个 pid 还活着吗？（Windows 用 OpenProcess，别的平台用 os.kill 0）

    为什么要它：库内租约在「进程被 kill -9 / 断电」之后会留下一条没到期的记录，
    不判存活就会让新进程白等一个 TTL（甚至一直只读）；但判「活的」也不能用
    「同主机就算旧」，因为副本打开时对方确实活着 —— 那正是要拦的场景。
    """
    try:
        pid = int(pid or 0)
    except Exception:
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        h = k32.OpenProcess(0x1000, False, pid)
        if not h:
            return False
        try:
            code = wintypes.DWORD()
            if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class MixStore:
    def remember(self, text, salience=0.6, ts=None, pool=None,
                 oxy=0.0, entities=None, emotion=None, normalize=True,
                 region=None, speaker=None, mtype=None, source="",
                 about_uid=""):
        now = self.clock.now()
        pool = POOL_SHORT if pool is None else pool  # 默认值里不能写 POOL_SHORT：类定义时 card2 还没 import 完
        at = min(now if ts is None else float(ts), now)
        age = max(0.0, now - at)

        # 🔧 2026-11-05：先剥系统工单壳，再谈记不记。
        #    壳（[dsh-mnemon:delegated write] + Execute this ... now）不是用户说的话，
        #    但它里面裹着的那句正文是。剥完若是空的，说明这本来就是一条工单、
        #    没有正文 —— 直接返回 None，不写、也不占用去重窗口。
        text = strip_sysmark(text)
        _plan_src = text  # 记约定要用**没被锚定**的原句：下面 normalize_time 会把
        # 「明天早上七点」改写成「11月12日早上七点」，再拿它去认时间词就只剩「早上」了。
        if not text:
            return None

        # 2026-11-09：群聊写入时把 salience 压下来。
        #    群里别人的闲话不该跟「我的号是多少」同权重 —— 这是写入侧的事，
        #    不该留给检索侧去补（见 recency 那段实测记录）。
        if str(source or "").startswith(GROUP_SOURCE_PREFIX):
            try:
                salience = min(float(salience), GROUP_SAL_MAX)
            except Exception:
                salience = GROUP_SAL_MAX

        # 🔧 时间归一化：把「今天/昨天/刚刚」换成绝对日期。
        #    必须用「这句话说出来的时刻」at 做参照，不能用 now。
        if normalize:
            text = normalize_time(text, at)

        core, details = split_memory(text)
        vec = to_vec(text)

        # 情绪强度：从文本里估
        if emotion is None:
            emotion = 1.0 if any(w in text for w in EMO_WORDS) else 0.25
        # 实体数：地点 + 数字
        if entities is None:
            entities = len(RE_PLACE.findall(text)) + len(RE_NUM.findall(text))
            entities = min(entities, 5)
        # 🔧 分区：不传就自动判
        _explicit_region = region is not None
        if region is None:
            region = classify_region(text, emotion)
        # 🔧 2026-11-06（V5.2）：按来源定区，不猜内容。
        #    只在调用方没显式传 region 时生效 —— 显式传的一律照传的走。
        if _REGION_LAMBDA_ON and not _explicit_region:
            region = region_by_source(source, region)

        # 🔧 2026-11-05 闲聊寿命按「对谁」来定（用户 m13292）。
        #    关系层在写入时算好存下（衰减是热路径，不能每次现查 core_tag）。
        _lay = ""
        if about_uid:
            try:
                _lay = self.person_layer_of(about_uid)
            except Exception:
                _lay = ""
        if region == R_CHATTER and not _lay:
            # 没登记过的人：群里见过的按 known，其余按默认
            _lay = "known" if str(source or "").startswith("qq") else ""

        # 🔧 2026-11-01 防污层（提前算：下面的去重路径也要看它）
        _trust, _conflict, _why = assess_trust(text, self)

        # 🔧 惊讶度 + 去重：跟最近 200 条比。
        #    Jaccard ≥ 0.72 视为「同一件事又说了一遍」→ 不新增，给老条目续命。
        #    flash 区不参与合并（闪光灯记忆一次就刻住）。
        surprise = 0.5
        dup_id = None
        # 🔧 V5.0 T1-3：先算这句话的亲密/伤人信号，全局 oxy 跟着动
        s_plus, s_minus = oxy_signal(text)
        if s_plus > 0 or s_minus > 0:
            self.oxy = update_oxy(getattr(self, "oxy", 0.0), s_plus, s_minus)
        # 🔧 V5.0 T2-3：心境也跟着推一下（慢，一句推不翻）
        if (speaker or "") != "self":  # 只有"听到的话"推心境；自己的回复不推
            self.touch_mood(text, emotion)
        # 🔧 V5.0 T3-2：四个激素也跟着扰动（内分泌层）
        self.touch_hormones(text)
        # 🔧 2026-11-09：欲望层接线 —— motiv_on_chat 定义了却从未被调用，
        #    导致七条驱力永远停在基线、上下文里也没有「我想干什么」这一格。
        #    在 remember() 这里调：每说一句话，依恋和关心泄掉一点（满足感）。
        try:
            self.motiv_on_chat()
        except Exception:
            pass
        # 🔧 2026-11-06（体检修）：完全相同的句子，隔多远都算同一条。
        #    Jaccard 只跟「最近 200 条」比 —— 隔了 300 条就漏，真卡里因此留下
        #    6 组孪生记录（#43/#326、#44/#325、#94/#327、#160/#362、
        #    #296/#485、#297/#472）。代价不只是占一行：同一条命被两行分着背，
        #    召回时还可能两条一起被捞出来占两个名额。
        #    精确全等查一次（七百来行的表扫一遍是亚毫秒），命中就走同一条「续命」路径。
        _exact_id = None
        if region != R_FLASH and _trust >= TRUST_WARN and not _conflict:
            try:
                _er = self.db.execute(
                    "SELECT id FROM mem WHERE raw=? AND region!=?"
                    " ORDER BY id DESC LIMIT 1",
                    (text, R_FLASH)).fetchone()
                if _er is not None:
                    _exact_id = _er[0]
            except Exception:
                _exact_id = None
        try:
            recent = []
            if _exact_id is None:
                recent = self.db.execute(
                    "SELECT id, vec, region FROM mem ORDER BY id DESC LIMIT 200"
                ).fetchall()
            best = 0.0
            if recent:
                for r in recent:
                    j = jaccard(vec.keys(), json.loads(r["vec"]).keys())
                    if j > best:
                        best = j
                        dup_id = r["id"]
                surprise = 1.0 - best
            if _exact_id is not None:
                dup_id = _exact_id
            if _exact_id is not None or (
                    best >= DEDUPE_JACCARD and region != R_FLASH
                    and _trust >= TRUST_WARN and not _conflict):
                    # 可信的重复句才续命；被灌进来的话不许借老记忆的壳活着
                    # 同一件事说第二遍：不加新条目，只把老条目往上抬
                    c0 = self.db.cursor()
                    old = c0.execute("SELECT * FROM mem WHERE id=?",
                                     (dup_id,)).fetchone()
                    wn = wn_row(old, now)
                    # 同一条记忆的 oxy 也按这句话的信号更新（但不低于全局值的七成）
                    new_oxy = update_oxy(old["oxy"], s_plus, s_minus)
                    new_oxy = max(new_oxy, self.oxy * 0.7)
                    # 🔧 2026-11-05：去重这条「续命」路径跟召回那条是同一个病。
                    #    旧写法 min(1.0, wn+0.10) 每重复一次加 0.10 并把锚推到此刻
                    #    —— 推锚 = 衰减计时归零，于是「天天说晚安」那条会一夜之间
                    #    钉死在 1.0，而且越重复越钉得牢（这跟召回正反馈是一回事，
                    #    只不过这里是写在写路径上）。
                    #    照召回那套处理：增益乘剩余空间 + 天花板。
                    _g = GAIN_REPEAT * max(0.0, 1.0 - float(wn))
                    _ceil = W_CEIL_RECALL.get(old["pool"], 0.95)
                    c0.execute(
                        "UPDATE mem SET w=?, t_anchor=?, access_count=access_count+1,"
                        " recall_n=COALESCE(recall_n,0)+1, t_last_recall=?,"
                        " last_active_ts=?, oxy=? WHERE id=?",
                        (min(_ceil, wn + _g), now, now, now, new_oxy, dup_id))
                    c0.execute("UPDATE detail SET weight=MIN(1.0, weight+?)"
                               " WHERE parent_id=?", (_g * 0.8, dup_id))
                    self.db.commit()
                    self._save_oxy()      # 去重路径也要落盘
                    return dup_id
        except Exception as e:
            # 去重路径挂了不该拖垮「记一条新的」，但必须留痕：
            # 这里静默失败过一次，就会表现为「同一句话重复进库」。
            soft_fail("remember_dedupe", e, "判重失败，本条按新记忆写入")

        # 基础权重：情绪 + 惊讶度
        w0 = (0.50 + 0.30 * min(emotion, 1.0)) * (0.75 + 0.50 * surprise)
        w0 = max(0.20, min(w0, 1.0))

        # 🔧 2026-11-01 防污层：可疑内容照写（"她被骗过"也是经历），
        #    但把信任分乘进权重——进得来，升不上去。
        if _trust < 1.0:
            w0 = max(0.05, w0 * _trust)

        # 🔧 2026-11-01 价值分：只记录，不拦门。控制台看分布，
        #    将来要拦再用（WHALE_VALUE_GATE）——现在不改变任何写入行为。
        _value = assess_value(text, region, salience)

        # 🔧 V5.0 T1-3：新记忆的 oxy 取「全局依恋」与「这条自身的信号」的较大者。
        #    这样一句亲密话既抬高全局 oxy，也让它自己带亲密标记（进海马打分）。
        if s_plus > 0 or s_minus > 0:
            oxy = max(oxy, self.oxy)
        else:
            oxy = max(oxy, self.oxy * 0.5)

        # 细节衰减也要按池放慢，否则长期档案一进来细节就全丢了
        dscale = {POOL_SHORT: 1.0, POOL_MEDIUM: 0.25, POOL_LONG: 0.03}[pool]

        # 🔧 V5.0 T2-1：主干拆成 主语/动词/宾语，供分级侵蚀用
        _subj, _verb, _obj = split_predicate(core)

        # 🔧 T4-3：存的时候顺手把语义向量算出来（约 70ms，一次性的）。
        #    失败就留 NULL —— backfill_embeddings() 以后还能补，不影响正确性。
        _emb = None
        if self.sem_ready():
            try:
                _emb = self.semantic.encode(embed_text_for(text, speaker, core))
            except Exception:
                _emb = None

        c = self.db.cursor()
        # 🔧 2026-10-31 双边记忆：不传 speaker 就猜一个（宁可猜错也别空着，
        #    空着就退回「半本日记」）。mtype 判指令还是事。
        if speaker not in ("user", "self", "scene"):
            speaker = guess_speaker(text)
        if mtype not in ("episode", "instruction"):
            mtype = guess_mtype(text)
        _quar = 1 if (_trust < TRUST_BLOCK_PROMOTE or _conflict == 1) else 0
        # V7 第 9 件：把「这件事哪天发生的」单独记一份。
        #   「我昨天去剪头发」写下来的时刻不是剪头发那天；不分开，
        #   隔三天问「你哪天剪的」按时间窗一筛就走错门。
        #   解析不到时间词就留 0（= 未知），检索时退回 create_ts，
        #   但至少它被标成「不知道哪天」，而不是被当成今天。
        _evts, _evprec = parse_event_time(text, ref_ts=at)
        c.execute(
            "INSERT INTO mem(raw,core,vec,pool,w,t_anchor,salience,oxy,entities,"
            "emotion,create_ts,last_active_ts,region,subj,verb,obj,erase_stage,emb,"
            "speaker,mtype,trust,conflict,conflict_why,value,source,"
            "about_uid,about_layer,quarantine,event_ts,event_precision) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (text, core, json.dumps(vec, ensure_ascii=False), pool, w0, at,
             salience, oxy, entities, emotion, at, at, region,
             _subj, _verb, _obj, 0, _emb, speaker, mtype,
             _trust, _conflict, _why, _value, source or "",
             str(about_uid or ""), _lay, _quar, float(_evts or 0.0), _evprec or ""))
        mid = c.lastrowid
        # V5.4：正式入库的记忆也在收件层留一份原文（kept=1）。
        # 这样短期池淘汰之后，60 小时内还能从收件层把它原样翻回来 —
        # 「原文进短期」不再依赖我每轮都记得调 /report。
        try:
            self._ensure_inbox(c)
            c.execute(
                "INSERT INTO inbox(ts,source,channel,who,uid,gid,kind,text,"
                "kept,kept_mid,note) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (at, source or "", "private", "", str(about_uid or ""), "",
                 mtype or "text", text, 1, mid, "remember()"))
        except Exception as _ibe:
            print("[card2] 收件层留档失败: %s" % _ibe)
        if _emb is not None:
            self._emb_cache[mid] = semantic_mod.from_blob(_emb)
            self.emb_record(n=1)
        for kind, txt, wgt in details:
            k = {"TIME": 4.5e-6, "NUMBER": 4.5e-6, "MODIFIER": 4.0e-6,
                 "PLACE": 3.2e-6, "EMOTION": 1.2e-6}.get(kind, 4.0e-6)
            ww = wgt * math.exp(-k * dscale * age)
            if ww < KEEP_DETAIL:
                continue
            c.execute("INSERT INTO detail(parent_id,kind,text,weight,last_touch)"
                      " VALUES(?,?,?,?,?)", (mid, kind, txt, ww, at))
        self.db.commit()
        # 语义近邻：拿刚存的 emb 直接算，不重新编码。放在 commit 之后，
        # 补边失败也不能连累这条记忆本身。
        if _emb is not None:
            try:
                self.synapse.add_semantic_edges([mid], [_emb])
            except Exception as _se:
                print("[card2] 语义边补建失败: %s" % _se)
        # 共现：这条和「上一条」连一笔（同一个人优先，没有就按全局）
        try:
            _au2 = str(about_uid or "")
            _prev = c.execute(
                "SELECT region FROM mem WHERE id<? AND COALESCE(dead,0)=0 "
                "AND (?='' OR COALESCE(about_uid,'')=?) "
                "ORDER BY id DESC LIMIT 1",
                (mid, _au2, _au2)).fetchone()
            if _prev and _prev[0]:
                self._bump_expected(c, _prev[0], region, at)
            self.db.commit()
        except Exception:
            pass
        # 关系卡：谁又出现了一次（只记频次与分区，不记评价）
        try:
            if about_uid:
                self.person_touch(about_uid, self.person_layer_of(about_uid),
                                  ts=float(ts or self.clock.now()), region=region)
        except Exception as _pe:
            print('[card2] 关系卡挂钩失败: %s' % _pe)
        self._invalidate_idf()    # 库变了，攒够 20 条再重算（不每次清空）
        self._save_oxy()          # 全局依恋落盘
        # 🔧 2026-11-06（V5.2）：记一条负载，供「今天挤不挤」用。
        #    放在 commit 之后：负载只是调节量，写失败了也不能连累这条记忆。
        try:
            self._load_bump(region, now)
        except Exception:
            pass
        # ---- 印象层：记一笔「谁在什么情境下说了什么」 --------------------
        # 只记账、不判断值不值得：筛选是短期池的事，印象是"很多次剩下的形状"。
        #
        # 🔧 2026-11-09（用户「印象生成那方面还有没有问题」）：这里以前不分说话人，
        #    自己的回复也照样记账 —— 而 mind_server 给自己的回复也传了
        #    about_uid=local_uid，于是「谁」被记成了他。真卡实测 144 条印象里
        #    109 条挂在他名下、内容是她的开发汇报（「20261108凌晨V5.5：短期池…」）。
        #    印象层的出口是 hint_lines —— 那句子的主语是「他常说」，
        #    自己的话一旦混进来，她就会把自己的技术日志当成他的口头禅。
        #    所以：自己的话不进印象层。她自己的说话习惯归 habits 层（那边挖的是 self）。
        if (speaker or "") != "self":
            try:
                impression_mod.observe(
                    self.db,
                    subject=(about_uid or ("user" if (speaker or "") == "user" else "")),
                    text=text, ts=(ts if ts is not None else self.clock.now()),
                    subject_kind=("person" if (about_uid or speaker) else "thing"),
                    now=self.clock.now())
            except Exception as _ie:
                print("[card2] 印象记账失败: %s" % _ie)

        # ---- 事实层（V5.7）：抽「谁 · 什么事 · 什么」-------------------------
        # 只做索引，不改原文。抽不出就不抽（宁可少一条，不要错一条）。
        # 用**未锚定**的 text：锚定后的日期会混进 object，抽出来是错的。
        try:
            _n_fact, _ = (0, []) if os.environ.get("WHALE_FACTS", "1") == "0" else facts_mod.record(
                self.db_path, mid, text,
                ts=(ts if ts is not None else self.clock.now()),
                speaker=(speaker or "user"), source=(source or ""))
            if _n_fact:
                pass
        except Exception as _fe:
            print("[card2] 事实抽取失败: %s" % _fe)

        # 约定与待办（2026-11-11）：句子里有将来的时间词、又带承诺口气的，
        # 记成一条 pending。到点没兑现由 sweep() 判 expired，late_hours 交给情绪层。
        # 写在 facts 之后、return 之前：这时候 mid 已经落地，失败也不该拖垮写入。
        try:
            if os.environ.get("WHALE_TEMPORA", "1") != "0":
                # 两边都记：他说「明天要去学校」是约定，我说「明天帮你查」也是约定。
                # subject 区分是谁许的，失约时才知道该谁难受。
                _cancel = any(w in _plan_src for w in
                              ("不去了", "取消了", "不开了", "不办了", "改天再说", "不来了", "算了不"))
                if _cancel:
                    # 「明天那事不去了」：把那条 pending 标 cancelled，而不是又记一条约定
                    self._tevent().cancel(_plan_src, ts=at)
                else:
                    self._tevent().add_plan(_plan_src, ts=at,
                                            subject=(speaker or "user"),
                                            source=(source or ""), importance=0.6)
        except Exception as _te:
            soft_fail("tempora_plan", _te)

        return mid

    def add_dialogue(self, mem_id, user_text, reply_text, ts=None, source=None):
        """给一条记忆补上「当时的原话 + 我当时的回答」

        T5-2 用：记忆是提炼过的（主干+细节+权重），原文另存。
        原文不进向量库 —— 它不参与召回，只用于「翻回原话」。
        """
        if not user_text:
            return None
        now = self.clock.now()
        t = float(ts) if ts else now
        # source 列是 NOT NULL（DDL 默认 'session'），显式写 None 会撞约束。
        # 2026-11-09 查出来的：这条 INSERT 一直在抛 IntegrityError，
        # 而调用方用 `except Exception: pass` 吞掉 —— 于是「/report 能写记忆，
        # 但对话原文一条都没落盘」，从 10 月底一直静默到现在。
        src = (source or "session")
        cur = self.db.cursor()
        cur.execute(
            "INSERT INTO dialogue(mem_id,ts,role,text,source) VALUES(?,?,?,?,?)",
            (int(mem_id), t, "user", str(user_text), src))
        if reply_text:
            cur.execute(
                "INSERT INTO dialogue(mem_id,ts,role,text,source) VALUES(?,?,?,?,?)",
                (int(mem_id), t + 1, "assistant", str(reply_text), src))
        self.db.commit()
        return cur.lastrowid

    def log_dialogue(self, mem_id, role, text, ts=None, source=None):
        """把一条原文记进 dialogue（单条，不成对）。

        为什么需要它（2026-11-09）：dialogue 原来只有一个写入口 add_dialogue，
        而它只被 /report（一问一答）调用。QQ / B站 / 抖音这些通道走的是
        /remember，于是**原文表从 10-28 起就再没长过一条**：
          · 「最近对话」翻回原话，翻到的是两周前的；
          · 习惯挖掘读的正是 dialogue —— 挖到的是十月底的口癖；
          · 梦层（dream_pending）永远 pending=0，因为对话原文根本没落盘。
        这里补的是「我听过/说过的一句话」，不需要配对。
        """
        if not text:
            return None
        t = float(ts) if ts else self.clock.now()
        role = "assistant" if (role or "") in ("self", "assistant") else "user"
        cur = self.db.cursor()
        cur.execute(
            "INSERT INTO dialogue(mem_id,ts,role,text,source) VALUES(?,?,?,?,?)",
            (int(mem_id or 0), t, role, str(text), (source or "session")))
        self.db.commit()
        return cur.lastrowid

    def _consolidate(self, r, now):
        """短期池结算：算海马分，够格进中期/长期；不够格的到期沉到中期（V5.6.3 起不再删）"""
        score = self._hippocampus_score(r)
        # 他本人说的话：+0.30，不是免筛。
        # （免筛实测会把 133 条短期里的 103 条全塞进中期，短期池就形同虚设；
        #   而海马分那三样——情绪/实体/被想起——对他随口一句全是零，
        #   一点不加又等于在删他的话。加分是两头都顾得住的那个解。）
        if (r["speaker"] or "") == "user":
            score += self.USER_SPEECH_BONUS
        # 🔴 2026-11-09（P12-NEW-002 暴露的 bug）：**我说过的话也必须记。**
        #    症状：780 条写入里，他说的存活 82.3%，我说的只有 23.3% ——
        #    被删掉 299/390 条，几乎全是我自己讲过的内容。库里最后只剩下
        #    「他问过我什么」，却没有「我当时怎么答的」，这就是最像失忆的状态。
        #    直接后果：题面问「你之前说自己理零票，第一步怎么做？」，
        #    答案「我会先按日期分堆」写在 5月5日那轮里，7 天结算时被当成
        #    「淡到看不见」删掉了 —— 不是模型不会答，是这句话根本不在了。
        #
        #    为什么原来没有这一刀：本意是「他说的是事，我说的是反应」，
        #    所以写入时 salience 给低（0.45 vs 0.62）。但 consolidate 这一关
        #    完全没看 salience，只看海马分；而海马分的四项（情绪/实体/被想起/
        #    亲密）对「我自己在说话」天生偏低 —— 于是低 salience 变成了
        #    「必然被删」，惩罚被放大了好几倍。
        #
        #    改法：给 self 一个同级加成。实测 P12 全量 390 轮：
        #      不加      → self 存活 23.3%，user 82.3%
        #      +0.22     → 73.6%
        #      +0.32     → self 80.8% / user 80.3%（两侧对齐）
        #    取 0.32 与 USER_SPEECH_BONUS 同值：两条都是「我们说过的原话」，
        #    没有道理差一个量级。salience 差（0.45/0.62）继续保留在
        #    权重与召回打分里 —— 那是「反应比事轻」的正确表达，不该在这里
        #    变成生死判定。
        if (r["speaker"] or "") == "self":
            _kind = self_speech_kind(r["raw"])
            score += self.SELF_BONUS_BY_KIND.get(_kind, self.SELF_SPEECH_BONUS)
        # 重复短句（7 天内同句 ≥3 次）打折：价值在「他常这么说」的模式里，
        # 不在这一条本身，让它留在短期按自己的曲线走完。
        if self._is_repeat_short(r, now):
            score *= self.REPEAT_SHORT_PENALTY
        c = self.db.cursor()
        # 🔧 2026-11-09（用户 m04803「底层和重要的那些，要让它进不会忘的区域」）：
        #    pin=1 的记忆是钉住的 —— 不判遗忘、不评分数、原地留在长期，
        #    权重给到存活下限之上，让它永远想得起来。
        #    谁被钉住由 `_repartition_pools.py` 一次性标记，之后新写入的
        #    规则/底线也可以显式钉（remember(pin=True) 目前没开，留口子）。
        try:
            if int(r["pin"] or 0):
                # 2026-11-09（用户 m04851）：长期池分两档 ——
                #   ①不衰减：pin=1，tier 提到 core，weight_now 直接返回原值，
                #     时间对它不起作用（不是「衰减得慢」，是「不衰减」）。
                #   ②长衰减：pool=long 但 tier=mid，走 LAM_L（半衰期 371 天 +
                #     90 天保护期）—— 会褪，但褪得极慢。
                # 注意：不刷新 t_anchor —— 钉住是「不衰减」，不是「永远最新」。
                # 刷新锚点会让它在时间线上压过所有真・最近发生的事（实测会把
                # 「你自己叫什么」压到一条「大肥鱼号被封」上）。
                if r["tier"] != TIER_CORE_MUTABLE or r["pool"] != POOL_LONG:
                    c.execute("UPDATE mem SET pool=?, w=?, tier=? WHERE id=?",
                              (POOL_LONG, 1.0, TIER_CORE_MUTABLE, r["id"]))
                return POOL_LONG
        except Exception:
            pass
        # 🔧 2026-11-08（V5.6.3）：权重已经被时间磨到看不见的，不必等分数判 ——
        #    它什么都不剩了，抬进中期只是给它续命。先判这一刀，再谈晋升。
        if wn_row(r, now) < CLARITY_GC:
            self._forget_row(c, r["id"], "池子里淡到看不见了")
            return None
        # 🔧 2026-11-01 防污层：可信度太低的，只许进中期、不许进长期。
        #    它不是被删掉（"她被骗过"要留着），只是永远升不上去。
        _trust = float(r["trust"] if "trust" in r.keys() else 1.0)
        _conflict = int(r["conflict"] if "conflict" in r.keys() else 0)
        _dirty = (_trust < TRUST_BLOCK_PROMOTE) or _conflict == 1
        # 🔧 2026-11-05：闲聊不许进长期。
        #    它本来就该一天内淡掉，进了长期等于永不消散 —— 那等于白设这个区。
        if r["region"] == R_CHATTER:
            _dirty = True
        # 🔧 2026-11-05：闲聊不参加海马评选。
        #    它的三样分数（显著度/情绪/被想起）天生都是零，必然"没上榜"被删 ——
        #    那不是"明天忘光"，是"进门就死"。直接落中期，让它按 chatter 的
        #    λ 自己淡掉（8.77 小时后自然归零，不需要海马再判一次）。
        if r["region"] == R_CHATTER:
            # 只换池名，w 和 t_anchor 一律不动 —— 闲聊的衰减由 LAM_CHATTER
            # 独立决定（weight_now 对 chatter 不看池），抬到存活线等于给它续命。
            # 已经掉到回收线以下的，直接走正常遗忘。
            if wn_row(r, now) < CLARITY_GC:
                self._forget_row(c, r["id"], "闲聊到期，自然淡出")
                return None
            c.execute("UPDATE mem SET pool=?, pool_in_ts=? WHERE id=?",
                      (POOL_MEDIUM, now, r["id"]))
            return POOL_MEDIUM
        if (not _dirty) and score >= THETA_LONG_STRICT and self._long_ticket_ok(r):
            target, floor = POOL_LONG, W_FLOOR_LONG
        elif score >= THETA_MEDIUM:
            target, floor = POOL_MEDIUM, W_FLOOR_MEDIUM
        else:
            # 🔧 2026-11-08（V5.5，用户拍板）：恢复「没进中期就留在短期，7 天忘光」。
            #    2026-11-06 那版是「没上榜也降级到中期」，中期半衰期 29.7 天，
            #    等于给本该忘掉的东西续命六倍，于是池子里几乎什么都不删。
            #
            #    例外一：**他本人说的话**免筛直通中期 —— 海马分那三样
            #    （情绪/实体/被想起）对他随口一句「今天好累」全是零，
            #    按分数判就等于在删他的话。这是他 m36485 拍板要的。
            #    例外二：**重复的短句**不给免筛 —— 他自己举的例子：
            #    「我回来了」这种话每天都说，放进短期就够了，不该每次都占中期。
            #    这类句子同时喂给印象模块计数（见 impression.py），
            #    留下的是「他回来会说这句」，不是每一句本身。
            # 🔧 2026-11-08（V5.6.3，用户拍板「A」）：到期不再删，改成「沉底」。
            #    依据：714 题里答不出的 304 题，**278 题的证据在库里根本没有**
            #    （只有 3 题是「有但没召到」）；运行卡上写进去的四分之一到一半
            #    被 7 天结算删掉。会忘这件事该发生在中期→长期那段（半衰期 29.7 天），
            #    不该发生在第七天那一刻。沉底只换池名，w 与 t_anchor 一律不动，
            #    让它接着按自己的曲线往下走；真淡到看不见了才删。
            # 🔧 2026-11-09（修池子漂移）：沉底不再无条件进中期。
            #    原来 age > 7天 就 `pool='medium'`，导致 901 条低分记忆被抬走
            #    （抽样：58/60 条海马分 <0.35 却进了中期）。
            #    现在：满 7 天且分数 < THETA_MEDIUM（0.35）→ 留在短期继续淡；
            #    分数 ≥ THETA_MEDIUM 的才沉底到中期（跟上面 score gate 一致）。
            #    真淡到 CLARITY_GC 以下才会被删（上面已判）。
            age = now - float(r["t_anchor"] or now)
            if age > SHORT_MAX_TTL:
                if score >= THETA_MEDIUM:
                    c.execute("UPDATE mem SET pool=?, pool_in_ts=? WHERE id=?",
                              (POOL_MEDIUM, now, r["id"]))
                    return POOL_MEDIUM
                # 分数不够中期线，留在短期继续衰减，不抬走
                return None
            return None
        # 先按旧池结算，再抬到新池存活下限
        wn = wn_row(r, now)
        wn = max(wn, floor)
        tier = TIER_MID
        c.execute("UPDATE mem SET pool=?, w=?, t_anchor=?, tier=?,"
                  " pool_in_ts=? WHERE id=?",
                  (target, wn, now, tier, now, r["id"]))
        return target


    # ---- 库内写者租约（V7 第 2 件）----
    # 说明写在 _init_schema 建表处；这里只负责读写。
    def lease_db_get(self):
        """读库内租约（只读）。返回 {"holder","epoch","expires_ts","remaining_s"} 或 None。"""
        try:
            r = self.db.execute(
                "SELECT holder, epoch, pid, host, expires_ts FROM writer_lease WHERE id=1"
            ).fetchone()
        except Exception:
            return None
        if not r:
            return None
        return {"holder": r[0], "epoch": r[1], "pid": r[2], "host": r[3],
                "expires_ts": r[4], "remaining_s": max(0.0, float(r[4]) - time.time())}

    def lease_db_check(self, holder, epoch=0, ttl=90.0, now=None):
        """占库内租约。别人还活着就不占，返回 {"ok": False, "holder": ...}。

        为什么不是「无条件覆盖」：这张库可能刚从另一台机器拷过来，
        覆盖就等于替对方宣布「你已经不写了」，那两份会一起写。
        """
        now = float(now if now is not None else time.time())
        cur = self.lease_db_get()
        if cur and cur["holder"] and cur["holder"] != holder and cur["expires_ts"] > now:
            same_host = (cur["host"] or "") == (os.environ.get("COMPUTERNAME") or "?")
            # 同主机还要看对方进程是否真的活着：进程没了（kill -9 / 断电）就不该白等一个 TTL。
            if not (same_host and not _pid_alive(cur["pid"])):
                return {"ok": False, "reason": "db_lease_held",
                        "holder": cur["holder"], "remaining_s": cur["remaining_s"]}
            # 走到这里是「同主机 + 对方进程已死」→ 按过期处理，直接接手。
        try:
            self.db.execute(
                "INSERT INTO writer_lease(id, holder, epoch, pid, host, expires_ts, updated_ts)"
                " VALUES(1,?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET holder=excluded.holder, epoch=excluded.epoch,"
                " pid=excluded.pid, host=excluded.host, expires_ts=excluded.expires_ts,"
                " updated_ts=excluded.updated_ts",
                (holder, int(epoch or 0), os.getpid(), (os.environ.get("COMPUTERNAME") or "?"),
                 now + float(ttl), now))
            self.db.commit()
            return {"ok": True, "holder": holder, "expires_ts": now + float(ttl)}
        except Exception as e:
            soft_fail("lease_db_put", e, holder)
            return {"ok": False, "reason": "db_lease_write_failed:%s" % e}

    def lease_db_touch(self, holder, ttl=90.0, now=None):
        """续期（心跳用）。持有者不是自己就什么都不做。"""
        cur = self.lease_db_get()
        if cur and cur["holder"] and cur["holder"] != holder:
            return False
        now = float(now if now is not None else time.time())
        try:
            self.db.execute(
                "UPDATE writer_lease SET expires_ts=?, updated_ts=? WHERE id=1 AND holder=?",
                (now + float(ttl), now, holder))
            self.db.commit()
            return True
        except Exception as e:
            soft_fail("lease_db_touch", e, holder)
            return False

    def lease_db_release(self, holder):
        """退出时交还（只交还自己的）。"""
        try:
            self.db.execute("DELETE FROM writer_lease WHERE id=1 AND holder=?", (holder,))
            self.db.commit()
            return True
        except Exception as e:
            soft_fail("lease_db_release", e, holder)
            return False

    def _archive_add(self, c, mid, reason):
        """把一条即将被忘掉的记忆整行抄进冷档（raw + 全部细节 + 语义向量）。

        为什么要有这一步：mem 里没有它，她平时就想不起来了 —— 这是"忘"；
        但内容还在硬盘上，问到非常具体的事时还能翻出来 —— 这是"没销毁"。
        两者不能混：**忘是检索行为，不是删除行为**。硬盘够大，就不该有删除。
        """
        try:
            r = c.execute("SELECT * FROM mem WHERE id=?", (int(mid),)).fetchone()
            if r is None:
                return
            keys = r.keys()
            det = [{"k": d["kind"], "t": d["text"], "w": d["weight"]}
                   for d in c.execute(
                       "SELECT kind,text,weight FROM detail WHERE parent_id=?",
                       (int(mid),)).fetchall()]
            def _g(k, default=""):
                return r[k] if k in keys and r[k] is not None else default
            emb = None
            try:
                emb = _g("emb", None)
            except Exception:
                emb = None
            c.execute(
                "INSERT OR REPLACE INTO mem_archive(mid,raw,core,speaker,region,pool,"
                "create_ts,last_active_ts,salience,subj,verb,obj,entities,about_uid,"
                "emb,details,reason,archived_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (int(mid), _g("raw"), _g("core"), _g("speaker"), _g("region"),
                 _g("pool"), float(_g("create_ts", 0) or 0),
                 float(_g("last_active_ts", 0) or 0),
                 float(_g("salience", 0) or 0), _g("subj"), _g("verb"), _g("obj"),
                 _g("entities"), _g("about_uid"), emb,
                 json.dumps(det, ensure_ascii=False), reason, time.time()))
        except Exception as e:
            print("[card2] 冷档归档失败 mid=%s: %s" % (mid, e))

    def _tomb_add(self, c, mid, reason):
        """在删除台账上记一笔（哈希必须趁记录还在的时候算）。"""
        h = ""
        try:
            r = self.db.execute(
                "SELECT id, raw, speaker, create_ts FROM mem WHERE id=?",
                (int(mid),)).fetchone()
            if r is not None:
                import timeline as _tl_mod
                h = _tl_mod._birth_hash(
                    r["id"], r["raw"] or "",
                    r["speaker"] if "speaker" in r.keys() else "",
                    r["create_ts"])
        except Exception:
            h = ""
        try:
            c.execute(
                "INSERT OR REPLACE INTO mem_tomb(mid,hash,ts,reason)"
                " VALUES(?,?,?,?)", (int(mid), h, time.time(), reason))
        except Exception:
            pass

    def worklog_add(self, title, body="", next_step="", kind="work", source="", ts=None):
        title = (title or "").strip()
        if not title:
            return None
        ts = float(ts if ts is not None else time.time())
        # card2 的连接属性叫 self.db（自己不加锁，串行化靠 mind_server 的 mind.lock）。
        c = self.db

        # 常换常新（用户 2026-11-10 提）：同一件事反复写，不该堆成十条。
        # 6 小时内标题几乎一样的，就地更新那一条（补新正文/新下一步），不新增。
        # 2026-11-10 修：原来只比前 6 个字，「V7-P0-1 搬家清单」和「V7-P0-4 写者租约」
        # 前 6 字都是「V7-P0-」，被当成同一条，于是四件不同的活全挤进一行。
        # 改成整体相似度判定，并且更新时连标题一起刷新（否则列表里永远是最早那个标题）。
        try:
            _last = c.execute(
                "SELECT id,ts,title FROM worklog ORDER BY ts DESC, id DESC LIMIT 6"
            ).fetchall()
            _b = title[:200]
            for _r in _last:
                if ts - float(_r["ts"] or 0) > 6 * 3600:
                    break
                _a = str(_r["title"] or "")
                if _a == _b:
                    _same = True
                else:
                    try:
                        import difflib as _dl
                        _same = _dl.SequenceMatcher(None, _a, _b).ratio() >= 0.86
                    except Exception:
                        _same = False
                if _same:
                    c.execute(
                        "UPDATE worklog SET ts=?, title=?, body=?, next_step=?, kind=?"
                        " WHERE id=?",
                        (ts, _b, (body or "").strip()[:1200],
                         (next_step or "").strip()[:300],
                         (kind or "work").strip() or "work", int(_r["id"])))
                    c.commit()
                    return int(_r["id"])
        except Exception as e:
            soft_fail("worklog_dedupe", e, "工作日志判重失败，本条按新日志写入")

        cur = c.execute(
            "INSERT INTO worklog(ts,kind,title,body,next_step,source)"
            " VALUES(?,?,?,?,?,?)",
            (ts, (kind or "work").strip() or "work", title[:200],
             (body or "").strip()[:1200], (next_step or "").strip()[:300],
             (source or "").strip()[:60]))
        c.commit()
        return cur.lastrowid

    def worklog_recent(self, n=6, kind=None):
        n = max(1, min(50, int(n or 6)))
        sql = "SELECT id,ts,kind,title,body,next_step FROM worklog"
        args = []
        if kind:
            sql += " WHERE kind=?"
            args.append(kind)
        sql += " ORDER BY ts DESC, id DESC LIMIT ?"
        args.append(n)
        rows = self.db.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def worklog_text(self, n=5, budget=320):
        """装配上下文用：先给「手上还没干完的」，再给最近的，压成几行。

        为什么分两档：只是「最近几条」的话，一整天的琐碎能把昨天那条
        「下一步：重启生产验证」挤出视野 —— 那正是换窗口后最该看见的一句。
        """
        try:
            rows = self.worklog_recent(n=max(n * 3, 12))
        except Exception:
            return ""
        if not rows:
            return ""
        now = time.time()
        pend = [r for r in rows
                if (r.get("next_step") or "").strip()
                and now - float(r.get("ts") or 0) < 48 * 3600]
        pid = {r["id"] for r in pend}
        rows = pend + [r for r in rows if r["id"] not in pid]
        rows = rows[:max(n, 6)]
        lines = []
        used = 0
        for r in rows:  # 已经是最新在前
            d = time.strftime("%m-%d %H:%M", time.localtime(float(r["ts"] or 0)))
            head = "- %s｜%s" % (d, r["title"])
            tail = ""
            if r["next_step"]:
                tail = "｜下一步：" + r["next_step"]
            body = ""
            if r["body"]:
                body = "：" + r["body"]
            line = head + body + tail
            if used + len(line) > budget:
                # 先试着把正文丢掉，只留标题+下一步
                line = head + tail
                if used + len(line) > budget:
                    break
            lines.append(line)
            used += len(line)
        return "\n".join(lines)

    def worklog_prune(self, days_work=14, days_keep=120, now=None):
        """常换常新：工作台不是档案柜，旧的该走就走。

        - kind=work 的条目：超过 days_work 天直接删（活干完了、也接不上现在这摊事）。
        - decision/bug/note：留 days_keep 天（这类是"为什么这么定"的依据，留久一点）。
        原文和记忆都在卡里，工作台清掉不会丢任何事实。
        """
        now = float(now if now is not None else time.time())
        c = self.db
        try:
            n1 = c.execute("DELETE FROM worklog WHERE kind='work' AND ts < ?",
                           (now - days_work * 86400.0,)).rowcount
            n2 = c.execute(
                "DELETE FROM worklog WHERE kind!='work' AND ts < ?",
                (now - days_keep * 86400.0,)).rowcount
            c.commit()
            return int(n1 or 0) + int(n2 or 0)
        except Exception:
            return 0

    def emb_record(self, n=1, model=None, dim=None, now=None):
        """记一笔「这个模型在这儿写过向量」。失败只记 soft_fail，不打断写入。

        为什么不是每行 mem 存一个 emb_model 列：换模型时最要紧的是
        「这张卡里到底混了几代向量」和「它们分别多少条」，按行存反而要
        改 schema、还要按行迁移；按模型聚合一张小表就够回答这些问题了。
        """
        nm = (model or self._emb_model_name() or "unknown").strip() or "unknown"
        d = int(dim or self.model_dim or 0)
        now = float(now if now is not None else time.time())
        try:
            c = self.db.cursor()
            c.execute(
                "INSERT INTO emb_meta(model,dim,adapter,n,first_ts,last_ts)"
                " VALUES(?,?,?,?,?,?)"
                " ON CONFLICT(model) DO UPDATE SET"
                "   n = n + excluded.n, last_ts = excluded.last_ts,"
                "   dim = CASE WHEN excluded.dim > 0 THEN excluded.dim ELSE dim END",
                (nm, d, EMB_ADAPTER_VERSION, int(n), now, now))
            # 真模型报到之后，同维度的 legacy 占位行就该退休 —— 否则 /health 会一直
            # 说「有两代」，其实两代是同一个模型（legacy 只是回填时按维度猜的占位）。
            if d > 0:
                c.execute("DELETE FROM emb_meta WHERE model LIKE 'legacy-dim%' AND dim=?", (d,))
            self.db.commit()
            return True
        except Exception as e:
            soft_fail("emb_meta", e, nm)
            return False

    def emb_models(self):
        """卡里见过的向量模型：[{model,dim,adapter,n,last_h}]，多的在前。"""
        out = []
        try:
            rows = self.db.execute(
                "SELECT model,dim,adapter,n,first_ts,last_ts FROM emb_meta"
                " ORDER BY n DESC").fetchall()
        except Exception:
            return out
        now = time.time()
        for r in rows:
            out.append({"model": r["model"], "dim": int(r["dim"] or 0),
                        "adapter": int(r["adapter"] or 0),
                        "n": int(r["n"] or 0),
                        "last_h": round((now - float(r["last_ts"] or 0)) / 3600.0, 1)})
        return out

    def backfill_embeddings(self, limit=None, progress=True, remodel=False):
        """给还没有语义向量的记忆补编码。

        返回 (编码条数, 跳过条数, 失败条数)。
        这是**幂等**的：已经有 emb 的直接跳过，所以可以反复跑、随时中断。

        remodel=True：顺便把「维度跟当前模型对不上」的老向量重编码一遍。
        换过模型的卡里会留着两代向量（512 / 768），老那批在检索里根本
        用不上 —— 覆盖率报 100%、语义其实只覆盖一半，这种数字最会骗人。
        """
        if not self.sem_ready():
            return (0, 0, 0)
        if remodel and self.model_dim:
            rows = self.db.execute(
                "SELECT id, raw, core FROM mem"
                " WHERE emb IS NULL"
                "    OR (length(emb) > 0 AND length(emb) != ?)",
                (self.model_dim * 4,)).fetchall()
        else:
            rows = self.db.execute(
                "SELECT id, raw, core FROM mem WHERE emb IS NULL").fetchall()
        if limit:
            rows = rows[:limit]
        if not rows:
            return (0, 0, 0)
        c = self.db.cursor()
        done = skip = fail = wrote = 0
        for i, r in enumerate(rows):
            try:
                blob = self.semantic.encode(embed_text_for(r["raw"], r["speaker"], r["core"]))
            except Exception:
                fail += 1
                continue
            if blob is None:
                fail += 1
                continue
            c.execute("UPDATE mem SET emb=? WHERE id=?", (blob, r["id"]))
            done += 1
            wrote += 1
            if progress and (i + 1) % 50 == 0:
                self.db.commit()
                print('  补编码 %d/%d' % (i + 1, len(rows)), flush=True)
        self.db.commit()
        self._emb_cache.clear()
        self._sem_cache_n = -1
        if wrote:
            self.emb_record(n=wrote)
        return (done, skip, fail)

    def backfill_emb_meta(self, now=None):
        """把卡里**已经存在的**向量按维度补登到 emb_meta（只跑一次，可重入）。

        为什么必须有它：emb_meta 是这一版才加的表，老卡升级上来是空的 ——
        于是 /health 的 emb 段只会说「当前模型是谁」，答不出「这张卡里混了几代」，
        换模型时那个「混代看不见」的坑就还在。这里从 mem.emb 的字节长度反推维度（注意是 emb 不是 vec：vec 那一列
        存的是老版本二元组的 JSON，长度跟维度没关系，用它算会得出 2~5404 维的鬼话）
        （float32 一条 = dim*4），按维度聚合成一条 legacy 记录。

        名字写成 legacy-dim<N> 而不是硬猜成某个模型名：这一版之前的向量是谁写的
        卡里没留下证据，猜成 bge-base 属于编造；写成 legacy 反而诚实，
        而且足够让人看出「768 那批是旧的、512 那批是新的」。
        """
        out = {}
        try:
            c = self.db.cursor()
            rows = c.execute(
                "SELECT length(emb) AS blen, COUNT(*) AS cn FROM mem"
                " WHERE emb IS NOT NULL AND length(emb) > 0 GROUP BY blen"
            ).fetchall()
            now = float(now if now is not None else time.time())
            for r in rows:
                blen = int(r["blen"] or 0)
                cn = int(r["cn"] or 0)
                if blen <= 0 or cn <= 0:
                    continue
                dim = blen // 4
                if dim <= 0:
                    continue
                nm = "legacy-dim%d" % dim
                c.execute(
                    "INSERT INTO emb_meta(model,dim,adapter,n,first_ts,last_ts)"
                    " VALUES(?,?,?,?,?,?)"
                    " ON CONFLICT(model) DO UPDATE SET"
                    "   n = MAX(n, excluded.n), dim = excluded.dim,"
                    "   last_ts = MAX(last_ts, excluded.last_ts)",
                    (nm, dim, 0, cn, now, now))
                out[nm] = cn
            if out:
                self.db.commit()
        except Exception as e:
            soft_fail("emb_meta_backfill", e)
        return out

    def backfill_event_time(self, limit=3000, now=None):
        """给老记忆补 event_ts：拿 create_ts 当「这句话是哪天说的」去解析。

        只补 event_ts=0 的行，且只补一次（补过就把 event_precision 记成 day/time，
        补不到的记成 '' 但 event_ts 仍是 0 —— 下次还会再看一眼，成本很低）。
        为什么不用 now 当参照：一条 10 月底的话里说「昨天」，指的是 10 月底的昨天，
        不是今天的昨天。参照点必须是它自己写下来的那一刻。
        """
        n = 0
        try:
            c = self.db.cursor()
            rows = c.execute(
                "SELECT id, raw, create_ts FROM mem WHERE COALESCE(event_ts,0)=0"
                " AND raw IS NOT NULL AND length(raw) >= 4 LIMIT ?",
                (int(limit),)).fetchall()
            for r in rows:
                ev, prec = parse_event_time(r["raw"], ref_ts=r["create_ts"])
                if ev <= 0:
                    continue
                c.execute("UPDATE mem SET event_ts=?, event_precision=? WHERE id=?",
                          (float(ev), prec or "day", int(r["id"])))
                n += 1
            self.db.commit()
        except Exception as e:
            print("[card2] event_ts 回填失败: %s" % e)
        return n
