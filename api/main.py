from fastapi import FastAPI

from api.routers import search

app = FastAPI(title="inbox-api")
app.include_router(search.router)
