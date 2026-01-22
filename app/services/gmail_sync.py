from __future__ import annotations

from datetime import datetime, timedelta, timezone, date
import logging
from typing import Optional, Set, List

from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import ThreadTicket, TicketStatus, BlacklistedSender
from app.services.gmail_client import get_gmail_service, gmail_user_id, is_from_me, parse_email_address
from app.services.state import get_state, set_state

logger = logging.getLogger(__name__)


def _get_header(headers: List[dict], name: str) -> str | None:
    for h in headers or []:
        if (h.get("name") or "").lower() == name.lower():
            return h.get("value")
    return None


def _yyyymmdd_to_gmail(d: str) -> str:
    # Gmail search expects YYYY/MM/DD
    return d.replace("-", "/")


def _increment_day(d: str) -> str:
    # inclusive end-date UX; Gmail `before:` is exclusive, so use end+1
    y, m, dd = [int(x) for x in d.split("-")]
    return (date(y, m, dd) + timedelta(days=1)).isoformat()


def _thread_ids_from_history(service, start_history_id: str) -> Set[str]:
    """Return threadIds that changed since start_history_id for INBOX.

    This is the accurate way to do incremental sync without missing messages.
    """
    thread_ids: Set[str] = set()
    page_token = None
    while True:
        req = service.users().history().list(
            userId=gmail_user_id(),
            startHistoryId=start_history_id,
            historyTypes=["messageAdded", "labelAdded", "labelRemoved"],
            labelId="INBOX",
            maxResults=500,
            pageToken=page_token,
        )
        resp = req.execute()
        for h in resp.get("history", []) or []:
            # messagesAdded: [{message:{threadId,...}}]
            for ma in h.get("messagesAdded", []) or []:
                msg = (ma.get("message") or {})
                tid = msg.get("threadId")
                if tid:
                    thread_ids.add(tid)
            # labels can also move threads in/out of INBOX
            for m in h.get("messages", []) or []:
                tid = m.get("threadId")
                if tid:
                    thread_ids.add(tid)

        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return thread_ids



def _list_thread_ids_in_range(service, start: str | None, end: str | None, max_threads: int, include_anywhere: bool) -> tuple[List[str], bool]:
    """List thread ids for the given date range, paging until max_threads.

    Returns (thread_ids, hit_limit).
    - include_anywhere=True adds Gmail search 'in:anywhere' and does not restrict labelIds to INBOX.
    """
    q_parts: List[str] = []
    if include_anywhere:
        q_parts.append("in:anywhere")
    if start:
        q_parts.append(f"after:{_yyyymmdd_to_gmail(start)}")
    if end:
        # Gmail before: is exclusive; use end+1 day for inclusive UX
        q_parts.append(f"before:{_yyyymmdd_to_gmail(_increment_day(end))}")
    q = " ".join(q_parts) if q_parts else None

    thread_ids: List[str] = []
    page_token = None
    hit_limit = False

    while True:
        kwargs = {
            "userId": gmail_user_id(),
            "maxResults": min(500, max_threads - len(thread_ids)),
            "pageToken": page_token,
        }
        if q:
            kwargs["q"] = q
        if not include_anywhere:
            kwargs["labelIds"] = ["INBOX"]

        # If we've already hit the limit, stop.
        if kwargs["maxResults"] <= 0:
            hit_limit = True
            break

        resp = service.users().threads().list(**kwargs).execute()

        for t in resp.get("threads", []) or []:
            tid = t.get("id")
            if tid:
                thread_ids.append(tid)
            if len(thread_ids) >= max_threads:
                hit_limit = True
                break

        if hit_limit:
            break

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    return thread_ids, hit_limit


