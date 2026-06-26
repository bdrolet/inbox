# Self-Healing Graph Subscription Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `inbox-renew` Cloud Function recover on its own when the Graph subscription has fully expired, instead of throwing `404` forever and silently halting email ingestion.

**Architecture:** Move the active subscription ID out of an immutable Terraform env var and into a mutable Secret Manager secret (`graph-subscription-id`) that the renew function both reads and writes — mirroring the existing `msal-token-cache` read-modify-write pattern. On each run the function PATCHes the stored subscription; if Graph returns `404 ResourceNotFound` (or the stored ID is empty), it `register()`s a fresh subscription and writes the new ID back to the secret, closing the loop so the next run renews the replacement.

**Tech Stack:** Python 3.11 (Cloud Functions Gen2 runtime), `functions_framework`, `msal`, `requests`, `google-cloud-secret-manager`, Terraform, pytest.

## Global Constraints

- **Branch:** Do this work on a dedicated branch off `main` (e.g. `feat/self-healing-subscription`). Do NOT build on the current `feat/outbound-email-api` branch — a Terraform apply from that branch would deploy unrelated unmerged code.
- **`functions/renew/main.py` stays standalone:** it must not import from `clients/`, `repo/`, `services/`, etc. It inlines its own Graph + MSAL + Secret Manager calls (existing convention). Keep it that way.
- **Runtime:** `python311` (matches `build_config.runtime` in `terraform/cloud_functions.tf`).
- **`clientState` must equal `inbox-webhook`** on any newly registered subscription — the webhook CF (`terraform/cloud_functions.tf:49`, `WEBHOOK_CLIENT_STATE = "inbox-webhook"`) rejects notifications whose `clientState` doesn't match.
- **Resource watched:** `me/mailFolders/inbox/messages`, **changeType:** `created` (must match the original registration in `clients/graph_subscriptions.py`).
- **Only `404` triggers re-registration.** Any other non-2xx PATCH response (401, 5xx, throttling) must raise — never register on those, or you leak duplicate subscriptions on transient errors.
- **Webhook URL:** `https://inbox-webhook-aizbgjlava-uc.a.run.app`, injected as env var `WEBHOOK_URL` (resolved in Terraform from `google_cloudfunctions2_function.webhook.service_config[0].uri`).
- **Terraform changes** are previewed/applied via the `/terraform-plan` and `/terraform-apply` skills (per `terraform/CLAUDE.md`), not hand-rolled.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `functions/renew/main.py` | Renewal entrypoint + self-heal logic | Modify |
| `tests/test_renew.py` | Unit tests for the self-heal branching | Create |
| `terraform/secrets.tf` | Add `graph-subscription-id` secret (self-managed, `ignore_changes`) | Modify |
| `terraform/iam.tf` | Grant `renew_cf` SA accessor + version-manager on the new secret | Modify |
| `terraform/cloud_functions.tf` | Drop `GRAPH_SUBSCRIPTION_ID` env; add `WEBHOOK_URL`, `SUBSCRIPTION_SECRET_NAME`, `WEBHOOK_CLIENT_STATE` | Modify |
| `CLAUDE.md` | Update the "Graph subscription" + "Secrets" sections | Modify |

---

## Task 1: Self-heal logic in the renew function (unit-tested)

**Files:**
- Modify: `functions/renew/main.py`
- Test: `tests/test_renew.py`

**Interfaces:**
- Produces:
  - `_load_subscription_id() -> str` — reads the `graph-subscription-id` secret's latest version; returns `""` if the secret has no version yet.
  - `_save_subscription_id(subscription_id: str) -> None` — adds a new secret version with the given ID.
  - `_expiry() -> str` — Graph expiry timestamp, now + 3 days, `...0000000Z` format.
  - `_patch_subscription(subscription_id: str, token: str) -> requests.Response` — PATCHes expiry; returns the raw response (caller inspects `status_code`).
  - `_register_subscription(token: str) -> dict` — POSTs a new subscription; raises on non-2xx; returns the subscription dict (contains `id`, `expirationDateTime`).
  - `_renew_or_register(subscription_id: str, token: str) -> dict` — orchestration: renew if possible, else register-and-persist.
  - `renew(request)` — HTTP entrypoint glue.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_renew.py`:

```python
"""Unit tests for the self-healing renew Cloud Function.

The renew module is a standalone Cloud Function that imports `functions_framework`,
which isn't a dependency of the main test venv. We stub it in sys.modules before
importing, then load the module straight from its file path.
"""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

