"""Switch platform for the opnsense_hass integration.

Two switch families are created at setup time:

* :class:`OPNSenseRuleSwitch` — one per API-created firewall filter rule present in
  ``coordinator.data[DATA_RULES]``. NOTE: rules created in the OPNsense GUI never
  appear in the ``searchRule`` results (verified ``total=0``); this family is for
  API-created rules only.
* :class:`OPNSenseAliasSwitch` — one per *tracked* host alias, toggling the alias's
  ``enabled`` flag (not its membership).

Both write through ``coordinator.client`` and request a coordinator refresh after
applying so HA state tracks OPNsense promptly.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import OPNSenseError
from .const import DATA_ALIASES, DATA_RULES
from .coordinator import OPNSenseConfigEntry, OPNSenseCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OPNSenseConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the OPNsense rule and tracked-alias switches."""
    coordinator = entry.runtime_data
    entities: list[SwitchEntity] = []

    # One switch per API-created filter rule.
    for uuid in coordinator.data.get(DATA_RULES, {}):
        entities.append(OPNSenseRuleSwitch(coordinator, uuid))

    # One enable switch per tracked host alias that currently exists.
    aliases: dict[str, Any] = coordinator.data.get(DATA_ALIASES, {})
    for name in coordinator.tracked_aliases:
        if name in aliases:
            entities.append(OPNSenseAliasSwitch(coordinator, name))

    async_add_entities(entities)


class OPNSenseRuleSwitch(CoordinatorEntity[OPNSenseCoordinator], SwitchEntity):
    """Enable/disable a single API-created firewall filter rule."""

    _attr_has_entity_name = True
    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: OPNSenseCoordinator, uuid: str) -> None:
        """Initialise the rule switch."""
        super().__init__(coordinator)
        self._uuid = uuid
        rule = coordinator.data.get(DATA_RULES, {}).get(uuid, {})
        description = rule.get("description")
        label = description if description else uuid[:8]
        self._attr_name = f"Rule {label}"
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_rule_{uuid}"
        self._attr_device_info = coordinator.device_info

    def _rule_data(self) -> dict[str, Any]:
        return self.coordinator.data.get(DATA_RULES, {}).get(self._uuid, {})

    @property
    def available(self) -> bool:
        """Return True while the rule is still present in coordinator data."""
        return super().available and self._uuid in self.coordinator.data.get(
            DATA_RULES, {}
        )

    @property
    def is_on(self) -> bool:
        """Return True when the rule is enabled."""
        return bool(self._rule_data().get("enabled"))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the rule and apply the ruleset."""
        await self._set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the rule and apply the ruleset."""
        await self._set_enabled(False)

    async def _set_enabled(self, enabled: bool) -> None:
        client = self.coordinator.client
        try:
            await client.toggle_rule(self._uuid, enabled)
            await client.filter_apply()
        except OPNSenseError as err:
            raise HomeAssistantError(str(err)) from err
        await self.coordinator.async_request_refresh()


class OPNSenseAliasSwitch(CoordinatorEntity[OPNSenseCoordinator], SwitchEntity):
    """Enable/disable a tracked host alias."""

    _attr_has_entity_name = True
    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: OPNSenseCoordinator, name: str) -> None:
        """Initialise the alias enable switch."""
        super().__init__(coordinator)
        self._name = name
        alias = coordinator.data.get(DATA_ALIASES, {}).get(name, {})
        self._uuid = alias.get("uuid")
        self._attr_name = f"{name} enabled"
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_alias_{self._uuid}_enabled"
        )
        self._attr_device_info = coordinator.device_info

    def _alias_data(self) -> dict[str, Any]:
        return self.coordinator.data.get(DATA_ALIASES, {}).get(self._name, {})

    @property
    def available(self) -> bool:
        """Return True while the alias is still present in coordinator data."""
        return super().available and self._name in self.coordinator.data.get(
            DATA_ALIASES, {}
        )

    @property
    def is_on(self) -> bool:
        """Return True when the alias is enabled."""
        return bool(self._alias_data().get("enabled"))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the alias and reconfigure."""
        await self._set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the alias and reconfigure."""
        await self._set_enabled(False)

    async def _set_enabled(self, enabled: bool) -> None:
        # Resolve the uuid lazily in case it was unknown at construction time.
        uuid = self._uuid or self._alias_data().get("uuid")
        if not uuid:
            raise HomeAssistantError(f"Alias '{self._name}' has no UUID")
        client = self.coordinator.client
        try:
            await client.alias_toggle(uuid, enabled)
            await client.alias_reconfigure()
        except OPNSenseError as err:
            raise HomeAssistantError(str(err)) from err
        await self.coordinator.async_request_refresh()
