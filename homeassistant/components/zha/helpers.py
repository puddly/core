"""Helper functions for the ZHA integration."""

import asyncio
import collections
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import asynccontextmanager
import copy
import dataclasses
import enum
from functools import partial
import itertools
import logging
import queue
import re
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, NamedTuple, cast, override
from zoneinfo import ZoneInfo

import probatio
from zha.application import Platform as ZhaPlatform
from zha.application.const import (
    ATTR_DEVICE_IEEE,
    ATTR_TYPE,
    ATTR_UNIQUE_ID,
    CLUSTER_TYPE_IN,
    CLUSTER_TYPE_OUT,
    CONF_DEFAULT_CONSIDER_UNAVAILABLE_BATTERY,
    CONF_DEFAULT_CONSIDER_UNAVAILABLE_MAINS,
    UNKNOWN_MANUFACTURER,
    UNKNOWN_MODEL,
    ZHA_CLUSTER_BIND_EVENT,
    ZHA_CLUSTER_CONFIGURE_REPORTING_EVENT,
    ZHA_DEVICE_CONFIGURED_EVENT,
    ZHA_DEVICE_ENTITY_ADDED_EVENT,
    ZHA_DEVICE_ENTITY_REMOVED_EVENT,
    ZHA_DEVICE_UPDATED_EVENT,
    ZHA_EVENT,
    ZHA_GW_MSG,
    ZHA_GW_MSG_DEVICE_FULL_INIT,
    ZHA_GW_MSG_DEVICE_INFO,
    ZHA_GW_MSG_DEVICE_JOINED,
    ZHA_GW_MSG_DEVICE_REMOVED,
    ZHA_GW_MSG_GROUP_ADDED,
    ZHA_GW_MSG_GROUP_INFO,
    ZHA_GW_MSG_GROUP_MEMBER_ADDED,
    ZHA_GW_MSG_GROUP_MEMBER_REMOVED,
    ZHA_GW_MSG_GROUP_REMOVED,
    ZHA_GW_MSG_RAW_INIT,
    RadioType,
)
from zha.application.gateway import (
    ConnectionLostEvent,
    DeviceFullInitEvent,
    DeviceJoinedEvent,
    DeviceLeftEvent,
    DeviceRemovedEvent,
    GroupEvent,
    RawDeviceInitializedEvent,
)
from zha.application.helpers import (
    AlarmControlPanelOptions,
    CoordinatorConfiguration,
    DeviceOptions,
    DeviceOverridesConfiguration,
    LightOptions,
    QuirksConfiguration,
    ZHAConfiguration,
    ZHAData,
    convert_install_code,
    qr_to_install_code,
)
from zha.client.client import Client
from zha.client.device import Device
from zha.client.entity import Entity
from zha.client.gateway import Gateway
from zha.client.group import Group
from zha.event import EventBase
from zha.exceptions import ZHAException
from zha.zigbee.device import (
    ClusterBindEvent,
    ClusterConfigureReportingEvent,
    DeviceConfiguredEvent,
    DeviceEntityAddedEvent,
    DeviceEntityRemovedEvent,
    DeviceFirmwareInfoUpdatedEvent,
    DeviceInfo,
    ExtendedDeviceInfo,
    ZHAEvent,
    ZigbeeDevice,
)
from zha.zigbee.group import GroupInfo
import zhaquirks
import zhaquirks.legacy
from zigpy.config import (
    CONF_DATABASE,
    CONF_DEVICE,
    CONF_DEVICE_PATH,
    CONF_NWK,
    CONF_NWK_CHANNEL,
)
import zigpy.exceptions
from zigpy.state import NetworkInfo
import zigpy.types
from zigpy.types import EUI64
from zigpy.zcl.foundation import CommandSchema

from homeassistant import __path__ as HOMEASSISTANT_PATH
from homeassistant.components.homeassistant_hardware.silabs_multiprotocol_addon import (
    is_multiprotocol_url,
)
from homeassistant.components.system_log import LogEntry
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_AREA_ID,
    ATTR_DEVICE_ID,
    ATTR_ENTITY_ID,
    ATTR_MANUFACTURER,
    ATTR_MODEL,
    ATTR_NAME,
    Platform,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.dispatcher import async_dispatcher_send, dispatcher_send
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, VolDictType
from homeassistant.util.logging import HomeAssistantQueueHandler

from .const import (
    ATTR_ACTIVE_COORDINATOR,
    ATTR_AVAILABLE,
    ATTR_DEVICE_TYPE,
    ATTR_DURATION,
    ATTR_ENDPOINT_NAMES,
    ATTR_EXPOSES_FEATURES,
    ATTR_IEEE,
    ATTR_INSTALL_CODE,
    ATTR_LAST_SEEN,
    ATTR_LQI,
    ATTR_MANUFACTURER_CODE,
    ATTR_NEIGHBORS,
    ATTR_NWK,
    ATTR_POWER_SOURCE,
    ATTR_QR_CODE,
    ATTR_QUIRK_APPLIED,
    ATTR_QUIRK_CLASS,
    ATTR_ROUTES,
    ATTR_RSSI,
    ATTR_SIGNATURE,
    ATTR_SOURCE_IEEE,
    CONF_ALARM_ARM_REQUIRES_CODE,
    CONF_ALARM_FAILED_TRIES,
    CONF_ALARM_MASTER_CODE,
    CONF_BAUDRATE,
    CONF_CONSIDER_UNAVAILABLE_BATTERY,
    CONF_CONSIDER_UNAVAILABLE_MAINS,
    CONF_CUSTOM_QUIRKS_PATH,
    CONF_DEFAULT_LIGHT_TRANSITION,
    CONF_DEVICE_CONFIG,
    CONF_ENABLE_ENHANCED_LIGHT_TRANSITION,
    CONF_ENABLE_IDENTIFY_ON_JOIN,
    CONF_ENABLE_LIGHT_TRANSITIONING_FLAG,
    CONF_ENABLE_MAINS_STARTUP_POLLING,
    CONF_ENABLE_QUIRKS,
    CONF_FLOW_CONTROL,
    CONF_GROUP_MEMBERS_ASSUME_STATE,
    CONF_RADIO_TYPE,
    CONF_ZIGPY,
    CUSTOM_CONFIGURATION,
    DATA_ZHA,
    DEFAULT_DATABASE_NAME,
    DEVICE_PAIRING_STATUS,
    DOMAIN,
    SIGNAL_DEVICE_RECONFIGURE_EVENT,
    ZHA_ALARM_OPTIONS,
    ZHA_OPTIONS,
)

if TYPE_CHECKING:
    from logging import Filter, LogRecord

    from .entity import ZHAEntity
    from .update import ZHAFirmwareUpdateCoordinator

    type _LogFilterType = Filter | Callable[[LogRecord], bool]

_LOGGER = logging.getLogger(__name__)

