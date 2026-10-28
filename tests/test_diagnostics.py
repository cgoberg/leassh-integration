"""Diagnostics must never leak the API token (roadmap M1.5)."""

from __future__ import annotations

from types import SimpleNamespace

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.leassh.const import DOMAIN
from custom_components.leassh.diagnostics import async_get_config_entry_diagnostics


async def test_diagnostics_redacts_token(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"server_url": "http://127.0.0.1:8790", "api_token": "super-secret-token"},
        entry_id="entry1",
        unique_id="127.0.0.1:8790",
    )
    entry.add_to_hass(hass)
    runtime = SimpleNamespace(
        client=SimpleNamespace(base_url="http://127.0.0.1:8790"),
        connected=True,
        ws=SimpleNamespace(last_frame_monotonic=None),
        coordinator=SimpleNamespace(
            last_update_success=True, _etag="abcdef0123456789", data={"nodes": [1, 2]}
        ),
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime

    result = await async_get_config_entry_diagnostics(hass, entry)
    blob = repr(result)
    assert "super-secret-token" not in blob
    assert result["api_token"] == "**REDACTED**"
    assert result["server_url"] == "http://127.0.0.1:8790"
    assert result["coordinator"]["nodes"] == 2
