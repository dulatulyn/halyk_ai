from decimal import Decimal

from solution.modifiers import Overlay, merge_patch


def test_merge_adds_reclassification():
    overlay = Overlay()
    merge_patch(overlay, {"recategorized": [{"txn_id": "TXN-X-1", "category": "interest"}]})
    assert overlay.recategorized["TXN-X-1"] == "interest"


def test_merge_never_overrides_what_regex_already_found():
    overlay = Overlay(recategorized={"TXN-X-1": "capex"})
    merge_patch(overlay, {"recategorized": [{"txn_id": "TXN-X-1", "category": "interest"}]})
    assert overlay.recategorized["TXN-X-1"] == "capex"


def test_merge_ignores_unknown_category():
    overlay = Overlay()
    merge_patch(overlay, {"recategorized": [{"txn_id": "TXN-X-1", "category": "выдумка"}]})
    assert "TXN-X-1" not in overlay.recategorized


def test_merge_adds_exclusions_and_amounts():
    overlay = Overlay()
    merge_patch(overlay, {
        "excluded": ["TXN-X-2"],
        "amounts": [{"txn_id": "TXN-X-3", "amount": 100.5, "kind": "расход"}],
    })
    assert "TXN-X-2" in overlay.excluded
    assert overlay.amounts["TXN-X-3"] == Decimal("-100.5")


def test_merge_tolerates_empty_patch():
    overlay = Overlay()
    merge_patch(overlay, {})
    assert not overlay.recategorized and not overlay.excluded
