import json
import os
import subprocess
import sys

from e7awgsw import CaptureCtrl
from e7awgsw.memorymap import CaptureMasterCtrlRegs

from .constants import QSConstants


############################################################
#
# TOOLS
#
def pingger(host):
    cmd = "ping -c 1 -W 1 %s" % host
    with open(os.devnull, "w") as f:
        resp = subprocess.call(cmd.split(" "), stdout=f, stderr=subprocess.STDOUT)
    return resp


############################################################
#
# e7awgsw wrappers
#
class QuBECaptureCtrl(CaptureCtrl):
    def terminate_capture_units(self, *capture_unit_id_list):
        with self._CaptureCtrl__flock:
            self._CaptureCtrl__select_ctrl_target(*capture_unit_id_list)
            self._CaptureCtrl__reg_access.write_bits(
                CaptureMasterCtrlRegs.ADDR,
                CaptureMasterCtrlRegs.Offset.CTRL,
                CaptureMasterCtrlRegs.Bit.CTRL_TERMINATE,
                1,
                0,
            )
            self._CaptureCtrl__reg_access.write_bits(
                CaptureMasterCtrlRegs.ADDR,
                CaptureMasterCtrlRegs.Offset.CTRL,
                CaptureMasterCtrlRegs.Bit.CTRL_TERMINATE,
                1,
                1,
            )
            self._CaptureCtrl__reg_access.write_bits(
                CaptureMasterCtrlRegs.ADDR,
                CaptureMasterCtrlRegs.Offset.CTRL,
                CaptureMasterCtrlRegs.Bit.CTRL_TERMINATE,
                1,
                0,
            )
            self._CaptureCtrl__deselect_ctrl_target(*capture_unit_id_list)


