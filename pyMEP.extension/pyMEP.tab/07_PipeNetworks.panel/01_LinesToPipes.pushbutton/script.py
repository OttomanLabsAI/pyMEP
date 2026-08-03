# -*- coding: utf-8 -*-
"""Lines to Pipes - turn the model lines you drew into a graded pipe
network.

Every line is graded at ITS OWN slope, read from its line style name:
'Pipe 1-80' runs at 1:80, 'Pipe 1-150' at 1:150. A style whose name
carries no number uses the dialog's default gradient, and a 'Slope
Custom' style opens a clickable plan of the network asking for each
custom line's gradient. Lines crossing or ending on another line are
teed in at the level of the run they meet; end-to-end joints get
elbows.
"""

__title__ = "Lines to\nPipes"
__author__ = "Glent Group"

import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

import os

from pyrevit import revit, forms, script

from pymep_config import get_export_folder, load_settings, save_settings
from pymep_log import Logger
from pymep_connect_fixtures import (
    list_pipe_type_options, list_system_type_options,
)
from pymep_lines_to_pipes import (
    MM_PER_FT, _workset_name, build_network_pipes, collect_lines,
    find_invert_markers, fit_plan, line_style_options,
    load_lines_record, parse_style_slope, save_lines_record, solve,
    workset_options,
)
from pymep_lines_custom_ui import ask_custom_slopes
from pymep_pipesizes import existing_segment_sizes_mm, list_pipe_segments

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
from Autodesk.Revit.UI.Selection import ObjectType

output = script.get_output()
log = Logger(output, "LinesToPipes")
doc = revit.doc
uidoc = revit.uidoc

log("### Lines to Pipes")

_LIB_DIR = os.path.dirname(
    os.path.abspath(sys.modules["pymep_config"].__file__))
XAML_PATH = os.path.join(_LIB_DIR, "pymep_lines_to_pipes.xaml")
CUSTOM_XAML_PATH = os.path.join(_LIB_DIR, "pymep_lines_custom.xaml")

ANY_STYLE = "(any line style)"
PREFIX_STYLES = "(styles starting with the prefix)"
# the pre-v1.99 sentinel, still possible in stored settings
SLOPE_STYLES = "(all slope-named styles - Pipe 1-n / Custom)"
ANY_WORKSET = "(any workset)"

styles = line_style_options(doc)
worksets = workset_options(doc)
_pt_opts = list_pipe_type_options(doc)
_st_opts = list_system_type_options(doc)
_seg_opts = list_pipe_segments(doc)
if not _pt_opts or not _st_opts:
    forms.alert("This model needs at least one pipe type and one piping "
                "system type.", exitscript=True)

AUTO_TYPE = "(automatic)"
NO_SEGMENT = "(from pipe type)"

settings = load_settings()
markers = find_invert_markers(doc)


def slope_breakdown(rows):
    """(styled, custom, defaulted) counts over the matched lines -
    where each gradient will come from."""
    styled = custom = defaulted = 0
    for r in rows:
        got = parse_style_slope(r[3])
        if got == "custom":
            custom += 1
        elif got is not None:
            styled += 1
        else:
            defaulted += 1
    return styled, custom, defaulted


def gather_lines(style_sel, workset, prefix=None):
    """The lines the current filter selects. PREFIX_STYLES takes every
    line whose style name STARTS WITH the prefix ('Slope' matches
    'Slope 1-80', 'Slope Custom', ...) - the whole named network in one
    go; an empty prefix falls back to any style whose name parses."""
    rows = collect_lines(doc, None, workset)
    if style_sel == ANY_STYLE:
        return rows
    if style_sel in (PREFIX_STYLES, SLOPE_STYLES):
        p = (prefix or "").strip().lower()
        if p:
            return [r for r in rows
                    if (r[3] or "").lower().startswith(p)]
        return [r for r in rows if parse_style_slope(r[3]) is not None]
    return [r for r in rows if r[3] == style_sel]


