from fastapi import APIRouter, Depends, HTTPException
from app.authz import get_mailbox_id
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import BlacklistedSender

router = APIRouter()


def _key(mailbox_id: str, email: str) -> str:
    return f"{mailbox_id}::{email}"

def _unkey(s: str) -> str:
    if "::" in s:
        return s.split("::",1)[1]
    return s


@router.get("")
def list_blacklist(mailbox_id: str = Depends(get_mailbox_id), db: Session = Depends(get_db)):
    items = db.query(BlacklistedSender).filter(BlacklistedSender.email.like(_key(mailbox_id, '') + '%')).order_by(BlacklistedSender.created_at.desc()).all()
    # Frontend expects a JSON array.
    return [{"id": x.id, "email": _unkey(x.email)} for x in items]

@router.post("")
def add_blacklist(email: str, mailbox_id: str = Depends(get_mailbox_id), db: Session = Depends(get_db)):
    email = email.strip().lower()
    if not email:
        raise HTTPException(400, "Email required")
    key = _key(mailbox_id, email)
    exists = db.query(BlacklistedSender).filter(BlacklistedSender.email == key).first()
    if exists:
        return {"ok": True, "already": True}
    db.add(BlacklistedSender(email=key))
    db.commit()
    return {"ok": True}


@router.delete("")
def delete_blacklist_by_email(email: str, mailbox_id: str = Depends(get_mailbox_id), db: Session = Depends(get_db)):
    """Delete a blacklisted sender by email.

    The frontend calls DELETE /blacklist?email=... (query param). Keep this
    endpoint for compatibility.
    """
    email = (email or "").strip().lower()
    if not email:
        raise HTTPException(400, "Email required")
    key = _key(mailbox_id, email)
    x = db.query(BlacklistedSender).filter(BlacklistedSender.email == key).first()
    if not x:
        raise HTTPException(404, "Not found")
    db.delete(x)
    db.commit()
    return {"ok": True}

@router.delete("/{item_id}")
def delete_blacklist(item_id: int, mailbox_id: str = Depends(get_mailbox_id), db: Session = Depends(get_db)):
    x = db.get(BlacklistedSender, item_id)
    if x and not x.email.startswith(_key(mailbox_id, '')):
        raise HTTPException(404, 'Not found')
    if not x:
        raise HTTPException(404, "Not found")
    db.delete(x)
    db.commit()
    return {"ok": True}
