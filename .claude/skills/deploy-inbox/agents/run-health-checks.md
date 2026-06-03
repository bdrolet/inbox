# Run Post-Deploy Health Checks

You are a subagent responsible for verifying that an inbox-process Cloud Function deploy is healthy by checking the function version and tailing logs from three services.

## Inputs (provided in your task prompt)

- `project`: GCP project ID (e.g. `bens-project-462804`)
- `region`: GCP region (e.g. `us-central1`)
- `check_embeddings`: `true` for Phase 2+ deploys (requires psql access), `false` otherwise

## Steps

### Step 1 — Run checks in parallel

In a single message, run these four Bash commands simultaneously:

1. **Function version:**
   ```bash
   gcloud functions describe inbox-process --region <region> --project <project> --format='value(updateTime)'
   ```

2. **Processor logs** (last 30 lines, freshness 1h):
   ```bash
   gcloud logging read 'resource.type="cloud_run_revision" resource.labels.service_name="inbox-process"' --project <project> --limit 30 --freshness=1h --format='table(timestamp, severity, textPayload)'
   ```

3. **Renew health** (last 10 lines, freshness 3d):
   ```bash
   gcloud logging read 'resource.type="cloud_run_revision" resource.labels.service_name="inbox-renew"' --project <project> --limit 10 --freshness=3d --format='table(timestamp, severity, httpRequest.status, textPayload)'
   ```

4. **Webhook activity** (last 10 lines, freshness 1d):
   ```bash
   gcloud logging read 'resource.type="cloud_run_revision" resource.labels.service_name="inbox-webhook" httpRequest.requestMethod!=""' --project <project> --limit 10 --freshness=1d --format='table(timestamp, httpRequest.requestMethod, httpRequest.status, httpRequest.latency)'
   ```

If `check_embeddings` is `true`, also run (requires active Cloud SQL Proxy or psql skill):
```bash
psql -c "SELECT count(*) FROM message_embeddings;"
```
If this fails due to connectivity, note it as "skipped — no DB access."

### Step 2 — Assess each service

| Check | Healthy signal | Flag |
|-------|----------------|------|
| Function version | Recent timestamp post-deploy | Timestamp unchanged from before deploy |
| Processor logs | `Stored <uuid> — <sender>: <subject>` lines | Any `ERROR`, traceback, or no recent lines |
| Renew | 200 HTTP responses | `403` → SA missing `roles/run.invoker` on `inbox-renew` |
| Webhook | POST requests present in window | Only GETs or no requests → Graph subscription may have expired |

## Output

Return this exact structure:

```
### Post-Deploy Health

**Function updated:** <updateTime>

| Service | Status | Notes |
|---------|--------|-------|
| inbox-process | ✅ / ⚠️ / ❌ | <last successful log line or error> |
| inbox-renew | ✅ / ⚠️ / ❌ | <last HTTP status or issue> |
| inbox-webhook | ✅ / ⚠️ / ❌ | <POST count or issue> |
| embeddings | ✅ <N> rows / skipped | <count or skip reason> |

**Issues:**
<bulleted list of any flags, or "None">
```
