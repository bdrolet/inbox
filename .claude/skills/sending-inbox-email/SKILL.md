---
name: sending-inbox-email
version: 1.0.0
description: >
  Use when the user wants to compose, draft, or send an email — write a new message,
  "draft a reply", "create a draft", "send an email to X", "email Y about Z", reply
  to someone, or attach a file to an outgoing message. Sends from the primary mailbox
  or from an alias, M365 group, or shared mailbox. Does not search or read existing
  mail — use searching-inbox-emails / fetching-inbox-email for that.
metadata:
  depends-on: "fetching-inbox-email, searching-inbox-emails"
---

# Sending Inbox Email

Outbound email via the `inbox-api` Cloud Run service (Microsoft Graph under the hood).

## Auth token

```bash
TOKEN=$(grep 'search_token' ~/src/inbox/terraform/terraform.tfvars | grep -o '"[^"]*"' | tr -d '"')
BASE=https://inbox-api.drolet.cloud
```

## Compose-and-send in one shot (preferred)

No draft id needed — simplest path:

```bash
curl -s -XPOST "$BASE/emails/send" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"to":["x@y.com"],"cc":[],"bcc":[],"subject":"Hi","body":"Hello","body_type":"Text"}'
# -> {"status":"sent"}
```

`body_type` is `"Text"` (default) or `"HTML"`.

## Draft, then send (when the user wants to review first)

```bash
# 1. Create draft -> returns {"id": "...", "web_link": "..."}
curl -s -XPOST "$BASE/emails/drafts" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"to":["x@y.com"],"subject":"Hi","body":"Hello"}'

# 2. (optional) attach a file < 3 MB. content_bytes is base64.
B64=$(base64 -i ./report.pdf | tr -d '\n')
ENC=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1],safe=''))" "$DRAFT_ID")
curl -s -XPOST "$BASE/emails/drafts/$ENC/attachments" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"name\":\"report.pdf\",\"content_bytes\":\"$B64\",\"content_type\":\"application/pdf\"}"

# 3. Send it
curl -s -XPOST "$BASE/emails/drafts/$ENC/send" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}'
```

**Message IDs contain `/ = +`** — always URL-encode the id for the path (the `ENC` step above). The one-shot `/emails/send` avoids this entirely.

## Sending as a different identity

Add a `from` block to any request body:

| Identity | `from` block |
|---|---|
| Primary mailbox (default) | omit `from` |
| Alias **or** M365 group | `"from":{"address":"alias@drolet.cloud","shared":false}` |
| Shared mailbox | `"from":{"address":"shared@drolet.cloud","shared":true}` |

Aliases/groups operate on the primary mailbox and stamp the `from`; shared mailboxes target the mailbox's own Drafts. **Prerequisite:** the account needs Exchange **Send As / Send on Behalf** on that alias/group/shared mailbox (and Full Access for shared) — otherwise the API returns **403** with Graph's error detail.

## Notes

- Attachments ≥ 3 MB are rejected (`400`) — large-file upload isn't supported yet.
- To reply to a found message, get its recipients/subject via [[fetching-inbox-email]] first, then compose here.
