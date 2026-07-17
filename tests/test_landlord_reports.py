from __future__ import annotations

import json
import os
from datetime import date, datetime
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image as PillowImage
from pypdf import PdfReader
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ["DEBUG"] = "false"

from app.authz import get_current_user, has_page_access
from app.db import get_db
from app.deps import get_current_mailbox
from app.models import (
    Base,
    ComplianceRecord,
    ComplianceRecordStatus,
    ComplianceType,
    LeaseRenewalRecord,
    LeaseRenewalStatus,
    MaintenanceAttachment,
    MaintenanceEvent,
    MaintenanceOrder,
    MaintenanceOrderStatus,
    ManagedProperty,
    RentDueTracker,
    RentTrackStatus,
    User,
    UserRole,
)
from app.services.landlord_report_pdf import generate_landlord_report_pdf
from app.routers.landlord_reports import router as landlord_reports_router
from app.services.landlord_reports import (
    ALL_SECTION_IDS,
    LandlordReportError,
    assemble_report,
    build_report_context,
    default_section_ids,
    format_currency_aud,
    format_date_au,
    load_photo_bytes,
    normalize_section_ids,
    render_preview_html,
    safe_report_filename,
)


MAILBOX = "admin@donspremier.com.au"
START = date(2026, 7, 1)
END = date(2026, 7, 31)


def _png_bytes(width: int = 900, height: int = 450) -> bytes:
    stream = BytesIO()
    PillowImage.new("RGB", (width, height), (199, 162, 70)).save(stream, format="PNG")
    return stream.getvalue()


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def seeded(db):
    user = User(
        email="manager@donspremier.com.au",
        name="Jessica Gale",
        role=UserRole.PM,
        is_active=True,
        password_hash="not-used-in-report-tests",
    )
    prop = ManagedProperty(
        mailbox=MAILBOX,
        property_address="8 Very Long Property Avenue",
        suburb="Hampton Park",
        state_code="VIC",
        postcode="3976",
        property_type="House",
        rental_type="Residential fixed term",
        tenancy_status="Tenanted",
        owners_json=json.dumps(
            {
                "contacts": [
                    {"name": "Alexandra Landlord", "email": "alex@example.com", "mobile": "0400000000"},
                    {"name": "Jordan Landlord", "email": "jordan@example.com"},
                ]
            }
        ),
        tenants_json=json.dumps({"contacts": [{"name": "Taylor Tenant", "email": "tenant@example.com"}]}),
        is_active=True,
    )
    db.add_all([user, prop])
    db.flush()

    order = MaintenanceOrder(
        mailbox=MAILBOX,
        property_id=prop.id,
        property_address=prop.property_address,
        suburb=prop.suburb,
        state_code="VIC",
        postcode=prop.postcode,
        title="Repair leaking kitchen tap",
        category="Plumbing",
        priority="urgent",
        description="The kitchen tap is leaking continuously.",
        access_notes="SECRET ACCESS NOTE: key is under the mat.",
        owner_name="Alexandra Landlord",
        tenant_name="Taylor Tenant",
        status=MaintenanceOrderStatus.WAITING_OWNER_APPROVAL,
        owner_sent_at=datetime(2026, 7, 7, 2, 30),
        quoted_amount=1250.0,
        quote_received_at=datetime(2026, 7, 8, 1, 0),
        tradie_company="Premier Plumbing",
        created_at=datetime(2026, 7, 5, 1, 30),
        updated_at=datetime(2026, 7, 8, 1, 0),
    )
    db.add(order)
    db.flush()
    db.add(
        MaintenanceEvent(
            mailbox=MAILBOX,
            order_id=order.id,
            actor_user_id=user.id,
            event_type="internal_note",
            detail="SECRET INTERNAL EVENT: negotiate a lower quote.",
            created_at=datetime(2026, 7, 9, 3, 0),
        )
    )
    photo = MaintenanceAttachment(
        mailbox=MAILBOX,
        order_id=order.id,
        kind="GENERAL",
        filename="wide-kitchen-tap.png",
        content_type="image/png",
        content_bytes=_png_bytes(),
        file_size=100,
        notes="Kitchen tap before repair",
        created_at=datetime(2026, 7, 6, 4, 0),
    )
    unsupported_photo = MaintenanceAttachment(
        mailbox=MAILBOX,
        order_id=order.id,
        kind="GENERAL",
        filename="phone-image.heic",
        content_type="image/heic",
        content_bytes=b"not-a-renderable-image",
        file_size=22,
        created_at=datetime(2026, 7, 6, 4, 5),
    )
    db.add_all([photo, unsupported_photo])

    db.add_all(
        [
            RentDueTracker(
                mailbox=MAILBOX,
                property_address="8 Very Long Property Avenue Hampton Park VIC 3976",
                frequency="MONTHLY",
                due_date=datetime(2026, 7, 1),
                status=RentTrackStatus.PARTIAL,
                partial_amount=600.0,
                notes="SECRET RENT FOLLOW-UP: tenant medical details.",
                created_at=datetime(2026, 7, 1),
                updated_at=datetime(2026, 7, 12),
            ),
            LeaseRenewalRecord(
                mailbox=MAILBOX,
                property_id=prop.id,
                status=LeaseRenewalStatus.SENT_TO_OWNER,
                current_lease_start=datetime(2025, 8, 1),
                current_lease_end=datetime(2026, 8, 1),
                renewal_due_date=datetime(2026, 7, 20),
                current_rent=550.0,
                proposed_rent=580.0,
                assigned_user_id=user.id,
                notes="SECRET LEASE NOTE: internal negotiation ceiling.",
                created_at=datetime(2026, 6, 1),
                updated_at=datetime(2026, 7, 10),
            ),
            ComplianceRecord(
                mailbox=MAILBOX,
                property_id=prop.id,
                compliance_type=ComplianceType.SMOKE,
                status=ComplianceRecordStatus.COMPLETED,
                due_date=datetime(2027, 7, 1),
                completed_at=datetime(2026, 7, 2),
                provider_name="Safe Homes Victoria",
                result_text="Compliant",
                notes="SECRET COMPLIANCE NOTE: internal provider comment.",
                created_at=datetime(2026, 7, 2),
                updated_at=datetime(2026, 7, 2),
            ),
        ]
    )
    db.commit()
    db.refresh(photo)
    return {"user": user, "property": prop, "order": order, "photo": photo}


