"""Sensor platform for the opnsense_hass integration.

Builds, at setup time, from the coordinator data:

* two diagnostic sensors per gateway (delay in ms, packet loss in %);
* two system sensors (OPNsense version string, count of available updates);
* per-interface traffic sensors (live in/out bit rate + cumulative bytes);
* a top-talkers summary sensor (count + an enriched per-device list);
* one item-count sensor per *tracked* host alias, whose attributes carry the
  alias's devices with friendly names + MAC addresses.

All sensors are :class:`CoordinatorEntity` instances that share the coordinator's
device and read their values straight from ``coordinator.data``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfDataRate,
    UnitOfInformation,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DATA_ALIAS_DEVICES,
    DATA_ALIAS_ITEMS,
    DATA_ALIASES,
    DATA_GATEWAYS,
    DATA_HEALTH,
    DATA_SYSTEM,
    DATA_TOP_TALKERS,
    DATA_TRAFFIC,
)
from .coordinator import OPNSenseConfigEntry, OPNSenseCoordinator

# Direction key ("in"/"out") -> the OPNsense-perspective word used in names.
_DIRECTION_LABEL = {"in": "received", "out": "transmitted"}


@dataclass(frozen=True, kw_only=True)
class OPNSenseHealthSensorDescription(SensorEntityDescription):
    """Describes a system-health sensor read from the DATA_HEALTH dict."""

    value_fn: Callable[[dict[str, Any]], StateType]
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


HEALTH_SENSORS: tuple[OPNSenseHealthSensorDescription, ...] = (
    OPNSenseHealthSensorDescription(
        key="cpu_usage",
        name="CPU usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:cpu-64-bit",
        value_fn=lambda h: h.get("cpu_usage"),
        attrs_fn=lambda h: {
            "load_1m": h.get("load_1m"),
            "load_5m": h.get("load_5m"),
            "load_15m": h.get("load_15m"),
            "cores": h.get("cpu_cores"),
            "model": h.get("cpu_model"),
        },
    ),
    OPNSenseHealthSensorDescription(
        key="load_1m",
        name="Load average",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        icon="mdi:gauge",
        value_fn=lambda h: h.get("load_1m"),
        attrs_fn=lambda h: {
            "1m": h.get("load_1m"),
            "5m": h.get("load_5m"),
            "15m": h.get("load_15m"),
        },
    ),
    OPNSenseHealthSensorDescription(
        key="memory_usage",
        name="Memory usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:memory",
        value_fn=lambda h: h.get("memory_usage"),
        attrs_fn=lambda h: {
            "used_mb": h.get("memory_used_mb"),
            "total_mb": h.get("memory_total_mb"),
            "arc_mb": h.get("arc_mb"),
        },
    ),
    OPNSenseHealthSensorDescription(
        key="swap_usage",
        name="Swap usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:harddisk",
        value_fn=lambda h: h.get("swap_usage"),
    ),
    OPNSenseHealthSensorDescription(
        key="disk_usage",
        name="Disk usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:harddisk",
        value_fn=lambda h: h.get("disk_usage"),
        attrs_fn=lambda h: {"filesystems": h.get("filesystems")},
    ),
    OPNSenseHealthSensorDescription(
        key="uptime",
        name="Uptime",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.DAYS,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda h: h.get("uptime_seconds"),
        attrs_fn=lambda h: {"uptime": h.get("uptime")},
    ),
    OPNSenseHealthSensorDescription(
        key="states_current",
        name="Firewall states",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:state-machine",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda h: h.get("states_current"),
        attrs_fn=lambda h: {
            "limit": h.get("states_limit"),
            "usage_percent": h.get("states_usage"),
        },
    ),
    OPNSenseHealthSensorDescription(
        key="states_usage",
        name="State table usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:state-machine",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda h: h.get("states_usage"),
    ),
    OPNSenseHealthSensorDescription(
        key="mbuf_usage",
        name="mbuf usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:network",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda h: h.get("mbuf_usage"),
        attrs_fn=lambda h: {
            "current": h.get("mbuf_current"),
            "max": h.get("mbuf_max"),
        },
    ),
    OPNSenseHealthSensorDescription(
        key="services_running",
        name="Services running",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:cogs",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda h: h.get("services_running"),
        attrs_fn=lambda h: {
            "total": h.get("services_total"),
            "stopped": h.get("services_stopped"),
        },
    ),
    OPNSenseHealthSensorDescription(
        key="dhcp_online",
        name="DHCP leases online",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:ip-network",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda h: h.get("dhcp_online"),
        attrs_fn=lambda h: {"total": h.get("dhcp_total")},
    ),
    OPNSenseHealthSensorDescription(
        key="dhcp_total",
        name="DHCP leases total",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:ip-network-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda h: h.get("dhcp_total"),
    ),
    OPNSenseHealthSensorDescription(
        key="arp_entries",
        name="ARP entries",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:lan",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda h: h.get("arp_entries"),
    ),
    OPNSenseHealthSensorDescription(
        key="memory_used_mb",
        name="Memory used",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.MEGABYTES,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda h: h.get("memory_used_mb"),
    ),
    OPNSenseHealthSensorDescription(
        key="arc_mb",
        name="ZFS ARC size",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.MEGABYTES,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:database",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda h: h.get("arc_mb"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OPNSenseConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the OPNsense sensors."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = []

    # Per-gateway delay + loss sensors.
    for gw_name in coordinator.data.get(DATA_GATEWAYS, {}):
        entities.append(OPNSenseGatewayDelaySensor(coordinator, gw_name))
        entities.append(OPNSenseGatewayLossSensor(coordinator, gw_name))

    # System sensors.
    entities.append(OPNSenseVersionSensor(coordinator))
    entities.append(OPNSenseUpdatesSensor(coordinator))

    # Per-interface traffic sensors: live rate + cumulative bytes, each direction.
    for iface in coordinator.data.get(DATA_TRAFFIC, {}):
        for direction in ("in", "out"):
            entities.append(
                OPNSenseTrafficRateSensor(coordinator, iface, direction)
            )
            entities.append(
                OPNSenseTrafficBytesSensor(coordinator, iface, direction)
            )

    # Top-talkers summary (always present; the list lives in its attributes).
    entities.append(OPNSenseTopTalkersSensor(coordinator))

    # System-health sensors — created only for metrics this API user can read.
    health = coordinator.data.get(DATA_HEALTH, {})
    for desc in HEALTH_SENSORS:
        if desc.value_fn(health) is not None:
            entities.append(OPNSenseHealthSensor(coordinator, desc))

    # Per-tracked-alias item-count sensors (only for aliases that exist).
    aliases: dict[str, Any] = coordinator.data.get(DATA_ALIASES, {})
    for name in coordinator.tracked_aliases:
        if name in aliases:
            entities.append(OPNSenseAliasItemsSensor(coordinator, name))

    async_add_entities(entities)


class OPNSenseGatewayDelaySensor(
    CoordinatorEntity[OPNSenseCoordinator], SensorEntity
):
    """Round-trip delay for a single OPNsense gateway, in milliseconds."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTime.MILLISECONDS
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: OPNSenseCoordinator, gw_name: str) -> None:
        """Initialise the gateway delay sensor."""
        super().__init__(coordinator)
        self._gw = gw_name
        self._attr_name = f"{gw_name} delay"
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_gw_{gw_name}_delay"
        )
        self._attr_device_info = coordinator.device_info

    @callback
    def _gw_data(self) -> dict[str, Any]:
        return self.coordinator.data.get(DATA_GATEWAYS, {}).get(self._gw, {})

    @property
    def available(self) -> bool:
        """Return True while the gateway is still present in coordinator data."""
        return super().available and self._gw in self.coordinator.data.get(
            DATA_GATEWAYS, {}
        )

    @property
    def native_value(self) -> float | None:
        """Return the gateway delay in milliseconds."""
        return self._gw_data().get("delay")


