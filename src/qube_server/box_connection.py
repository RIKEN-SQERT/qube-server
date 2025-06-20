from __future__ import annotations

import time
from collections.abc import Collection
from typing import Any, Final, NamedTuple, Optional

from quel_ic_config import (
    AbstractStartAwgunitsTask,
    BoxStartCapunitsByTriggerTask,
    Quel1Box,
    Quel1PortType,
)
from typing_extensions import TypeAlias

ContextId: TypeAlias = Any


class Lock(NamedTuple):
    context_id: ContextId
    acquire_time: float  # unixtime
    timeout_duration: float  # in sec


TIMEOUT_DURATION_DEFAULT: Final[float] = 5


class BoxConnection:
    def __init__(self, box_name: str, box: Quel1Box, timecounter_offset: int):
        self.box_name = box_name
        self._box = box
        self._resource_id_to_info = {}
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

    def acquire_lock(self, context_id: ContextId, timeout_duration: float) -> bool:
        self._check_and_release_timeout_locks()
        if self._lock is not None:
            if self._lock.context_id != context_id:
                return False
        self._lock = Lock(
            context_id=context_id,
            acquire_time=time.time(),
            timeout_duration=timeout_duration
            if timeout_duration
            else TIMEOUT_DURATION_DEFAULT,
        )
        return True

    def release_lock(self, context_id: ContextId):
        self._check_and_release_timeout_locks()
        if self._lock is not None:
            if self._lock.context_id == context_id:
                self._lock = None

    def _check_and_release_timeout_locks(self):
        if self._lock:
            if time.time() - self._lock.acquire_time > self._lock.timeout_duration:
                self._lock = None

    def _is_accessable_from_context(self, context_id: ContextId):
        if self._lock is None or self._lock.context_id == context_id:
            return True
        return False

    def get_box(self, context_id: ContextId) -> Quel1Box:
        if not self._is_accessable_from_context(context_id):
            raise RuntimeError(f"Box '{self.box_name}' is locked from another context.")
        return self._box

    @property
    def box_unsafe(self) -> Quel1Box:
        """
        Returns the Quel1Box instance without any lock checks.
        """
        return self._box

    def get_latest_sysref_timecounter(self) -> int:
        return int(self._box.get_latest_sysref_timecounter())

    def start_capture_by_awg_trigger(
        self,
        context_id: ContextId,
        runits: Collection[tuple[Quel1PortType, int]],
        channels: Collection[tuple[Quel1PortType, int]],
        timecounter: Optional[int] = None,
    ) -> tuple[BoxStartCapunitsByTriggerTask, AbstractStartAwgunitsTask]:
        return self.get_box(context_id).start_capture_by_awg_trigger(
            runits=runits, channels=channels, timecounter=timecounter
        )


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
        print(f"All locks acquired by [{context_id}].")
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
