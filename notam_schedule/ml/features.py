"""NOTAM text preprocessing and vectorization.

Specific numbers (runway designator, meters, times) are replaced by placeholders so the model
learns the pattern ("PISTA _RWY_ REDUZIDA PARA _DIST_") rather than particular values.
"""
from __future__ import annotations

import re

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import FeatureUnion

from ..domain.text import normalize

_SUBS = [
    (re.compile(r"\b\d{2}[LRC]?\s*/\s*\d{2}[LRC]?\b"), " _RWY_ "),
    (re.compile(r"\b(RWY|PISTA|PST|THR|CABECEIRA)\s+\d{2}[LRC]?\b"), r" \1 _RWY_ "),
    (re.compile(r"\b\d{4}\s*-\s*\d{4}\b"), " _TIME_ "),
    (re.compile(r"\b\d+(?:[.,]\d+)?\s?(?:M|MTS|METROS|MTR)\b"), " _DIST_ "),
    (re.compile(r"\b\d+(?:[.,]\d+)?\s?(?:FT|PES)\b"), " _ALT_ "),
    (re.compile(r"\b\d+\b"), " _NUM_ "),
]


# Administrative boilerplate that says nothing about the restriction itself. Left in, it dominates
# the TF-IDF/K-means similarity: the same runway closure lands in different clusters depending on
# whether it is a "NOTAM INICIADOR - SUP AIP ..." or ends with "RMK: ... PRORROGADA POR UM SUP AIP".
# Applied to normalized text (uppercase, single-line).
_BOILERPLATE = [
    # SUP AIP initiator header: "NOTAM INICIADOR - SUP AIP AIRAC N126/26 SUP (E-AIP) 126/26 WEF 11JAN27 TIL 19MAR27"
    re.compile(r"^NOTAM INICIADOR\s*-?\s*(?:SUP\b[^/]{0,30}?\d+/\d+\s*)*(?:WEF \w+ TIL \w+\s*)?"),
    re.compile(r"\bRMK:?\s*ESTA INFORMACAO AERONAUTICA SERA PRORROGADA POR UM SUP AIP\b\.?"),
    # Trailing AIP cross-references: "REF: AIP AD 2.12, AD 2.24 (ADC, AOC)"
    re.compile(r"\bREF:?\s*AIP\b.*$"),
]


def strip_boilerplate(normalized: str) -> str:
    t = normalized
    for rx in _BOILERPLATE:
        t = rx.sub(" ", t)
    t = t.strip()
    return t or normalized  # a NOTAM made only of boilerplate keeps its text


def preprocess(text: str) -> str:
    t = strip_boilerplate(normalize(text))
    for rx, rep in _SUBS:
        t = rx.sub(rep, t)
    t = re.sub(r"[^\w/ ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def build_vectorizer() -> FeatureUnion:
    """Words (1-2 grams) + characters (3-5 grams, robust to abbreviations and typos)."""
    return FeatureUnion([
        ("words", TfidfVectorizer(preprocessor=preprocess, lowercase=False, ngram_range=(1, 2),
                                  token_pattern=r"(?u)\b[\w/]{2,}\b", sublinear_tf=True, min_df=1)),
        ("chars", TfidfVectorizer(preprocessor=preprocess, lowercase=False, analyzer="char_wb",
                                  ngram_range=(3, 5), sublinear_tf=True, min_df=2)),
    ])
