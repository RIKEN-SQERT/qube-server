import copy
import random
from unittest.mock import MagicMock, Mock

import pytest
from labrad import types as T
from labrad.devices import DeviceWrapper
from labrad.util import ContextDict

from qube_server.constants import QSConstants
from qube_server.server import QuBE_Server


@pytest.fixture
def fake_devices() -> list[DeviceWrapper]:
    dev0 = DeviceWrapper(guid=0, name="device_00")
    dev0.chassis_name = "TestA"  # type: ignore
    dev1 = DeviceWrapper(guid=1, name="device_01")
    dev1.chassis_name = "TestA"  # type: ignore
    dev2 = DeviceWrapper(guid=2, name="device_02")
    dev2.chassis_name = "TestB"  # type: ignore
    return [dev0, dev1, dev2]


def _get_dummy_registry():
    mock = Mock()
    mock.get.side_effect = [  # depends on implementation of .initServer()
        "",
        "{}",  # possibleLinks
        "{}",  # chassisSkew
    ]
    return mock


@pytest.fixture
def qube_server_with_fake_devices(qube_server, fake_devices) -> QuBE_Server:
    for dev in fake_devices:
        qube_server.devices[dev.guid, dev.name] = dev
    return qube_server


@pytest.fixture
def qube_server() -> QuBE_Server:
    server = QuBE_Server()
    server.log = MagicMock()
    server._get_registry_service = _get_dummy_registry
    server.initServer()
    return server


@pytest.fixture
def context() -> ContextDict:
    context = ContextDict()
    context.ID = 9999  # type: ignore
    return context


def test_number_of_shots(fake_devices, qube_server_with_fake_devices: QuBE_Server, context):
    expected_list = []
    # setter
    for dev in fake_devices:
        num_shots_to_set = random.randrange(1, 2000)
        expected_list.append(num_shots_to_set)
        qube_server_with_fake_devices.select_device(context, dev.name)
        qube_server_with_fake_devices.number_of_shots(context, num_shots_to_set)

    # getter
    for dev, expected_shots in zip(fake_devices, expected_list):
        qube_server_with_fake_devices.select_device(context, dev.name)
        actual_shots = qube_server_with_fake_devices.number_of_shots(context)

        assert actual_shots == expected_shots


def test_repetition_time(fake_devices, qube_server_with_fake_devices: QuBE_Server, context):
    expected_list = []
    # setter
    for dev in fake_devices:
        reptime_ns = float(random.randrange(10240, 100000, 10240))
        expected_list.append(reptime_ns)
        reptime_val = T.Value(reptime_ns, "ns")
        qube_server_with_fake_devices.select_device(context, dev.name)

        dev.static_check_repetition_time = Mock()
        dev.static_check_repetition_time.return_value = True
        qube_server_with_fake_devices.repetition_time(context, reptime_val)
        # Test invalid setting
        dev.static_check_repetition_time.return_value = False
        with pytest.raises(ValueError):
            qube_server_with_fake_devices.repetition_time(context, reptime_val)

    # getter
    for dev, expected_reptime in zip(fake_devices, expected_list):
        qube_server_with_fake_devices.select_device(context, dev.name)
        actual = qube_server_with_fake_devices.repetition_time(context)
        assert actual["ns"] == expected_reptime


def test_sequence_length(fake_devices, qube_server_with_fake_devices: QuBE_Server, context):
    expected_list = []
    # setter
    for dev in fake_devices:
        length_ns = float(random.randrange(1280, 200000, 128))
        expected_list.append(length_ns)
        length_val = T.Value(length_ns, "ns")
        qube_server_with_fake_devices.select_device(context, dev.name)

        dev.static_check_sequence_length = Mock()
        dev.static_check_sequence_length.return_value = True
        qube_server_with_fake_devices.sequence_length(context, length_val)
        # Test invalid setting
        dev.static_check_sequence_length.return_value = False
        with pytest.raises(ValueError):
            qube_server_with_fake_devices.sequence_length(context, length_val)

    # getter
    for dev, expected_length in zip(fake_devices, expected_list):
        qube_server_with_fake_devices.select_device(context, dev.name)
        actual = qube_server_with_fake_devices.sequence_length(context)
        assert actual["ns"] == expected_length


