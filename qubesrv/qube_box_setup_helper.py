from pathlib import Path
import json
from pprint import pprint
import logging
from datetime import datetime
import re
import os
from dataclasses import dataclass

from quel_ic_config import Quel1BoxIntrinsic, Quel1BoxType
from quel_clock_master import SequencerClient
    
def convert_str_to_int_int_tuple(s):
    m = re.match(r"\((\d+),\s*(\d+)\)", s)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    else:
        return None

def convert_str_to_int_str_tuple(s):
    m = re.match(r"\((\d+),\s*(\w+)\)", s)
    if m:
        return (int(m.group(1)), m.group(2))
    else:
        return None

def convert_str_to_group_line_tuple(s):
    output_line_tuple = convert_str_to_int_int_tuple(s)
    if output_line_tuple:
        return output_line_tuple
    else:
        input_line_tuple = convert_str_to_int_str_tuple(s)
        return input_line_tuple

class QubeBoxInfo:
    config_directory = Path(__file__).parent.joinpath("config").resolve()
    box_info_filename = "box_info.json"

    def __new__(cls, *args, **kargs):
        if not hasattr(cls, "_instance"):
            cls._instance = super(QubeBoxInfo, cls).__new__(cls)
            with open(cls.config_directory.joinpath(cls.box_info_filename)) as info_file:
                cls.box_info = json.load(info_file)
        return cls._instance
    
    @classmethod
    def _get_subsystem_ipaddr(cls, device_id, ss_ip_identifier, place_holder="*"):
        return cls.box_info[device_id]['ip'].replace(place_holder, f"{ss_ip_identifier}")
    
    @classmethod
    def get_ipaddr_wss(cls, device_id):
        return cls._get_subsystem_ipaddr(device_id, ss_ip_identifier=1)

    @classmethod
    def get_ipaddr_sss(cls, device_id):
        return cls._get_subsystem_ipaddr(device_id, ss_ip_identifier=2) 

    @classmethod
    def get_ipaddr_css(cls, device_id):
        return cls._get_subsystem_ipaddr(device_id, ss_ip_identifier=5) 
    
    @classmethod
    def get_box_type_str(cls, device_id):
        #print(f"cls.box_info[device_id]:{cls.box_info[device_id]}")
        return cls.box_info[device_id]['type']        
    
    @classmethod
    def get_box_type(cls, device_id):
        #print(f"cls.box_info[device_id]:{cls.box_info[device_id]}")
        return Quel1BoxType.fromstr(cls.box_info[device_id]['type'])
    
    @classmethod
    def get_box_id(cls, device_id):
        return cls.box_info[device_id]['id']
    
    @classmethod
    def list_devices(cls):
        return list(cls.box_info.keys())

class QubeBoxGroup:
    config_directory = Path(__file__).parent.joinpath("config").resolve()
    box_group_filename = "box_group.json"

    def __new__(cls, *args, **kargs):
        self = super(QubeBoxGroup, cls).__new__(cls)
        if not hasattr(cls, "box_group_all"):
            with open(cls.config_directory.joinpath(cls.box_group_filename)) as group_file:
                cls.box_group_all = json.load(group_file)
        return self
    
    def __init__(self, group_id):
        self.group_id = group_id
        self.box_group = self.box_group_all[group_id]
    
    def list_devices(self):
        return self.box_group["devices"]
    
    def get_master_ip(self):
        return self.box_group["master_ip"]
    
    @classmethod
    def list_groups(cls):
        return list(cls.box_group_all.keys())

class QubePortMapper:
    config_directory = Path(__file__).parent.joinpath("config").resolve()
    port_mapping_filename = "port_mapping.json"

    def __new__(cls, *args, **kargs):
        self = super(QubePortMapper, cls).__new__(cls)
        if not hasattr(cls, "port_mapping_by_type"):
            with open(cls.config_directory.joinpath(cls.port_mapping_filename)) as port_file:
                cls.port_mapping_by_type = json.load(port_file)
        return self
 
    def __init__(self, box_type):
        self.port_mapping = {convert_str_to_group_line_tuple(k): v for (k, v) in self.port_mapping_by_type[box_type].items()}
        self.reverse_mapping = {info['port']: (group, line) for (group, line), info in self.port_mapping.items()}
    
    def get_port(self, group, line):
        return self.port_mapping[(group, line)]['port']
    
    def get_role(self, group, line):
        return self.port_mapping[(group, line)]['role']
    
    def get_all_keys(self):
        for key in self.port_mapping:
            yield key
    
    def resolve_line(self, port):
        return self.reverse_mapping[port]

