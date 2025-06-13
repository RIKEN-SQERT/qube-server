import sys

import numpy as np
from labrad.server import setting
from twisted.internet.defer import inlineCallbacks, returnValue

from .constants import QSConstants, QSMessage
from .devices import QuBE_Control_FPGA, QuBE_Control_LSI, QuBE_ControlLine, QuBE_ReadoutLine
from .server import QuBE_Server


class QuBE_Device_debug_otasuke(QuBE_Control_FPGA, QuBE_Control_LSI):
    @inlineCallbacks
    def get_connected(self, *args, **kw):
        yield super(QuBE_Device_debug_otasuke, self).get_connected(*args, **kw)
        self.__initialized = False
        try:
            self.__switch_mask = kw["gsw_mask"]
            self.__switch_ctrl = kw["gsw_ctrl"]
            self.__initialized = True
        except Exception as e:
            print(sys._getframe().f_code.co_name, e)
        yield

    @inlineCallbacks
    def get_microwave_switch(self):
        mask = self.__switch_mask
        resp = self.__switch_ctrl.read_value()
        output = True
        if resp & mask == mask:
            output = False
        yield
        returnValue(output)

    @inlineCallbacks
    def set_microwave_switch(self, output):
        mask = self.__switch_mask
        resp = self.__switch_ctrl.read_value()
        if output:
            resp = resp & (0x3FFF ^ mask)
        else:
            resp = resp | mask
        yield self.__switch_ctrl.write_value(resp)


class QuBE_ControlLine_debug_otasuke(QuBE_ControlLine, QuBE_Device_debug_otasuke):
    @inlineCallbacks
    def get_connected(self, *args, **kw):  # @inlineCallbacks
        super(QuBE_ControlLine_debug_otasuke, self).get_connected(*args, **kw)
        yield


class QuBE_ReadoutLine_debug_otasuke(QuBE_ReadoutLine, QuBE_Device_debug_otasuke):
    @inlineCallbacks
    def get_connected(self, *args, **kw):  # @inlineCallbacks
        super(QuBE_ReadoutLine_debug_otasuke, self).get_connected(*args, **kw)
        yield


