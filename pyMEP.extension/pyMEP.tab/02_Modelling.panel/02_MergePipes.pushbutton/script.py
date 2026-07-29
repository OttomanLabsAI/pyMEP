# -*- coding: utf-8 -*-
"""Merge Pipes - collapse selected straight runs of pipe into single
pipes.

Select the pipes that make up a run (the short couplings between them
don't need selecting - just the pipes) and click the button. Every set
of collinear pipes among the selection is replaced by ONE pipe spanning
the run's two extreme endpoints, at the EXACT XYZ of those outermost
segment ends - nothing is re-projected or rounded. The original pipes
are deleted, along with the couplings that sat entirely inside the run;
fittings where the run meets the rest of the model (elbows, tees) are
kept and reconnected to the new pipe's matching end.

The new pipe inherits the run's longest segment: its pipe type, system
type, level, workset, Mark and comments. Diameter is the run's (the
largest when a run mixes sizes - reported, never silently). Pipes in
the selection that line up with nothing are left untouched.
"""

__title__  = "Merge\nPipes"
__author__ = "Glent Group"

import os
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

from pymep_merge_pipes import (
    read_pipe_rows, group_collinear, chain_gaps, merge_chain,
)
from pymep_config import load_settings, save_settings
from pymep_revit import ft2mm
from pymep_log import Logger

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB.Plumbing import Pipe

output = script.get_output()
log = Logger(output, "MergePipes")
doc = revit.doc
uidoc = revit.uidoc

log("### Merge Pipes")

# ---------------------------------------------------------------------------
# The settings window: slope 1:n + which end's level stays as is. Modal
# after a pre-selection; MODELESS and parked just under the ribbon while
# pick mode runs, so the settings sit next to Finish / Cancel.
# ---------------------------------------------------------------------------
settings = load_settings()
XAML_PATH = os.path.join(
    os.path.dirname(os.path.abspath(sys.modules["pymep_config"].__file__)),
    "pymep_merge_pipes.xaml")


class MergeWindow(forms.WPFWindow):

    def __init__(self, info_text, modeless=False):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self.result = None
        self.TxtInfo.Text = info_text
        self.ChkSlope.IsChecked = bool(settings.get("merge_slope_on", True))
        self.TxtSlope.Text = "{:g}".format(
            float(settings.get("merge_slope", 150.0)))
        if settings.get("merge_keep_end", "bottom") == "top":
            self.RadTop.IsChecked = True
        else:
            self.RadBottom.IsChecked = True
        self.on_slope(None, None)
        if modeless:
            # park it top-centre, under the ribbon, above Revit; the
            # options bar's Finish/Cancel drive the flow, so the
            # window's own buttons go away
            try:
                from System.Windows import (WindowStartupLocation,
                                            Visibility, SystemParameters)
                self.WindowStartupLocation = WindowStartupLocation.Manual
                self.Left = (SystemParameters.PrimaryScreenWidth
                             - self.Width) / 2.0
                self.Top = 150.0
                self.Topmost = True
                self.BtnMerge.Visibility = Visibility.Collapsed
                self.BtnCancel.Visibility = Visibility.Collapsed
            except Exception:
                pass

    def read_values(self):
        """The dialog's current values, or None when the slope text is
        not a positive number."""
        slope = None
        if self.ChkSlope.IsChecked:
            try:
                slope = float(self.TxtSlope.Text)
                if slope <= 0:
                    raise ValueError()
            except Exception:
                return None
        return {"slope": slope,
                "keep": "top" if self.RadTop.IsChecked else "bottom"}

    def on_slope(self, sender, args):
        try:
            on = bool(self.ChkSlope.IsChecked)
            self.TxtSlope.IsEnabled = on
            self.RadTop.IsEnabled = on
            self.RadBottom.IsEnabled = on
        except Exception:
            pass

    def on_merge(self, sender, args):
        v = self.read_values()
        if v is None:
            self.StatusText.Text = ("The slope must be a positive "
                                    "number (the n of 1:n).")
            return
        self.result = v
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


opts = None

# ---------------------------------------------------------------------------
# 1. Gather the pipes: a pre-selection works as before; nothing selected
#    drops into PICK mode - the settings float under the ribbon while you
#    choose pipes, then Finish on the options bar
# ---------------------------------------------------------------------------
pipes = []
for eid in uidoc.Selection.GetElementIds():
    el = doc.GetElement(eid)
    if isinstance(el, Pipe):
        pipes.append(el)