def _upsert_ticket_from_thread(db: Session, service, thread_id: str) -> bool:
    """Fetch thread metadata and upsert a ThreadTicket row.

    Returns True if ticket was updated/created, False if skipped (e.g., blacklisted).
    """
    th = service.users().threads().get(
        userId=gmail_user_id(),
        id=thread_id,
        format="metadata",
        metadataHeaders=["From", "Subject", "Date", "Message-ID", "In-Reply-To", "References"],
    ).execute()

    messages = th.get("messages", []) or []
    if not messages:
        return False

    last_msg = messages[-1]
    last_msg_id = last_msg.get("id")
    payload = last_msg.get("payload") or {}
    headers = payload.get("headers") or []

    from_h = _get_header(headers, "From")
    subject = _get_header(headers, "Subject") or "(no subject)"
    snippet = last_msg.get("snippet") or ""
    is_unread = any("UNREAD" in (m.get("labelIds") or []) for m in messages)

    internal_ms = last_msg.get("internalDate")
    last_dt = None
    if internal_ms:
        last_dt = datetime.fromtimestamp(int(internal_ms) / 1000, tz=timezone.utc).replace(tzinfo=None)

    from_name, from_email = parse_email_address(from_h)
    if from_email:
        is_blacklisted = db.query(BlacklistedSender).filter(BlacklistedSender.email == from_email.lower()).first() is not None
        if is_blacklisted:
            return False

    ticket = db.get(ThreadTicket, thread_id)
    if ticket is None:
        ticket = ThreadTicket(thread_id=thread_id, status=TicketStatus.PENDING)
        db.add(ticket)

    ticket.last_message_id = last_msg_id
    ticket.subject = subject
    ticket.snippet = snippet
    ticket.last_message_at = last_dt
    ticket.is_unread = bool(is_unread)
    ticket.last_from_me = bool(is_from_me(from_h))
    ticket.from_name = from_name
    ticket.from_email = from_email

    if not ticket.priority:
        ticket.priority = "medium"
    if last_dt:
        days = {"high": 0, "medium": 2, "low": 3}.get(ticket.priority, 2)
        ticket.due_at = (last_dt + timedelta(days=days))

    ticket.is_not_replied = (
        (not ticket.last_from_me)
        and ticket.status not in (TicketStatus.RESPONDED, TicketStatus.NO_REPLY_NEEDED)
    )

    ticket.updated_at = datetime.utcnow()
    return True


def sync_inbox_threads(
    max_threads: int = 500,
    start: str | None = None,
    end: str | None = None,
    incremental: bool = True,
    include_anywhere=False
) -> dict:
    """Synchronize Gmail INBOX threads into the local DB.

    Modes:
      - Date range provided: fetch threads in that range with pagination (accurate up to max_threads).
      - No date range: use Gmail historyId incremental sync (accurate) when available.

    max_threads is a safety cap to avoid very large pulls.
    """
    db: Session = SessionLocal()
    try:
        try:
            service = get_gmail_service(db)
        except RuntimeError as e:
            logger.info("Gmail sync skipped: %s", e)
            return {"ok": False, "error": str(e)}

        # Always read current historyId so we can advance the watermark at the end.
        profile = service.users().getProfile(userId=gmail_user_id()).execute()
        current_history_id = str(profile.get("historyId") or "").strip() or None

        thread_ids: List[str]
        used_history = False
        hit_limit = False

        if start or end:
            # Range sync via Gmail search.
            thread_ids, hit_limit = _list_thread_ids_in_range(service, start=start, end=end, max_threads=max_threads, include_anywhere=include_anywhere)
        else:
            # Daily/ongoing sync: prefer historyId (accurate, doesn't miss messages).
            last_history_id = get_state(db, "gmail_history_id")
            if incremental and last_history_id and current_history_id:
                try:
                    tids = _thread_ids_from_history(service, start_history_id=last_history_id)
                    thread_ids = list(tids)
                    if len(thread_ids) > max_threads:
                        thread_ids = thread_ids[:max_threads]
                        hit_limit = True
                    used_history = True
                except HttpError as he:
                    # If startHistoryId is too old, Gmail returns 404. Fall back to a small recent pull.
                    if he.resp is not None and getattr(he.resp, "status", None) == 404:
                        logger.warning("HistoryId too old; falling back to recent sync and resetting watermark.")
                        # Pull last 7 days to rebuild state. (This is a compromise, but avoids missing current work.)
                        recent_start = (date.today() - timedelta(days=7)).isoformat()
                        thread_ids, hit_limit = _list_thread_ids_in_range(service, start=recent_start, end=None, max_threads=max_threads, include_anywhere=False)
                    else:
                        raise
            else:
                # First sync: pull a recent window (7 days) and set watermark.
                recent_start = (date.today() - timedelta(days=7)).isoformat()
                thread_ids, hit_limit = _list_thread_ids_in_range(service, start=recent_start, end=None, max_threads=max_threads, include_anywhere=False)

        upserted = 0
        skipped = 0
        for tid in thread_ids:
            try:
                if _upsert_ticket_from_thread(db, service, tid):
                    upserted += 1
                else:
                    skipped += 1
            except HttpError as he:
                # If a single thread fails, continue; we want robustness.
                logger.warning("Failed to fetch thread %s: %s", tid, he)
                skipped += 1

        # Advance watermark only for the incremental (no-range) flow.
        if (not start and not end) and current_history_id:
            set_state(db, "gmail_history_id", current_history_id)

        db.commit()
        return {
            "ok": True,
            "threads_requested": len(thread_ids),
            "upserted": upserted,
            "skipped": skipped,
            "mode": "history" if used_history else ("range" if (start or end) else "recent"),
            "watermark": current_history_id,
            "hit_limit": hit_limit,
            "target_mailbox": gmail_user_id(),
            "include_anywhere": bool(include_anywhere),
        }
    finally:
        db.close()
