"""Config flow for the opnsense_hass integration."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    OPNSenseAuthError,
    OPNSenseClient,
    OPNSenseConnectionError,
    OPNSenseError,
)
from .const import (
    CONF_API_KEY,
    CONF_API_SECRET,
    CONF_NAME,
    CONF_SCAN_INTERVAL,
    CONF_TRACKED_ALIASES,
    CONF_URL,
    CONF_VERIFY_SSL,
    DATA_ALIASES,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    LOGGER,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME, default="OPNsense"): str,
        vol.Required(CONF_URL): str,
        vol.Required(CONF_API_KEY): str,
        vol.Required(CONF_API_SECRET): str,
        vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): bool,
        vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(
            vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)
        ),
    }
)

STEP_REAUTH_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_KEY): str,
        vol.Required(CONF_API_SECRET): str,
    }
)


async def _validate(hass: HomeAssistant, data: Mapping[str, Any]) -> None:
    """Validate the user input by hitting the OPNsense firmware-status endpoint.

    Raises OPNSenseAuthError / OPNSenseConnectionError / OPNSenseError on failure.
    """
    verify_ssl = data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
    session = async_get_clientsession(hass, verify_ssl=verify_ssl)
    client = OPNSenseClient(
        url=data[CONF_URL],
        api_key=data[CONF_API_KEY],
        api_secret=data[CONF_API_SECRET],
        session=session,
        verify_ssl=verify_ssl,
    )
    await client.firmware_status()


class OPNSenseConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow for opnsense_hass."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial user step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await _validate(self.hass, user_input)
            except OPNSenseAuthError:
                errors["base"] = "invalid_auth"
            except OPNSenseConnectionError:
                errors["base"] = "cannot_connect"
            except OPNSenseError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001  pylint: disable=broad-except
                LOGGER.exception("Unexpected error validating OPNsense connection")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(
                    user_input[CONF_URL].rstrip("/").lower()
                )
                self._abort_if_unique_id_configured()

                data = {
                    CONF_NAME: user_input[CONF_NAME],
                    CONF_URL: user_input[CONF_URL],
                    CONF_API_KEY: user_input[CONF_API_KEY],
                    CONF_API_SECRET: user_input[CONF_API_SECRET],
                    CONF_VERIFY_SSL: user_input.get(
                        CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL
                    ),
                }
                options = {
                    CONF_SCAN_INTERVAL: user_input.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                    ),
                    CONF_TRACKED_ALIASES: [],
                }
                return self.async_create_entry(
                    title=user_input[CONF_NAME], data=data, options=options
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication when credentials are rejected."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm re-authentication with new API credentials."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            merged = {**entry.data, **user_input}
            try:
                await _validate(self.hass, merged)
            except OPNSenseAuthError:
                errors["base"] = "invalid_auth"
            except OPNSenseError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001  pylint: disable=broad-except
                LOGGER.exception("Unexpected error during OPNsense reauth")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates=user_input
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_DATA_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> OPNSenseOptionsFlow:
        """Return the options flow handler."""
        return OPNSenseOptionsFlow()


class OPNSenseOptionsFlow(OptionsFlow):
    """Handle the options flow for opnsense_hass (2026.6 pattern, no __init__)."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the integration options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        coordinator = self.config_entry.runtime_data
        data = getattr(coordinator, "data", None) or {}
        alias_names = sorted(data.get(DATA_ALIASES, {}))
        current = self.config_entry.options
        # Keep any previously-tracked alias selectable even if it is no longer
        # present (deleted on OPNsense, or empty coordinator data) so the options
        # form still renders and a stale selection can be removed.
        alias_options = {n: n for n in alias_names}
        for tracked in current.get(CONF_TRACKED_ALIASES, []):
            alias_options.setdefault(tracked, f"{tracked} (missing)")

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=current.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                ),
                vol.Optional(
                    CONF_TRACKED_ALIASES,
                    default=current.get(CONF_TRACKED_ALIASES, []),
                ): cv.multi_select(alias_options),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
