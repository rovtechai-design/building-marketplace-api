import logging
from time import perf_counter

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_db
from app.core.security import delete_firebase_user
from app.models.building import Building, BuildingMembership
from app.models.listing import Listing
from app.models.listing_image import ListingImage
from app.models.listing_report import ListingReport
from app.models.user import User
from app.schemas.user import MeOut, MeUpdateIn
from app.services.storage import delete_storage_objects_by_urls, upload_profile_image

logger = logging.getLogger(__name__)
router = APIRouter()


class DeleteMeOut(BaseModel):
    deleted: bool
    user_id: str
    firebase_auth_deleted: bool
    storage_objects_deleted: int
    sign_out_required: bool


def normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def get_locked_real_name(user: User) -> str | None:
    return normalize_optional_text(user.real_name) or normalize_optional_text(user.full_name)


def get_locked_room_number(user: User) -> str | None:
    return normalize_optional_text(user.room_number) or normalize_optional_text(user.room_number_private)


def apply_locked_profile_field(user: User, *, field_name: str, value: str | None) -> None:
    normalized_value = normalize_optional_text(value)

    if field_name == "real_name":
        current_value = get_locked_real_name(user)
        if current_value and normalized_value != current_value:
            raise HTTPException(status_code=400, detail="real_name cannot be changed once set")
        user.real_name = normalized_value
        user.full_name = normalized_value
        return

    if field_name == "room_number":
        current_value = get_locked_room_number(user)
        if current_value and normalized_value != current_value:
            raise HTTPException(status_code=400, detail="room_number cannot be changed once set")
        user.room_number = normalized_value
        user.room_number_private = normalized_value
        return

    if field_name == "profile_picture_url":
        current_value = normalize_optional_text(user.profile_picture_url)
        if current_value and normalized_value != current_value:
            raise HTTPException(status_code=400, detail="profile_picture_url cannot be changed once set")
        user.profile_picture_url = normalized_value
        return

    raise ValueError(f"Unsupported locked profile field: {field_name}")


def serialize_me(user: User) -> MeOut:
    public_alias = user.public_alias or user.display_name
    real_name = get_locked_real_name(user)
    room_number = get_locked_room_number(user)
    is_profile_complete = user.is_profile_complete
    return MeOut(
        id=str(user.id),
        firebase_uid=user.firebase_uid,
        email=user.email,
        display_name=user.display_name,
        public_alias=public_alias,
        real_name=real_name,
        full_name=real_name,
        building_id=user.building_id,
        room_number=room_number,
        room_number_private=room_number,
        profile_picture_url=user.profile_picture_url,
        role=user.role,
        is_profile_complete=is_profile_complete,
        profile_completed=is_profile_complete,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


def compute_profile_completed(user: User) -> bool:
    return user.is_profile_complete


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


@router.post("/me/profile-picture", response_model=MeOut)
async def upload_me_profile_picture(
    file: UploadFile = File(...),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if normalize_optional_text(user.profile_picture_url):
        raise HTTPException(
            status_code=400,
            detail="profile_picture_url cannot be changed once set",
        )

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

    image_url = upload_profile_image(
        user_id=user.id,
        filename=file.filename,
        content_type=file.content_type,
        content=file_bytes,
    )
    apply_locked_profile_field(user, field_name="profile_picture_url", value=image_url)
    user.profile_completed = compute_profile_completed(user)
    await db.commit()
    await db.refresh(user)
    return serialize_me(user)


@router.delete("/me", response_model=DeleteMeOut)
async def delete_me(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    logger.info("me.delete requested user_id=%s firebase_uid=%s", user.id, user.firebase_uid)

    owned_listings_result = await db.execute(
        select(Listing)
        .options(selectinload(Listing.images))
        .where(Listing.user_id == user.id)
    )
    owned_listings = owned_listings_result.scalars().all()
    owned_listing_ids = [listing.id for listing in owned_listings]

    storage_urls = []
    if user.profile_picture_url:
        storage_urls.append(user.profile_picture_url)
    for listing in owned_listings:
        storage_urls.extend(image.image_url for image in listing.images)

    delete_firebase_user(user.firebase_uid)

    try:
        if owned_listing_ids:
            await db.execute(delete(ListingImage).where(ListingImage.listing_id.in_(owned_listing_ids)))
            await db.execute(delete(ListingReport).where(ListingReport.listing_id.in_(owned_listing_ids)))

        await db.execute(
            delete(ListingReport).where(
                or_(
                    ListingReport.reporter_user_id == user.id,
                    ListingReport.reported_user_id == user.id,
                )
            )
        )
        await db.execute(
            update(ListingReport)
            .where(ListingReport.reviewed_by_user_id == user.id)
            .values(reviewed_by_user_id=None)
        )
        await db.execute(delete(BuildingMembership).where(BuildingMembership.user_id == user.id))
        await db.execute(
            update(Listing)
            .where(Listing.buyer_user_id == user.id, Listing.status == "in_progress")
            .values(
                buyer_user_id=None,
                status="active",
                reserved_at=None,
                sold_at=None,
                transaction_pin=None,
            )
        )
        await db.execute(
            update(Listing)
            .where(Listing.buyer_user_id == user.id, Listing.status != "in_progress")
            .values(buyer_user_id=None)
        )
        if owned_listing_ids:
            await db.execute(delete(Listing).where(Listing.id.in_(owned_listing_ids)))
        await db.delete(user)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.exception("me.delete database cleanup failed user_id=%s", user.id)
        raise HTTPException(
            status_code=500,
            detail=f"Account deletion failed: {type(exc).__name__}",
        ) from exc

    storage_objects_deleted = delete_storage_objects_by_urls(storage_urls)
    logger.info(
        "me.delete completed user_id=%s listings_deleted=%s storage_objects_deleted=%s",
        user.id,
        len(owned_listing_ids),
        storage_objects_deleted,
    )
    return DeleteMeOut(
        deleted=True,
        user_id=str(user.id),
        firebase_auth_deleted=True,
        storage_objects_deleted=storage_objects_deleted,
        sign_out_required=True,
    )


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
        apply_locked_profile_field(user, field_name="real_name", value=updates["real_name"])

    if "full_name" in updates:
        apply_locked_profile_field(user, field_name="real_name", value=updates["full_name"])

    if "room_number" in updates:
        apply_locked_profile_field(user, field_name="room_number", value=updates["room_number"])

    if "room_number_private" in updates:
        apply_locked_profile_field(user, field_name="room_number", value=updates["room_number_private"])

    if "profile_picture_url" in updates:
        apply_locked_profile_field(user, field_name="profile_picture_url", value=updates["profile_picture_url"])

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
                raise HTTPException(
                    status_code=403,
                    detail="You are not a member of this building",
                )

            user.building_id = building_id

    user.profile_completed = compute_profile_completed(user)
    await db.commit()
    await db.refresh(user)
    return serialize_me(user)
