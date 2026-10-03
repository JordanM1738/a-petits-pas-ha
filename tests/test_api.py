"""Tests for the Amisgest API client (custom_components/a_petits_pas/api.py)."""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta, timezone

import aiohttp
import pytest
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.a_petits_pas.api import (
    AmisgestApiError,
    AmisgestAuthError,
    AmisgestClient,
    AmisgestTokens,
)
from custom_components.a_petits_pas.const import BASE_URL

from .conftest import load_fixture_json


def _make_client(hass, *, expires_in: timedelta = timedelta(days=1)) -> AmisgestClient:
    session = async_get_clientsession(hass)
    tokens = AmisgestTokens(
        access_token="token",
        refresh_token="refresh",
        expires_at=datetime.now(UTC) + expires_in,
    )
    return AmisgestClient(session, device_id="device-1", tokens=tokens)


# ---- _decode_body -------------------------------------------------------


def test_decode_body_plain_json():
    assert AmisgestClient._decode_body('{"a": 1}') == {"a": 1}


def test_decode_body_base64_wrapped_json():
    inner = json.dumps([{"a": 1}])
    wrapped = json.dumps(base64.b64encode(inner.encode()).decode())
    assert AmisgestClient._decode_body(wrapped) == [{"a": 1}]


def test_decode_body_invalid_raises():
    with pytest.raises(AmisgestApiError):
        AmisgestClient._decode_body("not json at all")


# ---- Login / refresh ------------------------------------------------------


async def test_login_success(hass, aioclient_mock):
    aioclient_mock.post(f"{BASE_URL}/token", json=load_fixture_json("token_response.json"))

    session = async_get_clientsession(hass)
    tokens = await AmisgestClient.async_login(
        session, email="parent@example.com", password="secret", device_id="device-1"
    )
    assert tokens.access_token == "fake-access-token"
    assert tokens.refresh_token == "fake-refresh-token"


async def test_login_invalid_credentials_raises_auth_error(hass, aioclient_mock):
    aioclient_mock.post(f"{BASE_URL}/token", status=400)

    session = async_get_clientsession(hass)
    with pytest.raises(AmisgestAuthError):
        await AmisgestClient.async_login(
            session, email="parent@example.com", password="wrong", device_id="device-1"
        )


async def test_concurrent_refresh_only_hits_token_endpoint_once(hass, aioclient_mock):
    aioclient_mock.post(f"{BASE_URL}/token", json=load_fixture_json("token_response.json"))
    client = _make_client(hass, expires_in=timedelta(seconds=-1))

    await asyncio.gather(client._async_ensure_token(), client._async_ensure_token())

    token_calls = [c for c in aioclient_mock.mock_calls if c[0] == "POST"]
    assert len(token_calls) == 1
    assert client.tokens.access_token == "fake-access-token"


# ---- High-level calls -------------------------------------------------


async def test_get_cahier_description(hass, aioclient_mock):
    aioclient_mock.get(
        f"{BASE_URL}/api/Account/cahier_description",
        json=load_fixture_json("cahier_description.json"),
    )
    # person_link unreachable -> photo resolution fails gracefully rather
    # than raising (caught by _async_resolve_child_photo_url's except clause).
    aioclient_mock.get(f"{BASE_URL}/breeze/Breeze/person_link", exc=aiohttp.ClientError())
    client = _make_client(hass)

    children = await client.async_get_cahier_description()

    assert len(children) == 1
    assert children[0].person_id == "1000001"
    assert children[0].person_name == "ENFANT EXEMPLE"
    assert children[0].client_name == "CPE Exemple"
    assert children[0].photo_url is None


async def test_get_cahier_description_sends_bearer_token(hass, aioclient_mock):
    aioclient_mock.get(
        f"{BASE_URL}/api/Account/cahier_description",
        json=load_fixture_json("cahier_description.json"),
    )
    aioclient_mock.get(f"{BASE_URL}/breeze/Breeze/person_link", exc=aiohttp.ClientError())
    client = _make_client(hass)

    await client.async_get_cahier_description()

    call = aioclient_mock.mock_calls[0]
    headers = call[3]
    assert headers["Authorization"] == "Bearer token"


