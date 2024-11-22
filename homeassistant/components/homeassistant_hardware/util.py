"""Utility functions for Home Assistant SkyConnect integration."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum
import logging
from types import ModuleType
from typing import Self

from universal_silabs_flasher.const import ApplicationType
from universal_silabs_flasher.flasher import Flasher

from homeassistant.components.hassio import AddonError, AddonState, is_hassio
from homeassistant.config_entries import ConfigEntryState
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


class FirmwareType(StrEnum):
    """Firmware type for Silicon Labs radios."""

    THREAD = "thread"
    ZIGBEE = "zigbee"
    MULTIPROTOCOL = "multiprotocol"
    BOOTLOADER = "bootloader"

    @classmethod
    def from_application_type(cls, application_type: ApplicationType | str) -> Self:
        """Convert an ApplicationType to a FirmwareType."""
        if isinstance(application_type, str):
            application_type = ApplicationType(application_type)

        return cls(_APPLICATION_TYPE_TO_FIRMWARE_TYPE[application_type])

    def as_application_type(self) -> ApplicationType:
        """Convert a FirmwareType to an ApplicationType."""
        return _FIRMWARE_TYPE_TO_APPLICATION_TYPE[self]


_APPLICATION_TYPE_TO_FIRMWARE_TYPE = {
    ApplicationType.SPINEL: FirmwareType.THREAD,
    ApplicationType.EZSP: FirmwareType.ZIGBEE,
    ApplicationType.CPC: FirmwareType.MULTIPROTOCOL,
    ApplicationType.GECKO_BOOTLOADER: FirmwareType.BOOTLOADER,
}

_FIRMWARE_TYPE_TO_APPLICATION_TYPE = {
    v: k for k, v in _APPLICATION_TYPE_TO_FIRMWARE_TYPE.items()
}


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


@dataclass(kw_only=True)
class OwningAddon:
    """Owning add-on."""

    slug: str

    def _get_addon_manager(self, hass: HomeAssistant) -> WaitingAddonManager:
        return WaitingAddonManager(
            hass,
            _LOGGER,
            f"Add-on {self.slug}",
            self.slug,
        )

    async def is_running(self, hass: HomeAssistant) -> bool:
        """Check if the add-on is running."""
        addon_manager = self._get_addon_manager(hass)

        try:
            addon_info = await addon_manager.async_get_addon_info()
        except AddonError:
            return False
        else:
            return addon_info.state == AddonState.RUNNING


@dataclass(kw_only=True)
class OwningIntegration:
    """Owning integration."""

    config_entry_id: str

    async def is_running(self, hass: HomeAssistant) -> bool:
        """Check if the integration is running."""
        if (entry := hass.config_entries.async_get_entry(self.config_entry_id)) is None:
            return False

        return entry.state == ConfigEntryState.LOADED


@dataclass(kw_only=True)
class FirmwareInfo:
    """Firmware guess."""

    device: str
    firmware_type: FirmwareType
    firmware_version: str | None

    source: str
    owners: list[OwningAddon | OwningIntegration]


async def guess_hardware_owners(
    hass: HomeAssistant, device_path: str
) -> list[FirmwareInfo]:
    """Guess the firmware info based on installed addons and other integrations."""
    hardware_domains: dict[str, ModuleType] = {}

    try:
        # pylint: disable-next=import-outside-toplevel
        from homeassistant.components.zha import homeassistant_hardware as zha_hardware
    except ImportError:
        pass
    else:
        hardware_domains[ZHA_DOMAIN] = zha_hardware

    try:
        # pylint: disable-next=import-outside-toplevel
        from homeassistant.components.otbr import (
            homeassistant_hardware as otbr_hardware,
        )
    except ImportError:
        pass
    else:
        hardware_domains[OTBR_DOMAIN] = otbr_hardware

    device_guesses: defaultdict[str, list[FirmwareInfo]] = defaultdict(list)

    # Integrations that provide `homeassistant_hardware` will go first
    for domain, hardware in hardware_domains.items():
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
                            firmware_type=FirmwareType.MULTIPROTOCOL,
                            firmware_version=None,
                            source="multiprotocol",
                            owners=[
                                OwningAddon(slug=multipan_addon_manager.addon_slug)
                            ],
                        )
                    )

    return device_guesses.get(device_path, [])


async def guess_firmware_info(hass: HomeAssistant, device_path: str) -> FirmwareInfo:
    """Guess the firmware type based on installed addons and other integrations."""

    hardware_owners = await guess_hardware_owners(hass, device_path)

    # Fall back to EZSP if we have no way to guess
    if not hardware_owners:
        return FirmwareInfo(
            device=device_path,
            firmware_type=FirmwareType.ZIGBEE,
            firmware_version=None,
            source="unknown",
            owners=[],
        )

    # Prioritize guesses that are pulled from a real source
    guesses = [
        (guess, sum([await owner.is_running(hass) for owner in guess.owners]))
        for guess in hardware_owners
    ]
    guesses.sort(key=lambda p: p[1])
    assert guesses

    # Pick the best one. We use a stable sort so ZHA < OTBR < multi-PAN
    return guesses[-1][0]


async def probe_silabs_firmware(
    device: str, *, probe_methods: tuple[FirmwareType, ...] | None = None
) -> FirmwareInfo:
    """Probe the running firmware on a Silabs device."""
    flasher = Flasher(
        device=device,
        **(
            {"probe_methods": [m.as_application_type() for m in probe_methods]}
            if probe_methods
            else {}
        ),
    )

    try:
        await flasher.probe_app_type()
    except RuntimeError as exc:
        if str(exc) == "Failed to probe running application type":
            raise FirmwareProbingFailed from exc

    # The flasher represents the version as a parsed object, not as a string
    version = "".join(str(c.data) for c in flasher.app_version.components)

    return FirmwareInfo(
        device=device,
        firmware_type=FirmwareType.from_application_type(flasher.app_type),
        firmware_version=version,
        owners=[],
        source="probe",
    )
