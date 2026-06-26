"""
Cloud Function: Graph subscription renewal (self-healing).

Triggered by Cloud Scheduler every 2 days. Renews the inbox-messages Graph
subscription before it expires (max lifetime: 4,230 minutes ~= 3 days). If the
subscription has already expired (Graph returns 404), or no ID is stored yet,
it registers a fresh subscription and writes the new ID back to Secret Manager
so the next run renews the replacement.

Required env vars:
  GCP_PROJECT_ID           - GCP project (Secret Manager access)
  WEBHOOK_URL              - webhook CF URL to register new subscriptions against
  MSAL_SECRET_NAME         - optional; defaults to msal-token-cache
  SUBSCRIPTION_SECRET_NAME - optional; defaults to graph-subscription-id
  WEBHOOK_CLIENT_STATE     - optional; defaults to inbox-webhook (must match webhook CF)
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone

import functions_framework
import msal
import requests
from google.api_core import exceptions as gcp_exceptions
from google.cloud import secretmanager

logger = logging.getLogger(__name__)


def _load_msal_token() -> str:
    project_id = os.environ["GCP_PROJECT_ID"]
    secret_name = os.environ.get("MSAL_SECRET_NAME", "msal-token-cache")
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
    return client.access_secret_version(request={"name": name}).payload.data.decode()


def _save_msal_token(serialized: str) -> None:
    project_id = os.environ["GCP_PROJECT_ID"]
    secret_name = os.environ.get("MSAL_SECRET_NAME", "msal-token-cache")
    client = secretmanager.SecretManagerServiceClient()
    parent = f"projects/{project_id}/secrets/{secret_name}"
    client.add_secret_version(request={"parent": parent, "payload": {"data": serialized.encode()}})


def _load_subscription_id() -> str:
    project_id = os.environ["GCP_PROJECT_ID"]
    secret_name = os.environ.get("SUBSCRIPTION_SECRET_NAME", "graph-subscription-id")
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
    try:
        return client.access_secret_version(request={"name": name}).payload.data.decode().strip()
    except gcp_exceptions.NotFound:
        return ""  # no version yet -> bootstrap path registers a fresh subscription


def _save_subscription_id(subscription_id: str) -> None:
    project_id = os.environ["GCP_PROJECT_ID"]
    secret_name = os.environ.get("SUBSCRIPTION_SECRET_NAME", "graph-subscription-id")
    client = secretmanager.SecretManagerServiceClient()
    parent = f"projects/{project_id}/secrets/{secret_name}"
    client.add_secret_version(
        request={"parent": parent, "payload": {"data": subscription_id.encode()}}
    )


def _get_access_token() -> str:
    authority = f"https://login.microsoftonline.com/{os.environ['TENANT_ID']}"
    scopes = [
        "https://graph.microsoft.com/Mail.Read",
        "https://graph.microsoft.com/Mail.ReadWrite",
        "https://graph.microsoft.com/User.Read",
    ]

    cache = msal.SerializableTokenCache()
    cache.deserialize(_load_msal_token())

    app = msal.PublicClientApplication(
        os.environ["CLIENT_ID"],
        authority=authority,
        token_cache=cache,
    )

    accounts = app.get_accounts()
    if not accounts:
        raise RuntimeError("No accounts in MSAL token cache")

    result = app.acquire_token_silent(scopes, account=accounts[0])
    if not result or "access_token" not in result:
        raise RuntimeError(f"Silent token refresh failed: {result}")

    if cache.has_state_changed:
        _save_msal_token(cache.serialize())

    return result["access_token"]


def _expiry() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S.0000000Z")


def _patch_subscription(subscription_id: str, token: str) -> requests.Response:
    return requests.patch(
        f"https://graph.microsoft.com/v1.0/subscriptions/{subscription_id}",
        json={"expirationDateTime": _expiry()},
        headers={"Authorization": f"Bearer {token}"},
    )


def _register_subscription(token: str) -> dict:
    resp = requests.post(
        "https://graph.microsoft.com/v1.0/subscriptions",
        json={
            "changeType": "created",
            "notificationUrl": os.environ["WEBHOOK_URL"],
            "resource": "me/mailFolders/inbox/messages",
            "expirationDateTime": _expiry(),
            "clientState": os.environ.get("WEBHOOK_CLIENT_STATE", "inbox-webhook"),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    if not resp.ok:
        logger.error("Graph POST /subscriptions returned %d: %s", resp.status_code, resp.text)
        resp.raise_for_status()
    return resp.json()


def _renew_or_register(subscription_id: str, token: str) -> dict:
    if not subscription_id:
        logger.warning("No subscription ID on file -- registering a new subscription")
        sub = _register_subscription(token)
        _save_subscription_id(sub["id"])
        logger.info("Registered subscription %s (expires %s)", sub["id"], sub.get("expirationDateTime"))
        return sub

    resp = _patch_subscription(subscription_id, token)
    if resp.status_code == 404:
        logger.warning("Subscription %s not found (404) -- registering a replacement", subscription_id)
        sub = _register_subscription(token)
        _save_subscription_id(sub["id"])
        logger.info(
            "Registered replacement subscription %s (expires %s)",
            sub["id"], sub.get("expirationDateTime"),
        )
        return sub
    if not resp.ok:
        logger.error("Graph PATCH %s returned %d: %s", subscription_id, resp.status_code, resp.text)
        resp.raise_for_status()

    logger.info("Renewed subscription %s -- new expiry: %s", subscription_id, resp.json().get("expirationDateTime"))
    return resp.json()


@functions_framework.http
def renew(request):
    token = _get_access_token()
    subscription_id = _load_subscription_id()
    sub = _renew_or_register(subscription_id, token)
    return json.dumps(sub), 200, {"Content-Type": "application/json"}
