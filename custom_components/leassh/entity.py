"""Shared device-info helpers: one HA device per fleet node."""

from __future__ import annotations

import inspect

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN

# `via_device_id` replaced `via_device` in newer HA (via_device stops
# working in 2027.8) but does not exist at our 2024.6 floor. Feature-detect
# rather than version-guess: the registry signature is the arbiter.
_REGISTRY_ACCEPTS_VIA_DEVICE_ID = "via_device_id" in inspect.signature(
    dr.DeviceRegistry.async_get_or_create
).parameters


def hub_device(entry: ConfigEntry, server_url: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Leassh Server",
        manufacturer="Leassh",
        model="fleet server",
        configuration_url=server_url,
    )


def node_device(
    hass: HomeAssistant, entry: ConfigEntry, node_name: str, host: str | None = None
) -> DeviceInfo:
    info = DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_{node_name}")},
        name=node_name,
        manufacturer="Leassh",
        model="fleet node",
    )
    # The hub device is created by the connected sensor; when it is not
    # registered yet the link is simply omitted and the device attaches on
    # a later entity update.
    hub = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    if hub is not None:
        if _REGISTRY_ACCEPTS_VIA_DEVICE_ID:
            info["via_device_id"] = hub.id
        else:
            info["via_device"] = (DOMAIN, entry.entry_id)
    if host:
        info["configuration_url"] = f"ssh://{host}"
    return info