async def test_get_cahier_description_resolves_real_child_photo_not_parent_photo(
    hass, aioclient_mock
):
    """Regression test: cahier_description's userData.photoUrl points at the
    logged-in PARENT's photo, not the child's -- async_get_cahier_description
    must resolve the real child photo via person_link + GetAllPhotoGuid
    instead of trusting it."""
    aioclient_mock.get(
        f"{BASE_URL}/api/Account/cahier_description",
        json=load_fixture_json("cahier_description.json"),
    )
    aioclient_mock.get(
        f"{BASE_URL}/breeze/Breeze/person_link",
        json=load_fixture_json("person_link.json"),
    )
    aioclient_mock.get(
        f"{BASE_URL}/api/photo/GetAllPhotoGuid",
        json=load_fixture_json("photo_guid_roster.json"),
    )
    client = _make_client(hass)

    children = await client.async_get_cahier_description()

    assert children[0].photo_url is not None
    assert "getPhotoPersonWGuid" in children[0].photo_url
    assert "id=fakeguid123" in children[0].photo_url
    assert "id2=1000001" in children[0].photo_url
    # Never the wrong userData.photoUrl from the fixture.
    assert "getPhotoPerson?" not in children[0].photo_url


async def test_resolve_child_photo_url_is_cached(hass, aioclient_mock):
    aioclient_mock.get(
        f"{BASE_URL}/breeze/Breeze/person_link",
        json=load_fixture_json("person_link.json"),
    )
    aioclient_mock.get(
        f"{BASE_URL}/api/photo/GetAllPhotoGuid",
        json=load_fixture_json("photo_guid_roster.json"),
    )
    client = _make_client(hass)

    first = await client._async_resolve_child_photo_url("1000001")
    second = await client._async_resolve_child_photo_url("1000001")

    assert first == second
    person_link_calls = [c for c in aioclient_mock.mock_calls if "person_link" in str(c[1])]
    assert len(person_link_calls) == 1  # not re-fetched on the second call


async def test_get_latest_logbook_entry_resolves_categories(hass, aioclient_mock):
    aioclient_mock.get(
        f"{BASE_URL}/breeze/Breeze/logbook_entry",
        json=load_fixture_json("logbook_entry.json"),
    )
    aioclient_mock.get(
        f"{BASE_URL}/breeze/Breeze/activity",
        json=load_fixture_json("activity_list.json"),
    )
    aioclient_mock.get(
        f"{BASE_URL}/breeze/Breeze/activity_type",
        json=load_fixture_json("activity_type.json"),
    )
    aioclient_mock.get(
        f"{BASE_URL}/breeze/Breeze/activity_media",
        json=[],
    )
    client = _make_client(hass)

    entry = await client.async_get_latest_logbook_entry("1000001")

    assert entry is not None
    assert entry.id == 900001
    categories = {a.category for a in entry.activities}
    assert categories == {"meal", "nap", "diaper"}

    meal = next(a for a in entry.activities if a.category == "meal")
    assert meal.activity_type_name == "Repas"
    assert "pâtes" in meal.comment

    nap = next(a for a in entry.activities if a.category == "nap")
    assert nap.start is not None
    assert nap.end is not None


async def test_get_latest_logbook_entry_sends_person_id_header(hass, aioclient_mock):
    aioclient_mock.get(
        f"{BASE_URL}/breeze/Breeze/logbook_entry",
        json=load_fixture_json("logbook_entry.json"),
    )
    aioclient_mock.get(
        f"{BASE_URL}/breeze/Breeze/activity",
        json=load_fixture_json("activity_list.json"),
    )
    aioclient_mock.get(
        f"{BASE_URL}/breeze/Breeze/activity_type",
        json=load_fixture_json("activity_type.json"),
    )
    aioclient_mock.get(
        f"{BASE_URL}/breeze/Breeze/activity_media",
        json=[],
    )
    client = _make_client(hass)

    await client.async_get_latest_logbook_entry("1000001")

    logbook_call = next(c for c in aioclient_mock.mock_calls if "logbook_entry" in str(c[1]))
    assert logbook_call[3]["personid"] == "1000001"


async def test_get_latest_logbook_entry_no_entries_returns_none(hass, aioclient_mock):
    aioclient_mock.get(f"{BASE_URL}/breeze/Breeze/logbook_entry", json=[])
    client = _make_client(hass)

    entry = await client.async_get_latest_logbook_entry("1000001")

    assert entry is None


