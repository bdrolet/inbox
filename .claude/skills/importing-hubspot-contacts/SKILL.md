---
name: importing-hubspot-contacts
description: Use when the user wants to seed, backfill, or import contacts into HubSpot from email history, run the contact import script, or populate HubSpot with past senders and recipients.
---

# Importing HubSpot Contacts from Email History

Runs `scripts/import_contacts.py` from the repo root. The script fetches the last year of email across all folders via the Microsoft Graph API, extracts unique sender/recipient addresses, and upserts each as a HubSpot contact.

## Prerequisites

- `HUBSPOT_TOKEN` must be set in `.env` — the HubSpot private app access token
- Microsoft Graph auth token cached at `~/.inbox-token-cache.json` (device code flow; will prompt in browser on first run)

## Command

```bash
source .venv/bin/activate
python scripts/import_contacts.py
```

To scan a different history window (default is 365 days):

```bash
python scripts/import_contacts.py --days 180
```

## What to expect

- On first run: a browser prompt for Microsoft auth (device code flow)
- Progress lines as it pages through email: `Fetched N emails since YYYY-MM-DD...`
- A summary at the end: `Done. N upserted, N skipped (errors or no token).`

The script is idempotent — re-running it updates existing contacts rather than duplicating them.

## After running

Spot-check in HubSpot → Contacts that a few known senders appear with correct names. The `hs_last_contacted` field is populated automatically by HubSpot once the pipeline logs email engagements for each contact going forward.
