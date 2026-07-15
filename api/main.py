import logging

from fastapi import FastAPI

from api.routers import emails, redirect, search

# force=True installs a fresh stderr handler even though the Cloud Run/uvicorn
# runtime configures the root logger first (otherwise basicConfig no-ops and the
# app's own logger.* output never reaches Cloud Logging — only uvicorn's own logs
# do). uvicorn's log config (disable_existing_loggers=False) leaves this handler in
# place, so app-code logs propagate to root -> stderr. See
# docs/otel-metrics-in-cloud-functions.md.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s", force=True)

app = FastAPI(title="inbox-api")
app.include_router(search.router)
app.include_router(emails.router)
app.include_router(redirect.router)
