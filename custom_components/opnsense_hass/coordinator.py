"""DataUpdateCoordinator for the opnsense_hass integration."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import OPNSenseAuthError, OPNSenseClient, OPNSenseError
from .const import (
    CONF_NAME,
    CONF_SCAN_INTERVAL,
    CONF_TRACKED_ALIASES,
    CONF_URL,
    DATA_ALIAS_ITEMS,
    DATA_ALIASES,
    DATA_FIRMWARE,
    DATA_GATEWAYS,
    DATA_RULES,
    DATA_SYSTEM,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
    MANUFACTURER,
)

type OPNSenseConfigEntry = ConfigEntry[OPNSenseCoordinator]


def _strip_float(s: Any) -> float | None:
    """Parse a possibly unit-suffixed number into a float.

    Accepts ``"15.6 ms"`` / ``"0.0 %"`` / ``15.6`` / ``None``. Splits on
    whitespace, takes the first token, and coerces with ``float()``. Returns
    ``None`` on any failure.
    """
    if s is None:
        return None
    try:
        token = str(s).split()[0]
        return float(token)
    except (ValueError, TypeError, IndexError):
        return None


def _to_bool(v: Any) -> bool:
    """Coerce OPNsense truthy representations into a bool.

    Alias/rule rows return ``enabled`` as ``"1"``/``"0"`` (or ``1``/``0``).
    """
    return str(v) in ("1", "True", "true")


def _normalize_updates(updates: Any) -> Any:
    """Normalize the build-dependent ``updates`` shape into a count when possible.

    - int -> used as-is
    - dict with ``"updates"`` / ``"upgrade_packages"`` list -> its length
    - anything else -> 0
    """
    if isinstance(updates, bool):
        # bool is an int subclass; treat True as 1, False as 0.
        return int(updates)
    if isinstance(updates, int):
        return updates
    if isinstance(updates, str):
        try:
            return int(updates.strip())
        except (ValueError, AttributeError):
            return 0
    if isinstance(updates, dict):
        for key in ("updates", "upgrade_packages"):
            value = updates.get(key)
            if isinstance(value, list):
                return len(value)
            if isinstance(value, int):
                return value
        return 0
    return 0


class OPNSenseCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls the OPNsense API and exposes a normalized data dict."""

    config_entry: OPNSenseConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: OPNSenseConfigEntry,
        client: OPNSenseClient,
    ) -> None:
        """Initialize the coordinator."""
        scan = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            logger=LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan),
            config_entry=entry,
            always_update=False,
        )
        self.client = client
        self.device_info: DeviceInfo | None = None

    @property
    def tracked_aliases(self) -> list[str]:
        """Alias names selected for live polling / extra entities."""
        return self.config_entry.options.get(CONF_TRACKED_ALIASES, [])

    async def _async_setup(self) -> None:
        """One-time setup: build the device identity from firmware + sysinfo."""
        try:
            fw = await self.client.firmware_status()
            sysinfo = await self.client.system_information()
        except OPNSenseAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except OPNSenseError as err:
            raise UpdateFailed(str(err)) from err

        product = (fw or {}).get("product", {})
        if not isinstance(product, dict):
            product = {}
        versions = (sysinfo or {}).get("versions", [])
        if not isinstance(versions, list):
            versions = []

        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, self.config_entry.entry_id)},
            name=sysinfo.get("name")
            or self.config_entry.data.get(CONF_NAME, "OPNsense"),
            manufacturer=MANUFACTURER,
            model=versions[0] if versions else "OPNsense",
            sw_version=product.get("product_version"),
            configuration_url=self.config_entry.data.get(CONF_URL),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Poll the API and return the normalized coordinator data dict."""
        try:
            fw = await self.client.firmware_status()
            sysinfo = await self.client.system_information()
            gateways_raw = await self.client.gateway_status()
            aliases_raw = await self.client.list_aliases()
            rules_raw = await self.client.search_rules()

            alias_items: dict[str, list[str]] = {}
            for name in self.tracked_aliases:
                try:
                    alias_items[name] = await self.client.alias_list_items(name)
                except OPNSenseAuthError:
                    # Auth failures must propagate to the outer handler so they
                    # convert to ConfigEntryAuthFailed and trigger reauth.
                    raise
                except OPNSenseError as err:
                    # One bad/missing alias name must not fail the whole update.
                    LOGGER.debug(
                        "Skipping live items for alias %s: %s", name, err
                    )
        except OPNSenseAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except OPNSenseError as err:
            raise UpdateFailed(str(err)) from err

        return {
            DATA_FIRMWARE: self._build_firmware(fw),
            DATA_SYSTEM: self._build_system(sysinfo),
            DATA_GATEWAYS: self._build_gateways(gateways_raw),
            DATA_ALIASES: self._build_aliases(aliases_raw),
            DATA_ALIAS_ITEMS: alias_items,
            DATA_RULES: self._build_rules(rules_raw),
        }

    # ------------------------------------------------------------------
    # Normalization helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _build_firmware(fw: dict) -> dict[str, Any]:
        """Normalize the firmware status dict."""
        product = (fw or {}).get("product", {})
        if not isinstance(product, dict):
            product = {}
        return {
            "product_version": product.get("product_version"),
            "product_abi": product.get("product_abi"),
            "raw": fw or {},
        }

    @staticmethod
    def _build_system(sysinfo: dict) -> dict[str, Any]:
        """Normalize the system_information dict."""
        versions = (sysinfo or {}).get("versions", [])
        if not isinstance(versions, list):
            versions = []
        return {
            "name": (sysinfo or {}).get("name"),
            "versions": versions,
            "updates": _normalize_updates((sysinfo or {}).get("updates")),
            "version": versions[0] if versions else "",
        }

    @staticmethod
    def _build_gateways(gateways_raw: list[dict]) -> dict[str, dict[str, Any]]:
        """Normalize the gateway status list into a name-keyed dict."""
        gateways: dict[str, dict[str, Any]] = {}
        for gw in gateways_raw:
            if not isinstance(gw, dict):
                continue
            name = gw.get("name")
            if not name:
                continue
            gateways[name] = {
                "name": name,
                "address": gw.get("address"),
                "status": gw.get("status"),
                "status_translated": gw.get("status_translated"),
                "loss": _strip_float(gw.get("loss")),
                "delay": _strip_float(gw.get("delay")),
                "stddev": _strip_float(gw.get("stddev")),
                "monitor": gw.get("monitor"),
            }
        return gateways

    @staticmethod
    def _build_aliases(aliases_raw: list[dict]) -> dict[str, dict[str, Any]]:
        """Normalize the alias rows into a name-keyed dict."""
        aliases: dict[str, dict[str, Any]] = {}
        for row in aliases_raw:
            if not isinstance(row, dict):
                continue
            name = row.get("name")
            if not name:
                continue
            current_items = row.get("current_items")
            try:
                count = int(current_items)
            except (ValueError, TypeError):
                count = 0
            aliases[name] = {
                "uuid": row.get("uuid"),
                "name": name,
                "type": row.get("type"),
                "enabled": _to_bool(row.get("enabled")),
                "current_items": count,
                "description": row.get("description", ""),
            }
        return aliases

    @staticmethod
    def _build_rules(rules_raw: list[dict]) -> dict[str, dict[str, Any]]:
        """Normalize the filter rule rows into a uuid-keyed dict."""
        rules: dict[str, dict[str, Any]] = {}
        for row in rules_raw:
            if not isinstance(row, dict):
                continue
            uuid = row.get("uuid")
            if not uuid:
                continue
            rules[uuid] = {
                "uuid": uuid,
                "enabled": _to_bool(row.get("enabled")),
                "description": row.get("description", ""),
                "action": row.get("action"),
                "sequence": row.get("sequence"),
            }
        return rules
