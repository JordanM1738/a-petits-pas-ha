"""Tests for the config flow."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import aiohttp
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.a_petits_pas.const import (
    BASE_URL,
    CONF_ACCESS_TOKEN,
    CONF_DEVICE_ID,
    CONF_EXPIRES_AT,
    CONF_REFRESH_TOKEN,
    CONF_SAVE_PHOTOS_LOCALLY,
    CONF_SCAN_INTERVAL,
    DOMAIN,
)

from .conftest import load_fixture_json, mock_full_account


def _mock_login(aioclient_mock) -> None:
    aioclient_mock.post(f"{BASE_URL}/token", json=load_fixture_json("token_response.json"))
    aioclient_mock.get(
        f"{BASE_URL}/api/Account/cahier_description",
        json=load_fixture_json("cahier_description.json"),
    )
    # person_link unreachable -> photo resolution fails gracefully rather
    # than raising (caught by _async_resolve_child_photo_url's except clause).
    aioclient_mock.get(f"{BASE_URL}/breeze/Breeze/person_link", exc=aiohttp.ClientError())


async def test_user_flow_success(hass, aioclient_mock):
    _mock_login(aioclient_mock)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USERNAME: "parent@example.com", CONF_PASSWORD: "secret"},
    )
    assert result["step_id"] == "confirm"
    assert "ENFANT EXEMPLE" in result["description_placeholders"]["children"]

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry"
    assert result["data"][CONF_ACCESS_TOKEN] == "fake-access-token"
    assert result["data"][CONF_USERNAME] == "parent@example.com"


async def test_user_flow_invalid_auth(hass, aioclient_mock):
    aioclient_mock.post(f"{BASE_URL}/token", status=400)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USERNAME: "parent@example.com", CONF_PASSWORD: "wrong"},
    )
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_cannot_connect(hass, aioclient_mock):
    # Simulate a real network failure (not just an unmocked call, which the
    # test aiohttp mocker treats as a test-setup error rather than a network
    # error) -- surfaced as cannot_connect.
    aioclient_mock.post(f"{BASE_URL}/token", exc=aiohttp.ClientError())

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USERNAME: "parent@example.com", CONF_PASSWORD: "secret"},
    )
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_no_children_aborts_with_error(hass, aioclient_mock):
    aioclient_mock.post(f"{BASE_URL}/token", json=load_fixture_json("token_response.json"))
    aioclient_mock.get(f"{BASE_URL}/api/Account/cahier_description", json=[])

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USERNAME: "parent@example.com", CONF_PASSWORD: "secret"},
    )
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "no_children"}


async def test_duplicate_account_aborts(hass, aioclient_mock):
    MockConfigEntry(domain=DOMAIN, unique_id="parent@example.com").add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USERNAME: "parent@example.com", CONF_PASSWORD: "secret"},
    )
    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


async def test_reauth_flow_updates_existing_entry(hass, aioclient_mock):
    _mock_login(aioclient_mock)

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="parent@example.com",
        data={
            CONF_USERNAME: "parent@example.com",
            CONF_DEVICE_ID: "device-1",
            CONF_ACCESS_TOKEN: "old-token",
            CONF_REFRESH_TOKEN: "old-refresh",
            CONF_EXPIRES_AT: datetime.now(UTC).isoformat(),
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": entry.entry_id,
            "unique_id": entry.unique_id,
        },
        data=entry.data,
    )
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "new-secret"}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"

    assert entry.data[CONF_ACCESS_TOKEN] == "fake-access-token"
    assert entry.data[CONF_USERNAME] == "parent@example.com"
    # Only one entry should exist -- reauth must not create a duplicate.
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_options_flow_updates_scan_interval(hass, aioclient_mock):
    entry = await _setup_minimal_entry(hass, aioclient_mock)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 15, CONF_SAVE_PHOTOS_LOCALLY: True}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()

    assert entry.options[CONF_SCAN_INTERVAL] == 15
    assert entry.options[CONF_SAVE_PHOTOS_LOCALLY] is True


async def test_options_flow_can_disable_local_photo_saving(hass, aioclient_mock):
    entry = await _setup_minimal_entry(hass, aioclient_mock)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert CONF_SAVE_PHOTOS_LOCALLY in {str(key) for key in result["data_schema"].schema}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 5, CONF_SAVE_PHOTOS_LOCALLY: False}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()

    assert entry.options[CONF_SAVE_PHOTOS_LOCALLY] is False


async def _setup_minimal_entry(hass, aioclient_mock) -> MockConfigEntry:
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
