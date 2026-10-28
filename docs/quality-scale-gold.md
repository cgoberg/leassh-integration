# Integration quality scale — path to Gold

Written 2026-10-28. The 54-rule list is the current HA quality scale
(Bronze 20, Silver 10, Gold 21, Platinum 3). Source:
`developers.home-assistant.io/core/quality-scale/rules`, checked 2026-10-28.

Gold is not a standalone tier: **every Bronze and Silver rule must also be
done or exempt**. The current integration has all visible user features, but
several foundation rules were claimed on the old, shorter list. This plan is
the honest gap list.

## Current status

| Tier | Done / exempt | Gaps |
|---|---|---|
| Bronze (20) | 18 | `docs-removal-instructions`, `runtime-data` |
| Silver (10) | 7 | `action-exceptions`, `docs-configuration-parameters`, `parallel-updates`, `test-coverage` |
| Gold (21) | 10 | 11 rules listed below |
| Platinum (3) | 2 | `strict-typing` (optional for Gold) |

Gold is currently blocked by Bronze/Silver as much as by the Gold list.

## Gold rules already done (10)

`devices`, `diagnostics`, `discovery`, `docs-examples`, `dynamic-devices`,
`entity-device-class`, `entity-translations`, `reconfiguration-flow`,
`repair-issues`, `stale-devices`.

## Gold gaps (11) and what each actually needs

### Entity metadata and translations

1. **`entity-category`**
   - Mark `suggested_packs` (and any future health/diagnostic entity) as
     `EntityCategory.DIAGNOSTIC`.
   - Do not categorise the primary monitoring sensors; they are the product.

2. **`entity-disabled-by-default`**
   - Decide which optional entities start disabled. Candidate:
     `suggested_packs` (diagnostic), possibly the two idle-time sensors.
   - If none are noisy, mark the rule `exempt` with a written rationale
     instead of pretending.

3. **`exception-translations`**
   - Add `exceptions` entries to `strings.json` / `translations/en.json`.
   - Raise `HomeAssistantError` / `ServiceValidationError` with
     `translation_domain=DOMAIN` and `translation_key` instead of bare
     `ValueError`/re-raised `LeasshApiError`.
   - Cover action failures, notify failures and config-entry setup errors.

4. **`icon-translations`**
   - Add `icons.json` keyed by entity translation keys.
   - Remove `_attr_icon = "mdi:..."` from classes where the translation file
     can carry it; keep dynamic pack icons only where a pack supplies one.

### Discovery and dynamic behaviour

5. **`discovery-update-info`**
   - On `_leassh._tcp` rediscovery, if the unique_id (`host:port`) matches an
     existing entry but the URL changed, update the entry instead of aborting.
   - Test: discovered URL change updates `CONF_SERVER_URL`; a different host
     still aborts as already configured.

### Documentation (the bulk of the work)

6. **`docs-data-update`** — explain the 30 s coordinator poll, WS event
   acceleration, ETag/304 behaviour, and that events are never the source of
   truth.
7. **`docs-supported-devices`** — SSH-reachable Linux/Windows/macOS hosts;
   prerequisites per pack (`nvidia-smi`, `docker`, `smartctl`, `upsc`,
   `zpool`); Windows sampler requirement.
8. **`docs-supported-functions`** — entity list, per-GPU series behaviour,
   actions/buttons, notify, `leassh_event`, diagnostics.
9. **`docs-known-limitations`** — sampler-runs-as-user forgeability;
   Windows idle semantics; GPUs are sensors on the node device, not child
   devices; presence push needs `presence_token`; no remote pack catalogue.
10. **`docs-troubleshooting`** — link the repo troubleshooting guide and cover
    the HA-specific failures: invalid token, WS down, entity unavailable,
    repair actions.
11. **`docs-use-cases`** — family safety, homelab monitoring, per-GPU
    dashboards, "is anyone at the desk", pack examples.

Also add **`docs-removal-instructions`** (Bronze) and **`docs-configuration-parameters`**
(Silver) while touching the README.

## Foundation fixes (Bronze/Silver)

- **`runtime-data`** — set `entry.runtime_data = runtime` and read it in every
  platform; type it. Replace `hass.data[DOMAIN][entry_id]` lookups. This is
  the largest code change in the plan but mechanical.
- **`action-exceptions`** — wrap button/notify failures in HA exceptions.
- **`parallel-updates`** — set `PARALLEL_UPDATES = 1` (or 0 for
  coordinator-only writes) explicitly.
- **`test-coverage`** — add `pytest-cov`, enforce ≥95% per module, and close
  the gaps. This is the most time-consuming item; do it last so the code is
  stable.

## Suggested order

1. `runtime-data` migration + `parallel-updates` (mechanical, low risk).
2. Exception and icon translation plumbing (`exceptions`, `icons.json`,
   `entity-category`).
3. `discovery-update-info` + tests.
4. README/docs pass for the seven docs rules + Bronze/Silver docs.
5. `test-coverage` to 95% and wire it into CI.
6. Re-run hassfest/HACS validation, update `quality_scale.yaml` to all
   done/exempt, and cut the integration release.

Estimated effort: **4–5 focused days**. Docs are roughly half of it; the
runtime-data and coverage work is the other half.

## Acceptance for Gold

- `custom_components/leassh/quality_scale.yaml` has every Bronze, Silver and
  Gold rule `done` or a defensible `exempt`, with no `todo`.
- Hassfest quality-scale validation passes in CI.
- HA test suite covers ≥95% of every module (or a documented exemption).
- A fresh HACS install gets: devices, diagnostics, discovery, translated
  names/states/icons, repair actions, and a README that answers setup,
  removal, options, data update, supported hosts, troubleshooting and use
  cases.

## What Gold does *not* require

- Platinum: `strict-typing` is the only remaining Platinum item and is
  optional for Gold. `async-dependency` is exempt (aiohttp is HA's) and
  `inject-websession` is already done.
- A hosted catalogue, signing or tiers. Those are product decisions, not
  quality-scale rules.
