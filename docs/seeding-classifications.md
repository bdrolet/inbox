# Seeding Classifications

Before Phase 3's retrieval-augmented prompt can provide useful context, the vector store needs a baseline set of human-labeled examples. Without labels, `message_embeddings.current_label` is NULL for every row, and the retrieval query returns nothing.

## How many to label

Target **60–75 emails** (12–15 per category). The five categories are:

| Category | Description |
|----------|-------------|
| `urgent` | Needs attention today; will trigger a push notification |
| `respond` | Needs a reply, not today |
| `review` | Worth reading, no reply needed |
| `reference` | Keep but don't read now |
| `ignore` | Marketing/noise |

Your inbox will be skewed — probably lots of `ignore` and `reference`, fewer `urgent`. Be deliberate about labeling enough examples in the rarer categories. `urgent` especially matters since it drives the notification path.

Fewer than ~10 per category is workable but expect weaker retrieval quality early on. Beyond ~100 total, returns diminish quickly.

## What the script needs from you

For each email shown, you assign one of the five categories above. The script reads from the `messages` + `message_embeddings` tables (populated by Phases 1 & 2) and writes labels via `services/labeling.apply_label`.

```python
import os, textwrap
from clients.db import get_conn
from services.labeling import apply_label

SHORTCUTS = {"u": "urgent", "r": "respond", "v": "review", "e": "reference", "i": "ignore"}

def fetch_unlabeled(conn):
    return conn.execute(
        """
        SELECT m.id, m.sender, m.subject, m.received_at, m.body
        FROM messages m
        JOIN message_embeddings me ON me.message_id = m.id
        WHERE me.current_label IS NULL
        ORDER BY m.received_at DESC
        """
    ).fetchall()

def prompt_label(msg, idx, total):
    print(f"\n[{idx}/{total}] {msg['received_at'].strftime('%Y-%m-%d')}  {msg['sender']}")
    print(f"Subject: {msg['subject']}")
    print(textwrap.fill(msg['body'][:800], width=80))
    print("\n  u=urgent  r=respond  v=review  e=reference  i=ignore  s=skip  q=quit")
    return input("> ").strip().lower()

with get_conn() as conn:
    rows = fetch_unlabeled(conn)
    counts = {c: 0 for c in SHORTCUTS.values()}

    for idx, msg in enumerate(rows, 1):
        raw = prompt_label(msg, idx, len(rows))
        if raw == "q":
            break
        if raw == "s":
            continue
        label = SHORTCUTS.get(raw, raw)
        if label not in SHORTCUTS.values():
            print("Unrecognized — skipping.")
            continue
        # writes current_label to message_embeddings (enables retrieval) + inserts a classifications audit row
        apply_label(str(msg["id"]), label, source="human_confirmation")
        counts[label] += 1
        print(f"  ✓ {label}  ({', '.join(f'{k}: {v}' for k, v in counts.items() if v)})")
```

## Running the bootstrap script

Make sure your local DB env vars are set (see CLAUDE.md → Local development), then:

```bash
source .venv/bin/activate
python scripts/bootstrap_labels.py
```

Each email is displayed with sender, subject, received time, and a truncated body. Enter the first letter of the category (`u` / `r` / `v` / `e` / `i`) or the full word, then press Enter. Type `s` to skip an email without labeling it.

The script processes emails in reverse-chronological order and skips any message that already has a `current_label`.

## What happens under the hood

`apply_label(message_id, label, source="human_confirmation")` does two things atomically:

1. Sets `current_label` on the `message_embeddings` row — this is what makes the embedding eligible for retrieval
2. Inserts a row into `classifications` recording the label and its source

The retrieval query (`retrieve_neighbors`) only checks `current_label IS NOT NULL`. It never reads `source`. So `source` is purely an audit trail — useful later if you want to query "how often did my corrections disagree with the LLM?" but ignored by the pipeline today.

Use `source="human_confirmation"` for bootstrap labels. It fits the existing contract and is semantically accurate — you're confirming a label, just doing it upfront rather than in response to a notification.

## Checking your progress

```sql
SELECT current_label, count(*)
FROM message_embeddings
WHERE current_label IS NOT NULL
GROUP BY current_label
ORDER BY count DESC;
```

Run this against the DB (via `/psql-database` or directly) to see how many labeled examples exist per category before running the pipeline.

## Relationship to Phase 4

The bootstrap set is a one-time seed. Once Phase 4 is live, every ntfy.sh action button tap (confirm / correct) calls the same `apply_label` path with `source = "human_confirmation"` or `"human_correction"`, so the vector store grows automatically over time.
