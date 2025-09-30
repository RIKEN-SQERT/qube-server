import copy
import math
import random
from unittest.mock import MagicMock, Mock, create_autospec

import pytest
import pytest_twisted
from labrad import types as T
from labrad.devices import DeviceServer, DeviceWrapper
from labrad.util import ContextDict

from qube_server.box_connection import BoxConnection
from qube_server.constants import QSConstants
from qube_server.devices import DeviceType, QuBE_ControlPort, QuBE_ReadoutPort
from qube_server.server import QuBE_Server


@pytest.fixture
def fake_box_connections() -> list[BoxConnection]:
    box_conn_a = create_autospec(BoxConnection)
    box_conn_a.box_name = "BoxA"
    box_conn_b = create_autospec(BoxConnection)
    box_conn_b.box_name = "BoxB"
    return [box_conn_a, box_conn_b]


@pytest.fixture
def fake_devices(fake_box_connections) -> list[DeviceWrapper]:
    box_conn_a, box_conn_b = fake_box_connections
    dev0 = create_autospec(QuBE_ReadoutPort)
    dev0.box_conn = box_conn_a
    dev0.guid = 0
    dev0.name = "device_00"
    dev0.device_type = DeviceType.readout
    dev1 = create_autospec(QuBE_ControlPort)
    dev1.box_conn = box_conn_a
    dev1.guid = 2
    dev1.name = "device_01"
    dev1.device_type = DeviceType.ctrl
    dev2 = create_autospec(QuBE_ControlPort)
    dev2.box_conn = box_conn_b
    dev2.guid = 4
    dev2.name = "device_02"
    dev2.device_type = DeviceType.ctrl
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
def qube_server():
    server = QuBE_Server()
    server.log = MagicMock()
    server._get_possible_links = lambda: {}  # type: ignore
    server._get_skew_config = lambda: {}  # type: ignore
    DeviceServer.initServer(server)
    return server


@pytest.fixture
def qube_server_with_fake_devices(
    qube_server, fake_devices, fake_box_connections
) -> QuBE_Server:
    qube_server._name_to_box_conn = {bc.box_name: bc for bc in fake_box_connections}
    for dev in fake_devices:
        qube_server.devices[dev.guid, dev.name] = dev
    return qube_server


@pytest.fixture
def context() -> ContextDict:
    context = ContextDict()
    context.ID = 9999  # type: ignore
    return context


def test_number_of_shots(
    fake_devices, qube_server_with_fake_devices: QuBE_Server, context
):
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


def test_repetition_time(
    fake_devices, qube_server_with_fake_devices: QuBE_Server, context
):
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


def test_sequence_length(
    fake_devices, qube_server_with_fake_devices: QuBE_Server, context
):
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


@pytest_twisted.inlineCallbacks
def test_daq_trigger(
    qube_server_with_fake_devices: QuBE_Server,
    fake_box_connections,
    context,
    fake_devices,
):
    server = qube_server_with_fake_devices
    box_conn_a, box_conn_b = fake_box_connections

    server.initContext(context)

    context[QSConstants.DAQ_SDLY_TAG] = 2  # sec

    context[QSConstants.ACQ_CNXT_TAG] = {
        box_conn_a.box_name: {(4, 0)},
        box_conn_b.box_name: {(5, 1)},
    }

    context[QSConstants.DAC_CNXT_TAG] = {
        box_conn_a.box_name: {(0, 0), (2, 0), (3, 2)},
        box_conn_b.box_name: {(1, 0), (2, 1)},
    }
    current_timecounter = int(QSConstants.SYNC_CLOCK * 100.0)
    box_conn_a.last_trigger_timecounter = int(QSConstants.SYNC_CLOCK * 100.5)
    box_conn_a.start_capture_by_awg_trigger.return_value = (None, None)
    box_conn_a.get_current_timecounter.return_value = current_timecounter
    box_conn_b.last_trigger_timecounter = int(QSConstants.SYNC_CLOCK * 40.0)
    box_conn_b.start_capture_by_awg_trigger.return_value = (None, None)
    box_conn_b.get_current_timecounter.return_value = current_timecounter

    server._stable_count_proposer.propose_trigger_counts = MagicMock()
    server._stable_count_proposer.propose_trigger_counts.return_value = {
        box_conn_a.box_name: 100000,
        box_conn_b.box_name: 100001,
    }

    result = yield server.daq_trigger(context)
    assert result is True

    box_conn_a.start_capture_by_awg_trigger.assert_called_once_with(
        context.ID,
        runits={(4, 0)},
        channels={(0, 0), (2, 0), (3, 2)},
        timecounter=100000,
    )
    box_conn_b.start_capture_by_awg_trigger.assert_called_once_with(
        context.ID,
        runits={(5, 1)},
        channels={(1, 0), (2, 1)},
        timecounter=100001,
    )


