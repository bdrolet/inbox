# v1 Implementation Guide

Phases 1–4 of the Inbox migration. Each phase is independently deployable and builds on the last. Start at Phase 1.

Full architecture context: [inbox-architecture.md](inbox-architecture.md)

---

## Phase 1: Schema + storage layer

**What it does**: Stand up the event-driven GKE worker and database. Classification logic is unchanged (still gpt-4o-mini, 8 categories) — the goal is infrastructure only.

### 1. Enable pgvector on GKE Postgres

Run once against the existing Postgres instance:

```yaml
# infra/k8s/inbox/pgvector-init-job.yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: pgvector-init
  namespace: apps
spec:
  template:
    spec:
      containers:
      - name: pgvector-init
        image: postgres:16
        command: ["psql"]
        args: ["$(DATABASE_URL)", "-c", "CREATE EXTENSION IF NOT EXISTS vector;"]
        envFrom:
        - secretRef:
            name: postgres-credentials
      restartPolicy: Never
```

### 2. Write the schema

`repo/schema.sql` — run via `scripts/migrate_db.py` as a Kubernetes Job:

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

### 3. New files to write

| File | What it does |
|------|-------------|
| `clients/db.py` | psycopg connection pool; reads `DATABASE_URL` from env |
| `models/message.py` | `Message` TypedDict with all fields from the common shape |
| `services/ingestion.py` | `normalize(raw_notification) → Message`; extracts fields from Graph rich notification payload |
| `repo/messages.py` | `insert(msg)`, `exists(source, external_id)`, `get(id)` |
| `repo/classifications.py` | `insert(message_id, category, confidence, alternatives, tags, reasoning, model, prompt_version, source)` |
| `repo/senders.py` | `upsert(identifier, source)`, `get(identifier, source)` |
| `handlers/webhook.py` | Cloud Function entry point — see below |
| `scripts/worker.py` | GKE pod entry point — see below |
| `scripts/migrate_db.py` | Runs `repo/schema.sql` against the DB |
| `clients/graph_subscriptions.py` | `register(notification_url)`, `renew(subscription_id)` |

### 4. `handlers/webhook.py` — Cloud Function

```python
import functions_framework
from google.cloud import pubsub_v1
import json, os

publisher = pubsub_v1.PublisherClient()
TOPIC = publisher.topic_path(os.environ["GCP_PROJECT_ID"], "inbox-messages")

@functions_framework.http
def webhook(request):
    # Graph subscription validation handshake
    token = request.args.get("validationToken")
    if token:
        return token, 200, {"Content-Type": "text/plain"}

    publisher.publish(TOPIC, request.get_data())
    return "", 202
```

### 5. `scripts/worker.py` — GKE pod entry point

```python
from google.cloud import pubsub_v1
import os, json

from services import ingestion
from repo import messages, classifications, senders
from clients import db
# existing classification (Phase 1 only — replaced in Phase 3)
from services.email_processor import EmailProcessor

def process(message):
    payload = json.loads(message.data)
    if payload.get("changeType") != "created":
        return

    msg = ingestion.normalize(payload)

    if messages.exists(msg["source"], msg["external_id"]):
        return

    msg_id = messages.insert(msg)
    senders.upsert(msg["sender"], msg["source"])

    # Phase 1: run existing classification unchanged
    # Replaced by handlers/pipeline.py in Phase 3
    ...

def main():
    subscriber = pubsub_v1.SubscriberClient()
    subscription = subscriber.subscription_path(
        os.environ["GCP_PROJECT_ID"], "inbox-messages-pull"
    )
    with subscriber:
        future = subscriber.subscribe(subscription, callback=lambda m: (process(m), m.ack()))
        future.result()

if __name__ == "__main__":
    main()
```

### 6. GKE infrastructure (`~/src/infra/k8s/inbox/`)

**`deployment.yaml`**:
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: inbox-processor
  namespace: apps
spec:
  replicas: 0          # KEDA manages this
  selector:
    matchLabels:
      app: inbox-processor
  template:
    metadata:
      labels:
        app: inbox-processor
    spec:
      containers:
      - name: inbox-processor
        image: # Artifact Registry image URL
        command: ["python", "scripts/worker.py"]
        envFrom:
        - secretRef:
            name: postgres-credentials
        env:
        - name: GCP_PROJECT_ID
          value: "bens-project-462804"
        resources:
          requests:
            cpu: "1"
            memory: "2Gi"
