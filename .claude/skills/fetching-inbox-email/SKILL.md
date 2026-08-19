---
name: fetching-inbox-email
version: 1.1.0
description: >
  Use when the user wants to read, view, or open a specific email's full content —
  body text, recipients, or attachments — by Graph message ID. Use when asked to
  "show me this email", "read this message", "what does that email say", or "get
  the attachments from that email". Also use after searching with searching-inbox-emails
  to open a result the user selects.
metadata:
  depends-on: "searching-inbox-emails"
---

# Fetching Inbox Email

## Prerequisites

You need a Graph message ID **and its mailbox label**. If you don't have them yet, use the **searching-inbox-emails** skill — each search result has a `message_id` and a `mailbox` field (`"me"`, a shared-mailbox address, or `"group:..."`). Pass both: fetching a shared-mailbox or group result without its `mailbox` label returns 404.

## Auth token

```bash
TOKEN=$(grep 'search_token' ~/src/inbox/terraform/terraform.tfvars | grep -o '"[^"]*"' | tr -d '"')
```

## Fetch full email detail

```bash
curl -s -G "https://inbox-api.drolet.cloud/emails/<message_id>" \
  --data-urlencode "mailbox=<mailbox label from search result>" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

Omit `--data-urlencode "mailbox=..."` (or pass `mailbox=me`) for primary-mailbox messages.

**Response fields:**

| Field | Description |
|-------|-------------|
| `subject` | Email subject |
| `from_email` / `from_name` | Sender address and display name |
| `to` / `cc` | Recipient lists (`name`, `address`) |
| `received_at` / `sent_at` | Timestamps |
| `body` | Full body content |
| `body_type` | `"html"` or `"text"` |
| `has_attachments` | Boolean |
| `web_link` | Direct Outlook web URL |
| `posts` | Group conversations only: full thread as a list of posts (`id`, `thread_id`, `sender_name`, `sender_email`, `body`, `body_type`, `sent_at`, `has_attachments`), oldest first. `null` for regular messages. For groups, the top-level `body`/`from_*` mirror the latest post and `web_link` is `null`. |

Returns `404` if the message ID is not found (or if mailbox is invalid for that message); `403` if the token lacks rights to that shared mailbox; `404 "unknown group: ..."` if the group label isn't among the account's memberships; `503` if Graph auth is unavailable.

## Fetch attachments

```bash
curl -s -G "https://inbox-api.drolet.cloud/emails/<message_id>/attachments" \
  --data-urlencode "mailbox=<mailbox label from search result>" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

**Response:** `{ "attachments": [ { "id", "name", "content_type", "size", "is_inline", "content_bytes" } ] }`

For group conversations, attachment items include a `post_id` field tracing the attachment to its post.

**Errors:** `403` means the token lacks rights to that shared mailbox; `404 "unknown group: ..."` means the group label isn't among the account's memberships.

`content_bytes` is base64-encoded file data. To save an attachment to disk:

```bash
python3 -c "import base64, json, sys; d=json.load(sys.stdin); open(d['attachments'][0]['name'],'wb').write(base64.b64decode(d['attachments'][0]['content_bytes']))"
```

## Presenting results

- For plain-text bodies, display inline.
- For HTML bodies, strip tags or note that it's HTML and offer the `web_link` to open in Outlook.
- List attachments with name + size; offer to save or display content.
