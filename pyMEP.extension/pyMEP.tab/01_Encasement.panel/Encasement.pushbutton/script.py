# -*- coding: utf-8 -*-
"""Encasement - full pipework encasement pipeline in one button.

Replaces the old three-button panel (Initialize / Build Ducts /
Build Connections). Behaviour depends on the current Revit selection:

WITH a selection (pipes/conduits + fittings):
  1. Export  - reads the selection and writes
       pipes_<TS>.csv
       fittings_<TS>.csv
     into the active export folder (via lib/pymep_export.py).
  2. Compute - shells out to run_analysis.py on that folder, which writes
       duct_centrelines_<TS>.csv
       plan_bend_outlines_<TS>.csv
       (plus straight_outlines / sloped_straights / chain_order CSVs)
     and opens the runs_3d.html / runs_plan.html visualizations.
  3. Confirm - after a successful compute you are asked whether to build
     now. Choose 'Build' to place the ducts and elbows immediately, or
     'Not now' to stop and review the 3D/plan views first. Re-clicking
     this button later with NOTHING selected rebuilds from the latest
     analysis CSVs.

With NO selection:
  Offers to rebuild from the newest analysis CSVs already on disk:
  the newest duct_centrelines_<TS>.csv drives Build Ducts and the newest
  plan_bend_outlines_<TS>.csv drives Build Connections.

Build stage (both modes):
  - Places one rectangular duct per centreline row (duct type and MEP
    system type come from Settings), Mark = C{col}-O{order}.
  - Inserts elbow fittings between consecutive ducts per collection and
    sets each elbow radius from the matching plan bend,
    Mark = C{col}-O{n}@{n+1}.
  - Ends with one combined summary: ducts created/failed and elbows
    created/failed. The output window closes itself only when everything
    succeeded with zero failures; on any failure it stays open so the
    report can be read.

Requires Python 3 with numpy on the Compute side. Configure paths, duct
type and system type in Settings.
"""

__title__  = "Encasement"
__author__ = "Glent Group"

import os
import re
import sys
import glob

import clr
clr.AddReference("System")
clr.AddReference("RevitAPI")
from System.Diagnostics import Process, ProcessStartInfo

from Autodesk.Revit.DB import (
    BuiltInCategory, BuiltInParameter, FilteredElementCollector, Transaction,
)
from Autodesk.Revit.DB.Mechanical import Duct

# Force-reload pymep_* libs so edits on disk always take effect.
for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

from pymep_config import (
    get_export_folder, get_python_exe, get_script_folder,
    get_duct_type_name, get_duct_system_type_name,
)
from pymep_export  import export_pipework
from pymep_build   import build_ducts_from_centrelines
from pymep_connect import build_connections
from pymep_log     import Logger


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
output     = script.get_output()
log        = Logger(output, "Encasement")
doc, uidoc = revit.doc, revit.uidoc

folder        = get_export_folder(doc)
script_folder = get_script_folder()
python_exe    = get_python_exe()

log("### Encasement")
log("Export folder: **{}**".format(folder))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
# Analysis/export filenames end in _YYYYMMDD_HHMMSS.csv; capture the stamp.
TS_CSV_RE = re.compile(r"_(\d{8}_\d{6})\.csv$")

# Marks written by the build stage: ducts "C{col}-O{order}", elbows
# "C{col}-O{n}@{n+1}".
BUILD_MARK_RE = re.compile(r"^C\d+-O\d+(@\d+)?$")


def newest_csv(pattern):
    """Newest CSV matching `pattern` in the export folder, or None.

    Only files whose name ends in _YYYYMMDD_HHMMSS.csv are considered,
    and 'newest' is decided by that embedded timestamp - so OneDrive
    conflict copies or manual copies with mangled names never win."""
    best, best_ts = None, None
    for path in glob.glob(os.path.join(folder, pattern)):
        m = TS_CSV_RE.search(os.path.basename(path))
        if not m:
            continue
        ts = m.group(1)
        if best_ts is None or ts > best_ts:
            best, best_ts = path, ts
    return best


def _element_mark(el):
    p = (el.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
         or el.LookupParameter("Mark"))
    return p.AsString() if p is not None else None


def find_previous_build_elements():
    """Ducts and duct fittings left behind by a previous Encasement build
    (Mark matches C#-O# or C#-O#@#). build_connections collects ducts by
    Mark across the WHOLE document, so leftovers would silently
    cross-connect old runs with new ones."""
    old = []
    for el in FilteredElementCollector(doc).OfClass(Duct):
        mark = _element_mark(el)
        if mark and BUILD_MARK_RE.match(mark.strip()):
            old.append(el)
    fittings = (FilteredElementCollector(doc)
                .OfCategory(BuiltInCategory.OST_DuctFitting)
                .WhereElementIsNotElementType())
    for el in fittings:
        mark = _element_mark(el)
        if mark and BUILD_MARK_RE.match(mark.strip()):
            old.append(el)
    return old


