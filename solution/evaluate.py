from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, getcontext

from solution.categorize import categorize_one, is_related, normalize_party
from solution.adjustments import Adjustments, collect
from solution.covenants import Covenant
from solution.diag import DIAG
from solution.modifiers import apply, read_notes
from solution.parties import related_parties, unrestricted_subsidiaries
from solution.store import Store, Txn

getcontext().prec = 34
CENT = Decimal("0.01")

FISCAL_TAIL = re.compile(r"\b(20\d\d)\s*$")
QUALIFIER = "—"

OPERATING = re.compile(r"operating\b[\w\s]{0,25}?\b(costs?|expenses?)", re.I)
EQUIPMENT = re.compile(
    r"purchase of .*(equipment|plant|machin|vehicle|rig|line|system)|"
    r"construction of|acquisition of .*asset", re.I)
QUARTERS = {"first": 1, "second": 2, "third": 3, "fourth": 4}
QUARTER_WORD = re.compile(r"\b(first|second|third|fourth)\s+quarter\b", re.I)
QUARTER_SHORT = re.compile(r"\bQ([1-4])\b", re.I)
TRANSFER = re.compile(
    r"transfer[^.]{0,80}?\bto\s+[\w\s]{0,30}?subsidiar|asset transfer|"
    r"contribution of assets|unrestricted subsidiar|intra-?group transfer|"
    # «Transfer of production line machinery and tooling» — передача активов без
    # слова subsidiary. Требуем предмет передачи, иначе «Payroll top-up transfer»
    # тоже сойдёт за отчуждение активов.
    r"transfer of [^.]{0,40}?(?:equipment|machiner|asset|tooling|plant|line|vehicle|"
    r"facility|inventory)", re.I)
GROUP = re.compile(r"\bgroup\b|consolidated", re.I)

_CAPEX_ACT = (r"purchase|acquisition|procurement|installation|upgrade|overhaul|"
              r"retrofit|refurbish|modernis|moderniz|commissioning|replacement|"
              r"expansion|construction|fit-?out")
_CAPEX_OBJ = (r"equipment|machiner|machine|plant|vehicle|rig|kiln|conveyor|turbine|"
              r"boiler|furnace|press|crane|pipeline|substation|warehouse|facility|"
              r"infrastructure|asset|line\b|system")
CAPEX_BROAD = re.compile(
    rf"capital expenditure|\bcapex\b|"
    rf"(?:{_CAPEX_ACT})\w*[^.]{{0,40}}?(?:{_CAPEX_OBJ})|"
    rf"(?:{_CAPEX_OBJ})\w*[^.]{{0,40}}?(?:{_CAPEX_ACT})", re.I)

# Третий уровень: голое капитальное имущество без глагола —
# «Grain silo aeration equipment». Обслуживание и аренда такого же имущества
# капзатратами не являются, поэтому отсекаются явно.
CAPEX_GOODS = re.compile(
    r"\b(?:equipment|machiner\w*|turbine|kiln|conveyor|boiler|furnace|crane|"
    r"substation|pipeline|rolling stock|fixed assets?)\b", re.I)
CAPEX_NOT = re.compile(
    r"maintenance|repair|servicing|service contract|\blease\b|rental|\brent\b|"
    r"insurance|advisory|consult|training|licence|license|subscription", re.I)


@dataclass(slots=True)
class Verdict:
    scenario_id: str
    clause: str
    status: str
    actual: Decimal | None
    evidence_txn_id: str | None = None
    note: str = ""

    def cell(self) -> dict:
        return {
            "status": self.status,
            "actual": float(self.actual.quantize(CENT, rounding=ROUND_HALF_UP))
            if self.actual is not None else None,
            "evidence_txn_id": self.evidence_txn_id,
        }


def is_annual(txn: Txn, year: str) -> bool:
    if QUALIFIER in txn.description:
        return False
    tail = FISCAL_TAIL.search(txn.description)
    return bool(tail and tail.group(1) == year)


