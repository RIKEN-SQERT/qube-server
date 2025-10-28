"""
Registers the QuBE server with the labrad registry.
"""
import json
import labrad

# connect to the labrad manager
cxn = labrad.connect()

# create the directory for the qube server
reg = cxn.registry
reg.cd(["Servers", "Data Vault", "Repository"])
reg.set("kappa_docker", "/root/labrad-data")

# load qube ip info
reg.cd(["", "Servers", "QuBE"])
with open("/root/config/possible_links.json", encoding="utf-8") as f:
    possible_links_dict = json.load(f)
    reg.set("possible_links", json.dumps(possible_links_dict))

with open("/root/config/chassis_skew.json", encoding="utf-8") as f:
    possible_links_dict = json.load(f)
    reg.set("chassis_skew", json.dumps(possible_links_dict))
