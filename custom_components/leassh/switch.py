"""Pause/resume switches for recipe automations."""

from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import LeasshRuntime
from .api import LeasshApiError
from .const import DOMAIN
from .coordinator import LeasshCoordinator
from .entity import hub_device, recipe_display_name, recipe_slug

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: LeasshRuntime = entry.runtime_data
    coordinator = runtime.coordinator
    seen: set[str] = set()
    slugs: dict[str, int] = {}

    def add_switches() -> None:
        new: list[SwitchEntity] = []
        for record in coordinator.recipe_instances:
            instance = record.get("instance") or {}
            instance_id = instance.get("id")
            if not instance_id or instance_id in seen:
                continue
            seen.add(instance_id)
            base = recipe_slug(str(instance.get("recipe_id") or instance_id))
            count = slugs.get(base, 0) + 1
            slugs[base] = count
            slug = base if count == 1 else f"{base}_{count}"
            new.append(
                RecipeEnabledSwitch(
                    runtime,
                    entry,
                    record,
                    slug,
                )
            )
        if new:
            async_add_entities(new)

    add_switches()
    entry.async_on_unload(coordinator.async_add_listener(add_switches))


class RecipeEnabledSwitch(CoordinatorEntity[LeasshCoordinator], SwitchEntity):
    """One switch per automation; turning it off pauses the instance."""

    _attr_has_entity_name = False
    _attr_icon = "mdi:book-cog-outline"

    def __init__(
        self,
        runtime: LeasshRuntime,
        entry: ConfigEntry,
        record: dict,
        slug: str,
    ) -> None:
        super().__init__(runtime.coordinator)
        self._runtime = runtime
        self._entry = entry
        self._instance_id = str(record["instance"]["id"])
        self._attr_unique_id = (
            f"{entry.entry_id}_recipe_{self._instance_id}_enabled"
        )
        self._attr_name = f"{recipe_display_name(record)} enabled"
        self._attr_device_info = hub_device(entry, runtime.client.base_url)
        self.entity_id = f"switch.leassh_recipe_{slug}_enabled"

    def _record(self) -> dict:
        for record in self.coordinator.recipe_instances:
            if (record.get("instance") or {}).get("id") == self._instance_id:
                return record
        return {}

    @property
    def name(self) -> str:
        return f"{recipe_display_name(self._record())} enabled"

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and bool(self._record())

    @property
    def is_on(self) -> bool:
        instance = self._record().get("instance") or {}
        return bool(instance.get("enabled"))

    async def _set_enabled(self, enabled: bool) -> None:
        try:
            await self._runtime.client.set_recipe_enabled(
                self._instance_id, enabled
            )
        except LeasshApiError as err:
            _LOGGER.error(
                "Leassh automation %s enable=%s failed: %s",
                self._instance_id,
                enabled,
                err,
            )
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="automation_toggle_failed",
                translation_placeholders={
                    "automation": recipe_display_name(self._record()),
                    "error": str(err),
                },
            ) from err
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs) -> None:
        await self._set_enabled(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._set_enabled(False)
