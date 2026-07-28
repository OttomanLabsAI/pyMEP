# -*- coding: utf-8 -*-
"""Civil 3D LandXML Dashboard - open it WITH this project's stored data.

Reads the dashboard data file stored by Setup > Project Files
(the project's Civil 3D LandXML), writes a launch copy of the viewer
with the file injected, and opens it - the dashboard goes straight into
the 3D view, no browsing. Falls back to the empty dashboard (with a
pointer at Project Files) when nothing is stored yet.
"""

__title__  = "Open\nProject Data"
__author__ = "Glent Group"

import os
import sys

# Force-reload pymep_* libs so edits on disk always take effect.
for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

import pymep_project_files as pf
from pymep_config import get_dashboard_html, get_export_folder, DASHBOARD_DIR
from pymep_dashboard_launch import write_preload_html, launch_html
from pymep_log import Logger

output = script.get_output()
log = Logger(output, "DashboardProjectData")
doc = revit.doc

viewer = get_dashboard_html()
if not viewer:
    log("No dashboard HTML found in {}".format(DASHBOARD_DIR))
    log.close()
    forms.alert(
        "No dashboard HTML found.\n\nDrop the utilities 3D viewer .html "
        "into:\n{}\n\n(or set 'dashboard_html_path' in "
        "pyMEP_settings.json).".format(DASHBOARD_DIR),
        exitscript=True)

log("### Civil 3D LandXML Dashboard - project data")

base = os.path.join(get_export_folder(doc), "project_files")
data = pf.slot_file(base, "dashboard_data")

if data is None:
    log("No dashboard data stored for this project yet - opening the "
        "dashboard EMPTY. Store the project's LandXML in "
        "**Setup > Project Files** and this button will open it "
        "automatically.")
    launch_html(viewer)
else:
    log("Project data: **{}**".format(os.path.basename(data)))
    try:
        launch = write_preload_html(viewer, data, get_export_folder(doc))
        log("Opening the dashboard preloaded with it.")
        launch_html(launch)
    except Exception as ex:
        log("Couldn't build the preloaded launch copy ({}) - opening "
            "the dashboard empty instead.".format(ex))
        launch_html(viewer)

log("Use **Export model** in the dashboard, then Place Structures / "
    "Place Pipes here.")
log.close()
