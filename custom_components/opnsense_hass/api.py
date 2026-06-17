"""Async client for the OPNsense REST API.

Pure async client; the only Home Assistant coupling is that the caller injects an
``aiohttp.ClientSession`` (typically the HA-managed shared session). The client
NEVER logs the API key/secret or the ``Authorization`` header.

Two verified OPNsense API quirks are implemented in :meth:`OPNSenseClient._request`:

1. **GET requests MUST NOT send a ``Content-Type`` header or body.** A GET with
   ``Content-Type: application/json`` plus an empty body returns
   HTTP 400 ``{"message": "Invalid JSON syntax"}``.
2. **POST requests MUST always send a JSON body** (defaulting to ``{}``). An empty
   POST body returns HTTP 411 Length Required.
"""
from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from .const import API_PREFIX, DEFAULT_TIMEOUT, LOGGER, SEARCH_ROW_COUNT


class OPNSenseError(Exception):
    """Generic opnsense API error."""


class OPNSenseAuthError(OPNSenseError):
    """Authentication failure — bad API key/secret (HTTP 401)."""


class OPNSensePrivilegeError(OPNSenseError):
    """Authenticated, but the API user lacks the required privilege (HTTP 403)."""


class OPNSenseConnectionError(OPNSenseError):
    """Network/transport failure (aiohttp.ClientError / timeout)."""


# ---------------------------------------------------------------------------
# Module-level option-map conversion helpers
# ---------------------------------------------------------------------------
def selected_type(alias: dict) -> str:
    """From getItem ``alias['type']`` option-map -> single selected key (e.g. 'host')."""
    t = alias.get("type")
    if isinstance(t, dict):
        for k, v in t.items():
            if isinstance(v, dict) and v.get("selected"):
                return k
        return next(iter(t), "host")
    return t or "host"   # already a plain string


def selected_content(alias: dict) -> list[str]:
    """From getItem ``alias['content']`` option-map -> list of selected entries."""
    c = alias.get("content")
    if isinstance(c, dict):
        return [k for k, v in c.items() if isinstance(v, dict) and v.get("selected")]
    if isinstance(c, str):
        return [line for line in c.splitlines() if line.strip()]
    return []


def _is_enabled(item: dict) -> bool:
    """OPNsense ``getItem`` returns ``enabled`` as a ``"1"``/``"0"`` string."""
    return str(item.get("enabled", "1")) == "1"


