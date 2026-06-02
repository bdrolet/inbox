---
name: fetch-inbox-logs
description: Use when the user wants to fetch, read, tail, or inspect logs from the inbox Cloud Function (inbox-process), check for errors after a deploy, debug a processing failure, see what messages were recently processed, or investigate why an email wasn't classified.
---

# Fetching Inbox Logs

Reads logs from the `inbox-process` Cloud Function (Pub/Sub-triggered processor) via Cloud Logging.

**Project:** `bens-project-462804` | **Region:** `us-central1` | **Function:** `inbox-process`

## Basic commands

**Recent logs (default 50 lines):**
```bash
gcloud functions logs read inbox-process \
  --region us-central1 \
  --project bens-project-462804 \
  --limit 50
```

**Since a specific time:**
```bash
gcloud functions logs read inbox-process \
  --region us-central1 \
  --project bens-project-462804 \
  --start-time "2026-06-01T12:00:00Z" \
  --limit 100
```

**Errors only:**
```bash
gcloud logging read \
  'resource.type="cloud_function" resource.labels.function_name="inbox-process" severity>=ERROR' \
  --project bens-project-462804 \
  --limit 50 \
  --format='table(timestamp, severity, textPayload)'
```

**Free-text search:**
```bash
gcloud logging read \
  'resource.type="cloud_function" resource.labels.function_name="inbox-process" textPayload:"<keyword>"' \
  --project bens-project-462804 \
  --limit 50 \
  --format='table(timestamp, textPayload)'
```

## What to look for

| Pattern | Meaning |
|---------|---------|
| `Stored <uuid> — <sender>: <subject>` | Email successfully classified and persisted |
| `Duplicate <uuid> — skipping` | Message already in DB, idempotency working |
| `Could not fetch email` | Graph API fetch failed (token issue or transient) |
| `Notification missing resourceData.id` | Malformed Graph notification |
| Cold start with model loading output | First invocation after deploy — expected ~60s |
| Any `ERROR` or traceback | Investigate further |

## Retention

Logs persist for **30 days** in the `_Default` bucket at no extra cost for this volume. For older events, query the `classifications` and `messages` tables in Cloud SQL instead.
