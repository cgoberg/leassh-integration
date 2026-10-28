"""Per-node sensors built from explicit metadata.

Every entity declares `has_entity_name`, so HA renders "<device> <name>"
(e.g. "GPU Primary 131 CPU usage") and translation keys resolve. Identity is
`entry + host + metric`, never the display name.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfInformation,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import LeasshRuntime
from .const import DOMAIN
from .coordinator import LeasshCoordinator
from .entity import entity_unique_id, find_node, node_device, node_host


@dataclass(frozen=True)
class NodeSensorDescription:
    key: str
    translation_key: str | None = None
    name: str | None = None
    unit: str | None = None
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None
    icon: str | None = None
    options: tuple[str, ...] | None = None


NODE_SENSORS: tuple[NodeSensorDescription, ...] = (
    NodeSensorDescription(
        "activity",
        translation_key="activity",
        device_class=SensorDeviceClass.ENUM,
        icon="mdi:account-clock",
        options=("active", "away", "idle", "offline"),
    ),
    NodeSensorDescription(
        "cpu_usage",
        translation_key="cpu_usage",
        unit=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:cpu-64-bit",
    ),
    NodeSensorDescription(
        "ram_percent",
        translation_key="ram_percent",
        unit=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:memory",
    ),
    NodeSensorDescription(
        "ram_used_mb",
        translation_key="ram_used",
        unit=UnitOfInformation.MEGABYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    NodeSensorDescription(
        "ram_total_mb",
        translation_key="ram_total",
        unit=UnitOfInformation.MEGABYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    NodeSensorDescription(
        "disk_free_gb",
        translation_key="disk_free",
        unit=UnitOfInformation.GIGABYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:harddisk",
    ),
    NodeSensorDescription(
        "gpu_usage",
        translation_key="gpu_usage",
        unit=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:expansion-card",
    ),
    NodeSensorDescription(
        "gpu_temperature",
        translation_key="gpu_temperature",
        unit=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    NodeSensorDescription(
        "cpu_temperature",
        translation_key="cpu_temperature",
        unit=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    NodeSensorDescription(
        "vram_used_mb",
        translation_key="vram_used",
        unit=UnitOfInformation.MEGABYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    NodeSensorDescription(
        "vram_total_mb",
        translation_key="vram_total",
        unit=UnitOfInformation.MEGABYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    NodeSensorDescription(
        "idle_seconds",
        translation_key="idle",
        unit=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:timer-outline",
    ),
    NodeSensorDescription(
        "presence_idle_seconds",
        translation_key="presence_idle",
        unit=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:human-male",
    ),
    NodeSensorDescription(
        "load",
        translation_key="load",
        device_class=SensorDeviceClass.ENUM,
        icon="mdi:speedometer",
        options=("none", "low", "medium", "high", "unknown"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: LeasshRuntime = hass.data[DOMAIN][entry.entry_id]
    coordinator = runtime.coordinator

    seen: set[str] = set()

    def add_node_sensors() -> None:
        new: list[SensorEntity] = []
        for node in coordinator.data.get("nodes", []):
            host = node_host(node)
            if not host:
                continue
            device = node_device(hass, entry, node)
            if device is None:
                continue
            for description in NODE_SENSORS:
                identity = (host, description.key, None)
                if identity in seen:
                    continue
                seen.add(identity)
                new.append(NodeSensor(coordinator, entry, host, device, description))
            # Generic scalar metrics and series arrive in M2. They are built
            # from the API's own metadata so a pack file is the whole change.
            new.extend(_generic_entities(coordinator, entry, host, device, node, seen))
        if new:
            async_add_entities(new)

    add_node_sensors()
    entry.async_on_unload(coordinator.async_add_listener(add_node_sensors))


def _generic_entities(
    coordinator: LeasshCoordinator,
    entry: ConfigEntry,
    host: str,
    device: DeviceInfo,
    node: dict,
    seen: set,
) -> list[SensorEntity]:
    """Scalars from `metrics` and instances from `series`.

    Metadata (unit/device class/name/icon/provenance) comes from the server;
    this function never hardcodes a pack's metrics.
    """
    entities: list[SensorEntity] = []

    for metric_id, metric in (node.get("metrics") or {}).items():
        if not isinstance(metric, dict):
            continue
        identity = (host, metric_id, None)
        if identity in seen:
            continue
        seen.add(identity)
        entities.append(
            GenericMetricSensor(
                coordinator,
                entry,
                host,
                device,
                metric_id=metric_id,
                name=str(metric.get("name") or metric_id.replace("_", " ").title()),
                unit=metric.get("unit"),
                device_class=metric.get("device_class"),
                state_class=metric.get("state_class"),
                icon=metric.get("icon"),
                source=metric,
            )
        )

    for series_id, instances in (node.get("series") or {}).items():
        if not isinstance(instances, list):
            continue
        # Per-field metadata, falling back to the first instance's declaration.
        for instance in instances:
            if not isinstance(instance, dict):
                continue
            key = str(instance.get("key") or instance.get("index"))
            label = instance.get("label")
            for field, value in (instance.get("values") or {}).items():
                identity = (host, f"{series_id}_{key}_{field}", key)
                if identity in seen:
                    continue
                seen.add(identity)
                meta = (instance.get("meta") or {}).get(field, {})
                display = meta.get("name") or _series_display(label, field)
                entities.append(
                    SeriesMetricSensor(
                        coordinator,
                        entry,
                        host,
                        device,
                        series_id=series_id,
                        key=key,
                        field=field,
                        name=display,
                        unit=meta.get("unit"),
                        device_class=meta.get("device_class"),
                        state_class=meta.get("state_class") or "measurement",
                        icon=meta.get("icon"),
                        instance=instance,
                        declared=value,
                    )
                )
    return entities


def _series_display(label: Any, field: str) -> str:
    prefix = str(label) if label else "Series"
    return f"{prefix} {field.replace('_', ' ')}"


class NodeSensor(CoordinatorEntity[LeasshCoordinator], SensorEntity):
    """One fixed metric off a fleet node's state."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LeasshCoordinator,
        entry: ConfigEntry,
        host: str,
        device: DeviceInfo,
        description: NodeSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self._host = host
        self._description = description
        self._attr_unique_id = entity_unique_id(entry, host, description.key)
        self._attr_device_info = device
        if description.translation_key:
            self._attr_translation_key = description.translation_key
        if description.name:
            self._attr_name = description.name
        if description.unit:
            self._attr_native_unit_of_measurement = description.unit
        if description.device_class:
            self._attr_device_class = description.device_class
        if description.state_class:
            self._attr_state_class = description.state_class
        if description.icon:
            self._attr_icon = description.icon
        if description.options:
            self._attr_options = list(description.options)

    def _node_data(self) -> dict[str, Any] | None:
        return find_node(self.coordinator, self._host)

    @property
    def available(self) -> bool:
        return self._node_data() is not None

    @property
    def native_value(self) -> Any:
        data = self._node_data()
        if data is None:
            return None
        key = self._description.key
        if key == "activity":
            return str(data.get("activity", "offline")).lower()
        if key == "load":
            return str(data.get("load", "unknown")).lower()
        if key == "cpu_usage":
            return _rounded(data.get("cpu_percent"))
        if key == "ram_percent":
            used, total = data.get("ram_used_mb"), data.get("ram_total_mb")
            if used is not None and total:
                return round(used / total * 100.0, 1)
            return None
        if key == "gpu_usage":
            return _rounded(data.get("gpu_percent"))
        if key == "gpu_temperature":
            return _rounded(data.get("gpu_temp_c"))
        if key == "cpu_temperature":
            return _rounded(data.get("cpu_temp_c"))
        return data.get(key)


