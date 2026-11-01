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


CATALOG = {
    "catalog": [
        {
            "id": "household.bedtime",
            "name": "Bedtime",
            "bundle": "family",
            "scope": "node",
            "destructive": True,
            "description": "Quiet hours and session enforcement per device.",
        }
    ],
    "installed": [],
    "bundles": [{"id": "family", "recipes": ["household.bedtime"]}],
    "instances": [],
}

MANIFEST = {
    "id": "household.bedtime",
    "name": "Bedtime",
    "scope": "node",
    "parameters": {
        "node": {"type": "node", "required": True},
        "quiet_hours": {"type": "schedule", "default": "21:00-07:00"},
        "grace": {"type": "duration", "default": "5m", "description": "How long to wait before sign-out."},
        "enabled": {"type": "boolean", "default": True},
        "categories": {
            "type": "multi_select",
            "options": ["games", "video"],
            "default": ["games"],
        },
        "daily_limit_minutes": {
            "type": "number",
            "default": 120,
            "min": 15,
            "max": 1440,
        },
        "message": {"type": "text", "default": "Bedtime soon"},
        "meter": {"type": "select", "options": ["screen_time"], "default": "screen_time"},
        "digest_time": {"type": "time", "default": "20:00"},
    },
}

INSTANCES = {
    "instances": [
        {
            "instance": {
                "id": "inst-1",
                "recipe_id": "household.bedtime",
                "target": "rig",
                "parameters": {"node": "rig", "grace": "5m"},
                "enabled": True,
            },
            "display_name": "Bedtime rig",
            "status": {"recent_events": []},
        }
    ]
}


SITE_MANIFEST = {
    "id": "household.site-test",
    "name": "Site test",
    "scope": "site",
    "parameters": {
        "site": {"type": "site", "required": True},
        "enabled": {"type": "boolean", "default": True},
    },
}

FLEET_MANIFEST = {
    "id": "household.fleet-test",
    "name": "Fleet test",
    "scope": "fleet",
    "parameters": {
        "fleet": {"type": "fleet", "default": "local"},
        "enabled": {"type": "boolean", "default": True},
    },
}

SCOPED_CATALOG = {
    "catalog": [
        {
            "id": "household.site-test",
            "name": "Site test",
            "bundle": "family",
            "scope": "site",
            "destructive": False,
            "description": "A site-scoped test.",
        },
        {
            "id": "household.fleet-test",
            "name": "Fleet test",
            "bundle": "family",
            "scope": "fleet",
            "destructive": False,
            "description": "A fleet-scoped test.",
        },
    ],
    "installed": [
        {"id": "household.site-test"},
        {"id": "household.fleet-test"},
    ],
    "bundles": [{"id": "family", "recipes": ["household.site-test", "household.fleet-test"]}],
    "instances": [],
}


