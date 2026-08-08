from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

__all__ = ["CellScore", "Scoreboard", "score_cell", "score_submission", "load_key"]

W_STATUS = Decimal("0.50")
W_ACTUAL = Decimal("0.30")
W_EVIDENCE = Decimal("0.20")
TOLERANCE = Decimal("0.05")


def _num(value) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _actual_fraction(mine, key) -> Decimal:
    got, want = _num(mine), _num(key)
    if got is None or want is None:
        return Decimal(0)
    if want == 0:
        return Decimal(1) if got == 0 else Decimal(0)
    err = abs(got - want) / abs(want)
    if err >= TOLERANCE:
        return Decimal(0)
    return Decimal(1) - err / TOLERANCE


@dataclass(frozen=True, slots=True)
class CellScore:
    scenario: str
    clause: str
    status_ok: bool
    actual_fraction: Decimal
    evidence_ok: bool
    points: Decimal
    note: str = ""

    @property
    def key(self) -> str:
        return f"{self.scenario}/{self.clause}"


def score_cell(mine: dict | None, key: dict, scenario: str, clause: str) -> CellScore:
    mine = mine or {}
    status_ok = mine.get("status") == key.get("status")
    frac = _actual_fraction(mine.get("actual"), key.get("actual"))
    key_ev = key.get("evidence_txn_id")

    if not status_ok:
        return CellScore(
            scenario, clause, False, frac, False, Decimal(0),
            note="status неверен, ячейка обнулена",
        )

    points = W_STATUS + W_ACTUAL * frac
    if key_ev is None:
        evidence_ok = True
        points += W_EVIDENCE * frac
    else:
        evidence_ok = mine.get("evidence_txn_id") == key_ev
        if evidence_ok:
            points += W_EVIDENCE

    return CellScore(scenario, clause, True, frac, evidence_ok, points)


@dataclass(slots=True)
class Scoreboard:
    cells: list[CellScore] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return sum((c.points for c in self.cells), Decimal(0))

    @property
    def max_total(self) -> int:
        return len(self.cells)

    @property
    def ratio(self) -> Decimal:
        return self.total / self.max_total if self.cells else Decimal(0)

    def report(self) -> str:
        n = len(self.cells)
        status_ok = sum(c.status_ok for c in self.cells)
        exact = sum(c.actual_fraction == 1 for c in self.cells)
        partial = sum(0 < c.actual_fraction < 1 for c in self.cells)
        ev_needed = [c for c in self.cells if not c.evidence_ok]

        lines = [
            f"БАЛЛ  {self.total:.3f} / {self.max_total}  ({self.ratio:.1%})",
            "",
            f"  status верен      {status_ok}/{n}",
            f"  actual точен      {exact}/{n}   (частично {partial})",
            f"  evidence промах   {len(ev_needed)}",
        ]
        if self.missing:
            lines.append(f"  ПРОПУЩЕНЫ ЯЧЕЙКИ  {self.missing}")
        if self.extra:
            lines.append(f"  ЛИШНИЕ КЛЮЧИ      {self.extra}")

        lines += ["", "  ячейка   балл   status  actual  evidence"]
        for c in sorted(self.cells, key=lambda c: c.points):
            lines.append(
                f"  {c.key:9} {c.points:.2f}   "
                f"{'ok ' if c.status_ok else 'НЕТ'}     "
                f"{c.actual_fraction:.2f}    "
                f"{'ok' if c.evidence_ok else 'НЕТ'}"
            )
        return "\n".join(lines)


def load_key(path: Path | str) -> dict[str, dict[str, dict]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    scenarios = raw.get("scenarios", raw)
    return {
        sc: (body.get("covenants", body))
        for sc, body in scenarios.items()
    }


def score_submission(submission: dict, key: dict[str, dict[str, dict]]) -> Scoreboard:
    answers = submission.get("answers", {})
    board = Scoreboard()

    for scenario, clauses in key.items():
        for clause, cell_key in clauses.items():
            mine = (answers.get(scenario) or {}).get(clause)
            if mine is None:
                board.missing.append(f"{scenario}/{clause}")
            board.cells.append(score_cell(mine, cell_key, scenario, clause))

    for scenario, clauses in answers.items():
        for clause in clauses:
            if clause not in key.get(scenario, {}):
                board.extra.append(f"{scenario}/{clause}")

    return board


if __name__ == "__main__":
    import sys

    sub = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    key = load_key(sys.argv[2] if len(sys.argv) > 2
                   else "data/agentic-bank-public/ground_truth.json")
    print(score_submission(sub, key).report())
