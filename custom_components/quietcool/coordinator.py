"""DataUpdateCoordinator for QuietCool fans."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from bleak.exc import BleakError

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .ble_client import BLEConnectionError, QuietCoolBLEClient
from .const import UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)


class QuietCoolCoordinator(DataUpdateCoordinator[dict[str, Any] | None]):
    """Coordinator to poll QuietCool fan state via BLE."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: QuietCoolBLEClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="QuietCool",
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )
        self.client = client

    async def _async_update_data(self) -> dict[str, Any] | None:
        """Fetch current fan state via GetWorkState."""
        try:
            result = await self.client.get_work_state()
            if result is None:
                raise UpdateFailed("No response from fan")
            return result
        except (BleakError, BLEConnectionError) as err:
            raise UpdateFailed(f"Error communicating with fan: {err}") from err
