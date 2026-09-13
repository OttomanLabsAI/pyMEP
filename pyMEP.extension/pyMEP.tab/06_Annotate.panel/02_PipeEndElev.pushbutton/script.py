# -*- coding: utf-8 -*-
"""Pipe End Elev - place a Revit spot elevation at both ends of every
selected pipe in the active plan view. Each spot displays the INVERT
(bottom) elevation of the pipe at that point, regardless of the
SpotDimensionType's 'Display Elevations' configuration.

How it gets the invert value:

  1. The spot is created at the pipe centreline (Revit projects any
     shifted origin back onto the reference geometry, so we can't move
     the read-point via the API alone).
  2. The auto-computed value is read from `spot.ValueString` (Revit's
     formatted display string, in the project's elevation units).
  3. The pipe Outside Diameter is read via the shared get_od helper;
     half of it (the outside radius in mm) is subtracted from the
     parsed number. The elevation-base offset (project base, shared
     coords, level) is identical on both sides so it cancels out.
  4. The result is written back via Dimension.ValueOverride, replacing
     the number in-place while preserving any sign, prefix or suffix
     in the original display string.

Trade-off worth knowing: ValueOverride is STATIC. If the pipe's
diameter or invert elevation changes later, the override stays at the
old number. Re-run the button to refresh after any geometry edit.

If the OD is unreadable, or ValueOverride is unsupported in this
Revit build, the spot is left showing the auto-computed centreline
value rather than being skipped.
"""

__title__  = "Pipe End\nElev"
__author__ = "Glent Group"

import math
import re
import sys

# Reload pymep_* lib modules so the script picks up the latest helpers
for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (
    Transaction, ViewType, BuiltInCategory, XYZ, Options, Curve,
    GeometryInstance,
)
import System

# Units machinery for the invert override: the 2021+ ForgeTypeId API
# first, the pre-2021 UnitType API as the fallback, and None when a
# piece is missing (the override is then skipped and reported, never
# guessed).
try:
    from Autodesk.Revit.DB import UnitFormatUtils, FormatValueOptions
except ImportError:
    UnitFormatUtils = None
    FormatValueOptions = None
try:
    from Autodesk.Revit.DB import UnitUtils
except ImportError:
    UnitUtils = None
try:
    from Autodesk.Revit.DB import SpecTypeId
    _LENGTH_SPEC = SpecTypeId.Length
except Exception:
    _LENGTH_SPEC = None
try:
    from Autodesk.Revit.DB import UnitType
    _LENGTH_UT = UnitType.UT_Length
except Exception:
    _LENGTH_UT = None

from pyrevit import revit, forms, script

from pymep_config import get_annotate_pipe_offset_mm
from pymep_revit  import get_connectors, get_od, mm2ft
from pymep_revit import id_value, make_id
from pymep_revit import quiet

doc    = revit.doc
uidoc  = revit.uidoc
view   = doc.ActiveView


# ---------------------------------------------------------------------------
# Invert override - the displayed number lowered by the outside radius
# ---------------------------------------------------------------------------
def _spot_format(spot):
    # The FormatOptions the spot's own type formats its value with, or
    # None when it follows the project units.
    try:
        st = doc.GetElement(spot.GetTypeId())
        fo = st.GetUnitsFormatOptions()
        if fo is not None and not fo.UseDefault:
            return fo
    except Exception:
        pass
    return None


def _parse_length(units, text):
    # Display text -> internal feet through Revit's own parser, or None.
    if UnitFormatUtils is None:
        return None
    for spec in (_LENGTH_SPEC, _LENGTH_UT):
        if spec is None:
            continue
        try:
            out = clr.Reference[System.Double]()
            if UnitFormatUtils.TryParse(units, spec, text, out):
                return float(out.Value)
        except Exception:
            continue
    return None