def _options(seeded, sections, **overrides):
    values = {
        "property_id": seeded["property"].id,
        "start_date": START,
        "end_date": END,
        "prepared_date": date(2026, 7, 18),
        "landlord_name": "Alexandra Landlord and Jordan Landlord",
        "property_manager_id": seeded["user"].id,
        "intro_message": "Thank you for trusting us with the management of your property.",
        "overall_summary": "The tenancy remains active and the urgent plumbing quote needs a decision.",
        "additional_notes": "Please contact your property manager with any questions.",
        "include_no_activity": False,
        "include_photos": True,
        "include_financial": True,
        "include_internal_notes": False,
        "selected_sections": list(sections),
        "section_notes": {},
        "manual_activities": [],
        "photo_attachment_ids": [seeded["photo"].id],
        "hero_photo_id": seeded["photo"].id,
    }
    values.update(overrides)
    return values


def _report(db, seeded, sections, **overrides):
    return assemble_report(
        db,
        mailbox=MAILBOX,
        current_user=seeded["user"],
        options=_options(seeded, sections, **overrides),
    )


def _pdf_text(pdf_bytes: bytes) -> tuple[PdfReader, str]:
    reader = PdfReader(BytesIO(pdf_bytes))
    return reader, "\n".join(page.extract_text() or "" for page in reader.pages)


def test_section_selection_order_and_empty_exclusion(db, seeded):
    report = _report(
        db,
        seeded,
        ["market_update", "maintenance_repairs", "property_tenancy", "routine_inspections"],
    )

    assert report["included_section_ids"] == ["maintenance_repairs", "property_tenancy"]
    assert report["excluded_empty_section_ids"] == ["market_update", "routine_inspections"]
    preview = render_preview_html(report, {})
    assert "Maintenance and Repairs" in preview
    assert "Property and Tenancy Overview" in preview
    assert "Market Update" not in preview
    assert "Routine Inspections" not in preview


def test_select_all_clear_all_and_default_normalisation():
    assert normalize_section_ids(ALL_SECTION_IDS) == ALL_SECTION_IDS
    assert normalize_section_ids(ALL_SECTION_IDS + ALL_SECTION_IDS[:2]) == ALL_SECTION_IDS
    assert normalize_section_ids([], allow_empty=True) == []
    assert default_section_ids()
    with pytest.raises(LandlordReportError, match="Select at least one"):
        normalize_section_ids([])

    script = Path("app/static/app.js").read_text(encoding="utf-8")
    assert 'action === "clear" ? []' in script
    assert "landlordReportSelectAll" in script
    assert "landlordReportClearAll" in script


