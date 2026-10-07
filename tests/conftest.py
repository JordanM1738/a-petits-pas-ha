"""Shared test fixtures for the a_petits_pas integration tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from homeassistant.const import CONF_USERNAME
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.a_petits_pas.const import (
    BASE_URL,
    CONF_ACCESS_TOKEN,
    CONF_DEVICE_ID,
    CONF_EXPIRES_AT,
    CONF_REFRESH_TOKEN,
    DOMAIN,
)

pytest_plugins = "pytest_homeassistant_custom_component"

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    """Return the raw text of a fixture file under tests/fixtures/."""
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def load_fixture_json(name: str):
    return json.loads(load_fixture(name))


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Make custom_components discoverable in every test (HA core requirement)."""
    yield


def mock_full_account(aioclient_mock) -> None:
    """Register mocks for every call a full config-entry setup will make."""
    aioclient_mock.get(
        f"{BASE_URL}/api/Account/cahier_description",
        json=load_fixture_json("cahier_description.json"),
    )
    aioclient_mock.get(
        f"{BASE_URL}/api/wall/newInboxCount",
        text="0",
    )
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


async def async_setup_mock_entry(hass, aioclient_mock) -> MockConfigEntry:
    """Register API mocks and fully set up a config entry, ready to assert on."""
    mock_full_account(aioclient_mock)

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

    return entry
