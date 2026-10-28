"""The Leassh integration: one HA device per fleet node, events, notify.

Leassh server is the sole authority for fleet presence, enforcement and
orchestration — this integration is a thin client (connectors plan §0.1).
It never samples hosts itself and never runs arbitrary commands: the
server-side `ha` scope carries curated verbs only.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pathlib import Path

try:
    from homeassistant.components.http import StaticPathConfig
except ImportError:  # HA < 2024.7
    StaticPathConfig = None
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    LeasshAuthError,
    LeasshClient,
    LeasshConnectionError,
    normalize_server_url,
)
from .const import CONF_API_TOKEN, CONF_SERVER_URL, DOMAIN, EVENT_LEASSH
from .coordinator import LeasshCoordinator
from .ws_client import LeasshWsClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.BUTTON,
    Platform.NOTIFY,
]

# Brand assets served at /leassh_static (entity_picture on the hub and
# notify entities; no icon fonts for the brand mark).
_STATIC_DIR = Path(__file__).parent / "static"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})
    if _STATIC_DIR.is_dir() and hass.http is not None:
        try:
            # StaticPathConfig arrived ~2024.7; the floor (2024.6) uses
            # aiohttp's router directly.
            await hass.http.async_register_static_paths(
                [StaticPathConfig("/leassh_static", str(_STATIC_DIR), cache_headers=False)]
            )
        except (AttributeError, ImportError):
            hass.http.app.router.add_static("/leassh_static", str(_STATIC_DIR))
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    server_url = normalize_server_url(entry.data[CONF_SERVER_URL])
    client = LeasshClient(async_get_clientsession(hass), server_url, entry.data[CONF_API_TOKEN])

    # Fail the setup loudly on unreachable/auth errors; HA retries with
    # backoff, and the user sees the reason in the UI.
    try:
        await client.get_devices()
    except LeasshAuthError as err:
        # Auth failures start HA's reauth flow; only reachability is retried.
        raise ConfigEntryAuthFailed(f"Leassh token rejected: {err}") from err
    except LeasshConnectionError as err:
        raise ConfigEntryNotReady(f"Leassh unreachable: {err}") from err

    coordinator = LeasshCoordinator(hass, client, entry)
    await coordinator.async_config_entry_first_refresh()

    runtime = LeasshRuntime(hass, client=client, coordinator=coordinator)
    runtime.ws = LeasshWsClient(
        hass,
        client,
        on_event=runtime.handle_event,
        on_connection_state=runtime.set_connected,
        on_server_notify=runtime.deliver_server_notify,
    )
    runtime.ws.start()
    # Registered for the WS watchdog: outage push delivery (notify.py
    # replaces this once its entity exists).
    hass.data[DOMAIN].setdefault("outage_notifier", None)
    hass.data[DOMAIN][entry.entry_id] = runtime

    # async_forward_entry_setups (not async_forward_platforms, which only
    # exists from 2024.7) — our manifest floor is 2024.6.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runtime: LeasshRuntime | None = hass.data[DOMAIN].get(entry.entry_id)
    if runtime:
        await runtime.shutdown()
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


class LeasshRuntime:
    """Per-entry shared state: client, coordinator, WS session, notify."""

    def __init__(self, hass: HomeAssistant, client: LeasshClient, coordinator: LeasshCoordinator) -> None:
        self.hass = hass
        self.client = client
        self.coordinator = coordinator
        self.ws: LeasshWsClient | None = None
        self.connected = False
        # Entities (the connected binary sensor) refresh on WS state flips.
        self.ws_callbacks: list = []
        # Replaced by notify.py during platform setup (an async callable).
        self.send_notification = None
        # Presence/activity events are an accelerator over the 30 s poll, not
        # a source of truth. Coalesce bursts into one listener notification.
        self._event_pending = False

    def handle_event(self, kind: str, data: dict[str, Any]) -> None:
        """Apply a push event to coordinator state, debounced 0.5 s.

        `node.online`, `node.offline` and `node.activity` update the affected
        node in place. The 30 s poll reconciles afterwards; an event is never
        allowed to become the only source of truth.
        """
        name = data.get("node")
        if not name:
            return
        changed = False
        for node in (self.coordinator.data or {}).get("nodes", []):
            if node.get("name") != name:
                continue
            if kind == "node.online":
                node["reachable"] = True
                changed = True
            elif kind == "node.offline":
                node["reachable"] = False
                node["activity"] = "Offline"
                changed = True
            elif kind == "node.activity":
                activity = data.get("activity")
                if activity is not None:
                    node["activity"] = activity
                    changed = True
            break
        if changed:
            self._schedule_event_refresh()
        # Always publish on the HA event bus for automations.
        self.hass.bus.async_fire(EVENT_LEASSH, {"kind": kind, "data": data})

    def _schedule_event_refresh(self) -> None:
        if self._event_pending:
            return
        self._event_pending = True

        async def apply() -> None:
            await asyncio.sleep(0.5)
            self._event_pending = False
            if self.coordinator.data is not None:
                self.coordinator.async_set_updated_data(self.coordinator.data)

        self.hass.async_create_task(apply())

    def set_connected(self, connected: bool) -> None:
        self.connected = connected
        for callback in self.ws_callbacks:
            callback()

    def deliver_server_notify(self, data: dict[str, Any]) -> None:
        """A `notify` event arrived from the Leassh server — deliver it."""
        if self.send_notification is None:
            _LOGGER.warning(
                "Leassh notify event dropped (no notify entity ready): %s",
                data.get("title"),
            )
            return
        # Called from the WS receive loop; the deliverable is a coroutine —
        # schedule it, never drop it.
        self.hass.async_create_task(
            self.send_notification(
                data.get("title", "Leassh"),
                data.get("body", data.get("message", "")),
                data,
            )
        )

    async def shutdown(self) -> None:
        if self.ws:
            await self.ws.stop()
        # Cancels the pending poll timer; without it the next scheduled
        # refresh outlives the entry (and trips HA's lingering-timer check).
        await self.coordinator.async_shutdown()
