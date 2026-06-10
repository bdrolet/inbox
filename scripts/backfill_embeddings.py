"""
Backfill historical emails into messages + message_embeddings.

Fetches emails from Outlook via the Graph API, normalizes them, inserts into
the DB, and generates embeddings — giving the bootstrap labeling script
enough messages to work with.

Usage:
    python scripts/backfill_embeddings.py [--since DAYS] [--limit N]

Options:
    --since DAYS     How many days back to fetch (default: 90)
    --limit N        Stop after N emails are processed (default: no limit)
    --folder NAME    Outlook folder to pull from (default: inbox)
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Configure logging before any library imports so basicConfig isn't a no-op
_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("%(message)s"))
logging.getLogger().addHandler(_handler)
logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)

from clients.azure.graph_email_client import GraphEmailClient
from clients.bge import load_model
from clients.db import get_conn
from repo import messages, senders
from services.embedding import embed_and_store, text_for_embedding
from services.ingestion import normalize


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--since",
        type=int,
        default=90,
        metavar="DAYS",
        help="Fetch emails received in the last N days (default: 90)",
    )
    parser.add_argument(
        "--limit", type=int, default=None, metavar="N", help="Stop after processing N emails"
    )
    parser.add_argument(
        "--folder",
        type=str,
        default="inbox",
        metavar="NAME",
        help="Outlook folder to pull from (default: inbox)",
    )
    args = parser.parse_args()

    since_dt = datetime.now(timezone.utc) - timedelta(days=args.since)
    logger.info("Fetching emails since %s", since_dt.strftime("%Y-%m-%d"))

    client = GraphEmailClient()
    client.authenticate_interactive()

    logger.info("Loading embedding model...")
    model = load_model()

    logger.info("Fetching email list from '%s'...", args.folder)
    folder = args.folder
    # Well-known folders (inbox, sentitems, etc.) work by name; custom folders need an ID
    well_known = {"inbox", "sentitems", "deleteditems", "drafts", "junkemail", "archive"}
    if folder.lower().replace(" ", "") not in well_known:
        folder_id = client._find_top_level_mail_folder_id(folder)
        if not folder_id:
            logger.error("Folder %r not found", args.folder)
            sys.exit(1)
        folder = folder_id
    # get_all_emails returns bodyPreview only — we fetch full details per email below
    all_emails = client.get_all_emails(folder=folder)
    in_range = [
        e
        for e in all_emails
        if isinstance(e.received_datetime, datetime) and e.received_datetime >= since_dt
    ]

    if args.limit:
        in_range = in_range[: args.limit]

    logger.info("%d emails in range, %d to process", len(all_emails), len(in_range))

    inserted = skipped = errors = 0

    for i, stub in enumerate(in_range, 1):
        try:
            # Fetch full body — the list endpoint only returns bodyPreview
            email = client.get_email_details(stub.id)
            if email is None:
                logger.warning(
                    "[%d/%d] Could not fetch details for %s — skipping", i, len(in_range), stub.id
                )
                errors += 1
                continue

            msg = normalize(email, raw={})

            with get_conn() as conn:
                if messages.exists(conn, msg["source"], msg["external_id"]):
                    skipped += 1
                    logger.debug("[%d/%d] Already exists: %s", i, len(in_range), msg["subject"])
                    continue

                msg_id = messages.insert(conn, msg)
                senders.upsert(conn, msg["sender"], msg["source"])
                embed_and_store(conn, msg_id, text_for_embedding(msg), model)
                conn.commit()

            inserted += 1
            logger.info(
                "[%d/%d] ✓ %s — %s",
                i,
                len(in_range),
                email.received_datetime.strftime("%Y-%m-%d"),
                msg["subject"][:60],
            )

        except Exception as e:
            logger.error("[%d/%d] Error processing %s: %s", i, len(in_range), stub.id, e)
            errors += 1

    logger.info("\nDone — inserted: %d  skipped: %d  errors: %d", inserted, skipped, errors)


if __name__ == "__main__":
    main()