async def test_options_add_automation_wizard(hass) -> None:
    cfg = entry()
    cfg.add_to_hass(hass)
    calls = {}

    async def listing(self):
        return CATALOG

    async def install(self, recipe_id):
        calls["installed"] = recipe_id
        return {"ok": True}

    async def get_recipe(self, recipe_id):
        return {"manifest": MANIFEST}

    async def nodes(self):
        return {"nodes": [{"name": "rig"}]}

    async def sites(self):
        return {"sites": {}}

    async def create(self, recipe_id, parameters, enabled, display_name=None):
        calls.update(
            recipe_id=recipe_id,
            parameters=parameters,
            created_enabled=enabled,
            display_name=display_name,
        )
        return {
            "instance": {
                "id": "inst-1",
                "recipe_id": recipe_id,
                "parameters": parameters,
                "enabled": enabled,
                "display_name": display_name,
            }
        }

    async def dry_run(self, instance, node=None, idle_minutes=15):
        calls["preview"] = (instance, node)
        return {
            "summary": "Warn then log off",
            "eligible": True,
            "reasons": [],
            "steps": [
                {
                    "action": "session.warn",
                    "preview": {
                        "expected_effect": "Warn the session",
                        "risk": "T1",
                        "requires_approval": False,
                    },
                }
            ],
        }

    async def enable(self, instance, enabled):
        calls["enabled"] = (instance, enabled)
        return {"ok": True}

    with patch.multiple(
        "custom_components.leassh.api.LeasshClient",
        list_recipes=listing,
        install_recipe=install,
        get_recipe=get_recipe,
        list_nodes=nodes,
        list_sites=sites,
        create_recipe_instance=create,
        dry_run_recipe=dry_run,
        set_recipe_enabled=enable,
    ):
        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "add_automation"}
        )
        assert result["step_id"] == "add_automation"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"recipe_id": "household.bedtime"}
        )
        assert result["step_id"] == "add_automation_target"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"target": "rig"}
        )
        assert result["step_id"] == "add_automation_settings"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "quiet_hours": "22:00-06:00",
                "grace": "10m",
                "enabled": False,
                "categories": ["games", "video"],
                "daily_limit_minutes": 90,
                "message": "Go to bed",
                "meter": "screen_time",
                "digest_time": "20:30",
            },
        )
        assert result["step_id"] == "add_automation_review"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"display_name": "Bedtime rig"}
        )
        assert result["step_id"] == "add_automation_preview"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"enable_now": True}
        )

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert calls["installed"] == "household.bedtime"
    assert calls["recipe_id"] == "household.bedtime"
    assert calls["parameters"]["node"] == "rig"
    assert calls["parameters"]["categories"] == ["games", "video"]
    assert calls["created_enabled"] is False
    assert calls["display_name"] == "Bedtime rig"
    assert calls["preview"] == ("inst-1", "rig")
    assert calls["enabled"] == ("inst-1", True)


async def test_options_install_bundle(hass) -> None:
    cfg = entry()
    cfg.add_to_hass(hass)
    calls = []

    async def listing(self):
        return CATALOG

    async def install(self, recipe_id):
        calls.append(recipe_id)
        return {"ok": True}

    with patch.multiple(
        "custom_components.leassh.api.LeasshClient",
        list_recipes=listing,
        install_recipe=install,
    ):
        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "install_bundle"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"bundle": "family"}
        )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert calls == ["household.bedtime"]


async def test_options_add_automation_aborts(hass) -> None:
    cfg = entry()
    cfg.add_to_hass(hass)

    async def empty(self):
        return {"catalog": [], "installed": [], "bundles": []}

    with patch(
        "custom_components.leassh.api.LeasshClient.list_recipes", new=empty
    ):
        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "add_automation"}
        )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "no_templates"

    async def broken(self):
        raise LeasshApiError("offline")

    with patch(
        "custom_components.leassh.api.LeasshClient.list_recipes", new=broken
    ):
        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "add_automation"}
        )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "cannot_load_recipes"

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


async def test_options_manage_automations_actions(hass) -> None:
    cfg = entry()
    cfg.add_to_hass(hass)
    calls = {}

    async def instances(self):
        return INSTANCES

    async def get_recipe(self, recipe_id):
        return {"manifest": MANIFEST}

    async def enable(self, instance, enabled):
        calls["enabled"] = (instance, enabled)
        return {"ok": True}

    async def dry_run(self, instance, node=None, idle_minutes=15):
        calls["preview"] = (instance, node)
        return {
            "summary": "Warn then log off",
            "eligible": True,
            "reasons": [],
            "steps": [],
        }

    async def update(self, instance, parameters=None, enabled=None, display_name=None):
        calls["updated"] = (instance, parameters, enabled, display_name)
        return {"instance": {"id": instance}}

    async def delete(self, instance):
        calls["deleted"] = instance
        return {"ok": True}

    patches = patch.multiple(
        "custom_components.leassh.api.LeasshClient",
        recipe_instances=instances,
        get_recipe=get_recipe,
        set_recipe_enabled=enable,
        dry_run_recipe=dry_run,
        update_recipe_instance=update,
        delete_recipe_instance=delete,
    )
    with patches:
        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "manage_automations"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"instance": "inst-1"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "enable_automation"}
        )
        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert calls["enabled"] == ("inst-1", True)

        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "manage_automations"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"instance": "inst-1"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "preview_automation"}
        )
        assert result["step_id"] == "preview_automation"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"done": True}
        )
        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert calls["preview"] == ("inst-1", "rig")

        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "manage_automations"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"instance": "inst-1"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "edit_automation"}
        )
        assert result["step_id"] == "add_automation_settings"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {}
        )
        assert result["step_id"] == "add_automation_review"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"display_name": "Quiet hours rig"}
        )
        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert calls["updated"][0] == "inst-1"
        assert calls["updated"][3] == "Quiet hours rig"

        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "manage_automations"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"instance": "inst-1"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "delete_automation"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"confirm": True}
        )
        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert calls["deleted"] == "inst-1"