async def test_get_latest_logbook_entry_placeholder_posted_date_becomes_none(hass, aioclient_mock):
    """Amisgest returns posted_date=1900-01-01 for an entry not yet
    "posted"/finalized by daycare staff -- this sentinel must not be
    surfaced as a real date."""
    aioclient_mock.get(
        f"{BASE_URL}/breeze/Breeze/logbook_entry",
        json=[
            {
                "id": 900002,
                "owner_person_id": 1000001,
                "logbook_entry_status_id": 1,
                "posted_date": "1900-01-01T00:00:00.000-05:00",
                "activity": [],
            }
        ],
    )
    aioclient_mock.get(f"{BASE_URL}/breeze/Breeze/activity", json=[])
    aioclient_mock.get(f"{BASE_URL}/breeze/Breeze/activity_type", json=[])
    aioclient_mock.get(f"{BASE_URL}/breeze/Breeze/activity_media", json=[])
    client = _make_client(hass)

    entry = await client.async_get_latest_logbook_entry("1000001")

    assert entry is not None
    assert entry.posted_date is None


async def test_get_latest_logbook_entry_with_activity_media_and_previews(hass, aioclient_mock):
    aioclient_mock.get(
        f"{BASE_URL}/breeze/Breeze/logbook_entry",
        json=load_fixture_json("logbook_entry.json"),
    )
    aioclient_mock.get(
        f"{BASE_URL}/breeze/Breeze/activity",
        json=[
            {
                "id": 10,
                "activity_type_id": 9,
                "comment": "Belle journée de jeux",
                "logbook_entry_id": 900001,
            }
        ],
    )
    aioclient_mock.get(
        f"{BASE_URL}/breeze/Breeze/activity_type",
        json=[{"id": 9, "name": "Informations"}],
    )
    aioclient_mock.get(
        f"{BASE_URL}/breeze/Breeze/activity_media",
        json=[{"activity_id": 10, "media_id": 50001}],
    )
    aioclient_mock.get(
        f"{BASE_URL}/api/media/GetPreviewUrl",
        json=[{"id": 50001, "signedUrl": "https://s3.example.com/preview.png"}],
    )
    client = _make_client(hass)

    entry = await client.async_get_latest_logbook_entry("1000001")

    assert entry is not None
    assert entry.media_ids == [50001]
    assert len(entry.photos) == 1
    assert entry.photos[0]["media_id"] == 50001
    assert entry.photos[0]["preview_url"] == "https://s3.example.com/preview.png"
    assert entry.activities[0].category == "info"
    assert entry.activities[0].media_ids == [50001]


async def test_new_inbox_count(hass, aioclient_mock):
    aioclient_mock.get(f"{BASE_URL}/api/wall/newInboxCount", text="3")
    client = _make_client(hass)

    assert await client.async_get_new_inbox_count("1000001") == 3


async def test_unreachable_endpoint_raises_api_error(hass, aioclient_mock):
    aioclient_mock.get(f"{BASE_URL}/api/Account/cahier_description", exc=aiohttp.ClientError())
    client = _make_client(hass)

    with pytest.raises(AmisgestApiError):
        await client.async_get_cahier_description()


# ---- Presence -------------------------------------------------------------


async def test_get_presence_today(hass, aioclient_mock):
    aioclient_mock.get(
        f"{BASE_URL}/api/confirmationAbs/confirmationsWithDate",
        json=load_fixture_json("confirmations_with_date.json"),
    )
    client = _make_client(hass)

    presence = await client.async_get_presence_today("1000001")

    assert presence is not None
    assert presence.code == "EXPECTED"
    assert presence.answer_required is True


async def test_get_presence_today_present_and_absent_codes(hass, aioclient_mock):
    # "P"/"A" confirmed live against a real account on 2026-09-15;
    # "NOT_EXPECTED" (child not scheduled to attend that day at all, as
    # opposed to "A"'s unscheduled/unconfirmed absence) confirmed live on
    # 2026-09-17.
    for code in ("P", "A", "NOT_EXPECTED"):
        aioclient_mock.clear_requests()
        aioclient_mock.get(
            f"{BASE_URL}/api/confirmationAbs/confirmationsWithDate",
            json=[{"personId": 1000001, "code_ph": code, "answer_required": False}],
        )
        client = _make_client(hass)

        presence = await client.async_get_presence_today("1000001")

        assert presence is not None
        assert presence.code == code


