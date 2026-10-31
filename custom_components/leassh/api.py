"""Thin async client for the Leassh server HTTP+WS API.

One dialect, multiple transports: this client speaks the same JSON-RPC
method vocabulary the server uses everywhere. Bulk state sync is REST with
ETag caching; commands, events and presence ride the WS session.
"""

from __future__ import annotations

import json as jsonlib
import logging
from typing import Any

import aiohttp

from .const import DEFAULT_PORT, PROTOCOL_VERSION, WS_PATH

_LOGGER = logging.getLogger(__name__)


class LeasshError(Exception):
    """Base error."""


class LeasshConnectionError(LeasshError):
    """Server unreachable."""


class LeasshAuthError(LeasshError):
    """Token rejected (401)."""


class LeasshApiError(LeasshError):
    """Server refused a request (4xx/5xx body carried in message)."""


class LeasshLicenseError(LeasshError):
    """Server rejected the license (503 + {"error": "license invalid"})."""

    def __init__(self, message: str, tier: str | None = None) -> None:
        super().__init__(message)
        self.tier = tier


class LeasshClient:
    """HTTP + WS client. Owns nothing; the caller supplies the session."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        server_url: str,
        api_token: str,
    ) -> None:
        self._session = session
        self._server_url = server_url.rstrip("/")
        self._token = api_token

    @property
    def base_url(self) -> str:
        return self._server_url

    @property
    def ws_url(self) -> str:
        http = self._server_url
        if http.startswith("https://"):
            return "wss://" + http[len("https://"):] + WS_PATH
        if http.startswith("http://"):
            return "ws://" + http[len("http://"):] + WS_PATH
        return "ws://" + http + WS_PATH

    def _headers(self, etag: str | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._token}"}
        if etag:
            headers["If-None-Match"] = etag
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        etag: str | None = None,
        timeout: int = 10,
    ) -> aiohttp.ClientResponse:
        try:
            resp = await self._session.request(
                method,
                self._server_url + path,
                json=json,
                headers=self._headers(etag),
                timeout=aiohttp.ClientTimeout(total=timeout),
            )
        except (TimeoutError, aiohttp.ClientError) as err:
            msg = f"cannot connect to {self._server_url}: {err}"
            raise LeasshConnectionError(msg) from err
        if resp.status in (401, 403):
            await resp.release()
            raise LeasshAuthError(f"token rejected ({resp.status})")
        if resp.status == 503:
            body = await resp.text()
            await resp.release()
            # The server's license contract: 503 + {"error":"license invalid"}.
            # Never report this as "cannot connect" — the network is fine.
            if "license invalid" in body.lower() or "license" in body.lower():
                try:
                    payload = jsonlib.loads(body)
                except ValueError:
                    payload = {}
                raise LeasshLicenseError(
                    payload.get("error") or "license invalid",
                    tier=payload.get("tier"),
                )
        return resp

    async def health(self) -> dict[str, Any]:
        """Unauthenticated liveness + license banner."""
        try:
            resp = await self._session.request(
                "GET",
                self._server_url + "/api/health",
                timeout=aiohttp.ClientTimeout(total=10),
            )
        except (TimeoutError, aiohttp.ClientError) as err:
            msg = f"cannot connect to {self._server_url}: {err}"
            raise LeasshConnectionError(msg) from err
        async with resp:
            if resp.status != 200:
                raise LeasshApiError(f"health returned {resp.status}")
            return await resp.json()

    async def get_devices(self, etag: str | None = None) -> tuple[str | None, dict[str, Any] | None]:
        """Bulk fleet state. Returns (etag, data); data is None on 304."""
        resp = await self._request("GET", "/api/devices", etag=etag)
        async with resp:
            if resp.status == 304:
                return etag, None
            if resp.status != 200:
                body = await resp.text()
                raise LeasshApiError(f"/api/devices returned {resp.status}: {body[:200]}")
            return resp.headers.get("ETag"), await resp.json()

    async def ha_summary(self) -> dict[str, Any]:
        """Compact fleet summary used by the hub sensors."""
        resp = await self._request("GET", "/api/ha/summary")
        async with resp:
            if resp.status != 200:
                raise LeasshApiError(f"/api/ha/summary returned {resp.status}")
            return await resp.json()

    async def consent(self) -> dict[str, Any]:
        """Transparency view for parent dashboards."""
        resp = await self._request("GET", "/api/consent")
        async with resp:
            if resp.status != 200:
                raise LeasshApiError(f"/api/consent returned {resp.status}")
            return await resp.json()

    async def family_summary(self) -> dict[str, Any]:
        """Per-device screen time, limits and bedtime for parent dashboards."""
        resp = await self._request("GET", "/api/family/summary")
        async with resp:
            if resp.status != 200:
                raise LeasshApiError(f"/api/family/summary returned {resp.status}")
            return await resp.json()

    async def get_pack_suggestions(self) -> dict[str, Any]:
        """Bundled packs whose required tools exist on each node."""
        resp = await self._request("GET", "/api/packs/suggestions")
        async with resp:
            if resp.status != 200:
                raise LeasshApiError(f"/api/packs/suggestions returned {resp.status}")
            return await resp.json()

    async def reload_packs(self) -> dict[str, Any]:
        resp = await self._request("POST", "/api/packs/reload", json={}, timeout=30)
        async with resp:
            body = await resp.json(content_type=None)
            if resp.status != 200:
                raise LeasshApiError(f"pack reload failed: {body}")
            return body

    async def set_pack_enabled(self, pack_id: str, enabled: bool) -> dict[str, Any]:
        verb = "enable" if enabled else "disable"
        resp = await self._request("POST", f"/api/packs/{pack_id}/{verb}", json={})
        async with resp:
            body = await resp.json(content_type=None)
            if resp.status != 200:
                raise LeasshApiError(f"pack {verb} failed: {body}")
            return body

    async def get_actions(self) -> dict[str, Any]:
        """The curated verb catalog."""
        resp = await self._request("GET", "/api/actions")
        async with resp:
            if resp.status != 200:
                raise LeasshApiError(f"/api/actions returned {resp.status}")
            return await resp.json()

    async def action(self, node: str, verb: str) -> dict[str, Any]:
        """Run a curated verb on a node."""
        resp = await self._request(
            "POST", f"/api/devices/{node}/action", json={"action": verb}, timeout=60
        )
        async with resp:
            body = await resp.json(content_type=None)
            if resp.status != 200:
                raise LeasshApiError(f"action '{verb}' on {node} failed: {body}")
            return body

    async def notify(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Route a notification through the server (used by the notify entity)."""
        resp = await self._request("POST", "/api/notify", json=payload, timeout=15)
        async with resp:
            body = await resp.json(content_type=None)
            if resp.status != 200:
                raise LeasshApiError(f"/api/notify returned {resp.status}: {body}")
            return body

    async def ws_connect(self) -> aiohttp.ClientWebSocketResponse:
        """Open the WS session. Authentication happens via `hello` frame."""
        try:
            ws = await self._session.ws_connect(
                self.ws_url,
                heartbeat=15,  # client-side WS ping; server pings too
                timeout=20,
            )
        except (TimeoutError, aiohttp.ClientError) as err:
            msg = f"ws connect failed: {err}"
            raise LeasshConnectionError(msg) from err
        await ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "hello",
                "params": {
                    "protocol_version": PROTOCOL_VERSION,
                    "token": self._token,
                },
            }
        )
        return ws


def normalize_server_url(url: str) -> str:
    """Accept bare host[:port] and produce an http(s) URL."""
    url = url.strip().rstrip("/")
    if url.startswith(("http://", "https://")):
        return url
    return f"http://{url}"


def server_url_port(server_url: str) -> int:
    """Extract the port a server URL points at (for discovery checks)."""
    from urllib.parse import urlparse

    parsed = urlparse(server_url if "://" in server_url else f"http://{server_url}")
    return parsed.port or DEFAULT_PORT