# Stub functions_framework so the standalone CF module imports without the CF runtime.
if "functions_framework" not in sys.modules:
    ff = types.ModuleType("functions_framework")
    ff.http = lambda fn: fn  # identity decorator
    sys.modules["functions_framework"] = ff

_RENEW_PATH = Path(__file__).resolve().parents[1] / "functions" / "renew" / "main.py"
_spec = importlib.util.spec_from_file_location("renew_main", _RENEW_PATH)
renew_main = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(renew_main)


class _Resp:
    """Minimal stand-in for requests.Response."""

    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


def _fail(*_a, **_k):
    raise AssertionError("must not be called")


def test_renew_patches_existing_subscription(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        renew_main, "_patch_subscription",
        lambda sid, tok: _Resp(200, {"id": sid, "expirationDateTime": "2026-07-01T00:00:00Z"}),
    )
    monkeypatch.setattr(renew_main, "_register_subscription", lambda tok: calls.setdefault("registered", True))
    monkeypatch.setattr(renew_main, "_save_subscription_id", lambda sid: calls.setdefault("saved", sid))

    result = renew_main._renew_or_register("sub-123", "tok")

    assert result["id"] == "sub-123"
    assert "registered" not in calls  # must NOT re-register on a healthy subscription
    assert "saved" not in calls       # secret must NOT be rewritten on a plain renewal


def test_renew_registers_replacement_on_404(monkeypatch):
    saved = {}
    monkeypatch.setattr(renew_main, "_patch_subscription", lambda sid, tok: _Resp(404, text="ResourceNotFound"))
    monkeypatch.setattr(
        renew_main, "_register_subscription",
        lambda tok: {"id": "sub-new", "expirationDateTime": "2026-07-01T00:00:00Z"},
    )
    monkeypatch.setattr(renew_main, "_save_subscription_id", lambda sid: saved.update(id=sid))

    result = renew_main._renew_or_register("sub-dead", "tok")

    assert result["id"] == "sub-new"
    assert saved["id"] == "sub-new"  # new ID persisted for the next run


def test_register_when_no_subscription_id(monkeypatch):
    saved = {}
    monkeypatch.setattr(renew_main, "_patch_subscription", _fail)  # patch must never run
    monkeypatch.setattr(
        renew_main, "_register_subscription",
        lambda tok: {"id": "sub-boot", "expirationDateTime": "2026-07-01T00:00:00Z"},
    )
    monkeypatch.setattr(renew_main, "_save_subscription_id", lambda sid: saved.update(id=sid))

    result = renew_main._renew_or_register("", "tok")

    assert result["id"] == "sub-boot"
    assert saved["id"] == "sub-boot"


def test_non_404_error_does_not_register(monkeypatch):
    monkeypatch.setattr(renew_main, "_patch_subscription", lambda sid, tok: _Resp(401, text="Unauthorized"))
    monkeypatch.setattr(renew_main, "_register_subscription", _fail)  # never register on a 401
    monkeypatch.setattr(renew_main, "_save_subscription_id", _fail)

    with pytest.raises(RuntimeError):
        renew_main._renew_or_register("sub-123", "tok")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/src/inbox && .venv/bin/python -m pytest tests/test_renew.py -v`
Expected: FAIL — `AttributeError: module 'renew_main' has no attribute '_renew_or_register'` (the function doesn't exist yet).

- [ ] **Step 3: Implement the self-heal logic**

Edit `functions/renew/main.py`. Update the module docstring's "Required env vars" block and replace the helpers/entrypoint. The final file:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ~/src/inbox && .venv/bin/python -m pytest tests/test_renew.py -v`
Expected: PASS — all 4 tests green.