def test_daq_start(fake_devices, qube_server_with_fake_devices: QuBE_Server, context):
    server = qube_server_with_fake_devices
    for d in fake_devices:
        d.set_trigger_board = MagicMock()
    dev_ro, dev_ri, _ = fake_devices

    context[QSConstants.DAC_CNXT_TAG] = {
        dev_ro.chassis_name: (dev_ro, {2, 3, 4}),
    }

    context[QSConstants.ACQ_CNXT_TAG] = {
        dev_ri.chassis_name: [
            (dev_ri, 1, [0, 1]),
        ],
    }

    dev_ro.device_role = QSConstants.CNL_READ_VAL
    server.select_device(context, dev_ro.name)
    server.daq_start(context)

    dev_ri.set_trigger_board.assert_called_once_with(2, [0, 1])


def test_daq_trigger(qube_server_with_fake_devices: QuBE_Server, context, fake_devices):
    server = qube_server_with_fake_devices
    dev_A, _, dev_B = fake_devices

    _mock_seq_client_A = MagicMock()
    _mock_seq_client_A.read_clock.return_value = (True, 10000)
    _mock_seq_client_B = MagicMock()
    server._sync_ctrl = {"TestA": _mock_seq_client_A, "TestB": _mock_seq_client_B}
    server.chassisSkew = {"TestA": 10, "TestB": 20}
    context[QSConstants.DAQ_SDLY_TAG] = 2  # sec

    context[QSConstants.DAC_CNXT_TAG] = {
        "TestA": (dev_A, {0, 2, 3}),
        "TestB": (dev_B, {1, 2}),
    }

    result = server.daq_trigger(context)
    assert result is True

    expected_common_clock = (10000 + int(2 * QSConstants.SYNC_CLOCK + 0.5)) & 0xFFFFFFFFF0
    _mock_seq_client_A.add_sequencer.assert_called_once_with(expected_common_clock + 10, 0b1101)
    _mock_seq_client_B.add_sequencer.assert_called_once_with(expected_common_clock + 20, 0b0110)


def test_daq_stop(qube_server_with_fake_devices: QuBE_Server):
    qube_server_with_fake_devices.daq_stop  # confirms the method exists


def test_daq_terminate(qube_server_with_fake_devices: QuBE_Server, context, fake_devices):
    server = qube_server_with_fake_devices
    dev_dac, dev_acq, _ = fake_devices
    dev_dac.terminate_daq = Mock()
    dev_acq.terminate_acquisition = Mock()

    context[QSConstants.DAC_CNXT_TAG] = {
        dev_dac.chassis_name: (dev_dac, {2, 3, 4}),
    }
    context[QSConstants.ACQ_CNXT_TAG] = {
        dev_acq.chassis_name: [
            (dev_acq, 1, [0, 1]),
        ],
    }

    server.daq_terminate(context)
    dev_dac.terminate_daq.assert_called_once_with([2, 3, 4])
    dev_acq.terminate_acquisition.assert_called_once_with([0, 1])


def test_timeout(qube_server: QuBE_Server, context):
    another_context = copy.copy(context)

    val1 = T.Value(random.randrange(0, 10), "s")
    val2 = T.Value(random.randrange(0, 10), "s")
    # setter
    qube_server.daq_timeout(context, val1)
    qube_server.daq_timeout(another_context, val2)
    # getter
    actual = qube_server.daq_timeout(context)
    assert actual == val1
    actual = qube_server.daq_timeout(another_context)
    assert actual == val2


def test_daq_channel(qube_server_with_fake_devices: QuBE_Server, fake_devices, context):
    server = qube_server_with_fake_devices
    dev1, dev2, _ = fake_devices
    dev1.number_of_awgs = 4
    dev2.number_of_awgs = 5
    server.select_device(context, dev1.name)
    assert server.daq_channels(context) == 4
    server.select_device(context, dev2.name)
    assert server.daq_channels(context) == 5


def test_upload_parameter(qube_server_with_fake_devices: QuBE_Server, fake_devices, context): ...


def test_upload_readout_parameter(qube_server_with_fake_devices: QuBE_Server, fake_devices, context): ...


def test_upload_waveform(qube_server_with_fake_devices: QuBE_Server, fake_devices, context):
    server = qube_server_with_fake_devices
    dev1, _, _ = fake_devices

    channels = [0, 1]
    wavedata = [[1.0, 1.0j], [-1.0, -1.0]]

    dev1.check_awg_channels = Mock(return_value=True)
    dev1.check_waveform = Mock(return_value=(True, len(channels), len(wavedata[0])))
    dev1.upload_waveform = Mock()

    server.select_device(context, dev1.name)
    server.upload_waveform(context, wavedata, channels)

    dev1.upload_waveform.assert_called_once()