class OPNSenseClient:
    """Minimal async client for the subset of the OPNsense API we need."""

    def __init__(
        self,
        url: str,
        api_key: str,
        api_secret: str,
        session: aiohttp.ClientSession,
        *,
        verify_ssl: bool = False,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        """Initialize the client.

        ``url`` is the base host URL WITHOUT the ``/api`` suffix; the client
        appends :data:`API_PREFIX`. ``session`` is an externally-managed aiohttp
        session (the caller picks ``verify_ssl`` when fetching it). The client
        also passes ``ssl=False`` per-request when ``verify_ssl`` is False, as a
        belt-and-suspenders for self-signed HTTPS endpoints.
        """
        # Normalise the user-supplied base URL: default to http:// when no
        # scheme is given (a schemeless URL makes aiohttp raise InvalidUrlClientError),
        # and tolerate a trailing /api since we always append it ourselves.
        clean = url.strip().rstrip("/")
        if not clean.startswith(("http://", "https://")):
            clean = "http://" + clean
        if clean.endswith(API_PREFIX):
            clean = clean[: -len(API_PREFIX)]
        self._base = clean + API_PREFIX
        self._auth = aiohttp.BasicAuth(api_key, api_secret)
        self._session = session
        self._verify_ssl = verify_ssl
        self._timeout = timeout
        # Precomputed ssl kwarg: None means "use default verification",
        # False means "do not verify".
        self._ssl: bool | None = None if verify_ssl else False

    # ------------------------------------------------------------------
    # Core request machinery
    # ------------------------------------------------------------------
    async def _request(
        self,
        method: str,
        path: str,
        json_body: dict | None = None,
    ) -> Any:
        """Perform a single API request and return the parsed JSON body.

        Implements the two OPNsense body quirks (see module docstring). Never
        logs auth credentials or headers.
        """
        url = self._base + path
        method = method.upper()

        kwargs: dict[str, Any] = {
            "auth": self._auth,
            "timeout": aiohttp.ClientTimeout(total=self._timeout),
            "ssl": self._ssl,
        }

        if method == "GET":
            # Quirk 1: GET must NOT send Content-Type or any body. aiohttp only
            # sets Content-Type when json=/data= is supplied, so we simply pass
            # neither.
            LOGGER.debug("opnsense %s %s", method, path)
        else:
            # Quirk 2: every POST sends a JSON body, defaulting to {}.
            body = json_body if json_body is not None else {}
            kwargs["json"] = body
            LOGGER.debug(
                "opnsense %s %s body_keys=%s", method, path, sorted(body.keys())
            )

        try:
            async with self._session.request(method, url, **kwargs) as resp:
                status = resp.status

                if status == 401:
                    raise OPNSenseAuthError(
                        "Invalid API key or secret (HTTP 401)."
                    )
                if status == 403:
                    raise OPNSensePrivilegeError(
                        "Authenticated, but the API user lacks the required "
                        "privileges (HTTP 403)."
                    )

                if status >= 400:
                    text = ""
                    try:
                        text = await resp.text()
                    except (aiohttp.ClientError, UnicodeDecodeError):
                        text = ""
                    text = text[:300]
                    raise OPNSenseError(f"HTTP {status}: {text}")

                # OPNsense sometimes returns JSON with a text/html content-type,
                # so disable aiohttp's content-type check.
                try:
                    return await resp.json(content_type=None)
                except (
                    aiohttp.ContentTypeError,
                    ValueError,
                    asyncio.TimeoutError,
                ) as err:
                    raise OPNSenseError("Invalid JSON response") from err
        except OPNSenseError:
            # Already a typed error from above; re-raise unchanged.
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise OPNSenseConnectionError(str(err)) from err

    async def _get(self, path: str) -> Any:
        """Issue a GET request (no body, no Content-Type)."""
        return await self._request("GET", path)

    async def _post(self, path: str, body: dict | None = None) -> Any:
        """Issue a POST request (body defaults to ``{}``)."""
        return await self._request("POST", path, body)

    # ------------------------------------------------------------------
    # Read methods
    # ------------------------------------------------------------------
    async def firmware_status(self) -> dict:
        """GET /core/firmware/status -> full firmware status dict."""
        result = await self._get("/core/firmware/status")
        return result if isinstance(result, dict) else {}

    async def system_information(self) -> dict:
        """GET /diagnostics/system/system_information."""
        result = await self._get("/diagnostics/system/system_information")
        return result if isinstance(result, dict) else {}

    async def gateway_status(self) -> list[dict]:
        """GET /routes/gateway/status -> the ``items`` list."""
        result = await self._get("/routes/gateway/status")
        if isinstance(result, dict):
            items = result.get("items", [])
            return items if isinstance(items, list) else []
        return []

    async def list_aliases(self) -> list[dict]:
        """POST /firewall/alias/searchItem -> the ``rows`` list."""
        result = await self._post(
            "/firewall/alias/searchItem",
            {"current": 1, "rowCount": SEARCH_ROW_COUNT},
        )
        if isinstance(result, dict):
            rows = result.get("rows", [])
            return rows if isinstance(rows, list) else []
        return []

    async def alias_list_items(self, name: str) -> list[str]:
        """GET /firewall/alias_util/list/{name} -> live pf IPs."""
        result = await self._get(f"/firewall/alias_util/list/{name}")
        if isinstance(result, dict):
            rows = result.get("rows", [])
            if isinstance(rows, list):
                return [r["ip"] for r in rows if isinstance(r, dict) and r.get("ip")]
        return []

    async def alias_get_uuid(self, name: str) -> str | None:
        """GET /firewall/alias/getAliasUUID/{name} -> uuid or None when missing."""
        result = await self._get(f"/firewall/alias/getAliasUUID/{name}")
        # OPNsense returns [] when the alias does not exist.
        if isinstance(result, dict):
            uuid = result.get("uuid")
            return uuid if uuid else None
        return None

    async def alias_get_item(self, uuid: str) -> dict:
        """GET /firewall/alias/getItem/{uuid} -> the ``alias`` dict (option-map shape)."""
        result = await self._get(f"/firewall/alias/getItem/{uuid}")
        if isinstance(result, dict):
            alias = result.get("alias", {})
            return alias if isinstance(alias, dict) else {}
        return {}

    async def search_rules(self) -> list[dict]:
        """POST /firewall/filter/searchRule -> the ``rows`` list (API rules only)."""
        result = await self._post(
            "/firewall/filter/searchRule",
            {"current": 1, "rowCount": SEARCH_ROW_COUNT},
        )
        if isinstance(result, dict):
            rows = result.get("rows", [])
            return rows if isinstance(rows, list) else []
        return []

    # ------------------------------------------------------------------
    # Write methods (low-level)
    # ------------------------------------------------------------------
    async def alias_add(self, name: str, address: str) -> dict:
        """POST /firewall/alias_util/add/{name} -> add an IP to the live pf table."""
        return await self._post(
            f"/firewall/alias_util/add/{name}", {"address": address}
        )

    async def alias_delete(self, name: str, address: str) -> dict:
        """POST /firewall/alias_util/delete/{name} -> remove an IP from the live pf table."""
        return await self._post(
            f"/firewall/alias_util/delete/{name}", {"address": address}
        )

    async def alias_flush(self, name: str) -> dict:
        """POST /firewall/alias_util/flush/{name} -> flush the live pf table."""
        return await self._post(f"/firewall/alias_util/flush/{name}", {})

    async def alias_set_item(self, uuid: str, payload: dict) -> dict:
        """POST /firewall/alias/setItem/{uuid} -> persist alias config."""
        return await self._post(
            f"/firewall/alias/setItem/{uuid}", {"alias": payload}
        )

    async def alias_toggle(self, uuid: str, enabled: bool) -> dict:
        """POST /firewall/alias/toggleItem/{uuid}/{0|1} -> enable/disable an alias."""
        return await self._post(
            f"/firewall/alias/toggleItem/{uuid}/{1 if enabled else 0}", {}
        )

    async def alias_reconfigure(self) -> dict:
        """POST /firewall/alias/reconfigure -> apply persisted alias config."""
        return await self._post("/firewall/alias/reconfigure", {})

    async def toggle_rule(self, uuid: str, enabled: bool) -> dict:
        """POST /firewall/filter/toggleRule/{uuid}/{0|1} -> enable/disable a rule."""
        return await self._post(
            f"/firewall/filter/toggleRule/{uuid}/{1 if enabled else 0}", {}
        )

    async def filter_apply(self) -> dict:
        """POST /firewall/filter/apply -> apply the filter ruleset."""
        return await self._post("/firewall/filter/apply", {})

    async def raw(
        self, method: str, path: str, body: dict | None = None
    ) -> Any:
        """Passthrough to :meth:`_request` for advanced/raw API calls."""
        return await self._request(method.upper(), path, body)

    # ------------------------------------------------------------------
    # Convenience / high-level methods
    # ------------------------------------------------------------------
    async def alias_set_content(self, name: str, addresses: list[str]) -> dict:
        """Persistently replace an alias's content and apply.

        Flow: getAliasUUID -> getItem -> setItem(content="\\n".join) -> reconfigure.
        ``reconfigure`` is REQUIRED for the change to take effect.
        """
        uuid = await self.alias_get_uuid(name)
        if not uuid:
            raise OPNSenseError(f"Alias '{name}' not found")
        item = await self.alias_get_item(uuid)        # alias dict (option-map shape)
        payload = {
            "enabled": "1" if _is_enabled(item) else "0",
            "name": item.get("name", name),
            "type": selected_type(item),
            "content": "\n".join(addresses),
            "description": item.get("description", ""),
        }
        await self.alias_set_item(uuid, payload)        # {"result":"saved"}
        return await self.alias_reconfigure()           # {"status":"ok"}  REQUIRED to apply

    async def alias_block_device(self, name: str, address: str) -> dict:
        """Persistently add ``address`` to alias ``name`` (deduped) and apply."""
        current = await self._current_content(name)
        if address not in current:
            current.append(address)
        return await self.alias_set_content(name, current)

    async def alias_unblock_device(self, name: str, address: str) -> dict:
        """Persistently remove ``address`` from alias ``name`` and apply."""
        current = await self._current_content(name)
        current = [a for a in current if a != address]
        return await self.alias_set_content(name, current)

    async def _current_content(self, name: str) -> list[str]:
        """Read the persisted (config) selected content list for an alias.

        Reads via ``getItem`` (the config form), NOT the live pf table, so the
        rebuild done by :meth:`alias_set_content` is authoritative.
        """
        uuid = await self.alias_get_uuid(name)
        if not uuid:
            raise OPNSenseError(f"Alias '{name}' not found")
        item = await self.alias_get_item(uuid)
        return selected_content(item)
