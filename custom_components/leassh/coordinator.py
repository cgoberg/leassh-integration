"""Bulk state coordinator: REST polling with ETag caching + lifecycle pruning."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import LeasshApiError, LeasshAuthError, LeasshClient, LeasshConnectionError
from .const import DOMAIN
from .entity import device_identifier, entity_unique_id, node_host

_LOGGER = logging.getLogger(__name__)

# Fixed per-node entities. Must stay in sync with sensor.py / binary_sensor.py:
# this is the expected set the lifecycle prunes against.
FIXED_ENTITY_KEYS: tuple[str, ...] = (
    "activity",
    "cpu_usage",
    "ram_percent",
    "ram_used_mb",
    "ram_total_mb",
    "disk_free_gb",
    "gpu_usage",
    "gpu_temperature",
    "cpu_temperature",
    "vram_used_mb",
    "vram_total_mb",
    "idle_seconds",
    "presence_idle_seconds",
    "load",
    "online",
    "signed_in",
)


class LeasshCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch /api/devices every 30s; 304 keeps the previous data.

    The WS session carries commands, events and presence; this poll is the
    bulk-sync path so dashboards and automations have a consistent snapshot
    even after an HA restart that raced an event burst.
    """

    def __init__(
        self, hass: HomeAssistant, client: LeasshClient, entry: ConfigEntry | None = None
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="leassh",
            update_interval=timedelta(seconds=30),
        )
        self.client = client
        self._entry = entry
        self._etag: str | None = None
        # Unique ids owned by platforms that are not derived from bulk state
        # (the action-catalog buttons). Never pruned.
        self.protected_unique_ids: set[str] = set()
        # Last unauthenticated health snapshot: licence, pack counters and
        # presence pushes. Used by repairs and pack suggestions.
        self.health: dict[str, Any] = {}
        self.suggestions: dict[str, Any] = {}

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            etag, data = await self.client.get_devices(self._etag)
        except (LeasshApiError, LeasshConnectionError) as err:
            raise UpdateFailed(str(err)) from err
        except LeasshAuthError as err:
            raise UpdateFailed(f"token rejected: {err}") from err
        if data is None:  # 304: nothing changed
            assert self.data is not None
            return self.data
        self._etag = etag
        try:
            self.health = await self.client.health()
        except (LeasshApiError, LeasshConnectionError):
            self.health = {}
        try:
            self.suggestions = await self.client.get_pack_suggestions()
        except (LeasshApiError, LeasshConnectionError):
            self.suggestions = {}
        self._reconcile(data)
        return data

    # -- lifecycle ---------------------------------------------------------

    def _reconcile(self, data: dict[str, Any]) -> None:
        """Apply zero-grace removal, gated by reachability.

        Rules (roadmap M1.4):
        - node reachable, instance absent  -> remove immediately
        - node unreachable                 -> keep everything
        - node removed from fleet.yaml     -> remove immediately
        - startup waits for the first full payload (this is called from the
          first successful refresh, never from defaults)
        """
        entry = self._entry or getattr(self, "config_entry", None)
        nodes = data.get("nodes")
        if entry is None or not isinstance(nodes, list):
            return

        present_hosts: set[str] = set()
        for node in nodes:
            host = node_host(node)
            if host:
                present_hosts.add(host)

        dev_reg = dr.async_get(self.hass)
        ent_reg = er.async_get(self.hass)

        # Remove devices for nodes that no longer exist at all.
        for device in list(dr.async_entries_for_config_entry(dev_reg, entry.entry_id)):
            identifiers = {
                value for domain, value in device.identifiers if domain == DOMAIN
            }
            if entry.entry_id in identifiers:
                continue  # the hub
            host = _host_from_identifiers(entry, identifiers)
            if host is None:
                continue  # not ours (or a legacy name-keyed device)
            if host not in present_hosts:
                _LOGGER.info("Node %s left the fleet; removing its device", host)
                for entity in er.async_entries_for_device(
                    ent_reg, device.id, include_disabled_entities=True
                ):
                    ent_reg.async_remove(entity.entity_id)
                dev_reg.async_remove_device(device.id)

        # For reachable nodes, prune entities that are no longer produced.
        for node in nodes:
            host = node_host(node)
            if not host or not node.get("reachable", False):
                continue
            expected = self._expected_unique_ids(entry, host, node)
            expected |= self.protected_unique_ids
            for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
                if (DOMAIN, device_identifier(entry, host)) not in device.identifiers:
                    continue
                for entity in er.async_entries_for_device(
                    ent_reg, device.id, include_disabled_entities=True
                ):
                    if entity.unique_id and entity.unique_id.startswith(
                        f"{entry.entry_id}_{host}_"
                    ):
                        if entity.unique_id not in expected:
                            _LOGGER.info(
                                "Pruning %s (no longer present on reachable node %s)",
                                entity.entity_id,
                                host,
                            )
                            ent_reg.async_remove(entity.entity_id)
                break

    def _expected_unique_ids(
        self, entry: ConfigEntry, host: str, node: dict[str, Any]
    ) -> set[str]:
        expected = {entity_unique_id(entry, host, key) for key in FIXED_ENTITY_KEYS}
        for metric_id in (node.get("metrics") or {}):
            expected.add(entity_unique_id(entry, host, metric_id))
        for series_id, instances in (node.get("series") or {}).items():
            for instance in instances or []:
                key = str(instance.get("key") or instance.get("index"))
                for field in (instance.get("values") or {}):
                    expected.add(
                        entity_unique_id(entry, host, f"{series_id}_{field}", key)
                    )
        return expected


def _host_from_identifiers(entry: ConfigEntry, identifiers: set[str]) -> str | None:
    prefix = f"{entry.entry_id}_"
    for value in identifiers:
        if value.startswith(prefix):
            return value[len(prefix):]
    return None
