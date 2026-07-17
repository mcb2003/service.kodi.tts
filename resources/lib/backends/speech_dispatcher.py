# coding=utf-8
from __future__ import annotations

import threading

import langcodes
import speechd
from speechd.client import CallbackType

from backends.audio.sound_capabilities import ServiceType
from backends.base import ThreadedTTSBackend
from backends.settings.language_info import LanguageInfo
from backends.settings.service_types import ServiceKey, Services
from backends.settings.setting_properties import SettingProp
from common import *
from common.logger import BasicLogger
from common.message_ids import MessageId
from common.phrases import Phrase
from common.setting_constants import Backends, Genders
from common.settings import Settings
from common.settings_low_level import SettingsLowLevel

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
    _client_configuration = None
    _languages_loaded = False
    _speech_lock = threading.RLock()
    _speech_generation = 0
    _speaking = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @classmethod
    def get_backend_id(cls) -> str:
        return cls.service_id

    @classmethod
    def _new_client(cls):
        # Leave autospawn at Speech Dispatcher's default.  This first connects
        # to the standard per-user XDG socket (or SPEECHD_ADDRESS), and starts
        # a local daemon only if no server is already available.  Distributions
        # that manage a user service therefore keep using it unchanged.
        # SSIP client-name components may not contain whitespace.
        return speechd.SSIPClient('Kodi-Screen-Reader', component='kodi-tts',
                                  autospawn=None)

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
                cls._client_configuration = None
        cls._reset_speech_state()

    @classmethod
    def _reset_speech_state(cls) -> None:
        """Invalidate callbacks associated with a previous client session."""
        with cls._speech_lock:
            cls._speech_generation += 1
            cls._speaking = False

    @classmethod
    def _begin_speech(cls) -> int:
        with cls._speech_lock:
            cls._speech_generation += 1
            cls._speaking = True
            return cls._speech_generation

    @classmethod
    def _finish_speech(cls, generation: int) -> None:
        with cls._speech_lock:
            if generation == cls._speech_generation:
                cls._speaking = False

    @classmethod
    def _speech_callback(cls, generation: int):
        """Return the minimal callback required by python-speechd.

        Speech Dispatcher delivers callbacks from its own thread, where the
        client API must not be called.  State updates are intentionally the
        only work performed here.
        """
        def callback(event_type, **_kwargs) -> None:
            if event_type in (CallbackType.END, CallbackType.CANCEL):
                cls._finish_speech(generation)

        return callback

    @classmethod
    def _is_speaking(cls) -> bool:
        with cls._speech_lock:
            return cls._speaking

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
        try:
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

                    label = f'{module}: {voice_name}'
                    if variant:
                        label = f'{label} ({variant})'
                    LanguageInfo.add_language(
                            engine_key=cls.service_key,
                            language_id=ietf.language,
                            country_id=ietf.territory,
                            ietf=ietf,
                            region_id='',
                            gender=Genders.UNKNOWN,
                            voice=label,
                            engine_lang_id=language,
                            # Voices are discovered per output module.  Store the
                            # module alongside the real synthesis voice so choosing
                            # a voice also selects the module that provides it.
                            engine_voice_id=f'{module}\x1f{voice_name}',
                            engine_name_msg_id=MessageId.ENGINE_SPEECH_DISPATCHER,
                            engine_quality=3,
                            voice_quality=3)
        finally:
            cls._client_configuration = None
        cls._languages_loaded = True

    @classmethod
    def _setting(cls, setting: str, default):
        key = cls.service_key.with_prop(setting)
        if isinstance(default, int):
            return SettingsLowLevel.get_setting_int(key, default)
        return SettingsLowLevel.get_setting_str(key, load_on_demand=True,
                                                default=default)

    @classmethod
    def _configure_client(cls, client) -> None:
        module = cls._setting(SettingProp.MODULE, None)
        language = Settings.get_language(cls.service_key)
        voice = Settings.get_voice(cls.service_key)
        if voice and voice != SettingProp.VOICE_DEFAULT:
            voice_module, separator, voice_name = voice.partition('\x1f')
            if separator:
                module = voice_module
                voice = voice_name

        if language == SettingProp.LANGUAGE_DEFAULT:
            language = None
        if voice == SettingProp.VOICE_DEFAULT:
            voice = None
        configuration = (
                module,
                language,
                voice,
                int(cls._setting(SettingProp.SPEED, 0)),
                int(cls._setting(SettingProp.PITCH, 0)),
                int(cls._setting(SettingProp.INFLECTION, 0)),
                int(cls._setting(SettingProp.VOLUME, 0)),
        )
        previous = cls._client_configuration
        if configuration == previous:
            return

        if module and (previous is None or module != previous[0]):
            client.set_output_module(module)
        if language and (previous is None or language != previous[1]):
            client.set_language(language)
        if voice and (previous is None or voice != previous[2]):
            client.set_synthesis_voice(voice)
        if previous is None or configuration[3] != previous[3]:
            client.set_rate(configuration[3])
        if previous is None or configuration[4] != previous[4]:
            client.set_pitch(configuration[4])
        if previous is None or configuration[5] != previous[5]:
            client.set_pitch_range(configuration[5])
        if previous is None or configuration[6] != previous[6]:
            client.set_volume(configuration[6])
        cls._client_configuration = configuration

    def _speak(self, client, phrase: Phrase) -> None:
        """Submit one current phrase and retain its lifecycle state."""
        text = phrase.get_text()
        if not text:
            return
        generation = type(self)._begin_speech()
        try:
            phrase.add_event('speechd.speak')
            client.speak(
                    text,
                    callback=type(self)._speech_callback(generation),
                    event_types=(CallbackType.END, CallbackType.CANCEL))
        except Exception:
            type(self)._finish_speech(generation)
            raise
        if MY_LOGGER.isEnabledFor(DEBUG_V):
            MY_LOGGER.debug_v(f'Speech pipeline: {phrase.history()}')

    def threadedSay(self, phrase: Phrase) -> None:
        if phrase is None:
            return
        try:
            with type(self)._client_lock:
                client = type(self)._get_client()
                type(self)._configure_client(client)
                if phrase.get_interrupt():
                    type(self)._reset_speech_state()
                    client.cancel()
                self._speak(client, phrase)
        except ExpiredException:
            return
        except Exception:
            # Reconnect once after a daemon restart or a stale user socket.
            type(self).close_client()
            try:
                with type(self)._client_lock:
                    client = type(self)._get_client()
                    type(self)._configure_client(client)
                    self._speak(client, phrase)
            except ExpiredException:
                return
            except Exception:
                MY_LOGGER.exception('Speech Dispatcher failed to speak')

    def stop(self) -> None:
        try:
            type(self)._reset_speech_state()
            with type(self)._client_lock:
                client = type(self)._get_client()
                client.cancel()
        except Exception:
            MY_LOGGER.exception('Speech Dispatcher failed to cancel speech')

    def isSpeaking(self) -> bool:
        return type(self)._is_speaking() or super().isSpeaking()

    def destroy(self) -> None:
        self.stop()
        type(self).close_client()
        super().destroy()
