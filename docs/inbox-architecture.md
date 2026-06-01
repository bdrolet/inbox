# Inbox: Unified Message Triage — Architecture & Migration Plan

## 1. Current State Analysis

### What the existing system does

A Python Cloud Run Job that runs daily at 8 AM EST. It:

1. Connects to an Outlook/Office 365 mailbox via Microsoft Graph API (MSAL auth)
2. Fetches all emails in the inbox (`process_all_emails()`)
3. Passes each email to GPT-4o-mini via langchain-openai with a structured prompt
4. Receives a JSON classification and optional folder-move
5. Prints results to stdout (captured by Cloud Logging); no results are persisted

### File inventory

| File | Role |
|------|------|
| `scripts/analyze_emails.py` | Entry point; detects headless mode via `GCP_PROJECT_ID`; hardcoded placeholder user context |
| `clients/azure/graph_email_client.py` | Microsoft Graph API wrapper; MSAL device-code auth locally, token from Secret Manager in Cloud Run; email fetch, folder creation, folder move, mark-read |
| `services/email_analyzer.py` | LLM classification; gpt-4o-mini via langchain-openai (temp 0.3, max_tokens 500); prompt construction; JSON response parsing |
| `services/email_processor.py` | Orchestrator; `ContextManager` loads `email_context.json`; per-email analysis context; calls `EmailAnalyzer`; optionally moves to Outlook action folders |
| `models/email.py` | `Email` dataclass |

### Current classification

**LLM**: `gpt-4o-mini` via `langchain-openai`

**8 `ActionType` values** (map directly to Outlook folder display names):
`reply_required`, `urgent_attention`, `schedule_meeting`, `review_document`, `approve_request`, `follow_up`, `archive`, `no_action`

**Response schema**:
```json
{
  "action_type": "...",
  "priority": 1,
  "confidence": 0.85,
  "reasoning": "...",
  "suggested_response": "...",
  "deadline": null,
  "tags": []
}
```

### State between runs

The only persistent state is the MSAL token cache in Secret Manager (`msal-token-cache`). Analysis results are not stored anywhere. Each run classifies the full inbox from scratch.

### Infrastructure

- **Runtime**: Cloud Run Job `email-analysis`, GCP project `bens-project-462804`, `us-central1`
- **Schedule**: Cloud Scheduler `0 8 * * *` daily, `America/New_York`, OAuth-authenticated HTTP
- **Container**: `python:3.11-slim`, Artifact Registry repo `email-analysis`
- **Secrets**: Secret Manager — `client-id`, `client-secret`, `tenant-id`, `openai-api-key`, `msal-token-cache`
- **IaC**: Full Terraform in `terraform/`

### What to keep

- Graph API auth machinery (interactive + headless) — well-implemented, handles token refresh
- Secret Manager for secrets, Terraform structure
- `models/email.py` as the starting point for the common `Message` shape

### What to replace or extend

- `langchain-openai` / GPT-4o-mini → `anthropic` SDK / Claude Sonnet
- 8 flat action types → 5-category system + orthogonal tag array
- Stateless → persistent Postgres with pgvector
- Cron batch job → event-driven webhook processing
- Monolithic processor → layered architecture (clients / repo / services / handlers)

---

## 2. Target Architecture

### System diagram