DEBUG_COMP_BELLOWS = "bellows"
DEBUG_COMP_ZHA = "homeassistant.components.zha"
DEBUG_LIB_ZHA = "zha"
DEBUG_COMP_ZIGPY = "zigpy"
DEBUG_COMP_ZIGPY_ZNP = "zigpy_znp"
DEBUG_COMP_ZIGPY_DECONZ = "zigpy_deconz"
DEBUG_COMP_ZIGPY_XBEE = "zigpy_xbee"
DEBUG_COMP_ZIGPY_ZIGATE = "zigpy_zigate"
DEBUG_LEVEL_CURRENT = "current"
DEBUG_LEVEL_ORIGINAL = "original"
DEBUG_LEVELS = {
    DEBUG_COMP_BELLOWS: logging.DEBUG,
    DEBUG_COMP_ZHA: logging.DEBUG,
    DEBUG_COMP_ZIGPY: logging.DEBUG,
    DEBUG_COMP_ZIGPY_ZNP: logging.DEBUG,
    DEBUG_COMP_ZIGPY_DECONZ: logging.DEBUG,
    DEBUG_COMP_ZIGPY_XBEE: logging.DEBUG,
    DEBUG_COMP_ZIGPY_ZIGATE: logging.DEBUG,
    DEBUG_LIB_ZHA: logging.DEBUG,
}
DEBUG_RELAY_LOGGERS = [DEBUG_COMP_ZHA, DEBUG_COMP_ZIGPY, DEBUG_LIB_ZHA]
ZHA_GW_MSG_LOG_ENTRY = "log_entry"
ZHA_GW_MSG_LOG_OUTPUT = "log_output"
SIGNAL_REMOVE_ENTITIES = "zha_remove_entities"
SIGNAL_REMOVE_ENTITY = "zha_remove_entity"
GROUP_ENTITY_DOMAINS = [Platform.LIGHT, Platform.SWITCH, Platform.FAN]
SIGNAL_ADD_ENTITIES = "zha_add_entities"
ENTITIES = "entities"

RX_ON_WHEN_IDLE = "rx_on_when_idle"
RELATIONSHIP = "relationship"
EXTENDED_PAN_ID = "extended_pan_id"
PERMIT_JOINING = "permit_joining"
DEPTH = "depth"

DEST_NWK = "dest_nwk"
ROUTE_STATUS = "route_status"
MEMORY_CONSTRAINED = "memory_constrained"
MANY_TO_ONE = "many_to_one"
ROUTE_RECORD_REQUIRED = "route_record_required"
NEXT_HOP = "next_hop"

USER_GIVEN_NAME = "user_given_name"
DEVICE_REG_ID = "device_reg_id"


class GroupEntityReference(NamedTuple):
    """Reference to a group entity."""

    name: str | None
    original_name: str | None
    entity_id: str


class EntityReference(NamedTuple):
    """Describes an entity reference."""

    ha_entity_id: str
    entity_data: EntityData
    remove_future: asyncio.Future[Any]


@dataclasses.dataclass(kw_only=True, slots=True)
class EntityData:
    """A ZHA client entity and the device its HA entity is attached to."""

    entity: Entity
    # The coordinator for group entities
    device: Device
    group: Group | None = dataclasses.field(default=None)

    @property
    def is_group_entity(self) -> bool:
        """Return if this is a group entity."""
        return self.group is not None


def register_entity_reference(
    hass: HomeAssistant,
    ha_entity_id: str,
    entity_data: EntityData,
    remove_future: asyncio.Future[Any],
) -> None:
    """Record the creation of a hass entity associated with ieee."""
    get_zha_data(hass).entity_refs[entity_data.device.ieee].append(
        EntityReference(
            ha_entity_id=ha_entity_id,
            entity_data=entity_data,
            remove_future=remove_future,
        )
    )


def update_entity_reference(
    hass: HomeAssistant, entity: ZHAEntity, old_entity_id: str
) -> None:
    """Update the entity_id of the entity reference for a renamed entity."""
    entity_refs = get_zha_data(hass).entity_refs
    ieee = entity.entity_data.device.ieee
    if ieee not in entity_refs:
        return
    entity_refs[ieee] = [
        e._replace(ha_entity_id=entity.entity_id)
        if e.ha_entity_id == old_entity_id
        else e
        for e in entity_refs[ieee]
    ]


def remove_entity_reference(hass: HomeAssistant, entity: ZHAEntity) -> None:
    """Remove entity reference for given entity_id if found."""
    entity_refs = get_zha_data(hass).entity_refs
    ieee = entity.entity_data.device.ieee
    if ieee not in entity_refs:
        return
    entity_refs[ieee] = [
        e for e in entity_refs[ieee] if e.ha_entity_id != entity.entity_id
    ]


def get_entity_reference(hass: HomeAssistant, entity_id: str) -> EntityReference | None:
    """Return entity reference for given entity_id if found."""
    for entity_reference in itertools.chain.from_iterable(
        get_zha_data(hass).entity_refs.values()
    ):
        if entity_id == entity_reference.ha_entity_id:
            return entity_reference
    return None


def device_info_dict(info: DeviceInfo) -> dict[str, Any]:
    """Return a device description for device."""
    ieee = str(info.ieee)
    return {
        ATTR_IEEE: ieee,
        ATTR_NWK: info.nwk,
        ATTR_MANUFACTURER: info.manufacturer,
        ATTR_MODEL: info.model,
        ATTR_NAME: info.name or ieee,
        ATTR_QUIRK_APPLIED: info.quirk_applied,
        ATTR_QUIRK_CLASS: info.quirk_class,
        ATTR_EXPOSES_FEATURES: info.exposes_features,
        ATTR_MANUFACTURER_CODE: info.manufacturer_code,
        ATTR_POWER_SOURCE: info.power_source,
        ATTR_LQI: info.lqi,
        ATTR_RSSI: info.rssi,
        ATTR_LAST_SEEN: info.last_seen,
        ATTR_AVAILABLE: info.available,
        ATTR_DEVICE_TYPE: info.device_type,
        ATTR_SIGNATURE: info.signature,
    }


