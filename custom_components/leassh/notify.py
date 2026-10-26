"""notify.leassh — companion-app delivery with iOS critical sound for urgent.

The Leassh server routes notifications; this entity is a delivery channel
inside HA. When the server emits a `notify` event (e.g. forge-alert via
POST /api/notify), the integration hands it here — the push reaches the
phone from HA even though the request originated at the Leassh server.
`level=urgent` maps to the iOS critical-sound payload.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.notify import NotifyEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LeasshRuntime
from .const import CONF_NOTIFY_TARGETS, DOMAIN

_LOGGER = logging.getLogger(__name__)

_URGENCY = ("urgent", "critical")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: LeasshRuntime = hass.data[DOMAIN][entry.entry_id]
    entity = LeasshNotifyEntity(hass, entry, runtime)
    async_add_entities([entity])
    # Wire the two delivery paths to this entity: WS notify events and the
    # WS watchdog's outage push.
    runtime.send_notification = entity.async_deliver
    hass.data[DOMAIN]["outage_notifier"] = entity.async_deliver


class LeasshNotifyEntity(NotifyEntity):
    """The `notify.leassh` service."""

    _attr_has_entity_name = True
    _attr_translation_key = "notify"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, runtime: LeasshRuntime) -> None:
        self._hass = hass
        self._entry = entry
        self._runtime = runtime
        self._attr_unique_id = f"{entry.entry_id}_notify"

    async def async_send_message(self, message: str, **kwargs: Any) -> None:
        await self.async_deliver(
            kwargs.get(ATTR_TITLE, "Leassh"), message, kwargs.get("data") or {}
        )

    async def async_deliver(
        self, title: str, message: str, data: dict[str, Any]
    ) -> None:
        """Deliver to configured companion targets (default: all mobile_app)."""
        level = str(data.get("level", "info")).lower()
        ack_url = data.get("ack_url") or data.get("url")

        push: dict[str, Any] = {"interruption-level": "time-sensitive"}
        if level in _URGENCY:
            # iOS critical sound: plays through silent switch / DND when the
            # app has the critical-alert entitlement granted by the user.
            push["sound"] = {"name": "default", "critical": 1, "volume": 1.0}
        # Caller-provided push data wins (escape hatch).
        push.update((data.get("push") or {}))

        service_data: dict[str, Any] = {
            "title": title,
            "message": message,
            "data": {"push": push},
        }
        if ack_url:
            service_data["data"]["url"] = ack_url

        targets = self._targets()
        if not targets:
            _LOGGER.warning(
                "notify.leassh has no companion targets; configure them in "
                "the integration options. Message: %s",
                title,
            )
            return

        for target in targets:
            await self._hass.services.async_call(
                "notify",
                target,
                service_data,
                blocking=False,
            )
        _LOGGER.info("notify.leassh delivered '%s' (level=%s) to %d target(s)", title, level, len(targets))

    def _targets(self) -> list[str]:
        configured = self._entry.options.get(CONF_NOTIFY_TARGETS) or []
        if configured:
            return list(configured)
        # Default: every mobile_app notify service present in this HA.
        notify_services = self._hass.services.async_services().get("notify", {})
        return sorted(k for k in notify_services if k.startswith("mobile_app_"))
