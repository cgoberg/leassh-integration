"""Repairs: issues are created from live evidence and offer actions (M4.3)."""

from __future__ import annotations

from types import SimpleNamespace

from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.leassh.const import DOMAIN
from custom_components.leassh.repairs import _check_license, _check_packs, _check_telemetry


def _entry() -> MockConfigEntry:
    return MockConfigEntry(domain=DOMAIN, data={}, entry_id="entry1")


async def test_invalid_licence_creates_a_repair(hass) -> None:
    _check_license(hass, {"license": {"valid": False}})
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "license_invalid")
    assert issue is not None
    assert issue.is_fixable is True

    _check_license(hass, {"license": {"valid": True}})
    assert ir.async_get(hass).async_get_issue(DOMAIN, "license_invalid") is None


async def test_pack_parse_failure_is_identifiable_and_fixable(hass) -> None:
    health = {
        "packs": {
            "entries": [
                {
                    "pack": "gpu-nvidia",
                    "entry": "gpu_nvidia",
                    "last_error": "csv_rows: no rows",
                }
            ]
        }
    }
    _check_packs(hass, _entry(), health)
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, "pack_parse_gpu-nvidia_gpu_nvidia"
    )
    assert issue is not None
    assert issue.data["pack"] == "gpu-nvidia"

    _check_packs(hass, _entry(), {"packs": {"entries": []}})
    assert (
        ir.async_get(hass).async_get_issue(
            DOMAIN, "pack_parse_gpu-nvidia_gpu_nvidia"
        )
        is None
    )


async def test_telemetry_degraded_needs_three_cycles(hass) -> None:
    runtime = SimpleNamespace(
        coordinator=SimpleNamespace(
            data={
                "nodes": [
                    {
                        "name": "gpu-primary-131",
                        "host": "192.168.1.131",
                        "reachable": True,
                        "telemetry_ok": False,
                    }
                ]
            }
        )
    )
    _check_telemetry(hass, _entry(), runtime)
    _check_telemetry(hass, _entry(), runtime)
    assert (
        ir.async_get(hass).async_get_issue(
            DOMAIN, "telemetry_degraded_192.168.1.131"
        )
        is None
    )
    _check_telemetry(hass, _entry(), runtime)
    assert (
        ir.async_get(hass).async_get_issue(
            DOMAIN, "telemetry_degraded_192.168.1.131"
        )
        is not None
    )


async def test_node_limit_creates_repair_and_clears_when_fixed(hass) -> None:
    health = {
        "license": {
            "valid": True,
            "tier": "free",
            "max_nodes": 2,
            "node_limit": {"current": 3, "max": 2, "exceeded": True},
            "expires_at": None,
        },
        "degraded": True,
    }
    _check_license(hass, health)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "node_limit_reached")
    assert issue is not None
    assert issue.is_fixable is True
    assert issue.severity == ir.IssueSeverity.WARNING

    _check_license(
        hass,
        {
            "license": {
                "valid": True,
                "tier": "free",
                "max_nodes": 2,
                "node_limit": {"current": 2, "max": 2, "exceeded": False},
                "expires_at": None,
            },
            "degraded": False,
        },
    )
    assert ir.async_get(hass).async_get_issue(DOMAIN, "node_limit_reached") is None


async def test_recipe_failure_creates_and_clears_issue(hass) -> None:
    runtime = SimpleNamespace(
        coordinator=SimpleNamespace(
            recipe_instances=[
                {
                    "instance": {
                        "id": "inst-1",
                        "recipe_id": "household.bedtime",
                        "last_error": "missing provider",
                        "last_suppressed_reason": None,
                    }
                }
            ]
        )
    )
    from custom_components.leassh.repairs import _check_recipes

    _check_recipes(hass, _entry(), runtime)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "recipe_broken_inst-1")
    assert issue is not None
    assert issue.data["recipe"] == "household.bedtime"

    runtime.coordinator.recipe_instances = []
    _check_recipes(hass, _entry(), runtime)
    assert ir.async_get(hass).async_get_issue(DOMAIN, "recipe_broken_inst-1") is None


async def test_async_check_runs_all_sources(hass) -> None:
    runtime = SimpleNamespace(
        coordinator=SimpleNamespace(
            health={
                "license": {"valid": False, "expires_at": None},
                "packs": {"entries": []},
            },
            data={"nodes": []},
            recipe_instances=[],
        )
    )
    from custom_components.leassh.repairs import async_check

    await async_check(hass, _entry(), runtime)
    assert ir.async_get(hass).async_get_issue(DOMAIN, "license_invalid") is not None


async def test_license_expiry_issue(hass) -> None:
    from datetime import datetime, timedelta, timezone

    soon = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    _check_license(
        hass,
        {"license": {"valid": True, "expires_at": soon}},
    )
    assert ir.async_get(hass).async_get_issue(DOMAIN, "license_expiring") is not None


def test_repair_flow_helpers() -> None:
    from custom_components.leassh.repairs import LeasshRepairFlow

    flow = LeasshRepairFlow("license_invalid", {"upgrade_url": "u", "days": 3})
    assert flow._license_issue() is True
    assert flow._data()["upgrade_url"] == "u"
    assert LeasshRepairFlow("recipe_broken_x", {})._license_issue() is False


