"""Integration setup/unload, services and runtime event handling."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.leassh import (
    LeasshRuntime,
    _first_runtime,
    _register_services,
    async_setup,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.leassh.api import LeasshAuthError, LeasshConnectionError
from custom_components.leassh.const import CONF_API_TOKEN, CONF_SERVER_URL, DOMAIN, EVENT_LEASSH


class FakeCoordinator:
    def __init__(self):
        self.data = {"nodes": [{"name": "rig", "reachable": False}]}
        self.refreshed = 0
        self.shutdown_called = False
        self.listeners = []

    async def async_refresh(self):
        self.refreshed += 1

    async def async_shutdown(self):
        self.shutdown_called = True

    def async_add_listener(self, callback):
        self.listeners.append(callback)
        return lambda: None

    def async_set_updated_data(self, data):
        self.data = data


class FakeClient:
    base_url = "http://127.0.0.1:8790"

    def __init__(self):
        self.calls = []

    async def add_node(self, node):
        self.calls.append(("add", node))

    async def remove_node(self, name):
        self.calls.append(("remove", name))

    async def install_recipe(self, recipe_id):
        self.calls.append(("install", recipe_id))

    async def update_recipe_instance(self, instance, parameters=None):
        self.calls.append(("configure", instance, parameters))

    async def set_recipe_enabled(self, instance, enabled):
        self.calls.append(("enabled", instance, enabled))

    async def dry_run_recipe(self, instance, node=None, idle_minutes=15):
        self.calls.append(("dry", instance, node, idle_minutes))
        return {"ok": True}

    async def set_protection_profile(self, profile, confirmation=None):
        self.calls.append(("profile", profile, confirmation))

    async def approve_action(self, request_id, principal):
        self.calls.append(("approve", request_id, principal))

    async def deny_action(self, request_id, principal):
        self.calls.append(("deny", request_id, principal))


def make_entry(runtime=None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_SERVER_URL: "http://127.0.0.1:8790", CONF_API_TOKEN: "token"},
        entry_id="entry1",
        unique_id="127.0.0.1:8790",
    )
    if runtime is not None:
        entry.runtime_data = runtime
    return entry


def make_runtime(hass) -> tuple[LeasshRuntime, FakeCoordinator, FakeClient]:
    coordinator = FakeCoordinator()
    client = FakeClient()
    runtime = LeasshRuntime(hass, client=client, coordinator=coordinator)
    return runtime, coordinator, client


async def test_services_cover_all_control_plane_actions(hass) -> None:
    runtime, coordinator, client = make_runtime(hass)
    entry = make_entry(runtime)
    entry.add_to_hass(hass)
    _register_services(hass)
    assert _first_runtime(hass) is runtime

    await hass.services.async_call(
        DOMAIN, "add_node", {"name": "rig", "host": "h", "ssh": "u@h", "site": "home"}, blocking=True
    )
    await hass.services.async_call(DOMAIN, "remove_node", {"name": "rig"}, blocking=True)
    await hass.services.async_call(
        DOMAIN, "install_recipe", {"recipe_id": "household.bedtime"}, blocking=True
    )
    await hass.services.async_call(
        DOMAIN,
        "configure_recipe",
        {"instance": "inst", "parameters": {"grace": "60s"}},
        blocking=True,
    )
    await hass.services.async_call(
        DOMAIN, "set_recipe_enabled", {"instance": "inst", "enabled": True}, blocking=True
    )
    result = await hass.services.async_call(
        DOMAIN,
        "dry_run_recipe",
        {"instance": "inst", "node": "rig", "idle_minutes": 30},
        blocking=True,
        return_response=True,
    )
    await hass.services.async_call(
        DOMAIN, "set_protection_profile", {"profile": "guardian"}, blocking=True
    )
    await hass.services.async_call(
        DOMAIN, "approve_action", {"request_id": "a1", "principal": "owner"}, blocking=True
    )
    await hass.services.async_call(
        DOMAIN, "deny_action", {"request_id": "a1", "principal": "owner"}, blocking=True
    )
    assert result["ok"] is True
    assert ("add", {"name": "rig", "host": "h", "ssh": "u@h"}) in client.calls
    assert coordinator.refreshed == 8


async def test_services_without_runtime_raise(hass) -> None:
    _register_services(hass)
    with pytest.raises(RuntimeError):
        await hass.services.async_call(
            DOMAIN, "install_recipe", {"recipe_id": "x"}, blocking=True
        )


async def test_runtime_event_updates_and_notify_paths(hass) -> None:
    runtime, coordinator, _ = make_runtime(hass)
    fired = []
    hass.bus.async_listen(EVENT_LEASSH, lambda event: fired.append(event.data))
    runtime.handle_event("node.online", {"node": "rig"})
    assert coordinator.data["nodes"][0]["reachable"] is True
    runtime.handle_event("node.offline", {"node": "rig"})
    assert coordinator.data["nodes"][0]["activity"] == "Offline"
    runtime.handle_event("node.activity", {"node": "rig", "activity": "Idle"})
    assert coordinator.data["nodes"][0]["activity"] == "Idle"
    runtime.handle_event("node.online", {})
    await hass.async_block_till_done()
    assert len(fired) == 3

    delivered = []

    async def send(title, message, data):
        delivered.append((title, message, data))

    runtime.send_notification = send
    runtime.deliver_server_notify({"title": "t", "body": "b"})
    await hass.async_block_till_done()
    assert delivered[0][0] == "t"

    runtime.send_notification = None
    runtime.deliver_server_notify({"title": "dropped"})
    runtime.set_connected(True)
    assert runtime.connected is True


async def test_async_setup_and_entry_lifecycle(hass) -> None:
    assert await async_setup(hass, {}) is True
    entry = make_entry()
    entry.add_to_hass(hass)

    async def get_devices(self, etag=None):
        return None, {"nodes": []}

    with patch(
        "custom_components.leassh.api.LeasshClient.get_devices", new=get_devices
    ), patch(
        "custom_components.leassh.LeasshCoordinator.async_config_entry_first_refresh",
        new=lambda self: _noop(),
    ), patch(
        "custom_components.leassh.LeasshWsClient.start", new=lambda self: None
    ), patch(
        "custom_components.leassh.async_check", new=lambda *args: _noop()
    ), patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        new=lambda entry, platforms: _noop(),
    ), patch.object(
        hass.config_entries,
        "async_unload_platforms",
        new=lambda entry, platforms: _truthy(),
    ):
        assert await async_setup_entry(hass, entry) is True
        assert entry.runtime_data is not None
        assert await async_unload_entry(hass, entry) is True
        assert entry.runtime_data is None


async def _noop():
    return None


async def _truthy():
    return True


async def test_setup_entry_auth_and_connection_errors(hass) -> None:
    entry = make_entry()

    async def reject(self, etag=None):
        raise LeasshAuthError("401")

    with patch(
        "custom_components.leassh.api.LeasshClient.get_devices", new=reject
    ):
        with pytest.raises(ConfigEntryAuthFailed):
            await async_setup_entry(hass, entry)

    async def offline(self, etag=None):
        raise LeasshConnectionError("offline")

    with patch(
        "custom_components.leassh.api.LeasshClient.get_devices", new=offline
    ):
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, entry)
