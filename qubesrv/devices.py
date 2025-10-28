import sys
import copy

import numpy as np

from labrad.devices import DeviceWrapper

from twisted.internet.defer import inlineCallbacks, returnValue

from e7awgsw import (
    DspUnit,
    WaveSequence,
    CaptureParam,
)

from quel_ic_config import Quel1Box

from constants import QSConstants, QSMessage

############################################################
#
# DEVICE WRAPPERS
#
# X class tree
#
# labrad.devices.DeviceWrapper
#  |
#  + QuBE_DeviceBase
#    |
#    + QuBE_ControlFPGA -.
#    |                    .
#    |                     +-+-- QuBE_ControlLine ----------.
#    |                    /  |    |                          .
#    + QuBE_ControlLSI --/   |    + QuBE_ReadoutLine ----+--- .-- QuBE_ReadoutLine_debug_otasuke
#                            |                          /      .
#                            +-- QuBE_Device_debug_otasuke ----+- QuBE_ControlLine_debug_otasuke
#
class QuBE_DeviceBase(DeviceWrapper):
    @inlineCallbacks
    def connect(self, *args, **kw):  # @inlineCallbacks
        name, role = args
        self._name = name
        self._role = role
        self._chassis = kw["chassis"]

        print(QSMessage.CONNECTING_CHANNEL.format(name))
        yield self.get_connected(*args, **kw)
        yield print(QSMessage.CONNECTED_CHANNEL.format(self._name))

    @inlineCallbacks
    def get_connected(self, *args, **kwargs):  # @inlineCallbacks

        yield

    @property
    def device_name(self):  # @property
        return self._name

    @property
    def device_role(self):  # @property
        return self._role

    @property
    def chassis_name(self):
        return self._chassis

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


class QuBE_Control_FPGA(QuBE_DeviceBase):

    @inlineCallbacks
    def get_connected(self, *args, **kw):  # @inlineCallbacks

        yield super(QuBE_Control_FPGA, self).get_connected(*args, **kw)

        self.__initialized = False
        try:
            self._shots = QSConstants.DAQ_INITSHOTS
            self._reptime = QSConstants.DAQ_INITREPTIME
            self._seqlen = QSConstants.DAQ_INITLEN

            self._awg_ctrl = kw["awg_ctrl"]
            self._awg_ch_ids = kw["awg_ch_ids"]
            self._awg_chs = len(self._awg_ch_ids)

            self.__initialized = True
        except Exception as e:
            print(sys._getframe().f_code.co_name, e)

        if self.__initialized:
            pass
        yield

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
        self._reptime = int(
            (
                (value_in_ns + QSConstants.DAQ_REPT_RESOL / 2)
                // QSConstants.DAQ_REPT_RESOL
            )
            * QSConstants.DAQ_REPT_RESOL
        )

    @property
    def sequence_length(self):  # @property
        return int(self._seqlen)

    @sequence_length.setter
    def sequence_length(self, value):  # @sequence_length.setter
        self._seqlen = value

    @property
    def number_of_awgs(self):  # @property
        return self._awg_chs

    def get_awg_id(self, channel):
        return self._awg_ch_ids[channel]

    def check_awg_channels(self, channels):
        for _c in channels:
            if _c < 0 or self.number_of_awgs <= _c:
                return False
        return True

    def check_waveform(self, waveforms, channels):
        chans, length = waveforms.shape

        help = 1
        resp = chans == len(channels)
        if resp:
            resp = chans <= self.number_of_awgs
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

    def upload_waveform(self, waveforms, channels):

        wait_words = int(
            (
                (self.repetition_time - self.sequence_length)
                + QSConstants.DAC_WORD_IVL / 2
            )
            // QSConstants.DAC_WORD_IVL
        )

        for _waveform, _channel in zip(waveforms, channels):
            wave_seq = WaveSequence(num_wait_words=0, num_repeats=self.number_of_shots)
            iq_samples = list(zip(*self.static_DACify(_waveform)))
            wave_seq.add_chunk(
                iq_samples=iq_samples, num_blank_words=wait_words, num_repeats=1
            )
            self._awg_ctrl.set_wave_sequence(self._awg_ch_ids[_channel], wave_seq)
        return True

    def start_daq(self, awg_ids):  # OBSOLETED. For multi-chassis
        self._awg_ctrl.start_awgs(*awg_ids)  # operation, synchronization has
        # to be made using SequencerClinet.

    def stop_daq(self, awg_ids, timeout):
        self._awg_ctrl.wait_for_awgs_to_stop(timeout, *awg_ids)
        self._awg_ctrl.clear_awg_stop_flags(*awg_ids)

    def terminate_daq(self, awg_ids):
        self._awg_ctrl.terminate_awgs(*awg_ids)
        self._awg_ctrl.clear_awg_stop_flags(*awg_ids)

    def static_DACify(self, waveform):
        return (
            (np.real(waveform) * QSConstants.DAC_BITS_POW_HALF).astype(int),
            (np.imag(waveform) * QSConstants.DAC_BITS_POW_HALF).astype(int),
        )

    def static_check_repetition_time(self, reptime_in_nanosec):
        resolution = QSConstants.DAQ_REPT_RESOL
        return self.static_check_value(reptime_in_nanosec, resolution)

    def static_check_sequence_length(self, seqlen_in_nanosec):
        resolution = QSConstants.DAQ_SEQL_RESOL
        resp = self.static_check_value(seqlen_in_nanosec, resolution)
        if resp:
            resp = seqlen_in_nanosec < QSConstants.DAQ_MAXLEN
        return resp


