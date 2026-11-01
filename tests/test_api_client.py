"""LeasshClient HTTP/WS contract tests (success and failure paths)."""

from __future__ import annotations

from typing import Any

import aiohttp
import pytest

from custom_components.leassh.api import (
    LeasshApiError,
    LeasshAuthError,
    LeasshClient,
    LeasshConnectionError,
    LeasshLicenseError,
    normalize_server_url,
    server_url_port,
)


class FakeResponse:
    def __init__(self, status: int = 200, payload: Any = None, text: str = "") -> None:
        self.status = status
        self._payload = payload if payload is not None else {}
        self._text = text
        self.headers = {"ETag": '"abc"'}
        self.released = False

    async def json(self, content_type: str | None = None):
        return self._payload

    async def text(self) -> str:
        return self._text

    async def release(self) -> None:
        self.released = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args) -> None:
        await self.release()


class FakeWs:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self._frames: list[Any] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    async def close(self) -> None:
        self.closed = True

    def exception(self):
        return None


class FakeSession:
    def __init__(self, response: FakeResponse | Exception | None = None) -> None:
        self.response = response or FakeResponse()
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.ws = FakeWs()
        self.ws_exception: Exception | None = None

    async def request(self, method: str, url: str, **kwargs):
        self.calls.append((method, url, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    async def ws_connect(self, *args, **kwargs):
        if self.ws_exception:
            raise self.ws_exception
        return self.ws


def client(response: FakeResponse | Exception | None = None) -> tuple[LeasshClient, FakeSession]:
    session = FakeSession(response)
    return LeasshClient(session, "http://127.0.0.1:8790", "token"), session


async def test_read_and_action_methods_use_the_expected_paths() -> None:
    api, session = client(FakeResponse(payload={"ok": True}))
    assert await api.health() == {"ok": True}
    assert await api.get_devices() == ('"abc"', {"ok": True})
    assert await api.ha_summary() == {"ok": True}
    assert await api.consent() == {"ok": True}
    assert await api.setup_status() == {"ok": True}
    assert await api.setup_state() == {"ok": True}
    assert await api.list_nodes() == {"ok": True}
    assert await api.list_recipes() == {"ok": True}
    assert await api.recipe_instances() == {"ok": True}
    assert await api.capability_manifest() == {"ok": True}
    assert await api.get_pack_suggestions() == {"ok": True}
    assert await api.get_actions() == {"ok": True}
    assert await api.action("rig", "reboot_node") == {"ok": True}
    assert await api.notify({"title": "t"}) == {"ok": True}
    paths = [call[1] for call in session.calls]
    assert any(path.endswith("/api/health") for path in paths)
    assert any(path.endswith("/api/devices") for path in paths)
    assert any(path.endswith("/api/setup/status") for path in paths)
    assert any(path.endswith("/api/capabilities?client=ha") for path in paths)
    assert any(path.endswith("/api/devices/rig/action") for path in paths)


async def test_mutation_methods_send_expected_bodies() -> None:
    api, session = client(FakeResponse(payload={"ok": True}))
    await api.set_protection_profile("guardian", "UNRESTRICTED")
    await api.add_node({"name": "rig"})
    await api.remove_node("rig")
    await api.test_node("rig")
    await api.install_recipe("household.bedtime")
    await api.create_recipe_instance("household.bedtime", {"node": "rig"}, True)
    await api.update_recipe_instance("inst", parameters={"grace": "60s"}, enabled=False)
    await api.set_recipe_enabled("inst", True)
    await api.set_recipe_enabled("inst", False)
    await api.dry_run_recipe("inst", "rig", 30)
    await api.recipe_status("inst")
    await api.set_pack_enabled("gpu-nvidia", False)
    await api.reload_packs()
    assert any(call[0] == "PATCH" for call in session.calls)
    assert any(call[0] == "DELETE" for call in session.calls)


async def test_304_returns_no_data() -> None:
    api, _ = client(FakeResponse(status=304))
    etag, data = await api.get_devices("old")
    assert etag == "old"
    assert data is None


async def test_auth_and_license_and_api_errors_are_typed() -> None:
    api, _ = client(FakeResponse(status=401, text="no"))
    with pytest.raises(LeasshAuthError):
        await api.get_devices()

    api, _ = client(FakeResponse(status=503, text='{"error":"license invalid","tier":"free"}'))
    with pytest.raises(LeasshLicenseError) as exc:
        await api.get_devices()
    assert exc.value.tier == "free"

    api, _ = client(FakeResponse(status=500, text="boom"))
    with pytest.raises(LeasshApiError):
        await api.get_devices()


async def test_connection_failures_are_typed() -> None:
    api, _ = client(aiohttp.ClientError("offline"))
    with pytest.raises(LeasshConnectionError):
        await api.get_devices()
    with pytest.raises(LeasshConnectionError):
        await api.health()


async def test_ws_connect_sends_hello_and_urls_are_normalized() -> None:
    api, session = client()
    ws = await api.ws_connect()
    assert session.ws.sent[0]["method"] == "hello"
    assert session.ws.sent[0]["params"]["token"] == "token"
    assert ws is session.ws
    assert normalize_server_url("host:8790") == "http://host:8790"
    assert normalize_server_url("https://host/") == "https://host"
    assert server_url_port("https://host:9443") == 9443
    assert server_url_port("host") == 8790


async def test_every_wrapper_raises_api_error_on_500() -> None:
    api, _ = client(FakeResponse(status=500, payload={"error": "boom"}))
    calls = [
        lambda: api.ha_summary(),
        lambda: api.consent(),
        lambda: api.setup_status(),
        lambda: api.setup_state(),
        lambda: api.set_protection_profile("guardian"),
        lambda: api.list_nodes(),
        lambda: api.add_node({"name": "rig"}),
        lambda: api.remove_node("rig"),
        lambda: api.test_node("rig"),
        lambda: api.list_recipes(),
        lambda: api.install_recipe("x"),
        lambda: api.recipe_instances(),
        lambda: api.create_recipe_instance("x", {}, True),
        lambda: api.update_recipe_instance("i", parameters={}),
        lambda: api.set_recipe_enabled("i", True),
        lambda: api.dry_run_recipe("i"),
        lambda: api.recipe_status("i"),
        lambda: api.capability_manifest(),
        lambda: api.get_pack_suggestions(),
        lambda: api.reload_packs(),
        lambda: api.set_pack_enabled("p", True),
        lambda: api.get_actions(),
        lambda: api.action("rig", "reboot_node"),
        lambda: api.notify({"title": "t"}),
    ]
    for call in calls:
        with pytest.raises(LeasshApiError):
            await call()


async def test_health_non_200_and_ws_url_shapes() -> None:
    api, _ = client(FakeResponse(status=500))
    with pytest.raises(LeasshApiError):
        await api.health()
    assert api.base_url == "http://127.0.0.1:8790"
    assert api._headers("etag")["If-None-Match"] == "etag"
    https = LeasshClient(FakeSession(), "https://host", "t")
    assert https.ws_url == "wss://host/api/ws"
    bare = LeasshClient(FakeSession(), "host", "t")
    assert bare.ws_url == "ws://host/api/ws"


async def test_ws_connect_failure_is_typed() -> None:
    session = FakeSession()
    session.ws_exception = aiohttp.ClientError("down")
    api = LeasshClient(session, "http://127.0.0.1:8790", "token")
    with pytest.raises(LeasshConnectionError):
        await api.ws_connect()
