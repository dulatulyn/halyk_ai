from solution.covenants import clause_pattern


def test_pattern_built_from_template_clauses():
    rx = clause_pattern(["6.1", "7.2"])
    assert rx.search("Пункт 6.1. Текст")
    assert rx.search("Статья 7.2. Текст")
    assert not rx.search("Пункт 6.3. Текст")


def test_pattern_falls_back_to_any_number():
    rx = clause_pattern(None)
    assert rx.search("Пункт 6.1. Текст")
    assert rx.search("п. 12.4 Текст")


def test_longer_clause_numbers_win_over_prefixes():
    rx = clause_pattern(["6.1", "6.10"])
    assert rx.match("Пункт 6.10. Текст").group(1) == "6.10"


def test_split_pattern_has_no_capturing_group():
    assert clause_pattern(["6.1"], capture=False).groups == 0
