"""Sensor platform for the opnsense_hass integration.

Builds, at setup time, from the coordinator data:

* two diagnostic sensors per gateway (delay in ms, packet loss in %);
* two system sensors (OPNsense version string, count of available updates);
* one item-count sensor per *tracked* host alias.

All sensors are :class:`CoordinatorEntity` instances that share the coordinator's
device and read their values straight from ``coordinator.data``.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DATA_ALIAS_ITEMS, DATA_ALIASES, DATA_GATEWAYS, DATA_SYSTEM
from .coordinator import OPNSenseConfigEntry, OPNSenseCoordinator


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
        """Expose the live list of addresses in the alias."""
        return {
            "addresses": self.coordinator.data.get(DATA_ALIAS_ITEMS, {}).get(
                self._name, []
            )
        }
