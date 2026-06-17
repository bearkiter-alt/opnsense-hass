"""Binary sensor platform for the opnsense_hass integration.

Exposes one connectivity binary sensor per OPNsense gateway. The on/off state is
driven by the coordinator's ``status_translated`` field (NOT the raw ``status``),
which the API normalises to human-readable values such as ``"Online"``.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DATA_GATEWAYS
from .coordinator import OPNSenseConfigEntry, OPNSenseCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OPNSenseConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the OPNsense gateway connectivity binary sensors."""
    coordinator = entry.runtime_data
    gateways: dict[str, Any] = coordinator.data.get(DATA_GATEWAYS, {})
    async_add_entities(
        OPNSenseGatewayBinarySensor(coordinator, gw_name) for gw_name in gateways
    )


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
