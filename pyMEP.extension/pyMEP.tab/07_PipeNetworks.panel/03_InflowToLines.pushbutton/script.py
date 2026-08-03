# -*- coding: utf-8 -*-
"""Inflow Nodes -> Collector Pipes - connect node families into the
Lines to Pipes network, aimed by each node's rotation.

Every selected node casts a ray along its facing direction (family
rotation), the first network pipe that ray meets is the one it belongs
to, and the branch is built into exactly that pipe - drop-first or
grade-first per the family's 'Drop Pipe' parameter, oblique approaches
squared for the tee, branch pipe type and system inherited from the
pipe it joins.
"""

__title__ = "Inflow Nodes ->\nCollector Pipes"
__author__ = "Glent Group"

import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

import os

from pyrevit import revit, forms, script

from pymep_config import get_export_folder, load_settings, save_settings
from pymep_log import Logger
from pymep_connect_fixtures import (
    _has_point, connect_fixture_to_main, fixture_outlet_info,
    main_pipe_info, node_dia_mm, node_directions, outlet_is_connected,
    plan_dist_to_segment, ray_hits_main,
)
from pymep_lines_to_pipes import aim_pick, load_lines_record

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
from Autodesk.Revit.DB import FamilyInstance
from Autodesk.Revit.DB.Plumbing import Pipe
from Autodesk.Revit.UI.Selection import ObjectType

output = script.get_output()
log = Logger(output, "InflowToLines")
doc = revit.doc
uidoc = revit.uidoc

log("### Inflow Nodes -> Collector Pipes")

# ---------------------------------------------------------------------------
# 1. The network built by Lines to Pipes
# ---------------------------------------------------------------------------
base = os.path.join(get_export_folder(doc), "project_files")
rec = load_lines_record(base)
pipes = []
for uid in rec.get("element_uids", []):
    try:
        el = doc.GetElement(uid)
    except Exception:
        el = None
    if isinstance(el, Pipe):
        pipes.append(el)
if not pipes:
    forms.alert("No Lines to Pipes network is recorded for this model - "
                "run Lines to Pipes first.", exitscript=True)
log("**{}** network pipe(s) from the last Lines to Pipes build."
    .format(len(pipes)))

# ---------------------------------------------------------------------------
# 2. The nodes: current selection, else pick them
# ---------------------------------------------------------------------------
nodes = []
for eid in uidoc.Selection.GetElementIds():
    el = doc.GetElement(eid)
    if isinstance(el, FamilyInstance) and _has_point(el):
        nodes.append(el)
if not nodes:
    try:
        refs = uidoc.Selection.PickObjects(
            ObjectType.Element,
            "Pick the node families to connect, then Finish")
        for r in refs:
            el = doc.GetElement(r.ElementId)
            if isinstance(el, FamilyInstance) and _has_point(el):
                nodes.append(el)
    except Exception:
        pass
if not nodes:
    forms.alert("Nothing to connect - select or pick node families "
                "first.", exitscript=True)

settings = load_settings()
slope_txt = forms.ask_for_string(
    default="{:g}".format(float(settings.get("nodes_slope", 100.0))),
    prompt="Branch gradient 1 : n",
    title="Inflow Nodes -> Collector Pipes")
try:
    slope = float(slope_txt)
    if slope <= 0:
        raise ValueError()
except Exception:
    forms.alert("The gradient must be a positive 1:n number.",
                exitscript=True)
settings["nodes_slope"] = slope
try:
    save_settings(settings)
except Exception:
    pass

log("**{}** node(s), branch gradient **1:{:g}**; pipe type, system and "
    "size rules come from the pipe each node joins.".format(
        len(nodes), slope))

# ---------------------------------------------------------------------------
# 3. Aim every node along its rotation at the network
# ---------------------------------------------------------------------------
# main_pipe_info returns the two ends as (x, y, z) TUPLES
pipe_lines = []
unreadable = 0
for p in pipes:
    try:
        a, b, _t, _s, _l, _d = main_pipe_info(p)
        pipe_lines.append((p, (a[0], a[1]), (b[0], b[1])))
    except Exception as ex:
        unreadable += 1
        log("- ! a network pipe could not be read ({}) - it cannot "
            "receive branches".format(ex))
if unreadable:
    log("**{}** of {} network pipe(s) unreadable.".format(
        unreadable, len(pipes)))
if not pipe_lines:
    forms.alert("None of the recorded network pipes could be read - "
                "nothing to aim at. See the report for the reasons.",
                exitscript=True)


def aim_at_network(node, o_xy):
    """(pipe, how): the first network pipe the node's facing ray meets,
    trying the facing pair before the hand pair; else the plan-nearest
    pipe with a note."""
    # node_directions already yields (x, y) tuples - feed them
    # straight through, no reshaping
    return aim_pick(o_xy, node_directions(node), pipe_lines,
                    ray_hits_main, plan_dist_to_segment)


done, failed, skipped = 0, 0, 0
fitting_notes = []
for node in nodes:
    from pymep_revit import safe_name
    label = safe_name(node)
    try:
        outlet, odia = fixture_outlet_info(node)
    except Exception:
        outlet = None
    if outlet is None:
        skipped += 1
        log("- **{}**: no outlet connector - skipped".format(label))
        continue
    try:
        if outlet_is_connected(node):
            skipped += 1
            log("- **{}**: already connected - skipped".format(label))
            continue
    except Exception:
        pass
    # fixture_outlet_info returns the outlet POINT (x, y, z) in feet
    o_xy = (outlet[0], outlet[1])
    try:
        target, how = aim_at_network(node, o_xy)
    except Exception as ex:
        skipped += 1
        log("- **{}**: aim failed ({}) - skipped".format(label, ex))
        continue
    if target is None:
        skipped += 1
        log("- **{}**: no network pipe to join - skipped".format(label))
        continue
    if how == "nearest":
        log("- **{}**: its rotation aims at no network pipe - using "
            "the plan-nearest one".format(label))
    dia_mm = node_dia_mm(node, settings.get("nodes_dia_param")) or \
        node_dia_mm(node) or 100.0
    try:
        r = connect_fixture_to_main(doc, node, target, slope, dia_mm,
                                    invert_m=None, log=log,
                                    use_rotation=True)
        done += 1
        fitting_notes += r.get("fitting_misses", [])
    except Exception as ex:
        failed += 1
        import traceback
        log(traceback.format_exc())
        log("- ! **{}**: branch not built ({})".format(label, ex))

log("#### Summary")
log("- Branches built: **{}**".format(done))
if skipped:
    log("- Skipped: **{}**".format(skipped))
if failed:
    log("- Failed: **{}**".format(failed))
for n in fitting_notes:
    log("- {}".format(n))
log("Note: re-running Lines to Pipes rebuilds the MAINS only - run "
    "this button again afterwards to reconnect the nodes.")

forms.alert("Connected {} node(s) into the line network.{}{}".format(
    done,
    "\n{} skipped - see the report.".format(skipped) if skipped else "",
    "\n{} failed - see the report.".format(failed) if failed else ""),
    title="Inflow Nodes -> Collector Pipes")
log.close()
