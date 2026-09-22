"""inbox-redirect: the one anonymous surface.

GET /r/{uuid} resolves a message to its Outlook webLink and 302s. Its caller
is a tap on an ntfy push notification, which carries no Google credential,
so this runs as its own public Cloud Run service (terraform/redirect.tf)
while inbox-api itself is behind Cloud Run IAM. Same image, different
entrypoint (uvicorn api.redirect_app:app)."""

import logging

from fastapi import FastAPI

from api.routers import redirect

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s", force=True)

app = FastAPI(title="inbox-redirect")
app.include_router(redirect.router)
