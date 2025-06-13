import numpy as np
from labrad import types as T

from .constants import QSConstants


def test_control_ch(device_name):
    # from labrad.units import ns, us
    import labrad

    cxn = labrad.connect()
    qs = cxn.qube_server

    nsample = 4 * 5120
    data = np.exp(1j * 2 * np.pi * (0 / QSConstants.DACBB_SAMPLE_R) * np.arange(nsample)) * (1 - 1e-3)

    qs.select_device(device_name)  # 'qube004-control_6'

    qs.shots(1 * 25 * 1000 * 10)  # 10 seconds
    qs.daq_timeout(T.Value(30, "s"))
    qs.daq_length(T.Value(2 * nsample, "ns"))
    qs.repetition_time(T.Value(4 * 10.24, "us"))

    qs.upload_parameters([0, 1, 2])
    qs.upload_waveform([0.5 * data, 0.3 * data, 0.1 * data], [0, 1, 2])
    qs.frequency_local(T.Value(11, "GHz"))
    qs.frequency_tx_nco(T.Value(3000, "MHz"))  # 3.0GHz ~ 8 GHz
    qs.frequency_tx_fine_nco(0, T.Value(29.296875, "MHz"))
    qs.frequency_tx_fine_nco(1, T.Value(5.37109375, "MHz"))
    qs.frequency_tx_fine_nco(2, T.Value(-14.6484375, "MHz"))

    qs.daq_start()
    qs.daq_trigger()
    qs.daq_stop()


def test_control_ch_bandwidth(device_name):
    # from labrad.units import ns, us
    import labrad

    cxn = labrad.connect()
    qs = cxn.qube_server

    nsample = 4 * 5120
    data = np.exp(1j * 2 * np.pi * (0 / QSConstants.DACBB_SAMPLE_R) * np.arange(nsample)) * (1 - 1e-3)

    qs.select_device(device_name)  # 'qube004-control_6'

    qs.shots(1 * 1000 * 25)  # 1 seconds
    qs.daq_timeout(T.Value(30, "s"))
    qs.daq_length(T.Value(2 * nsample, "ns"))
    qs.repetition_time(T.Value(4 * 10.24, "us"))

    qs.upload_parameters([0, 1, 2])
    qs.upload_waveform([0.0 * data, 1.0 * data, 0.0 * data], [0, 1, 2])
    qs.frequency_local(T.Value(11, "GHz"))
    qs.frequency_tx_nco(T.Value(3000, "MHz"))  # 3.0GHz ~ 8 GHz
    qs.frequency_tx_fine_nco(0, T.Value(29.296875, "MHz"))
    qs.frequency_tx_fine_nco(1, T.Value(5.37109375, "MHz"))
    qs.frequency_tx_fine_nco(2, T.Value(-14.6484375, "MHz"))

    if False:  # IF NCO sweep
        for i in range(256):
            fnco = (i * 8 + 1024) * (12000 / 2**13)
            qs.frequency_tx_nco(T.Value(fnco, "MHz"))
            qs.daq_start()
            qs.daq_trigger()
            qs.daq_stop()
    else:  # BB AWG waveform sweep
        for i in range(256):
            bbfreq = (i * 20 - 2560) / 10.24
            phase_factor = 2 * np.pi * (bbfreq / QSConstants.DACBB_SAMPLE_R)
            data = np.exp(1j * phase_factor * np.arange(nsample)) * (1 - 1e-3)
            qs.upload_parameters([1])
            qs.upload_waveform([data], [1])
            qs.daq_start()
            qs.daq_trigger()
            qs.daq_stop()


