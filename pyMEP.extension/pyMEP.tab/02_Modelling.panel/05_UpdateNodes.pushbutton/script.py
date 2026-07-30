# -*- coding: utf-8 -*-
"""Update Nodes - adapt the tracked node branches to where the nodes are
NOW.

Every branch Inflow Drop Pipe to Collector builds is tracked in the project's file
store. Hit this after moving (or deleting) nodes:

  - untouched nodes with an intact branch are left alone;
  - moved nodes - and nodes that were TURNED or had their family's
    'Drop Pipe' yes/no toggled - get their old branch deleted, the
    main healed across the old tee, and the branch REBUILT with the
    same settings against the node and main as they now are;
  - deleted nodes get their branch removed and the main healed.

Everything runs in one go (a single undo step).
"""

__title__  = "Update\nNodes"
__author__ = "Glent Group"

import os
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

from pymep_config import get_export_folder
from pymep_nodes_track import update_branches
from pymep_log import Logger

output = script.get_output()
log = Logger(output, "UpdateNodes")
doc = revit.doc

log("### Update Nodes")

base = os.path.join(get_export_folder(doc), "project_files")
try:
    res = update_branches(doc, base, log=log)
except Exception as ex:
    import traceback
    log(traceback.format_exc())
    log.close()
    forms.alert("Update failed - nothing was changed:\n\n{}".format(ex),
                exitscript=True)

if res.get("none"):
    log("No tracked branches yet - run **Inflow Drop Pipe to Collector** first; every "
        "branch it builds is tracked automatically.")
    log.close()
    forms.alert("No tracked branches for this project yet.\n\nRun "
                "Inflow Drop Pipe to Collector first - every branch it builds is "
                "tracked automatically.", exitscript=True)

log("#### Summary")
log("- Unchanged: **{}**".format(res["unchanged"]))
log("- Rebuilt (node moved): **{}**".format(res["rebuilt"]))
log("- Removed (node deleted): **{}**".format(res["removed"]))
if res["failed"]:
    log("- Failed: **{}** (see above)".format(res["failed"]))
log.close()
