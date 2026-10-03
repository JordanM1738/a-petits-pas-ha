"""Config flow for the Journal à petits pas (Amisgest) integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AmisgestApiError, AmisgestAuthError, AmisgestClient, AmisgestTokens
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_DEVICE_ID,
    CONF_EXPIRES_AT,
    CONF_REFRESH_TOKEN,
    CONF_SAVE_PHOTOS_LOCALLY,
    CONF_SCAN_INTERVAL,
    DEFAULT_SAVE_PHOTOS_LOCALLY,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL_MINUTES,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class AmisgestConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Journal à petits pas."""

    VERSION = 1

    def __init__(self) -> None:
        self._username: str | None = None
        self._password: str | None = None
        self._device_id: str | None = None
        self._tokens: AmisgestTokens | None = None
        self._children_summary: str = ""
        self._reauth_entry: config_entries.ConfigEntry | None = None

    async def _async_try_login(self) -> dict[str, str]:
        """Attempt a login, returning a dict of form errors (empty on success)."""
        assert self._username is not None
        assert self._password is not None
        session = async_get_clientsession(self.hass)
        device_id = self._device_id or AmisgestClient.new_device_id()
        try:
            tokens = await AmisgestClient.async_login(
                session,
                email=self._username,
                password=self._password,
                device_id=device_id,
            )
        except AmisgestAuthError:
            return {"base": "invalid_auth"}
        except AmisgestApiError:
            return {"base": "cannot_connect"}
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error during Amisgest login")
            return {"base": "unknown"}

        self._device_id = device_id
        self._tokens = tokens

        client = AmisgestClient(session, device_id=device_id, tokens=tokens)
        try:
            children = await client.async_get_cahier_description()
        except AmisgestApiError:
            return {"base": "cannot_connect"}
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error listing children")
            return {"base": "unknown"}

        if not children:
            return {"base": "no_children"}

        self._children_summary = ", ".join(
            f"{child.person_name} ({child.client_name})" for child in children
        )
        return {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._username = user_input[CONF_USERNAME]
            self._password = user_input[CONF_PASSWORD]

            await self.async_set_unique_id(self._username.lower())
            self._abort_if_unique_id_configured()

            errors = await self._async_try_login()
            if not errors:
                return await self.async_step_confirm()

        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            assert self._username is not None
            assert self._tokens is not None
            return self.async_create_entry(
                title=self._username,
                data={
                    CONF_USERNAME: self._username,
                    CONF_DEVICE_ID: self._device_id,
                    CONF_ACCESS_TOKEN: self._tokens.access_token,
                    CONF_REFRESH_TOKEN: self._tokens.refresh_token,
                    CONF_EXPIRES_AT: self._tokens.expires_at.isoformat(),
                },
            )

        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"children": self._children_summary},
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        self._username = entry_data[CONF_USERNAME]
        self._device_id = entry_data.get(CONF_DEVICE_ID)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._password = user_input[CONF_PASSWORD]
            errors = await self._async_try_login()
            if not errors:
                assert self._reauth_entry is not None
                assert self._tokens is not None
                self.hass.config_entries.async_update_entry(
                    self._reauth_entry,
                    data={
                        **self._reauth_entry.data,
                        CONF_DEVICE_ID: self._device_id,
                        CONF_ACCESS_TOKEN: self._tokens.access_token,
                        CONF_REFRESH_TOKEN: self._tokens.refresh_token,
                        CONF_EXPIRES_AT: self._tokens.expires_at.isoformat(),
                    },
                )
                await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
            description_placeholders={"username": self._username or ""},
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> AmisgestOptionsFlowHandler:
        return AmisgestOptionsFlowHandler(config_entry)


class AmisgestOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options (polling interval)."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current_scan_interval = self._config_entry.options.get(
            CONF_SCAN_INTERVAL, int(DEFAULT_SCAN_INTERVAL.total_seconds() // 60)
        )
        current_save_photos = self._config_entry.options.get(
            CONF_SAVE_PHOTOS_LOCALLY, DEFAULT_SAVE_PHOTOS_LOCALLY
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=current_scan_interval): vol.All(
                    vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL_MINUTES)
                ),
                vol.Required(CONF_SAVE_PHOTOS_LOCALLY, default=current_save_photos): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
