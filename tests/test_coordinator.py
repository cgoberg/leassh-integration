"""Coordinator fetch, ETag/304 and degraded-endpoint behavior."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.leassh.api import LeasshApiError, LeasshAuthError
from custom_components.leassh.const import DOMAIN
from custom_components.leassh.coordinator import LeasshCoordinator


class FakeClient:
    base_url = "http://127.0.0.1:8790"

    def __init__(self, data=None, etag='"e1"', fail=None, optional_fail=False):
        self._data = data if data is not None else {"nodes": [{"name": "rig", "host": "10.0.0.9"}]}
        self._etag = etag
        self.fail = fail
        self.optional_fail = optional_fail

    async def get_devices(self, etag=None):
        if self.fail:
            raise self.fail
        if self._data is None:
            return etag, None
        return self._etag, self._data

    async def _optional(self, value):
        if self.optional_fail:
            raise LeasshApiError("optional down")
        return value

    async def ha_summary(self):
        return await self._optional({"total": 1})

    async def consent(self):
        return await self._optional({"total": 1})

    async def setup_status(self):
        return await self._optional({"ok": True, "profile": "trusted_owner"})

    async def list_recipes(self):
        return await self._optional({"catalog": [{"id": "household.bedtime"}]})

    async def recipe_instances(self):
        return await self._optional({"instances": [{"instance": {"id": "inst-1"}}]})

    async def capability_manifest(self):
        return await self._optional({"capabilities": []})

    async def health(self):
        return await self._optional({"license": {"valid": True}})

    async def get_pack_suggestions(self):
        return await self._optional({"nodes": []})


def _entry() -> MockConfigEntry:
    return MockConfigEntry(domain=DOMAIN, data={}, entry_id="entry1")


async def test_update_success_populates_all_sections(hass) -> None:
    coordinator = LeasshCoordinator(hass, FakeClient(), _entry())
    data = await coordinator._async_update_data()
    assert data["nodes"][0]["name"] == "rig"
    assert coordinator._etag == '"e1"'
    assert coordinator.fleet == {"total": 1}
    assert coordinator.consent == {"total": 1}
    assert coordinator.setup["profile"] == "trusted_owner"
    assert coordinator.recipes["catalog"][0]["id"] == "household.bedtime"
    assert coordinator.recipe_instances[0]["instance"]["id"] == "inst-1"
    assert coordinator.capabilities == {"capabilities": []}
    assert coordinator.health["license"]["valid"] is True


async def test_304_returns_previous_data_and_keeps_etag(hass) -> None:
    coordinator = LeasshCoordinator(hass, FakeClient(), _entry())
    first = await coordinator._async_update_data()
    coordinator.data = first
    coordinator.client = FakeClient(data=None)
    second = await coordinator._async_update_data()
    assert second == first


async def test_device_failure_raises_update_failed(hass) -> None:
    coordinator = LeasshCoordinator(hass, FakeClient(fail=LeasshApiError("down")), _entry())
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    coordinator = LeasshCoordinator(hass, FakeClient(fail=LeasshAuthError("401")), _entry())
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_optional_endpoint_failures_degrade_to_empty(hass) -> None:
    coordinator = LeasshCoordinator(hass, FakeClient(optional_fail=True), _entry())
    await coordinator._async_update_data()
    assert coordinator.fleet == {}
    assert coordinator.consent == {}
    assert coordinator.setup == {}
    assert coordinator.recipes == {}
    assert coordinator.recipe_instances == []
    assert coordinator.capabilities == {}
    assert coordinator.health == {}
    assert coordinator.suggestions == {}
