# -*- coding: utf-8 -*-
"""Lines to Pipes - turn the model lines you drew into a graded pipe
network.

Filter the lines by line style and workset, give a pipe type, size,
gradient 1:n and the invert level at the outfall, then click a line
near its outfall end. Every line becomes a pipe falling toward that
point at the gradient; lines that cross or end on another line are teed
into it at the level of the run they meet, and lines meeting end to end
are elbowed.
"""

__title__ = "Lines to\nPipes"
__author__ = "Glent Group"

import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

import os

from pyrevit import revit, forms, script

from pymep_config import load_settings, save_settings
from pymep_log import Logger
from pymep_connect_fixtures import (
    list_pipe_type_options, list_system_type_options,
)
from pymep_lines_to_pipes import (
    MM_PER_FT, build_network_pipes, collect_lines, line_style_options,
    solve, workset_options,
)

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
from Autodesk.Revit.UI.Selection import ObjectType

output = script.get_output()
log = Logger(output, "LinesToPipes")
doc = revit.doc
uidoc = revit.uidoc

log("### Lines to Pipes")

XAML_PATH = os.path.join(
    os.path.dirname(os.path.abspath(sys.modules["pymep_config"].__file__)),
    "pymep_lines_to_pipes.xaml")

ANY_STYLE = "(any line style)"
ANY_WORKSET = "(any workset)"

styles = line_style_options(doc)
worksets = workset_options(doc)
_pt_opts = list_pipe_type_options(doc)
_st_opts = list_system_type_options(doc)
if not _pt_opts or not _st_opts:
    forms.alert("This model needs at least one pipe type and one piping "
                "system type.", exitscript=True)

settings = load_settings()


class LinesWindow(forms.WPFWindow):

    def __init__(self):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self.result = None
        self._loading = True

        self.CmbStyle.Items.Clear()
        self.CmbStyle.Items.Add(ANY_STYLE)
        for nm in styles:
            self.CmbStyle.Items.Add(nm)
        self.CmbWorkset.Items.Clear()
        self.CmbWorkset.Items.Add(ANY_WORKSET)
        for nm in worksets:
            self.CmbWorkset.Items.Add(nm)
        for combo, opts, key in ((self.CmbPipeType, _pt_opts,
                                  "lines_pipe_type"),
                                 (self.CmbSysType, _st_opts,
                                  "lines_sys_type")):
            combo.Items.Clear()
            for nm, _eid in opts:
                combo.Items.Add(nm)
            combo.SelectedIndex = 0
            want = settings.get(key)
            for i, (nm, _eid) in enumerate(opts):
                if nm == want:
                    combo.SelectedIndex = i
                    break

        self.CmbStyle.SelectedItem = settings.get("lines_style") \
            if settings.get("lines_style") in styles else ANY_STYLE
        self.CmbWorkset.SelectedItem = settings.get("lines_workset") \
            if settings.get("lines_workset") in worksets else ANY_WORKSET
        self.TxtDia.Text = "{:g}".format(
            float(settings.get("lines_dia_mm", 150.0)))
        self.TxtSlope.Text = "{:g}".format(
            float(settings.get("lines_slope", 200.0)))
        self.TxtInvert.Text = "{:g}".format(
            float(settings.get("lines_invert_m", 0.0)))
        self.TxtInfo.Text = "Model lines -> graded pipes"
        self._loading = False
        self._refresh_count()

    def _filters(self):
        style = self.CmbStyle.SelectedItem
        ws = self.CmbWorkset.SelectedItem
        return (None if style in (None, ANY_STYLE) else str(style),
                None if ws in (None, ANY_WORKSET) else str(ws))

    def _refresh_count(self):
        style, ws = self._filters()
        n = len(collect_lines(doc, style, ws))
        self.TxtCount.Text = ("{} straight model line(s) match - they "
                              "become the network.".format(n))

    def on_filter_changed(self, sender, args):
        if self._loading:
            return
        self._refresh_count()

    def on_create(self, sender, args):
        try:
            dia = float(self.TxtDia.Text)
            slope = float(self.TxtSlope.Text)
            invert = float(self.TxtInvert.Text)
            if dia <= 0 or slope <= 0:
                raise ValueError()
        except Exception:
            self.StatusText.Text = ("Diameter and gradient must be "
                                    "positive numbers; the invert is in "
                                    "metres.")
            return
        style, ws = self._filters()
        if not collect_lines(doc, style, ws):
            self.StatusText.Text = "No straight model lines match."
            return
        i_pt = self.CmbPipeType.SelectedIndex
        i_st = self.CmbSysType.SelectedIndex
        self.result = {"style": style, "workset": ws, "dia_mm": dia,
                       "slope_n": slope, "invert_m": invert,
                       "pipe_type": _pt_opts[i_pt],
                       "sys_type": _st_opts[i_st]}
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