class LinesWindow(forms.WPFWindow):

    def __init__(self):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self.result = None
        self._loading = True

        self.CmbStyle.Items.Clear()
        self.CmbStyle.Items.Add(PREFIX_STYLES)
        self.CmbStyle.Items.Add(ANY_STYLE)
        for nm in styles:
            self.CmbStyle.Items.Add(nm)
        self.CmbWorkset.Items.Clear()
        self.CmbWorkset.Items.Add(ANY_WORKSET)
        for nm in worksets:
            self.CmbWorkset.Items.Add(nm)
        self.CmbSysType.Items.Clear()
        for nm, _eid in _st_opts:
            self.CmbSysType.Items.Add(nm)
        self.CmbSysType.SelectedIndex = 0
        want = settings.get("lines_sys_type")
        for i, (nm, _eid) in enumerate(_st_opts):
            if nm == want:
                self.CmbSysType.SelectedIndex = i
                break

        self.CmbPipeType.Items.Clear()
        self.CmbPipeType.Items.Add(AUTO_TYPE)
        for nm, _eid in _pt_opts:
            self.CmbPipeType.Items.Add(nm)
        self.CmbPipeType.SelectedIndex = 0
        want = settings.get("lines_pipe_type")
        for i, (nm, _eid) in enumerate(_pt_opts):
            if nm == want:
                self.CmbPipeType.SelectedIndex = i + 1
                break

        self.CmbSegment.Items.Clear()
        self.CmbSegment.Items.Add(NO_SEGMENT)
        for nm, _seg in _seg_opts:
            self.CmbSegment.Items.Add(nm)
        self.CmbSegment.SelectedIndex = 0
        want = settings.get("lines_segment")
        for i, (nm, _seg) in enumerate(_seg_opts):
            if nm == want:
                self.CmbSegment.SelectedIndex = i + 1
                break

        self.TxtPrefix.Text = settings.get("lines_prefix", "Pipes") \
            or "Pipes"
        want = settings.get("lines_style")
        if want == SLOPE_STYLES:
            want = PREFIX_STYLES
        if want in styles or want in (ANY_STYLE, PREFIX_STYLES):
            self.CmbStyle.SelectedItem = want
        else:
            self.CmbStyle.SelectedItem = PREFIX_STYLES
        self.CmbWorkset.SelectedItem = settings.get("lines_workset") \
            if settings.get("lines_workset") in worksets else ANY_WORKSET

        # the model may have changed since last run - when the
        # remembered filters find nothing, relax them instead of
        # opening on a stale zero
        style, ws = self._filters()
        pfx = self._prefix()
        if not gather_lines(style, ws, pfx):
            if ws and gather_lines(style, None, pfx):
                self.CmbWorkset.SelectedItem = ANY_WORKSET
            elif style != PREFIX_STYLES and \
                    gather_lines(PREFIX_STYLES, ws, pfx):
                self.CmbStyle.SelectedItem = PREFIX_STYLES
            elif gather_lines(PREFIX_STYLES, None, pfx):
                self.CmbStyle.SelectedItem = PREFIX_STYLES
                self.CmbWorkset.SelectedItem = ANY_WORKSET
            elif gather_lines(ANY_STYLE, None, pfx):
                self.CmbStyle.SelectedItem = ANY_STYLE
                self.CmbWorkset.SelectedItem = ANY_WORKSET
        self._fill_sizes()
        self.CmbDia.Text = "{:g}".format(
            float(settings.get("lines_dia_mm", 150.0)))
        self.TxtSlope.Text = "{:g}".format(
            float(settings.get("lines_slope", 200.0)))
        if markers:
            self.TxtInvert.Text = "(from {} Invert Level node(s))".format(
                len(markers))
            self.TxtInvert.IsEnabled = False
        else:
            self.TxtInvert.Text = "{:g}".format(
                float(settings.get("lines_invert_m", 0.0)))
        self.TxtInfo.Text = "Model lines -> graded pipes"
        self._loading = False
        self._refresh_count()

    def _segment(self):
        """(name, PipeSegment) of the chosen segment, or None."""
        i = self.CmbSegment.SelectedIndex
        if i <= 0 or i > len(_seg_opts):
            return None
        return _seg_opts[i - 1]

    def _fill_sizes(self):
        """The diameter dropdown carries the chosen segment's
        catalogued sizes."""
        keep = self.CmbDia.Text
        self.CmbDia.Items.Clear()
        seg = self._segment()
        sizes = existing_segment_sizes_mm(seg[1]) if seg else []
        for mm in sizes:
            self.CmbDia.Items.Add("{:g}".format(mm))
        if keep:
            self.CmbDia.Text = keep

    def on_segment_changed(self, sender, args):
        if self._loading:
            return
        self._fill_sizes()

    def _filters(self):
        style = self.CmbStyle.SelectedItem
        ws = self.CmbWorkset.SelectedItem
        return (str(style) if style is not None else PREFIX_STYLES,
                None if ws in (None, ANY_WORKSET) else str(ws))

    def _prefix(self):
        try:
            return self.TxtPrefix.Text
        except Exception:
            return "Pipes"

    def _refresh_count(self):
        style, ws = self._filters()
        rows = gather_lines(style, ws, self._prefix())
        n = len(rows)
        if n:
            styled, custom, defaulted = slope_breakdown(rows)
            bits = []
            if styled:
                bits.append("{} sloped by their style name".format(styled))
            if custom:
                bits.append("{} 'Slope Custom' (asked after OK)"
                            .format(custom))
            if defaulted:
                bits.append("{} with NO slope in the style - the "
                            "default gradient applies".format(defaulted))
            self.TxtCount.Text = "{} line(s): {}.".format(
                n, "; ".join(bits))
            # the default gradient only matters when something uses it
            self.TxtSlope.IsEnabled = defaulted > 0
            self.LblSlope.Opacity = 1.0 if defaulted else 0.45
            self._defaulted = defaulted
            return
        self._defaulted = 0
        # nothing matches - say WHY, and WHERE the lines actually are
        if ws and gather_lines(style, None, self._prefix()):
            homes = sorted(set(
                _workset_name(doc, r[0]) or "(none)"
                for r in gather_lines(style, None, self._prefix())))
            self.TxtCount.Text = ("0 match - those lines sit on "
                                  "workset(s) {}, not '{}'.".format(
                                      ", ".join("'{}'".format(h)
                                                for h in homes), ws))
        elif style not in (ANY_STYLE, SLOPE_STYLES) and \
                collect_lines(doc, None, ws):
            self.TxtCount.Text = ("0 match - no straight model line "
                                  "carries the style '{}'.".format(style))
        else:
            self.TxtCount.Text = "0 straight model line(s) match."

    def on_filter_changed(self, sender, args):
        if self._loading:
            return
        self._refresh_count()

    def on_create(self, sender, args):
        try:
            dia = float(self.CmbDia.Text)
            invert = 0.0 if markers else float(self.TxtInvert.Text)
            if dia <= 0:
                raise ValueError()
        except Exception:
            self.StatusText.Text = ("The diameter must be a positive "
                                    "number; the invert is in metres.")
            return
        # the default gradient is only validated when a line will use it
        slope = float(settings.get("lines_slope", 200.0) or 200.0)
        if getattr(self, "_defaulted", 0):
            try:
                slope = float(self.TxtSlope.Text)
                if slope <= 0:
                    raise ValueError()
            except Exception:
                self.StatusText.Text = ("{} line(s) have no slope in "
                                        "their style name - give them a "
                                        "positive default gradient."
                                        .format(self._defaulted))
                return
        style, ws = self._filters()
        if not gather_lines(style, ws, self._prefix()):
            self.StatusText.Text = "No straight model lines match."
            return
        i_pt = self.CmbPipeType.SelectedIndex
        i_st = self.CmbSysType.SelectedIndex
        seg = self._segment()
        self.result = {"style": style, "workset": ws, "dia_mm": dia,
                       "prefix": self._prefix(),
                       "slope_n": slope, "invert_m": invert,
                       "pipe_type": (_pt_opts[i_pt - 1] if i_pt > 0
                                     else _pt_opts[0]),
                       "auto_type": i_pt == 0,
                       "segment": seg,
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
settings["lines_style"] = opt["style"]
settings["lines_prefix"] = opt["prefix"]
settings["lines_workset"] = opt["workset"] or ANY_WORKSET
settings["lines_dia_mm"] = opt["dia_mm"]
settings["lines_slope"] = opt["slope_n"]
if not markers:
    settings["lines_invert_m"] = opt["invert_m"]
settings["lines_pipe_type"] = None if opt["auto_type"] \
    else opt["pipe_type"][0]
settings["lines_segment"] = opt["segment"][0] if opt["segment"] else None
settings["lines_sys_type"] = opt["sys_type"][0]
try:
    save_settings(settings)
except Exception:
    pass

lines = gather_lines(opt["style"], opt["workset"], opt["prefix"])
log("**{}** line(s), dia **{:.0f} mm**{}, default gradient **1:{:g}**, "
    "outfall invert **{:.3f} m**".format(
        len(lines), opt["dia_mm"],
        " on segment **{}**".format(opt["segment"][0])
        if opt["segment"] else "",
        opt["slope_n"], opt["invert_m"]))

# ---------------------------------------------------------------------------
# per-line slopes from the line style names
# ---------------------------------------------------------------------------
lines_mm = [(a, b) for _el, a, b, _st in lines]
slopes = {}
custom_idx = []
styled, defaulted = 0, 0
for i, (_el, _a, _b, style_name) in enumerate(lines):
    got = parse_style_slope(style_name)
    if got == "custom":
        custom_idx.append(i)
    elif got is not None:
        slopes[i] = got
        styled += 1
    else:
        slopes[i] = opt["slope_n"]
        defaulted += 1
log("Slopes: **{}** from their style name, **{}** at the default "
    "1:{:g}, **{}** 'Slope Custom'.".format(styled, defaulted,
                                            opt["slope_n"],
                                            len(custom_idx)))

# slopes remembered from an earlier build pre-fill the plan window
_prev = load_lines_record(os.path.join(get_export_folder(doc),
                                       "project_files"))
_prev_custom = _prev.get("custom_slopes", {}) or {}
if custom_idx:
    preset = {}
    for i in custom_idx:
        uid = lines[i][0].UniqueId
        if uid in _prev_custom:
            preset[i] = float(_prev_custom[uid])
    got = ask_custom_slopes(CUSTOM_XAML_PATH, lines_mm, custom_idx,
                            preset=preset)
    if got is None:
        log("Custom slopes cancelled - nothing changed.")
        log.close()
        script.exit()
    for i, n in got.items():
        slopes[i] = n
        log("- custom line at ({:.1f}, {:.1f}) m: **1:{:g}**".format(
            lines_mm[i][0][0] / 1000.0, lines_mm[i][0][1] / 1000.0, n))

# ---------------------------------------------------------------------------
# the outfall: Invert Level marker nodes when placed, else one pick
# ---------------------------------------------------------------------------
sources = None
pick_mm = None
if markers:
    sources = []
    for el, (mx, my), mz in markers:
        sources.append(((mx, my), mz))
        log("- Invert Level node at ({:.1f}, {:.1f}) m -> invert "
            "**{:.3f} m** (Level + Elevation from Level)".format(
                mx / 1000.0, my / 1000.0, mz / 1000.0))
    pick_mm = list(sources[0][0])
else:
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
        crv = pick_el.GeometryCurve
        p = crv.GetEndPoint(0)
        pick_mm = (p.X * MM_PER_FT, p.Y * MM_PER_FT)

# ---------------------------------------------------------------------------
# solve + build
# ---------------------------------------------------------------------------
sol = solve(lines_mm, pick_mm, slopes, sources=sources)

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
                              0.0 if markers else opt["invert_m"],
                              log=log,
                              segment_id=(opt["segment"][1].Id
                                          if opt["segment"] else None))
