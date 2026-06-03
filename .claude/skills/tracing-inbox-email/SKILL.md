---
name: tracing-inbox-email
description: Use when the user wants to trace an email through the inbox pipeline, verify that a sent email was received and stored, live-monitor the Cloud Function while waiting for a message to arrive, or diagnose why an email was skipped, errored, slow, or missing from the database.
metadata:
  depends-on:
    - fetch-inbox-logs
---

# Tracing an Inbox Email

End-to-end check for a message through the inbox pipeline: logs → database → diagnosis.

**Project:** `bens-project-462804` | **Function:** `inbox-process` | **DB:** Cloud SQL `app`

## Step 1 — Fetch recent logs and DB state

Read `agents/fetch-logs-and-db.md`, then spawn it (fast model). Pass:
- `since`: ISO timestamp — use the time the email was sent, or ~30 min ago if unknown
- `search_hint`: sender address or subject if known (e.g. `bdrolet@gmail.com`)

The subagent runs the log query and DB query in parallel and returns a structured summary.

## Step 2 — Live-poll (waiting for a message to arrive)

Use the `Monitor` tool with this poll loop (emits a line only when new content appears):
```bash
last="<ISO_TIME>"
while true; do
  out=$(gcloud logging read \
    "resource.type=\"cloud_run_revision\" resource.labels.service_name=\"inbox-process\" timestamp>=\"$last\"" \
    --project bens-project-462804 --limit 20 \
    --format='value(timestamp,textPayload)' 2>/dev/null | grep -v "^$" || true)
  [ -n "$out" ] && echo "$out" && last=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  sleep 8
done
```

## Step 3 — Diagnose

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Stored <uuid>` in logs | Success | Done |
| Weights loading, then silence, but DB has the row | Logs flushed before response log | None |
| 207-second HTTP latency after deploy | Cold start — model download + boot | Expected; warm calls are fast |
| `401 Unauthorized` from Graph API | MSAL token expired | Token refresh is per-invocation; if persisting, re-auth headless and update Secret Manager |
| `400 Bad Request` for a `test-message-*` ID | Fake test message in Pub/Sub | Harmless; ignore |
| Weights loaded, silence, no DB row | Pub/Sub retry in-flight or silent crash | Wait 30s, recheck DB; if still missing, check ERROR-level logs |
| `Duplicate <uuid> — skipping` | Pub/Sub redelivery (normal when first call was slow) | Idempotency working |
| No invocation at all | Graph subscription expired | Check renewal; re-register if needed (see CLAUDE.md) |

## Notes

- Cloud Logging lags ~5–15s — silence doesn't mean failure; verify via DB.
- Cold start after deploy: **2–4 minutes** (container boot + model download + processing).
- Function returns HTTP 200 even on skip/error — check log text, not just HTTP status.
