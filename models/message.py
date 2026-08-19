from datetime import datetime
from typing import Optional, TypedDict


class Message(TypedDict):
    id: Optional[str]  # internal UUID; None until inserted
    source: str  # "email" | "sms" | "voicemail"
    external_id: str  # provider's message ID
    sender: str  # email address or phone number
    sender_display: str  # human-readable name
    subject: str  # "" if absent (never None)
    to: list[str]  # recipient addresses
    cc: list[str]  # cc addresses
    body: str  # plain text
    body_html: Optional[str]  # raw HTML body when content type is html, else None
    received_at: datetime
    thread_id: Optional[str]
    raw: dict  # original provider payload
    web_link: Optional[str]  # Outlook web URL (from Graph webLink property)
    has_attachments: bool  # Graph hasAttachments — schedule uses it to decide whether to fetch .ics
    is_meeting_message: bool  # Graph @odata.type starts with #microsoft.graph.eventMessage (Exchange-native meeting request/cancel/response, no .ics)
