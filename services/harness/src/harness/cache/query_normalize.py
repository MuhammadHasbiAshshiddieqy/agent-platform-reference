"""§10 step 1 — trim/lowercase/strip greeting-filler before embedding, so
"Halo, tolong bantu, berapa lama masa pemberitahuan cuti panjang?" and
"berapa lama masa pemberitahuan cuti panjang" land closer together in
embedding space than the raw strings would. A heuristic word-list, not
NLP — the same "good enough for this POC's Indonesian text" tradeoff
`guardrails/offtopic.py`'s static description map already documents;
normalization only widens the net for a cache *hit*, a false negative
here just falls through to a normal (correct, just uncached) answer.
"""

from __future__ import annotations

import re

_FILLER_WORDS = {
    "halo",
    "hai",
    "hi",
    "hey",
    "permisi",
    "selamat",
    "pagi",
    "siang",
    "sore",
    "malam",
    "tolong",
    "mohon",
    "dong",
    "min",
    "kak",
    "ya",
    "nih",
    "gan",
    "please",
    "maaf",
}

_PUNCTUATION = re.compile(r"[.,!?;:]")


def normalize_query(text: str) -> str:
    normalized = _PUNCTUATION.sub(" ", text.strip().lower())
    tokens = [t for t in normalized.split() if t not in _FILLER_WORDS]
    return " ".join(tokens)
