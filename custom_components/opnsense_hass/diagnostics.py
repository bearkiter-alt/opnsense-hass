"""Diagnostics support for the opnsense_hass integration.

Dumps the config entry and the coordinator's current data, with all secrets
(API key, API secret) and the host URL redacted.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_API_KEY, CONF_API_SECRET, CONF_URL
from .coordinator import OPNSenseConfigEntry

TO_REDACT = {CONF_API_KEY, CONF_API_SECRET, CONF_URL}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: OPNSenseConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "coordinator_data": async_redact_data(coordinator.data, TO_REDACT),
    }