async def test_options_install_bundle_error(hass) -> None:
    cfg = entry()
    cfg.add_to_hass(hass)

    async def listing(self):
        return CATALOG

    async def install(self, recipe_id):
        raise LeasshApiError("bad bundle")

    with patch.multiple(
        "custom_components.leassh.api.LeasshClient",
        list_recipes=listing,
        install_recipe=install,
    ):
        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "install_bundle"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"bundle": "family"}
        )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"]["base"] == "recipe_install_failed"


async def test_options_manage_automations_aborts(hass) -> None:
    cfg = entry()
    cfg.add_to_hass(hass)

    async def empty(self):
        return {"instances": []}

    with patch(
        "custom_components.leassh.api.LeasshClient.recipe_instances", new=empty
    ):
        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "manage_automations"}
        )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "no_automations"

    async def broken(self):
        raise LeasshApiError("offline")

    with patch(
        "custom_components.leassh.api.LeasshClient.recipe_instances", new=broken
    ):
        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "manage_automations"}
        )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "cannot_load_automations"


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

    async def listing(self):
        return CATALOG

    with patch.multiple(
        "custom_components.leassh.api.LeasshClient",
        list_recipes=listing,
        install_recipe=auth_error,
    ):
        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "install_bundle"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"bundle": "family"}
        )
        assert result["errors"]["base"] == "invalid_auth"


async def test_options_add_automation_site_and_fleet_scopes(hass) -> None:
    cfg = entry()
    cfg.add_to_hass(hass)
    created = []

    async def listing(self):
        return SCOPED_CATALOG

    async def get_recipe(self, recipe_id):
        return {
            "manifest": SITE_MANIFEST
            if recipe_id == "household.site-test"
            else FLEET_MANIFEST
        }

    async def nodes(self):
        return {"nodes": [{"name": "rig"}]}

    async def sites(self):
        return {"sites": {"home": ["rig"]}}

    async def create(self, recipe_id, parameters, enabled, display_name=None):
        created.append((recipe_id, parameters, enabled, display_name))
        return {"instance": {"id": f"inst-{len(created)}"}}

    async def dry_run(self, instance, node=None, idle_minutes=15):
        return {"summary": "ok", "eligible": True, "reasons": [], "steps": []}

    with patch.multiple(
        "custom_components.leassh.api.LeasshClient",
        list_recipes=listing,
        get_recipe=get_recipe,
        list_nodes=nodes,
        list_sites=sites,
        create_recipe_instance=create,
        dry_run_recipe=dry_run,
    ):
        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "add_automation"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"recipe_id": "household.site-test"}
        )
        assert result["step_id"] == "add_automation_target"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"target": "home"}
        )
        assert result["step_id"] == "add_automation_settings"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"enabled": True}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"enable_now": False}
        )
        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY

        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "add_automation"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"recipe_id": "household.fleet-test"}
        )
        assert result["step_id"] == "add_automation_target"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"target": "local"}
        )
        assert result["step_id"] == "add_automation_settings"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"enabled": True}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"enable_now": False}
        )
        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY

    assert created[0][1]["site"] == "home"
    assert created[1][1]["fleet"] == "local"


