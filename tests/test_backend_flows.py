import pytest
from sqlalchemy import select

from app.models.building import Building, BuildingMembership
from app.models.listing import Listing
from app.models.listing_report import ListingReport
from app.models.user import User


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def create_building(db_sessionmaker, *, name: str, invite_code: str) -> Building:
    async with db_sessionmaker() as session:
        building = Building(name=name, invite_code=invite_code)
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
async def test_flow_1_user_onboarding(client, db_sessionmaker, token_claims):
    building = await create_building(db_sessionmaker, name="Alpha House", invite_code="ALPHA123")
    token_claims["user1"] = {
        "uid": "uid-user1",
        "email": "user1@example.com",
        "name": "Tomato",
    }

    me_response = await client.get("/me", headers=auth_headers("user1"))
    assert me_response.status_code == 200
    assert me_response.json()["email"] == "user1@example.com"
    assert me_response.json()["display_name"] == "Tomato"
    assert me_response.json()["profile_completed"] is False

    join_response = await client.post(
        "/join-building",
        json={"invite_code": building.invite_code},
        headers=auth_headers("user1"),
    )
    assert join_response.status_code == 200
    assert join_response.json()["joined"] is True

    update_response = await client.patch(
        "/me",
        json={
            "display_name": "Tomato",
            "full_name": "User One",
            "building_id": building.id,
            "room_number_private": "12B",
        },
        headers=auth_headers("user1"),
    )
    assert update_response.status_code == 200
    payload = update_response.json()
    assert payload["profile_completed"] is True
    assert payload["building_id"] == building.id

    my_buildings_response = await client.get("/my-buildings", headers=auth_headers("user1"))
    assert my_buildings_response.status_code == 200
    assert my_buildings_response.json()["count"] == 1


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
