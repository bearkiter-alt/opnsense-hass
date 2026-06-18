"""DataUpdateCoordinator for the opnsense_hass integration."""
from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable
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
    CONF_TOP_INTERFACE,
    CONF_TRACKED_ALIASES,
    CONF_URL,
    DATA_ALIAS_DEVICES,
    DATA_ALIAS_ITEMS,
    DATA_ALIASES,
    DATA_FIRMWARE,
    DATA_GATEWAYS,
    DATA_HEALTH,
    DATA_HOSTS,
    DATA_RULES,
    DATA_SYSTEM,
    DATA_TOP_TALKERS,
    DATA_TRAFFIC,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TOP_INTERFACE,
    DOMAIN,
    LOGGER,
    MANUFACTURER,
    TOP_TALKERS_LIMIT,
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


def _to_int(s: Any) -> int | None:
    """Parse a counter (string or int) into an int, or None on failure.

    Interface byte/packet counters arrive as strings (e.g. ``"49928705960"``).
    """
    if s is None:
        return None
    try:
        return int(str(s).strip())
    except (ValueError, TypeError):
        return None


def _pct(used: float | None, total: float | None) -> float | None:
    """Return ``used/total`` as a 0–100 percentage rounded to 1dp, or None."""
    if not total or used is None:
        return None
    try:
        return round(used / total * 100, 1)
    except (ZeroDivisionError, TypeError):
        return None


def _parse_loadavg(s: Any) -> list[float]:
    """Parse ``"0.48, 0.41, 0.36"`` into ``[0.48, 0.41, 0.36]`` (best effort)."""
    out: list[float] = []
    for tok in str(s or "").replace(",", " ").split():
        try:
            out.append(float(tok))
        except ValueError:
            break
    return out


