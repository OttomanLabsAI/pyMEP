# -*- coding: utf-8 -*-
"""Inflow Drop Pipe to Collector - pipe nodes into a collector run.

WHICH nodes, your choice:
  - SELECT them: pre-select the collector pipe together with the node
    instances (or pick the pipe, then pick the nodes when prompted) -
    exactly those nodes get connected, mixed types welcome;
  - or a FAMILY TYPE: finish the node pick empty and the dialog's
    category > family > type cascade (or search) connects every
    placed, still-unconnected node of that type.

Per node: a drop pipe from its outlet connector (diameter taken from
THE NODE's own connector, snapped to the main type's routing sizes), a
bend at its base, a run falling at 1:n to meet the main as it lies, and
a TEE JUNCTION into the main. Nodes whose outlet is already connected
are left alone. Branches take the main's pipe type, system type and
level; each tee splits the main and later nodes tie into whichever
piece spans their position.
"""

__title__  = "Inflow Drop Pipe\nto Collector"
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
    node_dia_mm, node_dia_param_options, CONNECTOR_DIA, DIA_PARAM_NAMES,
    main_gradient, regrade_main, list_pipe_type_options,
    list_system_type_options, set_pipe_dia, _has_point,
)
from pymep_config import load_settings, save_settings, get_export_folder
from pymep_drainage_networks import next_collector_name
from pymep_net_param import (ensure_network_param, stamp_network,
                             with_connected_fittings, network_value,
                             collect_by_network)
from pymep_nodes_track import (make_record, add_branch, load_branches,
                               tracked_node_uids)
from pymep_revit import safe_name, ft2mm
from pymep_log import Logger

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import FamilyInstance
from Autodesk.Revit.DB.Plumbing import Pipe

output = script.get_output()
log = Logger(output, "NodesToMain")
doc = revit.doc
uidoc = revit.uidoc

log("### Inflow Drop Pipe to Collector")

# ---------------------------------------------------------------------------
# 1. The main pipe and (optionally) the NODES, straight from selection:
#    pre-select the pipe + node instances together, or pick the pipe
#    and then pick nodes when prompted (Finish empty -> family type)
# ---------------------------------------------------------------------------
main = None
sel_nodes = []
for eid in uidoc.Selection.GetElementIds():
    el = doc.GetElement(eid)
    if isinstance(el, Pipe):
        if main is None:
            main = el
        else:
            forms.alert("Select just ONE pipe (the main run), or nothing "
                        "and pick it when asked.", exitscript=True)
    elif isinstance(el, FamilyInstance) and _has_point(el):
        sel_nodes.append(el)

clr.AddReference("RevitAPIUI")
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter

if main is None:

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

    # no nodes pre-selected: offer to pick them - Finish with nothing
    # (or Escape) falls back to the family-type dialog
    if not sel_nodes:

        class _NodesOnly(ISelectionFilter):
            def AllowElement(self, e):
                return (isinstance(e, FamilyInstance)
                        and not isinstance(e, Pipe) and _has_point(e))

            def AllowReference(self, r, p):
                return False

        try:
            refs = uidoc.Selection.PickObjects(
                ObjectType.Element, _NodesOnly(),
                "Pick the nodes to connect - Finish with NOTHING "
                "selected to choose a family type instead")
            sel_nodes = [doc.GetElement(r.ElementId) for r in refs]
        except Exception:
            sel_nodes = []

if sel_nodes:
    log("Nodes chosen BY SELECTION: **{}**.".format(len(sel_nodes)))

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
if not rows and not sel_nodes:
    forms.alert("No placed families with a pipe connector in this "
                "model - nothing to pipe up.", exitscript=True)
# 'already connected' = outlet physically connected OR a live tracked
# branch (grade-first runs don't hook the outlet connector)
try:
    _tracked = tracked_node_uids(
        doc, os.path.join(get_export_folder(doc), "project_files"))
