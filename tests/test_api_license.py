"""The 503 license contract must not be reported as a network failure."""

from __future__ import annotations

import pytest

from custom_components.leassh.api import LeasshClient, LeasshLicenseError


class _FakeResponse:
    status = 503

    async def text(self):
        return '{"error":"license invalid","tier":"free"}'

    async def release(self):
        return None


class _FakeSession:
    async def request(self, *args, **kwargs):
        return _FakeResponse()


async def test_503_license_invalid_maps_to_license_error() -> None:
    client = LeasshClient(_FakeSession(), "http://127.0.0.1:8790", "ha-token")
    with pytest.raises(LeasshLicenseError) as exc:
        await client._request("GET", "/api/devices")
    assert exc.value.tier == "free"
    assert "license invalid" in str(exc.value)
