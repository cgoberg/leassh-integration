"""Bulk state coordinator: REST polling with ETag caching."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import LeasshApiError, LeasshAuthError, LeasshClient, LeasshConnectionError

_LOGGER = logging.getLogger(__name__)


class LeasshCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch /api/devices every 30s; 304 keeps the previous data.

    The WS session carries commands, events and presence; this poll is the
    bulk-sync path so dashboards and automations have a consistent snapshot
    even after an HA restart that raced an event burst.
    """

    def __init__(self, hass: HomeAssistant, client: LeasshClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="leassh",
            update_interval=timedelta(seconds=30),
        )
        self.client = client
        self._etag: str | None = None

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
        return data
