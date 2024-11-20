"""Utility functions for Home Assistant SkyConnect integration."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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

    THREAD = "spinel"
    ZIGBEE = "ezsp"
    MULTIPROTOCOL = "cpc"
    BOOTLOADER = "bootloader"

    @classmethod
    def from_application_type(cls, application_type: ApplicationType) -> Self:
        """Convert an ApplicationType to a FirmwareType."""
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

    @asynccontextmanager
    async def temporarily_stop(self, hass: HomeAssistant) -> AsyncIterator[None]:
        """Temporarily stop the add-on, restarting it after completion."""
        addon_manager = self._get_addon_manager(hass)

        try:
            addon_info = await addon_manager.async_get_addon_info()
        except AddonError:
            yield
            return

        if addon_info.state != AddonState.RUNNING:
            yield
            return

        try:
            await addon_manager.async_stop_addon()
            await addon_manager.async_wait_until_addon_state(AddonState.NOT_RUNNING)
            yield
        finally:
            await addon_manager.async_start_addon_waiting()


@dataclass(kw_only=True)
class OwningIntegration:
    """Owning integration."""

    config_entry_id: str

    async def is_running(self, hass: HomeAssistant) -> bool:
        """Check if the integration is running."""
        if (entry := hass.config_entries.async_get_entry(self.config_entry_id)) is None:
            return False

        return entry.state == ConfigEntryState.LOADED

    @asynccontextmanager
    async def temporarily_stop(self, hass: HomeAssistant) -> AsyncIterator[None]:
        """Temporarily stop the integration, restarting it after completion."""
        if (entry := hass.config_entries.async_get_entry(self.config_entry_id)) is None:
            yield
            return

        if entry.state != ConfigEntryState.LOADED:
            yield
            return

        await hass.config_entries.async_unload(entry.entry_id)

        try:
            yield
        finally:
            await hass.config_entries.async_setup(entry.entry_id)


@dataclass(kw_only=True)
class FirmwareInfo:
    """Firmware guess."""

    device: str
    firmware_type: FirmwareType
    firmware_version: str | None

    source: str
    owners: list[OwningAddon | OwningIntegration]


async def guess_firmware_type(hass: HomeAssistant, device_path: str) -> FirmwareInfo:
    """Guess the firmware type based on installed addons and other integrations."""

    otbr_hardware: ModuleType | None
    zha_hardware: ModuleType | None

    try:
        # pylint: disable-next=import-outside-toplevel
        from homeassistant.components.otbr import (
            homeassistant_hardware as otbr_hardware,
        )
    except ImportError:
        otbr_hardware = None

    try:
        # pylint: disable-next=import-outside-toplevel
        from homeassistant.components.zha import homeassistant_hardware as zha_hardware
    except ImportError:
        zha_hardware = None

    device_guesses: defaultdict[str | None, list[FirmwareInfo]] = defaultdict(list)

    for domain, hardware in (
        (ZHA_DOMAIN, zha_hardware),
        (OTBR_DOMAIN, otbr_hardware),
    ):
        if hardware is None:
            continue

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

    # Fall back to EZSP if we can't guess the firmware type
    if device_path not in device_guesses:
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
        for guess in device_guesses[device_path]
    ]
    guesses.sort(key=lambda p: p[1])
    assert guesses

    # Pick the best one
    return guesses[-1][0]


async def probe_silabs_firmware(
    device: str, *, probe_methods: tuple[FirmwareType, ...] | None = None
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

    # The flasher represents the version as a parsed object, not as a string
    version = "".join(str(c.data) for c in flasher.app_version.components)

    return FirmwareInfo(
        device=device,
        firmware_type=FirmwareType.from_application_type(flasher.app_type),
        firmware_version=version,
        owners=[],
        source="probe",
    )
