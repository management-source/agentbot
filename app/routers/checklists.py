from __future__ import annotations

import json
import re
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.authz import get_current_user
from app.db import get_db
from app.deps import get_current_mailbox
from app.models import ChecklistRun, User
from app.services.checklist_pdf import generate_checklist_pdf
from app.services.gmail_send import send_new_email

router = APIRouter(prefix="/checklists", tags=["checklists"])

CHECKS = [
    "Social Media Check", "ID4Me / Identity Check", "Google / Online Search",
    "Employment Verification", "Current Property Manager Reference",
    "Supporting Documents Review",
]
CHECK_STATUSES = {"Verified / Positive", "Pending", "Concern", "Not Applicable"}
OVERALL = {"Recommended", "Pending", "Not Recommended"}
CHECKLIST_TEMPLATE_VERSION = 2
DEFAULT_CHECKER = "Jessica Gale — Property Manager"

def blank_payload() -> dict:
    return {
        "screened_by": DEFAULT_CHECKER, "default_result": "", "default_evidence": "",
        "key_positive_points": "", "outstanding_items": "",
        "overall_status": "Pending", "owner_comment": "",
        "checks": [{"name": name, "status": "Pending", "checked_by": DEFAULT_CHECKER,
                    "result": "", "notes": ""} for name in CHECKS],
    }

class CreateIn(BaseModel):
    process_key: str = "application_screening"
    applicant_name: str = Field(min_length=1, max_length=200)
    property_address: str = Field(min_length=1, max_length=500)
    application_received: datetime | None = None

class UpdateIn(BaseModel):
    applicant_name: str | None = None
    property_address: str | None = None
    application_received: datetime | None = None
    payload: dict | None = None
    complete: bool = False

class ApprovalIn(BaseModel):
    signature_data: str | None = None

def _normalize_payload(payload: dict | None, *, require_all_checks: bool = False) -> tuple[dict, int]:
    raw = payload if isinstance(payload, dict) else {}
    checks = raw.get("checks")
    if not isinstance(checks, list):
        if require_all_checks:
            raise HTTPException(400, "The Application Screening checklist must contain all six checks.")
        checks = []

    checks_by_name: dict[str, dict] = {}
    for item in checks:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name in CHECKS and name not in checks_by_name:
            checks_by_name[name] = item

    if require_all_checks and any(name not in checks_by_name for name in CHECKS):
        raise HTTPException(400, "The Application Screening checklist must contain all six checks.")

    clean = blank_payload()
    for key in ("default_result", "default_evidence", "key_positive_points", "outstanding_items", "owner_comment"):
        clean[key] = str(raw.get(key) or "").strip()
    clean["screened_by"] = str(raw.get("screened_by") or DEFAULT_CHECKER).strip()
    for key in ("approval_status", "approval_requested_at", "approved_at", "signature_data"):
        clean[key] = str(raw.get(key) or "")
    clean["signature_default"] = bool(raw.get("signature_default", False))

    overall_status = str(raw.get("overall_status") or "Pending")
    if overall_status not in OVERALL:
        if require_all_checks:
            raise HTTPException(400, "Invalid overall status.")
        overall_status = "Pending"
    clean["overall_status"] = overall_status

    completed = 0
    for index, expected in enumerate(CHECKS):
        item = checks_by_name.get(expected, {})
        status = str(item.get("status") or "Pending")
        if status not in CHECK_STATUSES:
            if require_all_checks:
                raise HTTPException(400, "Invalid check status.")
            status = "Pending"
        clean["checks"][index] = {
            "name": expected,
            "status": status,
            "checked_by": str(item.get("checked_by") or DEFAULT_CHECKER).strip(),
            "result": str(item.get("result") or ""),
            "notes": str(item.get("notes") or ""),
        }
        if status != "Pending":
            completed += 1

    return clean, round(completed * 100 / len(CHECKS))


