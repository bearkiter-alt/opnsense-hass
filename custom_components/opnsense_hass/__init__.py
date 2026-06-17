"""The opnsense_hass integration."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import OPNSenseAuthError, OPNSenseClient, OPNSenseError
from .const import (
    APPLY_ALIAS,
    APPLY_BOTH,
    APPLY_FILTER,
    ATTR_ADDRESS,
    ATTR_ADDRESSES,
    ATTR_ALIAS,
    ATTR_BODY,
    ATTR_ENABLED,
    ATTR_METHOD,
    ATTR_PATH,
    ATTR_TARGET,
    ATTR_UUID,
    CONF_API_KEY,
    CONF_API_SECRET,
    CONF_URL,
    CONF_VERIFY_SSL,
    DATA_ALIASES,
    DATA_RULES,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    LOGGER,
    PLATFORMS,
    SERVICE_ALIAS_ADD_HOST,
    SERVICE_ALIAS_BLOCK_DEVICE,
    SERVICE_ALIAS_FLUSH,
    SERVICE_ALIAS_REMOVE_HOST,
    SERVICE_ALIAS_SET_HOSTS,
    SERVICE_ALIAS_UNBLOCK_DEVICE,
    SERVICE_APPLY,
    SERVICE_EXEC_API,
    SERVICE_TOGGLE_RULE,
)
from .coordinator import OPNSenseConfigEntry, OPNSenseCoordinator

# ---------------------------------------------------------------------------
# Service voluptuous schemas (§13 — NORMATIVE)
# ---------------------------------------------------------------------------
SCHEMA_ALIAS_ADD_HOST = vol.Schema(
    {
        vol.Required(ATTR_ALIAS): cv.string,
        vol.Required(ATTR_ADDRESS): cv.string,
    }
)
SCHEMA_ALIAS_REMOVE_HOST = SCHEMA_ALIAS_ADD_HOST  # same shape
SCHEMA_ALIAS_FLUSH = vol.Schema(
    {
        vol.Required(ATTR_ALIAS): cv.string,
    }
)
SCHEMA_ALIAS_SET_HOSTS = vol.Schema(
    {
        vol.Required(ATTR_ALIAS): cv.string,
        vol.Required(ATTR_ADDRESSES): vol.All(cv.ensure_list, [cv.string]),
    }
)
SCHEMA_ALIAS_BLOCK_DEVICE = SCHEMA_ALIAS_ADD_HOST
SCHEMA_ALIAS_UNBLOCK_DEVICE = SCHEMA_ALIAS_ADD_HOST
SCHEMA_TOGGLE_RULE = vol.Schema(
    {
        vol.Required(ATTR_UUID): cv.string,
        vol.Required(ATTR_ENABLED): cv.boolean,
    }
)
SCHEMA_APPLY = vol.Schema(
    {
        vol.Required(ATTR_TARGET): vol.In([APPLY_FILTER, APPLY_ALIAS, APPLY_BOTH]),
    }
)
SCHEMA_EXEC_API = vol.Schema(
    {
        vol.Required(ATTR_METHOD): vol.All(cv.string, vol.Upper, vol.In(["GET", "POST"])),
        vol.Required(ATTR_PATH): cv.string,
        vol.Optional(ATTR_BODY, default=dict): dict,
    }
)


# ---------------------------------------------------------------------------
# Setup / teardown
# ---------------------------------------------------------------------------
async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration (register services once, independent of entries)."""
    _async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: OPNSenseConfigEntry) -> bool:
    """Set up opnsense_hass from a config entry."""
    verify_ssl = entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
    session = async_get_clientsession(hass, verify_ssl=verify_ssl)
    client = OPNSenseClient(
        url=entry.data[CONF_URL],
        api_key=entry.data[CONF_API_KEY],
        api_secret=entry.data[CONF_API_SECRET],
        session=session,
        verify_ssl=verify_ssl,
    )
    coordinator = OPNSenseCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: OPNSenseConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(
    hass: HomeAssistant, entry: OPNSenseConfigEntry
) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


