# -*- coding: utf-8 -*-
"""Connect plumbing fixtures into a main pipe: a vertical downpipe from
each fixture's outlet connector, an elbow, a sloped branch falling at
1:n toward the main, and a TEE JUNCTION where it meets it - the main is
split at the branch point and the two halves + branch joined with a tee
(takeoff only as a reported fallback). The main itself can be re-graded
at its own 1:n first, keeping its low end where it is.

Geometry per fixture (plan-projected onto the main's centreline):

    fixture outlet -> downpipe -> elbow -> sloped branch (1:n) -> tee
                                                                  in main

Upstream invert: by default it stays WHERE THE MODEL CURRENTLY PUTS IT -
the branch end meets the main's centreline as it lies today and the
elbow level derives back up the slope. Typing an absolute invert in the
dialog fixes the elbow level instead (branch centreline = invert + D/2)
and the far end derives down the slope; the takeoff still connects it to
the main.

The branch takes the MAIN's pipe type, system type and reference level,
so everything reads as one system. The diameter comes from the dialog
(defaulting to the fixture outlet size) and is snapped to the main
type's routing sizes.

Pure geometry at the top (unit-tested under CPython by
``tests/test_connect_fixtures.py`` - stdlib only); Revit API access
below, reusing the gully-connect helpers. IronPython 2.7 / Revit
2021-2026 safe.
"""

import clr
clr.AddReference("RevitAPI")

import math

from Autodesk.Revit.DB import (
    XYZ, Transaction, BuiltInParameter, ElementId, Line,
    FilteredElementCollector, FamilyInstance,
)
from Autodesk.Revit.DB.Plumbing import Pipe

from pymep_revit import get_connectors, safe_name, mm2ft, ft2mm
from pymep_gully_connect import (
    gully_outlet, _snap_dia_ft, _conn_near, _set_dia, _host_level,
)


MIN_LEN_FT = mm2ft(50.0)


def main_gradient(a, b):
    """The main's current gradient as the n of 1:n (plan run per unit of
    fall), or None when it is level (or vertical). Pure."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    run = math.sqrt(dx * dx + dy * dy)
    fall = abs(b[2] - a[2])
    if run < 1e-9 or fall < 1e-9:
        return None
    return run / fall


def regrade_main_ends(a, b, slope_n, keep="low"):
    """Re-grade the main at 1:slope_n, pinning ONE end's level:
    ``keep`` = "low" keeps the LOWER end where it is and the higher end
    becomes low_z + plan_run / n; "high" keeps the UPPER end and the
    lower becomes high_z - plan_run / n. Ends level -> the second end is
    treated as the low one. Returns (a2, b2), the pinned end the exact
    input tuple. Pure."""
    if not slope_n or slope_n <= 0:
        return a, b
    dx, dy = b[0] - a[0], b[1] - a[1]
    run = math.sqrt(dx * dx + dy * dy)
    fall = run / slope_n
    a_low = a[2] < b[2]
    pin_a = (a_low and keep == "low") or ((not a_low) and keep == "high")
    if pin_a:
        return a, (b[0], b[1], a[2] + (fall if keep == "low" else -fall))
    return (a[0], a[1], b[2] + (fall if keep == "low" else -fall)), b


def plan_dist_to_segment(p, a, b):
    """Plan (XY) distance from point ``p`` to the segment a-b. Pure -
    used to pick WHICH main segment a fixture ties into once tees have
    split the original main."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    dd = dx * dx + dy * dy
    if dd < 1e-12:
        t = 0.0
    else:
        t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / dd
        t = max(0.0, min(1.0, t))
    mx, my = a[0] + t * dx, a[1] + t * dy
    return math.sqrt((p[0] - mx) ** 2 + (p[1] - my) ** 2)


