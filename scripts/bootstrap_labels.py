"""
Interactive bootstrap labeling script.

Displays unlabeled emails one at a time and prompts for a category.
Run this before deploying Phase 3 to seed the vector store with
human-confirmed labels.

Usage:
    python scripts/bootstrap_labels.py [--likely CATEGORY] [--ai-filter CATEGORY]

Options:
    --likely CATEGORY     Show unlabeled emails ranked by embedding similarity
                          to existing examples of CATEGORY.
    --ai-filter CATEGORY  Use Claude to pre-classify unlabeled emails and show
                          only those predicted as CATEGORY. Predictions are
                          cached in the classifications table (source='llm') so
                          re-runs are instant for already-scanned emails.

Note: importance is LLM-only metadata and is never prompted here. It is set
by Claude during the ai_predict_category scan and stored in classifications.

See docs/seeding-classifications.md and docs/labels.md for full context.
"""

import argparse
import os
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from clients.db import get_conn
from models.message import Message
from services.labeling import apply_label
from repo.embeddings import retrieve_neighbors
from repo import classifications, senders
from services.classification import aggregate_neighbors, build_prompt
import clients.claude as claude_client

SHORTCUTS = {"u": "urgent", "r": "respond", "v": "review", "e": "reference", "i": "ignore"}


def fetch_unlabeled(conn):
    return conn.execute(
        """
        SELECT m.id, m.sender, m.sender_display, m.subject, m.received_at, m.body
        FROM messages m
        JOIN message_embeddings me ON me.message_id = m.id
        WHERE me.current_label IS NULL
        ORDER BY m.received_at DESC
        """,
        (),
    ).fetchall()


def get_stored_embedding(conn, message_id):
    row = conn.execute(
        "SELECT embedding FROM message_embeddings WHERE message_id = %s",
        (message_id,),
    ).fetchone()
    return row["embedding"] if row else None


def ai_predict_category(conn, msg) -> str | None:
    """Return Claude's predicted category, using a cached classifications row if available."""
    message_id = str(msg["id"])

    # Return cached prediction if we've already scanned this message
    existing = conn.execute(
        "SELECT category FROM classifications WHERE message_id = %s AND source = 'llm' LIMIT 1",
        (message_id,),
    ).fetchone()
    if existing:
        return existing["category"]

    vec = get_stored_embedding(conn, message_id)
    if vec is None:
        return None

    neighbors = retrieve_neighbors(conn, vec, exclude_id=message_id)
    aggregates = aggregate_neighbors(neighbors)
    sender_ctx = senders.get(conn, msg["sender"], "email")
    msg_typed: Message = {
        "id": message_id,
        "source": "email",
        "external_id": message_id,
        "sender": msg["sender"],
        "sender_display": msg["sender_display"] or "",
        "subject": msg["subject"] or "",
        "body": msg["body"] or "",
        "received_at": msg["received_at"],
        "thread_id": None,
        "raw": {},
    }
    system_prompt, user_message = build_prompt(msg_typed, aggregates, neighbors[:3], sender_ctx)

    try:
        result = claude_client.classify(system_prompt, user_message)
    except Exception:
        return None

    classifications.insert(
        conn,
        message_id=message_id,
        category=result.category.value,
        source="llm",
        confidence=result.confidence,
        alternatives=result.alternatives,
        tags=result.tags,
        reasoning=result.reasoning,
        model="claude-sonnet-4-6",
        prompt_version="bootstrap-v1",
        importance=result.importance.value,
    )
    conn.commit()

    return result.category.value