class QuBE_Server_debug_otasuke(QuBE_Server):
    deviceWrappers = {
        QSConstants.CNL_READ_VAL: QuBE_ReadoutLine_debug_otasuke,
        QSConstants.CNL_CTRL_VAL: QuBE_ControlLine_debug_otasuke,
    }

    def __init__(self, *args, **kw):
        QuBE_Server.__init__(self, *args, **kw)

    def instantiateChannel(self, name, channels, awg_ctrl, cap_ctrl, lsi_ctrl, info):
        devices = super(QuBE_Server_debug_otasuke, self).instantiateChannel(name, channels, awg_ctrl, cap_ctrl, lsi_ctrl, info)
        revised = []
        for device, channel in zip(devices, channels):
            name, args, kw = device
            _kw = dict(gsw_ctrl=lsi_ctrl.gpio, gsw_mask=channel[QSConstants.CNL_GPIOSW_TAG])
            kw.update(_kw)
            revised.append((name, args, kw))
        return revised

    @setting(
        502,
        "DEBUG AWG REG",
        addr=["w"],
        offset=["w"],
        pos=["w"],
        bits=["w"],
        data=["w"],
        returns=["w"],
    )
    def debug_awg_ctrl_reg(self, c, addr, offset, pos, bits, data=None):
        """
        Read and write to the AWG registers

        It is useful for debug but should not be used for daily operation.

        Args:

            addr  : w   0x00 for master control registers
            offset: w   0x00 [32bits] version
                        0x04 [16bits] control select
                        0x08 [4bits]  awg control. bit0 reset, bit 1 prepare,
                                                   bit2 start, bit 3 terminate
                        0x10 [16bits] busy
                        0x14 [16bits] ready
                        0x18 [16bits] done
            pos   : w   Bit location
            bits  : w   Number of bits to read/write
            data  : w   Data. Read operation is performed if None
        """
        dev = self.selectedDevice(c)
        reg = dev._awg_ctrl._AwgCtrl__reg_access  # DEBUG _awg_ctrl is a protected member
        if data is None:
            data = reg.read_bits(addr, offset, pos, bits)
            return data
        else:
            reg.write_bits(addr, offset, pos, bits, data)
        return 0

    @setting(
        501,
        "DEBUG CAP REG",
        addr=["w"],
        offset=["w"],
        pos=["w"],
        bits=["w"],
        data=["w"],
        returns=["w"],
    )
    def debug_cap_ctrl_reg(self, c, addr, offset, pos, bits, data=None):
        """
        Read and write to the AWG registers.

        It is useful for debug but should not be used for daily operation.

        Args:
            addr  : w    0x00 for Master control registers
            offset: w    Register index. See below
                         0x00 [32bits] version
                         0x04 [5bits ] select (mod0) bit0 no trig, bit1-4 daq id
                         0x08 [5bits ] select (mod1) bit0 no trig, bit1-4 daq id
                         0x0c [8bits]  trigger mask
                         0x10 [8bits] capmod select
                         0x1c [8bits] busy
                         0x20 [8bits] done
            pos   : w    Bit location
            bits  : w    Number of bits to read/write
            data  : w    Data. Read operation is performed if None
        """
        dev = self.selectedDevice(c)
        if QSConstants.CNL_READ_VAL != dev.device_role:
            raise Exception(QSMessage.ERR_INVALID_DEV.format("readout", dev.device_name))
        reg = dev._cap_ctrl._CaptureCtrl__reg_access  # DEBUG _cap_ctrl is a protected member
        if data is None:
            data = reg.read_bits(addr, offset, pos, bits)
            return data
        else:
            reg.write_bits(addr, offset, pos, bits, data)
        return 0

    @setting(
        503,
        "DEBUG Auto Acquisition FIR Coefficients",
        muxch=["w"],
        bb_frequency=["v[Hz]"],
        sigma=["v[s]"],
        returns=["b"],
    )
    def debug_auto_acquisition_fir_coefficients(self, c, muxch, bb_frequency, sigma=None):
        """
        Automatically set finite impulse resoponse filter coefficients.

        Set gauss-envelope FIR coefficients

        Args:
             muxch        : w
                Multiplex readout mux channel. 0-3 can be set

             bb_frequency : v[Hz]
                 The base-band frequency of the readout signal. It could be
                 f(readout) - f(local oscillator) - f(coase NCO frequency) when
                 upper-sideband modu- and demodulations are used.
        Returns:
             success      : b
        """

        if sigma is None:
            sigma = 3.0  # nanosecodnds

        freq_in_mhz = bb_frequency["MHz"]  # base-band frequency before decimation.
        if -QSConstants.ADCBB_SAMPLE_R / 2.0 >= freq_in_mhz or QSConstants.ADCBB_SAMPLE_R / 2.0 <= freq_in_mhz:
            raise Exception(
                QSMessage.ERR_INVALID_RANG.format(
                    "bb_frequency",
                    -QSConstants.ADCBB_SAMPLE_R / 2.0,
                    QSConstants.ADCBB_SAMPLE_R / 2.0,
                )
            )
        n_of_band = QSConstants.ACQ_MAX_FCOEF
        band_step = QSConstants.ADCBB_SAMPLE_R / n_of_band
        band_idx = int(freq_in_mhz / band_step + 0.5 + n_of_band) - n_of_band
        band_center = band_step * band_idx

        x = np.arange(QSConstants.ACQ_MAX_FCOEF) - (QSConstants.ACQ_MAX_FCOEF - 1) / 2  # symmetric in center.
        gaussian = np.exp(-0.5 * x**2 / (sigma**2))  # gaussian with sigma of [sigma]
        phase_factor = 2 * np.pi * (band_center / QSConstants.ADCBB_SAMPLE_R) * np.arange(QSConstants.ACQ_MAX_FCOEF)
        coeffs = gaussian * np.exp(1j * phase_factor) * (1 - 1e-3)

        return self.acquisition_fir_coefficients(c, muxch, coeffs)

    @setting(
        504,
        "DEBUG Auto Acquisition Window Coefficients",
        muxch=["w"],
        bb_frequency=["v[Hz]"],
        returns=["b"],
    )
    def debug_auto_acquisition_window_coefficients(self, c, muxch, bb_frequency):
        """
        Automatically set complex window coefficients

        debug_auto_acquisition_window_coefficients() sets rectangular window as a
        demodulation window. If you want to try windowed demodulation, it is better
        to give the coefs manually.

        *debug_auto_acquisition_window_coefficients() has to be called after
         acquisition_window().

        Args:
             muxch        : w
                 Multiplex readout mux channel. 0-3 can be set

             bb_frequency : v[Hz]
                 The base-band frequency of the readout signal. It could be
                 f(readout) - f(local oscillator) - f(coase NCO frequency) when
                 upper-sideband modu- and demodulations are used.
        Returns:
             success      : b
        """

        def _max_window_length(windows):
            def section_length(tuple_section):
                return tuple_section[1] - tuple_section[0]

            return max([section_length(_w) for _w in windows])

        dev = self.selectedDevice(c)
        freq_in_mhz = bb_frequency["MHz"]  # Base-band frequency before decimation

        if QSConstants.CNL_READ_VAL != dev.device_role:
            raise Exception(QSMessage.ERR_INVALID_DEV.format("readout", dev.device_name))
        elif not dev.static_check_mux_channel_range(muxch):
            raise ValueError(QSMessage.ERR_INVALID_RANG.format("muxch", 0, QSConstants.ACQ_MULP - 1))
        elif -QSConstants.ADCBB_SAMPLE_R / 2.0 >= freq_in_mhz or QSConstants.ADCBB_SAMPLE_R / 2.0 <= freq_in_mhz:
            raise Exception(
                QSMessage.ERR_INVALID_RANG.format(
                    "bb_frequency",
                    -QSConstants.ADCBB_SAMPLE_R / 2.0,
                    QSConstants.ADCBB_SAMPLE_R / 2.0,
                )
            )

        decim_factor = int(QSConstants.ADCBB_SAMPLE_R / QSConstants.ADCDCM_SAMPLE_R + 0.5)
        nsample = _max_window_length(dev.acquisition_window[muxch]) // (decim_factor * QSConstants.ADC_BBSAMP_IVL)
        phase_factor = 2 * np.pi * (freq_in_mhz / QSConstants.ADCDCM_SAMPLE_R) * np.arange(nsample)
        coeffs = np.exp(-1j * phase_factor) * (1 - 1e-3)  # Rectangular window

        return self.acquisition_window_coefficients(c, muxch, coeffs)

    @setting(505, "DEBUG Microwave Switch", output=["b"], returns=["b"])
    def debug_microwave_switch(self, c, output=None):
        """
        Enable and disable a microwave switch at the output.

        Args:
            output : b (bool)
                Outputs signal if output = True. Othewise no output or loopback.
        Returns:
            output : b (bool)
                Current status of the switch.
        """
        dev = self.selectedDevice(c)
        if output is not None:
            yield dev.set_microwave_switch(output)
        else:
            output = yield dev.get_microwave_switch()
        returnValue(output)
