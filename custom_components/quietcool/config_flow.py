"""Config flow for QuietCool integration."""

from __future__ import annotations

import logging
import uuid
from typing import Any

import voluptuous as vol

from bleak import BleakClient
from bleak.exc import BleakError

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .ble_client import BLEConnectionError, QuietCoolBLEClient
from .const import BLE_DEVICE_NAMES, DOMAIN

_LOGGER = logging.getLogger(__name__)


class QuietCoolConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for QuietCool."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._address: str | None = None
        self._name: str | None = None
        self._phone_id: str | None = None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a Bluetooth discovery."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        self._discovery_info = discovery_info
        self._address = discovery_info.address
        self._name = discovery_info.name or "QuietCool Fan"

        self.context["title_placeholders"] = {"name": self._name}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm Bluetooth discovery, then proceed to pairing."""
        assert self._discovery_info is not None

        if user_input is not None:
            return await self.async_step_pair()

        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={"name": self._name or "QuietCool Fan"},
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual setup — show discovered devices."""
        if user_input is not None:
            address = user_input["address"]
            await self.async_set_unique_id(address)
            self._abort_if_unique_id_configured()

            # Find the discovery info for the selected device
            for info in async_discovered_service_info(self.hass):
                if info.address == address:
                    self._discovery_info = info
                    self._address = info.address
                    self._name = info.name or "QuietCool Fan"
                    return await self.async_step_pair()

            return self.async_abort(reason="no_devices_found")

        # Build list of discovered QuietCool devices
        devices: dict[str, str] = {}
        for info in async_discovered_service_info(self.hass):
            if info.name and any(
                info.name.startswith(prefix) for prefix in BLE_DEVICE_NAMES
            ):
                devices[info.address] = f"{info.name} ({info.address})"

        if not devices:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("address"): vol.In(devices),
                }
            ),
        )

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle pairing with the fan.

        The user must press the pairing button on the fan, then confirm here.
        We connect, send Login, and if that fails, send Pair.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            # Generate a unique phone ID for this HA instance
            self._phone_id = str(uuid.uuid4()).replace("-", "")[:16]

            client: QuietCoolBLEClient | None = None
            try:
                assert self._discovery_info is not None
                client = QuietCoolBLEClient(self._discovery_info.device)
                await client.connect()

                # Try Login first (already paired)
                result = await client.login(self._phone_id)
                if result and result.get("Flag") == "success":
                    _LOGGER.debug("Login succeeded, already paired")
                    return self._create_entry()

                # Login failed — try Pair (new pairing)
                result = await client.pair(self._phone_id)
                if result and result.get("Flag") == "success":
                    _LOGGER.debug("Pairing succeeded")
                    return self._create_entry()

                # Both failed
                _LOGGER.warning("Pairing response: %s", result)
                errors["base"] = "pairing_failed"

            except (BleakError, BLEConnectionError, TimeoutError) as err:
                _LOGGER.error("Connection failed during pairing: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during pairing")
                errors["base"] = "unknown"
            finally:
                if client:
                    await client.disconnect()

        return self.async_show_form(
            step_id="pair",
            description_placeholders={"name": self._name or "QuietCool Fan"},
            errors=errors,
        )

    def _create_entry(self) -> ConfigFlowResult:
        """Create the config entry after successful pairing."""
        return self.async_create_entry(
            title=self._name or "QuietCool Fan",
            data={
                "address": self._address,
                "name": self._name,
                "phone_id": self._phone_id,
            },
        )
