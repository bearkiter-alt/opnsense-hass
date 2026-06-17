# OPNsense (HASS)

A Home Assistant custom integration for [OPNsense](https://opnsense.org/) that exposes
gateway, system, and firewall-alias status as entities and provides services for
per-device internet blocking — purpose-built to drive an ADHDTasker time-reward
automation, but useful for any OPNsense control from HA.

## Features

- Read **gateway** status (connectivity, delay, loss), **system** info (version,
  updates available), and **firewall alias** status.
- **Per-device internet blocking** by managing a host alias's contents, with both a
  **runtime** path (immediate, lost on reboot) and a **persistent** path (survives
  reboot via the alias config + reconfigure).
- **Switches** for toggling a tracked host alias's *enabled* flag and for enabling
  /disabling API-created firewall rules.
- **Sensors** for gateway delay/loss, OPNsense version, updates-available count, and
  tracked-alias item counts.
- A **full service set** (`alias_add_host`, `alias_remove_host`, `alias_flush`,
  `alias_set_hosts`, `alias_block_device`, `alias_unblock_device`, `toggle_rule`,
  `apply`) plus a raw **`exec_api`** passthrough that returns the parsed response.
- **Diagnostics** download with secrets (API key/secret/URL) redacted.
- Designed to power an **ADHDTasker → HA → OPNsense** time-reward automation: a kid's
  device is blocked by default and granted internet for N minutes when a task is
  completed.

## Requirements

- **Home Assistant 2026.6+**.
- An **OPNsense API key/secret** pair. Create one under
  *System > Access > Users > (edit user) > API keys*.
- A **pre-existing GUI firewall rule** that references the host alias you intend to
  manage (e.g. a block rule with source set to the `KidsBlocked` alias). The
  integration manages the alias *contents*; the rule that acts on those contents must
  already exist in the GUI.

## Installation (HACS custom repository)

1. In Home Assistant, open **HACS → Integrations**.
2. Click the **⋮** menu (top-right) → **Custom repositories**.
3. Add `https://github.com/bearkiter-alt/opnsense-hass`, choose category
   **Integration**, and click **Add**.
4. Find **OPNsense (HASS)** in the list and click **Install**.
5. **Restart Home Assistant**.
6. Go to **Settings → Devices & Services → Add Integration** and search for
   **"OPNsense (HASS)"**.

## Configuration

Set up via the UI config flow.

| Field           | Required | Default    | Notes                                                        |
| --------------- | -------- | ---------- | ------------------------------------------------------------ |
| `name`          | yes      | `OPNsense` | Friendly name / entry title.                                 |
| `url`           | yes      | —          | Base URL **without** the `/api` suffix, e.g. `http://192.168.1.254`. |
| `api_key`       | yes      | —          | API key from System > Access > Users.                        |
| `api_secret`    | yes      | —          | API secret paired with the key.                              |
| `verify_ssl`    | no       | `false`    | Leave off for self-signed HTTPS.                             |
| `scan_interval` | no       | `30`       | Polling interval in seconds (5–3600).                        |

### Options

After setup, open the integration's **Configure** dialog:

- **`scan_interval`** — change the polling interval (seconds, 5–3600).
- **`tracked_aliases`** — multi-select of host aliases to expose as item-count sensors
  and enable switches, and to poll live contents for.

## Entities

- **Binary sensor** — one per gateway, device class `connectivity`. `on` = the gateway
  is online (derived from the translated gateway status). Attributes: `address`,
  `monitor`, `loss`, `delay`.
- **Sensors**
  - Per gateway: **delay** (ms, duration) and **loss** (%, disabled by default — noisy).
  - **Version** — the OPNsense version string (diagnostic).
  - **Updates available** — count of pending updates (diagnostic).
  - Per tracked alias: **item count** — number of entries in the alias, with an
    `addresses` attribute listing the live IPs.
- **Switches**
  - Per **API-created firewall rule** — enable/disable the rule (then applies the
    ruleset). *Note:* GUI-created rules do not appear here; only rules created via the
    API are listed.
  - Per **tracked host alias** — toggle the alias's *enabled* flag (then reconfigures).

## Services

```yaml
# Block a device immediately (runtime, lost on reboot)
action: opnsense_hass.alias_add_host
data:
  alias: KidsBlocked
  address: 192.168.1.50
```

```yaml
# Persistent block (default for rewards/blocking)
action: opnsense_hass.alias_block_device
data:
  alias: KidsBlocked
  address: 192.168.1.50
```

```yaml
# Persistent unblock
action: opnsense_hass.alias_unblock_device
data:
  alias: KidsBlocked
  address: 192.168.1.50
```

```yaml
# Replace the whole alias content
action: opnsense_hass.alias_set_hosts
data:
  alias: KidsBlocked
  addresses:
    - 192.168.1.50
    - 192.168.1.51
```

```yaml
action: opnsense_hass.alias_remove_host
data: { alias: KidsBlocked, address: 192.168.1.50 }
```

```yaml
action: opnsense_hass.alias_flush
data: { alias: KidsBlocked }
```

```yaml
action: opnsense_hass.toggle_rule
data: { uuid: a1b2c3d4-...., enabled: false }
```

```yaml
action: opnsense_hass.apply
data: { target: both }
```

```yaml
# Raw passthrough, returns a response
action: opnsense_hass.exec_api
data:
  method: GET
  path: /routes/gateway/status
response_variable: gw
```

### Runtime vs persistent

- **Runtime** (`alias_add_host`, `alias_remove_host`, `alias_flush`) edit the live pf
  table directly — instant, but **lost on reboot**.
- **Persistent** (`alias_block_device`, `alias_unblock_device`, `alias_set_hosts`) edit
  the alias *config* and reconfigure — **survives reboot**. Prefer these for
  reward/blocking automations.

## Worked time-reward automation (ADHDTasker → HA → OPNsense)

```yaml
# Default state: kid's device is blocked. ADHDTasker completing a task fires an
# event that grants N minutes of internet, then re-blocks via a timer.
automation:
  - alias: "Reward - grant internet on ADHDTasker task complete"
    trigger:
      - platform: event
        event_type: adhdtasker_task_completed
        event_data:
          reward: internet
    action:
      - action: opnsense_hass.alias_unblock_device
        data:
          alias: KidsBlocked
          address: "{{ trigger.event.data.device_ip }}"
      - action: timer.start
        target:
          entity_id: timer.internet_reward
        data:
          duration: "{{ trigger.event.data.minutes | default(30) }}:00"

  - alias: "Reward - re-block internet when timer ends"
    trigger:
      - platform: event
        event_type: timer.finished
        event_data:
          entity_id: timer.internet_reward
    action:
      - action: opnsense_hass.alias_block_device
        data:
          alias: KidsBlocked
          address: "192.168.1.50"
```

> Prerequisites: a `timer.internet_reward` helper, and a GUI firewall **block rule**
> whose source is the `KidsBlocked` alias (the integration manages the alias contents;
> the rule must already exist).

## License

[MIT](LICENSE) © 2026 bearkiter-alt
