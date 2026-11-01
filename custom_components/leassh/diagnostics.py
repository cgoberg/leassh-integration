"""Diagnostics: support downloads for troubleshooting (redacted).

Settings → Devices → Leassh Server → "Download diagnostics" — carries
transport/coordinator state, never tokens.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    runtime = hass.data[DOMAIN].get(entry.entry_id)
    if runtime is None:
        return {"error": "runtime not loaded"}

    def redact(value: str) -> str:
        return f"{str(value)[:6]}…" if value else "(none)"

    return {
        "server_url": runtime.client.base_url,
        "api_token": "**REDACTED**",
        "license": {
            "valid": (runtime.coordinator.health or {}).get("license", {}).get("valid"),
            "tier": (runtime.coordinator.health or {}).get("license", {}).get("tier"),
            "max_nodes": (runtime.coordinator.health or {}).get("license", {}).get("max_nodes"),
            "nodes_current": (runtime.coordinator.health or {}).get("license", {}).get("node_limit", {}).get("current"),
            "node_limit_exceeded": (runtime.coordinator.health or {}).get("license", {}).get("node_limit", {}).get("exceeded"),
            "expires_at": (runtime.coordinator.health or {}).get("license", {}).get("expires_at"),
            "degraded": (runtime.coordinator.health or {}).get("degraded"),
        },
        "connection": {
            "connected": runtime.connected,
            "last_frame_age_s": (
                round(runtime.ws.last_frame_monotonic and __import__("time").monotonic() - runtime.ws.last_frame_monotonic, 1)
                if runtime.ws and runtime.ws.last_frame_monotonic
                else None
            ),
        },
        "coordinator": {
            "last_update_success": runtime.coordinator.last_update_success,
            "etag": redact(runtime.coordinator._etag or ""),
            "nodes": len((runtime.coordinator.data or {}).get("nodes", [])),
        },
        "setup": {
            "ok": (getattr(runtime.coordinator, "setup", None) or {}).get("ok"),
            "profile": (getattr(runtime.coordinator, "setup", None) or {}).get("profile"),
            "pending_steps": (getattr(runtime.coordinator, "setup", None) or {}).get("pending_steps"),
            "steps": [
                {"id": step.get("id"), "status": step.get("status")}
                for step in (getattr(runtime.coordinator, "setup", None) or {}).get("steps", [])
            ],
        },
        "recipes": {
            "catalog": len((getattr(runtime.coordinator, "recipes", None) or {}).get("catalog", [])),
            "installed": len((getattr(runtime.coordinator, "recipes", None) or {}).get("installed", [])),
            "instances": [
                {
                    "id": (record.get("instance") or {}).get("id"),
                    "recipe": (record.get("instance") or {}).get("recipe_id"),
                    "enabled": (record.get("instance") or {}).get("enabled"),
                    "status": (record.get("instance") or {}).get("status"),
                    "last_error": (record.get("instance") or {}).get("last_error"),
                }
                for record in getattr(runtime.coordinator, "recipe_instances", [])
            ],
        },
        # Keys, not values: enough to prove a pack's telemetry arrived without
        # turning a support download into a window-title dump.
        "telemetry": [
            {
                "name": node.get("name"),
                "host": node.get("host"),
                "metrics": sorted((node.get("metrics") or {}).keys()),
                "series": {
                    series_id: [
                        str(instance.get("key") or instance.get("index"))
                        for instance in instances or []
                    ]
                    for series_id, instances in (node.get("series") or {}).items()
                },
            }
            for node in (runtime.coordinator.data or {}).get("nodes", [])
            if isinstance(node, dict)
        ],
        "entry": {
            "title": entry.title,
            "options": dict(entry.options),
        },
    }
