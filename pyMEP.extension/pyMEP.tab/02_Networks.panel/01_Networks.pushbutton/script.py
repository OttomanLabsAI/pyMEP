# -*- coding: utf-8 -*-
"""Networks - the model's node families as editable 3D networks.

Scans every placed family instance whose FAMILY name contains the
Network Settings filter word (default "node"), groups the instances
into networks by their type name ('STORMWATER - IN - N1' -> system
STORMWATER, flow IN, network N1), joins them with the branches Nodes to
Main tracked and the mains they tee into, and opens the whole picture
in the drainage 3D viewer.

The dashboard is rebuilt from the model + registry on every launch -
run Inflow Drop Pipe to Collector, hit this again, and the networks follow. Edits made
in the viewer (sizes, gradients, worksets, main end inverts) download
as pymep_network_edits.json; Apply Edits adapts the model to them.
"""

__title__  = "Drainage"
__author__ = "Glent Group"

import os
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

from pymep_config import (load_settings, get_export_folder,
                          get_drainage_dashboard_html, DASHBOARD_DIR)
from pymep_dashboard_launch import write_preload_html, launch_html
from pymep_drainage_networks import (build_dashboard_data,
                                     write_networks_json,
                                     networks_settings,
                                     backfill_network_stamps)
from pymep_log import Logger

output = script.get_output()
log = Logger(output, "Networks")
doc = revit.doc

log("### Networks")

viewer = get_drainage_dashboard_html()
if not viewer:
    log("No drainage viewer HTML found in {}".format(DASHBOARD_DIR))
    log.close()
    forms.alert(
        "No drainage networks viewer found.\n\nExpected a "
        "*drainage*.html inside:\n{}\n\n(or set "
        "'drainage_dashboard_html_path' in pyMEP_settings.json).".format(
            DASHBOARD_DIR),
        exitscript=True)

filt, _folder, _confirm = networks_settings(load_settings())

base = os.path.join(get_export_folder(doc), "project_files")
# every tracked element carries its network name as the pyMEP_Network
# parameter - stamp anything an older version built without it
try:
    n = backfill_network_stamps(doc, base)
    if n:
        log("Stamped **{}** element(s) with their pyMEP_Network "
            "value.".format(n))
except Exception as ex:
    log("(network stamping skipped: {})".format(ex))
log("Scanning for families containing **{}** (set the word in "
    "**Network Settings**) ...".format(filt))
data = build_dashboard_data(doc, base, filt)

if not data["networks"]:
    log("No placed families whose family name contains '{}'.".format(filt))
    log.close()
    forms.alert(
        "No placed families whose FAMILY name contains '{}'.\n\n"
        "Place some node families, or change the word in Network "
        "Settings, and run this again.".format(filt),
        exitscript=True)

n_nodes = sum(len(nw["nodes"]) for nw in data["networks"])
n_br = sum(len(nw["branches"]) for nw in data["networks"])
n_mains = sum(len(nw["mains"]) for nw in data["networks"])
log("Found **{}** network(s): {} node(s), {} tracked branch(es), "
    "{} main run(s).".format(len(data["networks"]), n_nodes, n_br,
                             n_mains))
for nw in data["networks"]:
    log("- **{}**: {} nodes, {} branches, {} mains".format(
        nw["name"], len(nw["nodes"]), len(nw["branches"]),
        len(nw["mains"])))
if not n_br:
    log("(No tracked branches yet - run **Inflow Drop Pipe to Collector** and the "
        "pipework appears here automatically.)")

data_path = write_networks_json(base, data)
log("Networks stored: **{}**".format(data_path))

try:
    launch = write_preload_html(viewer, data_path, get_export_folder(doc),
                                out_name="drainage_dashboard.html")
    launch_html(launch)
    log("Dashboard opened. Select a network there to edit it; **Save "
        "changes for Revit** downloads the edits file for **Apply "
        "Edits**.")
except Exception as ex:
    log("Couldn't build the preloaded launch copy ({}) - opening the "
        "viewer empty instead.".format(ex))
    launch_html(viewer)
log.close()
