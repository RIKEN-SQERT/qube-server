from __future__ import annotations

import concurrent.futures
import json
import sys
from typing import Any, Final, Optional, cast

import numpy as np
from labrad import types as T
from labrad.devices import DeviceLockedError, DeviceServer, DeviceWrapper
from labrad.errors import DeviceNotSelectedError
from labrad.server import setting
from labrad.units import Value
from quel_ic_config import (
    AbstractStartAwgunitsTask,
    BoxStartCapunitsByTriggerTask,
    Quel1Box,
    Quel1BoxType,
)
from twisted.internet import defer, threads
from twisted.internet.defer import inlineCallbacks, returnValue
from typing_extensions import TypeAlias

from qube_server.utils import is_reachable

from .box_connection import BoxConnection, locked_boxes
from .constants import QSConstants, QSMessage
from .devices import (
    DeviceConnectionInfo,
    DeviceConnectionInfoArgs,
    DeviceType,
    QuBE_ControlPort,
    QuBE_DeviceBase,
    QuBE_PumpPort,
    QuBE_ReadoutPort,
    RfSwitchState,
    create_device_connection_infos_from_box_connection,
)
from .model import PossibleLinks, Skews

BOXLOCK_TIMEOUT_DURATION: Final[float] = 10  # sec

CapTaskType: TypeAlias = BoxStartCapunitsByTriggerTask
AwgTaskType: TypeAlias = AbstractStartAwgunitsTask


