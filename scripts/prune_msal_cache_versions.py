#!/usr/bin/env python3
"""One-off: destroy old msal-token-cache versions (there were 727 on 2026-08-17).

  GCP_PROJECT_ID=bens-project-462804 .venv/bin/python scripts/prune_msal_cache_versions.py [--keep 3] [--dry-run]

Requires secretmanager.versions.destroy on the secret (owner / secretVersionManager).
Ongoing hygiene is prune-on-write in graph_email_client + schedule's clients/graph.py.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from google.cloud import secretmanager

from clients.azure.graph_email_client import prune_secret_versions


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--secret", default=os.getenv("MSAL_SECRET_NAME", "msal-token-cache"))
    args = ap.parse_args()
    parent = f"projects/{os.environ['GCP_PROJECT_ID']}/secrets/{args.secret}"
    client = secretmanager.SecretManagerServiceClient()
    enabled = [
        v
        for v in client.list_secret_versions(request={"parent": parent})
        if v.state.name == "ENABLED"
    ]
    print(f"{len(enabled)} enabled versions; keeping {args.keep}")
    if args.dry_run:
        return
    print("destroyed", prune_secret_versions(client, parent, keep=args.keep))


if __name__ == "__main__":
    main()