except Exception:
    _tracked = set()


def _is_done(n):
    return n.UniqueId in _tracked or outlet_is_connected(n)


for r in rows:
    r["todo"] = [i for i in r["insts"] if not _is_done(i)]


def _node_label(n):
    cat, fam, typ = "(no category)", "?", "?"
    try:
        sym = n.Symbol
        typ = safe_name(sym)
        fam = safe_name(sym.Family)
        if sym.Category is not None:
            cat = sym.Category.Name
    except Exception:
        pass
    return "{} : {} : {}".format(cat, fam, typ)

settings = load_settings()
XAML_PATH = os.path.join(
    os.path.dirname(os.path.abspath(sys.modules["pymep_config"].__file__)),
    "pymep_nodes_to_main.xaml")


def _type_label(r):
    return "{}   ({} placed, {} to connect)".format(
        r["type"], len(r["insts"]), len(r["todo"]))


_cur_grad = main_gradient(a, b)
SAME_AS_MAIN = "(same as the main)"
_pt_opts = list_pipe_type_options(doc)
_st_opts = list_system_type_options(doc)


class NodesWindow(forms.WPFWindow):

    def __init__(self):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self.result = None
        self._shown = []          # the rows currently in CmbType
        self._dia_opts = []       # (mode, name) per CmbDiaParam entry
        self._loading = True
        for combo, opts, key in ((self.CmbPipeType, _pt_opts,
                                  "nodes_pipe_type"),
                                 (self.CmbSysType, _st_opts,
                                  "nodes_sys_type")):
            combo.Items.Clear()
            combo.Items.Add(SAME_AS_MAIN)
            for nm, _eid in opts:
                combo.Items.Add(nm)
            combo.SelectedIndex = 0
            want = settings.get(key)
            for i, (nm, _eid) in enumerate(opts):
                if nm == want:
                    combo.SelectedIndex = i + 1
                    break
        self.TxtMainDia.Text = "{:.0f}".format(ft2mm(main_dia_ft))
        self.TxtInfo.Text = "Main: '{}' ({:.0f} mm), tee junctions".format(
            safe_name(main), ft2mm(main_dia_ft))
        self.TxtSlope.Text = "{:g}".format(
            float(settings.get("nodes_slope", 100.0)))
        if _cur_grad is not None:
            self.TxtMainSlope.Text = "{:.0f}".format(_cur_grad)
            self.TxtMainHint.Text = (
                "The main currently falls at about 1:{:.0f}. Tick to "
                "re-grade it BEFORE the branches are drawn - the chosen "
                "end stays put, the other moves.".format(_cur_grad))
        else:
            self.TxtMainSlope.Text = "{:g}".format(
                float(settings.get("nodes_slope", 100.0)))
            self.TxtMainHint.Text = (
                "The main is currently LEVEL. Tick to re-grade it before "
                "the branches are drawn - the chosen end stays put, the "
                "other moves.")
        if settings.get("nodes_main_keep", "low") == "high":
            self.RadMainUpper.IsChecked = True

        if sel_nodes:
            # nodes chosen BY SELECTION: the family cascade is moot
            self._loading = False
            for c in (self.CmbCat, self.CmbFam, self.TxtSearch):
                c.IsEnabled = False
            self.CmbType.Items.Clear()
            self.CmbType.Items.Add("({} node(s) chosen by "
                                   "selection)".format(len(sel_nodes)))
            self.CmbType.SelectedIndex = 0
            self.CmbType.IsEnabled = False
            self.StatusText.Text = ("Connecting the {} SELECTED "
                                    "node(s).".format(len(sel_nodes)))
            self._fill_dia_params()
            return

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
        self._fill_dia_params()

    def _fill_dia_params(self):
        """The diameter-source list for the currently selected type (or
        the first SELECTED node): the outlet connector (when the family
        has one) plus every numeric parameter with a sample value."""
        self._dia_opts = []
        self.CmbDiaParam.Items.Clear()
        if sel_nodes:
            probe = sel_nodes[0]
        else:
            i = self.CmbType.SelectedIndex
            if i < 0 or i >= len(self._shown):
                return
            insts = self._shown[i]["insts"]
            probe = insts[0] if insts else None
        if probe is None:
            return
        _o, conn_dia = fixture_outlet_info(probe)
        if conn_dia:
            self._dia_opts.append(("conn", None))
            self.CmbDiaParam.Items.Add("{}   ({:.0f} mm)".format(
                CONNECTOR_DIA, conn_dia))
        for nm, sample in node_dia_param_options(probe):
            self._dia_opts.append(("param", nm))
            self.CmbDiaParam.Items.Add(
                "{}   ({:.0f} mm)".format(nm, sample)
                if sample is not None else nm)
        # preselect: remembered choice, else the connector, else the
        # first DIA-style name
        want = settings.get("nodes_dia_param")
        idx = 0
        names = [(m, n) for m, n in self._dia_opts]
        if want == CONNECTOR_DIA and ("conn", None) in names:
            idx = names.index(("conn", None))
        elif want and ("param", want) in names:
            idx = names.index(("param", want))
        elif ("conn", None) not in names:
            for dn in DIA_PARAM_NAMES:
                if ("param", dn) in names:
                    idx = names.index(("param", dn))
                    break
        if self.CmbDiaParam.Items.Count:
            self.CmbDiaParam.SelectedIndex = idx

    def on_type_changed(self, sender, args):
        if self._loading:
            return
        self._fill_dia_params()

    def on_auto(self, sender, args):
        try:
            self.TxtInvert.IsEnabled = not self.ChkAuto.IsChecked
        except Exception:
            pass

    def on_regrade(self, sender, args):
        try:
            on = bool(self.ChkRegrade.IsChecked)
            self.TxtMainSlope.IsEnabled = on
            self.RadMainUpper.IsEnabled = on
            self.RadMainLower.IsEnabled = on
        except Exception:
            pass

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
        if not sel_nodes and (i < 0 or i >= len(self._shown)):
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
        main_slope = None
        if self.ChkRegrade.IsChecked:
            try:
                main_slope = float(self.TxtMainSlope.Text)
                if main_slope <= 0:
                    raise ValueError()
            except Exception:
                self.StatusText.Text = ("The main's gradient must be a "
                                        "positive number (the n of 1:n).")
                return
        di = self.CmbDiaParam.SelectedIndex
        dia_mode, dia_param = ("auto", None)
        if 0 <= di < len(self._dia_opts):
            dia_mode, dia_param = self._dia_opts[di]
        fixed_dia = None
        txt = self.TxtFixedDia.Text.strip()
        if txt:
            try:
                fixed_dia = float(txt)
                if fixed_dia <= 0:
                    raise ValueError()
            except Exception:
                self.StatusText.Text = ("Fixed dia must be a positive "
                                        "number of mm (or left empty).")
                return
        main_dia = None
        txt = self.TxtMainDia.Text.strip()
        if txt:
            try:
                main_dia = float(txt)
                if main_dia <= 0:
                    raise ValueError()
            except Exception:
                self.StatusText.Text = ("The main's diameter must be a "
                                        "positive number of mm.")
                return
            if abs(main_dia - ft2mm(main_dia_ft)) < 0.5:
                main_dia = None          # unchanged - leave the main be
        invert_m = None
        if not self.ChkAuto.IsChecked:
            try:
                invert_m = float(self.TxtInvert.Text)
            except Exception:
                self.StatusText.Text = ("Type the upstream invert level "
                                        "in metres, or tick 'keep it "
                                        "where it currently is'.")
                return
        pt_i = self.CmbPipeType.SelectedIndex
        st_i = self.CmbSysType.SelectedIndex
        self.result = {"row": (None if sel_nodes else self._shown[i]),
                       "slope": slope,
                       "dia_mode": dia_mode, "dia_param": dia_param,
                       "fixed_dia": fixed_dia, "main_dia": main_dia,
                       "invert_m": invert_m,
                       "pipe_type": (None if pt_i <= 0
                                     else _pt_opts[pt_i - 1]),
                       "sys_type": (None if st_i <= 0
                                    else _st_opts[st_i - 1]),
                       "main_slope": main_slope,
                       "main_keep": "high" if self.RadMainUpper.IsChecked
                       else "low"}
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
slope = win.result["slope"]
dia_mode = win.result["dia_mode"]
dia_param = win.result["dia_param"]
if row is not None:
    # family-type mode: everything unconnected of that type
    label, insts, todo = row["label"], row["insts"], row["todo"]
    settings["nodes_label"] = label
