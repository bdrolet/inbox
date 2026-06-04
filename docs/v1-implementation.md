# v1 Implementation Guide

Phases 1–4 of the Inbox migration. Each phase is independently deployable and builds on the last. Start at Phase 1.

Full architecture context: [inbox-architecture.md](inbox-architecture.md)

---

## Phase 1: Schema + storage layer — ✅ Complete

**What it does**: Stand up the event-driven Cloud Function processor and Cloud SQL database. Classification logic is unchanged (still gpt-4o-mini, 8 categories) — the goal is infrastructure only.

### 1. Cloud SQL (managed Postgres 16 + pgvector)

Provisioned via `terraform/cloudsql.tf` (db-f1-micro, 10GB SSD, `us-central1`). pgvector is enabled as part of the schema migration — no separate init job needed; Cloud SQL ships the extension pre-installed.

### 2. Write the schema

`repo/schema.sql` — run via `scripts/migrate_db.py` after `terraform apply`:

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS messages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source TEXT NOT NULL,
  external_id TEXT NOT NULL,
  sender TEXT NOT NULL,
  sender_display TEXT,
  subject TEXT,
  body TEXT,
  received_at TIMESTAMPTZ NOT NULL,
  thread_id TEXT,
  raw JSONB,
  UNIQUE (source, external_id)
);

