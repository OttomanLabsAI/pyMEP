# -*- coding: utf-8 -*-
"""Create Pipe Sizes - read a dashboard PIPES export, extract every
distinct circular pipe diameter, and add the missing ones as selectable
sizes on a Revit pipe Segment.

Workflow (same as the old LandXML version, fed from the dashboard):
  1. Pick the pipes export (.json) - the dashboard's Export pipes button.
  2. Show the distinct circular sizes found (mm, with usage counts).
  3. Resolve the target Revit pipe Segment: when Settings > Pipes names a
     segment that exists in this model it is used automatically -
     otherwise a picker appears.
  4. Add the sizes not already on that segment, inside one transaction.

Sizes live on the Segment, not the PipeType - any PipeType that routes
through the segment immediately sees the new sizes in its dropdown.
Rectangular duct-bank rows are ignored (round pipe sizes only). Place
Pipes already ensures sizes automatically; keep this for adding sizes
without placing pipes.
"""

__title__  = "Create\nPipe Sizes"
__author__ = "Glent Group"

import os
import sys

# Force-reload pymep_* libs so edits on disk always take effect.
for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

from pymep_config import get_landxml_segment_name
from pymep_dashboard_pipes import read_pipes_export, distinct_circular_sizes
from pymep_pipesizes import (
    list_pipe_segments, existing_segment_sizes_mm, add_sizes_to_segment,
)
from pymep_log import Logger

output = script.get_output()
log = Logger(output, "DashboardCreatePipeSizes")
doc = revit.doc

log("### Create Pipe Sizes from dashboard export")

# ---------------------------------------------------------------------------
# 1. Pick export + read
# ---------------------------------------------------------------------------
json_path = forms.pick_file(file_ext="json",
                            title="Pick a dashboard PIPES export (.json)")
if not json_path:
    forms.alert("No export selected.", exitscript=True)
log("Export: **{}**".format(os.path.basename(json_path)))

try:
    meta, rows, notes = read_pipes_export(json_path)
except Exception as ex:
    import traceback
    log(traceback.format_exc())
    log.close()
    forms.alert("Could not read the export:\n\n{}".format(ex),
                exitscript=True)
for n in notes:
    log(n)

sizes = distinct_circular_sizes(rows)
if not sizes:
    forms.alert("No circular pipe sizes in this export.\n\n"
                "(All pipes may be rectangular duct banks, which this "
                "button does not create sizes for.)", exitscript=True)

# ---------------------------------------------------------------------------
# 2. Show what was found, ask which to create
# ---------------------------------------------------------------------------
class SizeOption(object):
    def __init__(self, s):
        self.s = s
        self.name = "{:>5.0f} mm   x{:<4d}".format(
            s["nominal_mm"], s["count"])

opts = [SizeOption(s) for s in sizes]
log("Found {} distinct circular size(s) across {} pipe row(s).".format(
    len(sizes), len(rows)))
log("All are pre-selected in the picker. Deselect any you don't want.")
picked = forms.SelectFromList.show(
    opts,
    title="Dashboard pipe sizes - pick which to create",
    button_name="Create these sizes",
    multiselect=True,
    name_attr="name")
if not picked:
    forms.alert("No sizes picked - nothing to do.", exitscript=True)

chosen = [o.s for o in picked]
log("Picked **{}** size(s): {}".format(
    len(chosen), ", ".join("{:.0f}".format(c["nominal_mm"]) for c in chosen)))

# ---------------------------------------------------------------------------
# 3. Resolve target segment - Settings preset automatically when it
#    exists in this model; otherwise the picker.
# ---------------------------------------------------------------------------
segs = list_pipe_segments(doc)
if not segs:
    forms.alert("This project has no pipe Segments defined.\n\n"
                "Create or load a pipe Segment (Manage > MEP Settings > "
                "Mechanical Settings > Pipe Settings > Segments) first.",
                exitscript=True)

preset = get_landxml_segment_name()

target_seg = None
target_name = None
if preset:
    for _nm, _sg in segs:
        if _nm == preset:
            target_seg = _sg
            target_name = _nm
            break
    if target_seg is not None:
        log("Using configured segment from Settings > Pipes: **{}** "
            "(picker skipped - the confirm dialog below still lets you "
            "cancel).".format(target_name))
    else:
        log("Configured segment '{}' (Settings > Pipes) not found in "
            "this model - pick one.".format(preset))

if target_seg is None:
    class SegOption(object):
        def __init__(self, name, seg):
            self.name_raw = name
            self.seg = seg
            try:
                n_existing = len(existing_segment_sizes_mm(seg))
            except Exception:
                n_existing = -1
            self.name = "{}   ({} existing size{})".format(
                name, n_existing if n_existing >= 0 else "?",
                "" if n_existing == 1 else "s")

    seg_opts = [SegOption(n, s) for n, s in segs]

    log("Sizes are added to the SEGMENT; every pipe type routing through "
        "it will see them.")
    if not preset:
        log("Tip: set a default segment in Settings > Pipes to skip "
            "this step next time.")
    seg_choice = forms.SelectFromList.show(
        seg_opts,
        title="Pick the pipe Segment to add sizes to",
        button_name="Use this segment",
        multiselect=False,
        name_attr="name")
    if not seg_choice:
        forms.alert("No segment picked - aborting.", exitscript=True)

    target_seg = seg_choice.seg
    target_name = seg_choice.name_raw

log("Target segment: **{}**".format(target_name))

existing = set(existing_segment_sizes_mm(target_seg))
to_add = [c for c in chosen if round(c["nominal_mm"], 2) not in existing]
already = [c for c in chosen if round(c["nominal_mm"], 2) in existing]

confirm = forms.alert(
    "Add pipe sizes to segment:\n  {}\n\n"
    "Will ADD ({}):  {}\n"
    "Already present ({}):  {}\n\n"
    "Proceed?".format(
        target_name,
        len(to_add),
        ", ".join("{:.0f}".format(c["nominal_mm"]) for c in to_add) or "(none)",
        len(already),
        ", ".join("{:.0f}".format(c["nominal_mm"]) for c in already) or "(none)"),
    title="Confirm size creation",
    options=["Add sizes", "Cancel"])
if confirm != "Add sizes":
    forms.alert("Cancelled.", exitscript=True)

# ---------------------------------------------------------------------------
# 4. Add
# ---------------------------------------------------------------------------
try:
    added, skipped, failed = add_sizes_to_segment(
        doc, target_seg, chosen, log=log)
    forms.alert(
        "Done.\n\n"
        "Added:           {}\n"
        "Already present: {}\n"
        "Failed:          {}\n\n"
        "Segment: {}".format(added, skipped, failed, target_name),
        title="Pipe sizes created")
    log("### Done - added {}, skipped {}, failed {}".format(
        added, skipped, failed))
except Exception as ex:
    import traceback
    log("Error: {}".format(ex))
    log(traceback.format_exc())
    forms.alert("Error adding sizes:\n\n{}".format(ex))
finally:
    log.close()
