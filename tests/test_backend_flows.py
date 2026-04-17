from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import or_, select

from app.api.routes import me as me_routes
from app.models.building import Building, BuildingMembership
from app.models.listing import Listing
from app.models.listing_image import ListingImage
from app.models.listing_report import ListingReport
from app.models.user import User


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def stub_profile_picture_upload(monkeypatch, url: str = "https://cdn.example.com/profile.jpg") -> None:
    monkeypatch.setattr(me_routes, "upload_profile_image", lambda **kwargs: url)


def stub_account_deletion_cleanup(monkeypatch) -> None:
    monkeypatch.setattr(me_routes, "delete_storage_objects_by_urls", lambda urls: len(urls))
    monkeypatch.setattr(me_routes, "delete_firebase_user", lambda firebase_uid: None)


async def create_building(
    db_sessionmaker,
    *,
    name: str,
    invite_code: str,
    vouchers_enabled: bool = False,
) -> Building:
    async with db_sessionmaker() as session:
        building = Building(name=name, invite_code=invite_code, vouchers_enabled=vouchers_enabled)
        session.add(building)
        await session.commit()
        await session.refresh(building)
        return building


async def get_user_by_email(db_sessionmaker, email: str) -> User:
    async with db_sessionmaker() as session:
        result = await session.execute(select(User).where(User.email == email))
        return result.scalar_one()


async def set_user_role(db_sessionmaker, email: str, *, role: str, building_id: int | None = None) -> User:
    async with db_sessionmaker() as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one()
        user.role = role
        user.building_id = building_id
        await session.commit()
        await session.refresh(user)
        return user


async def count_reports(db_sessionmaker, listing_id: int) -> int:
    async with db_sessionmaker() as session:
        result = await session.execute(select(ListingReport).where(ListingReport.listing_id == listing_id))
        return len(result.scalars().all())


@pytest.mark.asyncio
async def test_flow_1_user_onboarding(client, db_sessionmaker, token_claims, monkeypatch):
    building = await create_building(
        db_sessionmaker,
        name="Alpha House",
        invite_code="ALPHA123",
        vouchers_enabled=True,
    )
    token_claims["user1"] = {
        "uid": "uid-user1",
        "email": "user1@example.com",
        "name": "Tomato",
    }

    me_response = await client.get("/me", headers=auth_headers("user1"))
    assert me_response.status_code == 200
    assert me_response.json()["email"] == "user1@example.com"
    assert me_response.json()["display_name"] == "Tomato"
    assert me_response.json()["real_name"] is None
    assert me_response.json()["room_number"] is None
    assert me_response.json()["profile_picture_url"] is None
    assert me_response.json()["is_profile_complete"] is False
    assert me_response.json()["profile_completed"] is False

    join_response = await client.post(
        "/join-building",
        json={"invite_code": building.invite_code},
        headers=auth_headers("user1"),
    )
    assert join_response.status_code == 200
    assert join_response.json()["joined"] is True
    assert join_response.json()["building"]["vouchers_enabled"] is True

    update_response = await client.patch(
        "/me",
        json={
            "display_name": "Tomato",
            "real_name": "User One",
            "building_id": building.id,
            "room_number": "12B",
        },
        headers=auth_headers("user1"),
    )
    assert update_response.status_code == 200
    payload = update_response.json()
    assert payload["real_name"] == "User One"
    assert payload["room_number"] == "12B"
    assert payload["profile_picture_url"] is None
    assert payload["is_profile_complete"] is False
    assert payload["profile_completed"] is False
    assert payload["building_id"] == building.id

    stub_profile_picture_upload(monkeypatch, "https://cdn.example.com/user1.jpg")
    picture_response = await client.post(
        "/me/profile-picture",
        files={"file": ("user1.jpg", b"fake-image-bytes", "image/jpeg")},
        headers=auth_headers("user1"),
    )
    assert picture_response.status_code == 200
    picture_payload = picture_response.json()
    assert picture_payload["profile_picture_url"] == "https://cdn.example.com/user1.jpg"
    assert picture_payload["is_profile_complete"] is True
    assert picture_payload["profile_completed"] is True
    assert picture_payload["building_id"] == building.id

    me_after_upload_response = await client.get("/me", headers=auth_headers("user1"))
    assert me_after_upload_response.status_code == 200
    assert me_after_upload_response.json()["profile_picture_url"] == "https://cdn.example.com/user1.jpg"
    assert me_after_upload_response.json()["is_profile_complete"] is True

    my_buildings_response = await client.get("/my-buildings", headers=auth_headers("user1"))
    assert my_buildings_response.status_code == 200
    assert my_buildings_response.json()["count"] == 1
    assert my_buildings_response.json()["buildings"][0]["vouchers_enabled"] is True


