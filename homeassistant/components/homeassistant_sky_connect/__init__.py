"""The Home Assistant SkyConnect integration."""

from __future__ import annotations

import logging
import pathlib

from homeassistant.components import usb
from homeassistant.components.homeassistant_hardware.const import (
    EVENT_FIRMWARE_INFO_LOADED,
)
from homeassistant.components.homeassistant_hardware.util import (
    EventFirmwareInfoLoaded,
    guess_firmware_type,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up a Home Assistant SkyConnect config entry."""
    device = pathlib.Path(config_entry.data["device"])

    if not await hass.async_add_executor_job(device.exists):
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="device_not_plugged_in"
        )

    @callback
    def async_port_event(
        added_devices: set[usb.USBDevice], removed_devices: set[usb.USBDevice]
    ) -> None:
        _LOGGER.debug("Added devices: %r", added_devices)
        _LOGGER.debug("Removed devices: %r", removed_devices)

        for device in removed_devices:
            if device.device == config_entry.data["device"]:
                hass.config_entries.async_schedule_reload(config_entry.entry_id)
                break

    config_entry.async_on_unload(
        usb.async_register_port_event_callback(hass, async_port_event)
    )

    @callback
    def event_state_change_listener(event: Event[EventFirmwareInfoLoaded]) -> None:
        _LOGGER.debug("Firmware info event received: %s", event)

        firmware_info = event.data["firmware_info"]

        hass.config_entries.async_update_entry(
            config_entry,
            data={
                **config_entry.data,
                "firmware": firmware_info.firmware_type,
                "firmware_version": firmware_info.firmware_version,
            },
        )

    config_entry.async_on_unload(
        hass.bus.async_listen(EVENT_FIRMWARE_INFO_LOADED, event_state_change_listener)
    )

    await hass.config_entries.async_forward_entry_setups(config_entry, ["update"])

    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    await hass.config_entries.async_forward_entry_unload(config_entry, "update")
    return True


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old entry."""

    _LOGGER.debug(
        "Migrating from version %s:%s", config_entry.version, config_entry.minor_version
    )

    if config_entry.version == 1:
        if config_entry.minor_version == 1:
            # Add-on startup with type service get started before Core, always (e.g. the
            # Multi-Protocol add-on). Probing the firmware would interfere with the add-on,
            # so we can't safely probe here. Instead, we must make an educated guess!
            firmware_info = await guess_firmware_type(hass, config_entry.data["device"])

            new_data = {**config_entry.data}
            new_data["firmware"] = firmware_info.firmware_type.value

            # Copy `description` to `product`
            new_data["product"] = new_data["description"]

            hass.config_entries.async_update_entry(
                config_entry,
                data=new_data,
                version=1,
                minor_version=2,
            )

        if config_entry.minor_version == 2:
            firmware_info = await guess_firmware_type(hass, config_entry.data["device"])

            new_data = {**config_entry.data}
            new_data["firmware_version"] = firmware_info.firmware_version

            hass.config_entries.async_update_entry(
                config_entry,
                data=new_data,
                version=1,
                minor_version=3,
            )

        _LOGGER.debug(
            "Migration to version %s.%s successful",
            config_entry.version,
            config_entry.minor_version,
        )

        return True

    # This means the user has downgraded from a future version
    return False
