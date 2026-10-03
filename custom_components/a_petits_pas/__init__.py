"""The Journal à petits pas (Amisgest) integration."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AmisgestClient, AmisgestTokens
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_DEVICE_ID,
    CONF_EXPIRES_AT,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    DOMAIN,
)
from .coordinator import AmisgestDataUpdateCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.IMAGE]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Journal à petits pas from a config entry."""
    session = async_get_clientsession(hass)
    tokens = AmisgestTokens(
        access_token=entry.data[CONF_ACCESS_TOKEN],
        refresh_token=entry.data[CONF_REFRESH_TOKEN],
        expires_at=datetime.fromisoformat(entry.data[CONF_EXPIRES_AT]),
        username=entry.data[CONF_USERNAME],
    )

    def _persist_tokens(new_tokens: AmisgestTokens) -> None:
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_ACCESS_TOKEN: new_tokens.access_token,
                CONF_REFRESH_TOKEN: new_tokens.refresh_token,
                CONF_EXPIRES_AT: new_tokens.expires_at.isoformat(),
            },
        )

    client = AmisgestClient(
        session,
        device_id=entry.data[CONF_DEVICE_ID],
        tokens=tokens,
        on_tokens_updated=_persist_tokens,
    )

    coordinator = AmisgestDataUpdateCoordinator(hass, entry, client)

    scan_interval_minutes = entry.options.get(CONF_SCAN_INTERVAL)
    if scan_interval_minutes:
        coordinator.update_interval = timedelta(minutes=scan_interval_minutes)

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change (e.g. scan interval)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded
