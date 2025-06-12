import sys
import json
import struct

from labrad import types as T
from labrad.devices import DeviceWrapper, DeviceServer
from labrad.server import setting
from twisted.internet.defer import inlineCallbacks, returnValue

from quel_clock_master import (
    QuBEMasterClient,
    SequencerClient,
)  # for multi-sync operation
import qubelsi.qube

from .constants import QSConstants, QSMessage
from .utils import pingger

############################################################
#
# QUBE MANAGER
#
# Tips:
# When the master FPGA board halted (especially when you have tried to sync with a ghost QuBE unit),
# re-configuration of the master board is useful. Try do it using the command below thru bash.
#
# > BITFILE=/home/qube/qube_master_20220721.bit vivado -mode batch -source /config_au200.tcl
#

REGAPIPATH = "adi_api_path"

class QuBE_Manager_Device(DeviceWrapper):

    @inlineCallbacks
    def connect(self, *args, **kw):  # @inlineCallbacks
        name, role = args
        print(QSMessage.CONNECTING_CHANNEL.format(name))
        self.name = name
        self._role = role
        self._lsi_ctrl = kw["lsi_ctrl"]
        self._sync_ctrl = kw["sync_ctrl"]
        self._channel_info = kw["channels"]
        self._sync_addr = kw["sync_addr"]
        self._sync_func = kw["sync_func"]
        self._read_func = kw["read_func"]
        self._verbose = False
        yield

    @inlineCallbacks
    def initialize(self):  # @inlineCallbacks
        yield self._lsi_ctrl.do_init(rf_type=self._role, message_out=self.verbose)
        mixer_init = [
            (ch[QSConstants.CNL_MIXCH_TAG], ch[QSConstants.CNL_MIXSB_TAG])
            for ch in self._channel_info
        ]

        for ch, usb_lsb in mixer_init:  # Upper or lower sideband configuration
            if (
                usb_lsb == QSConstants.CNL_MXUSB_VAL
            ):  # in the active IQ mixer. The output
                yield self._lsi_ctrl.adrf6780[
                    ch
                ].set_usb()  # become small with a wrong sideband
            elif usb_lsb == QSConstants.CNL_MXLSB_VAL:  # setting.
                yield self._lsi_ctrl.adrf6780[ch].set_lsb()

    @inlineCallbacks
    def read_chassis_clock(self):
        syn = self._sync_ctrl
        resp = yield syn.read_clock()[1]
        returnValue(resp)

    @inlineCallbacks
    def set_microwave_switch(self, value):  # @inlineCallbacks
        g = self._lsi_ctrl.gpio
        yield g.write_value(value & 0x3FFF)

    @inlineCallbacks
    def read_microwave_switch(self):
        g = self._lsi_ctrl.gpio
        reps = yield g.read_value()
        returnValue(reps & 0x3FFF)

    @inlineCallbacks
    def read_adconverter_jesd_status(self):

        NUMBER_OF_ADCS_IN_A_CHASSIS = 2

        adc = self._lsi_ctrl.ad9082
        resp = []
        for channel in range(NUMBER_OF_ADCS_IN_A_CHASSIS):
            status = yield adc[channel].get_jesd_status()
            for _reg, _val in status:
                resp.append((channel, _reg, int(_val, 16)))
        returnValue(resp)

    @property
    def verbose(self):  # @property
        return self._verbose

    @verbose.setter
    def verbose(self, x):  # @verbose.setter
        if isinstance(x, bool):
            self._verbose = x

    @property
    def ipaddr_synchronization(self):
        return self._sync_addr

    @inlineCallbacks
    def synchronize_with_master(self):  # @inlineCallbacks

        func, srv = self._sync_func
        result = yield func(srv, self._sync_addr)
        if result:
            func, srv = self._read_func
            resp = yield func(srv)
            result = True if 0 != resp else False
        if result:
            print("QuBE_Manager_Deice.synchronize_with_master: read value = ", resp)
        returnValue(result)


