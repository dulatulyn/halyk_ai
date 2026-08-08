from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from solution.store import Store

MONEY = re.compile(r"\$\s?([\d][\d,\s]*\.\d{2})")
ONEOFF_HEAD = re.compile(r"разов\w+\s+стать\w+", re.I)
MATERIALITY = re.compile(
    r"(?:разовыми|разовой)[^.]{0,120}?не\s+менее\s+\$\s?([\d][\d,\s]*\.\d{2})", re.I)
ACCRUED = re.compile(
    r"начислен\w*,?\s+но\s+не\s+(?:уплачен|выплачен|погашен)\w*[^.]{0,200}", re.I)
GROUP_CAPEX = re.compile(
    r"(?:совокупн\w+\s+)?капитальн\w+\s+затрат\w*\s+Групп\w+[^.]{0,220}", re.I)
RECLASS = re.compile(r"переклассифиц\w+[^.]{0,260}", re.I)
CONSOLIDATED = re.compile(r"consolidated|консолидирован", re.I)
NBV_OPEN = re.compile(rf"beginning of the year\s+{MONEY.pattern}", re.I)
NBV_CLOSE = re.compile(rf"end of the year\s+{MONEY.pattern}", re.I)
DEPRECIATION = re.compile(rf"depreciation charge for the year\s+{MONEY.pattern}", re.I)


def money(text: str) -> list[Decimal]:
    return [Decimal(m.replace(",", "").replace(" ", "")) for m in MONEY.findall(text)]


@dataclass(slots=True)
class Adjustments:
    scenario_id: str
    one_offs: list[Decimal] = field(default_factory=list)
    materiality: Decimal | None = None
    accrued: dict[str, Decimal] = field(default_factory=dict)
    group_capex: Decimal | None = None

    @property
    def add_backs(self) -> Decimal:
        floor = self.materiality if self.materiality is not None else Decimal(0)
        return sum((x for x in self.one_offs if x >= floor), Decimal(0))

    @property
    def one_off_total(self) -> Decimal:
        return sum(self.one_offs, Decimal(0))


def _accrual_kind(sentence: str) -> str | None:
    if re.search(r"налог", sentence, re.I):
        return "taxes"
    if re.search(r"персонал|заработн|оплат\w+\s+труда|выплат\w+\s+работник", sentence, re.I):
        return "payroll"
    return None


def collect(store: Store, scenario_id: str) -> Adjustments:
    out = Adjustments(scenario_id)
    for doc in store.docs(scenario_id=scenario_id):
        if doc.superseded:
            continue
        text = doc.text

        head = ONEOFF_HEAD.search(text)
        rule = MATERIALITY.search(text)
        if head and not out.one_offs:
            stop = rule.start() if rule and rule.start() > head.end() else len(text)
            found = money(text[head.end():stop])
            if found:
                out.one_offs = found
        if rule and out.materiality is None:
            out.materiality = money(rule.group(0))[0]

        for m in ACCRUED.finditer(text):
            kind = _accrual_kind(m.group(0))
            amounts = money(m.group(0))
            if kind and amounts and kind not in out.accrued:
                out.accrued[kind] = amounts[0]

        if out.group_capex is None and CONSOLIDATED.search(text):
            out.group_capex = _additions(text)

    return out


def _additions(text: str) -> Decimal | None:
    opening = NBV_OPEN.search(text)
    closing = NBV_CLOSE.search(text)
    charge = DEPRECIATION.search(text)
    if not (opening and closing and charge):
        return None
    start, end = money(opening.group(0))[0], money(closing.group(0))[0]
    return end - start + money(charge.group(0))[0]
