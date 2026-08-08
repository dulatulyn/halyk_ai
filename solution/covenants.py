from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from solution import llm
from solution.diag import DIAG
from solution.link import covenants_text, live_agreement
from solution.store import Store

MONEY = re.compile(r"\$\s?([\d][\d,\s]*\.\d{2})")
RATIO = re.compile(r"(\d+(?:\.\d+)?)\s?x\b")
CLAUSE_HEAD = r"(?:Пункт|Статья|Раздел|п\.|ст\.)\s*"


def clause_pattern(clauses: list[str] | None, capture: bool = True) -> re.Pattern:
    body = "|".join(re.escape(c) for c in sorted(clauses, key=len, reverse=True)) \
        if clauses else r"\d+\.\d+"
    group = f"({body})" if capture else f"(?:{body})"
    return re.compile(rf"{CLAUSE_HEAD}{group}")
DATE = r"(?:\d{4}-\d\d-\d\d|\d\d\.\d\d\.\d{4}|\d\d/\d\d/\d{4})"
PERIOD = re.compile(rf"с\s+({DATE})\s+по\s+({DATE})")


def period_year(text: str) -> str:
    m = PERIOD.search(text)
    if not m:
        return ""
    year = re.search(r"\d{4}", m.group(2))
    return year.group(0) if year else ""

MIN_WORDS = re.compile(
    r"не\s+менее|не\s+ниже|не\s+допуска\w+\s+снижения|минимальн|составля\w+\s+не\s+менее|"
    r"обеспечи\w+.{0,40}не\s+менее|minimum|не\s+ниже\s+величины",
    re.I,
)
MAX_WORDS = re.compile(
    r"не\s+превыша\w+|не\s+более|максимальн|не\s+вправе\s+допускать|"
    r"не\s+должны\s+превышать|maximum|свыше|ceiling",
    re.I,
)

SPRING = re.compile(
    r"применяется\s+к\s+Заёмщику[^.]{0,80}только\s+при\s+условии,\s+что[^.]*?"
    r"превыша\w+\s+\$\s?([\d][\d,\s]*\.\d{2})",
    re.I,
)


@dataclass(frozen=True, slots=True)
class Covenant:
    scenario_id: str
    clause: str
    text: str
    metric: str
    threshold: Decimal
    direction: str
    trigger: Decimal | None = None
    doc_id: str = ""
    year: str = ""
    spec: object | None = None

    @property
    def title(self) -> str:
        body = self.text.split(".", 1)[0]
        return body[:90]


RULES: list[tuple[str, re.Pattern]] = [
    ("interest_cover", re.compile(r"покрыти\w+\s+процентов", re.I)),
    ("overhead_line_max", re.compile(r"Individual Overhead Line|отдельн\w+\s+стат\w+\s+накладн", re.I)),
    ("related_over_opex", re.compile(r"доля\s+платежей\s+связанным\s+сторонам\s+в\s+операционн", re.I)),
    ("related_over_revenue", re.compile(r"Proportion of Revenue|от\s+выручки\s+за\s+тот\s+же", re.I)),
    ("revenue_q4", re.compile(r"четвёрт\w+\s+(финансов\w+\s+)?квартал", re.I)),
    ("capital_intensity", re.compile(r"Capital Intensity|капиталоёмкост", re.I)),
    ("sources_cover", re.compile(r"Cover of Applications by Sources|поступлени\w+\s+по\s+финансированию\s+Заёмщика", re.I)),
    ("springing_leverage", re.compile(r"Springing|только\s+при\s+условии,\s+что", re.I)),
    ("adjusted_margin", re.compile(r"рентабельност\w+\s+по\s+EBITDA|Скорректированн\w+\s+EBITDA", re.I)),
    ("group_capex_ebitda", re.compile(r"капитальн\w+\s+затрат\s+Группы", re.I)),
    ("revenue_cover_costs", re.compile(r"покрытие\s+расходов\s+на\s+персонал\s+и\s+коммунальн", re.I)),
    ("tax_utility_ebitda", re.compile(r"налогов\w+\s+и\s+коммунальн\w+\s+нагрузки", re.I)),
    ("personnel_total", re.compile(r"обязательств\w+\s+по\s+персоналу", re.I)),
    ("unrestricted_transfers", re.compile(r"неограниченн\w+\s+дочерн", re.I)),
    ("insurance_cover", re.compile(r"страхов\w+\s+покрыти\w+\s+расходов", re.I)),
    ("revenue_less_max_overhead", re.compile(r"выручк\w+\s+за\s+вычетом\s+наибольш", re.I)),
    ("related_absolute", re.compile(r"платеж\w+\s+связанным\s+сторонам|Ограниченн\w+\s+платеж", re.I)),
    ("capex_absolute", re.compile(r"Капитальные\s+затраты|расход\w+\s+по\s+категории", re.I)),
    ("revenue_absolute", re.compile(r"выручк", re.I)),
]


