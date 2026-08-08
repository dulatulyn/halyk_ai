from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from solution import llm
from solution.categorize import categorize
from solution.covenants import parse_scenario
from solution.diag import DIAG
from solution.evaluate import evaluate
from solution.ingest import ingest_documents, ingest_ledger
from solution.ocr import transcribe
from solution.link import link_documents
from solution.store import Store


def build(dataset: Path, db: Path, template: dict, team: str, email: str, model: str) -> dict:
    DIAG.reset()
    llm.reset_breaker()
    with Store(db) as store:
        ingest_documents(store, dataset / "documents")
        ingest_ledger(store, dataset / "master_ledger_2025.csv")
        transcribe(store)
        link_documents(store)
        categorize(store)

        DIAG.bump("docs.total", len(list(store.docs())))
        DIAG.bump("clauses.expected", sum(len(v) for v in template["answers"].values()))

        answers: dict[str, dict] = {}
        for scenario, clauses in template["answers"].items():
            answers[scenario] = {}
            try:
                parsed = {c.clause: c
                          for c in parse_scenario(store, scenario, list(clauses))}
            except Exception as exc:
                print(f"  {scenario}: договор не разобран: {exc}", file=sys.stderr)
                DIAG.note("scenario.unparsed", f"{scenario}: {type(exc).__name__}: {exc}")
                parsed = {}

            for clause in clauses:
                cov = parsed.get(clause)
                if cov is None:
                    DIAG.note("cell.missing", f"{scenario}/{clause}")
                    answers[scenario][clause] = {
                        "status": "COMPLIANT", "actual": None, "evidence_txn_id": None
                    }
                    continue
                try:
                    answers[scenario][clause] = evaluate(store, cov).cell()
                except Exception as exc:
                    print(f"  {scenario}/{clause}: {type(exc).__name__}: {exc}", file=sys.stderr)
                    DIAG.note("cell.exception",
                              f"{scenario}/{clause}: {type(exc).__name__}: {exc}")
                    answers[scenario][clause] = {
                        "status": "COMPLIANT", "actual": None, "evidence_txn_id": None
                    }

        DIAG.bump("clauses.parsed", sum(
            1 for cells in answers.values() for cell in cells.values()
            if cell["actual"] is not None))

    used = llm.used_models()
    return {"team": team, "contact_email": email,
            "model": ", ".join(used) if used else model, "answers": answers}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("out/submission.json"))
    ap.add_argument("--db", type=Path, default=Path("data/bank.db"))
    ap.add_argument("--team", default="")
    ap.add_argument("--email", default="")
    ap.add_argument("--model", default="claude-opus-5")
    ap.add_argument("--score-against", type=Path)
    ap.add_argument("--report", action="store_true",
                    help="печатать диагностику: единственный сигнал качества без ключа")
    args = ap.parse_args(argv)

    template_path = args.dataset / "submission_template.json"
    if not template_path.exists():
        print(f"нет {template_path}", file=sys.stderr)
        return 2

    started = time.monotonic()
    template = json.loads(template_path.read_text(encoding="utf-8"))
    submission = build(args.dataset, args.db, template, args.team, args.email, args.model)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(submission, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"записано {args.out} за {time.monotonic() - started:.1f} c")

    if args.report:
        print()
        print(DIAG.render())

    if args.score_against and args.score_against.exists():
        from solution.score import load_key, score_submission
        print()
        print(score_submission(submission, load_key(args.score_against)).report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
