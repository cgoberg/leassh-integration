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


async def test_user_flow_reports_license_invalid_not_cannot_connect(hass) -> None:
    from custom_components.leassh.api import LeasshLicenseError

    async def reject_license(self, etag=None):
        raise LeasshLicenseError("license invalid", tier="free")

    with patch(
        "custom_components.leassh.api.LeasshClient.get_devices",
        new=reject_license,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_SERVER_URL: "http://192.168.1.127:8790",
                CONF_API_TOKEN: "ha-token",
            },
        )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "license_invalid"}


async def test_options_add_node_without_yaml(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_SERVER_URL: "http://127.0.0.1:8790", CONF_API_TOKEN: "token"},
        unique_id="127.0.0.1:8790",
    )
    entry.add_to_hass(hass)

    added = {}

    async def fake_add_node(self, node):
        added.update(node)
        return {"ok": True}

    async def fake_test_node(self, name):
        return {"ok": True, "reachable": True}

    with patch(
        "custom_components.leassh.api.LeasshClient.add_node", new=fake_add_node
    ), patch(
        "custom_components.leassh.api.LeasshClient.test_node", new=fake_test_node
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] == data_entry_flow.FlowResultType.MENU
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "add_node"}
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "name": "laptop",
                "host": "10.0.0.10",
                "ssh": "user@10.0.0.10",
                "os": "linux",
                "site": "home",
                "reclaim_idle_sessions": False,
            },
        )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert added["name"] == "laptop"
    assert added["ssh"] == "user@10.0.0.10"


async def test_options_protection_profile_requires_unrestricted_confirmation(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_SERVER_URL: "http://127.0.0.1:8790", CONF_API_TOKEN: "token"},
        unique_id="127.0.0.1:8790",
    )
    entry.add_to_hass(hass)
    calls = []

    async def fake_profile(self, profile, confirmation=None):
        calls.append((profile, confirmation))
        return {"ok": True}

    with patch(
        "custom_components.leassh.api.LeasshClient.set_protection_profile",
        new=fake_profile,
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "protection"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"profile": "unrestricted", "confirmation": "UNRESTRICTED"},
        )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert calls == [("unrestricted", "UNRESTRICTED")]
