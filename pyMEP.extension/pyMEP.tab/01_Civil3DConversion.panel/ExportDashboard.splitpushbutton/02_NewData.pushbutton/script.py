# -*- coding: utf-8 -*-
"""Civil 3D Export Dashboard - open it EMPTY for new data.

Launches the dashboard with its landing page: browse (or drag & drop) a
fresh Civil 3D LandXML export. Store that file in Setup > Project Files
and the main button opens it automatically next time.

Which viewer opens:
  1. the 'dashboard_html_path' override in pyMEP_settings.json, else
  2. the NEWEST *.html in <extension>/dashboard/.
"""

__title__  = "New\nData"
__author__ = "Glent Group"

import os
import sys

# Force-reload pymep_* libs so edits on disk always take effect.
for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import forms, script

from pymep_config import get_dashboard_html, DASHBOARD_DIR
from pymep_dashboard_launch import launch_html
from pymep_log import Logger

output = script.get_output()
log = Logger(output, "DashboardNewData")

path = get_dashboard_html()
if not path:
    log("No dashboard HTML found in {}".format(DASHBOARD_DIR))
    log.close()
    forms.alert(
        "No dashboard HTML found.\n\nDrop the utilities 3D viewer .html "
        "into:\n{}\n\n(or set 'dashboard_html_path' in "
        "pyMEP_settings.json).".format(DASHBOARD_DIR),
        exitscript=True)

log("### Civil 3D Export Dashboard - new data")
log("Opening **{}** empty - browse or drop the LandXML in the "
    "browser.".format(os.path.basename(path)))
launch_html(path)
log("Tip: store that LandXML in **Setup > Project Files** and the main "
    "button opens it automatically next time.")
log.close()
