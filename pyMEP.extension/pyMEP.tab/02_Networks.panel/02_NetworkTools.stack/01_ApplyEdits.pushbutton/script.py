# -*- coding: utf-8 -*-
"""Apply Edits - adapt the model to the Networks dashboard's saved
changes.

Picks the NEWEST pymep_network_edits*.json out of the Network Settings
edits folder (default: Downloads), then per edited network: resizes /
re-grades its mains, sets an end invert where one was typed, moves
worksets, and rebuilds the tracked branches against the main as it now
lies - the same delete-heal-rebuild machinery Update Nodes uses, all in
ONE undo step.

The dashboard keeps ONE edits file documented (every change rewrites
it), so this button is the whole update: each save carries a timestamp,
the last applied one is remembered, and the same save never applies
twice - while the file itself stays in place for the dashboard to keep
writing to.
"""

__title__  = "Apply Edits"
__author__ = "Glent Group"

import os
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

from pymep_config import (get_export_folder, get_downloads_folder,
                          load_settings, save_settings)
from pymep_drainage_networks import (find_edits_file, parse_edits,
                                     apply_edits, edits_stamp,
                                     networks_settings)
from pymep_log import Logger

output = script.get_output()
log = Logger(output, "ApplyNetworkEdits")
doc = revit.doc

log("### Apply Edits")

_filt, folder, confirm = networks_settings(load_settings())
if not (folder and os.path.isdir(folder)):
    folder = get_downloads_folder()
path = find_edits_file(folder)
if path is None:
    log("No pymep_network_edits*.json in {}".format(folder))
    log.close()
    forms.alert(
        "No edits file found in\n{}\n\nOpen the Networks dashboard, "
        "change something on a network and hit 'Save changes for "
        "Revit' - then run this again. (The folder is set in Network "
        "Settings; blank = Downloads.)".format(folder),
        exitscript=True)

f = open(path, "rb")
try:
    text = f.read().decode("utf-8-sig", "replace")
finally:
    f.close()
try:
    edits = parse_edits(text)
except ValueError as ex:
    log("'{}' rejected: {}".format(path, ex))
    log.close()
    forms.alert("'{}' doesn't hold any applicable dashboard edits:\n\n"
                "{}\n\nChange something in the Networks dashboard "
                "first - it keeps the file up to date.".format(
                    os.path.basename(path), ex), exitscript=True)

settings = load_settings()
stamp = edits_stamp(edits, path)
if stamp and stamp == settings.get("networks_applied_stamp"):
    log("The newest save ({}) was already applied - nothing new."
        .format(stamp))
    log.close()
    forms.alert(
        "These edits were already applied.\n\nChange something in the "
        "Networks dashboard (it re-saves the file) and run this "
        "again.", exitscript=True)

names = [e.get("network") or "?" for e in edits["edits"]]
log("Edits file: **{}**".format(path))
log("Networks to adapt: **{}**".format(", ".join(names)))
if confirm and not forms.alert(
        "Apply the dashboard edits from\n{}\n\nNetworks: {}\n\nThe "
        "mains and tracked branches of these networks will be "
        "reshaped/rebuilt (one undo step).".format(
            os.path.basename(path), ", ".join(names)),
        yes=True, cancel=True):
    log("Cancelled - nothing changed.")
    log.close()
    script.exit()

base = os.path.join(get_export_folder(doc), "project_files")
try:
    res = apply_edits(doc, base, edits, log=log)
except Exception as ex:
    import traceback
    log(traceback.format_exc())
    log.close()
    forms.alert("Applying the edits failed - everything was rolled "
                "back:\n\n{}".format(ex), exitscript=True)

if stamp:
    settings["networks_applied_stamp"] = stamp
    try:
        save_settings(settings)
    except Exception:
        pass
    log("Save **{}** remembered as applied - the file stays put for "
        "the dashboard to keep writing to.".format(stamp))

log("#### Summary")
log("- Networks adapted: **{}**".format(res["networks"]))
log("- Main runs reshaped: **{}**".format(res["mains"]))
log("- Branches rebuilt: **{}**".format(res["branches"]))
if res["worksets"]:
    log("- Elements moved to a new workset: **{}**".format(
        res["worksets"]))
if res["failed"]:
    log("- Failed: **{}** (see above)".format(res["failed"]))
log("Open the dashboard again and it shows the model as it now is.")
log.close()
