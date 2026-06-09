from datetime import datetime
from typing import Optional, TypedDict


class Message(TypedDict):
    id: Optional[str]           # internal UUID; None until inserted
    source: str                 # "email" | "sms" | "voicemail"
    external_id: str            # provider's message ID
    sender: str                 # email address or phone number
    sender_display: str         # human-readable name
    subject: str                # "" if absent (never None)
    body: str                   # plain text
    received_at: datetime
    thread_id: Optional[str]
    raw: dict                   # original provider payload
    web_link: Optional[str]     # Outlook web URL (from Graph webLink property)