```
  Microsoft Graph API
        │  POST — change notification
        ▼
  Cloud Function (HTTP trigger)      ← always-on serverless; responds HTTP 202 in <100ms
  functions/webhook/main.py            handles Graph validation handshake (10s window)
  ├── GET  ?validationToken=...  ── echo token
  ├── POST /webhook ── publish payload to Pub/Sub
  └── POST /label   ── receive ntfy.sh button callback → publish to inbox-labels topic
        │
        ▼
  Cloud Pub/Sub (inbox-messages / inbox-labels)   ← push trigger (CF event trigger)
        │
        │  Pub/Sub pushes to Cloud Function on each message
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│          Cloud Function — inbox-process  (Pub/Sub event trigger)     │
│                                                                      │
│  main.py  ── process(cloud_event)                                    │
│           │                                                          │
│           ▼                                                          │
│  handlers/pipeline.py (16-step orchestrator)                         │
│                                                                      │
│  services/ingestion.py ──[extract from notification]──► models/message
│           │                                                          │
│           ▼                                                          │
│  repo/messages.py  ──────────────────────────────────────────────►   │
│  repo/senders.py   ──────────────────────────────────────────────►   │
│           │                                                          │
│           ▼                                                          │
│  services/embedding.py                                               │
│  └── clients/bge.py (bge-small-en-v1.5, loaded once at startup) ──► │
│  └── repo/embeddings.py ────────────────── (message_embeddings)      │
│           │                                                          │
│           ▼                                                          │
│  repo/embeddings.retrieve_neighbors() ───────────────────────────►   │
│           │                                                          │
│           ▼                                                          │
│  services/classification.py ── build_prompt()                        │
│  └── clients/claude.py ──────────────────────────────────────────► Anthropic API
│  └── repo/classifications.py ────────────────────────────────────►   │
│  └── repo/tags.py ───────────────────────────────────────────────►   │
│           │                                                          │
│           ▼                                                          │
│  handlers/actions/dispatch.py                                        │
│  └── handlers/actions/urgent.py    → clients/ntfy.py ─────────────► ntfy.sh
│  └── handlers/actions/respond.py  ─┐                                │
│  └── handlers/actions/review.py   ─┤ services/archiving.py ───────► Graph API (folder move)
│  └── handlers/actions/reference.py─┤                                │
│  └── handlers/actions/ignore.py   ─┘                                │
└──────────────────────────────────────────────────────────────────────┘
          │                                       │
          ▼                                       ▼
  Cloud SQL (Postgres 16 + pgvector)    Secret Manager
  (managed; Cloud SQL Python Connector) ├── client-id, client-secret, tenant-id
  ├── messages                          ├── anthropic-api-key
  ├── message_embeddings                ├── inbox-db-password
  ├── classifications                   └── msal-token-cache
  ├── senders
  └── tags

  Cloud Scheduler → Cloud Function      ← runs every 2 days
  clients/graph_subscriptions.renew()     to renew Graph subscription before expiry
```

The webhook Cloud Function is the only public-internet-facing component. The processor Cloud Function is triggered by Pub/Sub push (not publicly exposed). The Graph validation handshake (10-second window) is handled entirely by the webhook CF.

Future ingestion services (`services/sms_ingestion.py`, `services/voicemail_ingestion.py`) publish to the same Pub/Sub topic with a `source` field; the processor routes by source without changes to the pipeline.

### Cost

| Component | Monthly cost |
|-----------|-------------|
| Cloud Function webhook (HTTP trigger) | Free tier; ~$0 |
| Cloud Function processor (Pub/Sub trigger, ~100 emails/day) | Free tier; ~$0 |
| Cloud Pub/Sub | Free up to 10GB/month; ~$0 |
| Cloud SQL db-f1-micro (Postgres 16 + pgvector) | ~$10 |
| Claude Sonnet (~100 emails/day) | ~$15 |
| Cloud Scheduler (renewal job) | Free tier; ~$0 |
| **Total new cost** | **~$25/month** |

---

## 3. End-to-End Flow

Steps when a new message arrives, each a function call in `handlers/pipeline.py`.

### Step 1 — Receive message from source

**Trigger**: Microsoft Graph POSTs a rich change notification to the Cloud Function. The subscription is registered with `includeResourceData: true` and `$select=subject,from,body,receivedDateTime,toRecipients,hasAttachments`, so the notification payload contains the full message content — no separate Graph API fetch needed.

The Cloud Function responds HTTP 202 immediately and publishes the payload to the `inbox-messages` Pub/Sub topic. The GKE worker pulls it via the `inbox-messages-pull` subscription. Only `changeType = "created"` notifications are processed; updates and deletes are discarded.

