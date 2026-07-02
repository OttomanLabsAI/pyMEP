# -*- coding: utf-8 -*-
"""Annotate Duct Group - tag a selection of parallel pipes / conduits with
a 'NxM - kNo.D\u00d8' label placed at a user-picked point in the active plan
view, with a leader arrow pointing at the centroid of each detected
sub-group.

Workflow:
  1. Pre-select pipes / conduits in a plan view.
  2. Click the button.
  3. The script computes:
       - the dominant run direction (average pipe XY direction),
       - each pipe's perpendicular offset and Z,
       - a 2D occupancy grid (perp clusters x Z clusters),
       - a greedy max-rectangle decomposition of that grid, so a mixed
         selection (e.g. a 2x1 cap sitting near a 3x2 bank) is reported
         as '3x2 + 2x1' instead of a wrong single grid,
       - the modal Outside Diameter in mm across the whole selection.
  4. A placement cursor appears - click anywhere in the view to drop a
     two-line TextNote, e.g.

         3x2 + 2x1 - 8No.110\u00d8
         PVCU DUCTS

     (single-group selections still produce the simpler '3x1 - 3No.200\u00d8'
     form.) One leader per detected sub-rectangle is added automatically,
     attached at the vertical middle of the text and pointing to that
     sub-group's centroid.

The suffix on the second line ('PVCU DUCTS' by default) is configurable in
Settings > Annotate > Set annotate suffix text.
"""

__title__  = "Annotate\nDucts"
__author__ = "Glent Group"

import math
import sys
from collections import Counter

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
from Autodesk.Revit.Exceptions import OperationCanceledException

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

# Vertical attachment side of the leader on the text - the Revit API
# misspells this enum as `LeaderAtachement` (since 2018). Try the typo
# first, then the correctly-spelt name as a fallback for any future
# rename, and fall through to None if neither resolves.
try:
    from Autodesk.Revit.DB import LeaderAtachement as _LeaderAttach
except ImportError:
    try:
        from Autodesk.Revit.DB import LeaderAttachement as _LeaderAttach
    except ImportError:
        _LeaderAttach = None

from pyrevit import revit, forms, script

from pymep_config import get_annotate_suffix
from pymep_revit  import get_connectors, get_od, mm2ft

doc    = revit.doc
uidoc  = revit.uidoc
view   = doc.ActiveView


# ---------------------------------------------------------------------------
# 0. PRE-FLIGHT: must be in a plan view, must have a selection
# ---------------------------------------------------------------------------
PLAN_VIEW_TYPES = (
    ViewType.FloorPlan,
    ViewType.CeilingPlan,
    ViewType.EngineeringPlan,
    ViewType.AreaPlan,
)
if view is None or view.ViewType not in PLAN_VIEW_TYPES:
    forms.alert("Open a plan view (Floor / Ceiling / Structural / Area) and "
                "try again.\n\nAnnotate Ducts assumes XY is the horizontal "
                "plane and Z is elevation, which only holds in plan views.",
                exitscript=True)

PIPE_CAT_INTS = (
    int(BuiltInCategory.OST_PipeCurves),
    int(BuiltInCategory.OST_Conduit),
)

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

sel_ids = list(uidoc.Selection.GetElementIds())
pipes = []
for eid in sel_ids:
    e = doc.GetElement(eid)
    if _cat_int(e) in PIPE_CAT_INTS:
        pipes.append(e)

if not pipes:
    forms.alert("Select one or more pipes / conduits in the view first, then "
                "click the button.\n\n"
                "({} element(s) selected, none of them pipes or conduits.)"
                .format(len(sel_ids)),
                exitscript=True)


# ---------------------------------------------------------------------------
# 1. ENDPOINTS + RUN DIRECTION
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

endpoints = []
for p in pipes:
    p0, p1 = _pipe_endpoints(p)
    if p0 is not None and p1 is not None:
        endpoints.append((p, p0, p1))

if not endpoints:
    forms.alert("None of the selected elements have a usable centreline "
                "(no Location.Curve and not enough connectors).",
                exitscript=True)

# Average XY direction. Each pipe's (dx, dy) is flipped to align with the
# running sum if needed, so anti-parallel pipes don't cancel each other.
sx, sy = 0.0, 0.0
for _, p0, p1 in endpoints:
    dx, dy = (p1.X - p0.X), (p1.Y - p0.Y)
    if sx == 0.0 and sy == 0.0:
        sx, sy = dx, dy
        continue
    if (dx * sx + dy * sy) < 0:
        dx, dy = -dx, -dy
    sx += dx; sy += dy

mag = math.sqrt(sx * sx + sy * sy)
if mag < 1e-9:
    forms.alert("Cannot determine a dominant pipe run direction from the "
                "selection (all centrelines are vertical or zero length).",
                exitscript=True)
ux, uy = sx / mag, sy / mag      # unit run direction in XY
px, py = -uy, ux                 # perpendicular (rotate +90 deg in XY)