# ---------------------------------------------------------------------------
# Coordinator resolution helpers
# ---------------------------------------------------------------------------
def _clients(
    hass: HomeAssistant,
) -> list[tuple[OPNSenseConfigEntry, OPNSenseCoordinator, OPNSenseClient]]:
    """Return (entry, coordinator, client) tuples for all loaded DOMAIN entries."""
    result: list[tuple[OPNSenseConfigEntry, OPNSenseCoordinator, OPNSenseClient]] = []
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        coordinator: OPNSenseCoordinator | None = getattr(
            entry, "runtime_data", None
        )
        if coordinator is None:
            continue
        result.append((entry, coordinator, coordinator.client))
    return result


def _client_for_alias(
    hass: HomeAssistant, alias_name: str
) -> tuple[OPNSenseCoordinator, OPNSenseClient]:
    """Resolve the coordinator/client owning the given alias name."""
    loaded = _clients(hass)
    matches = [
        (coord, client)
        for _entry, coord, client in loaded
        if alias_name in (coord.data or {}).get(DATA_ALIASES, {})
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches and len(loaded) == 1:
        _entry, coord, client = loaded[0]
        return coord, client
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="alias_not_found",
        translation_placeholders={"alias": alias_name},
    )


def _client_for_rule(
    hass: HomeAssistant, uuid: str
) -> tuple[OPNSenseCoordinator, OPNSenseClient]:
    """Resolve the coordinator/client owning the given rule uuid."""
    loaded = _clients(hass)
    matches = [
        (coord, client)
        for _entry, coord, client in loaded
        if uuid in (coord.data or {}).get(DATA_RULES, {})
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches and len(loaded) == 1:
        _entry, coord, client = loaded[0]
        return coord, client
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="rule_not_found",
        translation_placeholders={"uuid": uuid},
    )


def _single_client(hass: HomeAssistant) -> tuple[OPNSenseCoordinator, OPNSenseClient]:
    """Require exactly one loaded entry; raise otherwise."""
    loaded = _clients(hass)
    if len(loaded) != 1:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="no_single_entry",
        )
    _entry, coord, client = loaded[0]
    return coord, client