def confirm_previous_build():
    """If a previous build left marked ducts/fittings in the document, ask
    what to do with them. Returns True to continue building, False to
    cancel the build."""
    old_elems = find_previous_build_elements()
    if not old_elems:
        return True
    choice = forms.alert(
        "Found {} duct(s)/fitting(s) from a previous Encasement build "
        "(Mark 'C#-O#...').\n\n"
        "Building again on top of them would cross-connect the old runs "
        "with the new ones.".format(len(old_elems)),
        title="Encasement",
        options=["Delete old & rebuild", "Keep & continue", "Cancel"])
    if choice == "Delete old & rebuild":
        deleted = 0
        with Transaction(doc, "Encasement - clear previous build") as t:
            t.Start()
            for el in old_elems:
                try:
                    doc.Delete(el.Id)
                    deleted += 1
                except Exception:
                    # Already gone (cascade delete of a joined fitting).
                    pass
            t.Commit()
        log("Deleted **{}** element(s) from the previous build.".format(deleted))
        return True
    if choice == "Keep & continue":
        log("**Warning:** keeping previous build elements - old and new "
            "runs may end up cross-connected.")
        return True
    log("Cancelled - previous build left untouched.")
    return False


def run_build_stage(ts=None):
    """Build ducts then connections from analysis CSVs.

    `ts` (selection mode) is the export timestamp of this run: the exact
    duct_centrelines_<ts>.csv / plan_bend_outlines_<ts>.csv are preferred,
    falling back to the newest files with a logged warning. If the
    centrelines lookup falls back, the bends CSV MUST share the timestamp
    of the centrelines CSV actually used. With no `ts` (rebuild mode) the
    newest centrelines CSV is used and the bends CSV MUST share its
    timestamp. The centrelines CSV is resolved (and its absence aborts
    the stage) BEFORE confirm_previous_build() may delete anything.

    Returns (ducts_created, ducts_failed, elbows_created, elbows_failed)
    on a full run, or None if the stage aborted before both builders ran
    (missing CSV or a hard error). Callers decide what to do with the
    output window - this function never closes it.
    """
    # Resolve the centrelines CSV BEFORE confirm_previous_build(): that
    # dialog can DELETE the previous build in a committed transaction, so
    # it must never fire and then leave us with nothing to build.
    cl_csv = None
    cl_fell_back = False
    if ts:
        exact = os.path.join(folder, "duct_centrelines_{}.csv".format(ts))
        if os.path.isfile(exact):
            cl_csv = exact
        else:
            log("**Warning:** duct_centrelines_{}.csv not found - falling "
                "back to the newest centrelines CSV.".format(ts))
            cl_fell_back = True
    if cl_csv is None:
        cl_csv = newest_csv("duct_centrelines_*.csv")
    if cl_csv is None:
        forms.alert(
            "No duct_centrelines_<TS>.csv found in:\n\n{}\n\n"
            "Run Encasement with pipework selected first.".format(folder))
        log("No duct_centrelines CSV found - build aborted.")
        return None

    if not confirm_previous_build():
        return None

    # --- Ducts --------------------------------------------------------------
    log("---")
    log("### Build 1 / 2 - Ducts")
    log("CSV: `{}`".format(os.path.basename(cl_csv)))

    try:
        ducts_created, ducts_failed = build_ducts_from_centrelines(
            doc, cl_csv,
            duct_type_name=get_duct_type_name(),
            system_type_name=get_duct_system_type_name(),
            log=log)
    except Exception as ex:
        forms.alert("Build Ducts failed:\n\n{}".format(ex))
        log("Build Ducts error: {}".format(ex))
        return None

    # --- Connections ----------------------------------------------------
    log("---")
    log("### Build 2 / 2 - Connections")

    bend_csv = None
    if ts and not cl_fell_back:
        exact = os.path.join(folder, "plan_bend_outlines_{}.csv".format(ts))
        if os.path.isfile(exact):
            bend_csv = exact
        else:
            log("**Warning:** plan_bend_outlines_{}.csv not found - falling "
                "back to the newest bends CSV.".format(ts))
            bend_csv = newest_csv("plan_bend_outlines_*.csv")
    else:
        # Rebuild mode, or the centrelines lookup fell back to a different
        # analysis run: the bends CSV must come from the SAME analysis run
        # as the centrelines CSV actually used, or the elbows get radii
        # from a different run.
        m = TS_CSV_RE.search(os.path.basename(cl_csv))
        cl_ts = m.group(1) if m else None
        want = (os.path.join(folder,
                             "plan_bend_outlines_{}.csv".format(cl_ts))
                if cl_ts else None)
        if want and os.path.isfile(want):
            bend_csv = want
        else:
            forms.alert(
                "plan_bend_outlines_{}.csv (same analysis run as the "
                "centrelines CSV) was not found in:\n\n{}\n\n"
                "Connections skipped - re-run the analysis to get a "
                "matching pair.".format(cl_ts, folder))
            log("Ducts:  created **{}**, failed **{}**".format(
                ducts_created, ducts_failed))
            log("No plan_bend_outlines CSV with timestamp {} - connections "
                "aborted.".format(cl_ts))
            return None
    if bend_csv is None:
        forms.alert(
            "No plan_bend_outlines_<TS>.csv found in:\n\n{}\n\n"
            "Run Encasement with pipework selected first.".format(folder))
        log("No plan_bend_outlines CSV found - build aborted after ducts.")
        return None
    log("Bends CSV: `{}`".format(os.path.basename(bend_csv)))

    try:
        elbows_created, elbows_failed = build_connections(doc, bend_csv, log=log)
    except Exception as ex:
        forms.alert("Build Connections failed:\n\n{}".format(ex))
        log("Build Connections error: {}".format(ex))
        return None

    return ducts_created, ducts_failed, elbows_created, elbows_failed