Future SMS/voicemail ingestion services publish to the same topic with a `source` field; `handlers/pipeline.py` routes by source.

### Step 2 — Normalize to common Message shape

**Function**: `services.ingestion.normalize(raw) → Message`

Extracts `sender`, `sender_display`, `subject`, `body`, `received_at`, `thread_id`, `external_id` from the Graph payload and maps them to the `models/message.py` TypedDict. Body is extracted as plain text (strips HTML). Stores the original payload in `raw` for the audit trail. Subject is set to `""` rather than `None` if absent.

### Step 3 — Deduplicate

**Function**: `repo.messages.exists(source, external_id) → bool`

Checks `messages` for an existing row with `(source, external_id)`. If found, the message has already been processed — skip. Makes the pipeline fully idempotent.

### Step 4 — Write to messages table

**Function**: `repo.messages.insert(msg) → UUID`

Inserts a new row, generating the internal `id` UUID. Stores the full `Message` including the raw JSONB blob. This is the canonical record — embeddings and classifications reference it by `id`.

### Step 5 — Clean body for embedding

**Function**: `services.embedding.text_for_embedding(msg) → str` (local variable only, not written to DB)

Strips quoted reply chains (`> On Tuesday...` patterns) and signature blocks from `msg.body`. Truncates to 1500 characters. Prepends sender and subject: `f"From: {sender}\nSubject: {subject}\n\n{cleaned_body}"`. No-op for SMS and voicemail. The original `msg.body` is never modified.

### Step 6 — Update sender stats

**Function**: `repo.senders.upsert(msg.sender, msg.source)`

Inserts or updates the sender's row. Sets `first_seen` on first message. Increments `message_count`. Does not touch `my_response_count` — updated separately when outbound responses are tracked (Phase 6+).

### Step 7 — Generate and store embedding

**Function**: `services.embedding.embed_and_store(msg, cleaned_text)`

Passes `cleaned_text` to `clients/bge.py` which runs it through `bge-small-en-v1.5` and returns a 384-dimensional numpy array (~50ms). Inserts the vector into `message_embeddings` with `current_label = NULL`. The NULL is intentional — LLM-assigned labels never populate this column; only human feedback does.

### Step 8 — Retrieve nearest neighbors

**Function**: `repo.embeddings.retrieve_neighbors(msg, k=10) → list[dict]`

Runs a cosine-distance query using the `<=>` pgvector operator. Returns up to 10 rows where `current_label IS NOT NULL` and `message_id != msg.id`, ordered by distance. Each row includes `subject`, `body`, `sender`, `current_label`, and `similarity` (1 − distance). Returns an empty list on cold start — the classifier handles zero neighbors gracefully.

### Step 9 — Aggregate retrieval results

**Function**: `services.classification.aggregate_neighbors(rows) → dict`

Groups the 10 neighbor rows by `current_label`. For each category present, computes count, max similarity, and average similarity. Also identifies the top 3 rows overall to include as full examples in the prompt.

### Step 10 — Look up sender context

**Function**: `repo.senders.get(msg.sender, msg.source) → dict`

Fetches `message_count`, `my_response_count`, `relationship_label`, `notes`. Passed to the prompt as plain text. A sender seen 50 times with `relationship_label = "family"` gets very different weight than a first-time unknown sender.

### Step 11 — Build LLM prompt

**Function**: `services.classification.build_prompt(msg, aggregates, top_examples, sender_ctx) → str`

Assembles the full prompt from four parts:
1. **System prompt** — defines the 5 categories with descriptions, the tag controlled vocabulary, and the required JSON output schema
2. **Sender context** — message count, response rate, relationship label, notes
3. **Retrieval context** — per-category aggregates table + top 3 raw examples with human-confirmed labels
4. **Current message** — subject, sender, received time, and body (truncated to ~1500 chars)

If `top_examples` is empty (cold start), the retrieval section is omitted entirely.