def zha_device_info(hass: HomeAssistant, info: ExtendedDeviceInfo) -> dict[str, Any]:
    """Get ZHA device information from a device snapshot."""
    zha_data = get_zha_data(hass)
    device_info: dict[str, Any] = device_info_dict(info)
    device_info[ATTR_ACTIVE_COORDINATOR] = info.active_coordinator
    device_info[ENTITIES] = [
        {
            ATTR_ENTITY_ID: entity_ref.ha_entity_id,
            ATTR_NAME: device_info[ATTR_NAME],
        }
        for entity_ref in zha_data.entity_refs[info.ieee]
    ]

    device_info[ATTR_NEIGHBORS] = [
        {
            ATTR_DEVICE_TYPE: neighbor.device_type,
            RX_ON_WHEN_IDLE: neighbor.rx_on_when_idle,
            RELATIONSHIP: neighbor.relationship,
            EXTENDED_PAN_ID: str(neighbor.extended_pan_id),
            ATTR_IEEE: str(neighbor.ieee),
            ATTR_NWK: str(neighbor.nwk),
            PERMIT_JOINING: neighbor.permit_joining,
            DEPTH: str(neighbor.depth),
            ATTR_LQI: str(neighbor.lqi),
        }
        for neighbor in info.neighbors
    ]

    device_info[ATTR_ROUTES] = [
        {
            DEST_NWK: str(route.dest_nwk),
            ROUTE_STATUS: str(route.route_status),
            MEMORY_CONSTRAINED: bool(route.memory_constrained),
            MANY_TO_ONE: bool(route.many_to_one),
            ROUTE_RECORD_REQUIRED: bool(route.route_record_required),
            NEXT_HOP: str(route.next_hop),
        }
        for route in info.routes
    ]

    device_info[ATTR_ENDPOINT_NAMES] = [
        {ATTR_NAME: endpoint.name} for endpoint in info.endpoint_names
    ]

    if (device_id := zha_data.device_ids.get(info.ieee)) is not None:
        device_registry = dr.async_get(hass)
        reg_device = device_registry.async_get(device_id)
        if reg_device is not None:
            device_info[USER_GIVEN_NAME] = reg_device.name_by_user
            device_info[DEVICE_REG_ID] = reg_device.id
            device_info[ATTR_AREA_ID] = dr.async_get_effective_area_id(hass, reg_device)
    return device_info


async def async_get_zha_device_info(
    hass: HomeAssistant, device: Device
) -> dict[str, Any]:
    """Get ZHA device information after refreshing the device snapshot."""
    await device.async_get_extended_device_info()
    return zha_device_info(hass, device.info)


def group_associated_entities(
    hass: HomeAssistant, member_ieee: EUI64, group_id: int
) -> list[GroupEntityReference]:
    """Return the HA group entities that include this member."""
    entity_registry = er.async_get(hass)
    entity_info = []

    for entity_ref in get_zha_data(hass).entity_refs[member_ieee]:
        group = entity_ref.entity_data.group
        if group is None or group.group_id != group_id:
            continue
        entity = entity_registry.async_get(entity_ref.ha_entity_id)
        if entity is None:
            continue

        entity_info.append(
            GroupEntityReference(
                name=entity.name,
                original_name=entity.original_name,
                entity_id=entity_ref.ha_entity_id,
            )
        )

    return entity_info


def group_info(hass: HomeAssistant, info: GroupInfo) -> dict[str, Any]:
    """Return a group description for a group snapshot."""
    return {
        "name": info.name,
        "group_id": info.group_id,
        "members": [
            {
                "endpoint_id": member.endpoint_id,
                "device": zha_device_info(hass, member.device_info),
                "entities": [
                    e._asdict()
                    for e in group_associated_entities(hass, member.ieee, info.group_id)
                ],
            }
            for member in info.members
        ],
    }


