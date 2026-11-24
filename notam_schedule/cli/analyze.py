"""CLI: python -m notam_schedule SCHEDULE.(csv|xlsx) [options]

Thin layer: parses arguments, builds the adapters and calls pipelines.analyze.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from ..config import AIRPORTS_FILE, CACHE_DIR, MODEL_FILE, OUTPUT_DIR, RAW_DIR, RUNS_DB, AiswebConfig
from ..domain.categories import AERODROME_CLOSED, CATEGORIES, RUNWAY_CLOSED
from ..domain.changes import ENDED, NEW, SEVERITY_CHANGED, WINDOW_CHANGED
from ..domain.impact import daily_summary
from ..ingestion.airports import AirportDB
from ..ingestion.aisweb import AiswebClient
from ..ingestion.schedule import ScheduleError, load_schedule, parse_date
from ..pipelines.analyze import analyze
from ..pipelines.history import record_run
from ..reporting.csv_export import write_csv
from ..reporting.excel import write_excel
from ..storage.raw_archive import FileRawArchive
from ..storage.runs import RunStore


DEFAULT_CATEGORIES = f"{RUNWAY_CLOSED},{AERODROME_CLOSED}"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="notam_schedule",
        description="Cross-check a flight schedule (CSV/XLSX) against runway-condition NOTAMs from AISWEB/DECEA.",
    )
    p.add_argument("schedule", help="flight schedule file (.csv or .xlsx)")
    p.add_argument("-o", "--output", help=f"output .xlsx (default: {OUTPUT_DIR}/notam_impact_<timestamp>.xlsx)")
    p.add_argument("--csv", action="store_true", help="also write a CSV with the per-flight impact")
    p.add_argument("--history", action=argparse.BooleanOptionalAction, default=None,
                   help="record the run and compare with the previous one (default: on with the API, off offline)")
    p.add_argument("--history-db", default=str(RUNS_DB), help="runs database (default: %(default)s)")
    p.add_argument("--sheet", help="worksheet name for .xlsx input (default: first sheet)")
    p.add_argument("--from", dest="date_from", help="first date to analyze (YYYY-MM-DD or DD/MM/YYYY)")
    p.add_argument("--to", dest="date_to", help="last date to analyze")
    p.add_argument("--timezone", choices=["auto", "local", "utc"], default="auto",
                   help="time basis of STD/STA in the schedule (default: auto-detect from block time, else local)")
    p.add_argument("--margin-before", type=int, default=60, metavar="MIN",
                   help="minutes before STD/STA considered (default 60)")
    p.add_argument("--margin-after", type=int, default=60, metavar="MIN",
                   help="minutes after STD/STA considered (default 60)")
    p.add_argument("--categories", default=DEFAULT_CATEGORIES,
                   help=f"comma-separated categories to keep ({','.join(CATEGORIES)}) or 'all' "
                        "(default: %(default)s = closures only)")
    p.add_argument("--airports", help=f"airport CSV for IATA->ICAO (default: {AIRPORTS_FILE} when present); "
                                      "layouts: iata,icao,name,tz or ident,latitude_deg,longitude_deg,iata_code")
    p.add_argument("--notam-dir", help="offline mode: directory with <ICAO>.xml (and rotaer_<ICAO>.xml)")
    p.add_argument("--cache-dir", default=str(CACHE_DIR), help="API response cache directory")
    p.add_argument("--cache-ttl", type=int, default=60, metavar="MIN", help="cache lifetime (default 60 min)")
    p.add_argument("--no-rotaer", action="store_true", help="do not query ROTAER (published runway length)")
    p.add_argument("--detector", choices=["rules", "hybrid", "ml"], default="rules",
                   help="rules (default), hybrid (rule OR model — recommended with a trained model) or ml")
    p.add_argument("--model", default=str(MODEL_FILE), help="trained model (python -m notam_schedule.ml train)")
    p.add_argument("--threshold", type=float, help="model probability threshold (default: from the model card)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(message)s")

    try:
        airports = AirportDB.default(args.airports)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    local_times = {"auto": None, "local": True, "utc": False}[args.timezone]
    try:
        sched = load_schedule(
            args.schedule, airports, local_times=local_times, sheet=args.sheet,
            date_from=parse_date(args.date_from) if args.date_from else None,
            date_to=parse_date(args.date_to) if args.date_to else None,
        )
    except ScheduleError as exc:
        print(f"Erro na malha: {exc}", file=sys.stderr)
        return 2
    flights = sched.flights
    if not flights:
        print("Nenhum voo encontrado na malha para o período informado.", file=sys.stderr)
        return 2

    categories = None
    if args.categories and args.categories.strip().lower() != "all":
        categories = {c.strip().upper() for c in args.categories.split(",") if c.strip()}
        invalid = categories - set(CATEGORIES)
        if invalid:
            print(f"Categorias inválidas: {', '.join(sorted(invalid))}", file=sys.stderr)
            return 2

    detector = None
    if args.detector != "rules":
        if not Path(args.model).exists():
            print(f"Modelo não encontrado: {args.model}. Treine com: python -m notam_schedule.ml train",
                  file=sys.stderr)
            return 2
        from ..ml.detector import HybridDetector
        detector = HybridDetector(args.model, args.detector, args.threshold)

    config = None if args.notam_dir else AiswebConfig.from_env()
    archive = None if args.notam_dir else FileRawArchive(RAW_DIR)
    client = AiswebClient(config, cache_dir=args.cache_dir, cache_ttl_min=args.cache_ttl, offline_dir=args.notam_dir,
                          archive=archive)

    res = analyze(flights, client, args.margin_before, args.margin_after, categories,
                  use_rotaer=not args.no_rotaer, detector=detector)

    output = Path(args.output or OUTPUT_DIR / f"notam_impact_{datetime.now():%Y%m%d_%H%M}.xlsx")
    params = {
        "Malha": args.schedule, "Horários da malha": sched.time_basis,
        "Margem (min) antes/depois": f"{args.margin_before}/{args.margin_after}",
        "Categorias": "todas" if categories is None else ", ".join(sorted(categories)),
        "Fonte NOTAM": args.notam_dir or "API AISWEB",
        "Detector": args.detector if not detector else
        f"{args.detector} — modelo {detector.model.version}, limiar {detector.threshold}",
    }
    use_history = args.history if args.history is not None else not args.notam_dir
    changes = None
    if use_history:
        changes = record_run(RunStore(args.history_db), res, args.schedule, args.detector, params)
    write_excel(res, output, params, changes)
    if args.csv:
        write_csv(res, output.with_suffix(".csv"))

    impacted = {(i.flight.row, i.flight.flight, i.flight.date) for i in res.impacts}
    print(f"Horários da malha: {sched.time_basis}")
    print(f"Voos analisados: {len(flights)} | voos impactados: {len(impacted)} | "
          f"NOTAMs de pista na janela: {sum(len(v) for v in res.runway_notams.values())}")
    for d in daily_summary(res):
        if d["impacted"]:
            aps = ", ".join(sorted(d["airports"]))
            print(f"  {d['date']:%d/%m/%Y}: {d['impacted']}/{d['flights']} voos ({d['critical']} críticos) — {aps}")
    if res.out_of_coverage:
        print(f"Fora da cobertura DECEA (não consultados): {', '.join(res.out_of_coverage)}")
    for ap, err in res.errors.items():
        print(f"ERRO {ap}: {err}", file=sys.stderr)
    if changes is not None:
        if changes.previous_run is None:
            print("Histórico: primeira execução registrada.")
        else:
            print(f"Desde a última execução: {changes.count(NEW)} impactos novos, "
                  f"{changes.count(SEVERITY_CHANGED)} com severidade alterada, "
                  f"{changes.count(WINDOW_CHANGED)} com janela alterada, {changes.count(ENDED)} encerrados")
    print(f"Relatório: {output}")
    return 1 if res.errors else 0


if __name__ == "__main__":
    sys.exit(main())
