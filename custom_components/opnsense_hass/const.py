"""Constants for the opnsense_hass integration."""
from __future__ import annotations

import logging
from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "opnsense_hass"
LOGGER: Final = logging.getLogger(__package__)

PLATFORMS: Final[list[Platform]] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SWITCH,
]

# ---- Config entry data keys ----
CONF_NAME: Final = "name"            # also homeassistant.const.CONF_NAME == "name"
CONF_URL: Final = "url"              # base, WITHOUT trailing /api (e.g. http://192.168.1.254)
CONF_API_KEY: Final = "api_key"
CONF_API_SECRET: Final = "api_secret"
CONF_VERIFY_SSL: Final = "verify_ssl"

# ---- Options keys ----
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_TRACKED_ALIASES: Final = "tracked_aliases"   # list[str] of alias NAMES
CONF_TOP_INTERFACE: Final = "top_interface"        # interface key for top-talkers (e.g. "lan")

# ---- Defaults ----
DEFAULT_VERIFY_SSL: Final = False
DEFAULT_SCAN_INTERVAL: Final = 30                  # seconds
MIN_SCAN_INTERVAL: Final = 5
MAX_SCAN_INTERVAL: Final = 3600
DEFAULT_TOP_INTERFACE: Final = "lan"               # local side = top *device* talkers
TOP_TALKERS_LIMIT: Final = 10                      # max talkers exposed in the attribute

# ---- API ----
API_PREFIX: Final = "/api"
DEFAULT_TIMEOUT: Final = 30                         # seconds per request
SEARCH_ROW_COUNT: Final = 1000                      # rowCount for searchItem/searchRule paging

# ---- Coordinator data dict top-level keys ----
DATA_FIRMWARE: Final = "firmware"
DATA_SYSTEM: Final = "system"
DATA_GATEWAYS: Final = "gateways"          # dict[gw_name, gw_dict]
DATA_ALIASES: Final = "aliases"            # dict[alias_name, alias_dict]
DATA_ALIAS_ITEMS: Final = "alias_items"    # dict[alias_name, list[str ip]]  (tracked only)
DATA_RULES: Final = "rules"                # dict[uuid, rule_dict]
DATA_TRAFFIC: Final = "traffic"            # dict[iface_key, traffic_dict] (bytes/packets/rates)
DATA_TOP_TALKERS: Final = "top_talkers"    # list[talker_dict], sorted desc by rate_bits
DATA_HOSTS: Final = "hosts"                # dict[ip, host_dict] — DHCP+ARP identity index
DATA_ALIAS_DEVICES: Final = "alias_devices"  # dict[alias_name, list[device_dict]] (tracked only)
DATA_HEALTH: Final = "health"              # dict of system-health metrics (cpu/mem/disk/states/...)
DATA_TAILSCALE: Final = "tailscale"        # dict: os-tailscale service status + settings ({} if plugin absent)

# ---- Service names ----
SERVICE_ALIAS_ADD_HOST: Final = "alias_add_host"
SERVICE_ALIAS_REMOVE_HOST: Final = "alias_remove_host"
SERVICE_ALIAS_FLUSH: Final = "alias_flush"
SERVICE_ALIAS_SET_HOSTS: Final = "alias_set_hosts"
SERVICE_ALIAS_BLOCK_DEVICE: Final = "alias_block_device"
SERVICE_ALIAS_UNBLOCK_DEVICE: Final = "alias_unblock_device"
SERVICE_TOGGLE_RULE: Final = "toggle_rule"
SERVICE_APPLY: Final = "apply"
SERVICE_EXEC_API: Final = "exec_api"

# ---- Service field names ----
ATTR_ALIAS: Final = "alias"
ATTR_ADDRESS: Final = "address"
ATTR_ADDRESSES: Final = "addresses"
ATTR_UUID: Final = "uuid"
ATTR_ENABLED: Final = "enabled"
ATTR_TARGET: Final = "target"
ATTR_METHOD: Final = "method"
ATTR_PATH: Final = "path"
ATTR_BODY: Final = "body"

# ---- apply target values ----
APPLY_FILTER: Final = "filter"
APPLY_ALIAS: Final = "alias"
APPLY_BOTH: Final = "both"

MANUFACTURER: Final = "Deciso / OPNsense"
