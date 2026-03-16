from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text

from app.core.config import settings

# Register models so SQLAlchemy knows them
from app.models.base import Base
from app.models import user, building, listing, listing_image, listing_report

engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
SessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def test_connection():
    async with engine.begin() as conn:
        result = await conn.execute(text("select 1"))
        return result.scalar()


async def create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
