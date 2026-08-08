from __future__ import annotations

import json
import re
from pathlib import Path

SOLUTION = Path(__file__).resolve().parent.parent / "solution"
DATASET = Path(__file__).resolve().parent.parent / "data" / "agentic-bank-public"

SCENARIO = re.compile(r"['\"](?:[BP]\d{1,2})['\"]")
ACCOUNT = re.compile(r"ACC-\d{3,}")
TXN = re.compile(r"TXN-[A-Za-z0-9]+-\d+")


def sources() -> list[Path]:
    return sorted(p for p in SOLUTION.glob("*.py") if p.name != "__init__.py")


def strip_comments(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def test_no_scenario_ids():
    offenders = []
    for path in sources():
        for n, line in enumerate(strip_comments(path.read_text(encoding="utf-8")).splitlines(), 1):
            if SCENARIO.search(line):
                offenders.append(f"{path.name}:{n}: {line.strip()}")
    assert not offenders, "зашитые идентификаторы сценариев:\n" + "\n".join(offenders)


def test_no_account_or_txn_literals():
    offenders = []
    for path in sources():
        text = strip_comments(path.read_text(encoding="utf-8"))
        for pattern in (ACCOUNT, TXN):
            for match in pattern.finditer(text):
                if "\\d" in match.group(0):
                    continue
                offenders.append(f"{path.name}: {match.group(0)}")
    assert not offenders, "зашитые идентификаторы:\n" + "\n".join(offenders)


def test_no_counterparty_or_borrower_names():
    key = json.loads((DATASET / "ground_truth.json").read_text(encoding="utf-8"))
    scenarios = set(key["scenarios"])
    names = set()
    for row in (DATASET / "master_ledger_2025.csv").read_text(encoding="utf-8").splitlines()[1:]:
        parts = row.split(",")
        if len(parts) > 3 and len(parts[3].split()) > 1:
            names.add(parts[3].strip())
    assert scenarios and names

    offenders = []
    for path in sources():
        text = strip_comments(path.read_text(encoding="utf-8"))
        for name in names:
            if name in text:
                offenders.append(f"{path.name}: {name}")
    assert not offenders, "названия контрагентов в коде:\n" + "\n".join(sorted(set(offenders)))


def test_thresholds_are_not_pinned():
    numeric = re.compile(r"Decimal\(\"(\d+\.\d+)\"\)")
    allowed = {"0.50", "0.30", "0.20", "0.05", "0.01", "20", "0.0"}
    offenders = []
    for path in sources():
        if path.name == "score.py":
            continue
        for value in numeric.findall(path.read_text(encoding="utf-8")):
            if value not in allowed:
                offenders.append(f"{path.name}: Decimal(\"{value}\")")
    assert not offenders, "пороги зашиты в код:\n" + "\n".join(offenders)
