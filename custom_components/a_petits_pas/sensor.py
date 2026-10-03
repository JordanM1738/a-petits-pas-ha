"""Sensor platform for the Journal à petits pas integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MAX_LENGTH_STATE_STATE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import Activity
from .const import DOMAIN, PRESENCE_CODES
from .coordinator import AmisgestDataUpdateCoordinator
from .entity import AmisgestChildEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up sensors, adding new ones dynamically as new children/categories appear."""
    coordinator: AmisgestDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    known_children: set[str] = set()
    known_categories: set[tuple[str, str]] = set()

    @callback
    def _async_add_new_entities() -> None:
        new_entities: list[SensorEntity] = []
        for person_id, child_data in coordinator.data.children.items():
            if person_id not in known_children:
                known_children.add(person_id)
                new_entities.append(AmisgestLatestLogbookSensor(coordinator, person_id))
                new_entities.append(AmisgestPresenceSensor(coordinator, person_id))

            if child_data.latest_entry is None:
                continue
            for activity in child_data.latest_entry.activities:
                if not activity.category:
                    continue
                key = (person_id, activity.category)
                if key not in known_categories:
                    known_categories.add(key)
                    new_entities.append(
                        AmisgestActivityCategorySensor(coordinator, person_id, activity.category)
                    )
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_entities))
    _async_add_new_entities()


def _format_time_range(activity: Activity) -> str | None:
    """Format start/end as 'HH:MM - HH:MM'.

    Amisgest returns time-only fields (e.g. nap start/end) with a fixed
    placeholder date (observed: 2017-01-01) -- only the time-of-day is
    meaningful, so this deliberately never shows the date portion.
    """
    if activity.start is not None and activity.end is not None:
        return f"{activity.start:%H:%M} - {activity.end:%H:%M}"
    if activity.end is not None:
        return f"{activity.end:%H:%M}"
    if activity.start is not None:
        return f"{activity.start:%H:%M}"
    return None


def _truncate_state(text: str) -> str:
    """Fit free-form text into HA's sensor state length limit.

    Home Assistant silently falls back a sensor's state to "unknown" when
    it's longer than `MAX_LENGTH_STATE_STATE` -- observed for real with a
    verbose educator comment on a meal activity. The full, untruncated text
    is always still available via `extra_state_attributes`.
    """
    if len(text) <= MAX_LENGTH_STATE_STATE:
        return text
    return text[: MAX_LENGTH_STATE_STATE - 1] + "…"


def _activity_dict(activity: Activity) -> dict[str, Any]:
    return {
        "category": activity.category,
        "activity_type_id": activity.activity_type_id,
        "activity_type_name": activity.activity_type_name,
        "comment": activity.comment,
        # Full ISO values are kept for automations that want them, but note
        # the date portion is a placeholder for time-only fields (see
        # _format_time_range) -- start_time/end_time below are the clean
        # HH:MM values meant for display.
        "start": activity.start.isoformat() if activity.start else None,
        "end": activity.end.isoformat() if activity.end else None,
        "start_time": f"{activity.start:%H:%M}" if activity.start else None,
        "end_time": f"{activity.end:%H:%M}" if activity.end else None,
        "numeric_value": activity.numeric_value,
        "media_ids": activity.media_ids,
        "photo_count": len(activity.media_ids),
    }


class AmisgestLatestLogbookSensor(AmisgestChildEntity, SensorEntity):
    """Generic 'latest journal entry' summary sensor.

    Always works regardless of which activity categories a given daycare
    actually uses -- state is the entry's posted date, full detail lives in
    attributes so automations/dashboards can read whatever they need.
    """

    _attr_translation_key = "latest_logbook_entry"
    _attr_icon = "mdi:notebook-outline"

    def __init__(self, coordinator: AmisgestDataUpdateCoordinator, person_id: str) -> None:
        super().__init__(coordinator, person_id)
        self._attr_unique_id = f"{coordinator.entry_id}_{person_id}_latest_entry"

    @property
    def native_value(self) -> str | None:
        child = self._child
        entry = child.latest_entry if child else None
        if entry is None or entry.posted_date is None:
            return None
        return entry.posted_date.isoformat()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        child = self._child
        if child is None:
            return {}
        entry = child.latest_entry
        if entry is None:
            # No entry for today yet (e.g. between local midnight and
            # whenever the daycare posts it) -- archive_photos still comes
            # from disk, so previously synced photos stay usable as a
            # dashboard fallback even though there's nothing "today" yet.
            return {"archive_photos": child.archive_photo_urls}
        today_urls = [p["today_url"] for p in entry.photos if "today_url" in p]
        return {
            "logbook_entry_id": entry.id,
            "activity_count": len(entry.activities),
            "activities": [_activity_dict(activity) for activity in entry.activities],
            "photo_count": len(entry.media_ids),
            "media_ids": entry.media_ids,
            "photos": today_urls,
            "archive_photos": child.archive_photo_urls,
        }


