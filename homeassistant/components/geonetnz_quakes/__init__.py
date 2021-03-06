"""The GeoNet NZ Quakes integration."""
import asyncio
import logging
from datetime import timedelta

import voluptuous as vol
from aio_geojson_geonetnz_quakes import GeonetnzQuakesFeedManager

from homeassistant.core import callback
from homeassistant.util.unit_system import METRIC_SYSTEM
from homeassistant.config_entries import SOURCE_IMPORT
from homeassistant.const import (
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_RADIUS,
    CONF_SCAN_INTERVAL,
    CONF_UNIT_SYSTEM_IMPERIAL,
    CONF_UNIT_SYSTEM,
    LENGTH_MILES,
)
from homeassistant.helpers import config_validation as cv, aiohttp_client
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval

from .config_flow import configured_instances
from .const import (
    PLATFORMS,
    CONF_MINIMUM_MAGNITUDE,
    CONF_MMI,
    DEFAULT_FILTER_TIME_INTERVAL,
    DEFAULT_MINIMUM_MAGNITUDE,
    DEFAULT_MMI,
    DEFAULT_RADIUS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    FEED,
    SIGNAL_DELETE_ENTITY,
    SIGNAL_NEW_GEOLOCATION,
    SIGNAL_STATUS,
    SIGNAL_UPDATE_ENTITY,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(CONF_LATITUDE): cv.latitude,
                vol.Optional(CONF_LONGITUDE): cv.longitude,
                vol.Optional(CONF_MMI, default=DEFAULT_MMI): vol.All(
                    vol.Coerce(int), vol.Range(min=-1, max=8)
                ),
                vol.Optional(CONF_RADIUS, default=DEFAULT_RADIUS): vol.Coerce(float),
                vol.Optional(
                    CONF_MINIMUM_MAGNITUDE, default=DEFAULT_MINIMUM_MAGNITUDE
                ): vol.All(vol.Coerce(float), vol.Range(min=0)),
                vol.Optional(
                    CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL
                ): cv.time_period,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass, config):
    """Set up the GeoNet NZ Quakes component."""
    if DOMAIN not in config:
        return True

    conf = config[DOMAIN]

    latitude = conf.get(CONF_LATITUDE, hass.config.latitude)
    longitude = conf.get(CONF_LONGITUDE, hass.config.longitude)
    mmi = conf[CONF_MMI]
    scan_interval = conf[CONF_SCAN_INTERVAL]

    identifier = f"{latitude}, {longitude}"
    if identifier in configured_instances(hass):
        return True

    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_IMPORT},
            data={
                CONF_LATITUDE: latitude,
                CONF_LONGITUDE: longitude,
                CONF_RADIUS: conf[CONF_RADIUS],
                CONF_MINIMUM_MAGNITUDE: conf[CONF_MINIMUM_MAGNITUDE],
                CONF_MMI: mmi,
                CONF_SCAN_INTERVAL: scan_interval,
            },
        )
    )

    return True


async def async_setup_entry(hass, config_entry):
    """Set up the GeoNet NZ Quakes component as config entry."""
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}
    if FEED not in hass.data[DOMAIN]:
        hass.data[DOMAIN][FEED] = {}

    radius = config_entry.data[CONF_RADIUS]
    unit_system = config_entry.data[CONF_UNIT_SYSTEM]
    if unit_system == CONF_UNIT_SYSTEM_IMPERIAL:
        radius = METRIC_SYSTEM.length(radius, LENGTH_MILES)
    # Create feed entity manager for all platforms.
    manager = GeonetnzQuakesFeedEntityManager(hass, config_entry, radius, unit_system)
    hass.data[DOMAIN][FEED][config_entry.entry_id] = manager
    _LOGGER.debug("Feed entity manager added for %s", config_entry.entry_id)
    await manager.async_init()
    return True


async def async_unload_entry(hass, config_entry):
    """Unload an GeoNet NZ Quakes component config entry."""
    manager = hass.data[DOMAIN][FEED].pop(config_entry.entry_id)
    await manager.async_stop()
    await asyncio.wait(
        [
            hass.config_entries.async_forward_entry_unload(config_entry, domain)
            for domain in PLATFORMS
        ]
    )
    return True


class GeonetnzQuakesFeedEntityManager:
    """Feed Entity Manager for GeoNet NZ Quakes feed."""

    def __init__(self, hass, config_entry, radius_in_km, unit_system):
        """Initialize the Feed Entity Manager."""
        self._hass = hass
        self._config_entry = config_entry
        coordinates = (
            config_entry.data[CONF_LATITUDE],
            config_entry.data[CONF_LONGITUDE],
        )
        websession = aiohttp_client.async_get_clientsession(hass)
        self._feed_manager = GeonetnzQuakesFeedManager(
            websession,
            self._generate_entity,
            self._update_entity,
            self._remove_entity,
            coordinates,
            mmi=config_entry.data[CONF_MMI],
            filter_radius=radius_in_km,
            filter_minimum_magnitude=config_entry.data[CONF_MINIMUM_MAGNITUDE],
            filter_time=DEFAULT_FILTER_TIME_INTERVAL,
            status_callback=self._status_update,
        )
        self._config_entry_id = config_entry.entry_id
        self._scan_interval = timedelta(seconds=config_entry.data[CONF_SCAN_INTERVAL])
        self._unit_system = unit_system
        self._track_time_remove_callback = None
        self._status_info = None
        self.listeners = []

    async def async_init(self):
        """Schedule initial and regular updates based on configured time interval."""

        for domain in PLATFORMS:
            self._hass.async_create_task(
                self._hass.config_entries.async_forward_entry_setup(
                    self._config_entry, domain
                )
            )

        async def update(event_time):
            """Update."""
            await self.async_update()

        # Trigger updates at regular intervals.
        self._track_time_remove_callback = async_track_time_interval(
            self._hass, update, self._scan_interval
        )

        _LOGGER.debug("Feed entity manager initialized")

    async def async_update(self):
        """Refresh data."""
        await self._feed_manager.update()
        _LOGGER.debug("Feed entity manager updated")

    async def async_stop(self):
        """Stop this feed entity manager from refreshing."""
        for unsub_dispatcher in self.listeners:
            unsub_dispatcher()
        self.listeners = []
        if self._track_time_remove_callback:
            self._track_time_remove_callback()
        _LOGGER.debug("Feed entity manager stopped")

    @callback
    def async_event_new_entity(self):
        """Return manager specific event to signal new entity."""
        return SIGNAL_NEW_GEOLOCATION.format(self._config_entry_id)

    def get_entry(self, external_id):
        """Get feed entry by external id."""
        return self._feed_manager.feed_entries.get(external_id)

    def status_info(self):
        """Return latest status update info received."""
        return self._status_info

    async def _generate_entity(self, external_id):
        """Generate new entity."""
        async_dispatcher_send(
            self._hass,
            self.async_event_new_entity(),
            self,
            external_id,
            self._unit_system,
        )

    async def _update_entity(self, external_id):
        """Update entity."""
        async_dispatcher_send(self._hass, SIGNAL_UPDATE_ENTITY.format(external_id))

    async def _remove_entity(self, external_id):
        """Remove entity."""
        async_dispatcher_send(self._hass, SIGNAL_DELETE_ENTITY.format(external_id))

    async def _status_update(self, status_info):
        """Propagate status update."""
        _LOGGER.debug("Status update received: %s", status_info)
        self._status_info = status_info
        async_dispatcher_send(self._hass, SIGNAL_STATUS.format(self._config_entry_id))
