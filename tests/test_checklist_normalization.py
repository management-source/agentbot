from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

from app.routers.checklists import CHECKS, blank_payload, serialize, validate_payload


def test_legacy_seventh_check_is_removed_for_save_and_serialization():
    payload = blank_payload()
    for item in payload["checks"][:-1]:
        item["status"] = "Verified / Positive"
    payload["checks"][-1]["result"] = "Keep this supporting-document result."
    payload["checks"].insert(
        -1,
        {
            "name": "Rental " + "Led" + "ger Review",
            "status": "Verified / Positive",
            "checked_by": "Legacy Reviewer",
            "result": "Retired result",
            "notes": "Retired note",
        },
    )

    clean, progress = validate_payload(payload)
    assert [item["name"] for item in clean["checks"]] == CHECKS
    assert len(clean["checks"]) == 6
    assert progress == 83
    assert clean["checks"][-1]["result"] == "Keep this supporting-document result."

    timestamp = datetime(2026, 9, 1)
    row = SimpleNamespace(
        id=1,
        process_key="application_screening",
        template_version=1,
        status="IN_PROGRESS",
        title="Property Application Screening Checklist",
        applicant_name="Applicant",
        property_address="1 Example Street",
        application_received=timestamp,
        progress_percent=86,
        completed_at=None,
        created_at=timestamp,
        updated_at=timestamp,
        payload_json=json.dumps(payload),
    )
    serialized = serialize(row)
    assert serialized["progress_percent"] == 83
    assert serialized["payload"]["checks"] == clean["checks"]