@dataclass
class QubeLineConfig:
    vatt: int
    sideband: str

class QubeBoxLineConfig:
    def __init__(self):
        self.config = {}

    def load_line_config(self, group, line, **kwargs):
        self.config[(group, line)] = QubeLineConfig(**kwargs)

    def load_config_by_role(self, config_by_role: dict, pmap: QubePortMapper):
        for group, line in pmap.get_all_keys():
            role = pmap.get_role(group, line)
            if role in config_by_role:
                self.config[(group, line)] = QubeLineConfig(**config_by_role[role])

    def load_config(self, config):
        for group, line in config:
            self.config[(group, line)] = QubeLineConfig(**config[(group, line)])
            
    def get_line_config(self, group, line):
        return self.config[(group, line)]
    
class QubeDefaultConfigFactory:
    config_directory = Path(__file__).parent.joinpath("config").resolve()
    default_config_filename = "default_line_config.json"
    
    def __new__(cls, *args, **kargs):
        if not hasattr(cls, "_instance"):
            cls._instance = super(QubeDefaultConfigFactory, cls).__new__(cls)
            with open(cls.config_directory.joinpath(cls.default_config_filename)) as config_file:
                cls.default_config_by_roll = json.load(config_file)
        return cls._instance
    
    def create_config(self, device_id):
        box_info = QubeBoxInfo()
        box_type = box_info.get_box_type_str(device_id)
        pmap = QubePortMapper(box_type)
        box_line_config = QubeBoxLineConfig()
        box_line_config.load_config_by_role(self.default_config_by_roll[device_id], pmap)
        return box_line_config

class LogWriter:
    def __init__(self, log_filepath, logging_level):
        if log_filepath:
            self.log_filepath = Path(log_filepath)
        else:
            log_filepath = None
        self.logging_level = logging_level
    
    def __enter__(self):
        if self.log_filepath:
            os.makedirs(self.log_filepath.parent, exist_ok=True)
            logger = logging.getLogger()
            logger.setLevel(self.logging_level)
            self.file_handler = logging.FileHandler(self.log_filepath)
            self.file_handler.setLevel(self.logging_level)
            handler_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            self.file_handler.setFormatter(handler_format)
            logger.addHandler(self.file_handler)
        
    def __exit__(self, exc_type, exc_value, traceback):
        if hasattr(self, "file_handler"):
            logger = logging.getLogger()
            logger.removeHandler(self.file_handler)

