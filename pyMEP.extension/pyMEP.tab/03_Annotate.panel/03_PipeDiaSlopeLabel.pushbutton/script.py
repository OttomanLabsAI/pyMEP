# -*- coding: utf-8 -*-
"""Pipe Dia+Slope - populate the project parameter
'MEP_pipe_dia_slope_label' on every pipe with a static text like
'160mm @ 1:100' or '160mm @ level'.

Selection logic:
  - If any pipes are pre-selected, only those are processed (workset
    prompt is skipped - pre-selection is the more specific filter).
  - Otherwise, on a workshared document, a checkbox dialog opens
    listing every workset that contains pipes (with the per-workset
    pipe count next to the name). Tick the worksets to update;
    cancelling aborts cleanly. Empty worksets and worksets without
    pipes are hidden.
  - On a non-workshared document the prompt is skipped and every
    pipe in the model is processed.

The written value is static - re-run after diameters or slopes change
to refresh the labels.

Source values:
  - Diameter: BuiltInParameter.RBS_PIPE_DIAMETER_PARAM (nominal /
    trade size). Read as feet internally, converted to mm via *304.8.
  - Slope:    BuiltInParameter.RBS_PIPE_SLOPE (rise/run decimal).
    Inverted and rounded to int for the '1:X' form; magnitudes below
    1e-9 are written as 'level'.

Pipes that don't have the 'MEP_pipe_dia_slope_label' project parameter
(or that have it read-only) are skipped and counted in the summary
alert.
"""

__title__  = "Pipe\nDia+Slope"
__author__ = "Glent Group"

import sys

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (
    FilteredElementCollector, Transaction, BuiltInParameter,
)
from Autodesk.Revit.DB.Plumbing import Pipe

# Workset-specific imports - in their own try/except so a hypothetical
# Revit build that renamed them can't crash the button at load time.
# If unavailable, the workset prompt is skipped (every pipe processed).
try:
    from Autodesk.Revit.DB import FilteredWorksetCollector, WorksetKind
    HAVE_WORKSET_API = True
except ImportError:
    HAVE_WORKSET_API = False

from pyrevit import revit, forms, script

doc   = revit.doc
uidoc = revit.uidoc

PARAM = "MEP_pipe_dia_slope_label"
TOL   = 1e-9    # slope magnitudes below this -> 'level'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _ws_id_int(ws_id):
    """WorksetId as int, compatible with Revit 2024+ (.Value) and
    earlier (.IntegerValue)."""
    try:
        return ws_id.Value
    except AttributeError:
        return ws_id.IntegerValue


class _WSChoice(object):
    """Wrapper for forms.SelectFromList - exposes a `name` attribute
    for the checkbox label and keeps the workset id reachable after the
    dialog returns the selected items."""
    def __init__(self, ws_name, ws_id_int, pipe_count):
        self.name       = "{}  ({} pipes)".format(ws_name, pipe_count)
        self.ws_id_int  = ws_id_int


