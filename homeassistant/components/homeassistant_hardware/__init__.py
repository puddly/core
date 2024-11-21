"""The Home Assistant Hardware integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback as hass_callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import SKYCONNECT_DOMAIN
from .util import FirmwareInfo

if TYPE_CHECKING:
    from homeassistant.components.homeassistant_sky_connect import SkyConnectConfigEntry

DOMAIN = "homeassistant_hardware"
CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)


@dataclass(kw_only=True)
class HardwareRuntimeData:
    """Class to hold runtime data for Home Assistant Hardware."""

    device: str
    firmware_info: FirmwareInfo

    _firmware_info_update_callbacks: list[Callable[[FirmwareInfo], None]] = field(
        default_factory=list
    )

    def async_register_firmware_info_update_callback(
        self, callback: Callable[[FirmwareInfo], None]
    ) -> CALLBACK_TYPE:
        """Register a callback for firmware info updates."""
        self._firmware_info_update_callbacks.append(callback)

        @hass_callback
        def _async_remove_callback() -> None:
            if callback not in self._firmware_info_update_callbacks:
                return

            self._firmware_info_update_callbacks.remove(callback)

        return _async_remove_callback

    def async_notify_firmware_info(self, firmware_info: FirmwareInfo) -> None:
        """Notify the hardware integration about firmware information."""
        self.firmware_info = firmware_info

        for callback in self._firmware_info_update_callbacks:
            callback(firmware_info)


@hass_callback
def async_notify_firmware_info(
    hass: HomeAssistant, firmware_info: FirmwareInfo
) -> None:
    """Notify Home Assistant about firmware information."""
    for domain in (SKYCONNECT_DOMAIN,):
        for config_entry in hass.config_entries.async_entries(domain):
            if TYPE_CHECKING:
                config_entry = cast(SkyConnectConfigEntry, config_entry)

            if config_entry.state != ConfigEntryState.LOADED:
                continue

            if config_entry.runtime_data.device == firmware_info.device:
                config_entry.runtime_data.async_notify_firmware_info(firmware_info)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the component."""
    return True
