"""Shared device-info and naming helpers: one HA device per fleet node.

Naming policy (professional surface):
- Device names are humanized node names ("GPU Primary 131"), registry
  identifiers keep the raw node name for stability across renames.
- The hub device is "Leassh Server"; its entities use has_entity_name so
  HA renders "Leassh Server Connected" — no duplicated prefixes.
"""

from __future__ import annotations

import inspect

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN


def humanize(node_name: str) -> str:
    """'gpu-primary-131' -> 'GPU Primary 131'; leaves real words alone."""
    words = node_name.replace("_", "-").split("-")
    out = []
    for w in words:
        if w.isdigit():
            out.append(w)
        elif w.lower() in {"gpu", "cpu", "ram", "ssd", "nas", "pc", "ha", "htpc"}:
            out.append(w.upper())
        else:
            out.append(w[:1].upper() + w[1:] if w else w)
    return " ".join(out)


def title_case(verb: str) -> str:
    """'sign_out_session' -> 'Sign out session' (title-case first word only)."""
    words = verb.replace("_node", "").split("_")
    if not words:
        return verb
    return words[0][:1].upper() + words[0][1:] + " " + " ".join(words[1:])


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
        model="Fleet server",
        configuration_url=server_url,
    )


def node_device(
    hass: HomeAssistant, entry: ConfigEntry, node_name: str, host: str | None = None
) -> DeviceInfo:
    info = DeviceInfo(
        # Identifiers keep the RAW node name: stable even if the display
        # name changes, and greppable against fleet.yaml.
        identifiers={(DOMAIN, f"{entry.entry_id}_{node_name}")},
        name=humanize(node_name),
        manufacturer="Leassh",
        model="Fleet node",
    )
    # Link via the hub's registry id. The hub device is created by the
    # connected sensor; when it is not registered yet the link is simply
    # omitted and the device attaches on a later entity update.
    hub = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    if hub is not None:
        if _REGISTRY_ACCEPTS_VIA_DEVICE_ID:
            info["via_device_id"] = hub.id
        else:
            info["via_device"] = (DOMAIN, entry.entry_id)
    if host:
        info["configuration_url"] = f"ssh://{host}"
    return info
