"""Curated action catalog surfaced as per-node buttons.

Only server-defined verbs (GET /api/actions) ever become entities; the
integration cannot send arbitrary commands by construction — the ha scope
it holds would refuse them anyway.
"""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LeasshRuntime
from .api import LeasshApiError
from .const import DOMAIN
from .entity import entity_unique_id, node_device, node_host, title_case

_LOGGER = logging.getLogger(__name__)

_ICON_BY_VERB = {
    "reboot_node": "mdi:restart",
    "sign_out_session": "mdi:logout",
}

# Verbs with a translation key in strings.json. An unknown catalog verb still
# becomes a button, but carries a display name instead (never an empty one).
_TRANSLATED_VERBS = {"reboot_node", "sign_out_session"}


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
    seen: set[tuple[str, str]] = set()

    def add_buttons() -> None:
        new: list[ButtonEntity] = []
        for node in coordinator.data.get("nodes", []):
            host = node_host(node)
            if not host:
                continue
            device = node_device(hass, entry, node)
            if device is None:
                continue
            for verb in verbs:
                if (host, verb) in seen:
                    continue
                seen.add((host, verb))
                new.append(
                    ActionButton(
                        runtime,
                        entry,
                        device,
                        host,
                        verb,
                        catalog_desc(catalog, verb),
                    )
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
        entry: ConfigEntry,
        device: DeviceInfo,
        host: str,
        verb: str,
        description: str,
    ) -> None:
        self._runtime = runtime
        self._host = host
        self._verb = verb
        self._entry = entry
        self._attr_unique_id = entity_unique_id(entry, host, verb)
        self._attr_device_info = device
        self._attr_icon = _ICON_BY_VERB.get(verb, "mdi:flash")
        if verb in _TRANSLATED_VERBS:
            self._attr_translation_key = verb
        else:
            self._attr_name = title_case(verb) or verb
        self._attr_entity_registry_enabled_default = True

    async def async_press(self) -> None:
        node = self._node_name()
        if node is None:
            raise RuntimeError("node is no longer present in the fleet")
        try:
            result = await self._runtime.client.action(node, self._verb)
        except LeasshApiError as err:
            _LOGGER.error("Leassh action %s/%s failed: %s", node, self._verb, err)
            raise
        _LOGGER.info("Leassh action %s/%s ok: %s", node, self._verb, result)

    def _node_name(self) -> str | None:
        for node in (self._runtime.coordinator.data or {}).get("nodes", []):
            if node_host(node) == self._host:
                return node.get("name")
        return None
