"""The Leassh integration: one HA device per fleet node, events, notify.

Leassh server is the sole authority for fleet presence, enforcement and
orchestration — this integration is a thin client (connectors plan §0.1).
It never samples hosts itself and never runs arbitrary commands: the
server-side `ha` scope carries curated verbs only.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
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


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    server_url = normalize_server_url(entry.data[CONF_SERVER_URL])
    client = LeasshClient(async_get_clientsession(hass), server_url, entry.data[CONF_API_TOKEN])

    # Fail the setup loudly on unreachable/auth errors; HA retries with
    # backoff, and the user sees the reason in the UI.
    try:
        await client.get_devices()
    except LeasshAuthError as err:
        raise ConfigEntryNotReady(f"Leassh token rejected: {err}") from err
    except LeasshConnectionError as err:
        raise ConfigEntryNotReady(f"Leassh unreachable: {err}") from err

    coordinator = LeasshCoordinator(hass, client)
    await coordinator.async_config_entry_first_refresh()

    runtime = LeasshRuntime(client=client, coordinator=coordinator)
    runtime.ws = LeasshWsClient(
        hass,
        client,
        on_event=lambda kind, data: hass.bus.async_fire(
            EVENT_LEASSH, {"kind": kind, "data": data}
        ),
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

    def __init__(self, client: LeasshClient, coordinator: LeasshCoordinator) -> None:
        self.client = client
        self.coordinator = coordinator
        self.ws: LeasshWsClient | None = None
        self.connected = False
        # Entities (the connected binary sensor) refresh on WS state flips.
        self.ws_callbacks: list = []
        # Replaced by notify.py during platform setup.
        self.send_notification = None

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
        self.send_notification(
            data.get("title", "Leassh"),
            data.get("body", data.get("message", "")),
            data,
        )

    async def shutdown(self) -> None:
        if self.ws:
            await self.ws.stop()
        # Cancels the pending poll timer; without it the next scheduled
        # refresh outlives the entry (and trips HA's lingering-timer check).
        await self.coordinator.async_shutdown()
