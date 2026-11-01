# Leassh for Home Assistant

Home Assistant integration for [Leassh](https://leassh.com) — SSH fleet
monitoring and family safety. The Leassh server is the sole authority for
fleet presence, enforcement and device orchestration; this integration is a
thin, platform-native client. It never probes your machines itself and
never runs arbitrary commands: it holds a scoped token whose server-side
scope (`ha`) only ever sees a curated action catalog.

## Features

- **One HA device per fleet node** — online, signed-in, activity, CPU, RAM,
  load sensors.
- **Fleet and family hub sensors** — `sensor.leassh_fleet_state`
  (`ok` / `attention` / `degraded` / `critical`), `sensor.leassh_fleet_reachable`, `sensor.leassh_fleet_open_claims`,
  `sensor.leassh_fleet_critical_claims`, `sensor.leassh_fleet_ledger_valid`,
  `sensor.leassh_family_screen_time`, `sensor.leassh_family_over_limit`,
  `sensor.leassh_family_bedtime_active`, `sensor.leassh_family_devices`,
  `sensor.leassh_consent` and `sensor.leassh_idle_policy` (effective
  idle-logout/app-reclamation policy per node). Fleet claims, sites, blockers and recent actions
  are attributes; family children, limits and bedtime are attributes.
- **Per-child family sensors** — `sensor.<node>_screen_time_today`,
  `sensor.<node>_screen_time_limit` (`No limit` / `Within limit` /
  `Over limit`) and `sensor.<node>_bedtime` (`Off` / `Scheduled` /
  `Active`). These power native HA entity cards and history.
- **Console occupancy vs presence** — `binary_sensor.<node>_signed_in`
  maps the server's `console_signed_in` field: a human owns the console,
  even if idle. `binary_sensor.<node>_at_desk` is the active-presence
  entity.
- **`binary_sensor.leassh_server_connected`** — the watchdog. The WS connection
  state *is* the heartbeat; if the server goes silent for >75 s it flips
  off, and after 5 minutes HA raises a persistent notification and a
  companion-app alert ("monitoring interrupted") — delivered by HA itself,
  so it fires exactly when Leassh cannot tell you anything.
- **Curated action buttons** — `reboot_node`, `sign_out_session` (whatever
  the server's catalog offers), per node. No exec surface, ever.
- **`leassh_event`** — one HA event type; every server event (triggers,
  presence transitions, actions, notifications) arrives with a `kind`
  payload field for your automations.
- **`notify.leassh`** — delivery channel to the companion app(s). Requests
  routed through the Leassh server (e.g. `POST /api/notify` from other
  systems) land on phones with iOS **critical sound** when the level is
  `urgent`.

## Requirements

- A Leassh server ≥ 0.2.0 with the API enabled (see below).
- Home Assistant ≥ 2025.1.
- A Leassh license. **Home Assistant is included on every plan, including
  Free.** Free covers 2 computers and one `ha` API token; Family covers 10,
  Pro 25, Unlimited is contact-only. Get a Free key at
  <https://leassh.com/setup>.

The add-on accepts online `LSH-…` keys and offline `LSH1.…` tokens. Using
Home Assistant and OpenClaw at the same time requires Family or Pro because
Free carries exactly one API token.

## Server setup (minting the token)

On the machine running the Leassh server, edit `fleet.yaml`:

```yaml
api:
  enabled: true
  listen: "0.0.0.0:8790"   # LAN-reachable for HA; 127.0.0.1 if co-located
  tokens:
    ha: "choose-a-long-random-secret"
```

Restart the Leassh service. The `ha` scope is curated by design: it can
read fleet state, run catalog verbs, and route notifications — nothing
else.

## Installation

### HACS (recommended)

1. HACS → ⋮ → Custom repositories → add this repository, category
   **Integration**.
2. Install **Leassh**.
3. Restart Home Assistant.

### Manual

Copy `custom_components/leassh/` into your HA `config/custom_components/`
and restart.

## Pairing walkthrough

1. Settings → Devices & Services → Add Integration → **Leassh**.
   - If the server advertises `_leassh._tcp` on your LAN, it can be
     discovered; otherwise enter the URL manually:
     `http://192.168.1.127:8790`.
   - Paste the `ha` token. The flow tests the connection before saving.
2. Devices appear — one per fleet node, plus the Leassh Server hub device.
3. Configure companion-app targets via *Configure* (options): leave empty
   to fan out to all `mobile_app_*` notify services.
4. Optional automation on the watchdog:

```yaml
automation:
  - alias: Leassh down
    trigger:
      - platform: state
        entity_id: binary_sensor.leassh_server_connected
        to: "off"
        for: "00:05:00"
    action:
      - service: notify.mobile_app_your_phone
        data:
          title: "Leassh offline"
          message: "Family safety monitoring is interrupted."
          data:
            push:
              interruption-level: time-sensitive
```

## Using events

```yaml
automation:
  - alias: Gaming started
    trigger:
      - platform: event
        event_type: leassh_event
        event_data:
          kind: node.activity
    condition:
      - condition: template
        value_template: "{{ trigger.event.data.data.activity == 'Active' }}"
    action: ...
```

Event kinds: `node.online`, `node.offline`, `node.activity`,
`trigger.<name>`, `notify`, `action.executed`, `action.failed`. Payload
lives in `event.data.data`.

## License errors and repairs

The integration distinguishes a license refusal from a network failure.
When the server returns HTTP 503 with `{"error":"license invalid"}`, the
config flow shows `license_invalid` and the repair `license_invalid` offers
**Re-authenticate** (paste a fresh key in the add-on or `fleet.yaml`) and
**Upgrade**. It does not suggest disabling a pack.

Other repairs:

- `license_expiring` — renew at <https://leassh.com/account> or upgrade.
- `node_limit_reached` — the fleet is over the plan's node count. Existing
  nodes keep reporting in degraded mode; new nodes are refused with
  `NODE_LIMIT_REACHED`. Remove a node or upgrade.

Diagnostics include the license tier, max nodes, current node count, expiry,
and degraded state. A `sensor.leassh_server_license` hub sensor exposes the
same values. Tokens are still redacted.

## Security model

- Scoped token (`ha`): read fleet state, curated verbs, notifications.
  The server refuses `exec`/`screenshot` for this scope — see the
  [connectors program](https://github.com/cgoberg/leassh) scope model.
- Keep `listen` loopback if HA and Leassh run on the same host.
- The token grants family visibility; treat it like a password.

## License

MIT OR Apache-2.0 — see `LICENSE` and `LICENSE.APACHE`.

## Configuration parameters

The integration has one config entry (server URL + API token) and these options:

- **notify_targets** — comma-separated `notify` service names (for example
  `mobile_app_iphone`). Empty means every `mobile_app_*` target. Used only for
  server-originated notifications; it does not grant any new server scope.

Recipe parameters are configured through the options menu (**Configure →
Recipes**) or the Leassh Console. Common operational parameters stay in HA;
advanced authoring lives in the Console.

## Data updates

The coordinator polls `/api/devices` every 30 seconds with an ETag; a `304`
keeps the previous snapshot. A WebSocket session carries commands, events and
presence. `node.online`, `node.offline` and `node.activity` events update the
snapshot in place, then the next poll reconciles. Events are an accelerator,
never the only source of truth. Recipe/setup status is polled with the same
coordinator cycle.

## Supported devices

- Linux, Windows and macOS hosts reachable over SSH.
- Tool prerequisites are per pack (for example `nvidia-smi` for GPU packs,
  `docker` for container packs, `systemctl` for service actions).
- Home Assistant OS/Supervised/Container installations that can reach the
  Leassh server over HTTP/WS.

## Supported functions

- Per-node health, CPU/GPU/RAM/disk, presence, sessions, entities and series.
- Curated actions exposed as buttons and services; no arbitrary commands.
- Fleet claims, ledger, consent and setup doctor.
- Recipe catalog/install/configure/dry-run/enable/disable/status/fork.
- Server-originated notifications and `leassh_event` automations.
- Diagnostics download with tokens redacted.

## Known limitations

- The Windows presence sampler is the authoritative presence source; Windows
  idle APIs were proven unreliable under controlled ground truth.
- Sampler identity must match the console session; a stale or missing sampler
  fails closed.
- Per-container child devices are not created; containers are entities on the
  host device.
- Live recipe actions require the server live sink and a configured provider;
  simulation is the default.
- The 95% coverage target is measured but not yet enforced as a hard gate.

## Use cases

- Single-owner homelab: Monitor bundle, health/claims, safe power actions.
- Family household: bedtime, screen-time, family summary with opt-in
  enforcement and visible warnings.
- SMB/fleet: patch/maintenance windows, backup freshness, canary, approvals.
- Virtualization: Proxmox/Hyper-V guest lifecycle and backup/snapshot recipes.

## Troubleshooting

See `docs/troubleshooting.md` in the Leassh server repository and the repair
issues created by the integration. Common checks:

1. `Settings → Devices & Services → Leassh → Configure` for setup doctor.
2. Download diagnostics; tokens are redacted.
3. Check the recipe status sensor attributes for `last_error` and
   `suppressed_reason`.
4. Reauthenticate if the token was rotated.

## Removal

1. Remove the integration entry in Home Assistant.
2. Remove the Leassh add-on/container if no longer used.
3. Optionally delete `/config/leassh` (fleet config, recipe store, ledger) and
   the downloaded `custom_components/leassh` directory.
