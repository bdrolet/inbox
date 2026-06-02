import os

import psycopg
from psycopg.rows import dict_row


def _parse_port(value: str) -> int:
    # k8s injects POSTGRES_PORT as "tcp://host:port" for services named "postgres"
    if value.startswith("tcp://"):
        return int(value.rsplit(":", 1)[-1])
    return int(value)


def get_conn() -> psycopg.Connection:
    connection_name = os.environ.get("CLOUD_SQL_CONNECTION_NAME")
    if connection_name:
        return _cloud_sql_conn(connection_name)
    return _direct_conn()


def _cloud_sql_conn(connection_name: str) -> psycopg.Connection:
    # Cloud Run mounts the Unix socket when run.googleapis.com/cloudsql-instances
    # annotation is set. Connect directly via psycopg — avoids the connector's
    # psycopg driver registration issue.
    return psycopg.connect(
        host=f"/cloudsql/{connection_name}",
        dbname=os.environ.get("POSTGRES_DB", "app"),
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        row_factory=dict_row,
    )


def _direct_conn() -> psycopg.Connection:
    return psycopg.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres.apps.svc.cluster.local"),
        port=_parse_port(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "app"),
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        row_factory=dict_row,
    )
