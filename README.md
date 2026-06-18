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
- **Traffic stats** — per-interface live in/out **bit rate** plus cumulative
  **byte counters** (for the HA statistics/energy graphs).
- **Top talkers** — the busiest local devices, each resolved to a **friendly
  name + MAC** (from DHCP leases, with an ARP fallback).
- **Alias device lists** — each tracked alias exposes its members as a `devices`
  attribute carrying `{ip, mac, name, manufacturer, online}` per device.
- **System health** — CPU usage (normalised load) + load average, memory / swap /
  disk usage, uptime, firewall state-table + mbuf usage, running-services count,
  DHCP-lease + ARP counts, ZFS ARC size, per-interface link state, and an
  updates-pending flag.
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
  *System > Access > Users > (edit user) > API keys*. **The API user needs privileges** for
  the endpoints used — at minimum *System: Firmware* (setup validation) plus *Firewall: Aliases*
  for the block/unblock services. The traffic / top-talkers / device-name features
  additionally read *Diagnostics* (traffic + ARP) and *Services: DHCPv4: Leases*; if the
  user lacks those, only those extra sensors stay empty — the core poll still works.
  A brand-new user with **no privileges** authenticates but gets HTTP 403, shown as
  "insufficient privileges" — an admin user is simplest.
- The **URL** is the base host, e.g. `http://192.168.1.1` — include `http://` and **no** `/api`
  suffix. Use `http` unless your opnsense serves the API over HTTPS *and* that port is reachable
  from Home Assistant. (The integration now tolerates a missing scheme or a trailing `/api`, but
  being explicit avoids surprises.)
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
  and enable switches, and to poll live contents (devices with friendly names + MACs)
  for. Select e.g. `KidsBlocked` and `KidsSchool` to surface their members.
- **`top_interface`** — the interface whose top bandwidth users feed the **Top talkers**
  sensor. Defaults to `lan` (local devices); pick the WAN interface to rank by remote
  endpoint instead.

## Entities

- **Binary sensors**
  - One per gateway, device class `connectivity`. `on` = the gateway is online
    (derived from the translated gateway status). Attributes: `address`,
    `monitor`, `loss`, `delay`.
  - Per interface — **link** state (`connectivity`, `on` = link up).
  - **Updates pending** (`update`) — `on` when OPNsense reports pending updates.
  - **Tailscale** (`connectivity`, `on` = service running) — only when the
    *os-tailscale* plugin is installed; attributes include `enabled`,
    `advertise_exit_node`, `accept_subnet_routes`, `exit_node`, `subnets`.
- **Sensors**
  - Per gateway: **delay** (ms, duration) and **loss** (%, disabled by default — noisy).
  - **Version** — the OPNsense version string (diagnostic).
  - **Updates available** — count of pending updates (diagnostic).
  - Per tracked alias: **item count** — number of entries in the alias, with an
    `addresses` attribute (live IPs) and a `devices` attribute resolving each to
    `{ip, mac, name, manufacturer, online}`.
  - Per interface (e.g. `lan`, `opt1`/WAN): **received/transmitted rate** plus a
    combined **throughput** (in+out) (`data_rate`, bit/s — display defaults to
    Mbit/s), and cumulative **received/transmitted** bytes (`data_size`,
    `total_increasing`). Rates are derived from the poll-to-poll delta, so they
    appear from the second poll on.
  - **Top talkers** — count of active talkers on the selected interface; the
    ranked, name-resolved list (`{ip, name, mac, rate_in_bits, rate_out_bits}`)
    is in the `talkers` attribute.
  - **System health** — **CPU usage** (% = load ÷ cores; load 1/5/15m + model in
    attributes), **load average**, **memory** / **swap** / **disk** usage (%),
    **memory used** + **ZFS ARC** size (MB), **uptime**, **firewall states**
    (count) + **state-table usage** (%), **mbuf usage** (%), **services running**
    (with the stopped list in attributes), **DHCP leases online** / **total**, and
    **ARP entries**. Disk carries every filesystem in a `filesystems` attribute.
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
