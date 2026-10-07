"""Image platform for the Journal à petits pas integration."""

from __future__ import annotations

from typing import Any

import homeassistant.util.dt as dt_util
from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import AmisgestDataUpdateCoordinator
from .entity import AmisgestChildEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up image entities, adding profile and journal photos per child."""
    coordinator: AmisgestDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    known_children: set[str] = set()

    @callback
    def _async_add_new_entities() -> None:
        new_entities: list[ImageEntity] = []
        for person_id in coordinator.data.children:
            if person_id not in known_children:
                known_children.add(person_id)
                new_entities.append(AmisgestChildImage(coordinator, person_id, hass))
                new_entities.append(AmisgestJournalImage(coordinator, person_id, hass))
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_entities))
    _async_add_new_entities()


class AmisgestChildImage(AmisgestChildEntity, ImageEntity):
    """The child's profile photo.

    Amisgest's photo URL (from cahier_description) needs the same Bearer
    token as the rest of the API, so it can't be handed to Lovelace as a
    plain image URL -- async_image() fetches the bytes ourselves and Home
    Assistant serves them locally through its own image proxy.
    """

    _attr_translation_key = "profile_photo"
    _attr_content_type = "image/jpeg"

    def __init__(
        self,
        coordinator: AmisgestDataUpdateCoordinator,
        person_id: str,
        hass: HomeAssistant,
    ) -> None:
        AmisgestChildEntity.__init__(self, coordinator, person_id)
        ImageEntity.__init__(self, hass)
        self._attr_unique_id = f"{coordinator.entry_id}_{person_id}_photo"
        self._last_photo_url: str | None = None
        self._update_last_seen_url()

    @property
    def available(self) -> bool:
        child = self._child
        return super().available and child is not None and child.child.photo_url is not None

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_last_seen_url()
        super()._handle_coordinator_update()

    def _update_last_seen_url(self) -> None:
        child = self._child
        url = child.child.photo_url if child else None
        if url and url != self._last_photo_url:
            self._last_photo_url = url
            self._attr_image_last_updated = dt_util.utcnow()

    async def async_image(self) -> bytes | None:
        if not self.coordinator.last_update_success:
            # Avoid hammering Amisgest with a doomed token refresh on every
            # dashboard reload while the account needs re-authentication --
            # HA's image proxy calls async_image() regardless of `available`.
            return None
        child = self._child
        if child is None or child.child.photo_url is None:
            return None
        return await self.coordinator.client.async_get_photo_bytes(
            child.child.photo_url, person_id=self._person_id
        )


class AmisgestJournalImage(AmisgestChildEntity, ImageEntity):
    """The latest photo from the child's daily journal."""

    _attr_translation_key = "journal_photo"
    _attr_content_type = "image/jpeg"

    def __init__(
        self,
        coordinator: AmisgestDataUpdateCoordinator,
        person_id: str,
        hass: HomeAssistant,
    ) -> None:
        AmisgestChildEntity.__init__(self, coordinator, person_id)
        ImageEntity.__init__(self, hass)
        self._attr_unique_id = f"{coordinator.entry_id}_{person_id}_journal_photo"
        self._last_media_id: int | None = None
        self._update_last_seen_media()

    @property
    def available(self) -> bool:
        child = self._child
        return (
            super().available
            and child is not None
            and child.latest_entry is not None
            and bool(child.latest_entry.media_ids)
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        child = self._child
        if child is None:
            return {}
        entry = child.latest_entry
        if entry is None:
            return {"archive_photos": child.archive_photo_urls}
        today_urls = [p["today_url"] for p in entry.photos if "today_url" in p]
        return {
            "logbook_entry_id": entry.id,
            "photo_count": len(entry.media_ids),
            "media_ids": entry.media_ids,
            "photos": today_urls,
            "archive_photos": child.archive_photo_urls,
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_last_seen_media()
        super()._handle_coordinator_update()

    def _update_last_seen_media(self) -> None:
        child = self._child
        entry = child.latest_entry if child else None
        primary_media_id = entry.media_ids[0] if entry and entry.media_ids else None
        if primary_media_id and primary_media_id != self._last_media_id:
            self._last_media_id = primary_media_id
            self._attr_image_last_updated = dt_util.utcnow()

    async def async_image(self) -> bytes | None:
        if not self.coordinator.last_update_success:
            # See AmisgestChildImage.async_image: the image proxy calls this
            # regardless of `available`, so skip the doomed token refresh.
            return None
        child = self._child
        entry = child.latest_entry if child else None
        if not entry or not entry.media_ids:
            return None
        primary_media_id = entry.media_ids[0]
        return await self.coordinator.client.async_get_media_bytes(
            primary_media_id, person_id=self._person_id
        )
