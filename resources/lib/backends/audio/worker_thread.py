# coding=utf-8
from __future__ import annotations  # For union operator |

from queue import Empty as EmptyQueue, Full as FullQueue, Queue
from threading import RLock, Thread

from backends.players.iplayer import IPlayer
from backends.players.player_index import PlayerIndex
from backends.settings.service_types import ServiceID
from common import *
from common.base_services import BaseServices, IServices
from common.logger import *
from common.monitor import Monitor
from common.phrases import Phrase, PhraseList

MY_LOGGER = BasicLogger.get_logger(__name__)


class TTSQueueData:
    # callable, **kwargs,
    data: Tuple[callable, Dict[str, Any]] = None

    def __init__(self, call: callable, **kwargs) -> None:
        self.data = (call, kwargs)

    def getData(self) -> Tuple[callable, Dict[str, Any]]:
        return self.data

    def get_callable(self) -> callable:
        return self.data[0]

    def get_kwargs(self) -> Dict[str, Any]:
        return self.data[1]


class TTSQueue(Queue):

    def __init__(self, maxsize: int = 50):
        super().__init__(maxsize)


class WorkerThread:

    def __init__(self, thread_name: str, task: callable, **kwargs):
        clz = type(self)
        self.queue: TTSQueue = TTSQueue()
        self.queueFullCount: int = 0
        self.queueCount: int = 0
        self.started: bool = False
        self.idle_count: int = 0
        self.task: callable = task
        self.kwargs = kwargs
        self._queue_lock = RLock()
        self._closed = False

        self.thread = Thread(target=self.process_queue, name=thread_name)
        self.thread_started: bool = False

    def add_to_queue(self, tts_data: TTSQueueData) -> None:
        clz = type(self)
        try:
            with self._queue_lock:
                if self._closed:
                    return
                if MY_LOGGER.isEnabledFor(DEBUG_V):
                    MY_LOGGER.debug_v(f'tts_data: {tts_data.data}')
                if not self.thread_started:
                    self.thread.start()
                    self.thread_started = True
                self.queue.put_nowait(tts_data)
                self.queueCount += 1
        except FullQueue as e:
            self.queueFullCount += 1
        except Exception as e:
            MY_LOGGER.exception('')

    def discard_pending_playback(self) -> None:
        """Drop queued speech while retaining background cache work."""
        retained: list[TTSQueueData] = []
        with self._queue_lock:
            while True:
                try:
                    data = self.queue.get_nowait()
                except EmptyQueue:
                    break
                self.queue.task_done()
                if data.get_kwargs().get('state') != 'play_file':
                    retained.append(data)
            for data in retained:
                self.queue.put_nowait(data)

    def close(self) -> None:
        with self._queue_lock:
            self._closed = True
            if not self.thread_started:
                return
            while True:
                try:
                    self.queue.get_nowait()
                except EmptyQueue:
                    break
                self.queue.task_done()
            self.queue.put_nowait(None)

    def process_queue(self):
        clz = type(self)
        data: TTSQueueData | None = None
        try:
            while not Monitor.is_abort_requested():
                try:
                    data = self.queue.get(timeout=0.25)
                    self.idle_count = 0
                except EmptyQueue as e:
                    self.idle_count += 1
                    continue
                try:
                    if data is None:
                        return
                    kwargs: Dict[str, Any] = data.get_kwargs()
                    if kwargs['state'] == 'play_file':
                        player_key: ServiceID = kwargs.get('player_key')
                        player: IPlayer = PlayerIndex.get_player(player_key.service_id)
                        phrase: Phrase = kwargs.get('phrase')
                        engine_key: ServiceID = kwargs.get('engine_key')
                        # MY_LOGGER.debug(f'player_key: {player_key} '
                        #                 f'engine_key: {engine_key}')
                        try:
                            engine: IServices = BaseServices.get_service(engine_key)
                            phrase.add_event('worker.dequeue')
                            engine.say_phrase(phrase)
                        except Exception as e:
                            MY_LOGGER.exception('')
                        continue

                    if kwargs['state'] == 'seed_cache':
                        engine_key: ServiceID = kwargs.get('engine_key')
                        phrases: PhraseList = kwargs.get('phrases')
                        try:
                            engine: IServices = BaseServices.get_service(engine_key)
                            engine.seed_text_cache(phrases)
                            engine: BaseServices
                            engine = BaseServices.get_service(engine_key)
                            voice_cache = engine.get_voice_cache()
                            if MY_LOGGER.isEnabledFor(DEBUG):
                                MY_LOGGER.debug(f'engine_key: {engine_key}')
                            for phrase in phrases:
                                phrase: Phrase
                                voice_cache.text_referenced(phrase)
                        except Exception as e:
                            MY_LOGGER.exception('')
                except AbortException as e:
                    return  # Exit thread
                except Exception as e:
                    MY_LOGGER.exception('')
                finally:
                    self.queue.task_done()

        except AbortException as e:
            pass  # Let thread exit
        except Exception as e:
            MY_LOGGER.exception('')
        finally:
            pass
        return
