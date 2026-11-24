"""K-means label propagation guards."""
from notam_schedule.ml import NOT_RUNWAY
from notam_schedule.ml.clustering import cluster_corpus, propagate_labels


def test_propagation_never_drops_runway_mention():
    """A pure NOT_RUNWAY cluster must not label an unlabeled runway-condition NOTAM as NOT_RUNWAY."""
    from notam_schedule.ml.labeling import LabelSet
    texts = [f"VOR KPS U/S DEVIDO MAINT {i}" for i in range(8)] + ["RWY 14 ESCORREGADIA QUANDO MOLHADA",
                                                                    "VOR KPS U/S DEVIDO MAINT X"]
    labels = [NOT_RUNWAY] * 8 + [None, None]
    sources = ["qcode"] * 8 + ["unlabeled"] * 2
    ls = LabelSet([str(i) for i in range(10)], texts, labels, sources)
    cr = cluster_corpus(ls, k=1)
    cr.distance[:] = 0.0  # isolate the runway guard from the radius guard
    assert propagate_labels(ls, cr) == 1
    assert ls.labels[8] is None and ls.labels[9] == NOT_RUNWAY