def _rounded(value: Any) -> float | None:
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


class GenericMetricSensor(CoordinatorEntity[LeasshCoordinator], SensorEntity):
    """A scalar metric defined entirely by server metadata."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LeasshCoordinator,
        entry: ConfigEntry,
        host: str,
        device: DeviceInfo,
        *,
        metric_id: str,
        name: str,
        unit: str | None,
        device_class: str | None,
        state_class: str | None,
        icon: str | None,
        source: dict,
    ) -> None:
        super().__init__(coordinator)
        self._host = host
        self._metric_id = metric_id
        self._source = source
        self._attr_unique_id = entity_unique_id(entry, host, metric_id)
        self._attr_device_info = device
        self._attr_name = name
        if unit:
            self._attr_native_unit_of_measurement = str(unit)
        self._attr_device_class = _map_device_class(device_class)
        self._attr_state_class = _map_state_class(state_class)
        if icon:
            self._attr_icon = str(icon)

    def _node_data(self) -> dict[str, Any] | None:
        return find_node(self.coordinator, self._host)

    @property
    def native_value(self) -> Any:
        data = self._node_data()
        if data is None:
            return None
        return ((data.get("metrics") or {}).get(self._metric_id) or {}).get("value")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return _provenance(self._source)


class SeriesMetricSensor(CoordinatorEntity[LeasshCoordinator], SensorEntity):
    """One field of one instance in a series (e.g. GPU 0 temperature)."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LeasshCoordinator,
        entry: ConfigEntry,
        host: str,
        device: DeviceInfo,
        *,
        series_id: str,
        key: str,
        field: str,
        name: str,
        unit: str | None,
        device_class: str | None,
        state_class: str | None,
        icon: str | None,
        instance: dict,
        declared: Any,
    ) -> None:
        super().__init__(coordinator)
        self._host = host
        self._series_id = series_id
        self._key = key
        self._field = field
        self._instance = instance
        self._attr_unique_id = entity_unique_id(entry, host, f"{series_id}_{field}", key)
        self._attr_device_info = device
        self._attr_name = name
        if unit:
            self._attr_native_unit_of_measurement = str(unit)
        self._attr_device_class = _map_device_class(device_class)
        self._attr_state_class = _map_state_class(state_class)
        if icon:
            self._attr_icon = str(icon)

    def _instance_data(self) -> dict[str, Any] | None:
        data = find_node(self.coordinator, self._host)
        if data is None:
            return None
        for instance in (data.get("series") or {}).get(self._series_id) or []:
            if str(instance.get("key") or instance.get("index")) == self._key:
                return instance
        return None

    @property
    def native_value(self) -> Any:
        instance = self._instance_data()
        if instance is None:
            return None
        return (instance.get("values") or {}).get(self._field)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        instance = self._instance_data() or self._instance
        attrs = {
            "series": self._series_id,
            "series_key": self._key,
            "index": instance.get("index"),
            "label": instance.get("label"),
            "uuid": instance.get("uuid"),
        }
        attrs.update(_provenance(instance))
        return {k: v for k, v in attrs.items() if v is not None}


def _provenance(source: dict) -> dict[str, Any]:
    return {
        key: source.get(key)
        for key in ("pack", "pack_version", "observed_at", "age", "last_error")
        if source.get(key) is not None
    }


def _map_device_class(value: str | None) -> SensorDeviceClass | None:
    if not value:
        return None
    try:
        return SensorDeviceClass(str(value))
    except ValueError:
        return None


def _map_state_class(value: str | None) -> SensorStateClass | None:
    if not value:
        return None
    try:
        return SensorStateClass(str(value))
    except ValueError:
        return None
