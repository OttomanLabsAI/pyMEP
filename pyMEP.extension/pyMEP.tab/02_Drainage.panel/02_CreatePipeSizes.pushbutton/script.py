# -*- coding: utf-8 -*-
"""Create Pipe Sizes - read a Civil 3D LandXML export, extract every distinct
circular pipe diameter (with its wall thickness), and add the missing ones
as selectable sizes on a Revit pipe Segment.

Workflow:
  1. Pick the LandXML file.
  2. Parse it (streaming - handles the full multi-hundred-MB export).
  3. Show the distinct circular sizes found (nominal / inner / outer mm,
     count, and which networks use each).
  4. Resolve the target Revit pipe Segment: when Settings > LandXML
     names a segment that exists in this model it is used automatically
     (logged; the confirm dialog still shows it so you can cancel) -
     otherwise a picker appears.
  5. Add the sizes not already on that segment, inside one transaction.

Sizes live on the Segment, not the PipeType - any PipeType that routes
through this segment immediately sees the new sizes in its dropdown. Wall
thickness from the XML's `thickness` gives OD = ID + 2*wall; where the XML
has no wall the OD equals the bore.

Structures and non-circular profiles (RectPipe box culverts / channel
drains, zero-diameter placeholders) are ignored here - this button only
creates round pipe sizes.
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
from pymep_landxml import parse_landxml, distinct_circular_sizes
from pymep_pipesizes import (
    list_pipe_segments, existing_segment_sizes_mm, add_sizes_to_segment,
)
from pymep_log import Logger

output = script.get_output()
log = Logger(output, "LandXMLCreatePipeSizes")
doc = revit.doc

log("### Create Pipe Sizes from LandXML")

# ---------------------------------------------------------------------------
# 1. Pick LandXML
# ---------------------------------------------------------------------------
xml_path = forms.pick_file(file_ext="xml", title="Pick a Civil 3D LandXML export")
if not xml_path:
    forms.alert("No LandXML selected.", exitscript=True)
log("LandXML: **{}**".format(os.path.basename(xml_path)))

# ---------------------------------------------------------------------------
# 2. Parse + extract sizes
# ---------------------------------------------------------------------------
log("Parsing (this can take ~10-20s for a large export)...")
# Create the progress bar in its OWN try/except so a TypeError from an
# older forms.ProgressBar signature can't swallow a TypeError raised by
# the parse itself (which would silently re-run the whole 10-20s parse).
try:
    # Show an indeterminate progress bar if this pyRevit build supports it.
    pb = forms.ProgressBar(title="Parsing LandXML...", indeterminate=True)
except TypeError:
    # Older/newer signature - just parse without the bar.
    pb = None
try:
    if pb is not None:
        with pb:
            parsed = parse_landxml(xml_path, log=log)
    else:
        parsed = parse_landxml(xml_path, log=log)
except Exception as ex:
    import traceback
    log(traceback.format_exc())
    forms.alert("Failed to parse LandXML:\n\n{}".format(ex), exitscript=True)

sizes = distinct_circular_sizes(parsed)
if not sizes:
    forms.alert("No circular pipe sizes found in this LandXML.\n\n"
                "(All pipes may be rectangular/channel-drain profiles, "
                "which this button does not create sizes for.)",
                exitscript=True)

# ---------------------------------------------------------------------------
# 3. Show what was found, ask which to create
# ---------------------------------------------------------------------------
def _net_tags(networks):
    return ", ".join(n.split(" - ")[-1] for n in networks) if networks else "-"

# De-dupe by nominal for the size list (the segment size list keys on
# nominal); keep the most-common wall per nominal for display.
by_nominal = {}
for s in sizes:
    nm = round(s["nominal_mm"], 2)
    prev = by_nominal.get(nm)
    if prev is None or s["count"] > prev["count"]:
        by_nominal[nm] = s
nominal_sizes = [by_nominal[k] for k in sorted(by_nominal.keys())]

class SizeOption(object):
    def __init__(self, s):
        self.s = s
        self.name = ("{:>5.0f} mm   (ID {:>5.0f} / OD {:>5.0f})   "
                     "x{:<4d}   [{}]").format(
            s["nominal_mm"], s["inner_mm"], s["outer_mm"],
            s["count"], _net_tags(s["networks"]))

opts = [SizeOption(s) for s in nominal_sizes]
log("Found {} distinct circular bore size(s) across {} network(s).".format(
    len(nominal_sizes), len(parsed["networks"])))
log("All are pre-selected in the picker. Deselect any you don't want. "
    "OD = bore + 2 x wall thickness (from the XML).")
picked = forms.SelectFromList.show(
    opts,
    title="LandXML pipe sizes - pick which to create",
    button_name="Create these sizes",
    multiselect=True,
    name_attr="name")
if not picked:
    forms.alert("No sizes picked - nothing to do.", exitscript=True)

chosen = [o.s for o in picked]
log("Picked **{}** size(s): {}".format(
    len(chosen), ", ".join("{:.0f}".format(c["nominal_mm"]) for c in chosen)))

# ---------------------------------------------------------------------------
# 4. Resolve target segment - use the Settings preset automatically when
#    it exists in this model; otherwise fall back to the picker.
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
        log("Using configured segment from Settings > LandXML: **{}** "
            "(picker skipped - the confirm dialog below still lets you "
            "cancel).".format(target_name))
    else:
        log("Configured segment '{}' (Settings > LandXML) not found in "
            "this model - pick one.".format(preset))

if target_seg is None:
    # Build the picker label to show how many sizes each segment already has.
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
        log("Tip: set a default segment in Settings > LandXML to skip "
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

# Preview existing vs to-add
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
# 5. Add
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
