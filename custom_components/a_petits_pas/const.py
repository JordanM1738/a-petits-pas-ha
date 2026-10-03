"""Constants for the Journal à petits pas (Amisgest) integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "a_petits_pas"

BASE_URL = "https://serviceapp.amisgest.ca/8_2"
TOKEN_PATH = "/token"

# Public client id embedded in the official "Journal à petits pas" web app
# (observed in a HAR capture of app.journalapetitspas.ca). This is not a
# secret issued to this integration -- it's the same id every web-app user's
# browser sends. Kept as a constant since Amisgest could start rejecting it
# for a stale `v=` app version (see API_VERSION below).
CLIENT_ID = "099153c2625149bc8ecb3e85e03f0022"

# The `v=` parameter sent with every token request in the captured traffic.
# Isolated as its own constant: if Amisgest ever starts rejecting requests
# because this looks like a stale app version, bump it here.
API_VERSION = "47.1.1"

DEVICE_INFO = "Home Assistant integration (a_petits_pas)"

CONF_DEVICE_ID = "device_id"
CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_EXPIRES_AT = "expires_at"

CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_SCAN_INTERVAL = timedelta(minutes=5)
MIN_SCAN_INTERVAL_MINUTES = 1

# Whether the coordinator saves journal photos to `www/a_petits_pas/` (today/
# and archive/ folders, served unauthenticated by HA under /local/). Some
# users may not want their child's daycare photos written to disk at all --
# disabling this only stops local caching; the `image.*_photo_du_journal`
# entity still works, since it fetches bytes on demand through HA's own
# authenticated image proxy rather than reading these files.
CONF_SAVE_PHOTOS_LOCALLY = "save_photos_locally"
DEFAULT_SAVE_PHOTOS_LOCALLY = True

EVENT_NEW_LOGBOOK_ENTRY = f"{DOMAIN}_new_post"

# Known activity_type_id -> internal category key, confirmed against a real
# logbook_entry in a HAR capture (logbook_entry_id 68018508):
#   1 -> Sieste (has start/end)
#   2 -> Couche
#   3 -> Repas
#   4 -> Biberon
#   6 -> Écrit par / éducateur (comment=name, numeric_value=writer_person_id)
#   9 -> Informations / Remarques (comment=text, may have photos attached)
# Confirmed against Amisgest front-end app source code (getIdFromStrActivityType).
ACTIVITY_CATEGORY_NAP = "nap"
ACTIVITY_CATEGORY_DIAPER = "diaper"
ACTIVITY_CATEGORY_MEAL = "meal"
ACTIVITY_CATEGORY_BOTTLE = "bottle"
ACTIVITY_CATEGORY_WRITER = "writer"
ACTIVITY_CATEGORY_INFO = "info"

KNOWN_ACTIVITY_TYPE_CATEGORIES: dict[int, str] = {
    1: ACTIVITY_CATEGORY_NAP,
    2: ACTIVITY_CATEGORY_DIAPER,
    3: ACTIVITY_CATEGORY_MEAL,
    4: ACTIVITY_CATEGORY_BOTTLE,
    6: ACTIVITY_CATEGORY_WRITER,
    9: ACTIVITY_CATEGORY_INFO,
}

PHOTO_DIR_TODAY = "today"
PHOTO_DIR_ARCHIVE = "archive"

# `activity.start`/`activity.end` for time-only fields (e.g. nap start/end)
# come back from Amisgest with a fixed placeholder date (observed:
# 2017-01-01) and only the time-of-day is meaningful -- entities must format
# these as HH:MM, never display the date portion.

CONFIRMATION_ABS_PATH = "/api/confirmationAbs/confirmationsWithDate"

# Known `code_ph` values from confirmationAbs (today's attendance/presence
# status), lowercased to match AmisgestPresenceSensor's enum `options` and
# the "state" translation keys in strings.json/translations/*.json (HA's
# standard per-viewer-language state translation, which requires
# device_class=enum with a fixed options list). "p"/"a" were confirmed live
# against a real account (toggling présent/absent) on 2026-09-15. "dp" was
# observed live on 2026-09-17 appearing right at the child's evening daycare
# pickup, and separately in an older HAR capture where it showed for the
# just-finished day while the following day already showed "expected" --
# both consistent with "the day is done, child has left" (Départ/Departed),
# though this is still an inference, not something confirmed via Amisgest
# documentation or support. "expected" was only a best-effort guess from
# earlier static samples and hasn't been confirmed to actually appear as a
# live code_ph value (it may only show up in the advance/future-attendance-
# confirmation rows, not today's status). A code_ph outside this set makes
# the sensor report HA's generic "unknown" state rather than error out --
# the raw code and description stay visible as attributes for diagnosis
# (see AmisgestPresenceSensor in sensor.py). "not_expected" was observed
# live on 2026-09-17 for a day the child isn't scheduled to attend daycare
# at all (distinct from "a"/Absent, which is an unscheduled/unconfirmed
# absence on an otherwise-expected day). Extend this tuple (and the
# matching "state" blocks in strings.json/translations/*.json) as more
# codes are confirmed.
PRESENCE_CODES: tuple[str, ...] = ("p", "a", "dp", "expected", "not_expected")
