from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.authz import get_current_user, require_role
from app.db import get_db
from app.models import (
    MySpaceNote,
    MySpaceQuickLink,
    MySpaceSnippet,
    MySpaceTodo,
    GoogleCalendarConnection,
    StaffGuide,
    User,
    UserRole,
)
from app.services.google_calendar import (
    calendar_flow,
    calendar_service,
    make_calendar_state,
    normalize_event,
)

router = APIRouter(prefix="/my-space", tags=["my-space"])

MAX_GUIDE_BYTES = 15 * 1024 * 1024


class MySpaceTodoIn(BaseModel):
    title: str
    notes: str | None = None
    priority: str = "normal"
    bucket: str = "today"
    item_type: str = "task"
    follow_up_with: str | None = None
    due_at: datetime | None = None
    is_done: bool = False


class MySpaceTodoPatch(BaseModel):
    title: str | None = None
    notes: str | None = None
    priority: str | None = None
    bucket: str | None = None
    item_type: str | None = None
    follow_up_with: str | None = None
    due_at: datetime | None = None
    is_done: bool | None = None


class MySpaceNoteIn(BaseModel):
    body: str = ""


class QuickLinkIn(BaseModel):
    title: str
    url: str
    notes: str | None = None


class SnippetIn(BaseModel):
    title: str
    body: str
    category: str | None = None


def _clean_priority(value: str | None) -> str:
    priority = (value or "normal").strip().lower()
    return priority if priority in {"low", "normal", "high"} else "normal"


def _clean_bucket(value: str | None) -> str:
    bucket = (value or "today").strip().lower()
    return bucket if bucket in {"today", "week", "later"} else "today"


def _clean_item_type(value: str | None) -> str:
    item_type = (value or "task").strip().lower()
    return item_type if item_type in {"task", "follow_up"} else "task"


def _fields_set(payload: BaseModel) -> set[str]:
    return set(getattr(payload, "model_fields_set", getattr(payload, "__fields_set__", set())))


def _todo_out(todo: MySpaceTodo) -> dict:
    return {
        "id": todo.id,
        "title": todo.title,
        "notes": todo.notes,
        "priority": todo.priority,
        "bucket": todo.bucket,
        "item_type": todo.item_type,
        "follow_up_with": todo.follow_up_with,
        "due_at": todo.due_at,
        "is_done": todo.is_done,
        "created_at": todo.created_at,
        "updated_at": todo.updated_at,
        "completed_at": todo.completed_at,
    }


def _quick_link_out(link: MySpaceQuickLink) -> dict:
    return {
        "id": link.id,
        "title": link.title,
        "url": link.url,
        "notes": link.notes,
        "created_at": link.created_at,
        "updated_at": link.updated_at,
    }


def _snippet_out(snippet: MySpaceSnippet) -> dict:
    return {
        "id": snippet.id,
        "title": snippet.title,
        "body": snippet.body,
        "category": snippet.category,
        "created_at": snippet.created_at,
        "updated_at": snippet.updated_at,
    }


def _guide_out(guide: StaffGuide) -> dict:
    return {
        "id": guide.id,
        "title": guide.title,
        "description": guide.description,
        "filename": guide.filename,
        "content_type": guide.content_type,
        "uploaded_by_user_id": guide.uploaded_by_user_id,
        "created_at": guide.created_at,
        "updated_at": guide.updated_at,
    }


def _get_own_todo(db: Session, user_id: int, todo_id: int) -> MySpaceTodo:
    todo = (
        db.query(MySpaceTodo)
        .filter(MySpaceTodo.id == todo_id)
        .filter(MySpaceTodo.user_id == user_id)
        .first()
    )
    if not todo:
        raise HTTPException(status_code=404, detail="Todo item not found.")
    return todo


def _get_own_link(db: Session, user_id: int, link_id: int) -> MySpaceQuickLink:
    link = (
        db.query(MySpaceQuickLink)
        .filter(MySpaceQuickLink.id == link_id)
        .filter(MySpaceQuickLink.user_id == user_id)
        .first()
    )
    if not link:
        raise HTTPException(status_code=404, detail="Quick link not found.")
    return link


def _get_own_snippet(db: Session, user_id: int, snippet_id: int) -> MySpaceSnippet:
    snippet = (
        db.query(MySpaceSnippet)
        .filter(MySpaceSnippet.id == snippet_id)
        .filter(MySpaceSnippet.user_id == user_id)
        .first()
    )
    if not snippet:
        raise HTTPException(status_code=404, detail="Snippet not found.")
    return snippet


