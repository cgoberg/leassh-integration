"""Harness test: full integration setup against the live Leassh server.

Runs only when LEASSH_TEST_URL/LEASSH_TEST_TOKEN are set (CT103 dev box);
otherwise skipped. Exercises async_setup_entry end to end: coordinator
first refresh, WS session, entity registration across all platforms.
"""

from __future__ import annotations

import os

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.leassh.const import CONF_API_TOKEN, CONF_SERVER_URL, DOMAIN

SERVER_URL = os.environ.get("LEASSH_TEST_URL")
API_TOKEN = os.environ.get("LEASSH_TEST_TOKEN")

pytestmark = [
    pytest.mark.skipif(
        not SERVER_URL or not API_TOKEN, reason="live Leassh server not provided"
    ),
    pytest.mark.enable_socket,
]


async def test_full_setup(hass, socket_enabled):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_SERVER_URL: SERVER_URL, CONF_API_TOKEN: API_TOKEN},
        entry_id="test_entry",
        unique_id="test",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Hub sensor: WS heartbeat may still be ramping; the entity must exist.
    state = hass.states.get("binary_sensor.leassh_connected")
    assert state is not None, "connected binary_sensor missing"

    # Coordinator fetched the fleet: per-node devices/entities exist.
    entities = [
        e for e in hass.states.async_all() if e.domain in ("sensor", "binary_sensor", "button")
    ]
    assert len(entities) >= 10, f"expected per-node entities, got {[e.entity_id for e in entities]}"
    buttons = [e.entity_id for e in entities if e.domain == "button"]
    assert any("reboot" in b for b in buttons), buttons

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.leassh_connected")
    assert state is None or state.state == "unavailable"
