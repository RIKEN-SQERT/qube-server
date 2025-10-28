"""
Registers the QuBE server with the labrad registry.
"""
import json
import labrad

# connect to the labrad manager
cxn = labrad.connect()

## Set vault directory
reg = cxn.registry
reg.cd(["", "Servers"])
reg.mkdir("Data Vault")
reg.cd("Data Vault")
reg.mkdir("Repository")
reg.cd("Repository")
reg.set("quel-020_docker", "/root/labrad-data")

# create the directory for the qube server
reg.cd(["", "Servers"])
reg.mkdir("QuBE")
reg.cd("QuBE")

# set the parameters for the qube server
# reg.set("adi_api_path", "/root/lib/qube-calib-env/adi_api_mod")
# reg.set("master_link", "10.3.0.255")

with open("/root/config/possible_links.json", encoding="utf-8") as f:
    possible_links_dict = json.load(f)
    reg.set("possible_links", json.dumps(possible_links_dict))

with open("/root/config/chassis_skew.json", encoding="utf-8") as f:
    chassis_skew_dict = json.load(f)
    reg.set("chassis_skew", json.dumps(chassis_skew_dict))
