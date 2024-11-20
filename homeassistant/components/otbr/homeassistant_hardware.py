"""Home Assistant Hardware support."""

from __future__ import annotations

from typing import cast

from homeassistant.components.homeassistant_hardware.util import (
    FirmwareInfo,
    FirmwareType,
)
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant

from .const import DOMAIN


async def get_firmware_info(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> FirmwareInfo | None:
    """Return firmware information for the OpenThread Border Router."""
    device = config_entry.data["device"]
    if device is None:
        return None

    return FirmwareInfo(
        device=device,
        is_running=(config_entry.state == ConfigEntryState.LOADED),
        firmware_type=FirmwareType.THREAD,
        firmware_version=cast(str | None, config_entry.data["firmware_version"]),
        source=DOMAIN,
    )
