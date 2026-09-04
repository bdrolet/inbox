"""
Manage Microsoft Graph change-notification subscriptions for the inbox.

Usage (run once after the webhook Cloud Function is deployed):

    from clients.azure import GraphEmailClient
    from clients.graph_subscriptions import register

    client = GraphEmailClient()
    client.authenticate_interactive()  # or authenticate_headless()

    result = register(client, "https://<webhook-cf-url>")
    print(result["id"])   # the renew CF self-heals and stores the live ID in the graph-subscription-id Secret Manager secret

Graph subscriptions expire after ~3 days. The renewal Cloud Function
(functions/renew/main.py) handles automatic renewal via Cloud Scheduler.
"""

import os
from datetime import datetime, timedelta, timezone

import requests


def _expiry() -> str:
    dt = datetime.now(timezone.utc) + timedelta(days=3)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.0000000Z")


def register(
    client,
    notification_url: str,
    *,
    resource: str = "me/mailFolders/inbox/messages",
    client_state: str | None = None,
) -> dict:
    """Create a subscription (immutable IDs). resource/client_state default to
    the Inbox subscription; pass the Sent Items pair for the second one."""
    resp = requests.post(
        "https://graph.microsoft.com/v1.0/subscriptions",
        json={
            "changeType": "created",
            "notificationUrl": notification_url,
            "resource": resource,
            "expirationDateTime": _expiry(),
            "clientState": client_state or os.environ.get("WEBHOOK_CLIENT_STATE", "inbox-webhook"),
        },
        headers=client.get_headers(immutable=True),
    )
    resp.raise_for_status()
    return resp.json()


def renew(client, subscription_id: str) -> dict:
    """Extend an existing subscription by 3 days. Returns the updated subscription dict."""
    resp = requests.patch(
        f"https://graph.microsoft.com/v1.0/subscriptions/{subscription_id}",
        json={"expirationDateTime": _expiry()},
        headers=client.get_headers(),
    )
    resp.raise_for_status()
    return resp.json()


def delete(client, subscription_id: str) -> None:
    """Delete a subscription (e.g. during teardown)."""
    resp = requests.delete(
        f"https://graph.microsoft.com/v1.0/subscriptions/{subscription_id}",
        headers=client.get_headers(),
    )
    resp.raise_for_status()
