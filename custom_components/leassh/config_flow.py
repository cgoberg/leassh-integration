"""Config flow: server_url + api_token, mDNS discovery, connection test."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
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
    """Options: notifications, node onboarding, protection and recipes."""

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
            menu_options=["notify", "add_node", "protection", "recipes"],
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

    async def async_step_recipes(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            recipe_id = (user_input.get("recipe_id") or "").strip()
            if recipe_id:
                try:
                    await self._client().install_recipe(recipe_id)
                except LeasshApiError as err:
                    errors["base"] = "recipe_install_failed"
                    _LOGGER.warning("Recipe install failed: %s", err)
                except LeasshAuthError:
                    errors["base"] = "invalid_auth"
                else:
                    await self._client().list_recipes()
                    return self.async_create_entry(
                        title="", data={**self.config_entry.options}
                    )
            else:
                return self.async_create_entry(
                    title="", data={**self.config_entry.options}
                )
        return self.async_show_form(
            step_id="recipes",
            data_schema=vol.Schema(
                {vol.Optional("recipe_id", default=""): str}
            ),
            errors=errors,
        )