def test_download_waveform(qube_server_with_fake_devices: QuBE_Server, fake_devices, context):
    server = qube_server_with_fake_devices
    dev1, _, _ = fake_devices
    dev1.device_role = QSConstants.CNL_READ_VAL

    muxchs = [0, 1]

    dev1.static_check_mux_channel_range = Mock(return_value=True)
    dev1.download_waveform = Mock()

    server.select_device(context, dev1.name)
    server.download_waveform(context, muxchs)

    dev1.download_waveform.assert_called_once()


def test_frequency_local(qube_server_with_fake_devices, fake_devices, context):
    server = qube_server_with_fake_devices
    dev = fake_devices[0]
    dev.static_check_lo_frequency = MagicMock()
    dev.set_lo_frequency = MagicMock()
    dev.get_lo_frequency = MagicMock()
    server.select_device(context, dev.name)

    # setter
    freq_val = T.Value(10000, "MHz")
    dev.static_check_lo_frequency.return_value = True
    server.frequency_local(context, freq_val)
    dev.set_lo_frequency.assert_called_once_with(10000)

    # getter
    dev.get_lo_frequency.return_value = 11000
    actual = server.frequency_local(context)
    assert actual["MHz"] == 11000


def test_frequency_tx_nco(qube_server_with_fake_devices, fake_devices, context):
    server = qube_server_with_fake_devices
    dev = fake_devices[0]
    dev.set_dac_coarse_frequency = MagicMock()
    dev.get_dac_coarse_frequency = MagicMock()
    server.select_device(context, dev.name)

    # setter
    freq_val = T.Value(500, "MHz")
    server.frequency_tx_nco(context, freq_val)
    dev.set_dac_coarse_frequency.assert_called_once_with(500)

    # getter
    dev.get_dac_coarse_frequency.return_value = -500
    actual = server.frequency_tx_nco(context)
    assert actual["MHz"] == -500


def test_frequency_tx_fine_nco(qube_server_with_fake_devices, fake_devices, context):
    server = qube_server_with_fake_devices
    dev = fake_devices[0]
    dev.check_awg_channels = MagicMock()
    dev.static_check_dac_fine_frequency = MagicMock()
    dev.set_dac_fine_frequency = MagicMock()
    dev.get_dac_fine_frequency = MagicMock()
    server.select_device(context, dev.name)

    channel = 1
    freq_val = T.Value(100.5, "MHz")

    # setter
    dev.check_awg_channels.return_value = True
    dev.static_check_dac_fine_frequency.return_value = True
    server.frequency_tx_fine_nco(context, channel, freq_val)
    dev.set_dac_fine_frequency.assert_called_once_with(channel, 100.5)

    # getter
    dev.get_dac_fine_frequency.return_value = -100.5
    actual = server.frequency_tx_fine_nco(context, channel)
    assert actual["MHz"] == -100.5
    dev.get_dac_fine_frequency.assert_called_with(channel)


def test_coarse_rx_nco_frequency(qube_server_with_fake_devices, fake_devices, context):
    server = qube_server_with_fake_devices
    dev = fake_devices[0]
    dev.set_adc_coarse_frequency = MagicMock()
    dev.get_adc_coarse_frequency = MagicMock()
    server.select_device(context, dev.name)

    # setter
    dev.device_role = QSConstants.CNL_READ_VAL
    freq_val = T.Value(200, "MHz")
    server.coarse_rx_nco_frequency(context, freq_val)
    dev.set_adc_coarse_frequency.assert_called_once_with(200)

    # getter
    dev.get_adc_coarse_frequency.return_value = -200
    actual = server.coarse_rx_nco_frequency(context)
    assert actual["MHz"] == -200


def test_sideband_selection(qube_server_with_fake_devices, fake_devices, context):
    server = qube_server_with_fake_devices
    dev = fake_devices[0]
    server.select_device(context, dev.name)

    for sb in ["usb", "lsb"]:
        dev.set_mix_sideband = MagicMock()
        dev.get_mix_sideband = MagicMock()

        # setter
        server.sideband_selection(context, sb)
        dev.set_mix_sideband.assert_called_once_with(sb)

        # getter
        dev.get_mix_sideband.return_value = sb
        actual = server.sideband_selection(context)
        assert actual == sb
