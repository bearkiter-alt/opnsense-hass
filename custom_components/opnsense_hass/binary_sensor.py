"""Binary sensor platform for the opnsense_hass integration.

Exposes a connectivity binary sensor per OPNsense gateway (driven by the
coordinator's ``status_translated`` field, not the raw ``status``), a
per-interface physical-link sensor, and an updates-pending sensor.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DATA_GATEWAYS, DATA_SYSTEM, DATA_TAILSCALE, DATA_TRAFFIC
from .coordinator import OPNSenseConfigEntry, OPNSenseCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OPNSenseConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the OPNsense binary sensors (gateways, interface links, updates)."""
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = [
        OPNSenseGatewayBinarySensor(coordinator, gw_name)
        for gw_name in coordinator.data.get(DATA_GATEWAYS, {})
    ]
    entities.append(OPNSenseUpdatesPendingBinarySensor(coordinator))
    for iface, info in coordinator.data.get(DATA_TRAFFIC, {}).items():
        if info.get("link_up") is not None:
            entities.append(OPNSenseInterfaceLinkBinarySensor(coordinator, iface))
    # Tailscale (only when the os-tailscale plugin is present).
    if coordinator.data.get(DATA_TAILSCALE):
        entities.append(OPNSenseTailscaleBinarySensor(coordinator))
    async_add_entities(entities)


class OPNSenseGatewayBinarySensor(
    CoordinatorEntity[OPNSenseCoordinator], BinarySensorEntity
):
    """Connectivity status of a single OPNsense gateway."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: OPNSenseCoordinator, gw_name: str) -> None:
        """Initialise the gateway connectivity binary sensor."""
        super().__init__(coordinator)
        self._gw = gw_name
        self._attr_name = gw_name
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_gw_{gw_name}_connectivity"
        )
        self._attr_device_info = coordinator.device_info

    @callback
    def _gw_data(self) -> dict[str, Any]:
        """Return this gateway's data dict (empty if it disappeared)."""
        return self.coordinator.data.get(DATA_GATEWAYS, {}).get(self._gw, {})

    @property
    def available(self) -> bool:
        """Return True only while this gateway is still reported by OPNsense."""
        return super().available and self._gw in self.coordinator.data.get(
            DATA_GATEWAYS, {}
        )

    @property
    def is_on(self) -> bool:
        """Return True when the gateway connectivity is Online."""
        return self._gw_data().get("status_translated") == "Online"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose address, monitor target, packet loss and delay."""
        data = self._gw_data()
        return {
            "address": data.get("address"),
            "monitor": data.get("monitor"),
            "loss": data.get("loss"),
            "delay": data.get("delay"),
        }


class OPNSenseUpdatesPendingBinarySensor(
    CoordinatorEntity[OPNSenseCoordinator], BinarySensorEntity
):
    """On when OPNsense reports one or more pending firmware updates."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.UPDATE
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: OPNSenseCoordinator) -> None:
        """Initialise the updates-pending binary sensor."""
        super().__init__(coordinator)
        self._attr_name = "Updates pending"
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_updates_pending"
        )
        self._attr_device_info = coordinator.device_info

    @property
    def is_on(self) -> bool:
        """Return True when the normalised update count is greater than zero."""
        value = self.coordinator.data.get(DATA_SYSTEM, {}).get("updates")
        # bool is an int subclass; never treat True/False as a count.
        return isinstance(value, int) and not isinstance(value, bool) and value > 0


class OPNSenseInterfaceLinkBinarySensor(
    CoordinatorEntity[OPNSenseCoordinator], BinarySensorEntity
):
    """Physical link state of a single interface (on = link up)."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: OPNSenseCoordinator, iface: str) -> None:
        """Initialise the interface-link binary sensor."""
        super().__init__(coordinator)
        self._iface = iface
        label = (
            coordinator.data.get(DATA_TRAFFIC, {}).get(iface, {}).get("label")
            or iface.upper()
        )
        self._attr_name = f"{label} link"
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_link_{iface}"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        """Return True while the interface is still present in coordinator data."""
        return super().available and self._iface in self.coordinator.data.get(
            DATA_TRAFFIC, {}
        )

    @property
    def is_on(self) -> bool:
        """Return True when the interface link is up."""
        return bool(
            self.coordinator.data.get(DATA_TRAFFIC, {})
            .get(self._iface, {})
            .get("link_up")
        )


class OPNSenseTailscaleBinarySensor(
    CoordinatorEntity[OPNSenseCoordinator], BinarySensorEntity
):
    """Tailscale service connectivity (os-tailscale plugin); on = running."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_icon = "mdi:vpn"

    def __init__(self, coordinator: OPNSenseCoordinator) -> None:
        """Initialise the Tailscale binary sensor."""
        super().__init__(coordinator)
        self._attr_name = "Tailscale"
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_tailscale"
        self._attr_device_info = coordinator.device_info

    @property
    def is_on(self) -> bool:
        """Return True when the Tailscale service is running."""
        return bool(self.coordinator.data.get(DATA_TAILSCALE, {}).get("running"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose status + key Tailscale settings."""
        ts = self.coordinator.data.get(DATA_TAILSCALE, {})
        return {
            k: ts.get(k)
            for k in (
                "status",
                "enabled",
                "advertise_exit_node",
                "accept_subnet_routes",
                "accept_dns",
                "exit_node",
                "subnets",
            )
        }
