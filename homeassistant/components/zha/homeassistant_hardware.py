"""Home Assistant Hardware support."""

from __future__ import annotations

from zha.application.const import RadioType
from zigpy.config import CONF_DEVICE, CONF_DEVICE_PATH

from homeassistant.components.homeassistant_hardware.util import (
    FirmwareInfo,
    FirmwareType,
    OwningIntegration,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_RADIO_TYPE, DOMAIN
from .helpers import get_zha_gateway


async def get_firmware_info(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> FirmwareInfo | None:
    """Return firmware information for the ZHA instance."""
    try:
        gateway = get_zha_gateway(hass)
    except ValueError:
        firmware_version = None
    else:
        firmware_version = gateway.state.node_info.version

    radio_type = RadioType[config_entry.data[CONF_RADIO_TYPE]]

    # We only support EZSP firmware for now
    if radio_type != RadioType.ezsp:
        return None

    device = config_entry.data.get(CONF_DEVICE, {}).get(CONF_DEVICE_PATH, None)
    if device is None:
        return None

    return FirmwareInfo(
        device=device,
        firmware_type=FirmwareType.ZIGBEE,
        firmware_version=firmware_version,
        source=DOMAIN,
        owners=[OwningIntegration(config_entry_id=config_entry.entry_id)],
    )
