"""DataUpdateCoordinator for the Journal à petits pas integration."""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import homeassistant.util.dt as dt_util
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    AmisgestApiError,
    AmisgestAuthError,
    AmisgestClient,
    ChildLink,
    LogbookEntry,
    PresenceStatus,
)
from .const import (
    CONF_SAVE_PHOTOS_LOCALLY,
    DEFAULT_SAVE_PHOTOS_LOCALLY,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    EVENT_NEW_LOGBOOK_ENTRY,
    PHOTO_DIR_ARCHIVE,
    PHOTO_DIR_TODAY,
)

_LOGGER = logging.getLogger(__name__)

_LAST_SEEN_ENTRY_STORAGE_VERSION = 1


def _list_archive_photo_filenames(archive_dir: Path) -> list[str]:
    """List every saved photo in the archive directory, newest first.

    Reads the filesystem directly rather than trusting whatever the latest
    logbook_entry happens to contain -- Amisgest only ever returns "today's"
    entry, so between local midnight and whenever the daycare posts the new
    entry there is no latest_entry at all, even though every previous day's
    photos are still sitting on disk. Filenames are `<date>_<media_id>.jpg`,
    so a reverse lexicographic sort is also a reverse chronological sort.
    """
    if not archive_dir.is_dir():
        return []
    return sorted((f.name for f in archive_dir.iterdir() if f.suffix == ".jpg"), reverse=True)


def _check_archive_files_exist(
    archive_dir: Path, entry_date: str, media_ids: list[int]
) -> dict[int, bool]:
    """Check which photos already exist in the archive directory."""
    archive_dir.mkdir(parents=True, exist_ok=True)
    return {m_id: (archive_dir / f"{entry_date}_{m_id}.jpg").exists() for m_id in media_ids}


def _sync_photos_disk(
    today_dir: Path,
    archive_dir: Path,
    is_new_entry: bool,
    entry_date: str,
    fetched_photos: list[tuple[int, bytes]],
) -> None:
    """Synchronize photos on disk for today and the permanent archive."""
    today_dir.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)

    if is_new_entry:
        for f in today_dir.iterdir():
            if f.is_file():
                f.unlink()

    for media_id, image_bytes in fetched_photos:
        archive_file = archive_dir / f"{entry_date}_{media_id}.jpg"
        today_file = today_dir / f"{media_id}.jpg"

        if image_bytes:
            if not archive_file.exists():
                archive_file.write_bytes(image_bytes)
            if not today_file.exists():
                today_file.write_bytes(image_bytes)
        elif archive_file.exists() and not today_file.exists():
            shutil.copy2(archive_file, today_file)


@dataclass
class ChildData:
    """Latest known state for one child."""

    child: ChildLink
    latest_entry: LogbookEntry | None = None
    new_inbox_count: int = 0
    has_new_content: bool = False
    presence: PresenceStatus | None = None
    # Populated from disk (see `_list_archive_photo_filenames`), independent
    # of whether `latest_entry` exists -- so archived photos stay available
    # even on days when today's journal hasn't been posted yet.
    archive_photo_urls: list[str] = field(default_factory=list)


@dataclass
class AmisgestData:
    """All children's data for one config entry."""

    children: dict[str, ChildData] = field(default_factory=dict)


