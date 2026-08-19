---
name: searching-inbox-emails
version: 1.1.0
description: >
  Use when searching for emails in the inbox — finding messages by keyword, sender,
  subject, or topic. Use when asked to "search my inbox", "find emails about X",
  "look for a message from Y", or "search for emails where Z". Searches live Outlook
  (primary mailbox + multiple known mailboxes + M365 groups) via Graph API KQL, or
  processed/classified messages in the database.
---

# Searching Inbox Emails

## Endpoint

```
POST https://inbox-api.drolet.cloud/search
```

Search lives on the unified `inbox-api` Cloud Run service (the standalone
`inbox-search` service was retired). The same service also hosts the read and
outbound email endpoints — see [[fetching-inbox-email]] and [[sending-inbox-email]].

## Auth token

Read from the inbox project's tfvars (gitignored):

```bash
TOKEN=$(grep 'search_token' ~/src/inbox/terraform/terraform.tfvars | grep -o '"[^"]*"' | tr -d '"')
```

## Request

```bash
curl -s -X POST https://inbox-api.drolet.cloud/search \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "<query>", "mode": "<mode>", "limit": 25}' | python3 -m json.tool
```

## Modes

| Mode | When to use | Results include |
|------|-------------|-----------------|
| `graph` (default) | Finding any email, live Outlook search | subject, sender, preview, web_link |
| `db` | Finding processed emails with classification context | + category, importance |

**`graph` mode** uses KQL — support plain keywords or structured filters:
- `subject:budget` — subject contains word
- `from:alice@company.com` — from a specific sender
- `budget Q4` — keywords anywhere in the message

**`db` mode** uses ILIKE on subject, sender, and body of stored messages only.

## Default mailbox scope (graph mode)

Searches primary mailbox (`me`) plus any `SHARED_MAILBOXES` configured on the CF,
and auto-discovers + searches all M365 groups the user belongs to.

**Known accessible mailboxes:**
| Mailbox | Purpose |
|---------|---------|
| `me` | Primary mailbox (ben@drolet.cloud) |
| `ben.mediation@drolet.cloud` | Mediation/legal |
| `ben.personal@drolet.cloud` | Personal |
| `family@drolet.cloud` | Family |
| `bendrolet@drolet.cloud` | Personal alias |
| `soccer@drolet.cloud` | Soccer |
| `services@drolet.cloud` | Services/subscriptions |

Override to a specific mailbox:
```json
{"query": "...", "mailboxes": ["ben.mediation@drolet.cloud"]}
```

Search multiple mailboxes:
```json
{"query": "...", "mailboxes": ["me", "ben.personal@drolet.cloud"]}
```

## Presenting results

Format results as a readable list:
- `received_at` | `sender_display` | `subject` | `category`/`importance` (db mode) | `web_link` (graph mode)

Ask the user if they want to open a specific result or take action on it.
