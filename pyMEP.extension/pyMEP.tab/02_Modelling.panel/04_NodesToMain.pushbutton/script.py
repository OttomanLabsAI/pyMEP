# -*- coding: utf-8 -*-
"""Nodes to Main - select a main pipe, pick a node FAMILY TYPE and a
gradient, and every placed, still-unconnected node of that type gets
piped into the main.

Per node: a drop pipe from its outlet connector (diameter taken from
THE NODE's own connector, snapped to the main type's routing sizes), a
bend at its base, a run falling at 1:n to meet the main as it lies, and
a TEE JUNCTION into the main. Nodes whose outlet is already connected
are left alone. Branches take the main's pipe type, system type and
level; each tee splits the main and later nodes tie into whichever
piece spans their position.
"""

__title__  = "Nodes\nto Main"
__author__ = "Glent Group"

import os
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

from pymep_connect_fixtures import (
    fixture_outlet_info, main_pipe_info, connect_fixture_to_main,
    plan_dist_to_segment, list_node_types, outlet_is_connected,
)
from pymep_config import load_settings, save_settings
from pymep_revit import safe_name, ft2mm
from pymep_log import Logger

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB.Plumbing import Pipe

output = script.get_output()
log = Logger(output, "NodesToMain")
doc = revit.doc
uidoc = revit.uidoc

log("### Nodes to Main")

# ---------------------------------------------------------------------------
# 1. The main pipe: pre-selected, else pick it in the view
# ---------------------------------------------------------------------------
main = None
for eid in uidoc.Selection.GetElementIds():
    el = doc.GetElement(eid)
    if isinstance(el, Pipe):
        if main is None:
            main = el
        else:
            forms.alert("Select just ONE pipe (the main run), or nothing "
                        "and pick it when asked.", exitscript=True)

if main is None:
    clr.AddReference("RevitAPIUI")
    from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter

    class _PipesOnly(ISelectionFilter):
        def AllowElement(self, e):
            return isinstance(e, Pipe)

        def AllowReference(self, r, p):
            return False

    try:
        ref = uidoc.Selection.PickObject(
            ObjectType.Element, _PipesOnly(), "Pick the main pipe")
        main = doc.GetElement(ref.ElementId)
    except Exception:
        log("Cancelled - nothing changed.")
        log.close()
        script.exit()

try:
    a, b, _tid, _sid, _lid, main_dia_ft = main_pipe_info(main)
except Exception as ex:
    log("{}".format(ex))
    log.close()
    forms.alert(str(ex), exitscript=True)

# ---------------------------------------------------------------------------
# 2. The node types placed in this model (families with a pipe connector)
# ---------------------------------------------------------------------------
node_types = list_node_types(doc)
if not node_types:
    forms.alert("No placed families with a pipe connector in this "
                "model - nothing to pipe up.", exitscript=True)

rows = []
for label, insts in node_types:
    todo = [i for i in insts if not outlet_is_connected(i)]
    rows.append((label, insts, todo))

settings = load_settings()
XAML_PATH = os.path.join(
    os.path.dirname(os.path.abspath(sys.modules["pymep_config"].__file__)),
    "pymep_nodes_to_main.xaml")


class NodesWindow(forms.WPFWindow):

    def __init__(self):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self.result = None
        self.TxtInfo.Text = "Main: '{}' ({:.0f} mm), tee junctions".format(
            safe_name(main), ft2mm(main_dia_ft))
        self.CmbType.Items.Clear()
        for label, insts, todo in rows:
            self.CmbType.Items.Add("{}   ({} placed, {} to connect)"
                                   .format(label, len(insts), len(todo)))
        want = settings.get("nodes_family")
        idx = 0
        for i, (label, _insts, _todo) in enumerate(rows):
            if label == want:
                idx = i
                break
        self.CmbType.SelectedIndex = idx
        self.TxtSlope.Text = "{:g}".format(
            float(settings.get("nodes_slope", 100.0)))

    def on_connect(self, sender, args):
        if self.CmbType.SelectedIndex < 0:
            self.StatusText.Text = "Pick a node family type."
            return
        try:
            slope = float(self.TxtSlope.Text)
            if slope <= 0:
                raise ValueError()
        except Exception:
            self.StatusText.Text = ("The gradient must be a positive "
                                    "number (the n of 1:n).")
            return
        self.result = {"idx": self.CmbType.SelectedIndex, "slope": slope}
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


win = NodesWindow()
win.ShowDialog()
if win.result is None:
    log("Cancelled - nothing changed.")
    log.close()
    script.exit()

label, insts, todo = rows[win.result["idx"]]
slope = win.result["slope"]
settings["nodes_family"] = label
settings["nodes_slope"] = slope
try:
    save_settings(settings)
except Exception:
    pass

log("Type **{}**: {} placed, **{}** unconnected to pipe up; gradient "
    "**1:{:g}**.".format(label, len(insts), len(todo), slope))
if not todo:
    log("Every node of that type is already connected - nothing to do.")
    log.close()
    forms.alert("Every '{}' node is already connected.".format(label),
                exitscript=True)

# ---------------------------------------------------------------------------
# 3. Pipe each node up - dia from ITS connector; tees split the main, so
#    track the pieces and tie each node into the one spanning it
# ---------------------------------------------------------------------------
main_segs = [main]


def _nearest_seg(outlet_xyz):
    best, bestd = main_segs[0], None
    for seg in main_segs:
        try:
            sa, sb, _t, _s, _l, _d = main_pipe_info(seg)
        except Exception:
            continue
        d = plan_dist_to_segment(outlet_xyz, sa, sb)
        if bestd is None or d < bestd:
            best, bestd = seg, d
    return best


done = 0
failed = 0
fitting_notes = 0
for node in todo:
    log("**{}** (id {}):".format(safe_name(node), node.Id))
    try:
        o_xyz, node_dia = fixture_outlet_info(node)
        if o_xyz is None:
            failed += 1
            log("  ! no outlet connector - skipped")
            continue
        dia_mm = node_dia or 100.0
        if not node_dia:
            log("  ! connector has no size - using 100 mm")
        seg = _nearest_seg(o_xyz)
        r = connect_fixture_to_main(doc, node, seg, slope, dia_mm,
                                    invert_m=None, log=log)
        done += 1
        fitting_notes += r["fitting_misses"]
        if r.get("new_main_segment") is not None:
            main_segs.append(r["new_main_segment"])
    except Exception as ex:
        failed += 1
        import traceback
        log(traceback.format_exc())
        log("  ! not connected: {}".format(ex))

log("#### Summary")
log("- Nodes piped into the main: **{}** of {} (tee junctions; the "
    "main is now {} segment(s))".format(done, len(todo), len(main_segs)))
if fitting_notes:
    log("- Fitting notes: **{}** (see above)".format(fitting_notes))
if failed:
    log("- Failed / skipped: **{}**".format(failed))
log.close()
