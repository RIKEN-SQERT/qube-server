# Copyright (C) 2022 Yutaka Tabuchi

import os

import labrad
import numpy as np
from labrad import types as T
from labrad import util
from labrad.units import ns, us

# from plotly import graph_objects as go
from .constants import QSConstants

# from manager import QuBE_Manager_Server
from .helper import QuBE_Server_debug_otasuke
from .server import QuBE_Server


############################################################
#
# USAGE and for my debugging
#
#  > import QubeServer
#  > QubeServer.usage()
#
def usage():
    cxn = labrad.connect()
    qs = cxn.qube_server

    devices = ["qube010-readout_cd", "qube010-control_5", "qube011-control_6"]
    # Common settings
    Twaveform = 80 * 0.128  # = 10.24            # micro-seconds
    nsample = int(Twaveform * QSConstants.DACBB_SAMPLE_R + 0.5)
    # points
    freq = -189 / 1.024  # MHz, baseband signal frequency
    qs.daq_timeout(T.Value(20, "s"))
    qs.daq_synchronization_delay(T.Value(0.3, "s"))
    [(qs.select_device(i), qs.shots(1)) for i in devices]
    [(qs.select_device(i), qs.daq_length(Twaveform * us)) for i in devices]
    [(qs.select_device(i), qs.repetition_time(2 * Twaveform * us)) for i in devices]
    data = np.exp(1j * 2 * np.pi * (freq / QSConstants.DACBB_SAMPLE_R) * np.arange(nsample)) * (1 - 1e-3)
    # This set spositive frequency shift
    # of {freq} MHz for upper sideband modu-
    # lation. For control, we use lower-
    # side band modulation and it sets
    # {-freq} MHz baseband frequency

    # for pulse operation, try:
    #   data[0:2560]=0.0+1j*0.0

    qs.select_device(devices[0])  # Readout daq=dac/adc
    """
    Readout setting
      LO         = 8.5 GHz
      NCO        = 1.5 GHz (12000/8 MHz = 15360/10.24 MHz)
      fine NCO0  = 0.0 MHz
      NCO (RX)   = 1.5 GHz
      f(readout) = 8.5 GHz + 1.5 GHz + 0.0 MHz = 10.0 GHz
  """
    dac_chan = 0
    qs.upload_waveform([data], dac_chan)
    qs.upload_parameters([dac_chan])
    qs.frequency_local(T.Value(8500, "MHz"))  # 8.5+1.5=10.2GHz
    qs.frequency_tx_nco(T.Value(1500.0, "MHz"))  # 1.5GHz.
    qs.frequency_rx_nco(T.Value(1500, "MHz"))  # better to be the same as tx_nco
    qs.frequency_tx_fine_nco(dac_chan, T.Value(0, "MHz"))  # better not to use it.
    """
    MUX Window setting
      Enabled channel = 0 & 1
  """
    mux_channels = list()
    mux_chan = 0
    readout_window = [
        (640 * ns, (640 + 1024) * ns),  # two sections of 1 us
        (2224 * ns, (2224 + 1024) * ns),
    ]
    qs.acquisition_window(mux_chan, readout_window)
    qs.debug_auto_acquisition_fir_coefficients(mux_chan, T.Value(freq, "MHz"))
    qs.debug_auto_acquisition_window_coefficients(mux_chan, T.Value(freq, "MHz"))
    qs.acquisition_mode(mux_chan, "2")
    mux_channels.append(mux_chan)

    mux_chan = 1
    dT = 2.048 * us
    readout_window = []
    s = 512 * ns
    readout_window.append((s, s + dT))
    for i in range(3):
        s += dT + 8 * ns
        readout_window.append((s, s + dT))
        if (s + dT)["us"] > 10.24:
            raise Exception(None)
    # for i in range(32):
    #  readout_window.append(( (6*i+1)*dT, (6*i+3)*dT))
    #  readout_window.append(( (6*i+4)*dT, (6*i+6)*dT))
    qs.acquisition_window(mux_chan, readout_window)
    qs.acquisition_mode(mux_chan, "2")
    qs.debug_auto_acquisition_fir_coefficients(mux_chan, T.Value(freq, "MHz"))
    qs.debug_auto_acquisition_window_coefficients(mux_chan, T.Value(freq, "MHz"))
    # mux_channels.append(mux_chan)                            # DEBUG: Intensionally off
    qs.upload_readout_parameters(mux_channels)

    add_control = False
    if add_control:
        for device in devices[1:]:
            qs.select_device(device)  # control settings
            """
        Control frequency setting
          LO  = 11 GHz
          NCO =  3 GHz
          fine NCO0 = -24.9 MHz =-255/10.24 (Lower side-band modulation)
          fine NCO1 =   4.9 MHz =  50/10.24 (Lower side-band modulation)
          fine NCO2 =  14.5 MHz = 150/10.24 (Lower side-band modulation)
          f1 = 11 GHz - 3 GHz -   24.9 MHz = 7975.1 with amp = 0.25
          f2 = 11 GHz - 3 GHz -    4.9 MHz = 7995.1 with amp = 0.12
          f3 = 11 GHz - 3 GHz - (-14.6 MHz)= 8014.6 with amp = 0.10
      """
            qs.upload_parameters([0, 1, 2])
            qs.upload_waveform([0.25 * data, 0.12 * data, 0.10 * data], [0, 1, 2])
            qs.frequency_local(T.Value(11, "GHz"))
            qs.frequency_tx_nco(T.Value(3000, "MHz"))  # 3.0GHz
            dac_chan = 0
            qs.frequency_tx_fine_nco(dac_chan, T.Value(24.90234375, "MHz"))
            dac_chan = 1
            qs.frequency_tx_fine_nco(dac_chan, T.Value(4.8828125, "MHz"))
            dac_chan = 2
            qs.frequency_tx_fine_nco(dac_chan, T.Value(-14.6484375, "MHz"))

    qs.daq_start()  # daq_start
    qs.daq_trigger()  # daq_trigger
    qs.daq_stop()  # daq_stop waits for done

    # qs.debug_awg_reg(0,0x04,0,16)
    # qs.debug_awg_reg(0,0x10,0,16)
    # qs.debug_awg_reg(0,0x14,0,16)
    # qs.debug_awg_reg(0,0x18,0,16)

    qs.select_device(devices[0])
    mux_chan = 0
    dat = qs.download_waveform([mux_chan])
    cxn.disconnect()

    # data_view = False
    # if data_view:
    #     mx, length = dat.shape
    #     tdat = dat[0].reshape((length // 10, 10))
    #     dat = np.sum(tdat, axis=1) / 10.0
    #     e = np.exp(-1j * 2 * np.pi * (3.41796875 / 62.5) * np.arange(length))
    #     # You can apply fft if need
    #     #  dat[0]=np.fft.fft(dat[0])
    #     graph_data = []
    #     graph_data.append(go.Scatter(x=np.arange(length), y=np.real(dat), name="real"))
    #     graph_data.append(go.Scatter(x=np.arange(length), y=np.imag(dat), name="imag"))
    #     # graph_data.append(
    #     #  go.Scatter ( x   = np.arange(length),
    #     #               y   = np.real(dat[1]),
    #     #               name= "real")
    #     # )
    #     # graph_data.append(
    #     #  go.Scatter ( x   = np.arange(length),
    #     #               y   = np.imag(dat[1]),
    #     #               name= "imag")
    #     # )
    #     layout = go.Layout(
    #         title="Spur in RX",
    #         xaxis=dict(title="Frequency (GHz)", dtick=0.05),
    #         yaxis=dict(title="Dataset", dtick=20),
    #     )

    #     fig = go.Figure(graph_data)
    #     fig.write_html("1.html")
    return dat


############################################################
#
# SERVER WORKER
#
# In bash, to start QuBE Server w/o debuggin mode
#   $ QUBE_SERVER = 'QuBE Server' python3 QubeServer.py
#
# To start Qube Manager,
#   $ QUBE_SERVER = 'QuBE Manager' python3 QubeServer.py
#
# Otherwise, QuBE Server starts in debugging mode.
#

__server__ = QuBE_Server()


def main():
    # Import Psyco if available
    #  try:
    #    import psyco
    #    psyco.full()
    #  except ImportError:
    #    pass
    #  print sys.argv
    #  if sys.argv:
    #    del sys.argv[1:]
    print("new qube server start.")
    util.runServer(__server__)


if __name__ == "__main__":
    main()