def finish_build(result):
    """Print the combined summary and close. Self-destructs the output
    window ONLY when everything succeeded with zero failures; on any
    failure (or an aborted stage) the window stays open so the report
    can be read."""
    if result is None:
        log("---")
        log("### Build did not complete - see messages above.")
        log.close()
        script.exit()

    ducts_created, ducts_failed, elbows_created, elbows_failed = result
    log("---")
    log("### Encasement build summary")
    log("Ducts:  created **{}**, failed **{}**".format(
        ducts_created, ducts_failed))
    log("Elbows: created **{}**, failed **{}**".format(
        elbows_created, elbows_failed))

    if ducts_failed == 0 and elbows_failed == 0:
        log("All done - no failures.")
        log.close()
        output.self_destruct(5)
    else:
        log("**Some rows failed - review the messages above.** "
            "(Window left open.)")
        log.close()
    script.exit()


# ---------------------------------------------------------------------------
# Mode switch: selection -> full chain, no selection -> rebuild only
# ---------------------------------------------------------------------------
sel_ids = uidoc.Selection.GetElementIds()
has_selection = (sel_ids is not None and len(sel_ids) > 0)

if not has_selection:
    # ------------------------------------------------------------------
    # Rebuild-only mode
    # ------------------------------------------------------------------
    log("No selection - rebuild-only mode.")
    if not forms.alert(
            "No selection. Rebuild ducts + connections from the latest "
            "analysis CSVs?",
            title="Encasement", yes=True, no=True):
        log("Cancelled.")
        log.close()
        script.exit()

    finish_build(run_build_stage())


# ---------------------------------------------------------------------------
# Full chain: Export -> Compute -> (confirm) -> Build
# ---------------------------------------------------------------------------
# Ask for the encasement cover (offset) up front.
# Cover is added to the outside of the pipe envelope in every direction:
#   width  = pipe spread + OD + 2*cover
#   height = pipe Z-spread + OD + 2*cover
# So the duct cross-section grows by 2*cover in both X and Z for each
# collection. Passed through to run_analysis.py as --cover.
cover_str = forms.ask_for_string(
    prompt="Encasement cover (offset) in mm.\n\n"
           "Added on every side of the pipe envelope - duct width and\n"
           "height grow by 2 * cover compared to the bare pipe bundle.",
    default="100",
    title="Encasement - cover")
if cover_str is None:
    log("Cancelled."); log.close(); script.exit()
try:
    cover_mm = float(cover_str)
    # Bounded range check also rejects NaN and +/-inf (NaN fails every
    # comparison, inf fails the upper bound).
    if not (0 <= cover_mm < 1e6):
        raise ValueError("cover must be >= 0 mm and < 1e6 mm")
except Exception as ex:
    forms.alert("Invalid cover value: '{}'\n\n{}".format(cover_str, ex),
                exitscript=True)

log("Cover: **{:.1f} mm**".format(cover_mm))


# ---------------------------------------------------------------------------
# Step 1 - Export
# ---------------------------------------------------------------------------
log("---")
log("### Step 1 / 2 - Export")

try:
    r = export_pipework(doc, uidoc, folder, log=log)
    log("Export complete - {} pipe(s), {} fitting(s) written.".format(
        r["pipe_count"], r["fit_count"]))
except ValueError as ex:
    # Selection / input problems - clean cancel, no Compute attempted
    forms.alert(str(ex), exitscript=False)
    log("Cancelled: {}".format(ex))
    log.close()
    script.exit()
