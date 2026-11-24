"""Weak supervision: combine several label sources in order of confidence.

human > qcode > rules. NOTAMs without a reliable label stay unlabeled (None) and are used by
self-training and K-means.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from ..detection.rules import classify, mentions_runway
from ..domain.categories import CATEGORIES
from ..domain.notam import Notam
from ..domain.text import normalize
from . import NOT_RUNWAY
from ..domain.qcode import extract_qcode, label_from_qcode

LABELS = [NOT_RUNWAY] + CATEGORIES

# Mentions a runway, but the subject is a navaid/light/obstacle/wildlife and there is no restriction word
RE_NEG_SUBJECT = re.compile(
    r"\b(PAPI|VASIS|ALS|ALSF|ILS|LOC|GP|DME|VOR|NDB|REIL|RCLL|TDZ LGT|LGT|LUZ|LUZES|BALIZAMENTO|FAROL|"
    r"ANEMOMETRO|BIRDS?|AVES|FAUNA|GUINDASTE|CRANE|OBST|OBSTACULO|ANTENA|BALAO|DRONE|RPA|RNAV|RNP|IAC|SID|STAR)\b"
)
RE_RESTRICTION_WORDS = re.compile(
    r"\b(CLSD|CLOSED|FECHAD[AO]S?|INTERDITAD[AO]S?|TORA|TODA|ASDA|LDA|REDUZID[AO]S?|REDUCED|DESLOCAD[AO]S?|DISPLACED|"
    r"COMPRIMENTO|LENGTH|LARGURA|WIDTH|WIP|OBRAS?)\b"
)
# The NOTAM subject is a taxiway/apron (the runway only appears as a location reference)
RE_NON_RUNWAY_SUBJECT = re.compile(
    r"^(TWY|TAXIWAY|APN|APRON|PATIO|STAND|BOX|ACFT STAND|PISTA DE (?:TAXI|ROLAMENTO|TAXIAMENTO))\b"
)


@dataclass
class LabelSet:
    uids: list[str]
    texts: list[str]
    labels: list[str | None]
    sources: list[str]
    notams: list[Notam] = field(default_factory=list)
    gold: list[bool] = field(default_factory=list)   # human label from the random gold sample

    def summary(self) -> dict[str, dict[str, int]]:
        out: dict[str, Counter] = {}
        for label, source in zip(self.labels, self.sources):
            out.setdefault(source, Counter())[label or "(unlabeled)"] += 1
        return {s: dict(c) for s, c in out.items()}


def rule_label(n: Notam) -> str | None:
    c = classify(n)
    if c.relevant:
        return c.primary
    text = normalize(n.e)
    if not mentions_runway(text):
        return NOT_RUNWAY
    if RE_NEG_SUBJECT.search(text) and not RE_RESTRICTION_WORDS.search(text):
        return NOT_RUNWAY
    if RE_NON_RUNWAY_SUBJECT.match(text):
        return NOT_RUNWAY
    return None  # mentions a runway but the rules cannot tell: leave it to the model


def build_labels(items: list[tuple[str, Notam]], human: dict[str, str], use_rules: bool = True,
                 gold: set[str] | None = None) -> LabelSet:
    """gold: uids whose human label belongs to the random gold sample (used for unbiased evaluation)."""
    gold = gold or set()
    uids, texts, labels, sources, notams = [], [], [], [], []
    for uid, n in items:
        label, source = None, "unlabeled"
        if uid in human:
            label, source = human[uid], "human"
        else:
            q = label_from_qcode(extract_qcode(n.raw))
            if q:
                label, source = q, "qcode"
            elif use_rules:
                r = rule_label(n)
                if r:
                    label, source = r, "rules"
        uids.append(uid)
        texts.append(n.e)
        labels.append(label)
        sources.append(source)
        notams.append(n)
    return LabelSet(uids, texts, labels, sources, notams, [u in gold and u in human for u in uids])
