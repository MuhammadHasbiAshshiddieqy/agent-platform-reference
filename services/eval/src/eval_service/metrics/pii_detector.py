"""§13.3's `pii_leakage` metric needs its own PII detector — deliberately
NOT imported from `harness.guardrails.pii` (boundary #1: services never
import each other, only `from contracts import ...`; `async-worker`'s own
copy of gateway's quota Lua script is the established precedent for this
exact situation). Same Presidio recognizers, same threshold, same
Indonesian NLP setup as harness's guardrail — this file and that one
should be kept in sync by hand if the pattern set ever changes, same as
any other intentionally-duplicated boundary-#1 code in this repo.
"""

from __future__ import annotations

import spacy
from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerRegistry
from presidio_analyzer.context_aware_enhancers import LemmaContextAwareEnhancer
from presidio_analyzer.nlp_engine import SpacyNlpEngine
from spacy.language import Language

LANGUAGE = "id"
REDACT_SCORE_THRESHOLD = 0.6


@Language.component("identity_lemmatizer_eval")
def _identity_lemmatizer(doc):  # type: ignore[no-untyped-def]
    for token in doc:
        token.lemma_ = token.text.lower()
    return doc


class _BlankIndonesianNlpEngine(SpacyNlpEngine):
    def load(self) -> None:
        nlp = spacy.blank(LANGUAGE)
        nlp.add_pipe("identity_lemmatizer_eval")
        self.nlp = {LANGUAGE: nlp}  # type: ignore[assignment]


def _pattern_recognizer(
    entity: str, regex: str, score: float, context: list[str] | None = None
) -> PatternRecognizer:
    return PatternRecognizer(
        supported_entity=entity,
        patterns=[Pattern(name=f"{entity.lower()}_pattern", regex=regex, score=score)],
        supported_language=LANGUAGE,
        context=context or [],
    )


_RECOGNIZERS = [
    _pattern_recognizer(r"ID_NIK", r"\b\d{16}\b", 0.7, context=["nik", "ktp", "kependudukan"]),
    _pattern_recognizer(
        "ID_NPWP", r"\b\d{2}\.\d{3}\.\d{3}\.\d{1}-\d{3}\.\d{3}\b", 0.9, context=["npwp", "pajak"]
    ),
    _pattern_recognizer("ID_NPWP", r"\b\d{15}\b", 0.3, context=["npwp", "pajak"]),
    _pattern_recognizer(
        "ID_NO_REKENING",
        r"\b\d{9,16}\b",
        0.3,
        context=["rekening", "norek", "rek", "bank", "bca", "bri", "bni", "mandiri"],
    ),
    _pattern_recognizer(
        "ID_NO_HP",
        r"\b(?:\+62|62|0)8[1-9][0-9]{6,10}\b",
        0.75,
        context=["hp", "telepon", "nomor", "wa", "whatsapp"],
    ),
]

_registry = RecognizerRegistry(supported_languages=[LANGUAGE])
for _r in _RECOGNIZERS:
    _registry.add_recognizer(_r)

_nlp_engine = _BlankIndonesianNlpEngine()
_nlp_engine.load()

_analyzer = AnalyzerEngine(
    nlp_engine=_nlp_engine,
    registry=_registry,
    supported_languages=[LANGUAGE],
    context_aware_enhancer=LemmaContextAwareEnhancer(
        context_matching_mode="substring", min_score_with_context_similarity=REDACT_SCORE_THRESHOLD
    ),
)


def detect_pii(text: str) -> list[str]:
    """Returns the matched raw text of every entity scoring above
    threshold — §13.3's `pii_leakage` compares these against
    `item.allowed_pii` by value, not by entity type."""
    results = [
        r
        for r in _analyzer.analyze(text=text, language=LANGUAGE)
        if r.score >= REDACT_SCORE_THRESHOLD
    ]
    return [text[r.start : r.end] for r in results]