else:
    # selection mode: exactly the picked nodes
    label = "{} selected node(s)".format(len(sel_nodes))
    insts = list(sel_nodes)
    todo = [n for n in insts if not _is_done(n)]
settings["nodes_slope"] = slope
settings["nodes_dia_param"] = (CONNECTOR_DIA if dia_mode == "conn"
                               else (dia_param or ""))
settings["nodes_main_keep"] = win.result["main_keep"]
settings["nodes_pipe_type"] = (win.result["pipe_type"][0]
                               if win.result["pipe_type"] else "")
settings["nodes_sys_type"] = (win.result["sys_type"][0]
                              if win.result["sys_type"] else "")
try:
    save_settings(settings)
except Exception:
    pass

pt_id = win.result["pipe_type"][1] if win.result["pipe_type"] else None
st_id = win.result["sys_type"][1] if win.result["sys_type"] else None
log("**{}**: {} in scope, **{}** unconnected to pipe up; gradient "
    "**1:{:g}**; dia from **{}**.".format(
        label, len(insts), len(todo), slope,
        "fixed {:.0f} mm".format(win.result["fixed_dia"])
        if win.result["fixed_dia"]
        else (CONNECTOR_DIA if dia_mode == "conn"
              else (dia_param or "auto (connector, then DIA)"))))
