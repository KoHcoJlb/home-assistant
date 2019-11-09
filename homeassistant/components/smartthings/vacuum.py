"""Support for vacuums through the SmartThings cloud API."""
import logging
from typing import Optional, Sequence

from homeassistant.components.vacuum import StateVacuumDevice, SUPPORT_STATE, SUPPORT_START, \
    SUPPORT_STOP, SUPPORT_FAN_SPEED, SUPPORT_BATTERY, STATE_PAUSED, STATE_IDLE, STATE_DOCKED, \
    STATE_CLEANING, STATE_RETURNING, STATE_ERROR, SUPPORT_PAUSE, SUPPORT_RETURN_HOME
from . import SmartThingsEntity
from .const import DATA_BROKERS, DOMAIN

_LOGGER = logging.getLogger(__name__)
DEPENDENCIES = ['smartthings']

FAN_SPEEDS = ['low', 'medium', 'high']
FAN_SPEED_TO_TURBO_MODE = {
    'low': 'silence',
    'medium': 'off',
    'high': 'on'
}
TURBO_MODE_TO_FAN_SPEED = {v: k for k, v in FAN_SPEED_TO_TURBO_MODE.items()}

async def async_setup_platform(
        hass, config, async_add_entities, discovery_info=None):
    """Platform uses config entry setup."""
    pass


async def async_setup_entry(hass, config_entry, async_add_entities):
    broker = hass.data[DOMAIN][DATA_BROKERS][config_entry.entry_id]
    async_add_entities(
        [SmartThingsVacuum(device) for device in broker.devices.values()
         if broker.any_assigned(device.device_id, 'vacuum')])


def get_capabilities(capabilities: Sequence[str]) -> Optional[Sequence[str]]:
    from pysmartthings import Capability

    min_required = [
        Capability.robot_cleaner_movement,
        Capability.robot_cleaner_cleaning_mode,
    ]

    if all(cap in min_required for cap in min_required):
        return min_required + [
            Capability.fan_speed,
            Capability.battery,
            Capability.robot_cleaner_turbo_mode
        ]

    return None


class SmartThingsVacuum(SmartThingsEntity, StateVacuumDevice):
    def __init__(self, device):
        super().__init__(device)

        self._supported_features = self._determine_features()

    def _determine_features(self):
        from pysmartthings.device import Capability

        features = SUPPORT_STATE | SUPPORT_START | SUPPORT_STOP | SUPPORT_PAUSE | \
                   SUPPORT_RETURN_HOME
        if Capability.robot_cleaner_turbo_mode in self._device.capabilities:
            features |= SUPPORT_FAN_SPEED
        if Capability.battery in self._device.capabilities:
            features |= SUPPORT_BATTERY

        return features

    async def async_start(self):
        from pysmartthings import Capability

        await self._device.command('main', Capability.robot_cleaner_cleaning_mode,
                                   'setRobotCleanerCleaningMode', ['auto'])

    async def async_pause(self):
        from pysmartthings import Capability

        await self._device.command('main', Capability.robot_cleaner_cleaning_mode,
                                   'setRobotCleanerCleaningMode', ['stop'])

    async def async_stop(self, **kwargs):
        await self.async_return_to_base()

    async def async_return_to_base(self, **kwargs):
        from pysmartthings import Capability

        await self._device.command('main', Capability.robot_cleaner_movement,
                                   'setRobotCleanerMovement', ['homing'])

    async def async_set_fan_speed(self, fan_speed, **kwargs):
        from pysmartthings import Capability

        await self._device.command('main', Capability.robot_cleaner_turbo_mode,
                                   'setRobotCleanerTurboMode',
                                   [FAN_SPEED_TO_TURBO_MODE[fan_speed]])

    @property
    def battery_level(self):
        from pysmartthings import Attribute

        return self._device.status.attributes[Attribute.battery].value

    @property
    def state(self):
        from pysmartthings import Attribute

        cleaner_movement = self._device.status.attributes[Attribute.robot_cleaner_movement].value
        cleaning_mode = self._device.status \
            .attributes[Attribute.robot_cleaner_cleaning_mode].value
        state = {
            'cleaning': STATE_CLEANING,
            'after': STATE_CLEANING,
            'charging': STATE_DOCKED,
            'idle': STATE_IDLE,
            'pause': STATE_PAUSED,
            'homing': STATE_RETURNING,
            'alarm': STATE_ERROR
        }.get(cleaner_movement)
        if state is None:
            _LOGGER.error("unknown state '{}'".format(cleaner_movement))
            return None

        # After pausing 'robot_cleaner_movement' is not updated, so we deduce it here
        if state == STATE_CLEANING and cleaning_mode == 'stop':
            state = STATE_PAUSED

        return state

    @property
    def supported_features(self):
        return self._supported_features

    @property
    def fan_speed(self):
        from pysmartthings import Attribute

        return TURBO_MODE_TO_FAN_SPEED[
            self._device.status.attributes[Attribute.robot_cleaner_turbo_mode].value]

    @property
    def fan_speed_list(self):
        return FAN_SPEEDS
