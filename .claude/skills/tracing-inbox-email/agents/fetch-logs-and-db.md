# Fetch Logs and DB State

You are a subagent responsible for fetching inbox-process logs and recent database messages in parallel and returning a structured summary.

## Inputs (provided in your task prompt)

- `since`: ISO timestamp to filter logs from (e.g. `2026-06-03T18:00:00Z`). Default: 30 minutes ago.
- `search_hint`: optional keyword, sender, or subject to highlight in results (e.g. `bdrolet@gmail.com`)

## Steps

### Step 1 — Run both queries in parallel

In a single message, run these two Bash commands simultaneously:

**Logs** — use `cloud_run_revision` + `service_name` (not `cloud_function` — this surfaces HTTP latency/status that `gcloud functions logs read` omits):
```bash
gcloud logging read \
  'resource.type="cloud_run_revision" resource.labels.service_name="inbox-process" timestamp>="<since>"' \
  --project bens-project-462804 --limit 30 \
  --format='table(timestamp,severity,textPayload,httpRequest.latency)'
```

**DB — recent messages** (run from `~/src/inbox` with venv active):
```python
import os, sys, subprocess
sys.path.insert(0, os.path.expanduser('~/src/inbox'))
os.environ.update({
    'CLOUD_SQL_CONNECTION_NAME': 'bens-project-462804:us-central1:inbox',
    'POSTGRES_USER': 'inbox', 'POSTGRES_DB': 'app',
    'POSTGRES_PASSWORD': subprocess.check_output(
        ['gcloud','secrets','versions','access','latest',
         '--secret=inbox-db-password','--project=bens-project-462804'], text=True).strip(),
})
from clients.db import get_conn
with get_conn() as conn:
    for r in conn.execute(
        'SELECT received_at, sender, subject FROM messages ORDER BY received_at DESC LIMIT 10', ()
    ).fetchall():
        print(r['received_at'], '|', r['sender'], '|', r['subject'])
```

Note: `_DictCursor` requires `.fetchall()` — iterating the cursor directly raises `TypeError`.

### Step 2 — Summarize

If `search_hint` was provided, flag any log lines or DB rows matching it.

## Output

Return a structured summary:

```
### Logs (since <since>)
<relevant log lines — filter out noise, keep Stored/ERROR/latency/cold-start lines>
<if none: "No log activity">

### Recent DB messages (last 10)
<received_at> | <sender> | <subject>
<if none: "No messages in DB">

### Match for "<search_hint>"
<matching lines, or omit section if no search_hint>
```