except Exception as ex:
    log("### Export FAILED")
    log("```\n{}\n```".format(ex))
    forms.alert("Export failed:\n\n{}\n\nCompute skipped.".format(ex))
    log.close()
    script.exit()

# The analysis needs BOTH sets: pipes/conduits give the runs, fittings give
# the bends. export_pipework only writes fittings_<TS>.csv when fittings
# exist, and run_analysis.py dies on an unpaired export - stop here with a
# readable message instead.
if r["pipe_count"] == 0 or r["fit_count"] == 0:
    forms.alert(
        "Selection needs both pipes/conduits AND fittings for the "
        "analysis.\n\nExported: {} pipe(s)/conduit(s), {} fitting(s)."
        .format(r["pipe_count"], r["fit_count"]))
    log("Selection incomplete ({} pipe(s), {} fitting(s)) - Compute skipped."
        .format(r["pipe_count"], r["fit_count"]))
    log.close()
    script.exit()


# ---------------------------------------------------------------------------
# Step 2 - Compute
# ---------------------------------------------------------------------------
log("---")
log("### Step 2 / 2 - Compute")

# Pre-flight checks (match old ComputePipework behaviour)
if not script_folder or not os.path.isdir(script_folder):
    forms.alert(
        "The conduit_analysis folder is not set or does not exist.\n\n"
        "Open Settings and set it to the folder that contains run_analysis.py.",
        exitscript=False)
    log("conduit_analysis folder not set - Compute skipped.")
    log.close()
    script.exit()

run_script = os.path.join(script_folder, "run_analysis.py")
if not os.path.isfile(run_script):
    forms.alert(
        "run_analysis.py not found in:\n  {}\n\n"
        "Check the conduit_analysis folder in Settings.".format(script_folder),
        exitscript=False)
    log("run_analysis.py not found - Compute skipped.")
    log.close()
    script.exit()

log("Python:         `{}`".format(python_exe))
log("Script:         `{}`".format(run_script))
log("Export folder:  `{}`".format(folder))
log("Running... (this may take a moment)")

# Run synchronously, capturing stdout/stderr.
# CommandLineToArgvW treats a backslash right before a closing quote as an
# escape for that quote, so strip trailing backslashes before embedding
# the paths in the argument string.
arg_script = run_script.rstrip("\\")
arg_folder = folder.rstrip("\\")

psi = ProcessStartInfo()
psi.FileName               = python_exe
psi.Arguments              = ('"{}" --folder "{}" --cover {} '
                              '--timestamp {} --open'
                              .format(arg_script, arg_folder, cover_mm,
                                      r["timestamp"]))
psi.WorkingDirectory       = folder
psi.UseShellExecute        = False
psi.CreateNoWindow         = True
psi.RedirectStandardOutput = True
psi.RedirectStandardError  = True

try:
    p = Process()
    p.StartInfo = psi

    # Read stderr asynchronously: calling ReadToEnd() on both pipes
    # back-to-back deadlocks as soon as the child fills the stderr pipe
    # buffer while we are still blocked reading stdout.
    stderr_lines = []

    def _collect_stderr(sender, args):
        if args.Data is not None:
            stderr_lines.append(args.Data)

    p.ErrorDataReceived += _collect_stderr
    p.Start()
    p.BeginErrorReadLine()
    stdout = p.StandardOutput.ReadToEnd()
    p.WaitForExit()
    exit_code = p.ExitCode
    stderr = "\n".join(stderr_lines)
except Exception as ex:
    log("### ERROR launching Python")
    log("```\n{}\n```".format(ex))
    log("Check that **{}** is a valid Python executable (or on your PATH) "
        "via Settings.".format(python_exe))
    log.close()
    script.exit()

log("---")
if stdout:
    log("### Script output")
    log("```\n{}\n```".format(stdout))
if stderr:
    log("### stderr")
    log("```\n{}\n```".format(stderr))

if exit_code != 0:
    log("### Compute failed (exit {})".format(exit_code))
    log("Build skipped - fix the compute error and re-run.")
    log.close()
    script.exit()

log("### Compute done (exit 0)")
log("Outputs written to:\n\n`{}`".format(folder))


# ---------------------------------------------------------------------------
# Step 3 - Confirm, then Build
# ---------------------------------------------------------------------------
choice = forms.alert(
    "Analysis complete - review the 3D/plan views.\n\n"
    "Build ducts + connections now?",
    title="Encasement",
    options=["Build", "Not now"])

if choice != "Build":
    log("Build skipped by user.")
    log("**Note:** re-click the Encasement button with NOTHING selected to "
        "rebuild from the latest analysis CSVs.")
    log.close()
    script.exit()

finish_build(run_build_stage(ts=r["timestamp"]))
