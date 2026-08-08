from solution.covenants import ratio_expected


def test_ratio_form_threshold_implies_denominator():
    assert ratio_expected("величина составляет не менее 1.50x за период")


def test_money_threshold_does_not_imply_denominator():
    assert not ratio_expected("не превышают $1,200,000.00 за период")


def test_plain_number_does_not_imply_denominator():
    assert not ratio_expected("не более 42 единиц")