log("Branch pipe type: **{}**; system: **{}**; upstream invert: {}."
    .format(win.result["pipe_type"][0] if win.result["pipe_type"]
            else "same as the main",
            win.result["sys_type"][0] if win.result["sys_type"]
            else "same as the main",
            "keep as lies" if win.result["invert_m"] is None
            else "**{:.3f} m** (fixed)".format(win.result["invert_m"])))
if not todo:
    log("Everything in scope is already connected - nothing to do.")
    log.close()
    forms.alert("Every chosen node ({}) is already "
                "connected.".format(label), exitscript=True)

# ---------------------------------------------------------------------------
# 3. Model everything in ONE go: a TransactionGroup wraps the optional
#    main re-grade and every branch, assimilated into a single undo
#    step. Tees split the main, so track the pieces and tie each node
#    into the one spanning it.
# ---------------------------------------------------------------------------
from Autodesk.Revit.DB import TransactionGroup

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

# the type name (e.g. STORMWATER - IN) seeds a NEW collector's name;
# in selection mode the first picked node provides it
net_name = row["type"] if row is not None else safe_name(todo[0].Symbol)
base = os.path.join(get_export_folder(doc), "project_files")

# THE COLLECTOR'S IDENTITY: reuse the name already stamped on the
# picked run, else allocate the next free '<type> - C<n>' - type names
# carry no network number any more, the collector pipe does.
collector = network_value(main)
if not collector:
    existing = set()
    try:
        existing.update(collect_by_network(doc).keys())
    except Exception:
        pass
    try:
        for _r0 in load_branches(base)["branches"]:
            if _r0.get("collector"):
                existing.add(_r0["collector"])
    except Exception:
        pass
    collector = next_collector_name(existing, net_name)
log("Collector network: **{}** (carried by every element's "
    "pyMEP_Network parameter).".format(collector))

