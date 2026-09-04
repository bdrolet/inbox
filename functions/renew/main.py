"""
Cloud Function: Graph subscription renewal (self-healing).

Triggered by Cloud Scheduler every 2 days. Renews the Inbox and Sent Items
Graph subscriptions before they expire (max lifetime: 4,230 minutes ~= 3 days).
If a subscription has already expired (Graph returns 404), or no ID is stored
yet, it registers a fresh subscription and writes the new ID back to Secret
Manager so the next run renews the replacement.

Required env vars:
  GCP_PROJECT_ID                - GCP project (Secret Manager access)
  WEBHOOK_URL                   - webhook CF URL to register new subscriptions against
  MSAL_SECRET_NAME              - optional; defaults to msal-token-cache
  SUBSCRIPTION_SECRET_NAME      - optional; defaults to graph-subscription-id (Inbox)
  SENT_SUBSCRIPTION_SECRET_NAME - optional; defaults to graph-sent-subscription-id (Sent Items)
  WEBHOOK_CLIENT_STATE          - optional; defaults to inbox-webhook (must match webhook CF)
  WEBHOOK_CLIENT_STATE_SENT     - optional; defaults to inbox-webhook-sent (must match webhook CF)
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

# force=True installs a fresh stderr handler even though the gen2/gunicorn runtime
# configures the root logger before this module imports (otherwise basicConfig
# no-ops and app logs never reach Cloud Logging). See docs/otel-metrics-in-cloud-functions.md.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s", force=True)

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


def _subscriptions() -> list[dict]:
    """The Graph subscriptions this function keeps alive. Order matters only
    for logging. Env names match the webhook CF's."""
    return [
        {
            "resource": "me/mailFolders/inbox/messages",
            "client_state": os.environ.get("WEBHOOK_CLIENT_STATE", "inbox-webhook"),
            "secret_name": os.environ.get("SUBSCRIPTION_SECRET_NAME", "graph-subscription-id"),
        },
        {
            "resource": "me/mailFolders/sentitems/messages",
            "client_state": os.environ.get("WEBHOOK_CLIENT_STATE_SENT", "inbox-webhook-sent"),
            "secret_name": os.environ.get(
                "SENT_SUBSCRIPTION_SECRET_NAME", "graph-sent-subscription-id"
            ),
        },
    ]


def _load_subscription_id(sub: dict) -> str:
    project_id = os.environ["GCP_PROJECT_ID"]
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{sub['secret_name']}/versions/latest"
    try:
        return client.access_secret_version(request={"name": name}).payload.data.decode().strip()
    except gcp_exceptions.NotFound:
        return ""  # no version yet -> bootstrap path registers a fresh subscription


def _save_subscription_id(sub: dict, subscription_id: str) -> None:
    project_id = os.environ["GCP_PROJECT_ID"]
    client = secretmanager.SecretManagerServiceClient()
    parent = f"projects/{project_id}/secrets/{sub['secret_name']}"
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


def _list_subscriptions(token: str) -> list:
    resp = requests.get(
        "https://graph.microsoft.com/v1.0/subscriptions",
        headers={"Authorization": f"Bearer {token}"},
    )
    if not resp.ok:
        logger.error("Graph GET /subscriptions returned %d: %s", resp.status_code, resp.text)
        resp.raise_for_status()
    return resp.json().get("value", [])


def _create_subscription(sub: dict, token: str) -> dict:
    resp = requests.post(
        "https://graph.microsoft.com/v1.0/subscriptions",
        json={
            "changeType": "created",
            "notificationUrl": os.environ["WEBHOOK_URL"],
            "resource": sub["resource"],
            "expirationDateTime": _expiry(),
            "clientState": sub["client_state"],
        },
        headers={"Authorization": f"Bearer {token}", "Prefer": 'IdType="ImmutableId"'},
    )
    if not resp.ok:
        logger.error("Graph POST /subscriptions returned %d: %s", resp.status_code, resp.text)
        resp.raise_for_status()
    return resp.json()


def _register_subscription(sub: dict, token: str) -> dict:
    """Idempotent: reuse an existing subscription for our webhook+resource if one
    exists (e.g. a prior run registered but failed to persist the ID), else create
    a new one. Prevents orphaned duplicates from accumulating on partial failures."""
    webhook_url = os.environ["WEBHOOK_URL"]
    for existing in _list_subscriptions(token):
        if (
            existing.get("notificationUrl") == webhook_url
            and existing.get("resource") == sub["resource"]
        ):
            logger.info("Reusing existing subscription %s for %s", existing["id"], sub["resource"])
            return existing
    return _create_subscription(sub, token)


def _renew_or_register(sub: dict, subscription_id: str, token: str) -> dict:
    if not subscription_id:
        logger.warning("No subscription ID on file for %s -- registering", sub["resource"])
        created = _register_subscription(sub, token)
        _save_subscription_id(sub, created["id"])
        return created
    resp = _patch_subscription(subscription_id, token)
    if resp.status_code == 404:
        logger.warning("Subscription %s not found -- registering a replacement", subscription_id)
        created = _register_subscription(sub, token)
        _save_subscription_id(sub, created["id"])
        return created
    if not resp.ok:
        logger.error("Graph PATCH %s returned %d: %s", subscription_id, resp.status_code, resp.text)
        resp.raise_for_status()
    body = resp.json()
    logger.info(
        "Renewed %s (%s) -- expiry %s",
        subscription_id,
        sub["resource"],
        body.get("expirationDateTime"),
    )
    return body


@functions_framework.http
def renew(request):
    token = _get_access_token()
    results = [
        _renew_or_register(sub, _load_subscription_id(sub), token) for sub in _subscriptions()
    ]
    return json.dumps(results), 200, {"Content-Type": "application/json"}
