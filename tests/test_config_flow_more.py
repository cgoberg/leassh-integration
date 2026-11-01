"""Additional config/options flow coverage: discovery, notify, recipes, errors."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from homeassistant import config_entries, data_entry_flow
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.leassh.api import (
    LeasshApiError,
    LeasshAuthError,
    LeasshConnectionError,
    LeasshLicenseError,
)
from custom_components.leassh.const import (
    CONF_API_TOKEN,
    CONF_NOTIFY_TARGETS,
    CONF_SERVER_URL,
    DOMAIN,
)


def entry(options=None) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={CONF_SERVER_URL: "http://127.0.0.1:8790", CONF_API_TOKEN: "token"},
        options=options or {},
        entry_id="entry1",
        unique_id="127.0.0.1:8790",
    )


async def _noop(self, etag=None):
    return None, {"nodes": [], "total": 0}


async def test_zeroconf_discovery_and_confirm(hass) -> None:
    discovery = SimpleNamespace(
        properties={"api_port": "8790"},
        host="10.0.0.9",
    )
    with patch(
        "custom_components.leassh.api.LeasshClient.get_devices", new=_noop
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=discovery,
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "zeroconf_confirm"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_TOKEN: "token"}
        )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SERVER_URL] == "http://10.0.0.9:8790"


async def test_zeroconf_without_api_port_aborts(hass) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=SimpleNamespace(properties={}, host="10.0.0.9"),
    )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "no_api"


async def test_options_notify_targets(hass) -> None:
    cfg = entry()
    cfg.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(cfg.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "notify"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_NOTIFY_TARGETS: "mobile_app_a, mobile_app_b"}
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_NOTIFY_TARGETS] == ["mobile_app_a", "mobile_app_b"]


async def test_options_recipes_install_and_refresh(hass) -> None:
    cfg = entry()
    cfg.add_to_hass(hass)
    calls = []

    async def install(self, recipe_id):
        calls.append(recipe_id)
        return {"ok": True}

    async def listing(self):
        return {"catalog": []}

    with patch(
        "custom_components.leassh.api.LeasshClient.install_recipe", new=install
    ), patch(
        "custom_components.leassh.api.LeasshClient.list_recipes", new=listing
    ):
        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "recipes"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"recipe_id": "household.bedtime"}
        )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert calls == ["household.bedtime"]


async def test_options_add_node_and_profile_errors(hass) -> None:
    cfg = entry()
    cfg.add_to_hass(hass)

    async def bad_node(self, node):
        raise LeasshApiError("bad node")

    async def bad_profile(self, profile, confirmation=None):
        raise LeasshApiError("bad profile")

    with patch(
        "custom_components.leassh.api.LeasshClient.add_node", new=bad_node
    ):
        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "add_node"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"name": "rig", "host": "10.0.0.9", "ssh": "u@10.0.0.9", "os": "linux"},
        )
        assert result["errors"]["base"] == "cannot_add_node"

    with patch(
        "custom_components.leassh.api.LeasshClient.set_protection_profile",
        new=bad_profile,
    ):
        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "protection"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"profile": "guardian", "confirmation": ""}
        )
        assert result["errors"]["base"] == "invalid_profile"


async def test_options_recipe_install_error(hass) -> None:
    cfg = entry()
    cfg.add_to_hass(hass)

    async def bad(self, recipe_id):
        raise LeasshApiError("bad recipe")

    with patch(
        "custom_components.leassh.api.LeasshClient.install_recipe", new=bad
    ):
        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "recipes"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"recipe_id": "missing.recipe"}
        )
        assert result["errors"]["base"] == "recipe_install_failed"


async def test_reconfigure_rejects_bad_token_and_connection(hass) -> None:
    cfg = entry()
    cfg.add_to_hass(hass)

    async def reject(self, etag=None):
        raise LeasshAuthError("401")

    with patch(
        "custom_components.leassh.api.LeasshClient.get_devices", new=reject
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_RECONFIGURE, "entry_id": cfg.entry_id},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_SERVER_URL: "http://127.0.0.1:8790", CONF_API_TOKEN: "bad"},
        )
        assert result["errors"]["base"] == "invalid_auth"


async def test_user_flow_license_and_connection_errors(hass) -> None:
    async def license_error(self, etag=None):
        raise LeasshLicenseError("license invalid")

    async def connection_error(self, etag=None):
        raise LeasshConnectionError("offline")

    async def unexpected(self, etag=None):
        raise RuntimeError("boom")

    for error, expected in [
        (license_error, "license_invalid"),
        (connection_error, "cannot_connect"),
        (unexpected, "cannot_connect"),
    ]:
        with patch(
            "custom_components.leassh.api.LeasshClient.get_devices", new=error
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    CONF_SERVER_URL: "http://127.0.0.1:8790",
                    CONF_API_TOKEN: "token",
                },
            )
            assert result["errors"]["base"] == expected


async def test_reauth_and_reconfigure_license_error(hass) -> None:
    from custom_components.leassh.api import LeasshLicenseError

    cfg = entry()
    cfg.add_to_hass(hass)

    async def license_error(self, etag=None):
        raise LeasshLicenseError("license invalid")

    with patch(
        "custom_components.leassh.api.LeasshClient.get_devices", new=license_error
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_REAUTH, "entry_id": cfg.entry_id},
            data=cfg.data,
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_TOKEN: "new"}
        )
        assert result["errors"]["base"] == "license_invalid"

    with patch(
        "custom_components.leassh.api.LeasshClient.get_devices", new=license_error
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_RECONFIGURE, "entry_id": cfg.entry_id},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_SERVER_URL: "http://127.0.0.1:8790", CONF_API_TOKEN: "new"},
        )
        assert result["errors"]["base"] == "license_invalid"


async def test_options_auth_error_and_empty_recipe(hass) -> None:
    cfg = entry()
    cfg.add_to_hass(hass)

    async def auth_error(self, *args, **kwargs):
        raise LeasshAuthError("401")

    with patch(
        "custom_components.leassh.api.LeasshClient.add_node", new=auth_error
    ):
        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "add_node"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"name": "rig", "host": "h", "ssh": "u@h", "os": "linux"},
        )
        assert result["errors"]["base"] == "invalid_auth"

    with patch(
        "custom_components.leassh.api.LeasshClient.set_protection_profile",
        new=auth_error,
    ):
        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "protection"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"profile": "guardian", "confirmation": ""}
        )
        assert result["errors"]["base"] == "invalid_auth"

    with patch(
        "custom_components.leassh.api.LeasshClient.install_recipe", new=auth_error
    ):
        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "recipes"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"recipe_id": "x"}
        )
        assert result["errors"]["base"] == "invalid_auth"

    result = await hass.config_entries.options.async_init(cfg.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "recipes"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"recipe_id": ""}
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
