"""Tests for the OPNsense async API client.

These exercise the two verified body quirks (GET sends no Content-Type/body; no-arg
POST sends ``{}``), error mapping, the missing-UUID case, the option-map helper, and
the full persistent ``alias_set_content`` flow.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.opnsense_hass.api import (
    OPNSenseAuthError,
    OPNSenseClient,
    OPNSenseError,
    selected_content,
)


def _last_call_kwargs(request_mock: MagicMock) -> dict[str, Any]:
    """Return the kwargs of the most recent ``session.request`` call."""
    return request_mock.call_args.kwargs


def _last_call_args(request_mock: MagicMock) -> tuple:
    """Return the positional args (method, url) of the most recent call."""
    return request_mock.call_args.args


async def test_get_omits_content_type(
    client: OPNSenseClient, set_response: Callable[..., MagicMock]
) -> None:
    """GET must NOT send a Content-Type header and must NOT pass a body (quirk 1)."""
    request_mock = set_response(200, {"product": {}})

    await client.firmware_status()

    args = _last_call_args(request_mock)
    kwargs = _last_call_kwargs(request_mock)

    assert args[0] == "GET"
    assert args[1] == "http://test/api/core/firmware/status"
    # No JSON body for GET.
    assert "json" not in kwargs
    assert "data" not in kwargs
    # No headers carrying a Content-Type.
    headers = kwargs.get("headers") or {}
    assert not any(k.lower() == "content-type" for k in headers)


async def test_post_sends_empty_body_default(
    client: OPNSenseClient, set_response: Callable[..., MagicMock]
) -> None:
    """A no-arg POST must default its JSON body to ``{}`` (quirk 2)."""
    request_mock = set_response(200, {"status": "ok"})

    await client.alias_reconfigure()

    args = _last_call_args(request_mock)
    kwargs = _last_call_kwargs(request_mock)

    assert args[0] == "POST"
    assert args[1] == "http://test/api/firewall/alias/reconfigure"
    assert kwargs.get("json") == {}


async def test_post_sends_body(
    client: OPNSenseClient, set_response: Callable[..., MagicMock]
) -> None:
    """alias_add posts {"address": ...} to the alias_util/add path."""
    request_mock = set_response(200, {"result": "ok"})

    await client.alias_add("A", "1.2.3.4")

    args = _last_call_args(request_mock)
    kwargs = _last_call_kwargs(request_mock)

    assert args[0] == "POST"
    assert args[1] == "http://test/api/firewall/alias_util/add/A"
    assert kwargs.get("json") == {"address": "1.2.3.4"}


async def test_auth_error_401(
    client: OPNSenseClient, set_response: Callable[..., MagicMock]
) -> None:
    """HTTP 401 maps to OPNSenseAuthError."""
    set_response(401, None, text="Unauthorized")

    with pytest.raises(OPNSenseAuthError):
        await client.firmware_status()


async def test_auth_error_403(
    client: OPNSenseClient, set_response: Callable[..., MagicMock]
) -> None:
    """HTTP 403 maps to OPNSenseAuthError."""
    set_response(403, None, text="Forbidden")

    with pytest.raises(OPNSenseAuthError):
        await client.firmware_status()


async def test_generic_error_500(
    client: OPNSenseClient, set_response: Callable[..., MagicMock]
) -> None:
    """HTTP 500 maps to OPNSenseError (and NOT to the auth subclass)."""
    set_response(500, None, text="Internal Server Error")

    with pytest.raises(OPNSenseError) as exc_info:
        await client.firmware_status()
    assert not isinstance(exc_info.value, OPNSenseAuthError)


async def test_alias_get_uuid_missing_returns_none(
    client: OPNSenseClient, set_response: Callable[..., MagicMock]
) -> None:
    """getAliasUUID returning [] (missing alias) yields None."""
    set_response(200, [])

    result = await client.alias_get_uuid("DoesNotExist")
    assert result is None


def test_selected_content_from_option_map() -> None:
    """selected_content returns only the selected keys of an option-map dict."""
    alias = {
        "content": {
            "192.168.1.50": {"value": "192.168.1.50", "selected": 1},
            "192.168.1.51": {"value": "192.168.1.51", "selected": 0},
            "192.168.1.52": {"value": "192.168.1.52", "selected": 1},
        }
    }
    result = selected_content(alias)
    assert result == ["192.168.1.50", "192.168.1.52"]


async def test_alias_set_content_flow(
    client: OPNSenseClient, set_responses: Callable[[list[tuple]], MagicMock]
) -> None:
    """The persistent set-content flow: getAliasUUID -> getItem -> setItem -> reconfigure.

    Asserts the setItem payload's ``content`` is the newline-joined address list and
    that reconfigure is the final call.
    """
    addresses = ["10.0.0.1", "10.0.0.2", "10.0.0.3"]

    request_mock = set_responses(
        [
            # 1. GET getAliasUUID/{name}
            (200, {"uuid": "abc-123"}),
            # 2. GET getItem/{uuid} -> option-map shaped alias dict
            (
                200,
                {
                    "alias": {
                        "enabled": "1",
                        "name": "KidsBlocked",
                        "type": {
                            "host": {"value": "Host(s)", "selected": 1},
                            "network": {"value": "Network(s)", "selected": 0},
                        },
                        "content": {
                            "10.0.0.9": {"value": "10.0.0.9", "selected": 1},
                        },
                        "description": "kids",
                    }
                },
            ),
            # 3. POST setItem/{uuid}
            (200, {"result": "saved"}),
            # 4. POST reconfigure
            (200, {"status": "ok"}),
        ]
    )

    result = await client.alias_set_content("KidsBlocked", addresses)

    # Four requests issued in the documented order.
    assert request_mock.call_count == 4

    calls = request_mock.call_args_list
    # Call 3 == setItem with the joined content + plain-string type.
    set_item_args = calls[2].args
    set_item_kwargs = calls[2].kwargs
    assert set_item_args[0] == "POST"
    assert set_item_args[1] == "http://test/api/firewall/alias/setItem/abc-123"
    payload = set_item_kwargs["json"]["alias"]
    assert payload["content"] == "\n".join(addresses)
    assert payload["type"] == "host"  # plain string, converted from option-map
    assert payload["enabled"] == "1"
    assert payload["name"] == "KidsBlocked"

    # Call 4 == reconfigure (final), and its result is returned.
    reconfigure_args = calls[3].args
    assert reconfigure_args[0] == "POST"
    assert reconfigure_args[1] == "http://test/api/firewall/alias/reconfigure"
    assert result == {"status": "ok"}
