#!/usr/bin/env python3
"""
One-time script to generate an MSAL token cache via interactive device code flow.

Run this locally, then store the output in GCP Secret Manager:

    python3 scripts/seed_token_cache.py > /tmp/msal_cache.json
    gcloud secrets versions add msal-token-cache --data-file=/tmp/msal_cache.json
    rm /tmp/msal_cache.json
"""

import os
import sys
import msal
from dotenv import load_dotenv

load_dotenv()


def main():
    client_id = os.getenv('CLIENT_ID')
    tenant_id = os.getenv('TENANT_ID')
    scopes = os.getenv(
        'SCOPES',
        'https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/User.Read'
    ).split()

    if not client_id or not tenant_id:
        print("Error: CLIENT_ID and TENANT_ID must be set in .env", file=sys.stderr)
        sys.exit(1)

    authority = f"https://login.microsoftonline.com/{tenant_id}"
    cache = msal.SerializableTokenCache()
    app = msal.PublicClientApplication(client_id, authority=authority, token_cache=cache)

    flow = app.initiate_device_flow(scopes=scopes)
    if "user_code" not in flow:
        print("Error: Failed to create device flow", file=sys.stderr)
        sys.exit(1)

    print(f"To sign in, open: {flow['verification_uri']}", file=sys.stderr)
    print(f"Enter the code:   {flow['user_code']}", file=sys.stderr)
    print("Waiting for authentication...", file=sys.stderr)

    result = app.acquire_token_by_device_flow(flow)

    if "access_token" not in result:
        print(f"Error: {result.get('error_description', 'Unknown error')}", file=sys.stderr)
        sys.exit(1)

    print("Authentication successful!", file=sys.stderr)

    # Print the serialized cache to stdout so it can be piped to a file / secret
    print(cache.serialize())


if __name__ == "__main__":
    main()
