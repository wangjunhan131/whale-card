# -*- coding: utf-8 -*-
"""对话控制台（2026-11-06）

一张扁平的「接话/发言/记忆/语音」参数表，住在 qq_state/chat_cfg.json。

为什么要它：以前这些阈值散在 quiet_policy.py / proactive.py / voice.py / qq_guard.py
的常量里，改一次要重启进程、还要改代码。现在集中成一份 JSON：
控制台「对话」页能直接改，下一轮轮询就读到新值。

设计约束（照 voice.py 的老路）：
- 每个键都有 SPEC 里的 min/max，写进来一律夹一遍，不信任调用方；
- 读的时候缺键就用默认值 —— 手改坏了不会把整套判断弄崩；
- 落盘先写 .part 再 os.replace，读的人永远看不到半个文件。
"""
import json
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(ROOT, "qq_state", "chat_cfg.json")

# (key, 分组, 中文标签, 类型, 默认, 最小, 最大, 说明)
SPEC = [
    # ── 谁的话才接 ────────────────────────────────────────────
    ("reply.at_me", "触发", "被点名必接", "bool", True, None, None,
     "有人 @ 我或打我的名字时，先接。关掉就是连点名也可能不回。"),
    ("reply.private", "触发", "私聊必回", "bool", True, None, None,
     "主人私聊默认必回；关掉等于连他也不理。"),
    ("reply.p_group", "触发", "群里随意接话的概率", "float", 0.35, 0.0, 1.0,
     "没点名的群消息，有多大比例值得插一句。0 = 只在点名时说话。"),
    ("reply.hours", "触发", "活跃时段", "str", "07:30-23:30", None, None,
     "只在时段里开口，格式 07:30-23:30（多段用逗号）。"),

    # ── 频率（防刷屏，软上限） ────────────────────────────────
    ("freq.daily_cap", "频率", "一天最多主动找你几次", "int", 8, 1, 40,
     "只管主动开口的软上限；回你的话不算在这里。"),
    ("freq.cooldown_min", "频率", "主动开口的最小间隔（分钟）", "int", 3, 0, 600,
     "只管「我主动找你」；你跟我说话时没有冷却，随时回。"),
    ("freq.group_hour", "频率", "每群每小时最多几条", "int", 15, 1, 60,
     "限单个群里的发言密度。"),

    # ── 不每句都回 ────────────────────────────────────────────
    ("quiet.silent_run", "不每句都回", "别人连聊几条就别插嘴", "int", 6, 2, 40,
     "连续这么多条没叫我，说明人家在聊自己的。"),
    ("quiet.self_max", "不每句都回", "窗口内我说过几句就该停", "int", 2, 1, 10,
     "我怕自己一个人把话占满。"),
    ("quiet.window_s", "不每句都回", "这个窗口（秒）", "int", 300, 30, 3600,
     "上面那条的统计范围。"),
    ("quiet.burst_n", "不每句都回", "点名时最多连回几次", "int", 3, 1, 10,
     "被点名也别一句话拆成十句刷屏。"),
    ("quiet.burst_cool_s", "不每句都回", "连回的冷却（秒）", "int", 60, 10, 600,
     "过了这个时间才重新算一次「连回」。"),
    ("quiet.allow_emotion", "不每句都回", "他难过时必接", "bool", True, None, None,
     "强情绪豁免「别插嘴」的判断。这条别关。"),
    ("quiet.allow_question", "不每句都回", "在问我时必接", "bool", True, None, None,
     "问句里带着我，就算别人连聊也要接。"),

    # ── 说话的口味 ────────────────────────────────────────────
    ("talk.max_chars", "说话", "一条最多几个字", "int", 30, 5, 200,
     "超了就是在答题。真人聊天一条二三十字。"),
    ("talk.split", "说话", "太长就拆成几条", "bool", True, None, None,
     "想做长回复时，拆成两条短的，而不是一条墙。"),
    ("talk.no_period", "说话", "不用句号", "bool", True, None, None,
     "句号是「我说完了轮到你」的终结符，真人聊天不关门。"),
    ("talk.no_wave", "说话", "不堆波浪号和括号动作", "bool", True, None, None,
     "那是 AI 演可爱的三件套。"),

    # ── 主动开口 ──────────────────────────────────────────────
    ("open.enabled", "主动开口", "会自己找你说话", "bool", True, None, None,
     "到点也不等于该说话：还要有由头。"),
    ("open.meal", "主动开口", "饭点问一句", "bool", True, None, None,
     "中午/傍晚各一次上限。"),
    ("open.night", "主动开口", "睡前聊两句", "bool", True, None, None,
     "21:00 之后那段。"),

("open.mode", "主动开口", "开口模式", "str", "auto", None, None,
     "auto=照常开口；observe=只记录我本来会说什么、不真的发；off=完全不开口。"),
("open.school", "主动开口", "上学或工作时段别主动找我", "bool", True, None, None,
     "你在上课、上班，回不了消息，那会儿我自己憋着。"),
("open.school_hours", "主动开口", "那段时间（可写多段）", "str", "07:40-11:40,13:50-17:20", None, None,
     "格式 起-止,起-止（24 小时制），逗号或空格隔开。到点就别开口，等你下课/下班。"),
    # ── 记忆 ──────────────────────────────────────────────────
    ("mem.write_private", "记忆", "私聊写进卡", "bool", True, None, None,
     "私聊里的偏好、情绪、教训要不要落卡。"),
    ("mem.write_group", "记忆", "群里值得记的写进卡", "bool", True, None, None,
     "群里只挑「关于人的稳定陈述/强情绪/教训」，闲聊不落卡。"),
    ("mem.salience_floor", "记忆", "多重要才进卡", "float", 0.45, 0.0, 1.0,
     "低于这个重要度的消息只当素材，不写进记忆。"),

    # ── 语音 ──────────────────────────────────────────────────
    ("voice.enabled", "语音", "允许发语音条", "bool", True, None, None,
     "关掉就只打字。"),
    ("voice.max_chars", "语音", "语音最长几个字", "int", 40, 2, 200,
     "一条语音条的上限，太长听着累。"),

    # ── 日记 ──────────────────────────────────────────────────
    ("diary.enabled", "日记", "每天写一篇日记", "bool", True, None, None,
     "记「我今天怎么想的」，不是「我做了什么」。"),
    ("diary.at", "日记", "什么时候写", "str", "22:20", None, None,
     "HH:MM，24 小时制。"),
]

