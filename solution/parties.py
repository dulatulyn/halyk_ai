from __future__ import annotations

import re
from decimal import Decimal

from solution.categorize import normalize_party
from solution.store import Store

_TABLE_START = re.compile(r"Доля\s+голосующих\s+прав")
_TABLE_END = re.compile(r"Организации,\s+в\s+которых|Идентификация\s+и\s+проверка")
_SHARE = re.compile(r"(\d{1,3}(?:[.,]\d+)?)\s*%")
_THRESHOLD = re.compile(r"владеет\s+(\d{1,3}(?:[.,]\d+)?)\s*%\s+и\s+более")
_DEFAULT_THRESHOLD = Decimal("20")


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _holdings(segment: str) -> dict[str, Decimal]:
    rows: dict[str, Decimal] = {}
    cursor = 0
    for m in _SHARE.finditer(segment):
        name = segment[cursor:m.start()].strip(" .,;\"«»'")
        cursor = m.end()
        if not name or len(name) < 3:
            continue
        rows[name] = Decimal(m.group(1).replace(",", "."))
    return rows


def related_parties(store: Store, scenario_id: str) -> tuple[set[str], Decimal, dict]:
    best: tuple[set[str], Decimal, dict] | None = None

    for doc in store.docs(scenario_id=scenario_id):
        flat = _flat(doc.text)
        start = _TABLE_START.search(flat)
        if not start:
            continue
        tail = flat[start.end():]
        end = _TABLE_END.search(tail)
        segment = tail[: end.start()] if end else tail[:600]

        rows = _holdings(segment)
        if not rows:
            continue

        m = _THRESHOLD.search(tail)
        threshold = Decimal(m.group(1).replace(",", ".")) if m else _DEFAULT_THRESHOLD
        names = {normalize_party(n) for n, share in rows.items() if share >= threshold}
        candidate = (names, threshold, {n: str(s) for n, s in rows.items()})
        if best is None or len(rows) > len(best[2]):
            best = candidate

    if best and best[0]:
        return best

    # Досье оформлено не таблицей долей, а записями. Табличный разбор их не
    # видел, и у девяти заёмщиков из двадцати семи связанных сторон выходило ноль.
    narrative: set[str] = set()
    for doc in store.docs(scenario_id=scenario_id):
        if not doc.superseded:
            narrative |= narrative_parties(doc.text)
    if narrative:
        return narrative, _DEFAULT_THRESHOLD, {n: "запись досье" for n in narrative}

    return best or (set(), _DEFAULT_THRESHOLD, {})


_RECORD = re.compile(
    r"Контрагент\s*[«\"]([^»\"]{3,70})[»\"](.{0,320}?)"
    r"призна\w+\s+Ограниченными\s+платежами", re.I | re.S)


def narrative_parties(text: str) -> set[str]:
    """Связанные стороны из повествовательных записей досье.

    Формулировка: «Контрагент «X» классифицирован как АФФИЛИРОВАННОЕ ЛИЦО …
    Платежи данному контрагенту признаются Ограниченными платежами». Операнд —
    именно последняя фраза: она прямо говорит, что платежи считаются
    ограниченными для целей ковенантов.
    """
    out: set[str] = set()
    for m in _RECORD.finditer(_flat(text)):
        if re.search(r"\bне\s*$", m.group(2), re.I):
            continue
        out.add(normalize_party(m.group(1)))
    return out


_PLEDGE_START = re.compile(r"Доля\s+активов\s+в\s+залоге")
_PLEDGE_END = re.compile(r"Идентификация\s+и\s+проверка|Организации,\s+в\s+которых")
_PLEDGE_RULE = re.compile(r"доля\s+активов\s+в\s+залоге\s+ниже\s+(\d{1,3}(?:[.,]\d+)?)\s*%")


def unrestricted_subsidiaries(store: Store, scenario_id: str) -> set[str]:
    from solution.categorize import normalize_party

    for doc in store.docs(scenario_id=scenario_id):
        if doc.superseded:
            continue
        start = _PLEDGE_START.search(doc.text)
        if not start:
            continue
        end = _PLEDGE_END.search(doc.text, start.end())
        segment = doc.text[start.end(): end.start() if end else len(doc.text)]
        rule = _PLEDGE_RULE.search(segment) or _PLEDGE_RULE.search(doc.text)
        if not rule:
            continue
        floor = Decimal(rule.group(1).replace(",", "."))
        pledged = _holdings(segment)
        return {normalize_party(name) for name, share in pledged.items() if share < floor}
    return set()
