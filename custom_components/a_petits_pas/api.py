"""API client for the private Amisgest ("Journal à petits pas") service.

This is a reverse-engineered, unofficial client built from a HAR capture of
the app.journalapetitspas.ca web app. See the project README for the
"unofficial API" disclaimer. Do not add anything here that spoofs the
official app's identity or evades rate limiting -- device_info deliberately
identifies this as a Home Assistant integration.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import aiohttp
import homeassistant.util.dt as dt_util

from .const import (
    API_VERSION,
    BASE_URL,
    CLIENT_ID,
    CONFIRMATION_ABS_PATH,
    DEVICE_INFO,
    KNOWN_ACTIVITY_TYPE_CATEGORIES,
)

_LOGGER = logging.getLogger(__name__)

# Refresh the access token this long before it actually expires, so a
# request never races an expiry boundary.
_REFRESH_SAFETY_MARGIN = timedelta(minutes=5)


class AmisgestError(Exception):
    """Base error for the Amisgest API client."""


class AmisgestAuthError(AmisgestError):
    """Credentials or refresh token were rejected."""


class AmisgestApiError(AmisgestError):
    """Any other API/transport/parsing failure."""


@dataclass
class AmisgestTokens:
    """A login/refresh token pair plus its computed expiry."""

    access_token: str
    refresh_token: str
    expires_at: datetime
    username: str = ""


@dataclass
class ChildLink:
    """A child (and the daycare/client) linked to the logged-in account."""

    person_id: str
    client_id: str
    client_name: str
    person_name: str
    photo_url: str | None = None


@dataclass
class Activity:
    """One structured entry (repas/sieste/couche/...) within a logbook_entry."""

    id: int
    activity_type_id: int | None
    category: str | None
    activity_type_name: str | None
    comment: str
    start: datetime | None
    end: datetime | None
    numeric_value: int | None
    writer_person_id: int | None
    media_ids: list[int] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class PresenceStatus:
    """A child's attendance/presence confirmation status for one day.

    `code` (the raw `code_ph`) is the source of truth -- its localized
    label (HA-system-language-aware, e.g. via const.presence_label) must be
    computed where the entity has access to `hass`, not here.
    """

    code: str
    description: str | None
    answer_required: bool
    response: str | None
    installation: str | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class LogbookEntry:
    """A day's journal entry for one child, with its list of activities."""

    id: int
    owner_person_id: str
    posted_date: datetime | None
    status_id: int | None
    activities: list[Activity] = field(default_factory=list)
    media_ids: list[int] = field(default_factory=list)
    photos: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


# Amisgest returns `logbook_entry.posted_date` as a fixed sentinel
# (observed: 1900-01-01) while an entry has been created but not yet
# "posted"/finalized by daycare staff -- unlike activity.start/end's
# placeholder date (2017-01-01, where only the time-of-day matters), this
# field is a pure date and the whole value is meaningless while unset. Any
# year this old can't be a real logbook entry, so treat it as "not set yet".
_POSTED_DATE_SENTINEL_YEAR_CUTOFF = 2000


def _parse_posted_date(value: str | None) -> datetime | None:
    parsed = _parse_datetime(value)
    if parsed is not None and parsed.year < _POSTED_DATE_SENTINEL_YEAR_CUTOFF:
        return None
    return parsed


TokenUpdateCallback = Callable[[AmisgestTokens], Awaitable[None] | None]


