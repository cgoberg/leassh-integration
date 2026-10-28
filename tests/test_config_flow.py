"""Config, reauth and reconfigure flows (roadmap M2.8)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant import config_entries, data_entry_flow
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.leassh.api import LeasshAuthError
from custom_components.leassh.const import CONF_API_TOKEN, CONF_SERVER_URL, DOMAIN


async def _noop_get_devices(self, etag=None):
    return None, {"nodes": [], "total": 0}


async def test_user_flow_sets_unique_id(hass) -> None:
    with patch(
        "custom_components.leassh.api.LeasshClient.get_devices",
        new=_noop_get_devices,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_SERVER_URL: "http://192.168.1.127:8790",
                CONF_API_TOKEN: "token-one",
            },
        )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.unique_id == "192.168.1.127:8790"


async def test_reauth_updates_the_token_without_replacing_the_entry(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_SERVER_URL: "http://192.168.1.127:8790", CONF_API_TOKEN: "old"},
        unique_id="192.168.1.127:8790",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.leassh.api.LeasshClient.get_devices",
        new=_noop_get_devices,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
            data=entry.data,
        )
        assert result["step_id"] == "reauth_confirm"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_TOKEN: "new"}
        )

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_TOKEN] == "new"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_reauth_rejects_a_bad_token(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_SERVER_URL: "http://192.168.1.127:8790", CONF_API_TOKEN: "old"},
        unique_id="192.168.1.127:8790",
    )
    entry.add_to_hass(hass)

    async def reject(self, etag=None):
        raise LeasshAuthError("401")

    with patch(
        "custom_components.leassh.api.LeasshClient.get_devices", new=reject
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
            data=entry.data,
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_TOKEN: "still-wrong"}
        )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_reconfigure_changes_url_and_keeps_the_entry(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_SERVER_URL: "http://192.168.1.127:8790", CONF_API_TOKEN: "tok"},
        unique_id="192.168.1.127:8790",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.leassh.api.LeasshClient.get_devices",
        new=_noop_get_devices,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        assert result["step_id"] == "reconfigure"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_SERVER_URL: "192.168.1.200:8790",
                CONF_API_TOKEN: "tok",
            },
        )

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_SERVER_URL] == "http://192.168.1.200:8790"
    assert entry.unique_id == "192.168.1.200:8790"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
