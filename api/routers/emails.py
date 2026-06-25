import logging
import os
from datetime import datetime
from typing import Literal

import requests
from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

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


# --------------------------------------------------------------------------- #
# Outbound (write) request/response models
# --------------------------------------------------------------------------- #
class FromMailbox(BaseModel):
    address: str | None = None
    shared: bool = False


class CreateDraftRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    to: list[str]
    cc: list[str] = []
    bcc: list[str] = []
    subject: str
    body: str
    body_type: Literal["Text", "HTML"] = "Text"
    from_: FromMailbox | None = Field(default=None, alias="from")


class AddAttachmentRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    name: str
    content_bytes: str  # base64-encoded file content
    content_type: str | None = None
    is_inline: bool = False
    from_: FromMailbox | None = Field(default=None, alias="from")


class SendMessageRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    to: list[str]
    cc: list[str] = []
    bcc: list[str] = []
    subject: str
    body: str
    body_type: Literal["Text", "HTML"] = "Text"
    from_: FromMailbox | None = Field(default=None, alias="from")


class SendDraftRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_: FromMailbox | None = Field(default=None, alias="from")


class DraftResponse(BaseModel):
    id: str | None = None
    web_link: str | None = None


class StatusResponse(BaseModel):
    status: str


def _from_parts(from_: FromMailbox | None) -> tuple[str | None, bool]:
    if from_ is None:
        return None, False
    return from_.address, from_.shared


def _call_graph(fn, *args, **kwargs):
    """Invoke a Graph client write method, mapping failures to HTTPExceptions.

    Graph 403 (permission) surfaces as 403 with Graph's detail; other Graph errors
    as 502; client-side validation errors (e.g. attachment too large) as 400.
    """
    try:
        return fn(*args, **kwargs)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except requests.exceptions.HTTPError as e:
        resp = e.response
        detail = resp.text[:1000] if resp is not None else str(e)
        status = resp.status_code if resp is not None else 502
        if status == 403:
            raise HTTPException(status_code=403, detail=detail) from e
        raise HTTPException(status_code=502, detail=detail) from e


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


@router.post("/drafts", response_model=DraftResponse)
def create_draft(req: CreateDraftRequest, _: None = Depends(_verify_token)) -> DraftResponse:
    client = _get_client()
    addr, shared = _from_parts(req.from_)
    created = _call_graph(
        client.create_draft,
        to=req.to,
        subject=req.subject,
        body=req.body,
        cc=req.cc,
        bcc=req.bcc,
        body_type=req.body_type,
        from_address=addr,
        from_shared=shared,
    )
    return DraftResponse(id=created.get("id"), web_link=created.get("webLink"))


@router.post("/drafts/{draft_id}/attachments", response_model=StatusResponse)
def add_attachment(
    draft_id: str, req: AddAttachmentRequest, _: None = Depends(_verify_token)
) -> StatusResponse:
    client = _get_client()
    addr, shared = _from_parts(req.from_)
    _call_graph(
        client.add_attachment,
        draft_id,
        req.name,
        req.content_bytes,
        req.content_type,
        from_address=addr,
        from_shared=shared,
        is_inline=req.is_inline,
    )
    return StatusResponse(status="attached")


@router.post("/drafts/{draft_id}/send", response_model=StatusResponse)
def send_draft(
    draft_id: str, req: SendDraftRequest | None = None, _: None = Depends(_verify_token)
) -> StatusResponse:
    client = _get_client()
    addr, shared = _from_parts(req.from_ if req else None)
    _call_graph(client.send_draft, draft_id, from_address=addr, from_shared=shared)
    return StatusResponse(status="sent")


@router.post("/send", response_model=StatusResponse)
def send_message(req: SendMessageRequest, _: None = Depends(_verify_token)) -> StatusResponse:
    client = _get_client()
    addr, shared = _from_parts(req.from_)
    _call_graph(
        client.send_message,
        to=req.to,
        subject=req.subject,
        body=req.body,
        cc=req.cc,
        bcc=req.bcc,
        body_type=req.body_type,
        from_address=addr,
        from_shared=shared,
    )
    return StatusResponse(status="sent")
