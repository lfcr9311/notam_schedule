"""ML lifecycle CLI: python -m notam_schedule.ml <command> (thin: delegates to pipelines/)

Recommended loop:
  collect (daily)  ->  clusters  ->  train  ->  review  ->  [expert fills in the CSV]
  ->  label  ->  train  ->  ...  (repeat until gold-set metrics stabilize)
"""
from __future__ import annotations

import argparse
import logging
import random
import sys
from pathlib import Path

from ..config import CLUSTERS_FILE, CORPUS_DB, GOLD_FILE, MODEL_FILE, MODELS_DIR, RAW_DIR, REVIEW_FILE, AiswebConfig
from ..ingestion.airports import AirportDB
from ..ingestion.aisweb import AiswebClient
from ..ingestion.schedule import load_schedule
from ..ml.active import review_queue, save_gold_sample, save_queue
from ..ml.clustering import cluster_corpus
from ..ml.labeling import LABELS, build_labels
from ..ml.model import NotamModel
from ..pipelines.collect import collect, import_xml_dir
from ..pipelines.train import train_detector
from ..reporting.clusters import write_clusters_excel
from ..storage.corpus import Corpus
from ..storage.raw_archive import FileRawArchive


def _labels(corpus: Corpus):
    items = corpus.all()
    if not items:
        raise SystemExit("Corpus vazio. Use 'collect', 'import' ou 'synthetic' antes.")
    return build_labels(items, corpus.human_labels(), gold=corpus.gold_uids())


def cmd_status(a, corpus: Corpus) -> None:
    ls = _labels(corpus)
    print(f"Corpus: {corpus.path} — {len(ls.uids)} NOTAMs, {len(corpus.human_labels())} rótulos humanos")
    for source, dist in ls.summary().items():
        print(f"  {source:10s} {dist}")


def cmd_collect(a, corpus: Corpus) -> None:
    db = AirportDB.default()
    if a.icao:
        icaos = [x.strip().upper() for x in a.icao.split(",") if x.strip()]
    elif a.schedule:
        flights = load_schedule(a.schedule, db).flights
        icaos = sorted({f.origin.icao for f in flights} | {f.destination.icao for f in flights})
    else:
        icaos = sorted(db.by_icao)
    client = AiswebClient(AiswebConfig.from_env(), cache_ttl_min=0, archive=FileRawArchive(RAW_DIR))
    added, errors = collect(corpus, client, icaos)
    for icao, new in added.items():
        print(f"  {icao}: +{new}")
    for icao, err in errors.items():
        print(f"  {icao}: ERRO {err}", file=sys.stderr)
    print(f"Novos NOTAMs: {sum(added.values())} | total no corpus: {corpus.count()}")

def cmd_import(a, corpus: Corpus) -> None:
    print(f"Importados: {import_xml_dir(corpus, a.dir)} | total: {corpus.count()}")


def cmd_synthetic(a, corpus: Corpus) -> None:
    from ..ml.synthetic import generate
    data = generate(a.n, seed=a.seed)
    corpus.add([n for n, _ in data], "synthetic")
    rng = random.Random(a.seed)
    k = 0
    for n, label in data:
        if rng.random() < a.gold:
            corpus.set_label(f"{n.loc}|{n.number}", label, "simulated", "true label from the synthetic generator",
                             label_set="gold")
            k += 1
    corpus.db.commit()
    print(f"{len(data)} NOTAMs SINTÉTICOS adicionados, {k} com rótulo humano simulado. "
          f"Use apenas para demonstração (corpus: {corpus.path}).")


def cmd_clusters(a, corpus: Corpus) -> None:
    ls = _labels(corpus)
    cr = cluster_corpus(ls, k=a.k)
    print(f"K-means: k={cr.k}, silhouette={cr.silhouette:.3f}")
    for cl in cr.clusters:
        print(f"  [{cl.id:2d}] n={cl.size:4d} pureza={cl.purity} {cl.action:38s} {' '.join(cl.terms[:6])}")
    write_clusters_excel(cr, ls, a.output)
    print(f"Relatório de clusters: {a.output}")


def cmd_train(a, corpus: Corpus) -> None:
    ls = _labels(corpus)
    path, card = train_detector(ls, a.output, a.target_recall, propagate=not a.no_propagation)
    if "kmeans" in card:
        km = card["kmeans"]
        print(f"K-means k={km['k']}: {km['propagated_labels']} rótulos propagados de clusters puros")
    ev = card["evaluation"]
    print(f"Modelo salvo: {path}")
    print(f"Rótulos por fonte: {card['labels_by_source']}")
    print(f"Rotulados automaticamente pelo self-training: {card['labeled_by_self_training']}")
    if ev:
        print(f"Avaliação em {ev['n']} NOTAMs — base: {ev['basis']}")
        print(f"Limiar para recall-alvo de {ev['target_recall']:.0%}: {ev['threshold']}")
        for k in ("rules", "model", "hybrid"):
            m = ev[k]
            print(f"  {k:7s} recall={m['recall']}  precisão={m['precision']}  FN={m['fn']}  FP={m['fp']}")
    else:
        print("Dados insuficientes para avaliação.")