```

**`keda-scaledobject.yaml`**:
```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: inbox-processor
  namespace: apps
spec:
  scaleTargetRef:
    name: inbox-processor
  minReplicaCount: 0
  maxReplicaCount: 3
  triggers:
  - type: gcp-pubsub
    metadata:
      subscriptionName: inbox-messages-pull
      mode: SubscriptionSize
      value: "1"
```

### 7. GCP infrastructure (`terraform/`)

New files:

- **`cloud_functions.tf`** — HTTP-triggered Cloud Function deploying `handlers/webhook.py`; a second function for subscription renewal
- **`pubsub.tf`** — `inbox-messages` topic + `inbox-messages-pull` pull subscription
- **`scheduler.tf`** (update) — add renewal job: calls the renewal Cloud Function every 2 days

### 8. Register the Graph subscription

After deploying the Cloud Function, run once:

```python
from clients.graph_subscriptions import register
register(notification_url="https://<cloud-function-url>/webhook")
```

### 9. `requirements.txt` additions

```
psycopg[binary]>=3.1
pgvector>=0.2
functions-framework>=3.0
google-cloud-pubsub>=2.18
```

### Phase 1 deliverable

Event-driven GKE worker receives messages, writes to DB, runs existing gpt-4o-mini classification. Existing Cloud Run Job stays as daily fallback.

---

## Phase 2: Embeddings + retrieval

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

### Docker image

Add a `Dockerfile` for the GKE worker (separate from `Dockerfile.analyze-emails`). Bake the bge model into the image at build time so the pod doesn't download it on cold start:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONPATH=/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download bge model at build time
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-en-v1.5')"

COPY . .
CMD ["python", "scripts/worker.py"]
```

### Wire into `scripts/worker.py`

Load the model once at startup, embed after each DB write:

```python
from clients.bge import load_model
from services.embedding import text_for_embedding, embed_and_store
from repo.embeddings import retrieve_neighbors

model = load_model()  # once at startup

def process(message):
    ...
    msg_id = messages.insert(msg)
    senders.upsert(...)

    cleaned = text_for_embedding(msg)
    embed_and_store(msg_id, cleaned, model)

    neighbors = retrieve_neighbors(model.encode(cleaned), exclude_id=msg_id)
    log.info(f"Retrieved {len(neighbors)} neighbors for {msg_id}")
```

### `requirements.txt` addition

```
sentence-transformers>=2.7
```

### Phase 2 deliverable

Every new message gets an embedding stored in `message_embeddings`. Retrieval runs and logs top-k neighbors. No change to classification behavior.

---

## Phase 3: New prompt + categories + tags

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

Add `anthropic-api-key` to Secret Manager and inject into the GKE pod. Keep `openai-api-key` until Phase 5.

### Replace `scripts/worker.py` classification logic

```python
from handlers.pipeline import run as run_pipeline

model = load_model()  # bge, once at startup

def process(message):
    payload = json.loads(message.data)
    if payload.get("changeType") != "created":
        return
    run_pipeline(payload, model)
```

### Remove in this phase

- `services/email_analyzer.py`
- `services/email_processor.py`
- `models/email.py`

Update `requirements.txt`: add `anthropic>=0.25`; remove `langchain-openai`, `langchain-core`.

### Phase 3 deliverable

End-to-end new classification pipeline live. 5 categories, tags, retrieval-augmented prompt, Claude Sonnet. Old 8-category path removed.

---

## Phase 4: Feedback mechanism + action handlers

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

### Add `/label` route to `handlers/webhook.py`

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

### New Pub/Sub topic + subscription

Add to `terraform/pubsub.tf`:
- `inbox-labels` topic
- `inbox-labels-pull` pull subscription

### Update `scripts/worker.py`

Pull from both subscriptions. Route by topic:

```python
def process(message, topic):
    if topic == "inbox-messages-pull":
        handle_message(message)
    elif topic == "inbox-labels-pull":
        handle_label(message)

def handle_label(message):
    payload = json.loads(message.data)
    labeling.apply_label(
        message_id=payload["message_id"],
        label=payload["label"],
        source=payload["source"],
    )
```

### Update KEDA `ScaledObject`

Watch both subscriptions:

```yaml
triggers:
- type: gcp-pubsub
  metadata:
    subscriptionName: inbox-messages-pull
    mode: SubscriptionSize
    value: "1"
- type: gcp-pubsub
  metadata:
    subscriptionName: inbox-labels-pull
    mode: SubscriptionSize
    value: "1"
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
