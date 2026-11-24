"""Active learning: build the queue of NOTAMs most worth an expert's review."""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from . import NOT_RUNWAY
from .clustering import ClusterResult
from .labeling import LABELS, LabelSet, rule_label
from .model import NotamModel

QUEUE_COLUMNS = ["uid", "loc", "text", "current_label", "current_source", "model_suggestion", "runway_prob",
                 "reason", "human_label", "note"]


def review_queue(ls: LabelSet, model: NotamModel | None, cr: ClusterResult | None, n: int = 200,
                 seed: int = 42) -> list[dict]:
    """Priority: 1) rule x model disagreement; 2) model uncertainty near the threshold;
    3) samples of unlabeled/mixed clusters; 4) random control sample (audit)."""
    rng = np.random.default_rng(seed)
    cand: dict[int, dict] = {}
    preds = model.predict(ls.texts) if model else [None] * len(ls.texts)

    def add(i: int, reason: str, priority: float) -> None:
        if ls.sources[i] == "human":
            return
        if i in cand:
            cand[i]["reason"] += f"; {reason}"
            cand[i]["_p"] = min(cand[i]["_p"], priority)
            return
        pred = preds[i]
        cand[i] = {
            "uid": ls.uids[i], "loc": ls.notams[i].loc if ls.notams else "", "text": ls.texts[i],
            "current_label": ls.labels[i] or "", "current_source": ls.sources[i],
            "model_suggestion": pred[1] if pred and pred[0] >= model.threshold else (NOT_RUNWAY if pred else ""),
            "runway_prob": round(pred[0], 3) if pred else "",
            "reason": reason, "human_label": "", "note": "", "_p": priority,
        }

    if model:
        for i, (p, _, _) in enumerate(preds):
            r = rule_label(ls.notams[i]) if ls.notams else None
            rule_pos = bool(r and r != NOT_RUNWAY)
            ml_pos = p >= model.threshold
            if rule_pos != ml_pos:
                add(i, "regra e modelo discordam", 1 - abs(p - model.threshold))
            elif abs(p - model.threshold) < 0.15:
                add(i, "modelo incerto", 1 + abs(p - model.threshold))
    if cr:
        for cl in cr.clusters:
            if cl.action.startswith(("sem rótulo", "misturado", "rotular")):
                idx = np.where(cr.assignment == cl.id)[0]
                for i in rng.choice(idx, size=min(5, len(idx)), replace=False):
                    add(int(i), f"cluster {cl.id}: {cl.action}", 2)
    unlabeled = [i for i, label in enumerate(ls.labels) if label is None]
    if unlabeled:
        for i in rng.choice(unlabeled, size=min(len(unlabeled), max(5, n // 10)), replace=False):
            add(int(i), "sem rótulo", 3)
    everyone = list(range(len(ls.texts)))
    for i in rng.choice(everyone, size=min(len(everyone), max(5, n // 20)), replace=False):
        add(int(i), "amostra de controle", 4)

    out = sorted(cand.values(), key=lambda d: d["_p"])[:n]
    for d in out:
        d.pop("_p")
    return out


def save_queue(rows: list[dict], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(f"# Preencha 'human_label' com um de: {', '.join(LABELS)}\n")
        w = csv.DictWriter(f, fieldnames=QUEUE_COLUMNS, delimiter=";")
        w.writeheader()
        w.writerows(rows)
    return path


GOLD_COLUMNS = ["uid", "label_set", "human_label", "author", "note", "loc", "number", "d", "text"]


def save_gold_sample(items: list, path: str | Path) -> Path:
    """Random sample for the gold set. The automatic label is NOT shown, so it does not anchor the expert."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(f"# Amostra ALEATÓRIA para medir a acurácia. Preencha 'human_label' com um de: {', '.join(LABELS)}\n")
        f.write("# Rotule todas as linhas, inclusive as óbvias: pular as fáceis distorce a medição.\n")
        w = csv.DictWriter(f, fieldnames=GOLD_COLUMNS, delimiter=";")
        w.writeheader()
        for uid, n in items:
            w.writerow({"uid": uid, "label_set": "gold", "human_label": "", "author": "", "note": "",
                        "loc": n.loc, "number": n.number, "d": n.d, "text": n.e})
    return path