class ZHAEventHandler(EventBase):
    """Reflect ZHA client events into Home Assistant."""

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry, client: Client
    ) -> None:
        """Subscribe to the client gateway."""
        super().__init__()
        self.hass = hass
        self.config_entry = config_entry
        self.client = client
        assert client.gateway is not None
        self.gateway: Gateway = client.gateway

        self._device_event_handlers: dict[str, Callable[[Device, Any], None]] = {
            ZHA_EVENT: self.handle_zha_event,
            ZHA_CLUSTER_BIND_EVENT: self.handle_zha_cluster_bind,
            ZHA_CLUSTER_CONFIGURE_REPORTING_EVENT: (
                self.handle_zha_cluster_configure_reporting
            ),
            ZHA_DEVICE_CONFIGURED_EVENT: self.handle_zha_device_configured,
            ZHA_DEVICE_ENTITY_ADDED_EVENT: self.handle_zha_device_entity_added_event,
            ZHA_DEVICE_ENTITY_REMOVED_EVENT: (
                self.handle_zha_device_entity_removed_event
            ),
            ZHA_DEVICE_UPDATED_EVENT: self.handle_zha_device_updated_event,
        }

        self._unsubs: list[Callable[[], None]] = []
        self._device_unsubs: dict[EUI64, Callable[[], None]] = {}
        self._unsubs.append(self.gateway.on_all_events(self._handle_event_protocol))
        config_entry.async_on_unload(
            self.hass.bus.async_listen(
                er.EVENT_ENTITY_REGISTRY_UPDATED,
                self._handle_entity_registry_updated,
            )
        )

    @callback
    def async_start(self) -> None:
        """Register the devices the client already knows and queue their entities."""
        for device in self.gateway.devices.values():
            self._async_register_device(device)
            self._create_entity_metadata(device=device)
        for group in self.gateway.groups.values():
            self._create_entity_metadata(group=group)

    @callback
    def async_stop(self) -> None:
        """Unsubscribe from the client."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for unsub in self._device_unsubs.values():
            unsub()
        self._device_unsubs.clear()

    async def _handle_entity_registry_updated(
        self, event: Event[er.EventEntityRegistryUpdatedData]
    ) -> None:
        """Handle when entity registry updated."""
        entity_id = event.data["entity_id"]
        entity_entry: er.RegistryEntry | None = er.async_get(self.hass).async_get(
            entity_id
        )
        if (
            entity_entry is None
            or entity_entry.config_entry_id != self.config_entry.entry_id
            or entity_entry.device_id is None
        ):
            return
        device_entry: dr.AnyDeviceEntry | None = dr.async_get(self.hass).async_get(
            entity_entry.device_id
        )
        assert device_entry

        ieee_address = next(
            identifier
            for domain, identifier in device_entry.identifiers
            if domain == DOMAIN
        )
        assert ieee_address

        ieee = EUI64.convert(ieee_address)
        device = self.gateway.devices[ieee]
        entity_key = (entity_entry.domain, entity_entry.unique_id)
        if entity_key not in device.platform_entities:
            return
        platform_entity = device.platform_entities[entity_key]
        if entity_entry.disabled:
            platform_entity.disable()
        else:
            platform_entity.enable()

    def _async_register_device(self, device: Device) -> str:
        """Register the device in the device registry and subscribe to its events."""
        zha_data = get_zha_data(self.hass)
        if device.ieee in zha_data.device_ids:
            return zha_data.device_ids[device.ieee]

        coordinator_ieee = self.gateway.coordinator_zha_device.ieee
        via_device_id: str | None = None
        if device.ieee != coordinator_ieee:
            # The coordinator device is registered when the config entry is set up,
            # before any other device is registered here.
            via_device_id = dr.async_get_device_id_by_identifier(
                self.hass,
                (DOMAIN, str(coordinator_ieee)),
                config_entry_id=self.config_entry.entry_id,
            )

        device_registry = dr.async_get(self.hass)
        device_registry_device = device_registry.async_get_or_create(
            config_entry_id=self.config_entry.entry_id,
            connections={(dr.CONNECTION_ZIGBEE, str(device.ieee))},
            identifiers={(DOMAIN, str(device.ieee))},
            name=device.name,
            manufacturer=device.manufacturer,
            model=device.model,
            sw_version=device.firmware_version,
            via_device_id=via_device_id,
        )
        zha_data.device_ids[device.ieee] = device_registry_device.id
        self._device_unsubs[device.ieee] = device.on_all_events(
            partial(self._handle_device_event, device)
        )
        return device_registry_device.id

    def _handle_device_event(self, device: Device, event: Any) -> None:
        if event.event in self._device_event_handlers:
            self._device_event_handlers[event.event](device, event)

    @callback
    def handle_zha_event(self, device: Device, zha_event: ZHAEvent) -> None:
        """Handle a ZHA event."""
        if ATTR_UNIQUE_ID in zha_event.data:
            unique_id = zha_event.data[ATTR_UNIQUE_ID]

            # Client cluster handler unique IDs in the ZHA lib were disambiguated by
            # adding a suffix of `_CLIENT`. Unfortunately, this breaks existing
            # automations that match the `unique_id` key. This can be removed in a
            # future release with proper notice of a breaking change.
            unique_id = unique_id.removesuffix("_CLIENT")
        else:
            unique_id = zha_event.unique_id

        self.hass.bus.async_fire(
            ZHA_EVENT,
            {
                ATTR_DEVICE_IEEE: str(zha_event.device_ieee),
                ATTR_DEVICE_ID: get_zha_data(self.hass).device_ids[device.ieee],
                **zha_event.data,
                # The order of these keys is intentional, `zha_event.data` can contain
                # a `unique_id` key, which we explicitly replace
                ATTR_UNIQUE_ID: unique_id,
            },
        )

    @callback
    def handle_zha_cluster_bind(self, device: Device, event: ClusterBindEvent) -> None:
        """Forward a cluster bind result to the reconfigure websocket."""
        async_dispatcher_send(
            self.hass,
            SIGNAL_DEVICE_RECONFIGURE_EVENT,
            {
                "type": "zha_channel_bind",
                "zha_channel_msg_data": {
                    "cluster_name": event.cluster_name,
                    "cluster_id": event.cluster_id,
                    "success": event.success,
                },
            },
        )

    @callback
    def handle_zha_cluster_configure_reporting(
        self, device: Device, event: ClusterConfigureReportingEvent
    ) -> None:
        """Forward a cluster reporting-configured result to the reconfigure websocket."""
        async_dispatcher_send(
            self.hass,
            SIGNAL_DEVICE_RECONFIGURE_EVENT,
            {
                "type": "zha_channel_configure_reporting",
                "zha_channel_msg_data": {
                    "cluster_name": event.cluster_name,
                    "cluster_id": event.cluster_id,
                    "attributes": event.attributes,
                },
            },
        )

    @callback
    def handle_zha_device_configured(
        self, device: Device, event: DeviceConfiguredEvent
    ) -> None:
        """Forward the device configuration-complete signal to the reconfigure websocket."""
        async_dispatcher_send(
            self.hass,
            SIGNAL_DEVICE_RECONFIGURE_EVENT,
            {"type": "zha_channel_cfg_done"},
        )

    @callback
    def handle_zha_device_entity_added_event(
        self, device: Device, event: DeviceEntityAddedEvent
    ) -> None:
        """Handle a new entity being added to a device at runtime."""
        if event.platform is ZhaPlatform.VIRTUAL:
            return

        entity = device.platform_entities[(event.platform, event.unique_id)]
        ha_zha_data = get_zha_data(self.hass)
        ha_zha_data.platforms[Platform(event.platform)].append(
            EntityData(entity=entity, device=device)
        )
        async_dispatcher_send(self.hass, SIGNAL_ADD_ENTITIES)

    @callback
    def handle_zha_device_entity_removed_event(
        self, device: Device, event: DeviceEntityRemovedEvent
    ) -> None:
        """Handle an entity being removed from a device at runtime."""
        if event.platform is ZhaPlatform.VIRTUAL:
            return

        if not event.remove:
            # Soft remove: signal the entity to unload; registry entry stays
            async_dispatcher_send(
                self.hass,
                f"{SIGNAL_REMOVE_ENTITY}_{event.platform}_{event.unique_id}",
            )
            return

        # Hard remove: delete from registry, also works without a live entity loaded
        entity_registry = er.async_get(self.hass)
        domain = Platform(event.platform)
        if entity_id := entity_registry.async_get_entity_id(
            domain, DOMAIN, event.unique_id
        ):
            entity_registry.async_remove(entity_id)

    @callback
    def handle_zha_device_updated_event(
        self, device: Device, event: DeviceFirmwareInfoUpdatedEvent
    ) -> None:
        """Sync the device's firmware version into the device registry."""
        dr.async_get(self.hass).async_update_device(
            get_zha_data(self.hass).device_ids[device.ieee],
            sw_version=event.new_firmware_version,
        )

    @callback
    def handle_connection_lost(self, event: ConnectionLostEvent) -> None:
        """Handle a connection lost event."""

        _LOGGER.debug("Connection to the radio was lost: %r", event)
        self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)

    @callback
    def handle_device_joined(self, event: DeviceJoinedEvent) -> None:
        """Handle a device joined event."""
        async_dispatcher_send(
            self.hass,
            ZHA_GW_MSG,
            {
                ATTR_TYPE: ZHA_GW_MSG_DEVICE_JOINED,
                ZHA_GW_MSG_DEVICE_INFO: {
                    ATTR_NWK: event.device_info.nwk,
                    ATTR_IEEE: str(event.device_info.ieee),
                    DEVICE_PAIRING_STATUS: event.device_info.pairing_status.name,
                },
            },
        )

    @callback
    def handle_device_removed(self, event: DeviceRemovedEvent) -> None:
        """Handle a device removed event."""
        ieee = event.device_info.ieee
        zha_data = get_zha_data(self.hass)
        if ieee not in zha_data.device_ids:
            return

        # Build the message before dropping the bookkeeping it reads
        device_info = zha_device_info(self.hass, event.device_info)
        device_id = zha_data.device_ids.pop(ieee)
        entity_refs = zha_data.entity_refs.pop(ieee, None)
        self._device_unsubs.pop(ieee)()

        async_dispatcher_send(self.hass, f"{SIGNAL_REMOVE_ENTITIES}_{ieee!s}")
        self.hass.async_create_task(
            self._async_remove_device(device_id, entity_refs),
            "ZHAEventHandler._async_remove_device",
        )
        async_dispatcher_send(
            self.hass,
            ZHA_GW_MSG,
            {
                ATTR_TYPE: ZHA_GW_MSG_DEVICE_REMOVED,
                ZHA_GW_MSG_DEVICE_INFO: device_info,
            },
        )

    @callback
    def handle_device_left(self, event: DeviceLeftEvent) -> None:
        """Handle a device left event."""

    @callback
    def handle_raw_device_initialized(self, event: RawDeviceInitializedEvent) -> None:
        """Handle a raw device initialized event."""
        manuf = event.device_info.manufacturer
        async_dispatcher_send(
            self.hass,
            ZHA_GW_MSG,
            {
                ATTR_TYPE: ZHA_GW_MSG_RAW_INIT,
                ZHA_GW_MSG_DEVICE_INFO: {
                    ATTR_NWK: str(event.device_info.nwk),
                    ATTR_IEEE: str(event.device_info.ieee),
                    DEVICE_PAIRING_STATUS: event.device_info.pairing_status.name,
                    ATTR_MODEL: (event.device_info.model or UNKNOWN_MODEL),
                    ATTR_MANUFACTURER: manuf or UNKNOWN_MANUFACTURER,
                    ATTR_SIGNATURE: event.device_info.signature,
                },
            },
        )

    @callback
    def handle_device_fully_initialized(self, event: DeviceFullInitEvent) -> None:
        """Handle a device fully initialized event."""
        device = self.gateway.devices[event.device_info.ieee]

        # A re-interview rebuilds the device's entities silently on the server; the
        # client device announces the replacements as entity-added events, which
        # become new HA entities. Only a new join needs them all queued here.
        self._async_register_device(device)

        device_info = zha_device_info(self.hass, device.info)
        device_info[DEVICE_PAIRING_STATUS] = event.device_info.pairing_status.name
        if event.new_join:
            self._create_entity_metadata(device=device)
            async_dispatcher_send(self.hass, SIGNAL_ADD_ENTITIES)
        async_dispatcher_send(
            self.hass,
            ZHA_GW_MSG,
            {
                ATTR_TYPE: ZHA_GW_MSG_DEVICE_FULL_INIT,
                ZHA_GW_MSG_DEVICE_INFO: device_info,
            },
        )

    @callback
    def handle_group_member_removed(self, event: GroupEvent) -> None:
        """Handle a group member removed event."""
        group = self.gateway.groups[event.group_info.group_id]
        self._update_group_entities(group)
        self._send_group_gateway_message(group, ZHA_GW_MSG_GROUP_MEMBER_REMOVED)

    @callback
    def handle_group_member_added(self, event: GroupEvent) -> None:
        """Handle a group member added event."""
        group = self.gateway.groups[event.group_info.group_id]
        self._send_group_gateway_message(group, ZHA_GW_MSG_GROUP_MEMBER_ADDED)
        self._update_group_entities(group)

    @callback
    def handle_group_added(self, event: GroupEvent) -> None:
        """Handle a group added event."""
        group = self.gateway.groups[event.group_info.group_id]
        self._update_group_entities(group)
        self._send_group_gateway_message(group, ZHA_GW_MSG_GROUP_ADDED)

    @callback
    def handle_group_removed(self, event: GroupEvent) -> None:
        """Handle a group removed event."""
        async_dispatcher_send(
            self.hass,
            ZHA_GW_MSG,
            {
                ATTR_TYPE: ZHA_GW_MSG_GROUP_REMOVED,
                ZHA_GW_MSG_GROUP_INFO: group_info(self.hass, event.group_info),
            },
        )
        self._cleanup_group_entity_registry_entries(event.group_info.group_id)

    def _create_entity_metadata(
        self, *, device: Device | None = None, group: Group | None = None
    ) -> None:
        """Queue HA entity metadata for a device's or a group's entities."""
        ha_zha_data = get_zha_data(self.hass)

        if device is not None:
            for entity in device.platform_entities.values():
                if entity.PLATFORM is ZhaPlatform.VIRTUAL:
                    continue

                ha_zha_data.platforms[Platform(entity.PLATFORM)].append(
                    EntityData(entity=entity, device=device)
                )
            return

        assert group is not None
        coordinator = self.gateway.coordinator_zha_device
        for entity in group.group_entities.values():
            if entity.PLATFORM is ZhaPlatform.VIRTUAL:
                continue

            ha_zha_data.platforms[Platform(entity.PLATFORM)].append(
                EntityData(entity=entity, device=coordinator, group=group)
            )

    def _cleanup_group_entity_registry_entries(self, group_id: int) -> None:
        """Remove entity registry entries for removed group entities."""
        # first we collect the potential unique ids for
        # entities that could be created from this group
        possible_entity_unique_ids = [
            f"{domain}_zha_group_0x{group_id:04x}" for domain in GROUP_ENTITY_DOMAINS
        ]

        # then we get all group entity entries tied to the coordinator
        entity_registry = er.async_get(self.hass)
        coordinator_device_id = get_zha_data(self.hass).device_ids[
            self.gateway.coordinator_zha_device.ieee
        ]
        all_group_entity_entries = er.async_entries_for_device(
            entity_registry,
            coordinator_device_id,
            include_disabled_entities=True,
        )

        # then we get the entity entries for this specific group
        # by getting the entries that match
        entries_to_remove = [
            entry
            for entry in all_group_entity_entries
            if entry.unique_id in possible_entity_unique_ids
        ]

        # then we remove the entries from the entity registry
        for entry in entries_to_remove:
            _LOGGER.debug(
                "cleaning up entity registry entry for entity: %s", entry.entity_id
            )
            entity_registry.async_remove(entry.entity_id)

    def _update_group_entities(self, group: Group) -> None:
        """Update group entities when a group event is received."""
        async_dispatcher_send(
            self.hass,
            f"{SIGNAL_REMOVE_ENTITIES}_group_{group.group_id}",
        )
        self._create_entity_metadata(group=group)
        async_dispatcher_send(self.hass, SIGNAL_ADD_ENTITIES)

    def _send_group_gateway_message(
        self, group: Group, gateway_message_type: str
    ) -> None:
        """Send the gateway event for a zigpy group event."""
        async_dispatcher_send(
            self.hass,
            ZHA_GW_MSG,
            {
                ATTR_TYPE: gateway_message_type,
                ZHA_GW_MSG_GROUP_INFO: group_info(self.hass, group.info),
            },
        )

    async def _async_remove_device(
        self, device_id: str, entity_refs: list[EntityReference] | None
    ) -> None:
        if entity_refs is not None:
            remove_tasks: list[asyncio.Future[Any]] = [
                entity_ref.remove_future for entity_ref in entity_refs
            ]
            if remove_tasks:
                await asyncio.wait(remove_tasks)

        device_registry = dr.async_get(self.hass)
        reg_device = device_registry.async_get(device_id)
        if reg_device is not None:
            device_registry.async_remove_device(reg_device.id)


