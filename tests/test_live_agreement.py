from solution.link import needs_arbitration


def test_arbitration_needed_when_two_look_live():
    assert needs_arbitration(live_count=2)


def test_arbitration_needed_when_none_look_live():
    assert needs_arbitration(live_count=0)


def test_no_arbitration_when_exactly_one_live():
    assert not needs_arbitration(live_count=1)