# ---------------------------------------------------------------------------
# 2. PER-PIPE PERP / Z / OD
# ---------------------------------------------------------------------------
# perp_ft = signed offset of pipe midpoint along the perpendicular-to-run
# axis (in feet, Revit internal). z_ft = pipe midpoint Z (feet).
records  = []     # (perp_ft, z_ft, od_mm, pipe)
mid_pts  = []     # (mx_ft, my_ft, mz_ft) - used to centroid the leader End
for pipe, p0, p1 in endpoints:
    mx = (p0.X + p1.X) * 0.5
    my = (p0.Y + p1.Y) * 0.5
    mz = (p0.Z + p1.Z) * 0.5
    perp = mx * px + my * py
    od = get_od(pipe, list(get_connectors(pipe))) or 0.0
    records.append((perp, mz, od, pipe))
    mid_pts.append((mx, my, mz))


# ---------------------------------------------------------------------------
# 3. CLUSTER -> 2D OCCUPANCY -> RECTANGLE DECOMPOSITION
# ---------------------------------------------------------------------------
# Tolerance: half the largest OD, with a 50 mm floor so very thin conduits
# still cluster sensibly.  `records` is guaranteed non-empty here.
od_vals = [r[2] for r in records]
max_od_mm = max(od_vals) if od_vals else 0.0
tol_mm = max(max_od_mm * 0.5, 50.0)
tol_ft = mm2ft(tol_mm)


def _cluster_assign(values, tol):
    """Cluster 1D values: sort, split where the gap exceeds `tol`. Returns
    (centroids, assignments) where centroids is a sorted-ascending list of
    cluster mean values and assignments is the same length as `values`,
    mapping each original index to its cluster id (0-based, in ascending
    order of centroid)."""
    n = len(values)
    if n == 0:
        return [], []
    order = sorted(range(n), key=lambda i: values[i])
    cid_sorted = [0] * n
    for i in range(1, n):
        if (values[order[i]] - values[order[i - 1]]) > tol:
            cid_sorted[i] = cid_sorted[i - 1] + 1
        else:
            cid_sorted[i] = cid_sorted[i - 1]
    k = cid_sorted[-1] + 1
    sums   = [0.0] * k
    counts = [0]   * k
    for i in range(n):
        cid = cid_sorted[i]
        sums[cid]   += values[order[i]]
        counts[cid] += 1
    centroids = [sums[c] / counts[c] for c in range(k)]
    assignments = [0] * n
    for sorted_pos, orig_idx in enumerate(order):
        assignments[orig_idx] = cid_sorted[sorted_pos]
    return centroids, assignments


def _find_max_rect(matrix):
    """Return (area, top, left, h, w) for the largest all-ones rectangle
    inside `matrix` (list of lists of 0/1). Returns (0, 0, 0, 0, 0) if
    the matrix has no 1-cells."""
    n_rows = len(matrix)
    if n_rows == 0:
        return (0, 0, 0, 0, 0)
    n_cols = len(matrix[0])
    if n_cols == 0:
        return (0, 0, 0, 0, 0)
    best = (0, 0, 0, 0, 0)
    for top in range(n_rows):
        for left in range(n_cols):
            if matrix[top][left] != 1:
                continue
            # Width of all-ones run on `top` row starting at `left`
            max_w_top = 0
            while left + max_w_top < n_cols and matrix[top][left + max_w_top] == 1:
                max_w_top += 1
            current_max_w = max_w_top
            for h in range(1, n_rows - top + 1):
                if h > 1:
                    w_here = 0
                    while w_here < current_max_w and matrix[top + h - 1][left + w_here] == 1:
                        w_here += 1
                    current_max_w = min(current_max_w, w_here)
                    if current_max_w == 0:
                        break
                area = h * current_max_w
                # Tie-break: on equal area, prefer the wider rectangle so
                # ductbank labels read 'wide x stacked' (e.g. 3x2 not 2x3).
                # Without this, an ambiguous occupancy like
                #     [[1,1,0],
                #      [1,1,1],
                #      [1,1,1]]
                # would pick the 2x3 left-block first and report '2x3 + 1x2'
                # instead of the conventionally correct '3x2 + 2x1'.
                if (area > best[0]
                        or (area == best[0] and current_max_w > best[4])):
                    best = (area, top, left, h, current_max_w)
    return best


# Cluster perp (across-run) and Z (stacked) positions into discrete cells.
perp_centroids, perp_assign = _cluster_assign([r[0] for r in records], tol_ft)
z_centroids,    z_assign    = _cluster_assign([r[1] for r in records], tol_ft)

n_perp = len(perp_centroids)
n_z    = len(z_centroids)

# Build [n_z][n_perp] binary occupancy matrix + cell -> [pipe indices] map.
occupancy = [[0] * n_perp for _ in range(n_z)]
cell_to_pipes = {}
for i in range(len(records)):
    z = z_assign[i]
    p = perp_assign[i]
    occupancy[z][p] = 1
    cell_to_pipes.setdefault((z, p), []).append(i)

