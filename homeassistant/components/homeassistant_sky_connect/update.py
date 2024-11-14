"""SkyConnect firmware update entity."""

from __future__ import annotations

import logging
from typing import Any, cast

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
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


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the firmware update config entry."""

    session = async_get_clientsession(hass)

    async_add_entities(
        [
            FirmwareUpdateEntity(
                config_entry,
                update_coordinator=FirmwareUpdateCoordinator(hass, session),
            )
        ]
    )


class FirmwareUpdateEntity(CoordinatorEntity[FirmwareUpdateCoordinator], UpdateEntity):
    """SkyConnect firmware update entity."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS
    )

    def __init__(
        self,
        config_entry: ConfigEntry,
        update_coordinator: FirmwareUpdateCoordinator,
    ) -> None:
        """Initialize the SkyConnect firmware update entity."""
        super().__init__(update_coordinator)

        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.data['serial']}_firmware_update"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._config_entry.data["serial"])},
            manufacturer=self._config_entry.data["manufacturer"],
            model=self._config_entry.data["product"],
        )

        self._latest_manifest: FirmwareManifest | None = None
        self._latest_firmware: FirmwareMetadata | None = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._latest_manifest = self.coordinator.data

        if self._config_entry.data["firmware_type"] != "ezsp":
            return

        self._latest_firmware = next(
            f
            for f in self._latest_manifest.firmwares
            if f.filename.startswith("skyconnect_zigbee_ncp")
        )

        self._attr_latest_version = cast(
            str, self._latest_firmware.metadata["ezsp_version"]
        )
        self._attr_release_summary = self._latest_firmware.release_notes
        self.async_write_ha_state()

    @property
    def installed_version(self) -> str | None:
        """Version installed and in use."""

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
        raise RuntimeError("WIP")
