"""Constants for the Homeassistant Hardware integration."""

import logging
from typing import Final

LOGGER = logging.getLogger(__package__)

ZHA_DOMAIN = "zha"
OTBR_DOMAIN = "otbr"

OTBR_ADDON_NAME = "OpenThread Border Router"
OTBR_ADDON_MANAGER_DATA = "openthread_border_router"
OTBR_ADDON_SLUG = "core_openthread_border_router"

ZIGBEE_FLASHER_ADDON_NAME = "Silicon Labs Flasher"
ZIGBEE_FLASHER_ADDON_MANAGER_DATA = "silabs_flasher"
ZIGBEE_FLASHER_ADDON_SLUG = "core_silabs_flasher"

SILABS_MULTIPROTOCOL_ADDON_SLUG = "core_silabs_multiprotocol"
SILABS_FLASHER_ADDON_SLUG = "core_silabs_flasher"

EVENT_FIRMWARE_INFO_LOADED: Final = "firmware_info_loaded"
