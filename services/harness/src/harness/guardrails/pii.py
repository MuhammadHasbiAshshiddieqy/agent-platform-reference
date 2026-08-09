"""§9.1/§9.2 PII detection — Microsoft Presidio + custom Indonesian
recognizers (NIK, NPWP, no. rekening, no. HP), per the spec's guardrail
table verbatim.

Presidio's `AnalyzerEngine` needs an NLP engine even when every recognizer
here is a pure-regex `PatternRecognizer` — it uses the NLP pipeline for
tokenization and (if a recognizer declares `context=[...]`) confidence
boosting from nearby words. A full trained spaCy model (`en_core_web_*`)
would be both wrong (English NER on Indonesian text) and heavy on this
project's already-constrained dev VM (see CLAUDE.md's known quirks) for
capability we don't need — none of these four entity types are named-
entity recognition, they're all regex. `spacy.blank("id")` gives correct
Indonesian tokenization with no downloaded model at all.

One gap: a blank pipeline has no lemmatizer, and Presidio's context
enhancer reads `token.lemma_` to match context words — on a blank
pipeline every lemma is `""`, silently disabling context boosting
entirely (verified empirically, not documented anywhere obvious). Fixed
here with a trivial identity-lemmatizer pipe (`lemma_ = text.lower()`);
Indonesian has little inflectional morphology for common nouns like
"rekening"/"NPWP", so exact lowercasing is close enough for context
matching and needs no lookup tables.
"""

from __future__ import annotations

import spacy
from harness.guardrails.events import GuardrailEvent
from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerRegistry
from presidio_analyzer.context_aware_enhancers import LemmaContextAwareEnhancer
from presidio_analyzer.nlp_engine import SpacyNlpEngine
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig
from spacy.language import Language

LANGUAGE = "id"

# Below this, a bare digit run (rekening/NPWP-plain) is presumed to be
# something else (order number, phone extension, ...) and left alone —
# only entities with either a distinctive format (NIK, NPWP-dotted, HP)
# or a nearby context keyword clear the bar.
REDACT_SCORE_THRESHOLD = 0.6


@Language.component("identity_lemmatizer")
def _identity_lemmatizer(doc):  # type: ignore[no-untyped-def]
    for token in doc:
        token.lemma_ = token.text.lower()
    return doc


class _BlankIndonesianNlpEngine(SpacyNlpEngine):
    """Overrides `.load()` entirely — the base class insists on
    `spacy.load(a_package_name_string)`, which doesn't accept an
    in-memory blank pipeline. See module docstring."""

    def load(self) -> None:
        nlp = spacy.blank(LANGUAGE)
        nlp.add_pipe("identity_lemmatizer")
        # presidio-analyzer's own `self.nlp` attribute is untyped (`= None`
        # in the base class `__init__`), so mypy infers `None` rather than
        # the `dict[str, Language]` the rest of the class actually uses.
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
    # 16 digits, unambiguous enough on its own to redact without context,
    # but "nik"/"ktp" nearby still boosts confidence.
    _pattern_recognizer(r"ID_NIK", r"\b\d{16}\b", 0.7, context=["nik", "ktp", "kependudukan"]),
    # Dotted format (XX.XXX.XXX.X-XXX.XXX) is distinctive enough alone;
    # the bare-15-digit form is not, so it starts under threshold and
    # needs "npwp"/"pajak" nearby to clear REDACT_SCORE_THRESHOLD.
    _pattern_recognizer(
        "ID_NPWP", r"\b\d{2}\.\d{3}\.\d{3}\.\d{1}-\d{3}\.\d{3}\b", 0.9, context=["npwp", "pajak"]
    ),
    _pattern_recognizer("ID_NPWP", r"\b\d{15}\b", 0.3, context=["npwp", "pajak"]),
    # Bank account numbers have no fixed format in Indonesia (9-16 digits
    # depending on the bank) — deliberately low base score, redacted only
    # when "rekening"/"norek"/"rek"/a bank name is nearby.
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
# presidio-anonymizer ships no py.typed marker under `strict = true`.
_anonymizer = AnonymizerEngine()  # type: ignore[no-untyped-call]


