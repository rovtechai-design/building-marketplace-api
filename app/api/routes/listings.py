from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.building import BuildingMembership
from app.models.listing import Listing

router = APIRouter()


class ListingCreateIn(BaseModel):
    building_id: int
    title: str = Field(..., min_length=1, max_length=140)
    description: str | None = None
    price: float | None = None  # simple for V1


async def require_membership(db: AsyncSession, user_id, building_id: int) -> None:
    q = await db.execute(
        select(BuildingMembership)
        .where(
            BuildingMembership.user_id == user_id,
            BuildingMembership.building_id == building_id,
        )
        .limit(1)
    )
    if q.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this building",
        )


@router.post("/listings")
async def create_listing(
    payload: ListingCreateIn,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_membership(db, user.id, payload.building_id)

    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="title is required")

    listing = Listing(
        title=title,
        description=payload.description,
        price=payload.price,
        user_id=user.id,
        building_id=payload.building_id,
    )
    db.add(listing)

    # ✅ Avoid async expire/lazy-load problems: get id before commit
    await db.flush()
    listing_id = listing.id

    await db.commit()

    return {
        "id": listing_id,
        "building_id": payload.building_id,
        "title": listing.title,
        "description": listing.description,
        "price": float(listing.price) if listing.price is not None else None,
        "user_id": str(user.id),
    }


@router.get("/listings")
async def list_listings(
    building_id: int = Query(...),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_membership(db, user.id, building_id)

    q = await db.execute(
        select(Listing)
        .where(Listing.building_id == building_id)
        .order_by(Listing.created_at.desc())
    )
    listings = q.scalars().all()

    return {
        "count": len(listings),
        "listings": [
            {
                "id": l.id,
                "building_id": l.building_id,
                "title": l.title,
                "description": l.description,
                "price": float(l.price) if l.price is not None else None,
                "user_id": str(l.user_id),
                "created_at": l.created_at.isoformat() if l.created_at else None,
                "expires_at": l.expires_at.isoformat() if l.expires_at else None,
            }
            for l in listings
        ],
    }


@router.delete("/listings/{listing_id}")
async def delete_listing(
    listing_id: int,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = await db.execute(select(Listing).where(Listing.id == listing_id))
    listing = q.scalar_one_or_none()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    # ✅ owner-only
    if str(listing.user_id) != str(user.id):
        raise HTTPException(status_code=403, detail="Not allowed")

    # ✅ still enforce membership (paranoid but correct)
    await require_membership(db, user.id, listing.building_id)

    await db.delete(listing)
    await db.commit()
    return {"deleted": True, "id": listing_id}