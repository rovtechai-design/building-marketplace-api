from fastapi import Header, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal
from app.core.security import verify_firebase_token
from app.models.user import User


async def get_db():
    async with SessionLocal() as session:
        yield session


async def get_current_user(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")

    token = authorization.split(" ", 1)[1].strip()

    # ✅ DO NOT swallow the real Firebase error
    decoded = verify_firebase_token(token)

    firebase_uid = decoded.get("uid")
    email = decoded.get("email")

    if not firebase_uid:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    q = await db.execute(select(User).where(User.firebase_uid == firebase_uid))
    user = q.scalar_one_or_none()

    if not user:
        user = User(firebase_uid=firebase_uid, email=email)
        db.add(user)
        await db.commit()
        await db.refresh(user)

    return user