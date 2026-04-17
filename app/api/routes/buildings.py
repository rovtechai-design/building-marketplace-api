from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.api.deps import get_current_user, get_db
from app.models.building import Building, BuildingMembership

router = APIRouter()


class JoinBuildingIn(BaseModel):
    invite_code: str = Field(..., min_length=1)


@router.post("/join-building")
async def join_building(
    payload: JoinBuildingIn,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    code = payload.invite_code.strip().upper()
    if not code:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="invite_code is required",
        )

    q = await db.execute(select(Building).where(Building.invite_code == code))
    building = q.scalar_one_or_none()
    if not building:
        raise HTTPException(status_code=404, detail="Invalid invite code")

    # ✅ IMPORTANT: capture what we need BEFORE commit()
    building_payload = {
        "id": str(building.id),
        "name": building.name,
        "invite_code": building.invite_code,  # OK for V1 dev/testing
        "vouchers_enabled": building.vouchers_enabled,
    }

    membership = BuildingMembership(user_id=user.id, building_id=building.id)
    db.add(membership)
    if user.building_id is None:
        user.building_id = building.id
        user.profile_completed = user.is_profile_complete

    try:
        await db.commit()
        joined = True
    except IntegrityError:
        # UNIQUE(user_id, building_id) hit -> already a member
        await db.rollback()
        joined = False

    return {"joined": joined, "building": building_payload}


@router.get("/my-buildings")
async def my_buildings(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = await db.execute(
        select(Building)
        .join(BuildingMembership, BuildingMembership.building_id == Building.id)
        .where(BuildingMembership.user_id == user.id)
        .order_by(Building.id.desc())
    )
    buildings = q.scalars().all()

    return {
        "count": len(buildings),
        "buildings": [
            {
                "id": str(b.id),
                "name": b.name,
                "invite_code": b.invite_code,  # OK for V1 dev/testing
                "vouchers_enabled": b.vouchers_enabled,
            }
            for b in buildings
        ],
    }
