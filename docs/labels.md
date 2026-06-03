# Labels: how they work

A label is a category assigned to a message. Labels drive two things: what happens to the email (folder move or notification) and whether the message becomes a retrieval example for future classifications.

---

## The five categories

| Category | Action | Triggers notification |
|----------|--------|-----------------------|
| `urgent` | No folder move — notification sent via ntfy.sh | Yes |
| `respond` | Moved to "To Respond" | No |
| `review` | Moved to "To Review" | No |
| `reference` | Archived | No |
| `ignore` | Archived | No |

---

## How a label gets assigned

### LLM path (every new message)

1. `services/classification.build_prompt()` assembles a prompt with sender history, retrieval examples, and the message body
2. `clients/claude.classify()` calls Claude Sonnet and parses the JSON response into a `Classification` dataclass
3. The result is written to `classifications` via `repo/classifications.insert()` with `source="llm"`, along with confidence, alternatives, tags, and reasoning
4. **`current_label` is NOT set** — LLM guesses never enter the retrieval pool

### Human path (Phase 4 + bootstrap)

1. A human assigns a label — either via a ntfy.sh action button tap or the bootstrap script
2. `services/labeling.apply_label(message_id, label, source)` is called
3. Two writes happen atomically:
   - `message_embeddings.current_label` is set — this makes the message eligible for retrieval
   - A row is inserted into `classifications` with `source="human_confirmation"` or `"human_correction"`

---

## Where labels are stored

### `classifications` table

Every label ever assigned to a message, LLM or human. One row per classification event.

| Column | LLM | Human |
|--------|-----|-------|
| `message_id` | ✓ | ✓ |
| `category` | ✓ | ✓ |
| `source` | `"llm"` | `"human_confirmation"` / `"human_correction"` |
| `confidence` | ✓ | NULL |
| `alternatives` | ✓ | NULL |
| `tags` | ✓ | NULL |
| `reasoning` | ✓ | NULL |
| `model` | ✓ | NULL |

### `message_embeddings` table

One row per message. Holds the embedding vector and `current_label`.

`current_label` is only ever set by a human action. It is the single field that controls whether a message is eligible as a retrieval example.

---

## How retrieval works

When classifying a new message, `repo/embeddings.retrieve_neighbors()` runs:

```sql
SELECT m.subject, m.body, m.sender,
       me.current_label,
       1 - (me.embedding <=> %s) AS similarity
FROM message_embeddings me
JOIN messages m ON m.id = me.message_id
WHERE me.current_label IS NOT NULL   -- human-labeled only
  AND me.message_id != %s
ORDER BY me.embedding <=> %s
LIMIT 10
```

The top-k results are passed to `build_prompt()` as few-shot examples — the prompt shows Claude what similar emails were labeled by a human. The `source` column from `classifications` is never read here; only `current_label` matters.

---

## Key invariant

> LLM-assigned labels never set `current_label`.

This prevents bad LLM guesses from becoming retrieval examples that reinforce themselves. The retrieval pool only grows through human feedback — either via the bootstrap script (before Phase 3 goes live) or ntfy.sh action buttons (Phase 4+).
