# -*- coding: utf-8 -*-
"""Annotate Pipes - place a single-line '{D}mm @ 1:{X}' label next to
every pre-selected pipe in the active plan view. No click required:
each label is auto-placed at its pipe's midpoint, offset perpendicular
to the pipe's XY run direction by the distance configured in
Settings > Annotate > Set pipe annotation offset (mm) (default 500 mm).

Workflow:
  1. Pre-select one or more pipes in a plan view.
  2. Click the button.
  3. The script reads each pipe's Outside Diameter (mm) and slope
     (rise/run, converted to a '1:X' run-length), and places one text
     note per pipe at that pipe's midpoint, offset perpendicular by the
     configured distance. A leader is added from the text mid-line back
     to the pipe midpoint.

Layout:
  - For each pipe, the XY direction is sign-normalised (so dx >= 0,
    or dx == 0 and dy >= 0) before computing the perpendicular. That
    way parallel pipes drawn in opposite start/end order still get
    labels on the same side of the run.
  - Perpendicular is +90 deg CCW: a +X pipe gets labels above (+Y); a +Y
    pipe gets labels to the left (-X).
  - The leader exits whichever side of the text is closer to the pipe.

Notes:
  - One label per pipe, with that pipe's own diameter and slope - no
    modal collapse.
  - Horizontal pipes (slope = 0) print as '1:0', matching Revit's own
    display.
  - Selection is filtered to OST_PipeCurves only; conduits are ignored.
  - Pipes with no usable Outside Diameter or zero XY length are
    silently skipped.
"""

__title__  = "Annotate\nPipes"
__author__ = "Glent Group"

import math
import sys

# Force-reload pymep_* lib modules so the script picks up latest code
for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (
    Transaction, TextNote, ElementTypeGroup, ElementId,
    ViewType, FilteredElementCollector, TextNoteType, BuiltInCategory, XYZ,
)

# TextNoteLeaderType - some Revit builds don't expose this name under
# Autodesk.Revit.DB even though TextNote.AddLeader() still takes the same
# underlying integer values (StraightL=0, StraightR=1, ArcL=2, ArcR=3).
# Try the enum first; fall back to int constants so AddLeader still works.
_LEADER_LEFT  = 0
_LEADER_RIGHT = 1
try:
    from Autodesk.Revit.DB import TextNoteLeaderType
    _LEADER_LEFT  = TextNoteLeaderType.StraightL
    _LEADER_RIGHT = TextNoteLeaderType.StraightR
except (ImportError, AttributeError):
    pass

# LeaderAtachement (the Revit API misspells this since 2018). Try the
# typo first, then the correct spelling, fall through to None.
try:
    from Autodesk.Revit.DB import LeaderAtachement as _LeaderAttach
except ImportError:
    try:
        from Autodesk.Revit.DB import LeaderAttachement as _LeaderAttach
    except ImportError:
        _LeaderAttach = None

from pyrevit import revit, forms, script

from pymep_config import get_annotate_pipe_offset_mm
from pymep_revit  import get_connectors, get_od, get_slope, mm2ft

doc    = revit.doc
uidoc  = revit.uidoc
view   = doc.ActiveView


# ---------------------------------------------------------------------------
# 0. PRE-FLIGHT: plan view, pre-selected pipes
# ---------------------------------------------------------------------------
PLAN_VIEW_TYPES = (
    ViewType.FloorPlan,
    ViewType.CeilingPlan,
    ViewType.EngineeringPlan,
    ViewType.AreaPlan,
)
if view is None or view.ViewType not in PLAN_VIEW_TYPES:
    forms.alert("Open a plan view (Floor / Ceiling / Structural / Area) and "
                "try again.",
                exitscript=True)

def _cat_int(elem):
    """Element category id as int, compatible with Revit 2024+ (.Value) and
    earlier (.IntegerValue)."""
    if elem is None or elem.Category is None:
        return None
    cid = elem.Category.Id
    try:
        return cid.Value
    except AttributeError:
        return cid.IntegerValue

PIPE_CAT = int(BuiltInCategory.OST_PipeCurves)

sel_ids = list(uidoc.Selection.GetElementIds())
pipes = []
for eid in sel_ids:
    e = doc.GetElement(eid)
    if _cat_int(e) == PIPE_CAT:
        pipes.append(e)

if not pipes:
    forms.alert("Select one or more pipes (OST_PipeCurves) in the view "
                "first, then click the button.\n\n"
                "({} element(s) selected, none of them pipes.)"
                .format(len(sel_ids)),
                exitscript=True)


# ---------------------------------------------------------------------------
# 1. PER-PIPE READS + ENDPOINTS
# ---------------------------------------------------------------------------
def _pipe_endpoints(pipe):
    """Return (XYZ, XYZ) endpoints of the pipe centreline (Revit ft), or
    (None, None) if neither Location.Curve nor connectors are usable."""
    loc = getattr(pipe, "Location", None)
    if loc is not None and hasattr(loc, "Curve") and loc.Curve is not None:
        c = loc.Curve
        return c.GetEndPoint(0), c.GetEndPoint(1)
    conns = list(get_connectors(pipe))
    if len(conns) >= 2:
        return conns[0].Origin, conns[1].Origin
    return None, None