def test_internal_notes_are_deliberately_excluded_and_included(db, seeded):
    sections = ["rent_arrears", "lease_rent_review", "compliance_safety", "additional_notes"]
    safe_report = _report(db, seeded, sections, additional_notes=None, include_internal_notes=False)
    safe_text = json.dumps(safe_report, default=str)
    assert "SECRET" not in safe_text

    internal_report = _report(db, seeded, sections, additional_notes=None, include_internal_notes=True)
    internal_text = json.dumps(internal_report, default=str)
    assert "SECRET RENT FOLLOW-UP" in internal_text
    assert "SECRET LEASE NOTE" in internal_text
    assert "SECRET COMPLIANCE NOTE" in internal_text
    assert "SECRET ACCESS NOTE" in internal_text
    assert "SECRET INTERNAL EVENT" in internal_text


def test_manual_activity_mapping_deduplicates_and_requires_opt_in_for_internal(db, seeded):
    activity = {
        "id": "manual-1",
        "section_id": "routine_inspections",
        "date": date(2026, 7, 15),
        "title": "Routine inspection completed",
        "description": "Property presented in a clean condition.",
        "status": "completed",
        "category": "Routine inspection",
        "contractor": None,
        "amount": None,
        "landlord_action": "No action required.",
        "internal": False,
    }
    report = _report(
        db,
        seeded,
        ["routine_inspections"],
        intro_message=None,
        overall_summary=None,
        additional_notes=None,
        manual_activities=[activity, dict(activity, id="manual-duplicate")],
    )
    timeline = report["sections"][0]["blocks"][0]
    assert timeline["type"] == "timeline"
    assert len(timeline["items"]) == 1
    assert timeline["items"][0]["action_required"] == "No action required."

    with pytest.raises(LandlordReportError, match="no reportable activity"):
        _report(
            db,
            seeded,
            ["market_update"],
            intro_message=None,
            overall_summary=None,
            additional_notes=None,
            manual_activities=[dict(activity, section_id="market_update", internal=True)],
            include_internal_notes=False,
        )


def test_context_uses_real_sources_and_filters_unsupported_photos(db, seeded):
    context = build_report_context(
        db,
        mailbox=MAILBOX,
        property_id=seeded["property"].id,
        start_date=START,
        end_date=END,
        current_user=seeded["user"],
        defaults=default_section_ids(),
    )

    assert context["suggested_landlord_name"] == "Alexandra Landlord, Jordan Landlord"
    assert context["source_summary"]["maintenance_orders"] == 1
    assert context["source_summary"]["rent_records"] == 1
    assert context["source_summary"]["compliance_records"] == 1
    assert context["source_summary"]["lease_record_available"] is True
    assert [photo["attachment_id"] for photo in context["available_photos"]] == [seeded["photo"].id]


def test_pdf_generation_selected_contents_and_a4_pages(db, seeded):
    report = _report(db, seeded, ["executive_summary", "maintenance_repairs"])
    photos = load_photo_bytes(
        db,
        mailbox=MAILBOX,
        property_id=seeded["property"].id,
        attachment_ids=[seeded["photo"].id],
    )
    pdf = generate_landlord_report_pdf(report, photos)
    reader, text = _pdf_text(pdf)

    assert pdf.startswith(b"%PDF")
    assert len(reader.pages) >= 3
    assert "Monthly Property Report" in text
    assert "Executive Summary" in text
    assert "Maintenance and Repairs" in text
    assert "Compliance and Safety" not in text
    assert "admin@donspremier.com.au" in text
    assert "0422 643 451" in text
    assert "prepared for the property owner" in text
    for page in reader.pages:
        assert float(page.mediabox.width) == pytest.approx(595.28, abs=1)
        assert float(page.mediabox.height) == pytest.approx(841.89, abs=1)


