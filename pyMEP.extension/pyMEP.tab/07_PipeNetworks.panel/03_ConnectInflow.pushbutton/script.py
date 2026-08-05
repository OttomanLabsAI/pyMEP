# -*- coding: utf-8 -*-
"""Connect Inflow to Collector - pipe nodes into a collector run.

THE COLLECTOR finds itself: a candidate pipe DIRECTLY UNDER the node
takes the drop straight down; otherwise the single ray along the
node's drawn ARROW (the wire, 180 degrees off the API facing - no
other direction is ever tried) picks the first candidate pipe it
meets, plan-nearest as the last resort. Pre-select ONE
pipe together with the nodes to FORCE that pipe as every branch's
collector instead - that also unlocks the main resize / re-grade
options.

WHICH nodes, your choice:
  - SELECT them first: exactly those nodes get connected, mixed types
    welcome;
  - or select NOTHING: the dialog opens straight away and its
    category > family > type cascade (or search) plus the NODE
    WORKSET filter choose every placed, still-unconnected node of
    that type on that workset.

Per node: a drop pipe from its outlet connector (diameter taken from
THE NODE's own connector, snapped to the main type's routing sizes), a
bend at its base, a run falling at 1:n to meet the main as it lies, and
a TEE JUNCTION into the main. Nodes whose outlet is already connected
are left alone. Branches take the main's pipe type, system type and
level; each tee splits the main and later nodes tie into whichever
piece spans their position.
"""

__title__  = "Connect Inflow\nto Collector"
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
    node_directions, node_aim_directions, ray_hits_main,
)
from pymep_lines_to_pipes import (aim_pick, _workset_name,
                                  workset_options)
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

log("### Connect Inflow to Collector")

# ---------------------------------------------------------------------------
# 1. The NODES (and optionally ONE forced collector pipe), straight
#    from selection. No pipe selected = AIM MODE: each node finds its
#    collector along its own rotation, nothing to pick.
# ---------------------------------------------------------------------------
main = None
sel_nodes = []
for eid in uidoc.Selection.GetElementIds():
    el = doc.GetElement(eid)
    if isinstance(el, Pipe):
        if main is None:
            main = el
        else:
            forms.alert("Select just ONE pipe (to force it as the "
                        "collector), or none - each node then finds "
                        "its collector along its own rotation.",
                        exitscript=True)
    elif isinstance(el, FamilyInstance) and _has_point(el):
        sel_nodes.append(el)

# nothing pre-selected -> straight to the dialog: its family cascade
# + Node workset filter choose the nodes, no picking
if sel_nodes:
    log("Nodes chosen BY SELECTION: **{}**.".format(len(sel_nodes)))

a = b = None
main_dia_ft = None
cand = []                # aim mode: [(pipe, (ax, ay), (bx, by)), ...]
cand_ws = []             # the workset name of each candidate
ANY_PIPES_WS = "(any workset)"
if main is not None:
    try:
        a, b, _tid, _sid, _lid, main_dia_ft = main_pipe_info(main)
    except Exception as ex:
        log("{}".format(ex))
        log.close()
        forms.alert(str(ex), exitscript=True)
    log("Collector FORCED by selection: '{}'.".format(safe_name(main)))
else:
    # every readable, non-vertical pipe is a candidate for the rays -
    # a drop pipe has no plan length and cannot catch one, and tee
    # debris (stubs under ~450 mm) must not catch under/ray picks
    from Autodesk.Revit.DB import FilteredElementCollector
    for _p in FilteredElementCollector(doc).OfClass(Pipe):
        try:
            ca, cb, _t1, _s1, _l1, _d1 = main_pipe_info(_p)
        except Exception:
            continue
        if ((ca[0] - cb[0]) ** 2 + (ca[1] - cb[1]) ** 2) ** 0.5 < 1.5:
            continue
        cand.append((_p, (ca[0], ca[1]), (cb[0], cb[1])))
        cand_ws.append(_workset_name(doc, _p) or "")
    if not cand:
        forms.alert("No pipes in the model to aim at - draw the "
                    "collector runs first (or pre-select one pipe to "
                    "force it).", exitscript=True)
    log("AIM MODE: **{}** candidate pipe(s) - pick the collector "
        "pipes' WORKSET in the dialog; each node tees into the first "
        "candidate its rotation meets.".format(len(cand)))

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


