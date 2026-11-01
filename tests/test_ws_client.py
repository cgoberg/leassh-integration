"""WS session: frame handling, call RPC, watchdog and auth-stop behavior."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import aiohttp
import pytest

from custom_components.leassh.api import LeasshAuthError, LeasshConnectionError
from custom_components.leassh.const import DOMAIN
from custom_components.leassh.ws_client import LeasshWsClient


class FakeWs:
    def __init__(self, frames=None, closed=False):
        self.frames = list(frames or [])
        self.closed = closed
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)

    async def receive(self):
        if not self.frames:
            raise TimeoutError
        return self.frames.pop(0)

    async def close(self):
        self.closed = True

    def exception(self):
        return None

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.frames:
            raise StopAsyncIteration
        return self.frames.pop(0)


class FakeClient:
    base_url = "http://127.0.0.1:8790"

    def __init__(self, ws=None, error=None):
        self.ws = ws or FakeWs()
        self.error = error
        self.calls = 0

    async def ws_connect(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.ws


class FakeHass:
    def __init__(self):
        self.data = {}
        self.created = []
        self.calls = []
        self.services = SimpleNamespace(async_call=self._service_call)

    def async_create_background_task(self, coro, name):
        task = SimpleNamespace(coro=coro, name=name, cancel=lambda: None)
        self.created.append(task)
        return task

    def async_create_task(self, coro):
        self.created.append(coro)
        return coro

    async def _service_call(self, domain, service, data, blocking=False):
        self.calls.append((domain, service, data))


def make_client(hass=None, ws=None, error=None):
    events = []
    states = []
    notifications = []
    client = LeasshWsClient(
        hass or FakeHass(),
        FakeClient(ws=ws, error=error),
        on_event=lambda kind, data: events.append((kind, data)),
        on_connection_state=states.append,
        on_server_notify=notifications.append,
    )
    return client, events, states, notifications


def test_frame_handling_routes_events_and_notify() -> None:
    client, events, _, notifications = make_client()
    client._handle_frame(
        {
            "method": "leassh_event",
            "params": {"kind": "notify", "data": {"title": "hello"}},
        }
    )
    assert events == [("notify", {"title": "hello"})]
    assert notifications == [{"title": "hello"}]
    client._handle_frame({"id": 1, "result": {"ok": True}})


async def test_session_once_handles_text_then_close() -> None:
    frame = SimpleNamespace(
        type=aiohttp.WSMsgType.TEXT,
        json=lambda: {"method": "leassh_event", "params": {"kind": "x", "data": {}}},
    )
    ws = FakeWs(frames=[frame, SimpleNamespace(type=aiohttp.WSMsgType.CLOSE)])
    client, events, states, _ = make_client(ws=ws)
    with pytest.raises(LeasshConnectionError):
        await client._session_once()
    assert events[0][0] == "x"
    assert states == [True, False]
    assert ws.closed is True


async def test_run_stops_permanently_on_auth_error() -> None:
    client, _, states, _ = make_client(error=LeasshAuthError("bad token"))
    client.connected = True

    async def fail():
        raise LeasshAuthError("bad token")

    client._session_once = fail  # type: ignore[method-assign]
    await client._run()
    assert states == [False]


async def test_call_returns_result_and_raises_on_error() -> None:
    ws = FakeWs(
        frames=[
            SimpleNamespace(
                type=aiohttp.WSMsgType.TEXT,
                json=lambda: {"id": 2, "result": {"ok": True}},
            )
        ]
    )
    client, _, _, _ = make_client(ws=ws)
    assert await client.call("ping", {"a": 1}) == {"ok": True}
    assert ws.sent[0]["method"] == "ping"

    ws = FakeWs(
        frames=[
            SimpleNamespace(
                type=aiohttp.WSMsgType.TEXT,
                json=lambda: {"id": 2, "error": {"message": "nope"}},
            )
        ]
    )
    client, _, _, _ = make_client(ws=ws)
    with pytest.raises(LeasshConnectionError):
        await client.call("ping")

    client, _, _, _ = make_client(ws=FakeWs())
    with pytest.raises(LeasshConnectionError):
        await client.call("ping")


def test_outage_alert_fires_once() -> None:
    hass = FakeHass()
    client, _, _, _ = make_client(hass=hass)
    client.last_frame_monotonic = time.monotonic() - 400.0
    client._check_outage_alert()
    assert len(hass.created) == 1
    for created in hass.created:
        if hasattr(created, "close"):
            created.close()
    client._check_outage_alert()
    assert len(hass.created) == 1
    client.last_frame_monotonic = None
    client._check_outage_alert()


def test_set_connected_only_notifies_on_change() -> None:
    client, _, states, _ = make_client()
    client._set_connected(True)
    client._set_connected(True)
    client._set_connected(False)
    assert states == [True, False]


async def test_stop_cancels_task_and_marks_disconnected() -> None:
    client, _, states, _ = make_client()
    client.connected = True

    class Task:
        def cancel(self):
            pass

        def __await__(self):
            async def done():
                return None

            return done().__await__()

    client._task = Task()  # type: ignore[assignment]
    await client.stop()
    assert states == [False]
    assert client._task is None


async def test_start_creates_background_task_and_stop_handles_exception() -> None:
    client, _, states, _ = make_client()
    client.start()
    assert client._task is not None
    assert client._hass.created
    for task in client._hass.created:
        if hasattr(task, "coro") and hasattr(task.coro, "close"):
            task.coro.close()

    class BadTask:
        def cancel(self):
            pass

        def __await__(self):
            async def boom():
                raise RuntimeError("cancel")

            return boom().__await__()

    client._task = BadTask()  # type: ignore[assignment]
    client.connected = True
    await client.stop()
    assert states == [False]


async def test_run_clean_exit_and_generic_reconnect() -> None:
    client, _, _, _ = make_client()

    async def clean():
        client._stop.set()

    client._session_once = clean  # type: ignore[method-assign]
    await client._run()

    client, _, states, _ = make_client()
    client.connected = True

    async def generic():
        client._stop.set()
        raise RuntimeError("network")

    client._session_once = generic  # type: ignore[method-assign]
    await client._run()
    assert states == [False]


async def test_run_cancellation_propagates() -> None:
    client, _, _, _ = make_client()

    async def cancelled():
        raise asyncio.CancelledError

    client._session_once = cancelled  # type: ignore[method-assign]
    with pytest.raises(asyncio.CancelledError):
        await client._run()


async def test_session_timeout_and_error_frame() -> None:
    class TimeoutThenError(FakeWs):
        def __init__(self):
            super().__init__()
            self.calls = 0

        async def receive(self):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError
            return SimpleNamespace(type=aiohttp.WSMsgType.ERROR)

    client, _, _, _ = make_client(ws=TimeoutThenError())
    with pytest.raises(LeasshConnectionError):
        await client._session_once()


def test_outage_notifier_is_used_when_present() -> None:
    hass = FakeHass()
    called = []

    async def notify(title, message, data):
        called.append((title, message, data))

    hass.data[DOMAIN] = {"outage_notifier": notify}
    client, _, _, _ = make_client(hass=hass)
    client.last_frame_monotonic = time.monotonic() - 400.0
    client._check_outage_alert()
    for created in hass.created:
        if hasattr(created, "close"):
            created.close()
    assert len(hass.created) == 2


async def test_call_skips_non_text_frames() -> None:
    ws = FakeWs(
        frames=[
            SimpleNamespace(type=aiohttp.WSMsgType.PING),
            SimpleNamespace(
                type=aiohttp.WSMsgType.TEXT,
                json=lambda: {"id": 2, "result": {"ok": True}},
            ),
        ]
    )
    client, _, _, _ = make_client(ws=ws)
    assert await client.call("ping") == {"ok": True}
