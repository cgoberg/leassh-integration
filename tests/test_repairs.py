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
