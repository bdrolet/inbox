import logging
import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/emails")

_bearer = HTTPBearer(auto_error=False)


def _verify_token(credentials: HTTPAuthorizationCredentials | None = Security(_bearer)) -> None:
    expected = os.environ.get("SEARCH_TOKEN")
    if not expected:
        return
    if credentials is None or credentials.credentials != expected:
        raise HTTPException(status_code=401)


class Recipient(BaseModel):
    name: str | None = None
    address: str | None = None


class EmailDetailResponse(BaseModel):
    id: str | None = None
    subject: str | None = None
    from_email: str | None = None
    from_name: str | None = None
    to: list[Recipient] = []
    cc: list[Recipient] = []
    received_at: datetime | str | None = None
    sent_at: datetime | str | None = None
    body: str | None = None
    body_type: str | None = None
    has_attachments: bool = False
    web_link: str | None = None


class AttachmentItem(BaseModel):
    id: str | None = None
    name: str | None = None
    content_type: str | None = None
    size: int | None = None
    is_inline: bool = False
    content_bytes: str | None = None


class AttachmentsResponse(BaseModel):
    attachments: list[AttachmentItem]


def _get_client():
    from clients.azure import GraphEmailClient

    client = GraphEmailClient()
    if not client.authenticate_headless():
        logger.error("emails: Graph authentication failed")
        raise HTTPException(status_code=503, detail="authentication failed")
    return client


@router.get("/{message_id}", response_model=EmailDetailResponse)
def get_email(message_id: str, _: None = Depends(_verify_token)) -> EmailDetailResponse:
    client = _get_client()
    email = client.get_email_details(message_id)
    if email is None:
        raise HTTPException(status_code=404, detail="message not found")

    return EmailDetailResponse(
        id=email.id,
        subject=email.subject,
        from_email=email.from_email,
        from_name=email.from_name,
        to=[Recipient(name=r.get("name"), address=r.get("address")) for r in email.to_recipients],
        cc=[Recipient(name=r.get("name"), address=r.get("address")) for r in email.cc_recipients],
        received_at=email.received_datetime,
        sent_at=email.sent_datetime,
        body=email.body_content,
        body_type=email.body_type,
        has_attachments=email.has_attachments,
        web_link=email.web_link,
    )


@router.get("/{message_id}/attachments", response_model=AttachmentsResponse)
def get_attachments(message_id: str, _: None = Depends(_verify_token)) -> AttachmentsResponse:
    client = _get_client()
    raw = client.get_attachments(message_id)

    return AttachmentsResponse(
        attachments=[
            AttachmentItem(
                id=a.get("id"),
                name=a.get("name"),
                content_type=a.get("contentType"),
                size=a.get("size"),
                is_inline=a.get("isInline", False),
                content_bytes=a.get("contentBytes"),
            )
            for a in raw
        ]
    )