def fetch_ai_filtered(conn, category, limit=50):
    """Return unlabeled messages Claude predicts as category, up to limit."""
    rows = fetch_unlabeled(conn)
    matches = []
    print(f"  Scanning {len(rows)} emails with Claude...", flush=True)
    for i, msg in enumerate(rows, 1):
        cached = conn.execute(
            "SELECT category FROM classifications WHERE message_id = %s AND source = 'llm' LIMIT 1",
            (str(msg["id"]),),
        ).fetchone()
        predicted = ai_predict_category(conn, msg)
        flag = " (cached)" if cached else ""
        print(f"  [{i}/{len(rows)}] {predicted or '?'}{flag}  —  {msg['subject'][:60]}", flush=True)
        if predicted == category:
            matches.append(msg)
        if len(matches) >= limit:
            break
    print(f"\n  Found {len(matches)} likely '{category}' emails.\n")
    return matches


def fetch_likely(conn, category, limit=50):
    """Return unlabeled messages ranked by avg similarity to labeled examples of category."""
    return conn.execute(
        """
        WITH labeled AS (
            SELECT embedding
            FROM message_embeddings
            WHERE current_label = %s
        )
        SELECT m.id, m.sender, m.sender_display, m.subject, m.received_at, m.body,
               AVG(1 - (me.embedding <=> l.embedding)) AS avg_similarity
        FROM message_embeddings me
        JOIN messages m ON m.id = me.message_id
        CROSS JOIN labeled l
        WHERE me.current_label IS NULL
        GROUP BY m.id, m.sender, m.sender_display, m.subject, m.received_at, m.body, me.embedding
        ORDER BY avg_similarity DESC
        LIMIT %s
        """,
        (category, limit),
    ).fetchall()


def prompt_label(msg, idx, total, counts):
    print("\n" + "─" * 80)
    print(
        f"[{idx}/{total}]  {msg['received_at'].strftime('%Y-%m-%d %H:%M')}  |  {msg['sender_display'] or msg['sender']}"
    )
    print(f"Subject: {msg['subject']}")
    print()
    body = (msg["body"] or "").strip()
    print(textwrap.fill(body[:800], width=80))
    tally = "  ".join(f"{k[0]}:{v}" for k, v in counts.items() if v) or "none yet"
    print(f"\n  [{tally}]")
    print("  u=urgent  r=respond  v=review  e=reference  i=ignore  s=skip  q=quit")
    return input("> ").strip().lower()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--likely",
        metavar="CATEGORY",
        help="Show unlabeled emails most similar to existing examples of CATEGORY",
    )
    parser.add_argument(
        "--ai-filter",
        metavar="CATEGORY",
        help="Use Claude to pre-classify and show only emails predicted as CATEGORY",
    )
    args = parser.parse_args()

    for flag in [args.likely, args.ai_filter]:
        if flag and flag not in SHORTCUTS.values():
            print(f"Unknown category {flag!r}. Choose from: {', '.join(SHORTCUTS.values())}")
            sys.exit(1)

    with get_conn() as conn:
        if args.ai_filter:
            rows = fetch_ai_filtered(conn, args.ai_filter)
            label_hint = f" (Claude predicted '{args.ai_filter}')"
        elif args.likely:
            rows = fetch_likely(conn, args.likely)
            label_hint = f" (ranked by similarity to '{args.likely}' examples)"
        else:
            rows = fetch_unlabeled(conn)
            label_hint = ""

    if not rows:
        print("No unlabeled messages found.")
        return

    print(
        f"\n{len(rows)} unlabeled messages{label_hint}. Target: 60–75 labeled (12–15 per category)."
    )
    counts = {c: 0 for c in SHORTCUTS.values()}

    for idx, msg in enumerate(rows, 1):
        raw = prompt_label(msg, idx, len(rows), counts)

        if raw == "q":
            break
        if raw == "s":
            continue

        label = SHORTCUTS.get(raw, raw)
        if label not in SHORTCUTS.values():
            print("  Unrecognized — skipping.")
            continue

        apply_label(str(msg["id"]), label, source="human_confirmation")
        counts[label] += 1
        print(f"  ✓ {label}")

    total_labeled = sum(counts.values())
    print("\n" + "─" * 80)
    print(f"Session total: {total_labeled} labeled")
    for cat, n in counts.items():
        if n:
            print(f"  {cat}: {n}")


if __name__ == "__main__":
    main()
