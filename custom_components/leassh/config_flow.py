"""Config flow: server_url + api_token, mDNS discovery, connection test."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlparse

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TimeSelector,
)

if TYPE_CHECKING:
    from homeassistant.components.zeroconf import ZeroconfServiceInfo

from .api import (
    LeasshApiError,
    LeasshAuthError,
    LeasshClient,
    LeasshConnectionError,
    LeasshLicenseError,
    normalize_server_url,
)
from .const import CONF_API_TOKEN, CONF_NOTIFY_TARGETS, CONF_SERVER_URL, DEFAULT_PORT, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SERVER_URL): str,
        vol.Required(CONF_API_TOKEN): str,
    }
)


class LeasshConfigFlow(ConfigFlow, domain=DOMAIN):
    """Leassh server pairing."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered_url: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            server_url = normalize_server_url(user_input[CONF_SERVER_URL])
            client = LeasshClient(
                async_get_clientsession(self.hass),
                server_url,
                user_input[CONF_API_TOKEN],
            )
            try:
                # In-flow connection test: verifies reachability AND the
                # token in one round trip.
                await client.get_devices()
            except LeasshAuthError:
                errors["base"] = "invalid_auth"
            except LeasshLicenseError:
                errors["base"] = "license_invalid"
            except LeasshConnectionError:  # noqa: PERF203 - one by design
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001 - surface as cannot_connect
                _LOGGER.exception("Unexpected error connecting to Leassh")
                errors["base"] = "cannot_connect"
            else:
                parsed = urlparse(server_url)
                host = parsed.hostname or server_url
                await self.async_set_unique_id(
                    f"{host}:{parsed.port or DEFAULT_PORT}"
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Leassh {host}",
                    data={
                        CONF_SERVER_URL: server_url,
                        CONF_API_TOKEN: user_input[CONF_API_TOKEN],
                    },
                )

        schema = self.add_suggested_values_to_schema(
            STEP_USER_SCHEMA,
            {CONF_SERVER_URL: self._discovered_url} if self._discovered_url else None,
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:  # noqa: UP037 - string annotation via TYPE_CHECKING
        """Discovered a `_leassh._tcp` advertisement."""
        properties = discovery_info.properties
        api_port = properties.get("api_port")
        if not api_port:
            # A server advertising without api_port is not offering the API.
            return self.async_abort(reason="no_api")
        self._discovered_url = f"http://{discovery_info.host}:{int(api_port)}"
        await self.async_set_unique_id(
            f"{discovery_info.host}:{int(api_port)}"
        )
        # A re-discovered server updates the existing entry instead of only
        # aborting: a DHCP address change should repair the URL, not force a
        # delete/re-add.
        self._abort_if_unique_id_configured(
            updates={CONF_SERVER_URL: self._discovered_url}
        )
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return await self.async_step_user(
                {
                    CONF_SERVER_URL: self._discovered_url or "",
                    CONF_API_TOKEN: user_input[CONF_API_TOKEN],
                }
            )
        return self.async_show_form(
            step_id="zeroconf_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_TOKEN): str}),
            description_placeholders={"server": self._discovered_url or ""},
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """The stored token was rejected: ask for a fresh one, keep the entry."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            token = user_input[CONF_API_TOKEN]
            client = LeasshClient(
                async_get_clientsession(self.hass),
                normalize_server_url(entry.data[CONF_SERVER_URL]),
                token,
            )
            try:
                await client.get_devices()
            except LeasshAuthError:
                errors["base"] = "invalid_auth"
            except LeasshLicenseError:
                errors["base"] = "license_invalid"
            except LeasshConnectionError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data={**entry.data, CONF_API_TOKEN: token},
                    reason="reauth_successful",
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_TOKEN): str}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the server URL or token without deleting the entry."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            server_url = normalize_server_url(user_input[CONF_SERVER_URL])
            token = user_input[CONF_API_TOKEN]
            client = LeasshClient(
                async_get_clientsession(self.hass), server_url, token
            )
            try:
                await client.get_devices()
            except LeasshAuthError:
                errors["base"] = "invalid_auth"
            except LeasshLicenseError:
                errors["base"] = "license_invalid"
            except LeasshConnectionError:
                errors["base"] = "cannot_connect"
            else:
                parsed = urlparse(server_url)
                host = parsed.hostname or server_url
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=f"{host}:{parsed.port or DEFAULT_PORT}",
                    data={
                        **entry.data,
                        CONF_SERVER_URL: server_url,
                        CONF_API_TOKEN: token,
                    },
                    reason="reconfigure_successful",
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA,
                {
                    CONF_SERVER_URL: entry.data.get(CONF_SERVER_URL, ""),
                    CONF_API_TOKEN: entry.data.get(CONF_API_TOKEN, ""),
                },
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> LeasshOptionsFlow:
        return LeasshOptionsFlow()


class LeasshOptionsFlow(OptionsFlow):
    """Options: notifications, nodes, protection and automation management."""

    def __init__(self) -> None:
        super().__init__()
        self._recipe_id: str | None = None
        self._manifest: dict[str, Any] = {}
        self._parameters: dict[str, Any] = {}
        self._display_name = ""
        self._target_key = "node"
        self._editing_instance: str | None = None
        self._created_instance: str | None = None
        self._selected_instance: str | None = None

    def _client(self) -> LeasshClient:
        entry = self.config_entry
        return LeasshClient(
            async_get_clientsession(self.hass),
            normalize_server_url(entry.data[CONF_SERVER_URL]),
            entry.data[CONF_API_TOKEN],
        )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "add_automation",
                "manage_automations",
                "install_bundle",
                "notify",
                "add_node",
                "protection",
            ],
            description_placeholders={
                "server": self.config_entry.data.get(CONF_SERVER_URL, "")
            },
        )

    async def async_step_notify(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            raw = user_input.get(CONF_NOTIFY_TARGETS, "")
            targets = [t.strip() for t in raw.split(",") if t.strip()]
            return self.async_create_entry(
                title="", data={**self.config_entry.options, CONF_NOTIFY_TARGETS: targets}
            )
        current = self.config_entry.options.get(CONF_NOTIFY_TARGETS, [])
        return self.async_show_form(
            step_id="notify",
            data_schema=vol.Schema(
                {vol.Optional(CONF_NOTIFY_TARGETS, default=",".join(current)): str}
            ),
        )

    async def async_step_add_node(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            node = {
                "name": user_input["name"].strip(),
                "host": user_input["host"].strip(),
                "ssh": user_input["ssh"].strip(),
                "os": (user_input.get("os") or "").strip() or None,
                "reclaim_idle_sessions": bool(
                    user_input.get("reclaim_idle_sessions", False)
                ),
            }
            try:
                await self._client().add_node(node)
            except LeasshAuthError:
                errors["base"] = "invalid_auth"
            except LeasshApiError as err:
                errors["base"] = "cannot_add_node"
                _LOGGER.warning("Add node failed: %s", err)
            else:
                await self._client().test_node(node["name"])
                return self.async_create_entry(
                    title="", data={**self.config_entry.options}
                )
        return self.async_show_form(
            step_id="add_node",
            data_schema=vol.Schema(
                {
                    vol.Required("name"): str,
                    vol.Required("host"): str,
                    vol.Required("ssh"): str,
                    vol.Optional("os", default="linux"): vol.In(
                        ["linux", "windows", "macos"]
                    ),
                    vol.Optional("site", default=""): str,
                    vol.Optional("reclaim_idle_sessions", default=False): bool,
                }
            ),
            errors=errors,
        )

    async def async_step_protection(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            profile = user_input["profile"]
            try:
                await self._client().set_protection_profile(
                    profile, user_input.get("confirmation")
                )
            except LeasshApiError as err:
                errors["base"] = "invalid_profile"
                _LOGGER.warning("Protection profile failed: %s", err)
            except LeasshAuthError:
                errors["base"] = "invalid_auth"
            else:
                return self.async_create_entry(
                    title="", data={**self.config_entry.options}
                )
        return self.async_show_form(
            step_id="protection",
            data_schema=vol.Schema(
                {
                    vol.Required("profile", default="trusted_owner"): vol.In(
                        ["trusted_owner", "guardian", "unrestricted", "custom"]
                    ),
                    vol.Optional("confirmation", default=""): str,
                }
            ),
            errors=errors,
        )

    async def _catalog(self) -> dict[str, Any]:
        return await self._client().list_recipes()

    async def _instances(self) -> list[dict[str, Any]]:
        payload = await self._client().recipe_instances()
        return [
            item
            for item in (payload.get("instances") or [])
            if isinstance(item, dict)
        ]

    async def _node_options(self) -> list[str]:
        payload = await self._client().list_nodes()
        return sorted(
            {
                str(node["name"])
                for node in (payload.get("nodes") or [])
                if isinstance(node, dict) and node.get("name")
            }
        )

    async def _site_options(self) -> list[str]:
        payload = await self._client().list_sites()
        return sorted((payload.get("sites") or {}).keys())

    @staticmethod
    def _target_key_for(scope: str) -> str:
        return {"site": "site", "fleet": "fleet"}.get(scope, "node")

    @staticmethod
    def _target_options(
        scope: str, nodes: list[str], sites: list[str]
    ) -> list[SelectOptionDict]:
        if scope == "site":
            return [{"value": name, "label": name} for name in sites]
        if scope == "fleet":
            return [{"value": "local", "label": "Local fleet"}]
        return [{"value": name, "label": name} for name in nodes]

    def _default_parameters(self) -> dict[str, Any]:
        parameters = self._manifest.get("parameters") or {}
        defaults = {
            name: spec["default"]
            for name, spec in parameters.items()
            if isinstance(spec, dict) and "default" in spec
        }
        if self._manifest.get("scope") == "fleet":
            defaults.setdefault("fleet", "local")
        return defaults

    def _field(
        self, name: str, spec: dict[str, Any], default: Any
    ) -> tuple[vol.Marker, Any]:
        kind = str(spec.get("type", "text"))
        required = bool(spec.get("required"))
        description = spec.get("description") or None
        kwargs: dict[str, Any] = {}
        if default is not None:
            kwargs["default"] = default
        if description:
            kwargs["description"] = str(description)
        marker = vol.Required if required else vol.Optional
        if kind == "boolean":
            return marker(name, **kwargs), BooleanSelector()
        if kind == "number":
            config: dict[str, Any] = {"mode": "box", "step": "any"}
            if spec.get("min") is not None:
                config["min"] = float(spec["min"])
            if spec.get("max") is not None:
                config["max"] = float(spec["max"])
            return marker(name, **kwargs), NumberSelector(
                cast(NumberSelectorConfig, config)
            )
        if kind == "select":
            options = [str(option) for option in (spec.get("options") or [])]
            return marker(name, **kwargs), SelectSelector(
                SelectSelectorConfig(options=options)
            )
        if kind == "multi_select":
            options = [str(option) for option in (spec.get("options") or [])]
            return marker(name, **kwargs), SelectSelector(
                SelectSelectorConfig(options=options, multiple=True)
            )
        if kind == "time":
            return marker(name, **kwargs), TimeSelector()
        return marker(name, **kwargs), TextSelector()

    def _settings_schema(self) -> vol.Schema:
        parameters = self._manifest.get("parameters") or {}
        fields: dict[Any, Any] = {}
        for name, spec in parameters.items():
            if name == self._target_key or not isinstance(spec, dict):
                continue
            default = self._parameters.get(name, spec.get("default"))
            marker, selector = self._field(name, spec, default)
            fields[marker] = selector
        return vol.Schema(fields)

    def _summary(self) -> str:
        name = self._manifest.get("name") or self._recipe_id or "Automation"
        target = self._parameters.get(self._target_key, "—")
        description = (self._manifest.get("ui") or {}).get("description") or ""
        return f"{name} → {target}. {description}".strip()

    @staticmethod
    def _preview_text(report: dict[str, Any]) -> str:
        lines: list[str] = []
        summary = report.get("summary")
        if summary:
            lines.append(str(summary))
        if not report.get("eligible"):
            reasons = report.get("reasons") or []
            lines.append("Would not run: " + "; ".join(str(reason) for reason in reasons))
        for step in report.get("steps") or []:
            if not isinstance(step, dict):
                continue
            preview = step.get("preview") or {}
            effect = preview.get("expected_effect") or step.get("action") or "Action"
            after = step.get("after_seconds")
            if after:
                lines.append(f"- {effect} after {after}s")
            else:
                lines.append(f"- {effect}")
        return "\n".join(lines) or "No actions would run."

    async def async_step_add_automation(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        try:
            payload = await self._catalog()
        except (LeasshApiError, LeasshAuthError) as err:
            _LOGGER.warning("Could not load recipe catalog: %s", err)
            return self.async_abort(reason="cannot_load_recipes")
        catalog = payload.get("catalog") or []
        installed = {
            str(item.get("id"))
            for item in (payload.get("installed") or [])
            if isinstance(item, dict) and item.get("id")
        }
        options: list[SelectOptionDict] = [
            {
                "value": str(item["id"]),
                "label": f"{item.get('name') or item['id']} ({item.get('bundle', 'other')})",
            }
            for item in catalog
            if isinstance(item, dict) and item.get("id")
        ]
        if not options:
            return self.async_abort(reason="no_templates")
        if user_input is not None:
            recipe_id = str(user_input["recipe_id"])
            try:
                if recipe_id not in installed:
                    await self._client().install_recipe(recipe_id)
                detail = await self._client().get_recipe(recipe_id)
            except LeasshAuthError:
                errors["base"] = "invalid_auth"
            except LeasshApiError as err:
                errors["base"] = "recipe_install_failed"
                _LOGGER.warning("Recipe setup failed: %s", err)
            else:
                self._recipe_id = recipe_id
                self._manifest = detail.get("manifest") or {}
                self._parameters = self._default_parameters()
                self._display_name = ""
                self._editing_instance = None
                self._target_key = self._target_key_for(
                    str(self._manifest.get("scope", "node"))
                )
                return await self.async_step_add_automation_target()
        return self.async_show_form(
            step_id="add_automation",
            data_schema=vol.Schema(
                {
                    vol.Required("recipe_id"): SelectSelector(
                        SelectSelectorConfig(options=options)
                    )
                }
            ),
            errors=errors,
            description_placeholders={"count": str(len(options))},
        )

    async def async_step_add_automation_target(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not self._manifest:
            return await self.async_step_add_automation()
        scope = str(self._manifest.get("scope", "node"))
        self._target_key = self._target_key_for(scope)
        try:
            nodes = await self._node_options() if scope == "node" else []
            sites = await self._site_options() if scope == "site" else []
        except (LeasshApiError, LeasshAuthError) as err:
            _LOGGER.warning("Could not load targets: %s", err)
            return self.async_abort(reason="cannot_load_targets")
        options = self._target_options(scope, nodes, sites)
        errors: dict[str, str] = {}
        if not options:
            errors["base"] = "no_targets"
        if user_input is not None and not errors:
            self._parameters[self._target_key] = user_input["target"]
            return await self.async_step_add_automation_settings()
        target_default = self._parameters.get(self._target_key)
        target_marker = (
            vol.Required("target", default=target_default)
            if target_default
            else vol.Required("target")
        )
        return self.async_show_form(
            step_id="add_automation_target",
            data_schema=vol.Schema(
                {
                    target_marker: SelectSelector(
                        SelectSelectorConfig(options=options)
                    )
                }
            ),
            errors=errors,
            description_placeholders={
                "recipe": str(self._manifest.get("name") or self._recipe_id or ""),
            },
        )

    async def async_step_add_automation_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not self._manifest:
            return await self.async_step_add_automation()
        if user_input is not None:
            self._parameters.update(user_input)
            return await self.async_step_add_automation_review()
        schema = self._settings_schema()
        if not schema.schema:
            return await self.async_step_add_automation_review()
        return self.async_show_form(
            step_id="add_automation_settings",
            data_schema=schema,
            description_placeholders={
                "recipe": str(self._manifest.get("name") or self._recipe_id or ""),
                "target": str(self._parameters.get(self._target_key, "")),
            },
        )

    async def async_step_add_automation_review(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._display_name = str(user_input.get("display_name") or "")
            if self._editing_instance:
                try:
                    await self._client().update_recipe_instance(
                        self._editing_instance,
                        parameters=self._parameters,
                        display_name=self._display_name,
                    )
                except LeasshAuthError:
                    errors["base"] = "invalid_auth"
                except LeasshApiError as err:
                    errors["base"] = "automation_save_failed"
                    _LOGGER.warning("Automation update failed: %s", err)
                else:
                    return self.async_create_entry(
                        title="", data={**self.config_entry.options}
                    )
            else:
                try:
                    result = await self._client().create_recipe_instance(
                        self._recipe_id or "",
                        self._parameters,
                        False,
                        self._display_name or None,
                    )
                except LeasshAuthError:
                    errors["base"] = "invalid_auth"
                except LeasshApiError as err:
                    errors["base"] = "automation_create_failed"
                    _LOGGER.warning("Automation create failed: %s", err)
                else:
                    instance = result.get("instance") or {}
                    self._created_instance = instance.get("id")
                    return await self.async_step_add_automation_preview()
        return self.async_show_form(
            step_id="add_automation_review",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "display_name", default=self._display_name
                    ): TextSelector()
                }
            ),
            errors=errors,
            description_placeholders={
                "summary": self._summary(),
                "parameters": ", ".join(
                    f"{key}={value}" for key, value in self._parameters.items()
                ),
            },
        )

    async def async_step_add_automation_preview(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not self._created_instance:
            return self.async_abort(reason="automation_not_created")
        report: dict[str, Any] = {}
        error = ""
        try:
            report = await self._client().dry_run_recipe(
                self._created_instance,
                self._parameters.get(self._target_key),
            )
        except (LeasshApiError, LeasshAuthError) as err:
            error = str(err)
            _LOGGER.warning("Automation preview failed: %s", err)
        if user_input is not None:
            if user_input.get("enable_now"):
                try:
                    await self._client().set_recipe_enabled(
                        self._created_instance, True
                    )
                except (LeasshApiError, LeasshAuthError) as err:
                    _LOGGER.warning("Automation enable failed: %s", err)
                    return self.async_show_form(
                        step_id="add_automation_preview",
                        data_schema=vol.Schema(
                            {vol.Optional("enable_now", default=False): BooleanSelector()}
                        ),
                        errors={"base": "automation_enable_failed"},
                        description_placeholders={
                            "summary": self._preview_text(report) if report else error
                        },
                    )
            return self.async_create_entry(
                title="", data={**self.config_entry.options}
            )
        return self.async_show_form(
            step_id="add_automation_preview",
            data_schema=vol.Schema(
                {vol.Optional("enable_now", default=False): BooleanSelector()}
            ),
            description_placeholders={
                "summary": self._preview_text(report) if report else error
            },
        )

    async def async_step_manage_automations(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        try:
            instances = await self._instances()
        except (LeasshApiError, LeasshAuthError) as err:
            _LOGGER.warning("Could not load automations: %s", err)
            return self.async_abort(reason="cannot_load_automations")
        if not instances:
            return self.async_abort(reason="no_automations")
        options: list[SelectOptionDict] = [
            {
                "value": str(item["instance"]["id"]),
                "label": str(
                    item.get("display_name")
                    or item["instance"].get("recipe_id")
                    or item["instance"]["id"]
                ),
            }
            for item in instances
            if isinstance(item.get("instance"), dict)
        ]
        if user_input is not None:
            self._selected_instance = str(user_input["instance"])
            return await self.async_step_manage_automation_action()
        return self.async_show_form(
            step_id="manage_automations",
            data_schema=vol.Schema(
                {
                    vol.Required("instance"): SelectSelector(
                        SelectSelectorConfig(options=options)
                    )
                }
            ),
        )

    async def async_step_manage_automation_action(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not self._selected_instance:
            return await self.async_step_manage_automations()
        return self.async_show_menu(
            step_id="manage_automation_action",
            menu_options=[
                "enable_automation",
                "disable_automation",
                "preview_automation",
                "edit_automation",
                "delete_automation",
                "back_to_automations",
            ],
        )

    async def _set_selected_enabled(self, enabled: bool) -> ConfigFlowResult:
        if not self._selected_instance:
            return await self.async_step_manage_automations()
        try:
            await self._client().set_recipe_enabled(self._selected_instance, enabled)
        except (LeasshApiError, LeasshAuthError) as err:
            _LOGGER.warning("Automation enable change failed: %s", err)
            return self.async_abort(reason="automation_update_failed")
        return self.async_create_entry(title="", data={**self.config_entry.options})

    async def async_step_enable_automation(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._set_selected_enabled(True)

    async def async_step_disable_automation(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._set_selected_enabled(False)

    async def async_step_preview_automation(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not self._selected_instance:
            return await self.async_step_manage_automations()
        report: dict[str, Any] = {}
        error = ""
        try:
            instances = await self._instances()
            record = next(
                (
                    item
                    for item in instances
                    if item.get("instance", {}).get("id")
                    == self._selected_instance
                ),
                None,
            )
            target = (
                (record or {}).get("instance", {}).get("target")
                if record
                else None
            )
            report = await self._client().dry_run_recipe(
                self._selected_instance, target
            )
        except (LeasshApiError, LeasshAuthError) as err:
            error = str(err)
            _LOGGER.warning("Automation preview failed: %s", err)
        if user_input is not None:
            return self.async_create_entry(
                title="", data={**self.config_entry.options}
            )
        return self.async_show_form(
            step_id="preview_automation",
            data_schema=vol.Schema({vol.Optional("done", default=True): BooleanSelector()}),
            description_placeholders={
                "summary": self._preview_text(report) if report else error
            },
        )

    async def async_step_edit_automation(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not self._selected_instance:
            return await self.async_step_manage_automations()
        try:
            instances = await self._instances()
            record = next(
                (
                    item
                    for item in instances
                    if item.get("instance", {}).get("id")
                    == self._selected_instance
                ),
                None,
            )
            if not record:
                return self.async_abort(reason="automation_not_found")
            instance = record["instance"]
            detail = await self._client().get_recipe(str(instance["recipe_id"]))
        except (LeasshApiError, LeasshAuthError) as err:
            _LOGGER.warning("Automation edit load failed: %s", err)
            return self.async_abort(reason="automation_not_found")
        self._editing_instance = self._selected_instance
        self._recipe_id = str(instance["recipe_id"])
        self._manifest = detail.get("manifest") or {}
        self._parameters = dict(instance.get("parameters") or {})
        self._display_name = str(record.get("display_name") or "")
        self._target_key = self._target_key_for(
            str(self._manifest.get("scope", "node"))
        )
        return await self.async_step_add_automation_settings()

    async def async_step_delete_automation(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not self._selected_instance:
            return await self.async_step_manage_automations()
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get("confirm"):
                errors["base"] = "confirm_delete"
            else:
                try:
                    await self._client().delete_recipe_instance(
                        self._selected_instance
                    )
                except (LeasshApiError, LeasshAuthError) as err:
                    _LOGGER.warning("Automation delete failed: %s", err)
                    errors["base"] = "automation_delete_failed"
                else:
                    return self.async_create_entry(
                        title="", data={**self.config_entry.options}
                    )
        return self.async_show_form(
            step_id="delete_automation",
            data_schema=vol.Schema(
                {vol.Required("confirm", default=False): BooleanSelector()}
            ),
            errors=errors,
            description_placeholders={"instance": self._selected_instance},
        )

    async def async_step_back_to_automations(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        self._selected_instance = None
        return await self.async_step_manage_automations()

    async def async_step_install_bundle(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        try:
            payload = await self._catalog()
        except (LeasshApiError, LeasshAuthError) as err:
            _LOGGER.warning("Could not load bundles: %s", err)
            return self.async_abort(reason="cannot_load_recipes")
        bundles = [
            bundle
            for bundle in (payload.get("bundles") or [])
            if isinstance(bundle, dict) and bundle.get("id")
        ]
        options: list[SelectOptionDict] = [
            {
                "value": str(bundle["id"]),
                "label": f"{bundle['id']} ({len(bundle.get('recipes') or [])} recipes)",
            }
            for bundle in bundles
        ]
        if not options:
            return self.async_abort(reason="no_bundles")
        if user_input is not None:
            bundle_id = str(user_input["bundle"])
            bundle = next(
                (item for item in bundles if str(item.get("id")) == bundle_id),
                None,
            )
            if bundle is None:
                errors["base"] = "unknown_bundle"
            else:
                try:
                    for recipe_id in bundle.get("recipes") or []:
                        await self._client().install_recipe(str(recipe_id))
                except LeasshAuthError:
                    errors["base"] = "invalid_auth"
                except LeasshApiError as err:
                    errors["base"] = "recipe_install_failed"
                    _LOGGER.warning("Bundle install failed: %s", err)
                else:
                    return self.async_create_entry(
                        title="", data={**self.config_entry.options}
                    )
        return self.async_show_form(
            step_id="install_bundle",
            data_schema=vol.Schema(
                {
                    vol.Required("bundle"): SelectSelector(
                        SelectSelectorConfig(options=options)
                    )
                }
            ),
            errors=errors,
        )
