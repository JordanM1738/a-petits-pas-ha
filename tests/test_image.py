"""Tests for the image platform (child profile photo)."""

from __future__ import annotations

from homeassistant.helpers import entity_registry as er

from custom_components.a_petits_pas.const import DOMAIN

from .conftest import async_setup_mock_entry

PERSON_ID = "1000001"


async def test_profile_photo_entity_created(hass, aioclient_mock):
    entry = await async_setup_mock_entry(hass, aioclient_mock)

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("image", DOMAIN, f"{entry.entry_id}_{PERSON_ID}_photo")
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None


async def test_profile_photo_bytes_are_fetched_with_auth(hass, aioclient_mock):
    entry = await async_setup_mock_entry(hass, aioclient_mock)

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("image", DOMAIN, f"{entry.entry_id}_{PERSON_ID}_photo")
    assert entity_id is not None

    entity = hass.data["image"].get_entity(entity_id)
    assert entity is not None

    image = await entity.async_image()
    assert image == b"fake-jpeg-bytes"


async def test_journal_photo_entity_unavailable_when_no_photos(hass, aioclient_mock):
    entry = await async_setup_mock_entry(hass, aioclient_mock)

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "image", DOMAIN, f"{entry.entry_id}_{PERSON_ID}_journal_photo"
    )
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "unavailable"


async def test_journal_photo_archive_photos_survive_missing_entry(hass, aioclient_mock):
    """Same fallback as the logbook sensor: archive_photos must keep coming
    from disk even when there's no latest_entry (e.g. before today's journal
    is posted), so a dashboard can still show yesterday's photos."""
    entry = await async_setup_mock_entry(hass, aioclient_mock)

    coordinator = hass.data[DOMAIN][entry.entry_id]
    child_data = coordinator.data.children[PERSON_ID]
    child_data.latest_entry = None
    child_data.archive_photo_urls = ["/local/a_petits_pas/1000001/archive/2026-09-13_1.jpg"]
    coordinator.async_set_updated_data(coordinator.data)

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "image", DOMAIN, f"{entry.entry_id}_{PERSON_ID}_journal_photo"
    )
    assert entity_id is not None

    entity = hass.data["image"].get_entity(entity_id)
    assert entity is not None
    assert entity.extra_state_attributes == {
        "archive_photos": ["/local/a_petits_pas/1000001/archive/2026-09-13_1.jpg"]
    }


async def test_journal_photo_entity_available_and_fetches_bytes(hass, aioclient_mock):
    from custom_components.a_petits_pas.const import BASE_URL

    from .conftest import load_fixture_json

    aioclient_mock.get(
        f"{BASE_URL}/api/Account/cahier_description",
        json=load_fixture_json("cahier_description.json"),
    )
    aioclient_mock.get(f"{BASE_URL}/api/wall/newInboxCount", text="0")
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
        json=[{"activity_id": 1, "media_id": 60001}],
    )
    aioclient_mock.get(
        f"{BASE_URL}/api/media/GetPreviewUrl",
        json=[{"id": 60001, "signedUrl": "https://s3.example.com/preview.jpg"}],
    )
    aioclient_mock.get(
        f"{BASE_URL}/api/media/GetMediaGuid",
        json={"id": 60001, "signedUrl": "https://s3.example.com/final.jpg"},
    )
    aioclient_mock.get("https://s3.example.com/final.jpg", content=b"journal-photo-bytes")
    aioclient_mock.get(
        f"{BASE_URL}/api/confirmationAbs/confirmationsWithDate",
        json=load_fixture_json("confirmations_with_date.json"),
    )
    aioclient_mock.get(
        f"{BASE_URL}/breeze/Breeze/person_link",
        json=load_fixture_json("person_link.json"),
    )
    aioclient_mock.get(
        f"{BASE_URL}/api/photo/GetAllPhotoGuid",
        json=load_fixture_json("photo_guid_roster.json"),
    )
    aioclient_mock.get(
        f"{BASE_URL}/api/Photo/getPhotoPersonWGuid",
        content=b"fake-jpeg-bytes",
    )

    from datetime import UTC, datetime, timedelta

    from homeassistant.const import CONF_USERNAME
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.a_petits_pas.const import (
        CONF_ACCESS_TOKEN,
        CONF_DEVICE_ID,
        CONF_EXPIRES_AT,
        CONF_REFRESH_TOKEN,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="parent@example.com",
        data={
            CONF_USERNAME: "parent@example.com",
            CONF_DEVICE_ID: "device-1",
            CONF_ACCESS_TOKEN: "fake-access-token",
            CONF_REFRESH_TOKEN: "fake-refresh-token",
            CONF_EXPIRES_AT: (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "image", DOMAIN, f"{entry.entry_id}_{PERSON_ID}_journal_photo"
    )
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state != "unavailable"
    assert state.attributes["photo_count"] == 1
    assert state.attributes["media_ids"] == [60001]
    assert len(state.attributes["photos"]) == 1

    entity = hass.data["image"].get_entity(entity_id)
    assert entity is not None
    photo_bytes = await entity.async_image()
    assert photo_bytes == b"journal-photo-bytes"