### Step 12 — Call LLM

**Function**: `clients.claude.call(prompt) → dict`

Calls Claude Sonnet via the Anthropic API. Returns structured JSON:

```json
{
  "category": "urgent | respond | review | reference | ignore",
  "confidence": 0.0,
  "alternatives": { "urgent": 0.0, "respond": 0.0, "review": 0.0, "reference": 0.0, "ignore": 0.0 },
  "tags": ["topic:finances", "from:family"],
  "reasoning": "one sentence"
}
```

Raises on invalid JSON or missing `category`. Temperature 0.2 for consistent structured output.

### Step 13 — Write classification

**Function**: `repo.classifications.insert(msg.id, result, source='llm')`

Inserts `category`, `confidence`, `alternatives` (JSONB), `tags` (TEXT[]), `reasoning`, model name, prompt version. `source = 'llm'`. Does **not** update `message_embeddings.current_label` — that column is only set by human feedback.

### Step 14 — Update tags table

**Function**: `repo.tags.ensure_exists(result.tags)`

Inserts any tags not already in `tags`. Lets the controlled vocabulary grow organically while staying queryable.

### Step 15 — Take per-category action

**Function**: `handlers.actions.dispatch.handle(msg, result)`

| Category | Handler | Action |
|----------|---------|--------|
| `urgent` | `handlers/actions/urgent.py` | ntfy.sh push notification — subject, sender, reasoning, action buttons (`✓ Correct`, `↓ Respond`, `↓ Review`) |
| `respond` | `handlers/actions/respond.py` | Move to "To Respond" Outlook folder |
| `review` | `handlers/actions/review.py` | Move to "To Review" Outlook folder |
| `reference` | `handlers/actions/reference.py` | Mark read + move to Archive folder |
| `ignore` | `handlers/actions/ignore.py` | Move to Archive folder |

`respond`, `review`, `reference`, and `ignore` all call `services/archiving.move_to_folder()`. ntfy.sh action button callbacks POST to the Cloud Function's `/label` route, which publishes to `inbox-labels` Pub/Sub; the worker calls `services/labeling.apply_label()`, setting `message_embeddings.current_label` and inserting a `human_confirmation` or `human_correction` row in `classifications`.

### Step 16 — Return

Pipeline complete. The message is stored, embedded, classified, tagged, and acted on.

---

## 4. Migration Plan

Each phase is independently deployable. Phases 1–2 preserve existing behavior.

### Phase 1: Schema + storage layer

**Goal**: Add the database and write to it. Keep the existing Cloud Run Job running as a fallback while the new event-driven worker is stood up.

1. Enable pgvector on GKE Postgres: run `CREATE EXTENSION IF NOT EXISTS vector;` as a one-shot Kubernetes Job against `postgres.apps.svc.cluster.local`
2. Add DB credentials to a k8s Secret in the `apps` namespace
3. Write `clients/db.py` — psycopg connection to `postgres.apps.svc.cluster.local`
4. Write `repo/schema.sql` — all 5 tables per Section 6
5. Write `scripts/migrate_db.py` — one-shot schema migration; run as a Kubernetes Job
6. Write `models/message.py` — common `Message` TypedDict
7. Write `services/ingestion.py` — `normalize(raw_notification)` extracting fields from the Graph rich notification payload
8. Write `repo/messages.py`, `repo/classifications.py`, `repo/senders.py`
9. Write `handlers/webhook.py` — Cloud Function: `GET` echoes `validationToken`; `POST` publishes to Pub/Sub and returns HTTP 202
10. Write `scripts/worker.py` — GKE pod entry point: Pub/Sub pull loop → `services/ingestion.normalize()` → DB write → existing gpt-4o-mini classification
11. Write `clients/graph_subscriptions.py` — `register()` and `renew()`
12. Install KEDA on the GKE cluster via Helm
13. Add Terraform: `cloud_functions.tf` (webhook CF + renewal CF), `pubsub.tf` (topic + pull subscription), update `scheduler.tf` (renewal job); add `infra/k8s/inbox/deployment.yaml`, `keda-scaledobject.yaml`
14. Update `requirements.txt`: add `psycopg[binary]`, `pgvector`, `functions-framework`, `google-cloud-pubsub`
15. Register the Graph subscription with `includeResourceData: true` pointing at the Cloud Function URL

