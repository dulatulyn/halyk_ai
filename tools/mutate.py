from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

from solution.run import build
from solution.score import load_key, score_submission
from solution.store import Store

MUTATIONS = {
    "clause_word": lambda t: re.sub(r"\bПункт(\s+\d)", r"Статья\1", t),
    "edition_marker": lambda t: re.sub(
        r"УТРАТИЛА\s+СИЛУ", "ПРЕКРАТИЛА ДЕЙСТВИЕ",
        re.sub(r"НЕ\s+ПРИМЕНЯЕТСЯ", "НЕ ПОДЛЕЖИТ ПРИМЕНЕНИЮ",
               re.sub(r"НЕДЕЙСТВУЮЩАЯ\s+РЕДАКЦИЯ", "РЕДАКЦИЯ УТРАТИЛА АКТУАЛЬНОСТЬ",
                      t, flags=re.I), flags=re.I), flags=re.I),
    "period_format": lambda t: re.sub(r"(\d{4})-(\d\d)-(\d\d)", r"\3.\2.\1", t),
    "modifier_phrase": lambda t: re.sub(
        r"не\s+отражена\s+в\s+выгрузке", "отсутствует в реестре операций", t, flags=re.I),
    "metric_title": lambda t: re.sub(
        r"покрыти(\w+)\s+процентов", r"обслуживани\1 процентных обязательств", t, flags=re.I),
}


def apply_mutation(db_path: Path, name: str) -> int:
    fn = MUTATIONS[name]
    touched = 0
    with Store(db_path) as store:
        for doc in store.docs():
            new = fn(doc.text)
            if new != doc.text:
                store.replace_text(doc.doc_id, new)
                touched += 1
        store.commit()
    return touched


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", type=Path, required=True)
    ap.add_argument("--base-db", type=Path, required=True,
                    help="прогретая БД, копируется под каждую мутацию")
    ap.add_argument("--work", type=Path, required=True, help="каталог для копий БД")
    args = ap.parse_args(argv)

    template = json.loads(
        (args.dataset / "submission_template.json").read_text(encoding="utf-8"))
    key = load_key(args.dataset / "ground_truth.json")
    args.work.mkdir(parents=True, exist_ok=True)

    rows: list[tuple[str, int, float]] = []
    for name in ["none", *MUTATIONS]:
        db = args.work / f"{name}.db"
        shutil.copy(args.base_db, db)
        touched = apply_mutation(db, name) if name != "none" else 0
        submission = build(args.dataset, db, template, "", "", "claude-opus-5")
        total = float(score_submission(submission, key).total)
        rows.append((name, touched, total))
        print(f"  {name}: {total:.3f} (документов изменено {touched})")

    width = max(len(r[0]) for r in rows)
    base = rows[0][2]
    print(f"\n{'мутация'.ljust(width)}  докум.    балл  дельта")
    for name, touched, total in rows:
        delta = "" if name == "none" else f"  {total - base:+.3f}"
        print(f"{name.ljust(width)}  {touched:6d}  {total:6.3f}{delta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
