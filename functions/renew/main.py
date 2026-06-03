"""
Cloud Function: Graph subscription renewal.

Triggered by Cloud Scheduler every 2 days. Renews the inbox-messages
Graph subscription before it expires (max lifetime: 4,230 minutes ≈ 3 days).

Required env vars:
  GCP_PROJECT_ID        — GCP project (used to load MSAL cache from Secret Manager)
  GRAPH_SUBSCRIPTION_ID — ID returned when the subscription was first registered
                          (set this after running clients/graph_subscriptions.py register)
  MSAL_SECRET_NAME      — optional; defaults to msal-token-cache
"""
import json
import logging
import os
from datetime import datetime, timezone, timedelta

import functions_framework
import msal
import requests
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
    client.add_secret_version(
        request={"parent": parent, "payload": {"data": serialized.encode()}}
    )


def _get_access_token() -> str:
    authority = f"https://login.microsoftonline.com/{os.environ['TENANT_ID']}"
    scopes = ["https://graph.microsoft.com/Mail.Read",
              "https://graph.microsoft.com/Mail.ReadWrite",
              "https://graph.microsoft.com/User.Read"]

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


@functions_framework.http
def renew(request):
    subscription_id = os.environ.get("GRAPH_SUBSCRIPTION_ID")
    if not subscription_id:
        logger.error("GRAPH_SUBSCRIPTION_ID not set")
        return "GRAPH_SUBSCRIPTION_ID not set", 500

    expiry = (datetime.now(timezone.utc) + timedelta(days=3)).strftime(
        "%Y-%m-%dT%H:%M:%S.0000000Z"
    )
    logger.info("Renewing subscription %s until %s", subscription_id, expiry)

    token = _get_access_token()
    resp = requests.patch(
        f"https://graph.microsoft.com/v1.0/subscriptions/{subscription_id}",
        json={"expirationDateTime": expiry},
        headers={"Authorization": f"Bearer {token}"},
    )
    if not resp.ok:
        logger.error("Graph PATCH %s returned %d: %s", subscription_id, resp.status_code, resp.text)
        resp.raise_for_status()

    logger.info("Subscription renewed — new expiry: %s", resp.json().get("expirationDateTime"))
    return json.dumps(resp.json()), 200, {"Content-Type": "application/json"}