@pytest_twisted.inlineCallbacks
def test_daq_trigger_raises_error_when_a_box_is_busy(
    qube_server_with_fake_devices: QuBE_Server,
    fake_box_connections,
    context,
    fake_devices,
):
    server = qube_server_with_fake_devices
    box_conn_a, box_conn_b = fake_box_connections

    box_conn_b.is_sequencer_available.return_value = False

    server.initContext(context)

    context[QSConstants.DAQ_SDLY_TAG] = 2  # sec

    context[QSConstants.ACQ_CNXT_TAG] = {
        box_conn_a.box_name: {(4, 0)},
        box_conn_b.box_name: {(5, 1)},
    }

    context[QSConstants.DAC_CNXT_TAG] = {
        box_conn_a.box_name: {(0, 0), (2, 0), (3, 2)},
        box_conn_b.box_name: {(1, 0), (2, 1)},
    }
    current_timecounter = int(QSConstants.SYNC_CLOCK * 100.0)
    box_conn_a.last_trigger_timecounter = int(QSConstants.SYNC_CLOCK * 50.0)
    box_conn_a.start_capture_by_awg_trigger.return_value = (None, None)
    box_conn_a.get_current_timecounter.return_value = current_timecounter
    box_conn_b.last_trigger_timecounter = int(QSConstants.SYNC_CLOCK * 40.0)
    box_conn_b.start_capture_by_awg_trigger.return_value = (None, None)
    box_conn_b.get_current_timecounter.return_value = current_timecounter

    latest_time_counter = 0b1_000000
    box_conn_a.last_trigger_timecounter = 0b010100
    box_conn_a.timecounter_offset = 0b0100
    box_conn_a.start_capture_by_awg_trigger.return_value = (None, None)
    box_conn_a.get_latest_sysref_timecounter.return_value = latest_time_counter
    box_conn_b.last_trigger_timecounter = 0b111100
    box_conn_b.timecounter_offset = 0b1100
    box_conn_b.start_capture_by_awg_trigger.return_value = (None, None)
    box_conn_b.get_latest_sysref_timecounter.return_value = latest_time_counter

    with pytest.raises(RuntimeError):
        yield server.daq_trigger(context)


def test_daq_stop(qube_server_with_fake_devices: QuBE_Server):
    qube_server_with_fake_devices.daq_stop  # confirms the method exists


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
    dev1.channels_of_port = {0, 1, 2}
    dev2.channels_of_port = {0, 1}
    server.select_device(context, dev1.name)
    assert server.daq_channels(context) == 3
    server.select_device(context, dev2.name)
    assert server.daq_channels(context) == 2


def test_upload_parameter(
    qube_server_with_fake_devices: QuBE_Server, fake_devices, context
): ...


def test_upload_readout_parameter(
    qube_server_with_fake_devices: QuBE_Server, fake_devices, context
): ...


def test_upload_waveform(
    qube_server_with_fake_devices: QuBE_Server, fake_devices, context
):
    server = qube_server_with_fake_devices
    dev1, _, _ = fake_devices

    channels = [0, 1]
    wavedata = [[1.0, 1.0j], [-1.0, -1.0]]

    dev1.check_awg_channels = Mock(return_value=True)
    dev1.check_waveform = Mock(return_value=(True, len(channels), len(wavedata[0])))
    dev1.upload_waveform = Mock()
    dev1.channels_of_port = {0, 1, 2}

    server.select_device(context, dev1.name)
    server.upload_waveform(context, wavedata, channels)

    dev1.upload_waveform.assert_called()


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
    dev.set_lo_frequency.assert_called_once_with(10_000_000_000)

    # getter
    dev.get_lo_frequency.return_value = 11_000_000_000
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
    dev.set_dac_coarse_frequency.assert_called_once_with(500_000_000)

    # getter
    dev.get_dac_coarse_frequency.return_value = -500_000_000
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

    dev.channels_of_port = {0, 1, 2}

    # setter
    dev.check_awg_channels.return_value = True
    dev.static_check_dac_fine_frequency.return_value = True
    server.frequency_tx_fine_nco(context, channel, freq_val)
    dev.set_dac_fine_frequency.assert_called_once_with(channel, 100_500_000)

    # getter
    dev.get_dac_fine_frequency.return_value = -100_500_000
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
    dev.set_adc_coarse_frequency.assert_called_once_with(200_000_000)

    # getter
    dev.get_adc_coarse_frequency.return_value = -200_000_000
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


def test_vatt(qube_server_with_fake_devices, fake_devices, context):
    server: QuBE_Server = qube_server_with_fake_devices
    dev = fake_devices[0]
    dev.set_vatt = MagicMock()
    dev.get_vatt = MagicMock()
    server.select_device(context, dev.name)

    # setter
    val = 3071
    server.vatt(context, val)
    dev.set_vatt.assert_called_once_with(3071)

    # getter
    dev.get_vatt.return_value = 3070
    actual = server.vatt(context)
    assert actual == 3070


def test_fullscale_current(qube_server_with_fake_devices, fake_devices, context):
    server: QuBE_Server = qube_server_with_fake_devices
    dev = fake_devices[0]
    dev.set_fullscale_current = MagicMock()
    dev.get_fullscale_current = MagicMock()
    server.select_device(context, dev.name)

    # setter
    val = 3071
    server.fullscale_current(context, val)
    dev.set_fullscale_current.assert_called_once_with(3071)

    # getter
    dev.get_fullscale_current.return_value = 3070
    actual = server.fullscale_current(context)
    assert actual == 3070


def test_device_delay_offset(qube_server_with_fake_devices, fake_devices, context):
    server = qube_server_with_fake_devices
    dev = fake_devices[0]
    dev.delay_offset = MagicMock()
    server.select_device(context, dev.name)

    # setter
    delay_offset_val = T.Value(2, "ns")
    server.device_delay_offset(context, delay_offset_val)
    assert math.isclose(dev.delay_offset, 2000)

    # getter
    dev.delay_offset = -4000
    actual = server.device_delay_offset(context)
    assert actual["ps"] == -4000