def _format_length(units, fmt, value_ft):
    # Internal feet -> display text, honouring the spot type's own
    # format when it has one. None when no overload works.
    if UnitFormatUtils is None:
        return None
    fvo = None
    if fmt is not None and FormatValueOptions is not None:
        try:
            fvo = FormatValueOptions()
            fvo.SetFormatOptions(fmt)
        except Exception:
            fvo = None
    if _LENGTH_SPEC is not None:
        try:
            if fvo is not None:
                return UnitFormatUtils.Format(units, _LENGTH_SPEC, value_ft,
                                              False, fvo)
            return UnitFormatUtils.Format(units, _LENGTH_SPEC, value_ft, False)
        except Exception:
            pass
    if _LENGTH_UT is not None:
        try:
            if fvo is not None:
                return UnitFormatUtils.Format(units, _LENGTH_UT, value_ft,
                                              False, False, fvo)
            return UnitFormatUtils.Format(units, _LENGTH_UT, value_ft,
                                          False, False)
        except Exception:
            pass
    return None


def _number_in_unit(text, fmt):
    # A plain number in the spot type's own display unit -> internal
    # feet, for spot types formatted in a unit other than the project's.
    if fmt is None or UnitUtils is None:
        return None
    try:
        unit = fmt.GetUnitTypeId()
    except Exception:
        try:
            unit = fmt.DisplayUnits
        except Exception:
            return None
    dec = "."
    try:
        if "Comma" in str(doc.GetUnits().DecimalSymbol):
            dec = ","
    except Exception:
        pass
    m = re.search(r"-?\d[\d\s,.']*", text)
    if m is None:
        return None
    kept = []
    for ch in m.group(0):
        if ch.isdigit() or ch == "-":
            kept.append(ch)
        elif ch == dec:
            kept.append(".")
    try:
        return UnitUtils.ConvertToInternalUnits(float("".join(kept)), unit)
    except Exception:
        return None


def _invert_text(spot, radius_ft):
    """The spot's display text with its number lowered by the outside
    radius, or (None, reason). The number is read with Revit's own unit
    parser and written back with Revit's own formatter, and the rewrite
    only goes ahead when the formatted current value is found verbatim
    in the current text and the new text parses back to the new value -
    so prefixes, suffixes, thousands separators and metre-formatted
    spots either work or are left alone, never silently wrong."""
    try:
        current = spot.ValueString
    except Exception:
        current = None
    if not current:
        return None, "no value text"
    units = doc.GetUnits()
    fmt = _spot_format(spot)
    candidates = []
    for v in (_parse_length(units, current), _number_in_unit(current, fmt)):
        if v is not None and v not in candidates:
            candidates.append(v)
    if not candidates:
        return None, "value text not understood"
    for value_ft in candidates:
        old_txt = _format_length(units, fmt, value_ft)
        if not old_txt or old_txt not in current:
            continue
        target = value_ft - radius_ft
        new_txt = _format_length(units, fmt, target)
        if not new_txt:
            continue
        back = _parse_length(units, new_txt)
        if back is None:
            back = _number_in_unit(new_txt, fmt)
        back_old = _parse_length(units, old_txt)
        if back_old is None:
            back_old = _number_in_unit(old_txt, fmt)
        # allow the display rounding seen on the current value, at least 5 mm
        tol = mm2ft(5.0)
        if back_old is not None:
            tol = max(tol, 2.0 * abs(back_old - value_ft))
        if back is None or abs(back - target) > tol:
            continue
        return current.replace(old_txt, new_txt, 1), ""
    return None, "formatted value not found in the text"


# ---------------------------------------------------------------------------
# 0. PRE-FLIGHT
# ---------------------------------------------------------------------------
PLAN_VIEW_TYPES = (
    ViewType.FloorPlan,
    ViewType.CeilingPlan,
    ViewType.EngineeringPlan,
    ViewType.AreaPlan,
)
if view is None or view.ViewType not in PLAN_VIEW_TYPES:
    forms.alert("Open a plan view (Floor / Ceiling / Structural / Area) "
                "and try again.",
                exitscript=True)


def _cat_int(elem):
    """Element category id as int, compatible with Revit 2024+ (.Value)
    and earlier (.IntegerValue)."""
    if elem is None or elem.Category is None:
        return None
    cid = elem.Category.Id
    try:
        return cid.Value
    except AttributeError:
        return id_value(cid)

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
# 1. HELPERS
# ---------------------------------------------------------------------------
def _pipe_endpoints(pipe):
    """Return (XYZ, XYZ) endpoints of the pipe centreline in Revit ft."""
    loc = getattr(pipe, "Location", None)
    if loc is not None and hasattr(loc, "Curve") and loc.Curve is not None:
        c = loc.Curve
        return c.GetEndPoint(0), c.GetEndPoint(1)
    conns = list(get_connectors(pipe))
    if len(conns) >= 2:
        return conns[0].Origin, conns[1].Origin
    return None, None


