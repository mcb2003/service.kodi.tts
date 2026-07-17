# coding=utf-8
from __future__ import annotations

import threading

import langcodes
import speechd

from backends.audio.sound_capabilities import ServiceType
from backends.base import ThreadedTTSBackend
from backends.settings.language_info import LanguageInfo
from backends.settings.service_types import ServiceKey, Services
from backends.settings.setting_properties import SettingProp
from backends.settings.settings_map import SettingsMap
from common import *
from common.logger import BasicLogger
from common.message_ids import MessageId
from common.phrases import Phrase
from common.setting_constants import Backends
from common.settings import Settings

MY_LOGGER = BasicLogger.get_logger(__name__)


class SpeechDispatcherTTSBackend(ThreadedTTSBackend):
    """Speak through Speech Dispatcher instead of producing an audio file.

    Speech Dispatcher owns synthesis and playback, so this backend deliberately
    bypasses Kodi TTS's file, cache, transcoder, and media-player pipeline.
    """

    ID = Backends.SPEECH_DISPATCHER_ID
    engine_id = Backends.SPEECH_DISPATCHER_ID
    service_id = Services.SPEECH_DISPATCHER_ID
    service_type = ServiceType.ENGINE
    service_key = ServiceKey.SPEECH_DISPATCHER_KEY
    displayName = MessageId.ENGINE_SPEECH_DISPATCHER.get_msg()

    _client = None
    _client_lock = threading.RLock()
    _languages_loaded = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @classmethod
    def get_backend_id(cls) -> str:
        return cls.service_id

    @classmethod
    def _new_client(cls):
        # autospawn must stay disabled: on a desktop or HTPC, NixOS owns the
        # daemon and determines its audio configuration.
        return speechd.SSIPClient('Kodi Screen Reader', component='kodi-tts',
                                  autospawn=False)

    @classmethod
    def _get_client(cls):
        with cls._client_lock:
            if cls._client is None:
                cls._client = cls._new_client()
            return cls._client

    @classmethod
    def close_client(cls) -> None:
        with cls._client_lock:
            if cls._client is None:
                return
            try:
                cls._client.close()
            except Exception:
                MY_LOGGER.exception('Closing Speech Dispatcher client failed')
            finally:
                cls._client = None

    @classmethod
    def list_output_modules(cls) -> List[str]:
        client = cls._get_client()
        return list(client.list_output_modules())

    @classmethod
    def load_languages(cls) -> None:
        """Publish Speech Dispatcher voices to Kodi TTS's shared voice model."""
        if cls._languages_loaded:
            return

        client = cls._get_client()
        for module in cls.list_output_modules():
            client.set_output_module(module)
            for voice_name, language, variant in client.list_synthesis_voices():
                if not language:
                    continue
                try:
                    ietf = langcodes.Language.get(language)
                except Exception:
                    MY_LOGGER.warning('Ignoring invalid Speech Dispatcher language: %s',
                                      language)
                    continue

                label = voice_name
                if variant:
                    label = f'{voice_name} ({variant})'
                LanguageInfo.add_language(
                        engine_key=cls.service_key,
                        language_id=ietf.language,
                        country_id=ietf.territory,
                        ietf=ietf,
                        region_id='',
                        gender=None,
                        voice=label,
                        engine_lang_id=language,
                        engine_voice_id=voice_name,
                        engine_name_msg_id=MessageId.ENGINE_SPEECH_DISPATCHER,
                        engine_quality=3,
                        voice_quality=3)
        cls._languages_loaded = True

    @classmethod
    def _setting(cls, setting: str, default):
        validator = SettingsMap.get_validator(cls.service_key.with_prop(setting))
        if validator is None:
            return default
        try:
            return validator.get_tts_value(default)
        except TypeError:
            return validator.get_tts_value()

    @classmethod
    def _configure_client(cls, client) -> None:
        module = cls._setting(SettingProp.MODULE, None)
        if module:
            client.set_output_module(module)

        language = Settings.get_language(cls.service_key)
        if language and language != SettingProp.LANGUAGE_DEFAULT:
            client.set_language(language)

        voice = Settings.get_voice(cls.service_key)
        if voice and voice != SettingProp.VOICE_DEFAULT:
            client.set_synthesis_voice(voice)

        client.set_rate(int(cls._setting(SettingProp.SPEED, 0)))
        client.set_pitch(int(cls._setting(SettingProp.PITCH, 0)))
        client.set_pitch_range(int(cls._setting(SettingProp.INFLECTION, 0)))
        client.set_volume(int(cls._setting(SettingProp.VOLUME, 0)))

    def threadedSay(self, phrase: Phrase) -> None:
        if phrase is None or not phrase.text:
            return
        try:
            with type(self)._client_lock:
                client = type(self)._get_client()
                type(self)._configure_client(client)
                if phrase.get_interrupt():
                    client.cancel()
                client.speak(phrase.text)
        except Exception:
            # Reconnect once after a daemon restart or a stale user socket.
            type(self).close_client()
            try:
                with type(self)._client_lock:
                    client = type(self)._get_client()
                    type(self)._configure_client(client)
                    client.speak(phrase.text)
            except Exception:
                MY_LOGGER.exception('Speech Dispatcher failed to speak')

    def stop(self) -> None:
        try:
            with type(self)._client_lock:
                client = type(self)._get_client()
                client.cancel()
        except Exception:
            MY_LOGGER.exception('Speech Dispatcher failed to cancel speech')

    def destroy(self) -> None:
        self.stop()
        type(self).close_client()
        super().destroy()