############################################################
#
# AUX SUBROUTINES FOR EASY SETUP
#
# > import labrad
# > import QubeServer
# > cxn  = labrad.connect()
# > conf = QubeServer.basic_config()
# > QubeServer.load_config(cxn,conf)
# > QubeServer.load_skew_zer(cxn)
#
def basic_config():
    _name_tag = QSConstants.CNL_NAME_TAG
    _type_tag = QSConstants.CNL_TYPE_TAG
    _control_val = QSConstants.CNL_CTRL_VAL
    _readout_val = QSConstants.CNL_READ_VAL
    mixer_tag = QSConstants.CNL_MIXCH_TAG
    usb_lsb_tag = QSConstants.CNL_MIXSB_TAG
    usb_val = QSConstants.CNL_MXUSB_VAL
    lsb_val = QSConstants.CNL_MXLSB_VAL
    gpiosw_tag = QSConstants.CNL_GPIOSW_TAG

    control_qube_500_1500 = [
        {
            _name_tag: "control_0",
            _type_tag: _control_val,
            "ch_dac": [15],  # awg id
            "cnco_dac": (0, 0),  # chip, main path id
            "fnco_dac": [(0, 0)],  # chip, link no
            "lo_dac": 0,  # local oscillator id
            mixer_tag: 0,  # mixer channel
            usb_lsb_tag: lsb_val,  # mixder sideband (initial value)
            gpiosw_tag: 0x0003,  # switch mask bit(s)
        },
        {
            _name_tag: "control_2",
            _type_tag: _control_val,
            "ch_dac": [14],  # awg id
            "cnco_dac": (0, 1),  # chip, main path id
            "fnco_dac": [(0, 1)],  # chip, link no
            "lo_dac": 1,  # local oscillator id
            mixer_tag: 1,  # mixer channel
            usb_lsb_tag: lsb_val,  # mixder sideband (initial value)
            gpiosw_tag: 0x0004,  # switch mask bit(s)
        },
        {
            _name_tag: "control_5",
            _type_tag: _control_val,
            "ch_dac": [11, 12, 13],  # awg id
            "cnco_dac": (0, 2),  # chip, main path id
            "fnco_dac": [(0, 4), (0, 3), (0, 2)],  # chip, link no
            "lo_dac": 2,  # local oscillator id
            mixer_tag: 2,  # mixer channel
            usb_lsb_tag: lsb_val,  # mixder sideband (initial value)
            gpiosw_tag: 0x0020,  # switch mask bit(s)
        },
        {
            _name_tag: "control_6",
            _type_tag: _control_val,
            "ch_dac": [8, 9, 10],  # awg id
            "cnco_dac": (0, 3),  # chip, main path id
            "fnco_dac": [(0, 5), (0, 6), (0, 7)],  # chip, link no
            "lo_dac": 3,  # local oscillator id
            mixer_tag: 3,  # mixer channel
            usb_lsb_tag: lsb_val,  # mixder sideband (initial value)
            gpiosw_tag: 0x0040,  # switch mask bit(s)
        },
        {
            _name_tag: "control_7",
            _type_tag: _control_val,
            "ch_dac": [5, 6, 7],  # awg id
            "cnco_dac": (1, 0),  # chip, main path id
            "fnco_dac": [(1, 2), (1, 1), (1, 0)],  # chip, link no
            "lo_dac": 4,  # local oscillator id
            mixer_tag: 4,  # mixer channel
            usb_lsb_tag: lsb_val,  # mixder sideband (initial value)
            gpiosw_tag: 0x0080,  # switch mask bit(s)
        },
        {
            _name_tag: "control_8",
            _type_tag: _control_val,
            "ch_dac": [0, 3, 4],  # awg id
            "cnco_dac": (1, 1),  # chip, main path id
            "fnco_dac": [(1, 5), (1, 4), (1, 3)],  # chip, link no
            "lo_dac": 5,  # local oscillator id
            mixer_tag: 5,  # mixer channel
            usb_lsb_tag: lsb_val,  # mixder sideband (initial value)
            gpiosw_tag: 0x0100,  # switch mask bit(s)
        },
        {
            _name_tag: "control_b",
            _type_tag: _control_val,
            "ch_dac": [1],  # awg id
            "cnco_dac": (1, 2),  # chip, main path id
            "fnco_dac": [(1, 6)],  # chip, link no
            "lo_dac": 6,  # local oscillator id
            mixer_tag: 6,  # mixer channel
            usb_lsb_tag: lsb_val,  # mixder sideband (initial value)
            gpiosw_tag: 0x0800,  # switch mask bit(s)
        },
        {
            _name_tag: "control_d",
            _type_tag: _control_val,
            "ch_dac": [2],  # awg id
            "cnco_dac": (1, 3),  # chip, main path id
            "fnco_dac": [(1, 7)],  # chip, link no
            "lo_dac": 7,  # local oscillator id
            mixer_tag: 7,  # mixer channel
            usb_lsb_tag: lsb_val,  # mixder sideband (initial value)
            gpiosw_tag: 0x2000,  # switch mask bit(s)
        },
    ]

    readout_control_qube = [
        {
            _name_tag: "readout_01",
            _type_tag: _readout_val,
            "ch_dac": [15],  # awg id
            "ch_adc": 1,  # module id
            "cnco_dac": (0, 0),  # chip, main path
            "cnco_adc": (0, 3),  # chip, main path
            "fnco_dac": [(0, 0)],  # chip, link id
            "lo_dac": 0,  # local oscillator id
            mixer_tag: 0,  # mixer channel
            usb_lsb_tag: usb_val,  # mixder sideband (initial value)
            gpiosw_tag: 0x0003,  # switch mask bit(s)
        },
        {
            _name_tag: "pump_2",
            _type_tag: _control_val,
            "ch_dac": [14],  # awg id
            "cnco_dac": (0, 1),  # chip, main path id
            "fnco_dac": [(0, 1)],  # chip, link no
            "lo_dac": 1,  # local oscillator id
            mixer_tag: 1,  # mixer channel
            usb_lsb_tag: usb_val,  # mixder sideband (initial value)
            gpiosw_tag: 0x0004,  # switch mask bit(s)
        },
        {
            _name_tag: "control_5",
            _type_tag: _control_val,
            "ch_dac": [11, 12, 13],  # awg id
            "cnco_dac": (0, 2),  # chip, main path id
            "fnco_dac": [(0, 4), (0, 3), (0, 2)],  # chip, link no
            "lo_dac": 2,  # local oscillator id
            mixer_tag: 2,  # mixer channel
            usb_lsb_tag: lsb_val,  # mixder sideband (initial value)
            gpiosw_tag: 0x0020,  # switch mask bit(s)
        },
        {
            _name_tag: "control_6",
            _type_tag: _control_val,
            "ch_dac": [8, 9, 10],  # awg id
            "cnco_dac": (0, 3),  # chip, main path id
            "fnco_dac": [(0, 5), (0, 6), (0, 7)],  # chip, link no
            "lo_dac": 3,  # local oscillator id
            mixer_tag: 3,  # mixer channel
            usb_lsb_tag: lsb_val,  # mixder sideband (initial value)
            gpiosw_tag: 0x0040,  # switch mask bit(s)
        },
        {
            _name_tag: "control_7",
            _type_tag: _control_val,
            "ch_dac": [5, 6, 7],  # awg id
            "cnco_dac": (1, 0),  # chip, main path id
            "fnco_dac": [(1, 2), (1, 1), (1, 0)],  # chip, link no
            "lo_dac": 4,  # local oscillator id
            mixer_tag: 4,  # mixer channel
            usb_lsb_tag: lsb_val,  # mixder sideband (initial value)
            gpiosw_tag: 0x0080,  # switch mask bit(s)
        },
        {
            _name_tag: "control_8",
            _type_tag: _control_val,
            "ch_dac": [0, 3, 4],  # awg id
            "cnco_dac": (1, 1),  # chip, main path id
            "fnco_dac": [(1, 5), (1, 4), (1, 3)],  # chip, link no
            "lo_dac": 5,  # local oscillator id
            mixer_tag: 5,  # mixer channel
            usb_lsb_tag: lsb_val,  # mixder sideband (initial value)
            gpiosw_tag: 0x0100,  # switch mask bit(s)
        },
        {
            _name_tag: "pump_b",
            _type_tag: _control_val,
            "ch_dac": [1],  # awg id
            "cnco_dac": (1, 2),  # chip, main path id
            "fnco_dac": [(1, 6)],  # chip, link no
            "lo_dac": 6,  # local oscillator id
            mixer_tag: 6,  # mixer channel
            usb_lsb_tag: usb_val,  # mixder sideband (initial value)
            gpiosw_tag: 0x0800,  # switch mask bit(s)
        },
        {
            _name_tag: "readout_cd",
            _type_tag: _readout_val,
            "ch_dac": [2],  # awg id
            "ch_adc": 0,  # module id
            "cnco_dac": (1, 3),  # chip, main path
            "cnco_adc": (1, 3),  # chip, main path
            "fnco_dac": [(1, 7)],  # chip, link no
            "lo_dac": 7,  # local oscillator id
            mixer_tag: 7,  # mixer channel
            usb_lsb_tag: usb_val,  # mixder sideband (initial value)
            gpiosw_tag: 0x3000,  # switch mask bit(s)
        },
    ]

    servers = {
        "qube001": {
            QSConstants.SRV_IPFPGA_TAG: "10.1.0.19",
            QSConstants.SRV_IPLSI_TAG: "10.5.0.19",
            QSConstants.SRV_IPCLK_TAG: "10.2.0.19",
            QSConstants.SRV_QUBETY_TAG: "A",
            QSConstants.SRV_CHANNEL_TAG: readout_control_qube,
        },
        "qube002": {
            QSConstants.SRV_IPFPGA_TAG: "10.1.0.6",
            QSConstants.SRV_IPLSI_TAG: "10.5.0.6",
            QSConstants.SRV_IPCLK_TAG: "10.2.0.6",
            QSConstants.SRV_QUBETY_TAG: "A",
            QSConstants.SRV_CHANNEL_TAG: readout_control_qube,
        },
        "qube003": {
            QSConstants.SRV_IPFPGA_TAG: "10.1.0.21",
            QSConstants.SRV_IPLSI_TAG: "10.5.0.21",
            QSConstants.SRV_IPCLK_TAG: "10.2.0.21",
            QSConstants.SRV_QUBETY_TAG: "B",
            QSConstants.SRV_CHANNEL_TAG: control_qube_500_1500,
        },
        "qube004": {
            QSConstants.SRV_IPFPGA_TAG: "10.1.0.22",
            QSConstants.SRV_IPLSI_TAG: "10.5.0.22",
            QSConstants.SRV_IPCLK_TAG: "10.2.0.22",
            QSConstants.SRV_QUBETY_TAG: "A",
            QSConstants.SRV_CHANNEL_TAG: readout_control_qube,
        },
        "qube005": {
            QSConstants.SRV_IPFPGA_TAG: "10.1.0.23",
            QSConstants.SRV_IPLSI_TAG: "10.5.0.23",
            QSConstants.SRV_IPCLK_TAG: "10.2.0.23",
            QSConstants.SRV_QUBETY_TAG: "A",
            QSConstants.SRV_CHANNEL_TAG: readout_control_qube,
        },
        "qube006": {
            QSConstants.SRV_IPFPGA_TAG: "10.1.0.24",
            QSConstants.SRV_IPLSI_TAG: "10.5.0.24",
            QSConstants.SRV_IPCLK_TAG: "10.2.0.24",
            QSConstants.SRV_QUBETY_TAG: "B",
            QSConstants.SRV_CHANNEL_TAG: control_qube_500_1500,
        },
        "qube007": {
            QSConstants.SRV_IPFPGA_TAG: "10.1.0.1",
            QSConstants.SRV_IPLSI_TAG: "10.5.0.1",
            QSConstants.SRV_IPCLK_TAG: "10.2.0.1",
            QSConstants.SRV_QUBETY_TAG: "A",
            QSConstants.SRV_CHANNEL_TAG: readout_control_qube,
        },
        "qube008": {
            QSConstants.SRV_IPFPGA_TAG: "10.1.0.9",
            QSConstants.SRV_IPLSI_TAG: "10.5.0.9",
            QSConstants.SRV_IPCLK_TAG: "10.2.0.9",
            QSConstants.SRV_QUBETY_TAG: "A",
            QSConstants.SRV_CHANNEL_TAG: readout_control_qube,
        },
        "qube009": {
            QSConstants.SRV_IPFPGA_TAG: "10.1.0.27",
            QSConstants.SRV_IPLSI_TAG: "10.5.0.27",
            QSConstants.SRV_IPCLK_TAG: "10.2.0.27",
            QSConstants.SRV_QUBETY_TAG: "B",
            QSConstants.SRV_CHANNEL_TAG: control_qube_500_1500,
        },
        "qube010": {
            QSConstants.SRV_IPFPGA_TAG: "10.1.0.15",
            QSConstants.SRV_IPLSI_TAG: "10.5.0.15",
            QSConstants.SRV_IPCLK_TAG: "10.2.0.15",
            QSConstants.SRV_QUBETY_TAG: "A",
            QSConstants.SRV_CHANNEL_TAG: readout_control_qube,
        },
        "qube011": {
            QSConstants.SRV_IPFPGA_TAG: "10.1.0.29",
            QSConstants.SRV_IPLSI_TAG: "10.5.0.29",
            QSConstants.SRV_IPCLK_TAG: "10.2.0.29",
            QSConstants.SRV_QUBETY_TAG: "A",
            QSConstants.SRV_CHANNEL_TAG: readout_control_qube,
        },
        "qube012": {
            QSConstants.SRV_IPFPGA_TAG: "10.1.0.30",
            QSConstants.SRV_IPLSI_TAG: "10.5.0.30",
            QSConstants.SRV_IPCLK_TAG: "10.2.0.30",
            QSConstants.SRV_QUBETY_TAG: "B",
            QSConstants.SRV_CHANNEL_TAG: control_qube_500_1500,
        },
    }
    return json.dumps(servers)


def load_config(cxn, config):
    reg = cxn[QSConstants.REGSRV]
    try:
        reg.cd(QSConstants.REGDIR)
        if isinstance(config, str):
            reg.set(QSConstants.REGLNK, config)
        else:
            raise TypeError(config)
    except Exception as e:
        print(sys._getframe().f_code.co_name, e)


def load_skew_zero(cxn):
    reg = cxn[QSConstants.REGSRV]
    zero = 0  # The zero skew time difference.
    try:  # We specify the skew value with
        reg.cd(QSConstants.REGDIR)  # the number of clocks (8 ns).
        config = json.loads(reg.get(QSConstants.REGLNK))
        chassis = config.keys()
        skew = {}
        for chassis_name in chassis:
            skew.update({chassis_name: zero})
        reg.set(QSConstants.REGSKEW, json.dumps(skew))
    except Exception as e:
        print(sys._getframe().f_code.co_name, e)
