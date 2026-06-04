# Seeding Classifications

Before Phase 3's retrieval-augmented prompt can provide useful context, the vector store needs a baseline set of human-labeled examples. Without labels, `message_embeddings.current_label` is NULL for every row, and the retrieval query returns nothing.

## How many to label

Target **12–15 per category** (60–75 total minimum). The five categories are:

| Category | Description |
|----------|-------------|
| `urgent` | Needs attention today; will trigger a push notification |
| `respond` | Needs a reply, not today |
| `review` | Worth reading, no reply needed |
| `reference` | Keep but don't read now |
| `ignore` | Marketing/noise |

`ignore` and `review` fill up naturally from any inbox sample. `urgent`, `respond`, and `reference` require deliberate targeting — they won't appear in enough volume unless you seek them out.

Fewer than ~10 per category is workable but expect weaker retrieval quality early on. Beyond ~100 total, returns diminish quickly.

---

## Step 1: Backfill emails into the DB

The labeling script works from what's already in `messages` + `message_embeddings`. If you have only a few emails (e.g. just what has arrived since Phase 1), backfill historical emails first.

```bash
source .venv/bin/activate

# Pull from specific folders by name — pick folders whose content matches the
# categories you need
python scripts/backfill_embeddings.py --folder inbox --since 90
python scripts/backfill_embeddings.py --folder reply_required --since 3650
python scripts/backfill_embeddings.py --folder urgent_attention --since 3650
python scripts/backfill_embeddings.py --folder review_document --since 3650 --limit 100
```

**Folder selection strategy:**

| Need more of... | Try these folders |
|-----------------|-------------------|
| `urgent` | `urgent_attention`, `inbox` (recent) |
| `respond` | `reply_required`, `reply_needed`, `follow_up`, `schedule_meeting` |
| `reference` | `Banking, Invoices and Payments`, `Medical`, `review_document` |
| `ignore` / `review` | `Archive`, `no_action`, `Inbox` — these fill up naturally |

Use `--limit N` on large folders to avoid pulling thousands of emails at once.

---

## Step 2: Run the labeling script

```bash
python scripts/bootstrap_labels.py
```

Each email is displayed with sender, subject, received time, and up to 800 chars of body. Press a key to label, `s` to skip, `q` to quit. Progress is shown as a running tally per category.

---

## Faster labeling with `--ai-filter`

The most efficient technique for hitting the per-category targets: let Claude pre-classify the unlabeled pool and only show you the emails it predicts as the target category.

```bash
# Only shows emails Claude predicts as urgent
python scripts/bootstrap_labels.py --ai-filter urgent

# Only shows emails Claude predicts as respond
python scripts/bootstrap_labels.py --ai-filter respond
```

Claude's predictions are cached in the `classifications` table (`source='llm'`) after the first scan, so re-running is instant for already-scanned emails.

**Workflow that worked:**

1. Check counts: query `message_embeddings WHERE current_label IS NOT NULL GROUP BY current_label`
2. Identify the category furthest below 12–15
3. If not enough emails in DB for that category, backfill a relevant folder
4. Run `--ai-filter <category>`, review the suggestions, label the correct ones
5. Repeat until all five categories are at target

---

## Correcting mislabeled emails

If you labeled something wrong, correct it directly:

```python
from services.labeling import apply_label
apply_label("<message_id>", "correct_category", source="human_correction")
```

This overwrites `current_label` on the embedding and adds a new `classifications` row with `source="human_correction"`. Fix mistakes early — bad labels become retrieval examples that nudge future classifications in the wrong direction.

---

## What happens under the hood

`apply_label(message_id, label, source="human_confirmation")` does two things atomically:

1. Sets `current_label` on the `message_embeddings` row — this is what makes the embedding eligible for retrieval
2. Inserts a row into `classifications` recording the label and its source

The retrieval query (`retrieve_neighbors`) only checks `current_label IS NOT NULL`. It never reads `source`. So `source` is purely an audit trail — useful later if you want to query "how often did my corrections disagree with the LLM?" but ignored by the pipeline today.

**Note on importance:** Importance (P0–P3) is LLM-only metadata. It is stored in `classifications` by the AI scan but never prompted during human labeling. See `docs/labels.md` for the full importance model.

---

## Checking your progress

```sql
SELECT current_label, count(*)
FROM message_embeddings
WHERE current_label IS NOT NULL
GROUP BY current_label
ORDER BY count DESC;
```

---

## Relationship to Phase 4

The bootstrap set is a one-time seed. Once Phase 4 is live, every ntfy.sh action button tap (confirm / correct) calls the same `apply_label` path with `source = "human_confirmation"` or `"human_correction"`, so the vector store grows automatically over time.
