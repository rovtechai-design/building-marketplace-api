import logging
from time import perf_counter

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal
from app.core.security import verify_firebase_token
from app.models.user import User

logger = logging.getLogger(__name__)
SPECIAL_ADMIN_EMAIL = "kevinlukeuwu@gmail.com"


async def get_db():
    async with SessionLocal() as session:
        yield session


def apply_role_overrides(user: User, email: str | None) -> bool:
    if email and email.lower() == SPECIAL_ADMIN_EMAIL and user.role != "admin":
        user.role = "admin"
        return True
    return False


def ensure_moderator_access(user: User) -> User:
    if user.role not in {"ambassador", "admin"}:
        raise HTTPException(status_code=403, detail="Not allowed")
    if user.role == "ambassador" and user.building_id is None:
        raise HTTPException(status_code=403, detail="Ambassador is not assigned to a building")
    return user


async def get_current_user(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    auth_started_at = perf_counter()
    logger.info("auth.get_current_user entered")

    try:
        logger.info(
            "auth.authorization header received: present=%s bearer_prefix=%s",
            bool(authorization),
            authorization.startswith("Bearer ") if authorization else False,
        )
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing Bearer token")

        token = authorization.split(" ", 1)[1].strip()
        logger.info("auth.token parsed: length=%s", len(token))

        logger.info("auth.firebase verification start")
        decoded = verify_firebase_token(token)
        logger.info("auth.firebase verification success")

        firebase_uid = decoded.get("uid")
        email = decoded.get("email")
        display_name = decoded.get("name") or decoded.get("display_name")
        logger.info("auth.decoded token uid=%s email=%s", firebase_uid, email)

        if not firebase_uid:
            raise HTTPException(status_code=401, detail="Invalid token payload")

        logger.info("auth.db lookup start uid=%s", firebase_uid)
        q = await db.execute(select(User).where(User.firebase_uid == firebase_uid))
        user = q.scalar_one_or_none()
        logger.info("auth.db lookup result found=%s uid=%s", user is not None, firebase_uid)

        if not user:
            logger.info("auth.creating local user uid=%s email=%s", firebase_uid, email)
            user = User(
                firebase_uid=firebase_uid,
                email=email,
                display_name=display_name,
                public_alias=display_name,
            )
            apply_role_overrides(user, email)
            db.add(user)
            await db.commit()
            await db.refresh(user)
            logger.info(
                "auth.local user created uid=%s user_id=%s elapsed_ms=%.2f",
                firebase_uid,
                user.id,
                (perf_counter() - auth_started_at) * 1000,
            )
            return user

        changed = False
        if email and user.email != email:
            user.email = email
            changed = True
        if display_name and not user.display_name:
            user.display_name = display_name
            changed = True
        if display_name and not user.public_alias:
            user.public_alias = display_name
            changed = True
        if apply_role_overrides(user, email):
            changed = True

        if changed:
            logger.info("auth.updating local user uid=%s", firebase_uid)
            await db.commit()
            await db.refresh(user)

        logger.info(
            "auth.get_current_user returning uid=%s user_id=%s elapsed_ms=%.2f",
            firebase_uid,
            user.id,
            (perf_counter() - auth_started_at) * 1000,
        )
        return user
    except HTTPException:
        logger.exception("auth.http exception path")
        raise
    except SQLAlchemyError as exc:
        await db.rollback()
        logger.exception("auth.database failure")
        raise HTTPException(status_code=500, detail=f"Database auth failure: {type(exc).__name__}") from exc
    except Exception as exc:
        logger.exception("auth.unexpected exception path")
        raise HTTPException(status_code=500, detail=f"Unexpected auth failure: {type(exc).__name__}") from exc
