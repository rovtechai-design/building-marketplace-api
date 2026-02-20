from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.building import Building, BuildingMembership

router = APIRouter()


class JoinBuildingIn(BaseModel):
    invite_code: str


@router.post("/join-building")
async def join_building(
    payload: JoinBuildingIn,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    code = payload.invite_code.strip()

    q = await db.execute(select(Building).where(Building.invite_code == code))
    building = q.scalar_one_or_none()
    if not building:
        raise HTTPException(status_code=404, detail="Invalid invite code")

    q2 = await db.execute(
        select(BuildingMembership).where(
            BuildingMembership.user_id == user.id,
            BuildingMembership.building_id == building.id,
        )
    )
    existing = q2.scalar_one_or_none()
    if existing:
        return {"joined": True, "building_id": building.id}

    membership = BuildingMembership(user_id=user.id, building_id=building.id)
    db.add(membership)
    await db.commit()

    return {"joined": True, "building_id": building.id}