class ZHALogRelay:
    """Relay ZHA library logs to websocket subscribers while debug mode is on."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the log relay."""
        self.hass = hass
        self._log_levels: dict[str, dict[str, int]] = {
            DEBUG_LEVEL_ORIGINAL: async_capture_log_levels(),
            DEBUG_LEVEL_CURRENT: async_capture_log_levels(),
        }
        self.debug_enabled: bool = False

        log_relay_handler: LogRelayHandler = LogRelayHandler(hass)
        log_simple_queue: queue.SimpleQueue[logging.Handler] = queue.SimpleQueue()
        self._log_queue_handler = HomeAssistantQueueHandler(log_simple_queue)
        self._log_queue_handler.listener = logging.handlers.QueueListener(
            log_simple_queue, log_relay_handler
        )
        self._log_queue_handler_count: int = 0

    @callback
    def async_enable_debug_mode(self, filterer: _LogFilterType | None = None) -> None:
        """Enable debug mode for ZHA."""
        self._log_levels[DEBUG_LEVEL_ORIGINAL] = async_capture_log_levels()
        async_set_logger_levels(DEBUG_LEVELS)
        self._log_levels[DEBUG_LEVEL_CURRENT] = async_capture_log_levels()

        if filterer:
            self._log_queue_handler.addFilter(filterer)

        # Only start a new log queue handler if the old one is no longer running
        self._log_queue_handler_count += 1

        if self._log_queue_handler.listener and self._log_queue_handler_count == 1:
            self._log_queue_handler.listener.start()

        for logger_name in DEBUG_RELAY_LOGGERS:
            logging.getLogger(logger_name).addHandler(self._log_queue_handler)

        self.debug_enabled = True

    @callback
    def async_disable_debug_mode(self, filterer: _LogFilterType | None = None) -> None:
        """Disable debug mode for ZHA."""
        async_set_logger_levels(self._log_levels[DEBUG_LEVEL_ORIGINAL])
        self._log_levels[DEBUG_LEVEL_CURRENT] = async_capture_log_levels()
        for logger_name in DEBUG_RELAY_LOGGERS:
            logging.getLogger(logger_name).removeHandler(self._log_queue_handler)

        # Only stop the log queue handler if nothing else is using it
        self._log_queue_handler_count -= 1

        if self._log_queue_handler.listener and self._log_queue_handler_count == 0:
            self._log_queue_handler.listener.stop()

        if filterer:
            self._log_queue_handler.removeFilter(filterer)

        self.debug_enabled = False