_BY_KEY = {s[0]: s for s in SPEC}


def defaults():
    return {s[0]: s[4] for s in SPEC}


def _clamp(spec, v):
    kind = spec[3]
    if kind == "bool":
        return bool(v)
    if kind == "int":
        try:
            v = int(float(v))
        except Exception:
            return spec[4]
        if spec[5] is not None:
            v = max(spec[5], v)
        if spec[6] is not None:
            v = min(spec[6], v)
        return v
    if kind == "float":
        try:
            v = float(v)
        except Exception:
            return spec[4]
        if spec[5] is not None:
            v = max(spec[5], v)
        if spec[6] is not None:
            v = min(spec[6], v)
        return round(v, 4)
    v = "" if v is None else str(v).strip()
    return v[:64] if v else spec[4]


def load_cfg():
    """默认值打底 + 文件覆盖。文件写坏也不抛，最坏就是回到默认。"""
    out = defaults()
    try:
        with open(CFG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            for k, v in raw.items():
                spec = _BY_KEY.get(k)
                if spec:
                    out[k] = _clamp(spec, v)
    except Exception:
        pass
    return out


def save_cfg(patch):
    """只认 SPEC 里的键；没提到的键保留原值。"""
    cur = load_cfg()
    for k, v in (patch or {}).items():
        spec = _BY_KEY.get(k)
        if spec:
            cur[k] = _clamp(spec, v)
    os.makedirs(os.path.dirname(CFG_PATH), exist_ok=True)
    tmp = CFG_PATH + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CFG_PATH)
    return cur


def spec_json():
    return [{"key": s[0], "group": s[1], "label": s[2], "kind": s[3],
             "default": s[4], "min": s[5], "max": s[6], "hint": s[7]}
            for s in SPEC]


def get(key):
    spec = _BY_KEY.get(key)
    if not spec:
        return None
    return load_cfg().get(key, spec[4])


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps({"cfg": load_cfg(), "path": CFG_PATH}, ensure_ascii=False, indent=2))
