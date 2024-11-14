"""SkyConnect firmware update entity."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any, cast

from universal_silabs_flasher.const import ApplicationType
from universal_silabs_flasher.flasher import Flasher

from homeassistant.components.homeassistant_hardware.util import probe_silabs_firmware
from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityDescription,
    UpdateEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import FirmwareUpdateCoordinator
from .models import FirmwareManifest, FirmwareMetadata

_LOGGER = logging.getLogger(__name__)


@dataclass(kw_only=True, frozen=True)
class SkyConnectUpdateEntityDescription(UpdateEntityDescription):
    """Describes SkyConnect firmware update entity."""

    version_parser: Callable[[str], str]
    fw_type: str
    version_key: str
    expected_firmware_type: ApplicationType


ZIGBEE_UPDATE_DESCRIPTION = SkyConnectUpdateEntityDescription(
    key="firmware",
    display_precision=0,
    device_class=UpdateDeviceClass.FIRMWARE,
    entity_category=EntityCategory.DIAGNOSTIC,
    version_parser=lambda fw: fw.split(" ", 1)[0],
    fw_type="skyconnect_zigbee_ncp",
    version_key="ezsp_version",
    expected_firmware_type=ApplicationType.EZSP,
)


THREAD_UPDATE_DESCRIPTION = SkyConnectUpdateEntityDescription(
    key="firmware",
    display_precision=0,
    device_class=UpdateDeviceClass.FIRMWARE,
    entity_category=EntityCategory.DIAGNOSTIC,
    version_parser=lambda fw: fw,
    fw_type="skyconnect_openthread_rcp",
    version_key="ot_rcp_version",
    expected_firmware_type=ApplicationType.SPINEL,
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the firmware update config entry."""

    session = async_get_clientsession(hass)

    if config_entry.data["firmware"] == "ezsp":
        entity_description = ZIGBEE_UPDATE_DESCRIPTION
    elif config_entry.data["firmware"] == "spinel":
        entity_description = THREAD_UPDATE_DESCRIPTION
    else:
        return

    async_add_entities(
        [
            FirmwareUpdateEntity(
                config_entry=config_entry,
                entity_description=entity_description,
                update_coordinator=FirmwareUpdateCoordinator(hass, session),
            )
        ]
    )


class FirmwareUpdateEntity(CoordinatorEntity[FirmwareUpdateCoordinator], UpdateEntity):
    """SkyConnect firmware update entity."""

    entity_description: SkyConnectUpdateEntityDescription

    _attr_supported_features = (
        UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS
    )
    _attr_has_entity_name = True

    def __init__(
        self,
        config_entry: ConfigEntry,
        entity_description: SkyConnectUpdateEntityDescription,
        update_coordinator: FirmwareUpdateCoordinator,
    ) -> None:
        """Initialize the SkyConnect firmware update entity."""
        super().__init__(update_coordinator)

        self.entity_description = entity_description
        self._config_entry = config_entry
        self._attr_unique_id = (
            f"{config_entry.data['serial_number']}_{self.entity_description.key}"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._config_entry.data["serial_number"])},
            manufacturer=self._config_entry.data["manufacturer"],
            model=self._config_entry.data["product"],
            sw_version=self._config_entry.data["firmware_version"],
        )

        self._latest_manifest: FirmwareManifest | None = None
        self._latest_firmware: FirmwareMetadata | None = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._latest_manifest = self.coordinator.data

        self._latest_firmware = next(
            f
            for f in self._latest_manifest.firmwares
            if f.filename.startswith(self.entity_description.fw_type)
        )

        self._attr_latest_version = cast(
            str, self._latest_firmware.metadata[self.entity_description.version_key]
        )
        self._attr_release_summary = self._latest_firmware.release_notes
        self.async_write_ha_state()

    @property
    def installed_version(self) -> str | None:
        """Version installed and in use."""
        version = self._config_entry.data["firmware_version"]
        if version is None:
            return None

        return self.entity_description.version_parser(version)

    def _update_progress(self, offset: int, total_size: int) -> None:
        """Handle update progress."""
        self._attr_update_percentage = (offset * 100) / total_size
        self.async_write_ha_state()

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        """Install an update."""
        assert self._latest_firmware is not None

        async with self.coordinator.session.get(
            self._latest_firmware.url, raise_for_status=True
        ) as fw_rsp:
            fw_data = await fw_rsp.read()

        # At this point, we will have a valid firmware image that can be flasher
        fw_image = await self.hass.async_add_executor_job(
            self._latest_firmware.parse_firmware, fw_data
        )

        assert fw_image is not None

        flasher = Flasher(
            device=self._config_entry.data["device"],
            probe_methods=(
                ApplicationType.GECKO_BOOTLOADER,
                ApplicationType.EZSP,
                ApplicationType.SPINEL,
                ApplicationType.CPC,
            ),
        )

        try:
            # Enter the bootloader with indeterminate progress
            self._attr_in_progress = True
            self._attr_update_percentage = None
            self.async_write_ha_state()
            await flasher.enter_bootloader()

            # Flash the firmware, with progress
            await flasher.flash_firmware(
                fw_image, progress_callback=self._update_progress
            )

            # Probe the running application type with indeterminate progress
            self._attr_update_percentage = None
            self.async_write_ha_state()
            firmware_info = await probe_silabs_firmware(
                self._config_entry.data["device"],
                probe_methods=(self.entity_description.expected_firmware_type,),
            )

            # Update the config entry
            self.hass.config_entries.async_update_entry(
                self._config_entry,
                data={
                    **self._config_entry.data,
                    "firmware": firmware_info.firmware_type,
                    "firmware_version": firmware_info.firmware_version,
                },
            )
        finally:
            self._attr_in_progress = False
            self.async_write_ha_state()