@callback
def async_capture_log_levels() -> dict[str, int]:
    """Capture current logger levels for ZHA."""
    return {
        DEBUG_COMP_BELLOWS: logging.getLogger(DEBUG_COMP_BELLOWS).getEffectiveLevel(),
        DEBUG_COMP_ZHA: logging.getLogger(DEBUG_COMP_ZHA).getEffectiveLevel(),
        DEBUG_COMP_ZIGPY: logging.getLogger(DEBUG_COMP_ZIGPY).getEffectiveLevel(),
        DEBUG_COMP_ZIGPY_ZNP: logging.getLogger(
            DEBUG_COMP_ZIGPY_ZNP
        ).getEffectiveLevel(),
        DEBUG_COMP_ZIGPY_DECONZ: logging.getLogger(
            DEBUG_COMP_ZIGPY_DECONZ
        ).getEffectiveLevel(),
        DEBUG_COMP_ZIGPY_XBEE: logging.getLogger(
            DEBUG_COMP_ZIGPY_XBEE
        ).getEffectiveLevel(),
        DEBUG_COMP_ZIGPY_ZIGATE: logging.getLogger(
            DEBUG_COMP_ZIGPY_ZIGATE
        ).getEffectiveLevel(),
        DEBUG_LIB_ZHA: logging.getLogger(DEBUG_LIB_ZHA).getEffectiveLevel(),
    }


@callback
def async_set_logger_levels(levels: dict[str, int]) -> None:
    """Set logger levels for ZHA."""
    logging.getLogger(DEBUG_COMP_BELLOWS).setLevel(levels[DEBUG_COMP_BELLOWS])
    logging.getLogger(DEBUG_COMP_ZHA).setLevel(levels[DEBUG_COMP_ZHA])
    logging.getLogger(DEBUG_COMP_ZIGPY).setLevel(levels[DEBUG_COMP_ZIGPY])
    logging.getLogger(DEBUG_COMP_ZIGPY_ZNP).setLevel(levels[DEBUG_COMP_ZIGPY_ZNP])
    logging.getLogger(DEBUG_COMP_ZIGPY_DECONZ).setLevel(levels[DEBUG_COMP_ZIGPY_DECONZ])
    logging.getLogger(DEBUG_COMP_ZIGPY_XBEE).setLevel(levels[DEBUG_COMP_ZIGPY_XBEE])
    logging.getLogger(DEBUG_COMP_ZIGPY_ZIGATE).setLevel(levels[DEBUG_COMP_ZIGPY_ZIGATE])
    logging.getLogger(DEBUG_LIB_ZHA).setLevel(levels[DEBUG_LIB_ZHA])


class LogRelayHandler(logging.Handler):
    """Log handler for error messages."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize a new LogErrorHandler."""
        super().__init__()
        self.hass = hass
        hass_path: str = HOMEASSISTANT_PATH[0]
        config_dir = self.hass.config.config_dir
        self.paths_re = re.compile(
            rf"(?:{re.escape(hass_path)}|{re.escape(config_dir)})/(.*)"
        )

    @override
    def emit(self, record: LogRecord) -> None:
        """Relay log message via dispatcher."""
        entry = LogEntry(
            record, self.paths_re, figure_out_source=record.levelno >= logging.WARNING
        )
        dispatcher_send(
            self.hass,
            ZHA_GW_MSG,
            {ATTR_TYPE: ZHA_GW_MSG_LOG_OUTPUT, ZHA_GW_MSG_LOG_ENTRY: entry.to_dict()},
        )


@dataclasses.dataclass(kw_only=True, slots=True)
class HAZHAData:
    """ZHA data stored in `hass.data`."""

    yaml_config: ConfigType = dataclasses.field(default_factory=dict)
    config_entry: ConfigEntry | None = dataclasses.field(default=None)
    device_trigger_cache: dict[str, tuple[str, dict]] = dataclasses.field(
        default_factory=dict
    )
    client: Client | None = dataclasses.field(default=None)
    handler: ZHAEventHandler | None = dataclasses.field(default=None)
    log_relay: ZHALogRelay | None = dataclasses.field(default=None)
    # HA device registry id per ZHA device
    device_ids: dict[EUI64, str] = dataclasses.field(default_factory=dict)
    entity_refs: collections.defaultdict[EUI64, list[EntityReference]] = (
        dataclasses.field(default_factory=lambda: collections.defaultdict(list))
    )
    platforms: collections.defaultdict[Platform, list] = dataclasses.field(
        default_factory=lambda: collections.defaultdict(list)
    )
    update_coordinator: ZHAFirmwareUpdateCoordinator | None = dataclasses.field(
        default=None
    )