**Deliverable**: Event-driven GKE worker receives messages via Pub/Sub, scales 0→1 with KEDA, writes to GKE Postgres. Existing Cloud Run Job stays as daily fallback until Phase 5.

### Phase 2: Embeddings + retrieval

**Goal**: Embed each new message and implement retrieval. Results are logged but not yet used in the prompt.

1. Write `clients/bge.py` — `load_model()`, `encode()`; model loads once at pod startup
2. Write `services/embedding.py` — `text_for_embedding()`, `strip_reply_chain()`, `strip_signature()`, `embed_and_store()`
3. Write `repo/embeddings.py` — `store_embedding()`, `retrieve_neighbors()` using psycopg + pgvector
4. Update `requirements.txt`: add `sentence-transformers`
5. Add `Dockerfile` for the GKE worker image; bake bge model in during build (~400MB image, avoids cold-start download)
6. Wire `services/embedding.embed_and_store()` and `repo/embeddings.retrieve_neighbors()` into `scripts/worker.py`
7. Log retrieval results; don't feed into prompt yet

**Deliverable**: Every new message gets an embedding stored; retrieval runs and logs top-k neighbors for validation.

### Phase 3: New prompt + categories + tags

**Goal**: Switch to Claude Sonnet, new 5-category system, tag extraction, retrieval-augmented prompt.

1. Write `models/types.py` — `Category` enum, `Classification` dataclass
2. Write `services/classification.py` — `build_prompt()`, `aggregate_neighbors()`, tag controlled vocabulary
3. Write `clients/claude.py` — Anthropic SDK call, structured JSON response parsing
4. Write `services/labeling.py` — `apply_label()` for human correction/confirmation path
5. Write `handlers/pipeline.py` — full 16-step orchestrator
6. Add `anthropic-api-key` to Secret Manager and k8s Secret; keep `openai-api-key` until Phase 5
7. Update `requirements.txt`: add `anthropic`; remove `langchain-openai`, `langchain-core`
8. Update `scripts/worker.py` to call `handlers.pipeline.run()` instead of the old classification logic
9. Remove `services/email_analyzer.py`, `services/email_processor.py`; remove `models/email.py`

**Deliverable**: End-to-end new classification system live; old 8-category path removed.

### Phase 4: Feedback mechanism + action handlers

**Goal**: Human corrections flow back to the vector store; all five per-category actions implemented.

1. Write `services/archiving.py` — `move_to_folder(msg, folder_name)` via Graph API; shared by respond, review, reference, and ignore handlers
2. Write `handlers/actions/dispatch.py` and all five handlers:
   - `urgent.py` — ntfy.sh notification with subject, sender, reasoning, and action buttons pointing to CF `/label`
   - `respond.py` — `archiving.move_to_folder(msg, "To Respond")`
   - `review.py` — `archiving.move_to_folder(msg, "To Review")`
   - `reference.py` — `archiving.move_to_folder(msg, "Archive")`
   - `ignore.py` — `archiving.move_to_folder(msg, "Archive")`
3. Write `clients/ntfy.py` — POST to `https://ntfy.sh/{topic}` with action buttons configured
4. Add `/label` route to `handlers/webhook.py` — receives ntfy.sh button callback, publishes to `inbox-labels` Pub/Sub topic
5. Add `inbox-labels` topic + pull subscription to `terraform/pubsub.tf`
6. Update `scripts/worker.py` to pull from both `inbox-messages-pull` and `inbox-labels-pull`; call `services/labeling.apply_label()` on label events
7. Update KEDA `ScaledObject` to watch both subscriptions

