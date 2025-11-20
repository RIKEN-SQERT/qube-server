from __future__ import annotations

import copy
from abc import abstractmethod
from enum import Enum, auto
from typing import NamedTuple, Optional, TypedDict, cast

import numpy as np
from labrad.devices import DeviceWrapper
from quel_ic_config import (
    CapParam,
    Quel1BoxType,
    Quel1PortType,
)
from quel_ic_config_utils import deskew_tools

from .box_connection import BoxConnection
from .constants import QSConstants, QSMessage


class DeviceType(Enum):
    ctrl = "control"
    readout = "readout"
    pump = "pump"
    readin = "readin"  # paired with readout, just used for creating device_name


class RfSwitchState(Enum):
    undefined = auto()
    loop = auto()
    open = auto()


ADC_DAC_PORT_PAIR_DICT: dict[Quel1BoxType, dict[Quel1PortType, Quel1PortType]] = {
    Quel1BoxType.QuEL1SE_RIKEN8: {0: 1},
    Quel1BoxType.QuEL1_TypeA: {0: 1, 7: 8},
    Quel1BoxType.QuEL1_TypeB: {},
}

DAC_PORT_TYPE_DICT: dict[Quel1BoxType, dict[Quel1PortType, DeviceType]] = {
    Quel1BoxType.QuEL1SE_RIKEN8: {
        0: DeviceType.readin,
        1: DeviceType.readout,
        (1, 1): DeviceType.ctrl,
        2: DeviceType.pump,
        3: DeviceType.ctrl,
        6: DeviceType.ctrl,
        7: DeviceType.ctrl,
        8: DeviceType.ctrl,
        9: DeviceType.ctrl,
    },
    Quel1BoxType.QuEL1_TypeA: {
        0: DeviceType.readin,
        1: DeviceType.readout,
        2: DeviceType.ctrl,
        3: DeviceType.pump,
        4: DeviceType.ctrl,
        7: DeviceType.readin,
        8: DeviceType.readout,
        9: DeviceType.ctrl,
        10: DeviceType.pump,
        11: DeviceType.ctrl,
    },
    Quel1BoxType.QuEL1_TypeB: {
        1: DeviceType.ctrl,
        2: DeviceType.ctrl,
        3: DeviceType.ctrl,
        4: DeviceType.ctrl,
        8: DeviceType.ctrl,
        9: DeviceType.ctrl,
        10: DeviceType.ctrl,
        11: DeviceType.ctrl,
    },
}


class DeviceConnectionInfo(NamedTuple):
    """A set of Arguments for DeviceWrapper.connect"""

    name: str
    args: DeviceConnectionInfoArgs
    kwargs: DeviceConnectionInfoKwargs


class DeviceConnectionInfoArgs(NamedTuple):
    box_conn: BoxConnection
    device_type: DeviceType
    port_in: Optional[Quel1PortType]
    port_out: Optional[Quel1PortType]


class DeviceConnectionInfoKwargs(TypedDict): ...


def create_device_connection_infos_from_box_connection(
    box_conn: BoxConnection,
) -> list[DeviceConnectionInfo]:
    dev_conn_infos: list[DeviceConnectionInfo] = []

    def _create_boxport_str(box_port: Quel1PortType, port_prefix: str = "") -> str:
        if isinstance(box_port, int):
            return f"{port_prefix}{box_port:02d}"
        elif isinstance(box_port, tuple) and len(box_port) == 2:
            return f"{port_prefix}{box_port[0]:02d}-{box_port[1]:02d}"

    port_type_dict = DAC_PORT_TYPE_DICT[
        Quel1BoxType.fromstr(box_conn.box_unsafe.boxtype)
    ]
    port_pair_dict = ADC_DAC_PORT_PAIR_DICT[
        Quel1BoxType.fromstr(box_conn.box_unsafe.boxtype)
    ]

    all_output_ports = box_conn.box_unsafe.get_output_ports()

    for input_port in box_conn.box_unsafe.get_read_input_ports():
        if input_port in port_pair_dict:
            paired_output_port = port_pair_dict[input_port]
        else:
            continue

        input_port_type = port_type_dict[input_port]
        output_port_type = port_type_dict[paired_output_port]
        all_output_ports.remove(paired_output_port)

        name = f"{box_conn.box_name}-{_create_boxport_str(input_port, port_prefix=f'{input_port_type.value}_')}-{_create_boxport_str(paired_output_port, port_prefix=f'{output_port_type.value}_')}"
        dev_conn_infos.append(
            DeviceConnectionInfo(
                name=name,
                args=DeviceConnectionInfoArgs(
                    box_conn=box_conn,
                    device_type=output_port_type,
                    port_in=input_port,
                    port_out=paired_output_port,
                ),
                kwargs=DeviceConnectionInfoKwargs(),
            )
        )

    for output_port in all_output_ports:
        output_port_type = port_type_dict[output_port]
        name = f"{box_conn.box_name}-{_create_boxport_str(output_port, port_prefix=f'{output_port_type.value}_')}"
        dev_conn_infos.append(
            DeviceConnectionInfo(
                name=name,
                args=DeviceConnectionInfoArgs(
                    box_conn=box_conn,
                    device_type=output_port_type,
                    port_in=None,
                    port_out=output_port,
                ),
                kwargs=DeviceConnectionInfoKwargs(),
            )
        )
    return dev_conn_infos


