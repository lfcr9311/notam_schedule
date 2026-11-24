"""Confidence degree of each runway NOTAM and of each flight impact.

The score starts at 100 and loses points for each known source of doubt, so every value can be explained
(the reasons list says exactly what lowered it). It is a transparent rule, NOT a calibrated probability:
once experts label a gold set, measure the precision per level and adjust the penalties.

Evidence used (measured on 449 real AISWEB NOTAMs, Nov/2026): where the Q-code is conclusive, the text rules
agreed with it in 403 of 403 cases, so "text and Q-code agree" is the strongest signal available.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .categories import LENGTH_REDUCTION, NOT_RUNWAY, RUNWAY_CLOSED
from .impact import Impact, RunwayNotam
from .qcode import extract_qcode, label_from_qcode

HIGH, MEDIUM, LOW = "ALTA", "MÉDIA", "BAIXA"
LEVEL_MIN = {HIGH: 80, MEDIUM: 55}    # score >= 80 -> ALTA; 55-79 -> MÉDIA; below -> BAIXA

# Penalties (points). Kept together so they can be tuned against the gold set.
P_QCODE_NOT_RUNWAY = 35   # the NOTAM office coded it as something else (taxiway, lights...)
P_QCODE_OTHER_CAT = 15    # Q-code says runway, but a different restriction
P_NO_QCODE = 10           # nothing independent confirms the text interpretation
P_ML_ONLY = 35            # rules did not fire; only the model flagged it
P_D_NOT_PARSED = 30       # item D not understood: assumed active the whole B-C period
P_END_ESTIMATED = 10      # item C marked EST
P_SUNRISE_SUNSET = 10     # SR/SS times are approximations
P_NO_BASE_LENGTH = 10     # length reduction without the published length (ROTAER): % and severity guessed
P_NO_RUNWAY_ID = 10       # closure without an identified runway: "all runways closed?" is a guess
P_FLIGHT_NO_TIME = 20     # flight without STD/STA: checked against the whole day
P_MARGIN_ONLY = 10        # flight time itself is outside the NOTAM window; conflict only within the margin


@dataclass(frozen=True)
class Confidence:
    score: int
    reasons: list[str] = field(default_factory=list)

    @property
    def level(self) -> str:
        if self.score >= LEVEL_MIN[HIGH]:
            return HIGH
        return MEDIUM if self.score >= LEVEL_MIN[MEDIUM] else LOW

    @property
    def explanation(self) -> str:
        return "; ".join(self.reasons)


def _clamp(score: int) -> int:
    return max(0, min(100, score))


def notam_confidence(rn: RunwayNotam) -> Confidence:
    """How sure we are about WHAT the NOTAM restricts and WHEN it is active."""
    score, reasons = 100, []
    c, n = rn.cls, rn.notam

    q = label_from_qcode(extract_qcode(n.raw))
    if q is None:
        score -= P_NO_QCODE
        reasons.append(f"sem código Q conclusivo que confirme (-{P_NO_QCODE})")
    elif q == NOT_RUNWAY:
        score -= P_QCODE_NOT_RUNWAY
        reasons.append(f"código Q indica que não é restrição de pista (-{P_QCODE_NOT_RUNWAY})")
    elif q != c.primary:
        score -= P_QCODE_OTHER_CAT
        reasons.append(f"código Q indica outra categoria: {q} (-{P_QCODE_OTHER_CAT})")
    else:
        reasons.append("texto e código Q concordam")

    if c.source == "ml":
        score -= P_ML_ONLY
        prob = f", prob. {c.prob:.2f}" if c.prob is not None else ""
        reasons.append(f"detectado só pelo modelo de ML{prob} (-{P_ML_ONLY})")

    if not rn.d_parsed:
        score -= P_D_NOT_PARSED
        reasons.append(f"período (item D) não interpretado: considerado ativo o tempo todo (-{P_D_NOT_PARSED})")
    if n.end_estimated:
        score -= P_END_ESTIMATED
        reasons.append(f"fim da vigência estimado (EST) (-{P_END_ESTIMATED})")
    if "SR/SS" in rn.note:
        score -= P_SUNRISE_SUNSET
        reasons.append(f"horário nascer/pôr do sol aproximado (-{P_SUNRISE_SUNSET})")

    if c.primary == LENGTH_REDUCTION and rn.reduction_pct is None:
        score -= P_NO_BASE_LENGTH
        reasons.append(f"comprimento original desconhecido: % de redução não calculado (-{P_NO_BASE_LENGTH})")
    if c.primary == RUNWAY_CLOSED and not c.runways:
        score -= P_NO_RUNWAY_ID
        reasons.append(f"pista fechada não identificada no texto (-{P_NO_RUNWAY_ID})")
    return Confidence(_clamp(score), reasons)


def impact_confidence(i: Impact) -> Confidence:
    """NOTAM confidence, minus doubts about how the flight time meets the NOTAM window."""
    base = notam_confidence(i.rn)
    score, reasons = base.score, list(base.reasons)
    ref = i.reference_utc
    if ref is None:
        score -= P_FLIGHT_NO_TIME
        reasons.append(f"voo sem horário: comparado com o dia inteiro (-{P_FLIGHT_NO_TIME})")
    elif not any(s <= ref <= e for s, e in i.windows):
        score -= P_MARGIN_ONLY
        reasons.append(f"horário do voo fora do período ativo; conflito só pela margem (-{P_MARGIN_ONLY})")
    else:
        reasons.append("horário do voo dentro do período ativo")
    return Confidence(_clamp(score), reasons)
