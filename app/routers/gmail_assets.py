from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from app.services.gmail_client import get_gmail_service
from app.services.gmail_parse import build_cid_attachment_map  # we’ll add below

router = APIRouter(prefix="/gmail", tags=["gmail-assets"])

@router.get("/inline-image")
def inline_image(msg_id: str, cid: str):
    service = get_gmail_service()
    gmail_user = "me"

    msg = service.users().messages().get(userId=gmail_user, id=msg_id, format="full").execute()
    cid_map = build_cid_attachment_map(msg.get("payload", {}))

    if cid not in cid_map:
        raise HTTPException(status_code=404, detail="CID not found")

    info = cid_map[cid]
    att = service.users().messages().attachments().get(
        userId=gmail_user, messageId=msg_id, id=info["attachmentId"]
    ).execute()

    data_b64 = att.get("data")
    if not data_b64:
        raise HTTPException(status_code=404, detail="Attachment data missing")

    import base64
    raw = base64.urlsafe_b64decode(data_b64.encode("utf-8"))

    return Response(content=raw, media_type=info["mimeType"])