async def test_options_add_automation_errors(hass) -> None:
    cfg = entry()
    cfg.add_to_hass(hass)

    async def listing(self):
        return CATALOG

    async def installed_listing(self):
        return {**CATALOG, "installed": [{"id": "household.bedtime"}]}

    async def bad_recipe(self, recipe_id):
        raise LeasshApiError("bad manifest")

    async def nodes(self):
        return {"nodes": [{"name": "rig"}]}

    async def sites(self):
        return {"sites": {}}

    with patch.multiple(
        "custom_components.leassh.api.LeasshClient",
        list_recipes=installed_listing,
        get_recipe=bad_recipe,
        list_nodes=nodes,
        list_sites=sites,
    ):
        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "add_automation"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"recipe_id": "household.bedtime"}
        )
    assert result["errors"]["base"] == "recipe_install_failed"

    async def good_recipe(self, recipe_id):
        return {"manifest": MANIFEST}

    async def bad_create(self, recipe_id, parameters, enabled, display_name=None):
        raise LeasshApiError("bad create")

    with patch.multiple(
        "custom_components.leassh.api.LeasshClient",
        list_recipes=installed_listing,
        get_recipe=good_recipe,
        list_nodes=nodes,
        list_sites=sites,
        create_recipe_instance=bad_create,
    ):
        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "add_automation"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"recipe_id": "household.bedtime"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"target": "rig"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"display_name": "Bedtime rig"}
        )
    assert result["errors"]["base"] == "automation_create_failed"

    async def bad_preview(self, instance, node=None, idle_minutes=15):
        raise LeasshApiError("bad preview")

    async def bad_enable(self, instance, enabled):
        raise LeasshApiError("bad enable")

    async def create(self, recipe_id, parameters, enabled, display_name=None):
        return {"instance": {"id": "inst-1"}}

    with patch.multiple(
        "custom_components.leassh.api.LeasshClient",
        list_recipes=installed_listing,
        get_recipe=good_recipe,
        list_nodes=nodes,
        list_sites=sites,
        create_recipe_instance=create,
        dry_run_recipe=bad_preview,
        set_recipe_enabled=bad_enable,
    ):
        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "add_automation"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"recipe_id": "household.bedtime"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"target": "rig"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"display_name": "Bedtime rig"}
        )
        assert result["step_id"] == "add_automation_preview"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"enable_now": True}
        )
    assert result["errors"]["base"] == "automation_enable_failed"


async def test_options_manage_disable_delete_back(hass) -> None:
    cfg = entry()
    cfg.add_to_hass(hass)
    calls = []

    async def instances(self):
        return INSTANCES

    async def enable(self, instance, enabled):
        calls.append(("enabled", instance, enabled))

    async def delete(self, instance):
        calls.append(("deleted", instance))
        raise LeasshApiError("cannot delete")

    with patch.multiple(
        "custom_components.leassh.api.LeasshClient",
        recipe_instances=instances,
        set_recipe_enabled=enable,
        delete_recipe_instance=delete,
    ):
        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "manage_automations"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"instance": "inst-1"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "disable_automation"}
        )
        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert calls[-1] == ("enabled", "inst-1", False)

        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "manage_automations"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"instance": "inst-1"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "back_to_automations"}
        )
        assert result["step_id"] == "manage_automations"

        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "manage_automations"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"instance": "inst-1"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "delete_automation"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"confirm": False}
        )
        assert result["errors"]["base"] == "confirm_delete"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"confirm": True}
        )
    assert result["errors"]["base"] == "automation_delete_failed"


TARGET_ONLY_MANIFEST = {
    "id": "household.target-only",
    "name": "Target only",
    "scope": "node",
    "parameters": {"node": {"type": "node", "required": True}},
}

