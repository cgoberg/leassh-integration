"""Curated action catalog surfaced as per-node buttons.

Only server-defined verbs (GET /api/actions) ever become entities; the
integration cannot send arbitrary commands by construction — the ha scope
it holds would refuse them anyway.
"""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LeasshRuntime
from .api import LeasshApiError
from .const import DOMAIN
from .entity import node_device, title_case

_LOGGER = logging.getLogger(__name__)

_ICON_BY_VERB = {
    "reboot_node": "mdi:restart",
    "sign_out_session": "mdi:logout",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: LeasshRuntime = hass.data[DOMAIN][entry.entry_id]

    # The catalog is a server-side product decision; fetch once at setup.
    try:
        catalog = await runtime.client.get_actions()
    except LeasshApiError as err:
        _LOGGER.warning("Could not fetch the Leassh action catalog: %s", err)
        catalog = {"verbs": []}
    verbs = [v["verb"] for v in catalog.get("verbs", []) if v.get("verb")]

    coordinator = runtime.coordinator
    seen: set[str] = set()

    def add_buttons() -> None:
        new: list[ButtonEntity] = []
        for node in coordinator.data.get("nodes", []):
            name = node.get("name")
            if not name:
                continue
            for verb in verbs:
                key = f"{name}:{verb}"
                if key in seen:
                    continue
                seen.add(key)
                new.append(
                    ActionButton(runtime, node_device(hass, entry, name), name, verb, catalog_desc(catalog, verb))
                )
        if new:
            async_add_entities(new)

    add_buttons()
    entry.async_on_unload(coordinator.async_add_listener(add_buttons))


def catalog_desc(catalog: dict, verb: str) -> str:
    for v in catalog.get("verbs", []):
        if v.get("verb") == verb:
            return v.get("description", verb)
    return verb


class ActionButton(ButtonEntity):
    """Press = run the curated verb on the node via POST /api/devices/{id}/action."""

    _attr_has_entity_name = True
    _attr_entity_category = None  # safety actions belong on the main surface

    def __init__(
        self,
        runtime: LeasshRuntime,
        device: DeviceInfo,
        node_name: str,
        verb: str,
        description: str,
    ) -> None:
        self._runtime = runtime
        self._node = node_name
        self._verb = verb
        self._entry = runtime.coordinator.config_entry
        self._attr_unique_id = f"{self._entry.entry_id}_{node_name}_{verb}"
        self._attr_device_info = device
        self._attr_translation_key = "action_button"
        self._attr_translation_placeholders = {"action": title_case(verb)}
        self._attr_icon = _ICON_BY_VERB.get(verb, "mdi:flash")
        self._attr_name = title_case(verb)  # "Sign out session", shown under the node device

    async def async_press(self) -> None:
        try:
            result = await self._runtime.client.action(self._node, self._verb)
        except LeasshApiError as err:
            _LOGGER.error("Leassh action %s/%s failed: %s", self._node, self._verb, err)
            raise
        _LOGGER.info("Leassh action %s/%s ok: %s", self._node, self._verb, result)
