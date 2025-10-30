import os
import random
from collections.abc import Iterator
from unittest.mock import MagicMock

import matplotlib.pyplot as plt
import numpy as np
import pytest
from dotenv import load_dotenv
from labrad import types as T
from labrad.devices import DeviceLockedError
from labrad.units import ns
from labrad.util import ContextDict
from quel_ic_config import force_unlock_all_boxes

from qube_server.constants import QSConstants
from qube_server.model import PossibleLinks, Skews
from qube_server.server import QuBE_Server

load_dotenv()

_DO_PLOT: bool = os.getenv("QS_TEST_PLOT") == "True" or False
_ARTIFACT_DIR: str = "artifacts/"

_VARNAME_SKIP_TESTS_WITH_DEVICES = "QS_SKIP_TESTS_WITH_DEVICES"
_SKIP_TESTS_WITH_DEVICES: bool = (
    os.getenv(_VARNAME_SKIP_TESTS_WITH_DEVICES, default="True") == "True"
)
test_with_devices = pytest.mark.skipif(
    _SKIP_TESTS_WITH_DEVICES,
    reason=f"since the environment variable '{_VARNAME_SKIP_TESTS_WITH_DEVICES}' is True or is not set.",
)


@pytest.fixture(scope="function")
def qube_server_base():
    server = QuBE_Server(
        deskew_conf_filepath=os.environ.get("QS_TEST_DESKEW_CONF_PATH", default=None)
    )
    server.log = MagicMock()
    with open(os.environ["QS_TEST_LINKS_JSON_PATH"]) as fp:
        links = PossibleLinks.model_validate_json(fp.read())
    with open(os.environ["QS_TEST_SKEW_JSON_PATH"]) as fp:
        skew = Skews.model_validate_json(fp.read())
    server._get_possible_links = lambda: links  # type: ignore
    server._get_skew_config = lambda: skew  # type: ignore
    server.initServer()
    yield server
    del server
    force_unlock_all_boxes()


@pytest.fixture
def qube_server(qube_server_base, context):
    qube_server_base.initContext(context)
    yield qube_server_base


@pytest.fixture
def context() -> ContextDict:
    context = ContextDict()
    context.ID = 9999  # type: ignore
    return context


@pytest.fixture
def another_context() -> ContextDict:
    context = ContextDict()
    context.ID = 8888  # type: ignore
    return context


def _find_readout_portname(qube_server) -> Iterator[str]:
    _, names = qube_server.deviceLists()
    for name in names:
        if "readin" in name and "readout" in name:
            yield name


def _find_control_portname(qube_server) -> Iterator[str]:
    _, names = qube_server.deviceLists()
    for name in names:
        if "control" in name:
            yield name


@test_with_devices
def test_readin_readout_with_decimation(qube_server: QuBE_Server, context):
    readout_portname = next(_find_readout_portname(qube_server))

    sequence_length = 10.24  # in us. length must be a multiple of 10240ns.
    n_sample = int(sequence_length * QSConstants.DACBB_SAMPLE_R + 0.5)

    qube_server.select_device(context, key=readout_portname)  # type: ignore
    qube_server.number_of_shots(context, 1)
    qube_server.sequence_length(context, T.Value(sequence_length, "us"))
    qube_server.repetition_time(context, T.Value(2 * sequence_length, "us"))

    freq_mhz = random.uniform(-100, 100)
    amp = 0.999
    wavedata = amp * np.exp(
        2j * np.pi * freq_mhz * np.arange(n_sample) / QSConstants.DACBB_SAMPLE_R
    )
    channel = 0
    qube_server.upload_waveform(context, [wavedata], channel)
    qube_server.upload_parameters(context, [channel])
    qube_server.internal_loopback(context, True)

    qube_server.frequency_local(context, T.Value(8500, "MHz"))  # 8.5+1.5=10.0GHz
    qube_server.frequency_tx_nco(context, T.Value(1500.0, "MHz"))  # 1.5GHz.
    qube_server.coarse_rx_nco_frequency(
        context, T.Value(1500, "MHz")
    )  # better to be the same as tx_nco
    qube_server.frequency_tx_fine_nco(
        context, channel, T.Value(0, "MHz")
    )  # better not to use it.

    mux_channel = 0
    readout_window = [
        (
            8 * 128 * ns,
            (8 + 16) * 128 * ns,
        )
    ]
    qube_server.acquisition_window(context, mux_channel, readout_window)
    qube_server.acquisition_mode(context, mux_channel, "2")

    qube_server.upload_readout_parameters(context, [mux_channel])

    qube_server.daq_start(context)
    qube_server.daq_trigger(context)
    qube_server.daq_stop(context)

    downloaded_waveform = qube_server.download_waveform(context, [mux_channel])
    observed = downloaded_waveform[0] / np.linalg.norm(
        downloaded_waveform[0]
    )  # normalize
    wavedata_trimmed = wavedata[::4][: len(observed)]  # decimated by 4 in mode-2
    wavedata_normalized = wavedata_trimmed / np.linalg.norm(wavedata_trimmed)
    multipled = np.conjugate(observed) * wavedata_normalized

    if _DO_PLOT:
        plt.figure()
        plt.plot(observed.real)
        plt.plot(observed.imag)
        plt.savefig(_ARTIFACT_DIR + "test_readin_readout_observed.png")
        plt.figure()
        plt.plot(multipled.real)
        plt.plot(multipled.imag)
        plt.savefig(_ARTIFACT_DIR + "test_readin_readout_multiplied.png")

    correlation = np.sum(multipled)
    assert np.abs(correlation) > 0.94


@test_with_devices
def test_lock_device(qube_server: QuBE_Server, context, another_context):
    portname_iter = _find_control_portname(qube_server)
    port1 = next(portname_iter)
    port2 = next(portname_iter)

    qube_server.select_device(context, port1)  # type: ignore
    qube_server.lock_device(context, timeout=None)

    # without error
    qube_server.select_device(context, port1)  # type: ignore

    # raises DeviceLockedError when accessed with another context
    with pytest.raises(DeviceLockedError):
        qube_server.select_device(another_context, port1)  # type: ignore

    # selecting to another device with another context does not raise the error.
    qube_server.select_device(another_context, port2)  # type: ignore

    # release lock for the device
    qube_server.select_device(context, port1)  # type: ignore
    qube_server.release_device(context)

    # port1 is selectable from another context now
    qube_server.select_device(another_context, port1)  # type: ignore


@test_with_devices
def test_reconnect_box(qube_server: QuBE_Server, context):
    box_names = qube_server.list_boxes(context)
    qube_server.reconnect_box(context, box_names[0])


@test_with_devices
def test_relinkup_box(qube_server: QuBE_Server, context):
    box_names = qube_server.list_boxes(context)
    qube_server.reconnect_box(context, box_names[0], linkup=True)


@test_with_devices
def test_reload_deskew_conf(qube_server: QuBE_Server, context):
    qube_server.load_deskew_conf(context)
