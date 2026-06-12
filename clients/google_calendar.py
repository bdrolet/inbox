import logging
import os

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

from models.types import CalendarInvite

logger = logging.getLogger(__name__)


def _load_credentials() -> Credentials:
    gcp_project = os.environ.get("GCP_PROJECT_ID")
    if gcp_project:
        from google.cloud import secretmanager

        client = secretmanager.SecretManagerServiceClient()

        def _secret(name: str) -> str:
            resp = client.access_secret_version(
                request={"name": f"projects/{gcp_project}/secrets/{name}/versions/latest"}
            )
            return resp.payload.data.decode("UTF-8")

        client_id = _secret("google-calendar-client-id")
        client_secret = _secret("google-calendar-client-secret")
        refresh_token = _secret("google-calendar-refresh-token")
    else:
        client_id = os.environ["GOOGLE_CALENDAR_CLIENT_ID"]
        client_secret = os.environ["GOOGLE_CALENDAR_CLIENT_SECRET"]
        refresh_token = os.environ["GOOGLE_CALENDAR_REFRESH_TOKEN"]

    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/calendar"],
    )


def add_event(invite: CalendarInvite) -> str | None:
    """Create event in Google Calendar via API v3; return htmlLink or None on failure."""
    try:
        creds = _load_credentials()
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)

        start = invite.start.isoformat()
        end = invite.end.isoformat()
        tz = invite.timezone or "UTC"

        body: dict = {
            "summary": invite.title,
            "start": {"dateTime": start, "timeZone": tz},
            "end": {"dateTime": end, "timeZone": tz},
            "iCalUID": invite.ical_uid,
            "organizer": {"email": invite.organizer},
        }
        if invite.location:
            body["location"] = invite.location
        if invite.zoom_link:
            body["description"] = invite.zoom_link

        event = (
            service.events()
            .insert(calendarId="primary", body=body)
            .execute()
        )
        link = event.get("htmlLink")
        logger.info("Added Google Calendar event: %s", link)
        return link
    except Exception:
        logger.exception("Failed to add Google Calendar event for ical_uid=%s", invite.ical_uid)
        return None
