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
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LeasshRuntime
from .api import LeasshApiError
from .const import DOMAIN
from .entity import (
    entity_unique_id,
    hub_device,
    node_device,
    node_host,
    recipe_display_name,
    recipe_slug,
    title_case,
)

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

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
    runtime: LeasshRuntime = entry.runtime_data

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

    recipe_seen: set[str] = set()
    recipe_slugs: dict[str, int] = {}

    def add_recipe_buttons() -> None:
        new: list[ButtonEntity] = []
        for record in coordinator.recipe_instances:
            instance = record.get("instance") or {}
            instance_id = instance.get("id")
            if not instance_id or instance_id in recipe_seen:
                continue
            recipe_seen.add(instance_id)
            base = recipe_slug(str(instance.get("recipe_id") or instance_id))
            count = recipe_slugs.get(base, 0) + 1
            recipe_slugs[base] = count
            slug = base if count == 1 else f"{base}_{count}"
            new.append(RecipePreviewButton(runtime, entry, record, slug))
        if new:
            async_add_entities(new)

    add_recipe_buttons()
    entry.async_on_unload(
        coordinator.async_add_listener(add_recipe_buttons)
    )


def _preview_text(report: dict) -> str:
    lines: list[str] = []
    if report.get("summary"):
        lines.append(str(report["summary"]))
    if not report.get("eligible"):
        reasons = report.get("reasons") or []
        lines.append(
            "Would not run: " + "; ".join(str(reason) for reason in reasons)
        )
    for step in report.get("steps") or []:
        if not isinstance(step, dict):
            continue
        preview = step.get("preview") or {}
        effect = preview.get("expected_effect") or step.get("action") or "Action"
        after = step.get("after_seconds")
        lines.append(f"- {effect}" + (f" after {after}s" if after else ""))
    return "\n".join(line for line in lines if line) or "No actions would run."


class RecipePreviewButton(ButtonEntity):
    """Press = preview a recipe instance without executing it."""

    _attr_has_entity_name = False
    _attr_icon = "mdi:eye-outline"

    def __init__(
        self,
        runtime: LeasshRuntime,
        entry: ConfigEntry,
        record: dict,
        slug: str,
    ) -> None:
        self._runtime = runtime
        self._entry = entry
        self._instance_id = str(record["instance"]["id"])
        self._attr_unique_id = (
            f"{entry.entry_id}_recipe_{self._instance_id}_preview"
        )
        self._attr_name = f"{recipe_display_name(record)} preview"
        self._attr_device_info = hub_device(entry, runtime.client.base_url)
        self.entity_id = f"button.leassh_recipe_{slug}_preview"

    def _record(self) -> dict:
        for record in self._runtime.coordinator.recipe_instances:
            if (record.get("instance") or {}).get("id") == self._instance_id:
                return record
        return {}

    @property
    def name(self) -> str:
        return f"{recipe_display_name(self._record())} preview"

    @property
    def available(self) -> bool:
        return self._runtime.coordinator.last_update_success and bool(
            self._record()
        )

    async def async_press(self) -> None:
        record = self._record()
        instance = record.get("instance") or {}
        try:
            report = await self._runtime.client.dry_run_recipe(
                self._instance_id, instance.get("target")
            )
        except LeasshApiError as err:
            _LOGGER.error(
                "Leassh automation preview %s failed: %s",
                self._instance_id,
                err,
            )
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="automation_preview_failed",
                translation_placeholders={
                    "automation": recipe_display_name(record),
                    "error": str(err),
                },
            ) from err
        await self.hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": f"{recipe_display_name(record)} preview",
                "message": _preview_text(report),
                "notification_id": f"leassh_recipe_preview_{self._instance_id}",
            },
            blocking=False,
        )
        self.hass.bus.async_fire(
            "leassh_recipe_preview",
            {"instance": self._instance_id, "report": report},
        )


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
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="action_failed",
                translation_placeholders={
                    "action": self._verb,
                    "node": node,
                    "error": str(err),
                },
            ) from err
        _LOGGER.info("Leassh action %s/%s ok: %s", node, self._verb, result)

    def _node_name(self) -> str | None:
        for node in (self._runtime.coordinator.data or {}).get("nodes", []):
            if node_host(node) == self._host:
                return node.get("name")
        return None