class AmisgestDataUpdateCoordinator(DataUpdateCoordinator[AmisgestData]):
    """Fetches data for every child linked to one Amisgest login, once per interval."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: AmisgestClient,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.client = client
        self._entry = entry
        self.entry_id: str = entry.entry_id
        # Tracks the last logbook_entry id we already fired an event for,
        # per child, so `has_new_content` only pulses once per new entry.
        # Persisted across HA restarts/reloads (see `_async_load_last_seen`)
        # -- otherwise every restart forgets it, and the first entry seen
        # afterwards is wrongly treated as a "baseline" instead of a pulse,
        # even when it's a genuinely new entry (e.g. one posted while HA was
        # down, or right after a restart when the previous entry had
        # already aged out of the 24h lookback window).
        self._last_seen_entry_id: dict[str, int] = {}
        self._last_seen_store: Store[dict[str, int]] = Store(
            hass, _LAST_SEEN_ENTRY_STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}_last_seen_entry"
        )
        self._last_seen_loaded = False
        self._last_synced_entry_id: dict[str, int] = {}
        self._save_photos_locally: bool = entry.options.get(
            CONF_SAVE_PHOTOS_LOCALLY, DEFAULT_SAVE_PHOTOS_LOCALLY
        )

    async def _async_load_last_seen(self) -> None:
        if self._last_seen_loaded:
            return
        stored = await self._last_seen_store.async_load()
        if stored:
            self._last_seen_entry_id.update(stored)
        self._last_seen_loaded = True

    async def _async_update_data(self) -> AmisgestData:
        await self._async_load_last_seen()
        try:
            children = await self.client.async_get_cahier_description()
        except AmisgestAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except AmisgestApiError as err:
            raise UpdateFailed(f"Could not list children: {err}") from err

        results = await asyncio.gather(
            *(self._async_fetch_child(child) for child in children),
            return_exceptions=True,
        )

        child_data: dict[str, ChildData] = {}
        for child, result in zip(children, results, strict=True):
            if isinstance(result, AmisgestAuthError):
                raise ConfigEntryAuthFailed(str(result))
            if isinstance(result, AmisgestApiError):
                _LOGGER.warning("Could not refresh data for %s: %s", child.person_name, result)
                previous = self.data.children.get(child.person_id) if self.data else None
                child_data[child.person_id] = previous or ChildData(child=child)
                continue
            if isinstance(result, Exception):
                raise result
            assert isinstance(result, ChildData)
            child_data[child.person_id] = result

        return AmisgestData(children=child_data)

    async def _async_fetch_child(self, child: ChildLink) -> ChildData:
        # `api/wall/newInboxCount` was assumed to be a proxy for "new
        # logbook_entry exists" but a real capture showed it staying at 0
        # while a brand new logbook_entry (with a genuinely new, unread
        # message_sent) was posted -- it does NOT reliably signal new
        # journal content, so it's fetched only for diagnostics and no
        # longer gates whether we fetch the latest entry.
        inbox_count = await self.client.async_get_new_inbox_count(child.person_id)

        latest_entry = await self.client.async_get_latest_logbook_entry(child.person_id)
        has_new_content = False
        if latest_entry is not None:
            if self._save_photos_locally:
                await self._async_sync_child_photos(child, latest_entry)
            last_seen_id = self._last_seen_entry_id.get(child.person_id)
            first_run = last_seen_id is None
            if latest_entry.posted_date is not None and last_seen_id != latest_entry.id:
                has_new_content = not first_run
                self._last_seen_entry_id[child.person_id] = latest_entry.id
                await self._last_seen_store.async_save(self._last_seen_entry_id)
                if has_new_content:
                    self._fire_new_entry_event(child, latest_entry)

        try:
            presence = await self.client.async_get_presence_today(child.person_id)
        except AmisgestApiError as err:
            _LOGGER.debug("Could not fetch presence for %s: %s", child.person_name, err)
            previous = self.data.children.get(child.person_id) if self.data else None
            presence = previous.presence if previous else None

        archive_photo_urls = (
            await self._async_list_archive_photo_urls(child) if self._save_photos_locally else []
        )

        return ChildData(
            child=child,
            latest_entry=latest_entry,
            new_inbox_count=inbox_count,
            has_new_content=has_new_content,
            presence=presence,
            archive_photo_urls=archive_photo_urls,
        )

    async def _async_list_archive_photo_urls(self, child: ChildLink) -> list[str]:
        """Build /local/ URLs for every photo currently saved in child's archive/ folder."""
        archive_dir = Path(
            self.hass.config.path("www", "a_petits_pas", str(child.person_id), PHOTO_DIR_ARCHIVE)
        )
        filenames = await self.hass.async_add_executor_job(
            _list_archive_photo_filenames, archive_dir
        )
        return [
            f"/local/a_petits_pas/{child.person_id}/{PHOTO_DIR_ARCHIVE}/{filename}"
            for filename in filenames
        ]

    async def _async_sync_child_photos(self, child: ChildLink, latest_entry: LogbookEntry) -> None:
        """Sync photos for the current logbook entry into today/ and archive/ folders."""
        base_dir = Path(self.hass.config.path("www", "a_petits_pas", str(child.person_id)))
        today_dir = base_dir / PHOTO_DIR_TODAY
        archive_dir = base_dir / PHOTO_DIR_ARCHIVE

        last_synced = self._last_synced_entry_id.get(child.person_id)
        is_new_entry = last_synced != latest_entry.id
        self._last_synced_entry_id[child.person_id] = latest_entry.id

        entry_date = (
            latest_entry.posted_date.date().isoformat()
            if latest_entry.posted_date
            else dt_util.now().date().isoformat()
        )

        existing_in_archive: dict[int, bool] = await self.hass.async_add_executor_job(
            _check_archive_files_exist, archive_dir, entry_date, latest_entry.media_ids
        )

        fetched_photos: list[tuple[int, bytes]] = []
        for media_id in latest_entry.media_ids:
            if not existing_in_archive.get(media_id, False):
                photo_bytes = await self.client.async_get_media_bytes(
                    media_id, person_id=child.person_id
                )
                if photo_bytes:
                    fetched_photos.append((media_id, photo_bytes))
            else:
                fetched_photos.append((media_id, b""))

        await self.hass.async_add_executor_job(
            _sync_photos_disk,
            today_dir,
            archive_dir,
            is_new_entry,
            entry_date,
            fetched_photos,
        )

        latest_entry.photos = [
            {
                "media_id": m_id,
                "today_url": f"/local/a_petits_pas/{child.person_id}/{PHOTO_DIR_TODAY}/{m_id}.jpg",
                "archive_url": f"/local/a_petits_pas/{child.person_id}/{PHOTO_DIR_ARCHIVE}/{entry_date}_{m_id}.jpg",
            }
            for m_id in latest_entry.media_ids
        ]

    def _fire_new_entry_event(self, child: ChildLink, entry: LogbookEntry) -> None:
        today_urls = [p["today_url"] for p in entry.photos if "today_url" in p]
        self.hass.bus.async_fire(
            EVENT_NEW_LOGBOOK_ENTRY,
            {
                "person_id": child.person_id,
                "child_name": child.person_name,
                "logbook_entry_id": entry.id,
                "posted_date": entry.posted_date.isoformat() if entry.posted_date else None,
                "activity_count": len(entry.activities),
                "photo_count": len(entry.media_ids),
                "photos": today_urls,
            },
        )