def serialize(row: ChecklistRun, full: bool = True) -> dict:
    payload, progress = _normalize_payload(json.loads(row.payload_json))
    data = {"id": row.id, "process_key": row.process_key, "template_version": row.template_version,
            "status": row.status, "title": row.title, "applicant_name": row.applicant_name,
            "property_address": row.property_address, "application_received": row.application_received,
            "progress_percent": progress, "completed_at": row.completed_at,
            "created_at": row.created_at, "updated_at": row.updated_at,
            "approval_status": payload.get("approval_status") or "NOT_REQUESTED"}
    if full: data["payload"] = payload
    return data


def validate_payload(payload: dict) -> tuple[dict, int]:
    return _normalize_payload(payload, require_all_checks=True)

@router.get("/processes")
def processes(_: User = Depends(get_current_user)):
    return [{"key": "application_screening", "name": "Application Screening", "version": CHECKLIST_TEMPLATE_VERSION,
             "description": "Six-step property application screening and owner assessment."}]

@router.post("/runs")
def create(payload: CreateIn, db: Session = Depends(get_db), mailbox: str = Depends(get_current_mailbox), user: User = Depends(get_current_user)):
    if payload.process_key != "application_screening": raise HTTPException(400, "Unknown checklist process.")
    row = ChecklistRun(mailbox=mailbox, process_key=payload.process_key, template_version=CHECKLIST_TEMPLATE_VERSION,
        status="IN_PROGRESS", title="Property Application Screening Checklist",
        applicant_name=payload.applicant_name.strip(), property_address=payload.property_address.strip(),
        application_received=payload.application_received, payload_json=json.dumps(blank_payload()), created_by_user_id=user.id)
    db.add(row); db.commit(); db.refresh(row)
    return serialize(row)

@router.get("/runs")
def list_runs(status: str = "IN_PROGRESS", db: Session = Depends(get_db), mailbox: str = Depends(get_current_mailbox), _: User = Depends(get_current_user)):
    q = db.query(ChecklistRun).filter(ChecklistRun.mailbox == mailbox)
    if status != "ALL": q = q.filter(ChecklistRun.status == status)
    return [serialize(r, False) for r in q.order_by(ChecklistRun.updated_at.desc()).all()]