def _get_centreline_ref(pipe):
    """Try multiple strategies to find a Reference suitable for
    NewSpotElevation on a pipe. Pipes typically have their centreline
    curve in non-visible geometry; some are wrapped in a
    GeometryInstance. Returns None only after all strategies fail."""
    # Each strategy is an (include_non_visible, use_view) combination.
    # Non-visible-first because that's where the pipe centreline lives.
    strategies = (
        (True,  None),    # everything except view-clipping
        (True,  view),    # everything including view geometry
        (False, None),    # only the visible solid - faces / edges
        (False, view),
    )
    for include_non_vis, opts_view in strategies:
        opts = Options()
        opts.ComputeReferences = True
        opts.IncludeNonVisibleObjects = include_non_vis
        if opts_view is not None:
            try:
                opts.View = opts_view
            except Exception:
                pass
        try:
            geom = pipe.get_Geometry(opts)
        except Exception:
            continue
        if geom is None:
            continue

        # Pass 1: a Curve at the top level (this is the centreline if
        # IncludeNonVisibleObjects gave it to us). Strongly preferred.
        for obj in geom:
            if obj is None:
                continue
            if isinstance(obj, Curve):
                ref = getattr(obj, "Reference", None)
                if ref is not None:
                    return ref

        # Pass 2: walk into any GeometryInstance for nested curves.
        for obj in geom:
            if not isinstance(obj, GeometryInstance):
                continue
            try:
                inst_geom = obj.GetInstanceGeometry()
            except Exception:
                continue
            if inst_geom is None:
                continue
            for inner in inst_geom:
                if isinstance(inner, Curve):
                    ref = getattr(inner, "Reference", None)
                    if ref is not None:
                        return ref

        # Pass 3: any top-level object with a Reference (last resort).
        for obj in geom:
            if obj is None:
                continue
            ref = getattr(obj, "Reference", None)
            if ref is not None:
                return ref

    return None


# ---------------------------------------------------------------------------
# 2. MAIN: place a spot elevation at each end of each pipe
# ---------------------------------------------------------------------------
offset_mm = get_annotate_pipe_offset_mm()
offset_ft = mm2ft(offset_mm)

# Per-reason skip counters so the final alert tells the user WHY rather
# than guessing between "no reference" and "incompatible type".
skip_no_endpoints  = 0
skip_no_reference  = 0
skip_api_returned_none = 0
skip_api_exception     = 0
override_ok            = 0     # invert value written
override_skipped       = 0     # spot left showing the centreline level
override_reasons       = {}
vertical_pipes     = 0     # not a skip - we still try, with a default dir

t = Transaction(doc, "pyMEP: Spot Elev at Pipe Ends ({} pipes)"
                    .format(len(pipes)))