def _parse_uptime(s: Any) -> int | None:
    """Parse OPNsense uptime (``"5 days, 11:42:05"``) into total seconds."""
    text = str(s or "")
    days = 0
    m = re.search(r"(\d+)\s*day", text)
    if m:
        days = int(m.group(1))
    hms = re.search(r"(\d{1,2}):(\d{2}):(\d{2})", text)
    if not hms and not m:
        return None
    h, mnt, sec = (int(g) for g in hms.groups()) if hms else (0, 0, 0)
    return days * 86400 + h * 3600 + mnt * 60 + sec


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
        # device-name map (vtnet0 -> "LAN"), fetched once in _async_setup.
        self._if_names: dict[str, str] = {}
        # per-interface previous (bytes_in, bytes_out, monotonic_ts) for rate calc.
        self._prev_traffic: dict[str, tuple[int, int, float]] = {}
        # CPU identity (cores used to normalise load average), fetched once.
        self._cpu_model: str = ""
        self._cpu_cores: int | None = None

    @property
    def tracked_aliases(self) -> list[str]:
        """Alias names selected for live polling / extra entities."""
        return self.config_entry.options.get(CONF_TRACKED_ALIASES, [])

    @property
    def top_interface(self) -> str:
        """Interface key polled for top-talkers (default the LAN side)."""
        return self.config_entry.options.get(
            CONF_TOP_INTERFACE, DEFAULT_TOP_INTERFACE
        )

    async def _async_setup(self) -> None:
        """One-time setup: build the device identity from firmware + sysinfo."""
        try:
            fw = await self.client.firmware_status()
            sysinfo = await self.client.system_information()
        except OPNSenseAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except OPNSenseError as err:
            raise UpdateFailed(str(err)) from err

        # Interface friendly-name map is best-effort; missing it only costs us
        # nicer entity names, never the whole setup.
        try:
            self._if_names = await self.client.interface_names()
        except OPNSenseError as err:
            LOGGER.debug("interface_names unavailable: %s", err)
            self._if_names = {}

        # CPU identity is static; fetch once so we can normalise load average to
        # a percentage (load / cores). Best-effort.
        try:
            self._cpu_model = await self.client.cpu_type()
            m = re.search(r"(\d+)\s*cores?", self._cpu_model)
            self._cpu_cores = int(m.group(1)) if m else None
        except OPNSenseError as err:
            LOGGER.debug("cpu_type unavailable: %s", err)

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

            # Best-effort extras: a missing privilege or disabled plugin must
            # not take down the core poll, so each falls back to a default.
            traffic_raw = await self._safe(self.client.traffic_interfaces, {})
            dhcp_rows = await self._safe(self.client.dhcp_leases, [])
            arp_rows = await self._safe(self.client.arp_table, [])
            top_records = await self._safe(
                lambda: self.client.top_talkers(self.top_interface), []
            )
            systime = await self._safe(self.client.system_time, {})
            resources = await self._safe(self.client.system_resources, {})
            disks = await self._safe(self.client.system_disk, [])
            swap = await self._safe(self.client.system_swap, [])
            mbuf = await self._safe(self.client.system_mbuf, {})
            states = await self._safe(self.client.pf_states, {})
            services = await self._safe(self.client.services, [])
        except OPNSenseAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except OPNSenseError as err:
            raise UpdateFailed(str(err)) from err

        hosts = self._build_hosts(dhcp_rows, arp_rows)
        alias_devices = {
            name: [self._device_for(ip, hosts) for ip in ips]
            for name, ips in alias_items.items()
        }

        return {
            DATA_FIRMWARE: self._build_firmware(fw),
            DATA_SYSTEM: self._build_system(sysinfo),
            DATA_GATEWAYS: self._build_gateways(gateways_raw),
            DATA_ALIASES: self._build_aliases(aliases_raw),
            DATA_ALIAS_ITEMS: alias_items,
            DATA_RULES: self._build_rules(rules_raw),
            DATA_HOSTS: hosts,
            DATA_TRAFFIC: self._build_traffic(traffic_raw),
            DATA_TOP_TALKERS: self._build_top(top_records, hosts),
            DATA_ALIAS_DEVICES: alias_devices,
            DATA_HEALTH: self._build_health(
                systime, resources, disks, swap, mbuf, states, services,
                dhcp_rows, arp_rows,
            ),
        }

    async def _safe(
        self, factory: Callable[[], Awaitable[Any]], default: Any
    ) -> Any:
        """Await an optional API call, swallowing non-auth errors.

        Auth failures are re-raised so the caller converts them to
        ConfigEntryAuthFailed; any other OPNsense error returns ``default`` so a
        single unavailable endpoint never fails the whole refresh.
        """
        try:
            return await factory()
        except OPNSenseAuthError:
            raise
        except OPNSenseError as err:
            LOGGER.debug("optional fetch failed, using default: %s", err)
            return default

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

    # ------------------------------------------------------------------
    # Host identity + traffic builders
    # ------------------------------------------------------------------
    @staticmethod
    def _build_hosts(
        dhcp_rows: list[dict], arp_rows: list[dict]
    ) -> dict[str, dict[str, Any]]:
        """Build an ip -> {mac, name, manufacturer, online} identity index.

        DHCP leases are the primary source (they carry a hostname/description);
        ARP fills in static-IP devices and any MAC/manufacturer still missing.
        """
        hosts: dict[str, dict[str, Any]] = {}
        for lease in dhcp_rows:
            if not isinstance(lease, dict):
                continue
            ip = lease.get("address")
            if not ip:
                continue
            hostname = (lease.get("hostname") or "").strip()
            hosts[ip] = {
                "mac": (lease.get("mac") or "").strip() or None,
                "name": (lease.get("descr") or "").strip() or hostname,
                "hostname": hostname,
                "manufacturer": (lease.get("man") or "").strip(),
                "online": str(lease.get("status", "")).lower() == "online",
            }
        for arp in arp_rows:
            if not isinstance(arp, dict):
                continue
            ip = arp.get("ip")
            if not ip:
                continue
            mac = (arp.get("mac") or "").strip() or None
            manuf = (arp.get("manufacturer") or "").strip()
            host = hosts.get(ip)
            if host is None:
                hostname = (arp.get("hostname") or "").strip()
                hosts[ip] = {
                    "mac": mac,
                    "name": hostname,
                    "hostname": hostname,
                    "manufacturer": manuf,
                    # An ARP entry that has not expired means the host is live.
                    "online": not arp.get("expired", False),
                }
            else:
                if not host.get("mac"):
                    host["mac"] = mac
                if not host.get("manufacturer"):
                    host["manufacturer"] = manuf
        return hosts

    @staticmethod
    def _device_for(ip: str, hosts: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """Resolve an IP into a friendly device dict, falling back to the IP."""
        host = hosts.get(ip, {})
        name = (
            host.get("name")
            or host.get("hostname")
            or host.get("manufacturer")
            or ip
        )
        return {
            "ip": ip,
            "mac": host.get("mac"),
            "name": name,
            "manufacturer": host.get("manufacturer") or None,
            "online": host.get("online"),
        }

    def _build_traffic(
        self, interfaces: dict[str, dict]
    ) -> dict[str, dict[str, Any]]:
        """Normalize per-interface counters and derive in/out bit rates.

        Rates come from the delta against the previous poll (bytes*8/seconds);
        the first poll and any counter reset yield ``None`` until the next pass.
        """
        now = time.monotonic()
        out: dict[str, dict[str, Any]] = {}
        for iface, info in (interfaces or {}).items():
            if not isinstance(info, dict):
                continue
            device = info.get("device")
            bytes_in = _to_int(info.get("bytes received"))
            bytes_out = _to_int(info.get("bytes transmitted"))
            rate_in: float | None = None
            rate_out: float | None = None
            prev = self._prev_traffic.get(iface)
            if prev and bytes_in is not None and bytes_out is not None:
                p_in, p_out, p_ts = prev
                dt = now - p_ts
                if dt > 0:
                    if bytes_in >= p_in:
                        rate_in = (bytes_in - p_in) * 8 / dt
                    if bytes_out >= p_out:
                        rate_out = (bytes_out - p_out) * 8 / dt
            if bytes_in is not None and bytes_out is not None:
                self._prev_traffic[iface] = (bytes_in, bytes_out, now)
            # FreeBSD link state: "2" == LINK_STATE_UP.
            link_state = info.get("link state")
            out[iface] = {
                "device": device,
                "label": self._if_names.get(device) or iface.upper(),
                "bytes_in": bytes_in,
                "bytes_out": bytes_out,
                "packets_in": _to_int(info.get("packets received")),
                "packets_out": _to_int(info.get("packets transmitted")),
                "rate_in_bits": round(rate_in) if rate_in is not None else None,
                "rate_out_bits": round(rate_out) if rate_out is not None else None,
                "rate_total_bits": (
                    round(rate_in + rate_out)
                    if rate_in is not None and rate_out is not None
                    else None
                ),
                "link_up": str(link_state) == "2" if link_state is not None else None,
            }
        return out

    def _build_top(
        self, records: list[dict], hosts: dict[str, dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Enrich top-talker records with friendly names; sort + cap the list."""
        talkers: list[dict[str, Any]] = []
        for rec in records or []:
            if not isinstance(rec, dict):
                continue
            ip = rec.get("address")
            if not ip:
                continue
            device = self._device_for(ip, hosts)
            talkers.append(
                {
                    "ip": ip,
                    "name": device["name"],
                    "mac": device["mac"],
                    "rate_in_bits": rec.get("rate_bits_in"),
                    "rate_out_bits": rec.get("rate_bits_out"),
                    "rate_bits": rec.get("rate_bits"),
                    "rate": rec.get("rate"),
                }
            )
        talkers.sort(key=lambda t: t.get("rate_bits") or 0, reverse=True)
        return talkers[:TOP_TALKERS_LIMIT]

    def _build_health(
        self,
        systime: dict,
        resources: dict,
        disks: list[dict],
        swap: list[dict],
        mbuf: dict,
        states: dict,
        services: list[dict],
        dhcp_rows: list[dict],
        arp_rows: list[dict],
    ) -> dict[str, Any]:
        """Fold the system-health endpoints into one flat metrics dict."""
        health: dict[str, Any] = {"cpu_model": self._cpu_model, "cpu_cores": self._cpu_cores}

        # CPU: load average + normalised usage (load_1m / cores).
        load = _parse_loadavg(systime.get("loadavg"))
        health["load_1m"] = load[0] if len(load) > 0 else None
        health["load_5m"] = load[1] if len(load) > 1 else None
        health["load_15m"] = load[2] if len(load) > 2 else None
        if health["load_1m"] is not None and self._cpu_cores:
            health["cpu_usage"] = round(health["load_1m"] / self._cpu_cores * 100, 1)
        else:
            health["cpu_usage"] = None
        health["uptime"] = systime.get("uptime")
        health["uptime_seconds"] = _parse_uptime(systime.get("uptime"))

        # Memory + ZFS ARC (frmt fields are already in MB).
        mem = resources.get("memory", {}) if isinstance(resources, dict) else {}
        used = _to_int(mem.get("used"))
        total = _to_int(mem.get("total"))
        health["memory_usage"] = _pct(used, total)
        health["memory_used_mb"] = _to_int(mem.get("used_frmt"))
        health["memory_total_mb"] = _to_int(mem.get("total_frmt"))
        health["arc_mb"] = _to_int(mem.get("arc_frmt"))

        # Swap (KB totals across all swap devices).
        s_total = sum(_to_int(d.get("total")) or 0 for d in swap)
        s_used = sum(_to_int(d.get("used")) or 0 for d in swap)
        health["swap_usage"] = _pct(s_used, s_total) if s_total else 0.0

        # Disk: headline = the root filesystem; keep the full list as an attr.
        root = next((d for d in disks if d.get("mountpoint") == "/"), None)
        if root is None and disks:
            root = disks[0]
        health["disk_usage"] = (root or {}).get("used_pct")
        health["filesystems"] = [
            {
                "mountpoint": d.get("mountpoint"),
                "used_pct": d.get("used_pct"),
                "used": d.get("used"),
                "available": d.get("available"),
            }
            for d in disks
            if isinstance(d, dict)
        ]

        # Firewall state table.
        cur = _to_int(states.get("current"))
        lim = _to_int(states.get("limit"))
        health["states_current"] = cur
        health["states_limit"] = lim
        health["states_usage"] = _pct(cur, lim)

        # mbuf clusters (the constrained resource on busy firewalls).
        mb_cur = _to_int(mbuf.get("mbuf-current"))
        mb_max = _to_int(mbuf.get("cluster-max")) or _to_int(mbuf.get("mbuf-total"))
        health["mbuf_current"] = mb_cur
        health["mbuf_max"] = mb_max
        health["mbuf_usage"] = _pct(mb_cur, mb_max)

        # Services.
        running = [s for s in services if str(s.get("running")) == "1"]
        health["services_running"] = len(running)
        health["services_total"] = len(services)
        health["services_stopped"] = sorted(
            s.get("name") for s in services if str(s.get("running")) != "1" and s.get("name")
        )

        # Leases + ARP counts.
        health["dhcp_total"] = len(dhcp_rows)
        health["dhcp_online"] = sum(
            1 for d in dhcp_rows if str(d.get("status", "")).lower() == "online"
        )
        health["arp_entries"] = len(arp_rows)
        return health