def get_zha_data(hass: HomeAssistant) -> HAZHAData:
    """Get the global ZHA data object."""
    if DATA_ZHA not in hass.data:
        hass.data[DATA_ZHA] = HAZHAData()

    return hass.data[DATA_ZHA]


def get_zha_client(hass: HomeAssistant) -> Client:
    """Get the ZHA client."""
    if (client := get_zha_data(hass).client) is None:
        raise ValueError("No ZHA client exists")

    return client


def get_zha_gateway(hass: HomeAssistant) -> Gateway:
    """Get the client-side ZHA gateway model."""
    gateway = get_zha_client(hass).gateway
    assert gateway is not None
    return gateway


def get_config_entry(hass: HomeAssistant) -> ConfigEntry:
    """Get the ZHA config entry."""
    if (config_entry := get_zha_data(hass).config_entry) is None:
        raise ValueError("No gateway object exists to retrieve the config entry from.")

    return config_entry


@callback
def async_get_zha_device(hass: HomeAssistant, device_id: str) -> Device:
    """Get a ZHA device for the given device registry id."""
    device_registry = dr.async_get(hass)
    registry_device = device_registry.async_get(device_id)
    if not registry_device:
        _LOGGER.error("Device id `%s` not found in registry", device_id)
        raise KeyError(f"Device id `{device_id}` not found in registry.")
    ieee_address = next(
        identifier
        for domain, identifier in registry_device.identifiers
        if domain == DOMAIN
    )
    ieee = EUI64.convert(ieee_address)
    return get_zha_gateway(hass).devices[ieee]


def cluster_command_schema_to_vol_schema(schema: CommandSchema) -> probatio.Schema:
    """Convert a cluster command schema to a probatio schema."""
    return probatio.Schema(
        {
            (
                probatio.Optional(field.name)
                if field.optional
                else probatio.Required(field.name)
            ): schema_type_to_vol(field.type)
            for field in schema.fields
        }
    )


def schema_type_to_vol(field_type: Any) -> Any:
    """Convert a schema type to a probatio type."""
    if issubclass(field_type, enum.Flag) and field_type.__members__:
        return cv.multi_select(
            [key.replace("_", " ") for key in field_type.__members__]
        )
    if issubclass(field_type, enum.Enum) and field_type.__members__:
        return probatio.In([key.replace("_", " ") for key in field_type.__members__])
    if (
        issubclass(field_type, zigpy.types.FixedIntType)
        or issubclass(field_type, enum.Flag)
        or issubclass(field_type, enum.Enum)
    ):
        return probatio.All(
            probatio.Coerce(int),
            probatio.Range(field_type.min_value, field_type.max_value),
        )
    return str


def convert_to_zcl_values(
    fields: dict[str, Any], schema: CommandSchema
) -> dict[str, Any]:
    """Convert user input to ZCL values."""
    converted_fields: dict[str, Any] = {}
    for field in schema.fields:
        if field.name not in fields:
            continue
        value = fields[field.name]
        if issubclass(field.type, enum.Flag) and isinstance(value, list):
            # Zigpy internal bitmap types are int subclasses as well
            assert issubclass(field.type, int)
            new_value = 0

            for flag in value:
                if isinstance(flag, str):
                    new_value |= field.type[flag.replace(" ", "_")]
                else:
                    new_value |= flag

            value = field.type(new_value)
        elif issubclass(field.type, enum.Enum):
            value = (
                field.type[value.replace(" ", "_")]
                if isinstance(value, str)
                else field.type(value)
            )
        else:
            value = field.type(value)
        _LOGGER.debug(
            "Converted ZCL schema field(%s) value from: %s to: %s",
            field.name,
            fields[field.name],
            value,
        )
        converted_fields[field.name] = value
    return converted_fields


def async_cluster_exists(hass: HomeAssistant, cluster_id, skip_coordinator=True):
    """Determine if a device containing the specified in cluster is paired."""
    client = get_zha_client(hass)
    # Cluster introspection is not part of the client API yet
    assert client.server is not None
    for zha_device in client.server.gateway.devices.values():
        # Green Power devices have no clusters
        if not isinstance(zha_device, ZigbeeDevice):
            continue
        if skip_coordinator and zha_device.is_coordinator:
            continue
        clusters_by_endpoint = zha_device.async_get_clusters()
        for clusters in clusters_by_endpoint.values():
            if (
                cluster_id in clusters[CLUSTER_TYPE_IN]
                or cluster_id in clusters[CLUSTER_TYPE_OUT]
            ):
                return True
    return False


@callback
def async_add_entities(
    _async_add_entities: AddEntitiesCallback,
    entity_class: Callable[[EntityData], ZHAEntity],
    entities: list[EntityData],
    **kwargs,
) -> None:
    """Add entities helper."""
    if not entities:
        return

    entities_to_add: list[ZHAEntity] = []
    for entity_data in entities:
        try:
            entities_to_add.append(entity_class(entity_data))
        # broad exception to prevent a single entity from
        # preventing an entire platform from loading.
        # this can potentially be caused by a misbehaving
        # device or a bad quirk. Not ideal but the
        # alternative is adding try/catch to each entity
        # class __init__ method with a specific exception
        except Exception:
            _LOGGER.exception(
                "Error while adding entity from entity data: %s", entity_data
            )
    _async_add_entities(entities_to_add, update_before_add=False)
    for entity in entities_to_add:
        if not entity.enabled:
            entity.entity_data.entity.disable()
    entities.clear()


CONF_ZHA_OPTIONS_SCHEMA = probatio.Schema(
    {
        probatio.Optional(CONF_DEFAULT_LIGHT_TRANSITION, default=0): probatio.All(
            probatio.Coerce(float), probatio.Range(min=0, max=2**16 / 10)
        ),
        probatio.Required(
            CONF_ENABLE_ENHANCED_LIGHT_TRANSITION, default=False
        ): cv.boolean,
        probatio.Required(
            CONF_ENABLE_LIGHT_TRANSITIONING_FLAG, default=True
        ): cv.boolean,
        probatio.Required(CONF_GROUP_MEMBERS_ASSUME_STATE, default=True): cv.boolean,
        probatio.Required(CONF_ENABLE_IDENTIFY_ON_JOIN, default=True): cv.boolean,
        probatio.Optional(
            CONF_CONSIDER_UNAVAILABLE_MAINS,
            default=CONF_DEFAULT_CONSIDER_UNAVAILABLE_MAINS,
        ): cv.positive_int,
        probatio.Optional(
            CONF_CONSIDER_UNAVAILABLE_BATTERY,
            default=CONF_DEFAULT_CONSIDER_UNAVAILABLE_BATTERY,
        ): cv.positive_int,
        probatio.Required(CONF_ENABLE_MAINS_STARTUP_POLLING, default=True): cv.boolean,
    },
    extra=probatio.REMOVE_EXTRA,
)