class OPNSenseGatewayLossSensor(
    CoordinatorEntity[OPNSenseCoordinator], SensorEntity
):
    """Packet loss for a single OPNsense gateway, as a percentage."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: OPNSenseCoordinator, gw_name: str) -> None:
        """Initialise the gateway loss sensor."""
        super().__init__(coordinator)
        self._gw = gw_name
        self._attr_name = f"{gw_name} loss"
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_gw_{gw_name}_loss"
        )
        self._attr_device_info = coordinator.device_info

    @callback
    def _gw_data(self) -> dict[str, Any]:
        return self.coordinator.data.get(DATA_GATEWAYS, {}).get(self._gw, {})

    @property
    def available(self) -> bool:
        """Return True while the gateway is still present in coordinator data."""
        return super().available and self._gw in self.coordinator.data.get(
            DATA_GATEWAYS, {}
        )

    @property
    def native_value(self) -> float | None:
        """Return the gateway packet loss percentage."""
        return self._gw_data().get("loss")


class OPNSenseVersionSensor(CoordinatorEntity[OPNSenseCoordinator], SensorEntity):
    """The running OPNsense version string."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: OPNSenseCoordinator) -> None:
        """Initialise the version sensor."""
        super().__init__(coordinator)
        self._attr_name = "Version"
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_version"
        self._attr_device_info = coordinator.device_info

    @property
    def native_value(self) -> StateType:
        """Return the OPNsense version string (capped to HA's 255-char limit)."""
        value = self.coordinator.data.get(DATA_SYSTEM, {}).get("version")
        if isinstance(value, str):
            return value[:255]
        return value


