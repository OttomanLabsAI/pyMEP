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


def regrade_main_ends(a, b, slope_n):
    """Re-grade the main at 1:slope_n keeping its LOW end where it is:
    the higher end's Z becomes low_z + plan_run / n (ends level -> the
    second end is treated as the fixed low one). Returns (a2, b2). Pure."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    run = math.sqrt(dx * dx + dy * dy)
    fall = (run / slope_n) if (slope_n and slope_n > 0) else 0.0
    if a[2] < b[2]:
        return a, (b[0], b[1], a[2] + fall)
    return (a[0], a[1], b[2] + fall), b


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
def branch_points(outlet, main_a, main_b, slope_n, dia_ft, invert_m=None):
    """Where the branch runs, all coordinates internal feet.

    outlet / main_a / main_b: (x, y, z) tuples - the fixture outlet and
    the main pipe's centreline endpoints. slope_n: the 1:n ratio (fall =
    run / n; 0 or None = level). dia_ft: branch diameter.

    invert_m None (the default) keeps the upstream invert where the
    model currently puts it: the branch END sits ON the main's
    centreline at the plan-nearest point (the main's own Z there,
    interpolated along its slope) and the elbow derives back UP the
    branch slope. A number fixes the upstream INVERT (metres, absolute):
    elbow centreline = invert + D/2, and the end derives DOWN the slope.

    Returns a dict:
      bend (x,y,z)   the elbow point under the fixture
      end (x,y,z)    the branch end at the main
      run_xy_ft      plan length of the sloped branch
      drop_ft        outlet Z minus bend Z (downpipe length; <=0 = none)
      upstream_invert_m   the resulting upstream invert (centreline -
                          D/2), for reporting and dialog defaults
    """
    ox, oy, oz = outlet
    ax, ay, az = main_a
    bx, by, bz = main_b

    dx, dy = bx - ax, by - ay
    dd = dx * dx + dy * dy
    if dd < 1e-12:
        t = 0.0
    else:
        t = ((ox - ax) * dx + (oy - ay) * dy) / dd
        t = max(0.0, min(1.0, t))
    mx = ax + t * dx
    my = ay + t * dy
    mz = az + t * (bz - az)          # main centreline Z at that point

    run = math.sqrt((ox - mx) ** 2 + (oy - my) ** 2)
    fall = (run / slope_n) if (slope_n and slope_n > 0) else 0.0

    if invert_m is None:
        end_z = mz
        bend_z = mz + fall
    else:
        bend_z = invert_m * 1000.0 / 304.8 + dia_ft / 2.0
        end_z = bend_z - fall

    return {"bend": (ox, oy, bend_z),
            "end": (mx, my, end_z),
            "run_xy_ft": run,
            "drop_ft": oz - bend_z,
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
    then lowest (same rule as the gully button)."""
    o, dia_mm = gully_outlet(fixture)
    if o is None:
        return None, None
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


DIA_PARAM_NAMES = ["DIA", "Diameter", "Nominal Diameter", "dia", "D"]


def node_dia_mm(inst):
    """The node's own pipe size in mm: its outlet connector first, then a
    DIA-style instance/type parameter (families without connectors -
    Generic Model chambers and the like - carry their bore there), else
    None for the caller's fallback."""
    _o, dia = fixture_outlet_info(inst)
    if dia:
        return dia
    for nm in DIA_PARAM_NAMES:
        try:
            p = inst.LookupParameter(nm)
            if p is not None and p.HasValue \
                    and str(p.StorageType) == "Double":
                v = ft2mm(p.AsDouble())
                if v > 0:
                    return v
        except Exception:
            pass
    return None


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


def regrade_main(doc, main, slope_n, log=None):
    """Re-grade the main pipe at 1:slope_n, keeping its low end where it
    is, in ONE transaction. Returns the new (a, b)."""
    a, b, _t, _s, _l, _d = main_pipe_info(main)
    a2, b2 = regrade_main_ends(a, b, slope_n)
    t = Transaction(doc, "Re-grade main pipe")
    t.Start()
    try:
        main.Location.Curve = Line.CreateBound(XYZ(*a2), XYZ(*b2))
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    if log is not None:
        log("Main re-graded at **1:{:g}** (low end fixed): high end now "
            "Z {:.3f} m.".format(
                slope_n, ft2mm(max(a2[2], b2[2])) / 1000.0))
    return a2, b2


