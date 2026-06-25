"""
Microsoft Graph API Email Client
Handles authentication and email retrieval from Outlook/Office 365
"""

import base64
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional

import msal
import requests
from dotenv import load_dotenv

from clients.azure.email import Email

logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()


class GraphEmailClient:
    def __init__(self):
        self.client_id = os.getenv("CLIENT_ID")
        self.client_secret = os.getenv("CLIENT_SECRET")
        self.tenant_id = os.getenv("TENANT_ID")
        self.redirect_uri = os.getenv("REDIRECT_URI", "http://localhost:8080/callback")
        self.scopes = os.getenv(
            "SCOPES",
            "https://graph.microsoft.com/Mail.Read "
            "https://graph.microsoft.com/Mail.Read.Shared "
            "https://graph.microsoft.com/User.Read "
            "https://graph.microsoft.com/Calendars.ReadWrite "
            "https://graph.microsoft.com/Group.Read.All",
        ).split()

        # Validate configuration
        if not all([self.client_id, self.client_secret, self.tenant_id]):
            raise ValueError("Missing required environment variables. Please check your .env file.")

        self.authority = f"https://login.microsoftonline.com/{self.tenant_id}"
        self.graph_endpoint = "https://graph.microsoft.com/v1.0"
        self.access_token = None
        self.token_cache_file = os.path.expanduser("~/.inbox-token-cache.json")
        # displayName -> folder id (action folders: reply_required, no_action, etc.)
        self._mail_folder_id_cache: Dict[str, str] = {}

        # Initialize MSAL app for public client (interactive auth) with token cache
        self.app = msal.PublicClientApplication(
            self.client_id, authority=self.authority, token_cache=msal.SerializableTokenCache()
        )

        # Load existing token cache if it exists
        if os.path.exists(self.token_cache_file):
            self.app.token_cache.deserialize(open(self.token_cache_file, "r").read())

    def authenticate(self) -> bool:
        """
        Authenticate using client credentials flow (for app-only access)
        This is suitable for accessing shared mailboxes or when you don't need user interaction
        """
        try:
            result = self.app.acquire_token_silent(self.scopes, account=None)

            if not result:
                result = self.app.acquire_token_for_client(
                    scopes=["https://graph.microsoft.com/.default"]
                )

            if "access_token" in result:
                self.access_token = result["access_token"]
                return True
            else:
                print(f"Authentication failed: {result.get('error_description', 'Unknown error')}")
                return False

        except Exception as e:
            print(f"Authentication error: {str(e)}")
            return False

    def save_token_cache(self):
        """Save the token cache to disk"""
        if self.app.token_cache.has_state_changed:
            with open(self.token_cache_file, "w") as cache_file:
                cache_file.write(self.app.token_cache.serialize())

    def _load_cache_from_secret_manager(self) -> str:
        """Load MSAL token cache from GCP Secret Manager."""
        from google.cloud import secretmanager

        project_id = os.getenv("GCP_PROJECT_ID")
        secret_name = os.getenv("MSAL_SECRET_NAME", "msal-token-cache")
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("UTF-8")

    def _save_cache_to_secret_manager(self):
        """Write the updated MSAL token cache back to GCP Secret Manager."""
        if not self.app.token_cache.has_state_changed:
            return

        from google.cloud import secretmanager

        project_id = os.getenv("GCP_PROJECT_ID")
        secret_name = os.getenv("MSAL_SECRET_NAME", "msal-token-cache")
        client = secretmanager.SecretManagerServiceClient()
        parent = f"projects/{project_id}/secrets/{secret_name}"
        payload = self.app.token_cache.serialize().encode("UTF-8")
        client.add_secret_version(request={"parent": parent, "payload": {"data": payload}})
        logger.info("Persisted refreshed MSAL token cache to Secret Manager")

    def authenticate_headless(self) -> bool:
        """
        Headless authentication for Cloud Run / CI environments.
        Loads a cached MSAL refresh token from GCP Secret Manager, performs a
        silent token refresh, and writes the updated cache back.
        """
        try:
            cache_data = self._load_cache_from_secret_manager()
            self.app.token_cache.deserialize(cache_data)

            accounts = self.app.get_accounts()
            if not accounts:
                raise RuntimeError("No accounts found in MSAL token cache from Secret Manager")

            result = self.app.acquire_token_silent(self.scopes, account=accounts[0])
            if not result or "access_token" not in result:
                error = result.get("error_description", "Unknown error") if result else "No result"
                raise RuntimeError(f"Silent token refresh failed: {error}")

            self.access_token = result["access_token"]
            self._save_cache_to_secret_manager()
            logger.info("Headless authentication successful")
            return True

        except Exception as e:
            logger.error(f"Headless authentication failed: {e}")
            return False

    def authenticate_interactive(self) -> bool:
        """
        Interactive authentication flow using device code (for user-specific access)
        This displays a code for the user to enter in a browser
        """
        try:
            # Try to get token silently first
            accounts = self.app.get_accounts()

            if accounts:
                result = self.app.acquire_token_silent(self.scopes, account=accounts[0])
                if result and "access_token" in result:
                    self.access_token = result["access_token"]
                    self.save_token_cache()
                    return True

            # If silent auth fails, do device code auth
            flow = self.app.initiate_device_flow(scopes=self.scopes)

            if "user_code" not in flow:
                print("Failed to create device flow")
                return False

            print("\nTo sign in, use a web browser to open the page:")
            print(f"  {flow['verification_uri']}")
            print(f"and enter the code: {flow['user_code']}")
            print("\nWaiting for authentication...")

            result = self.app.acquire_token_by_device_flow(flow)

            if "access_token" in result:
                self.access_token = result["access_token"]
                self.save_token_cache()
                print("✓ Authentication successful!")
                return True
            else:
                print(
                    f"Device flow authentication failed: {result.get('error_description', 'Unknown error')}"
                )
                return False

        except Exception as e:
            print(f"Device flow authentication error: {str(e)}")
            return False

    def get_headers(self) -> Dict[str, str]:
        """Get headers for Graph API requests"""
        if not self.access_token:
            raise ValueError("Not authenticated. Call authenticate() first.")

        return {"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"}

    def get_emails_since(self, since_datetime: datetime, folder: str = "inbox") -> List[Email]:
        """
        Retrieve emails received since a specific datetime

        Args:
            since_datetime: Datetime object - emails received after this time
            folder: Email folder to read from (default: 'inbox')

        Returns:
            List of Email objects
        """
        if not self.access_token:
            raise ValueError("Not authenticated. Call authenticate() first.")

        try:
            # Construct the API endpoint
            endpoint = f"{self.graph_endpoint}/me/mailFolders/{folder}/messages"

            # Format datetime for Graph API (ISO 8601 format)
            since_str = since_datetime.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

            # Query parameters
            params = {
                "$filter": f"receivedDateTime ge {since_str}",
                "$orderby": "receivedDateTime desc",
                "$select": "id,subject,from,receivedDateTime,bodyPreview,isRead,hasAttachments",
            }

            response = requests.get(endpoint, headers=self.get_headers(), params=params)
            response.raise_for_status()

            data = response.json()
            emails_data = data.get("value", [])
            return [Email(email_data) for email_data in emails_data]

        except requests.exceptions.RequestException as e:
            print(f"Error retrieving emails: {str(e)}")
            if hasattr(e, "response") and e.response is not None:
                print(f"Response: {e.response.text}")
            return []

    def get_latest_emails(self, count: int = 10, folder: str = "inbox") -> List[Email]:
        """
        Retrieve the latest emails from specified folder

        Args:
            count: Number of emails to retrieve (default: 10)
            folder: Email folder to read from (default: 'inbox')

        Returns:
            List of Email objects
        """
        if not self.access_token:
            raise ValueError("Not authenticated. Call authenticate() first.")

        try:
            # Construct the API endpoint
            endpoint = f"{self.graph_endpoint}/me/mailFolders/{folder}/messages"

            # Query parameters
            params = {
                "$top": count,
                "$orderby": "receivedDateTime desc",
                "$select": "id,subject,from,receivedDateTime,bodyPreview,isRead,hasAttachments",
            }

            response = requests.get(endpoint, headers=self.get_headers(), params=params)
            response.raise_for_status()

            data = response.json()
            emails_data = data.get("value", [])
            return [Email(email_data) for email_data in emails_data]

        except requests.exceptions.RequestException as e:
            print(f"Error retrieving emails: {str(e)}")
            if hasattr(e, "response") and e.response is not None:
                print(f"Response: {e.response.text}")
            return []

    def get_all_emails(self, folder: str = "inbox", page_size: int = 100) -> List[Email]:
        """
        Retrieve all emails from a folder, following pagination links.

        Args:
            folder: Email folder to read from (default: 'inbox')
            page_size: Number of emails per API request (default: 100)

        Returns:
            List of Email objects
        """
        if not self.access_token:
            raise ValueError("Not authenticated. Call authenticate() first.")

        all_emails = []
        endpoint = f"{self.graph_endpoint}/me/mailFolders/{folder}/messages"
        params = {
            "$top": page_size,
            "$orderby": "receivedDateTime desc",
            "$select": "id,subject,from,receivedDateTime,bodyPreview,isRead,hasAttachments",
        }

        try:
            while endpoint:
                response = requests.get(endpoint, headers=self.get_headers(), params=params)
                response.raise_for_status()
                data = response.json()

                all_emails.extend(Email(e) for e in data.get("value", []))
                logger.info(f"Fetched {len(all_emails)} emails so far...")

                endpoint = data.get("@odata.nextLink")
                params = None  # nextLink already includes query params

            logger.info(f"Finished fetching {len(all_emails)} total emails from {folder}")
            return all_emails

        except requests.exceptions.RequestException as e:
            print(f"Error retrieving emails: {str(e)}")
            if hasattr(e, "response") and e.response is not None:
                print(f"Response: {e.response.text}")
            return all_emails

    def get_all_emails_since(self, since_datetime: datetime, page_size: int = 100) -> List[Email]:
        """
        Retrieve all emails across all folders received since a given datetime.

        Uses /me/messages (not a specific folder) so a single paginated call
        covers inbox, sent items, archive, and custom folders.
        """
        if not self.access_token:
            raise ValueError("Not authenticated. Call authenticate() first.")

        all_emails = []
        since_str = since_datetime.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        endpoint = f"{self.graph_endpoint}/me/messages"
        params: Optional[Dict[str, str]] = {
            "$filter": f"receivedDateTime ge {since_str}",
            "$orderby": "receivedDateTime desc",
            "$top": str(page_size),
            "$select": "id,subject,from,toRecipients,ccRecipients,receivedDateTime,bodyPreview",
        }

        try:
            while endpoint:
                response = requests.get(endpoint, headers=self.get_headers(), params=params)
                response.raise_for_status()
                data = response.json()
                all_emails.extend(Email(e) for e in data.get("value", []))
                logger.info(f"Fetched {len(all_emails)} emails since {since_str}...")
                endpoint = data.get("@odata.nextLink")
                params = None
            logger.info(f"Finished fetching {len(all_emails)} total emails since {since_str}")
            return all_emails
        except requests.exceptions.RequestException as e:
            print(f"Error retrieving emails: {str(e)}")
            if hasattr(e, "response") and e.response is not None:
                print(f"Response: {e.response.text}")
            return all_emails

    def get_email_details(self, email_id: str) -> Optional[Email]:
        """
        Get detailed information about a specific email

        Args:
            email_id: The ID of the email to retrieve

        Returns:
            Email object or None if not found
        """
        if not self.access_token:
            raise ValueError("Not authenticated. Call authenticate() first.")

        try:
            endpoint = f"{self.graph_endpoint}/me/messages/{email_id}"

            # Get full email details including body
            params = {
                "$select": "id,subject,from,toRecipients,ccRecipients,bccRecipients,receivedDateTime,sentDateTime,body,bodyPreview,isRead,hasAttachments,attachments,webLink"
            }

            response = requests.get(endpoint, headers=self.get_headers(), params=params)
            response.raise_for_status()

            email_data = response.json()
            return Email(email_data)

        except requests.exceptions.RequestException as e:
            print(f"Error retrieving email details: {str(e)}")
            return None

    def mark_as_read(self, email_id: str) -> bool:
        """
        Mark an email as read

        Args:
            email_id: The ID of the email to mark as read

        Returns:
            True if successful, False otherwise
        """
        if not self.access_token:
            raise ValueError("Not authenticated. Call authenticate() first.")

        try:
            endpoint = f"{self.graph_endpoint}/me/messages/{email_id}"

            data = {"isRead": True}

            response = requests.patch(endpoint, headers=self.get_headers(), json=data)
            response.raise_for_status()

            return True

        except requests.exceptions.RequestException as e:
            print(f"Error marking email as read: {str(e)}")
            return False

    def _find_top_level_mail_folder_id(self, display_name: str) -> Optional[str]:
        """Find a top-level mail folder by display name (paginated, case-insensitive)."""
        endpoint = f"{self.graph_endpoint}/me/mailFolders"
        params: Optional[Dict[str, str]] = {"$top": "100"}
        target_lower = display_name.casefold()
        while endpoint:
            response = requests.get(
                endpoint,
                headers=self.get_headers(),
                params=params,
            )
            response.raise_for_status()
            data = response.json()
            for folder in data.get("value", []):
                fname = folder.get("displayName") or ""
                if fname == display_name or fname.casefold() == target_lower:
                    return folder["id"]
            endpoint = data.get("@odata.nextLink")
            params = None
        return None

    def get_or_create_mail_folder(self, display_name: str) -> str:
        """
        Return Graph folder id for a folder whose display name matches display_name.
        Creates the folder at the mailbox root if it does not exist.
        """
        if display_name in self._mail_folder_id_cache:
            return self._mail_folder_id_cache[display_name]

        found = self._find_top_level_mail_folder_id(display_name)
        if found:
            self._mail_folder_id_cache[display_name] = found
            return found

        create_url = f"{self.graph_endpoint}/me/mailFolders"
        try:
            response = requests.post(
                create_url,
                headers=self.get_headers(),
                json={"displayName": display_name},
            )
            response.raise_for_status()
            folder_id = response.json()["id"]
            self._mail_folder_id_cache[display_name] = folder_id
            return folder_id
        except requests.exceptions.HTTPError as e:
            # Another run may have created it, or name collision
            if e.response is not None and e.response.status_code in (400, 409):
                found = self._find_top_level_mail_folder_id(display_name)
                if found:
                    self._mail_folder_id_cache[display_name] = found
                    return found
            raise

    def tag_message(self, message_id: str, categories: list[str]) -> bool:
        """Set Outlook color categories on a message. Categories must exist in the mailbox
        master list to display with colors; PATCH succeeds regardless."""
        try:
            response = requests.patch(
                f"{self.graph_endpoint}/me/messages/{message_id}",
                headers=self.get_headers(),
                json={"categories": categories},
            )
            response.raise_for_status()
            logger.info("Tagged message %s with %s", message_id, categories)
            return True
        except requests.exceptions.RequestException as e:
            detail = e.response.text[:500] if e.response is not None else ""
            logger.error("Failed to tag message %s: %s %s", message_id, e, detail)
            return False

    def create_reply_draft(self, external_id: str, body_text: str) -> str | None:
        """Create a pre-addressed Outlook draft reply; return its webLink or None on failure."""
        try:
            resp = requests.post(
                f"{self.graph_endpoint}/me/messages/{external_id}/createReply",
                headers=self.get_headers(),
            )
            resp.raise_for_status()
            draft_id = resp.json()["id"]

            requests.patch(
                f"{self.graph_endpoint}/me/messages/{draft_id}",
                headers=self.get_headers(),
                json={"body": {"contentType": "Text", "content": body_text}},
            ).raise_for_status()

            # Fetch webLink explicitly — createReply response may return wrong folder context
            get_resp = requests.get(
                f"{self.graph_endpoint}/me/messages/{draft_id}",
                headers=self.get_headers(),
                params={"$select": "webLink"},
            )
            get_resp.raise_for_status()
            web_link = get_resp.json().get("webLink")

            logger.info("Created reply draft %s for message %s", draft_id, external_id)
            return web_link
        except requests.exceptions.RequestException as e:
            detail = e.response.text[:500] if e.response is not None else ""
            logger.error("create_reply_draft failed for %s: %s %s", external_id, e, detail)
            return None

    def send_mail(
        self,
        to: str,
        subject: str,
        body: str,
        save_to_sent: bool = False,
    ) -> None:
        """Send an email via Graph API. Requires Mail.Send scope."""
        response = requests.post(
            f"{self.graph_endpoint}/me/sendMail",
            headers=self.get_headers(),
            json={
                "message": {
                    "subject": subject,
                    "body": {"contentType": "Text", "content": body},
                    "toRecipients": [{"emailAddress": {"address": to}}],
                },
                "saveToSentItems": save_to_sent,
            },
        )
        response.raise_for_status()
        logger.info("Sent email to %s: %s", to, subject)

    # ------------------------------------------------------------------ #
    # Outbound write operations (draft / attachment / send)
    # ------------------------------------------------------------------ #
    def _mailbox_base(self, from_address: str | None, from_shared: bool) -> str:
        """Return the Graph base path for an outbound operation.

        Shared mailbox -> /users/{addr} so the draft lives in that mailbox's Drafts.
        Otherwise -> /me (primary mailbox); aliases/groups are stamped via the
        message-level `from` instead of path-targeting.
        """
        if from_shared and from_address:
            return f"/users/{from_address}"
        return "/me"

    def _build_message(
        self,
        *,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        body_type: str = "Text",
        from_address: str | None = None,
        from_shared: bool = False,
    ) -> dict:
        """Assemble a Graph `message` resource from outbound fields.

        A `from` is set only for alias/group sends (from_address set, not shared);
        shared-mailbox sends path-target /users/{addr}, so no `from` is needed.
        """
        message: dict = {
            "subject": subject,
            "body": {"contentType": body_type, "content": body},
            "toRecipients": [{"emailAddress": {"address": a}} for a in to],
        }
        if cc:
            message["ccRecipients"] = [{"emailAddress": {"address": a}} for a in cc]
        if bcc:
            message["bccRecipients"] = [{"emailAddress": {"address": a}} for a in bcc]
        if from_address and not from_shared:
            message["from"] = {"emailAddress": {"address": from_address}}
        return message

    def create_draft(
        self,
        *,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        body_type: str = "Text",
        from_address: str | None = None,
        from_shared: bool = False,
    ) -> dict:
        """Create a draft message saved to Drafts (not sent). Requires Mail.ReadWrite.

        Returns the created Graph message dict (includes `id` and `webLink`).
        """
        base = self._mailbox_base(from_address, from_shared)
        message = self._build_message(
            to=to,
            subject=subject,
            body=body,
            cc=cc,
            bcc=bcc,
            body_type=body_type,
            from_address=from_address,
            from_shared=from_shared,
        )
        response = requests.post(
            f"{self.graph_endpoint}{base}/messages",
            headers=self.get_headers(),
            json=message,
        )
        response.raise_for_status()
        created = response.json()
        logger.info("Created draft %s (base=%s)", created.get("id"), base)
        return created

    def add_attachment(
        self,
        message_id: str,
        name: str,
        content_bytes_b64: str,
        content_type: str | None = None,
        *,
        from_address: str | None = None,
        from_shared: bool = False,
        is_inline: bool = False,
    ) -> dict:
        """Add a small (<3 MB) file attachment to a draft. Requires Mail.ReadWrite.

        content_bytes_b64 is the base64-encoded file content, as Graph expects.
        Files >= 3 MB need the chunked upload-session path, which is not yet supported.
        """
        try:
            decoded_size = len(base64.b64decode(content_bytes_b64, validate=True))
        except Exception as e:
            raise ValueError(f"content_bytes is not valid base64: {e}") from e
        if decoded_size >= 3 * 1024 * 1024:
            raise ValueError(
                f"attachment {name!r} is {decoded_size} bytes; files >= 3 MB require the "
                "large-file upload path (createUploadSession), not yet supported"
            )

        base = self._mailbox_base(from_address, from_shared)
        payload: dict = {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": name,
            "contentBytes": content_bytes_b64,
            "isInline": is_inline,
        }
        if content_type:
            payload["contentType"] = content_type
        response = requests.post(
            f"{self.graph_endpoint}{base}/messages/{message_id}/attachments",
            headers=self.get_headers(),
            json=payload,
        )
        response.raise_for_status()
        created = response.json()
        logger.info("Added attachment %r to message %s", name, message_id)
        return created

    def send_draft(
        self,
        message_id: str,
        *,
        from_address: str | None = None,
        from_shared: bool = False,
    ) -> None:
        """Send an existing draft by id. Requires Mail.Send."""
        base = self._mailbox_base(from_address, from_shared)
        response = requests.post(
            f"{self.graph_endpoint}{base}/messages/{message_id}/send",
            headers=self.get_headers(),
        )
        response.raise_for_status()
        logger.info("Sent draft %s (base=%s)", message_id, base)

    def send_message(
        self,
        *,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        body_type: str = "Text",
        from_address: str | None = None,
        from_shared: bool = False,
        save_to_sent: bool = True,
    ) -> None:
        """Compose and send a message in one shot via sendMail. Requires Mail.Send.

        Supports cc/bcc, HTML bodies, and alias/group/shared `from` routing, unlike
        the simpler legacy send_mail().
        """
        base = self._mailbox_base(from_address, from_shared)
        message = self._build_message(
            to=to,
            subject=subject,
            body=body,
            cc=cc,
            bcc=bcc,
            body_type=body_type,
            from_address=from_address,
            from_shared=from_shared,
        )
        response = requests.post(
            f"{self.graph_endpoint}{base}/sendMail",
            headers=self.get_headers(),
            json={"message": message, "saveToSentItems": save_to_sent},
        )
        response.raise_for_status()
        logger.info("Sent message to %s (base=%s)", to, base)

    def get_attachments(self, message_id: str) -> list[dict]:
        """GET /me/messages/{id}/attachments — returns raw attachment dicts."""
        try:
            response = requests.get(
                f"{self.graph_endpoint}/me/messages/{message_id}/attachments",
                headers=self.get_headers(),
            )
            response.raise_for_status()
            return response.json().get("value", [])
        except requests.exceptions.RequestException as e:
            detail = e.response.text[:500] if e.response is not None else ""
            logger.error("get_attachments failed for %s: %s %s", message_id, e, detail)
            return []

    def _find_event_id_by_ical_uid(self, ical_uid: str) -> str | None:
        """Return the Graph event id matching iCalUID, or None if not found."""
        try:
            response = requests.get(
                f"{self.graph_endpoint}/me/events",
                headers=self.get_headers(),
                params={
                    "$filter": f"iCalUId eq '{ical_uid}'",
                    "$select": "id,iCalUId",
                    "$top": "1",
                },
            )
            response.raise_for_status()
            items = response.json().get("value", [])
            return items[0]["id"] if items else None
        except requests.exceptions.RequestException as e:
            logger.error("_find_event_id_by_ical_uid failed for %s: %s", ical_uid, e)
            return None

    def accept_event(self, ical_uid: str) -> bool:
        """Find event by iCalUID and POST /me/events/{id}/accept."""
        event_id = self._find_event_id_by_ical_uid(ical_uid)
        if not event_id:
            logger.warning("accept_event: no event found for iCalUID %s", ical_uid)
            return False
        try:
            requests.post(
                f"{self.graph_endpoint}/me/events/{event_id}/accept",
                headers=self.get_headers(),
                json={"sendResponse": True},
            ).raise_for_status()
            logger.info("Accepted event iCalUID=%s", ical_uid)
            return True
        except requests.exceptions.RequestException as e:
            logger.error("accept_event failed for iCalUID=%s: %s", ical_uid, e)
            return False

    def decline_event(self, ical_uid: str) -> bool:
        """POST /me/events/{id}/decline."""
        event_id = self._find_event_id_by_ical_uid(ical_uid)
        if not event_id:
            logger.warning("decline_event: no event found for iCalUID %s", ical_uid)
            return False
        try:
            requests.post(
                f"{self.graph_endpoint}/me/events/{event_id}/decline",
                headers=self.get_headers(),
                json={"sendResponse": True},
            ).raise_for_status()
            logger.info("Declined event iCalUID=%s", ical_uid)
            return True
        except requests.exceptions.RequestException as e:
            logger.error("decline_event failed for iCalUID=%s: %s", ical_uid, e)
            return False

    def tentatively_accept_event(self, ical_uid: str) -> bool:
        """POST /me/events/{id}/tentativelyAccept."""
        event_id = self._find_event_id_by_ical_uid(ical_uid)
        if not event_id:
            logger.warning("tentatively_accept_event: no event found for iCalUID %s", ical_uid)
            return False
        try:
            requests.post(
                f"{self.graph_endpoint}/me/events/{event_id}/tentativelyAccept",
                headers=self.get_headers(),
                json={"sendResponse": True},
            ).raise_for_status()
            logger.info("Tentatively accepted event iCalUID=%s", ical_uid)
            return True
        except requests.exceptions.RequestException as e:
            logger.error("tentatively_accept_event failed for iCalUID=%s: %s", ical_uid, e)
            return False

    def search_emails(self, query: str, mailbox: str = "me", limit: int = 25) -> List[Email]:
        """Search a mailbox using Graph API KQL $search.

        Args:
            query: KQL query string — plain keywords or 'subject:word', 'from:addr', etc.
            mailbox: 'me' for primary mailbox or an email address for a shared mailbox.
            limit: Maximum number of results to return.
        """
        if mailbox == "me":
            endpoint = f"{self.graph_endpoint}/me/messages"
        else:
            endpoint = f"{self.graph_endpoint}/users/{mailbox}/messages"

        params = {
            "$search": f'"{query}"',
            "$top": str(limit),
            "$select": "id,subject,from,toRecipients,receivedDateTime,bodyPreview,webLink",
        }
        try:
            response = requests.get(endpoint, headers=self.get_headers(), params=params)
            response.raise_for_status()
            return [Email(e) for e in response.json().get("value", [])]
        except requests.exceptions.RequestException as e:
            detail = e.response.text[:500] if e.response is not None else ""
            logger.error(
                "search_emails failed for mailbox=%s query=%r: %s %s", mailbox, query, e, detail
            )
            return []

    def get_member_groups(self) -> List[Dict]:
        """Return M365 Unified groups the authenticated user belongs to.

        Returns list of dicts with 'id', 'display_name', 'mail'.
        """
        endpoint = f"{self.graph_endpoint}/me/memberOf"
        params: Optional[Dict[str, str]] = {
            "$select": "id,displayName,mail,groupTypes",
            "$top": "100",
        }
        groups = []
        try:
            while endpoint:
                response = requests.get(endpoint, headers=self.get_headers(), params=params)
                response.raise_for_status()
                data = response.json()
                for item in data.get("value", []):
                    if "Unified" in item.get("groupTypes", []):
                        groups.append(
                            {
                                "id": item["id"],
                                "display_name": item.get("displayName", ""),
                                "mail": item.get("mail", ""),
                            }
                        )
                endpoint = data.get("@odata.nextLink")
                params = None
        except requests.exceptions.RequestException as e:
            detail = e.response.text[:500] if e.response is not None else ""
            logger.error("get_member_groups failed: %s %s", e, detail)
        return groups

    def search_group_conversations(self, group_id: str, query: str, limit: int = 25) -> List[Email]:
        """Search a M365 group's conversations using Graph API $search.

        Returns results normalized into Email objects. Subject is taken from the
        conversation thread; sender and preview from the most recent post.
        """
        endpoint = f"{self.graph_endpoint}/groups/{group_id}/conversations"
        params = {
            "$search": f'"{query}"',
            "$top": str(limit),
            "$select": "id,topic,hasAttachments,lastDeliveredDateTime",
        }
        results = []
        try:
            response = requests.get(endpoint, headers=self.get_headers(), params=params)
            response.raise_for_status()
            for convo in response.json().get("value", []):
                # Fetch threads to get sender + preview from latest post
                threads_resp = requests.get(
                    f"{self.graph_endpoint}/groups/{group_id}/conversations/{convo['id']}/threads",
                    headers=self.get_headers(),
                    params={
                        "$select": "id,topic,sender,preview,lastDeliveredDateTime",
                        "$top": "1",
                    },
                )
                threads_resp.raise_for_status()
                threads = threads_resp.json().get("value", [])
                if not threads:
                    continue
                thread = threads[0]
                # Build a pseudo-Email from the conversation/thread fields
                synthetic = {
                    "id": convo["id"],
                    "subject": convo.get("topic", ""),
                    "from": thread.get("sender") or {},
                    "toRecipients": [],
                    "receivedDateTime": convo.get("lastDeliveredDateTime"),
                    "bodyPreview": thread.get("preview", ""),
                    "webLink": None,
                }
                results.append(Email(synthetic))
        except requests.exceptions.RequestException as e:
            detail = e.response.text[:500] if e.response is not None else ""
            logger.error(
                "search_group_conversations failed for group=%s: %s %s", group_id, e, detail
            )
        return results

    def move_message_to_action_folder(
        self, message_id: str, folder_display_name: str
    ) -> dict | None:
        """
        Move a message to the folder named folder_display_name (e.g. reply_required).
        Returns the moved message object (webLink reflects new folder) or None on failure.
        Requires Mail.ReadWrite (or equivalent) on the token.
        """
        try:
            dest_id = self.get_or_create_mail_folder(folder_display_name)
            move_url = f"{self.graph_endpoint}/me/messages/{message_id}/move"
            response = requests.post(
                move_url,
                headers=self.get_headers(),
                json={"destinationId": dest_id},
            )
            response.raise_for_status()
            logger.info("Moved message %s to folder %s", message_id, folder_display_name)
            return response.json()
        except Exception as e:
            detail = ""
            if isinstance(e, requests.exceptions.RequestException) and e.response is not None:
                detail = e.response.text[:500]
            logger.error(
                "Failed to move message %s to folder %s: %s %s",
                message_id,
                folder_display_name,
                e,
                detail,
            )
            return None
