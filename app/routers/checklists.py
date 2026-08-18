from __future__ import annotations

import json
import re
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.authz import get_current_user
from app.db import get_db
from app.deps import get_current_mailbox
from app.models import ChecklistRun, User
from app.services.checklist_pdf import generate_checklist_pdf

router = APIRouter(prefix="/checklists", tags=["checklists"])

CHECKS = [
    "Social Media Check", "ID4Me / Identity Check", "Google / Online Search",
    "Employment Verification", "Current Property Manager Reference",
    "Rental Ledger Review", "Supporting Documents Review",
]
CHECK_STATUSES = {"Verified / Positive", "Pending", "Concern", "Not Applicable"}
OVERALL = {"Recommended", "Pending", "Not Recommended"}
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

def serialize(row: ChecklistRun, full: bool = True) -> dict:
    data = {"id": row.id, "process_key": row.process_key, "template_version": row.template_version,
            "status": row.status, "title": row.title, "applicant_name": row.applicant_name,
            "property_address": row.property_address, "application_received": row.application_received,
            "progress_percent": row.progress_percent, "completed_at": row.completed_at,
            "created_at": row.created_at, "updated_at": row.updated_at}
    if full: data["payload"] = json.loads(row.payload_json)
    return data

def validate_payload(payload: dict) -> tuple[dict, int]:
    checks = payload.get("checks")
    if not isinstance(checks, list) or len(checks) != len(CHECKS):
        raise HTTPException(400, "The Application Screening checklist must contain all seven checks.")
    clean = blank_payload()
    for key in ("default_result", "default_evidence", "key_positive_points", "outstanding_items", "owner_comment"):
        clean[key] = str(payload.get(key) or "").strip()
    clean["screened_by"] = DEFAULT_CHECKER
    clean["overall_status"] = str(payload.get("overall_status") or "Pending")
    if clean["overall_status"] not in OVERALL: raise HTTPException(400, "Invalid overall status.")
    completed = 0
    for i, expected in enumerate(CHECKS):
        item = checks[i] if isinstance(checks[i], dict) else {}
        status = str(item.get("status") or "Pending")
        if status not in CHECK_STATUSES: raise HTTPException(400, "Invalid check status.")
        clean["checks"][i] = {"name": expected, "status": status,
            "checked_by": DEFAULT_CHECKER, "result": str(item.get("result") or ""),
            "notes": str(item.get("notes") or "")}
        if status != "Pending": completed += 1
    return clean, round(completed * 100 / len(CHECKS))

@router.get("/processes")
def processes(_: User = Depends(get_current_user)):
    return [{"key": "application_screening", "name": "Application Screening", "version": 1,
             "description": "Seven-step property application screening and owner assessment."}]

@router.post("/runs")
def create(payload: CreateIn, db: Session = Depends(get_db), mailbox: str = Depends(get_current_mailbox), user: User = Depends(get_current_user)):
    if payload.process_key != "application_screening": raise HTTPException(400, "Unknown checklist process.")
    row = ChecklistRun(mailbox=mailbox, process_key=payload.process_key, template_version=1,
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
    row.payload_json, row.progress_percent, row.updated_at = json.dumps(clean), progress, datetime.utcnow()
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
