"""Read a message or group conversation given a search-result mailbox label.

Owns the mailbox-label policy: parses the `mailbox` label that /search
returns ("me", a shared-mailbox address, or "group:{mail-or-id}"), resolves
group labels to a group ID via the user's memberships, and normalizes group
conversations into the same Email shape as single messages.
"""
import logging
from dataclasses import dataclass

from clients.azure.email import Email
from clients.graph import get_graph_client

logger = logging.getLogger(__name__)

_GROUP_PREFIX = "group:"


@dataclass(frozen=True)
class FetchedEmail:
    email: Email
    posts: list[dict] | None = None  # group conversations only, oldest first


def _resolve_group_id(client, value: str) -> str:
    """Match a group mail address or ID against the user's groups."""
    needle = value.strip().lower()
    for group in client.get_member_groups():
        if group["id"].lower() == needle or (group.get("mail") or "").lower() == needle:
            return group["id"]
    raise LookupError(f"unknown group: {value}")


def _conversation_to_fetched(conversation_id: str, convo: dict) -> FetchedEmail:
    posts = sorted(convo["posts"], key=lambda p: p.get("receivedDateTime") or "")
    latest = posts[-1] if posts else {}
    # Synthetic Email: top-level fields mirror the latest post. Author
    # precedence is `from` then `sender` — the Email class and Outlook both
    # treat `from` as the display author, with `sender` the delegate-scenario
    # fallback.
    synthetic = {
        "id": conversation_id,
        "subject": convo.get("topic", ""),
        "from": latest.get("from") or latest.get("sender") or {},
        "toRecipients": [],
        "receivedDateTime": convo.get("lastDeliveredDateTime"),
        "sentDateTime": latest.get("receivedDateTime"),
        "body": latest.get("body", {}),
        "hasAttachments": any(p.get("hasAttachments") for p in posts),
        "webLink": None,  # Graph has no webLink for group posts
    }
    return FetchedEmail(email=Email(synthetic), posts=posts)


def fetch_email(message_id: str, mailbox: str = "me") -> FetchedEmail | None:
    """Fetch one message (primary/shared mailbox) or group conversation."""
    client = get_graph_client()
    if mailbox.startswith(_GROUP_PREFIX):
        group_id = _resolve_group_id(client, mailbox[len(_GROUP_PREFIX):])
        convo = client.get_group_conversation(group_id, message_id)
        if convo is None:
            return None
        return _conversation_to_fetched(message_id, convo)

    email = client.get_email_details(message_id, mailbox=mailbox)
    if email is None:
        return None
    return FetchedEmail(email=email)


def fetch_attachments(message_id: str, mailbox: str = "me") -> list[dict]:
    """Fetch attachments; for groups, aggregate across the conversation's posts."""
    client = get_graph_client()
    if not mailbox.startswith(_GROUP_PREFIX):
        return client.get_attachments(message_id, mailbox=mailbox)

    group_id = _resolve_group_id(client, mailbox[len(_GROUP_PREFIX):])
    convo = client.get_group_conversation(group_id, message_id)
    if convo is None:
        raise LookupError("message not found")
    attachments: list[dict] = []
    for post in convo["posts"]:
        if not post.get("hasAttachments"):
            continue
        for att in client.get_group_post_attachments(group_id, post["threadId"], post["id"]):
            attachments.append({**att, "postId": post["id"]})
    return attachments