def _non_overlapping_results(results: list) -> list:  # type: ignore[type-arg]
    """Different entity types' regexes can match the same span (a 16-digit
    NIK is also a valid 9-16 digit `ID_NO_REKENING` match). Presidio
    doesn't resolve cross-type overlaps itself — keep only the
    highest-scoring result per overlapping span so a digit run is redacted
    once, not twice with mismatched placeholder numbering."""
    kept: list = []  # type: ignore[type-arg]
    for result in sorted(results, key=lambda r: r.score, reverse=True):
        if any(result.start < k.end and k.start < result.end for k in kept):
            continue
        kept.append(result)
    return sorted(kept, key=lambda r: r.start)


def redact_input_pii(text: str) -> tuple[str, dict[str, str], GuardrailEvent | None]:
    """§9.1 input PII row: `redact` — placeholder `[NIK_1]` etc., mapping
    kept in run memory (not persisted) so `restore_input_pii` can put the
    user's own data back in the final output."""
    results = [
        r
        for r in _analyzer.analyze(text=text, language=LANGUAGE)
        if r.score >= REDACT_SCORE_THRESHOLD
    ]
    results = _non_overlapping_results(results)
    if not results:
        return text, {}, None

    mapping: dict[str, str] = {}
    counters: dict[str, int] = {}
    out = text
    entity_counts: dict[str, int] = {}
    for result in sorted(results, key=lambda r: r.start, reverse=True):
        entity_short = result.entity_type.removeprefix("ID_")
        counters[entity_short] = counters.get(entity_short, 0) + 1
        placeholder = f"[{entity_short}_{counters[entity_short]}]"
        mapping[placeholder] = text[result.start : result.end]
        out = out[: result.start] + placeholder + out[result.end :]
        entity_counts[result.entity_type] = entity_counts.get(result.entity_type, 0) + 1

    event = GuardrailEvent(
        stage="input",
        rule_id="pii_redaction",
        severity="info",
        action_taken="redact",
        detail={"entities": entity_counts},
    )
    return out, mapping, event


def restore_input_pii(text: str, mapping: dict[str, str]) -> str:
    """§9.2's "Restore PII" row. Unconditional in this milestone — the
    values being restored are always the requesting user's own input
    being echoed back, not another user's data; a recipient-authorization
    check belongs to RBAC maturity (§22, M5b), not here."""
    for placeholder, original in mapping.items():
        text = text.replace(placeholder, original)
    return text


def scan_output_pii(text: str) -> tuple[str, GuardrailEvent | None]:
    """§9.2 output PII row: `redact`, not restore — this is PII the model
    generated on its own (a hallucinated example NIK, a made-up phone
    number), not the user's own data round-tripping through a placeholder,
    so there is no original value to give back. One-way mask."""
    results = [
        r
        for r in _analyzer.analyze(text=text, language=LANGUAGE)
        if r.score >= REDACT_SCORE_THRESHOLD
    ]
    results = _non_overlapping_results(results)
    if not results:
        return text, None

    operators = {
        r.entity_type: OperatorConfig("replace", {"new_value": f"<{r.entity_type}>"})
        for r in results
    }
    # presidio-anonymizer declares its own `RecognizerResult` type, distinct
    # from (but structurally identical to, and interchangeable at runtime
    # with) presidio-analyzer's — two separate packages, same duck type.
    anonymized = _anonymizer.anonymize(
        text=text,
        analyzer_results=results,  # type: ignore[arg-type]
        operators=operators,
    )
    entity_counts: dict[str, int] = {}
    for r in results:
        entity_counts[r.entity_type] = entity_counts.get(r.entity_type, 0) + 1

    event = GuardrailEvent(
        stage="output",
        rule_id="pii_leakage",
        severity="warning",
        action_taken="redact",
        detail={"entities": entity_counts},
    )
    return anonymized.text, event
