from __future__ import annotations

import sys
import time
import warnings
from collections.abc import Collection
from contextlib import contextmanager
from typing import Any, Callable, Final, NamedTuple, Optional

from quel_ic_config import (
    AbstractStartAwgunitsTask,
    BoxStartCapunitsByTriggerTask,
    Quel1Box,
    Quel1PortType,
)
from typing_extensions import TypeAlias

ContextId: TypeAlias = Any
BoxFactory: TypeAlias = Callable[[], Quel1Box]


class Lock(NamedTuple):
    context_id: ContextId
    acquire_time: float  # unixtime
    timeout_duration: float  # in sec


TIMEOUT_DURATION_DEFAULT: Final[float] = 5


class BoxConnection:
    def __init__(self, box_name: str, box_factory: BoxFactory, timecounter_offset: int):
        """
        Adapter for the Quel1Box.
        """
        self.box_name = box_name
        self._box_factory = box_factory
        self.__box: Optional[Quel1Box] = None
        self._dumped_data: Optional[Any] = None

        self.connect()

        self._lock: Optional[Lock] = None
        self._timecounter_offset: int = timecounter_offset
        self._last_trigger_timecounter: int = 0

    @property
    def timecounter_offset(self) -> int:
        return self._timecounter_offset

    @timecounter_offset.setter
    def timecounter_offset(self, offset: int):
        self._timecounter_offset = offset

    @property
    def last_trigger_timecounter(self) -> int:
        return self._last_trigger_timecounter

    @last_trigger_timecounter.setter
    def last_trigger_timecounter(self, val):
        self._last_trigger_timecounter = val

    def acquire_lock(
        self, context_id: ContextId, timeout_duration: float = TIMEOUT_DURATION_DEFAULT
    ) -> bool:
        self._check_and_release_timed_out_lock()
        if self._lock is not None:
            if self._lock.context_id != context_id:
                return False
        self._lock = Lock(
            context_id=context_id,
            acquire_time=time.time(),
            timeout_duration=timeout_duration,
        )
        return True

    def release_lock(self, context_id: ContextId):
        self._check_and_release_timed_out_lock()
        if self._lock is not None:
            if self._lock.context_id == context_id:
                self._lock = None

    def _check_and_release_timed_out_lock(self):
        if self._lock:
            if time.time() - self._lock.acquire_time > self._lock.timeout_duration:
                self._lock = None

    def _is_accessable_from_context(self, context_id: Optional[ContextId]):
        self._check_and_release_timed_out_lock()
        if self._lock is not None and self._lock.context_id == context_id:
            return True
        return False

    def get_box(self, context_id: Optional[ContextId]) -> Quel1Box:
        if not self._is_accessable_from_context(context_id):
            raise RuntimeError(
                f"The context doesn't have the lock for Box '{self.box_name}'."
            )
        return self.box_unsafe

    def is_sequencer_avaliable(self):
        # This is workaround to stabilize the sequencer inside QuEL devices.
        if self.box_unsafe.wss.hal.awgctrl.are_busy_any(
            self.box_unsafe.wss.hal.awgctrl.units
        ):
            return False
        return True

    @property
    def box_unsafe(self) -> Quel1Box:
        """
        Returns the Quel1Box instance without any lock checks.
        """
        if self.__box is None:
            raise RuntimeError("No reference to Quel1Box.")
        return self.__box

    def get_current_timecounter(self) -> int:
        return int(self.box_unsafe.get_current_timecounter()) - self._timecounter_offset

    def get_latest_sysref_timecounter(self) -> int:
        return (
            int(self.box_unsafe.get_latest_sysref_timecounter())
            - self._timecounter_offset
        )

    def start_capture_by_awg_trigger(
        self,
        context_id: ContextId,
        runits: Collection[tuple[Quel1PortType, int]],
        channels: Collection[tuple[Quel1PortType, int]],
        timecounter: int,
    ) -> tuple[BoxStartCapunitsByTriggerTask, AbstractStartAwgunitsTask]:
        if timecounter % 16 != 0:
            raise RuntimeError("timecounter must be a multiple of 16.")
        self._last_trigger_timecounter = timecounter
        timecounter_raw = timecounter + self._timecounter_offset
        return self.get_box(context_id).start_capture_by_awg_trigger(
            runits=runits, channels=channels, timecounter=timecounter_raw
        )

    def start_wavegen(
        self,
        context_id: ContextId,
        channels: Collection[tuple[Quel1PortType, int]],
        timecounter: int,
    ) -> AbstractStartAwgunitsTask:
        if timecounter % 16 != 0:
            raise RuntimeError("timecounter must be a multiple of 16.")
        self._last_trigger_timecounter = timecounter
        timecounter_raw = timecounter + self._timecounter_offset
        return self.get_box(context_id).start_wavegen(
            channels=channels, timecounter=timecounter_raw
        )

    def disconnect(self, context_id: ContextId):
        self._dumped_data = self.get_box(context_id).dump_box()
        if (count := sys.getrefcount(self.__box)) > 1:
            warnings.warn(
                f"Reference to Quel1Box object seems to be leaked (count: {count}). Disconnection may be failed."
            )
        del self.__box

    def connect(self, linkup: bool = False):
        try:
            if self.__box is not None:
                raise ValueError("Box is already connected.")
        except AttributeError:
            pass
        self.__box = self._box_factory()
        if linkup:
            self.__box.relinkup()
        self.__box.reconnect()
        if self._dumped_data is not None:
            self.__box.config_box(self._dumped_data)
            self._dumped_data = None

    def purge(self):
        """
        Only for tests and debuggings.
        """
        del self.__box


def acquire_all_locks(
    box_conns: Collection[BoxConnection],
    context_id: ContextId,
    timeout_duration: float = TIMEOUT_DURATION_DEFAULT,
) -> bool:
    """
    Attempts to acquire locks on all managed BoxConnection instances for the given context ID.
    This operation is atomic: if any lock fails to acquire or times out, all acquired locks
    will be released (rollback).

    Args:
        context_id: The unique identifier for the client requesting the locks.
        timeout_duration: The duration (in seconds) after which each acquired lock
          will automatically expire if not explicitly released.

    Returns:
        bool: True if all locks were successfully acquired, False otherwise.
    """
    acquired_box_conns = []
    try:
        for box_conn in box_conns:
            if not box_conn.acquire_lock(context_id, timeout_duration):
                # If acquisition fails, roll back all already acquired locks
                release_all_locks(acquired_box_conns, context_id)
                return False
            acquired_box_conns.append(box_conn)
        return True
    except Exception:
        release_all_locks(acquired_box_conns, context_id)
        raise  # Re-raise the original timeout exception


def release_all_locks(
    box_conns: Collection[BoxConnection], context_id: ContextId
) -> None:
    """
    Attempts to release locks on all managed BoxConnection instances for the given context ID.

    Args:
        context_id (ContextId): The unique identifier of the client attempting to release the locks.
    """
    for box_conn in box_conns:
        box_conn.release_lock(context_id)


@contextmanager
def locked_boxes(
    box_conns: Collection[BoxConnection],
    context_id: ContextId,
    timeout_duration: float = TIMEOUT_DURATION_DEFAULT,
):
    if not acquire_all_locks(box_conns, context_id, timeout_duration):
        raise RuntimeError(
            "Failed to atomically acquire locks because they are held by another context."
        )
    yield box_conns
    release_all_locks(box_conns, context_id)
