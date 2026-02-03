from __future__ import annotations

from datetime import datetime, timedelta, timezone, date
import logging
from typing import Optional, Set, List

from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.config import settings
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


def _exclude_from_me_query() -> str | None:
    """Build a Gmail search snippet to exclude messages sent by us.

    This reduces candidate threads we need to inspect, but we still compute the
    final 'awaiting reply' state by inspecting the thread.
    """
    my_emails = [e.strip().lower() for e in settings.my_emails_list() if e.strip()]
    if not my_emails:
        return None
    if len(my_emails) == 1:
        return f"-from:{my_emails[0]}"
    joined = " OR ".join(my_emails)
    return f"-from:({joined})"

def _exclude_from_me_query() -> str | None:
    """Build a Gmail search snippet to exclude messages sent by us.

    This reduces candidate threads we need to inspect, but we still compute the
    final 'awaiting reply' state by inspecting the thread.
    """
    my_emails = [e.strip().lower() for e in settings.my_emails_list() if e.strip()]
    if not my_emails:
        return None
    if len(my_emails) == 1:
        return f"-from:{my_emails[0]}"
    joined = " OR ".join(my_emails)
    return f"-from:({joined})"

def _exclude_from_me_query() -> str | None:
    """Build a Gmail search snippet to exclude messages sent by us.

    This reduces candidate threads we need to inspect, but we still compute the
    final 'awaiting reply' state by inspecting the thread.
    """
    my_emails = [e.strip().lower() for e in settings.my_emails_list() if e.strip()]
    if not my_emails:
        return None
    if len(my_emails) == 1:
        return f"-from:{my_emails[0]}"
    joined = " OR ".join(my_emails)
    return f"-from:({joined})"

def _exclude_from_me_query() -> str | None:
    """Build a Gmail search snippet to exclude messages sent by us.

    This reduces candidate threads we need to inspect, but we still compute the
    final 'awaiting reply' state by inspecting the thread.
    """
    my_emails = [e.strip().lower() for e in settings.my_emails_list() if e.strip()]
    if not my_emails:
        return None
    if len(my_emails) == 1:
        return f"-from:{my_emails[0]}"
    # Gmail supports groups via parentheses; use OR.
    joined = " OR ".join(my_emails)
    return f"-from:({joined})"


def _exclude_from_me_query() -> str | None:
    """Build a Gmail search snippet to exclude messages sent by us.

    This reduces candidate threads we need to inspect, but we still compute the
    final "awaiting reply" state by inspecting the thread.
    """
    my_emails = [e.strip().lower() for e in settings.my_emails_list() if e.strip()]
    if not my_emails:
        return None
    if len(my_emails) == 1:
        return f"-from:{my_emails[0]}"
    joined = " OR ".join(my_emails)
    return f"-from:({joined})"


def _exclude_from_me_query() -> str | None:
    """Build a Gmail search snippet to exclude messages sent by us.

    This reduces candidate threads we need to inspect, but we still compute the
    final 'awaiting reply' state by inspecting the thread.
    """
    my_emails = [e.strip().lower() for e in settings.my_emails_list() if e.strip()]
    if not my_emails:
        return None
    if len(my_emails) == 1:
        return f"-from:{my_emails[0]}"
    joined = " OR ".join(my_emails)
    return f"-from:({joined})"


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