win = LinesWindow()
win.ShowDialog()
if win.result is None:
    log("Cancelled - nothing changed.")
    log.close()
    script.exit()

opt = win.result
settings["lines_style"] = opt["style"] or ANY_STYLE
settings["lines_workset"] = opt["workset"] or ANY_WORKSET
settings["lines_dia_mm"] = opt["dia_mm"]
settings["lines_slope"] = opt["slope_n"]
settings["lines_invert_m"] = opt["invert_m"]
settings["lines_pipe_type"] = opt["pipe_type"][0]
settings["lines_sys_type"] = opt["sys_type"][0]
try:
    save_settings(settings)
except Exception:
    pass

lines = collect_lines(doc, opt["style"], opt["workset"])
log("**{}** line(s), dia **{:.0f} mm**, gradient **1:{:g}**, outfall "
    "invert **{:.3f} m**".format(len(lines), opt["dia_mm"],
                                 opt["slope_n"], opt["invert_m"]))

# ---------------------------------------------------------------------------
# the outfall pick
# ---------------------------------------------------------------------------
try:
    ref = uidoc.Selection.PickObject(
        ObjectType.Element,
        "Click a line NEAR ITS OUTFALL END - pipes fall toward it")
except Exception:
    log("Pick cancelled - nothing changed.")
    log.close()
    script.exit()

pick_el = doc.GetElement(ref.ElementId)
gp = ref.GlobalPoint
if gp is not None:
    pick_mm = (gp.X * MM_PER_FT, gp.Y * MM_PER_FT)
else:
    # no point on the reference - fall back to the picked line's start
    crv = pick_el.GeometryCurve
    p = crv.GetEndPoint(0)
    pick_mm = (p.X * MM_PER_FT, p.Y * MM_PER_FT)

# ---------------------------------------------------------------------------
# solve + build
# ---------------------------------------------------------------------------
sol = solve([(a, b) for _el, a, b in lines], pick_mm, opt["slope_n"])

for s in sol["skipped"]:
    log("- {}".format(s))
log("**{}** run(s), **{}** tee(s), **{}** elbow(s) to build.".format(
    len(sol["runs"]), len(sol["tees"]), len(sol["elbows"])))
if not sol["runs"]:
    forms.alert("Nothing to build - see the report for why every line "
                "was skipped.", exitscript=True)

try:
    res = build_network_pipes(doc, sol, opt["sys_type"][1],
                              opt["pipe_type"][1], opt["dia_mm"],
                              opt["slope_n"], opt["invert_m"], log=log)
except Exception as ex:
    import traceback
    log(traceback.format_exc())
    forms.alert("Nothing was built - the model is unchanged.\n\n"
                "{}".format(ex), title="Lines to Pipes", exitscript=True)

log("#### Summary")
log("- Pipes created: **{}**".format(res["pipes"]))
log("- Fittings placed: **{}**".format(res["fittings"]))
if res["failed"]:
    log("- Failed: **{}**".format(res["failed"]))
for n in res["notes"]:
    log("- {}".format(n))

forms.alert(
    "Built {} pipe(s) and {} fitting(s) from {} line(s).{}".format(
        res["pipes"], res["fittings"], len(lines),
        "\n{} failed - see the report.".format(res["failed"])
        if res["failed"] else ""),
    title="Lines to Pipes")
log.close()
