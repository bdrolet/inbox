"""
Microsoft Graph API Email Client
Handles authentication and email retrieval from Outlook/Office 365
"""

import os
import json
import requests
import logging
from typing import List, Dict, Optional
from datetime import datetime, timedelta
import msal
from dotenv import load_dotenv
from models import Email

# Set up logging
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

class GraphEmailClient:
    def __init__(self):
        self.client_id = os.getenv('CLIENT_ID')
        self.client_secret = os.getenv('CLIENT_SECRET')
        self.tenant_id = os.getenv('TENANT_ID')
        self.redirect_uri = os.getenv('REDIRECT_URI', 'http://localhost:8080/callback')
        self.scopes = os.getenv('SCOPES', 'https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/User.Read').split()
        
        # Validate configuration
        if not all([self.client_id, self.client_secret, self.tenant_id]):
            raise ValueError("Missing required environment variables. Please check your .env file.")
        
        self.authority = f"https://login.microsoftonline.com/{self.tenant_id}"
        self.graph_endpoint = "https://graph.microsoft.com/v1.0"
        self.access_token = None
        self.token_cache_file = ".token_cache.json"
        # displayName -> folder id (action folders: reply_required, no_action, etc.)
        self._mail_folder_id_cache: Dict[str, str] = {}
        
        # Initialize MSAL app for public client (interactive auth) with token cache
        self.app = msal.PublicClientApplication(
            self.client_id,
            authority=self.authority,
            token_cache=msal.SerializableTokenCache()
        )
        
        # Load existing token cache if it exists
        if os.path.exists(self.token_cache_file):
            self.app.token_cache.deserialize(open(self.token_cache_file, 'r').read())
    
    def authenticate(self) -> bool:
        """
        Authenticate using client credentials flow (for app-only access)
        This is suitable for accessing shared mailboxes or when you don't need user interaction
        """
        try:
            result = self.app.acquire_token_silent(self.scopes, account=None)
            
            if not result:
                result = self.app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
            
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
            with open(self.token_cache_file, 'w') as cache_file:
                cache_file.write(self.app.token_cache.serialize())

    def _load_cache_from_secret_manager(self) -> str:
        """Load MSAL token cache from GCP Secret Manager."""
        from google.cloud import secretmanager

        project_id = os.getenv('GCP_PROJECT_ID')
        secret_name = os.getenv('MSAL_SECRET_NAME', 'msal-token-cache')
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("UTF-8")

    def _save_cache_to_secret_manager(self):
        """Write the updated MSAL token cache back to GCP Secret Manager."""
        if not self.app.token_cache.has_state_changed:
            return

        from google.cloud import secretmanager

        project_id = os.getenv('GCP_PROJECT_ID')
        secret_name = os.getenv('MSAL_SECRET_NAME', 'msal-token-cache')
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
            
            print(f"\nTo sign in, use a web browser to open the page:")
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
                print(f"Device flow authentication failed: {result.get('error_description', 'Unknown error')}")
                return False
                
        except Exception as e:
            print(f"Device flow authentication error: {str(e)}")
            return False
    
    def get_headers(self) -> Dict[str, str]:
        """Get headers for Graph API requests"""
        if not self.access_token:
            raise ValueError("Not authenticated. Call authenticate() first.")
        
        return {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }
    
    def get_emails_since(self, since_datetime: datetime, folder: str = 'inbox') -> List[Email]:
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
            since_str = since_datetime.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            
            # Query parameters
            params = {
                '$filter': f"receivedDateTime ge {since_str}",
                '$orderby': 'receivedDateTime desc',
                '$select': 'id,subject,from,receivedDateTime,bodyPreview,isRead,hasAttachments'
            }
            
            response = requests.get(endpoint, headers=self.get_headers(), params=params)
            response.raise_for_status()
            
            data = response.json()
            emails_data = data.get('value', [])
            return [Email(email_data) for email_data in emails_data]
            
        except requests.exceptions.RequestException as e:
            print(f"Error retrieving emails: {str(e)}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"Response: {e.response.text}")
            return []
    
    def get_latest_emails(self, count: int = 10, folder: str = 'inbox') -> List[Email]:
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
                '$top': count,
                '$orderby': 'receivedDateTime desc',
                '$select': 'id,subject,from,receivedDateTime,bodyPreview,isRead,hasAttachments'
            }
            
            response = requests.get(endpoint, headers=self.get_headers(), params=params)
            response.raise_for_status()
            
            data = response.json()
            emails_data = data.get('value', [])
            return [Email(email_data) for email_data in emails_data]
            
        except requests.exceptions.RequestException as e:
            print(f"Error retrieving emails: {str(e)}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"Response: {e.response.text}")
            return []
    
    def get_all_emails(self, folder: str = 'inbox', page_size: int = 100) -> List[Email]:
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
            '$top': page_size,
            '$orderby': 'receivedDateTime desc',
            '$select': 'id,subject,from,receivedDateTime,bodyPreview,isRead,hasAttachments'
        }

        try:
            while endpoint:
                response = requests.get(endpoint, headers=self.get_headers(), params=params)
                response.raise_for_status()
                data = response.json()

                all_emails.extend(Email(e) for e in data.get('value', []))
                logger.info(f"Fetched {len(all_emails)} emails so far...")

                endpoint = data.get('@odata.nextLink')
                params = None  # nextLink already includes query params

            logger.info(f"Finished fetching {len(all_emails)} total emails from {folder}")
            return all_emails

        except requests.exceptions.RequestException as e:
            print(f"Error retrieving emails: {str(e)}")
            if hasattr(e, 'response') and e.response is not None:
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
                '$select': 'id,subject,from,toRecipients,ccRecipients,bccRecipients,receivedDateTime,sentDateTime,body,bodyPreview,isRead,hasAttachments,attachments'
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
            
            data = {
                "isRead": True
            }
            
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

    def move_message_to_action_folder(self, message_id: str, folder_display_name: str) -> bool:
        """
        Move a message to the folder named folder_display_name (e.g. reply_required).
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
            return True
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
            return False
