# -*- coding: utf-8 -*-
"""SYNAPSE-1024 情绪引擎（RPE + 神经调质动力学）"""

from .config import DEFAULTS, EMOTION_LABELS
from .appraisal import Appraisal, estimate_appraisal
from .engine import EmotionEngine, TurnResult
from .prompt import render_behavior_block, render_compact

__all__ = [
    "DEFAULTS",
    "EMOTION_LABELS",
    "Appraisal",
    "estimate_appraisal",
    "EmotionEngine",
    "TurnResult",
    "render_behavior_block",
    "render_compact",
]
__version__ = "1.0.0"
