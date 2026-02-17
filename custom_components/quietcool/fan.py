"""Fan platform for QuietCool integration."""

from __future__ import annotations

import math
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    MODE_IDLE,
    MODE_RUN,
    PRESET_MODE_HIGH,
    PRESET_MODE_LOW,
    PRESET_MODE_MEDIUM,
    PRESET_TO_SPEED,
    SPEED_CLOSE,
    SPEED_HIGH,
    SPEED_LOW,
    SPEED_MEDIUM,
    SPEED_TO_PRESET,
)
from .coordinator import QuietCoolCoordinator

# Number of discrete speed steps
SPEED_COUNT = 3

ORDERED_NAMED_FAN_SPEEDS = [SPEED_LOW, SPEED_MEDIUM, SPEED_HIGH]


def percentage_to_speed(percentage: int) -> str:
    """Convert percentage to fan speed string."""
    if percentage == 0:
        return SPEED_CLOSE
    index = math.ceil(percentage / (100 / SPEED_COUNT)) - 1
    index = max(0, min(index, SPEED_COUNT - 1))
    return ORDERED_NAMED_FAN_SPEEDS[index]


def speed_to_percentage(speed: str) -> int:
    """Convert fan speed string to percentage."""
    if speed == SPEED_CLOSE:
        return 0
    try:
        index = ORDERED_NAMED_FAN_SPEEDS.index(speed)
    except ValueError:
        return 0
    return int((index + 1) * (100 / SPEED_COUNT))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up QuietCool fan entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: QuietCoolCoordinator = data["coordinator"]
    device_info: dict[str, Any] = data.get("device_info", {})

    async_add_entities([QuietCoolFan(coordinator, entry, device_info)])


class QuietCoolFan(CoordinatorEntity[QuietCoolCoordinator], FanEntity):
    """Representation of a QuietCool fan."""

    _attr_has_entity_name = True
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | FanEntityFeature.PRESET_MODE
    )
    _attr_speed_count = SPEED_COUNT
    _attr_preset_modes = [PRESET_MODE_LOW, PRESET_MODE_MEDIUM, PRESET_MODE_HIGH]

    def __init__(
        self,
        coordinator: QuietCoolCoordinator,
        entry: ConfigEntry,
        device_info: dict[str, Any],
    ) -> None:
        """Initialize the fan entity."""
        super().__init__(coordinator)
        address = entry.data["address"]
        self._attr_unique_id = f"quietcool_{address.replace(':', '')}"
        self._attr_name = entry.data.get("name", "QuietCool Fan")
        self._attr_device_info = {
            "identifiers": {(DOMAIN, address)},
            "name": self._attr_name,
            "manufacturer": "QuietCool",
            "model": device_info.get("Model", entry.data.get("name", "Unknown")),
            "sw_version": device_info.get("Version"),
        }
        self._update_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_state()
        self.async_write_ha_state()

    def _update_state(self) -> None:
        """Update local state from coordinator data."""
        data = self.coordinator.data
        if not data:
            return

        mode = data.get("Mode", MODE_IDLE)
        speed = data.get("Range", data.get("Speed", SPEED_CLOSE))

        self._attr_is_on = mode != MODE_IDLE and speed != SPEED_CLOSE

        if self._attr_is_on:
            self._attr_percentage = speed_to_percentage(speed)
            self._attr_preset_mode = SPEED_TO_PRESET.get(speed)
        else:
            self._attr_percentage = 0
            self._attr_preset_mode = None

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn on the fan."""
        speed = None
        if preset_mode is not None:
            speed = PRESET_TO_SPEED.get(preset_mode)
        elif percentage is not None:
            speed = percentage_to_speed(percentage)

        await self.coordinator.client.turn_on(speed)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the fan."""
        await self.coordinator.client.turn_off()
        await self.coordinator.async_request_refresh()

    async def async_set_percentage(self, percentage: int) -> None:
        """Set fan speed percentage."""
        if percentage == 0:
            await self.async_turn_off()
            return
        speed = percentage_to_speed(percentage)
        await self.coordinator.client.set_speed(speed)
        await self.coordinator.client.set_mode(MODE_RUN)
        await self.coordinator.async_request_refresh()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the preset mode."""
        speed = PRESET_TO_SPEED.get(preset_mode)
        if speed is None:
            return
        await self.coordinator.client.set_speed(speed)
        await self.coordinator.client.set_mode(MODE_RUN)
        await self.coordinator.async_request_refresh()
