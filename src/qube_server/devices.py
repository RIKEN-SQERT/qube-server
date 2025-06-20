from __future__ import annotations

import copy
from abc import abstractmethod
from enum import Enum, auto
from typing import NamedTuple, Optional, TypedDict, cast

import numpy as np
from labrad.devices import DeviceWrapper
from quel_ic_config import AwgParam, CapParam, CapSection, Quel1PortType, WaveChunk

from .box_connection import BoxConnection
from .constants import QSConstants, QSMessage


class DeviceType(Enum):
    ctrl = auto()
    readout = auto()
    fogi = auto()
    pump = auto()


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

    for input_port in box_conn._box.get_read_input_ports():
        paired_output_ports = box_conn._box.get_loopbacks_of_port(input_port)
        if len(paired_output_ports) < 1:
            continue
        paired_output_port = paired_output_ports.pop()

        name = f"{box_conn.box_name}-{_create_boxport_str(input_port, port_prefix='in')}-{_create_boxport_str(paired_output_port, port_prefix='out')}"
        dev_conn_infos.append(
            DeviceConnectionInfo(
                name=name,
                args=DeviceConnectionInfoArgs(
                    box_conn=box_conn,
                    device_type=DeviceType.readout,
                    port_in=input_port,
                    port_out=paired_output_port,
                ),
                kwargs=DeviceConnectionInfoKwargs(),
            )
        )

    for output_port in box_conn._box.get_output_ports():
        name = (
            f"{box_conn.box_name}-{_create_boxport_str(output_port, port_prefix='out')}"
        )
        dev_conn_infos.append(
            DeviceConnectionInfo(
                name=name,
                args=DeviceConnectionInfoArgs(
                    box_conn=box_conn,
                    device_type=DeviceType.ctrl,
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


class QuBE_ControlPort(QuBE_DeviceBase):
    def _initialize(self, args: DeviceConnectionInfoArgs):
        assert args.port_out is not None
        self.port_out: Quel1PortType = args.port_out

    @property
    def channels_of_port(self):
        return self.box_conn.box_unsafe.get_channels_of_port(self.port_out)

    # TODO: check later
    def check_waveform(self, waveforms, channels):
        chans, length = waveforms.shape

        help = 1
        resp = chans == len(channels)
        if resp:
            resp = all(c in self.channels_of_port for c in channels)
            help += 1
        if resp:
            resp = QSConstants.DAC_WVSAMP_IVL * length == self.sequence_length
            help += 1
        if resp:
            block_restriction = QSConstants.DAQ_SEQL_RESOL // QSConstants.DAC_WVSAMP_IVL
            resp = 0 == length % block_restriction
            help += 1
        if resp:
            resp = np.max(np.abs(waveforms)) < 1.0
            help += 1
        if resp:
            return (True, chans, length)
        else:
            return (False, help, None)

    def upload_waveform(self, waveform, channel):
        wait_words = int(
            (
                (self.repetition_time - self.sequence_length)
                + QSConstants.DAC_WORD_IVL / 2
            )
            // QSConstants.DAC_WORD_IVL
        )

        awg_param = AwgParam(num_wait_word=0, num_repeat=self.number_of_shots)
        waveform_name = f"{self.name}-{channel}"
        self.box_conn.box_unsafe.register_wavedata(
            port=self.port_out, channel=channel, name=waveform_name, iq=waveform
        )
        awg_param.chunks.append(
            WaveChunk(
                name_of_wavedata=waveform_name, num_blank_word=wait_words, num_repeat=1
            )
        )
        self.box_conn.box_unsafe.config_channel(
            port=self.port_out, channel=channel, awg_param=awg_param
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
        def fircoef_DACify(coeffs):
            return (np.real(coeffs) * QSConstants.ACQ_FCBIT_POW_HALF).astype(
                int
            ) + 1j * (np.imag(coeffs) * QSConstants.ACQ_FCBIT_POW_HALF).astype(int)

        self._fir_coefs[muxch] = fircoef_DACify(coeffs)

    def set_acquisition_window_coefficient(self, muxch, coeffs):
        def window_DACify(coeffs):
            return (np.real(coeffs) * QSConstants.ACQ_WCBIT_POW_HALF).astype(
                int
            ) + 1j * (np.imag(coeffs) * QSConstants.ACQ_WCBIT_POW_HALF).astype(int)

        self._window_coefs[muxch] = window_DACify(coeffs)

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
        repetition_word = int(
            (self.repetition_time + QSConstants.ACQ_CAPW_RESOL // 2)
            // QSConstants.ACQ_CAPW_RESOL
        )
        param = CapParam()
        win_word = list()
        for _s, _e in self.acquisition_window[
            muxch
        ]:  # flatten window (start,end) to a series
            # of timestamps
            win_word.append(
                int((_s + QSConstants.ACQ_CAPW_RESOL / 2) // QSConstants.ACQ_CAPW_RESOL)
            )
            win_word.append(
                int((_e + QSConstants.ACQ_CAPW_RESOL / 2) // QSConstants.ACQ_CAPW_RESOL)
            )
        win_word.append(repetition_word)

        param.num_repeat = int(self.number_of_shots)
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
                CapSection(
                    name=f"{idx}",
                    num_capture_word=section_length,
                    num_blank_word=blank_length,
                )
            )
            idx += 1

        self.configure_readout_mode(muxch, param, self._acq_mode[muxch])
        self.box_conn.box_unsafe.config_runit(
            port=self.port_in, runit=muxch, capture_param=param
        )
        return True

    def configure_readout_mode(self, mux, param: CapParam, mode):
        """
        Configure readout parametes to acquisition modes.

        It enables and disables decimation, averaging, and summation operations with
        filter coefficients and the number of averaging.

        Args:
            param     : e7awgsw.captureparam.CaptureParam
            mode      : character
                Acceptable parameters are '1', '2', '3', 'A', 'B'
        """
        decim, averg, summn = QSConstants.ACQ_MODEFUNC[mode]

        if decim:
            # [Decimation] 500MSa/s datapoints are reduced to 125 MSa/s (8ns interval)
            param.realfirs_real_coeff = self._fir_coefs[mux].real
            param.realfirs_imag_coeff = self._fir_coefs[mux].imag
            param.realfirs_enable = True
            param.decimation_enable = True
        if averg:
            # [Averaging] Averaging datapoints for all experiments.
            param.integration_enable = True
            param.num_repeat = int(self.number_of_shots)
        if summn:
            # [Summation] For a given readout window, the DSP apply complex window filter.
            # (This is equivalent to the convolution in frequency domain of a filter
            # function with frequency offset). Then, DSP sums all the datapoints
            # in the readout window.
            # resp = self.configure_readout_summation(mux, param, summn)
            param.sum_enable = True
            param.sum_range = (0, param.num_section)
            param.window_enable = True
            param.window_coeff = self._window_coefs[mux]

    def set_adc_coarse_frequency(self, freq):
        self.box_conn.box_unsafe.config_port(self.port_in, cnco_freq=freq)

    def get_adc_coarse_frequency(self):
        dumped = self.box_conn.box_unsafe.dump_port(self.port_in)
        return dumped["cnco_freq"]

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


class QuBE_FogiPort(QuBE_ControlPort): ...


class QuBE_PumpPort(QuBE_ControlPort): ...