# Greedy max-rectangle decomposition: repeatedly carve off the largest
# all-ones rectangle. Each rectangle becomes one 'WxH' sub-group, and the
# pipes inside it get a dedicated leader pointing to their centroid.
_work = [row[:] for row in occupancy]
rects = []     # list of (cols, rows, [pipe_indices])
while True:
    area, top, left, h, w = _find_max_rect(_work)
    if area == 0:
        break
    pipe_indices = []
    for r in range(h):
        for c in range(w):
            pipe_indices.extend(cell_to_pipes.get((top + r, left + c), []))
            _work[top + r][left + c] = 0
    rects.append((w, h, pipe_indices))   # w=cols, h=rows
rects.sort(key=lambda x: -x[0] * x[1])   # biggest first


# ---------------------------------------------------------------------------
# 4. DIAMETER + LABEL TEXT
# ---------------------------------------------------------------------------
# Modal OD in mm across the selection (rounded to nearest mm before counting,
# so 109.99 and 110.01 still land in the same bucket). Fall back to the max
# OD if every pipe somehow has OD = 0.
dia_buckets = Counter(int(round(r[2])) for r in records if r[2] > 0)
if dia_buckets:
    dia_mm = dia_buckets.most_common(1)[0][0]
else:
    dia_mm = int(round(max_od_mm))

n_total = len(records)
suffix  = get_annotate_suffix() or "PVCU DUCTS"

# Line 1: single rectangle  -> '3x1 - 3No.200Ø'  (Ø = U+00D8)
#         multiple rectangles -> '3x2 + 2x1 - 8No.110Ø'
# Line 2: suffix (from settings)
try:
    _text_t = unicode      # IronPython 2.7 / CPython 2
except NameError:
    _text_t = str          # CPython 3

def _u(s):
    """Coerce to a unicode/str text instance for the active runtime, decoding
    PY2 bytes from UTF-8 if needed."""
    if isinstance(s, _text_t):
        return s
    try:
        return s.decode("utf-8", "replace")
    except (AttributeError, UnicodeError):
        return _text_t(s)

if len(rects) <= 1:
    cols, rows = (rects[0][0], rects[0][1]) if rects else (1, 1)
    line1 = u"{}x{} - {}No.{}\u00d8".format(cols, rows, n_total, dia_mm)
else:
    parts = [u"{}x{}".format(w, h) for w, h, _ in rects]
    line1 = u"{} - {}No.{}\u00d8".format(u" + ".join(parts), n_total, dia_mm)
line2 = _u(suffix)
label = line1 + u"\n" + line2


# ---------------------------------------------------------------------------
# 5. PICK A POINT + PLACE TEXTNOTE
# ---------------------------------------------------------------------------
try:
    pt = uidoc.Selection.PickPoint(
        "Click to place duct label: {} ({} pipes)".format(line1, n_total))
except OperationCanceledException:
    forms.alert("Cancelled - no annotation placed.", exitscript=True)

# Default TextNoteType; if the document has none set, fall back to any
# TextNoteType in the project.
text_type_id = doc.GetDefaultElementTypeId(ElementTypeGroup.TextNoteType)
if text_type_id is None or text_type_id == ElementId.InvalidElementId:
    any_type = list(FilteredElementCollector(doc).OfClass(TextNoteType))
    if not any_type:
        forms.alert("This document has no TextNoteType loaded - cannot place "
                    "a text note.", exitscript=True)
    text_type_id = any_type[0].Id

t = Transaction(doc, "pyMEP: Annotate Ducts")
t.Start()
try:
    note = TextNote.Create(doc, view.Id, pt, label, text_type_id)

    # Anchor any leader at the vertical MIDDLE of the text (so the
    # arrow connects to mid-line, not top or bottom). Older Revit
    # versions without LeaderAtachement still get a usable leader,
    # just attached at the default position.
    if _LeaderAttach is not None:
        try:
            note.LeftAttachment  = _LeaderAttach.Midline
            note.RightAttachment = _LeaderAttach.Midline
        except Exception:
            pass

    # One leader per detected sub-rectangle, each pointing at the
    # centroid of the pipes inside that sub-group. For a single-group
    # selection this gives exactly one leader (same as before).
    # The leader exits whichever side of the text is closer to that
    # centroid. End Z = picked-point Z so the leader sits cleanly in
    # the active view plane.
    for _cols, _rows, pipe_indices in rects:
        if not pipe_indices:
            continue
        group_mids = [mid_pts[i] for i in pipe_indices]
        cx = sum(p[0] for p in group_mids) / len(group_mids)
        cy = sum(p[1] for p in group_mids) / len(group_mids)
        leader_side = _LEADER_RIGHT if cx >= pt.X else _LEADER_LEFT
        try:
            leader = note.AddLeader(leader_side)
            leader.End = XYZ(cx, cy, pt.Z)
        except Exception:
            # Per-leader failure is non-fatal - the text itself stays
            # and other leaders still get a chance.
            pass

    t.Commit()
except Exception as ex:
    t.RollBack()
    forms.alert("Failed to place text note:\n\n{}: {}"
                .format(type(ex).__name__, ex),
                exitscript=True)

# Close the pyRevit output window if anything in this script opened it
# (the user asked for the terminal to close at the end).
try:
    script.get_output().close()
except Exception:
    pass
