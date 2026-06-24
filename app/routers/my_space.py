from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.authz import get_current_user
from app.db import get_db
from app.models import MySpaceNote, MySpaceTodo, User

router = APIRouter(prefix="/my-space", tags=["my-space"])


class MySpaceTodoIn(BaseModel):
    title: str
    notes: str | None = None
    priority: str = "normal"
    due_at: datetime | None = None
    is_done: bool = False


class MySpaceTodoPatch(BaseModel):
    title: str | None = None
    notes: str | None = None
    priority: str | None = None
    due_at: datetime | None = None
    is_done: bool | None = None


class MySpaceNoteIn(BaseModel):
    body: str = ""


def _todo_out(todo: MySpaceTodo) -> dict:
    return {
        "id": todo.id,
        "title": todo.title,
        "notes": todo.notes,
        "priority": todo.priority,
        "due_at": todo.due_at,
        "is_done": todo.is_done,
        "created_at": todo.created_at,
        "updated_at": todo.updated_at,
        "completed_at": todo.completed_at,
    }


def _clean_priority(value: str | None) -> str:
    priority = (value or "normal").strip().lower()
    return priority if priority in {"low", "normal", "high"} else "normal"


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


@router.get("")
def get_my_space(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    todos = (
        db.query(MySpaceTodo)
        .filter(MySpaceTodo.user_id == user.id)
        .order_by(MySpaceTodo.is_done.asc(), MySpaceTodo.due_at.is_(None), MySpaceTodo.due_at.asc(), MySpaceTodo.created_at.desc())
        .all()
    )
    note = db.get(MySpaceNote, user.id)
    return {
        "todos": [_todo_out(todo) for todo in todos],
        "note": note.body if note else "",
    }


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
    if "due_at" in payload.model_fields_set:
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
