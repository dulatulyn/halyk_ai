from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pdfplumber

from solution.store import Store, Txn, sha256

__all__ = ["ingest_documents", "ingest_ledger", "IngestReport", "TXN_ID_RE"]

TXN_ID_RE = re.compile(r"^TXN-(?P<scenario>[A-Za-z0-9]+)-\S+$")


@dataclass(slots=True)
class IngestReport:
    scanned: int = 0
    parsed: int = 0
    cached: int = 0
    failed: list[tuple[str, str]] = None
    empty_pages: int = 0

    def __post_init__(self):
        if self.failed is None:
            self.failed = []

    def line(self) -> str:
        return (
            f"документов {self.scanned}: разобрано {self.parsed}, "
            f"из кэша {self.cached}, ошибок {len(self.failed)}, "
            f"пустых страниц {self.empty_pages}"
        )


def ingest_documents(store: Store, folder: Path | str) -> IngestReport:
    folder = Path(folder)
    rep = IngestReport()

    for path in sorted(folder.glob("*.pdf")):
        rep.scanned += 1
        doc_id = path.stem
        digest = sha256(path)

        if store.known_sha(doc_id) == digest:
            rep.cached += 1
            continue

        try:
            with pdfplumber.open(path) as pdf:
                pages = [(p.extract_text() or "").strip() for p in pdf.pages]
        except Exception as exc:
            rep.failed.append((path.name, f"{type(exc).__name__}: {exc}"))
            continue

        rep.empty_pages += sum(1 for p in pages if not p)
        store.put_doc(doc_id, path, digest, pages)
        rep.parsed += 1

    return rep


def _amount(raw: str) -> Decimal:
    try:
        return Decimal(raw.strip().replace(",", "").replace(" ", ""))
    except (InvalidOperation, AttributeError):
        raise ValueError(f"не число: {raw!r}") from None


def ingest_ledger(store: Store, csv_path: Path | str) -> dict[str, int]:
    csv_path = Path(csv_path)
    rows: list[Txn] = []
    bad: list[str] = []

    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            m = TXN_ID_RE.match((r.get("txn_id") or "").strip())
            if not m:
                bad.append(r.get("txn_id", "?"))
                continue
            try:
                amount = _amount(r["amount"])
            except ValueError:
                amount = Decimal(0)
                bad.append(r["txn_id"])
            rows.append(
                Txn(
                    txn_id=r["txn_id"].strip(),
                    scenario_id=m.group("scenario"),
                    date=r["date"].strip(),
                    account_id=r["account_id"].strip(),
                    counterparty=(r.get("counterparty") or "").strip(),
                    description=(r.get("description") or "").strip(),
                    amount=amount,
                    currency=(r.get("currency") or "USD").strip().upper(),
                )
            )

    store.put_txns(rows)
    return {"loaded": len(rows), "rejected": len(bad)}
