from fastapi import FastAPI
from app.core.config import settings
from app.core.db import test_connection, create_tables
from app.api.routes.buildings import router as buildings_router


# Import routers
from app.api.routes.me import router as me_router

app = FastAPI(title=settings.APP_NAME)

# Register routes
app.include_router(me_router)
app.include_router(buildings_router)



@app.on_event("startup")
async def startup():
    result = await test_connection()
    print("DATABASE CONNECTED:", result)

    await create_tables()
    print("TABLES SYNCED")


@app.get("/health")
def health():
    return {"status": "ok", "env": settings.APP_ENV}
