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
- **`binary_sensor.leassh_connected`** — the watchdog. The WS connection
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
        entity_id: binary_sensor.leassh_connected
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

## Security model

- Scoped token (`ha`): read fleet state, curated verbs, notifications.
  The server refuses `exec`/`screenshot` for this scope — see the
  [connectors program](https://github.com/cgoberg/leassh) scope model.
- Keep `listen` loopback if HA and Leassh run on the same host.
- The token grants family visibility; treat it like a password.

## License

MIT OR Apache-2.0 — see `LICENSE` and `LICENSE.APACHE`.
