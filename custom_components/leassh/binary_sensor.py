"""Binary sensors: the connection watchdog and per-node presence."""

from __future__ import annotations

import time
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import LeasshRuntime
from .const import DOMAIN
from .coordinator import LeasshCoordinator
from .entity import (
    entity_unique_id,
    find_node,
    hub_device,
    node_device,
    node_host,
    node_provenance,
)


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

    seen: set[tuple[str, str]] = set()

    def add_node_sensors() -> None:
        new: list[BinarySensorEntity] = []
        for node in coordinator.data.get("nodes", []):
            host = node_host(node)
            if not host:
                continue
            device = node_device(hass, entry, node)
            if device is None:
                continue
            for key, device_class, field in (
                ("online", BinarySensorDeviceClass.CONNECTIVITY, "reachable"),
                # `signed_in` is console occupancy (a human owns the session),
                # not home/away: keep OCCUPANCY.
                ("signed_in", BinarySensorDeviceClass.OCCUPANCY, "human_present"),
                # `at_desk` is the accurate PRESENCE claim: the human is
                # actively using the machine now. The Activity sensor is the
                # source of truth; this is a short-hold projection of it.
                ("at_desk", BinarySensorDeviceClass.PRESENCE, "activity"),
            ):
                if (host, key) in seen:
                    continue
                seen.add((host, key))
                new.append(
                    NodeBinarySensor(
                        coordinator, entry, host, device, device_class, key, field
                    )
                )
        if new:
            async_add_entities(new)

    add_node_sensors()
    entry.async_on_unload(coordinator.async_add_listener(add_node_sensors))
    async_add_entities(entities)


class LeasshConnectedSensor(BinarySensorEntity):
    """The WS heartbeat made visible: "Leassh Server Connected".

    Connection state is the heartbeat (plan §2): off means no frames from
    the server, which automations alert on. Deliberately NOT a coordinator
    entity — it must report the connection, not the last successful poll.
    has_entity_name under the "Leassh Server" hub device renders as
    "Leassh Server Connected" with entity id binary_sensor.leassh_server_connected.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    # Brand: the integration-served logo instead of a generic icon.
    _attr_entity_picture = "/leassh_static/logo.png"

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
    """Per-node binary sensor (Online / Signed in / At desk)."""

    _attr_has_entity_name = True
    AT_DESK_HOLD_SECS = 60

    def __init__(
        self,
        coordinator: LeasshCoordinator,
        entry: ConfigEntry,
        host: str,
        device: DeviceInfo,
        device_class: BinarySensorDeviceClass,
        key: str,
        field: str,
    ) -> None:
        super().__init__(coordinator)
        self._host = host
        self._key = key
        self._field = field
        self._at_desk_last: float | None = None
        self._attr_device_class = device_class
        self._attr_translation_key = key
        self._attr_unique_id = entity_unique_id(entry, host, key)
        self._attr_device_info = device

    def _node_data(self) -> dict[str, Any] | None:
        return find_node(self.coordinator, self._host)

    @property
    def available(self) -> bool:
        return self._node_data() is not None

    @property
    def is_on(self) -> bool | None:
        data = self._node_data()
        if data is None:
            return None
        if self._key == "at_desk":
            # Hold the ON state briefly so a single idle sample does not flap
            # the entity. Activity itself already applies the 5-minute band.
            now = time.monotonic()
            if str(data.get("activity", "")).lower() == "active":
                self._at_desk_last = now
            return (
                self._at_desk_last is not None
                and now - self._at_desk_last <= self.AT_DESK_HOLD_SECS
            )
        return bool(data.get(self._field))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = node_provenance(self._node_data())
        if self._key == "at_desk":
            attrs["activity"] = (self._node_data() or {}).get("activity")
            attrs["hold_seconds"] = self.AT_DESK_HOLD_SECS
        return attrs
