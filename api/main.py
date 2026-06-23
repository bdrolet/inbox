from fastapi import FastAPI

from api.routers import emails, search

app = FastAPI(title="inbox-api")
app.include_router(search.router)
app.include_router(emails.router)
