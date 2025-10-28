import sys
import json
import concurrent
import re

import numpy as np

from labrad import types as T
from labrad.devices import DeviceServer
from labrad.server import setting
from labrad.units import Value
from labrad.concurrent import future_to_deferred
from twisted.internet.defer import inlineCallbacks, returnValue

from e7awgsw import (
    AwgCtrl,
    AWG,
    CaptureModule,
)
from quel_clock_master import (
    QuBEMasterClient,
    SequencerClient,
)  # for multi-sync operation

from quel_ic_config import Quel1Box, Quel1BoxType
from quel_ic_config.e7resource_mapper import Quel1E7ResourceMapper

from constants import QSConstants, QSMessage
from devices import QuBE_ReadoutLine, QuBE_ControlLine
from utils import pingger, QuBECaptureCtrl

from qube_box_setup_helper import QubeBoxInfo, QubePortMapper


############################################################
#
# QUBE SERVER
#
class QuBE_Server(DeviceServer):
    name = QSConstants.SRVNAME
    deviceWrappers = {
        QSConstants.CNL_READ_VAL: QuBE_ReadoutLine,
        QSConstants.CNL_CTRL_VAL: QuBE_ControlLine,
    }
    possibleLinks = {}
    chassisSkew = {}

    @inlineCallbacks
    def initServer(self):  # @inlineCallbacks
        yield DeviceServer.initServer(self)

        cxn = self.client
        reg = cxn[QSConstants.REGSRV]
        try:
            yield reg.cd(QSConstants.REGDIR)
            config = yield reg.get(QSConstants.REGLNK)
            self.possibleLinks = json.loads(config)
            skew = yield reg.get(QSConstants.REGSKEW)
            self.chassisSkew = json.loads(skew)
            self._sync_ctrl = dict()
            self.box_info = QubeBoxInfo()
        except Exception as e:
            print(sys._getframe().f_code.co_name, e)

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
        c[QSConstants.DAQ_TOUT_TAG] = QSConstants.DAQ_INITTOUT
        c[QSConstants.DAQ_SDLY_TAG] = QSConstants.DAQ_INITSDLY

    def chooseDeviceWrapper(self, *args, **kw):
        tag = (
            QSConstants.CNL_READ_VAL
            if QSConstants.CNL_READ_VAL in args[2]
            else QSConstants.CNL_CTRL_VAL
        )
        return self.deviceWrappers[tag]

    # QuEL-1 SE Riken8 の場合
    # | MxFEの番号 | DAC番号 | (group, line)　 | ポート番号 | 機能 |
    # |-----------|-------|----------------|-------|--|
    # | 0       | 0     | (0, 0)         | 1     | Read-out |
    # | 0       | 1     | (0, 1)         | 1     | Fogi |
    # | 0       | 2     | (0, 2)         | 2     | Pump |
    # | 0       | 3     | (0, 3)         | 3     | Ctrl |
    # | 1       | 0     | (1, 0)         | 6     | Ctrl |
    # | 1       | 1     | (1, 1)         | 7    | Ctrl |
    # | 1       | 2     | (1, 2)         | 8    | Ctrl |
    # | 1       | 3     | (1, 3)         | 9     | Ctrl |

    def parse_qube_device_id(self, device_id):
        devicd_id_regexp = re.compile(
            r"(quel1se-[0-9]-[0-9]{2})-(control|readout|pump|fogi)_([0-9a-d]{1,2})"
        )
        m = devicd_id_regexp.match(device_id)
        if m:
            device_name = m.group(1)
            port_type = m.group(2)
            port_string = m.group(3)
        else:
            raise ValueError(f"Cannot parse device_id: {device_id}")
        return device_name, port_type, port_string

    def get_dac_group_line_from_name(self, box, pmaper, name):
        _, role, port_string = self.parse_qube_device_id(name)
        # if role == "readout":
        #     for c in port_string:
        #         port = int(c, 16)
        #         group, line = pmaper.resolve_line(port)
        #         if box._dev.is_output_line(group, line):  # select the output line
        #             break
        # else:
        #     port = int(port_string, 16)
        #     group, line = pmaper.resolve_line(port)
        # return group, line
        
        # TODO: fogi
        port_group_map = {
            1: (0,0),
            2: (0,2),
            3: (0,3),   
            6: (1,0),
            7: (1,1),
            8: (1,2),
            9: (1,3)           
        }
        #print(f"get_dac_group_line_from_name port_string: {port_string} ")
        port = int(port_string)
        return port_group_map.get(port)

    # def get_adc_group_line_from_name(self, box, pmaper, name):
    #     # _, role, port_string = self.parse_qube_device_id(name)
    #     # if role == "readout":
    #     #     for c in port_string:
    #     #         port = int(c, 16)
    #     #         group, line = pmaper.resolve_line(port)
    #     #         if box._dev.is_input_line(group, line):  # select the input line
    #     #             break
    #     # else:
    #     #     raise Exception("Only readout line has adc.")
    #     return group, line

    def instantiateChannel(self, name, channels, awg_ctrl, cap_ctrl, info):
        box_type = self.box_info.get_box_type(name)
        box_type_str = self.box_info.get_box_type_str(name)
        ipfpga = info[QSConstants.SRV_IPFPGA_TAG]
        iplsi = info[QSConstants.SRV_IPLSI_TAG]
        ipsync = info[QSConstants.SRV_IPCLK_TAG]
        box = Quel1Box.create(
            ipaddr_wss=ipfpga,
            boxtype=box_type,
        )
        # box.reconnect()
        rmap = Quel1E7ResourceMapper(box.css, box.wss)

        def gen_awg(name, role, chassis, channel, awg_ctrl, cap_ctrl):
            pmaper = QubePortMapper(box_type_str)
            group, line = self.get_dac_group_line_from_name(box, pmaper, name)
            # TODO: rline type:B の場合は、rline = "m" にする？ そうでもないらしい。
            try:
                group, rline = self.get_dac_group_line_from_name(box, pmaper, name)
            except:
                rline = None
            awg_ch_ids = []
            # TODO: モニター
            #rline = "m"
            rline = "r"
            # print(f"gen_awg() name: {name}, group: {group}, line: {line}, rline: {rline}")
            chs = box._dev.get_channels_of_line(group, line)
            for i in range(len(chs)):
                awg_idx = rmap.get_awg_of_channel(group, line, i)
                awg_ch_ids.append(awg_idx)

            args = name, role
            kw = dict(
                awg_ctrl=awg_ctrl,
                awg_ch_ids=awg_ch_ids,
                chassis=chassis,
                ipfpga=ipfpga,
                iplsi=iplsi,
                ipsync=ipsync,
                device_type=box_type,
                group=group,
                line=line,
                rline=rline,
            )

            return (name, args, kw)

        def gen_mux(name, role, chassis, channel, awg_ctrl, cap_ctrl):
            _name, _args, _kw = gen_awg(
                name, role, chassis, channel, awg_ctrl, cap_ctrl
            )
            pmaper = QubePortMapper(box_type_str)
            group, line = self.get_dac_group_line_from_name(box, pmaper, name)
            #print(f"name: {name}, group: {group}, line: {line}")
            # TODO: モニター: こちらはモニターの場合でも"r"のままでよい
            cap_mod_id = rmap.get_capture_module_of_rline(group, "r")
            #cap_mod_id = rmap.get_capture_module_of_rline(group, "m")
            #print(f"cap_mod_id: {cap_mod_id}")
            capture_units = CaptureModule.get_units(cap_mod_id)
            #print(f"capture_units: {capture_units}")

            kw = dict(
                cap_ctrl=cap_ctrl,
                capture_units=capture_units,
                cap_mod_id=cap_mod_id,
            )
            _kw.update(kw)
            return (_name, _args, _kw)

        devices = []
        for channel in channels:
            #print(f"instantiateChannel: {channel}")
            channel_type = channel[QSConstants.CNL_TYPE_TAG]
            channel_name = name + "-" + channel[QSConstants.CNL_NAME_TAG]
            #print(f"channel_type: {channel_type}, channel_name: {channel_name}")
            # TODO fogiはスキップ
            if "fogi" in channel_name:
                continue
            
            args = (
                channel_name,
                channel_type,
                name,
                channel,
                awg_ctrl,
                cap_ctrl,
            )
            to_be_added = (
                gen_awg(*args)
                if channel_type == QSConstants.CNL_CTRL_VAL or channel_type == QSConstants.CNL_PUMP_VAL
                else (
                    gen_mux(*args) if channel_type == QSConstants.CNL_READ_VAL else None
                )
            )
            if to_be_added is not None:
                devices.append(to_be_added)
        return devices

    def instantiateQube(self, name, info):
        try:
            ipfpga = info[QSConstants.SRV_IPFPGA_TAG]
            channels = info["channels"]
        except Exception as e:
            print(sys._getframe().f_code.co_name, e)
            return list()

        try:
            awg_ctrl = AwgCtrl(ipfpga)  # AWG CONTROL (e7awgsw)
            cap_ctrl = QuBECaptureCtrl(ipfpga)  # CAP CONTROL (inherited from e7awgsw)
            awg_ctrl.initialize(*AWG.all())
            cap_ctrl.initialize(*CaptureModule.all())
        except Exception as e:
            print(sys._getframe().f_code.co_name, e)
            return list()

        try:
            devices = self.instantiateChannel(name, channels, awg_ctrl, cap_ctrl, info)
        except Exception as e:
            print("Exception!!! instantiateChannel")
            print(sys._getframe().f_code.co_name, e)
            devices = list()
        return devices

    @inlineCallbacks
    def findDevices(self):  # @inlineCallbacks
        cxn = self.client
        found = []

        for name in self.possibleLinks.keys():
            print(QSMessage.CHECKING_QUBEUNIT.format(name))
            # try:
            #     res = pingger(self.possibleLinks[name][QSConstants.SRV_IPFPGA_TAG])
            #     if 0 == res:
            #         res = pingger(self.possibleLinks[name][QSConstants.SRV_IPLSI_TAG])
            #     if 0 != res:
            #         res = pingger(self.possibleLinks[name][QSConstants.SRV_IPCLK_TAG])
            #     if 0 != res:
            #         raise Exception(QSMessage.ERR_HOST_NOTFOUND.format(name))
            # except Exception as e:
            #     print(sys._getframe().f_code.co_name, e)
            #     continue

            print(QSMessage.CNCTABLE_QUBEUNIT.format(name))
            devices = self.instantiateQube(name, self.possibleLinks[name])
            found.extend(devices)

            sync_ctrl = SequencerClient(
                self.possibleLinks[name][QSConstants.SRV_IPCLK_TAG],
                receiver_limit_by_bind=True,
            )
            self._sync_ctrl.update({name: sync_ctrl})
            yield
            # print(sys._getframe().f_code.co_name,found)
        returnValue(found)

    @setting(10, "Reload Skew", returns=["b"])
    def reload_config_skew(self, c):
        """
        Reload skew adjustment time difference among chassis from the registry.

        Returns:
            success : True if successfuly obtained skew value from the registry
        """
        cxn = self.client
        reg = cxn[QSConstants.REGSRV]
        try:
            skew = yield reg.get(QSConstants.REGSKEW)
            self.chassisSkew = json.loads(skew)
        except Exception as e:
            print(sys._getframe().f_code.co_name, e)
            return False
        return True

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

        The method name [daq_start()] is for backward compatibility with a former
        version of quantum logic analyzer, and I like it. This method finds trigger
        boards to readout FPGA circuits and give them to the boards. All the
        enabled AWGs and MUXs are supposed to be in the current context [c] through
        [._register_awg_channels()] and [_register_mux_channels()].

        Compared to the previous implementation, this method does not require
        [select_device()] before the call.
        """
        dev = self.selectedDevice(c)

        if (
            QSConstants.CNL_READ_VAL == dev.device_role
        ):  # Set trigger board to capture units
            self._readout_mux_start(c)

        for chassis_name in c[QSConstants.ACQ_CNXT_TAG].keys():
            for _dev, _m, _units in c[QSConstants.ACQ_CNXT_TAG][chassis_name]:
                print(chassis_name, _units)  # DEBUG

        for chassis_name in c[QSConstants.DAC_CNXT_TAG].keys():
            _dev, _awgs = c[QSConstants.DAC_CNXT_TAG][chassis_name]
            print(chassis_name, _awgs)  # DEBUG
        return True

    def _readout_mux_start(self, c):
        """
        Find trigger AWG bords in multiple chassis

        For each QuBE chassis, we have to select trigger AWG from the AWGs involved
        in the operation. For each QuBE readout module, [_readout_mux_start()]
        sets the trigger AWG and enables the capture units.

        """
        for chassis_name in c[QSConstants.ACQ_CNXT_TAG].keys():
            if chassis_name not in c[QSConstants.DAC_CNXT_TAG].keys():
                raise Exception(QSMessage.ERR_NOARMED_DAC)
            else:
                dev, awgs = c[QSConstants.DAC_CNXT_TAG][chassis_name]
                trigger_board = list(awgs)[0]

            for _dev, _module, _units in c[QSConstants.ACQ_CNXT_TAG][chassis_name]:
                _dev.set_trigger_board(trigger_board, _units)
        return

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
        tentative_master = list(chassis_list)[0]
        clock = (
            self._sync_ctrl[tentative_master].read_clock()[1] + delay
        ) & 0xFFFFFFFFFFFFFFF0
        # In a case where we use master FPGA
        # board as a trigger source
        # clock = self._master_ctrl.read_clock() + delay
        for chassis_name in chassis_list:
            skew = self.chassisSkew[chassis_name]
            dev, enabled_awgs = c[QSConstants.DAC_CNXT_TAG][chassis_name]
            awg_bitmap = 0
            for _awg in enabled_awgs:
                if 0 <= _awg and _awg < 16:
                    awg_bitmap += 1 << _awg
            resp = self._sync_ctrl[chassis_name].add_sequencer(clock + skew, awg_bitmap)
            print(chassis_name, "kick at ", clock + skew, enabled_awgs)

        return True

    @setting(107, "DAQ Stop", returns=["b"])
    def daq_stop(self, c):
        """
        Wait until synchronous measurement is done.

        """
        if 1 > len(c[QSConstants.DAC_CNXT_TAG].keys()):
            return False  # Nothing to stop

        for chassis_name in c[QSConstants.DAC_CNXT_TAG].keys():

            dev, enabled_awgs = c[QSConstants.DAC_CNXT_TAG][chassis_name]
            concurrent_deferred_obj = self._thread_pool.submit(
                dev.stop_daq, list(enabled_awgs), c[QSConstants.DAQ_TOUT_TAG]
            )
            twisted_deferred_obj = future_to_deferred(concurrent_deferred_obj)

            result = yield twisted_deferred_obj

        returnValue(True)

    @setting(112, "DAQ Clear", returns=["b"])
    def daq_clear(self, c):
        """
        Clear registed control and readout channels from the device context.

        """
        c[QSConstants.DAC_CNXT_TAG] = dict()
        c[QSConstants.ACQ_CNXT_TAG] = dict()

        return True  # Nothing to stop

    @setting(113, "DAQ Terminate", returns=["b"])
    def daq_terminate(self, c):
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
        """
        Retrieve the number of available AWG channels. The number of available AWG c
        hannels is configured through adi_api_mod/v1.0.6/src/helloworld.c and the
        lane information is stored in the registry /Servers/QuBE/possible_links.

        Returns:
            channels : w
                The number of available AWG channels.
        """
        dev = self.selectedDevice(c)
        return dev.number_of_awgs

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
        channels = np.atleast_1d(channels).astype(int)
        if not dev.check_awg_channels(channels):
            raise ValueError(
                QSMessage.ERR_INVALID_RANG.format(
                    "awg index", 0, dev.number_of_awgs - 1
                )
            )
        return self._register_awg_channels(c, dev, channels)

    def _register_awg_channels(self, c, dev, channels):
        """
        Register selected DAC AWG channels

        The method [_register_awg_channels()] register the enabled AWG IDs to the device
        context. This information is used in daq_start() and daq_trigger()

        Data structure:
          qube010: (dev, set{0,1,2,3,...}),
          qube011: (dev, set{0,2,15,..})
        """
        chassis_name = dev.chassis_name

        if chassis_name not in c[QSConstants.DAC_CNXT_TAG].keys():
            c[QSConstants.DAC_CNXT_TAG].update({chassis_name: (dev, set())})

        _to_be_added = list()
        for channel in channels:
            _dev, awgs = c[QSConstants.DAC_CNXT_TAG][chassis_name]

            _to_be_added = dev.get_awg_id(channel)
            awgs.add(_to_be_added)
            c[QSConstants.DAC_CNXT_TAG][chassis_name] = (_dev, awgs)

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
        if QSConstants.CNL_READ_VAL != dev.device_role:
            raise Exception(
                QSMessage.ERR_INVALID_DEV.format("readout", dev.device_name)
            )

        muxchs = np.atleast_1d(muxchs).astype(int)
        for _mux in muxchs:
            if not dev.static_check_mux_channel_range(_mux):
                raise ValueError(
                    QSMessage.ERR_INVALID_RANG.format(
                        "muxch", 0, QSConstants.ACQ_MULP - 1
                    )
                )
        resp = dev.upload_readout_parameters(muxchs)
        if resp:
            resp = self._register_mux_channels(c, dev, muxchs)
        return resp

    def _register_mux_channels(self, c, dev, selected_mux_channels):
        """
        Register selected readout channels

        The method [_register_mux_channels()] register the selected capture module
        IDs and the selected capture units to the device context. This information
        is used in daq_start() and daq_trigger().

        """
        chassis_name = dev.chassis_name
        module_id = dev.get_capture_module_id()
        unit_ids = [dev.get_capture_unit_id(_s) for _s in selected_mux_channels]

        if chassis_name not in c[QSConstants.ACQ_CNXT_TAG].keys():
            c[QSConstants.ACQ_CNXT_TAG].update({chassis_name: list()})

        registered_ids = [
            _id for _d, _id, _u in c[QSConstants.ACQ_CNXT_TAG][chassis_name]
        ]
        try:
            addition = False
            idx = registered_ids.index(module_id)
        except ValueError as e:
            c[QSConstants.ACQ_CNXT_TAG][chassis_name].append((dev, module_id, unit_ids))
        else:
            addition = True

        if addition:
            _dev, _module_id, registered_units = c[QSConstants.ACQ_CNXT_TAG][
                chassis_name
            ][idx]
            registered_units.extend(
                [unit_id for unit_id in unit_ids if unit_id not in registered_units]
            )
            c[QSConstants.ACQ_CNXT_TAG][chassis_name][idx] = (
                dev,
                module_id,
                registered_units,
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
        channels = np.atleast_1d(channels).astype(int)
        waveforms = np.atleast_2d(wavedata).astype(complex)

        if not dev.check_awg_channels(channels):
            raise ValueError(
                QSMessage.ERR_INVALID_RANG.format(
                    "awg index", 0, dev.number_of_awgs - 1
                )
            )
        
        resp, number_of_chans, data_length = dev.check_waveform(waveforms, channels)
        if not resp:
            raise ValueError(QSMessage.ERR_INVALID_WAVD.format(number_of_chans))

        return dev.upload_waveform(waveforms, channels)

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
        if QSConstants.CNL_READ_VAL != dev.device_role:
            raise Exception(
                QSMessage.ERR_INVALID_DEV.format("readout", dev.device_name)
            )

        muxchs = np.atleast_1d(muxchs).astype(int)
        for _mux in muxchs:
            if not dev.static_check_mux_channel_range(_mux):
                raise ValueError(
                    QSMessage.ERR_INVALID_RANG.format(
                        "muxch", 0, QSConstants.ACQ_MULP - 1
                    )
                )

        data = dev.download_waveform(muxchs)

        return data

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
        if QSConstants.CNL_READ_VAL != dev.device_role:
            raise Exception(
                QSMessage.ERR_INVALID_DEV.format("readout", dev.device_name)
            )
        elif not dev.static_check_mux_channel_range(muxch):
            raise ValueError(
                QSMessage.ERR_INVALID_RANG.format("muxch", 0, QSConstants.ACQ_MULP - 1)
            )
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
        if QSConstants.CNL_READ_VAL != dev.device_role:
            raise Exception(
                QSMessage.ERR_INVALID_DEV.format("readout", dev.device_name)
            )
        elif not dev.static_check_mux_channel_range(muxch):
            raise ValueError(
                QSMessage.ERR_INVALID_RANG.format("muxch", 0, QSConstants.ACQ_MULP - 1)
            )
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
        if QSConstants.CNL_READ_VAL != dev.device_role:
            raise Exception(
                QSMessage.ERR_INVALID_DEV.format("readout", dev.device_name)
            )
        elif not dev.static_check_mux_channel_range(muxch):
            raise ValueError(
                QSMessage.ERR_INVALID_RANG.format("muxch", 0, QSConstants.ACQ_MULP - 1)
            )
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
            if resp:
                module_enabled = c[QSConstants.ACQ_CNXT_TAG][chassis_name]
                resp = True
                try:
                    idx = [_m for _d, _m, _u in module_enabled].index(
                        dev.get_capture_module_id()
                    )
                except ValueError as e:
                    resp = False
            if resp:
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
        if QSConstants.CNL_READ_VAL != dev.device_role:
            raise Exception(
                QSMessage.ERR_INVALID_DEV.format("readout", dev.device_name)
            )
        elif not dev.static_check_mux_channel_range(muxch):
            raise ValueError(
                QSMessage.ERR_INVALID_RANG.format("muxch", 0, QSConstants.ACQ_MULP - 1)
            )
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
        if QSConstants.CNL_READ_VAL != dev.device_role:
            raise Exception(
                QSMessage.ERR_INVALID_DEV.format("readout", dev.device_name)
            )
        elif not dev.static_check_mux_channel_range(muxch):
            raise ValueError(
                QSMessage.ERR_INVALID_RANG.format("muxch", 0, QSConstants.ACQ_MULP - 1)
            )
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
        # TODO: 後で消す
        #print(f"dev.device_name: {dev.device_name}")
        if frequency is None:
            resp = dev.get_lo_frequency()
            frequency = T.Value(resp, "MHz")
        elif dev.static_check_lo_frequency(frequency["MHz"]):
            dev.set_lo_frequency(frequency["MHz"])
        else:
            raise ValueError(
                QSMessage.ERR_FREQ_SETTING.format("LO", QSConstants.DAC_LO_RESOL)
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
            frequency = T.Value(resp, "MHz")
        else:
            dev.set_dac_coarse_frequency(frequency["MHz"])
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
        if not dev.check_awg_channels([channel]):
            raise ValueError(
                QSMessage.ERR_INVALID_RANG.format(
                    "awg index", 0, dev.number_of_awgs - 1
                )
            )
        elif frequency is None:
            resp = dev.get_dac_fine_frequency(channel)
            frequency = T.Value(resp, "MHz")
        elif dev.static_check_dac_fine_frequency(frequency["MHz"]):
            dev.set_dac_fine_frequency(channel, frequency["MHz"])
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
        # if QSConstants.CNL_READ_VAL != dev.device_role:
        #     raise Exception(
        #         QSMessage.ERR_INVALID_DEV.format("readout", dev.device_name)
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
        if QSConstants.CNL_READ_VAL != dev.device_role:
            raise Exception(
                QSMessage.ERR_INVALID_DEV.format("readout", dev.device_name)
            )
        elif frequency is None:
            resp = dev.get_adc_coarse_frequency()
            frequency = T.Value(resp, "MHz")
        else:
            dev.set_adc_coarse_frequency(frequency["MHz"])
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
