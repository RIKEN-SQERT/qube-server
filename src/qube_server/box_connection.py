from __future__ import annotations

import sys
import time
import warnings
from collections.abc import Collection, Sequence
from contextlib import contextmanager
from typing import Any, Callable, Final, NamedTuple, Optional

import numpy as np
import numpy.typing as npt
import quel_ic_config as qi
from quel_ic_config_utils import deskew_tools
from typing_extensions import TypeAlias

from qube_server.constants import QSConstants

ContextId: TypeAlias = Any
BoxFactory: TypeAlias = Callable[[], qi.Quel1Box]


class Lock(NamedTuple):
    context_id: ContextId
    acquire_time: float  # unixtime
    timeout_duration: float  # in sec


TIMEOUT_DURATION_DEFAULT: Final[float] = 5


def _ps_to_word(ps) -> int:
    return int(round(ps * QSConstants.SYNC_CLOCK / 1_000_000_000_000))


class BoxConnection:
    def __init__(
        self,
        box_factory: BoxFactory,
        timecounter_additional_offset: Optional[int],
    ):
        """
        Adapter for the Quel1Box.
        """
        self._box_name = ""
        self._box_factory = box_factory
        self.__box: Optional[qi.Quel1Box] = None
        self._dumped_data: Optional[Any] = None

        self.connect()

        self._delay_compensator = deskew_tools.E7awgDelayCompensator()
        self._wait_amount_resolver = deskew_tools.WaitAmountResolver()
        self._lock: Optional[Lock] = None
        if timecounter_additional_offset:
            self._timecounter_additional_offset: int = timecounter_additional_offset
        else:
            self._timecounter_additional_offset = 0
        self._last_trigger_timecounter: int = 0

    @property
    def box_name(self) -> str:
        return self._box_name

    @property
    def timecounter_offset(self) -> int:
        return self._timecounter_additional_offset

    @timecounter_offset.setter
    def timecounter_offset(self, offset: int):
        self._timecounter_additional_offset = offset

    @property
    def last_trigger_timecounter(self) -> int:
        return self._last_trigger_timecounter

    @last_trigger_timecounter.setter
    def last_trigger_timecounter(self, val):
        self._last_trigger_timecounter = val

    def update_deskew_conf(self, deskew_conf: deskew_tools.DeskewConfiguration):
        self._wait_amount_resolver = (
            deskew_tools.WaitAmountResolver.from_deskew_configuration(deskew_conf)
        )

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

    def get_box(self, context_id: Optional[ContextId]) -> qi.Quel1Box:
        if not self._is_accessable_from_context(context_id):
            raise RuntimeError(
                f"The context doesn't have the lock for Box '{self.box_name}'."
            )
        return self.box_unsafe

    def is_sequencer_available(self):
        # This is workaround to stabilize the sequencer inside QuEL devices.
        if self.box_unsafe.wss.hal.awgctrl.are_busy_any(
            self.box_unsafe.wss.hal.awgctrl.units
        ):
            return False
        return True

    @property
    def box_unsafe(self) -> qi.Quel1Box:
        """
        Returns the Quel1Box instance without any lock checks.
        """
        if self.__box is None:
            raise RuntimeError("No reference to Quel1Box.")
        return self.__box

    def get_current_timecounter(self) -> int:
        return (
            int(self.box_unsafe.get_current_timecounter())
            - self._timecounter_additional_offset
        )

    def get_latest_sysref_timecounter(self) -> int:
        return (
            int(self.box_unsafe.get_latest_sysref_timecounter())
            - self._timecounter_additional_offset
        )

    def prepare_wave_generation(
        self,
        port: qi.Quel1PortType,
        channel: int,
        iq: npt.NDArray[np.complex64],
        post_blank_word: int,
        num_repeat: int = 1,
        delay_ps: int = 0,
    ):
        waveform_name = f"{port}-{channel}"
        deskew_tools.register_blank_wavedata(
            self.box_unsafe, port=port, channel=channel
        )
        self.box_unsafe.register_wavedata(
            port=port, channel=channel, name=waveform_name, iq=iq
        )
        awg_param = qi.AwgParam(num_wait_word=0, num_repeat=num_repeat)
        awg_param.chunks.append(
            qi.WaveChunk(name_of_wavedata=waveform_name, num_blank_word=post_blank_word)
        )
        awg_param = self._delay_compensator.adjust_awg_param(
            awg_param,
            init_blank_offset_word=self._wait_amount_resolver.get_word_to_wait(
                self.box_unsafe.name, port
            )
            + _ps_to_word(delay_ps),
        )
        self.box_unsafe.config_channel(port=port, channel=channel, awg_param=awg_param)

    def prepare_capture(
        self,
        port: qi.Quel1PortType,
        runit: int,
        windows: Sequence[tuple[int, int]],
        repetition_time: int,
        base_cap_param: qi.CapParam,
        num_repeat: int = 1,
        delay_ps: int = 0,
    ):
        """
        NOTE:

        Example for param.num_sum_sections = 1 (a single readout in an experiment like Rabi)
          +----------------------+------------+----------------------+------------+----------------------+
          |   blank   | readout  | post-blank |   blank   | readout  | post-blank |   blank   | readout  |
          | (control  |          | (relax ba- | (control  |          | (relax ba- | (control  |          |
          | operation)|          | ck to |g>) | operation)|          | ck to |g>) | operation)|          |
          +----------------------+------------+----------------------+------------+----------------------+
                      |<------- REPETITION TIME --------->|<------- REPETITION TIME --------->|<---
        ->|-----------|<- CAPTURE DELAY

          |<-------- SINGLE EXPERIMENT ------>|<-------- SINGLE EXPERIMENT ------>|<-------- SINGLE EXP..

        - Given that the sum_section is defined as a pair of capture duration and
          post blank, the initial non-readout duration has to be implemented usi-
          ng capture_delay.
        - The repetition duration starts at the beginning of readout operation
          and ends at the end of 2nd control operation (just before 2nd readout)
        - The capture word is defined as the four multiple of sampling points. It
          corresponds to 4 * ADC_BBSAMP_IVL = ACQ_CAPW_RESOL (nanoseconds).
        """
        param = base_cap_param
        repetition_word = int(
            (repetition_time + QSConstants.ACQ_CAPW_RESOL // 2)
            // QSConstants.ACQ_CAPW_RESOL
        )
        win_word = list()
        for _s, _e in windows:
            # flatten window (start,end) to a series
            # of timestamps
            win_word.append(
                int((_s + QSConstants.ACQ_CAPW_RESOL / 2) // QSConstants.ACQ_CAPW_RESOL)
            )
            win_word.append(
                int((_e + QSConstants.ACQ_CAPW_RESOL / 2) // QSConstants.ACQ_CAPW_RESOL)
            )
        win_word.append(repetition_word)

        param.num_repeat = int(num_repeat)
        _s0 = win_word.pop(0)
        param.num_wait_word = _s0
        win_word[-1] += _s0  # win_word[-1] is the end time of a single sequence.
        # As the repeat duration is offset by capture_delay, we have to add the
        # capture_delay time.
        idx = 0
        while len(win_word) > 1:
            _e = win_word.pop(0)
            _s = win_word.pop(0)
            blank_length = _s - _e
            section_length = _e - _s0
            _s0 = _s
            param.sections.append(
                qi.CapSection(
                    name=f"{idx}",
                    num_capture_word=section_length,
                    num_blank_word=blank_length,
                )
            )
            idx += 1

        param = self._delay_compensator.adjust_cap_param(
            param,
            init_blank_offset_word=self._wait_amount_resolver.get_word_to_wait(
                self.box_unsafe.name, port
            )
            + _ps_to_word(delay_ps),
        )
        print(param)
        self.box_unsafe.config_runit(port=port, runit=runit, capture_param=param)

    def update_dsp_mode_setting(
        self,
        base_cap_param: qi.CapParam,
        complexfir_coeff: npt.NDArray[np.complex64],
        sum_range: tuple[int, int],
        window_coeff: npt.NDArray[np.complex128],
        num_repeat: int,
        enable_decim: bool,
        enable_averg: bool,
        enable_summn: bool,
    ):
        """
        Configure readout parametes to acquisition modes.
        """
        param = base_cap_param

        if enable_decim:
            # [Decimation] 500MSa/s datapoints are reduced to 125 MSa/s (8ns interval)
            param.complexfir_coeff = complexfir_coeff
            param.complexfir_enable = True
            param.decimation_enable = True
        if enable_averg:
            # [Averaging] Averaging datapoints for all experiments.
            param.integration_enable = True
            param.num_repeat = num_repeat
        if enable_summn:
            # [Summation] For a given readout window, the DSP apply complex window filter.
            # (This is equivalent to the convolution in frequency domain of a filter
            # function with frequency offset). Then, DSP sums all the datapoints
            # in the readout window.
            # resp = self.configure_readout_summation(mux, param, summn)
            param.sum_enable = True
            param.sum_range = sum_range
            param.window_enable = True
            param.window_coeff = window_coeff
        return param

    def start_capture_by_awg_trigger(
        self,
        context_id: ContextId,
        runits: Collection[tuple[qi.Quel1PortType, int]],
        channels: Collection[tuple[qi.Quel1PortType, int]],
        timecounter: int,
    ) -> tuple[qi.BoxStartCapunitsByTriggerTask, qi.AbstractStartAwgunitsTask]:
        self._last_trigger_timecounter = timecounter
        timecounter_raw = timecounter + self._timecounter_additional_offset
        return self.get_box(context_id).start_capture_by_awg_trigger(
            runits=runits, channels=channels, timecounter=timecounter_raw
        )

    def start_wavegen(
        self,
        context_id: ContextId,
        channels: Collection[tuple[qi.Quel1PortType, int]],
        timecounter: int,
    ) -> qi.AbstractStartAwgunitsTask:
        self._last_trigger_timecounter = timecounter
        timecounter_raw = timecounter + self._timecounter_additional_offset
        return self.get_box(context_id).start_wavegen(
            channels=channels, timecounter=timecounter_raw
        )

    def extract_wavedict_from_iq_reader(
        self, iq_reader: qi.CapIqDataReader
    ) -> dict[str, npt.NDArray[np.complex64]]:
        return deskew_tools.extract_wave_dict(iq_reader)

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
        self._box_name = self.__box.name
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
