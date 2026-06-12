#!/usr/bin/env python3
"""
Import contacts from the last year of email history into HubSpot.

Fetches all emails across all folders received in the past year, extracts
unique sender/recipient addresses, and upserts each as a HubSpot contact
with their most recent interaction date.

Idempotent — safe to re-run; existing contacts are updated, not duplicated.

Usage:
    python scripts/import_contacts.py [--days N]

Options:
    --days N    How many days of history to scan (default: 365)
"""

import argparse
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

OWN_EMAIL = os.environ.get("OWN_EMAIL", "ben@drolet.cloud")
_SKIP_PATTERN = re.compile(
    r"(no.?reply|noreply|do.?not.?reply|mailer.?daemon|notifications?@|alerts?@|support@|newsletter@)",
    re.IGNORECASE,
)


def _should_skip(address: str) -> bool:
    if not address:
        return True
    if address.lower() == OWN_EMAIL.lower():
        return True
    return bool(_SKIP_PATTERN.search(address))


def main():
    parser = argparse.ArgumentParser(description="Import email contacts into HubSpot")
    parser.add_argument("--days", type=int, default=365, help="Days of history to scan")
    args = parser.parse_args()

    if not os.environ.get("HUBSPOT_TOKEN"):
        print("Error: HUBSPOT_TOKEN is not set")
        sys.exit(1)

    from clients.azure.graph_email_client import GraphEmailClient
    import clients.hubspot as hubspot

    print("Authenticating with Microsoft Graph...")
    client = GraphEmailClient()
    client.authenticate_interactive()

    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    print(f"Fetching emails since {since.strftime('%Y-%m-%d')} (last {args.days} days)...")
    emails = client.get_all_emails_since(since)
    print(f"Fetched {len(emails)} emails.")

    # address → (display_name, most_recent_datetime)
    contacts: dict[str, tuple[str, datetime]] = {}

    def _record(address: str, display: str, ts: datetime) -> None:
        if _should_skip(address):
            return
        address = address.lower().strip()
        existing_ts = contacts.get(address, (display, datetime.min.replace(tzinfo=timezone.utc)))[1]
        if ts > existing_ts:
            contacts[address] = (display or address, ts)

    for email in emails:
        ts = email.received_datetime
        if not isinstance(ts, datetime):
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        # From address
        _record(email.from_email, email.from_name, ts)

        # To/CC recipients
        for r in (email.to_recipients or []) + (email.cc_recipients or []):
            _record(r.get("address", ""), r.get("name", ""), ts)

    print(f"Found {len(contacts)} unique contacts after filtering.")

    updated = skipped = 0
    for i, (address, (display, last_seen)) in enumerate(
        sorted(contacts.items(), key=lambda x: x[0]), start=1
    ):
        if i % 50 == 0:
            print(f"  Progress: {i}/{len(contacts)}...")
        contact_id = hubspot.upsert_contact(address, display)
        if contact_id is None:
            skipped += 1
        else:
            # upsert_contact doesn't tell us create vs update, so just count non-None
            updated += 1

    print(f"\nDone. {updated} upserted, {skipped} skipped (errors or no token).")


if __name__ == "__main__":
    main()
