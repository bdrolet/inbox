---
name: refreshing-msal-token
version: 1.1.0
description: >
  Use when the MSAL token cache needs to be refreshed — e.g. after adding new OAuth
  scopes to the Azure app registration, when the refresh token has expired, or when
  authenticate_headless() fails in the Cloud Function. Runs a device code flow locally,
  verifies the new scopes are present in the token, and pushes the updated cache to
  Secret Manager.
metadata:
  type: manual
---

# Refreshing the MSAL Token Cache

## Prerequisites

If you are adding **new scopes**, they must already be added **and admin-consented** on the
Azure app registration before the device flow will mint them. The token only ever contains
scopes that are both registered on the app and consented — silent refresh will not pick up a
scope that isn't there.

The user does this in the portal (there is no `az` CLI on this machine). Walk them through it:

### Find the app registration

1. **portal.azure.com** → in the top **search bar** type **App registrations** and open it
   (or go via **Microsoft Entra ID**, but the search bar is fastest).
2. Click the **All applications** tab. Paste the inbox app's **client ID** into the filter box
   to find it. The client ID is in `.env` (`CLIENT_ID`) / `terraform/terraform.tfvars`
   (tenant ID alongside it as `TENANT_ID` / `tenant_id`).
3. Open the app.

### Add the delegated scopes

4. Left nav → **API permissions** → **Add a permission** → **Microsoft Graph** →
   **Delegated permissions**.
5. Search for and tick each scope you need, then **Add permissions**. Scopes already on the
   app are fine to leave — just make sure every required one is listed.
6. Back on the API permissions list, click **Grant admin consent for &lt;tenant&gt;** (e.g.
   "Drolet Family") and confirm. The user must be **Global Administrator** for this button to
   work (Ben is). Each scope should then show a green **Granted** check.
   - `Group.Read.All` and the `*.Shared` / write scopes always require admin consent.

Wait for the user to confirm all required scopes show **Granted** before starting the flow.
If a scope is missing at the verify step (Step 5), it almost always means it wasn't consented
here — send them back to this section.

> **Tip:** before kicking off the interactive device flow, confirm with the user that the
> scopes are already consented. The flow needs them to authenticate in a browser, so it's
> wasteful to start it if the app registration isn't ready yet.

## Steps

### 1. Clear the local cache

```bash
rm -f ~/.inbox-token-cache.json
echo "Cache cleared"
```

### 2. Start device code flow in background (unbuffered)

Run in background so you can read the output file:

```bash
PYTHONUNBUFFERED=1 python -u - <<'EOF'
from clients.azure import GraphEmailClient
c = GraphEmailClient()
c.authenticate_interactive()
EOF
```

Note the output file path from the background task result.

### 3. Poll for the device code and display it

```bash
until grep -q "enter the code" <output-file> 2>/dev/null; do sleep 1; done
cat <output-file>
```

Show the user the URL (`https://login.microsoft.com/device`) and the code. Wait for them to authenticate in their browser.

### 4. Wait for authentication to complete

```bash
until grep -q "Authentication successful\|authentication failed\|error" <output-file> 2>/dev/null; do sleep 3; done
cat <output-file>
```

If it failed, check Azure app permissions (see [Prerequisites](#prerequisites)) and retry.

### 5. Verify scopes

```bash
python - <<'EOF'
import base64, json
from clients.azure import GraphEmailClient
c = GraphEmailClient()
c.authenticate_interactive()
parts = c.access_token.split(".")
decoded = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
print("Scopes:", decoded.get("scp", ""))
EOF
```

Confirm the expected new scopes appear. If they are missing, the Azure app registration
is still not configured correctly — do not push to Secret Manager yet.

### 6. Push to Secret Manager

```bash
gcloud secrets versions add msal-token-cache \
  --data-file="$HOME/.inbox-token-cache.json" \
  --project=bens-project-462804
```

Note: `lifecycle { ignore_changes = [secret_data] }` in `secrets.tf` means CI will
**never** overwrite this — no GitHub secret update needed.

### 7. Confirm

Report the new Secret Manager version number and the verified scopes to the user.
