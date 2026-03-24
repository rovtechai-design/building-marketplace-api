from datetime import datetime, timezone
import secrets

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

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
LISTING_STATUS_IN_PROGRESS = "in_progress"
LISTING_STATUS_SOLD = "sold"
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


class ListingBuyOut(BaseModel):
    success: bool
    listing_id: int
    status: str
    buyer_user_id: str
    reserved_at: str


class ListingConfirmPinIn(BaseModel):
    pin: str = Field(..., min_length=4, max_length=4, pattern=r"^\d{4}$")


class ListingConfirmPinOut(BaseModel):
    success: bool
    listing_id: int
    status: str
    sold_at: str


class OrderSellerOut(BaseModel):
    id: str
    display_name: str | None = None


class OrderOut(BaseModel):
    id: int
    listing_id: int
    title: str
    price: float | None = None
    seller: OrderSellerOut
    user_id: str
    seller_display_name: str | None = None
    buyer_user_id: str | None = None
    status: str
    images: list[str]
    image_urls: list[str]
    created_at: str | None = None
    reserved_at: str | None = None
    sold_at: str | None = None
    purchased_at: str | None = None
    buyer_pin: str | None = None
    has_buyer_pin: bool = False


class OrdersMeOut(BaseModel):
    count: int
    orders: list[OrderOut]


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


def serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


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


def serialize_listing(
    listing: Listing,
    seller: User | None = None,
    buyer: User | None = None,
    *,
    include_buyer_display_name: bool = False,
) -> dict:
    seller_display_name = (seller.public_alias or seller.display_name) if seller else None
    buyer_display_name = None
    if include_buyer_display_name and buyer:
        buyer_display_name = buyer.public_alias or buyer.display_name
    image_urls = [img.image_url for img in listing.images]
    return {
        "id": listing.id,
        "building_id": listing.building_id,
        "title": listing.title,
        "description": listing.description,
        "price": float(listing.price) if listing.price is not None else None,
        "user_id": str(listing.user_id),
        "seller_display_name": seller_display_name,
        "images": image_urls,
        "image_urls": image_urls,
        "created_at": serialize_datetime(listing.created_at),
        "expires_at": serialize_datetime(listing.expires_at),
        "status": compute_listing_status(listing),
        "buyer_user_id": str(listing.buyer_user_id) if listing.buyer_user_id else None,
        "buyer_display_name": buyer_display_name,
        "reserved_at": serialize_datetime(listing.reserved_at),
        "sold_at": serialize_datetime(listing.sold_at),
    }


def serialize_order(listing: Listing, seller: User, *, include_buyer_pin: bool = False) -> dict:
    seller_display_name = seller.public_alias or seller.display_name
    image_urls = [img.image_url for img in listing.images]
    buyer_pin = None
    if include_buyer_pin and listing.status == LISTING_STATUS_IN_PROGRESS:
        buyer_pin = listing.transaction_pin
    return {
        "id": listing.id,
        "listing_id": listing.id,
        "title": listing.title,
        "price": float(listing.price) if listing.price is not None else None,
        "seller": {
            "id": str(seller.id),
            "display_name": seller_display_name,
        },
        "user_id": str(listing.user_id),
        "seller_display_name": seller_display_name,
        "buyer_user_id": str(listing.buyer_user_id) if listing.buyer_user_id else None,
        "status": listing.status,
        "images": image_urls,
        "image_urls": image_urls,
        "created_at": serialize_datetime(listing.created_at),
        "reserved_at": serialize_datetime(listing.reserved_at),
        "sold_at": serialize_datetime(listing.sold_at),
        "purchased_at": (
            serialize_datetime(listing.reserved_at) if listing.reserved_at else serialize_datetime(listing.sold_at)
        ),
        "buyer_pin": buyer_pin,
        "has_buyer_pin": buyer_pin is not None,
    }


async def get_listing_with_people(
    db: AsyncSession,
    *,
    listing_id: int,
) -> tuple[Listing | None, User | None, User | None]:
    seller = aliased(User)
    buyer = aliased(User)
    result = await db.execute(
        select(Listing, seller, buyer)
        .join(seller, seller.id == Listing.user_id)
        .outerjoin(buyer, buyer.id == Listing.buyer_user_id)
        .options(selectinload(Listing.images))
        .where(Listing.id == listing_id)
    )
    return result.one_or_none() or (None, None, None)


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


