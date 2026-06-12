#!/usr/bin/env python
"""
Full pipeline runner: processes a real email through the complete inbox pipeline locally.
Equivalent to what the Cloud Function does when triggered by a Pub/Sub message.

Runs: fetch → normalize → embed → classify → store → dispatch
(folder move + Asana task creation + optional ntfy notification)

Usage:
  python run-pipeline-local.py [--message-id <graph_message_id>]
  python run-pipeline-local.py --send-test-email

Without --message-id, fetches the most recent unprocessed email from your Outlook inbox.
--send-test-email sends an urgent-looking test email to yourself first, then processes it.
"""

import sys
import os
import argparse
import logging
from datetime import datetime
import time

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
sys.path.insert(0, REPO_ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(REPO_ROOT, ".env"), override=True)

_REQUIRED = [
    "CLIENT_ID", "CLIENT_SECRET", "TENANT_ID",
    "ANTHROPIC_API_KEY",
    "CLOUD_SQL_CONNECTION_NAME", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB",
    "ASANA_API_KEY", "ASANA_PROJECT_ID", "WEBHOOK_URL", "WEBHOOK_LABEL_TOKEN",
]
missing = [v for v in _REQUIRED if not os.environ.get(v)]
if missing:
    print(f"ERROR: missing env vars: {', '.join(missing)}")
    print("These should be in .env — check the SKILL.md for details.")
    sys.exit(1)

parser = argparse.ArgumentParser(description="Run the inbox pipeline against a real email locally")
parser.add_argument("--message-id", help="Graph API message ID to process (optional)")
parser.add_argument("--send-test-email", action="store_true",
                    help="Send an urgent test email to yourself, then run the pipeline on it")
args = parser.parse_args()

print("Loading embedding model (this takes a few seconds)...")
from clients.bge import load_model

model = load_model()
print("Model loaded.\n")

from clients.db import get_conn
from clients.graph import get_graph_client
from repo import messages as msg_repo
import requests as req

graph = get_graph_client()

if args.send_test_email:
    user_email = os.environ.get("USER_EMAIL", "ben@drolet.cloud")
    print(f"Sending urgent test email to {user_email}...")
    from datetime import timezone
    send_time = datetime.now(timezone.utc)
    graph.send_mail(
        to=user_email,
        subject="[LOCAL-TEST] Server down - production is unreachable",
        body=(
            "Ben,\n\n"
            "The production API has been returning 503s for the last 10 minutes. "
            "Customers are getting errors on checkout. I've already paged the on-call team "
            "but we need you to look at the database connections ASAP — "
            "last deploy touched the connection pool config.\n\n"
            "Can you jump on this now? Revenue impact is already ~$2k/min.\n\n"
            "— Alex"
        ),
    )
    since = send_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    print("Waiting for test email to arrive in inbox...")
    message_id = None
    for attempt in range(12):  # poll up to 60s
        time.sleep(5)
        resp = req.get(
            f"{graph.graph_endpoint}/me/messages",
            headers=graph.get_headers(),
            params={
                "$filter": f"receivedDateTime ge {since} and isDraft eq false",
                "$select": "id,subject,receivedDateTime",
                "$top": "5",
                "$orderby": "receivedDateTime desc",
            },
        )
        resp.raise_for_status()
        msgs = resp.json().get("value", [])
        if msgs:
            message_id = msgs[0]["id"]
            print(f"Found '{msgs[0]['subject']}' after {(attempt + 1) * 5}s\n")
            break
        print(f"  Not yet... ({(attempt + 1) * 5}s elapsed)")
    if not message_id:
        print("ERROR: test email didn't arrive within 60s")
        sys.exit(1)
elif args.message_id:
    message_id = args.message_id
    print(f"Using provided message ID: {message_id}\n")
else:
    print("Fetching latest unprocessed email from inbox...")

    resp = req.get(
        f"{graph.graph_endpoint}/me/messages",
        headers=graph.get_headers(),
        params={
            "$top": "20",
            "$select": "id,subject,from,receivedDateTime",
            "$orderby": "receivedDateTime desc",
            "$filter": "isDraft eq false",
        },
    )
    resp.raise_for_status()
    emails = resp.json().get("value", [])

    message_id = None
    with get_conn() as conn:
        for e in emails:
            if not msg_repo.exists(conn, "email", e["id"]):
                message_id = e["id"]
                print(f"Found unprocessed email:")
                print(f"  Subject : {e['subject']}")
                print(f"  From    : {e['from']['emailAddress']['address']}")
                print(f"  ID      : {message_id}\n")
                break

    if not message_id:
        print("No unprocessed emails found in the last 20 inbox messages.")
        print("Send a test email to yourself and try again, or use --message-id.")
        sys.exit(0)

notification = {"resourceData": {"id": message_id}}

print("Running pipeline...\n")
from handlers.pipeline import run as run_pipeline

run_pipeline(notification, model)
print("\nDone.")
