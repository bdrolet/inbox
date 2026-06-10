"""
Manage Microsoft Graph change-notification subscriptions for the inbox.

Usage (run once after the webhook Cloud Function is deployed):

    from clients.azure import GraphEmailClient
    from clients.graph_subscriptions import register

    client = GraphEmailClient()
    client.authenticate_interactive()  # or authenticate_headless()

    result = register(client, "https://<webhook-cf-url>")
    print(result["id"])   # save this as GRAPH_SUBSCRIPTION_ID env var

Graph subscriptions expire after ~3 days. The renewal Cloud Function
(functions/renew/main.py) handles automatic renewal via Cloud Scheduler.
"""

import os
from datetime import datetime, timedelta, timezone

import requests


def _expiry() -> str:
    dt = datetime.now(timezone.utc) + timedelta(days=3)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.0000000Z")


def register(client, notification_url: str) -> dict:
    """Create a new subscription. Returns the subscription dict (contains 'id')."""
    resp = requests.post(
        "https://graph.microsoft.com/v1.0/subscriptions",
        json={
            "changeType": "created",
            "notificationUrl": notification_url,
            "resource": "me/mailFolders/inbox/messages",
            "expirationDateTime": _expiry(),
            "clientState": os.environ.get("WEBHOOK_CLIENT_STATE", "inbox-webhook"),
        },
        headers=client.get_headers(),
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