class AmisgestActivityCategorySensor(AmisgestChildEntity, SensorEntity):
    """Best-effort structured sensor for one recognized activity category
    (meal/nap/diaper/writer). Only created once that category has actually
    been observed for a given child. Once the current logbook entry no
    longer has a matching activity (e.g. the rolling 24h window has moved
    past yesterday's entry and today's hasn't been written yet), this
    reports the HA-native "unknown" state via `native_value` returning
    None -- it stays `available` as long as the coordinator itself last
    succeeded (see `AmisgestChildEntity.available`), since the source
    answered normally; there's just nothing to show yet. `available`
    must not be tied to whether today's data exists -- only to whether
    the coordinator could fetch data at all."""

    _attr_icon = "mdi:calendar-clock"

    def __init__(
        self,
        coordinator: AmisgestDataUpdateCoordinator,
        person_id: str,
        category: str,
    ) -> None:
        super().__init__(coordinator, person_id)
        self._category = category
        self._attr_translation_key = f"activity_{category}"
        self._attr_unique_id = f"{coordinator.entry_id}_{person_id}_activity_{category}"

    def _matching_activity(self) -> Activity | None:
        child = self._child
        entry = child.latest_entry if child else None
        if entry is None:
            return None
        for activity in reversed(entry.activities):
            if activity.category == self._category:
                return activity
        return None

    @property
    def native_value(self) -> str | None:
        activity = self._matching_activity()
        if activity is None:
            return None
        time_range = _format_time_range(activity)
        if time_range is not None:
            return time_range
        text = activity.comment or activity.activity_type_name
        return _truncate_state(text) if text else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        activity = self._matching_activity()
        if activity is None:
            return {}
        return _activity_dict(activity)


class AmisgestPresenceSensor(AmisgestChildEntity, SensorEntity):
    """Today's attendance/presence confirmation status.

    Reverse-engineered from api/confirmationAbs/confirmationsWithDate -- the
    exact meaning of every `code_ph` value isn't fully confirmed yet (see
    const.PRESENCE_CODES). An enum sensor so HA translates its state per
    the viewer's own language via strings.json/translations/*.json. A
    code_ph outside PRESENCE_CODES reports as HA's generic "unknown" state
    instead of erroring out -- the raw code and description stay visible as
    attributes, so nothing is truly hidden, even before PRESENCE_CODES (and
    the matching translations) are extended to cover it.
    """

    _attr_translation_key = "presence_today"
    _attr_icon = "mdi:account-check-outline"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(PRESENCE_CODES)

    def __init__(self, coordinator: AmisgestDataUpdateCoordinator, person_id: str) -> None:
        super().__init__(coordinator, person_id)
        self._attr_unique_id = f"{coordinator.entry_id}_{person_id}_presence_today"

    @property
    def available(self) -> bool:
        child = self._child
        return super().available and child is not None and child.presence is not None

    @property
    def native_value(self) -> str | None:
        child = self._child
        presence = child.presence if child else None
        if presence is None:
            return None
        code = presence.code.lower()
        return code if code in PRESENCE_CODES else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        child = self._child
        presence = child.presence if child else None
        if presence is None:
            return {}
        return {
            "code": presence.code,
            "description": presence.description,
            "answer_required": presence.answer_required,
            "response": presence.response,
            "installation": presence.installation,
        }
