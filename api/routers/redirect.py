import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/r/{message_uuid}")
def redirect(message_uuid: str) -> RedirectResponse:
    # Imported lazily (matches api/routers/search.py, emails.py) so the app
    # can be imported without psycopg/google-cloud/MSAL installed — those are
    # not present in the CI dev environment and only needed once a request
    # actually hits this route.
    from clients.db import get_conn
    from clients.graph import get_graph_client
    from repo import messages

    with get_conn() as conn:
        row = messages.get(conn, message_uuid)
    if not row or not row.get("external_id"):
        raise HTTPException(status_code=404, detail="unknown message")
    try:
        web_link = get_graph_client().get_web_link(row["external_id"])
    except Exception as e:
        logger.warning("redirect: webLink resolution failed for %s: %s", message_uuid, e)
        raise HTTPException(status_code=502, detail="resolution failed") from e
    if not web_link:
        raise HTTPException(status_code=404, detail="message not found")
    return RedirectResponse(url=web_link, status_code=302)
