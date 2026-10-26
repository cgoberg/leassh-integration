"""Shared device-info helpers: one HA device per fleet node."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN


def hub_device(entry: ConfigEntry, server_url: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Leassh Server",
        manufacturer="Leassh",
        model="fleet server",
        configuration_url=server_url,
    )


def node_device(entry: ConfigEntry, node_name: str, host: str | None = None) -> DeviceInfo:
    info = DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_{node_name}")},
        name=node_name,
        manufacturer="Leassh",
        model="fleet node",
        via_device=(DOMAIN, entry.entry_id),
    )
    if host:
        info["configuration_url"] = f"ssh://{host}"
    return info