**Deliverable**: All five categories drive folder moves or notifications; urgent messages include ntfy.sh action buttons; human corrections flow back to `message_embeddings.current_label`.

### Phase 5: Bootstrap + go-live

**Goal**: Seed the vector store and decommission the old system.

1. Write `scripts/bootstrap_labels.py` — iterate past inbox messages with interactive or CSV bulk-import labeling; insert into `message_embeddings` with `current_label` set
2. Hand-label 30–50 messages spanning all 5 categories
3. Run the new pipeline in shadow mode (classifies but does not act); compare output against old system
4. Decommission: delete `openai-api-key` Secret Manager secret, remove `Dockerfile.analyze-emails`, delete Cloud Run Job and Cloud Scheduler resources from Terraform
5. Remove `scripts/analyze_emails.py`

**Deliverable**: New system fully live; vector store seeded; old system decommissioned.

### Phase 6+

- `services/sms_ingestion.py` — Twilio or Google Voice
- `services/voicemail_ingestion.py` — audio → Whisper → `Message`
- Cross-channel contacts unification

### v2 backlog

- `respond` and `review` digests / summaries
- Snooze / defer (`snoozed_until` on messages; re-surface after N days)
- Misrouting surfacing (weekly patterns summary)
- Web UI for corrections

---

## 5. Decisions Log

| Decision | Choice |
|----------|--------|
| Categories | `urgent`, `respond`, `review`, `reference`, `ignore` |
| Tag system | Orthogonal to categories; multiple per message; controlled vocabulary, extensible; same LLM call |
| Embedding model | BAAI/bge-small-en-v1.5 — 384d, ~130MB, ~50ms/msg, in-process via sentence-transformers |
| Vector store | pgvector in Cloud SQL Postgres — no separate vector DB |
| Human-only feedback | `current_label` set only on human confirm/correct; LLM labels never enter the retrieval pool |
| LLM | Claude Sonnet via Anthropic API; replaces gpt-4o-mini |
| Worker topology | Cloud Function (Pub/Sub event trigger); webhook CF handles public endpoint |
| Database | Cloud SQL Postgres 16 + pgvector (db-f1-micro); Cloud SQL Python Connector |
| Event trigger | Microsoft Graph change notifications; message ID in payload; worker fetches full message |
| Notification | ntfy.sh; includes reasoning; action buttons for confirm/correct |
| Folder actions | All non-urgent categories move to Outlook folders via Graph API (`Mail.ReadWrite` already granted) |
| Infra split | GCP resources in `inbox/terraform/`; no k8s manifests for inbox |
| Bootstrap requirement | 30–50 hand-labeled messages before Phase 5 go-live |

---