class AmisgestClient:
    """Thin async client for the Amisgest private API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        device_id: str,
        tokens: AmisgestTokens,
        on_tokens_updated: TokenUpdateCallback | None = None,
    ) -> None:
        self._session = session
        self._device_id = device_id
        self._tokens = tokens
        self._on_tokens_updated = on_tokens_updated
        self._refresh_lock = asyncio.Lock()
        self._activity_type_names: dict[int, str] | None = None
        self._photo_url_cache: dict[str, str | None] = {}

    @property
    def tokens(self) -> AmisgestTokens:
        return self._tokens

    # ---- Auth ---------------------------------------------------------

    @staticmethod
    def new_device_id() -> str:
        return str(uuid.uuid4())

    @classmethod
    async def async_login(
        cls,
        session: aiohttp.ClientSession,
        *,
        email: str,
        password: str,
        device_id: str,
    ) -> AmisgestTokens:
        """Exchange email/password for a token pair (OAuth2 password grant)."""
        data = {
            "grant_type": "password",
            "username": email,
            "password": password,
            "client_id": CLIENT_ID,
            "v": API_VERSION,
            "device_id": device_id,
            "device_info": DEVICE_INFO,
        }
        return await cls._async_token_request(session, data)

    @classmethod
    async def _async_token_request(
        cls, session: aiohttp.ClientSession, data: dict[str, str]
    ) -> AmisgestTokens:
        try:
            async with session.post(
                f"{BASE_URL}/token",
                data=data,
                headers={"Accept": "application/json"},
            ) as resp:
                if resp.status in (400, 401):
                    raise AmisgestAuthError(
                        f"Amisgest rejected the credentials/refresh token (HTTP {resp.status})"
                    )
                if resp.status != 200:
                    text = await resp.text()
                    raise AmisgestApiError(
                        f"Unexpected status {resp.status} from token endpoint: {text[:200]}"
                    )
                payload = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise AmisgestApiError(f"Network error contacting Amisgest: {err}") from err

        try:
            expires_in = int(payload["expires_in"])
            return AmisgestTokens(
                access_token=payload["access_token"],
                refresh_token=payload["refresh_token"],
                expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
                username=payload.get("userName", ""),
            )
        except (KeyError, ValueError, TypeError) as err:
            raise AmisgestApiError(f"Malformed token response: {err}") from err

    async def _async_ensure_token(self) -> None:
        """Refresh the access token if it's expired or about to expire."""
        if datetime.now(UTC) < self._tokens.expires_at - _REFRESH_SAFETY_MARGIN:
            return
        async with self._refresh_lock:
            # Another concurrent caller may have refreshed while we waited.
            if datetime.now(UTC) < self._tokens.expires_at - _REFRESH_SAFETY_MARGIN:
                return
            data = {
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "refresh_token": self._tokens.refresh_token,
                "v": API_VERSION,
                "device_id": self._device_id,
                "device_info": DEVICE_INFO,
            }
            self._tokens = await self._async_token_request(self._session, data)
            if self._on_tokens_updated is not None:
                result = self._on_tokens_updated(self._tokens)
                if result is not None:
                    await result

    # ---- Low-level request helpers -------------------------------------

    @staticmethod
    def _decode_body(raw_text: str) -> Any:
        """Decode a response body.

        Observed across two separate HAR captures: the SAME endpoint
        (api/wall/wallposts) sometimes returns plain JSON and sometimes a
        JSON string whose value is a base64-encoded JSON payload. This
        auto-detects rather than assuming one format per endpoint.
        """
        if not raw_text:
            return None
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError as err:
            raise AmisgestApiError(f"Response body is not valid JSON: {err}") from err

        if isinstance(parsed, str):
            try:
                decoded_bytes = base64.b64decode(parsed)
                return json.loads(decoded_bytes)
            except Exception as err:  # noqa: BLE001 - any decode failure is fatal here
                raise AmisgestApiError(
                    f"Response body was a string but not valid base64-JSON: {err}"
                ) from err
        return parsed

    async def _async_request(
        self,
        method: str,
        path: str,
        *,
        person_id: str | None = None,
        params: dict[str, str] | None = None,
    ) -> Any:
        await self._async_ensure_token()
        url = f"{BASE_URL}{path}"
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Authorization": f"Bearer {self._tokens.access_token}",
        }
        if person_id is not None:
            headers["personid"] = str(person_id)

        try:
            async with self._session.request(method, url, params=params, headers=headers) as resp:
                if resp.status == 401:
                    raise AmisgestAuthError(f"Amisgest rejected the access token calling {path}")
                if resp.status >= 400:
                    text = await resp.text()
                    raise AmisgestApiError(f"HTTP {resp.status} calling {path}: {text[:200]}")
                raw_text = await resp.text()
        except aiohttp.ClientError as err:
            raise AmisgestApiError(f"Network error calling {path}: {err}") from err

        return self._decode_body(raw_text)

    # ---- High-level calls -----------------------------------------------

    async def async_get_cahier_description(self) -> list[ChildLink]:
        """List the children/daycares linked to the logged-in account."""
        data = await self._async_request("GET", "/api/Account/cahier_description")
        raw_children: list[tuple[str, str, str, str]] = []
        for entry in data or []:
            person_id = entry.get("personId")
            if person_id is None:
                continue
            user_data = entry.get("userData") or {}
            raw_children.append(
                (
                    str(person_id),
                    str(entry.get("clientId", "")),
                    entry.get("clientName") or "",
                    user_data.get("linkedPersons") or entry.get("personName") or "",
                )
            )

        photo_urls = await asyncio.gather(
            *(self._async_resolve_child_photo_url(person_id) for person_id, *_ in raw_children)
        )

        return [
            ChildLink(
                person_id=person_id,
                client_id=client_id,
                client_name=client_name,
                person_name=person_name,
                photo_url=photo_url,
            )
            for (person_id, client_id, client_name, person_name), photo_url in zip(
                raw_children, photo_urls, strict=True
            )
        ]

    async def _async_resolve_child_photo_url(self, person_id: str) -> str | None:
        """Resolve the CHILD's own profile photo URL.

        `personId` in cahier_description (and the `personid` request header
        used on every other call) identifies the parent-child access link,
        not the child itself -- `userData.photoUrl` from that same response
        actually points at the logged-in PARENT's photo (confirmed by
        matching exact photo bytes across a HAR capture). The real child
        photo requires:
        1. `person_link` filtered on `parent_person_id eq <our link id>` to
           get the child's own `child_person_id`.
        2. `GetAllPhotoGuid` (a roster of everyone at that daycare with a
           photo) to find that `child_person_id`'s `guid`.
        3. `getPhotoPersonWGuid?id=<guid>&size=Normal&id2=<our link id>`.
        Cached after first resolution -- the guid doesn't change often and
        GetAllPhotoGuid returns a full roster, not worth re-fetching every
        coordinator cycle.
        """
        if person_id in self._photo_url_cache:
            return self._photo_url_cache[person_id]

        photo_url: str | None = None
        try:
            links = await self._async_request(
                "GET",
                "/breeze/Breeze/person_link",
                person_id=person_id,
                params={"$filter": f"parent_person_id eq {person_id}"},
            )
            child_person_id = (links or [{}])[0].get("child_person_id")
            if child_person_id is not None:
                roster = await self._async_request(
                    "GET", "/api/photo/GetAllPhotoGuid", person_id=person_id
                )
                guid = next(
                    (
                        item.get("guid")
                        for item in (roster or [])
                        if item.get("personId") == child_person_id
                    ),
                    None,
                )
                if guid:
                    photo_url = (
                        f"{BASE_URL}/api/Photo/getPhotoPersonWGuid"
                        f"?id={guid}&size=Normal&id2={person_id}"
                    )
        except AmisgestApiError:
            _LOGGER.debug("Could not resolve child photo for %s", person_id, exc_info=True)

        self._photo_url_cache[person_id] = photo_url
        return photo_url

    async def async_get_activity_type_names(self, *, force_refresh: bool = False) -> dict[int, str]:
        """Return the activity_type_id -> name dictionary, cached after first fetch.

        This is only used for a human-readable label on structured sensors;
        category detection (which sensor an activity maps to) uses the
        id-based KNOWN_ACTIVITY_TYPE_CATEGORIES table in const.py, so a
        failure here degrades to an unnamed-but-still-categorized sensor
        rather than breaking anything.
        """
        if self._activity_type_names is not None and not force_refresh:
            return self._activity_type_names
        try:
            data = await self._async_request("GET", "/breeze/Breeze/activity_type")
            names = {int(item["id"]): item.get("name", "") for item in (data or []) if "id" in item}
            if names:
                self._activity_type_names = names
                return names
        except AmisgestApiError:
            _LOGGER.debug("Could not fetch activity_type dictionary", exc_info=True)
        self._activity_type_names = {}
        return self._activity_type_names

    async def async_get_latest_logbook_entry(
        self, person_id: str, *, since: datetime | None = None
    ) -> LogbookEntry | None:
        """Fetch the most recent logbook_entry (with its activities) for a child."""
        now = datetime.now(UTC)
        begin = since or (now - timedelta(days=1))
        params = {
            "beginDateTime": begin.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "endDateTime": now.strftime("%Y-%m-%dT%H:%M:%S.999Z"),
            "ownerPersonId": "",
        }
        entries = await self._async_request(
            "GET",
            "/breeze/Breeze/logbook_entry",
            person_id=person_id,
            params=params,
        )
        if not entries:
            return None

        entries.sort(key=lambda e: e.get("posted_date") or "", reverse=True)
        latest = entries[0]

        activities_raw = latest.get("activity") or []
        if not activities_raw:
            # The `activity` array embedded directly in the logbook_entry
            # payload was observed empty even when activities exist for
            # that entry -- fall back to the dedicated per-entry query seen
            # in the captured traffic.
            activities_raw = (
                await self._async_request(
                    "GET",
                    "/breeze/Breeze/activity",
                    person_id=person_id,
                    params={"$filter": f"logbook_entry_id eq {latest['id']}"},
                )
                or []
            )

        type_names = await self.async_get_activity_type_names()

        # Query media attached to activities for this time window
        media_by_activity: dict[int, list[int]] = {}
        all_media_ids: list[int] = []
        try:
            activity_media_raw = (
                await self._async_request(
                    "GET",
                    "/breeze/Breeze/activity_media",
                    person_id=person_id,
                    params=params,
                )
                or []
            )
            for am in activity_media_raw:
                act_id = am.get("activity_id")
                m_id = am.get("media_id")
                if act_id is not None and m_id is not None:
                    m_int = int(m_id)
                    media_by_activity.setdefault(int(act_id), []).append(m_int)
                    if m_int not in all_media_ids:
                        all_media_ids.append(m_int)
        except AmisgestApiError:
            _LOGGER.debug("Could not fetch activity_media", exc_info=True)

        preview_urls: dict[int, str] = {}
        if all_media_ids:
            try:
                media_ids_str = ",".join(str(m) for m in all_media_ids)
                previews = (
                    await self._async_request(
                        "GET",
                        "/api/media/GetPreviewUrl",
                        person_id=person_id,
                        params={"mediaIdsStr": media_ids_str},
                    )
                    or []
                )
                for p in previews:
                    if p.get("id") is not None and p.get("signedUrl"):
                        preview_urls[int(p["id"])] = p["signedUrl"]
            except AmisgestApiError:
                _LOGGER.debug("Could not fetch preview URLs", exc_info=True)

        activities = [
            self._parse_activity(item, type_names, media_by_activity.get(int(item["id"]), []))
            for item in activities_raw
            if "id" in item
        ]

        photos = [
            {
                "media_id": m_id,
                "preview_url": preview_urls.get(m_id),
            }
            for m_id in all_media_ids
        ]

        return LogbookEntry(
            id=latest["id"],
            owner_person_id=str(latest.get("owner_person_id", person_id)),
            posted_date=_parse_posted_date(latest.get("posted_date")),
            status_id=latest.get("logbook_entry_status_id"),
            activities=activities,
            media_ids=all_media_ids,
            photos=photos,
            raw=latest,
        )

    @staticmethod
    def _parse_activity(
        item: dict[str, Any], type_names: dict[int, str], media_ids: list[int] | None = None
    ) -> Activity:
        activity_type_id: int | None = item.get("activity_type_id")
        category = (
            KNOWN_ACTIVITY_TYPE_CATEGORIES.get(activity_type_id)
            if activity_type_id is not None
            else None
        )
        activity_type_name = (
            type_names.get(activity_type_id) if activity_type_id is not None else None
        )
        return Activity(
            id=item["id"],
            activity_type_id=activity_type_id,
            category=category,
            activity_type_name=activity_type_name,
            comment=item.get("comment") or "",
            start=_parse_datetime(item.get("start")),
            end=_parse_datetime(item.get("end")),
            numeric_value=item.get("numeric_value"),
            writer_person_id=item.get("writer_person_id"),
            media_ids=list(media_ids or []),
            raw=item,
        )

    async def async_get_new_inbox_count(self, person_id: str) -> int:
        """Cheap probe for unseen inbox items -- kept for diagnostics only.

        CONFIRMED (from a real capture) this is NOT a reliable proxy for
        "new logbook_entry exists": it stayed at 0 while a brand new entry
        (with a genuinely unread message_sent) had just been posted. Do not
        use this to gate whether to fetch the latest logbook entry.
        """
        result = await self._async_request("GET", "/api/wall/newInboxCount", person_id=person_id)
        return int(result or 0)

    async def async_get_presence_today(
        self, person_id: str, *, target_date: date | None = None
    ) -> PresenceStatus | None:
        """Fetch a child's attendance/presence confirmation status for a day.

        TODO(verify): see const.PRESENCE_CODES -- the exact set/meaning of
        `code_ph` values isn't fully confirmed. Only the raw `code` is
        returned here; AmisgestPresenceSensor (sensor.py) maps it onto its
        enum `options` and lets HA's translation system localize it.
        """
        # Must use HA's local timezone, not UTC: Amisgest's "today" is the
        # daycare's calendar day (Quebec/Eastern). Using UTC's date rolls
        # over to the next day in the evening (e.g. 8pm EDT is already
        # midnight UTC), so a status like "DP" (departed) recorded for the
        # current evening would get looked up under tomorrow's date -- which
        # has no record yet, so it fell back to "EXPECTED"/Attendu.
        day = target_date or dt_util.now().date()
        records = await self._async_request(
            "GET",
            CONFIRMATION_ABS_PATH,
            person_id=person_id,
            params={"curDate": day.isoformat()},
        )
        records = records or []
        matching = [r for r in records if str(r.get("personId")) == str(person_id)]
        record = (matching or records or [None])[0]
        if record is None:
            return None

        code = record.get("code_ph") or ""
        return PresenceStatus(
            code=code,
            description=record.get("description"),
            answer_required=bool(record.get("answer_required")),
            response=record.get("response"),
            installation=record.get("installation"),
            raw=record,
        )

    async def async_get_photo_bytes(
        self, url: str, *, person_id: str | None = None
    ) -> bytes | None:
        """Fetch a child's profile photo (URL from _async_resolve_child_photo_url).

        Already absolute and needs the same Bearer auth (and, as observed in
        the captured traffic, the same `personid` header) as every other
        call -- that's why this can't just be handed to Lovelace as a plain
        image URL, it has to be fetched server-side. Returns None (rather
        than raising) on any failure, since a missing photo shouldn't break
        the rest of the integration.
        """
        await self._async_ensure_token()
        headers = {"Authorization": f"Bearer {self._tokens.access_token}"}
        if person_id is not None:
            headers["personid"] = str(person_id)
        try:
            async with self._session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    _LOGGER.debug("Photo fetch failed (HTTP %s) for %s", resp.status, url)
                    return None
                return await resp.read()
        except aiohttp.ClientError as err:
            _LOGGER.debug("Photo fetch network error for %s: %s", url, err)
            return None

    async def async_get_media_url(
        self, media_id: int, *, person_id: str | None = None
    ) -> str | None:
        """Fetch a fresh signed URL for a logbook media item."""
        try:
            res = await self._async_request(
                "GET",
                "/api/media/GetMediaGuid",
                person_id=person_id,
                params={"mediaId": str(media_id)},
            )
            if isinstance(res, dict) and res.get("signedUrl"):
                return res["signedUrl"]
        except AmisgestApiError:
            _LOGGER.debug("GetMediaGuid failed for media %s", media_id, exc_info=True)

        try:
            previews = await self._async_request(
                "GET",
                "/api/media/GetPreviewUrl",
                person_id=person_id,
                params={"mediaIdsStr": str(media_id)},
            )
            if previews and isinstance(previews, list) and previews[0].get("signedUrl"):
                return previews[0]["signedUrl"]
        except AmisgestApiError:
            _LOGGER.debug("GetPreviewUrl fallback failed for media %s", media_id, exc_info=True)

        return None

    async def async_get_media_bytes(
        self, media_id: int, *, person_id: str | None = None
    ) -> bytes | None:
        """Download image bytes for a logbook media item (pre-signed S3 URL)."""
        url = await self.async_get_media_url(media_id, person_id=person_id)
        if not url:
            return None
        try:
            async with self._session.get(url) as resp:
                if resp.status != 200:
                    _LOGGER.debug("Media fetch failed (HTTP %s) for %s", resp.status, url)
                    return None
                return await resp.read()
        except aiohttp.ClientError as err:
            _LOGGER.debug("Media fetch network error for %s: %s", url, err)
            return None
