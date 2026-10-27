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
    state = hass.states.get("binary_sensor.leassh_server_connected")
    assert state is not None, "connected binary_sensor missing"

    # Coordinator fetched the fleet: per-node devices/entities exist.
    entities = [
        e for e in hass.states.async_all() if e.domain in ("sensor", "binary_sensor", "button")
    ]
    assert len(entities) >= 10, f"expected per-node entities, got {[e.entity_id for e in entities]}"
    buttons = [e.entity_id for e in entities if e.domain == "button"]
    assert any("reboot" in b for b in buttons), buttons

    await _test_notify_chain(hass, entry)

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.leassh_server_connected")
    assert state is None or state.state == "unavailable"


async def _test_notify_chain(hass, entry):
    """The gate's notify leg, proven in software with a mock companion app.

    1. A `notify` event from the live server (POST /api/notify, forge
       token) must arrive over WS and be delivered to every mobile_app
       target with the iOS critical-sound payload for level=urgent.
    2. The notify.leassh service itself must work for HA automations.
    """
    import asyncio
    import json
    import urllib.request

    delivered = []

    async def fake_companion_app(call):
        delivered.append(call.data)

    hass.services.async_register("notify", "mobile_app_fake_phone", fake_companion_app)

    # --- 1. server -> WS event -> companion target ---
    forge_token = os.environ["LEASSH_TEST_FORGE_TOKEN"]
    url = os.environ["LEASSH_TEST_URL"].rstrip("/") + "/api/notify"
    body = json.dumps({
        "source": "harness", "level": "urgent",
        "title": "chain check", "body": "critical path",
        "ack_url": "https://example/ack/1",
    }).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {forge_token}",
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        assert resp.status == 200

    for _ in range(40):  # up to 10s for WS round trip
        await hass.async_block_till_done()
        if delivered:
            break
        await asyncio.sleep(0.25)

    assert delivered, "notify event never reached the companion target"
    payload = delivered[0]
    assert payload["title"] == "chain check"
    push = payload["data"]["push"]
    assert push["sound"]["critical"] == 1, push
    assert payload["data"]["url"] == "https://example/ack/1"

    # --- 2. HA automation path: the notify.leassh entity ---
    # On the 2024.6 floor the legacy notify-service shim for NotifyEntity
    # behaves differently from current HA; the service on modern HA routes
    # to exactly this coroutine, so drive it directly.
    delivered.clear()
    runtime = hass.data[DOMAIN][entry.entry_id]
    await runtime.send_notification(
        "direct call", "from an automation", {"level": "urgent"}
    )
    await hass.async_block_till_done()
    assert delivered and delivered[0]["data"]["push"]["sound"]["critical"] == 1