_cur_grad = main_gradient(a, b) if main is not None else None
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
        self.TxtSlope.Text = "{:g}".format(
            float(settings.get("nodes_slope", 100.0)))
        self.CmbPipesWorkset.Items.Clear()
        self.CmbPipesWorkset.Items.Add(ANY_PIPES_WS)
        for w in sorted(set(w for w in cand_ws if w),
                        key=lambda x: x.lower()):
            self.CmbPipesWorkset.Items.Add(w)
        self.CmbPipesWorkset.SelectedIndex = 0
        want_ws = settings.get("nodes_pipes_workset") or ""
        for i in range(self.CmbPipesWorkset.Items.Count):
            if str(self.CmbPipesWorkset.Items[i]) == want_ws:
                self.CmbPipesWorkset.SelectedIndex = i
                break
        if main is not None:
            self.CmbPipesWorkset.IsEnabled = False
        self.CmbNodeWorkset.Items.Clear()
        self.CmbNodeWorkset.Items.Add(ANY_PIPES_WS)
        for w in workset_options(doc):
            self.CmbNodeWorkset.Items.Add(w)
        self.CmbNodeWorkset.SelectedIndex = 0
        want_nws = settings.get("nodes_node_workset") or ""
        for i in range(self.CmbNodeWorkset.Items.Count):
            if str(self.CmbNodeWorkset.Items[i]) == want_nws:
                self.CmbNodeWorkset.SelectedIndex = i
                break
        if sel_nodes:
            # nodes chosen by hand - the workset filter is moot
            self.CmbNodeWorkset.IsEnabled = False
        if main is not None:
            self.TxtMainDia.Text = "{:.0f}".format(ft2mm(main_dia_ft))
            self.TxtInfo.Text = ("Main: '{}' ({:.0f} mm), tee "
                                 "junctions".format(safe_name(main),
                                                    ft2mm(main_dia_ft)))
            if _cur_grad is not None:
                self.TxtMainSlope.Text = "{:.0f}".format(_cur_grad)
                self.TxtMainHint.Text = (
                    "The main currently falls at about 1:{:.0f}. Tick "
                    "to re-grade it BEFORE the branches are drawn - "
                    "the chosen end stays put, the other "
                    "moves.".format(_cur_grad))
            else:
                self.TxtMainSlope.Text = "{:g}".format(
                    float(settings.get("nodes_slope", 100.0)))
                self.TxtMainHint.Text = (
                    "The main is currently LEVEL. Tick to re-grade it "
                    "before the branches are drawn - the chosen end "
                    "stays put, the other moves.")
        else:
            # aim mode - the main options only mean something when ONE
            # collector is forced by selection
            self.TxtInfo.Text = ("No pipe selected - each node tees "
                                 "into the pipe its ROTATION finds")
            self.TxtMainDia.Text = ""
            for c in (self.TxtMainDia, self.ChkRegrade,
                      self.TxtMainSlope, self.RadMainUpper,
                      self.RadMainLower):
                c.IsEnabled = False
            self.TxtMainHint.Text = (
                "Resize / re-grade apply only when ONE collector pipe "
                "is pre-selected together with the nodes.")
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
        pipes_ws = str(self.CmbPipesWorkset.SelectedItem
                       or ANY_PIPES_WS)
        node_ws = str(self.CmbNodeWorkset.SelectedItem
                      or ANY_PIPES_WS)
        self.result = {"row": (None if sel_nodes else self._shown[i]),
                       "pipes_workset": ("" if pipes_ws == ANY_PIPES_WS
                                         else pipes_ws),
                       "node_workset": ("" if node_ws == ANY_PIPES_WS
                                        else node_ws),
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
    # family-type mode: everything unconnected of that type, cut down
    # to the picked NODE WORKSET
    label, insts, todo = row["label"], row["insts"], row["todo"]
    settings["nodes_label"] = label
    node_ws = win.result.get("node_workset") or ""
    if node_ws:
        todo = [n for n in todo
                if _workset_name(doc, n) == node_ws]
        label = "{} [{}]".format(label, node_ws)
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
settings["nodes_pipes_workset"] = win.result.get("pipes_workset") or ""
settings["nodes_node_workset"] = win.result.get("node_workset") or ""
try:
    save_settings(settings)
except Exception:
    pass

# aim mode: only the chosen workset's pipes can catch a ray - no
# more teeing into random pipes from other systems
if main is None:
    _ws_pick = win.result.get("pipes_workset") or ""
    if _ws_pick:
        cand = [c for c, w in zip(list(cand), list(cand_ws))
                if w == _ws_pick]
        if not cand:
            forms.alert("No candidate pipes on workset '{}' - nothing "
                        "to aim at.".format(_ws_pick), exitscript=True)
        log("Collector pipes filtered to workset **{}**: **{}** "
            "candidate(s).".format(_ws_pick, len(cand)))
    else:
        log("Collector pipes: ANY workset ({} candidate(s)).".format(
            len(cand)))

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

main_segs = [main] if main is not None else []


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


def _aim_target(node, outlet_xyz):
    """(pipe, how) - a candidate DIRECTLY UNDER the node (within
    ~300 mm in plan) wins outright: the drop goes straight down into
    it. Otherwise the single ray along the node's drawn ARROW picks
    the first candidate it meets (no other direction is tried - the
    hand-axis ray built runs 90 degrees off the arrow). NO nearest
    fallback: a node whose arrow hits nothing is SKIPPED and reported
    - blind-nearest is how mis-rotated nodes all chained into one
    stray branch and drew a star of criss-crossing runs."""
    return aim_pick((outlet_xyz[0], outlet_xyz[1]),
                    node_aim_directions(node), cand,
                    ray_hits_main, plan_dist_to_segment,
                    under=1.0, fallback=False)


def _refresh_cand(target, new_seg):
    """Keep the candidate lines honest after a tee: the aimed pipe was
    re-curved and the split-off half is a fresh element."""
    for _i2 in range(len(cand)):
        if cand[_i2][0].Id == target.Id:
            try:
                na, nb, _x1, _x2, _x3, _x4 = main_pipe_info(target)
                cand[_i2] = (target, (na[0], na[1]), (nb[0], nb[1]))
            except Exception:
                pass
            break
    if new_seg is not None:
        try:
            na, nb, _x1, _x2, _x3, _x4 = main_pipe_info(new_seg)
            cand.append((new_seg, (na[0], na[1]), (nb[0], nb[1])))
        except Exception:
            pass


done = 0
failed = 0
fitting_notes = 0
aim_missed = []

# the type name (e.g. STORMWATER - IN) seeds a NEW collector's name;
# in selection mode the first picked node provides it
net_name = row["type"] if row is not None else safe_name(todo[0].Symbol)
base = os.path.join(get_export_folder(doc), "project_files")

# names already in use - every new collector picks the next free one
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

# THE COLLECTOR'S IDENTITY: reuse the name already stamped on the run,
# else allocate the next free '<type> - C<n>' - type names carry no
# network number any more, the collector pipe does. In aim mode each
# AIMED pipe gets (or keeps) its own identity.
_collectors = {}


def _collector_for(target):
    key = str(target.Id)
    if key in _collectors:
        return _collectors[key]
    name = network_value(target)
    if not name:
        name = next_collector_name(existing, net_name)
    existing.add(name)
    _collectors[key] = name
    return name


if main is not None:
    collector = _collector_for(main)
    log("Collector network: **{}** (carried by every element's "
        "pyMEP_Network parameter).".format(collector))
else:
    collector = None
    log("Collector networks are assigned PER AIMED PIPE - each pipe "
        "keeps its stamped pyMEP_Network name or takes the next free "
        "'{} - C<n>'.".format(net_name))

tg = TransactionGroup(doc, "Connect Inflow to Collector")
tg.Start()
try:
    # the network parameter rides on everything this run creates
    if not ensure_network_param(doc):
        log("(pyMEP_Network parameter not bound - stamping skipped)")
    if main is not None and win.result["main_dia"] is not None:
        try:
            set_pipe_dia(doc, main, win.result["main_dia"], log=log)
        except Exception as ex:
            log("! Couldn't resize the main ({}) - keeping its current "
                "size.".format(ex))
    if main is not None and win.result["main_slope"] is not None:
        try:
            regrade_main(doc, main, win.result["main_slope"],
                         keep=win.result["main_keep"], log=log)
        except Exception as ex:
            log("! Couldn't re-grade the main ({}) - branches meet it "
                "as it lies.".format(ex))
    # the line the tracker uses to find the main's pieces later - taken
    # AFTER any resize/re-grade so it matches what gets built against
    if main is not None:
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
            if main is not None:
                seg = _nearest_seg(o_xyz)
                rec_line = (a, b)
                col = collector
            else:
                seg, how = _aim_target(node, o_xyz)
                if seg is None:
                    failed += 1
                    if how == "miss":
                        aim_missed.append(safe_name(node))
                        log("  ! its ARROW hits no collector pipe - "
                            "SKIPPED (rotate the node to face the "
                            "collector, or pre-select a main pipe to "
                            "force it)")
                    else:
                        log("  ! no pipe to aim at - skipped")
                    continue
                if how == "under":
                    log("  directly over the pipe - dropped straight "
                        "in")
                ta, tb, _t2, _s2, _l2, _d2 = main_pipe_info(seg)
                rec_line = (ta, tb)
                col = _collector_for(seg)
                log("  -> '{}' ({})".format(safe_name(seg), col))
            r = connect_fixture_to_main(doc, node, seg, slope, dia_mm,
                                        invert_m=win.result["invert_m"],
                                        log=log, pipe_type_id=pt_id,
                                        system_type_id=st_id,
                                        use_rotation=True)
            done += 1
            fitting_notes += r["fitting_misses"]
            if r.get("new_main_segment") is not None:
                main_segs.append(r["new_main_segment"])
            if main is None:
                # the tee re-curved the aimed pipe and may have split
                # off a fresh piece - keep the ray candidates honest,
                # and both pieces carry the collector name
                _refresh_cand(seg, r.get("new_main_segment"))
                if r.get("new_main_segment") is not None:
                    _collectors[str(r["new_main_segment"].Id)] = col
                try:
                    stamp_network(
                        doc,
                        [seg] + ([r["new_main_segment"]]
                                 if r.get("new_main_segment") is not None
                                 else []), col)
                except Exception:
                    pass
            # the collector name rides on the node, its branch and every
            # fitting Revit slipped in - the dashboard groups by it
            try:
                els = [node, r.get("down"), r.get("sloped"),
                       r.get("stub"), r.get("elbow"),
                       r.get("elbow2"), r.get("tee")]
                els += with_connected_fittings(
                    [r.get("down"), r.get("sloped"), r.get("stub")])
                stamp_network(doc, els, col)
            except Exception:
                pass
            # track it so Sync Input Nodes can adapt when the node moves
            try:
                rec = make_record(
                    node, r, slope, dia_mm, win.result["invert_m"],
                    win.result["pipe_type"][0]
                    if win.result["pipe_type"] else "",
                    win.result["sys_type"][0]
                    if win.result["sys_type"] else "",
                    rec_line,
                    row["label"] if row is not None else _node_label(node))
                rec["collector"] = col
                add_branch(base, rec)
            except Exception as ex:
                log("  (branch not tracked: {})".format(ex))
        except Exception as ex:
            failed += 1
            import traceback
            log(traceback.format_exc())
            log("  ! not connected: {}".format(ex))
    # every piece of the (split) forced collector belongs to the
    # network too (aim mode stamps its pieces per node, as it goes)
    if main is not None:
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
if main is not None:
    log("- Nodes piped into the main: **{}** of {} in one go (tee "
        "junctions; the main is now {} segment(s))".format(
            done, len(todo), len(main_segs)))
else:
    log("- Nodes piped: **{}** of {} in one go, each into the pipe "
        "its rotation found ({} collector network(s) touched)".format(
            done, len(todo), len(set(_collectors.values()))))
if fitting_notes:
    log("- Fitting notes: **{}** (see above)".format(fitting_notes))
if aim_missed:
    log("- Nodes whose ARROW hits no collector pipe (skipped, NOT "
        "connected): **{}** - {}. Rotate them to face the collector "
        "(or pre-select a main pipe to force it) and run again."
        .format(len(aim_missed), ", ".join(aim_missed[:12])
                + (" ..." if len(aim_missed) > 12 else "")))
if failed:
    log("- Failed / skipped: **{}**".format(failed))
log.close()
