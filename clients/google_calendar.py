import logging
import os

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

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

    # Do not pass scopes — google-auth includes them in the refresh request,
    # which causes invalid_scope when the token server sees scopes it didn't
    # explicitly grant. The refresh token already carries its own scope grant.
    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
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

        try:
            event = service.events().insert(calendarId="primary", body=body).execute()
        except HttpError as e:
            if e.resp.status == 409:
                # iCalUID already exists — update the existing event with current details
                existing = service.events().list(
                    calendarId="primary",
                    iCalUID=invite.ical_uid,
                    singleEvents=True,
                ).execute()
                items = existing.get("items", [])
                if not items:
                    return None
                event = service.events().patch(
                    calendarId="primary",
                    eventId=items[0]["id"],
                    body=body,
                ).execute()
                logger.info("Updated existing Google Calendar event: %s", event.get("htmlLink"))
                return event.get("htmlLink")
            raise
        link = event.get("htmlLink")
        logger.info("Added Google Calendar event: %s", link)
        return link
    except Exception:
        logger.exception("Failed to add Google Calendar event for ical_uid=%s", invite.ical_uid)
        return None