async def test_get_presence_today_unknown_code_passes_through(hass, aioclient_mock):
    aioclient_mock.get(
        f"{BASE_URL}/api/confirmationAbs/confirmationsWithDate",
        json=[{"personId": 1000001, "code_ph": "SOME_NEW_CODE", "answer_required": False}],
    )
    client = _make_client(hass)

    presence = await client.async_get_presence_today("1000001")

    assert presence is not None
    assert presence.code == "SOME_NEW_CODE"  # unmapped code passed through raw, not hidden


async def test_get_presence_today_no_records_returns_none(hass, aioclient_mock):
    aioclient_mock.get(f"{BASE_URL}/api/confirmationAbs/confirmationsWithDate", json=[])
    client = _make_client(hass)

    assert await client.async_get_presence_today("1000001") is None


async def test_get_presence_today_uses_local_date_not_utc_date(hass, aioclient_mock, monkeypatch):
    """Regression test: a poll in the Eastern-time evening (already the next
    calendar day in UTC) must query confirmationsWithDate for the local
    ("today" at the daycare) date, not the UTC date -- otherwise a status
    like "DP" (departed) recorded for the current evening gets looked up
    under tomorrow's date, which has no record yet."""
    # 2026-01-15 20:00 Eastern (UTC-5) == 2026-01-16 01:00 UTC.
    local_evening = datetime(2026, 1, 15, 20, 0, tzinfo=timezone(timedelta(hours=-5)))
    monkeypatch.setattr("custom_components.a_petits_pas.api.dt_util.now", lambda: local_evening)
    aioclient_mock.get(
        f"{BASE_URL}/api/confirmationAbs/confirmationsWithDate",
        json=[{"personId": 1000001, "code_ph": "DP", "answer_required": False}],
    )
    client = _make_client(hass)

    await client.async_get_presence_today("1000001")

    call = aioclient_mock.mock_calls[0]
    assert call[1].query["curDate"] == "2026-01-15"


# ---- Photo ------------------------------------------------------------


async def test_get_photo_bytes(hass, aioclient_mock):
    photo_url = "https://serviceapp.amisgest.ca/8_2/api/Photo/getPhotoPerson?id=1000001"
    aioclient_mock.get(photo_url, content=b"fake-jpeg-bytes")
    client = _make_client(hass)

    data = await client.async_get_photo_bytes(photo_url)

    assert data == b"fake-jpeg-bytes"


async def test_get_photo_bytes_returns_none_on_failure(hass, aioclient_mock):
    photo_url = "https://serviceapp.amisgest.ca/8_2/api/Photo/getPhotoPerson?id=1000001"
    aioclient_mock.get(photo_url, status=404)
    client = _make_client(hass)

    assert await client.async_get_photo_bytes(photo_url) is None


async def test_get_media_bytes_success(hass, aioclient_mock):
    aioclient_mock.get(
        f"{BASE_URL}/api/media/GetMediaGuid",
        json={"id": 50001, "signedUrl": "https://s3.example.com/final.jpg"},
    )
    aioclient_mock.get("https://s3.example.com/final.jpg", content=b"s3-image-data")
    client = _make_client(hass)

    data = await client.async_get_media_bytes(50001)

    assert data == b"s3-image-data"


async def test_get_media_bytes_preview_fallback(hass, aioclient_mock):
    aioclient_mock.get(f"{BASE_URL}/api/media/GetMediaGuid", status=500)
    aioclient_mock.get(
        f"{BASE_URL}/api/media/GetPreviewUrl",
        json=[{"id": 50001, "signedUrl": "https://s3.example.com/preview.jpg"}],
    )
    aioclient_mock.get("https://s3.example.com/preview.jpg", content=b"preview-image-data")
    client = _make_client(hass)

    data = await client.async_get_media_bytes(50001)

    assert data == b"preview-image-data"


async def test_get_media_bytes_failure_returns_none(hass, aioclient_mock):
    aioclient_mock.get(f"{BASE_URL}/api/media/GetMediaGuid", status=500)
    aioclient_mock.get(f"{BASE_URL}/api/media/GetPreviewUrl", status=500)
    client = _make_client(hass)

    data = await client.async_get_media_bytes(50001)

    assert data is None
