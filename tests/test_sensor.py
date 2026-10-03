"""Tests for the sensor platform, via a full config entry setup."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import aiohttp
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.helpers import entity_registry as er

from custom_components.a_petits_pas.api import Activity
from custom_components.a_petits_pas.const import BASE_URL, DOMAIN
from custom_components.a_petits_pas.sensor import _format_time_range, _truncate_state

from .conftest import async_setup_mock_entry, load_fixture_json, mock_full_account

PERSON_ID = "1000001"

ACTIVITY_CATEGORY_KEYS = ("activity_meal", "activity_nap", "activity_diaper")


def _activity(start=None, end=None) -> Activity:
    return Activity(
        id=1,
        activity_type_id=1,
        category="nap",
        activity_type_name="Sieste",
        comment="",
        start=start,
        end=end,
        numeric_value=None,
        writer_person_id=None,
    )


def test_format_time_range_uses_only_time_of_day():
    # Amisgest returns time-only fields with a fixed placeholder date
    # (observed: 2017-01-01) -- the formatted range must ignore it.
    start = datetime.fromisoformat("2017-01-01T12:30:00-05:00")
    end = datetime.fromisoformat("2017-01-01T14:00:00-05:00")
    assert _format_time_range(_activity(start, end)) == "12:30 - 14:00"


def test_format_time_range_single_bound():
    end = datetime.fromisoformat("2017-01-01T14:00:00-05:00")
    assert _format_time_range(_activity(end=end)) == "14:00"


def test_format_time_range_no_bounds_returns_none():
    assert _format_time_range(_activity()) is None


def test_truncate_state_leaves_short_text_untouched():
    assert _truncate_state("Elle a bien mangé.") == "Elle a bien mangé."


def test_truncate_state_fits_long_text_into_ha_state_limit():
    # A real capture had a 306-char meal comment: HA silently falls the
    # sensor's state back to "unknown" for anything over 255 chars, instead
    # of raising, so this must proactively fit within that limit.
    comment = "Elle a bien mangé. " * 20
    assert len(comment) > 255

    result = _truncate_state(comment)

    assert len(result) == 255
    assert result.endswith("…")
    assert comment.startswith(result[:-1])


async def test_latest_logbook_sensor_state_and_attributes(hass, aioclient_mock):
    entry = await async_setup_mock_entry(hass, aioclient_mock)

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_{PERSON_ID}_latest_entry"
    )
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "2026-09-14T13:00:00-04:00"
    assert state.attributes["logbook_entry_id"] == 900001
    assert state.attributes["activity_count"] == 3
    assert state.attributes["photo_count"] == 0
    assert state.attributes["photos"] == []
    categories = {a["category"] for a in state.attributes["activities"]}
    assert categories == {"meal", "nap", "diaper"}


async def test_latest_logbook_sensor_archive_photos_survive_missing_entry(hass, aioclient_mock):
    """When Amisgest returns no logbook_entry at all for today (e.g. between
    local midnight and the daycare posting the day's entry), archive_photos
    must still reflect what's actually saved on disk, not go empty along
    with the rest of the entry's attributes."""
    entry = await async_setup_mock_entry(hass, aioclient_mock)

    coordinator = hass.data[DOMAIN][entry.entry_id]
    child_data = coordinator.data.children[PERSON_ID]
    child_data.latest_entry = None
    child_data.archive_photo_urls = ["/local/a_petits_pas/1000001/archive/2026-09-13_1.jpg"]
    coordinator.async_set_updated_data(coordinator.data)

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_{PERSON_ID}_latest_entry"
    )
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == STATE_UNKNOWN
    assert state.attributes["archive_photos"] == [
        "/local/a_petits_pas/1000001/archive/2026-09-13_1.jpg"
    ]
    assert "logbook_entry_id" not in state.attributes


async def test_latest_logbook_sensor_unposted_placeholder_date_is_unknown(hass, aioclient_mock):
    """Amisgest returns posted_date=1900-01-01 for an unposted/draft entry.
    The sensor state must be unknown rather than falling back to the numeric entry ID."""
    aioclient_mock.get(
        f"{BASE_URL}/api/Account/cahier_description",
        json=load_fixture_json("cahier_description.json"),
    )
    aioclient_mock.get(f"{BASE_URL}/api/wall/newInboxCount", text="0")
    aioclient_mock.get(
        f"{BASE_URL}/breeze/Breeze/logbook_entry",
        json=[
            {
                "id": 68654761,
                "owner_person_id": int(PERSON_ID),
                "writer_person_id": 800001,
                "logbook_entry_status_id": 1,
                "posted_date": "1900-01-01T00:00:00.000-05:00",
                "activity": [],
            }
        ],
    )
    aioclient_mock.get(f"{BASE_URL}/breeze/Breeze/activity", json=[])
    aioclient_mock.get(f"{BASE_URL}/breeze/Breeze/activity_type", json=[])
    aioclient_mock.get(f"{BASE_URL}/breeze/Breeze/activity_media", json=[])
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
        "sensor", DOMAIN, f"{entry.entry_id}_{PERSON_ID}_latest_entry"
    )
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    # Must never take the entry ID as state
    assert state.state != "68654761"
    assert state.state == STATE_UNKNOWN
    assert state.attributes["logbook_entry_id"] == 68654761


