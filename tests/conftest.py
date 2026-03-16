import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import deps
from app.main import app
from app.models.base import Base
from app.models import building, listing, listing_image, listing_report, user


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db_sessionmaker(tmp_path) -> AsyncIterator[async_sessionmaker]:
    db_path = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sessionmaker = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield sessionmaker
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def token_claims(monkeypatch):
    claims_map: dict[str, dict] = {}

    def fake_verify_firebase_token(token: str):
        return claims_map[token]

    monkeypatch.setattr(deps, "verify_firebase_token", fake_verify_firebase_token)
    return claims_map


@pytest_asyncio.fixture
async def client(db_sessionmaker, token_claims) -> AsyncIterator[AsyncClient]:
    async def override_get_db():
        async with db_sessionmaker() as session:
            yield session

    app.dependency_overrides[deps.get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client

    app.dependency_overrides.clear()


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
