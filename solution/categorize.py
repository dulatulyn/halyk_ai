from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal

from solution.diag import DIAG
from solution.store import Store, Txn

REVENUE = "revenue"
CAPEX = "capex"
PAYROLL = "payroll"
UTILITIES = "utilities"
TAXES = "taxes"
RENT = "rent"
INSURANCE = "insurance"
INTEREST = "interest"
FINANCING_IN = "financing_in"
MARKETING = "marketing"
TELECOM = "telecom"
OTHER = "other"

OPEX_MEMBERS = (PAYROLL, UTILITIES, TAXES, RENT, INSURANCE, MARKETING, TELECOM, OTHER)

_REFUND = re.compile(
    r"refund|rebate|credit|returned|return\b|recovered|sweep back|released|"
    r"reversal|reimburse|adjustment credit|write-back",
    re.I,
)

_PATTERNS: list[tuple[str, re.Pattern]] = [
    # «sales settlement» — формулировка открытого набора. В приватном выручка
    # записана как «Mobile service revenue settlement», и без слова revenue эти
    # строки утекали в финансирование: выручка 0, EBITDA отрицательная.
    (REVENUE, re.compile(
        r"sales settlement|\brevenue\b|sales proceeds|turnover|"
        r"customer (?:receipt|collection|payment)|subscription (?:revenue|income)", re.I)),
    (INTEREST, re.compile(r"\binterest\b|coupon|subordinated notes", re.I)),
    (CAPEX, re.compile(
        r"purchase of .*(equipment|plant|machin|vehicle|rig|line|system)|"
        r"capital expenditure|construction of|acquisition of .*asset", re.I)),
    (PAYROLL, re.compile(
        r"payroll|salar|wage|staff cost|personnel|bonus|severance|"
        r"social contribution|pension", re.I)),
    (UTILITIES, re.compile(
        r"electric|water|gas supply|heating|utility|utilities|power supply|"
        r"network capacity charge", re.I)),
    (TAXES, re.compile(r"\btax\b|taxes|vat\b|duty|levy|customs", re.I)),
    (RENT, re.compile(r"\brent\b|rental|lease|tenancy|dormitory", re.I)),
    (INSURANCE, re.compile(r"insurance|premium|assurance|indemnity cover", re.I)),
    (TELECOM, re.compile(r"telecom|antenna|bandwidth|connectivity|data link", re.I)),
    (MARKETING, re.compile(
        r"marketing|advertis|media buy|sponsorship|campaign|branding|livery", re.I)),
    (FINANCING_IN, re.compile(
        r"drawdown|loan proceeds|facility draw|financing received|"
        r"bond issue|note issue|capital injection|shareholder loan", re.I)),
]


def categorize_one(txn: Txn) -> str:
    text = txn.description
    for name, rx in _PATTERNS:
        if rx.search(text):
            if name == REVENUE:
                return REVENUE
            if txn.amount > 0 and name not in (FINANCING_IN,):
                return OTHER
            return name
    if txn.amount > 0:
        return FINANCING_IN if not _REFUND.search(text) else OTHER
    return OTHER


def categorize(store: Store) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for txn in store.txns():
        cat = categorize_one(txn)
        store.set_category(txn.txn_id, cat)
        counts[cat] += 1
    store.commit()
    for name, n in counts.items():
        DIAG.bump(f"txn.{name}", n)
    return dict(counts)


def totals(txns: list[Txn], related: set[str]) -> dict[str, Decimal]:
    out: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    for t in txns:
        cat = t.category or categorize_one(t)
        if cat == REVENUE:
            out[REVENUE] += t.abs_amount
        elif t.amount < 0:
            out[cat] += t.abs_amount
            if cat in OPEX_MEMBERS:
                out["opex"] += t.abs_amount
        elif cat == FINANCING_IN:
            out[FINANCING_IN] += t.abs_amount
        if t.amount < 0 and is_related(t, related):
            out["related_party"] += t.abs_amount
    return out


def is_related(txn: Txn, related: set[str]) -> bool:
    return normalize_party(txn.counterparty) in related


def normalize_party(name: str) -> str:
    cleaned = re.sub(r"\s*\(.*?\)\s*", " ", name)
    cleaned = re.sub(r"\b(L\.?\s?L\.?\s?P\.?|LLC|JSC|Ltd\.?)\b", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"[^\w ]", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip().lower()
