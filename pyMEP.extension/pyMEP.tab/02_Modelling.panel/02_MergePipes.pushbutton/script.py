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

import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

from pymep_merge_pipes import (
    read_pipe_rows, group_collinear, chain_gaps, merge_chain,
)
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
# 1. Gather the selected pipes
# ---------------------------------------------------------------------------
pipes = []
for eid in uidoc.Selection.GetElementIds():
    el = doc.GetElement(eid)
    if isinstance(el, Pipe):
        pipes.append(el)

if len(pipes) < 2:
    forms.alert("Select at least two pipes to merge.\n\n"
                "Tip: select all the pipes that make up a run (you can "
                "leave the couplings between them unselected) and run "
                "this again.", exitscript=True)

log("Selected **{}** pipe(s).".format(len(pipes)))
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
                "Merge only joins pipes that are collinear (same line, "
                "any gaps allowed). These point in different directions "
                "or are offset from each other.", exitscript=True)

log("Found **{}** run(s) to merge; {} selected pipe(s) line up with "
    "nothing and will be left alone.".format(len(chains), len(singles)))

# report any large gaps so an accidental bridge across a real break is a
# conscious choice
gap_warn = []
for ci, chain in enumerate(chains):
    gaps = chain_gaps(chain)
    if gaps:
        big = max(g[0] for g in gaps)
        gap_warn.append("Run {}: {} pipe(s), largest gap along it "
                        "**{:.0f} mm**".format(
                            ci + 1, len(chain), ft2mm(big)))
for w in gap_warn:
    log(w)

msg = "Merge {} run(s) into {} single pipe(s)?\n\n".format(
    len(chains), len(chains))
for ci, chain in enumerate(chains):
    dias = sorted(set(round(ft2mm(r["dia_ft"]), 0) for r in chain))
    msg += "Run {}: {} pipes -> 1  ({})\n".format(
        ci + 1, len(chain),
        "/".join("{:.0f}".format(d) for d in dias) + " mm")
if gap_warn:
    msg += ("\nSome runs have gaps larger than a coupling - check they "
            "are meant to be one pipe.\n")
msg += "\nThe original pipes and their internal couplings are deleted."

if forms.alert(msg, title="Merge Pipes",
               options=["Merge", "Cancel"]) != "Merge":
    forms.alert("Cancelled - nothing changed.", exitscript=True)

# ---------------------------------------------------------------------------
# 3. Merge each chain (each in its own transaction)
# ---------------------------------------------------------------------------
merged = 0
new_pipes = 0
deleted_couplings = 0
failed = 0
for ci, chain in enumerate(chains):
    log("Run {}:".format(ci + 1))
    try:
        res = merge_chain(doc, pipes_by_id, chain, log=log)
        merged += res["pipes"]
        new_pipes += 1
        deleted_couplings += res["internal"]
    except Exception as ex:
        failed += 1
        import traceback
        log(traceback.format_exc())
        log("  ! run {} not merged: {}".format(ci + 1, ex))

log("#### Summary")
log("- Runs merged: **{}**".format(new_pipes))
log("- Pipes removed: **{}** (plus {} coupling fitting(s))".format(
    merged, deleted_couplings))
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