- [ ] **Step 5: Confirm the existing suite still passes**

Run: `cd ~/src/inbox && .venv/bin/python -m pytest -q`
Expected: PASS — `test_renew.py` + `test_classification.py`, no regressions.

- [ ] **Step 6: Add `google-api-core` to the function's requirements if absent**

`_load_subscription_id` catches `google.api_core.exceptions.NotFound`. `google-api-core` ships transitively with `google-cloud-secret-manager`, but pin it explicitly so the catch never breaks.

Check: `cat functions/renew/requirements.txt`
If `google-api-core` is not already listed, append a line: `google-api-core`

- [ ] **Step 7: Commit**

```bash
git add functions/renew/main.py functions/renew/requirements.txt tests/test_renew.py
git commit -m "feat: self-heal Graph subscription on 404 in renew CF"
```

---

## Task 2: Terraform — mutable subscription-ID secret, IAM, and env wiring

**Files:**
- Modify: `terraform/secrets.tf`
- Modify: `terraform/iam.tf`
- Modify: `terraform/cloud_functions.tf`

**Interfaces:**
- Consumes: `var.graph_subscription_id` (already defined in `terraform/variables.tf:42`, already set to the live ID `f58b30e4-4090-433a-87cc-fbe1f87f574a` in `terraform.tfvars`) — used to seed the secret's first version.
- Produces: Secret `graph-subscription-id` with `lifecycle.ignore_changes = [secret_data]`; IAM bindings `renew_cf_subscription_accessor` + `renew_cf_subscription_version_manager`; renew CF reading the secret instead of an env-var ID.

- [ ] **Step 1: Add the self-managed secret in `terraform/secrets.tf`**

The `local.secrets` map (around `secrets.tf:7`) and the `secrets_without_msal` filter (around `secrets.tf:22`) drive secret creation. Two edits:

1. Add the new key to `local.secrets` so the secret *container* is created:
```hcl
    "graph-subscription-id"           = var.graph_subscription_id
```

2. Broaden the auto-version exclusion so Terraform does NOT manage this secret's data after creation (mirroring msal). Replace the `secrets_without_msal` local:
```hcl
  # Secrets whose live value is updated at runtime (by the renew/process CFs) and
  # must not be overwritten by CI / Terraform after their initial seed.
  self_managed_secrets = ["msal-token-cache", "graph-subscription-id"]
  secrets_without_msal = { for k, v in local.secrets : k => v if !contains(local.self_managed_secrets, k) }
```

3. Add a seeded initial version with `ignore_changes`, right after the `msal_token_cache` version resource (around `secrets.tf:45-51`):
```hcl
# Separate resource so lifecycle.ignore_changes prevents CI/Terraform from
# overwriting the live subscription ID the renew CF writes on self-heal.
resource "google_secret_manager_secret_version" "graph_subscription_id" {
  secret      = google_secret_manager_secret.secrets["graph-subscription-id"].id
  secret_data = var.graph_subscription_id

  lifecycle {
    ignore_changes = [secret_data]
  }
}
```

> Note: `var.graph_subscription_id` must be non-empty at apply time (it is — `terraform.tfvars` holds the live ID). An empty payload is rejected by Secret Manager.

- [ ] **Step 2: Grant the renew SA access in `terraform/iam.tf`**

After the `renew_cf_msal_version_manager` binding (around `iam.tf:158-162`), add accessor + version-manager on the new secret:
```hcl
resource "google_secret_manager_secret_iam_member" "renew_cf_subscription_accessor" {
  secret_id = google_secret_manager_secret.secrets["graph-subscription-id"].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.renew_cf.email}"
}

resource "google_secret_manager_secret_iam_member" "renew_cf_subscription_version_manager" {
  secret_id = google_secret_manager_secret.secrets["graph-subscription-id"].secret_id
  role      = "roles/secretmanager.secretVersionManager"
  member    = "serviceAccount:${google_service_account.renew_cf.email}"
}
```