CONF_ZHA_ALARM_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_ALARM_MASTER_CODE, default="1234"): cv.string,
        probatio.Required(CONF_ALARM_FAILED_TRIES, default=3): cv.positive_int,
        probatio.Required(CONF_ALARM_ARM_REQUIRES_CODE, default=False): cv.boolean,
    }
)


def create_zha_config(hass: HomeAssistant, ha_zha_data: HAZHAData) -> ZHAData:
    """Create ZHA lib configuration from HA config objects."""

    # ensure that we have the necessary HA configuration data
    assert ha_zha_data.config_entry is not None
    assert ha_zha_data.yaml_config is not None

    # deep copy the yaml config to avoid modifying the original and to safely
    # pass it to the ZHA library
    app_config = copy.deepcopy(ha_zha_data.yaml_config.get(CONF_ZIGPY, {}))
    database = ha_zha_data.yaml_config.get(
        CONF_DATABASE,
        hass.config.path(DEFAULT_DATABASE_NAME),
    )
    app_config[CONF_DATABASE] = database
    app_config[CONF_DEVICE] = ha_zha_data.config_entry.data[CONF_DEVICE]

    radio_type = RadioType[ha_zha_data.config_entry.data[CONF_RADIO_TYPE]]

    # Until we have a way to coordinate channels with the Thread half of multi-PAN,
    # stick to the old zigpy default of channel 15 instead of dynamically scanning
    if (
        is_multiprotocol_url(app_config[CONF_DEVICE][CONF_DEVICE_PATH])
        and app_config.get(CONF_NWK, {}).get(CONF_NWK_CHANNEL) is None
    ):
        app_config.setdefault(CONF_NWK, {})[CONF_NWK_CHANNEL] = 15

    options: MappingProxyType[str, Any] = ha_zha_data.config_entry.options.get(
        CUSTOM_CONFIGURATION, {}
    )
    zha_options = CONF_ZHA_OPTIONS_SCHEMA(options.get(ZHA_OPTIONS, {}))
    ha_acp_options = CONF_ZHA_ALARM_SCHEMA(options.get(ZHA_ALARM_OPTIONS, {}))
    light_options: LightOptions = LightOptions(
        default_light_transition=zha_options.get(CONF_DEFAULT_LIGHT_TRANSITION),
        enable_enhanced_light_transition=zha_options.get(
            CONF_ENABLE_ENHANCED_LIGHT_TRANSITION
        ),
        enable_light_transitioning_flag=zha_options.get(
            CONF_ENABLE_LIGHT_TRANSITIONING_FLAG
        ),
        group_members_assume_state=zha_options.get(CONF_GROUP_MEMBERS_ASSUME_STATE),
    )
    device_options: DeviceOptions = DeviceOptions(
        enable_identify_on_join=zha_options.get(CONF_ENABLE_IDENTIFY_ON_JOIN),
        consider_unavailable_mains=zha_options.get(CONF_CONSIDER_UNAVAILABLE_MAINS),
        consider_unavailable_battery=zha_options.get(CONF_CONSIDER_UNAVAILABLE_BATTERY),
        enable_mains_startup_polling=zha_options.get(CONF_ENABLE_MAINS_STARTUP_POLLING),
    )
    acp_options: AlarmControlPanelOptions = AlarmControlPanelOptions(
        master_code=ha_acp_options.get(CONF_ALARM_MASTER_CODE),
        failed_tries=ha_acp_options.get(CONF_ALARM_FAILED_TRIES),
        arm_requires_code=ha_acp_options.get(CONF_ALARM_ARM_REQUIRES_CODE),
    )
    coord_config: CoordinatorConfiguration = CoordinatorConfiguration(
        path=app_config[CONF_DEVICE][CONF_DEVICE_PATH],
        baudrate=app_config[CONF_DEVICE][CONF_BAUDRATE],
        flow_control=app_config[CONF_DEVICE][CONF_FLOW_CONTROL],
        radio_type=radio_type.name,
    )
    quirks_config: QuirksConfiguration = QuirksConfiguration(
        enabled=ha_zha_data.yaml_config.get(CONF_ENABLE_QUIRKS, True),
        custom_quirks_path=ha_zha_data.yaml_config.get(CONF_CUSTOM_QUIRKS_PATH),
        setup_function=zhaquirks.setup,
        uninitialized_packet_handler=(
            zhaquirks.legacy.handle_message_from_uninitialized_sender
        ),
    )
    overrides_config: dict[str, DeviceOverridesConfiguration] = {}
    overrides: dict[str, dict[str, Any]] = cast(
        dict[str, dict[str, Any]], ha_zha_data.yaml_config.get(CONF_DEVICE_CONFIG)
    )
    if overrides is not None:
        for unique_id, override_data in overrides.items():
            overrides_config[unique_id] = DeviceOverridesConfiguration(
                type=override_data["type"],
            )

    return ZHAData(
        zigpy_config=app_config,
        config=ZHAConfiguration(
            light_options=light_options,
            device_options=device_options,
            alarm_control_panel_options=acp_options,
            coordinator_configuration=coord_config,
            quirks_configuration=quirks_config,
            device_overrides=overrides_config,
        ),
        local_timezone=ZoneInfo(hass.config.time_zone),
        country_code=hass.config.country,
    )


@asynccontextmanager
async def convert_zha_error_to_ha_error() -> AsyncGenerator[None]:
    """Decorate ZHA commands and re-raises ZHAException as HomeAssistantError."""
    try:
        yield
    except TimeoutError as exc:
        raise HomeAssistantError(
            "Failed to send request: device did not respond"
        ) from exc
    except zigpy.exceptions.ZigbeeException as exc:
        message = "Failed to send request"

        if str(exc):
            message = f"{message}: {exc}"

        raise HomeAssistantError(message) from exc
    except ZHAException as err:
        raise HomeAssistantError(err) from err


def exclude_none_values(obj: Mapping[str, Any]) -> dict[str, Any]:
    """Return a new dictionary excluding keys with None values."""
    return {k: v for k, v in obj.items() if v is not None}


def get_config_entry_unique_id(network_info: NetworkInfo) -> str:
    """Generate a unique id for a config entry based on the network info."""
    return f"epid={network_info.extended_pan_id}".lower()


IEEE_SCHEMA = probatio.All(cv.string, EUI64.convert)

SERVICE_PERMIT_PARAMS: VolDictType = {
    probatio.Optional(ATTR_IEEE): IEEE_SCHEMA,
    probatio.Optional(ATTR_DURATION, default=60): probatio.All(
        probatio.Coerce(int), probatio.Range(0, 254)
    ),
    probatio.Inclusive(ATTR_SOURCE_IEEE, "install_code"): IEEE_SCHEMA,
    probatio.Inclusive(ATTR_INSTALL_CODE, "install_code"): probatio.All(
        cv.string, convert_install_code
    ),
    probatio.Exclusive(ATTR_QR_CODE, "install_code"): probatio.All(
        cv.string, qr_to_install_code
    ),
}
