# -*- coding: utf-8 -*-
"""Connect plumbing fixtures into a main pipe: a vertical downpipe from
each fixture's outlet connector, an elbow, a sloped branch falling at
1:n toward the main, and a takeoff fitting where it meets it.

Geometry per fixture (plan-projected onto the main's centreline):

    fixture outlet -> downpipe -> elbow -> sloped branch (1:n) -> takeoff
                                                                  on main

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
    XYZ, Transaction, BuiltInParameter, ElementId,
)
from Autodesk.Revit.DB.Plumbing import Pipe

from pymep_revit import get_connectors, safe_name, mm2ft, ft2mm
from pymep_gully_connect import (
    gully_outlet, _snap_dia_ft, _conn_near, _set_dia, _host_level,
)


MIN_LEN_FT = mm2ft(50.0)


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

        # the takeoff joins the branch end into the main with a fitting
        end_pipe = sloped if sloped is not None else down
        c_end = _conn_near(end_pipe, end)
        if c_end is not None:
            try:
                doc.Create.NewTakeoffFitting(c_end, main)
            except Exception as ex:
                fit_notes.append("takeoff into the main not placed ({}) - "
                                 "the branch still ends on the main's "
                                 "centreline".format(ex))
        t.Commit()
    except Exception:
        t.RollBack()
        raise

    for n in fit_notes:
        say("  ! {}".format(n))
    return {"down": down, "sloped": sloped,
            "upstream_invert_m": pts["upstream_invert_m"],
            "fitting_misses": len(fit_notes)}
