"""Anchor-text rotation pool, per PRD §3.7.

Used by the renderer to vary how we link to toktranscript.com so Google
doesn't flag the link profile as manipulated. Weights match the PRD.
"""

import random


_EN_ANCHORS = [
    ("TokTranscript", 30),
    ("TokTranscript.com", 20),
    ("try this transcription tool", 15),
    ("free TikTok transcript generator", 15),
    ("the tool we used to generate this", 10),
    ("analyze your own TikToks", 10),
]

_ZH_ANCHORS = [
    ("TokTranscript", 30),
    ("TokTranscript 转录工具", 20),
    ("TikTok 转录工具", 15),
    ("免费 TikTok 文稿生成器", 15),
    ("我们用的转录工具", 10),
    ("拆解你自己的 TikTok", 10),
]


def pick_anchor(lang: str, seed: str | None = None) -> str:
    """Pick a weighted anchor text. `seed` makes selection deterministic per slug."""
    pool = _ZH_ANCHORS if lang.lower().startswith("zh") else _EN_ANCHORS
    if seed is not None:
        rng = random.Random(seed)
    else:
        rng = random
    items, weights = zip(*pool)
    return rng.choices(items, weights=weights, k=1)[0]
