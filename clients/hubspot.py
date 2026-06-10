import json
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

_HUBSPOT_TOKEN = os.environ.get("HUBSPOT_TOKEN", "")


def _client():
    from hubspot import HubSpot

    return HubSpot(access_token=_HUBSPOT_TOKEN)


def upsert_contact(sender_email: str, display_name: str) -> str | None:
    """Upsert a contact by email address. Returns the HubSpot contact ID, or None on error."""
    if not _HUBSPOT_TOKEN:
        return None

    from hubspot.crm.contacts import (
        PublicObjectSearchRequest,
        SimplePublicObjectInputForCreate,
    )
    from hubspot.crm.contacts.exceptions import ApiException

    # Split display name into first/last (best-effort)
    parts = (display_name or "").strip().split(" ", 1)
    firstname = parts[0] if parts else ""
    lastname = parts[1] if len(parts) > 1 else ""

    try:
        client = _client()
        result = client.crm.contacts.search_api.do_search(
            PublicObjectSearchRequest(
                filter_groups=[
                    {
                        "filters": [
                            {"value": sender_email, "propertyName": "email", "operator": "EQ"}
                        ]
                    }
                ],
                limit=1,
            )
        )
        if result.results:
            return result.results[0].id
        else:
            props = {"email": sender_email}
            if firstname:
                props["firstname"] = firstname
            if lastname:
                props["lastname"] = lastname
            created = client.crm.contacts.basic_api.create(
                SimplePublicObjectInputForCreate(properties=props)
            )
            return created.id
    except ApiException as e:
        logger.warning("HubSpot upsert_contact failed for %s: %s", sender_email, e)
        return None


def log_email(
    contact_id: str,
    subject: str,
    sender_email: str,
    body: str,
    received_at: datetime,
    body_html: str | None = None,
) -> None:
    """Log an inbound email engagement against a HubSpot contact."""
    if not _HUBSPOT_TOKEN:
        return

    from hubspot.crm import AssociationType
    from hubspot.crm.objects.emails import (
        AssociationSpec,
        PublicAssociationsForObject,
        PublicObjectId,
        SimplePublicObjectInputForCreate,
    )
    from hubspot.crm.objects.emails.exceptions import ApiException

    ts_ms = str(int(received_at.timestamp() * 1000))
    props = {
        "hs_timestamp": ts_ms,
        "hs_email_subject": subject or "(no subject)",
        "hs_email_direction": "INCOMING_EMAIL",
        "hs_email_status": "SENT",
        "hs_email_headers": json.dumps({"from": {"email": sender_email}}),
    }
    if body_html:
        props["hs_email_html"] = body_html
    else:
        props["hs_email_text"] = body

    association = PublicAssociationsForObject(
        to=PublicObjectId(id=contact_id),
        types=[
            AssociationSpec(
                association_category="HUBSPOT_DEFINED",
                association_type_id=AssociationType.EMAIL_TO_CONTACT,
            )
        ],
    )

    try:
        _client().crm.objects.emails.basic_api.create(
            SimplePublicObjectInputForCreate(
                properties=props,
                associations=[association],
            )
        )
    except ApiException as e:
        logger.warning("HubSpot log_email failed for contact %s: %s", contact_id, e)