## 6. Database Schema

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE messages (
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

CREATE TABLE message_embeddings (
  message_id UUID PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
  embedding vector(384) NOT NULL,
  current_label TEXT,          -- NULL until human confirms or corrects; never set by LLM
  updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON message_embeddings USING hnsw (embedding vector_cosine_ops);

CREATE TABLE classifications (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  message_id UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  category TEXT NOT NULL,
  confidence FLOAT,
  alternatives JSONB,
  tags TEXT[],
  reasoning TEXT,
  model TEXT,
  prompt_version TEXT,
  source TEXT NOT NULL,        -- 'llm' | 'human_correction' | 'human_confirmation'
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON classifications (message_id, created_at DESC);

CREATE TABLE senders (
  identifier TEXT NOT NULL,
  source TEXT NOT NULL,
  first_seen TIMESTAMPTZ,
  message_count INT DEFAULT 0,
  my_response_count INT DEFAULT 0,
  relationship_label TEXT,
  notes TEXT,
  PRIMARY KEY (source, identifier)
);

CREATE TABLE tags (
  name TEXT PRIMARY KEY,
  description TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);
```

---

## 7. Module Structure

```
inbox/
├── main.py                      # Processor Cloud Function entry point (Pub/Sub event trigger)
│
├── scripts/
│   ├── analyze_emails.py        # existing Cloud Run Job entry point — kept until Phase 5
│   ├── seed_token_cache.py      # one-time MSAL token setup
│   ├── migrate_db.py            # one-shot schema migration
│   └── bootstrap_labels.py     # Phase 5: seed message_embeddings with hand-labeled examples
│
├── models/                      # shared types — no logic
│   ├── email.py                 # existing — deprecated in Phase 3
│   ├── message.py               # Phase 1: Message TypedDict
│   └── types.py                 # Phase 3: Category enum, Classification dataclass
│
├── clients/                     # connections to external services and dependencies
│   ├── azure/
│   │   └── graph_email_client.py     # Graph API wrapper; token cached at ~/.inbox-token-cache.json
│   ├── graph_subscriptions.py   # register() + renew() change-notification subscriptions
│   ├── db.py                    # psycopg connection; Cloud SQL Python Connector in production
│   ├── bge.py                   # Phase 2: sentence-transformers model load + encode()
│   ├── claude.py                # Phase 3: Anthropic SDK call + response parsing
│   └── ntfy.py                  # Phase 4: push notification via ntfy.sh
│
├── repo/                        # read/write to app-owned database tables
│   ├── schema.sql               # CREATE TABLE + CREATE INDEX statements
│   ├── messages.py              # insert(), exists(), get()
│   ├── classifications.py       # insert()
│   ├── senders.py               # upsert(), get()
│   ├── embeddings.py            # Phase 2: store_embedding(), retrieve_neighbors(), apply_label()
│   └── tags.py                  # Phase 3: ensure_exists()
│
├── services/                    # business logic — one concern per file
│   ├── ingestion.py             # normalize Graph notification → Message + upsert sender
│   ├── embedding.py             # Phase 2: text_for_embedding(), strip reply chains, embed + store
│   ├── classification.py        # Phase 3: retrieve neighbors, build prompt, call Claude, write result
│   ├── labeling.py              # Phase 4: apply_label() — human correction/confirmation path
│   └── archiving.py             # Phase 4: move_to_folder(msg, folder_name) via Graph API
│
├── handlers/                    # orchestration across multiple services
│   ├── pipeline.py              # Phase 3: 16-step orchestrator
│   └── actions/
│       ├── dispatch.py          # Phase 4: route by category
│       ├── urgent.py            # Phase 4: ntfy.sh notification + action buttons
│       ├── respond.py           # Phase 4: move to "To Respond"
│       ├── review.py            # Phase 4: move to "To Review"
│       ├── reference.py         # Phase 4: move to Archive
│       └── ignore.py            # Phase 4: move to Archive
│
├── functions/                   # standalone Cloud Function entry points
│   ├── webhook/                 # HTTP trigger: Graph validation + Pub/Sub publish
│   └── renew/                   # HTTP trigger: Graph subscription renewal
│
├── terraform/                   # GCP resources (Cloud Functions, Pub/Sub, Cloud SQL, Scheduler, Secrets)
│   ├── main.tf
│   ├── cloud_functions.tf       # webhook CF + renew CF + processor CF
│   ├── cloudsql.tf              # Cloud SQL Postgres 16 instance
│   ├── pubsub.tf                # inbox-messages topic
│   ├── cloud_run_job.tf         # existing Cloud Run Job — removed in Phase 5
│   ├── scheduler.tf             # daily Cloud Run Job + subscription renewal job
│   ├── secrets.tf               # Secret Manager secrets
│   ├── iam.tf                   # service account bindings
│   ├── registry.tf
│   ├── variables.tf
│   ├── outputs.tf
│   ├── project.auto.tfvars
│   └── terraform.tfvars.example
│
├── docs/
│   └── inbox-architecture.md   # this file
│
├── Dockerfile.analyze-emails    # existing Cloud Run Job — removed in Phase 5
├── requirements.txt
└── .dockerignore
```