@router.get("/runs/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db), mailbox: str = Depends(get_current_mailbox), _: User = Depends(get_current_user)):
    row = db.query(ChecklistRun).filter_by(id=run_id, mailbox=mailbox).first()
    if not row: raise HTTPException(404, "Checklist not found.")
    return serialize(row)

@router.put("/runs/{run_id}")
def update(run_id: int, payload: UpdateIn, db: Session = Depends(get_db), mailbox: str = Depends(get_current_mailbox), _: User = Depends(get_current_user)):
    row = db.query(ChecklistRun).filter_by(id=run_id, mailbox=mailbox).first()
    if not row: raise HTTPException(404, "Checklist not found.")
    if row.status == "COMPLETED": raise HTTPException(409, "Completed reports are read-only.")
    if payload.applicant_name is not None: row.applicant_name = payload.applicant_name.strip()
    if payload.property_address is not None: row.property_address = payload.property_address.strip()
    if payload.application_received is not None: row.application_received = payload.application_received
    clean, progress = validate_payload(payload.payload or json.loads(row.payload_json))
    if payload.complete:
        if progress != 100: raise HTTPException(400, "Resolve every Pending check before completing the report.")
        row.status, row.completed_at = "COMPLETED", datetime.utcnow()
    row.payload_json, row.progress_percent, row.template_version, row.updated_at = json.dumps(clean), progress, CHECKLIST_TEMPLATE_VERSION, datetime.utcnow()
    db.commit(); db.refresh(row)
    return serialize(row)

@router.get("/runs/{run_id}/pdf")
def download_pdf(run_id: int, db: Session = Depends(get_db), mailbox: str = Depends(get_current_mailbox), _: User = Depends(get_current_user)):
    row = db.query(ChecklistRun).filter_by(id=run_id, mailbox=mailbox).first()
    if not row: raise HTTPException(404, "Checklist not found.")
    if row.status != "COMPLETED": raise HTTPException(400, "Complete the checklist before exporting its report.")
    filename = re.sub(r"[^A-Za-z0-9._-]+", "-", f"application-screening-{row.applicant_name}").strip("-") + ".pdf"
    return Response(generate_checklist_pdf(serialize(row)), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

@router.post("/runs/{run_id}/request-approval")
def request_approval(run_id: int, db: Session = Depends(get_db), mailbox: str = Depends(get_current_mailbox), user: User = Depends(get_current_user)):
    row = db.query(ChecklistRun).filter_by(id=run_id, mailbox=mailbox).first()
    if not row: raise HTTPException(404, "Checklist not found.")
    if row.status != "COMPLETED": raise HTTPException(400, "Complete the checklist before requesting approval.")
    payload, progress = _normalize_payload(json.loads(row.payload_json))
    payload["approval_status"], payload["approval_requested_at"] = "REQUESTED", datetime.utcnow().isoformat()
    try:
        send_new_email(db=db, mailbox=mailbox, to_email="admin@donspremier.com.au",
            subject=f"Approval required: Application Screening — {row.applicant_name}",
            body_text=(f"Jessica, please review, sign and approve the Application Screening report for {row.applicant_name}.\n\n"
                       f"Property: {row.property_address}\nRequested by: {user.name or user.email}\n\nOpen Checklist > Reports in AgentBot to review and approve."))
    except Exception as exc:
        raise HTTPException(502, f"Approval email could not be sent: {exc}") from exc
    row.payload_json, row.progress_percent, row.template_version, row.updated_at = json.dumps(payload), progress, CHECKLIST_TEMPLATE_VERSION, datetime.utcnow(); db.commit(); db.refresh(row)
    return serialize(row)

@router.post("/runs/{run_id}/approve")
def approve(run_id: int, approval: ApprovalIn, db: Session = Depends(get_db), mailbox: str = Depends(get_current_mailbox), _: User = Depends(get_current_user)):
    row = db.query(ChecklistRun).filter_by(id=run_id, mailbox=mailbox).first()
    if not row: raise HTTPException(404, "Checklist not found.")
    if row.status != "COMPLETED": raise HTTPException(400, "Complete the checklist before approving it.")
    signature = (approval.signature_data or "").strip()
    if signature and (not re.match(r"^data:image/(png|jpeg|webp);base64,", signature, re.I) or len(signature) > 1_500_000): raise HTTPException(400, "Upload a valid PNG, JPEG, or WebP signature image under 1 MB.")
    payload, progress = _normalize_payload(json.loads(row.payload_json)); payload.update({"approval_status":"APPROVED", "approved_at":datetime.utcnow().isoformat(), "signature_data":signature, "signature_default":not bool(signature)})
    row.payload_json, row.progress_percent, row.template_version, row.updated_at = json.dumps(payload), progress, CHECKLIST_TEMPLATE_VERSION, datetime.utcnow(); db.commit(); db.refresh(row)
    jessica = db.query(User).filter(func.lower(User.name) == "jessica gale").filter(User.is_active == True).first()
    recipient = jessica.email if jessica else "admin@donspremier.com.au"
    notification_sent = True
    try:
        send_new_email(db=db, mailbox=mailbox, to_email=recipient,
            subject=f"Application Screening approved — {row.applicant_name}",
            body_text=f"The Application Screening report for {row.applicant_name} at {row.property_address} has been signed and approved using Jessica Gale's authorised signature.")
    except Exception:
        notification_sent = False
    result = serialize(row); result["confirmation_email_sent"] = notification_sent
    return result

@router.delete("/runs/{run_id}")
def delete_run(run_id: int, db: Session = Depends(get_db), mailbox: str = Depends(get_current_mailbox), _: User = Depends(get_current_user)):
    row = db.query(ChecklistRun).filter_by(id=run_id, mailbox=mailbox).first()
    if not row: raise HTTPException(404, "Checklist not found.")
    db.delete(row); db.commit()
    return {"ok": True}
