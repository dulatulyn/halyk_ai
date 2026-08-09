from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from solution.diag import DIAG
from solution.store import Store

__all__ = ["ACCOUNT_RE", "DocKind", "link_documents", "LinkReport", "account_map"]

ACCOUNT_RE = re.compile(r"\bACC-\d{3,}\b")

SUPERSEDED_RE = re.compile(
    r"НЕДЕЙСТВУЮЩАЯ\s+РЕДАКЦИЯ|НЕ\s+ПРИМЕНЯЕТСЯ|УТРАТИЛА\s+СИЛУ", re.IGNORECASE
)
SUPERSEDED_ANYWHERE_RE = re.compile(
    r"ЗАМЕНЕН\w*\s+ОКОНЧАТЕЛЬНЫМ\s+ОТЧЁТОМ|"
    r"ПРОМЕЖУТОЧН\w+\s+ВЕДОМОСТЬ\s+ВОПРОСОВ|"
    r"РАБОЧИЙ\s+ДОКУМЕНТ\s+[—–-]\s+ЗАМЕНЕН|SUPERSEDED|NOT\s+OPERATIVE|"
    r"PRIOR-YEAR\s+AGREEMENT",
    re.IGNORECASE)
HEADER_CHARS = 600
ARBITRATION_CHARS = 1500


class DocKind:
    AGREEMENT = "agreement"
    AUDIT = "audit"
    COMPLIANCE = "compliance"
    GROUP = "group"
    TREASURY = "treasury"
    OPS = "ops"
    OTHER = "other"


_RULES: list[tuple[str, re.Pattern]] = [
    (DocKind.AGREEMENT, re.compile(
        r"Статья\s+\d+\s*[—–-]\s*Финансовые\s+ковенанты|"
        r"Article\s+[IVXL]+\s*[—–-]?\s*Financial\s+Covenants|"
        r"CREDIT\s+AGREEMENT|ДОГОВОР\s+БАНКОВСКОГО\s+ЗАЙМА", re.I)),
    (DocKind.AUDIT, re.compile(
        r"независим\w+\s+аудитор|аудиторск\w+\s+заключени|реклассифик|переквалифик",
        re.IGNORECASE)),
    (DocKind.COMPLIANCE, re.compile(
        r"знай\s+своего\s+клиента|\bKYC\b|комплаенс|связанн\w+\s+стор|"
        r"аффилирован|идентификаци\w+\s+клиента", re.IGNORECASE)),
    (DocKind.GROUP, re.compile(
        r"структур\w+\s+группы|конечн\w+\s+материнск|дочерн\w+\s+организац|"
        r"участник\w+\s+группы|неограниченн\w+\s+дочерн", re.IGNORECASE)),
    (DocKind.TREASURY, re.compile(
        r"казначейств|начислен\w+,?\s+но\s+не\s+уплачен|"
        r"учётн\w+\s+данн\w+\s+казначейства", re.IGNORECASE)),
    (DocKind.OPS, re.compile(
        r"еженедельн\w+\s+обновлени|отчёт\s+о\s+статусе|операции\s+—", re.IGNORECASE)),
]


@dataclass(slots=True)
class LinkReport:
    linked: int = 0
    linked_by_name: int = 0
    unlinked: list[str] = field(default_factory=list)
    multi_account: list[tuple[str, list[str]]] = field(default_factory=list)
    kinds: Counter = field(default_factory=Counter)
    per_scenario: dict[str, int] = field(default_factory=dict)
    agreements: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)

    def line(self) -> str:
        return (
            f"привязано {self.linked} "
            f"(по счёту {self.linked - self.linked_by_name}, "
            f"по названию {self.linked_by_name}), "
            f"без привязки {len(self.unlinked)}, типы: {dict(self.kinds)}"
        )


def account_map(store: Store) -> dict[str, str]:
    seen: dict[str, set[str]] = defaultdict(set)
    for t in store.txns():
        seen[t.account_id].add(t.scenario_id)

    conflicts = {a: sorted(s) for a, s in seen.items() if len(s) > 1}
    if conflicts:
        raise ValueError(
            f"account_id указывает на несколько сценариев: {conflicts}. "
            "Связь документ↔заёмщик построена на другом ключе."
        )
    return {a: next(iter(s)) for a, s in seen.items()}


def classify(text: str) -> str:
    head = text[:4000]
    for kind, rx in _RULES:
        if rx.search(head) or rx.search(text[:20000]):
            return kind
    return DocKind.OTHER


_SUFFIX = r"(?:JSC|LLP|LLC|Ltd\.?|B\.V\.|GmbH|AG|SA)"
_BORROWER_RE = re.compile(rf"Заёмщик[а-я]*\s*[,(]?\s*([A-Z][A-Za-z&.\- ]{{3,60}}?\s{_SUFFIX})")
_COMPANY_RE = re.compile(rf"^([A-Z][A-Za-z&.\- ]{{3,60}}?\s{_SUFFIX})\b")


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


