from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import ensure_moderator_access, get_current_user, get_db
from app.api.routes.listings import (
    LISTING_STATUS_ACTIVE,
    LISTING_STATUS_HIDDEN,
    REPORT_STATUS_ACTIONED,
    REPORT_STATUS_DISMISSED,
    REPORT_STATUS_OPEN,
    serialize_listing,
)
from app.models.listing import Listing
from app.models.listing_report import ListingReport
from app.models.user import User
from app.schemas.listing_report import ModerationActionOut, ModerationQueueOut, ModerationReportOut, ReportReviewIn

router = APIRouter()


def build_available_actions(listing: Listing) -> list[str]:
    if listing.status == LISTING_STATUS_HIDDEN:
        return ["unhide"]
    if listing.status == LISTING_STATUS_ACTIVE:
        return ["hide", "dismiss"]
    return []


async def get_scoped_report(
    report_id: int,
    moderator: User,
    db: AsyncSession,
) -> tuple[ListingReport, Listing]:
    q = select(ListingReport, Listing).join(Listing, Listing.id == ListingReport.listing_id).where(
        ListingReport.id == report_id
    )
    if moderator.role == "ambassador":
        q = q.where(ListingReport.building_id == moderator.building_id)

    row = (await db.execute(q)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Report not found")

    report, listing = row
    return report, listing


async def close_open_reports_for_listing(
    listing_id: int,
    moderator: User,
    db: AsyncSession,
    report_status: str,
    action_taken: str,
) -> int:
    q = select(ListingReport).where(
        ListingReport.listing_id == listing_id,
        ListingReport.status == REPORT_STATUS_OPEN,
    )
    if moderator.role == "ambassador":
        q = q.where(ListingReport.building_id == moderator.building_id)

    reports = (await db.execute(q)).scalars().all()
    reviewed_at = datetime.now(timezone.utc)
    for report in reports:
        report.status = report_status
        report.reviewed_at = reviewed_at
        report.reviewed_by_user_id = moderator.id
        report.action_taken = action_taken

    return len(reports)


async def get_scoped_listing(
    listing_id: int,
    moderator: User,
    db: AsyncSession,
) -> tuple[Listing, User]:
    q = (
        select(Listing, User)
        .join(User, User.id == Listing.user_id)
        .options(selectinload(Listing.images))
        .where(Listing.id == listing_id)
    )
    if moderator.role == "ambassador":
        q = q.where(Listing.building_id == moderator.building_id)

    row = (await db.execute(q)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    return row


@router.get("/moderation/reports", response_model=ModerationQueueOut)
async def list_open_reports(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    moderator = ensure_moderator_access(user)

    reporter_count_subquery = (
        select(
            ListingReport.listing_id,
            func.count(ListingReport.id).label("reporter_count"),
        )
        .where(ListingReport.status != REPORT_STATUS_DISMISSED)
        .group_by(ListingReport.listing_id)
        .subquery()
    )

    q = (
        select(ListingReport, Listing, User, reporter_count_subquery.c.reporter_count)
        .join(Listing, Listing.id == ListingReport.listing_id)
        .join(User, User.id == Listing.user_id)
        .options(selectinload(Listing.images))
        .join(
            reporter_count_subquery,
            reporter_count_subquery.c.listing_id == ListingReport.listing_id,
        )
        .where(ListingReport.status.in_((REPORT_STATUS_OPEN, REPORT_STATUS_ACTIONED)))
        .order_by(ListingReport.created_at.desc())
    )
    if moderator.role == "ambassador":
        q = q.where(ListingReport.building_id == moderator.building_id)

    rows = (await db.execute(q)).all()
    reports = [
        ModerationReportOut(
            id=report.id,
            listing_id=report.listing_id,
            building_id=report.building_id,
            reason=report.reason,
            details=report.details,
            status=report.status,
            created_at=report.created_at.isoformat() if report.created_at else None,
            reporter_count=reporter_count,
            action_taken=report.action_taken,
            available_actions=build_available_actions(listing),
            listing=serialize_listing(listing, seller),
            seller={
                "id": str(seller.id),
                "public_alias": seller.public_alias or seller.display_name,
                "display_name": seller.display_name,
                "real_name": seller.full_name,
                "room_number": seller.room_number_private,
                "profile_picture_url": seller.profile_picture_url,
                "email": seller.email,
            },
        )
        for report, listing, seller, reporter_count in rows
    ]

    return ModerationQueueOut(count=len(reports), reports=reports)


@router.post("/moderation/reports/{report_id}/dismiss", response_model=ModerationActionOut)
async def dismiss_report(
    report_id: int,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    moderator = ensure_moderator_access(user)
    report, listing = await get_scoped_report(report_id, moderator, db)
    if report.status != REPORT_STATUS_OPEN:
        raise HTTPException(status_code=409, detail="Report is already reviewed")

    updated_reports = await close_open_reports_for_listing(
        listing.id,
        moderator,
        db,
        REPORT_STATUS_DISMISSED,
        "dismissed",
    )
    await db.commit()

    return ModerationActionOut(
        success=True,
        action="dismiss",
        listing_id=listing.id,
        updated_reports=updated_reports,
        listing_status=listing.status,
    )


@router.post("/moderation/reports/{report_id}/hide", response_model=ModerationActionOut)
async def hide_listing_from_report(
    report_id: int,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    moderator = ensure_moderator_access(user)
    report, listing = await get_scoped_report(report_id, moderator, db)
    if report.status != REPORT_STATUS_OPEN:
        raise HTTPException(status_code=409, detail="Report is already reviewed")

    if listing.status == LISTING_STATUS_ACTIVE:
        listing.status = LISTING_STATUS_HIDDEN

    updated_reports = await close_open_reports_for_listing(
        listing.id,
        moderator,
        db,
        REPORT_STATUS_ACTIONED,
        "hidden",
    )
    await db.commit()

    return ModerationActionOut(
        success=True,
        action="hide",
        listing_id=listing.id,
        updated_reports=updated_reports,
        listing_status=listing.status,
    )


@router.get("/admin/reports", response_model=ModerationQueueOut)
async def list_admin_reports(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_open_reports(user=user, db=db)


@router.patch("/admin/reports/{report_id}", response_model=ModerationActionOut)
async def review_admin_report(
    report_id: int,
    payload: ReportReviewIn,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    action = payload.action.strip().lower()
    if action == "dismiss":
        return await dismiss_report(report_id=report_id, user=user, db=db)
    if action == "hide":
        return await hide_listing_from_report(report_id=report_id, user=user, db=db)
    raise HTTPException(status_code=422, detail="Unsupported report action")


@router.get("/moderation/listings")
async def list_moderation_listings(
    building_id: int | None = None,
    status_filter: str | None = None,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    moderator = ensure_moderator_access(user)

    q = (
        select(Listing, User)
        .join(User, User.id == Listing.user_id)
        .options(selectinload(Listing.images))
        .order_by(Listing.created_at.desc())
    )
    if moderator.role == "ambassador":
        q = q.where(Listing.building_id == moderator.building_id)
    elif building_id is not None:
        q = q.where(Listing.building_id == building_id)

    if status_filter:
        q = q.where(Listing.status == status_filter)

    rows = (await db.execute(q)).all()
    return {
        "count": len(rows),
        "listings": [serialize_listing(listing, seller) for listing, seller in rows],
    }


@router.post("/moderation/listings/{listing_id}/hide", response_model=ModerationActionOut)
async def hide_listing_direct(
    listing_id: int,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    moderator = ensure_moderator_access(user)
    listing, _seller = await get_scoped_listing(listing_id, moderator, db)
    listing.status = LISTING_STATUS_HIDDEN
    await db.commit()
    return ModerationActionOut(
        success=True,
        action="hide",
        listing_id=listing.id,
        updated_reports=0,
        listing_status=listing.status,
    )


@router.post("/moderation/listings/{listing_id}/unhide", response_model=ModerationActionOut)
async def unhide_listing(
    listing_id: int,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    moderator = ensure_moderator_access(user)
    listing, _seller = await get_scoped_listing(listing_id, moderator, db)
    listing.status = LISTING_STATUS_ACTIVE
    await db.commit()
    return ModerationActionOut(
        success=True,
        action="unhide",
        listing_id=listing.id,
        updated_reports=0,
        listing_status=listing.status,
    )


@router.post("/moderation/listings/{listing_id}/approve", response_model=ModerationActionOut)
async def approve_listing(
    listing_id: int,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    moderator = ensure_moderator_access(user)
    listing, _seller = await get_scoped_listing(listing_id, moderator, db)
    listing.status = LISTING_STATUS_ACTIVE
    await db.commit()
    return ModerationActionOut(
        success=True,
        action="approve",
        listing_id=listing.id,
        updated_reports=0,
        listing_status=listing.status,
    )
