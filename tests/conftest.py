"""Shared pytest fixtures for the opnsense_hass integration tests.

These tests mock at the ``aiohttp.ClientSession`` level so they run without a live
OPNsense. No ``pytest-homeassistant-custom-component`` plugin is required for the API
tests; the config-flow tests use ``hass`` from that plugin when available.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.opnsense_hass.api import OPNSenseClient


def make_response(
    status: int = 200,
    json_data: Any | None = None,
    text: str = "",
) -> MagicMock:
    """Build a mock aiohttp response usable as an async context manager.

    The returned object yields itself from ``__aenter__`` and exposes ``.status``,
    an async ``.json(content_type=None)`` and an async ``.text()``.
    """
    resp = MagicMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data)
    resp.text = AsyncMock(return_value=text)

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


@pytest.fixture
def mock_session() -> MagicMock:
    """A MagicMock standing in for ``aiohttp.ClientSession``.

    ``.request(...)`` is a plain (non-async) MagicMock that returns an
    async-context-manager mock, mirroring how ``aiohttp`` is used:
    ``async with session.request(...) as resp:``.
    """
    session = MagicMock()
    session.request = MagicMock(return_value=make_response())
    return session


@pytest.fixture
def client(mock_session: MagicMock) -> OPNSenseClient:
    """An OPNSenseClient wired to the mocked session."""
    return OPNSenseClient(
        "http://test",
        "key",
        "secret",
        session=mock_session,
        verify_ssl=False,
    )


@pytest.fixture
def set_response(mock_session: MagicMock) -> Callable[[int, Any], MagicMock]:
    """Factory: configure the canned response for the next ``request`` call.

    Returns the ``mock_session.request`` mock so callers can assert on call args
    (method/url/kwargs) after invoking the client.
    """

    def _set(status: int = 200, json_data: Any | None = None, text: str = "") -> MagicMock:
        mock_session.request.return_value = make_response(
            status=status, json_data=json_data, text=text
        )
        return mock_session.request

    return _set


@pytest.fixture
def set_responses(mock_session: MagicMock) -> Callable[[list[tuple]], MagicMock]:
    """Factory: queue a SEQUENCE of canned responses for successive requests.

    Pass a list of ``(status, json_data)`` or ``(status, json_data, text)`` tuples;
    each ``request`` call consumes the next one in order.
    """

    def _set(responses: list[tuple]) -> MagicMock:
        side_effect = []
        for item in responses:
            status = item[0]
            json_data = item[1] if len(item) > 1 else None
            text = item[2] if len(item) > 2 else ""
            side_effect.append(make_response(status=status, json_data=json_data, text=text))
        mock_session.request.side_effect = side_effect
        return mock_session.request

    return _set
