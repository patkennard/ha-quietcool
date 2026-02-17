"""BLE client for communicating with QuietCool fans."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from bleak import BleakClient
from bleak.exc import BleakError

from .const import (
    CLASSICAL_CHAR_UUID,
    CLASSICAL_SERVICE_UUID,
    COMMAND_TIMEOUT,
    MAX_RETRIES,
    MESH_PROXY_DATA_IN_UUID,
    MESH_PROXY_DATA_OUT_UUID,
    MESH_PROXY_SERVICE_UUID,
    MODE_IDLE,
    MODE_RUN,
    SPEED_CLOSE,
    SPEED_HIGH,
)

_LOGGER = logging.getLogger(__name__)


class QuietCoolBLEClient:
    """BLE client for QuietCool fan communication."""

    def __init__(self, ble_device: Any) -> None:
        """Initialize the BLE client.

        Args:
            ble_device: A BLEDevice from HA's bluetooth integration or bleak scanner.
        """
        self._ble_device = ble_device
        self._client: BleakClient | None = None
        self._lock = asyncio.Lock()
        self._response_event = asyncio.Event()
        self._response_data: str = ""
        self._receive_buffer = bytearray()
        self._is_classical: bool | None = None
        self._write_char: str | None = None
        self._notify_char: str | None = None

    @property
    def address(self) -> str:
        """Return the BLE address."""
        return self._ble_device.address

    @property
    def is_connected(self) -> bool:
        """Return True if connected."""
        return self._client is not None and self._client.is_connected

    async def connect(self) -> None:
        """Connect to the fan."""
        if self.is_connected:
            return

        self._client = BleakClient(
            self._ble_device,
            disconnected_callback=self._on_disconnect,
        )
        await self._client.connect()

        # Log all discovered services for debugging
        services = self._client.services
        for svc in services:
            _LOGGER.debug(
                "Service %s: %s",
                svc.uuid,
                [str(c.uuid) for c in svc.characteristics],
            )

        # Determine mode: classical vs mesh
        classical_svc = services.get_service(str(CLASSICAL_SERVICE_UUID))
        mesh_svc = services.get_service(str(MESH_PROXY_SERVICE_UUID))

        if classical_svc:
            self._is_classical = True
            self._write_char = str(CLASSICAL_CHAR_UUID)
            self._notify_char = str(CLASSICAL_CHAR_UUID)
            _LOGGER.debug("Connected in classical mode to %s", self.address)
        elif mesh_svc:
            self._is_classical = False
            self._write_char = str(MESH_PROXY_DATA_IN_UUID)
            self._notify_char = str(MESH_PROXY_DATA_OUT_UUID)
            _LOGGER.debug("Connected in mesh mode to %s", self.address)
        else:
            await self._client.disconnect()
            raise BLEConnectionError(
                f"No recognized QuietCool service found on {self.address}"
            )

        # Enable notifications for responses
        await self._client.start_notify(self._notify_char, self._notification_handler)

    async def disconnect(self) -> None:
        """Disconnect from the fan."""
        if self._client and self._client.is_connected:
            try:
                await self._client.disconnect()
            except BleakError:
                pass
        self._client = None

    def _on_disconnect(self, client: BleakClient) -> None:
        """Handle unexpected disconnection."""
        _LOGGER.debug("Disconnected from %s", self.address)
        self._client = None

    def _notification_handler(self, _sender: int, data: bytearray) -> None:
        """Handle incoming BLE notifications."""
        self._receive_buffer.extend(data)

        # Try to parse as complete JSON
        try:
            text = self._receive_buffer.decode("utf-8")
            # Check if we have a complete JSON object
            if text.strip().startswith("{") and text.strip().endswith("}"):
                self._response_data = text.strip()
                self._receive_buffer.clear()
                self._response_event.set()
        except UnicodeDecodeError:
            pass

    async def _ensure_connected(self) -> None:
        """Ensure we're connected, reconnecting if necessary."""
        if not self.is_connected:
            await self.connect()

    async def send_command(self, command: dict[str, Any]) -> dict[str, Any] | None:
        """Send a JSON command and optionally wait for a response.

        Args:
            command: The command dict to send as JSON.

        Returns:
            Parsed JSON response dict, or None if no response received.
        """
        async with self._lock:
            await self._ensure_connected()

            json_str = json.dumps(command)
            data = json_str.encode("utf-8")

            self._response_event.clear()
            self._response_data = ""
            self._receive_buffer.clear()

            last_error: Exception | None = None
            for attempt in range(MAX_RETRIES):
                try:
                    await self._client.write_gatt_char(
                        self._write_char,
                        data,
                        response=False,  # WRITE_NO_RESPONSE per the app
                    )

                    # Wait for response
                    try:
                        await asyncio.wait_for(
                            self._response_event.wait(),
                            timeout=COMMAND_TIMEOUT,
                        )
                        return json.loads(self._response_data)
                    except asyncio.TimeoutError:
                        _LOGGER.debug(
                            "No response for %s (attempt %d/%d)",
                            command.get("Api", "?"),
                            attempt + 1,
                            MAX_RETRIES,
                        )
                        last_error = TimeoutError(
                            f"No response for {command.get('Api')}"
                        )
                except BleakError as err:
                    last_error = err
                    _LOGGER.debug(
                        "BLE write failed (attempt %d/%d): %s",
                        attempt + 1,
                        MAX_RETRIES,
                        err,
                    )
                    # Try reconnecting
                    await self.disconnect()
                    try:
                        await self.connect()
                    except (BleakError, BLEConnectionError):
                        pass

            _LOGGER.warning(
                "Command %s failed after %d attempts",
                command.get("Api", "?"),
                MAX_RETRIES,
            )
            return None

    async def get_work_state(self) -> dict[str, Any] | None:
        """Get the current fan state."""
        return await self.send_command({"Api": "GetWorkState"})

    async def set_speed(self, speed: str) -> dict[str, Any] | None:
        """Set fan speed: CLOSE, LOW, MEDIUM, HIGH."""
        return await self.send_command({"Api": "SetSpeed", "Speed": speed})

    async def set_mode(self, mode: str) -> dict[str, Any] | None:
        """Set fan mode: Idle, Run, Timer, TH."""
        return await self.send_command({"Api": "SetMode", "Mode": mode})

    async def turn_on(self, speed: str | None = None) -> None:
        """Turn on the fan."""
        await self.set_mode(MODE_RUN)
        if speed and speed != SPEED_CLOSE:
            await self.set_speed(speed)
        else:
            # Default to HIGH if no speed specified
            await self.set_speed(SPEED_HIGH)

    async def turn_off(self) -> None:
        """Turn off the fan."""
        await self.set_mode(MODE_IDLE)

    async def get_fan_info(self) -> dict[str, Any] | None:
        """Get fan name, model, serial number."""
        return await self.send_command({"Api": "GetFanInfo"})

    async def get_version(self) -> dict[str, Any] | None:
        """Get firmware version."""
        return await self.send_command({"Api": "GetVersion"})

    async def login(self, phone_id: str) -> dict[str, Any] | None:
        """Login/authenticate with the fan."""
        return await self.send_command({"Api": "Login", "PhoneID": phone_id})

    async def pair(self, phone_id: str) -> dict[str, Any] | None:
        """Pair this phone/HA instance with the fan."""
        return await self.send_command({"Api": "Pair", "PhoneID": phone_id})

    async def enter_pair_mode(self) -> dict[str, Any] | None:
        """Tell the fan to enter pairing mode."""
        return await self.send_command({"Api": "PairMode"})

    def update_ble_device(self, ble_device: Any) -> None:
        """Update the BLE device reference (for address changes after reconnect)."""
        self._ble_device = ble_device
        if self._client:
            self._client = None  # Force reconnect with new device


class BLEConnectionError(Exception):
    """Raised when BLE connection fails."""


class BLECommandError(Exception):
    """Raised when a BLE command fails."""
