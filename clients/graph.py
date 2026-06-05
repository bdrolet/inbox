import logging
import os

from clients.azure.graph_email_client import GraphEmailClient

logger = logging.getLogger(__name__)

_graph_client: GraphEmailClient | None = None


def get_graph_client() -> GraphEmailClient:
    global _graph_client
    if _graph_client is None:
        _graph_client = GraphEmailClient()
    if os.environ.get("GCP_PROJECT_ID"):
        if not _graph_client.authenticate_headless():
            _graph_client = None
            raise RuntimeError("Graph API headless authentication failed")
    else:
        if not _graph_client.authenticate_interactive():
            _graph_client = None
            raise RuntimeError("Graph API interactive authentication failed")
    return _graph_client
