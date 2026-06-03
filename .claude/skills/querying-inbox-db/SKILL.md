---
name: querying-inbox-db
description: >
  Use when the user wants to query, read, or inspect data in the inbox Cloud SQL
  database — checking messages, classifications, embeddings, or senders,
  verifying pipeline output, or debugging what's stored in the DB.
---

# Querying the Inbox DB (GCP Cloud SQL)

**Project:** `bens-project-462804` | **Instance:** `us-central1:inbox` | **DB:** `app`

## Connection setup

Always activate the venv and fetch the password from Secret Manager:

```bash
source ~/src/inbox/.venv/bin/activate
```

```python
import os, sys, subprocess
sys.path.insert(0, os.path.expanduser('~/src/inbox'))
os.environ.update({
    'CLOUD_SQL_CONNECTION_NAME': 'bens-project-462804:us-central1:inbox',
    'POSTGRES_USER': 'inbox',
    'POSTGRES_DB': 'app',
    'POSTGRES_PASSWORD': subprocess.check_output(
        ['gcloud', 'secrets', 'versions', 'access', 'latest',
         '--secret=inbox-db-password', '--project=bens-project-462804'],
        text=True
    ).strip(),
})
from clients.db import get_conn
```

`get_conn()` uses the Cloud SQL Python Connector with `pg8000` over IAM/ADC — no proxy or firewall rules needed.

## Running queries

```python
with get_conn() as conn:
    rows = conn.execute("SELECT ...", ()).fetchall()
    for r in rows:
        print(r['column_name'])
```

**Gotchas:**
- Always call `.fetchall()` or `.fetchone()` — iterating the cursor directly raises `TypeError`
- Always pass `()` as the params argument, even for queries with no parameters (passing `None` raises `TypeError`)
- Use `r['column_name']` dict-style access — rows are dicts

## Key tables

| Table | Contents |
|-------|----------|
| `messages` | `id`, `sender`, `subject`, `body`, `received_at`, `source`, `external_id` |
| `classifications` | `message_id`, `category`, `confidence`, `alternatives`, `tags`, `reasoning`, `model`, `prompt_version`, `source`, `created_at` |
| `message_embeddings` | `message_id`, `embedding` (vector), `current_label`, `updated_at` |
| `senders` | `identifier`, `source`, `message_count`, `my_response_count`, `relationship_label`, `notes` |

## Common queries

**Recent messages with classifications:**
```sql
SELECT m.received_at, m.sender, m.subject,
       c.category, c.confidence, c.reasoning
FROM messages m
JOIN classifications c ON c.message_id = m.id
ORDER BY m.received_at DESC
LIMIT 10
```

**Messages without a classification (pipeline gaps):**
```sql
SELECT received_at, sender, subject
FROM messages
WHERE id NOT IN (SELECT message_id FROM classifications)
ORDER BY received_at DESC
```

**Label distribution:**
```sql
SELECT category, count(*) FROM classifications
WHERE source = 'llm'
GROUP BY category ORDER BY count DESC
```

**Labeled embeddings (available for RAG retrieval):**
```sql
SELECT count(*) FROM message_embeddings WHERE current_label IS NOT NULL
```
