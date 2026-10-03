"""Binary sensor platform for the Journal à petits pas integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import AmisgestDataUpdateCoordinator
from .entity import AmisgestChildEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: AmisgestDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    known_children: set[str] = set()

    @callback
    def _async_add_new_entities() -> None:
        new_entities = [
            AmisgestNewEntryBinarySensor(coordinator, person_id)
            for person_id in coordinator.data.children
            if person_id not in known_children
        ]
        known_children.update(coordinator.data.children.keys())
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_entities))
    _async_add_new_entities()


class AmisgestNewEntryBinarySensor(AmisgestChildEntity, BinarySensorEntity):
    """Turns on for exactly one refresh cycle when a new journal entry is detected.

    This is edge-triggered ("pulse"), not a sustained state: automations
    should react to the state change (or the a_petits_pas_new_post event
    fired at the same moment), not to it staying on.
    """

    _attr_translation_key = "new_logbook_entry"
    _attr_icon = "mdi:notebook-plus-outline"

    def __init__(self, coordinator: AmisgestDataUpdateCoordinator, person_id: str) -> None:
        super().__init__(coordinator, person_id)
        self._attr_unique_id = f"{coordinator.entry_id}_{person_id}_new_entry"

    @property
    def is_on(self) -> bool | None:
        child = self._child
        return child.has_new_content if child else None