class QuBE_Manager_Server(DeviceServer):
    name = QSConstants.MNRNAME
    possibleLinks = list()
    adi_api_path = None
    deviceWrapper = QuBE_Manager_Device

    @inlineCallbacks
    def initServer(self):  # @inlineCallbacks
        yield DeviceServer.initServer(self)

        cxn = self.client
        reg = cxn[QSConstants.REGSRV]
        try:
            yield reg.cd(QSConstants.REGDIR)
            config = yield reg.get(QSConstants.REGLNK)
            self.possibleLinks = self.extract_links(json.loads(config))
            self.master_link = yield reg.get(QSConstants.REGMASTERLNK)
            self.adi_api_path = yield reg.get(REGAPIPATH)

            self._master_ctrl = yield QuBEMasterClient(
                self.master_link, receiver_limit_by_bind=True
            )
            self.__is_clock_opened = True
        except Exception as e:
            print(sys._getframe().f_code.co_name, e)

    def extract_links(self, link):
        return [
            (
                _name,
                link[_name][QSConstants.SRV_QUBETY_TAG],
                link[_name][QSConstants.SRV_IPLSI_TAG],
                link[_name][QSConstants.SRV_IPCLK_TAG],
                link[_name][QSConstants.SRV_CHANNEL_TAG],
            )
            for _name in link.keys()
        ]

    def initContext(self, c):
        DeviceServer.initContext(self, c)
        c[QSConstants.SYN_CNXT_TAG] = dict()

    @inlineCallbacks
    def findDevices(self):  # @inlineCallbacks
        cxn = self.client
        found = list()

        for _name, _type, _iplsi, _ipclk, _channel in self.possibleLinks:
            print(QSMessage.CHECKING_QUBEUNIT.format(_name))
            try:
                res = pingger(_iplsi)
                if 0 == res:
                    res = pingger(_ipclk)
                else:
                    raise Exception(QSMessage.ERR_HOST_NOTFOUND.format(_name))
            except Exception as e:
                print(sys._getframe().f_code.co_name, e)
                continue

            print(QSMessage.CNCTABLE_QUBEUNIT.format(_name))
            device = yield self.instantiateQube(_name, _type, _iplsi, _ipclk, _channel)
            found.append(device)
            yield

        returnValue(found)

    @inlineCallbacks
    def instantiateQube(
        self, name, role, iplsi, ipclk, channel_info
    ):  # @inlineCallbacks
        lsi_ctrl = yield qubelsi.qube.Qube(iplsi, self.adi_api_path)
        sync_ctrl = yield SequencerClient(ipclk, receiver_limit_by_bind=True)
        args = (name, role)
        kw = dict(
            lsi_ctrl=lsi_ctrl,
            sync_ctrl=sync_ctrl,
            sync_addr=ipclk,
            sync_func=(QuBE_Manager_Server._synchronize_with_master_clock, self),
            read_func=(QuBE_Manager_Server._read_master_clock, self),
            channels=channel_info,
        )
        returnValue((name, args, kw))

    @setting(100, "Reset", returns=["b"])
    def device_reinitialize(self, c):
        """
        Reset QuBE units.

        This routine resets ICs in a QuBE unit such as local oscillators, AD/DA
        converters, analog mixers, etc.

        Returns:
            success : Always True
        """
        dev = self.selectedDevice(c)
        yield dev.initialize()
        returnValue(True)

    @setting(101, "Microwave Switch", value=["w"], returns=["w"])
    def microwave_switch(self, c, value=None):
        """
        Read and write the microwave switch settting.

        The on-off setting of the microwave switch at each ports can be set using the following setting
        bits. The logic high '1' = 0b1 makes the switch off or loop back state. The logic AND of the
        settings bits become a value to the argument [value].

            0x0003 - channel 0-1
            0x0004 - channel 2
            0x0020 - channel 5
            0x0040 - channel 6
            0x0080 - channel 7
            0x0100 - channel 8
            0x0800 - channel 11
            0x3000 - channel 12-13

        Args:
            value : w (unsigned int)
                See above.
        Returns:
            value : w (unsigned int)
                Current status of the switch is retrieved.
        """
        dev = self.selectedDevice(c)
        if value is not None:
            yield dev.set_microwave_switch(value)
        else:
            value = yield dev.read_microwave_switch()
        returnValue(value)

    @setting(200, "Debug Verbose", flag=["b"], returns=["b"])
    def debug_verbose_message(self, c, flag=None):
        """
        Select debugging mode.

        Set flag = True to see long message output in the console.

        Args:
            flag : b (bool)
        Returns:
            flag : b (bool)
        """
        dev = self.selectedDevice(c)
        if flag is not None:
            dev.verbose = flag
        return dev.verbose

    @setting(201, "Debug JESD Status", returns=["*(isi)"])
    def debug_jesd_status(self, c):
        """
        Read JESD Status register in AD9082

        Returns:
            list: a list of (channel: integer, register name: string, value : interger)
        """
        dev = self.selectedDevice(c)
        resp = yield dev.read_adconverter_jesd_status()

        returnValue(resp)

    @setting(301, "Reconnect Master Clock", returns=["b"])
    def reconnect_master(self, c):
        """
        Reconnect to the master FPGA board.

        reconnect_master_clock() close the UDP port to the master and reopen the
        socket to the master clock FPGA board.

        Returns:
            flag : b (bool)
                Always True
        """

        if self.__is_clock_opened:
            del self._master_ctrl

        self._master_ctrl = QuBEMasterClient(
            self.master_link, receiver_limit_by_bind=True
        )
        self.__is_clock_opened = True

        return True

    @setting(302, "Clear Master Clock", returns=["b"])
    def clear_master_clock(self, c):
        """
        Reset synchronization clock in the master FPGA board.

        This method reset the syncronization clock in the master FPGA board.

        Returns:
            flag : b (bool)
                Always True
        """

        if not self.__is_clock_opened:
            raise Exception(QSMessage.ERR_DEV_NOT_OPEN)

        resp = False
        try:
            ret = yield self._master_ctrl.clear_clock()
            resp = True
        except Exception as e:
            print(sys._getframe().f_code.co_name, e)
            raise (e)

        returnValue(resp)

    @setting(303, "Read Master Clock", returns=["ww"])
    def read_master_clock(self, c):
        """
        Read synchronization clock in the master FPGA board.

        This method read the value of syncronization clock in the master FPGA board.

        Returns:
            clock : ww (two of 32-bit unsigned int)
                The first and the last 32-bit words corresponds to the high and low
                words of the clock value represented in 64-bit unsigned int.
        """
        resp = yield self._read_master_clock()
        h = (resp & 0xFFFFFFFF00000000) >> 32
        l = resp & 0xFFFFFFFF
        returnValue((h, l))

    @setting(305, "Read Chassis Clock", returns=["ww"])
    def read_chassis_clock(self, c):
        """
        Read synchronization clock in QuBE AU50 FPGA boards.

        This method read the value of syncronization clock in the master FPGA board.

        Returns:
            clock : ww (two of 32-bit unsigned int)
                The first and the last 32-bit words corresponds to the high and low
                words of the clock value represented in 64-bit unsigned int.
        """
        dev = self.selectedDevice(c)
        resp = yield dev.read_chassis_clock()
        h = (resp & 0xFFFFFFFF00000000) >> 32
        l = resp & 0xFFFFFFFF
        returnValue((h, l))

    @setting(304, "Synchronize Clock", returns=["b"])
    def synchronize_with_master(self, c):
        """
        Synchronize QuBE unit to the clock in the master FPGA board.

        This method triggers synchronization between the master and the selected
        device clocks.

        Returns:
            flag : b (bool)
                Always True
        """
        dev = self.selectedDevice(c)
        resp = yield dev.synchronize_with_master()
        returnValue(resp)

    @inlineCallbacks
    def _synchronize_with_master_clock(self, target_addr):  # @inlineCallbacks

        if not self.__is_clock_opened:
            raise Exception(QSMessage.ERR_DEV_NOT_OPEN)

        resp = False
        try:
            ret, fromaddr = yield self._master_ctrl.kick_clock_synch([target_addr])
            if 16 <= len(ret):
                (ret,) = struct.unpack("b", ret[:1])
            if 0 <= ret:
                print("sync", ret)
            else:
                raise Exception(QSMessage.ERR_MAST_NOT_OPEN)
            resp = True
        except Exception as e:
            print(sys._getframe().f_code.co_name, e)

        returnValue(resp)

    @inlineCallbacks
    def _read_master_clock(self):  # @inlineCallbacks

        if not self.__is_clock_opened:
            raise Exception(QSMessage.ERR_DEV_NOT_OPEN)

        resp = 0
        try:
            ret = yield self._master_ctrl.read_clock()
            resp = ret
        except Exception as e:
            print(sys._getframe().f_code.co_name, e)
            raise (e)

        returnValue(resp)

    @setting(306, "Debug Select Synchronization Device")
    def debug_select_synchronization_device(self, c):
        """
        Register selected DAC AWG chassis as a synchronization target chassis

        This method [debug_select_synchronization_device] selects target chasssis
        for group synchronization. The group synchronization is a synchronization
        method which is better than individual synchronization with the master
        trigger FPGA.
        """
        dev = self.selectedDevice(c)
        chassis_name = dev.name

        if chassis_name not in c[QSConstants.SYN_CNXT_TAG].keys():
            sync_ip_addr = dev.ipaddr_synchronization
            c[QSConstants.SYN_CNXT_TAG].update({chassis_name: sync_ip_addr})

    @setting(307, "Debug Group Synchronization")
    def debug_group_synchronization(self, c):
        """
        Group synchronization.

        This method synchronize timing clock among chassis through the master FPGA
        board. Target chassis have to be selected using [debug_select_synchronizatio
        n_device()] before the call.
        """
        if 1 >= len(c[QSConstants.SYN_CNXT_TAG]):
            returnValue(False)

        print("Group synchronization", list(c[QSConstants.SYN_CNXT_TAG].keys()))
        target_addrs = c[QSConstants.SYN_CNXT_TAG].values()
        resp = False
        try:
            ret, fromaddr = yield self._master_ctrl.kick_clock_synch(target_addrs)
            if 16 <= len(ret):
                (ret,) = struct.unpack("b", ret[:1])
            if 0 <= ret:
                print("sync", ret)
            else:
                raise Exception(QSMessage.ERR_MAST_NOT_OPEN)

            c[QSConstants.SYN_CNXT_TAG] = dict()  # Clear the selected chassis
            resp = True
        except Exception as e:
            print(sys._getframe().f_code.co_name, e)

        returnValue(resp)

