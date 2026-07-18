from app.models import RentTrackStatus
from app.routers.rent_tracker import _normalize_status, _parse_month_matrix_sheet


def test_operational_status_phrases_are_classified_safely():
    assert _normalize_status("Not Paid") == RentTrackStatus.DUE
    assert _normalize_status("71 Days on arrears") == RentTrackStatus.DUE
    assert _normalize_status("Awaiting clearence") == RentTrackStatus.AWAITING_CLEARANCE
    assert _normalize_status("No tenant") == RentTrackStatus.VACANT
    assert _normalize_status("Paid 01/07") == RentTrackStatus.PAID


def test_unnamed_property_month_matrix_is_detected():
    rows = [
        (1, {"A": "Property Address", "B": "Paid"}),
        (2, {"B": "January", "C": "February", "D": "March"}),
        (3, {"A": "1 Example St", "B": "Paid 01/01", "C": "Not Paid", "D": "No tenant"}),
    ]

    items = _parse_month_matrix_sheet("Sheet1", rows)

    assert len(items) == 3
    assert [item["status"] for item in items] == [
        RentTrackStatus.PAID,
        RentTrackStatus.DUE,
        RentTrackStatus.VACANT,
    ]
    assert all(item["property_address"] == "1 Example St" for item in items)