TARGET_ONLY_CATALOG = {
    "catalog": [
        {
            "id": "household.target-only",
            "name": "Target only",
            "bundle": "family",
            "scope": "node",
            "destructive": False,
            "description": "No settings beyond the target.",
        }
    ],
    "installed": [{"id": "household.target-only"}],
    "bundles": [],
    "instances": [],
}


async def test_options_add_automation_target_errors(hass) -> None:
    cfg = entry()
    cfg.add_to_hass(hass)

    async def listing(self):
        return {**CATALOG, "installed": [{"id": "household.bedtime"}]}

    async def get_recipe(self, recipe_id):
        return {"manifest": MANIFEST}

    async def empty_nodes(self):
        return {"nodes": []}

    async def broken_nodes(self):
        raise LeasshApiError("offline")

    async def sites(self):
        return {"sites": {}}

    with patch.multiple(
        "custom_components.leassh.api.LeasshClient",
        list_recipes=listing,
        get_recipe=get_recipe,
        list_nodes=empty_nodes,
        list_sites=sites,
    ):
        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "add_automation"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"recipe_id": "household.bedtime"}
        )
    assert result["step_id"] == "add_automation_target"
    assert result["errors"]["base"] == "no_targets"

    with patch.multiple(
        "custom_components.leassh.api.LeasshClient",
        list_recipes=listing,
        get_recipe=get_recipe,
        list_nodes=broken_nodes,
        list_sites=sites,
    ):
        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "add_automation"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"recipe_id": "household.bedtime"}
        )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "cannot_load_targets"


async def test_options_add_automation_auth_errors(hass) -> None:
    cfg = entry()
    cfg.add_to_hass(hass)

    async def listing(self):
        return {**CATALOG, "installed": [{"id": "household.bedtime"}]}

    async def auth_error(self, *args, **kwargs):
        raise LeasshAuthError("401")

    with patch.multiple(
        "custom_components.leassh.api.LeasshClient",
        list_recipes=listing,
        get_recipe=auth_error,
    ):
        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "add_automation"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"recipe_id": "household.bedtime"}
        )
    assert result["errors"]["base"] == "invalid_auth"

    async def get_recipe(self, recipe_id):
        return {"manifest": MANIFEST}

    async def nodes(self):
        return {"nodes": [{"name": "rig"}]}

    async def sites(self):
        return {"sites": {}}

    async def create(self, *args, **kwargs):
        raise LeasshAuthError("401")

    with patch.multiple(
        "custom_components.leassh.api.LeasshClient",
        list_recipes=listing,
        get_recipe=get_recipe,
        list_nodes=nodes,
        list_sites=sites,
        create_recipe_instance=create,
    ):
        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "add_automation"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"recipe_id": "household.bedtime"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"target": "rig"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"display_name": "Bedtime rig"}
        )
    assert result["errors"]["base"] == "invalid_auth"


async def test_options_add_automation_preview_text(hass) -> None:
    cfg = entry()
    cfg.add_to_hass(hass)

    async def listing(self):
        return {**CATALOG, "installed": [{"id": "household.bedtime"}]}

    async def get_recipe(self, recipe_id):
        return {"manifest": MANIFEST}

    async def nodes(self):
        return {"nodes": [{"name": "rig"}]}

    async def sites(self):
        return {"sites": {}}

    async def create(self, recipe_id, parameters, enabled, display_name=None):
        return {"instance": {"id": "inst-1"}}

    async def preview(self, instance, node=None, idle_minutes=15):
        return {
            "summary": "Warn then log off",
            "eligible": False,
            "reasons": ["not signed in"],
            "steps": [
                {
                    "action": "session.warn",
                    "after_seconds": 300,
                    "preview": {"expected_effect": "Warn the session"},
                },
                "not-a-dict",
            ],
        }

    with patch.multiple(
        "custom_components.leassh.api.LeasshClient",
        list_recipes=listing,
        get_recipe=get_recipe,
        list_nodes=nodes,
        list_sites=sites,
        create_recipe_instance=create,
        dry_run_recipe=preview,
    ):
        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "add_automation"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"recipe_id": "household.bedtime"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"target": "rig"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"display_name": "Bedtime rig"}
        )
        assert result["step_id"] == "add_automation_preview"
        description = result["description_placeholders"]["summary"]
        assert "Would not run" in description
        assert "after 300s" in description
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"enable_now": False}
        )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY


