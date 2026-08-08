from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from decimal import Decimal

from solution.diag import DIAG
from solution.store import Store, Txn

MONEY = r"\$\s?([\d][\d,\s]*\.\d{2})"
TXN = r"(TXN-[A-Za-z0-9]+-\d+)"

MISSING = re.compile(
    rf"Операция\s+{TXN}[^.]{{0,140}}?не\s+отражена\s+в\s+выгрузке[^.]{{0,140}}?"
    rf"составляет\s+{MONEY}\s*\((расход|поступление)\)", re.I)
EXCLUDED = re.compile(
    rf"Операция\s+{TXN}[^.]{{0,200}}?исключена\s+из\s+ковенантного\s+периода", re.I)
TARGET = r"переклассифицирована[^.]{0,60}?\bкак\s+([^.]{3,60})\."
RECLASS_TXN = re.compile(
    rf"Операция\s+{TXN},\s+первоначально\s+учтённая\s+как\s+[^(]{{0,80}}"
    rf"\({MONEY}\),\s+{TARGET}", re.I)
RECLASS_SUM = re.compile(
    rf"Сумма\s+в\s+размере\s+{MONEY},\s+выплаченная\s+контрагенту\s+([^,]{{3,70}}),"
    rf"[^.]{{0,120}}{TARGET}", re.I)
FX = re.compile(
    rf"([\d][\d,\s]*\.\d{{2}})\s*([A-Z]{{3}})[^.]{{0,120}}?{MONEY}", re.I)
LIABILITY = re.compile(
    rf"обязательство\s+по\s+([^$]{{0,90}}?)\s+в\s+размере\s+{MONEY}\s+"
    rf"раскрывается\s+и\s+не\s+отражается", re.I)

CATEGORY_WORDS = [
    (re.compile(r"процентн\w+\s+расход", re.I), "interest"),
    (re.compile(r"страхов\w+\s+преми", re.I), "insurance"),
    (re.compile(r"капитальн\w+\s+затрат", re.I), "capex"),
    (re.compile(r"выходн\w+\s+пособи|оплат\w+\s+труда|персонал", re.I), "payroll"),
    (re.compile(r"коммунальн", re.I), "utilities"),
    (re.compile(r"аренд", re.I), "rent"),
    (re.compile(r"налог", re.I), "taxes"),
    (re.compile(r"операционн\w+\s+расход", re.I), "other"),
]


def amount(raw: str) -> Decimal:
    return Decimal(raw.replace(",", "").replace(" ", ""))


def category_of(phrase: str) -> str | None:
    for rx, name in CATEGORY_WORDS:
        if rx.search(phrase):
            return name
    return None


@dataclass(slots=True)
class Overlay:
    amounts: dict[str, Decimal] = field(default_factory=dict)
    excluded: set[str] = field(default_factory=set)
    recategorized: dict = field(default_factory=dict)
    restated: set[str] = field(default_factory=set)
    disclosed: list[tuple[str, Decimal]] = field(default_factory=list)
    rates: dict = field(default_factory=dict)
    converted: set = field(default_factory=set)


CATEGORIES = {"interest", "insurance", "capex", "payroll", "utilities", "rent",
              "taxes", "other"}

MODIFIER_KINDS = ("audit", "treasury", "compliance")
TXN_REF = re.compile(r"TXN-[A-Za-z0-9]+-\d+")


def merge_patch(overlay: Overlay, patch: dict) -> None:
    for item in patch.get("recategorized") or []:
        txn_id, category = item.get("txn_id"), item.get("category")
        if txn_id and category in CATEGORIES:
            overlay.recategorized.setdefault(txn_id, category)

    for txn_id in patch.get("excluded") or []:
        overlay.excluded.add(txn_id)

    for item in patch.get("amounts") or []:
        txn_id, value = item.get("txn_id"), item.get("amount")
        if not txn_id or value is None:
            continue
        money = Decimal(str(value)).copy_abs()
        overlay.amounts.setdefault(
            txn_id, -money if item.get("kind", "").lower() == "расход" else money)


def read_notes(store: Store, scenario_id: str) -> Overlay:
    overlay = Overlay()
    for doc in store.docs(scenario_id=scenario_id):
        if doc.superseded:
            continue
        text = doc.text
        before = len(overlay.amounts) + len(overlay.excluded) + len(overlay.recategorized)

        for m in MISSING.finditer(text):
            value = amount(m.group(2))
            overlay.amounts[m.group(1)] = -value if m.group(3).lower() == "расход" else value
            DIAG.bump("modifier.missing")

        for m in EXCLUDED.finditer(text):
            overlay.excluded.add(m.group(1))
            DIAG.bump("modifier.excluded")

        for m in RECLASS_TXN.finditer(text):
            target = category_of(m.group(3))
            if target:
                overlay.recategorized[m.group(1)] = target
                DIAG.bump("modifier.reclass_txn")

        for m in RECLASS_SUM.finditer(text):
            target = category_of(m.group(3))
            if target:
                overlay.recategorized.setdefault(("party", m.group(2).strip()), target)
                DIAG.bump("modifier.reclass_sum")

        for m in FX.finditer(text):
            code = m.group(2).upper()
            if code == "USD" or code in overlay.rates:
                continue
            foreign, usd = amount(m.group(1)), amount(m.group(3))
            if foreign > 0:
                overlay.rates[code] = usd / foreign
                DIAG.bump("modifier.fx")

        for m in LIABILITY.finditer(text):
            target = category_of(m.group(1))
            if target:
                overlay.disclosed.append((target, amount(m.group(2))))
                DIAG.bump("modifier.liability")

        after = len(overlay.amounts) + len(overlay.excluded) + len(overlay.recategorized)
        if after == before and doc.kind in MODIFIER_KINDS and TXN_REF.search(text):
            from solution import llm

            patch = llm.read_modifiers(text)
            if patch is None:
                DIAG.note("modifier.llm_failed", f"{scenario_id}/{doc.doc_id}")
            else:
                merge_patch(overlay, patch)
                DIAG.note("modifier.llm_applied", f"{scenario_id}/{doc.doc_id}")

    return overlay


def apply(overlay: Overlay, txns: list[Txn]) -> list[Txn]:
    out: list[Txn] = []
    for t in txns:
        if t.txn_id in overlay.excluded:
            continue
        changed = {}
        rate = overlay.rates.get(t.currency)
        if rate is not None:
            changed["amount"] = (t.amount * rate).quantize(Decimal("0.01"))
            changed["currency"] = "USD"
            overlay.converted.add(t.txn_id)
        if t.txn_id in overlay.amounts:
            changed["amount"] = overlay.amounts[t.txn_id]
        target = overlay.recategorized.get(t.txn_id)
        if target is None:
            target = overlay.recategorized.get(("party", t.counterparty))
        if target is not None:
            changed["category"] = target
            overlay.restated.add(t.txn_id)
        out.append(replace(t, **changed) if changed else t)
    return out
