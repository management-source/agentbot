from fastapi import APIRouter, Depends, HTTPException
from app.authz import get_current_user
from app.deps import get_current_mailbox
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import BlacklistedSender

router = APIRouter()

@router.get("")
def list_blacklist(db: Session = Depends(get_db), mailbox: str = Depends(get_current_mailbox), user=Depends(get_current_user)):
    items = db.query(BlacklistedSender).filter(BlacklistedSender.mailbox == mailbox).order_by(BlacklistedSender.created_at.desc()).all()
    # Frontend expects a JSON array.
    return [{"id": x.id, "email": x.email} for x in items]

@router.post("")
def add_blacklist(email: str, db: Session = Depends(get_db), mailbox: str = Depends(get_current_mailbox), user=Depends(get_current_user)):
    email = email.strip().lower()
    if not email:
        raise HTTPException(400, "Email required")
    exists = db.query(BlacklistedSender).filter(BlacklistedSender.mailbox == mailbox).filter(BlacklistedSender.email == email).first()
    if exists:
        return {"ok": True, "already": True}
    db.add(BlacklistedSender(mailbox=mailbox, email=email))
    db.commit()
    return {"ok": True}


@router.delete("")
def delete_blacklist_by_email(email: str, db: Session = Depends(get_db), mailbox: str = Depends(get_current_mailbox), user=Depends(get_current_user)):
    """Delete a blacklisted sender by email.

    The frontend calls DELETE /blacklist?email=... (query param). Keep this
    endpoint for compatibility.
    """
    email = (email or "").strip().lower()
    if not email:
        raise HTTPException(400, "Email required")
    x = db.query(BlacklistedSender).filter(BlacklistedSender.mailbox == mailbox).filter(BlacklistedSender.email == email).first()
    if not x:
        raise HTTPException(404, "Not found")
    db.delete(x)
    db.commit()
    return {"ok": True}

@router.delete("/{item_id}")
def delete_blacklist(item_id: int, db: Session = Depends(get_db)):
    x = db.get(BlacklistedSender, item_id)
    if not x:
        raise HTTPException(404, "Not found")
    db.delete(x)
    db.commit()
    return {"ok": True}
