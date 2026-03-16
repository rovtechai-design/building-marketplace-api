import logging
from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.building import Building, BuildingMembership
from app.models.user import User
from app.schemas.user import MeOut, MeUpdateIn

logger = logging.getLogger(__name__)
router = APIRouter()


def serialize_me(user: User) -> MeOut:
    public_alias = user.public_alias or user.display_name
    return MeOut(
        id=str(user.id),
        firebase_uid=user.firebase_uid,
        email=user.email,
        display_name=user.display_name,
        public_alias=public_alias,
        real_name=user.full_name,
        full_name=user.full_name,
        building_id=user.building_id,
        room_number=user.room_number_private,
        room_number_private=user.room_number_private,
        profile_picture_url=user.profile_picture_url,
        role=user.role,
        profile_completed=user.profile_completed,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


def compute_profile_completed(user: User) -> bool:
    return bool(
        (user.public_alias or user.display_name)
        and user.full_name
        and user.building_id is not None
        and user.room_number_private
    )


@router.get("/me", response_model=MeOut)
async def me(user=Depends(get_current_user)):
    request_started_at = perf_counter()
    logger.info("me.request entered user_id=%s firebase_uid=%s", user.id, user.firebase_uid)
    response = serialize_me(user)
    logger.info(
        "me.response about to return user_id=%s elapsed_ms=%.2f",
        user.id,
        (perf_counter() - request_started_at) * 1000,
    )
    return response


@router.patch("/me", response_model=MeOut)
async def update_me(
    payload: MeUpdateIn,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    updates = payload.model_dump(exclude_unset=True)

    if "display_name" in updates:
        display_name = updates["display_name"].strip() if updates["display_name"] is not None else None
        if not display_name:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="display_name is required",
            )
        user.display_name = display_name
        if not user.public_alias:
            user.public_alias = display_name

    if "public_alias" in updates:
        public_alias = updates["public_alias"].strip() if updates["public_alias"] is not None else None
        if not public_alias:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="public_alias is required",
            )
        user.public_alias = public_alias
        user.display_name = public_alias

    if "real_name" in updates:
        real_name = updates["real_name"].strip() if updates["real_name"] is not None else None
        user.full_name = real_name or None

    if "full_name" in updates:
        full_name = updates["full_name"].strip() if updates["full_name"] is not None else None
        user.full_name = full_name or None

    if "room_number" in updates:
        room_number = updates["room_number"].strip() if updates["room_number"] is not None else None
        user.room_number_private = room_number or None

    if "room_number_private" in updates:
        room_number_private = (
            updates["room_number_private"].strip()
            if updates["room_number_private"] is not None
            else None
        )
        user.room_number_private = room_number_private or None

    if "profile_picture_url" in updates:
        profile_picture_url = (
            updates["profile_picture_url"].strip()
            if updates["profile_picture_url"] is not None
            else None
        )
        user.profile_picture_url = profile_picture_url or None

    if "building_id" in updates:
        building_id = updates["building_id"]
        if building_id is None:
            user.building_id = None
        else:
            building_q = await db.execute(select(Building).where(Building.id == building_id))
            building = building_q.scalar_one_or_none()
            if not building:
                raise HTTPException(status_code=404, detail="Building not found")

            membership_q = await db.execute(
                select(BuildingMembership).where(
                    BuildingMembership.user_id == user.id,
                    BuildingMembership.building_id == building_id,
                )
            )
            membership = membership_q.scalar_one_or_none()
            if membership is None:
                db.add(BuildingMembership(user_id=user.id, building_id=building_id))

            user.building_id = building_id

    user.profile_completed = compute_profile_completed(user)
    await db.commit()
    await db.refresh(user)
    return serialize_me(user)
