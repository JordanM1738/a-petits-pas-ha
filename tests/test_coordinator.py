"""Tests for AmisgestDataUpdateCoordinator, using a fake API client (no HTTP)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.a_petits_pas.api import (
    AmisgestApiError,
    AmisgestAuthError,
    ChildLink,
    LogbookEntry,
    PresenceStatus,
)
from custom_components.a_petits_pas.const import (
    CONF_SAVE_PHOTOS_LOCALLY,
    DOMAIN,
    EVENT_NEW_LOGBOOK_ENTRY,
)
from custom_components.a_petits_pas.coordinator import AmisgestDataUpdateCoordinator


def _entry(entry_id: int) -> LogbookEntry:
    return LogbookEntry(
        id=entry_id,
        owner_person_id="1",
        posted_date=datetime.now(UTC),
        status_id=2,
        activities=[],
    )


def _mock_entry(hass) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    return entry


class FakeClient:
    """Duck-types the subset of AmisgestClient the coordinator calls."""

    def __init__(self, children: list[ChildLink]) -> None:
        self.children = children
        self.inbox_counts: dict[str, int] = {c.person_id: 0 for c in children}
        self.entries: dict[str, LogbookEntry | None] = {c.person_id: None for c in children}
        self.raise_on_inbox: dict[str, Exception] = {}

    async def async_get_cahier_description(self) -> list[ChildLink]:
        return self.children

    async def async_get_new_inbox_count(self, person_id: str) -> int:
        if person_id in self.raise_on_inbox:
            raise self.raise_on_inbox[person_id]
        return self.inbox_counts[person_id]

    async def async_get_latest_logbook_entry(self, person_id: str, since=None):
        return self.entries[person_id]

    async def async_get_presence_today(self, person_id: str) -> PresenceStatus | None:
        return None

    async def async_get_media_bytes(
        self, media_id: int, *, person_id: str | None = None
    ) -> bytes | None:
        return b"fake-media-bytes"


class FailingCahierClient:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def async_get_cahier_description(self):
        raise self._error


async def test_first_refresh_populates_children_without_pulsing(hass):
    child = ChildLink(person_id="1", client_id="10", client_name="CPE", person_name="Enfant")
    client = FakeClient([child])
    client.entries["1"] = _entry(100)
    entry = _mock_entry(hass)
    coordinator = AmisgestDataUpdateCoordinator(hass, entry, client)

    await coordinator.async_refresh()

    data = coordinator.data.children["1"]
    assert data.latest_entry.id == 100
    assert data.has_new_content is False  # never pulses on first run


async def test_new_entry_pulses_once_then_clears(hass):
    child = ChildLink(person_id="1", client_id="10", client_name="CPE", person_name="Enfant")
    client = FakeClient([child])
    client.entries["1"] = _entry(100)
    entry = _mock_entry(hass)
    coordinator = AmisgestDataUpdateCoordinator(hass, entry, client)
    await coordinator.async_refresh()

    events = []
    hass.bus.async_listen(EVENT_NEW_LOGBOOK_ENTRY, lambda e: events.append(e))

    # newInboxCount deliberately stays at 0 here: a real capture showed it
    # NOT incrementing for a genuinely new entry, so detection must not
    # depend on it changing.
    client.entries["1"] = _entry(101)
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.data.children["1"].has_new_content is True
    assert coordinator.data.children["1"].latest_entry.id == 101
    assert len(events) == 1
    assert events[0].data["logbook_entry_id"] == 101

    # Same entry on the next cycle -> pulse clears.
    await coordinator.async_refresh()
    assert coordinator.data.children["1"].has_new_content is False
    assert coordinator.data.children["1"].latest_entry.id == 101


async def test_unposted_draft_entry_does_not_pulse_or_fire_event(hass):
    """An unposted draft entry (posted_date is None) must not pulse or fire an event
    until it is actually posted with a valid date."""
    child = ChildLink(person_id="1", client_id="10", client_name="CPE", person_name="Enfant")
    client = FakeClient([child])
    client.entries["1"] = _entry(100)
    entry = _mock_entry(hass)
    coordinator = AmisgestDataUpdateCoordinator(hass, entry, client)
    await coordinator.async_refresh()

    events = []
    hass.bus.async_listen(EVENT_NEW_LOGBOOK_ENTRY, lambda e: events.append(e))

    # Draft entry arrives without a posted_date (e.g. at 13:15)
    draft_entry = LogbookEntry(
        id=68654761,
        owner_person_id="1",
        posted_date=None,
        status_id=1,
        activities=[],
    )
    client.entries["1"] = draft_entry
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    # Draft must not pulse or fire event
    assert coordinator.data.children["1"].has_new_content is False
    assert len(events) == 0

    # Later (e.g. at 13:25), daycare posts the entry with a real date
    posted_entry = LogbookEntry(
        id=68654761,
        owner_person_id="1",
        posted_date=datetime.now(UTC),
        status_id=2,
        activities=[],
    )
    client.entries["1"] = posted_entry
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    # Now that it has a posted_date, it pulses and fires the event
    assert coordinator.data.children["1"].has_new_content is True
    assert coordinator.data.children["1"].latest_entry.id == 68654761
    assert len(events) == 1
    assert events[0].data["logbook_entry_id"] == 68654761


async def test_last_seen_entry_id_persists_across_restart(hass):
    """A fresh coordinator instance (simulating an HA restart/reload) must
    still pulse for a genuinely new entry, instead of silently adopting it
    as a new baseline -- see CHANGELOG for the real bug this pins."""
    child = ChildLink(person_id="1", client_id="10", client_name="CPE", person_name="Enfant")
    entry = _mock_entry(hass)

    client1 = FakeClient([child])
    client1.entries["1"] = _entry(100)
    coordinator1 = AmisgestDataUpdateCoordinator(hass, entry, client1)
    await coordinator1.async_refresh()
    assert coordinator1.data.children["1"].has_new_content is False

    # New coordinator instance, same config entry -- simulates HA having
    # restarted, wiping any in-memory-only state.
    client2 = FakeClient([child])
    client2.entries["1"] = _entry(101)
    coordinator2 = AmisgestDataUpdateCoordinator(hass, entry, client2)

    events = []
    hass.bus.async_listen(EVENT_NEW_LOGBOOK_ENTRY, lambda e: events.append(e))

    await coordinator2.async_refresh()
    await hass.async_block_till_done()

    assert coordinator2.data.children["1"].has_new_content is True
    assert len(events) == 1
    assert events[0].data["logbook_entry_id"] == 101


async def test_auth_error_maps_to_config_entry_auth_failed(hass):
    entry = _mock_entry(hass)
    client = FailingCahierClient(AmisgestAuthError("bad token"))
    coordinator = AmisgestDataUpdateCoordinator(hass, entry, client)

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_api_error_maps_to_update_failed(hass):
    entry = _mock_entry(hass)
    client = FailingCahierClient(AmisgestApiError("boom"))
    coordinator = AmisgestDataUpdateCoordinator(hass, entry, client)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_one_child_failing_does_not_break_others(hass):
    good = ChildLink(person_id="good", client_id="1", client_name="CPE", person_name="Bon")
    bad = ChildLink(person_id="bad", client_id="1", client_name="CPE", person_name="Mauvais")
    client = FakeClient([good, bad])
    client.raise_on_inbox["bad"] = AmisgestApiError("child endpoint down")
    entry = _mock_entry(hass)
    coordinator = AmisgestDataUpdateCoordinator(hass, entry, client)

    await coordinator.async_refresh()

    assert "good" in coordinator.data.children
    assert "bad" in coordinator.data.children
    assert coordinator.data.children["bad"].latest_entry is None


async def test_sync_photos_today_and_archive_and_event(hass, tmp_path, monkeypatch):
    monkeypatch.setattr(hass.config, "path", lambda *args: str(tmp_path.joinpath(*args)))

    child = ChildLink(person_id="1", client_id="10", client_name="CPE", person_name="Enfant")
    client = FakeClient([child])
    entry1 = _entry(100)
    entry1.media_ids = [43976930]
    client.entries["1"] = entry1

    config_entry = _mock_entry(hass)
    coordinator = AmisgestDataUpdateCoordinator(hass, config_entry, client)

    events = []
    hass.bus.async_listen(EVENT_NEW_LOGBOOK_ENTRY, events.append)

    # First run: baseline (no event fired on first run)
    await coordinator.async_refresh()

    today_file = tmp_path / "www" / "a_petits_pas" / "1" / "today" / "43976930.jpg"
    assert today_file.exists()
    assert today_file.read_bytes() == b"fake-media-bytes"

    archive_dir = tmp_path / "www" / "a_petits_pas" / "1" / "archive"
    archive_files = list(archive_dir.glob("*.jpg"))
    assert len(archive_files) == 1
    assert "43976930" in archive_files[0].name

    assert len(coordinator.data.children["1"].latest_entry.photos) == 1
    assert "today_url" in coordinator.data.children["1"].latest_entry.photos[0]

    # Second entry arrives (new day / new entry_id with no photos)
    entry2 = _entry(101)
    entry2.media_ids = []
    client.entries["1"] = entry2

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    # today/ should now be empty (yesterday's photo cleaned up!)
    assert not today_file.exists()
    # But archive/ still holds the archived photo!
    assert len(list(archive_dir.glob("*.jpg"))) == 1

    # Event should have fired with photo_count=0
    assert len(events) == 1
    assert events[0].data["logbook_entry_id"] == 101
    assert events[0].data["photo_count"] == 0
    assert events[0].data["photos"] == []


async def test_archive_photos_available_when_no_latest_entry(hass, tmp_path, monkeypatch):
    """Between local midnight and the daycare posting today's entry, Amisgest
    returns no logbook_entry at all -- archive_photo_urls must still reflect
    what's actually on disk instead of going empty along with latest_entry."""
    monkeypatch.setattr(hass.config, "path", lambda *args: str(tmp_path.joinpath(*args)))

    child = ChildLink(person_id="1", client_id="10", client_name="CPE", person_name="Enfant")
    client = FakeClient([child])
    entry1 = _entry(100)
    entry1.media_ids = [43976930]
    client.entries["1"] = entry1

    config_entry = _mock_entry(hass)
    coordinator = AmisgestDataUpdateCoordinator(hass, config_entry, client)

    await coordinator.async_refresh()
    first_run_urls = coordinator.data.children["1"].archive_photo_urls
    assert len(first_run_urls) == 1
    assert "43976930" in first_run_urls[0]

    # Next day: no entry for today yet.
    client.entries["1"] = None
    await coordinator.async_refresh()

    assert coordinator.data.children["1"].latest_entry is None
    archive_urls = coordinator.data.children["1"].archive_photo_urls
    assert len(archive_urls) == 1
    assert "43976930" in archive_urls[0]
    assert archive_urls[0].startswith("/local/a_petits_pas/1/archive/")


async def test_save_photos_locally_disabled_skips_disk_and_archive_listing(
    hass, tmp_path, monkeypatch
):
    monkeypatch.setattr(hass.config, "path", lambda *args: str(tmp_path.joinpath(*args)))

    child = ChildLink(person_id="1", client_id="10", client_name="CPE", person_name="Enfant")
    client = FakeClient([child])
    entry1 = _entry(100)
    entry1.media_ids = [43976930]
    client.entries["1"] = entry1

    config_entry = MockConfigEntry(domain=DOMAIN, options={CONF_SAVE_PHOTOS_LOCALLY: False})
    config_entry.add_to_hass(hass)
    coordinator = AmisgestDataUpdateCoordinator(hass, config_entry, client)

    await coordinator.async_refresh()

    assert not (tmp_path / "www" / "a_petits_pas").exists()
    assert coordinator.data.children["1"].latest_entry.photos == []
    assert coordinator.data.children["1"].archive_photo_urls == []