def cmd_review(a, corpus: Corpus) -> None:
    ls = _labels(corpus)
    model = NotamModel(a.model) if Path(a.model).exists() else None
    cr = cluster_corpus(ls) if len(ls.texts) >= 10 else None
    rows = review_queue(ls, model, cr, n=a.n)
    save_queue(rows, a.output)
    print(f"{len(rows)} NOTAMs na fila de revisão: {a.output}")
    print(f"Preencha a coluna 'human_label' e rode: python -m notam_schedule.ml label {a.output}")


def cmd_gold(a, corpus: Corpus) -> None:
    items = corpus.unlabeled_sample(a.n, seed=a.seed)
    save_gold_sample(items, a.output)
    print(f"{len(items)} NOTAMs sorteados para o gold set (já no gold set: {len(corpus.gold_uids())}): {a.output}")
    print(f"Preencha a coluna 'human_label' e rode: python -m notam_schedule.ml label {a.output}")


def cmd_label(a, corpus: Corpus) -> None:
    ok, errors = corpus.import_labels_csv(a.file, set(LABELS), a.author)
    print(f"Rótulos importados: {ok}")
    for e in errors[:30]:
        print(f"  {e}", file=sys.stderr)


def cmd_predict(a, corpus: Corpus) -> None:
    m = NotamModel(a.model)
    for text, (p, cat, pc) in zip(a.texts, m.predict(a.texts)):
        decision = cat if p >= m.threshold else "NOT_RUNWAY"
        print(f"{decision:20s} p_pista={p:.3f} (limiar {m.threshold})  cat={cat} ({pc:.2f})  | {text}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="notam_schedule.ml", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=str(CORPUS_DB), help="SQLite corpus")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="corpus and label summary")
    s = sub.add_parser("collect", help="download NOTAMs from the AISWEB API into the corpus")
    s.add_argument("--icao", help="comma-separated ICAO list (default: airport table)")
    s.add_argument("--schedule", help="use the airports of a schedule file")
    s = sub.add_parser("import", help="import AISWEB XML (*.xml or the raw archive *.xml.gz, e.g. data/raw/aisweb/notam)")
    s.add_argument("dir")
    s = sub.add_parser("synthetic", help="(demo) add synthetic NOTAMs and simulated human labels")
    s.add_argument("--n", type=int, default=1500)
    s.add_argument("--gold", type=float, default=0.1, help="share with a simulated human label")
    s.add_argument("--seed", type=int, default=7)
    s = sub.add_parser("clusters", help="K-means: patterns and suggested action per cluster")
    s.add_argument("--k", type=int, help="number of clusters (default: chosen by silhouette)")
    s.add_argument("-o", "--output", default=str(CLUSTERS_FILE))
    s = sub.add_parser("train", help="train the semi-supervised model and write the model card")
    s.add_argument("-o", "--output", default=str(MODELS_DIR))
    s.add_argument("--target-recall", type=float, default=0.98)
    s.add_argument("--no-propagation", action="store_true", help="do not propagate labels via K-means")
    s = sub.add_parser("review", help="build the review queue (active learning)")
    s.add_argument("-n", type=int, default=200)
    s.add_argument("-o", "--output", default=str(REVIEW_FILE))
    s.add_argument("--model", default=str(MODEL_FILE))
    s = sub.add_parser("gold", help="random sample for the gold set (unbiased accuracy measurement)")
    s.add_argument("-n", type=int, default=400)
    s.add_argument("--seed", type=int, default=42)
    s.add_argument("-o", "--output", default=str(GOLD_FILE))
    s = sub.add_parser("label", help="import a filled-in review queue or gold sample")
    s.add_argument("file")
    s.add_argument("--author", default="")
    s = sub.add_parser("predict", help="classify NOTAM texts")
    s.add_argument("texts", nargs="+")
    s.add_argument("--model", default=str(MODEL_FILE))

    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    corpus = Corpus(a.db)
    {"status": cmd_status, "collect": cmd_collect, "import": cmd_import, "synthetic": cmd_synthetic,
     "clusters": cmd_clusters, "train": cmd_train, "review": cmd_review, "gold": cmd_gold, "label": cmd_label,
     "predict": cmd_predict}[a.cmd](a, corpus)
    return 0


if __name__ == "__main__":
    sys.exit(main())