# ---------------------------------------------------------------------------
# Service registration + handlers (§4.1 — NORMATIVE)
# ---------------------------------------------------------------------------
@callback
def _async_register_services(hass: HomeAssistant) -> None:
    """Register the integration's services exactly once."""
    if hass.services.has_service(DOMAIN, SERVICE_APPLY):
        return

    async def _handle_alias_add_host(call: ServiceCall) -> None:
        alias = call.data[ATTR_ALIAS]
        address = call.data[ATTR_ADDRESS]
        coordinator, client = _client_for_alias(hass, alias)
        try:
            await client.alias_add(alias, address)
        except OPNSenseError as err:
            raise HomeAssistantError(str(err)) from err
        await coordinator.async_request_refresh()

    async def _handle_alias_remove_host(call: ServiceCall) -> None:
        alias = call.data[ATTR_ALIAS]
        address = call.data[ATTR_ADDRESS]
        coordinator, client = _client_for_alias(hass, alias)
        try:
            await client.alias_delete(alias, address)
        except OPNSenseError as err:
            raise HomeAssistantError(str(err)) from err
        await coordinator.async_request_refresh()

    async def _handle_alias_flush(call: ServiceCall) -> None:
        alias = call.data[ATTR_ALIAS]
        coordinator, client = _client_for_alias(hass, alias)
        try:
            await client.alias_flush(alias)
        except OPNSenseError as err:
            raise HomeAssistantError(str(err)) from err
        await coordinator.async_request_refresh()

    async def _handle_alias_set_hosts(call: ServiceCall) -> None:
        alias = call.data[ATTR_ALIAS]
        addresses = call.data[ATTR_ADDRESSES]
        coordinator, client = _client_for_alias(hass, alias)
        try:
            await client.alias_set_content(alias, addresses)
        except OPNSenseError as err:
            raise HomeAssistantError(str(err)) from err
        await coordinator.async_request_refresh()

    async def _handle_alias_block_device(call: ServiceCall) -> None:
        alias = call.data[ATTR_ALIAS]
        address = call.data[ATTR_ADDRESS]
        coordinator, client = _client_for_alias(hass, alias)
        try:
            await client.alias_block_device(alias, address)
        except OPNSenseError as err:
            raise HomeAssistantError(str(err)) from err
        await coordinator.async_request_refresh()

    async def _handle_alias_unblock_device(call: ServiceCall) -> None:
        alias = call.data[ATTR_ALIAS]
        address = call.data[ATTR_ADDRESS]
        coordinator, client = _client_for_alias(hass, alias)
        try:
            await client.alias_unblock_device(alias, address)
        except OPNSenseError as err:
            raise HomeAssistantError(str(err)) from err
        await coordinator.async_request_refresh()

    async def _handle_toggle_rule(call: ServiceCall) -> None:
        uuid = call.data[ATTR_UUID]
        enabled = call.data[ATTR_ENABLED]
        coordinator, client = _client_for_rule(hass, uuid)
        try:
            await client.toggle_rule(uuid, enabled)
            await client.filter_apply()
        except OPNSenseError as err:
            raise HomeAssistantError(str(err)) from err
        await coordinator.async_request_refresh()

    async def _handle_apply(call: ServiceCall) -> None:
        target = call.data[ATTR_TARGET]
        loaded = _clients(hass)
        if not loaded:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="no_single_entry",
            )
        errors: list[str] = []
        for entry, coordinator, client in loaded:
            try:
                if target in (APPLY_FILTER, APPLY_BOTH):
                    await client.filter_apply()
                if target in (APPLY_ALIAS, APPLY_BOTH):
                    await client.alias_reconfigure()
            except OPNSenseError as err:
                errors.append(f"{entry.title}: {err}")
            # Refresh regardless of outcome so succeeded instances reflect the
            # applied state; failed ones surface the stale data they had.
            await coordinator.async_request_refresh()
        if errors:
            raise HomeAssistantError("; ".join(errors))

    async def _handle_exec_api(call: ServiceCall) -> ServiceResponse:
        method = call.data[ATTR_METHOD]
        path = call.data[ATTR_PATH]
        body = call.data.get(ATTR_BODY) or {}
        coordinator, client = _single_client(hass)
        try:
            result = await client.raw(method, path, body)
        except OPNSenseError as err:
            raise HomeAssistantError(str(err)) from err
        await coordinator.async_request_refresh()
        return {"result": result}

    hass.services.async_register(
        DOMAIN,
        SERVICE_ALIAS_ADD_HOST,
        _handle_alias_add_host,
        schema=SCHEMA_ALIAS_ADD_HOST,
        supports_response=SupportsResponse.NONE,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ALIAS_REMOVE_HOST,
        _handle_alias_remove_host,
        schema=SCHEMA_ALIAS_REMOVE_HOST,
        supports_response=SupportsResponse.NONE,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ALIAS_FLUSH,
        _handle_alias_flush,
        schema=SCHEMA_ALIAS_FLUSH,
        supports_response=SupportsResponse.NONE,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ALIAS_SET_HOSTS,
        _handle_alias_set_hosts,
        schema=SCHEMA_ALIAS_SET_HOSTS,
        supports_response=SupportsResponse.NONE,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ALIAS_BLOCK_DEVICE,
        _handle_alias_block_device,
        schema=SCHEMA_ALIAS_BLOCK_DEVICE,
        supports_response=SupportsResponse.NONE,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ALIAS_UNBLOCK_DEVICE,
        _handle_alias_unblock_device,
        schema=SCHEMA_ALIAS_UNBLOCK_DEVICE,
        supports_response=SupportsResponse.NONE,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_TOGGLE_RULE,
        _handle_toggle_rule,
        schema=SCHEMA_TOGGLE_RULE,
        supports_response=SupportsResponse.NONE,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_APPLY,
        _handle_apply,
        schema=SCHEMA_APPLY,
        supports_response=SupportsResponse.NONE,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_EXEC_API,
        _handle_exec_api,
        schema=SCHEMA_EXEC_API,
        supports_response=SupportsResponse.OPTIONAL,
    )

    LOGGER.debug("opnsense_hass services registered")