async def test_repair_flow_all_steps(hass) -> None:
    from custom_components.leassh.repairs import LeasshRepairFlow

    calls = []

    class Client:
        async def reload_packs(self):
            calls.append("reload")

        async def set_pack_enabled(self, pack, enabled):
            calls.append(("pack", pack, enabled))

        async def set_recipe_enabled(self, instance, enabled):
            calls.append(("recipe", instance, enabled))

    class Coordinator:
        async def async_refresh(self):
            calls.append("refresh")

    runtime = SimpleNamespace(client=Client(), coordinator=Coordinator())
    entry = _entry()
    entry.runtime_data = runtime
    entry.add_to_hass(hass)

    for issue_id, data in [
        ("license_invalid", {}),
        ("license_expiring", {}),
        ("node_limit_reached", {}),
        ("pack_parse_x_y", {"pack": "x", "entry": "y"}),
        ("recipe_broken_i", {"recipe": "r", "instance": "i"}),
        ("telemetry_degraded_h", {}),
    ]:
        flow = LeasshRepairFlow(issue_id, data)
        flow.hass = hass
        flow.flow_id = "f"
        flow.handler = DOMAIN
        result = await flow.async_step_init()
        assert result["type"].value == "menu"

    flow = LeasshRepairFlow("license_invalid", {})
    flow.hass = hass
    flow.flow_id = "f"
    flow.handler = DOMAIN
    assert (await flow.async_step_update_license({}))["type"].value == "create_entry"
    assert (await flow.async_step_renew({}))["type"].value == "create_entry"
    assert (await flow.async_step_remove_node({}))["type"].value == "create_entry"
    assert (await flow.async_step_upgrade({}))["type"].value == "create_entry"

    form_flow = LeasshRepairFlow("license_invalid", {})
    form_flow.hass = hass
    form_flow.flow_id = "f"
    form_flow.handler = DOMAIN
    assert (await form_flow.async_step_update_license())["type"].value == "form"

    flow = LeasshRepairFlow("pack_parse_x_y", {"pack": "x"})
    flow.hass = hass
    flow.flow_id = "f"
    flow.handler = DOMAIN
    assert (await flow.async_step_retry({}))["type"].value == "create_entry"
    assert (await flow.async_step_disable_pack({}))["type"].value == "create_entry"

    flow = LeasshRepairFlow("recipe_broken_i", {"instance": "i"})
    flow.hass = hass
    flow.flow_id = "f"
    flow.handler = DOMAIN
    assert (await flow.async_step_disable_recipe({}))["type"].value == "create_entry"
    assert ("recipe", "i", False) in calls
    assert ("pack", "x", False) in calls
    assert "reload" in calls


async def test_repair_edge_cases_and_error_branches(hass, caplog) -> None:
    from datetime import datetime, timedelta, timezone

    from custom_components.leassh.repairs import (
        LeasshRepairFlow,
        _check_packs,
        _check_recipes,
        _check_telemetry,
        async_create_fix_flow,
    )

    runtime = SimpleNamespace(
        coordinator=SimpleNamespace(
            recipe_instances=[
                {"instance": {"id": None}},
                {"instance": {"id": "ok", "last_error": None, "last_suppressed_reason": None}},
            ],
            data={"nodes": [{"name": "no-host"}, {"name": "ok", "host": "h", "reachable": True, "telemetry_ok": True}]},
        )
    )
    _check_recipes(hass, _entry(), runtime)
    _check_telemetry(hass, _entry(), runtime)
    _check_packs(hass, _entry(), {"packs": {"entries": [{"pack": "p", "entry": "e"}]}})

    _check_license(hass, {"license": {"valid": True, "expires_at": "not-a-date"}})
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    _check_license(hass, {"license": {"valid": True, "expires_at": future}})
    assert ir.async_get(hass).async_get_issue(DOMAIN, "license_expiring") is None

    flow = await async_create_fix_flow(hass, "x", None)
    assert isinstance(flow, LeasshRepairFlow)

    # _runtime/_entry fallbacks.
    flow = LeasshRepairFlow("x", {})
    flow.hass = hass
    flow.flow_id = "f"
    flow.handler = DOMAIN
    assert await flow._runtime() is None
    plain_entry = _entry()
    plain_entry.add_to_hass(hass)
    assert await flow._entry() is not None

    class BadClient:
        async def reload_packs(self):
            raise RuntimeError("reload failed")

        async def set_recipe_enabled(self, instance, enabled):
            raise RuntimeError("recipe failed")

        async def set_pack_enabled(self, pack, enabled):
            raise RuntimeError("pack failed")

    bad_runtime = SimpleNamespace(
        client=BadClient(), coordinator=SimpleNamespace(async_refresh=lambda: _noop())
    )
    entry = _entry()
    entry.runtime_data = bad_runtime
    entry.add_to_hass(hass)

    form_flow = LeasshRepairFlow("license_invalid", {})
    form_flow.hass = hass
    form_flow.flow_id = "f"
    form_flow.handler = DOMAIN
    assert (await form_flow.async_step_update_license())["type"].value == "form"

    flow = LeasshRepairFlow("pack_parse_x_y", {"pack": "x"})
    flow.hass = hass
    flow.flow_id = "f"
    flow.handler = DOMAIN
    assert (await flow.async_step_retry({}))["type"].value == "create_entry"
    assert (await flow.async_step_disable_pack({}))["type"].value == "create_entry"
    flow = LeasshRepairFlow("recipe_broken_i", {"instance": "i"})
    flow.hass = hass
    flow.flow_id = "f"
    flow.handler = DOMAIN
    assert (await flow.async_step_disable_recipe({}))["type"].value == "create_entry"
    assert "reload failed" in caplog.text


async def _noop():
    return None
