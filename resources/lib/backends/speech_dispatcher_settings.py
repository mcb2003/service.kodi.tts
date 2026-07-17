# coding=utf-8
from __future__ import annotations

from backends.audio.sound_capabilities import SoundCapabilities
from backends.engines.base_engine_settings import BaseEngineSettings
from backends.settings.service_types import ServiceKey, Services, ServiceType
from backends.settings.setting_properties import SettingProp, SettingType
from backends.settings.settings_map import Status, SettingsMap
from backends.settings.validators import SimpleStringValidator, StringValidator
from backends.speech_dispatcher import SpeechDispatcherTTSBackend
from common import *
from common.config_exception import UnusableServiceException
from common.logger import BasicLogger
from common.message_ids import MessageId
from common.service_status import Progress, ServiceStatus, StatusType
from common.setting_constants import AudioType, Backends, PlayerMode, Players
from common.system_queries import SystemQueries

MY_LOGGER = BasicLogger.get_logger(__name__)


class SpeechDispatcherSettings:
    ID = Backends.SPEECH_DISPATCHER_ID
    engine_id = Backends.SPEECH_DISPATCHER_ID
    service_id = Services.SPEECH_DISPATCHER_ID
    service_type = ServiceType.ENGINE
    service_key = ServiceKey.SPEECH_DISPATCHER_KEY
    NAME_KEY = service_key.with_prop(SettingProp.SERVICE_NAME)
    displayName = MessageId.ENGINE_SPEECH_DISPATCHER.get_msg()

    initialized = False
    _service_status = ServiceStatus()

    @classmethod
    def config_settings(cls) -> None:
        if cls.initialized:
            return

        cls.check_is_supported_on_platform()
        cls.check_is_installed()
        cls.check_is_available()
        cls.check_is_usable()
        if cls._service_status.status != Status.OK:
            raise UnusableServiceException(cls.service_key, cls._service_status, msg='')
        cls.is_usable()

        BaseEngineSettings.config_settings(cls.service_key, settings=[])
        modules = SpeechDispatcherTTSBackend.list_output_modules()
        if not modules:
            raise UnusableServiceException(cls.service_key, cls._service_status, msg='')

        SimpleStringValidator(cls.NAME_KEY, value=cls.displayName, const=True,
                              define_setting=True, service_status=StatusType.OK,
                              persist=False)
        StringValidator(cls.service_key.with_prop(SettingProp.MODULE),
                        allowed_values=modules, default=modules[0],
                        define_setting=True, service_status=StatusType.OK, persist=True)
        # These are populated from Speech Dispatcher at runtime.  LanguageInfo
        # drives the custom dialog's language/voice selectors.
        StringValidator(cls.service_key.with_prop(SettingProp.LANGUAGE),
                        allowed_values=[], default=SettingProp.LANGUAGE_DEFAULT,
                        define_setting=True, service_status=StatusType.OK, persist=True)
        StringValidator(cls.service_key.with_prop(SettingProp.VOICE),
                        allowed_values=[], default=SettingProp.VOICE_DEFAULT,
                        define_setting=True, service_status=StatusType.OK, persist=True)
        StringValidator(cls.service_key.with_prop(SettingProp.PLAYER_MODE),
                        allowed_values=[PlayerMode.ENGINE_SPEAK.value],
                        default=PlayerMode.ENGINE_SPEAK.value,
                        const=True, define_setting=True, service_status=StatusType.OK,
                        persist=False)
        # Configure treats every engine as having a player, even when the
        # engine speaks directly.  The built-in player is the framework's
        # representation of that direct-speech path; it does not send audio to
        # Kodi or an external media player.
        StringValidator(cls.service_key.with_prop(SettingProp.PLAYER),
                        allowed_values=[Players.BUILT_IN],
                        default=Players.BUILT_IN,
                        const=True, define_setting=True, service_status=StatusType.OK,
                        persist=False)

        for setting in [
                SettingProp.SPEED,
                SettingProp.PITCH,
                SettingProp.INFLECTION,
                SettingProp.VOLUME,
        ]:
            # Speech Dispatcher uses its own -100 through 100 scale.  Do not
            # use NumericValidator here: that validator is deliberately bound
            # to Kodi TTS's global rate/volume conversion settings.
            SettingsMap.define_setting(cls.service_key.with_prop(setting),
                                       setting_type=SettingType.INTEGER_TYPE,
                                       service_status=StatusType.OK,
                                       persist=True)

        SettingsMap.define_setting(cls.service_key.with_prop(SettingProp.CACHE_SPEECH),
                                   setting_type=SettingType.BOOLEAN_TYPE,
                                   service_status=StatusType.OK, persist=False)
        SoundCapabilities.add_service(cls.service_key, [ServiceType.ENGINE], [],
                                      [AudioType.BUILT_IN])
        SpeechDispatcherTTSBackend.load_languages()
        cls.initialized = True

    @classmethod
    def check_is_supported_on_platform(cls) -> None:
        if cls._service_status.progress != Progress.START:
            return
        cls._service_status.progress = Progress.SUPPORTED
        if not SystemQueries.isLinux():
            cls._service_status.status = Status.FAILED
            cls._service_status.status_summary = StatusType.NOT_ON_PLATFORM

    @classmethod
    def check_is_installed(cls) -> None:
        if cls._service_status.progress != Progress.SUPPORTED:
            return
        try:
            import speechd  # noqa: F401
            cls._service_status.progress = Progress.INSTALLED
        except ImportError:
            cls._service_status.status = Status.FAILED
            cls._service_status.status_summary = StatusType.NOT_FOUND

    @classmethod
    def check_is_available(cls) -> None:
        if cls._service_status.progress != Progress.INSTALLED:
            return
        try:
            if SpeechDispatcherTTSBackend.list_output_modules():
                cls._service_status.progress = Progress.AVAILABLE
                return
        except Exception:
            MY_LOGGER.exception('Speech Dispatcher is unavailable')
        cls._service_status.status = Status.FAILED
        cls._service_status.status_summary = StatusType.BROKEN

    @classmethod
    def check_is_usable(cls) -> None:
        """Register the direct-speech engine as selectable by Kodi TTS."""
        if cls._service_status.progress == Progress.AVAILABLE:
            cls._service_status.progress = Progress.USABLE
            cls._service_status.status_summary = StatusType.OK
            SettingsMap.define_setting(cls.service_key,
                                       setting_type=SettingType.STRING_TYPE,
                                       service_status=StatusType.OK,
                                       validator=None)

    @classmethod
    def is_usable(cls) -> bool:
        if (cls._service_status.status != Status.OK
                or cls._service_status.progress != Progress.USABLE):
            raise UnusableServiceException(cls.service_key,
                                           cls._service_status,
                                           msg='')
        return True
