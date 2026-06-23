import logging
import os
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()

_bearer = HTTPBearer(auto_error=False)


def _verify_token(credentials: HTTPAuthorizationCredentials | None = Security(_bearer)) -> None:
    expected = os.environ.get("SEARCH_TOKEN")
    if not expected:
        return
    if credentials is None or credentials.credentials != expected:
        raise HTTPException(status_code=401)


class SearchRequest(BaseModel):
    query: str
    mode: Literal["graph", "db"] = "graph"
    mailboxes: list[str] | None = None
    limit: int = Field(default=25, ge=1, le=100)


class SearchResult(BaseModel):
    subject: str | None = None
    sender: str | None = None
    sender_display: str | None = None
    received_at: datetime | str | None = None
    preview: str | None = None
    mailbox: str | None = None
    web_link: str | None = None
    category: str | None = None
    importance: str | None = None


class SearchResponse(BaseModel):
    results: list[SearchResult]


@router.post("/search", response_model=SearchResponse)
def search(body: SearchRequest, _: None = Depends(_verify_token)) -> SearchResponse:
    query = body.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")

    if body.mode == "db":
        from clients.db import get_conn
        from repo.messages import search_messages

        conn = get_conn()
        try:
            rows = search_messages(conn, query, limit=body.limit)
        finally:
            conn.close()

        return SearchResponse(
            results=[
                SearchResult(
                    subject=r.get("subject"),
                    sender=r.get("sender"),
                    sender_display=r.get("sender_display"),
                    received_at=r.get("received_at"),
                    mailbox="db",
                    category=r.get("category"),
                    importance=r.get("importance"),
                )
                for r in rows
            ]
        )

    # Graph mode
    from clients.azure import GraphEmailClient

    client = GraphEmailClient()
    if not client.authenticate_headless():
        logger.error("search: Graph authentication failed")
        raise HTTPException(status_code=503, detail="authentication failed")

    if body.mailboxes is not None:
        mailboxes = body.mailboxes
    else:
        shared = [m.strip() for m in os.environ.get("SHARED_MAILBOXES", "").split(",") if m.strip()]
        mailboxes = ["me"] + shared

    collected: list[tuple] = []

    for mailbox in mailboxes:
        for email in client.search_emails(query, mailbox=mailbox, limit=body.limit):
            collected.append((email, mailbox))

    for group in client.get_member_groups():
        label = f"group:{group['mail'] or group['id']}"
        for email in client.search_group_conversations(group["id"], query, limit=body.limit):
            collected.append((email, label))

    # Deduplicate on (subject, sender_email, minute-truncated received_at)
    seen: set = set()
    unique: list[tuple] = []
    for email, source in collected:
        received = email.received_datetime
        bucket = (
            received.replace(second=0, microsecond=0).isoformat()
            if isinstance(received, datetime)
            else str(received)
        )
        key = (email.subject, email.from_email, bucket)
        if key not in seen:
            seen.add(key)
            unique.append((email, source))

    unique.sort(
        key=lambda item: (
            item[0].received_datetime
            if isinstance(item[0].received_datetime, datetime)
            else datetime.min.replace(tzinfo=timezone.utc)
        ),
        reverse=True,
    )
    unique = unique[: body.limit]

    return SearchResponse(
        results=[
            SearchResult(
                subject=email.subject,
                sender=email.from_email,
                sender_display=email.from_name,
                received_at=email.received_datetime,
                preview=email.body_preview,
                mailbox=source,
                web_link=email.web_link,
            )
            for email, source in unique
        ]
    )