def test_long_multi_page_report_and_photo_preview(db, seeded):
    activities = [
        {
            "id": f"long-{index}",
            "section_id": "additional_notes",
            "date": date(2026, 7, (index % 28) + 1),
            "title": f"Detailed property update {index + 1}",
            "description": ("A verified landlord-facing explanation with a long property address and clear outcome. " * 8).strip(),
            "status": "in_progress" if index % 2 else "completed",
            "landlord_action": "Please review and provide instructions." if index % 7 == 0 else None,
            "internal": False,
        }
        for index in range(45)
    ]
    report = _report(
        db,
        seeded,
        ["supporting_photos", "additional_notes"],
        intro_message=None,
        overall_summary=None,
        additional_notes=None,
        manual_activities=activities,
    )
    photos = load_photo_bytes(
        db,
        mailbox=MAILBOX,
        property_id=seeded["property"].id,
        attachment_ids=report["available_photo_ids"],
    )
    preview = render_preview_html(report, photos)
    pdf = generate_landlord_report_pdf(report, photos)
    reader, text = _pdf_text(pdf)

    assert "data:image/png;base64," in preview
    assert len(reader.pages) >= 6
    assert "Detailed property update 45" in text
    assert "Supporting Photos" in text


def test_missing_data_financial_toggle_and_safe_values(db):
    user = User(
        email="pm2@donspremier.com.au",
        name="Property Manager",
        role=UserRole.PM,
        is_active=True,
        password_hash="not-used",
    )
    prop = ManagedProperty(
        mailbox=MAILBOX,
        property_address="99 Minimal Street",
        is_active=True,
    )
    db.add_all([user, prop])
    db.commit()
    seeded = {"user": user, "property": prop, "photo": type("Photo", (), {"id": 0})()}
    report = _report(
        db,
        seeded,
        ["property_tenancy", "rent_financial"],
        include_financial=False,
        include_no_activity=True,
        photo_attachment_ids=[],
        hero_photo_id=None,
        intro_message=None,
        overall_summary=None,
        additional_notes=None,
    )
    text = json.dumps(report, default=str)
    assert "Not recorded" in text
    assert "Excluded" in text
    assert "undefined" not in text
    assert "NaN" not in text
    assert "null" not in render_preview_html(report, {})


def test_permissions_mailbox_isolation_and_formatting(db, seeded):
    assert has_page_access(UserRole.PM, "landlord_reports", db)
    assert has_page_access(UserRole.ADMIN, "landlord_reports", db)
    assert not has_page_access(UserRole.LEASING, "landlord_reports", db)
    assert not has_page_access(UserRole.READONLY, "landlord_reports", db)

    with pytest.raises(LandlordReportError, match="not available in this mailbox"):
        build_report_context(
            db,
            mailbox="other@donspremier.com.au",
            property_id=seeded["property"].id,
            start_date=START,
            end_date=END,
            current_user=seeded["user"],
            defaults=default_section_ids(),
        )

    filename = safe_report_filename("8 Kawai La, Hampton Park / VIC 3976", START, END)
    assert filename == "Monthly-Property-Report_8-Kawai-La-Hampton-Park-VIC-3976_July-2026.pdf"
    assert format_date_au(date(2026, 7, 18)) == "18/07/2026"
    assert format_currency_aud(1250) == "$1,250.00"


def test_report_api_preview_pdf_and_permission_dependency(db, seeded):
    api = FastAPI()
    api.include_router(landlord_reports_router)

    def override_db():
        yield db

    api.dependency_overrides[get_db] = override_db
    api.dependency_overrides[get_current_mailbox] = lambda: MAILBOX
    api.dependency_overrides[get_current_user] = lambda: seeded["user"]
    client = TestClient(api)

    context_response = client.get(
        "/landlord-reports/context",
        params={"property_id": seeded["property"].id, "start_date": START.isoformat(), "end_date": END.isoformat()},
    )
    assert context_response.status_code == 200
    assert context_response.json()["property"]["id"] == seeded["property"].id

    payload = _options(seeded, ["executive_summary", "maintenance_repairs"])
    for key in ("start_date", "end_date", "prepared_date"):
        payload[key] = payload[key].isoformat()
    preview_response = client.post("/landlord-reports/preview", json=payload)
    assert preview_response.status_code == 200
    assert "Monthly Property Report" in preview_response.json()["html"]
    pdf_response = client.post("/landlord-reports/pdf", json=payload)
    assert pdf_response.status_code == 200
    assert pdf_response.headers["content-type"] == "application/pdf"
    assert pdf_response.content.startswith(b"%PDF")

    restricted = User(
        email="restricted@donspremier.com.au",
        name="Restricted Staff",
        role=UserRole.READONLY,
        is_active=True,
        password_hash="not-used",
    )
    db.add(restricted)
    db.commit()
    api.dependency_overrides[get_current_user] = lambda: restricted
    denied = client.get(
        "/landlord-reports/context",
        params={"property_id": seeded["property"].id, "start_date": START.isoformat(), "end_date": END.isoformat()},
    )
    assert denied.status_code == 403
