"""Binary sensors: the connection watchdog and per-node presence."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import LeasshRuntime
from .const import DOMAIN
from .coordinator import LeasshCoordinator
from .entity import hub_device, node_device


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: LeasshRuntime = hass.data[DOMAIN][entry.entry_id]
    coordinator = runtime.coordinator

    entities: list[BinarySensorEntity] = [
        LeasshConnectedSensor(entry, runtime),
    ]

    seen: set[str] = set()

    def add_node_sensors() -> None:
        new: list[BinarySensorEntity] = []
        for node in coordinator.data.get("nodes", []):
            name = node.get("name")
            if not name or name in seen:
                continue
            seen.add(name)
            new.extend(
                [
                    NodeBinarySensor(
                        coordinator, entry, name, BinarySensorDeviceClass.CONNECTIVITY,
                        "online", "reachable",
                    ),
                    NodeBinarySensor(
                        coordinator, entry, name, BinarySensorDeviceClass.OCCUPANCY,
                        "human_present", "human_present",
                    ),
                ]
            )
        if new:
            async_add_entities(new)

    add_node_sensors()
    entry.async_on_unload(coordinator.async_add_listener(add_node_sensors))
    async_add_entities(entities)


class LeasshConnectedSensor(BinarySensorEntity):
    """`binary_sensor.leassh_connected` — the WS heartbeat made visible.

    Connection state is the heartbeat (plan §2): off means no frames from
    the server, which automations alert on. Deliberately NOT a coordinator
    entity — it must report the connection, not the last successful poll.
    The entity_id is part of the documented contract, so it is pinned via
    an explicit name rather than derived from device+translation.
    """

    _attr_has_entity_name = False
    _attr_name = "Leassh connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, entry: ConfigEntry, runtime: LeasshRuntime) -> None:
        self._entry = entry
        self._runtime = runtime
        self._attr_unique_id = f"{entry.entry_id}_connected"
        self._attr_device_info = hub_device(entry, runtime.client.base_url)
        runtime.ws_callbacks.append(self._schedule_update)
        self._attr_is_on = runtime.connected

    def _schedule_update(self) -> None:
        self._attr_is_on = self._runtime.connected
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool | None:
        return self._runtime.connected


class NodeBinarySensor(CoordinatorEntity[LeasshCoordinator], BinarySensorEntity):
    """Per-node binary sensor (online / human_present)."""

    def __init__(
        self,
        coordinator: LeasshCoordinator,
        entry: ConfigEntry,
        node_name: str,
        device_class: BinarySensorDeviceClass,
        key: str,
        field: str,
    ) -> None:
        super().__init__(coordinator)
        self._node = node_name
        self._field = field
        self._attr_device_class = device_class
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{node_name}_{key}"
        self._attr_device_info = node_device(entry, node_name)

    def _node_data(self) -> dict[str, Any] | None:
        for node in self.coordinator.data.get("nodes", []):
            if node.get("name") == self._node:
                return node
        return None

    @property
    def available(self) -> bool:
        return self._node_data() is not None

    @property
    def is_on(self) -> bool | None:
        data = self._node_data()
        return bool(data.get(self._field)) if data else None