import traceback

class QuBE_Control_LSI(QuBE_DeviceBase):

    @inlineCallbacks
    def get_connected(self, *args, **kw):  # @inlineCallbacks

        yield super(QuBE_Control_LSI, self).get_connected(*args, **kw)

        try:
            ipfpga = kw["ipfpga"]
            iplsi = kw["iplsi"]
            ipsync = kw["ipsync"]
            device_type=kw["device_type"]

            box = Quel1Box.create(
                ipaddr_wss=ipfpga,
                #ipaddr_sss=ipsync,
                #ipaddr_css=iplsi,
                boxtype=device_type,
            )
            # TODO: reconnect
            box.reconnect()
            # use only box.css
            self._css = box.css
            self._group = kw["group"]
            self._line = kw["line"]
            self._rline = kw["rline"]

            # # DEBUG: for buffered operation, not used.
            # self._lo_frequency = self.get_lo_frequency()
            # self._coarse_frequency = self.get_dac_coarse_frequency()
            # # DEBUG: for buffered operation, partly used.

        except Exception as e:
            print("Exception!!!!!!!!")
            print(sys._getframe().f_code.co_name, e)
            traceback.print_exc() # DEBUG

        yield

    def get_lo_frequency(self):
        return self._css.get_lo_multiplier(self._group, self._line) * 100

    def set_lo_frequency(self, freq_in_mhz):
        return self._css.set_lo_multiplier(self._group, self._line, int(freq_in_mhz // 100))

    def get_mix_sideband(self):
        resp = self._css.get_sideband(self._group, self._line)
        if resp == "U":
            return QSConstants.CNL_MXUSB_VAL
        else:
            return QSConstants.CNL_MXLSB_VAL

    def set_mix_sideband(self, sideband: str):
        if sideband == QSConstants.CNL_MXUSB_VAL:
            qwsb = "U"
        else:
            qwsb = "L"
        self._css.set_sideband(self._group, self._line, qwsb)
        #self._mix_usb_lsb = sideband

    def get_dac_coarse_frequency(self):
        return self._css.get_dac_cnco(self._group, self._line) / 1e6

    def set_dac_coarse_frequency(self, freq_in_mhz):
        self._css.set_dac_cnco(self._group, self._line, 1e6 * freq_in_mhz)
        self._coarse_frequency = freq_in_mhz

    def get_dac_fine_frequency(self, channel):
        return self._css.get_dac_fnco(self._group, self._line, channel) / 1e6

    def set_dac_fine_frequency(self, channel, freq_in_mhz):
        self._css.set_dac_fnco(self._group, self._line, channel, 1e6 * freq_in_mhz)

    def static_check_lo_frequency(self, freq_in_mhz):
        resolution = QSConstants.DAQ_LO_RESOL
        return self.static_check_value(freq_in_mhz, resolution)

    def static_check_dac_coarse_frequency(self, freq_in_mhz):
        resolution = QSConstants.DAC_CNCO_RESOL
        return self.static_check_value(freq_in_mhz, resolution)

    def static_check_dac_fine_frequency(self, freq_in_mhz):
        resolution = QSConstants.DAC_FNCO_RESOL
        resp = self.static_check_value(freq_in_mhz, resolution, include_zero=True)
        return resp

class QuBE_ControlLine(QuBE_Control_FPGA, QuBE_Control_LSI):

    @inlineCallbacks
    def get_connected(self, *args, **kw):  # @inlineCallbacks
        super(QuBE_ControlLine, self).get_connected(*args, **kw)
        yield


class QuBE_ReadoutLine(QuBE_ControlLine):

    @inlineCallbacks
    def get_connected(self, *args, **kw):  # @inlineCallbacks

        yield super(QuBE_ReadoutLine, self).get_connected(*args, **kw)

        self.__initialized = False
        try:

            # TODO: 後で消す
            #print("start QuBE_ReadoutLine")

            self._cap_ctrl = kw["cap_ctrl"]
            self._cap_mod_id = kw["cap_mod_id"]
            self._cap_unit = kw["capture_units"]

            # TODO: 後で消す
            ##print("QuBE_ReadoutLine kw:", kw)
            self._rx_coarse_frequency = self.get_adc_coarse_frequency()
            #print(self._name,'rxnco',self._rx_coarse_frequency)
            self.__initialized = True
        except Exception as e:
            print("Exception: QuBE_ReadoutLine!!!!!!!!")
            print(sys._getframe().f_code.co_name, e)

        if self.__initialized:
            self._window = [
                QSConstants.ACQ_INITWINDOW for i in range(QSConstants.ACQ_MULP)
            ]
            self._window_coefs = [
                QSConstants.ACQ_INITWINDCOEF for i in range(QSConstants.ACQ_MULP)
            ]
            self._fir_coefs = [
                QSConstants.ACQ_INITFIRCOEF for i in range(QSConstants.ACQ_MULP)
            ]
            self._acq_mode = [
                QSConstants.ACQ_INITMODE for i in range(QSConstants.ACQ_MULP)
            ]

    def get_capture_module_id(self):
        return self._cap_mod_id

    def get_capture_unit_id(self, mux_channel):
        return self._cap_unit[mux_channel]

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

    def upload_readout_parameters(self, muxchs):
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
        for mux in muxchs:
            param = CaptureParam()
            win_word = list()
            for _s, _e in self.acquisition_window[
                mux
            ]:  # flatten window (start,end) to a series
                # of timestamps
                win_word.append(
                    int(
                        (_s + QSConstants.ACQ_CAPW_RESOL / 2)
                        // QSConstants.ACQ_CAPW_RESOL
                    )
                )
                win_word.append(
                    int(
                        (_e + QSConstants.ACQ_CAPW_RESOL / 2)
                        // QSConstants.ACQ_CAPW_RESOL
                    )
                )
            win_word.append(repetition_word)

            param.num_integ_sections = int(self.number_of_shots)
            _s0 = win_word.pop(0)
            param.capture_delay = _s0
            win_word[-1] += _s0  # win_word[-1] is the end time of a sin-
            # gle sequence. As the repeat duration
            # is offset by capture_delay, we have to
            # add the capture_delay time.
            while len(win_word) > 1:
                _e = win_word.pop(0)
                _s = win_word.pop(0)
                blank_length = _s - _e
                section_length = _e - _s0
                _s0 = _s
                param.add_sum_section(section_length, blank_length)

            self.configure_readout_mode(mux, param, self._acq_mode[mux])
            # import pickle
            # import base64
            # print('mux setup')
            # print(base64.b64encode(pickle.dumps(param)))
            self._cap_ctrl.set_capture_params(self._cap_unit[mux], param)
        return True

    def configure_readout_mode(self, mux, param, mode):
        """
        Configure readout parametes to acquisition modes.

        It enables and disables decimation, averaging, and summation operations with
        filter coefficients and the number of averaging.

        Args:
            param     : e7awgsw.captureparam.CaptureParam
            mode      : character
                Acceptable parameters are '1', '2', '3', 'A', 'B'
        """
        dsp = self.configure_readout_dsp(mux, param, mode)
        param.sel_dsp_units_to_enable(*dsp)

    def configure_readout_dsp(self, mux, param, mode):
        dsp = []
        decim, averg, summn = QSConstants.ACQ_MODEFUNC[mode]

        resp = self.configure_readout_decimation(mux, param, decim)
        dsp.extend(resp)
        resp = self.configure_readout_averaging(mux, param, averg)
        dsp.extend(resp)
        resp = self.configure_readout_summation(mux, param, summn)
        dsp.extend(resp)
        return dsp

    def configure_readout_decimation(self, mux, param, decimation):
        """
        Configure readout mux channel parameters.

        [Decimation] 500MSa/s datapoints are reduced to 125 MSa/s (8ns interval)

        Args:
            param     : e7awgsw.captureparam.CaptureParam
            decimation: bool
        Returns:
            dsp       : list.
                The list of enabled e7awgsw.hwdefs.DspUnit objects
        """
        dsp = list()
        if decimation:
            param.complex_fir_coefs = list(self._fir_coefs[mux])
            dsp.append(DspUnit.COMPLEX_FIR)
            dsp.append(DspUnit.DECIMATION)
        return dsp

    def configure_readout_averaging(self, mux, param, averaging):
        """
        Configure readout mux channel parameters.

        [Averaging] Averaging datapoints for all experiments.

        Args:
            param    : e7awgsw.captureparam.CaptureParam
            average  : bool
        Returns:
            dsp      : list.
                The list of enabled e7awgsw.hwdefs.DspUnit objects
        """
        dsp = list()
        if averaging:
            dsp.append(DspUnit.INTEGRATION)
        param.num_integ_sections = int(self.number_of_shots)
        return dsp

    def configure_readout_summation(self, mux, param, summation):
        """
        Configure readout mux channel parameters.

        [Summation] For a given readout window, the DSP apply complex window filter.
        (This is equivalent to the convolution in frequency domain of a filter
        function with frequency offset). Then, DSP sums all the datapoints
        in the readout window.

        Args:
            param    : e7awgsw.captureparam.CaptureParam
            summation: bool
        Returns:
            dsp      : list
                The list of enabled e7awgsw.hwdefs.DspUnit objects
        """
        dsp = list()
        if summation:
            param.sum_start_word_no = 0
            param.num_words_to_sum = CaptureParam.MAX_SUM_SECTION_LEN
            param.complex_window_coefs = list(self._window_coefs[mux])
            dsp.append(DspUnit.COMPLEX_WINDOW)
            dsp.append(DspUnit.SUM)
        else:
            pass
        return dsp

    def terminate_acquisition(self, unit_ids):
        self._cap_ctrl.terminate_capture_units(*unit_ids)

    def download_waveform(self, muxchs):
        """
        Download captured waveforms (datapoints)

        Transfer datapoints from FPGA to a host computer.

        Args:
            muxchs : List[int]
                A list of the readout mux channels for transfer.
        Returns:
            datapoints: *2c
                Two-dimensional complex data matrix. The row corrsponds to the
                readout mux channel and the column of the matrix is time dimention
                of datapoints.
        """

        vault = []
        for mux in muxchs:
            data = self.download_single_waveform(mux)
            vault.append(data)
        return np.vstack(vault)

    def download_single_waveform(self, muxch):
        capture_unit = self._cap_unit[muxch]

        n_of_samples = self._cap_ctrl.num_captured_samples(capture_unit)
        iq_tuple_data = self._cap_ctrl.get_capture_data(capture_unit, n_of_samples)

        return np.array([(_i + 1j * _q) for _i, _q in iq_tuple_data]).astype(complex)

    def set_trigger_board(self, trigger_board, enabled_capture_units):
        self._cap_ctrl.select_trigger_awg(self._cap_mod_id, trigger_board)
        self._cap_ctrl.enable_start_trigger(*enabled_capture_units)

    def set_adc_coarse_frequency(self, freq_in_mhz):
        self._css.set_adc_cnco(self._group, self._rline, 1e6 * freq_in_mhz)
        self._rx_coarse_frequency = freq_in_mhz  # DEBUG seems not used right now

    def get_adc_coarse_frequency(self):
        # FIXME: あとで直す。とりあえずrを固定で入れる
        return self._css.get_adc_cnco(self._group, "r") / 1e6

    def static_check_adc_coarse_frequency(self, freq_in_mhz):
        resolution = QSConstants.ADC_CNCO_RESOL
        return self.static_check_value(freq_in_mhz, resolution)

    def static_check_mux_channel_range(self, mux):
        return True if 0 <= mux and mux < QSConstants.ACQ_MULP else False

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

