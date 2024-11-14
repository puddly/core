"""Utility functions for Home Assistant SkyConnect integration."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import logging

from universal_silabs_flasher.const import ApplicationType
from universal_silabs_flasher.flasher import Flasher

from homeassistant.components.hassio import AddonError, AddonState, is_hassio
from homeassistant.components.otbr import homeassistant_hardware as otbr_hardware
from homeassistant.components.zha import homeassistant_hardware as zha_hardware
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.singleton import singleton

from .const import (
    OTBR_ADDON_MANAGER_DATA,
    OTBR_ADDON_NAME,
    OTBR_ADDON_SLUG,
    OTBR_DOMAIN,
    ZHA_DOMAIN,
    ZIGBEE_FLASHER_ADDON_MANAGER_DATA,
    ZIGBEE_FLASHER_ADDON_NAME,
    ZIGBEE_FLASHER_ADDON_SLUG,
)
from .silabs_multiprotocol_addon import (
    WaitingAddonManager,
    get_multiprotocol_addon_manager,
)

_LOGGER = logging.getLogger(__name__)


class FirmwareProbingFailed(Exception):
    """Firmware probing failed."""


@singleton(OTBR_ADDON_MANAGER_DATA)
@callback
def get_otbr_addon_manager(hass: HomeAssistant) -> WaitingAddonManager:
    """Get the OTBR add-on manager."""
    return WaitingAddonManager(
        hass,
        _LOGGER,
        OTBR_ADDON_NAME,
        OTBR_ADDON_SLUG,
    )


@singleton(ZIGBEE_FLASHER_ADDON_MANAGER_DATA)
@callback
def get_zigbee_flasher_addon_manager(hass: HomeAssistant) -> WaitingAddonManager:
    """Get the flasher add-on manager."""
    return WaitingAddonManager(
        hass,
        _LOGGER,
        ZIGBEE_FLASHER_ADDON_NAME,
        ZIGBEE_FLASHER_ADDON_SLUG,
    )


@dataclass(slots=True, kw_only=True)
class FirmwareInfo:
    """Firmware guess."""

    device: str
    is_running: bool
    firmware_type: ApplicationType
    firmware_version: str | None
    source: str


async def guess_firmware_type(hass: HomeAssistant, device_path: str) -> FirmwareInfo:
    """Guess the firmware type based on installed addons and other integrations."""
    device_guesses: defaultdict[str | None, list[FirmwareInfo]] = defaultdict(list)

    for domain, hardware in (
        (ZHA_DOMAIN, zha_hardware),
        (OTBR_DOMAIN, otbr_hardware),
    ):
        for config_entry in hass.config_entries.async_entries(domain):
            firmware_info = await hardware.get_firmware_info(hass, config_entry)
            device_guesses[firmware_info.device].append(firmware_info)

    if is_hassio(hass):
        multipan_addon_manager = await get_multiprotocol_addon_manager(hass)

        try:
            multipan_addon_info = await multipan_addon_manager.async_get_addon_info()
        except AddonError:
            pass
        else:
            if multipan_addon_info.state != AddonState.NOT_INSTALLED:
                multipan_path = multipan_addon_info.options.get("device")

                if multipan_path is not None:
                    device_guesses[multipan_path].append(
                        FirmwareInfo(
                            device=multipan_path,
                            is_running=(
                                multipan_addon_info.state == AddonState.RUNNING
                            ),
                            firmware_type=ApplicationType.CPC,
                            firmware_version=None,
                            source="multiprotocol",
                        )
                    )

    # Fall back to EZSP if we can't guess the firmware type
    if device_path not in device_guesses:
        return FirmwareInfo(
            device=device_path,
            is_running=False,
            firmware_type=ApplicationType.EZSP,
            firmware_version=None,
            source="unknown",
        )

    # Prioritizes guesses that were pulled from a running addon or integration but keep
    # the sort order we defined above
    guesses = sorted(
        device_guesses[device_path],
        key=lambda guess: guess.is_running,
    )

    assert guesses

    return guesses[-1]


async def probe_silabs_firmware(
    device: str, *, probe_methods: ApplicationType | None = None
) -> FirmwareInfo:
    """Probe the running firmware on a Silabs device."""
    flasher = Flasher(
        device=device,
        **({"probe_methods": probe_methods} if probe_methods else {}),
    )

    try:
        await flasher.probe_app_type()
    except RuntimeError as exc:
        if str(exc) == "Failed to probe running application type":
            raise FirmwareProbingFailed from exc

    return FirmwareInfo(
        device=device,
        is_running=True,
        firmware_type=flasher.app_type,
        firmware_version=str(flasher.app_version),
        source="probe",
    )
