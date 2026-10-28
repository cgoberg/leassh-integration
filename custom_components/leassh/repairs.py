"""Repairs: degraded telemetry, pack parse failures and licence expiry.

Each issue is created from live evidence (health snapshot / per-node
telemetry), never from a guess, and offers the two actions an operator
actually needs: re-run the pipeline, or stop the faulty pack.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_DEGRADED_CYCLES = 3


async def async_check(hass: HomeAssistant, entry: ConfigEntry, runtime) -> None:
    health = runtime.coordinator.health or {}
    _check_license(hass, health)
    _check_packs(hass, entry, health)
    _check_telemetry(hass, entry, runtime)


def _check_license(hass: HomeAssistant, health: dict) -> None:
    license_info = health.get("license") or {}
    issue_id = "license_invalid"
    if health and license_info.get("valid") is False:
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=True,
            is_persistent=True,
            severity=ir.IssueSeverity.ERROR,
            translation_key="license_invalid",
            translation_placeholders={"upgrade_url": "https://leassh.com/pricing"},
            data={"upgrade_url": "https://leassh.com/pricing"},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, issue_id)

    # Node limit is a distinct, non-crashing state: existing nodes keep
    # reporting, new nodes are refused, and the fix is remove-or-upgrade.
    limit = license_info.get("node_limit") or {}
    over = bool(limit.get("exceeded")) or bool(health.get("degraded"))
    if over:
        current = limit.get("current", (health.get("nodes") or {}).get("unique", "?"))
        maximum = limit.get("max", license_info.get("max_nodes", "?"))
        ir.async_create_issue(
            hass,
            DOMAIN,
            "node_limit_reached",
            is_fixable=True,
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="node_limit_reached",
            translation_placeholders={
                "current": str(current),
                "max": str(maximum),
                "tier": str(license_info.get("tier", "current")),
                "upgrade_url": "https://leassh.com/pricing",
            },
            data={"upgrade_url": "https://leassh.com/pricing"},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, "node_limit_reached")

    expires = license_info.get("expires_at")
    if not expires:
        return
    try:
        when = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
    except ValueError:
        return
    remaining = when - datetime.now(timezone.utc)
    if remaining < timedelta(days=14):
        ir.async_create_issue(
            hass,
            DOMAIN,
            "license_expiring",
            is_fixable=True,
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="license_expiring",
            translation_placeholders={
                "days": str(max(remaining.days, 0)),
                "upgrade_url": "https://leassh.com/pricing",
                "account_url": "https://leassh.com/account",
            },
            data={"upgrade_url": "https://leassh.com/pricing"},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, "license_expiring")


def _check_packs(hass: HomeAssistant, entry: ConfigEntry, health: dict) -> None:  # noqa: ARG001
    active: set[str] = set()
    for counter in (health.get("packs") or {}).get("entries", []):
        error = counter.get("last_error")
        if not error:
            continue
        pack = counter.get("pack")
        entry_id = counter.get("entry")
        issue_id = f"pack_parse_{pack}_{entry_id}"
        active.add(issue_id)
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=True,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="pack_parse_failure",
            translation_placeholders={
                "pack": str(pack),
                "entry": str(entry_id),
                "error": str(error)[:200],
            },
            data={"pack": pack, "entry": entry_id},
        )
    # Clear issues whose failure has gone away.
    previous = hass.data.setdefault(DOMAIN, {}).get("repair_pack_issues", set())
    for issue_id in previous - active:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
    hass.data[DOMAIN]["repair_pack_issues"] = active


def _check_telemetry(hass: HomeAssistant, entry: ConfigEntry, runtime) -> None:  # noqa: ARG001
    counts = hass.data.setdefault(DOMAIN, {}).setdefault("telemetry_degraded", {})
    for node in (runtime.coordinator.data or {}).get("nodes", []):
        host = node.get("host")
        if not host:
            continue
        if node.get("reachable") and not node.get("telemetry_ok", True):
            counts[host] = counts.get(host, 0) + 1
        else:
            counts[host] = 0
        issue_id = f"telemetry_degraded_{host}"
        if counts[host] >= _DEGRADED_CYCLES:
            ir.async_create_issue(
                hass,
                DOMAIN,
                issue_id,
                is_fixable=True,
                is_persistent=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="telemetry_degraded",
                translation_placeholders={
                    "node": str(node.get("name") or host),
                    "cycles": str(counts[host]),
                },
                data={"node": node.get("name"), "host": host},
            )
        else:
            ir.async_delete_issue(hass, DOMAIN, issue_id)


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict | None
) -> RepairsFlow:
    return LeasshRepairFlow(issue_id, data or {})


class LeasshRepairFlow(RepairsFlow):
    """License issues get reauth/upgrade; pack issues keep retry/disable."""

    def __init__(self, issue_id: str, data: dict) -> None:
        self.issue_id = issue_id
        self.data = data

    def _license_issue(self) -> bool:
        return self.issue_id in {
            "license_invalid",
            "license_expiring",
            "node_limit_reached",
        }

    async def async_step_init(self, user_input=None):
        if self._license_issue():
            options = ["reauth", "upgrade"]
            return self.async_show_menu(
                step_id="init",
                menu_options=options,
                description_placeholders={
                    "upgrade_url": "https://leassh.com/pricing",
                    "account_url": "https://leassh.com/account",
                },
            )
        if self.issue_id.startswith("pack_parse_"):
            return self.async_show_menu(
                step_id="init",
                menu_options=["retry", "disable_pack"],
                description_placeholders={
                    "pack": str(self.data.get("pack")),
                    "entry": str(self.data.get("entry")),
                },
            )
        return self.async_show_menu(
            step_id="init",
            menu_options=["retry"],
        )

    async def _runtime(self):
        for runtime in self.hass.data.get(DOMAIN, {}).values():
            if hasattr(runtime, "client"):
                return runtime
        return None

    async def _entry(self):
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if self.hass.data.get(DOMAIN, {}).get(entry.entry_id) is not None:
                return entry
        entries = self.hass.config_entries.async_entries(DOMAIN)
        return entries[0] if entries else None

    async def async_step_reauth(self, user_input=None):
        """Start HA's reauth flow so the user can paste a fresh license key."""
        entry = await self._entry()
        if entry is not None:
            entry.async_start_reauth(self.hass)
        ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
        return self.async_create_entry(title="", data={})

    async def async_step_upgrade(self, user_input=None):
        """No in-app purchase: the description carries the upgrade URL."""
        ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
        return self.async_create_entry(title="", data={})

    async def async_step_retry(self, user_input=None):
        runtime = await self._runtime()
        if runtime is not None:
            try:
                await runtime.client.reload_packs()
            except Exception as err:  # noqa: BLE001 - surface, do not crash the flow
                _LOGGER.warning("Pack reload failed: %s", err)
            await runtime.coordinator.async_refresh()
        ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
        return self.async_create_entry(title="", data={})

    async def async_step_disable_pack(self, user_input=None):
        runtime = await self._runtime()
        pack = self.data.get("pack")
        if runtime is not None and pack:
            try:
                await runtime.client.set_pack_enabled(str(pack), False)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Disabling pack %s failed: %s", pack, err)
        ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
        return self.async_create_entry(title="", data={})
