"""Tests for the opnsense_hass config + reauth flow.

These patch ``OPNSenseClient.firmware_status`` (the validation call) so no live
OPNsense is needed, and drive the flow via ``hass.config_entries.flow``. They rely on
the ``hass`` fixture provided by ``pytest-homeassistant-custom-component``.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.opnsense_hass.api import (
    OPNSenseAuthError,
    OPNSenseConnectionError,
)
from custom_components.opnsense_hass.const import (
    CONF_API_KEY,
    CONF_API_SECRET,
    CONF_NAME,
    CONF_SCAN_INTERVAL,
    CONF_TRACKED_ALIASES,
    CONF_URL,
    CONF_VERIFY_SSL,
    DOMAIN,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

_USER_INPUT = {
    CONF_NAME: "Edge OPNsense",
    CONF_URL: "http://192.168.1.254",
    CONF_API_KEY: "key",
    CONF_API_SECRET: "secret",
    CONF_VERIFY_SSL: False,
    CONF_SCAN_INTERVAL: 45,
}

_VALIDATE_PATH = "custom_components.opnsense_hass.config_flow.OPNSenseClient.firmware_status"


async def test_user_flow_success(hass: HomeAssistant) -> None:
    """A successful validation creates an entry with split data/options."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    with patch(_VALIDATE_PATH, return_value={"product": {"product_version": "25.7.11_9"}}):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], dict(_USER_INPUT)
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Edge OPNsense"

    data = result["data"]
    assert data[CONF_URL] == "http://192.168.1.254"
    assert data[CONF_API_KEY] == "key"
    assert data[CONF_API_SECRET] == "secret"
    assert data[CONF_VERIFY_SSL] is False
    # scan_interval lives in options, NOT data.
    assert CONF_SCAN_INTERVAL not in data

    options = result["options"]
    assert options[CONF_SCAN_INTERVAL] == 45
    assert options[CONF_TRACKED_ALIASES] == []


async def test_user_flow_invalid_auth(hass: HomeAssistant) -> None:
    """An auth failure re-shows the form with an invalid_auth error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    with patch(_VALIDATE_PATH, side_effect=OPNSenseAuthError("nope")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], dict(_USER_INPUT)
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_cannot_connect(hass: HomeAssistant) -> None:
    """A connection failure re-shows the form with a cannot_connect error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    with patch(_VALIDATE_PATH, side_effect=OPNSenseConnectionError("down")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], dict(_USER_INPUT)
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_reauth_success(hass: HomeAssistant) -> None:
    """Reauth with valid new credentials aborts as reauth_successful and updates data."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Edge OPNsense",
        unique_id="http://192.168.1.254",
        data={
            CONF_NAME: "Edge OPNsense",
            CONF_URL: "http://192.168.1.254",
            CONF_API_KEY: "old-key",
            CONF_API_SECRET: "old-secret",
            CONF_VERIFY_SSL: False,
        },
        options={CONF_SCAN_INTERVAL: 30, CONF_TRACKED_ALIASES: []},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": SOURCE_REAUTH,
            "entry_id": entry.entry_id,
        },
        data=entry.data,
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    with patch(_VALIDATE_PATH, return_value={"product": {"product_version": "25.7.11_9"}}):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_API_KEY: "new-key", CONF_API_SECRET: "new-secret"},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_KEY] == "new-key"
    assert entry.data[CONF_API_SECRET] == "new-secret"
    # The URL (and thus unique_id) is unchanged.
    assert entry.data[CONF_URL] == "http://192.168.1.254"
