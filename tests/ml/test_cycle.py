"""Full semi-supervised loop on synthetic data."""
from notam_schedule.domain.categories import RUNWAY_CLOSED
from notam_schedule.ml import NOT_RUNWAY
from notam_schedule.ml.clustering import cluster_corpus, propagate_labels
from notam_schedule.storage.corpus import Corpus
from notam_schedule.ml.labeling import build_labels
from tests.helpers import mk


def test_ml_cycle(tmp_path):
    """Full loop on synthetic data: corpus -> labels -> K-means -> training -> detector -> review."""
    from notam_schedule.ml.active import review_queue, save_queue
    from notam_schedule.ml.detector import HybridDetector
    from notam_schedule.ml.model import NotamModel, train
    from notam_schedule.ml.synthetic import generate

    data = generate(500, seed=1)
    corpus = Corpus(tmp_path / "c.sqlite")
    corpus.add([n for n, _ in data], "synthetic")
    for n, label in data[:80]:
        corpus.set_label(f"{n.loc}|{n.number}", label, "test", label_set="gold")
    ls = build_labels(corpus.all(), corpus.human_labels(), gold=corpus.gold_uids())
    assert sum(s == "human" for s in ls.sources) == 80

    cr = cluster_corpus(ls, k=12)
    assert len(cr.clusters) == 12 and len(cr.assignment) == 500
    propagate_labels(ls, cr)

    path, card = train(ls, tmp_path / "models", target_recall=0.95)
    ev = card["evaluation"]
    assert ev["basis"].startswith("gold")
    assert ev["hybrid"]["recall"] >= 0.95
    assert ev["hybrid"]["recall"] >= ev["rules"]["recall"]

    det = HybridDetector(tmp_path / "models" / "current.joblib")
    c = det(mk(e="PISTA 09/27 OPERANDO COM 1500 METROS UTEIS"))
    assert c.relevant and c.source == "ml" and c.available_length == 1500
    assert not det(mk(e="VOR KPS U/S")).relevant

    rows = review_queue(ls, NotamModel(path), cr, n=30)
    assert 0 < len(rows) <= 30 and all(r["current_source"] != "human" for r in rows)

    # round trip: expert fills the queue, labels are imported
    for r in rows[:3]:
        r["human_label"] = NOT_RUNWAY
    q = save_queue(rows, tmp_path / "review.csv")
    ok, errors = corpus.import_labels_csv(q, {NOT_RUNWAY, RUNWAY_CLOSED})
    assert ok == 3 and not errors
    assert len(corpus.human_labels()) == 83


def test_labels_are_append_only_and_gold_is_tracked(tmp_path):
    import sqlite3
    from notam_schedule.ml.active import save_gold_sample
    corpus_path = tmp_path / "c.sqlite"
    # old schema (one overwritten label per NOTAM) is migrated without losing labels
    db = sqlite3.connect(corpus_path)
    db.executescript("CREATE TABLE labels (uid TEXT PRIMARY KEY, label TEXT NOT NULL, author TEXT, created_at TEXT, "
                     "note TEXT); INSERT INTO labels VALUES ('SBKP|C1/26', 'NOT_RUNWAY', 'ana', '2026-11-01', '');")
    db.commit(); db.close()
    corpus = Corpus(corpus_path)
    corpus.add([mk(e="RWY 15/33 CLSD", n="C1/26"), mk(e="VOR KPS U/S", n="C2/26")], "test")
    assert corpus.human_labels() == {"SBKP|C1/26": NOT_RUNWAY}

    corpus.set_label("SBKP|C1/26", RUNWAY_CLOSED, "bia", label_set="gold")
    assert corpus.human_labels()["SBKP|C1/26"] == RUNWAY_CLOSED
    assert [h["label"] for h in corpus.label_history("SBKP|C1/26")] == [NOT_RUNWAY, RUNWAY_CLOSED]
    assert corpus.gold_uids() == {"SBKP|C1/26"}

    sample = corpus.unlabeled_sample(10)
    assert [u for u, _ in sample] == ["SBKP|C2/26"]       # already-gold NOTAMs are not drawn again
    path = save_gold_sample(sample, tmp_path / "gold.csv")
    rows = path.read_text(encoding="utf-8-sig").splitlines()
    rows[-1] = rows[-1].replace("SBKP|C2/26;gold;;", "SBKP|C2/26;gold;NOT_RUNWAY;")
    path.write_text("\n".join(rows), encoding="utf-8-sig")
    ok, errors = corpus.import_labels_csv(path, {NOT_RUNWAY, RUNWAY_CLOSED})
    assert ok == 1 and not errors and corpus.gold_uids() == {"SBKP|C1/26", "SBKP|C2/26"}
