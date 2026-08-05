# -*- coding: utf-8 -*-
"""Quick Merge - Merge Pipes with the remembered settings and no
popups.

Runs exactly what the Merge Pipes button runs, but with the settings
last used in its dialog (slope on/off + ratio, which end's level to
keep, workset for the merged pipe) and without showing any window or
alert. Select the pipes of one or more runs and click; with nothing
selected it drops into pick mode (pick, then Finish on the options
bar). Everything it did - and anything it skipped - is written to the
pyRevit output report only.
"""

__title__  = "Quick\nMerge"
__author__ = "Glent Group"

import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, script

from pymep_merge_pipes import (
    read_pipe_rows, group_collinear, chain_gaps, merge_chain,
)
from pymep_config import load_settings
from pymep_revit import ft2mm
from pymep_log import Logger

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB.Plumbing import Pipe

output = script.get_output()
log = Logger(output, "QuickMerge")
doc = revit.doc
uidoc = revit.uidoc

log("### Quick Merge")

# ---------------------------------------------------------------------------
# The remembered Merge Pipes settings drive everything - no dialog
# ---------------------------------------------------------------------------
settings = load_settings()
WS_ACTIVE = "(my active workset)"
WS_KEEP = "(keep from the merged pipes)"

slope = None
if settings.get("merge_slope_on", True):
    try:
        slope = float(settings.get("merge_slope", 150.0))
        if slope <= 0:
            slope = 150.0
    except Exception:
        slope = 150.0
keep = settings.get("merge_keep_end", "bottom")
if keep not in ("top", "bottom"):
    keep = "bottom"
ws_name = settings.get("merge_workset") or WS_ACTIVE

if ws_name == WS_ACTIVE:
    ws_arg = "active"
elif ws_name == WS_KEEP:
    ws_arg = "keep"
else:
    ws_arg = "keep"
    try:
        from Autodesk.Revit.DB import (FilteredWorksetCollector,
                                       WorksetKind)
        found = False
        for w in FilteredWorksetCollector(doc).OfKind(
                WorksetKind.UserWorkset):
            if w.Name == ws_name:
                ws_arg = w.Id.IntegerValue
                found = True
                break
        if not found:
            log("Workset '{}' from the remembered settings no longer "
                "exists - keeping the merged pipes' own workset."
                .format(ws_name))
            ws_name = WS_KEEP
    except Exception:
        pass

log("Slope: {} - keeping the **{}** end's level - workset: **{}**. "
    "(Change these in the Merge Pipes dialog.)".format(
        "**1:{:g}**".format(slope) if slope is not None
        else "none (exact extreme endpoints)", keep.upper(), ws_name))

# ---------------------------------------------------------------------------
# 1. Gather the pipes: the selection, or a plain pick (no window)
# ---------------------------------------------------------------------------
pipes = []
for eid in uidoc.Selection.GetElementIds():
    el = doc.GetElement(eid)
    if isinstance(el, Pipe):
        pipes.append(el)

if len(pipes) < 2:
    log("No pipes pre-selected - pick the pipes to merge, then "
        "**Finish** on the options bar.")
    clr.AddReference("RevitAPIUI")
    from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter

    class _PipesOnly(ISelectionFilter):
        def AllowElement(self, e):
            return isinstance(e, Pipe)

        def AllowReference(self, r, p):
            return False

    try:
        refs = uidoc.Selection.PickObjects(
            ObjectType.Element, _PipesOnly(),
            "Pick the pipes to merge, then Finish")
    except Exception:            # Esc / Cancel on the options bar
        refs = None
    if refs is None:
        log("Pick cancelled - nothing changed.")
        log.close()
        script.exit()
    for r in refs:
        el = doc.GetElement(r.ElementId)
        if isinstance(el, Pipe):
            pipes.append(el)

if len(pipes) < 2:
    log("At least two pipes are needed to merge - nothing changed.")
    log.close()
    script.exit()

log("Working on **{}** pipe(s).".format(len(pipes)))
pipes_by_id = dict((p.Id.IntegerValue, p) for p in pipes)

rows, notes = read_pipe_rows(pipes)
for n in notes:
    log(n)
if len(rows) < 2:
    log("Fewer than two straight pipes in the selection - nothing to "
        "merge.")
    log.close()
    script.exit()

# ---------------------------------------------------------------------------
# 2. Group into collinear chains
# ---------------------------------------------------------------------------
chains, singles = group_collinear(rows)
if not chains:
    log("None of the selected pipes line up into a run - nothing "
        "changed.")
    log.close()
    script.exit()

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
                          slope_n=slope, keep_end=keep,
                          workset=ws_arg)
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
log.close()
