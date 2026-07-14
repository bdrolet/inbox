"""Build stable redirector URLs for the inbox-api /r/{uuid} endpoint."""

import os


def redirector_url(message_uuid: str) -> str | None:
    base = os.environ.get("REDIRECTOR_BASE_URL")
    if not base or not message_uuid:
        return None
    return f"{base.rstrip('/')}/r/{message_uuid}"
