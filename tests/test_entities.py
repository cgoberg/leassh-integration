"""Naming, identity and lifecycle tests (roadmap v2.1 M1).

These run without a live server: they exercise the metadata layer, which is
where the old undefined-name / `_2` suffix bug lived.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

from custom_components.leassh.binary_sensor import NodeBinarySensor
from custom_components.leassh.button import ActionButton
from custom_components.leassh.const import DOMAIN
from custom_components.leassh.const import FAMILY_SENSOR_KEYS
from custom_components.leassh.coordinator import FIXED_ENTITY_KEYS, LeasshCoordinator
from custom_components.leassh.entity import (
    device_identifier,
    entity_unique_id,
    humanize,
    node_device,
    title_case,
)
from custom_components.leassh.sensor import (
    NODE_SENSORS,
    GenericMetricSensor,
    NodeSensor,
    SeriesMetricSensor,
)

NODE = {
    "name": "gpu-primary-131",
    "host": "192.168.1.131",
    "reachable": True,
    "telemetry_ok": True,
    "activity": "Active",
    "load": "Low",
    "cpu_percent": 11.25,
    "ram_used_mb": 8192,
    "ram_total_mb": 16384,
    "gpu_temp_c": 46.0,
}


def make_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={"server_url": "http://127.0.0.1:8790", "api_token": "secret-token"},
        entry_id="entry1",
        unique_id="127.0.0.1:8790",
    )


def stub_coordinator(nodes: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(data={"nodes": nodes}, config_entry=make_entry())


def test_humanize_preserves_acronyms() -> None:
    assert humanize("gpu-primary-131") == "GPU Primary 131"


def test_title_case_has_no_trailing_space() -> None:
    assert title_case("reboot_node") == "Reboot"
    assert title_case("sign_out_session") == "Sign out session"


def test_device_identity_is_host_not_name() -> None:
    entry = make_entry()
    assert device_identifier(entry, "192.168.1.131") == "entry1_192.168.1.131"
    # Renaming the node must not move identity.
    renamed = dict(NODE, name="kids-gaming-pc")
    assert device_identifier(entry, renamed["host"]) == device_identifier(
        entry, NODE["host"]
    )


def test_entity_unique_id_is_host_based() -> None:
    entry = make_entry()
    assert entity_unique_id(entry, "192.168.1.131", "cpu_usage") == (
        "entry1_192.168.1.131_cpu_usage"
    )
    assert entity_unique_id(entry, "192.168.1.131", "gpu_nvidia_temperature", "GPU-abc") == (
        "entry1_192.168.1.131_gpu_nvidia_temperature_GPU-abc"
    )


async def test_sensor_names_ids_and_device(hass) -> None:
    entry = make_entry()
    coordinator = stub_coordinator([NODE])
    device = node_device(hass, entry, NODE)
    assert device is not None
    assert (DOMAIN, "entry1_192.168.1.131") in device["identifiers"]

    node = dict(
        NODE,
        engine_version="0.2.1",
        observed_at="2026-10-28T10:00:00Z",
        age=4,
    )
    coordinator = stub_coordinator([node])
    cpu = NodeSensor(
        coordinator,
        entry,
        "192.168.1.131",
        device,
        next(d for d in NODE_SENSORS if d.key == "cpu_usage"),
    )
    assert cpu.has_entity_name is True
    assert cpu.unique_id == "entry1_192.168.1.131_cpu_usage"
    assert cpu.translation_key == "cpu_usage"
    assert cpu.native_value == 11.2  # rounded for display
    assert cpu.device_class is None
    assert cpu.state_class is SensorStateClass.MEASUREMENT
    provenance = cpu.extra_state_attributes
    assert provenance["pack"] == "core"
    assert provenance["pack_version"] == "0.2.1"
    assert provenance["observed_at"] == "2026-10-28T10:00:00Z"
    assert provenance["age"] == 4

    temperature = NodeSensor(
        coordinator,
        entry,
        "192.168.1.131",
        device,
        next(d for d in NODE_SENSORS if d.key == "gpu_temperature"),
    )
    assert temperature.device_class is SensorDeviceClass.TEMPERATURE
    assert temperature.native_unit_of_measurement == "°C"


async def test_enum_options_and_state_tokens(hass) -> None:
    entry = make_entry()
    coordinator = stub_coordinator([NODE])
    device = node_device(hass, entry, NODE)

    activity = NodeSensor(
        coordinator,
        entry,
        "192.168.1.131",
        device,
        next(d for d in NODE_SENSORS if d.key == "activity"),
    )
    assert activity.device_class is SensorDeviceClass.ENUM
    assert activity.options == ["active", "away", "idle", "offline"]
    assert activity.native_value == "active"

    load = NodeSensor(
        coordinator,
        entry,
        "192.168.1.131",
        device,
        next(d for d in NODE_SENSORS if d.key == "load"),
    )
    assert load.options == ["none", "low", "medium", "high", "unknown"]
    assert load.native_value == "low"


async def test_binary_sensors_use_host_identity(hass) -> None:
    entry = make_entry()
    coordinator = stub_coordinator([NODE])
    device = node_device(hass, entry, NODE)
    online = NodeBinarySensor(
        coordinator,
        entry,
        "192.168.1.131",
        device,
        "connectivity",
        "online",
        "reachable",
    )
    assert online.has_entity_name is True
    assert online.unique_id == "entry1_192.168.1.131_online"
    assert online.is_on is True


async def test_at_desk_is_presence_and_signed_in_stays_occupancy(hass) -> None:
    entry = make_entry()
    coordinator = stub_coordinator([NODE])
    device = node_device(hass, entry, NODE)

    signed_in = NodeBinarySensor(
        coordinator,
        entry,
        "192.168.1.131",
        device,
        "occupancy",
        "signed_in",
        "console_signed_in",
    )
    assert signed_in.device_class is not None
    assert str(signed_in.device_class) == "occupancy"

    at_desk = NodeBinarySensor(
        coordinator,
        entry,
        "192.168.1.131",
        device,
        "presence",
        "at_desk",
        "activity",
    )
    assert str(at_desk.device_class) == "presence"
    assert at_desk.unique_id == "entry1_192.168.1.131_at_desk"
    assert at_desk.is_on is True
    assert at_desk.extra_state_attributes["activity"] == "Active"

    # Activity falling to idle turns it off after the hold window, not on the
    # first sample (single-sample flap protection).
    coordinator.data["nodes"][0]["activity"] = "Idle"
    assert at_desk.is_on is True
    at_desk._at_desk_last = time.monotonic() - (at_desk.AT_DESK_HOLD_SECS + 1)
    assert at_desk.is_on is False


async def test_buttons_have_distinct_translation_keys(hass) -> None:
    entry = make_entry()
    coordinator = stub_coordinator([NODE])
    runtime = SimpleNamespace(
        coordinator=coordinator, client=SimpleNamespace(base_url="http://x")
    )
    device = node_device(hass, entry, NODE)
    reboot = ActionButton(runtime, entry, device, "192.168.1.131", "reboot_node", "Reboot")
    signout = ActionButton(
        runtime, entry, device, "192.168.1.131", "sign_out_session", "Sign out"
    )
    assert reboot.translation_key == "reboot_node"
    assert signout.translation_key == "sign_out_session"
    # Resolved from translations, not a stale _attr_name (the trailing-space bug).
    assert getattr(reboot, "_attr_name", None) is None
    assert getattr(signout, "_attr_name", None) is None
    assert reboot.unique_id != signout.unique_id


async def test_generic_scalar_metric_uses_pack_metadata(hass) -> None:
    entry = make_entry()
    node = dict(
        NODE,
        metrics={
            "cpu_temp_c": {
                "value": 54.0,
                "name": "CPU temperature",
                "unit": "°C",
                "device_class": "temperature",
                "state_class": "measurement",
                "pack": "cpu-thermal",
                "pack_version": "1.0.0",
                "observed_at": "2026-10-28T08:00:00Z",
                "age": 3,
            }
        },
    )
    coordinator = stub_coordinator([node])
    device = node_device(hass, entry, node)
    sensor = GenericMetricSensor(
        coordinator,
        entry,
        "192.168.1.131",
        device,
        metric_id="cpu_temp_c",
        name="CPU temperature",
        unit="°C",
        device_class="temperature",
        state_class="measurement",
        icon=None,
        source=node["metrics"]["cpu_temp_c"],
    )
    assert sensor.has_entity_name is True
    assert sensor.unique_id == "entry1_192.168.1.131_cpu_temp_c"
    assert sensor.name == "CPU temperature"
    assert sensor.device_class is SensorDeviceClass.TEMPERATURE
    assert sensor.native_value == 54.0
    assert sensor.extra_state_attributes["pack"] == "cpu-thermal"
    assert sensor.extra_state_attributes["age"] == 3


async def test_per_gpu_series_names_are_numbered_and_keyed_by_uuid(hass) -> None:
    entry = make_entry()
    node = dict(
        NODE,
        series={
            "gpu_nvidia": [
                {
                    "key": "GPU-aaa",
                    "uuid": "GPU-aaa",
                    "index": 0,
                    "label": "NVIDIA GeForce RTX 4090",
                    "values": {"utilization": 9, "temperature": 46},
                },
                {
                    "key": "GPU-bbb",
                    "uuid": "GPU-bbb",
                    "index": 1,
                    "label": "NVIDIA GeForce RTX 4090",
                    "values": {"utilization": 19, "temperature": 48},
                },
            ]
        },
    )
    coordinator = stub_coordinator([node])
    device = node_device(hass, entry, node)
    usage0 = SeriesMetricSensor(
        coordinator,
        entry,
        "192.168.1.131",
        device,
        series_id="gpu_nvidia",
        key="GPU-aaa",
        field="utilization",
        name="GPU 0 usage",
        unit="%",
        device_class=None,
        state_class="measurement",
        icon=None,
        instance=node["series"]["gpu_nvidia"][0],
        declared=9,
    )
    usage1 = SeriesMetricSensor(
        coordinator,
        entry,
        "192.168.1.131",
        device,
        series_id="gpu_nvidia",
        key="GPU-bbb",
        field="utilization",
        name="GPU 1 usage",
        unit="%",
        device_class=None,
        state_class="measurement",
        icon=None,
        instance=node["series"]["gpu_nvidia"][1],
        declared=19,
    )
    assert usage0.name == "GPU 0 usage"
    assert usage1.name == "GPU 1 usage"
    assert usage0.native_value == 9
    assert usage1.native_value == 19
    assert usage0.unique_id != usage1.unique_id
    assert usage0.unique_id.endswith("GPU-aaa")
    assert usage0.extra_state_attributes["label"] == "NVIDIA GeForce RTX 4090"
    assert usage0.extra_state_attributes["uuid"] == "GPU-aaa"


async def test_lifecycle_prunes_missing_series_on_reachable_node(hass) -> None:
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    entry = make_entry()
    entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "entry1_192.168.1.131")},
        name="GPU Primary 131",
    )
    stale = ent_reg.async_get_or_create(
        domain="sensor",
        platform="leassh",
        unique_id="entry1_192.168.1.131_gpu_nvidia_temperature_GPU-gone",
        config_entry=entry,
        device_id=device.id,
    )
    fresh = ent_reg.async_get_or_create(
        domain="sensor",
        platform="leassh",
        unique_id="entry1_192.168.1.131_cpu_usage",
        config_entry=entry,
        device_id=device.id,
    )

    coordinator = LeasshCoordinator(hass, SimpleNamespace(), entry)
    node = dict(
        NODE,
        series={"gpu_nvidia": [{"key": "GPU-live", "values": {"temperature": 46}}]},
    )
    coordinator._reconcile({"nodes": [node]})
    assert ent_reg.async_get(stale.entity_id) is None
    assert ent_reg.async_get(fresh.entity_id) is not None


async def test_lifecycle_keeps_everything_when_node_is_unreachable(hass) -> None:
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    entry = make_entry()
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "entry1_192.168.1.131")},
        name="GPU Primary 131",
    )
    ent_reg = er.async_get(hass)
    kept = ent_reg.async_get_or_create(
        domain="sensor",
        platform="leassh",
        unique_id="entry1_192.168.1.131_gpu_nvidia_temperature_GPU-gone",
        config_entry=entry,
        device_id=device.id,
    )
    coordinator = LeasshCoordinator(hass, SimpleNamespace(), entry)
    coordinator._reconcile({"nodes": [dict(NODE, reachable=False)]})
    assert ent_reg.async_get(kept.entity_id) is not None


async def test_lifecycle_removes_device_for_node_deleted_from_fleet(hass) -> None:
    from homeassistant.helpers import device_registry as dr

    entry = make_entry()
    entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "entry1_192.168.1.131")},
        name="GPU Primary 131",
    )
    hub = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "entry1")},
        name="Leassh Server",
    )
    coordinator = LeasshCoordinator(hass, SimpleNamespace(), entry)
    coordinator._reconcile({"nodes": []})
    assert dev_reg.async_get(device.id) is None
    assert dev_reg.async_get(hub.id) is not None


async def test_lifecycle_expects_fixed_and_generic_entities(hass) -> None:
    entry = make_entry()
    coordinator = LeasshCoordinator(hass, SimpleNamespace(), entry)
    node = dict(
        NODE,
        metrics={"cpu_temp_c": {"value": 54.0}},
        series={"gpu_nvidia": [{"key": "GPU-abc", "values": {"temperature": 46}}]},
    )
    expected = coordinator._expected_unique_ids(entry, "192.168.1.131", node)
    for key in FIXED_ENTITY_KEYS:
        assert entity_unique_id(entry, "192.168.1.131", key) in expected
    for key in FAMILY_SENSOR_KEYS:
        assert entity_unique_id(entry, "192.168.1.131", key) in expected
    assert "entry1_192.168.1.131_cpu_temp_c" in expected
    assert "entry1_192.168.1.131_gpu_nvidia_temperature_GPU-abc" in expected
