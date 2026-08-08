from solution.covenants import period_year


def test_iso_dates():
    assert period_year("за период с 2025-01-01 по 2025-12-31.") == "2025"


def test_dotted_dates():
    assert period_year("за период с 01.01.2025 по 31.12.2025.") == "2025"


def test_slashed_dates():
    assert period_year("за период с 01/01/2025 по 31/12/2025.") == "2025"


def test_no_period_returns_empty():
    assert period_year("без указания периода") == ""