RATIO_METRICS = {
    "interest_cover", "related_over_revenue", "related_over_opex",
    "capital_intensity", "sources_cover", "springing_leverage",
    "adjusted_margin", "group_capex_ebitda", "revenue_cover_costs",
    "tax_utility_ebitda", "unrestricted_transfers", "insurance_cover",
}


def _without_trigger(text: str) -> str:
    m = SPRING.search(text)
    return text[: m.start()] + text[m.end():] if m else text


def metric_inconsistent(metric: str, text: str) -> bool:
    if metric == "unknown" or metric in RATIO_METRICS:
        return False
    body = _without_trigger(text)
    return bool(RATIO.search(body)) and not MONEY.search(body)


def ratio_expected(text: str) -> bool:
    return bool(RATIO.search(text))


def pick_metric(text: str) -> str:
    for name, rx in RULES:
        if rx.search(text):
            return name
    return "unknown"


def pick_direction(text: str) -> str:
    first = text[:400]
    has_min, has_max = MIN_WORDS.search(first), MAX_WORDS.search(first)
    if has_min and has_max:
        return "min" if has_min.start() < has_max.start() else "max"
    if has_min:
        return "min"
    return "max"


def pick_threshold(text: str, metric: str) -> tuple[Decimal | None, Decimal | None]:
    trigger = None
    body = text
    if m := SPRING.search(text):
        trigger = Decimal(m.group(1).replace(",", "").replace(" ", ""))
        body = text[: m.start()] + text[m.end():]

    ratios = RATIO.findall(body)
    monies = [Decimal(x.replace(",", "").replace(" ", "")) for x in MONEY.findall(body)]

    ratio_metrics = {
        "interest_cover", "related_over_revenue", "related_over_opex",
        "capital_intensity", "sources_cover", "springing_leverage",
        "adjusted_margin", "group_capex_ebitda", "revenue_cover_costs",
        "tax_utility_ebitda", "unrestricted_transfers", "insurance_cover",
    }
    if metric in ratio_metrics and ratios:
        return Decimal(ratios[0]), trigger
    if monies:
        return monies[0], trigger
    if ratios:
        return Decimal(ratios[0]), trigger
    return None, trigger


def parse_scenario(store: Store, scenario_id: str,
                   clauses: list[str] | None = None) -> list[Covenant]:
    doc = live_agreement(store, scenario_id)
    body = covenants_text(doc)
    rx = clause_pattern(clauses)
    out: list[Covenant] = []

    chunks = re.split(rf"(?={clause_pattern(clauses, capture=False).pattern})", body)
    for chunk in chunks:
        text = chunk.strip()
        m = rx.match(text)
        if not m:
            continue
        clause = m.group(1)
        metric = pick_metric(text)
        threshold, trigger = pick_threshold(text, metric)
        if metric_inconsistent(metric, text):
            DIAG.note("metric.inconsistent", f"{scenario_id}/{clause}: {metric}")
            metric = "unknown"
        spec = None
        if metric == "unknown" or threshold is None:
            DIAG.note("metric.unknown", f"{scenario_id}/{clause}")
            spec = llm.read_covenant(text)
            if spec is not None and ratio_expected(text) and not spec.denominator:
                DIAG.note("llm.denominator_missing", f"{scenario_id}/{clause}")
                spec = llm.read_covenant(text, expect_ratio=True) or spec
            if spec is None:
                DIAG.note("clause.dropped", f"{scenario_id}/{clause}")
                continue
            DIAG.bump("llm.covenant")
            threshold = llm.as_decimal(spec.threshold)
            trigger = llm.as_decimal(spec.trigger)
        out.append(
            Covenant(
                scenario_id=scenario_id,
                clause=clause,
                text=text,
                metric=metric,
                threshold=threshold,
                direction=(spec.direction if spec else pick_direction(text)),
                trigger=trigger,
                doc_id=doc.doc_id,
                spec=spec,
                year=period_year(text),
            )
        )
    return out


def parse_all(store: Store, scenarios: list[str]) -> dict[str, list[Covenant]]:
    return {sc: parse_scenario(store, sc) for sc in scenarios}
