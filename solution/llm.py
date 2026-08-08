from __future__ import annotations

import hashlib
import json
import os
import pathlib
from decimal import Decimal
from typing import Literal, TypeVar

from pydantic import BaseModel, Field

from solution.diag import DIAG

T = TypeVar("T", bound=BaseModel)

PROVIDERS = ("anthropic", "openai", "gemini")
MODELS = {
    "anthropic": "claude-opus-5",
    "openai": "gpt-5",
    "gemini": "gemini-2.5-flash",
}
ENV_VARS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

MODEL = MODELS["anthropic"]
CACHE = pathlib.Path("data/llm_cache")

QUANTITIES = (
    "revenue", "revenue_q4", "opex", "ebitda", "adjusted_ebitda", "capex",
    "group_capex", "payroll", "utilities",
    "taxes", "rent", "insurance", "marketing", "telecom", "interest", "financing_in",
    "related_total", "transfers", "accrued_taxes", "accrued_payroll",
)


def _key(provider: str) -> str | None:
    name = ENV_VARS[provider]
    if os.environ.get(name):
        return os.environ[name]
    env = pathlib.Path(".env")
    if not env.exists():
        return None
    for line in env.read_text(encoding="utf-8").splitlines():
        head, _, value = line.partition("=")
        if head.strip() == name and value.strip():
            os.environ[name] = value.strip()
            return value.strip()
    return None


FAILURE_LIMIT = 3
_failures: dict[str, int] = {}
_used: set[str] = set()


def note_failure(provider: str) -> None:
    _failures[provider] = _failures.get(provider, 0) + 1


def note_success(provider: str) -> None:
    _failures[provider] = 0
    _used.add(MODELS[provider])


def reset_breaker() -> None:
    _failures.clear()
    _used.clear()


def breaker_open(provider: str) -> bool:
    return _failures.get(provider, 0) >= FAILURE_LIMIT


def live_providers() -> list[str]:
    return [p for p in PROVIDERS if _key(p) and not breaker_open(p)]


def available() -> bool:
    return bool(live_providers())


def used_models() -> list[str]:
    return sorted(_used)


def _cached(digest: str) -> dict | None:
    path = CACHE / f"{digest}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def _store(digest: str, payload: dict) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / f"{digest}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _call_anthropic(system: str, user: str, schema: type[T]) -> T | None:
    import anthropic

    response = anthropic.Anthropic(api_key=_key("anthropic")).messages.parse(
        model=MODELS["anthropic"], max_tokens=8000, system=system,
        messages=[{"role": "user", "content": user}], output_format=schema)
    return response.parsed_output


def _call_openai(system: str, user: str, schema: type[T]) -> T | None:
    from openai import OpenAI

    completion = OpenAI(api_key=_key("openai")).chat.completions.parse(
        model=MODELS["openai"],
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        response_format=schema)
    return completion.choices[0].message.parsed


def _call_gemini(system: str, user: str, schema: type[T]) -> T | None:
    from google import genai
    from google.genai import types

    response = genai.Client(api_key=_key("gemini")).models.generate_content(
        model=MODELS["gemini"], contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema))
    if getattr(response, "parsed", None) is not None:
        return response.parsed
    return schema(**json.loads(response.text))


_CALLS = {"anthropic": _call_anthropic, "openai": _call_openai, "gemini": _call_gemini}


def ask(system: str, user: str, schema: type[T]) -> T | None:
    digest = hashlib.sha256((system + user).encode("utf-8")).hexdigest()[:20]
    if (hit := _cached(digest)) is not None:
        try:
            return schema(**hit)
        except Exception:
            pass

    for provider in live_providers():
        try:
            parsed = _CALLS[provider](system, user, schema)
        except Exception as exc:
            note_failure(provider)
            DIAG.note(f"llm.{provider}.failed", f"{type(exc).__name__}: {str(exc)[:120]}")
            continue
        if parsed is None:
            note_failure(provider)
            continue
        note_success(provider)
        DIAG.bump(f"llm.{provider}.ok")
        _store(digest, parsed.model_dump())
        return parsed
    return None