# ---------------------------------------------------------------------------
# pure geometry (stdlib only - unit-tested without Revit)
# ---------------------------------------------------------------------------
def ray_hits_main(outlet, direction, main_a, main_b):
    """Where a plan RAY from the outlet along ``direction`` (the node's
    facing) meets the main's plan line: (mx, my, t) with t the main's
    0..1 parameter (a small overshoot clamps onto the run). None when
    the ray is parallel, points away, or misses the run - the caller
    falls back to the plan-nearest point. Pure."""
    ox, oy = outlet[0], outlet[1]
    ax, ay = main_a[0], main_a[1]
    dx, dy = main_b[0] - ax, main_b[1] - ay
    ex, ey = direction[0], direction[1]
    el = math.sqrt(ex * ex + ey * ey)
    if el < 1e-9:
        return None
    ex, ey = ex / el, ey / el
    det = dx * ey - ex * dy
    if abs(det) < 1e-9:
        return None                          # parallel to the main
    rx, ry = ox - ax, oy - ay
    s = (rx * dy - ry * dx) / det            # distance along the ray
    t = (rx * ey - ry * ex) / det            # parameter along the main
    if s < MIN_LEN_FT:
        return None                          # behind / on top of the node
    if t < -0.05 or t > 1.05:
        return None                          # misses the run
    t = max(0.0, min(1.0, t))
    return (ax + t * dx, ay + t * dy, t)


def branch_points(outlet, main_a, main_b, slope_n, dia_ft, invert_m=None,
                  direction=None, drop_pipe=True):
    """Where the branch runs, all coordinates internal feet.

    outlet / main_a / main_b: (x, y, z) tuples - the fixture outlet and
    the main pipe's centreline endpoints. slope_n: the 1:n ratio (fall =
    run / n; 0 or None = level). dia_ft: branch diameter.

    ``direction`` (plan vector, e.g. the node's FacingOrientation)
    aims the branch: it meets the main where the facing RAY crosses it,
    falling back to the plan-nearest point when the ray misses.

    ``drop_pipe`` picks the geometry (the family's 'Drop Pipe' yes/no):
      True  - DROP FIRST (classic): vertical drop under the outlet,
              then the graded run to the main;
      False - GRADE FIRST: the run falls at 1:n straight from the
              outlet, then a vertical DROP down onto the main.

    invert_m (drop-first only) fixes the upstream INVERT (metres,
    absolute): elbow centreline = invert + D/2, the end derives DOWN
    the slope. None keeps the branch end ON the main's centreline as
    it lies. Grade-first starts AT the outlet, so invert_m is ignored.

    An OBLIQUE aim gets its last bit SQUARED: standard tee fittings
    only place at ~90 degrees, so when the facing ray meets the main
    more than ~10 degrees off perpendicular, the run elbows just short
    of it into a short plan-perpendicular STUB (max of 2 diameters and
    300 mm) and the tee lands square.

    Returns a dict:
      points         the centreline polyline, outlet first, end last
      stub (x,y)     plan corner where the squared stub starts (None
                     when the approach is already square)
      bend (x,y,z)   the corner next to the vertical piece
      end (x,y,z)    the branch end at the main
      run_xy_ft      plan length of the graded run (incl. the stub)
      drop_ft        the vertical piece's length (<=0 = none)
      mode           "drop_first" | "drop_last"
      aimed          True when the facing ray set the end point
      upstream_invert_m   the resulting upstream invert (centreline -
                          D/2), for reporting and dialog defaults
    """
    ox, oy, oz = outlet
    ax, ay, az = main_a
    bx, by, bz = main_b

    hit = None
    if direction is not None:
        hit = ray_hits_main(outlet, direction, main_a, main_b)
    if hit is not None:
        mx, my, t = hit
    else:
        dx, dy = bx - ax, by - ay
        dd = dx * dx + dy * dy
        if dd < 1e-12:
            t = 0.0
        else:
            t = ((ox - ax) * dx + (oy - ay) * dy) / dd
            t = max(0.0, min(1.0, t))
        mx = ax + t * dx
        my = ay + t * dy

    # oblique approach? square the last bit so the tee can place
    stub = None
    if hit is not None:
        vx, vy = bx - ax, by - ay
        vl = math.sqrt(vx * vx + vy * vy)
        ux, uy = mx - ox, my - oy
        ul = math.sqrt(ux * ux + uy * uy)
        if vl > 1e-9 and ul > 1e-9:
            vx, vy = vx / vl, vy / vl
            ux, uy = ux / ul, uy / ul
            dot = abs(ux * vx + uy * vy)
            if dot > 0.17:                       # > ~10 deg off square
                sin_t = math.sqrt(max(1.0 - dot * dot, 1e-6))
                perp = max(2.0 * dia_ft, 300.0 / 304.8)
                s = perp / sin_t
                if ul > s + MIN_LEN_FT:
                    px, py = mx - ux * s, my - uy * s
                    # perpendicular foot of the stub corner on the main
                    t2 = ((px - ax) * (bx - ax) + (py - ay) * (by - ay)) \
                        / max((bx - ax) ** 2 + (by - ay) ** 2, 1e-12)
                    t2 = max(0.0, min(1.0, t2))
                    mx = ax + t2 * (bx - ax)
                    my = ay + t2 * (by - ay)
                    t = t2
                    stub = (px, py)
    mz = az + t * (bz - az)          # main centreline Z at that point

    if stub is not None:
        l_run = math.sqrt((stub[0] - ox) ** 2 + (stub[1] - oy) ** 2)
        l_stub = math.sqrt((mx - stub[0]) ** 2 + (my - stub[1]) ** 2)
    else:
        l_run = math.sqrt((ox - mx) ** 2 + (oy - my) ** 2)
        l_stub = 0.0
    run = l_run + l_stub
    fall = (run / slope_n) if (slope_n and slope_n > 0) else 0.0

    def graded(z_start):
        """the graded plan points with z falling from ``z_start``."""
        pts = []
        if stub is not None:
            z_p = z_start - (l_run / slope_n if slope_n else 0.0)
            pts.append((stub[0], stub[1], z_p))
        return pts

    if not drop_pipe:
        # grade from the outlet, then drop onto the main
        bend_z = oz - fall
        points = [(ox, oy, oz)] + graded(oz) + \
            [(mx, my, bend_z), (mx, my, mz)]
        return {"points": points, "stub": stub,
                "bend": (mx, my, bend_z),
                "end": (mx, my, mz),
                "run_xy_ft": run,
                "drop_ft": bend_z - mz,
                "mode": "drop_last",
                "aimed": hit is not None,
                "upstream_invert_m": (oz - dia_ft / 2.0) * 304.8 / 1000.0}

    if invert_m is None:
        end_z = mz
        bend_z = mz + fall
    else:
        bend_z = invert_m * 1000.0 / 304.8 + dia_ft / 2.0
        end_z = bend_z - fall

    points = [(ox, oy, oz), (ox, oy, bend_z)] + graded(bend_z) + \
        [(mx, my, end_z)]
    return {"points": points, "stub": stub,
            "bend": (ox, oy, bend_z),
            "end": (mx, my, end_z),
            "run_xy_ft": run,
            "drop_ft": oz - bend_z,
            "mode": "drop_first",
            "aimed": hit is not None,
            "upstream_invert_m": (bend_z - dia_ft / 2.0) * 304.8 / 1000.0}