class QubeBoxSetupHelper:
    log_directory = Path(__file__).parent.joinpath("log").resolve()
    linkup_data_directory = Path(__file__).parent.joinpath("linkup_log").resolve()

    def __init__(self, device_id):
        self.box_info = QubeBoxInfo() 
        self.device_id = device_id
        self.box = self._create_box(device_id)
        self.sequencer_client = self._create_sequencer_client(device_id)
        self.port_mapper = self._create_port_mapping(device_id)
        self.is_linkedup = False

    def _create_box(self, device_id):
        ipaddr_wss = self.box_info.get_ipaddr_wss(device_id)
        ipaddr_sss = self.box_info.get_ipaddr_sss(device_id)
        ipaddr_css = self.box_info.get_ipaddr_css(device_id)
        boxtype = self.box_info.get_box_type(device_id)

        config_root = None
        config_options = []

        box = Quel1BoxIntrinsic.create(
            ipaddr_wss=ipaddr_wss,
            ipaddr_sss=ipaddr_sss,
            ipaddr_css=ipaddr_css,
            boxtype=boxtype,
            config_root=config_root,
            config_options=config_options
        )
        
        return box

    def _create_port_mapping(self, device_id):
        boxtype = self.box_info.get_box_type_str(device_id)
        pmap = QubePortMapper(boxtype)
        return pmap

    def _create_sequencer_client(self, device_id):
        ipaddr_sss = self.box_info.get_ipaddr_sss(device_id)
        sequencer_client = SequencerClient(ipaddr_sss)
        return sequencer_client

    def get_box_info(self):
        return self.box_info

    def get_box(self):
        return self.box

    def get_port_map(self):
        return self.port_mapper

    def get_css(self):
        return self.box.css
    
    def get_wss(self):
        return self.box.wss
    
    def get_sss(self):
        return self.get_sequencer_client()

    def get_linkupper(self):
        return self.box.linkupper
    
    def get_sequencer_client(self):
        return self.sequencer_client
    
    def read_clock(self):
        return self.sequencer_client.read_clock()[1]
    
    def linkup(self, background_noise_threshold = 5000, save_data=True, ignore_access_failure_of_adrf6780=False):
        # init peripherals
        self.box.css.configure_peripherals(ignore_access_failure_of_adrf6780=ignore_access_failure_of_adrf6780)
        self.box.css.configure_all_mxfe_clocks()
    
        # prepare linkup log dir
        if save_data:
            now = datetime.now()
            date_str = now.strftime('%Y%m%d')
            save_dirpath = self.linkup_data_directory.joinpath(date_str, self.device_id)
        else:
            save_dirpath = None

        # linkup
        mxfe_list = [0, 1]
        linkup_ok = [False, False]

        for mxfe in mxfe_list:
            linkup_ok[mxfe] = self.box.linkupper.linkup_and_check(
                mxfe, soft_reset=True, use_204b=True, ignore_crc_error=False, background_noise_threshold=background_noise_threshold, save_dirpath=save_dirpath
            )

        print(f"{self.device_id} linkup result: MxFE0: {linkup_ok[0]}, MxFE1: {linkup_ok[1]}")

        for mxfe in mxfe_list:
            status, err_flag = list(map(hex, self.box.css.get_link_status(mxfe)))
            print(f"{self.device_id} MxFE{mxfe} link status: {status}, crc error: {err_flag}")
        return linkup_ok

    def configure_lines(self, box_line_config):
        for group in self.box.css.get_all_groups():
            for line in self.box.css.get_all_lines_of_group(group):
                line_config = box_line_config.get_line_config(group, line)
                self.box.css.set_sideband(group, line, line_config.sideband)
                self.box.css.set_vatt(group, line, line_config.vatt)

    def initialize(self, box_line_config=None, save_linkup_data=True, save_log=True, logging_level=logging.INFO, ignore_access_failure_of_adrf6780={}):
        if box_line_config is None:
            default_config_factory = QubeDefaultConfigFactory()
            box_line_config = default_config_factory.create_config(self.device_id)
        
        if save_log:
            log_filepath = self.log_directory.joinpath(f"{self.device_id}.log")
        else:
            log_filepath = None
        with LogWriter(log_filepath, logging_level=logging_level):
            linkup_ok = self.linkup(save_data=save_linkup_data, ignore_access_failure_of_adrf6780=ignore_access_failure_of_adrf6780)
            if all(linkup_ok):
                self.configure_lines(box_line_config)
                self.open_all_lines()
            else:
                print("Linkup failed. Skipped configuring lines.")

    def get_awgs_of_port(self, port):
        group, line = self.port_mapper.resolve_line(port)
        awgs = self.rsource_mapper.get_awg_of_line(group, line)
        return awgs

    def open_all_lines(self):
        for group in self.box.css.get_all_groups():
            for line in self.box.css.get_all_lines_of_group(group):
                self.box.open_rfswitch(group, line)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('target', type=str)
    from quel_ic_config_utils.common_arguments import add_common_workaround_arguments
    add_common_workaround_arguments(parser, use_ignore_access_failure_of_adrf6780=True)
    args = parser.parse_args()
    
    setup_helper = QubeBoxSetupHelper(args.target)
    setup_helper.initialize(ignore_access_failure_of_adrf6780=args.ignore_access_failure_of_adrf6780)