async def buy_listing_for_user(
    *,
    listing_id: int,
    user: User,
    db: AsyncSession,
) -> ListingBuyOut:
    listing, _, _ = await get_listing_with_people(db, listing_id=listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    await require_membership(db, user.id, listing.building_id)

    if str(listing.user_id) == str(user.id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="You cannot buy your own listing",
        )

    if compute_listing_status(listing) != LISTING_STATUS_ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Listing is not available for purchase",
        )

    reserved_at = datetime.now(timezone.utc)
    transaction_pin = f"{secrets.randbelow(10000):04d}"
    result = await db.execute(
        update(Listing)
        .where(
            Listing.id == listing.id,
            Listing.status == LISTING_STATUS_ACTIVE,
            Listing.buyer_user_id.is_(None),
        )
        .values(
            status=LISTING_STATUS_IN_PROGRESS,
            buyer_user_id=user.id,
            reserved_at=reserved_at,
            sold_at=None,
            transaction_pin=transaction_pin,
        )
    )
    if result.rowcount != 1:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Listing was already purchased",
        )

    await db.commit()

    return ListingBuyOut(
        success=True,
        listing_id=listing.id,
        status=LISTING_STATUS_IN_PROGRESS,
        buyer_user_id=str(user.id),
        reserved_at=reserved_at.isoformat(),
    )


async def confirm_listing_pin_for_seller(
    *,
    listing_id: int,
    pin: str,
    user: User,
    db: AsyncSession,
) -> ListingConfirmPinOut:
    listing, _, _ = await get_listing_with_people(db, listing_id=listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    await require_membership(db, user.id, listing.building_id)

    if str(listing.user_id) != str(user.id):
        raise HTTPException(status_code=403, detail="Not allowed")

    if listing.status != LISTING_STATUS_IN_PROGRESS or not listing.buyer_user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Listing is not awaiting PIN confirmation",
        )

    if listing.transaction_pin != pin:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid transaction PIN",
        )

    sold_at = datetime.now(timezone.utc)
    result = await db.execute(
        update(Listing)
        .where(
            Listing.id == listing.id,
            Listing.user_id == user.id,
            Listing.status == LISTING_STATUS_IN_PROGRESS,
            Listing.transaction_pin == pin,
        )
        .values(
            status=LISTING_STATUS_SOLD,
            sold_at=sold_at,
            transaction_pin=None,
        )
    )
    if result.rowcount != 1:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Listing could not be confirmed",
        )

    await db.commit()

    return ListingConfirmPinOut(
        success=True,
        listing_id=listing.id,
        status=LISTING_STATUS_SOLD,
        sold_at=sold_at.isoformat(),
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

    seller = aliased(User)
    q = await db.execute(
        select(Listing, seller)
        .join(seller, seller.id == Listing.user_id)
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
        "listings": [serialize_listing(listing, seller_row) for listing, seller_row in rows],
    }


@router.get("/my-listings")
async def list_my_listings(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    seller = aliased(User)
    buyer = aliased(User)
    q = await db.execute(
        select(Listing, seller, buyer)
        .join(seller, seller.id == Listing.user_id)
        .outerjoin(buyer, buyer.id == Listing.buyer_user_id)
        .options(selectinload(Listing.images))
        .where(Listing.user_id == user.id)
        .order_by(Listing.created_at.desc())
    )
    rows = q.all()

    return {
        "count": len(rows),
        "listings": [
            serialize_listing(
                listing,
                seller_row,
                buyer_row,
                include_buyer_display_name=True,
            )
            for listing, seller_row, buyer_row in rows
        ],
    }


@router.get("/orders/me", response_model=OrdersMeOut)
async def list_my_orders(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    seller = aliased(User)
    q = await db.execute(
        select(Listing, seller)
        .join(seller, seller.id == Listing.user_id)
        .options(selectinload(Listing.images))
        .where(Listing.buyer_user_id == user.id)
        .where(Listing.status.in_([LISTING_STATUS_IN_PROGRESS, LISTING_STATUS_SOLD]))
        .order_by(Listing.reserved_at.desc(), Listing.sold_at.desc(), Listing.created_at.desc())
    )
    rows = q.all()

    return {
        "count": len(rows),
        "orders": [
            serialize_order(listing, seller_row, include_buyer_pin=True)
            for listing, seller_row in rows
        ],
    }


@router.post("/listings/{listing_id}/buy", response_model=ListingBuyOut)
async def buy_listing(
    listing_id: int,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await buy_listing_for_user(
        listing_id=listing_id,
        user=user,
        db=db,
    )


@router.post("/listings/{listing_id}/confirm-pin", response_model=ListingConfirmPinOut)
async def confirm_listing_pin(
    listing_id: int,
    payload: ListingConfirmPinIn,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await confirm_listing_pin_for_seller(
        listing_id=listing_id,
        pin=payload.pin,
        user=user,
        db=db,
    )


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