def _tee_into_main(doc, c_end, main_seg, end_xyz, fit_notes):
    """Split ``main_seg`` at the branch point and join the two halves +
    the branch with a TEE fitting. Returns the new second half (so the
    caller can keep tracking every piece of the main), or None when no
    split happened. Failures degrade: tee -> takeoff -> note."""
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
                doc.Create.NewTeeFitting(c1, c2, c_end)
                return other
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
    return other


def connect_fixture_to_main(doc, fixture, main, slope_n, dia_mm,
                            invert_m=None, log=None):
    """Build one fixture's branch, in ONE transaction. Returns a summary
    dict; raises with everything rolled back when the pipes can't be
    created (a failed FITTING never fails the branch - the pipes stay
    and the miss is reported)."""
    def say(m):
        if log is not None:
            log(m)

    outlet, odia_mm = fixture_outlet_info(fixture)
    if outlet is None:
        raise RuntimeError("'{}' has no outlet connector.".format(
            safe_name(fixture)))

    a, b, type_id, sys_id, lvl_id, _mdia = main_pipe_info(main)
    if lvl_id is None or lvl_id == ElementId.InvalidElementId:
        lvl = _host_level(doc, fixture)
        if lvl is None:
            raise RuntimeError("No level available for the new pipes.")
        lvl_id = lvl.Id

    pipe_type = doc.GetElement(type_id)
    dia_ft = _snap_dia_ft(doc, pipe_type, mm2ft(dia_mm))

    pts = branch_points(outlet, a, b, slope_n, dia_ft, invert_m)
    bend = XYZ(*pts["bend"])
    end = XYZ(*pts["end"])
    o_xyz = XYZ(*outlet)

    say("  outlet Z {:.3f} m, run {:.2f} m -> upstream invert "
        "**{:.3f} m**, drop {:.0f} mm".format(
            ft2mm(outlet[2]) / 1000.0, ft2mm(pts["run_xy_ft"]) / 1000.0,
            pts["upstream_invert_m"], ft2mm(max(pts["drop_ft"], 0.0))))
    if pts["drop_ft"] < MIN_LEN_FT:
        say("  ! the elbow level sits at/above the outlet - no downpipe "
            "(check the invert/slope)")

    t = Transaction(doc, "Connect fixture to main")
    t.Start()
    down = sloped = None
    fit_notes = []
    try:
        have_down = pts["drop_ft"] >= MIN_LEN_FT
        have_run = pts["run_xy_ft"] >= MIN_LEN_FT
        if have_down:
            down = Pipe.Create(doc, sys_id, type_id, lvl_id, o_xyz, bend)
        if have_run:
            start = bend if have_down else o_xyz
            sloped = Pipe.Create(doc, sys_id, type_id, lvl_id, start, end)
        if down is None and sloped is None:
            # fixture directly on the main: single vertical drop
            down = Pipe.Create(doc, sys_id, type_id, lvl_id, o_xyz, end)
        doc.Regenerate()
        for p in (down, sloped):
            if p is not None:
                _set_dia(p, dia_ft)
        doc.Regenerate()

        if down is not None and sloped is not None:
            c1 = _conn_near(down, bend)
            c2 = _conn_near(sloped, bend)
            if c1 is not None and c2 is not None:
                try:
                    doc.Create.NewElbowFitting(c1, c2)
                except Exception as ex:
                    fit_notes.append("elbow not placed ({})".format(ex))

        # hook the top of the branch to the fixture's outlet connector
        top_pipe = down if down is not None else sloped
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
        end_pipe = sloped if sloped is not None else down
        c_end = _conn_near(end_pipe, end)
        if c_end is not None:
            new_seg = _tee_into_main(doc, c_end, main, end, fit_notes)
        t.Commit()
    except Exception:
        t.RollBack()
        raise

    for n in fit_notes:
        say("  ! {}".format(n))
    return {"down": down, "sloped": sloped,
            "upstream_invert_m": pts["upstream_invert_m"],
            "fitting_misses": len(fit_notes),
            "new_main_segment": new_seg}
