---
name: send-test-notification
description: >
  Use when the user wants to send a test ntfy notification from their local machine,
  verify notification delivery to their phone, test an action button format (e.g. Outlook
  deep-link, http callback), or confirm ntfy credentials are working.
metadata:
  depends-on: querying-inbox-db
---

# Send Test Notification

Push a one-off ntfy notification from local using real credentials and optionally a real
message from the DB.

## Credentials

All credentials come from existing sources — no prompting needed:

| Value | Source |
|-------|--------|
| `NTFY_BASE_URL` | `https://ntfy.drolet.ai` |
| `NTFY_TOPIC` | `inbox` (from `terraform/terraform.tfvars`) |
| `NTFY_TOKEN` | Secret Manager: `ntfy-token` in project `bens-project-462804` |

Fetch the token:
```bash
gcloud secrets versions access latest --secret=ntfy-token --project=bens-project-462804
```

## Getting a real message_id (optional)

Use the **querying-inbox-db** skill, or run directly:

```python
source ~/src/inbox/.venv/bin/activate
# then query: SELECT id, subject, sender FROM messages ORDER BY received_at DESC LIMIT 5
```

## Sending the notification

```python
import httpx

resp = httpx.post(
    "https://ntfy.drolet.ai/",
    headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer <token>",
    },
    json={
        "topic": "inbox",
        "title": "[TEST] <subject>",
        "message": "From: <sender>\n\n<body or description>",
        "actions": [
            # view — opens URL on device (works on iOS)
            {"action": "view", "label": "Open in Outlook", "url": f"ms-outlook://emails/deeplink/{message_id}"},

            # http — fires a POST/GET in the background (for label callbacks)
            {"action": "http", "label": "Confirm", "url": "<webhook_url>/label?id=<id>&label=urgent", "headers": {"Authorization": "Bearer <webhook_token>"}},
        ],
    },
    timeout=10,
)
print(resp.status_code, resp.text)
```

## Action button types

| Type | Behavior | iOS support |
|------|----------|-------------|
| `view` | Opens URL in system handler / browser | Yes |
| `http` | Background HTTP request, no UI | Yes |
| `broadcast` | Android broadcast intent | No |

**Outlook deep-link:** `ms-outlook://emails/deeplink/<message_id>` opens the specific email
if Outlook is installed; falls back silently (no error shown) if not installed or ID doesn't
resolve — use plain `ms-outlook://` as a fallback to just open the app.
