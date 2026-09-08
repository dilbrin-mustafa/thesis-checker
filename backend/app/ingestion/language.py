"""Language detection (doc 01 §4.1 step 2).

``lingua-language-detector`` is the configured backend — it is markedly better
than ``langdetect`` on short Polish strings, which matters because bilingual
theses have short EN abstracts and figure captions. A stopword-scoring
heuristic is the fallback so ingestion never hard-fails on a missing optional
dependency.

Detection is per section (not per document) because abstracts are deliberately
bilingual and the exclusion mask depends on knowing which is which.
"""

from __future__ import annotations

import re
from functools import lru_cache

from app.models.ir import DocumentIR, Language

#: Below this many characters lingua's confidence is not worth trusting.
MIN_CHARS_FOR_DETECTION = 40

_POLISH_STOPWORDS = frozenset(
    [
        "i",
        "w",
        "na",
        "z",
        "do",
        "jest",
        "to",
        "się",
        "nie",
        "że",
        "oraz",
        "dla",
        "od",
        "po",
        "przez",
        "który",
        "która",
        "które",
        "jak",
        "ich",
        "jego",
        "jej",
        "być",
        "był",
        "była",
        "były",
        "zostały",
        "został",
        "został",
        "zostały",
        "można",
        "tym",
        "tego",
        "tej",
        "tym",
        "bardziej",
        "bardzo",
        "także",
        "czyli",
        "aby",
        "żeby",
        "przy",
        "ze",
        "nad",
        "pod",
        "między",
        "gdzie",
        "gdy",
        "ponieważ",
        "jednak",
    ]
)
_ENGLISH_STOPWORDS = frozenset(
    [
        "the",
        "and",
        "of",
        "to",
        "in",
        "is",
        "for",
        "with",
        "that",
        "this",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "it",
        "its",
        "as",
        "at",
        "by",
        "from",
        "an",
        "or",
        "which",
        "their",
        "there",
        "they",
        "we",
        "you",
        "your",
        "can",
        "will",
        "would",
        "should",
        "could",
        "this",
        "these",
        "those",
        "than",
        "then",
        "such",
    ]
)

SUPPORTED: tuple[str, ...] = ("pl", "en")


class _Detector:
    """Thin adapter so the lingua import stays optional and lazy."""

    def __init__(self) -> None:
        self._impl: object | None = None
        self._polish: object | None = None
        self._english: object | None = None
        self._unavailable = False

    def _load(self) -> bool:
        if self._impl is not None:
            return True
        if self._unavailable:
            return False
        try:
            from lingua import Language, LanguageDetectorBuilder

            self._impl = (
                LanguageDetectorBuilder.from_languages(Language.POLISH, Language.ENGLISH)
                .with_preloaded_language_models()
                .build()
            )
            self._polish = Language.POLISH
            self._english = Language.ENGLISH
        except Exception:  # pragma: no cover - optional dependency missing
            self._impl = None
            self._unavailable = True
            return False
        return True

    def detect(self, text: str) -> str | None:
        if len(text.strip()) < MIN_CHARS_FOR_DETECTION:
            return None
        if self._load():
            try:
                found = self._impl.detect_language_of(text)  # type: ignore[union-attr]
            except Exception:  # pragma: no cover - defensive
                found = None
            if found == self._polish:
                return "pl"
            if found == self._english:
                return "en"
            return None
        return _heuristic(text)


@lru_cache(maxsize=1)
def detector() -> _Detector:
    return _Detector()


_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _heuristic(text: str) -> str | None:
    """Stopword scoring — the fallback when lingua is not installed."""
    words = [w.lower() for w in _WORD_RE.findall(text)]
    if len(words) < 8:
        return None
    pl = sum(1 for w in words if w in _POLISH_STOPWORDS)
    en = sum(1 for w in words if w in _ENGLISH_STOPWORDS)
    if pl == en == 0:
        return None
    return "pl" if pl >= en else "en"


def detect_text_language(text: str) -> str | None:
    """'pl' | 'en' | None when the sample is too short or ambiguous."""
    return detector().detect(text)


def section_text(ir: DocumentIR, block_ids: list[str]) -> str:
    by_id = {b.id: b for b in ir.blocks}
    return "\n".join(by_id[bid].text for bid in block_ids if bid in by_id)


def detect_languages(ir: DocumentIR, *, default: str = "pl") -> Language:
    """Document language + per-section breakdown.

    The primary language is character-weighted over blocks (not a vote over
    sections) so a 3-page English abstract cannot outvote a Polish thesis.
    """
    weights: dict[str, int] = {lang: 0 for lang in SUPPORTED}
    for block in ir.blocks:
        if block.type not in ("paragraph", "list_item", "quote"):
            continue
        lang = detect_text_language(block.text)
        if lang:
            weights[lang] += len(block.text)
    primary = default
    best = max(weights.values())
    if best > 0:
        primary = next(lang for lang in SUPPORTED if weights[lang] == best)

    per_section: dict[str, str] = {}
    for section in ir.sections:
        lang = detect_text_language(section_text(ir, section.block_ids))
        per_section[section.id] = lang or primary

    return Language(primary=primary, per_section=per_section)