# ---------------------------------------------------------------------------
# Revit API access
# ---------------------------------------------------------------------------
def main_pipe_info(main):
    """(a, b, type_id, system_id, level_id, dia_ft) of the main pipe -
    everything the branches inherit. Raises with a clear message when the
    main can't provide it."""
    try:
        crv = main.Location.Curve
        a = crv.GetEndPoint(0)
        b = crv.GetEndPoint(1)
    except Exception:
        raise RuntimeError("The selected main pipe has no straight "
                           "location curve.")
    type_id = main.GetTypeId()
    sys_id = None
    try:
        sp = main.get_Parameter(BuiltInParameter.RBS_PIPING_SYSTEM_TYPE_PARAM)
        if sp is not None:
            sys_id = sp.AsElementId()
    except Exception:
        pass
    if sys_id is None or sys_id == ElementId.InvalidElementId:
        raise RuntimeError("The main pipe has no piping system type.")
    lvl_id = None
    try:
        lp = main.get_Parameter(BuiltInParameter.RBS_START_LEVEL_PARAM)
        if lp is not None:
            lvl_id = lp.AsElementId()
    except Exception:
        pass
    dia_ft = 0.0
    try:
        dp = main.get_Parameter(BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
        if dp is not None:
            dia_ft = dp.AsDouble()
    except Exception:
        pass
    return ((a.X, a.Y, a.Z), (b.X, b.Y, b.Z), type_id, sys_id, lvl_id,
            dia_ft)


def fixture_outlet_info(fixture):
    """((x,y,z), dia_mm or None) - the outlet connector, most-downward
    then lowest (same rule as the gully button). A family with NO
    connector hangs its branch from its LOCATION POINT: the node
    families are spheres placed by their centre, and the gully-style
    bounding-box bottom (extension geometry and all) started the drop
    below the ball instead of at it."""
    o, dia_mm = gully_outlet(fixture)
    if o is None:
        return None, None
    if not get_connectors(fixture):
        try:
            p = fixture.Location.Point
            if p is not None:
                return (p.X, p.Y, p.Z), dia_mm
        except Exception:
            pass
    return (o.X, o.Y, o.Z), dia_mm


def list_node_types(doc):
    """Every placed, point-placed family type in the model - the NODES a
    main can be piped up from, ACROSS ALL CATEGORIES.

    A pipe connector is not required: families without one (Generic
    Model chambers, cylinders placed by Place Structures, ...) still
    work - the outlet falls back to the instance's location/bounding-box
    bottom and the diameter to the dialog's fallback. Only families with
    no usable origin at all (curve-based instances) are skipped.

    Returns [(label, [instances]), ...] sorted by label, label =
    'Family : Type'."""
    groups = {}
    for inst in FilteredElementCollector(doc).OfClass(FamilyInstance):
        try:
            if not get_connectors(inst) and not _has_point(inst):
                continue
            sym = inst.Symbol
            key = sym.Id.IntegerValue
            if key not in groups:
                try:
                    fam = safe_name(sym.Family)
                except Exception:
                    fam = "?"
                groups[key] = ("{} : {}".format(fam, safe_name(sym)), [])
            groups[key][1].append(inst)
        except Exception:
            continue
    return sorted(groups.values(), key=lambda t: t[0].lower())


DROP_PIPE_PARAM = "Drop Pipe"


def node_direction(inst):
    """The node's plan facing direction - its ROTATION aims the branch.
    None when the instance has no usable facing."""
    try:
        f = inst.FacingOrientation
        v = (f.X, f.Y)
        if abs(v[0]) + abs(v[1]) > 1e-6:
            return v
    except Exception:
        pass
    return None


def node_directions(inst):
    """Candidate plan directions from the node's ROTATION, in priority
    order: the FACING pair (facing, then its opposite), then the HAND
    pair. Families draw their direction wire along different local
    axes, so the caller tries each and keeps the FIRST whose ray meets
    the main. Within a pair only ONE direction can hit forward (they
    point apart), so pair order is what matters: a long main can be hit
    by both pairs 90 degrees apart - the facing pair, the axis families
    draw their wire on, must win."""
    out = []
    for attr in ("FacingOrientation", "HandOrientation"):
        try:
            v = getattr(inst, attr)
            d = (v.X, v.Y)
            if abs(d[0]) + abs(d[1]) > 1e-6:
                out.append(d)
                out.append((-d[0], -d[1]))
        except Exception:
            continue
    return out


def node_aim_directions(inst):
    """The directions the node's drawn ARROW points: these node
    families draw their direction wire along the OPPOSITE of the API
    FacingOrientation, so the aim rays are -facing, then -hand - and
    never the other two, so a miss cannot latch onto a pipe behind
    the arrow. Against one known main the full pairs stay the right
    tool (there they only choose the tee point)."""
    return node_directions(inst)[1::2]


def node_drop_pipe(inst):
    """The family's 'Drop Pipe' yes/no (instance first, then type):
    ticked (or absent) = classic drop-first geometry; unticked = grade
    from the outlet first, then drop onto the main."""
    for el in (inst, getattr(inst, "Symbol", None)):
        if el is None:
            continue
        try:
            p = el.LookupParameter(DROP_PIPE_PARAM)
            if p is not None and p.HasValue \
                    and str(p.StorageType) == "Integer":
                return p.AsInteger() == 1
        except Exception:
            pass
    return True


def node_pose(inst):
    """The rotation + Drop Pipe signature Update Nodes watches: turning
    the node or toggling the family's 'Drop Pipe' yes/no changes it,
    and the branch gets rebuilt. JSON-stable (rounded floats / None /
    bool)."""
    sig = []
    for attr in ("FacingOrientation", "HandOrientation"):
        try:
            v = getattr(inst, attr)
            sig.extend([round(v.X, 4), round(v.Y, 4)])
        except Exception:
            sig.extend([None, None])
    sig.append(bool(node_drop_pipe(inst)))
    return sig


DIA_PARAM_NAMES = ["DIA", "Diameter", "Nominal Diameter", "dia", "D"]

# the picker's sentinel for 'take the size off the outlet connector'
CONNECTOR_DIA = "(outlet connector size)"


def node_dia_mm(inst, param_name=None):
    """The node's own pipe size in mm.

    ``param_name`` set: read THAT parameter (instance first, then type) -
    the dialog asks which family parameter carries the diameter. None:
    the automatic chain - outlet connector, then the usual DIA-style
    names. Returns None when nothing readable/positive is found."""
    if param_name:
        for el in (inst, getattr(inst, "Symbol", None)):
            if el is None:
                continue
            try:
                p = el.LookupParameter(param_name)
                if p is not None and p.HasValue \
                        and str(p.StorageType) == "Double":
                    v = ft2mm(p.AsDouble())
                    if v > 0:
                        return v
            except Exception:
                pass
        return None
    _o, dia = fixture_outlet_info(inst)
    if dia:
        return dia
    for nm in DIA_PARAM_NAMES:
        v = node_dia_mm(inst, nm)
        if v:
            return v
    return None


def node_dia_param_options(inst):
    """The numeric (length-like) parameters a node offers for its
    diameter: [(name, sample_mm or None), ...] - instance parameters
    first, then its type's, deduped, sorted. The dialog shows these with
    the sample value so the right one is obvious."""
    out = []
    seen = set()

    def scan(el):
        if el is None:
            return
        try:
            params = el.Parameters
        except Exception:
            return
        for p in params:
            try:
                if str(p.StorageType) != "Double":
                    continue
                nm = p.Definition.Name
                if nm in seen:
                    continue
                seen.add(nm)
                v = ft2mm(p.AsDouble()) if p.HasValue else None
                out.append((nm, v))
            except Exception:
                continue

    scan(inst)
    scan(getattr(inst, "Symbol", None))
    out.sort(key=lambda t: t[0].lower())
    return out


def _has_point(inst):
    """True when the instance has a location point (or a bounding box) -
    i.e. an origin a drop pipe can start from."""
    try:
        if inst.Location.Point is not None:
            return True
    except Exception:
        pass
    try:
        return inst.get_BoundingBox(None) is not None
    except Exception:
        return False


def node_type_rows(doc):
    """One row per placed node TYPE, for the category > family > type
    picker: [{"cat", "fam", "type", "label", "insts"}, ...] sorted by
    category, family, type. ``label`` is 'Category : Family : Type'."""
    rows = []
    for _lbl, insts in list_node_types(doc):
        cat, fam, typ = "(no category)", "?", "?"
        try:
            sym = insts[0].Symbol
            typ = safe_name(sym)
            try:
                fam = safe_name(sym.Family)
            except Exception:
                pass
            c = sym.Category
            if c is not None:
                cat = c.Name
        except Exception:
            pass
        rows.append({"cat": cat, "fam": fam, "type": typ,
                     "label": "{} : {} : {}".format(cat, fam, typ),
                     "insts": insts})
    rows.sort(key=lambda r: (r["cat"].lower(), r["fam"].lower(),
                             r["type"].lower()))
    return rows


# --- pure helpers over those rows (unit-tested without Revit) ----------
def node_categories(rows):
    """Sorted category names present."""
    return sorted(set(r["cat"] for r in rows), key=lambda s: s.lower())


def node_families(rows, cat):
    """Sorted family names inside one category."""
    return sorted(set(r["fam"] for r in rows if r["cat"] == cat),
                  key=lambda s: s.lower())


def node_types_in(rows, cat, fam):
    """The rows of one category+family, in type order."""
    return [r for r in rows if r["cat"] == cat and r["fam"] == fam]


def search_node_rows(rows, query):
    """Rows whose 'Category : Family : Type' contains EVERY whitespace-
    separated word of ``query`` (case-insensitive). Empty query -> []
    so the caller can fall back to the cascade."""
    words = (query or "").lower().split()
    if not words:
        return []
    out = []
    for r in rows:
        hay = r["label"].lower()
        if all(w in hay for w in words):
            out.append(r)
    return out


def outlet_is_connected(fixture):
    """True when the fixture's outlet connector already has something on
    it - such nodes are left alone."""
    o, _d = fixture_outlet_info(fixture)
    if o is None:
        return False
    ox = XYZ(o[0], o[1], o[2])
    for c in get_connectors(fixture):
        try:
            if c.Origin.DistanceTo(ox) < mm2ft(10.0):
                return bool(c.IsConnected)
        except Exception:
            continue
    return False


def regrade_main(doc, main, slope_n, keep="low", log=None):
    """Re-grade the main pipe at 1:slope_n, keeping its ``keep``
    ("low"/"high") end where it is, in ONE transaction. Returns the new
    (a, b)."""
    a, b, _t, _s, _l, _d = main_pipe_info(main)
    a2, b2 = regrade_main_ends(a, b, slope_n, keep)
    t = Transaction(doc, "Re-grade main pipe")
    t.Start()
    try:
        main.Location.Curve = Line.CreateBound(XYZ(*a2), XYZ(*b2))
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    if log is not None:
        moved = a2 if a2 != a else b2
        log("Main re-graded at **1:{:g}** ({} end kept): the moved end "
            "is now Z {:.3f} m.".format(
                slope_n, "LOWER" if keep == "low" else "UPPER",
                ft2mm(moved[2]) / 1000.0))
    return a2, b2


def _tee_into_main(doc, c_end, main_seg, end_xyz, fit_notes):
    """Split ``main_seg`` at the branch point and join the two halves +
    the branch with a TEE fitting. Returns ``(other, tee)`` - the new
    second half (so the caller can keep tracking every piece of the
    main) and the tee instance; either may be None. Failures degrade:
    tee -> takeoff -> note."""
    other = None
    try:
        from Autodesk.Revit.DB.Plumbing import PlumbingUtils
        new_id = PlumbingUtils.BreakCurve(doc, main_seg.Id, end_xyz)
        other = doc.GetElement(new_id)
    except Exception as ex:
        fit_notes.append("couldn't split the main at the branch point "
                         "({}) - trying a takeoff instead".format(ex))
    if other is not None:
        c1 = _conn_near(main_seg, end_xyz)
        c2 = _conn_near(other, end_xyz)
        if c1 is not None and c2 is not None:
            try:
                tee = doc.Create.NewTeeFitting(c1, c2, c_end)
                return other, tee
            except Exception as ex:
                fit_notes.append("tee junction not placed ({}) - trying "
                                 "a takeoff instead".format(ex))
        else:
            fit_notes.append("split the main but couldn't find its "
                             "connectors - trying a takeoff instead")
    try:
        doc.Create.NewTakeoffFitting(c_end, main_seg)
        fit_notes.append("connected with a TAKEOFF, not a tee")
    except Exception as ex:
        fit_notes.append("takeoff fallback also failed ({}) - the branch "
                         "ends on the main's centreline, join it "
                         "manually".format(ex))
    return other, None


def list_pipe_type_options(doc):
    """[(name, ElementId), ...] of the model's pipe types, sorted."""
    from Autodesk.Revit.DB.Plumbing import PipeType
    out = []
    for t in FilteredElementCollector(doc).OfClass(PipeType):
        try:
            out.append((safe_name(t), t.Id))
        except Exception:
            continue
    out.sort(key=lambda x: x[0].lower())
    return out


def list_system_type_options(doc):
    """[(name, ElementId), ...] of the model's piping system types."""
    from Autodesk.Revit.DB.Plumbing import PipingSystemType
    out = []
    for t in FilteredElementCollector(doc).OfClass(PipingSystemType):
        try:
            out.append((safe_name(t), t.Id))
        except Exception:
            continue
    out.sort(key=lambda x: x[0].lower())
    return out


def set_pipe_dia(doc, pipe, dia_mm, log=None):
    """Resize a pipe (the main) to ``dia_mm``, snapped to its own type's
    routing sizes, in ONE transaction. Returns the new dia in feet."""
    pipe_type = doc.GetElement(pipe.GetTypeId())
    dia_ft = _snap_dia_ft(doc, pipe_type, mm2ft(dia_mm))
    t = Transaction(doc, "Resize main pipe")
    t.Start()
    try:
        _set_dia(pipe, dia_ft)
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    if log is not None:
        log("Main resized to **{:.0f} mm**.".format(ft2mm(dia_ft)))
    return dia_ft


def connect_fixture_to_main(doc, fixture, main, slope_n, dia_mm,
                            invert_m=None, log=None,
                            pipe_type_id=None, system_type_id=None,
                            use_rotation=False):
    """Build one fixture's branch, in ONE transaction. Returns a summary
    dict; raises with everything rolled back when the pipes can't be
    created (a failed FITTING never fails the branch - the pipes stay
    and the miss is reported).

    ``use_rotation`` (the node flow): the branch leaves along the
    node's FACING direction (its rotation) when that ray meets the
    main, and the family's 'Drop Pipe' yes/no picks drop-first vs
    grade-first geometry."""
    def say(m):
        if log is not None:
            log(m)

    outlet, odia_mm = fixture_outlet_info(fixture)
    if outlet is None:
        raise RuntimeError("'{}' has no outlet connector.".format(
            safe_name(fixture)))

    a, b, type_id, sys_id, lvl_id, _mdia = main_pipe_info(main)
    if pipe_type_id is not None:
        type_id = pipe_type_id
    if system_type_id is not None:
        sys_id = system_type_id
    if lvl_id is None or lvl_id == ElementId.InvalidElementId:
        lvl = _host_level(doc, fixture)
        if lvl is None:
            raise RuntimeError("No level available for the new pipes.")
        lvl_id = lvl.Id

    pipe_type = doc.GetElement(type_id)
    dia_ft = _snap_dia_ft(doc, pipe_type, mm2ft(dia_mm))
    # Revit compares connector sizes EXACTLY: a branch that differs from
    # the main by a rounding hair (235.0 vs 234.9998856 - a feet value
    # rounded somewhere upstream) reads as a size change and buys a
    # 'reducing' coupling at the tee. Same-within-a-millimetre means
    # same: take the main's exact diameter.
    if _mdia and abs(dia_ft - _mdia) <= mm2ft(1.0):
        dia_ft = _mdia

    direction = None
    if use_rotation:
        # the node's rotation aims the branch: try facing, hand, then
        # their opposites - the first axis whose ray meets the main is
        # the one the modeller pointed at it
        for cand in node_directions(fixture):
            if ray_hits_main(outlet, cand, a, b) is not None:
                direction = cand
                break
    drop_first = node_drop_pipe(fixture) if use_rotation else True
    if not drop_first and invert_m is not None:
        say("  (Drop Pipe is OFF - the run starts AT the outlet, the "
            "typed invert doesn't apply)")
        invert_m = None
    pts = branch_points(outlet, a, b, slope_n, dia_ft, invert_m,
                        direction=direction, drop_pipe=drop_first)
    if use_rotation:
        if pts["aimed"]:
            say("  branch aimed along the node's rotation")
        else:
            say("  (no rotation axis of the node points at the main - "
                "branch takes the plan-nearest route)")
        if not drop_first:
            say("  Drop Pipe OFF: graded run from the outlet, then a "
                "drop onto the main")
    bend = XYZ(*pts["bend"])
    end = XYZ(*pts["end"])
    o_xyz = XYZ(*outlet)

    say("  outlet Z {:.3f} m, run {:.2f} m -> upstream invert "
        "**{:.3f} m**, drop {:.0f} mm".format(
            ft2mm(outlet[2]) / 1000.0, ft2mm(pts["run_xy_ft"]) / 1000.0,
            pts["upstream_invert_m"], ft2mm(max(pts["drop_ft"], 0.0))))
    if pts["mode"] == "drop_first" and pts["drop_ft"] < MIN_LEN_FT:
        say("  ! the elbow level sits at/above the outlet - no downpipe "
            "(check the invert/slope)")
    if pts["mode"] == "drop_last" and pts["drop_ft"] < MIN_LEN_FT:
        say("  ! the graded run bottoms out at/below the main - no "
            "drop piece")

    t = Transaction(doc, "Connect fixture to main")
    t.Start()
    fit_notes = []
    built = []                       # (leg index, pipe)
    try:
        # the centreline polyline: outlet -> ... -> end. The vertical
        # leg only exists when it actually drops DOWN far enough;
        # skipped (degenerate) legs let the next one carry on from the
        # last built point.
        pline = pts["points"]
        drop_leg = 0 if pts["mode"] == "drop_first" else len(pline) - 2
        cur = XYZ(*pline[0])
        for i in range(len(pline) - 1):
            tgt = XYZ(*pline[i + 1])
            if i == drop_leg:
                ok = pts["drop_ft"] >= MIN_LEN_FT
            else:
                ok = cur.DistanceTo(tgt) >= MIN_LEN_FT
            if ok:
                built.append((i, Pipe.Create(doc, sys_id, type_id,
                                             lvl_id, cur, tgt)))
                cur = tgt
        if not built:
            # fixture directly on the main: single vertical drop
            built.append((0, Pipe.Create(doc, sys_id, type_id, lvl_id,
                                         o_xyz, end)))
        doc.Regenerate()
        for _i, p in built:
            _set_dia(p, dia_ft)
        doc.Regenerate()

        elbows = []
        for k in range(len(built) - 1):
            pa, pb = built[k][1], built[k + 1][1]
            joint = XYZ(*pline[built[k][0] + 1])   # where the legs meet
            c1 = _conn_near(pa, joint)
            c2 = _conn_near(pb, joint)
            if c1 is not None and c2 is not None:
                try:
                    elbows.append(doc.Create.NewElbowFitting(c1, c2))
                except Exception as ex:
                    fit_notes.append("elbow not placed ({})".format(ex))

        # hook the top of the branch to the fixture's outlet connector -
        # but NOT in grade-first mode: the outlet faces DOWN, and
        # force-connecting a graded run to it makes Revit yank the pipe
        # into a kink to align. The run starts AT the outlet and stays
        # physically unconnected instead (tracking covers it).
        if pts["mode"] != "drop_last":
            top_pipe = built[0][1]
            try:
                fconn = None
                for c in get_connectors(fixture):
                    if c.Origin.DistanceTo(o_xyz) < mm2ft(10.0):
                        fconn = c
                        break
                if fconn is not None and not fconn.IsConnected:
                    c_up = _conn_near(top_pipe, o_xyz)
                    if c_up is not None and not c_up.IsConnected:
                        fconn.ConnectTo(c_up)
            except Exception:
                pass

        # a TEE joins the branch into the main: split the main at the
        # branch point, tee the two halves + the branch together
        new_seg = None
        tee = None
        end_pipe = built[-1][1]
        c_end = _conn_near(end_pipe, end)
        if c_end is not None:
            new_seg, tee = _tee_into_main(doc, c_end, main, end,
                                          fit_notes)
        t.Commit()
    except Exception:
        t.RollBack()
        raise

    for n in fit_notes:
        say("  ! {}".format(n))
    # tracking keys: "down" = the vertical piece, "sloped" = the first
    # graded run, "stub" = the squared last bit (oblique aims only)
    down = sloped = stub_pipe = None
    for i, p in built:
        if i == drop_leg:
            down = p
        elif sloped is None:
            sloped = p
        else:
            stub_pipe = p
    return {"down": down, "sloped": sloped, "stub": stub_pipe,
            "elbow": elbows[0] if elbows else None,
            "elbow2": elbows[1] if len(elbows) > 1 else None,
            "tee": tee,
            "end": pts["end"], "mode": pts["mode"],
            "upstream_invert_m": pts["upstream_invert_m"],
            "fitting_misses": len(fit_notes),
            "new_main_segment": new_seg}