COVENANTS_RE = re.compile(
    r"(?:Статья\s+\d+\s*[—–-]\s*Финансовые\s+ковенанты"
    r"|Article\s+[IVXL]+\s*[—–-]?\s*Financial\s+Covenants)"
    r"(.*?)(?=Статья\s+\d+\s*[—–-]|Article\s+[IVXL]+\s*[—–-])", re.S | re.I
)
_ANY_COMPANY_RE = re.compile(rf"\b([A-Z][A-Za-z&.\- ]{{3,60}}?\s{_SUFFIX})\b")


def covenants_text(doc) -> str:
    m = COVENANTS_RE.search(_flat(doc.text))
    return m.group(1).strip() if m else ""


def borrower_names(store: Store) -> dict[str, str]:
    names: dict[str, str] = {}
    for doc in store.docs(kind=DocKind.AGREEMENT):
        if not doc.scenario_id or doc.superseded:
            continue
        body = covenants_text(doc)
        if not body:
            continue
        counts = Counter(m.group(1).strip() for m in _ANY_COMPANY_RE.finditer(body))
        if counts:
            names[counts.most_common(1)[0][0]] = doc.scenario_id
    return names


def owner_by_name(text: str, names: dict[str, str]) -> str | None:
    flat = _flat(text)
    hits = {
        names[n]
        for n in sorted(names, key=len, reverse=True)
        if re.search(rf"\b{re.escape(n)}", flat)
    }
    return next(iter(hits)) if len(hits) == 1 else None


def link_documents(store: Store) -> LinkReport:
    known_accounts = set(account_map(store))
    amap = account_map(store)
    known_scenarios = set(amap.values())
    rep = LinkReport()

    for doc in store.docs():
        accounts = sorted(a for a in known_accounts if a in doc.text)
        owners = sorted({amap[a] for a in accounts if a in amap})

        kind = classify(doc.text)
        header = doc.pages()[0][:HEADER_CHARS] if doc.n_pages else ""
        superseded = bool(SUPERSEDED_RE.search(header)
                          or SUPERSEDED_ANYWHERE_RE.search(doc.text))

        scenario = None
        if len(owners) == 1:
            scenario = owners[0]
            rep.linked += 1
        elif len(owners) > 1:
            rep.multi_account.append((doc.doc_id, owners))
        else:
            rep.unlinked.append(doc.doc_id)

        store.tag_doc(doc.doc_id, kind=kind, scenario_id=scenario, superseded=superseded)
        rep.kinds[kind] += 1

    names = borrower_names(store)
    rep.names = names
    still = list(rep.unlinked)
    rep.unlinked = []
    for doc_id in still:
        owner = owner_by_name(store.doc(doc_id).text, names)
        if owner:
            store.tag_doc(doc_id, scenario_id=owner)
            rep.linked += 1
            rep.linked_by_name += 1
        else:
            rep.unlinked.append(doc_id)

    for sc in sorted(known_scenarios):
        docs = store.docs(scenario_id=sc)
        if not docs:
            continue
        rep.per_scenario[sc] = len(docs)
        live = [d.doc_id for d in docs if d.kind == DocKind.AGREEMENT and not d.superseded]
        dead = [d.doc_id for d in docs if d.kind == DocKind.AGREEMENT and d.superseded]
        rep.agreements[sc] = {"live": live, "dead": dead}
        if len(live) != 1:
            DIAG.note("agreement.ambiguous", f"{sc}: живых {len(live)}, мёртвых {len(dead)}")

    DIAG.bump("docs.linked", rep.linked)
    DIAG.bump("docs.unlinked", len(rep.unlinked))
    DIAG.bump("scenarios.found", len(rep.per_scenario))
    return rep


def needs_arbitration(live_count: int) -> bool:
    return live_count != 1


def arbitrate(scenario_id: str, candidates: list):
    if len(candidates) < 2:
        return None
    from solution import llm

    headers = [(d.pages()[0] if d.n_pages else d.text)[:ARBITRATION_CHARS]
               for d in candidates]
    index = llm.is_operative(headers)
    if index is None:
        DIAG.note("agreement.arbitration_failed", scenario_id)
        return None
    DIAG.note("agreement.arbitrated", f"{scenario_id} → {candidates[index].doc_id}")
    return candidates[index]


def live_agreement(store: Store, scenario_id: str):
    agreements = store.docs(scenario_id=scenario_id, kind=DocKind.AGREEMENT)
    docs = [d for d in agreements if not d.superseded]
    if not needs_arbitration(len(docs)):
        return docs[0]

    chosen = arbitrate(scenario_id, docs if len(docs) > 1 else list(agreements))
    if chosen is not None:
        return chosen

    if not docs:
        raise LookupError(f"{scenario_id}: не найден действующий договор")
    raise LookupError(
        f"{scenario_id}: действующих договоров несколько: {[d.doc_id for d in docs]}"
    )