class Book:
    def __init__(self, txns: list[Txn], related: set[str], year: str,
                 adj: Adjustments, disclosed: list[tuple[str, Decimal]],
                 restated: set[str] | None = None,
                 converted: set[str] | None = None) -> None:
        self.txns = [t for t in txns if t.amount != 0]
        self.related = related
        self.year = year
        self.adj = adj
        self.disclosed = disclosed
        self.restated = restated or set()
        self.converted = converted or set()
        self.unrestricted: set[str] = set()
        self.cat: dict[str, list[Txn]] = {}
        for t in self.txns:
            self.cat.setdefault(t.category or categorize_one(t), []).append(t)

    def _pick(self, cat: str, outflow: bool = True) -> list[Txn]:
        rows = [t for t in self.cat.get(cat, []) if (t.amount < 0) == outflow]
        picked = [t for t in rows if is_annual(t, self.year)]
        if not picked:
            picked = [t for t in rows if QUALIFIER not in t.description]
        extra = [t for t in rows if t.txn_id in self.restated and t not in picked]
        return picked + extra

    def line(self, *cats: str) -> Decimal:
        total = sum((t.abs_amount for c in cats for t in self._pick(c)), Decimal(0))
        return total + sum((v for name, v in self.disclosed if name in cats), Decimal(0))

    @property
    def opex(self) -> Decimal:
        rows = [t for t in self.txns
                if t.amount < 0 and QUALIFIER not in t.description
                and (OPERATING.search(t.description)
                     or (t.txn_id in self.restated and (t.category or "") == "other")
                     or t.txn_id in self.converted)]
        base = sum((t.abs_amount for t in rows), Decimal(0))
        if not base:
            # Запасной путь — вся статья «прочее». Из неё надо вычесть капитальные
            # покупки: иначе одна и та же строка идёт и в капзатраты, и в опекс,
            # и EBITDA уходит в минус (у KC — на 387 млн).
            picked = [t for t in self._pick("other")
                      if not CAPEX_BROAD.search(t.description)]
            base = sum((t.abs_amount for t in picked), Decimal(0))
            base += sum((v for name, v in self.disclosed if name == "other"), Decimal(0))
        return base + self.adj.one_off_total

    @property
    def revenue(self) -> Decimal:
        rows = [t for t in self.cat.get("revenue", []) if is_annual(t, self.year)]
        if not rows:
            rows = self.cat.get("revenue", [])
        return sum((t.abs_amount for t in rows), Decimal(0))

    def quarter_of(self, text: str) -> int | None:
        if word := QUARTER_WORD.search(text):
            return QUARTERS[word.group(1).lower()]
        if short := QUARTER_SHORT.search(text):
            return int(short.group(1))
        return None

    def revenue_quarter(self, n: int) -> Decimal:
        rows = self.cat.get("revenue", [])
        total = sum((t.abs_amount for t in rows if self.quarter_of(t.description) == n),
                    Decimal(0))
        if total or not rows:
            return total
        # Кварталы не размечены словами — берём наибольшую квартальную долю.
        by_q: dict[int, Decimal] = {}
        for t in rows:
            q = self.quarter_of(t.description)
            if q:
                by_q[q] = by_q.get(q, Decimal(0)) + t.abs_amount
        return max(by_q.values()) if by_q else total

    @property
    def capex(self) -> Decimal:
        rows = [t for t in self.txns if t.amount < 0
                and (EQUIPMENT.search(t.description) or TRANSFER.search(t.description))]
        if not rows:
            # Узкий детектор промолчал — на приватном наборе это давало ноль там,
            # где капзатраты записаны как «Kiln conveyor machinery upgrade».
            # Широкий требует пары «действие + объект»: одного слова мало, иначе
            # «Equipment yard lease» и «Plant and boiler insurance» пройдут за капзатраты.
            rows = [t for t in self.txns if t.amount < 0
                    and (t.category or "") not in ("interest", "insurance", "rent",
                                                   "taxes", "payroll", "utilities",
                                                   "revenue", "financing_in")
                    and CAPEX_BROAD.search(t.description)]
        if not rows:
            rows = [t for t in self.cat.get("other", [])
                    if t.amount < 0 and CAPEX_GOODS.search(t.description)
                    and not CAPEX_NOT.search(t.description)]
        return sum((t.abs_amount for t in rows), Decimal(0))

    def group_capex(self) -> Decimal:
        rows = [t for t in self.txns
                if t.amount < 0 and EQUIPMENT.search(t.description)
                and GROUP.search(t.description)]
        return sum((t.abs_amount for t in rows), Decimal(0)) or self.capex

    @property
    def ebitda(self) -> Decimal:
        return self.revenue - self.opex

    @property
    def adjusted_ebitda(self) -> Decimal:
        return self.ebitda + self.adj.add_backs

    @property
    def related_total(self) -> Decimal:
        return sum(
            (t.abs_amount for t in self.txns if t.amount < 0 and is_related(t, self.related)),
            Decimal(0),
        )

    def related_txn(self) -> Txn | None:
        hits = [t for t in self.txns if t.amount < 0 and is_related(t, self.related)]
        return max(hits, key=lambda t: t.abs_amount) if hits else None

    def transfers(self) -> Decimal:
        rows = [t for t in self.txns if t.amount < 0 and TRANSFER.search(t.description)]
        if self.unrestricted:
            rows = [t for t in rows if normalize_party(t.counterparty) in self.unrestricted]
        return sum((t.abs_amount for t in rows), Decimal(0))

    def financing(self) -> Decimal:
        rows = [t for t in self.cat.get("financing_in", []) if t.amount > 0]
        annual = [t for t in rows if is_annual(t, self.year)]
        return sum((t.abs_amount for t in (annual or rows)), Decimal(0))


def ratio(num: Decimal, den: Decimal) -> Decimal | None:
    return None if den == 0 else num / den


QUANTITY = {
    "revenue": lambda b: b.revenue,
    "revenue_q4": lambda b: b.revenue_quarter(4),
    "opex": lambda b: b.opex,
    "ebitda": lambda b: b.ebitda,
    "adjusted_ebitda": lambda b: b.adjusted_ebitda,
    "capex": lambda b: b.capex,
    "group_capex": lambda b: b.adj.group_capex or b.group_capex(),
    "related_total": lambda b: b.related_total,
    "transfers": lambda b: b.transfers(),
    "financing_in": lambda b: b.financing(),
    "accrued_taxes": lambda b: b.adj.accrued.get("taxes", Decimal(0)),
    "accrued_payroll": lambda b: b.adj.accrued.get("payroll", Decimal(0)),
}


