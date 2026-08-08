from tools.mutate import MUTATIONS

SAMPLE = (
    "Пункт 6.1. Покрытие процентов за период с 2025-01-01 по 2025-12-31.\n"
    "НЕДЕЙСТВУЮЩАЯ РЕДАКЦИЯ — НЕ ПРИМЕНЯЕТСЯ.\n"
    "Операция TXN-P1-0001 не отражена в выгрузке реестра."
)


def test_every_mutation_changes_the_sample():
    for name, fn in MUTATIONS.items():
        assert fn(SAMPLE) != SAMPLE, f"мутация {name} ничего не изменила"


def test_clause_word_mutation_keeps_the_number():
    out = MUTATIONS["clause_word"]("Пункт 6.1. Текст")
    assert "6.1" in out
    assert "Пункт" not in out


def test_period_format_mutation_keeps_the_dates():
    out = MUTATIONS["period_format"]("с 2025-01-01 по 2025-12-31")
    assert "01.01.2025" in out
    assert "31.12.2025" in out
