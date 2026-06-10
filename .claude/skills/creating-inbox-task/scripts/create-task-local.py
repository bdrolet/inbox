#!/usr/bin/env python
"""
Local smoke-test: create an Asana task from either:
  - A real classified message in Cloud SQL (when DB env vars are set), or
  - The latest non-reply email fetched live from Graph API (fallback)

Usage:
  python create-task-local.py [--category respond|review]
"""
import sys
import os
import re
import uuid
import argparse
import subprocess
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../.."))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "../../../../.env"), override=True)

import clients.asana as asana

# Reload module-level env vars (read at import time before dotenv ran)
asana.ASANA_API_KEY    = os.environ.get("ASANA_API_KEY", "")
asana.ASANA_PROJECT_ID = os.environ.get("ASANA_PROJECT_ID", "")

parser = argparse.ArgumentParser()
parser.add_argument("--category", choices=["respond", "review"], default="respond")
args = parser.parse_args()

# Validate required env vars
missing = [v for v in ("ASANA_API_KEY", "ASANA_PROJECT_ID", "WEBHOOK_URL", "WEBHOOK_LABEL_TOKEN")
           if not os.environ.get(v)]
if missing:
    print(f"ERROR: missing env vars: {', '.join(missing)}")
    print("Add them to .env (see SKILL.md for how to pull from Secret Manager)")
    sys.exit(1)


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def fetch_from_db(category: str) -> dict | None:
    """Fetch the latest message of the given category from Cloud SQL. Returns None if DB not configured."""
    if not os.environ.get("CLOUD_SQL_CONNECTION_NAME") and not os.environ.get("POSTGRES_HOST"):
        return None
    try:
        from clients.db import get_conn
        with get_conn() as conn:
            row = conn.execute("""
                SELECT m.id, m.sender, m.sender_display, m.subject,
                       m.body, m.received_at,
                       c.importance, c.tags, c.reasoning
                FROM messages m
                JOIN classifications c ON c.message_id = m.id
                WHERE c.category = %s AND c.source = 'llm'
                ORDER BY m.received_at DESC
                LIMIT 1
            """, (category,)).fetchone()
        if row is None:
            print(f"No '{category}' messages found in DB")
            return None
        return dict(row)
    except Exception as e:
        print(f"DB unavailable ({e}) — falling back to Graph API")
        return None


def fetch_from_graph(category: str) -> dict:
    """Fetch the latest non-reply email from Graph API."""
    import requests as req
    from clients.azure.graph_email_client import GraphEmailClient
    client = GraphEmailClient()
    client.authenticate_interactive()
    resp = req.get(
        f"{client.graph_endpoint}/me/messages",
        headers=client.get_headers(),
        params={"$top": "10", "$select": "id,subject,from,body,receivedDateTime,webLink",
                "$orderby": "receivedDateTime desc"},
    )
    resp.raise_for_status()
    raw = next((e for e in resp.json()["value"] if not e["subject"].startswith("RE:")), None)
    if raw is None:
        print("ERROR: no non-reply emails found — send a test email first")
        sys.exit(1)
    body = raw["body"]["content"]
    if raw["body"]["contentType"] == "html":
        body = strip_html(body)
    return {
        "id": uuid.uuid4(),
        "sender": raw["from"]["emailAddress"]["address"],
        "sender_display": raw["from"]["emailAddress"].get("name", ""),
        "subject": raw["subject"],
        "body": body,
        "received_at": datetime.fromisoformat(raw["receivedDateTime"].replace("Z", "+00:00")),
        "importance": "P1",
        "tags": [],
        "reasoning": f"Graph API fallback — no DB classification available.",
        "_graph_external_id": raw["id"],
        "_web_link": raw.get("webLink"),
    }


# --- Main ---

print(f"Category : {args.category}")

# Try DB first, fall back to Graph
db_row = fetch_from_db(args.category)
if db_row:
    print(f"Source   : Cloud SQL")
    print(f"Subject  : {db_row['subject']}")
    print(f"From     : {db_row['sender_display']} <{db_row['sender']}>")
    body_text = strip_html(db_row['body'] or '')[:500]
    tags = db_row['tags'] or []
    msg = db_row
    graph_external_id = None
    web_link = None
else:
    print(f"Source   : Graph API (live)")
    msg = fetch_from_graph(args.category)
    print(f"Subject  : {msg['subject']}")
    print(f"From     : {msg['sender_display']} <{msg['sender']}>")
    body_text = (msg['body'] or '')[:500]
    tags = []
    graph_external_id = msg.get("_graph_external_id")
    web_link = msg.get("_web_link")

# Resolve tag GIDs (requires DB + Asana)
tag_gids = []
if tags:
    try:
        from services import asana_tag_cache as tag_cache_svc
        tag_gids = tag_cache_svc.resolve_gids(tags)
        print(f"Tags     : {tags}")
        print(f"Tag GIDs : {tag_gids}")
    except Exception as e:
        print(f"Tag resolution failed ({e}) — creating task without tags")

# Generate draft and Outlook draft for respond category
draft_link = None
if args.category == "respond" and graph_external_id:
    import services.draft_reply as draft_svc
    from clients.azure.graph_email_client import GraphEmailClient
    from clients.graph import get_graph_client
    print("\nGenerating Claude draft reply...")
    draft_text = draft_svc.generate(msg)
    print(f"\n{draft_text}\n")
    print("Creating Outlook draft...")
    draft_link = get_graph_client().create_reply_draft(graph_external_id, draft_text)
    print(f"Draft link: {draft_link}\n")
elif args.category == "respond" and not graph_external_id:
    print("Note: Outlook draft skipped (DB-sourced message — no Graph external_id available)")

# Create Asana task (always use fresh UUID to avoid duplicate external.gid conflicts in testing)
print("\nCreating Asana task...")
task_gid = asana.create_task(
    message_id=str(uuid.uuid4()),
    subject=msg['subject'],
    sender=msg['sender'],
    sender_display=str(msg['sender_display'] or msg['sender']),
    received_at=str(msg['received_at']),
    importance=str(msg['importance'] or 'P2'),
    tags=tags,
    reasoning=str(msg['reasoning'] or ''),
    body=body_text,
    web_link=web_link,
    due_date=None,
    category=args.category,
    draft_link=draft_link,
    tag_gids=tag_gids,
)
print(f"Task GID : {task_gid}")
print(f"Task URL : https://app.asana.com/0/{asana.ASANA_PROJECT_ID}/{task_gid}")