@pytest.mark.asyncio
async def test_flow_1b_profile_identity_fields_lock_after_first_save(client, db_sessionmaker, token_claims, monkeypatch):
    building = await create_building(db_sessionmaker, name="Alpha House Lock", invite_code="ALPHALOCK")
    token_claims["user_lock"] = {
        "uid": "uid-user-lock",
        "email": "userlock@example.com",
        "name": "Lock User",
    }

    await client.get("/me", headers=auth_headers("user_lock"))
    await client.post(
        "/join-building",
        json={"invite_code": building.invite_code},
        headers=auth_headers("user_lock"),
    )

    first_save_response = await client.patch(
        "/me",
        json={
            "real_name": "Locked User",
            "room_number": "7B",
        },
        headers=auth_headers("user_lock"),
    )
    assert first_save_response.status_code == 200
    assert first_save_response.json()["is_profile_complete"] is False

    stub_profile_picture_upload(monkeypatch, "https://cdn.example.com/lock.jpg")
    upload_response = await client.post(
        "/me/profile-picture",
        files={"file": ("lock.jpg", b"fake-image-bytes", "image/jpeg")},
        headers=auth_headers("user_lock"),
    )
    assert upload_response.status_code == 200
    assert upload_response.json()["is_profile_complete"] is True

    locked_attempts = [
        ({"real_name": "Changed User"}, "real_name cannot be changed once set"),
        ({"full_name": "Changed User"}, "real_name cannot be changed once set"),
        ({"room_number": "8C"}, "room_number cannot be changed once set"),
        ({"room_number_private": "8C"}, "room_number cannot be changed once set"),
    ]

    for payload, expected_detail in locked_attempts:
        response = await client.patch("/me", json=payload, headers=auth_headers("user_lock"))
        assert response.status_code == 400
        assert response.json()["detail"] == expected_detail

    second_upload_response = await client.post(
        "/me/profile-picture",
        files={"file": ("changed.jpg", b"fake-image-bytes", "image/jpeg")},
        headers=auth_headers("user_lock"),
    )
    assert second_upload_response.status_code == 400
    assert second_upload_response.json()["detail"] == "profile_picture_url cannot be changed once set"

    my_buildings_response = await client.get("/my-buildings", headers=auth_headers("user_lock"))
    assert my_buildings_response.status_code == 200
    assert my_buildings_response.json()["count"] == 1


@pytest.mark.asyncio
async def test_flow_1bb_patch_me_cannot_join_building_without_invite_code(client, db_sessionmaker, token_claims):
    joined_building = await create_building(db_sessionmaker, name="Joined House", invite_code="JOINED123")
    unjoined_building = await create_building(db_sessionmaker, name="Locked House", invite_code="LOCKED123")
    token_claims["user_building_guard"] = {
        "uid": "uid-user-building-guard",
        "email": "buildingguard@example.com",
        "name": "Building Guard",
    }

    await client.get("/me", headers=auth_headers("user_building_guard"))
    join_response = await client.post(
        "/join-building",
        json={"invite_code": joined_building.invite_code},
        headers=auth_headers("user_building_guard"),
    )
    assert join_response.status_code == 200
    assert join_response.json()["joined"] is True

    allowed_update_response = await client.patch(
        "/me",
        json={
            "display_name": "Building Guard Updated",
            "building_id": joined_building.id,
        },
        headers=auth_headers("user_building_guard"),
    )
    assert allowed_update_response.status_code == 200
    allowed_payload = allowed_update_response.json()
    assert allowed_payload["display_name"] == "Building Guard Updated"
    assert allowed_payload["building_id"] == joined_building.id

    blocked_update_response = await client.patch(
        "/me",
        json={"building_id": unjoined_building.id},
        headers=auth_headers("user_building_guard"),
    )
    assert blocked_update_response.status_code == 403
    assert blocked_update_response.json()["detail"] == "You are not a member of this building"

    user = await get_user_by_email(db_sessionmaker, "buildingguard@example.com")
    async with db_sessionmaker() as session:
        memberships = await session.execute(
            select(BuildingMembership).where(BuildingMembership.user_id == user.id)
        )
        membership_building_ids = sorted(membership.building_id for membership in memberships.scalars().all())
        assert membership_building_ids == [joined_building.id]

        refreshed_user = await session.get(User, user.id)
        assert refreshed_user is not None
        assert refreshed_user.building_id == joined_building.id