quiet(t)
t.Start()
placed = 0
last_exception_msg = ""
try:
    for pipe in pipes:
        p0, p1 = _pipe_endpoints(pipe)
        if p0 is None or p1 is None:
            skip_no_endpoints += 1
            continue

        # Sign-normalised XY direction. For vertical pipes (drainage
        # stacks) dx and dy are both 0; use a default +Y perpendicular
        # so the label still places (both ends will overlap in plan -
        # the user can drag them apart).
        dx = p1.X - p0.X
        dy = p1.Y - p0.Y
        if dx < 0 or (dx == 0.0 and dy < 0):
            dx, dy = -dx, -dy
        mag = math.sqrt(dx * dx + dy * dy)
        if mag < 1e-9:
            vertical_pipes += 1
            px = 0.0
            py = 1.0    # default +Y offset
        else:
            ux = dx / mag
            uy = dy / mag
            px = -uy
            py = ux

        ref = _get_centreline_ref(pipe)
        if ref is None:
            skip_no_reference += 1
            continue

        # Pipe outside radius in Revit feet - used to shift the spot
        # from the centreline down to the INVERT (bottom of pipe).
        # If we can't read the OD, fall back to no shift (centreline).
        od_mm = get_od(pipe, list(get_connectors(pipe))) or 0.0
        radius_ft = mm2ft(od_mm * 0.5)

        for end_pt in (p0, p1):
            # Invert point: same XY as the centreline endpoint, Z
            # lowered by the outside radius. Used as both the spot
            # origin (where Revit reads the elevation) and the refPt
            # (where the dot/indicator is drawn). The reference still
            # points to the pipe centreline curve so Revit knows what
            # element the spot is hosted on.
            invert_pt = XYZ(end_pt.X,
                            end_pt.Y,
                            end_pt.Z - radius_ft)
            text_pos = XYZ(invert_pt.X + px * offset_ft,
                           invert_pt.Y + py * offset_ft,
                           invert_pt.Z)
            try:
                spot = doc.Create.NewSpotElevation(
                    view,        # active plan view
                    ref,         # reference to pipe centreline
                    invert_pt,   # origin (snap to invert, not centreline)
                    text_pos,    # bend (= text for a straight leader)
                    text_pos,    # end (text position)
                    invert_pt,   # refPt (dot on the pipe invert)
                    True,        # hasLeader
                )
                if spot is not None:
                    placed += 1
                    # Revit reads the elevation off the centreline
                    # reference even with an invert-shifted origin, so
                    # the auto value is the CENTRELINE level. The
                    # displayed number is lowered by the outside radius
                    # through Revit's own unit parser and formatter; when
                    # the text cannot be understood the spot is left
                    # showing the centreline level and reported - never
                    # a guessed number. ValueOverride is STATIC: re-run
                    # after a diameter or level change.
                    if od_mm > 0:
                        new_str, why = _invert_text(spot, radius_ft)
                        if new_str is None:
                            override_skipped += 1
                            override_reasons[why] = \
                                override_reasons.get(why, 0) + 1
                        else:
                            try:
                                spot.ValueOverride = new_str
                                override_ok += 1
                            except Exception as ex:
                                override_skipped += 1
                                why = "ValueOverride refused ({})".format(
                                    type(ex).__name__)
                                override_reasons[why] = \
                                    override_reasons.get(why, 0) + 1
                else:
                    skip_api_returned_none += 1
            except Exception as ex:
                skip_api_exception += 1
                last_exception_msg = "{}: {}".format(type(ex).__name__, ex)

    t.Commit()
except Exception as ex:
    t.RollBack()
    forms.alert("Failed mid-batch (placed {} so far):\n\n{}: {}"
                .format(placed, type(ex).__name__, ex),
                exitscript=True)


# ---------------------------------------------------------------------------
# 3. REPORT
# ---------------------------------------------------------------------------
if placed == 0 or (skip_no_reference + skip_api_returned_none
                   + skip_api_exception + skip_no_endpoints
                   + override_skipped) > 0:
    lines = ["Placed: {}    Pipes selected: {}".format(placed, len(pipes))]
    if override_skipped:
        lines.append("Invert value NOT written on {} spot(s) - they show "
                     "the CENTRELINE level: {}".format(
                         override_skipped,
                         "; ".join("{} x {}".format(n, why) for why, n in
                                   sorted(override_reasons.items()))))
    if vertical_pipes:
        lines.append("Vertical pipes (placed with default +Y offset): {}"
                     .format(vertical_pipes))
    if skip_no_endpoints:
        lines.append("Skipped (no usable centreline): {}"
                     .format(skip_no_endpoints))
    if skip_no_reference:
        lines.append("Skipped (no Reference could be built for the pipe "
                     "centreline - try a different view): {}"
                     .format(skip_no_reference))
    if skip_api_returned_none:
        lines.append("NewSpotElevation returned null: {}  (often means "
                     "the active SpotDimensionType isn't valid for pipes)"
                     .format(skip_api_returned_none))
    if skip_api_exception:
        lines.append("NewSpotElevation threw an exception: {}  (last: {})"
                     .format(skip_api_exception, last_exception_msg))
    forms.alert("\n\n".join(lines))

# Close the pyRevit output window if anything opened it.
try:
    script.get_output().close()
except Exception:
    pass