def _upsert_ticket_from_thread(db: Session, service, thread_id: str, *, awaiting_only: bool = True, auto_triage: bool = True) -> bool:
    """Fetch thread metadata and upsert a ThreadTicket row.

    Returns True if ticket was updated/created, False if skipped (e.g., blacklisted, or not awaiting reply when awaiting_only=True).

    Awaiting reply logic (high accuracy):
      - Determine inbound vs outbound per message.
      - Outbound if message is in SENT or From matches any configured MY_EMAILS.
      - Awaiting reply if last inbound timestamp is newer than last outbound timestamp.

    Notes:
      - This is only as "airtight" as your MY_EMAILS list. Add all aliases/shared mailbox addresses there.
    """
    th = service.users().threads().get(
        userId=gmail_user_id(),
        id=thread_id,
        format='metadata',
        metadataHeaders=['From', 'Subject', 'Date', 'Message-ID', 'In-Reply-To', 'References'],
    ).execute()

    messages = th.get('messages', []) or []
    if not messages:
        return False

    # Compute awaiting-reply state across the whole thread.
    last_inbound_at = None
    last_outbound_at = None
    last_msg = messages[-1]

    for m in messages:
        payload = m.get('payload') or {}
        headers = payload.get('headers') or []
        from_h = _get_header(headers, 'From')

        internal_ms = m.get('internalDate')
        dt = None
        if internal_ms:
            try:
                dt = datetime.fromtimestamp(int(internal_ms) / 1000, tz=timezone.utc).replace(tzinfo=None)
            except Exception:
                dt = None

        label_ids = set(m.get('labelIds') or [])
        outbound = ('SENT' in label_ids) or bool(is_from_me(from_h))
        if dt is None:
            continue
        if outbound:
            if (last_outbound_at is None) or (dt > last_outbound_at):
                last_outbound_at = dt
        else:
            if (last_inbound_at is None) or (dt > last_inbound_at):
                last_inbound_at = dt

    awaiting_reply = bool(last_inbound_at) and (last_outbound_at is None or last_inbound_at > last_outbound_at)

    # Extract last message metadata for display.
    last_msg_id = last_msg.get('id')
    payload = last_msg.get('payload') or {}
    headers = payload.get('headers') or []

    from_h = _get_header(headers, 'From')
    subject = _get_header(headers, 'Subject') or '(no subject)'
    snippet = last_msg.get('snippet') or ''
    is_unread = any('UNREAD' in (m.get('labelIds') or []) for m in messages)

    internal_ms = last_msg.get('internalDate')
    last_dt = None
    if internal_ms:
        last_dt = datetime.fromtimestamp(int(internal_ms) / 1000, tz=timezone.utc).replace(tzinfo=None)

    from_name, from_email = parse_email_address(from_h)
    if from_email:
        is_blacklisted = db.query(BlacklistedSender).filter(BlacklistedSender.email == from_email.lower()).first() is not None
        if is_blacklisted:
            return False

    ticket = db.get(ThreadTicket, thread_id)

    # If we only want awaiting-reply threads, do not create new tickets for threads that do not need a reply.
    if ticket is None and awaiting_only and not awaiting_reply:
        return False

    if ticket is None:
        ticket = ThreadTicket(thread_id=thread_id, status=TicketStatus.PENDING)
        db.add(ticket)

    ticket.last_message_id = last_msg_id
    ticket.subject = subject
    ticket.snippet = snippet
    ticket.last_message_at = last_dt
    ticket.is_unread = bool(is_unread)

    # last message outbound?
    last_label_ids = set(last_msg.get('labelIds') or [])
    ticket.last_from_me = bool(('SENT' in last_label_ids) or is_from_me(from_h))

    ticket.from_name = from_name
    ticket.from_email = from_email

    if not ticket.priority:
        ticket.priority = 'medium'
    if last_dt:
        days = {'high': 0, 'medium': 2, 'low': 3}.get(ticket.priority, 2)
        ticket.due_at = (last_dt + timedelta(days=days))

    ticket.is_not_replied = bool(awaiting_reply)

    # Keep status aligned with awaiting_reply, but do not overwrite NO_REPLY_NEEDED.
    if awaiting_reply:
        if ticket.status == TicketStatus.RESPONDED:
            ticket.status = TicketStatus.PENDING
    else:
        if ticket.status not in (TicketStatus.NO_REPLY_NEEDED,):
            ticket.status = TicketStatus.RESPONDED

    # --- Automatic AI categorisation (runs only when awaiting reply and content changed) ---
    if auto_triage and awaiting_reply:
        try:
            # Pull last message body (plain text best-effort) for better triage.
            last_body = ''
            if last_msg_id:
                msg_full = service.users().messages().get(userId=gmail_user_id(), id=last_msg_id, format='full').execute()
                pl = msg_full.get('payload') or {}
                # Reuse existing decoder from gmail_threads
                from app.services.gmail_threads import _decode_body  # local import to avoid circular
                last_body = (_decode_body(pl) or '')

            from app.services.ai_assistant import triage_email, content_hash
            h = content_hash(subject or '', snippet or '', (last_body or '')[:4000])
            if ticket.ai_source_hash != h:
                tri = triage_email(subject or '', snippet or '', (last_body or '')[:4000])
                ticket.ai_category = tri.ai_category
                ticket.category = tri.ticket_category
                ticket.ai_summary = tri.summary
                ticket.ai_reasons = "\n".join(tri.reasons or [])
                ticket.ai_source_hash = h
                ticket.ai_last_scored_at = datetime.utcnow()
                # We are not using urgency/confidence in UI anymore.
                ticket.ai_urgency = None
                ticket.ai_confidence = None
        except Exception:
            # Never fail sync due to AI.
            pass

    ticket.updated_at = datetime.utcnow()
    return True


def sync_inbox_threads(
    max_threads: int = 500,
    start: str | None = None,
    end: str | None = None,
    incremental: bool = True,
    include_anywhere: bool = False,
    awaiting_only: bool = True,
    auto_triage: bool = True,
) -> dict:
    """Synchronize Gmail INBOX threads into the local DB.

    Modes:
      - Date range provided: fetch threads in that range with pagination (accurate up to max_threads).
        If include_anywhere=True, includes archived mail via Gmail search `in:anywhere`.
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
                        # Pull last 30 days to rebuild state. This matches the product requirement
                        # ("check emails up to a month") and avoids missing active unreplied threads.
                        recent_start = (date.today() - timedelta(days=30)).isoformat()
                        thread_ids, hit_limit = _list_thread_ids_in_range(service, start=recent_start, end=None, max_threads=max_threads, include_anywhere=False)
                    else:
                        raise
            else:
                # First sync: pull a recent window (30 days) and set watermark.
                recent_start = (date.today() - timedelta(days=30)).isoformat()
                thread_ids, hit_limit = _list_thread_ids_in_range(service, start=recent_start, end=None, max_threads=max_threads, include_anywhere=False)

        upserted = 0
        skipped = 0
        for tid in thread_ids:
            try:
                if _upsert_ticket_from_thread(db, service, tid, awaiting_only=awaiting_only, auto_triage=auto_triage):
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
            "awaiting_only": bool(awaiting_only),
            "auto_triage": bool(auto_triage),
        }
    finally:
        db.close()
