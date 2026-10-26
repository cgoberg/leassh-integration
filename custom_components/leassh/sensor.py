"""Per-node sensors: activity, cpu, ram, load state."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import LeasshRuntime
from .const import DOMAIN
from .coordinator import LeasshCoordinator
from .entity import node_device


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
            name = node.get("name")
            if not name or name in seen:
                continue
            seen.add(name)
            new.extend(
                [
                    NodeSensor(coordinator, entry, name, "activity", None, None, "mdi:account-clock"),
                    NodeSensor(coordinator, entry, name, "cpu_percent", "%", float, "mdi:cpu-64-bit"),
                    NodeSensor(coordinator, entry, name, "ram_percent", "%", float, "mdi:memory"),
                    NodeSensor(coordinator, entry, name, "load", None, None, "mdi:speedometer"),
                ]
            )
        if new:
            async_add_entities(new)

    add_node_sensors()
    entry.async_on_unload(coordinator.async_add_listener(add_node_sensors))


class NodeSensor(CoordinatorEntity[LeasshCoordinator], SensorEntity):
    """One metric off a fleet node's state."""

    def __init__(
        self,
        coordinator: LeasshCoordinator,
        entry: ConfigEntry,
        node_name: str,
        key: str,
        unit: str | None,
        value_type: type | None,
        icon: str | None,
    ) -> None:
        super().__init__(coordinator)
        self._node = node_name
        self._key = key
        self._value_type = value_type
        self._attr_unique_id = f"{entry.entry_id}_{node_name}_{key}"
        self._attr_device_info = node_device(entry, node_name, self._node_host(coordinator))
        self._attr_translation_key = key
        if unit:
            self._attr_native_unit_of_measurement = unit
            self._attr_state_class = SensorStateClass.MEASUREMENT
        if icon:
            self._attr_icon = icon

    @staticmethod
    def _node_host(coordinator: LeasshCoordinator) -> str | None:
        return None  # host arrives with first coordinator payload; device updated lazily

    def _node_data(self) -> dict[str, Any] | None:
        for node in self.coordinator.data.get("nodes", []):
            if node.get("name") == self._node:
                return node
        return None

    @property
    def available(self) -> bool:
        return self._node_data() is not None

    @property
    def native_value(self) -> Any:
        data = self._node_data()
        if data is None:
            return None
        value = data.get(self._key)
        if value is None:
            # ram_percent is not published by the server; derive it.
            if self._key == "ram_percent":
                used, total = data.get("ram_used_mb"), data.get("ram_total_mb")
                if used is not None and total:
                    return round(used / total * 100.0, 1)
            return None
        if self._value_type is float:
            try:
                return round(float(value), 1)
            except (TypeError, ValueError):
                return None
        return value