############################################################
#
# QUBE SERVER
#
class QuBE_Server(DeviceServer):
    name = QSConstants.SRVNAME
    deviceWrapper = None

    def __init__(
        self,
        possible_links_json_filepath: Optional[str] = None,
        chassis_skew_json_filepath: Optional[str] = None,
    ):
        super().__init__()
        self._name_to_box_conn: dict[str, BoxConnection] = {}
        self._possible_links_json_filepath = possible_links_json_filepath
        self._chassis_skew_json_filepath = chassis_skew_json_filepath

    @inlineCallbacks
    def _get_possible_links(self):
        cxn: Any = self.client
        reg = cxn[QSConstants.REGSRV]
        try:
            yield reg.cd(QSConstants.REGDIR)
            config = yield reg.get(QSConstants.REGLNK)
            possibleLinks = PossibleLinks.model_validate_json(config)
            returnValue(possibleLinks)
        except Exception as e:
            print(sys._getframe().f_code.co_name, e)

    @inlineCallbacks
    def _get_skew_config(self):
        cxn: Any = self.client
        reg = cxn[QSConstants.REGSRV]
        chassisSkew: dict[str, int] = {}
        try:
            yield reg.cd(QSConstants.REGDIR)
            skew = yield reg.get(QSConstants.REGSKEW)
            chassisSkew = json.loads(skew)
        except Exception as e:
            print(sys._getframe().f_code.co_name, e)
        returnValue(chassisSkew)

    @inlineCallbacks
    def initServer(self):
        if self._possible_links_json_filepath is None:
            self.possibleLinks: PossibleLinks = yield self._get_possible_links()
        else:
            with open(self._possible_links_json_filepath) as f:
                self.possibleLinks = PossibleLinks.model_validate_json(f.read())

        if self._chassis_skew_json_filepath is None:
            self.chassisSkew: Skews = yield self._get_skew_config()
        else:
            with open(self._chassis_skew_json_filepath) as f:
                self.chassisSkew = Skews.model_validate_json(f.read())

        yield DeviceServer.initServer(self)

        try:
            max_workers = QSConstants.THREAD_MAX_WORKERS
            self._thread_pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=max_workers
            )  # for a threaded operation
        except Exception as e:
            print(sys._getframe().f_code.co_name, e)

    def initContext(self, c):
        DeviceServer.initContext(self, c)
        c[QSConstants.DAC_CNXT_TAG] = dict()
        c[QSConstants.ACQ_CNXT_TAG] = dict()
        c[QSConstants.AWG_TASK_TAG] = dict()
        c[QSConstants.CAP_TASK_TAG] = dict()
        c[QSConstants.DAQ_TOUT_TAG] = QSConstants.DAQ_INITTOUT
        c[QSConstants.DAQ_SDLY_TAG] = QSConstants.DAQ_INITSDLY

    def chooseDeviceWrapper(self, *args, **kw) -> type[QuBE_DeviceBase]:
        _, *args = args  # the first item is 'name'
        args = DeviceConnectionInfoArgs(*args)
        if args.device_type is DeviceType.ctrl:
            return QuBE_ControlPort
        elif args.device_type is DeviceType.readout:
            return QuBE_ReadoutPort
        elif args.device_type is DeviceType.pump:
            return QuBE_PumpPort
        else:
            raise ValueError(f"Unknown device type: {type}")

    def findDevices(self) -> list[DeviceConnectionInfo]:
        dev_conn_infos: list[DeviceConnectionInfo] = []

        box_name_to_timecounter_offset = {
            b.box_name: b.offset for b in self.chassisSkew.boxes
        }

        for box_link in self.possibleLinks.boxes:
            if box_link.name not in self._name_to_box_conn:
                print(QSMessage.CHECKING_QUBEUNIT.format(box_link.name))
                if not is_reachable(box_link.ipaddr_wss):
                    print(
                        sys._getframe().f_code.co_name,
                        RuntimeError(QSMessage.ERR_HOST_NOTFOUND.format(box_link.name)),
                    )

                print(QSMessage.CNCTABLE_QUBEUNIT.format(box_link.name))

                def _box_factory():
                    box = Quel1Box.create(
                        ipaddr_wss=box_link.ipaddr_wss,
                        boxtype=Quel1BoxType.fromstr(box_link.boxtype),
                    )
                    return box

                box_conn = BoxConnection(
                    box_name=box_link.name,
                    box_factory=_box_factory,
                    timecounter_offset=box_name_to_timecounter_offset[box_link.name],
                )
                self._name_to_box_conn[box_link.name] = box_conn

            box_conn = self._name_to_box_conn[box_link.name]
            dev_conn_infos.extend(
                create_device_connection_infos_from_box_connection(box_conn)
            )
        return dev_conn_infos

    @setting(10, "Reload Skew", returns=["b"])
    def reload_config_skew(self, c):
        raise NotImplementedError("Unused API")
        """
        Reload skew adjustment time difference among chassis from the registry.

        Returns:
            success : True if successfuly obtained skew value from the registry
        """
        try:
            self.chassisSkew = yield self._get_skew_config()
        except Exception as e:
            print(sys._getframe().f_code.co_name, e)
            return False
        return True

    @setting(20, "Device Type", returns=["s"])
    def device_type(self, c):
        dev = self.selectedDevice(c)
        return dev.device_type.value

    @setting(100, "Shots", num_shots=["w"], returns=["w"])
    def number_of_shots(self, c, num_shots=None):
        """
        Read and write the number of repeated experiments.

        The number of <shots> of an experiment with fixed waveform.

        Args:
            num_shots: w
                The number of repeat in an extire experiments. Used to say "shots"
        Returns:
            num_shots: w
        """
        dev = self.selectedDevice(c)
        if num_shots is not None:
            dev.number_of_shots = num_shots
            return num_shots
        else:
            return dev.number_of_shots

    @setting(101, "Repeat Count", repeat=["w"], returns=["w"])
    def repeat_count(self, c, repeat=None):
        """
        OBSOLETED. Use repetition time instead.

        This is no longer used.

        Args:
            repeat: w
                The number of repeat in an extire experiments. Used to say "shots"
        Returns:
            repeat: w
        """
        raise Exception('obsoleted. use "shots" instead')
        return self.number_of_shots(c, repeat)

    @setting(102, "Repetition Time", reptime=["v[s]"], returns=["v[s]"])
    def repetition_time(self, c, reptime=None):
        """
        Read and write reperition time.

        The repetition time of a single experiments include control/readout waveform
        plus wait (blank, not output) duration.

        Args:
            reptime: v[s]
                10.24us - 1s can be set. The duration must be a multiple of 10.24 us
                to satisty phase coherence.
        Returns:
            reptime: v[s]
        """
        dev = self.selectedDevice(c)
        if reptime is None:
            return T.Value(dev.repetition_time, "ns")
        elif dev.static_check_repetition_time(reptime["ns"]):
            dev.repetition_time = int(round(reptime["ns"]))
            return reptime
        else:
            raise ValueError(
                QSMessage.ERR_REP_SETTING.format(
                    "Sequencer", QSConstants.DAQ_REPT_RESOL
                )
            )

    @setting(103, "DAQ Length", length=["v[s]"], returns=["v[s]"])
    def sequence_length(self, c, length=None):
        """
        Read and write waveform length.

        The waveform length supposed to be identical among all channels. It can be
        different, but we have not done yet.

        Args:
            length: v[s]
                The length of sequence waveforms. The length must be a
                multiple of 128 ns. 0.128ns - 200us can be set.
        Returns:
            length: v[s]
        """
        dev = self.selectedDevice(c)
        if length is None:
            return Value(dev.sequence_length, "ns")
        elif dev.static_check_sequence_length(length["ns"]):
            dev.sequence_length = int(length["ns"] + 0.5)
            return length
        else:
            raise ValueError(
                QSMessage.ERR_REP_SETTING.format(
                    "Sequencer", QSConstants.DAQ_SEQL_RESOL
                )
                + QSMessage.ERR_INVALID_RANG.format(
                    "daq_length", "128 ns", "{} ns".format(QSConstants.DAQ_MAXLEN)
                )
            )

    @setting(105, "DAQ Start", returns=["b"])
    def daq_start(self, c):
        """
        Start data acquisition

        The method name [daq_start()] is for backward compatibility with a former version of quantum logic analyzer, and I like it.
        """
        return True

    @setting(106, "DAQ Trigger", returns=["b"])
    def daq_trigger(self, c):
        """
        Start synchronous measurement.

        Read the clock value from the master FPGA board and set a planned timing
        to the QuBE units. Measurement is to start at the given timing.

        """
        if 1 > len(c[QSConstants.DAC_CNXT_TAG].keys()):
            return False  # Nothing to start.
        delay = int(c[QSConstants.DAQ_SDLY_TAG] * QSConstants.SYNC_CLOCK + 0.5)

        chassis_list = c[QSConstants.DAC_CNXT_TAG].keys()
        box_conns = [self._name_to_box_conn[box_name] for box_name in chassis_list]

        with locked_boxes(box_conns, c.ID, timeout_duration=BOXLOCK_TIMEOUT_DURATION):
            # the first box is used as a tentative master
            cur_timecounter = int(box_conns[0].get_current_timecounter())
            latest_trigger_timecounter = max(
                bc.last_trigger_timecounter for bc in box_conns
            )
            timecounter = (
                max(latest_trigger_timecounter, cur_timecounter) + delay
            ) & 0xFFFFFFFFFFFFFFF0

            for bc in box_conns:
                if bc.box_name in c[QSConstants.ACQ_CNXT_TAG]:
                    cap_task, awg_task = bc.start_capture_by_awg_trigger(
                        c.ID,
                        runits=c[QSConstants.ACQ_CNXT_TAG][bc.box_name],
                        channels=c[QSConstants.DAC_CNXT_TAG][bc.box_name],
                        timecounter=timecounter,
                    )
                    c[QSConstants.CAP_TASK_TAG][bc.box_name] = cap_task
                else:
                    awg_task = bc.start_wavegen(
                        c.ID,
                        channels=c[QSConstants.DAC_CNXT_TAG][bc.box_name],
                        timecounter=timecounter,
                    )
                c[QSConstants.AWG_TASK_TAG][bc.box_name] = awg_task
                print(bc.box_name, "kick at ", timecounter)
        return True

    @setting(107, "DAQ Stop", returns=["b"])
    def daq_stop(self, c):
        deferreds = []

        for task_obj in c[QSConstants.AWG_TASK_TAG].values():
            task_obj = cast(AwgTaskType, task_obj)
            deferreds.append(threads.deferToThread(task_obj.result))

        for task_obj in c[QSConstants.CAP_TASK_TAG].values():
            task_obj = cast(CapTaskType, task_obj)
            deferreds.append(threads.deferToThread(task_obj.result))

        dlist = defer.DeferredList(deferreds, fireOnOneErrback=True, consumeErrors=True)

        dlist.addCallback(lambda _: True)
        dlist.addErrback(lambda _: False)

        return dlist

    @setting(112, "DAQ Clear", returns=["b"])
    def daq_clear(self, c):
        """
        Clear registed control and readout channels from the device context.

        """
        c[QSConstants.DAC_CNXT_TAG] = dict()
        c[QSConstants.ACQ_CNXT_TAG] = dict()
        c[QSConstants.AWG_TASK_TAG] = dict()
        c[QSConstants.CAP_TASK_TAG] = dict()

        return True  # Nothing to stop

    @setting(113, "DAQ Terminate", returns=["b"])
    def daq_terminate(self, c):
        raise NotImplementedError()
        """
        Force terminate a current running measurement

        """
        if 1 > len(c[QSConstants.DAC_CNXT_TAG].keys()):
            return False

        for chassis_name in c[QSConstants.DAC_CNXT_TAG].keys():
            dev, enabled_awgs = c[QSConstants.DAC_CNXT_TAG][chassis_name]
            dev.terminate_daq(list(enabled_awgs))

        for chassis_name in c[QSConstants.ACQ_CNXT_TAG].keys():
            for dev, module, units in c[QSConstants.ACQ_CNXT_TAG][chassis_name]:
                dev.terminate_acquisition(units)

        return True

    @setting(108, "DAQ Timeout", t=["v[s]"], returns=["v[s]"])
    def daq_timeout(self, c, t=None):
        if t is None:
            val = c[QSConstants.DAQ_TOUT_TAG]
            return T.Value(val, "s")
        else:
            c[QSConstants.DAQ_TOUT_TAG] = t["s"]
            return t

    @setting(111, "DAQ Synchronization Delay", t=["v[s]"], returns=["v[s]"])
    def daq_sync_delay(self, c, t=None):
        if t is None:
            val = c[QSConstants.DAQ_SDLY_TAG]
            return T.Value(val, "s")
        else:
            c[QSConstants.DAQ_SDLY_TAG] = t["s"]
            return t

    @setting(110, "DAC Channels", returns=["w"])
    def daq_channels(self, c):
        """Retrieveout the number of available AWG channels. The number of available AWG c
        hannels is configured through adi_api_mod/v1.0.6/src/helloworld.c and the
        lane information is stored in the registry /Servers/QuBE/possible_links.

        Returns:
            channels : w
                The number of available AWG channels.
        """
        dev = self.selectedDevice(c)
        return len(dev.channels_of_port)

    @setting(200, "Upload Parameters", channels=["w", "*w"], returns=["b"])
    def upload_parameters(self, c, channels):
        """
        Upload channel parameters.

        Sequence setting.

        Args:
            channels : w, *w
                waveform channel   0 to 2 [The number of waveform channels - 1]
        Returns:
            success  : b
                True if successful.
        """
        dev = self.selectedDevice(c)
        channels = _convert_to_builtin_type(channels, ensure_list=True)
        channels_of_port = dev.channels_of_port
        if any(chan not in channels_of_port for chan in channels):
            raise ValueError(
                QSMessage.ERR_INVALID_ITEM.format("channel", channels_of_port)
            )
        return self._register_awg_channels(c, dev, channels)

    def _register_awg_channels(self, c, dev, channels):
        """
        Register selected DAC AWG channels

        The method [_register_awg_channels()] register the enabled AWG IDs to the device context.
        This information is used in daq_start() and daq_trigger()

        Data structure:
          chassis_name00: set{(5, 1), ...}, # Quel1PortType=0, channel=1
          chassis_name01: set{(3, 0), ...}
        """
        chassis_name = dev.chassis_name

        if chassis_name not in c[QSConstants.DAC_CNXT_TAG]:
            c[QSConstants.DAC_CNXT_TAG][chassis_name] = set()

        for chan in channels:
            c[QSConstants.DAC_CNXT_TAG][chassis_name].add((dev.port_out, chan))
        return True

    @setting(201, "Upload Readout Parameters", muxchs=["*w", "w"], returns=["b"])
    def upload_readout_parameters(self, c, muxchs):
        """
        Upload readout demodulator parameters.

        It sends the necessary parameters for readout operation.

        Args:
            muxchs: w, *w
                multiplex channel   0 to 3 [QSConstants.ACQ_MULP-1]
        """
        dev = self.selectedDevice(c)
        if dev.device_type is not DeviceType.readout:
            raise Exception(QSMessage.ERR_INVALID_DEV.format("readout", dev.name))

        muxchs = _convert_to_builtin_type(muxchs, ensure_list=True)
        runits_of_port = dev.runits_of_port
        if any(chan not in runits_of_port for chan in muxchs):
            raise ValueError(QSMessage.ERR_INVALID_ITEM.format("muxch", runits_of_port))
        for muxch in muxchs:
            if dev.upload_readout_parameters(muxch):
                self._register_mux_channels(c, dev, muxch)
            else:
                return False
        return True

    def _register_mux_channels(self, c, dev, selected_mux_channel):
        """
        Register selected readout channel

        The method [_register_mux_channel()] register the selected capture module
        IDs and the selected capture units to the device context. This information
        is used in daq_start() and daq_trigger().

        """
        chassis_name = dev.chassis_name

        if chassis_name not in c[QSConstants.ACQ_CNXT_TAG]:
            c[QSConstants.ACQ_CNXT_TAG][chassis_name] = set()

        c[QSConstants.ACQ_CNXT_TAG][chassis_name].add(
            (dev.port_in, selected_mux_channel)
        )
        return True

    @setting(
        202,
        "Upload Waveform",
        wavedata=["*2c", "*c"],
        channels=["*w", "w"],
        returns=["b"],
    )
    def upload_waveform(self, c, wavedata, channels):
        """
        Upload waveform to FPGAs.

        Transfer 500MSa/s complex waveforms to the QuBE FPGAs.

        Args:
            wavedata : *2c,*c
                Complex waveform data with a sampling interval of 2 ns [QSConstants.
                DAC_WVSAMP_IVL]. When more than two channels, speficy the waveform
                data using list, i.e.  [data0,data1,...], or tuple (data0,data1,...)

            channels: *w, w
                List of the channels, e.g., [0,1] for the case where the number of
                rows of wavedata is more than 1. You can simply give the channel
                number to set a single-channel waveform.
        """
        dev = self.selectedDevice(c)
        channels = _convert_to_builtin_type(channels, ensure_list=True)
        waveforms = np.atleast_2d(wavedata).astype(np.complex64)

        channels_of_port = dev.channels_of_port
        if any(chan not in channels_of_port for chan in channels):
            raise ValueError(
                QSMessage.ERR_INVALID_ITEM.format("channel", channels_of_port)
            )

        resp, number_of_chans, data_length = dev.check_waveform(waveforms, channels)
        if not resp:
            raise ValueError(QSMessage.ERR_INVALID_WAVD.format(number_of_chans))

        for waveform, channel in zip(waveforms, channels):
            dev.upload_waveform(waveform, channel)
        return True

    @setting(203, "Download Waveform", muxchs=["*w", "w"], returns=["*c", "*2c"])
    def download_waveform(self, c, muxchs):
        """
        Download acquired waveforms (or processed data points).

        Transfer waveforms or datapoints from Alevo FPGA to a host computer.

        Args:
            muxchs  : *w, w

        Returns:
            data    : *2c,*c
        """

        dev = self.selectedDevice(c)
        if dev.device_type is not DeviceType.readout:
            raise Exception(QSMessage.ERR_INVALID_DEV.format("readout", dev.name))

        for awg_task in c[QSConstants.AWG_TASK_TAG].values():
            awg_task = cast(AwgTaskType, awg_task)
            awg_task.result()

        for cap_task in c[QSConstants.CAP_TASK_TAG].values():
            cap_task = cast(CapTaskType, cap_task)
            cap_task.result()

        waveforms = []
        muxchs = _convert_to_builtin_type(muxchs, ensure_list=True)
        for mux in muxchs:
            if cap_task := c[QSConstants.CAP_TASK_TAG].get(dev.chassis_name, None):
                cap_task = cast(BoxStartCapunitsByTriggerTask, cap_task)
                reader = cap_task.result()
                wavelist = reader[dev.port_in, mux].as_wave_list()
                flatten_wavelist = []
                for section in wavelist:
                    for rep in section:
                        flatten_wavelist.append(rep)
                waveforms.append(np.hstack(flatten_wavelist))
            else:
                raise ValueError("Acquired waveforms not found.")
        return np.vstack(waveforms).astype(complex)

    @setting(300, "Acquisition Count", acqcount=["w"], returns=["w"])
    def acquisition_count(self, c, acqcount=None):
        """
        Read and write acquisition count.

        OBSOLETED

        Args:
           acqcount : w
                The number of acquisition in a single experiment. 1 to 8 can be set.
        """
        raise Exception('obsoleted. use "acquisition_number" instead')

    @setting(301, "Acquisition Number", muxch=["w"], acqnumb=["w"], returns=["w"])
    def acquisition_number(self, c, muxch, acqnumb=None):
        """
        Read and write the number of acquisition windows

        Setting for acquistion windows. You can have several accquisition windows in
        a single experiments.

        Args:
           muxch   : w
                Multiplex channel id. 0 to 3 [QSConstants.ACQ_MULP-1] can be set
           acqnumb : w
                The number of acquisition in a single experiment. 1 to 8 can be set.
        """
        dev = self.selectedDevice(c)
        if dev.device_type is not DeviceType.readout:
            raise Exception(QSMessage.ERR_INVALID_DEV.format("readout", dev.name))
        if muxch not in (runits_of_port := dev.runits_of_port):
            raise ValueError(QSMessage.ERR_INVALID_ITEM.format("muxch", runits_of_port))
        elif acqnumb is None:
            return dev.acquisition_number_of_windows[muxch]
        elif 0 < acqnumb and acqnumb <= QSConstants.ACQ_MAXNUMCAPT:
            dev.acquisition_number_of_windows[muxch] = acqnumb
            return acqnumb
        else:
            raise ValueError(
                QSMessage.ERR_INVALID_RANG.format(
                    "Acquisition number of windows", 1, QSConstants.ACQ_MAXNUMCAPT
                )
            )

    @setting(
        302,
        "Acquisition Window",
        muxch=["w"],
        window=["*(v[s]v[s])"],
        returns=["*(v[s]v[s])"],
    )
    def acquisition_window(self, c, muxch, window=None):
        """
        Read and write acquisition windows.

        Setting for acquistion windows. You can have several accquisition windows
        in a single experiments. A windows is defined as a tuple of two timestamps
        e.g., (start, end). Multiples windows can be set like [(start1, end1),
        (start2, end2), ... ]

        Args:
            muxch: w
                multiplex channel   0 to 3 [QSConstants.ACQ_MULP-1]

            window: *(v[s]v[s])
                List of windows. The windows are given by tuples of (window start,
                window end).
        Returns:
            window: *(v[s]v[s])
                Current window setting
        """
        dev = self.selectedDevice(c)
        if dev.device_type is not DeviceType.readout:
            raise Exception(QSMessage.ERR_INVALID_DEV.format("readout", dev.name))
        if muxch not in (runits_of_port := dev.runits_of_port):
            raise ValueError(QSMessage.ERR_INVALID_ITEM.format("muxch", runits_of_port))
        elif window is None:
            return [
                (T.Value(_s, "ns"), T.Value(_e, "ns"))
                for _s, _e in dev.acquisition_window[muxch]
            ]

        wl = [(int(_w[0]["ns"] + 0.5), int(_w[1]["ns"] + 0.5)) for _w in window]
        if dev.static_check_acquisition_windows(wl):
            dev.set_acquisition_window(muxch, wl)
            return window
        else:
            raise ValueError(QSMessage.ERR_INVALID_WIND)

    @setting(303, "Acquisition Mode", muxch=["w"], mode=["s"], returns=["s"])
    def acquisition_mode(self, c, muxch, mode=None):
        """
        Read and write acquisition mode

        Five (or six) acquisition modes are defined, i.e., 1, 2, 3, A, B, (C) for
        predefined experiments.

        SIGNAL PROCESSING MAP <MODE NUMBER IN THE FOLLOWING TABLES>

          DECIMATION = NO
                              |       Averaging       |
                              |    NO     |   YES     |
                ------+-------+-----------+-----------+--
                 SUM |   NO   |           |     1     |
                 MAT +--------+-----------|-----------+--
                 ION |  YES   |           |           |

          DECIMATION = YES
                              |       Averaging       |
                              |    NO     |   YES     |
                ------+-------+-----------+-----------+--
                 SUM |   NO   |     2     |     3     |
                 MAT +--------+-----------|-----------+--
                 ION |  YES   |     A     |     B     |

          DECIMATION = YES / BINARIZE = YES
                              |       Averaging       |
                              |    NO     |   YES     |
                ------+-------+-----------+-----------+--
                 SUM |   NO   |           |           |
                 MAT +--------+-----------|-----------+--
                 ION |  YES   |     C     |           |

        DEBUG, The mode "C" has not been implemented yet.

        Args:
            muxch    : w
                multiplex channel   0 to 3 [QSConstants.ACQ_MULP-1]

            mode     : s
                Acquisition mode. one of '1', '2', '3', 'A', 'B' can be set.

        Returns:
            mode     : s
        """
        dev = self.selectedDevice(c)
        if dev.device_type is not DeviceType.readout:
            raise Exception(QSMessage.ERR_INVALID_DEV.format("readout", dev.name))
        if muxch not in (runits_of_port := dev.runits_of_port):
            raise ValueError(QSMessage.ERR_INVALID_ITEM.format("muxch", runits_of_port))
        elif mode is None:
            return dev.acquisition_mode[muxch]
        elif mode in QSConstants.ACQ_MODENUMBER:
            dev.set_acquisition_mode(muxch, mode)
            return mode
        else:
            raise ValueError(
                QSMessage.ERR_INVALID_ITEM.format(
                    "Acquisition mode", ",".join(QSConstants.ACQ_MODENUMBER)
                )
            )

    @setting(304, "Acquisition Mux Enable", muxch=["w"], returns=["b", "*b"])
    def acquisition_mux_enable(self, c, muxch=None):
        """
        Obtain enabled demodulation mux channels

        Mux demodulation channels are enabled in upload_readout_parameters().

        Args:
            muxch : w
                multiplex channel   0 to 3 [QSConstants.ACQ_MULP-1].
                Read all channel if None.
        Returns:
            Enabled(True)/Disabled(False) status of the channel.
        """
        raise NotImplementedError("Unused API")
        dev = self.selectedDevice(c)
        if QSConstants.CNL_READ_VAL != dev.device_role:
            raise Exception(
                QSMessage.ERR_INVALID_DEV.format("readout", dev.device_name)
            )
        elif muxch is not None and not dev.static_check_mux_channel_range(muxch):
            raise ValueError(
                QSMessage.ERR_INVALID_RANG.format("muxch", 0, QSConstants.ACQ_MULP - 1)
            )
        else:
            chassis_name = dev.chassis_name
            resp = chassis_name in c[QSConstants.ACQ_CNXT_TAG].keys()
            idx = None
            module_enabled = None
            if resp:
                module_enabled = c[QSConstants.ACQ_CNXT_TAG][chassis_name]
                resp = True
                try:
                    idx = [_m for _d, _m, _u in module_enabled].index(
                        dev.get_capture_module_id()
                    )
                except ValueError:
                    resp = False
            if resp and resp is not None and module_enabled is not None:
                _d, _m, unit_enabled = module_enabled[idx]
                if muxch is not None:
                    resp = dev.get_capture_unit_id(muxch) in unit_enabled
                    result = True if resp else False
                else:
                    result = [
                        (dev.get_capture_unit_id(i) in unit_enabled)
                        for i in range(QSConstants.ACQ_MULP)
                    ]
            else:
                result = (
                    [False for _i in range(QSConstants.ACQ_MULP)]
                    if muxch is None
                    else False
                )
            return result

    @setting(305, "Filter Pre Coefficients", muxch=["w"], coeffs=["*c"], returns=["b"])
    def filter_pre_coefficients(self, c, muxch, coeffs):
        """
        Set complex FIR coefficients to a mux channel. (getting obsoleted)
        """
        raise NotImplementedError("Unused API")
        self.acquisition_fir_coefficients(c, muxch, coeffs)
        raise Exception(
            "Tabuchi wants to rename the API to acquisition_fir_coefficients"
        )

    @setting(
        306, "Average Window Coefficients", muxch=["w"], coeffs=["*c"], returns=["b"]
    )
    def set_window_coefficients(self, c, muxch, coeffs):
        """
        Set complex window coefficients to a mux channel. (getting obsoleted)
        """
        self.acquisition_window_coefficients(c, muxch, coeffs)
        raise Exception(
            "Tabuchi wants to rename the API to acquisition_window_coefficients"
        )

    @setting(
        307, "Acquisition FIR Coefficients", muxch=["w"], coeffs=["*c"], returns=["b"]
    )
    def acquisition_fir_coefficients(self, c, muxch, coeffs):
        """
        Set complex FIR (finite impulse response) filter coefficients to a mux channel.

        In the decimation DSP logic, a 8-tap FIR filter is applied before decimation.

        Args:
            muxch : w
                Multiplex readout mux channel. 0-3 can be set

            coeffs : *c
                Complex window coefficients. The absolute values of the coeffs has
                to be less than 1.

        Returns:
            success: b
        """
        dev = self.selectedDevice(c)
        if dev.device_type is not DeviceType.readout:
            raise Exception(QSMessage.ERR_INVALID_DEV.format("readout", dev.name))
        if muxch not in (runits_of_port := dev.runits_of_port):
            raise ValueError(QSMessage.ERR_INVALID_ITEM.format("muxch", runits_of_port))
        elif not dev.static_check_acquisition_fir_coefs(coeffs):
            raise ValueError(
                QSMessage.ERR_INVALID_RANG.format("abs(coeffs)", 0, 1)
                + QSMessage.ERR_INVALID_RANG.format(
                    "len(coeffs)", 1, QSConstants.ACQ_MAX_FCOEF
                )
            )
        else:
            dev.set_acquisition_fir_coefficient(muxch, coeffs)
        return True

    @setting(
        308,
        "Acquisition Window Coefficients",
        muxch=["w"],
        coeffs=["*c"],
        returns=["b"],
    )
    def acquisition_window_coefficients(self, c, muxch, coeffs):
        """
        Set complex window coefficients to a mux channel.

        In the summation DSP logic, a readout signal is multipled by the window
        coefficients before sum operatation for weighted demodulation.

        Args:
            muxch  : w
                Multiplex readout mux channel. 0-3 can be set

            coeffs : *c
                Complex window coefficients. The absolute values of the coeffs has
                to be less than 1.

        Returns:
            success: b
        """
        dev = self.selectedDevice(c)
        if dev.device_type is not DeviceType.readout:
            raise Exception(QSMessage.ERR_INVALID_DEV.format("readout", dev.name))
        if muxch not in (runits_of_port := dev.runits_of_port):
            raise ValueError(QSMessage.ERR_INVALID_ITEM.format("muxch", runits_of_port))
        elif not dev.static_check_acquisition_window_coefs(coeffs):
            raise ValueError(
                QSMessage.ERR_INVALID_RANG.format("abs(coeffs)", 0, 1)
                + QSMessage.ERR_INVALID_RANG.format(
                    "len(coeffs)", 1, QSConstants.ACQ_MAX_WCOEF
                )
            )
        else:
            dev.set_acquisition_window_coefficient(muxch, coeffs)
        return True

    @setting(400, "Frequency Local", frequency=["v[Hz]"], returns=["v[Hz]"])
    def frequency_local(self, c, frequency=None):
        """
        Read and write frequency setting from/to local oscillators.

        The waveform singnals from D/A converters is upconverted using local osci-
        llators (LMX2594).

        Args:
            frequency: v[Hz]
                The mininum frequency resolution of oscillators are 100 MHz [QSCons
                tants.DAC_LO_RESOL].

        Returns:
            frequency: v[Hz]

        """
        dev = self.selectedDevice(c)
        if frequency is None:
            resp = dev.get_lo_frequency()
            frequency = T.Value(resp, "Hz")
        elif dev.static_check_lo_frequency(frequency["Hz"]):
            dev.set_lo_frequency(frequency["Hz"])
        else:
            raise ValueError(
                QSMessage.ERR_FREQ_SETTING.format("LO", QSConstants.DAQ_LO_RESOL)
            )
        return frequency

    @setting(401, "Frequency TX NCO", frequency=["v[Hz]"], returns=["v[Hz]"])
    def frequency_tx_nco(self, c, frequency=None):
        """
        Read and write frequency setting from/to coarse NCOs.

        A D/A converter have multiple waveform channels. The channels have a common
        coarse NCO for upconversion. The center center frequency can be tuned with
        the coarse NCO from -6 GHz to 6 GHz.

        Args:
            frequency: v[Hz]
                The minimum resolution of NCO frequencies is 1.46484375 MHz [QSConst
                ants.DAC_CNCO_RESOL].

        Returns:
            frequency: v[Hz]

        """
        # TODO: static_check_dac_coarse_frequency
        # dev = self.selectedDevice(c)
        # if frequency is None:
        #     resp = dev.get_dac_coarse_frequency()
        #     frequency = T.Value(resp, "MHz")
        # elif dev.static_check_dac_coarse_frequency(frequency["MHz"]):
        #     dev.set_dac_coarse_frequency(frequency["MHz"])
        # else:
        #     raise ValueError(
        #         QSMessage.ERR_FREQ_SETTING.format(
        #             "TX Corse NCO", QSConstants.DAC_CNCO_RESOL
        #         )
        #     )
        # return frequency

        dev = self.selectedDevice(c)
        if frequency is None:
            resp = dev.get_dac_coarse_frequency()
            frequency = T.Value(resp, "Hz")
        else:
            dev.set_dac_coarse_frequency(frequency["Hz"])
        return frequency

    @setting(
        402,
        "Frequency TX Fine NCO",
        channel=["w"],
        frequency=["v[Hz]"],
        returns=["v[Hz]"],
    )
    def frequency_tx_fine_nco(self, c, channel, frequency=None):
        """
        Read and write frequency setting from/to fine NCOs.

        A D/A converter havs multiple waveform channels. Each channel center frequ-
        ency can be tuned using fine NCOs from -1.5 GHz to 1.5 GHz. Note that the
        maximum frequency difference is 1.2 GHz.

        Args:
            channel  : w
                The NCO channel index. The index number corresponds to that of wave-
                form channel index.
            frequency: v[Hz]
                The minimum resolution of NCO frequencies is 0.48828125 MHz [QSConst
                ants.DAC_FNCO_RESOL].

        Returns:
            frequency: v[Hz]

        """
        dev = self.selectedDevice(c)
        if channel not in (channels_of_port := dev.channels_of_port):
            raise ValueError(
                QSMessage.ERR_INVALID_ITEM.format("channel", channels_of_port)
            )

        elif frequency is None:
            resp = dev.get_dac_fine_frequency(channel)
            frequency = T.Value(resp, "Hz")
        elif dev.static_check_dac_fine_frequency(frequency["Hz"]):
            dev.set_dac_fine_frequency(channel, frequency["Hz"])
        else:
            raise ValueError(
                QSMessage.ERR_FREQ_SETTING.format(
                    "TX Fine NCO", QSConstants.DAC_FNCO_RESOL
                )
                + "\n"
                + QSMessage.ERR_INVALID_RANG.format(
                    "TX Fine NCO frequency",
                    "{} MHz.".format(-QSConstants.NCO_SAMPLE_F // 2),
                    "{} MHz.".format(QSConstants.NCO_SAMPLE_F // 2),
                )
            )
        return frequency

    @setting(403, "Frequency RX NCO", frequency=["v[Hz]"], returns=["v[Hz]"])
    def coarse_rx_nco_frequency(self, c, frequency=None):
        # dev = self.selectedDevice(c)
        # if QSConstant_?s.CNL_READ_VAL != dev.device_role:
        #     raise Exception(
        #         QSMessage.ERR_INVALID_DEV.format("readout", dev.name)
        #     )
        # elif frequency is None:
        #     resp = dev.get_adc_coarse_frequency()
        #     frequency = T.Value(resp, "MHz")
        # elif dev.static_check_adc_coarse_frequency(frequency["MHz"]):
        #     dev.set_adc_coarse_frequency(frequency["MHz"])
        # else:
        #     raise ValueError(
        #         QSMessage.ERR_FREQ_SETTING.format(
        #             "RX Corse NCO", QSConstants.ADC_CNCO_RESOL
        #         )
        #     )
        # return frequency

        # TODO: static_check_adc_coarse_frequency
        dev = self.selectedDevice(c)
        if dev.device_type is not DeviceType.readout:
            raise Exception(QSMessage.ERR_INVALID_DEV.format("readout", dev.name))
        elif frequency is None:
            resp = dev.get_adc_coarse_frequency()
            frequency = T.Value(resp, "Hz")
        else:
            dev.set_adc_coarse_frequency(frequency["Hz"])
        return frequency

    @setting(404, "Frequency Sideband", sideband=["s"], returns=["s"])
    def sideband_selection(self, c, sideband=None):
        """
        Read and write the frequency sideband setting to the up- and down-conversion
        mixers.

        Args:
            sideband : s
                The sideband selection string. Either 'usb' or 'lsb' (QSConstants.CN
                L_MXUSB_VAL and QSConstants.CNL_MXLSB_VAL) can be set.

        Returns:
            sideband : s
                The current sideband selection string.
        """
        dev = self.selectedDevice(c)
        if sideband is None:
            sideband = dev.get_mix_sideband()
        elif sideband not in [QSConstants.CNL_MXUSB_VAL, QSConstants.CNL_MXLSB_VAL]:
            raise Exception(
                QSMessage.ERR_INVALID_ITEM.format(
                    "The sideband string",
                    "{} or {}".format(
                        QSConstants.CNL_MXUSB_VAL, QSConstants.CNL_MXLSB_VAL
                    ),
                )
            )
        else:
            dev.set_mix_sideband(sideband)
        return sideband

    @setting(505, "Internal loopback", enabled=["b"], returns=["b"])
    def internal_loopback(self, c, enabled=None):
        """
        Enable loopback by controlling the rfswitches at the input and output ports.

        Args:
            enabled : b (bool)
                If True, the internal loopback is enabled and no output.
        Returns:
            enabled : b (bool)
                Current state of the switch.
        """
        dev = self.selectedDevice(c)

        if dev.device_type is not DeviceType.readout:
            ValueError(f"Loopback is not available for the device {dev.name}.")

        if enabled is True:
            dev.set_rfswitch(RfSwitchState.loop)
        elif enabled is False:
            dev.set_rfswitch(RfSwitchState.open)
        return dev.get_rfswitch()

    @setting(600, "List Boxes", returns=["*s"])
    def list_boxes(self, c):
        return list(self._name_to_box_conn.keys())

    @setting(601, "Reconnect Box", box_name=["s"], linkup=["b"], returns=["b"])
    def reconnect_box(self, c, box_name, linkup=False):
        if box_name not in self._name_to_box_conn:
            raise ValueError(f"Box with name {box_name} not found.")
        box_conn = self._name_to_box_conn[box_name]

        try:
            _dev_selected: Optional[DeviceWrapper] = self.selectedDevice(c)
        except DeviceNotSelectedError:
            _dev_selected = None
        locked_devices = []
        try:
            for dev in self.devices.values():
                if dev.box_conn.box_name == box_name:
                    self.selectDevice(c, dev.name)
                    self.lock_device(c, timeout=None)
                    locked_devices.append(dev)

            with locked_boxes([box_conn], c.ID):
                box_conn.disconnect(c.ID)
                box_conn.connect(linkup)
        except DeviceLockedError as e:
            print(sys._getframe().f_code.co_name, e)  # TODO: improve the message
        finally:
            for dev in locked_devices:
                self.selectDevice(c, dev.name)
                self.release_device(c)
            if _dev_selected is not None:
                self.selectDevice(c, _dev_selected.name)


def _convert_to_builtin_type(np_array, ensure_list=False):
    if isinstance(np_array, np.ndarray):
        ret = np_array.tolist()  # 0D array is converted to a simple Python "scalar"
    else:
        ret = np_array
    if ensure_list:
        if not isinstance(ret, list):
            ret = [ret]
    return ret
