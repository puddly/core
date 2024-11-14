"""Home Assistant Hardware support."""

from __future__ import annotations

from typing import cast

from universal_silabs_flasher.const import ApplicationType
from yarl import URL

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .util import get_otbr_addon_device


async def get_radio_serial_port(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> str | None:
    """Return the serial port of the OTBR addon."""
    addon_slug = URL(config_entry.data["url"]).host
    if addon_slug is None:
        return None

    return await get_otbr_addon_device(hass, addon_slug)


async def get_radio_model(hass: HomeAssistant, config_entry: ConfigEntry) -> str | None:
    """Return the model of the OTBR."""
    return None


async def get_radio_manufacturer(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> str | None:
    """Return the manufacturer of the OTBR."""
    return None


async def get_radio_firmware_version(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> str | None:
    """Return the firmware version of the OTBR."""
    return cast(str | None, config_entry.data["firmware"])


async def get_radio_firmware_type(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> ApplicationType | None:
    """Return the firmware version of the OTBR."""
    return ApplicationType.SPINEL
