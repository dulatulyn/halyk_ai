from solution.diag import DIAG


def test_diag_collects_counters_and_events():
    DIAG.reset()
    DIAG.bump("clauses.parsed", 3)
    DIAG.bump("clauses.parsed")
    DIAG.note("metric.unknown", "P4/6.3")

    assert DIAG.counters["clauses.parsed"] == 4
    assert ("metric.unknown", "P4/6.3") in DIAG.events
    text = DIAG.render()
    assert "clauses.parsed" in text
    assert "P4/6.3" in text


def test_diag_reset_clears_state():
    DIAG.bump("x")
    DIAG.reset()
    assert not DIAG.counters
    assert not DIAG.events
