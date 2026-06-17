---
name: refreshing-msal-token
version: 1.0.0
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

If you are adding **new scopes**, they must already be added to the Azure app registration
before this will work:

1. portal.azure.com → Azure Active Directory → App Registrations → inbox app
   (client ID in `terraform/terraform.tfvars`)
2. API permissions → Add permission → Microsoft Graph → Delegated → add the scope(s)
3. Grant admin consent if required (Group.Read.All always needs it)

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

If it failed, check Azure app permissions (step 0) and retry.

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