@router.get("")
def get_my_space(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    todos = (
        db.query(MySpaceTodo)
        .filter(MySpaceTodo.user_id == user.id)
        .order_by(
            MySpaceTodo.is_done.asc(),
            MySpaceTodo.bucket.asc(),
            MySpaceTodo.due_at.is_(None),
            MySpaceTodo.due_at.asc(),
            MySpaceTodo.created_at.desc(),
        )
        .all()
    )
    note = db.get(MySpaceNote, user.id)
    links = (
        db.query(MySpaceQuickLink)
        .filter(MySpaceQuickLink.user_id == user.id)
        .order_by(MySpaceQuickLink.title.asc())
        .all()
    )
    snippets = (
        db.query(MySpaceSnippet)
        .filter(MySpaceSnippet.user_id == user.id)
        .order_by(MySpaceSnippet.category.asc(), MySpaceSnippet.title.asc())
        .all()
    )
    guides = db.query(StaffGuide).order_by(StaffGuide.created_at.desc()).all()
    return {
        "todos": [_todo_out(todo) for todo in todos],
        "note": note.body if note else "",
        "quick_links": [_quick_link_out(link) for link in links],
        "snippets": [_snippet_out(snippet) for snippet in snippets],
        "staff_guides": [_guide_out(guide) for guide in guides],
    }


@router.get("/calendar/status")
def google_calendar_status(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    connection = db.get(GoogleCalendarConnection, user.id)
    return {
        "connected": bool(connection),
        "email": connection.google_email if connection else None,
        "updated_at": connection.updated_at if connection else None,
    }


@router.post("/calendar/google/connect")
def connect_google_calendar(user: User = Depends(get_current_user)):
    flow = calendar_flow()
    authorization_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=make_calendar_state(user.id),
    )
    return {"authorization_url": authorization_url}


@router.post("/calendar/google/disconnect")
def disconnect_google_calendar(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    connection = db.get(GoogleCalendarConnection, user.id)
    if connection:
        db.delete(connection)
        db.commit()
    return {"ok": True}


@router.get("/calendar/events")
def get_google_calendar_events(
    time_min: datetime | None = None,
    time_max: datetime | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    connection = db.get(GoogleCalendarConnection, user.id)
    if not connection:
        return {"connected": False, "events": []}
    now = datetime.now(timezone.utc)
    start = time_min or (now - timedelta(days=7))
    end = time_max or (now + timedelta(days=90))
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    if end <= start or end - start > timedelta(days=370):
        raise HTTPException(status_code=400, detail="Calendar date range must be between 1 and 370 days.")
    try:
        service = calendar_service(db, connection)
        response = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=500,
            )
            .execute()
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Google Calendar could not be loaded right now.") from exc
    events = [normalize_event(item) for item in response.get("items", []) if item.get("status") != "cancelled"]
    return {"connected": True, "email": connection.google_email, "events": events}


@router.post("/todos")
def create_todo(
    payload: MySpaceTodoIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    title = (payload.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Todo title is required.")

    now = datetime.utcnow()
    todo = MySpaceTodo(
        user_id=user.id,
        title=title,
        notes=(payload.notes or "").strip() or None,
        priority=_clean_priority(payload.priority),
        bucket=_clean_bucket(payload.bucket),
        item_type=_clean_item_type(payload.item_type),
        follow_up_with=(payload.follow_up_with or "").strip() or None,
        due_at=payload.due_at,
        is_done=bool(payload.is_done),
        created_at=now,
        updated_at=now,
        completed_at=now if payload.is_done else None,
    )
    db.add(todo)
    db.commit()
    db.refresh(todo)
    return _todo_out(todo)


@router.patch("/todos/{todo_id}")
def update_todo(
    todo_id: int,
    payload: MySpaceTodoPatch,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    todo = _get_own_todo(db, user.id, todo_id)
    was_done = bool(todo.is_done)

    if payload.title is not None:
        title = payload.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="Todo title is required.")
        todo.title = title
    if payload.notes is not None:
        todo.notes = payload.notes.strip() or None
    if payload.priority is not None:
        todo.priority = _clean_priority(payload.priority)
    if payload.bucket is not None:
        todo.bucket = _clean_bucket(payload.bucket)
    if payload.item_type is not None:
        todo.item_type = _clean_item_type(payload.item_type)
    if payload.follow_up_with is not None:
        todo.follow_up_with = payload.follow_up_with.strip() or None
    if "due_at" in _fields_set(payload):
        todo.due_at = payload.due_at
    if payload.is_done is not None:
        todo.is_done = bool(payload.is_done)
        if todo.is_done and not was_done:
            todo.completed_at = datetime.utcnow()
        if not todo.is_done:
            todo.completed_at = None

    todo.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(todo)
    return _todo_out(todo)


@router.delete("/todos/{todo_id}")
def delete_todo(
    todo_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    todo = _get_own_todo(db, user.id, todo_id)
    db.delete(todo)
    db.commit()
    return {"ok": True}


@router.put("/note")
def save_note(
    payload: MySpaceNoteIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    body = payload.body or ""
    note = db.get(MySpaceNote, user.id)
    if note:
        note.body = body
        note.updated_at = datetime.utcnow()
    else:
        note = MySpaceNote(user_id=user.id, body=body, updated_at=datetime.utcnow())
        db.add(note)
    db.commit()
    return {"ok": True, "note": note.body}


@router.post("/quick-links")
def create_quick_link(
    payload: QuickLinkIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    title = payload.title.strip()
    url = payload.url.strip()
    if not title or not url:
        raise HTTPException(status_code=400, detail="Title and URL are required.")
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Use a full http:// or https:// URL.")
    now = datetime.utcnow()
    link = MySpaceQuickLink(user_id=user.id, title=title, url=url, notes=(payload.notes or "").strip() or None, created_at=now, updated_at=now)
    db.add(link)
    db.commit()
    db.refresh(link)
    return _quick_link_out(link)


@router.patch("/quick-links/{link_id}")
def update_quick_link(
    link_id: int,
    payload: QuickLinkIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    link = _get_own_link(db, user.id, link_id)
    title = payload.title.strip()
    url = payload.url.strip()
    if not title or not url:
        raise HTTPException(status_code=400, detail="Title and URL are required.")
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Use a full http:// or https:// URL.")
    link.title = title
    link.url = url
    link.notes = (payload.notes or "").strip() or None
    link.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(link)
    return _quick_link_out(link)


@router.delete("/quick-links/{link_id}")
def delete_quick_link(
    link_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    link = _get_own_link(db, user.id, link_id)
    db.delete(link)
    db.commit()
    return {"ok": True}


@router.post("/snippets")
def create_snippet(
    payload: SnippetIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    title = payload.title.strip()
    body = payload.body.strip()
    if not title or not body:
        raise HTTPException(status_code=400, detail="Title and snippet body are required.")
    now = datetime.utcnow()
    snippet = MySpaceSnippet(user_id=user.id, title=title, body=body, category=(payload.category or "").strip() or None, created_at=now, updated_at=now)
    db.add(snippet)
    db.commit()
    db.refresh(snippet)
    return _snippet_out(snippet)


@router.patch("/snippets/{snippet_id}")
def update_snippet(
    snippet_id: int,
    payload: SnippetIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    snippet = _get_own_snippet(db, user.id, snippet_id)
    title = payload.title.strip()
    body = payload.body.strip()
    if not title or not body:
        raise HTTPException(status_code=400, detail="Title and snippet body are required.")
    snippet.title = title
    snippet.body = body
    snippet.category = (payload.category or "").strip() or None
    snippet.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(snippet)
    return _snippet_out(snippet)


@router.delete("/snippets/{snippet_id}")
def delete_snippet(
    snippet_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    snippet = _get_own_snippet(db, user.id, snippet_id)
    db.delete(snippet)
    db.commit()
    return {"ok": True}


@router.post("/staff-guides")
def upload_staff_guide(
    title: str = Form(...),
    description: str | None = Form(default=None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
):
    clean_title = title.strip()
    if not clean_title:
        raise HTTPException(status_code=400, detail="Guide title is required.")
    raw = file.file.read(MAX_GUIDE_BYTES + 1)
    if not raw:
        raise HTTPException(status_code=400, detail="PDF file is empty.")
    if len(raw) > MAX_GUIDE_BYTES:
        raise HTTPException(status_code=413, detail="PDF guide exceeds 15MB.")
    if not raw.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    now = datetime.utcnow()
    guide = StaffGuide(
        title=clean_title,
        description=(description or "").strip() or None,
        filename=file.filename or "staff-guide.pdf",
        content_type="application/pdf",
        content_bytes=raw,
        uploaded_by_user_id=user.id,
        created_at=now,
        updated_at=now,
    )
    db.add(guide)
    db.commit()
    db.refresh(guide)
    return _guide_out(guide)


@router.get("/staff-guides/{guide_id}/view")
def view_staff_guide(
    guide_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    guide = db.get(StaffGuide, guide_id)
    if not guide:
        raise HTTPException(status_code=404, detail="Staff guide not found.")
    filename = guide.filename.replace('"', "")
    return Response(
        content=guide.content_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.delete("/staff-guides/{guide_id}")
def delete_staff_guide(
    guide_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN)),
):
    guide = db.get(StaffGuide, guide_id)
    if not guide:
        raise HTTPException(status_code=404, detail="Staff guide not found.")
    db.delete(guide)
    db.commit()
    return {"ok": True}