def quantity(book: Book, name: str) -> Decimal:
    if name in QUANTITY:
        return QUANTITY[name](book)
    return book.line(name)


def from_spec(spec, book: Book) -> Decimal | None:
    top = sum((quantity(book, n) for n in spec.numerator), Decimal(0))
    if not spec.denominator:
        return top
    return ratio(top, sum((quantity(book, n) for n in spec.denominator), Decimal(0)))


def compute(cov: Covenant, book: Book) -> tuple[Decimal | None, str | None]:
    if cov.spec is not None:
        return from_spec(cov.spec, book), None
    m = cov.metric
    if m.startswith("cat_"):
        name = m[4:]
        return (book.capex if name == "capex" else book.line(name)), None
    if m == "revenue_absolute":
        return book.revenue, None
    if m == "revenue_q4":
        return book.revenue_quarter(4), None
    if m == "capex_absolute":
        return book.capex, None
    if m == "related_absolute":
        t = book.related_txn()
        return book.related_total, (t.txn_id if t else None)
    if m == "related_over_revenue":
        t = book.related_txn()
        return ratio(book.related_total, book.revenue), (t.txn_id if t else None)
    if m == "related_over_opex":
        t = book.related_txn()
        return ratio(book.related_total, book.opex), (t.txn_id if t else None)
    if m == "interest_cover":
        return ratio(book.ebitda, book.line("interest")), None
    if m == "overhead_line_max":
        return max(book.line("payroll"), book.line("utilities")), None
    if m == "capital_intensity":
        return ratio(book.capex, book.opex + book.line("rent")), None
    if m == "sources_cover":
        return ratio(book.revenue + book.financing(), book.opex + book.capex), None
    if m == "springing_leverage":
        return ratio(book.financing(), book.ebitda), None
    if m == "adjusted_margin":
        return ratio(book.adjusted_ebitda, book.revenue), None
    if m == "group_capex_ebitda":
        group = book.adj.group_capex or book.group_capex()
        return ratio(group, book.ebitda), None
    if m == "revenue_cover_costs":
        return ratio(book.revenue, book.line("payroll", "utilities")), None
    if m == "tax_utility_ebitda":
        return ratio(book.line("taxes", "utilities"), book.ebitda), None
    if m == "personnel_total":
        return book.line("payroll"), None
    if m == "unrestricted_transfers":
        return ratio(book.transfers(), book.capex), None
    if m == "insurance_cover":
        return ratio(book.line("insurance"), book.line("rent", "utilities")), None
    if m == "revenue_less_max_overhead":
        return book.revenue - max(book.line("payroll"), book.line("taxes")), None
    return None, None


def decide(cov: Covenant, value: Decimal | None) -> str:
    if value is None:
        DIAG.note("value.none", f"{cov.scenario_id}/{cov.clause} ({cov.metric})")
        return "COMPLIANT"
    if cov.direction == "max":
        return "BREACH" if value > cov.threshold else "COMPLIANT"
    return "BREACH" if value < cov.threshold else "COMPLIANT"


def evaluate(store: Store, cov: Covenant) -> Verdict:
    names, _, _ = related_parties(store, cov.scenario_id)
    overlay = read_notes(store, cov.scenario_id)
    txns = apply(overlay, store.txns(scenario_id=cov.scenario_id))
    adj = collect(store, cov.scenario_id)
    free = unrestricted_subsidiaries(store, cov.scenario_id)

    def build(rows: list[Txn]) -> Book:
        book = Book(rows, names, cov.year, adj, overlay.disclosed,
                    overlay.restated, overlay.converted)
        book.unrestricted = free
        return book

    book = build(txns)
    if cov.trigger is not None and book.financing() <= cov.trigger:
        value, _ = compute(cov, book)
        return Verdict(cov.scenario_id, cov.clause, "COMPLIANT", value,
                       note="springing-условие не сработало")

    value, _ = compute(cov, book)
    status = decide(cov, value)
    touched = set(overlay.restated) | set(overlay.converted) | set(overlay.amounts)
    evidence = decisive(cov, build, txns, touched) if status == "BREACH" else None
    return Verdict(cov.scenario_id, cov.clause, status, value, evidence)


def decisive(cov: Covenant, build, txns: list[Txn], touched: set[str]) -> str | None:
    flips = []
    for dropped in txns:
        if dropped.amount == 0:
            continue
        trial = build([t for t in txns if t.txn_id != dropped.txn_id])
        value, _ = compute(cov, trial)
        if value is None:
            continue
        if decide(cov, value) != "BREACH":
            flips.append(dropped.txn_id)

    if len(flips) == 1:
        return flips[0]
    adjusted = [txn_id for txn_id in flips if txn_id in touched]
    return adjusted[0] if len(adjusted) == 1 else None
