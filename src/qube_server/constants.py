import numpy as np

############################################################
#
# CONSTANTS
#
class QSConstants:
    REGDIR = ["", "Servers", "QuBE"]
    REGSRV = "registry"
    REGLNK = "possible_links"
    REGSKEW = "chassis_skew"
    REGMASTERLNK = "master_link"
    #REGAPIPATH = "adi_api_path"
    SRVNAME = "QuBE Server"
    MNRNAME = "QuBE Manager"
    ENV_SRVSEL = "QUBE_SERVER"
    THREAD_MAX_WORKERS = 32
    #DAQ_MAXLEN = 199936  # nano-seconds -> 24,992 AWG Word
    DAQ_MAXLEN = 134_217_728 # nano-seconds -> 2^26
    #DAC_SAMPLE_R = 12000  # MHz
    NCO_SAMPLE_F = 2000  # MHz, NCO frequency at main data path
    #ADC_SAMPLE_R = 6000  # MHz
    DACBB_SAMPLE_R = 500  # MHz, baseband sampling frequency
    ADCBB_SAMPLE_R = 500  # MHz, baseband sampling frequency
    ADCDCM_SAMPLE_R = 125  # MHz, decimated sampling frequency
    #   Note: This has been changed from
    #         62.5 MHz in May 2022.
    DAC_BITS = 16  # bits
    DAC_BITS_POW_HALF = 2**15  # 2^(DAC_BITS-1)
    DAC_WVSAMP_IVL = 2  # ns; Sampling intervals of waveforms
    #    = 1/DACBB_SAMPLE_R
    ADC_BBSAMP_IVL = 2  # ns; Sampling intervals of readout waveform
    #    = 1/ADCBB_SAMPLE_R
    DAC_WORD_IVL = 8  # ns; DAC WORD in nanoseconds
    DAC_WORD_SAMPLE = 4  # Sample/(DAC word); DEBUG not used
    #DAQ_CNCO_BITS = 48
    DAQ_LO_RESOL = 100  # - The minimum frequency resolution of
    #   the analog local oscillators in MHz.
    DAC_CNCO_RESOL = 12000 / 2**11  # - The frequency resolution of the
    #   coarse NCOs in upconversion paths.
    #   unit in MHz; DAC_SAMPLE_R/2**11
    DAC_FNCO_RESOL = 2000 / 2**10  # - The frequency resolution of the fine
    #   NCOs in digital upconversion paths.
    #   unit in MHz; DAC_SAMPLE_R/M=6/2**10
    ADC_CNCO_RESOL = 6000 / 2**10  # - The frequency resolution of coarse
    #   NCOs in demodulation path
    #   unit in MHz; ADC_SAMPLE_R/2**10
    ADC_FNCO_RESOL = 1000 / 2**9  # - The frequency resolution of fine
    #   NCOs in demodulation path.
    #   unit in MHz; ADC_SAMPLE_R/M=6/2**9
    DAQ_REPT_RESOL = 10240  # - The mininum time resolution of a
    #   repetition time in nanoseconds.
    DAQ_SEQL_RESOL = 128  # - The mininum time resolution of a
    #   sequence length in nanoseconds.
    ACQ_MULP = 4  # - 4 channel per mux
    ACQ_MAXWINDOW = 2048  # - The maximum duration of a measure-
    #   ment window in nano-seconds.
    ACQ_MAX_FCOEF = 16  # - The maximum number of the FIR filter
    #   taps prior to decimation process.
    ACQ_FCOEF_BITS = 16  # - The number of vertical bits of the
    #   FIR filter coefficient.
    ACQ_FCBIT_POW_HALF = 2**15  # - equivalent to 2^(ACQ_FCOEF_BITS-1).
    ACQ_MAX_WCOEF = 256  # - The maximally applicable complex
    #   window coefficients. It is equiva-
    #   lent to ACQ_MAXWINDOW * ADCDCM_
    #   SAMPLE_R.
    ACQ_WCOEF_BITS = 31  # - The number of vertical bits of the
    #   complex window coefficients.
    ACQ_WCBIT_POW_HALF = 2**30  # - equivalent to 2^(ACQ_WCOEF_BITS-1)
    ACQ_MAXNUMCAPT = 8  # - Maximum iteration number of acquisi-
    #   tion window in a single sequence.
    #   DEBUG: There is no obvious reason to
    #   set the number. We'd better to
    #   change the number later.
    ACQ_CAPW_RESOL = 8  # - The capture word in nano-seconds
    #   prior to the decimation. It is equi-
    #   valent to 4 * ADC_BBSAMP_IVL.
    ACQ_CAST_RESOL = 128  # - The minimum time resolution of start
    #   delay in nano-seconds. The first
    #   capture window must start from the
    #   multiple of 128 ns to maintain the
    #   the phase coherence.
    ACQ_MODENUMBER = ["1", "2", "3", "A", "B"]
    ACQ_MODEFUNC = {
        "1": (False, False, False),  # ACQ_MODEFUNC
        "2": (True, False, False),  # - The values in the dictionary are
        "3": (True, True, False),  # tuples of enable/disable booleans of
        "A": (True, False, True),  # functions: decimation, averaging,
        "B": (True, True, True),
    }  # and summation.

    DAQ_INITLEN = 8192  # nano-seconds -> 1,024 AWG Word
    DAQ_INITREPTIME = 30720  # nano-seconds -> 3,840 AWG Word
    DAQ_INITSHOTS = 1  # one shot
    DAQ_INITTOUT = 5  # seconds
    DAQ_INITSDLY = 1  # seconds; synchronization delay
    ACQ_INITMODE = "3"
    ACQ_INITWINDOW = [(0, 2048)]  # initial demodulation windows
    ACQ_INITFIRCOEF = np.array([1] * 8).astype(
        complex
    )  # initial complex FIR filter coeffs
    ACQ_INITWINDCOEF = np.array([]).astype(complex)  # initial complex window coeffs
    SYNC_CLOCK = 125 * 1000 * 1000  # synchronization clock
    DAC_CNXT_TAG = "awgs"  # used in the device context
    ACQ_CNXT_TAG = "muxs"  # used in the device context
    SYN_CNXT_TAG = "synch"  # used in the device context
    DAQ_TOUT_TAG = "timeout"  # used in the device context
    DAQ_SDLY_TAG = "sync_delay"  # used in the device context; synchronization delay
    SRV_IPLSI_TAG = "ip_lsi"  # refered in the json config
    SRV_IPFPGA_TAG = "ip_fpga"  # refered in the json config
    SRV_IPCLK_TAG = "ip_sync"  # refered in the json config
    SRV_QUBETY_TAG = "type"  # refered in the json config; either
    # 'A' or 'B' is allowed for the value
    SRV_CHANNEL_TAG = "channels"  # refered in the json config
    CNL_NAME_TAG = "name"  # used in the json config. channel(CNL) name.
    CNL_TYPE_TAG = "type"  # used in the json config. channel(CNL) type.
    # either value is to be specified:
    CNL_CTRL_VAL = "control"  # + the channel is for control
    CNL_READ_VAL = "mux"  # + the channel is for readout
    CNL_MIXCH_TAG = "mixer_ch"  # used in the json config. mixer channel(CNL).
    CNL_MIXSB_TAG = "mixer_sb"  # used in the json config. mixer channel(CNL)
    # side-band selection. Either value can be set
    CNL_MXUSB_VAL = "usb"  # + upper sideband
    CNL_MXLSB_VAL = "lsb"  # + lower sideband
    CNL_GPIOSW_TAG = "gpio_mask"  # used in the json config for gpio-controlled
    # microwave switches. '1'=0xb1 deactivates
    # channel or makes it loopback.

    def __init__(self):
        pass