if len(pipes) < 2:
    log("No pipes pre-selected - pick the pipes to merge in the view "
        "(the settings sit under the ribbon), then **Finish** on the "
        "options bar.")
    clr.AddReference("RevitAPIUI")
    from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter

    class _PipesOnly(ISelectionFilter):
        def AllowElement(self, e):
            return isinstance(e, Pipe)

        def AllowReference(self, r, p):
            return False

    pick_win = MergeWindow("Pick the pipes in the view, set the slope "
                           "here, then Finish on the options bar.",
                           modeless=True)
    pick_win.Show()
    try:
        refs = uidoc.Selection.PickObjects(
            ObjectType.Element, _PipesOnly(),
            "Pick the pipes to merge, then Finish")
    except Exception:            # Esc / Cancel on the options bar
        refs = None
    opts = pick_win.read_values()    # whatever was set while picking
    try:
        pick_win.Close()
    except Exception:
        pass
    if refs is None:
        log("Pick cancelled - nothing changed.")
        log.close()
        script.exit()
    for r in refs:
        el = doc.GetElement(r.ElementId)
        if isinstance(el, Pipe):
            pipes.append(el)

if len(pipes) < 2:
    forms.alert("At least two pipes are needed to merge.\n\n"
                "Tip: pick all the pipes that make up a run (you can "
                "leave the couplings between them unpicked).",
                exitscript=True)

log("Working on **{}** pipe(s).".format(len(pipes)))
pipes_by_id = dict((p.Id.IntegerValue, p) for p in pipes)

rows, notes = read_pipe_rows(pipes)
for n in notes:
    log(n)
if len(rows) < 2:
    forms.alert("Fewer than two straight pipes in the selection - "
                "nothing to merge.", exitscript=True)

# ---------------------------------------------------------------------------
# 2. Group into collinear chains
# ---------------------------------------------------------------------------
chains, singles = group_collinear(rows)
if not chains:
    forms.alert("None of the selected pipes line up into a run.\n\n"
                "Merge joins pipes whose ends line up end-to-end along a "
                "run - the turn between them can be any angle, and gaps "
                "along the line are fine. These run parallel and offset "
                "to the side (more than a pipe diameter apart), so they "
                "don't share a line.", exitscript=True)

log("Found **{}** run(s) to merge; {} selected pipe(s) line up with "
    "nothing and will be left alone.".format(len(chains), len(singles)))

for ci, chain in enumerate(chains):
    dias = sorted(set(round(ft2mm(r["dia_ft"]), 0) for r in chain))
    log("Run {}: {} pipes -> 1  ({} mm)".format(
        ci + 1, len(chain),
        "/".join("{:.0f}".format(d) for d in dias)))
    gaps = chain_gaps(chain)
    if gaps:
        big = max(g[0] for g in gaps)
        log("  (largest gap along it **{:.0f} mm** - check it is meant "
            "to be one pipe)".format(ft2mm(big)))

# ---------------------------------------------------------------------------
# 2b. The settings: already collected under the ribbon in pick mode;
#     the modal dialog runs for a pre-selection (or when the pick-mode
#     slope text wasn't a valid number)
# ---------------------------------------------------------------------------
if opts is None:
    win = MergeWindow("{} run(s) from {} selected pipe(s)".format(
        len(chains), len(rows)))
    win.ShowDialog()
    if win.result is None:
        log("Cancelled - nothing changed.")
        log.close()
        script.exit()
    opts = win.result

settings["merge_slope_on"] = opts["slope"] is not None
if opts["slope"] is not None:
    settings["merge_slope"] = opts["slope"]
settings["merge_keep_end"] = opts["keep"]
try:
    save_settings(settings)
except Exception:
    pass

log("Slope: {} - keeping the **{}** end's level.".format(
    "**1:{:g}**".format(opts["slope"]) if opts["slope"] is not None
    else "none (exact extreme endpoints)", opts["keep"].upper()))

# ---------------------------------------------------------------------------
# 3. Merge each chain (each in its own transaction)
# ---------------------------------------------------------------------------
merged = 0
new_pipes = 0
deleted_couplings = 0
ws_mixed_runs = 0
failed = 0
for ci, chain in enumerate(chains):
    log("Run {}:".format(ci + 1))
    try:
        res = merge_chain(doc, pipes_by_id, chain, log=log,
                          slope_n=opts["slope"], keep_end=opts["keep"])
        merged += res["pipes"]
        new_pipes += 1
        deleted_couplings += res["internal"]
        if res.get("ws_mixed"):
            ws_mixed_runs += 1
    except Exception as ex:
        failed += 1
        import traceback
        log(traceback.format_exc())
        log("  ! run {} not merged: {}".format(ci + 1, ex))

log("#### Summary")
log("- Runs merged: **{}**".format(new_pipes))
log("- Pipes removed: **{}** (plus {} coupling fitting(s))".format(
    merged, deleted_couplings))
if ws_mixed_runs:
    log("- Runs whose pipes were on DIFFERENT worksets: **{}** - their "
        "new pipes are on the ACTIVE workset (see above)".format(
            ws_mixed_runs))
if failed:
    log("- Runs that failed: **{}** (left untouched)".format(failed))

forms.alert(
    "Merged {} run(s):\n"
    "  {} pipes -> {} pipes\n"
    "  {} coupling fitting(s) removed\n"
    "{}".format(
        new_pipes, merged, new_pipes, deleted_couplings,
        "  {} run(s) failed - see the report.".format(failed)
        if failed else ""),
    title="Pipes merged")
log.close()