async def test_options_add_automation_without_settings(hass) -> None:
    cfg = entry()
    cfg.add_to_hass(hass)

    async def listing(self):
        return TARGET_ONLY_CATALOG

    async def get_recipe(self, recipe_id):
        return {"manifest": TARGET_ONLY_MANIFEST}

    async def nodes(self):
        return {"nodes": [{"name": "rig"}]}

    async def sites(self):
        return {"sites": {}}

    async def create(self, recipe_id, parameters, enabled, display_name=None):
        return {"instance": {"id": "inst-1"}}

    async def preview(self, instance, node=None, idle_minutes=15):
        return {"summary": "ok", "eligible": True, "reasons": [], "steps": []}

    with patch.multiple(
        "custom_components.leassh.api.LeasshClient",
        list_recipes=listing,
        get_recipe=get_recipe,
        list_nodes=nodes,
        list_sites=sites,
        create_recipe_instance=create,
        dry_run_recipe=preview,
    ):
        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "add_automation"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"recipe_id": "household.target-only"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"target": "rig"}
        )
        assert result["step_id"] == "add_automation_review"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"display_name": "Target only"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"enable_now": False}
        )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY


async def test_options_manage_edit_and_preview_errors(hass) -> None:
    cfg = entry()
    cfg.add_to_hass(hass)

    calls = {"instances": 0}

    async def empty_second(self):
        calls["instances"] += 1
        if calls["instances"] <= 2:
            return INSTANCES
        return {"instances": []}

    with patch(
        "custom_components.leassh.api.LeasshClient.recipe_instances",
        new=empty_second,
    ):
        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "manage_automations"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"instance": "inst-1"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "edit_automation"}
        )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "automation_not_found"

    async def instances(self):
        return INSTANCES

    async def broken_recipe(self, recipe_id):
        raise LeasshApiError("offline")

    with patch.multiple(
        "custom_components.leassh.api.LeasshClient",
        recipe_instances=instances,
        get_recipe=broken_recipe,
    ):
        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "manage_automations"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"instance": "inst-1"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "edit_automation"}
        )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "automation_not_found"

    async def broken_preview(self, instance, node=None, idle_minutes=15):
        raise LeasshApiError("offline")

    with patch.multiple(
        "custom_components.leassh.api.LeasshClient",
        recipe_instances=instances,
        dry_run_recipe=broken_preview,
    ):
        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "manage_automations"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"instance": "inst-1"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "preview_automation"}
        )
        assert result["step_id"] == "preview_automation"
        assert "offline" in result["description_placeholders"]["summary"]
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"done": True}
        )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY

    async def broken_enable(self, instance, enabled):
        raise LeasshApiError("offline")

    with patch.multiple(
        "custom_components.leassh.api.LeasshClient",
        recipe_instances=instances,
        set_recipe_enabled=broken_enable,
    ):
        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "manage_automations"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"instance": "inst-1"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "enable_automation"}
        )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "automation_update_failed"


async def test_options_install_bundle_aborts(hass) -> None:
    cfg = entry()
    cfg.add_to_hass(hass)

    async def broken(self):
        raise LeasshApiError("offline")

    with patch(
        "custom_components.leassh.api.LeasshClient.list_recipes", new=broken
    ):
        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "install_bundle"}
        )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "cannot_load_recipes"

    async def empty(self):
        return {"bundles": []}

    with patch(
        "custom_components.leassh.api.LeasshClient.list_recipes", new=empty
    ):
        result = await hass.config_entries.options.async_init(cfg.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "install_bundle"}
        )
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "no_bundles"
