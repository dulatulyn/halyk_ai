from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

__all__ = ["Store", "Doc", "Txn", "sha256"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id      TEXT PRIMARY KEY,
    path        TEXT NOT NULL,
    sha         TEXT NOT NULL,
    n_pages     INTEGER NOT NULL,
    text        TEXT NOT NULL,
    kind        TEXT,
    scenario_id TEXT,
    superseded  INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pages (
    doc_id  TEXT NOT NULL,
    page_no INTEGER NOT NULL,
    text    TEXT NOT NULL,
    PRIMARY KEY (doc_id, page_no)
);
CREATE TABLE IF NOT EXISTS txns (
    txn_id       TEXT PRIMARY KEY,
    scenario_id  TEXT NOT NULL,
    date         TEXT NOT NULL,
    account_id   TEXT NOT NULL,
    counterparty TEXT NOT NULL,
    description  TEXT NOT NULL,
    amount       TEXT NOT NULL,
    currency     TEXT NOT NULL,
    category     TEXT
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE INDEX IF NOT EXISTS ix_txn_scenario ON txns(scenario_id);
CREATE INDEX IF NOT EXISTS ix_doc_scenario ON documents(scenario_id);
"""

PAGE_SEP = "\f"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True, slots=True)
class Doc:
    doc_id: str
    path: str
    n_pages: int
    text: str
    kind: str | None = None
    scenario_id: str | None = None
    superseded: bool = False

    def pages(self) -> list[str]:
        return self.text.split(PAGE_SEP)

    def page(self, n: int) -> str:
        pages = self.pages()
        if not 1 <= n <= len(pages):
            raise IndexError(f"{self.doc_id}: нет страницы {n} из {len(pages)}")
        return pages[n - 1]


@dataclass(frozen=True, slots=True)
class Txn:
    txn_id: str
    scenario_id: str
    date: str
    account_id: str
    counterparty: str
    description: str
    amount: Decimal
    currency: str
    category: str | None = None

    @property
    def is_outflow(self) -> bool:
        return self.amount < 0

    @property
    def abs_amount(self) -> Decimal:
        return abs(self.amount)


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)


    def close(self) -> None:
        self.db.commit()
        self.db.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def get_meta(self, key: str, default=None):
        row = self.db.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        return json.loads(row["v"]) if row else default

    def set_meta(self, key: str, value) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO kv(k,v) VALUES(?,?)", (key, json.dumps(value))
        )
        self.db.commit()


    def known_sha(self, doc_id: str) -> str | None:
        row = self.db.execute(
            "SELECT sha FROM documents WHERE doc_id=?", (doc_id,)
        ).fetchone()
        return row["sha"] if row else None

    def put_doc(self, doc_id: str, path: Path, sha: str, pages: list[str]) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO documents(doc_id,path,sha,n_pages,text) "
            "VALUES(?,?,?,?,?)",
            (doc_id, str(path), sha, len(pages), PAGE_SEP.join(pages)),
        )
        self.db.execute("DELETE FROM pages WHERE doc_id=?", (doc_id,))
        self.db.executemany(
            "INSERT INTO pages(doc_id,page_no,text) VALUES(?,?,?)",
            [(doc_id, i, t) for i, t in enumerate(pages, start=1)],
        )
        self.db.commit()

    def replace_text(self, doc_id: str, text: str) -> None:
        pages = text.split(PAGE_SEP)
        self.db.execute("UPDATE documents SET text=? WHERE doc_id=?", (text, doc_id))
        self.db.executemany(
            "UPDATE pages SET text=? WHERE doc_id=? AND page_no=?",
            [(t, doc_id, i) for i, t in enumerate(pages, start=1)],
        )

    def tag_doc(
        self,
        doc_id: str,
        *,
        kind: str | None = None,
        scenario_id: str | None = None,
        superseded: bool | None = None,
    ) -> None:
        sets, args = [], []
        for col, val in (("kind", kind), ("scenario_id", scenario_id)):
            if val is not None:
                sets.append(f"{col}=?")
                args.append(val)
        if superseded is not None:
            sets.append("superseded=?")
            args.append(int(superseded))
        if not sets:
            return
        args.append(doc_id)
        self.db.execute(f"UPDATE documents SET {','.join(sets)} WHERE doc_id=?", args)
        self.db.commit()

    def _doc(self, row: sqlite3.Row) -> Doc:
        return Doc(
            doc_id=row["doc_id"],
            path=row["path"],
            n_pages=row["n_pages"],
            text=row["text"],
            kind=row["kind"],
            scenario_id=row["scenario_id"],
            superseded=bool(row["superseded"]),
        )

    def docs(
        self, *, scenario_id: str | None = None, kind: str | None = None
    ) -> list[Doc]:
        sql, args = "SELECT * FROM documents WHERE 1=1", []
        if scenario_id is not None:
            sql += " AND scenario_id=?"
            args.append(scenario_id)
        if kind is not None:
            sql += " AND kind=?"
            args.append(kind)
        sql += " ORDER BY doc_id"
        return [self._doc(r) for r in self.db.execute(sql, args)]

    def doc(self, doc_id: str) -> Doc:
        row = self.db.execute(
            "SELECT * FROM documents WHERE doc_id=?", (doc_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"нет документа {doc_id!r}")
        return self._doc(row)


    def put_txns(self, rows: list[Txn]) -> None:
        self.db.executemany(
            "INSERT OR REPLACE INTO txns"
            "(txn_id,scenario_id,date,account_id,counterparty,description,"
            " amount,currency,category) VALUES(?,?,?,?,?,?,?,?,?)",
            [
                (
                    t.txn_id, t.scenario_id, t.date, t.account_id, t.counterparty,
                    t.description, str(t.amount), t.currency, t.category,
                )
                for t in rows
            ],
        )
        self.db.commit()

    def set_category(self, txn_id: str, category: str) -> None:
        self.db.execute("UPDATE txns SET category=? WHERE txn_id=?", (category, txn_id))

    def commit(self) -> None:
        self.db.commit()

    def txns(self, *, scenario_id: str | None = None) -> list[Txn]:
        sql, args = "SELECT * FROM txns", []
        if scenario_id is not None:
            sql += " WHERE scenario_id=?"
            args.append(scenario_id)
        sql += " ORDER BY date, txn_id"
        return [
            Txn(
                txn_id=r["txn_id"], scenario_id=r["scenario_id"], date=r["date"],
                account_id=r["account_id"], counterparty=r["counterparty"],
                description=r["description"], amount=Decimal(r["amount"]),
                currency=r["currency"], category=r["category"],
            )
            for r in self.db.execute(sql, args)
        ]

    def scenarios(self) -> list[str]:
        rows = self.db.execute(
            "SELECT DISTINCT scenario_id FROM documents WHERE scenario_id IS NOT NULL"
        ).fetchall()
        return sorted({r["scenario_id"] for r in rows})
