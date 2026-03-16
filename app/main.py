from fastapi import FastAPI
from contextlib import asynccontextmanager

from app.core.config import settings
from app.core.db import test_connection, create_tables

# Routers
from app.api.routes.me import router as me_router
from app.api.routes.buildings import router as buildings_router
from app.api.routes.listings import router as listings_router
from app.api.routes.moderation import router as moderation_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    result = await test_connection()
    print("DATABASE CONNECTED:", result)

    await create_tables()
    print("TABLES SYNCED")

    yield


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)

# Register routes
app.include_router(me_router)
app.include_router(buildings_router)
app.include_router(listings_router)
app.include_router(moderation_router)


@app.get("/health")
def health():
    return {"status": "ok", "env": settings.APP_ENV}
