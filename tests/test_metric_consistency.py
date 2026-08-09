from solution.covenants import metric_inconsistent


def test_absolute_metric_with_ratio_threshold_is_inconsistent():
    assert metric_inconsistent("revenue_absolute", "составляет не менее 2.00x от суммы")


def test_absolute_metric_with_money_threshold_is_fine():
    assert not metric_inconsistent("revenue_absolute", "не менее $1,200,000.00 за период")


def test_ratio_metric_with_ratio_threshold_is_fine():
    assert not metric_inconsistent("interest_cover", "не менее 2.00x")


def test_unknown_metric_is_never_flagged():
    assert not metric_inconsistent("unknown", "не менее 2.00x")


def test_springing_trigger_money_does_not_excuse_ratio_metric():
    text = ("применяется к Заёмщику только при условии, что поступления "
            "превышают $4,000,000.00, и составляет не более 1.70x")
    assert metric_inconsistent("revenue_absolute", text)


def test_ratio_metric_with_money_threshold_is_inconsistent():
    """Обратная сторона: правило дало отношение, а порог — сумма в долларах."""
    assert metric_inconsistent(
        "unrestricted_transfers", "на совокупную сумму, превышающую $250,000.00, в пользу")
