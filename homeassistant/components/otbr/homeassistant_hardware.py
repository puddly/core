"""Home Assistant Hardware support."""

from __future__ import annotations

from typing import cast

import voluptuous as vol
from yarl import URL

from homeassistant.components.hassio import is_hassio, valid_addon
from homeassistant.components.homeassistant_hardware.util import (
    FirmwareInfo,
    FirmwareType,
    OwningAddon,
    OwningIntegration,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN


async def get_firmware_info(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> FirmwareInfo | None:
    """Return firmware information for the OpenThread Border Router."""
    device = config_entry.data["device"]
    if device is None:
        return None

    owners: list[OwningIntegration | OwningAddon] = [
        OwningIntegration(config_entry_id=config_entry.entry_id)
    ]

    if is_hassio(hass) and (host := URL(config_entry.data["url"]).host) is not None:
        try:
            valid_addon(host)
        except vol.Invalid:
            pass
        else:
            owners.append(OwningAddon(slug=host))

    return FirmwareInfo(
        device=device,
        firmware_type=FirmwareType.THREAD,
        firmware_version=cast(str | None, config_entry.data["firmware_version"]),
        source=DOMAIN,
        owners=owners,
    )