CREATE TABLE IF NOT EXISTS message_embeddings (
  message_id UUID PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
  embedding vector(384) NOT NULL,
  current_label TEXT,
  updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ON message_embeddings USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS classifications (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  message_id UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  category TEXT NOT NULL,
  confidence FLOAT,
  alternatives JSONB,
  tags TEXT[],
  reasoning TEXT,
  model TEXT,
  prompt_version TEXT,
  source TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ON classifications (message_id, created_at DESC);

CREATE TABLE IF NOT EXISTS senders (
  identifier TEXT NOT NULL,
  source TEXT NOT NULL,
  first_seen TIMESTAMPTZ,
  message_count INT DEFAULT 0,
  my_response_count INT DEFAULT 0,
  relationship_label TEXT,
  notes TEXT,
  PRIMARY KEY (source, identifier)
);

CREATE TABLE IF NOT EXISTS tags (
  name TEXT PRIMARY KEY,
  description TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);
```

### 3. New / updated files

| File | What it does |
|------|-------------|
| `main.py` | Processor Cloud Function entry point (`process(cloud_event)`) — Pub/Sub event trigger |
| `clients/db.py` | psycopg connection; uses Cloud SQL Python Connector when `CLOUD_SQL_CONNECTION_NAME` is set, falls back to direct connect for local dev |
| `models/message.py` | `Message` TypedDict |
| `services/ingestion.py` | `normalize(email, raw) → Message`; `fetch(message_id, client)` |
| `repo/messages.py` | `insert(conn, msg)`, `exists(conn, source, external_id)`, `get(conn, id)` |
| `repo/classifications.py` | `insert(...)` |
| `repo/senders.py` | `upsert(conn, identifier, source)`, `get(...)` |
| `functions/webhook/main.py` | HTTP Cloud Function — GET validates, POST publishes to Pub/Sub |
| `functions/renew/main.py` | HTTP Cloud Function — renews Graph subscription |
| `scripts/migrate_db.py` | Runs `repo/schema.sql` against Cloud SQL |
| `clients/graph_subscriptions.py` | `register(client, notification_url)`, `renew(client, subscription_id)` |

### 4. `main.py` — processor Cloud Function entry point

```python
import base64, json, logging
import functions_framework
from clients.azure.graph_email_client import GraphEmailClient
from clients.db import get_conn
from repo import messages, senders
from services.ingestion import fetch, normalize

_graph_client = None  # module-level singleton; reused on warm invocations

def _get_graph_client():
    global _graph_client
    if _graph_client is None:
        client = GraphEmailClient()
        client.authenticate_headless()
        _graph_client = client
    return _graph_client

@functions_framework.cloud_event
def process(cloud_event):
    data = base64.b64decode(cloud_event.data["message"]["data"]).decode()
    notification = json.loads(data)
    message_id = notification.get("resourceData", {}).get("id")
    if not message_id:
        return
    email = fetch(message_id, _get_graph_client())
    if email is None:
        return
    msg = normalize(email, raw=notification)
    with get_conn() as conn:
        if messages.exists(conn, msg["source"], msg["external_id"]):
            return
        msg_id = messages.insert(conn, msg)
        senders.upsert(conn, msg["sender"], msg["source"])
        conn.commit()
```

### 5. `clients/db.py` — Cloud SQL connector

```python
def get_conn():
    connection_name = os.environ.get("CLOUD_SQL_CONNECTION_NAME")
    if connection_name:
        from google.cloud.sql.connector import Connector
        return Connector().connect(connection_name, "psycopg",
            user=..., password=..., db=..., row_factory=dict_row)
    return psycopg.connect(host=..., ...)  # local dev fallback
```

### 6. `clients/azure/graph_email_client.py` — token cache path

Token cache moved from `.token_cache.json` (relative) to `~/.inbox-token-cache.json` (absolute) to prevent accidental inclusion in the Cloud Function source zip.

### 7. GCP infrastructure (`terraform/`)

- **`cloudsql.tf`** (new) — Cloud SQL Postgres 16 instance + database + user
- **`cloud_functions.tf`** (updated) — adds `inbox-process` CF with Pub/Sub event trigger; source is repo root (includes all shared modules)
- **`pubsub.tf`** (updated) — pull subscription removed; CF event trigger creates its own
- **`iam.tf`** (updated) — GKE worker SA removed; `inbox-process-cf` SA added with `cloudsql.client` + Secret Manager roles
- **`secrets.tf`** (updated) — adds `inbox-db-password`
- **`variables.tf`** (updated) — adds `db_user`, `db_password`
- **`main.tf`** (updated) — enables `sqladmin.googleapis.com` API

### 8. Register the Graph subscription

After `terraform apply`, run once:

```python
from clients.azure import GraphEmailClient
from clients.graph_subscriptions import register
c = GraphEmailClient(); c.authenticate_headless()
result = register(c, "https://inbox-webhook-aizbgjlava-uc.a.run.app")
print(result["id"])  # set as graph_subscription_id in terraform.tfvars
```

### 9. `requirements.txt` additions

```
psycopg[binary]>=3.1
pgvector>=0.2
functions-framework>=3.0
google-cloud-pubsub>=2.18
cloud-sql-python-connector[psycopg]>=1.7.0
```

### Phase 1 deliverable

Event-driven Cloud Function processor receives messages via Pub/Sub, writes to Cloud SQL, runs existing gpt-4o-mini classification. Existing Cloud Run Job stays as daily fallback.

---

## Phase 2: Embeddings + retrieval — ✅ Complete

**What it does**: Embed each new message and store it. Implement nearest-neighbor retrieval. Results are logged but not yet used in the prompt.

### New files to write

| File | What it does |
|------|-------------|
| `clients/bge.py` | `load_model() → SentenceTransformer`; `encode(text) → np.ndarray` |
| `services/embedding.py` | `text_for_embedding(msg)`, `strip_reply_chain(text)`, `strip_signature(text)`, `embed_and_store(msg, model)` |
| `repo/embeddings.py` | `store_embedding(message_id, vec)`, `retrieve_neighbors(vec, exclude_id, k=10)` |

### Key implementation notes

**`services/embedding.py`**:
```python
def text_for_embedding(msg: Message) -> str:
    body = strip_reply_chain(msg["body"])
    body = strip_signature(body)
    return f"From: {msg['sender']}\nSubject: {msg['subject']}\n\n{body[:1500]}"
```

**`repo/embeddings.py`** — retrieval query:
```sql
SELECT m.subject, m.body, m.sender,
       me.current_label,
       1 - (me.embedding <=> %s) AS similarity
FROM message_embeddings me
JOIN messages m ON m.id = me.message_id
WHERE me.current_label IS NOT NULL
  AND me.message_id != %s
ORDER BY me.embedding <=> %s
LIMIT %s
```

**`current_label` is always NULL at write time.** LLM-assigned labels never go into this column — only human corrections and confirmations (Phase 4) do. This prevents bad LLM guesses from becoming retrieval examples.

### Wire into `main.py`

Model is lazy-initialized on first invocation (not at module level) so the Cloud Run health check passes before PyTorch loads:

```python
import base64, json, logging
import functions_framework
from clients.azure.graph_email_client import GraphEmailClient
from clients.bge import load_model
from clients.db import get_conn
from repo import messages, senders
from repo.embeddings import retrieve_neighbors
from services.embedding import text_for_embedding, embed_and_store
from services.ingestion import fetch, normalize

_graph_client = None
_model = None  # lazy — loaded on first invocation, reused on warm instances


def _get_graph_client():
    global _graph_client
    if _graph_client is None:
        client = GraphEmailClient()
        client.authenticate_headless()
        _graph_client = client
    return _graph_client


def _get_model():
    global _model
    if _model is None:
        _model = load_model()
    return _model


@functions_framework.cloud_event
def process(cloud_event):
    data = base64.b64decode(cloud_event.data["message"]["data"]).decode()
    notification = json.loads(data)

    message_id = notification.get("resourceData", {}).get("id")
    if not message_id:
        return

    email = fetch(message_id, _get_graph_client())
    if email is None:
        return

    msg = normalize(email, raw=notification)

    with get_conn() as conn:
        if messages.exists(conn, msg["source"], msg["external_id"]):
            return

        msg_id = messages.insert(conn, msg)
        senders.upsert(conn, msg["sender"], msg["source"])

        cleaned = text_for_embedding(msg)
        vec = embed_and_store(conn, msg_id, cleaned, _get_model())
        conn.commit()

        neighbors = retrieve_neighbors(conn, vec, exclude_id=msg_id)

    logger.info(f"Stored {msg_id} — retrieved {len(neighbors)} neighbors")
```

**Cold start note**: PyTorch + sentence-transformers uses ~518MB at import time, so the CF requires **1 vCPU / 2Gi memory** (`available_cpu = "1"`, `available_memory = "2Gi"` in `cloud_functions.tf`). First invocation after deploy takes ~60s to load bge-small-en-v1.5. Subsequent warm invocations reuse `_model`.

**Deferred import**: `clients/bge.py` imports `sentence_transformers` inside `load_model()`, not at module level. This keeps the container startup fast so the Cloud Run health check passes before PyTorch loads.

### `requirements.txt` addition

```
sentence-transformers>=2.7
```

### Phase 2 deliverable

Every new message gets an embedding stored in `message_embeddings`. Retrieval runs and logs top-k neighbors. No change to classification behavior.

**Infrastructure note**: `terraform/cloud_functions.tf` sets `available_cpu = "1"` and `available_memory = "2Gi"` for `inbox-process` (required for PyTorch).

---

## Phase 3: New prompt + categories + tags — ⬅️ Current

**What it does**: Switch to Claude Sonnet, new 5-category system, retrieval-augmented prompt. This is the first behavior change visible to the user.

### New files to write

| File | What it does |
|------|-------------|
| `models/types.py` | `Category` enum, `Classification` dataclass |
| `clients/claude.py` | Anthropic SDK call; structured JSON response parsing |
| `services/classification.py` | `build_prompt()`, `aggregate_neighbors()`, tag vocabulary |
| `services/labeling.py` | `apply_label(message_id, label, source)` |
| `handlers/pipeline.py` | Full 16-step orchestrator |

### Categories

```python
class Category(str, Enum):
    URGENT = "urgent"      # needs attention today; triggers push notification
    RESPOND = "respond"    # needs a reply, not today; moved to "To Respond"
    REVIEW = "review"      # worth reading, no reply needed; moved to "To Review"
    REFERENCE = "reference" # keep but don't read now; archived
    IGNORE = "ignore"      # marketing/noise; archived
```

### `clients/claude.py` — expected response schema

```json
{
  "category": "urgent | respond | review | reference | ignore",
  "confidence": 0.0,
  "alternatives": { "urgent": 0.0, "respond": 0.0, "review": 0.0, "reference": 0.0, "ignore": 0.0 },
  "tags": ["topic:finances", "from:family"],
  "reasoning": "one sentence"
}
```

Temperature: 0.2. Raise on invalid JSON or missing `category`.

### `services/classification.py` — prompt structure

`build_prompt(msg, aggregates, top_examples, sender_ctx)` assembles four sections:
1. System prompt with category definitions, tag vocabulary, and JSON schema
2. Sender context: message count, response rate, relationship label, notes
3. Retrieval context: per-category aggregates + top 3 raw examples with human-confirmed labels (omit section if no labeled examples yet)
4. Current message: subject, sender, received time, body (≤1500 chars)

### Secrets

Add `anthropic-api-key` to Secret Manager and inject into the processor CF as a secret env var. Keep `openai-api-key` until Phase 5.

### Replace Phase 1 logic in `main.py`

```python
from clients.bge import load_model
from handlers.pipeline import run as run_pipeline

_model = None  # lazy — same pattern as Phase 2

def _get_model():
    global _model
    if _model is None:
        _model = load_model()
    return _model

@functions_framework.cloud_event
def process(cloud_event):
    data = base64.b64decode(cloud_event.data["message"]["data"]).decode()
    notification = json.loads(data)
    run_pipeline(notification, _get_model())
```

### Remove in this phase

- `services/email_analyzer.py`
- `services/email_processor.py`
- `models/email.py`

Update `requirements.txt`: add `anthropic>=0.25`; remove `langchain-openai`, `langchain-core`.

### Phase 3 deliverable

End-to-end new classification pipeline live. 5 categories, tags, retrieval-augmented prompt, Claude Sonnet. Old 8-category path removed.

---

## Phase 4: Feedback mechanism + action handlers — Pending

**What it does**: All five categories drive Outlook folder moves or push notifications. Human corrections via ntfy.sh action buttons flow back to the vector store.

### New files to write

| File | What it does |
|------|-------------|
| `clients/ntfy.py` | POST to `https://ntfy.sh/{topic}` with action buttons |
| `services/archiving.py` | `move_to_folder(msg, folder_name)` via Graph API |
| `handlers/actions/dispatch.py` | Routes `result.category` to the right handler |
| `handlers/actions/urgent.py` | ntfy.sh notification |
| `handlers/actions/respond.py` | `archiving.move_to_folder(msg, "To Respond")` |
| `handlers/actions/review.py` | `archiving.move_to_folder(msg, "To Review")` |
| `handlers/actions/reference.py` | `archiving.move_to_folder(msg, "Archive")` |
| `handlers/actions/ignore.py` | `archiving.move_to_folder(msg, "Archive")` |

### `clients/ntfy.py` — notification payload

```python
import httpx, os

def notify(message_id: str, subject: str, sender: str, reasoning: str) -> None:
    topic = os.environ["NTFY_TOPIC"]
    httpx.post(
        f"https://ntfy.sh/{topic}",
        json={
            "topic": topic,
            "title": f"Urgent: {subject}",
            "message": f"From: {sender}\n\n{reasoning}",
            "actions": [
                {"action": "http", "label": "✓ Correct",  "url": f"{os.environ['WEBHOOK_URL']}/label?id={message_id}&label=urgent&source=human_confirmation"},
                {"action": "http", "label": "↓ Respond",  "url": f"{os.environ['WEBHOOK_URL']}/label?id={message_id}&label=respond&source=human_correction"},
                {"action": "http", "label": "↓ Review",   "url": f"{os.environ['WEBHOOK_URL']}/label?id={message_id}&label=review&source=human_correction"},
            ],
        },
    )
```

### Add `/label` route to `functions/webhook/main.py`

```python
@functions_framework.http
def webhook(request):
    # validation handshake
    if request.args.get("validationToken"):
        return request.args["validationToken"], 200, {"Content-Type": "text/plain"}

    # ntfy.sh action button callback
    if request.path == "/label":
        publisher.publish(
            LABELS_TOPIC,
            json.dumps({
                "message_id": request.args["id"],
                "label": request.args["label"],
                "source": request.args["source"],
            }).encode(),
        )
        return "", 202

    # Graph change notification
    publisher.publish(MESSAGES_TOPIC, request.get_data())
    return "", 202
```

### New Pub/Sub topic + CF event trigger

Add to `terraform/pubsub.tf` and `terraform/cloud_functions.tf`:
- `inbox-labels` topic
- A second processor CF (or extend `main.py`) with an event trigger on `inbox-labels`

### Update `main.py`

Route by the originating topic using the CloudEvent source attribute:

```python
@functions_framework.cloud_event
def process(cloud_event):
    source = cloud_event.get("source", "")
    data = base64.b64decode(cloud_event.data["message"]["data"]).decode()
    payload = json.loads(data)

    if "inbox-labels" in source:
        labeling.apply_label(
            message_id=payload["message_id"],
            label=payload["label"],
            source=payload["source"],
        )
    else:
        handle_message(payload)
```

### `services/labeling.py` — `apply_label`

```python
def apply_label(message_id: str, label: str, source: str) -> None:
    """source is 'human_confirmation' or 'human_correction'."""
    with db.transaction():
        classifications.insert(
            message_id=message_id,
            category=label,
            source=source,
            # confidence, alternatives, tags, reasoning all None for human labels
        )
        embeddings.set_current_label(message_id, label)
```

`embeddings.set_current_label` sets `current_label` and `updated_at` in `message_embeddings`. Once set, the embedding becomes eligible as a retrieval example for future classifications.

### Environment variables to add

| Variable | Value |
|----------|-------|
| `NTFY_TOPIC` | your ntfy.sh topic name |
| `WEBHOOK_URL` | Cloud Function public URL (for action button callbacks) |
| `ANTHROPIC_API_KEY` | from Secret Manager |

### Phase 4 deliverable

All five categories drive folder moves or notifications. Urgent messages include ntfy.sh action buttons. Tapping a button corrects the label and feeds the vector store.

---

## After Phase 4

The system is fully functional for v1. Before going to production, complete Phase 5:

1. Run `scripts/bootstrap_labels.py` to hand-label 30–50 past messages
2. Run the new pipeline in shadow mode alongside the old system; compare output
3. Decommission the old Cloud Run Job (delete from Terraform, remove `Dockerfile.analyze-emails`, delete `openai-api-key` secret)

See [inbox-architecture.md](inbox-architecture.md) for full Phase 5 details.

---

## Longer term

### Importance (P0–P3) — wired in Phase 3, surfaces in Phase 4+

A second classification axis — **importance** — was added alongside the five urgency categories. Category captures *when to act* (timing/routing); importance captures *how much it matters* (stakes), independent of timing.

| Level | Meaning |
|-------|---------|
| P0 | Critical — major consequence if missed |
| P1 | Needs to be done — real obligation or opportunity |
| P2 | Would be pretty great if accomplished |
| P3 | Nice to have |

**What's already done:**
- `Importance` enum in `models/types.py`; `Classification.importance` defaults to P2
- LLM prompt (v2) asks for both `category` and `importance`; they are framed as independent axes with cross-axis examples
- `classifications.importance` column stores the LLM-assigned value
- `message_embeddings.current_importance` column stores the human-confirmed value (set via `set_current_importance()`)
- `retrieve_neighbors()` returns `current_importance`; `build_prompt()` shows `[category, importance]` in few-shot examples when set
- `apply_label()` accepts optional `importance` parameter

**Phase 4 follow-ons:**
- `handlers/actions/urgent.py` should include `importance` in the ntfy.sh notification payload so you can distinguish a P0-urgent from a P3-urgent at a glance
- The `/label` webhook route currently only passes `message_id`, `label`, and `source` — extend it to also carry `importance` if you want ntfy.sh button taps to set `current_importance`

**Phase 5 follow-on:**
- `scripts/bootstrap_labels.py` already stores LLM-assigned `importance` when caching predictions via `ai_predict_category()`; those values populate `classifications.importance` but not `current_importance` (which requires a human action). If you want retrieval examples to carry importance from the start, add a step to the bootstrap session that writes `current_importance` from the cached LLM value for high-confidence rows.