class OPNSenseUpdatesSensor(CoordinatorEntity[OPNSenseCoordinator], SensorEntity):
    """Number of pending OPNsense updates."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: OPNSenseCoordinator) -> None:
        """Initialise the updates-available sensor."""
        super().__init__(coordinator)
        self._attr_name = "Updates available"
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_updates_available"
        )
        self._attr_device_info = coordinator.device_info

    @property
    def native_value(self) -> StateType:
        """Return the normalised update count, or the raw value as a string."""
        value = self.coordinator.data.get(DATA_SYSTEM, {}).get("updates")
        if isinstance(value, bool):
            # bool is an int subclass; never report True/False as a count.
            return str(value)
        if isinstance(value, int):
            return value
        if value is None:
            return None
        return str(value)


class OPNSenseAliasItemsSensor(
    CoordinatorEntity[OPNSenseCoordinator], SensorEntity
):
    """Number of entries currently in a tracked host alias."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = None

    def __init__(self, coordinator: OPNSenseCoordinator, name: str) -> None:
        """Initialise the alias item-count sensor."""
        super().__init__(coordinator)
        self._name = name
        self._attr_name = f"{name} items"
        alias = coordinator.data.get(DATA_ALIASES, {}).get(name, {})
        uuid = alias.get("uuid")
        suffix = uuid if uuid else name
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_alias_{suffix}_items"
        )
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        """Return True while the alias is still present in coordinator data."""
        return super().available and self._name in self.coordinator.data.get(
            DATA_ALIASES, {}
        )

    @property
    def native_value(self) -> int:
        """Return the live pf item count, falling back to the config count."""
        live = self.coordinator.data.get(DATA_ALIAS_ITEMS, {}).get(self._name)
        if live is not None:
            return len(live)
        alias = self.coordinator.data.get(DATA_ALIASES, {}).get(self._name, {})
        return int(alias.get("current_items", 0))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the live addresses plus enriched per-device details.

        ``devices`` resolves each address to its friendly name + MAC (from DHCP
        leases, ARP fallback), which is what dashboards/automations consume.
        """
        return {
            "addresses": self.coordinator.data.get(DATA_ALIAS_ITEMS, {}).get(
                self._name, []
            ),
            "devices": self.coordinator.data.get(DATA_ALIAS_DEVICES, {}).get(
                self._name, []
            ),
        }


class _OPNSenseTrafficBase(CoordinatorEntity[OPNSenseCoordinator], SensorEntity):
    """Shared plumbing for per-interface, per-direction traffic sensors."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: OPNSenseCoordinator, iface: str, direction: str
    ) -> None:
        """Initialise a traffic sensor for one interface key and direction."""
        super().__init__(coordinator)
        self._iface = iface
        self._direction = direction  # "in" | "out"
        self._attr_device_info = coordinator.device_info

    @callback
    def _iface_data(self) -> dict[str, Any]:
        return self.coordinator.data.get(DATA_TRAFFIC, {}).get(self._iface, {})

    @property
    def _label(self) -> str:
        return self._iface_data().get("label") or self._iface.upper()

    @property
    def available(self) -> bool:
        """Return True while the interface is still present in coordinator data."""
        return super().available and self._iface in self.coordinator.data.get(
            DATA_TRAFFIC, {}
        )


