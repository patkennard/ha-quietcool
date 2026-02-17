"""The QuietCool integration."""

from __future__ import annotations

import logging

from homeassistant.components.bluetooth import async_ble_device_from_address
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .ble_client import QuietCoolBLEClient
from .const import DOMAIN
from .coordinator import QuietCoolCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.FAN]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up QuietCool from a config entry."""
    address = entry.data["address"]
    phone_id = entry.data.get("phone_id", "")

    ble_device = async_ble_device_from_address(hass, address, connectable=True)
    if ble_device is None:
        _LOGGER.error("Could not find QuietCool device %s", address)
        return False

    client = QuietCoolBLEClient(ble_device)
    await client.connect()

    # Authenticate with the stored phone ID from pairing
    if phone_id:
        result = await client.login(phone_id)
        if not result or result.get("Result") != "Success":
            _LOGGER.warning(
                "Login failed for %s — fan may need to be re-paired", address
            )

    # Try to get device info for model/version metadata
    device_info = await client.get_fan_info() or {}

    coordinator = QuietCoolCoordinator(hass, client)

    # Fetch initial state
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
        "device_info": device_info,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry_data = hass.data[DOMAIN].pop(entry.entry_id)
        client: QuietCoolBLEClient = entry_data["client"]
        await client.disconnect()
    return unload_ok
