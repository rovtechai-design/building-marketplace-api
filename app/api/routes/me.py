from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.building import BuildingMembership, Building

router = APIRouter()


@router.get("/me")
async def me(user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    q = await db.execute(
        select(Building.id, Building.name, Building.invite_code)
        .join(BuildingMembership, BuildingMembership.building_id == Building.id)
        .where(BuildingMembership.user_id == user.id)
        .order_by(Building.id.desc())
    )
    buildings = [{"id": r[0], "name": r[1], "invite_code": r[2]} for r in q.all()]

    return {"id": str(user.id), "email": user.email, "buildings": buildings}
