# -*- coding: utf-8 -*-
"""Open Dashboard - launch the OttomanLabs utilities 3D dashboard in the
default browser.

The dashboard is a self-contained HTML app bundled inside the extension
(<extension>/dashboard/). It runs entirely in the browser: it opens (or
asks for) a LandXML export, renders the buried-utilities networks in 3D,
and its EXPORT button writes the JSON files that the other Dashboard
buttons consume (Place Boxes / Place Cylinders / Place Pipes).

Which file opens:
  1. the 'dashboard_html_path' override in pyMEP_settings.json, else
  2. the NEWEST *.html in <extension>/dashboard/ - so upgrading the
     viewer is just dropping the new file into that folder.

The first load needs internet access (the viewer pulls three.js from a
CDN); after that it is all local.
"""

__title__  = "Create LandXML\nDashboard"
__author__ = "Glent Group"

import os
import sys

# Force-reload pymep_* libs so edits on disk always take effect.
for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import forms, script

from pymep_config import get_dashboard_html, DASHBOARD_DIR
from pymep_log import Logger

output = script.get_output()
log = Logger(output, "OpenDashboard")

path = get_dashboard_html()
if not path:
    log("No dashboard HTML found in {}".format(DASHBOARD_DIR))
    log.close()
    forms.alert(
        "No dashboard HTML found.\n\nDrop the utilities 3D viewer .html "
        "into:\n{}\n\n(or set 'dashboard_html_path' in "
        "pyMEP_settings.json).".format(DASHBOARD_DIR),
        exitscript=True)

log("### Open Dashboard")
log("Opening **{}** in the default browser.".format(os.path.basename(path)))
try:
    from System.Diagnostics import Process
    Process.Start(path)
except Exception:
    import webbrowser
    webbrowser.open("file:///" + path.replace(os.sep, "/"))
log("Use the dashboard's EXPORT button, then Place Boxes / Place "
    "Cylinders / Place Pipes here to model what it exported.")
log.close()
