"""Home Assistant Hardware support."""

from __future__ import annotations

from universal_silabs_flasher.const import ApplicationType
from zha.application.const import RadioType
from zigpy.config import CONF_DEVICE, CONF_DEVICE_PATH

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_RADIO_TYPE
from .helpers import get_zha_gateway


def get_radio_serial_port(hass: HomeAssistant, config_entry: ConfigEntry) -> str | None:
    """Return the serial port of the coordinator."""
    return config_entry.data.get(CONF_DEVICE, {}).get(CONF_DEVICE_PATH, None)


def get_radio_model(hass: HomeAssistant, config_entry: ConfigEntry) -> str | None:
    """Return the model of the coordinator."""
    assert config_entry is not None
    return get_zha_gateway(hass).state.node_info.model


def get_radio_manufacturer(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> str | None:
    """Return the manufacturer of the coordinator."""
    assert config_entry is not None
    return get_zha_gateway(hass).state.node_info.manufacturer


def get_radio_firmware_version(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> str | None:
    """Return the firmware version of the coordinator."""
    assert config_entry is not None
    return get_zha_gateway(hass).state.node_info.version


def get_radio_firmware_type(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> ApplicationType | None:
    """Return the firmware version of the coordinator."""
    radio_type = RadioType[config_entry.data[CONF_RADIO_TYPE]]

    # We only support EZSP firmware for now
    if radio_type != RadioType.EZSP:
        return None

    return ApplicationType.EZSP
