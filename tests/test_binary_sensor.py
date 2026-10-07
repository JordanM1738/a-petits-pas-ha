"""Tests for the binary_sensor platform, via a full config entry setup."""

from __future__ import annotations

from homeassistant.helpers import entity_registry as er

from custom_components.a_petits_pas.const import DOMAIN
from custom_components.a_petits_pas.coordinator import AmisgestData, ChildData

from .conftest import async_setup_mock_entry

PERSON_ID = "1000001"


async def test_binary_sensor_off_on_first_setup(hass, aioclient_mock):
    entry = await async_setup_mock_entry(hass, aioclient_mock)

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "binary_sensor", DOMAIN, f"{entry.entry_id}_{PERSON_ID}_new_entry"
    )
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "off"


async def test_binary_sensor_pulses_on_then_clears(hass, aioclient_mock):
    """Simulate the coordinator detecting a new entry, then the following
    cycle where it's already been accounted for -- verifies the entity
    reacts to has_new_content directly, independent of network timing."""
    entry = await async_setup_mock_entry(hass, aioclient_mock)
    coordinator = hass.data[DOMAIN][entry.entry_id]

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "binary_sensor", DOMAIN, f"{entry.entry_id}_{PERSON_ID}_new_entry"
    )

    child = coordinator.data.children[PERSON_ID].child
    latest_entry = coordinator.data.children[PERSON_ID].latest_entry

    coordinator.async_set_updated_data(
        AmisgestData(
            children={
                PERSON_ID: ChildData(
                    child=child,
                    latest_entry=latest_entry,
                    new_inbox_count=1,
                    has_new_content=True,
                )
            }
        )
    )
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "on"

    coordinator.async_set_updated_data(
        AmisgestData(
            children={
                PERSON_ID: ChildData(
                    child=child,
                    latest_entry=latest_entry,
                    new_inbox_count=1,
                    has_new_content=False,
                )
            }
        )
    )
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "off"