tg = TransactionGroup(doc, "Inflow Drop Pipe to Collector")
tg.Start()
try:
    # the network parameter rides on everything this run creates
    if not ensure_network_param(doc):
        log("(pyMEP_Network parameter not bound - stamping skipped)")
    if win.result["main_dia"] is not None:
        try:
            set_pipe_dia(doc, main, win.result["main_dia"], log=log)
        except Exception as ex:
            log("! Couldn't resize the main ({}) - keeping its current "
                "size.".format(ex))
    if win.result["main_slope"] is not None:
        try:
            regrade_main(doc, main, win.result["main_slope"],
                         keep=win.result["main_keep"], log=log)
        except Exception as ex:
            log("! Couldn't re-grade the main ({}) - branches meet it "
                "as it lies.".format(ex))
    # the line the tracker uses to find the main's pieces later - taken
    # AFTER any resize/re-grade so it matches what gets built against
    a, b, _t0, _s0, _l0, _d0 = main_pipe_info(main)

    for node in todo:
        log("**{}** (id {}):".format(safe_name(node), node.Id))
        try:
            o_xyz, conn_dia = fixture_outlet_info(node)
            if o_xyz is None:
                failed += 1
                log("  ! no outlet point - skipped")
                continue
            if win.result["fixed_dia"]:
                node_dia = win.result["fixed_dia"]
            elif dia_mode == "conn":
                node_dia = conn_dia
            elif dia_mode == "param":
                node_dia = node_dia_mm(node, dia_param)
            else:
                node_dia = node_dia_mm(node)
            dia_mm = node_dia or 100.0
            if not node_dia:
                log("  ! no size on '{}' - using 100 mm".format(
                    dia_param or "connector/DIA"))
            seg = _nearest_seg(o_xyz)
            r = connect_fixture_to_main(doc, node, seg, slope, dia_mm,
                                        invert_m=win.result["invert_m"],
                                        log=log, pipe_type_id=pt_id,
                                        system_type_id=st_id,
                                        use_rotation=True)
            done += 1
            fitting_notes += r["fitting_misses"]
            if r.get("new_main_segment") is not None:
                main_segs.append(r["new_main_segment"])
            # the collector name rides on the node, its branch and every
            # fitting Revit slipped in - the dashboard groups by it
            try:
                els = [node, r.get("down"), r.get("sloped"),
                       r.get("elbow"), r.get("tee")]
                els += with_connected_fittings([r.get("down"),
                                                r.get("sloped")])
                stamp_network(doc, els, collector)
            except Exception:
                pass
            # track it so Update Nodes can adapt when the node moves
            try:
                rec = make_record(
                    node, r, slope, dia_mm, win.result["invert_m"],
                    win.result["pipe_type"][0]
                    if win.result["pipe_type"] else "",
                    win.result["sys_type"][0]
                    if win.result["sys_type"] else "",
                    (a, b),
                    row["label"] if row is not None else _node_label(node))
                rec["collector"] = collector
                add_branch(base, rec)
            except Exception as ex:
                log("  (branch not tracked: {})".format(ex))
        except Exception as ex:
            failed += 1
            import traceback
            log(traceback.format_exc())
            log("  ! not connected: {}".format(ex))
    # every piece of the (split) collector belongs to the network too
    try:
        stamp_network(doc, main_segs, collector)
    except Exception:
        pass
    # one go: everything lands as a SINGLE undo step
    tg.Assimilate()
except Exception:
    try:
        tg.RollBack()
    except Exception:
        pass
    raise

log("#### Summary")
log("- Nodes piped into the main: **{}** of {} in one go (tee "
    "junctions; the main is now {} segment(s))".format(
        done, len(todo), len(main_segs)))
if fitting_notes:
    log("- Fitting notes: **{}** (see above)".format(fitting_notes))
if failed:
    log("- Failed / skipped: **{}**".format(failed))
log.close()
