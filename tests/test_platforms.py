"""Platform setup and entity behavior for sensor, binary_sensor and button."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.leassh.api import LeasshApiError
from custom_components.leassh.binary_sensor import (
    LeasshConnectedSensor,
    NodeBinarySensor,
    async_setup_entry as binary_setup,
)
from custom_components.leassh.button import (
    ActionButton,
    async_setup_entry as button_setup,
    catalog_desc,
)
from custom_components.leassh.const import DOMAIN
from custom_components.leassh.sensor import (
    HUB_SENSORS,
    LeasshHubSensor,
    LeasshPackSuggestionsSensor,
    LeasshLicenseSensor,
    async_setup_entry as sensor_setup,
)

NODE = {
    "name": "rig",
    "host": "10.0.0.9",
    "reachable": True,
    "telemetry_ok": True,
    "activity": "Active",
    "load": "Low",
    "console_signed_in": True,
    "cpu_percent": 12.0,
    "metrics": {"cpu_temp_c": {"value": 50, "name": "CPU temperature", "unit": "°C"}},
    "series": {
        "gpu_nvidia": [
            {
                "key": "GPU-1",
                "index": 0,
                "label": "GPU",
                "values": {"temperature": 60},
                "meta": {"temperature": {"unit": "°C"}},
            }
        ]
    },
}


class FakeCoordinator:
    def __init__(self, nodes):
        self.data = {"nodes": nodes}
        self.listeners = []
        self.recipe_instances = []

    def async_add_listener(self, callback):
        self.listeners.append(callback)

        def remove():
            self.listeners.remove(callback)

        return remove

    def async_set_updated_data(self, data):
        self.data = data
        for callback in list(self.listeners):
            callback()


def make_runtime(nodes):
    coordinator = FakeCoordinator(nodes)
    runtime = SimpleNamespace(
        coordinator=coordinator,
        client=SimpleNamespace(base_url="http://127.0.0.1:8790"),
        connected=True,
        ws_callbacks=[],
    )
    return runtime, coordinator


def make_entry(runtime):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        entry_id="entry1",
        unique_id="127.0.0.1:8790",
    )
    entry.runtime_data = runtime
    return entry


async def test_sensor_platform_adds_nodes_generic_and_hub_entities(hass) -> None:
    runtime, coordinator = make_runtime([NODE])
    entry = make_entry(runtime)
    added = []
    await sensor_setup(hass, entry, added.extend)
    assert added
    assert any(type(entity).__name__ == "NodeSensor" for entity in added)
    assert any(type(entity).__name__ == "GenericMetricSensor" for entity in added)
    assert any(type(entity).__name__ == "SeriesMetricSensor" for entity in added)
    assert any(isinstance(entity, LeasshHubSensor) for entity in added)
    # Listener is registered and a new node adds entities.
    coordinator.async_set_updated_data({"nodes": [NODE, dict(NODE, name="rig2", host="10.0.0.10")]})
    assert any(getattr(entity, "_host", None) == "10.0.0.10" for entity in added)


async def test_hub_sensors_cover_fleet_consent_setup_and_recipes(hass) -> None:
    runtime, coordinator = make_runtime([NODE])
    entry = make_entry(runtime)
    coordinator.fleet = {
        "total": 1,
        "reachable": 1,
        "open_claims": 1,
        "critical_claims": 0,
        "ledger_valid": True,
        "ledger_last_hash": "abc",
    }
    coordinator.consent = {"total": 1, "statement": "s", "nodes": []}
    coordinator.setup = {"ok": False, "pending_steps": 2, "steps": [], "profile": "guardian", "version": "0.3.0"}
    coordinator.recipes = {"catalog": [{}], "installed": [{}], "instances": [{}]}
    for description in HUB_SENSORS:
        sensor = LeasshHubSensor(entry, runtime, description)
        value = sensor.native_value
        attrs = sensor.extra_state_attributes
        assert isinstance(attrs, dict)
        assert value is not None or description.key == "setup_status"

    suggestions = LeasshPackSuggestionsSensor(entry, runtime)
    coordinator.suggestions = {"nodes": [{"node": "rig", "suggested_packs": [{"id": "gpu"}]}]}
    assert suggestions.native_value == 1
    assert suggestions.extra_state_attributes["suggestions"][0]["pack"] == "gpu"

    license_sensor = LeasshLicenseSensor(entry, runtime)
    coordinator.health = {"license": {"tier": "family", "valid": True, "max_nodes": 10}}
    assert license_sensor.native_value == "family"
    assert license_sensor.extra_state_attributes["max_nodes"] == 10


async def test_binary_platform_adds_connected_and_node_sensors(hass) -> None:
    runtime, coordinator = make_runtime([NODE])
    entry = make_entry(runtime)
    added = []
    await binary_setup(hass, entry, added.extend)
    connected = next(entity for entity in added if isinstance(entity, LeasshConnectedSensor))
    assert connected.is_on is True
    runtime.connected = False
    assert connected.is_on is False
    node_sensor = next(
        entity
        for entity in added
        if isinstance(entity, NodeBinarySensor) and entity._key == "at_desk"
    )
    assert node_sensor.is_on is True
    assert node_sensor.extra_state_attributes["activity"] == "Active"
    coordinator.async_set_updated_data({"nodes": []})
    assert node_sensor.available is False


async def test_button_platform_and_press_paths(hass, caplog) -> None:
    runtime, coordinator = make_runtime([NODE])
    entry = make_entry(runtime)
    calls = []

    async def get_actions():
        return {"verbs": [{"verb": "reboot_node", "description": "Reboot"}]}

    async def action(node, verb):
        calls.append((node, verb))
        return {"ok": True}

    runtime.client.get_actions = get_actions
    runtime.client.action = action
    added = []
    await button_setup(hass, entry, added.extend)
    assert added
    button = added[0]
    assert isinstance(button, ActionButton)
    await button.async_press()
    assert calls == [("rig", "reboot_node")]
    assert catalog_desc({"verbs": [{"verb": "x", "description": "X"}]}, "x") == "X"
    assert catalog_desc({}, "x") == "x"

    async def fail(node, verb):
        raise LeasshApiError("nope")

    runtime.client.action = fail
    with pytest.raises(HomeAssistantError):
        await button.async_press()
    coordinator.async_set_updated_data({"nodes": []})
    with pytest.raises(RuntimeError):
        await button.async_press()


async def test_sensor_edge_cases_and_fallbacks(hass) -> None:
    from custom_components.leassh.sensor import (
        GenericMetricSensor,
        NodeSensor,
        RecipeStatusSensor,
        SeriesMetricSensor,
        _map_device_class,
        _map_state_class,
        _series_display,
    )

    assert _map_device_class(None) is None
    assert _map_device_class("temperature") is not None
    assert _map_device_class("not-a-class") is None
    assert _map_state_class(None) is None
    assert _map_state_class("measurement") is not None
    assert _map_state_class("not-a-class") is None
    assert _series_display(None, "label", "temperature").startswith("label")
    assert _series_display(None, None, "unknown_field") == "Series unknown field"

    runtime, coordinator = make_runtime(
        [
            {"name": "no-host"},
            {
                "name": "rig",
                "host": "10.0.0.9",
                "reachable": True,
                "console_signed_in": True,
                "activity": "Active",
                "metrics": {
                    "bad": "not-a-dict",
                    "cpu_temp_c": {
                        "value": 50,
                        "device_class": "not-a-class",
                        "state_class": "not-a-class",
                        "icon": "mdi:x",
                    },
                },
                "series": {
                    "bad": "not-a-list",
                    "gpu": [
                        "not-a-dict",
                        {
                            "key": "GPU-1",
                            "index": None,
                            "values": {"temperature": 60, "memory_used": 10},
                            "meta": {"temperature": {"name": "GPU temp"}},
                        },
                    ],
                },
            },
        ]
    )
    entry = make_entry(runtime)
    added = []
    await sensor_setup(hass, entry, added.extend)
    assert added

    node = next(entity for entity in added if isinstance(entity, NodeSensor))
    coordinator.data = {"nodes": []}
    assert node.native_value is None
    assert node.available is False

    # Direct value branches.
    data = {
        "name": "rig",
        "host": "10.0.0.9",
        "activity": "Active",
        "load": "Low",
        "cpu_percent": 12.0,
        "ram_used_mb": 50,
        "ram_total_mb": 100,
        "metrics": {"cpu_temp_c": {"value": 50}},
        "gpu_percent": 12.3,
        "gpu_temp_c": 60,
        "cpu_temp_c": 50,
    }
    coordinator.data = {"nodes": [data]}
    for key in ["activity", "load", "cpu_usage", "ram_percent", "gpu_usage", "gpu_temperature", "cpu_temperature", "unknown"]:
        from custom_components.leassh.sensor import NODE_SENSORS

        description = next((d for d in NODE_SENSORS if d.key == key), NODE_SENSORS[0])
        sensor = NodeSensor(coordinator, entry, "10.0.0.9", {}, description)
        assert sensor.native_value is not None or key == "unknown"

    generic = GenericMetricSensor(
        coordinator, entry, "10.0.0.9", {}, metric_id="cpu_temp_c", name="CPU", unit=None,
        device_class=None, state_class=None, icon=None, source={}
    )
    assert generic.native_value == 50
    coordinator.data = {"nodes": []}
    assert generic.native_value is None

    series = SeriesMetricSensor(
        coordinator, entry, "10.0.0.9", {}, series_id="gpu", key="GPU-1",
        field="temperature", name="GPU temp", unit=None, device_class=None,
        state_class=None, icon=None, instance={}, declared=60,
    )
    coordinator.data = {"nodes": [data]}
    assert series.native_value is None
    coordinator.data = {"nodes": []}
    assert series.native_value is None


async def test_hub_and_recipe_sensor_value_branches(hass) -> None:
    from custom_components.leassh.sensor import (
        HUB_SENSORS,
        LeasshHubSensor,
        LeasshLicenseSensor,
        LeasshPackSuggestionsSensor,
        RecipeStatusSensor,
    )

    runtime, coordinator = make_runtime([NODE])
    entry = make_entry(runtime)
    coordinator.last_update_success = True
    coordinator.fleet = {"total": 1, "reachable": 1, "critical_claims": 1}
    fleet_state = next(d for d in HUB_SENSORS if d.key == "fleet_state")
    sensor = LeasshHubSensor(entry, runtime, fleet_state)
    assert sensor.native_value == "critical"
    coordinator.fleet = {"total": 1, "reachable": 0}
    assert sensor.native_value == "degraded"
    coordinator.fleet = {"total": 1, "reachable": 1, "open_claims": 1}
    assert sensor.native_value == "attention"
    coordinator.fleet = {"total": 1, "reachable": 1}
    assert sensor.native_value == "ok"
    coordinator.fleet = {}
    assert sensor.native_value is None
    assert sensor.available is False

    license_sensor = LeasshLicenseSensor(entry, runtime)
    coordinator.health = {}
    assert license_sensor.native_value == "unknown"
    assert license_sensor.available is True

    suggestions = LeasshPackSuggestionsSensor(entry, runtime)
    coordinator.suggestions = {}
    assert suggestions.native_value == 0
    assert suggestions.available is True

    record = {
        "instance": {"id": "inst-1", "recipe_id": "r", "enabled": False},
        "status": {},
    }
    coordinator.recipe_instances = [record]
    recipe = RecipeStatusSensor(entry, runtime, record)
    assert recipe.suggested_object_id == "leassh_recipe_r"
    assert recipe.native_value == "disabled"
    record["instance"]["enabled"] = True
    record["instance"]["status"] = None
    assert recipe.native_value == "installed"
    coordinator.recipe_instances = []
    assert recipe.native_value == "disabled"
    assert recipe.available is False