- [ ] **Step 3: Rewire the renew CF env in `terraform/cloud_functions.tf`**

In the `google_cloudfunctions2_function "renew"` block (`environment_variables`, around `cloud_functions.tf:127-132`), replace:
```hcl
    environment_variables = {
      GCP_PROJECT_ID = var.project_id
      # Set GRAPH_SUBSCRIPTION_ID after registering the subscription:
      #   terraform apply -var="graph_subscription_id=<id>"
      GRAPH_SUBSCRIPTION_ID = var.graph_subscription_id
    }
```
with:
```hcl
    environment_variables = {
      GCP_PROJECT_ID           = var.project_id
      WEBHOOK_URL              = google_cloudfunctions2_function.webhook.service_config[0].uri
      SUBSCRIPTION_SECRET_NAME = "graph-subscription-id"
      WEBHOOK_CLIENT_STATE     = "inbox-webhook"
    }
```

(The active subscription ID now lives in the `graph-subscription-id` secret, read at runtime, not in the function env.)

- [ ] **Step 4: Preview with the terraform-plan skill**

Invoke the `/terraform-plan` skill. Expected plan:
- **add:** `google_secret_manager_secret.secrets["graph-subscription-id"]`, `google_secret_manager_secret_version.graph_subscription_id`, two `google_secret_manager_secret_iam_member` (renew subscription accessor + version manager).
- **change (in place):** `google_cloudfunctions2_function.renew` (env vars + new source hash from Task 1).
- **no destroys** beyond the function source object being replaced.

Confirm there are no unexpected destroys before continuing.

- [ ] **Step 5: Commit**

```bash
git add terraform/secrets.tf terraform/iam.tf terraform/cloud_functions.tf
git commit -m "feat: store Graph subscription ID in mutable Secret Manager secret"
```

---

## Task 3: Deploy, then prove self-heal end-to-end

**Files:** none (operational). Uses `/terraform-apply` and `gcloud`.

- [ ] **Step 1: Apply**

Invoke the `/terraform-apply` skill. This deploys the new renew function code AND creates the secret + IAM. Wait for `Apply complete`.

- [ ] **Step 2: Confirm the secret was seeded with the live ID**

```bash
gcloud secrets versions access latest --secret=graph-subscription-id --project=bens-project-462804
```
Expected: `f58b30e4-4090-433a-87cc-fbe1f87f574a` (the currently-live subscription).

- [ ] **Step 3: Confirm a normal renewal still works**

```bash
gcloud scheduler jobs run inbox-subscription-renew --location=us-central1 --project=bens-project-462804
sleep 15
gcloud logging read \
  'resource.labels.service_name="inbox-renew" textPayload:"Renewed subscription" timestamp>="'"$(date -u -v-3M +%Y-%m-%dT%H:%M:%SZ)"'"' \
  --project bens-project-462804 --limit 5 --format='value(timestamp,textPayload)'
```
Expected: a `Renewed subscription f58b30e4-... -- new expiry: ...` log line. No 404, no Exception.

- [ ] **Step 4: Force a genuine 404 to exercise self-heal**

Delete the live subscription so the stored ID becomes a real `404` (this reproduces the original incident exactly):
```bash
cd ~/src/inbox && GCP_PROJECT_ID=bens-project-462804 .venv/bin/python - <<'EOF'
import os
os.environ['GCP_PROJECT_ID'] = 'bens-project-462804'
from clients.azure import GraphEmailClient
from clients.graph_subscriptions import delete
c = GraphEmailClient(); c.authenticate_headless()
sub_id = os.popen("gcloud secrets versions access latest --secret=graph-subscription-id --project=bens-project-462804").read().strip()
delete(c, sub_id)
print("Deleted", sub_id)
EOF
```

- [ ] **Step 5: Trigger the renew job and confirm it self-heals**