@pytest.mark.asyncio
async def test_flow_1c_profile_picture_upload_rejects_invalid_file_type(client, token_claims):
    token_claims["user_upload_invalid"] = {
        "uid": "uid-user-upload-invalid",
        "email": "useruploadinvalid@example.com",
        "name": "Upload Invalid",
    }

    await client.get("/me", headers=auth_headers("user_upload_invalid"))

    response = await client.post(
        "/me/profile-picture",
        files={"file": ("profile.txt", b"not-an-image", "text/plain")},
        headers=auth_headers("user_upload_invalid"),
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "file must be an image"


@pytest.mark.asyncio
async def test_flow_1d_delete_account_removes_owned_data_and_preserves_unrelated_users(
    client,
    db_sessionmaker,
    token_claims,
    monkeypatch,
):
    building = await create_building(db_sessionmaker, name="Delete House", invite_code="DELETE123")
    users = [
        ("deletee", "deletee@example.com", "Deletee", "Deletee User", "11A"),
        ("other", "other@example.com", "Other", "Other User", "22B"),
    ]

    for token, email, alias, full_name, room in users:
        token_claims[token] = {"uid": f"uid-{token}", "email": email, "name": alias}
        await client.get("/me", headers=auth_headers(token))
        await client.post("/join-building", json={"invite_code": building.invite_code}, headers=auth_headers(token))
        await client.patch(
            "/me",
            json={
                "display_name": alias,
                "full_name": full_name,
                "building_id": building.id,
                "room_number_private": room,
            },
            headers=auth_headers(token),
        )

    deletee_listing_response = await client.post(
        "/listings",
        json={"building_id": building.id, "title": "Deletee Lamp", "description": "Owned by deletee", "price": 15},
        headers=auth_headers("deletee"),
    )
    deletee_listing_id = deletee_listing_response.json()["id"]

    other_listing_response = await client.post(
        "/listings",
        json={"building_id": building.id, "title": "Other Chair", "description": "Owned by other", "price": 25},
        headers=auth_headers("other"),
    )
    other_listing_id = other_listing_response.json()["id"]

    await client.post(
        f"/listings/{deletee_listing_id}/report",
        json={"reason": "other", "details": "report on deletee listing"},
        headers=auth_headers("other"),
    )
    await client.post(
        f"/listings/{other_listing_id}/report",
        json={"reason": "other", "details": "deletee reported other listing"},
        headers=auth_headers("deletee"),
    )
    buy_response = await client.post(f"/listings/{other_listing_id}/buy", headers=auth_headers("deletee"))
    assert buy_response.status_code == 200

    deletee_user = await get_user_by_email(db_sessionmaker, "deletee@example.com")
    async with db_sessionmaker() as session:
        deletee_user_in_db = await session.get(User, deletee_user.id)
        deletee_user_in_db.profile_picture_url = "https://cdn.example.com/profiles/deletee.jpg"
        session.add(
            ListingImage(
                listing_id=deletee_listing_id,
                image_url="https://cdn.example.com/listings/deletee-lamp.jpg",
            )
        )
        await session.commit()

    stub_account_deletion_cleanup(monkeypatch)
    delete_response = await client.delete("/me", headers=auth_headers("deletee"))
    assert delete_response.status_code == 200
    payload = delete_response.json()
    assert payload["deleted"] is True
    assert payload["firebase_auth_deleted"] is True
    assert payload["storage_objects_deleted"] == 2
    assert payload["sign_out_required"] is True

    async with db_sessionmaker() as session:
        deleted_user = await session.get(User, deletee_user.id)
        assert deleted_user is None

        deletee_listing = await session.get(Listing, deletee_listing_id)
        assert deletee_listing is None

        listing_image_rows = await session.execute(
            select(ListingImage).where(ListingImage.listing_id == deletee_listing_id)
        )
        assert listing_image_rows.scalars().all() == []

        memberships = await session.execute(
            select(BuildingMembership).where(BuildingMembership.user_id == deletee_user.id)
        )
        assert memberships.scalars().all() == []

        related_reports = await session.execute(
            select(ListingReport).where(
                or_(
                    ListingReport.reporter_user_id == deletee_user.id,
                    ListingReport.reported_user_id == deletee_user.id,
                    ListingReport.listing_id == deletee_listing_id,
                )
            )
        )
        assert related_reports.scalars().all() == []

        surviving_listing = await session.get(Listing, other_listing_id)
        assert surviving_listing is not None
        assert surviving_listing.buyer_user_id is None
        assert surviving_listing.status == "active"

        other_user = await get_user_by_email(db_sessionmaker, "other@example.com")
        assert other_user is not None


@pytest.mark.asyncio
async def test_flow_2_listing_creation_visible_in_listings(client, db_sessionmaker, token_claims):
    building = await create_building(db_sessionmaker, name="Beta House", invite_code="BETA123")
    token_claims["seller"] = {
        "uid": "uid-seller",
        "email": "seller@example.com",
        "name": "Tomato",
    }

    await client.get("/me", headers=auth_headers("seller"))
    await client.post("/join-building", json={"invite_code": building.invite_code}, headers=auth_headers("seller"))
    await client.patch(
        "/me",
        json={
            "display_name": "Tomato",
            "full_name": "Seller Name",
            "building_id": building.id,
            "room_number_private": "9A",
        },
        headers=auth_headers("seller"),
    )

    create_listing_response = await client.post(
        "/listings",
        json={
            "building_id": building.id,
            "title": "Desk Lamp",
            "description": "Bright lamp",
            "price": 15.5,
        },
        headers=auth_headers("seller"),
    )
    assert create_listing_response.status_code == 200

    listings_response = await client.get(
        "/listings",
        params={"building_id": building.id},
        headers=auth_headers("seller"),
    )
    assert listings_response.status_code == 200
    payload = listings_response.json()
    assert payload["count"] == 1
    listing_payload = payload["listings"][0]
    assert listing_payload["title"] == "Desk Lamp"
    assert listing_payload["seller_display_name"] == "Tomato"
    assert "room_number_private" not in listing_payload


@pytest.mark.asyncio
async def test_flow_3_report_system_prevents_duplicate_reports(client, db_sessionmaker, token_claims):
    building = await create_building(db_sessionmaker, name="Gamma House", invite_code="GAMMA123")
    token_claims["seller"] = {"uid": "uid-seller", "email": "seller@example.com", "name": "Tomato"}
    token_claims["reporter"] = {"uid": "uid-reporter", "email": "reporter@example.com", "name": "Carrot"}

    for token, full_name, room in [("seller", "Seller", "1A"), ("reporter", "Reporter", "2A")]:
        await client.get("/me", headers=auth_headers(token))
        await client.post("/join-building", json={"invite_code": building.invite_code}, headers=auth_headers(token))
        await client.patch(
            "/me",
            json={
                "display_name": token_claims[token]["name"],
                "full_name": full_name,
                "building_id": building.id,
                "room_number_private": room,
            },
            headers=auth_headers(token),
        )

    create_listing_response = await client.post(
        "/listings",
        json={"building_id": building.id, "title": "Bike", "description": "Blue bike", "price": 100},
        headers=auth_headers("seller"),
    )
    listing_id = create_listing_response.json()["id"]

    report_response = await client.post(
        f"/listings/{listing_id}/report",
        json={"reason": "scam_misleading", "details": "Looks suspicious"},
        headers=auth_headers("reporter"),
    )
    assert report_response.status_code == 200
    assert report_response.json()["success"] is True
    assert await count_reports(db_sessionmaker, listing_id) == 1

    duplicate_response = await client.post(
        f"/listings/{listing_id}/report",
        json={"reason": "scam_misleading", "details": "Still suspicious"},
        headers=auth_headers("reporter"),
    )
    assert duplicate_response.status_code == 409
    assert await count_reports(db_sessionmaker, listing_id) == 1


@pytest.mark.asyncio
async def test_flow_4_auto_hide_after_three_reports(client, db_sessionmaker, token_claims):
    building = await create_building(db_sessionmaker, name="Delta House", invite_code="DELTA123")
    users = [
        ("seller", "seller@example.com", "Tomato", "Seller", "1A"),
        ("r1", "r1@example.com", "Carrot", "Reporter One", "2A"),
        ("r2", "r2@example.com", "Bean", "Reporter Two", "3A"),
        ("r3", "r3@example.com", "Onion", "Reporter Three", "4A"),
    ]

    for token, email, alias, full_name, room in users:
        token_claims[token] = {"uid": f"uid-{token}", "email": email, "name": alias}
        await client.get("/me", headers=auth_headers(token))
        await client.post("/join-building", json={"invite_code": building.invite_code}, headers=auth_headers(token))
        await client.patch(
            "/me",
            json={
                "display_name": alias,
                "full_name": full_name,
                "building_id": building.id,
                "room_number_private": room,
            },
            headers=auth_headers(token),
        )

    create_listing_response = await client.post(
        "/listings",
        json={"building_id": building.id, "title": "Chair", "description": "Office chair", "price": 20},
        headers=auth_headers("seller"),
    )
    listing_id = create_listing_response.json()["id"]

    for token in ["r1", "r2", "r3"]:
        response = await client.post(
            f"/listings/{listing_id}/report",
            json={"reason": "other", "details": f"report from {token}"},
            headers=auth_headers(token),
        )
        assert response.status_code == 200

    async with db_sessionmaker() as session:
        result = await session.execute(select(Listing).where(Listing.id == listing_id))
        listing = result.scalar_one()
        assert listing.status == "hidden"

    listings_response = await client.get(
        "/listings",
        params={"building_id": building.id},
        headers=auth_headers("seller"),
    )
    assert listings_response.status_code == 200
    assert listings_response.json()["count"] == 0


@pytest.mark.asyncio
async def test_flow_5_moderator_actions(client, db_sessionmaker, token_claims):
    building = await create_building(db_sessionmaker, name="Echo House", invite_code="ECHO123")
    users = [
        ("seller", "seller@example.com", "Tomato", "Seller", "1A"),
        ("reporter", "reporter@example.com", "Carrot", "Reporter", "2A"),
        ("moderator", "mod@example.com", "Pepper", "Moderator", "3A"),
    ]

    for token, email, alias, full_name, room in users:
        token_claims[token] = {"uid": f"uid-{token}", "email": email, "name": alias}
        await client.get("/me", headers=auth_headers(token))
        await client.post("/join-building", json={"invite_code": building.invite_code}, headers=auth_headers(token))
        await client.patch(
            "/me",
            json={
                "display_name": alias,
                "full_name": full_name,
                "building_id": building.id,
                "room_number_private": room,
            },
            headers=auth_headers(token),
        )

    await set_user_role(db_sessionmaker, "mod@example.com", role="ambassador", building_id=building.id)

    create_listing_response = await client.post(
        "/listings",
        json={"building_id": building.id, "title": "Table", "description": "Dining table", "price": 40},
        headers=auth_headers("seller"),
    )
    listing_id = create_listing_response.json()["id"]

    report_response = await client.post(
        f"/listings/{listing_id}/report",
        json={"reason": "harassment", "details": "Bad content"},
        headers=auth_headers("reporter"),
    )
    report_id = report_response.json()["report_id"]

    queue_response = await client.get("/moderation/reports", headers=auth_headers("moderator"))
    assert queue_response.status_code == 200
    assert queue_response.json()["count"] == 1

    hide_response = await client.post(
        f"/moderation/reports/{report_id}/hide",
        headers=auth_headers("moderator"),
    )
    assert hide_response.status_code == 200
    assert hide_response.json()["listing_status"] == "hidden"

    queue_after_hide = await client.get("/moderation/reports", headers=auth_headers("moderator"))
    assert queue_after_hide.status_code == 200
    assert queue_after_hide.json()["count"] == 1
    moderated_report = queue_after_hide.json()["reports"][0]
    assert moderated_report["listing"]["status"] == "hidden"
    assert moderated_report["listing"]["title"] == "Table"
    assert moderated_report["listing"]["seller_display_name"] == "Tomato"
    assert moderated_report["available_actions"] == ["unhide"]

    public_listings_hidden = await client.get(
        "/listings",
        params={"building_id": building.id},
        headers=auth_headers("seller"),
    )
    assert public_listings_hidden.status_code == 200
    assert public_listings_hidden.json()["count"] == 0

    moderation_listings = await client.get("/moderation/listings", headers=auth_headers("moderator"))
    assert moderation_listings.status_code == 200
    assert moderation_listings.json()["count"] == 1

    unhide_response = await client.post(
        f"/moderation/listings/{listing_id}/unhide",
        headers=auth_headers("moderator"),
    )
    assert unhide_response.status_code == 200
    assert unhide_response.json()["listing_status"] == "active"

    public_listings_visible = await client.get(
        "/listings",
        params={"building_id": building.id},
        headers=auth_headers("seller"),
    )
    assert public_listings_visible.status_code == 200
    assert public_listings_visible.json()["count"] == 1


@pytest.mark.asyncio
async def test_flow_6_admin_override_for_kevin_account(client, db_sessionmaker, token_claims):
    building = await create_building(db_sessionmaker, name="Foxtrot House", invite_code="FOX123")
    token_claims["seller"] = {"uid": "uid-seller", "email": "seller@example.com", "name": "Tomato"}
    token_claims["reporter"] = {"uid": "uid-reporter", "email": "reporter@example.com", "name": "Carrot"}
    token_claims["kevin"] = {"uid": "uid-kevin", "email": "kevinlukeuwu@gmail.com", "name": "Kevin"}

    for token, full_name, room in [("seller", "Seller", "1A"), ("reporter", "Reporter", "2A")]:
        await client.get("/me", headers=auth_headers(token))
        await client.post("/join-building", json={"invite_code": building.invite_code}, headers=auth_headers(token))
        await client.patch(
            "/me",
            json={
                "display_name": token_claims[token]["name"],
                "full_name": full_name,
                "building_id": building.id,
                "room_number_private": room,
            },
            headers=auth_headers(token),
        )

    create_listing_response = await client.post(
        "/listings",
        json={"building_id": building.id, "title": "Mirror", "description": "Wall mirror", "price": 25},
        headers=auth_headers("seller"),
    )
    listing_id = create_listing_response.json()["id"]

    await client.post(
        f"/listings/{listing_id}/report",
        json={"reason": "other", "details": "Needs admin review"},
        headers=auth_headers("reporter"),
    )

    me_response = await client.get("/me", headers=auth_headers("kevin"))
    assert me_response.status_code == 200
    assert me_response.json()["role"] == "admin"

    moderation_response = await client.get("/moderation/reports", headers=auth_headers("kevin"))
    assert moderation_response.status_code == 200
    assert moderation_response.json()["count"] == 1


@pytest.mark.asyncio
async def test_flow_7_buy_listing_reserves_listing_and_generates_buyer_pin(client, db_sessionmaker, token_claims):
    building = await create_building(db_sessionmaker, name="Golf House", invite_code="GOLF123")
    users = [
        ("seller", "seller@example.com", "Tomato", "Seller", "1A"),
        ("buyer", "buyer@example.com", "Carrot", "Buyer", "2A"),
        ("viewer", "viewer@example.com", "Bean", "Viewer", "3A"),
    ]

    for token, email, alias, full_name, room in users:
        token_claims[token] = {"uid": f"uid-{token}", "email": email, "name": alias}
        await client.get("/me", headers=auth_headers(token))
        await client.post("/join-building", json={"invite_code": building.invite_code}, headers=auth_headers(token))
        await client.patch(
            "/me",
            json={
                "display_name": alias,
                "full_name": full_name,
                "building_id": building.id,
                "room_number_private": room,
            },
            headers=auth_headers(token),
        )

    create_listing_response = await client.post(
        "/listings",
        json={"building_id": building.id, "title": "Monitor", "description": "27 inch", "price": 90},
        headers=auth_headers("seller"),
    )
    listing_id = create_listing_response.json()["id"]

    buy_response = await client.post(f"/listings/{listing_id}/buy", headers=auth_headers("buyer"))
    assert buy_response.status_code == 200
    payload = buy_response.json()
    assert payload["success"] is True
    assert payload["listing_id"] == listing_id
    assert payload["status"] == "in_progress"
    assert payload["buyer_user_id"] is not None
    assert payload["reserved_at"] is not None

    feed_response = await client.get(
        "/listings",
        params={"building_id": building.id},
        headers=auth_headers("viewer"),
    )
    assert feed_response.status_code == 200
    assert feed_response.json()["count"] == 0

    seller_listings_response = await client.get("/my-listings", headers=auth_headers("seller"))
    assert seller_listings_response.status_code == 200
    seller_listing = seller_listings_response.json()["listings"][0]
    assert seller_listing["status"] == "in_progress"
    assert seller_listing["buyer_user_id"] == payload["buyer_user_id"]
    assert seller_listing["buyer_display_name"] == "Carrot"
    assert seller_listing["reserved_at"] is not None
    assert seller_listing["sold_at"] is None
    assert "transaction_pin" not in seller_listing
    assert "buyer_pin" not in seller_listing

    buyer_orders_response = await client.get("/orders/me", headers=auth_headers("buyer"))
    assert buyer_orders_response.status_code == 200
    buyer_order = buyer_orders_response.json()["orders"][0]
    assert buyer_order["listing_id"] == listing_id
    assert buyer_order["status"] == "in_progress"
    assert buyer_order["buyer_pin"] is not None
    assert len(buyer_order["buyer_pin"]) == 4
    assert buyer_order["buyer_pin"].isdigit()
    assert buyer_order["has_buyer_pin"] is True
    assert buyer_order["reserved_at"] == payload["reserved_at"]
    assert buyer_order["sold_at"] is None

    async with db_sessionmaker() as session:
        result = await session.execute(select(Listing).where(Listing.id == listing_id))
        listing = result.scalar_one()
        assert listing.status == "in_progress"
        assert str(listing.buyer_user_id) == payload["buyer_user_id"]
        assert listing.reserved_at is not None
        assert listing.sold_at is None
        assert listing.transaction_pin == buyer_order["buyer_pin"]


@pytest.mark.asyncio
async def test_flow_8_buy_listing_rejects_invalid_purchase_states(client, db_sessionmaker, token_claims):
    building = await create_building(db_sessionmaker, name="Hotel House", invite_code="HOTEL123")
    token_claims["seller"] = {"uid": "uid-seller", "email": "seller@example.com", "name": "Tomato"}
    token_claims["buyer"] = {"uid": "uid-buyer", "email": "buyer@example.com", "name": "Carrot"}
    token_claims["outsider"] = {"uid": "uid-outsider", "email": "outsider@example.com", "name": "Bean"}

    for token, full_name, room in [("seller", "Seller", "1A"), ("buyer", "Buyer", "2A")]:
        await client.get("/me", headers=auth_headers(token))
        await client.post("/join-building", json={"invite_code": building.invite_code}, headers=auth_headers(token))
        await client.patch(
            "/me",
            json={
                "display_name": token_claims[token]["name"],
                "full_name": full_name,
                "building_id": building.id,
                "room_number_private": room,
            },
            headers=auth_headers(token),
        )

    await client.get("/me", headers=auth_headers("outsider"))

    create_listing_response = await client.post(
        "/listings",
        json={"building_id": building.id, "title": "Lamp", "description": "Desk lamp", "price": 15},
        headers=auth_headers("seller"),
    )
    listing_id = create_listing_response.json()["id"]

    hidden_listing_response = await client.post(
        "/listings",
        json={"building_id": building.id, "title": "Hidden Lamp", "description": "Hidden", "price": 16},
        headers=auth_headers("seller"),
    )
    hidden_listing_id = hidden_listing_response.json()["id"]

    deleted_listing_response = await client.post(
        "/listings",
        json={"building_id": building.id, "title": "Deleted Lamp", "description": "Deleted", "price": 17},
        headers=auth_headers("seller"),
    )
    deleted_listing_id = deleted_listing_response.json()["id"]

    expired_listing_response = await client.post(
        "/listings",
        json={"building_id": building.id, "title": "Expired Lamp", "description": "Expired", "price": 18},
        headers=auth_headers("seller"),
    )
    expired_listing_id = expired_listing_response.json()["id"]

    async with db_sessionmaker() as session:
        result = await session.execute(
            select(Listing).where(
                Listing.id.in_(
                    [
                        hidden_listing_id,
                        deleted_listing_id,
                        expired_listing_id,
                    ]
                )
            )
        )
        listings = {listing.id: listing for listing in result.scalars().all()}
        listings[hidden_listing_id].status = "hidden"
        listings[deleted_listing_id].status = "deleted"
        listings[expired_listing_id].expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await session.commit()

    own_listing_response = await client.post(f"/listings/{listing_id}/buy", headers=auth_headers("seller"))
    assert own_listing_response.status_code == 422

    outsider_response = await client.post(f"/listings/{listing_id}/buy", headers=auth_headers("outsider"))
    assert outsider_response.status_code == 403

    hidden_response = await client.post(f"/listings/{hidden_listing_id}/buy", headers=auth_headers("buyer"))
    assert hidden_response.status_code == 409

    deleted_response = await client.post(f"/listings/{deleted_listing_id}/buy", headers=auth_headers("buyer"))
    assert deleted_response.status_code == 409

    expired_response = await client.post(f"/listings/{expired_listing_id}/buy", headers=auth_headers("buyer"))
    assert expired_response.status_code == 409

    first_buy_response = await client.post(f"/listings/{listing_id}/buy", headers=auth_headers("buyer"))
    assert first_buy_response.status_code == 200
    assert first_buy_response.json()["status"] == "in_progress"

    duplicate_buy_response = await client.post(f"/listings/{listing_id}/buy", headers=auth_headers("buyer"))
    assert duplicate_buy_response.status_code == 409

    missing_auth_response = await client.post(f"/listings/{listing_id}/buy")
    assert missing_auth_response.status_code == 401


@pytest.mark.asyncio
async def test_flow_9_seller_confirms_pin_and_unrelated_users_cannot(client, db_sessionmaker, token_claims):
    building = await create_building(db_sessionmaker, name="India House", invite_code="INDIA123")
    users = [
        ("seller", "seller@example.com", "Tomato", "Seller", "1A"),
        ("buyer", "buyer@example.com", "Carrot", "Buyer", "2A"),
        ("other", "other@example.com", "Bean", "Other", "3A"),
    ]

    for token, email, alias, full_name, room in users:
        token_claims[token] = {"uid": f"uid-{token}", "email": email, "name": alias}
        await client.get("/me", headers=auth_headers(token))
        await client.post("/join-building", json={"invite_code": building.invite_code}, headers=auth_headers(token))
        await client.patch(
            "/me",
            json={
                "display_name": alias,
                "full_name": full_name,
                "building_id": building.id,
                "room_number_private": room,
            },
            headers=auth_headers(token),
        )

    create_listing_response = await client.post(
        "/listings",
        json={"building_id": building.id, "title": "Desk", "description": "Wood desk", "price": 40},
        headers=auth_headers("seller"),
    )
    listing_id = create_listing_response.json()["id"]

    await client.post(f"/listings/{listing_id}/buy", headers=auth_headers("buyer"))
    buyer_orders_response = await client.get("/orders/me", headers=auth_headers("buyer"))
    buyer_pin = buyer_orders_response.json()["orders"][0]["buyer_pin"]

    wrong_pin_response = await client.post(
        f"/listings/{listing_id}/confirm-pin",
        json={"pin": "0000" if buyer_pin != "0000" else "9999"},
        headers=auth_headers("seller"),
    )
    assert wrong_pin_response.status_code == 422

    unrelated_response = await client.post(
        f"/listings/{listing_id}/confirm-pin",
        json={"pin": buyer_pin},
        headers=auth_headers("other"),
    )
    assert unrelated_response.status_code == 403

    buyer_cannot_confirm_response = await client.post(
        f"/listings/{listing_id}/confirm-pin",
        json={"pin": buyer_pin},
        headers=auth_headers("buyer"),
    )
    assert buyer_cannot_confirm_response.status_code == 403

    confirm_response = await client.post(
        f"/listings/{listing_id}/confirm-pin",
        json={"pin": buyer_pin},
        headers=auth_headers("seller"),
    )
    assert confirm_response.status_code == 200
    confirm_payload = confirm_response.json()
    assert confirm_payload["success"] is True
    assert confirm_payload["listing_id"] == listing_id
    assert confirm_payload["status"] == "sold"
    assert confirm_payload["sold_at"] is not None

    seller_listings_response = await client.get("/my-listings", headers=auth_headers("seller"))
    seller_listing = seller_listings_response.json()["listings"][0]
    assert seller_listing["status"] == "sold"
    assert seller_listing["reserved_at"] is not None
    assert seller_listing["sold_at"] == confirm_payload["sold_at"]
    assert "buyer_pin" not in seller_listing

    buyer_orders_after_response = await client.get("/orders/me", headers=auth_headers("buyer"))
    buyer_order = buyer_orders_after_response.json()["orders"][0]
    assert buyer_order["status"] == "sold"
    assert buyer_order["buyer_pin"] is None
    assert buyer_order["has_buyer_pin"] is False
    assert buyer_order["sold_at"] == confirm_payload["sold_at"]

    feed_response = await client.get(
        "/listings",
        params={"building_id": building.id},
        headers=auth_headers("buyer"),
    )
    assert feed_response.status_code == 200
    assert feed_response.json()["count"] == 0

    async with db_sessionmaker() as session:
        result = await session.execute(select(Listing).where(Listing.id == listing_id))
        listing = result.scalar_one()
        assert listing.status == "sold"
        assert listing.sold_at is not None
        assert listing.reserved_at is not None
        assert listing.transaction_pin is None


@pytest.mark.asyncio
async def test_flow_10_orders_and_my_listings_reflect_in_progress_and_sold_states(client, db_sessionmaker, token_claims):
    building = await create_building(db_sessionmaker, name="Juliet House", invite_code="JULIET123")
    users = [
        ("seller", "seller@example.com", "Tomato", "Seller", "1A"),
        ("buyer", "buyer@example.com", "Carrot", "Buyer", "2A"),
        ("other_buyer", "otherbuyer@example.com", "Bean", "Other Buyer", "3A"),
    ]

    for token, email, alias, full_name, room in users:
        token_claims[token] = {"uid": f"uid-{token}", "email": email, "name": alias}
        await client.get("/me", headers=auth_headers(token))
        await client.post("/join-building", json={"invite_code": building.invite_code}, headers=auth_headers(token))
        await client.patch(
            "/me",
            json={
                "display_name": alias,
                "full_name": full_name,
                "building_id": building.id,
                "room_number_private": room,
            },
            headers=auth_headers(token),
        )

    active_listing_response = await client.post(
        "/listings",
        json={"building_id": building.id, "title": "Chair", "description": "Desk chair", "price": 25},
        headers=auth_headers("seller"),
    )
    active_listing_id = active_listing_response.json()["id"]

    in_progress_listing_response = await client.post(
        "/listings",
        json={"building_id": building.id, "title": "Shelf", "description": "Wall shelf", "price": 35},
        headers=auth_headers("seller"),
    )
    in_progress_listing_id = in_progress_listing_response.json()["id"]

    sold_listing_response = await client.post(
        "/listings",
        json={"building_id": building.id, "title": "Plant", "description": "Indoor plant", "price": 20},
        headers=auth_headers("seller"),
    )
    sold_listing_id = sold_listing_response.json()["id"]

    extra_in_progress_listing_response = await client.post(
        "/listings",
        json={"building_id": building.id, "title": "Fan", "description": "Quiet fan", "price": 18},
        headers=auth_headers("seller"),
    )
    extra_in_progress_listing_id = extra_in_progress_listing_response.json()["id"]

    await client.post(f"/listings/{in_progress_listing_id}/buy", headers=auth_headers("buyer"))
    await client.post(f"/listings/{sold_listing_id}/buy", headers=auth_headers("buyer"))

    sold_orders_response = await client.get("/orders/me", headers=auth_headers("buyer"))
    sold_order = {order["listing_id"]: order for order in sold_orders_response.json()["orders"]}[sold_listing_id]

    confirm_response = await client.post(
        f"/listings/{sold_listing_id}/confirm-pin",
        json={"pin": sold_order["buyer_pin"]},
        headers=auth_headers("seller"),
    )
    assert confirm_response.status_code == 200

    await client.post(f"/listings/{extra_in_progress_listing_id}/buy", headers=auth_headers("other_buyer"))

    seller_listings_response = await client.get("/my-listings", headers=auth_headers("seller"))
    assert seller_listings_response.status_code == 200
    listings_by_id = {listing["id"]: listing for listing in seller_listings_response.json()["listings"]}
    assert listings_by_id[active_listing_id]["status"] == "active"
    assert listings_by_id[in_progress_listing_id]["status"] == "in_progress"
    assert listings_by_id[sold_listing_id]["status"] == "sold"
    assert listings_by_id[extra_in_progress_listing_id]["status"] == "in_progress"
    assert listings_by_id[in_progress_listing_id]["reserved_at"] is not None
    assert listings_by_id[sold_listing_id]["sold_at"] is not None
    assert "buyer_pin" not in listings_by_id[in_progress_listing_id]

    buyer_orders_response = await client.get("/orders/me", headers=auth_headers("buyer"))
    assert buyer_orders_response.status_code == 200
    buyer_orders = {order["listing_id"]: order for order in buyer_orders_response.json()["orders"]}
    assert set(buyer_orders.keys()) == {in_progress_listing_id, sold_listing_id}
    assert buyer_orders[in_progress_listing_id]["status"] == "in_progress"
    assert buyer_orders[in_progress_listing_id]["buyer_pin"] is not None
    assert buyer_orders[in_progress_listing_id]["has_buyer_pin"] is True
    assert buyer_orders[sold_listing_id]["status"] == "sold"
    assert buyer_orders[sold_listing_id]["buyer_pin"] is None
    assert buyer_orders[sold_listing_id]["has_buyer_pin"] is False
    assert buyer_orders[sold_listing_id]["sold_at"] is not None