class OPNSenseTrafficRateSensor(_OPNSenseTrafficBase):
    """Live throughput for one interface + direction, in bits per second."""

    _attr_device_class = SensorDeviceClass.DATA_RATE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfDataRate.BITS_PER_SECOND
    _attr_suggested_unit_of_measurement = UnitOfDataRate.MEGABITS_PER_SECOND
    _attr_suggested_display_precision = 2

    def __init__(
        self, coordinator: OPNSenseCoordinator, iface: str, direction: str
    ) -> None:
        """Initialise the rate sensor."""
        super().__init__(coordinator, iface, direction)
        self._attr_name = f"{self._label} {_DIRECTION_LABEL[direction]} rate"
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_traffic_{iface}_{direction}_rate"
        )

    @property
    def native_value(self) -> int | None:
        """Return the derived bit rate for this direction (None until 2nd poll)."""
        return self._iface_data().get(f"rate_{self._direction}_bits")


class OPNSenseTrafficBytesSensor(_OPNSenseTrafficBase):
    """Cumulative byte counter for one interface + direction."""

    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfInformation.BYTES
    _attr_suggested_unit_of_measurement = UnitOfInformation.GIGABYTES
    _attr_suggested_display_precision = 2

    def __init__(
        self, coordinator: OPNSenseCoordinator, iface: str, direction: str
    ) -> None:
        """Initialise the cumulative-bytes sensor."""
        super().__init__(coordinator, iface, direction)
        self._attr_name = f"{self._label} {_DIRECTION_LABEL[direction]}"
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_traffic_{iface}_{direction}_bytes"
        )

    @property
    def native_value(self) -> int | None:
        """Return the cumulative byte counter for this direction."""
        return self._iface_data().get(f"bytes_{self._direction}")


class OPNSenseTopTalkersSensor(
    CoordinatorEntity[OPNSenseCoordinator], SensorEntity
):
    """Number of active top-talkers on the polled interface.

    The state is the talker count; the ranked per-device list (name, IP, MAC,
    in/out rate) is exposed in the ``talkers`` attribute.
    """

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:chart-timeline-variant"

    def __init__(self, coordinator: OPNSenseCoordinator) -> None:
        """Initialise the top-talkers sensor."""
        super().__init__(coordinator)
        self._attr_name = "Top talkers"
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_top_talkers"
        self._attr_device_info = coordinator.device_info

    @property
    def native_value(self) -> int:
        """Return the number of active talkers."""
        return len(self.coordinator.data.get(DATA_TOP_TALKERS, []))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the interface polled and the ranked, name-resolved talkers."""
        return {
            "interface": self.coordinator.top_interface,
            "talkers": self.coordinator.data.get(DATA_TOP_TALKERS, []),
        }


class OPNSenseHealthSensor(CoordinatorEntity[OPNSenseCoordinator], SensorEntity):
    """A single system-health metric read from the coordinator DATA_HEALTH dict."""

    _attr_has_entity_name = True
    entity_description: OPNSenseHealthSensorDescription

    def __init__(
        self,
        coordinator: OPNSenseCoordinator,
        description: OPNSenseHealthSensorDescription,
    ) -> None:
        """Initialise a health sensor from its description."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{description.key}"
        )
        self._attr_device_info = coordinator.device_info

    @callback
    def _health(self) -> dict[str, Any]:
        return self.coordinator.data.get(DATA_HEALTH, {})

    @property
    def native_value(self) -> StateType:
        """Return the metric value via the description's value function."""
        return self.entity_description.value_fn(self._health())

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the description's attributes, if any."""
        attrs_fn = self.entity_description.attrs_fn
        return attrs_fn(self._health()) if attrs_fn else None