def make_label(pipe):
    """Build the label string for a single pipe."""
    # Diameter: Revit internal feet -> mm
    dia_p = pipe.get_Parameter(BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
    dia_mm = dia_p.AsDouble() * 304.8 if dia_p else 0.0
    dia_txt = int(round(dia_mm))

    # Slope: rise/run decimal (e.g. 0.01 == 1:100)
    slope_p = pipe.get_Parameter(BuiltInParameter.RBS_PIPE_SLOPE)
    slope = slope_p.AsDouble() if slope_p else 0.0

    if abs(slope) > TOL:
        ratio = int(round(1.0 / abs(slope)))
        return "{}mm @ 1:{}".format(dia_txt, ratio)
    return "{}mm @ level".format(dia_txt)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
pipes = None

# Pre-selection wins: if the user has pipes selected before clicking,
# process those directly and skip the workset prompt entirely.
sel_ids = list(uidoc.Selection.GetElementIds())
if sel_ids:
    picked = [doc.GetElement(i) for i in sel_ids]
    sel_pipes = [e for e in picked if isinstance(e, Pipe)]
    if sel_pipes:
        pipes = sel_pipes

if pipes is None:
    # No pre-selection - collect every pipe in the model.
    all_pipes = list(
        FilteredElementCollector(doc)
            .OfClass(Pipe)
            .WhereElementIsNotElementType()
            .ToElements()
    )
    if not all_pipes:
        forms.alert("No pipes in the model.", exitscript=True)

    if doc.IsWorkshared and HAVE_WORKSET_API:
        # Group pipes by their workset id.
        pipes_by_ws = {}
        for p in all_pipes:
            try:
                ws_int = _ws_id_int(p.WorksetId)
            except Exception:
                ws_int = -1
            pipes_by_ws.setdefault(ws_int, []).append(p)

        # Map workset id -> name across all user worksets (the workset
        # collector may legitimately miss system worksets; pipes on
        # those get a fallback display name).
        ws_name_by_id = {}
        try:
            for ws in FilteredWorksetCollector(doc).OfKind(WorksetKind.UserWorkset):
                ws_name_by_id[_ws_id_int(ws.Id)] = ws.Name
        except Exception:
            pass

        # Build choices: only worksets that actually contain pipes,
        # sorted alphabetically by display name.
        choices = []
        for ws_int, ps in pipes_by_ws.items():
            name = ws_name_by_id.get(
                ws_int, "(non-user workset #{})".format(ws_int))
            choices.append(_WSChoice(name, ws_int, len(ps)))
        choices.sort(key=lambda c: c.name.lower())

        if not choices:
            forms.alert("No worksets contain pipes.", exitscript=True)

        picked_choices = forms.SelectFromList.show(
            choices,
            multiselect=True,
            name_attr="name",
            title="Pipe Dia+Slope - pick worksets to update",
            button_name="Update",
        )
        if not picked_choices:
            sys.exit()    # cancelled

        picked_ids = set()
        for c in picked_choices:
            picked_ids.add(c.ws_id_int)

        pipes = [p for p in all_pipes
                 if _ws_id_int(p.WorksetId) in picked_ids]
    else:
        # Non-workshared (or no Workset API available) - process all pipes.
        pipes = all_pipes

if not pipes:
    forms.alert("No pipes to process after filtering.", exitscript=True)

written         = 0
skipped_missing = 0    # no MEP_pipe_dia_slope_label parameter on the pipe
skipped_ro      = 0    # parameter exists but is read-only
skipped_error   = 0    # Set() threw (wrong type, value rejected, etc.)
last_error_msg  = ""

t = Transaction(doc, "pyMEP: Write pipe dia/slope label")
t.Start()
try:
    for p in pipes:
        tgt = p.LookupParameter(PARAM)
        if tgt is None:
            skipped_missing += 1
            continue
        if tgt.IsReadOnly:
            skipped_ro += 1
            continue
        try:
            tgt.Set(make_label(p))
            written += 1
        except Exception as ex:
            skipped_error += 1
            last_error_msg = "{}: {}".format(type(ex).__name__, ex)
    t.Commit()
except Exception as ex:
    t.RollBack()
    forms.alert("Rolled back (placed {} of {} before the error):\n\n"
                "{}: {}".format(written, len(pipes),
                                type(ex).__name__, ex),
                exitscript=True)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
lines = ["Pipes processed: {}".format(len(pipes)),
         "Labels written:  {}".format(written)]
if skipped_missing:
    lines.append("Skipped (no '{}' param): {}"
                 .format(PARAM, skipped_missing))
if skipped_ro:
    lines.append("Skipped (param is read-only): {}".format(skipped_ro))
if skipped_error:
    lines.append("Skipped (Set() threw): {}  (last: {})"
                 .format(skipped_error, last_error_msg))
forms.alert("\n".join(lines))

# Close pyRevit output window if anything opened it.
try:
    script.get_output().close()
except Exception:
    pass
