# -*- coding: utf-8 -*-
"""Export Pipe Types - dump the model's pipe type definitions to a
versioned JSON for rebuilding them in another (even older) Revit.

Workflow:
  1. Pick which pipe types to export (all by default).
  2. Optionally also save the referenced fitting families as .rfa
     (reference / same-version reuse only - RFAs can't go DOWN a
     Revit version; the JSON can).
  3. Pick the output file; the JSON is written and a summary printed.

Everything is referenced by stable NAME (family / type / segment /
schedule / material) - no ElementIds - and every length is mm, so a
companion Import Pipe Types button in a different model or Revit
version can rebuild the types. All extraction logic lives in
lib/pymep_pipetypes_export.py; this file is UI + orchestration only.
"""

__title__  = "Export\nPipe Types"
__author__ = "Glent Group"

import os
import sys

# Force-reload pymep_* libs so edits on disk always take effect.
for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

from pymep_pipetypes_export import (
    list_pipe_types, export_pipe_types, write_export, save_fitting_rfas,
    summarize_payload,
)
from pymep_log import Logger

output = script.get_output()
log = Logger(output, "ExportPipeTypes")
doc = revit.doc

log("### Export Pipe Types")

# ---------------------------------------------------------------------------
# 1. Collect + pick pipe types (default: all)
# ---------------------------------------------------------------------------
all_types = list_pipe_types(doc)
if not all_types:
    forms.alert("This model has no pipe types.", exitscript=True)
log("Found **{}** pipe type(s) in {}.".format(len(all_types), doc.Title))

choice = forms.alert(
    "Export which pipe types?\n\n"
    "The JSON captures each type's routing preferences (segments,"
    " elbows, junctions, ...), the referenced segments with their full"
    " size catalogues, and the referenced fitting identities - all by"
    " name, no ElementIds, lengths in mm.",
    title="Export Pipe Types",
    options=["Export all {}".format(len(all_types)),
             "Pick which types...", "Cancel"])
if not choice or choice == "Cancel":
    script.exit()

picked_types = [pt for _nm, pt in all_types]
if choice.startswith("Pick"):
    class TypeOption(object):
        def __init__(self, nm, pt):
            self.name = nm
            self.pt = pt
    sel = forms.SelectFromList.show(
        [TypeOption(nm, pt) for nm, pt in all_types],
        title="Pick pipe types to export",
        button_name="Export these",
        multiselect=True,
        name_attr="name")
    if not sel:
        forms.alert("No types picked - nothing to export.",
                    exitscript=True)
    picked_types = [o.pt for o in sel]
log("Exporting **{}** pipe type(s).".format(len(picked_types)))

# ---------------------------------------------------------------------------
# 2. Optional RFA save of the referenced fitting families
# ---------------------------------------------------------------------------
rfa_choice = forms.alert(
    "Also save the referenced fitting families as .rfa files (into a "
    "folder next to the JSON)?\n\n"
    "WARNING: the RFAs are saved in THIS model's Revit version and "
    "will NOT load into an older Revit - they are for reference or "
    "same-version/upward reuse only. The JSON itself has no such "
    "limit.",
    title="Save fitting RFAs?",
    options=["JSON only", "JSON + fitting RFAs", "Cancel"])
if not rfa_choice or rfa_choice == "Cancel":
    script.exit()
save_rfas = rfa_choice.startswith("JSON +")

# ---------------------------------------------------------------------------
# 3. Output path
# ---------------------------------------------------------------------------
default_name = "{}_pipetypes.json".format(doc.Title or "model")
json_path = forms.save_file(file_ext="json", default_name=default_name,
                            title="Save pipe types export")
if not json_path:
    forms.alert("No output file picked - nothing exported.",
                exitscript=True)

# ---------------------------------------------------------------------------
# 4. Extract + write
# ---------------------------------------------------------------------------
try:
    payload, families, warnings = export_pipe_types(
        doc, picked_types, log=log)
    write_export(json_path, payload)
except Exception as ex:
    import traceback
    log(traceback.format_exc())
    log.close()
    forms.alert("Export failed:\n\n{}".format(ex), exitscript=True)

log("Written: **{}**".format(json_path))

rfa_summary = ""
if save_rfas and families:
    rfa_folder = os.path.join(
        os.path.dirname(json_path),
        os.path.splitext(os.path.basename(json_path))[0] + "_fittings")
    log("Saving **{}** fitting famil{} to {} ...".format(
        len(families), "y" if len(families) == 1 else "ies", rfa_folder))
    log("(These RFAs are version-locked to this Revit - see the warning "
        "above.)")
    saved, failed = save_fitting_rfas(doc, families, rfa_folder, log=log)
    rfa_summary = "\nRFAs saved:       {} (failed {})".format(
        len(saved), len(failed))
elif save_rfas:
    log("No fitting families referenced - nothing to save as RFA.")

# ---------------------------------------------------------------------------
# 5. Summary
# ---------------------------------------------------------------------------
s = summarize_payload(payload)
log("#### Summary")
log("- Pipe types exported: **{}** ({} routing rule(s))".format(
    s["types"], s["rules"]))
log("- Segments: **{}**".format(s["segments"]))
log("- Fittings: **{}** ({} distinct famil{})".format(
    s["fittings"], s["fitting_families"],
    "y" if s["fitting_families"] == 1 else "ies"))
if warnings:
    log("- Warnings: **{}**".format(len(warnings)))
    for w in warnings:
        log("  - {}".format(w))
else:
    log("- Warnings: none")

forms.alert(
    "Exported {} pipe type(s), {} segment(s), {} fitting reference(s)."
    "{}\nWarnings: {}\n\n{}".format(
        s["types"], s["segments"], s["fittings"], rfa_summary,
        s["warnings"], json_path),
    title="Pipe types exported")
log.close()
