"""Diagnostics support for Journal à petits pas."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_ACCESS_TOKEN, CONF_DEVICE_ID, CONF_REFRESH_TOKEN, DOMAIN
from .coordinator import AmisgestDataUpdateCoordinator

TO_REDACT = {
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_DEVICE_ID,
    "username",
    "person_id",
    "owner_person_id",
    "writer_person_id",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry, with tokens/PII redacted."""
    coordinator: AmisgestDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    children_data = {
        person_id: {
            "client_name": data.child.client_name,
            "new_inbox_count": data.new_inbox_count,
            "has_new_content": data.has_new_content,
            "latest_entry_id": data.latest_entry.id if data.latest_entry else None,
            "activity_count": len(data.latest_entry.activities) if data.latest_entry else 0,
            "presence_code": data.presence.code if data.presence else None,
            "has_photo_url": data.child.photo_url is not None,
        }
        for person_id, data in coordinator.data.children.items()
    }

    return {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "entry_options": dict(entry.options),
        "children": async_redact_data(children_data, TO_REDACT),
    }
