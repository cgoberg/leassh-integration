"""WS session: commands, events, presence — and the connection watchdog.

The WS connection state IS the heartbeat (plan §2): while frames arrive,
`binary_sensor.leassh_connected` is on. The server pings every
HEARTBEAT_INTERVAL_SECS; if nothing arrives for STALE_AFTER_SECS the
session is considered down. A outage lasting ALERT_AFTER_SECS triggers a
companion-app alert (delivered by HA itself, so it fires precisely when
the Leassh server cannot deliver anything).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

import aiohttp

from .api import LeasshAuthError, LeasshClient, LeasshConnectionError
from .const import (
    ALERT_AFTER_SECS,
    DOMAIN,
    KIND_NOTIFY,
    STALE_AFTER_SECS,
)

_LOGGER = logging.getLogger(__name__)

_BACKOFF_START = 1.0
_BACKOFF_MAX = 60.0


class LeasshWsClient:
    """Maintains one authenticated WS session with reconnect + watchdog."""

    def __init__(
        self,
        hass,  # HomeAssistant; kept untyped to avoid import cycle weight
        client: LeasshClient,
        on_event: Callable[[str, dict[str, Any]], None],
        on_connection_state: Callable[[bool], None],
        on_server_notify: Callable[[dict[str, Any]], None],
    ) -> None:
        self._hass = hass
        self._client = client
        self._on_event = on_event
        self._on_connection_state = on_connection_state
        self._on_server_notify = on_server_notify
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.connected = False
        self.last_frame_monotonic: float | None = None
        self._outage_alerted = False

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self._stop = asyncio.Event()
        self._task = self._hass.async_create_background_task(
            self._run(), "leassh_ws_session"
        )

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - shutdown path
                pass
            self._task = None
        if self.connected:
            self._set_connected(False)

    # -- session loop --------------------------------------------------------

    async def _run(self) -> None:
        backoff = _BACKOFF_START
        while not self._stop.is_set():
            try:
                await self._session_once()
                backoff = _BACKOFF_START  # clean exit -> reset
            except LeasshAuthError as err:
                _LOGGER.error("Leassh rejected the token: %s", err)
                self._set_connected(False)
                return  # permanent: user must fix the token
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - reconnect on anything
                _LOGGER.warning("Leassh WS session ended: %s", err)
                self._set_connected(False)
            if self._stop.is_set():
                break
            # Sleep in slices so the outage alert can fire while we are
            # disconnected — the very window it exists for.
            deadline = time.monotonic() + backoff
            while not self._stop.is_set() and time.monotonic() < deadline:
                await asyncio.sleep(min(5.0, max(0.5, deadline - time.monotonic())))
                self._check_outage_alert()
            backoff = min(backoff * 2, _BACKOFF_MAX)

    async def _session_once(self) -> None:
        ws = await self._client.ws_connect()
        self.last_frame_monotonic = time.monotonic()
        self._outage_alerted = False
        self._set_connected(True)
        _LOGGER.info("Leassh WS session up (%s)", self._client.base_url)
        try:
            while not self._stop.is_set():
                # Staleness check: server pings on a fixed cadence; silence
                # beyond STALE_AFTER_SECS means the link is dead even if the
                # socket has not errored yet.
                since = time.monotonic() - (self.last_frame_monotonic or 0.0)
                if since > STALE_AFTER_SECS:
                    raise LeasshConnectionError(
                        f"no frames for {since:.0f}s (server heartbeat missed)"
                    )
                self._check_outage_alert()
                try:
                    msg = await asyncio.wait_for(
                        ws.receive(), timeout=min(STALE_AFTER_SECS, 15.0)
                    )
                except TimeoutError:
                    continue
                self.last_frame_monotonic = time.monotonic()

                if msg.type == aiohttp.WSMsgType.TEXT:
                    self._handle_frame(msg.json())
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED):
                    raise LeasshConnectionError("server closed the session")
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    raise LeasshConnectionError(f"ws error: {ws.exception()}")
                # PING/PONG handled by aiohttp automatically.
        finally:
            if not ws.closed:
                await ws.close()
            self._set_connected(False)

    def _handle_frame(self, frame: dict[str, Any]) -> None:
        method = frame.get("method")
        if method == "leassh_event":
            params = frame.get("params", {})
            kind = params.get("kind", "unknown")
            data = params.get("data", {})
            self._on_event(kind, data)
            if kind == KIND_NOTIFY:
                self._on_server_notify(data)
        elif frame.get("id") is not None:
            # Response to a client-initiated RPC; nothing to do here yet —
            # calls are awaited inside action(), this is the async mirror.
            _LOGGER.debug("leassh rpc response: %s", frame)

    # -- watchdog --------------------------------------------------------

    def _check_outage_alert(self) -> None:
        """Alert once per outage when stale beyond ALERT_AFTER_SECS."""
        if self.connected or self.last_frame_monotonic is None:
            return
        stale_for = time.monotonic() - self.last_frame_monotonic
        if stale_for > ALERT_AFTER_SECS and not self._outage_alerted:
            self._outage_alerted = True
            self._fire_outage_alert(stale_for)

    def _fire_outage_alert(self, stale_for: float) -> None:
        self._hass.async_create_task(
            self._hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "Leassh server unreachable",
                    "message": (
                        "No Leassh heartbeats for "
                        f"{stale_for / 60:.0f} minutes ({self._client.base_url}). "
                        "Family safety monitoring is interrupted."
                    ),
                    "notification_id": "leassh_disconnected",
                },
            )
        )
        # Companion-app push is delivered by the notify entity if targets
        # are configured (see notify.py); the persistent notification above
        # guarantees visibility even without targets.
        notify = self._hass.data.get(DOMAIN, {}).get("outage_notifier")
        if notify:
            self._hass.async_create_task(
                notify(
                    "Leassh server unreachable",
                    f"No heartbeats for {stale_for / 60:.0f} minutes.",
                    {"level": "urgent"},
                )
            )

    def _set_connected(self, connected: bool) -> None:
        if connected != self.connected:
            self.connected = connected
            _LOGGER.info("Leassh connection state: %s", "up" if connected else "down")
            self._on_connection_state(connected)
        if connected:
            self._outage_alerted = False

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Fire an RPC over the live session (used for quick commands).

        The REST path is authoritative for actions; this exists for
        future session-scoped methods. Returns the `result` or raises.
        """
        ws = await self._client.ws_connect()
        try:
            ws.send_json(
                {"jsonrpc": "2.0", "id": 2, "method": method, "params": params or {}}
            )
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                frame = msg.json()
                if frame.get("id") == 2:
                    if "error" in frame:
                        raise LeasshConnectionError(str(frame["error"]))
                    return frame.get("result")
        finally:
            if not ws.closed:
                await ws.close()
        raise LeasshConnectionError("no response")
