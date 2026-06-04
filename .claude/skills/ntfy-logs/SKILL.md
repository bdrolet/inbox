---
name: ntfy-logs
description: Use when the user wants to fetch, read, tail, or inspect logs from the self-hosted ntfy server, check for errors, see recent notification activity, or debug why a notification wasn't delivered.
---

# Fetching ntfy Server Logs

Reads logs from the ntfy systemd service on the GCP e2-micro VM via `gcloud compute ssh`.

**VM:** `ntfy` | **Project:** `bens-project-462804` | **Zone:** `us-central1-a`

## Basic commands

**Recent 100 lines:**
```bash
gcloud compute ssh ntfy \
  --project bens-project-462804 \
  --zone us-central1-a \
  --command "sudo journalctl -u ntfy -n 100 --no-pager"
```

**Follow live (Ctrl-C to exit):**
```bash
gcloud compute ssh ntfy \
  --project bens-project-462804 \
  --zone us-central1-a \
  --command "sudo journalctl -u ntfy -f"
```

**Errors only:**
```bash
gcloud compute ssh ntfy \
  --project bens-project-462804 \
  --zone us-central1-a \
  --command "sudo journalctl -u ntfy -p err --no-pager"
```

**Since a specific time:**
```bash
gcloud compute ssh ntfy \
  --project bens-project-462804 \
  --zone us-central1-a \
  --command "sudo journalctl -u ntfy --since '2026-06-04 12:00:00' --no-pager"
```

**Startup script output (for debugging VM provisioning):**
```bash
gcloud compute ssh ntfy \
  --project bens-project-462804 \
  --zone us-central1-a \
  --command "sudo journalctl -u google-startup-scripts --no-pager"
```

## What to look for

| Pattern | Meaning |
|---------|---------|
| `ntfy version X.Y.Z` | Service started successfully |
| `Listening on :443` | TLS is active |
| `Listening on :80` | Running pre-TLS (bootstrap-tls.sh not yet run) |
| `POST /<topic>` | Notification received and delivered |
| `certificate expired` | Let's Encrypt renewal failed — run `sudo certbot renew` |
| Any `error` or panic | Investigate further |

## Check ntfy health directly

```bash
curl -s https://ntfy.drolet.ai/v1/health
# Expected: {"healthy":true}
```

## Check certbot renewal status

```bash
gcloud compute ssh ntfy \
  --project bens-project-462804 \
  --zone us-central1-a \
  --command "sudo certbot certificates"
```
