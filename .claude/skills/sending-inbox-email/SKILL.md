---
name: sending-inbox-email
version: 1.1.0
description: >
  Use when the user wants to compose, draft, or send an email — write a new message,
  "draft a reply", "create a draft", "send an email to X", "email Y about Z", reply
  to someone, or attach a file to an outgoing message. Sends from the primary mailbox
  or from an alias, M365 group, or shared mailbox. Rough requests dispatch the
  email-builder agent, which drafts for review. Does not search or read existing
  mail — use searching-inbox-emails / fetching-inbox-email for that.
metadata:
  depends-on: "email-builder (agent), fetching-inbox-email, searching-inbox-emails, searching-people, matching-writing-style"
---

# Sending Inbox Email

Outbound email via the `inbox-api` Cloud Run service (Microsoft Graph under the hood).

## Rough requests go through the email-builder agent

New messages ("email Alice that Thursday works") and replies ("reply to the
landlord about the lease") alike: anything where recipients, wording, or the
thread must be worked out. Spawn
the `email-builder` agent (`subagent_type: "email-builder"`). It resolves
addresses, reads the message being replied to, writes in Ben's voice, and
leaves an Outlook **draft**. It sends nothing unless told to.

The agent starts blank and can't see this conversation. Pass it:

- the request in the user's own words
- the Graph message id being replied to, and its mailbox, if one is on screen
- people named earlier, with addresses if you already have them
- file paths to attach
- an identity to send from, if the user named one
- `send: true` **only** if the user explicitly said to send without
  reviewing first ("just send it", "no need to show me"). "Email X" alone
  means draft.
- today's date

Don't research first. That's the agent's job.

**Relay** what it returns:

- **`DRAFTED`**: show from/to/cc/subject/attachments and the full body, plus
  any `Assumptions`, then ask: *send it, change it, or leave it in Drafts?*
  - *send*: send the draft yourself (`POST /emails/drafts/{id}/send`, below,
    with the same `from` block). No re-dispatch.
  - *change*: small wording edits, re-dispatch with `draft_id` and the
    changes; the agent creates a new draft and reports the old one's id.
  - *leave it*: done; give the `web_link`.
- **`SENT`**: say plainly that it went out and to whom.
- **`RECIPIENT_UNRESOLVED`** / **`AMBIGUOUS`**: show the candidates, ask
  which one, and re-dispatch with that address or message id.
- **Failure**: relay the error and what exists now (e.g. a draft missing an
  attachment).

**Skip the agent** when the user dictated the exact recipients, subject, and
body. Use the API below directly. Still confirm before `/emails/send`
unless the user already said to send.

## Auth token

```bash
TOKEN=$(gcloud auth print-identity-token)   # Cloud Run IAM; your gcloud login is the credential
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

## Reply to an existing message (threaded)

**Use this, not `/emails/send` with an `RE:` subject, whenever the user is
answering a message.** Graph addresses the reply, sets the threading headers,
and keeps the quoted history below `body`. Get the `message_id` from
[[searching-inbox-emails]]; URL-encode it for the path.

```bash
ENC=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1],safe=''))" "$MESSAGE_ID")
curl -s -XPOST "$BASE/emails/$ENC/reply" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"body":"Sounds great. See you then.","from":{"address":"alias@drolet.ai","shared":false}}'
# -> {"status":"sent"}
```

Optional: `"reply_all": true`; `"send": false` leaves a threaded draft and
returns `{"status":"drafted","id":"...","web_link":"..."}` — send it with
`/emails/drafts/{id}/send` below. `from` follows the identity table below;
reply from the address the user used on the thread.

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
- To reply to a found message, use the threaded reply endpoint above; read it first via [[fetching-inbox-email]] if you need its content.
