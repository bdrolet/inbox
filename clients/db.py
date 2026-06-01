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
    from google.cloud.sql.connector import Connector
    connector = Connector()
    return connector.connect(
        connection_name,
        "psycopg",
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        db=os.environ.get("POSTGRES_DB", "app"),
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
