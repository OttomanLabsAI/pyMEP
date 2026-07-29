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
    plan_dist_to_segment, outlet_is_connected, node_type_rows,
    node_categories, node_families, node_types_in, search_node_rows,
    node_dia_mm,
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
rows = node_type_rows(doc)
if not rows:
    forms.alert("No placed families with a pipe connector in this "
                "model - nothing to pipe up.", exitscript=True)
for r in rows:
    r["todo"] = [i for i in r["insts"] if not outlet_is_connected(i)]

settings = load_settings()
XAML_PATH = os.path.join(
    os.path.dirname(os.path.abspath(sys.modules["pymep_config"].__file__)),
    "pymep_nodes_to_main.xaml")


def _type_label(r):
    return "{}   ({} placed, {} to connect)".format(
        r["type"], len(r["insts"]), len(r["todo"]))


class NodesWindow(forms.WPFWindow):

    def __init__(self):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self.result = None
        self._shown = []          # the rows currently in CmbType
        self._loading = True
        self.TxtInfo.Text = "Main: '{}' ({:.0f} mm), tee junctions".format(
            safe_name(main), ft2mm(main_dia_ft))
        self.TxtSlope.Text = "{:g}".format(
            float(settings.get("nodes_slope", 100.0)))

        self.CmbCat.Items.Clear()
        for c in node_categories(rows):
            self.CmbCat.Items.Add(c)
        # restore the last-used category > family > type when it is still
        # in the model, else start at the top of each list
        want = settings.get("nodes_label")
        prev = None
        for r in rows:
            if r["label"] == want:
                prev = r
                break
        self._loading = False
        self.CmbCat.SelectedItem = prev["cat"] if prev else (
            self.CmbCat.Items[0] if self.CmbCat.Items.Count else None)
        if prev:
            self.CmbFam.SelectedItem = prev["fam"]
            for i, r in enumerate(self._shown):
                if r["label"] == prev["label"]:
                    self.CmbType.SelectedIndex = i
                    break

    # ---- cascade ----------------------------------------------------------
    def _fill_fams(self):
        cat = self.CmbCat.SelectedItem
        self.CmbFam.Items.Clear()
        if cat is None:
            return
        for f in node_families(rows, str(cat)):
            self.CmbFam.Items.Add(f)
        if self.CmbFam.Items.Count:
            self.CmbFam.SelectedIndex = 0

    def _fill_types(self):
        cat, fam = self.CmbCat.SelectedItem, self.CmbFam.SelectedItem
        self._show_rows(node_types_in(rows, str(cat), str(fam))
                        if (cat is not None and fam is not None) else [])

    def _show_rows(self, shown):
        self._shown = list(shown)
        self.CmbType.Items.Clear()
        for r in self._shown:
            self.CmbType.Items.Add(_type_label(r))
        if self.CmbType.Items.Count:
            self.CmbType.SelectedIndex = 0

    def on_cat_changed(self, sender, args):
        if self._loading or self.TxtSearch.Text.strip():
            return
        self._fill_fams()
        self._fill_types()

    def on_fam_changed(self, sender, args):
        if self._loading or self.TxtSearch.Text.strip():
            return
        self._fill_types()

    # ---- search overrides the cascade --------------------------------------
    def on_search(self, sender, args):
        if self._loading:
            return
        q = self.TxtSearch.Text.strip()
        if not q:
            self.CmbCat.IsEnabled = self.CmbFam.IsEnabled = True
            self._fill_types()
            self.StatusText.Text = ""
            return
        self.CmbCat.IsEnabled = self.CmbFam.IsEnabled = False
        hits = search_node_rows(rows, q)
        # searching shows the FULL label so matches stay unambiguous
        self._shown = list(hits)
        self.CmbType.Items.Clear()
        for r in hits:
            self.CmbType.Items.Add("{}   ({} placed, {} to connect)".format(
                r["label"], len(r["insts"]), len(r["todo"])))
        if self.CmbType.Items.Count:
            self.CmbType.SelectedIndex = 0
        self.StatusText.Text = ("" if hits else
                                "Nothing matches '{}'.".format(q))

    def on_connect(self, sender, args):
        i = self.CmbType.SelectedIndex
        if i < 0 or i >= len(self._shown):
            self.StatusText.Text = "Pick a node type."
            return
        try:
            slope = float(self.TxtSlope.Text)
            if slope <= 0:
                raise ValueError()
        except Exception:
            self.StatusText.Text = ("The gradient must be a positive "
                                    "number (the n of 1:n).")
            return
        self.result = {"row": self._shown[i], "slope": slope}
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

row = win.result["row"]
label, insts, todo = row["label"], row["insts"], row["todo"]
slope = win.result["slope"]
settings["nodes_label"] = label
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
        o_xyz, _c_dia = fixture_outlet_info(node)
        if o_xyz is None:
            failed += 1
            log("  ! no outlet point - skipped")
            continue
        node_dia = node_dia_mm(node)
        dia_mm = node_dia or 100.0
        if not node_dia:
            log("  ! no connector size or DIA parameter - using 100 mm")
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