```bash
gcloud scheduler jobs run inbox-subscription-renew --location=us-central1 --project=bens-project-462804
sleep 20
gcloud logging read \
  'resource.labels.service_name="inbox-renew" (textPayload:"registering a replacement" OR textPayload:"Registered replacement") timestamp>="'"$(date -u -v-3M +%Y-%m-%dT%H:%M:%SZ)"'"' \
  --project bens-project-462804 --limit 5 --format='value(timestamp,textPayload)'
```
Expected: `Subscription ... not found (404) -- registering a replacement` followed by `Registered replacement subscription <new-id> ...`.

- [ ] **Step 6: Verify exactly one live subscription, and the secret tracks it**

```bash
echo "--- secret now holds ---"
gcloud secrets versions access latest --secret=graph-subscription-id --project=bens-project-462804
echo "--- live subscriptions in Graph ---"
cd ~/src/inbox && .venv/bin/python - <<'EOF'
import os, sys, requests
sys.path.insert(0, os.path.expanduser('~/src/inbox'))
os.environ['GCP_PROJECT_ID'] = 'bens-project-462804'
from clients.azure import GraphEmailClient
c = GraphEmailClient(); c.authenticate_headless()
tok = next((getattr(c,a) for a in ('access_token','_access_token','token','_token') if isinstance(getattr(c,a,None),str)), None)
for s in requests.get("https://graph.microsoft.com/v1.0/subscriptions",
                      headers={"Authorization": f"Bearer {tok}"}).json().get("value", []):
    print(s["id"], "|", s.get("resource"), "|", s.get("expirationDateTime"))
EOF
```
Expected: the secret value equals the single subscription ID listed by Graph, watching `me/mailFolders/inbox/messages`. **The new ID was written by the function itself — no manual tfvars edit, no Terraform apply.** That is the self-heal proven.

- [ ] **Step 7: Update `CLAUDE.md`**

In the **Graph subscription** section, replace the "To re-register..." manual snippet with a note that the renew CF now self-heals: on a `404` it auto-registers a replacement and writes the new ID to the `graph-subscription-id` secret — manual re-registration is only needed for first-time bootstrap. Drop the hardcoded "Active subscription ID" line (the secret is now the source of truth) or note that the live value is `gcloud secrets versions access latest --secret=graph-subscription-id`. In the **Secrets** table, add a row: `graph-subscription-id | Renew CF — active Graph subscription ID (self-updated on re-register)`.

```bash
git add CLAUDE.md
git commit -m "docs: document self-healing Graph subscription"
```

- [ ] **Step 8: Open the PR**

Use the `/pr-open` skill (per the repo's PR-only workflow) to push the branch and open the PR.

---

## Self-Review

**Spec coverage:**
- "Recover when subscription fully expired" → Task 1 `_renew_or_register` 404 branch + Task 3 Step 4–6 proof. ✓
- "Persist the new ID so it converges" → `_save_subscription_id` + `graph-subscription-id` secret (Task 1 + Task 2). ✓
- "Don't leak duplicates on transient errors" → `test_non_404_error_does_not_register` + only-404 branch. ✓
- "No new heavy deps in the standalone function" → reuses Secret Manager (already a dep); no DB/Cloud SQL connector pulled in. ✓
- "Bootstrap a fresh environment" → empty-ID path registers (`test_register_when_no_subscription_id`), and `_load_subscription_id` swallows `NotFound`. ✓

**Out of scope (intentional, YAGNI):** alerting on renew failures and widening the renewal cadence are separate improvements, not part of self-heal. The function does NOT reconcile a subscription that exists but points at the wrong URL/clientState — only the missing (404) case.

**Type consistency:** `_renew_or_register(subscription_id, token)`, `_register_subscription(token)`, `_patch_subscription(subscription_id, token)`, `_save_subscription_id(id)`, `_load_subscription_id()` — names/arities match between the implementation (Task 1 Step 3), the tests (Task 1 Step 1), and the env vars wired in Task 2 (`WEBHOOK_URL`, `SUBSCRIPTION_SECRET_NAME`, `WEBHOOK_CLIENT_STATE`). ✓
