#!/usr/bin/env python3
"""
One-shot schema migration. Run as a Kubernetes Job after Phase 1 infra is up.

  kubectl run migrate --image=<worker-image> --restart=Never \
    --env="POSTGRES_USER=..." --env="POSTGRES_PASSWORD=..." \
    -- python scripts/migrate_db.py
"""
import sys
from pathlib import Path

from clients.db import get_conn

SCHEMA = Path(__file__).parent.parent / "repo" / "schema.sql"


def main() -> None:
    sql = SCHEMA.read_text()
    with get_conn() as conn:
        conn.execute(sql)
        conn.commit()
    print("Migration complete")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Migration failed: {e}", file=sys.stderr)
        sys.exit(1)
