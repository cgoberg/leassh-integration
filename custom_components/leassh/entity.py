"""Shared device-info and naming helpers: one HA device per fleet node.

Identity policy (roadmap v2.1, M1.3):
- Device identity is the node's **host**, never its display name.
- Entity identity is `host` + metric (+ series key for multi-instance data).
- Display names are cosmetic: renaming a node in `fleet.yaml` keeps the same
  device, entities and history.

Naming policy:
- `has_entity_name` makes HA render "<Device> <Entity>", e.g.
  "GPU Primary 131 CPU usage", so translations resolve and object ids are not
  deduplicated into `_2` suffixes.
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
    """'sign_out_session' -> 'Sign out session'; no trailing space."""
    words = [w for w in verb.replace("_node", "").split("_") if w]
    if not words:
        return verb
    return words[0][:1].upper() + words[0][1:] + (
        " " + " ".join(words[1:]) if len(words) > 1 else ""
    )


def node_host(node: dict) -> str | None:
    """Identity for a node payload. Never the display name."""
    host = node.get("host")
    if isinstance(host, str) and host:
        return host
    return None


def device_identifier(entry: ConfigEntry, host: str) -> str:
    return f"{entry.entry_id}_{host}"


def entity_unique_id(
    entry: ConfigEntry, host: str, metric: str, series_key: str | None = None
) -> str:
    """Stable identity: entry + host + metric (+ series key)."""
    base = f"{entry.entry_id}_{host}_{metric}"
    if series_key:
        return f"{base}_{series_key}"
    return base


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
    hass: HomeAssistant, entry: ConfigEntry, node: dict
) -> DeviceInfo | None:
    """Device for a node payload; None when the payload carries no host.

    A node without a host cannot be identified stably, so it gets no device
    rather than a name-keyed one that would silently move history on rename.
    """
    host = node_host(node)
    if host is None:
        return None
    info = DeviceInfo(
        identifiers={(DOMAIN, device_identifier(entry, host))},
        name=humanize(str(node.get("name") or host)),
        manufacturer="Leassh",
        model="Fleet node",
        serial_number=host,
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
    info["configuration_url"] = f"ssh://{host}"
    return info


def find_node(coordinator, host: str) -> dict | None:
    for node in (coordinator.data or {}).get("nodes", []):
        if node_host(node) == host:
            return node
    return None