except Exception as ex:
    import traceback
    log(traceback.format_exc())
    forms.alert("Nothing was built - the model is unchanged.\n\n"
                "{}".format(ex), title="Lines to Pipes", exitscript=True)

# record the build so Update Pipes can delete + rebuild it later
try:
    import datetime
    base = os.path.join(get_export_folder(doc), "project_files")
    custom_by_uid = {}
    for i in custom_idx:
        if i in slopes:
            custom_by_uid[lines[i][0].UniqueId] = slopes[i]
    uids = []
    for el in res.get("elements", []):
        try:
            uids.append(el.UniqueId)
        except Exception:
            pass
    save_lines_record(base, {
        "style": opt["style"], "workset": opt["workset"],
        "dia_mm": opt["dia_mm"], "invert_m": opt["invert_m"],
        "slope_default": opt["slope_n"],
        "pipe_type": opt["pipe_type"][0], "auto_type": opt["auto_type"],
        "sys_type": opt["sys_type"][0],
        "segment": opt["segment"][0] if opt["segment"] else None,
        "pick_mm": [pick_mm[0], pick_mm[1]],
        "prefix": opt["prefix"],
        "use_markers": bool(markers),
        "custom_slopes": custom_by_uid,
        "element_uids": uids,
        "when": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    log("Build recorded for **Update Pipes** ({} element(s))."
        .format(len(uids)))
except Exception as ex:
    log("! build record not saved ({}) - Update Pipes will not track "
        "this run".format(ex))

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