SYSTEM = """Ты разбираешь пункт кредитного договора на казахстанском рынке и
превращаешь его в формулу. Отвечай только тем, что прямо написано в тексте.

Величина ковенанта = сумма(numerator) / сумма(denominator).
Если знаменателя нет — оставь denominator пустым, тогда величина = сумма(numerator).

Доступные величины: {names}.
Все они относятся к периоду ковенанта и к самому Заёмщику, кроме group_capex —
это консолидированные капзатраты Группы.

direction — "max", если текст запрещает превышать порог, и "min", если требует
не опускаться ниже. trigger — сумма из условия срабатывания springing-теста,
если такое условие есть; иначе null. Порог из springing-условия не является
порогом ковенанта.""".format(names=", ".join(QUANTITIES))


class Spec(BaseModel):
    numerator: list[str] = Field(description="величины в числителе")
    denominator: list[str] = Field(default_factory=list, description="величины в знаменателе")
    threshold: float = Field(description="порог из текста")
    direction: Literal["max", "min"]
    trigger: float | None = Field(default=None, description="порог срабатывания springing")


RATIO_HINT = """

Порог в этом пункте записан в форме «Nx». Это означает отношение: denominator
не может быть пустым. Определи, что стоит в знаменателе."""


def read_covenant(text: str, expect_ratio: bool = False) -> Spec | None:
    return ask(SYSTEM + (RATIO_HINT if expect_ratio else ""), text, Spec)


def as_decimal(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


MODIFIER_SYSTEM = """Ты читаешь записку к банковской выгрузке: аудиторское заключение,
казначейские данные или комплаенс-досье. Найди в ней правки к реестру операций.

Виды правок:
- операция переклассифицирована в другую категорию расходов;
- операция исключена из ковенантного периода;
- фактическая сумма операции отличается от той, что в выгрузке, либо операция в
  выгрузке отсутствует.

Категории: interest, insurance, capex, payroll, utilities, rent, taxes, other.
kind — "расход" или "поступление".

Бери только то, что прямо утверждается. Отклонённые и предложенные, но не принятые
правки не включай. Если правок нет — верни пустые списки."""


class Reclass(BaseModel):
    txn_id: str = Field(description="идентификатор операции")
    category: str = Field(description="категория, в которую переклассифицирована")


class AmountFix(BaseModel):
    txn_id: str = Field(description="идентификатор операции")
    amount: float = Field(description="фактическая сумма, положительное число")
    kind: str = Field(description="расход или поступление")


class ModifierPatch(BaseModel):
    recategorized: list[Reclass] = Field(default_factory=list)
    excluded: list[str] = Field(default_factory=list, description="id исключённых операций")
    amounts: list[AmountFix] = Field(default_factory=list)


def read_modifiers(text: str) -> dict | None:
    parsed = ask(MODIFIER_SYSTEM, text[:60000], ModifierPatch)
    return parsed.model_dump() if parsed else None


EDITION_SYSTEM = """Перед тобой шапки нескольких редакций одного кредитного договора.
Ровно одна из них действующая, остальные заменены или утратили силу.

Верни номер действующей редакции. Ориентируйся на смысл пометок о статусе
документа и на даты, а не на конкретные слова: формулировка может быть любой."""


class Edition(BaseModel):
    operative_index: int = Field(description="номер действующей редакции, отсчёт с нуля")
    reason: str = Field(description="краткое обоснование выбора")


def is_operative(headers: list[str]) -> int | None:
    if len(headers) < 2:
        return None
    joined = "\n\n---\n\n".join(f"[{i}]\n{h}" for i, h in enumerate(headers))
    parsed = ask(EDITION_SYSTEM, joined, Edition)
    if parsed is None or not 0 <= parsed.operative_index < len(headers):
        return None
    return parsed.operative_index