# Build a per-pipe record list. Each entry has everything needed to
# auto-place that pipe's label. We don't collapse to a modal label any
# more - one label per pipe so a selection of N pipes produces N
# annotations.
records = []     # list of dicts: {label, anchor, leader_end, leader_side}

offset_mm = get_annotate_pipe_offset_mm()
offset_ft = mm2ft(offset_mm)

for pipe in pipes:
    p0, p1 = _pipe_endpoints(pipe)
    if p0 is None or p1 is None:
        continue

    # XY run direction. Normalise sign so parallel pipes drawn in
    # opposite start/end order still get labels on the SAME side of the
    # run (otherwise the perpendicular would flip and labels would
    # appear on alternating sides for visually-parallel pipes).
    dx = p1.X - p0.X
    dy = p1.Y - p0.Y
    if dx < 0 or (dx == 0.0 and dy < 0):
        dx, dy = -dx, -dy
    mag = math.sqrt(dx * dx + dy * dy)
    if mag < 1e-9:
        continue   # zero-length pipe in XY (purely vertical or degenerate)
    ux = dx / mag
    uy = dy / mag
    # Perpendicular = +90 deg CCW in XY. For a +X pipe this points +Y
    # (label above); for a +Y pipe this points -X (label to the left).
    px = -uy
    py = ux

    # Pipe midpoint and offset text anchor.
    mx = (p0.X + p1.X) * 0.5
    my = (p0.Y + p1.Y) * 0.5
    mz = (p0.Z + p1.Z) * 0.5
    ax = mx + px * offset_ft
    ay = my + py * offset_ft

    # Diameter -> int mm; skip pipes with no usable OD.
    od = get_od(pipe, list(get_connectors(pipe))) or 0.0
    if od <= 0:
        continue
    dia_mm = int(round(od))

    # Slope -> 1:X run-length (per-pipe, not modal).
    s = abs(get_slope(pipe))
    if s > 1e-9:
        run = int(round(1.0 / s))
    else:
        run = 0

    label = u"{}mm @ 1:{}".format(dia_mm, run)

    # Leader side: text is at (mx+px*off, my+py*off), so the pipe sits
    # in the -perp direction from the text. If px > 0 the pipe is to the
    # LEFT of the text (leader exits LEFT); if px <= 0 it's to the RIGHT.
    leader_side = _LEADER_LEFT if px > 0 else _LEADER_RIGHT

    records.append({
        "label":      label,
        "anchor":     XYZ(ax, ay, mz),
        "leader_end": XYZ(mx, my, mz),
        "leader_side": leader_side,
    })

if not records:
    forms.alert("None of the selected pipes could be annotated\n"
                "(no Outside Diameter, or no XY run direction).",
                exitscript=True)


# ---------------------------------------------------------------------------
# 2. RESOLVE THE DEFAULT TEXTNOTETYPE
# ---------------------------------------------------------------------------
text_type_id = doc.GetDefaultElementTypeId(ElementTypeGroup.TextNoteType)
if text_type_id is None or text_type_id == ElementId.InvalidElementId:
    any_type = list(FilteredElementCollector(doc).OfClass(TextNoteType))
    if not any_type:
        forms.alert("This document has no TextNoteType loaded - cannot place "
                    "a text note.", exitscript=True)
    text_type_id = any_type[0].Id


# ---------------------------------------------------------------------------
# 3. PLACE ALL LABELS IN ONE TRANSACTION
# ---------------------------------------------------------------------------
t = Transaction(doc, "pyMEP: Annotate Pipes ({})".format(len(records)))
t.Start()
placed = 0
try:
    for rec in records:
        note = TextNote.Create(
            doc, view.Id, rec["anchor"], rec["label"], text_type_id)

        # Anchor any leader at the vertical MIDDLE of the text.
        if _LeaderAttach is not None:
            try:
                note.LeftAttachment  = _LeaderAttach.Midline
                note.RightAttachment = _LeaderAttach.Midline
            except Exception:
                pass

        # Leader from text mid-line to the pipe's midpoint. Per-leader
        # failure is swallowed so one bad pipe doesn't roll back the lot.
        try:
            leader = note.AddLeader(rec["leader_side"])
            leader.End = rec["leader_end"]
        except Exception:
            pass

        placed += 1

    t.Commit()
except Exception as ex:
    t.RollBack()
    forms.alert("Failed during batch placement (placed {} of {} before "
                "the error):\n\n{}: {}"
                .format(placed, len(records), type(ex).__name__, ex),
                exitscript=True)

# Close the pyRevit output window if anything opened it.
try:
    script.get_output().close()
except Exception:
    pass
