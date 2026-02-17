"""Config flow for QuietCool integration."""

from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Any

import voluptuous as vol

from bleak.exc import BleakError

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_ble_device_from_address,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .ble_client import BLEConnectionError, QuietCoolBLEClient
from .const import BLE_DEVICE_NAMES, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Max pairing attempts (matches app's pairCount limit of 5)
MAX_PAIR_ATTEMPTS = 5


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

            for info in async_discovered_service_info(self.hass):
                if info.address == address:
                    self._discovery_info = info
                    self._address = info.address
                    self._name = info.name or "QuietCool Fan"
                    return await self.async_step_pair()

            return self.async_abort(reason="no_devices_found")

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

        Follows the app's exact sequence:
        1. Connect via BLE
        2. Send Login with PhoneID
        3. If Login Result=Success → done
        4. If Login Result=Fail and PairState=No → send Pair, then Login again
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            # Generate a 16-char hex PhoneID (matches Android's android_id format)
            self._phone_id = secrets.token_hex(8)

            client: QuietCoolBLEClient | None = None
            try:
                assert self._discovery_info is not None
                assert self._address is not None

                # Get a fresh BLEDevice — the one from discovery may be stale
                ble_device = async_ble_device_from_address(
                    self.hass, self._address, connectable=True
                )
                if ble_device is None:
                    _LOGGER.error(
                        "Could not find BLE device %s", self._address
                    )
                    errors["base"] = "cannot_connect"
                    return self.async_show_form(
                        step_id="pair",
                        description_placeholders={
                            "name": self._name or "QuietCool Fan"
                        },
                        errors=errors,
                    )

                client = QuietCoolBLEClient(ble_device)
                await client.connect()

                # Brief delay to let notifications stabilize
                await asyncio.sleep(0.5)

                # Step 1: Try Login first (matches app behavior)
                _LOGGER.debug("Sending Login with PhoneID: %s", self._phone_id)
                result = await client.login(self._phone_id)
                _LOGGER.debug("Login response: %s", result)

                if result and result.get("Result") == "Success":
                    _LOGGER.debug("Login succeeded, already paired")
                    return self._create_entry()

                if result and result.get("Result") == "Fail":
                    pair_state = result.get("PairState", "")

                    if pair_state == "Yes":
                        # Already paired to another device, can't pair again
                        _LOGGER.warning("Fan is already paired to another device")
                        errors["base"] = "already_paired"
                    elif pair_state == "No":
                        # Not paired — send Pair command, then Login again
                        _LOGGER.debug("Not paired, sending Pair command")
                        pair_result = await client.pair(self._phone_id)
                        _LOGGER.debug("Pair response: %s", pair_result)

                        if pair_result and pair_result.get("Result") == "Success":
                            # Pair succeeded — send Login to confirm
                            login_result = await client.login(self._phone_id)
                            _LOGGER.debug("Post-pair Login response: %s", login_result)

                            if login_result and login_result.get("Result") == "Success":
                                _LOGGER.debug("Pairing and login succeeded")
                                return self._create_entry()

                            errors["base"] = "pairing_failed"
                        elif pair_result and pair_result.get("Result") == "Beyond":
                            _LOGGER.warning("Fan device memory full, cannot pair")
                            errors["base"] = "device_full"
                        else:
                            errors["base"] = "pairing_failed"
                    else:
                        errors["base"] = "pairing_failed"
                else:
                    # No response or unexpected format
                    _LOGGER.warning("Unexpected login response: %s", result)
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