def test_readout_ch_bandwidth_and_spurious(device_name):
    # from labrad.units import ns, us
    import pickle
    import time

    import labrad

    def spectrum_analyzer_get():
        import socket

        # import pickle
        import struct

        import numpy as np

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        sock.connect(("localhost", 19001))
        sock.send(b":TRAC:DATA? 1\n")
        rdat = b""
        while True:
            try:
                r = sock.recv(1024)
                rdat = rdat + r
            except Exception:
                break
        sock.close()
        y = np.array(struct.unpack("<501d", rdat[6:]))
        return y

    cxn = labrad.connect()
    qs = cxn.qube_server

    nsample = 4 * 5120  # 4 x 10.24us = 40.96us
    phase_factor = 2 * np.pi * (-189 / 1.024 / QSConstants.DACBB_SAMPLE_R)  # 2pi x normalized frequency
    data = np.exp(1j * phase_factor * np.arange(nsample)) * (1 - 1e-3)

    qs.select_device(device_name)  # 'qube004-control_6'

    qs.shots(6 * 1000 * 25)  # 6 seconds
    qs.daq_timeout(T.Value(30, "s"))
    qs.daq_length(T.Value(2 * nsample, "ns"))
    qs.repetition_time(T.Value(4 * 10.24, "us"))  # identical to the daq_length = CW operation

    qs.upload_parameters([0])
    qs.upload_waveform([data], [0])
    qs.frequency_local(T.Value(8500, "MHz"))
    qs.frequency_tx_nco(T.Value(1599.609375, "MHz"))  # 1.5GHz
    qs.frequency_tx_fine_nco(0, T.Value(0, "MHz"))

    def experiment_nco_sweep(vault, fnco, file_idx):
        qs.frequency_tx_nco(T.Value(fnco, "MHz"))
        qs.daq_start()
        qs.daq_trigger()
        time.sleep(3.5)
        dat = spectrum_analyzer_get()
        print(file_idx, fnco)
        vault.append(dat)
        with open("data{0:03d}.pkl".format(file_idx), "wb") as f:
            pickle.dump(np.array(vault), f)
        qs.daq_stop()

    if False:
        vault = []
        for i in range(512):
            fnco = (i * 2 + 512) * (12000 / 2**13)
            experiment_nco_sweep(vault, fnco, 0)

    elif False:
        for freq_lo, j in zip(range(9500, 8000, -100), range(15)):
            qs.frequency_local(T.Value(freq_lo, "MHz"))
            vault = []
            for i in range(256):
                fnco = (i * 4 + 512) * (12000 / 2**13)
                experiment_nco_sweep(vault, fnco, j)

    elif False:
        for i in range(256):
            bbfreq = (i * 20 - 2560) / 10.24
            phase_factor = 2 * np.pi * (bbfreq / QSConstants.DACBB_SAMPLE_R)
            data = np.exp(1j * phase_factor * np.arange(nsample)) * (1 - 1e-3)
            qs.upload_waveform([data], [0])
            qs.upload_parameters([0])
            qs.daq_start()
            qs.daq_trigger()
            qs.daq_stop()
    else:
        qs.daq_start()
        qs.daq_trigger()
        time.sleep(3.5)
        dat = spectrum_analyzer_get()
        with open("data.pkl", "wb") as f:
            pickle.dump(np.array(dat), f)
        qs.daq_stop()


def test_timing_calib(cxn, device_name):
    class TCConstants:
        QUBE_SRV_TAG = "qube_server"
        CALIB_SHOT = 1
        CALIB_TOUT = T.Value(30, "s")
        CALIB_BURST = T.Value(10.24 - 0.128, "us")
        CALIB_REPT = T.Value(10.24, "us")
        CALIB_FREQ = T.Value(10.0, "GHz")
        CALIB_FCNCO = T.Value(1.5, "GHz")
        CALIB_FFNCO = T.Value(0, "GHz")

    class TCMessages:
        INVALID_DEVICE = "Invalid device pair: {}."

    class TimingSyncCalibrator:
        def __init__(self, cxn, device_name):
            if isinstance(device_name, tuple):
                resp = self.check_chassis_name(device_name)
            if resp:
                self._device_pair = device_name
                self._cxn = cxn
                self._ql = cxn[TCConstants.QUBE_SRV_TAG]
                self.__initialized = True
            else:
                print(TCMessages.INVALID_DEVICE.format(device_name))

        def check_chassis_name(self, device_name):
            chassisA, chassisB = tuple([_device.split("-")[0] for _device in device_name])
            if chassisA == chassisB:
                return False
            return True

        def set_basic_config(self):
            for _device in self._device_pair:
                self._ql.shots(TCConstants.CALIB_SHOT)
                self._ql.daq_timeout(TCConstants.CALIB_TOUT)
                self._ql.daq_length(TCConstants.CALIB_BURST)
                self._ql.repetition_time(TCConstants.CALIB_REPT)

                self._ql.frequency_local(TCConstants.CALIB_FREQ - TCConstants.CALIB_FCNCO - TCConstants.CALIB_FFNCO)
                self._ql.frequency_tx_nco(TCConstants.FCNCO)
                self._ql.frequency_tx_fine_nco(0, TCConstants.FFNCO)

    TimingSyncCalibrator(cxn, device_name)