async def test_info_category_sensor_created_and_populated(hass, aioclient_mock):
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
        json=[
            {
                "id": 10,
                "activity_type_id": 9,
                "comment": "Superbe activité de pâte à modeler",
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
        json=[],
    )
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
        "sensor", DOMAIN, f"{entry.entry_id}_{PERSON_ID}_activity_info"
    )
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert "pâte à modeler" in state.state


async def test_meal_category_sensor_created_and_populated(hass, aioclient_mock):
    entry = await async_setup_mock_entry(hass, aioclient_mock)

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_{PERSON_ID}_activity_meal"
    )
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert "pâtes" in state.state


async def test_nap_category_sensor_state_is_a_clean_time_range(hass, aioclient_mock):
    entry = await async_setup_mock_entry(hass, aioclient_mock)

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_{PERSON_ID}_activity_nap"
    )
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "12:30 - 14:00"
    assert state.attributes["start_time"] == "12:30"
    assert state.attributes["end_time"] == "14:00"
    assert state.attributes["start"] is not None
    assert state.attributes["end"] is not None


async def test_presence_sensor_state_and_attributes(hass, aioclient_mock):
    """The entity state is the raw enum option (lowercased code_ph) --
    HA's translation system localizes it per-viewer in the real frontend,
    not in the backend state itself (see strings.json/translations/*.json
    for the fr/en labels of each option)."""
    entry = await async_setup_mock_entry(hass, aioclient_mock)

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_{PERSON_ID}_presence_today"
    )
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "expected"
    assert state.attributes["device_class"] == "enum"
    assert state.attributes["options"] == ["p", "a", "dp", "expected", "not_expected"]
    assert state.attributes["code"] == "EXPECTED"
    assert state.attributes["answer_required"] is True


async def test_no_sensor_for_unobserved_category(hass, aioclient_mock):
    """We don't have a 'writer' activity in the fixtures, so no sensor for it."""
    entry = await async_setup_mock_entry(hass, aioclient_mock)

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_{PERSON_ID}_activity_writer"
    )
    assert entity_id is None


async def test_daily_reset_leaves_activity_sensors_unknown_not_unavailable(hass, aioclient_mock):
    """When the CPE hasn't written today's entry yet, the source still
    answers normally (just with no matching entry in the rolling 24h
    window) -- the four 'latest activity' sensors must report the
    HA-native "unknown" state, not "unavailable", exactly like
    `dernier_journal_de_bord` already does."""
    entry = await async_setup_mock_entry(hass, aioclient_mock)
    registry = er.async_get(hass)

    activity_entity_ids = {
        key: registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{PERSON_ID}_{key}")
        for key in ACTIVITY_CATEGORY_KEYS
    }
    for entity_id in activity_entity_ids.values():
        assert entity_id is not None
        assert hass.states.get(entity_id).state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)

    # Simulate the rolling 24h window moving past yesterday's entry with no
    # new one posted yet -- the API answers normally with an empty list.
    # Registered before mock_full_account() so it wins the first-match
    # lookup for this URL (AiohttpClientMocker keeps first-registered-wins
    # semantics).
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE_URL}/breeze/Breeze/logbook_entry", json=[])
    mock_full_account(aioclient_mock)

    coordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.last_update_success is True
    for entity_id in activity_entity_ids.values():
        state = hass.states.get(entity_id)
        assert state.state == STATE_UNKNOWN
        assert state.attributes.get("comment") is None


async def test_fetch_failure_marks_sensors_unavailable_then_recovers(hass, aioclient_mock):
    """A real connectivity failure (e.g. 'Cannot connect to host
    serviceapp.amisgest.ca') must mark every child sensor unavailable via
    coordinator.last_update_success, and a subsequent successful refresh
    ('Fetching a_petits_pas data recovered') must bring them back."""
    entry = await async_setup_mock_entry(hass, aioclient_mock)
    registry = er.async_get(hass)

    entity_ids = {
        key: registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{PERSON_ID}_{key}")
        for key in (*ACTIVITY_CATEGORY_KEYS, "latest_entry")
    }
    for entity_id in entity_ids.values():
        assert entity_id is not None
        assert hass.states.get(entity_id).state != STATE_UNAVAILABLE

    coordinator = hass.data[DOMAIN][entry.entry_id]

    aioclient_mock.clear_requests()
    aioclient_mock.get(
        f"{BASE_URL}/api/Account/cahier_description", exc=aiohttp.ClientError("Cannot connect")
    )
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.last_update_success is False
    for entity_id in entity_ids.values():
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    aioclient_mock.clear_requests()
    mock_full_account(aioclient_mock)
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.last_update_success is True
    for entity_id in entity_ids.values():
        state = hass.states.get(entity_id)
        assert state.state != STATE_UNAVAILABLE

    meal_state = hass.states.get(entity_ids["activity_meal"])
    assert "pâtes" in meal_state.state
