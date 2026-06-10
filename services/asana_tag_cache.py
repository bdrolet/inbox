import logging

import clients.asana as asana
from clients.db import get_conn
from repo import asana_tags

logger = logging.getLogger(__name__)


def resolve_gids(tag_names: list[str]) -> list[str]:
    """Resolve tag names to Asana GIDs using DB cache, falling back to API lookup/create."""
    if not tag_names or not asana.ASANA_API_KEY:
        return []
    gids = []
    with get_conn() as conn:
        for name in tag_names:
            gid = asana_tags.get_gid(conn, name)
            if not gid:
                wgid = asana._get_workspace_gid()
                gid = asana._find_tag(name, wgid) or asana._create_tag(name, wgid)
                asana_tags.store_gid(conn, name, gid)
            gids.append(gid)
    return gids