class QuBE_DeviceBase(DeviceWrapper):
    def connect(self, *args, **kwargs):
        args = DeviceConnectionInfoArgs(*args)
        kwargs = cast(DeviceConnectionInfoKwargs, kwargs)

        print(QSMessage.CONNECTING_CHANNEL.format(self.name))
        self.box_conn: BoxConnection = args.box_conn
        self._type = args.device_type
        self._delay_offset: int = 0
        self._delay_compensator = deskew_tools.E7awgDelayCompensator()
        self._initialize(args)
        print(QSMessage.CONNECTED_CHANNEL.format(self.name))

    @abstractmethod
    def _initialize(self, args: DeviceConnectionInfoArgs): ...

    @property
    def device_type(self) -> DeviceType:
        return self._type

    @property
    def chassis_name(self):
        return self.box_conn.box_name

    def static_check_value(self, value, resolution, multiplier=50, include_zero=False):
        resp = resolution > multiplier * abs(
            ((2 * value + resolution) % (2 * resolution)) - resolution
        )
        if resp:
            resp = (
                ((2 * value + resolution) // (2 * resolution)) > 0
                if not include_zero
                else True
            )
        return resp

    @property
    def number_of_shots(self):  # @property
        return int(self._shots)

    @number_of_shots.setter
    def number_of_shots(self, value):  # @number_of_shots.setter
        self._shots = int(value)

    @property
    def repetition_time(self):  # @property
        return int(self._reptime)

    @repetition_time.setter
    def repetition_time(self, value_in_ns):  # @repetition_time.setter
        self._reptime = (
            int(value_in_ns / QSConstants.DAQ_REPT_RESOL + 0.5)
            * QSConstants.DAQ_REPT_RESOL
        )

    @property
    def sequence_length(self):  # @property
        return int(self._seqlen)

    @sequence_length.setter
    def sequence_length(self, value):  # @sequence_length.setter
        self._seqlen = value

    @property
    def delay_offset(self):
        return self._delay_offset

    @delay_offset.setter
    def delay_offset(self, value_in_ps):
        self._delay_offset = int(value_in_ps)


class QuBE_ControlPort(QuBE_DeviceBase):
    def _initialize(self, args: DeviceConnectionInfoArgs):
        assert args.port_out is not None
        self.port_out: Quel1PortType = args.port_out

    @property
    def channels_of_port(self):
        return self.box_conn.box_unsafe.get_channels_of_port(self.port_out)

    def check_waveform(self, waveforms, channels):
        chans, length = waveforms.shape

        errors = []
        if not chans == len(channels):
            errors.append(
                QSMessage.ERR_INVALID_WAVD_INCONSISTENT_CH_WF.format(
                    chans, len(channels)
                )
            )
        if not all(c in self.channels_of_port for c in channels):
            errors.append(QSMessage.ERR_INVALID_WAVD_NOT_ALL_PORT_CHANNELS)
        if not QSConstants.DAC_WVSAMP_IVL * length == self.sequence_length:
            errors.append(QSMessage.ERR_INVALID_WAVD_MISMATCHED_LEN)
        if not (
            length % (QSConstants.DAQ_SEQL_RESOL // QSConstants.DAC_WVSAMP_IVL) == 0
        ):
            errors.append(QSMessage.ERR_INVALID_WAVD_LENGTH_DIV)

        max_amp = np.max(np.abs(waveforms))
        if not max_amp < 1.0:
            errors.append(QSMessage.ERR_INVALID_WAVD_MAGNITUDE.format(max_amp))

        return (errors, chans, length)

    def upload_waveform(self, waveform, channel):
        wait_words = int(
            (
                (self.repetition_time - self.sequence_length)
                + QSConstants.DAC_WORD_IVL / 2
            )
            // QSConstants.DAC_WORD_IVL
        )
        iq = QSConstants.DAC_BITS_POW_HALF * waveform
        self.box_conn.prepare_wave_generation(
            self.port_out,
            channel,
            iq,
            wait_words,
            num_repeat=self.number_of_shots,
            delay_ps=self._delay_offset,
        )
        return True

    def static_check_repetition_time(self, reptime_in_nanosec):
        resolution = QSConstants.DAQ_REPT_RESOL
        return self.static_check_value(reptime_in_nanosec, resolution)

    def static_check_sequence_length(self, seqlen_in_nanosec):
        resolution = QSConstants.DAQ_SEQL_RESOL
        resp = self.static_check_value(seqlen_in_nanosec, resolution)
        if resp:
            resp = seqlen_in_nanosec < QSConstants.DAQ_MAXLEN
        return resp

    def get_lo_frequency(self):
        dumped = self.box_conn.box_unsafe.dump_port(self.port_out)
        return dumped["lo_freq"]

    def set_lo_frequency(self, freq):
        self.box_conn.box_unsafe.config_port(self.port_out, lo_freq=freq)

    def get_mix_sideband(self):
        dumped = self.box_conn.box_unsafe.dump_port(self.port_out)
        if dumped["sideband"] == "U":
            return QSConstants.CNL_MXUSB_VAL
        else:
            return QSConstants.CNL_MXLSB_VAL

    def set_mix_sideband(self, sideband: str):
        if sideband == QSConstants.CNL_MXUSB_VAL:
            qwsb = "U"
        else:
            qwsb = "L"
        self.box_conn.box_unsafe.config_port(self.port_out, sideband=qwsb)

    def get_dac_coarse_frequency(self):
        dumped = self.box_conn.box_unsafe.dump_port(self.port_out)
        return dumped["cnco_freq"]

    def set_dac_coarse_frequency(self, freq):
        self.box_conn.box_unsafe.config_port(self.port_out, cnco_freq=freq)

    def get_dac_fine_frequency(self, channel):
        dumped = self.box_conn.box_unsafe.dump_port(self.port_out)
        return dumped["channels"][channel]["fnco_freq"]

    def set_dac_fine_frequency(self, channel, freq):
        self.box_conn.box_unsafe.config_channel(self.port_out, channel, fnco_freq=freq)

    def static_check_lo_frequency(self, freq):
        resolution = QSConstants.DAQ_LO_RESOL
        freq_in_mhz = freq / 1_000_000
        return self.static_check_value(freq_in_mhz, resolution)

    def static_check_dac_coarse_frequency(self, freq):
        resolution = QSConstants.DAC_CNCO_RESOL
        freq_in_mhz = freq / 1_000_000
        return self.static_check_value(freq_in_mhz, resolution)

    def static_check_dac_fine_frequency(self, freq):
        resolution = QSConstants.DAC_FNCO_RESOL
        freq_in_mhz = freq / 1_000_000
        resp = self.static_check_value(freq_in_mhz, resolution, include_zero=True)
        return resp

    def set_vatt(self, vatt: int):
        self.box_conn.box_unsafe.config_port(self.port_out, vatt=vatt)

    def get_vatt(self) -> int:
        dumped = self.box_conn.box_unsafe.dump_port(self.port_out)
        return dumped["vatt"]

    def set_fullscale_current(self, fullscale_current: int):
        self.box_conn.box_unsafe.config_port(
            self.port_out, fullscale_current=fullscale_current
        )

    def get_fullscale_current(self) -> int:
        dumped = self.box_conn.box_unsafe.dump_port(self.port_out)
        return dumped["fullscale_current"]


class QuBE_ReadoutPort(QuBE_ControlPort):
    def _initialize(self, args: DeviceConnectionInfoArgs):
        self.box_conn: BoxConnection = args.box_conn
        assert args.port_in is not None
        self.port_in: Quel1PortType = args.port_in
        assert args.port_out is not None
        self.port_out: Quel1PortType = args.port_out

        self._window = [QSConstants.ACQ_INITWINDOW for i in range(QSConstants.ACQ_MULP)]
        self._window_coefs = [
            QSConstants.ACQ_INITWINDCOEF for i in range(QSConstants.ACQ_MULP)
        ]
        self._fir_coefs = [
            QSConstants.ACQ_INITFIRCOEF for i in range(QSConstants.ACQ_MULP)
        ]
        self._acq_mode = [QSConstants.ACQ_INITMODE for i in range(QSConstants.ACQ_MULP)]

    @property
    def runits_of_port(self):
        return self.box_conn.box_unsafe.get_runits_of_port(self.port_in)

    @property
    def acquisition_window(self):  # @property
        return copy.copy(self._window)

    def set_acquisition_window(self, mux, window):
        self._window[mux] = window

    @property
    def acquisition_mode(self):  # @property, only referenced in QuBE_Server
        return copy.copy(self._acq_mode)  # .acquisition_mode() for @setting 303

    def set_acquisition_mode(self, mux, mode):
        self._acq_mode[mux] = mode

    def set_acquisition_fir_coefficient(self, muxch, coeffs):
        self._fir_coefs[muxch] = coeffs

    def set_acquisition_window_coefficient(self, muxch, coeffs):
        self._window_coefs[muxch] = coeffs

    def upload_readout_parameters(self, muxch):
        """
        Upload readout parameters

        *Note for other guys

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
        decim, averg, summn = QSConstants.ACQ_MODEFUNC[self._acq_mode[muxch]]
        cap_param = self.box_conn.update_dsp_mode_setting(
            CapParam(),
            np.array(self._fir_coefs[muxch]),
            (0, (len(self._window_coefs[muxch]) // 4) - 1),
            self._window_coefs[muxch],
            self.number_of_shots,
            decim,
            averg,
            summn,
        )

        self.box_conn.prepare_capture(
            self.port_in,
            muxch,
            self.acquisition_window[muxch],
            self.repetition_time,
            cap_param,
            self.number_of_shots,
            self.delay_offset,
        )
        return True

    def set_adc_coarse_frequency(self, freq):
        self.box_conn.box_unsafe.config_port(self.port_in, cnco_freq=freq)

    def get_adc_coarse_frequency(self):
        dumped = self.box_conn.box_unsafe.dump_port(self.port_in)
        return dumped["cnco_freq"]

    def get_rfswitch(self) -> RfSwitchState:
        state_in = self.box_conn.box_unsafe.dump_rfswitch(self.port_in)
        state_out = self.box_conn.box_unsafe.dump_rfswitch(self.port_out)
        if (state_in, state_out) == ("loop", "block"):
            return RfSwitchState.loop
        elif (state_in, state_out) == ("open", "pass"):
            return RfSwitchState.open
        return RfSwitchState.undefined

    def set_rfswitch(self, state: RfSwitchState):
        if state is RfSwitchState.open:
            in_switch_str, out_switch_str = "open", "pass"
        elif state is RfSwitchState.loop:
            in_switch_str, out_switch_str = "loop", "block"
        else:
            raise ValueError(f"Unavailable switch state: {state}")
        self.box_conn.box_unsafe.config_rfswitch(self.port_in, rfswitch=in_switch_str)
        self.box_conn.box_unsafe.config_rfswitch(self.port_out, rfswitch=out_switch_str)

    def static_check_adc_coarse_frequency(self, freq):
        resolution = QSConstants.ADC_CNCO_RESOL
        freq_in_mhz = freq / 1_000_000
        return self.static_check_value(freq_in_mhz, resolution)

    def static_check_acquisition_windows(self, list_of_windows):
        def check_value(w):
            return False if 0 != w % QSConstants.ACQ_CAPW_RESOL else True

        def check_duration(start, end):
            return (
                False
                if start > end or end - start > QSConstants.ACQ_MAXWINDOW
                else True
            )

        if 0 != list_of_windows[0][0] % QSConstants.ACQ_CAST_RESOL:
            return False

        for _s, _e in list_of_windows:
            if not check_value(_s) or not check_value(_e) or not check_duration(_s, _e):
                return False

        return True

    def static_check_acquisition_fir_coefs(self, coeffs):
        length = len(coeffs)

        resp = QSConstants.ACQ_MAX_FCOEF >= length
        if resp:
            resp = 1.0 > np.max(np.abs(coeffs))
        return resp

    def static_check_acquisition_window_coefs(self, coeffs):
        length = len(coeffs)

        resp = QSConstants.ACQ_MAX_WCOEF >= length
        if resp:
            resp = 1.0 > np.max(np.abs(coeffs))
        return resp


class QuBE_PumpPort(QuBE_ControlPort): ...