class QSMessage:
    CONNECTING_CHANNEL = "connecting to {}"
    CHECKING_QUBEUNIT = "Checking {} ..."
    CNCTABLE_QUBEUNIT = "Link possible: {}"
    CONNECTED_CHANNEL = "Link : {}"

    ERR_HOST_NOTFOUND = "QuBE {} not found (ping unreachable). "
    ERR_DEV_NOT_OPEN = "Device is not open"
    ERR_MAST_NOT_OPEN = "Master FPGA is not ready"
    ERR_FREQ_SETTING = "{} accepts a frequency multiple of {} MHz. "
    ERR_REP_SETTING = "{} accepts a multiple of {} ns. "
    ERR_INVALID_DEV = "Invalid device. You may have called {} specific API in {}. "
    ERR_INVALID_RANG = "Invalid range. {} must be between {} and {}. "
    ERR_INVALID_ITEM = "Invalid data. {} must be one of {}. "
    ERR_INVALID_WIND = "Invalid window range. "
    ERR_INVALID_WAVD = (
        "Invalid waveform data. "
        + "(1) Inconsistent number of waveforms and channels. "
        + "(2) The number of channels are less than that of # of awgs. "
        + "(3) The sequence length in nano-second must be identical to "
        + "the value set by daq_length(). "
        + "(4) The data length must be multiple of {}. ".format(
            QSConstants.DAQ_SEQL_RESOL // QSConstants.DAC_WVSAMP_IVL
        )
        + "(5) The absolute value of complex data is less than 1. "
        + "The problem is {}. "
    )
    ERR_NOARMED_DAC = "No ready dac channels. "

    def __init__(self):
        pass

