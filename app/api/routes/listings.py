from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_db
from app.models.building import BuildingMembership
from app.models.listing import Listing
from app.models.listing_image import ListingImage
from app.models.listing_report import ListingReport
from app.models.user import User
from app.schemas.listing_image import ListingImageOut
from app.schemas.listing_report import ListingReportCreateIn, ListingReportCreateOut, ReportCreateIn
from app.services.storage import upload_listing_image

LISTING_STATUS_ACTIVE = "active"
LISTING_STATUS_HIDDEN = "hidden"
LISTING_STATUS_DELETED = "deleted"
REPORT_STATUS_OPEN = "open"
REPORT_STATUS_REVIEWED = "reviewed"
REPORT_STATUS_DISMISSED = "dismissed"
REPORT_STATUS_ACTIONED = "actioned"
AUTO_HIDE_OPEN_REPORT_THRESHOLD = 3
ALLOWED_REPORT_REASONS = {
    "prohibited_item",
    "suspicious_illegal",
    "scam_misleading",
    "harassment",
    "other",
}

router = APIRouter()


class ListingCreateIn(BaseModel):
    building_id: int
    title: str = Field(..., min_length=1, max_length=140)
    description: str | None = None
    price: float | None = None


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


def compute_listing_status(listing: Listing) -> str:
    if listing.status != LISTING_STATUS_ACTIVE:
        return listing.status

    if listing.expires_at is None:
        return LISTING_STATUS_ACTIVE

    expires_at = listing.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        return "expired"

    return LISTING_STATUS_ACTIVE


def serialize_listing(listing: Listing, seller: User | None = None) -> dict:
    seller_display_name = (seller.public_alias or seller.display_name) if seller else None
    return {
        "id": listing.id,
        "building_id": listing.building_id,
        "title": listing.title,
        "description": listing.description,
        "price": float(listing.price) if listing.price is not None else None,
        "user_id": str(listing.user_id),
        "seller_display_name": seller_display_name,
        "images": [img.image_url for img in listing.images],
        "created_at": listing.created_at.isoformat() if listing.created_at else None,
        "expires_at": listing.expires_at.isoformat() if listing.expires_at else None,
        "status": compute_listing_status(listing),
    }


async def create_listing_report(
    *,
    listing_id: int,
    payload: ListingReportCreateIn,
    user: User,
    db: AsyncSession,
) -> ListingReportCreateOut:
    reason = payload.reason.strip().lower()
    if reason not in ALLOWED_REPORT_REASONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid report reason",
        )

    q = await db.execute(select(Listing).where(Listing.id == listing_id))
    listing = q.scalar_one_or_none()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    await require_membership(db, user.id, listing.building_id)

    if str(listing.user_id) == str(user.id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="You cannot report your own listing",
        )

    details = payload.details.strip() if payload.details else None
    report = ListingReport(
        listing_id=listing.id,
        reporter_user_id=user.id,
        reported_user_id=listing.user_id,
        building_id=listing.building_id,
        reason=reason,
        details=details or None,
        status=REPORT_STATUS_OPEN,
    )
    db.add(report)

    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You have already reported this listing",
        )

    open_reports_q = await db.execute(
        select(func.count(ListingReport.id)).where(
            ListingReport.listing_id == listing.id,
            ListingReport.status == REPORT_STATUS_OPEN,
        )
    )
    open_reports = open_reports_q.scalar_one()
    auto_hidden = False
    if open_reports >= AUTO_HIDE_OPEN_REPORT_THRESHOLD and listing.status == LISTING_STATUS_ACTIVE:
        listing.status = LISTING_STATUS_HIDDEN
        auto_hidden = True

    await db.commit()

    return ListingReportCreateOut(
        success=True,
        report_id=report.id,
        status=report.status,
        auto_hidden=auto_hidden,
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
        status=LISTING_STATUS_ACTIVE,
    )
    db.add(listing)
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
        select(Listing, User)
        .join(User, User.id == Listing.user_id)
        .options(selectinload(Listing.images))
        .where(
            Listing.building_id == building_id,
            Listing.status == LISTING_STATUS_ACTIVE,
        )
        .order_by(Listing.created_at.desc())
    )
    rows = q.all()

    return {
        "count": len(rows),
        "listings": [serialize_listing(listing, seller) for listing, seller in rows],
    }


@router.get("/my-listings")
async def list_my_listings(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = await db.execute(
        select(Listing, User)
        .join(User, User.id == Listing.user_id)
        .options(selectinload(Listing.images))
        .where(Listing.user_id == user.id)
        .order_by(Listing.created_at.desc())
    )
    rows = q.all()

    return {
        "count": len(rows),
        "listings": [serialize_listing(listing, seller) for listing, seller in rows],
    }


@router.post("/listings/{listing_id}/report", response_model=ListingReportCreateOut)
async def report_listing(
    listing_id: int,
    payload: ListingReportCreateIn,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_listing_report(
        listing_id=listing_id,
        payload=payload,
        user=user,
        db=db,
    )


@router.post("/reports", response_model=ListingReportCreateOut)
async def create_report(
    payload: ReportCreateIn,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_listing_report(
        listing_id=payload.listing_id,
        payload=ListingReportCreateIn(reason=payload.reason, details=payload.details),
        user=user,
        db=db,
    )


@router.get("/reports/mine")
async def list_my_reports(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = await db.execute(
        select(ListingReport, Listing)
        .join(Listing, Listing.id == ListingReport.listing_id)
        .where(ListingReport.reporter_user_id == user.id)
        .order_by(ListingReport.created_at.desc())
    )
    rows = q.all()
    return {
        "count": len(rows),
        "reports": [
            {
                "id": report.id,
                "listing_id": report.listing_id,
                "building_id": report.building_id,
                "reason": report.reason,
                "details": report.details,
                "status": report.status,
                "action_taken": report.action_taken,
                "created_at": report.created_at.isoformat() if report.created_at else None,
                "listing": {
                    "id": listing.id,
                    "title": listing.title,
                    "status": listing.status,
                },
            }
            for report, listing in rows
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

    if str(listing.user_id) != str(user.id):
        raise HTTPException(status_code=403, detail="Not allowed")

    await require_membership(db, user.id, listing.building_id)

    await db.delete(listing)
    await db.commit()
    return {"deleted": True, "id": listing_id}


@router.post(
    "/listings/{listing_id}/images",
    response_model=ListingImageOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_listing_image(
    listing_id: int,
    file: UploadFile = File(...),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = await db.execute(select(Listing).where(Listing.id == listing_id))
    listing = q.scalar_one_or_none()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    if str(listing.user_id) != str(user.id):
        raise HTTPException(status_code=403, detail="Not allowed")

    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="file must be an image",
        )

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="file is required",
        )

    image_url = upload_listing_image(
        listing_id=listing.id,
        filename=file.filename,
        content_type=file.content_type,
        content=file_bytes,
    )

    image = ListingImage(listing_id=listing.id, image_url=image_url)
    db.add(image)
    await db.flush()
    await db.commit()
    await db.refresh(image)

    return image
