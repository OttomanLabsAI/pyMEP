# -*- coding: utf-8 -*-
"""Draw a downpipe from a gully, a 90 deg bend, and a horizontal pipe into the
centre of a manhole - from two selected Revit family instances.

Geometry (normal case):
    gully outlet  ->  vertical downpipe  ->  90 deg elbow  ->  horizontal pipe
                                                              ->  manhole centre

* The downpipe starts at the gully's outlet CONNECTOR (its real outlet point
  and size). The pipe size is snapped to the chosen pipe type's routing sizes
  so Revit never aborts with the "protect from corruption" diameter error.
* The horizontal run sits at the manhole's invert (its geometry bottom) and
  ends at the manhole's plan centre.
* Two edge cases are handled: gully sitting (almost) directly above the manhole
  centre -> a single vertical pipe, no bend; gully outlet at (almost) the same
  level as the manhole invert -> a single horizontal pipe, no bend.
"""

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import (
    FilteredElementCollector, XYZ, Transaction, BuiltInParameter, ElementId,
    Level,
)
from Autodesk.Revit.DB.Plumbing import Pipe, PipeType, PipingSystemType

from pymep_revit import get_connectors, safe_name, mm2ft, ft2mm


# --------------------------------------------------------------------------
# diameter snapping (copied from the pipe placer so this module loads stand-
# alone, with minimal top-level imports)
# --------------------------------------------------------------------------
def _routing_sizes_ft(doc, pipe_type):
    sizes = set()
    try:
        from Autodesk.Revit.DB.Plumbing import PipeSegment
        from Autodesk.Revit.DB import RoutingPreferenceRuleGroupType
        rpm = pipe_type.RoutingPreferenceManager
        seg_ids = set()
        try:
            nrules = rpm.GetNumberOfRules(
                RoutingPreferenceRuleGroupType.Segments)
        except Exception:
            nrules = 0
        for i in range(nrules):
            try:
                rule = rpm.GetRule(RoutingPreferenceRuleGroupType.Segments, i)
                mid = rule.MEPPartId
                if mid is not None and mid != ElementId.InvalidElementId:
                    seg_ids.add(mid.IntegerValue)
            except Exception:
                pass
        segs = []
        for sid in seg_ids:
            el = doc.GetElement(ElementId(sid))
            if el is not None:
                segs.append(el)
        if not segs:
            segs = list(FilteredElementCollector(doc).OfClass(PipeSegment))
        for seg in segs:
            try:
                for s in seg.GetSizes():
                    sizes.add(s.NominalDiameter)
            except Exception:
                pass
    except Exception:
        pass
    return sizes


def _snap_dia_ft(doc, pt, dia_ft):
    sizes = _routing_sizes_ft(doc, pt)
    if not sizes:
        return dia_ft
    return min(sizes, key=lambda s: abs(s - dia_ft))


# --------------------------------------------------------------------------
# element identification (gully vs manhole)
# --------------------------------------------------------------------------
def has_pipe_connector(e):
    try:
        return len([c for c in get_connectors(e)]) > 0
    except Exception:
        return False


def _name_blob(e):
    parts = [safe_name(e) or ""]
    for getter in (lambda: e.Symbol.Family.Name, lambda: e.Symbol.Name):
        try:
            parts.append(getter())
        except Exception:
            pass
    return " ".join(parts).lower()


def looks_like_gully(e):
    b = _name_blob(e)
    return ("gully" in b or b.strip().startswith("fg ") or b.strip() == "fg"
            or " fg" in b or b.strip().startswith("fg:"))


def looks_like_manhole(e):
    b = _name_blob(e)
    return ("manhole" in b or "concentric" in b or "chamber" in b
            or "cylindrical structure" in b)


def identify_pair(a, b):
    """Return (gully, manhole), or (None, None) if it can't be decided.

    Primary signal: the gully has a pipe connector (its outlet), the manhole
    doesn't. Secondary: name keywords.
    """
    ca, cb = has_pipe_connector(a), has_pipe_connector(b)
    if ca and not cb:
        return a, b
    if cb and not ca:
        return b, a
    ga, gb = looks_like_gully(a), looks_like_gully(b)
    if ga and not gb:
        return a, b
    if gb and not ga:
        return b, a
    ma, mb = looks_like_manhole(a), looks_like_manhole(b)
    if ma and not mb:
        return b, a
    if mb and not ma:
        return a, b
    return None, None


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------
def _loc_point(e):
    try:
        return e.Location.Point
    except Exception:
        return None


def _bbox_min_z(e):
    try:
        bb = e.get_BoundingBox(None)
        if bb is not None:
            return bb.Min.Z
    except Exception:
        pass
    return None


def gully_outlet(g):
    """Return (origin XYZ, dia_mm) for the gully's outlet.

    Prefers the connector pointing most downward (then the lowest one). Falls
    back to the location point + bounding-box bottom if there's no connector.
    """
    best = None
    best_score = None
    for c in get_connectors(g):
        try:
            o = c.Origin
            try:
                dz = c.CoordinateSystem.BasisZ.Z
            except Exception:
                dz = 0.0
            score = (-dz, -o.Z)        # most downward, then lowest
        except Exception:
            continue
        if best is None or score > best_score:
            best, best_score = c, score
    if best is not None:
        try:
            dia_mm = ft2mm(best.Radius * 2.0)
        except Exception:
            dia_mm = None
        return best.Origin, dia_mm
    p = _loc_point(g)
    if p is None:
        return None, None
    bz = _bbox_min_z(g)
    return XYZ(p.X, p.Y, bz if bz is not None else p.Z), None


# --------------------------------------------------------------------------
# type / system / level
# --------------------------------------------------------------------------
def _find_pipe_type(doc, preferred):
    types = list(FilteredElementCollector(doc).OfClass(PipeType))
    if preferred:
        for t in types:
            if safe_name(t).strip() == preferred.strip():
                return t
    for kw in ("pe sdr11", "drainage", "pe "):
        for t in types:
            if kw in safe_name(t).lower():
                return t
    return types[0] if types else None


def _find_system_type(doc, preferred):
    types = list(FilteredElementCollector(doc).OfClass(PipingSystemType))
    if preferred:
        for t in types:
            if safe_name(t).strip() == preferred.strip():
                return t
    for kw in ("storm", "sanitary", "drain", "waste"):
        for t in types:
            if kw in safe_name(t).lower():
                return t
    return types[0] if types else None


def _host_level(doc, inst):
    try:
        lid = inst.LevelId
        if lid and lid != ElementId.InvalidElementId:
            lv = doc.GetElement(lid)
            if lv is not None:
                return lv
    except Exception:
        pass
    lvls = list(FilteredElementCollector(doc).OfClass(Level))
    return min(lvls, key=lambda l: l.Elevation) if lvls else None


def _conn_near(pipe, pt):
    best, bestd = None, None
    for c in get_connectors(pipe):
        try:
            d = c.Origin.DistanceTo(pt)
        except Exception:
            continue
        if best is None or d < bestd:
            best, bestd = c, d
    return best


def _set_dia(pipe, dia_ft):
    try:
        dp = pipe.get_Parameter(BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
        if dp is not None and not dp.IsReadOnly:
            dp.Set(dia_ft)
    except Exception:
        pass


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def connect_gully_to_manhole(doc, gully, manhole, log=None,
                             pipe_type_name=None, system_type_name=None,
                             default_dia_mm=100.0, invert_offset_mm=0.0,
                             slope_ratio=0.0):
    def say(m):
        if log:
            log(m)

    outlet, gdia_mm = gully_outlet(gully)
    if outlet is None:
        raise RuntimeError("Couldn't find the gully's outlet point.")
    mp = _loc_point(manhole)
    if mp is None:
        raise RuntimeError("The manhole has no location point.")
    mx, my = mp.X, mp.Y
    bottom_z = _bbox_min_z(manhole)
    if bottom_z is None:
        bottom_z = mp.Z
    inv_z = bottom_z + mm2ft(invert_offset_mm)   # entry = bottom + user offset

    pt = _find_pipe_type(doc, pipe_type_name)
    st = _find_system_type(doc, system_type_name)
    if pt is None or st is None:
        raise RuntimeError("No pipe type / piping system type in this model.")
    lvl = _host_level(doc, gully)
    if lvl is None:
        raise RuntimeError("No level found in this model.")

    dia_mm = gdia_mm or default_dia_mm
    dia_ft = _snap_dia_ft(doc, pt, mm2ft(dia_mm))

    say("Gully outlet ~{:.0f} mm -> pipe nominal **{:.0f} mm**.".format(
        dia_mm, ft2mm(dia_ft)))
    say("Pipe type **{}**, system **{}**, level **{}**.".format(
        safe_name(pt), safe_name(st), safe_name(lvl)))
    say("Manhole bottom {:.3f} m + offset {:.0f} mm -> entry level **{:.3f} m**."
        .format(ft2mm(bottom_z) / 1000.0, invert_offset_mm,
                ft2mm(inv_z) / 1000.0))
    say("Gully outlet Z {:.3f} m.".format(ft2mm(outlet.Z) / 1000.0))

    MIN = mm2ft(50.0)
    run_xy = XYZ(outlet.X, outlet.Y, 0.0).DistanceTo(XYZ(mx, my, 0.0))
    # Fall over the run for a 1:slope_ratio gradient. The manhole entry is the
    # FIXED low point (bottom + offset); the bend is raised by the fall so the
    # run slopes down toward the manhole.
    fall = (run_xy / slope_ratio) if (slope_ratio and slope_ratio > 0) else 0.0
    bend_z = inv_z + fall
    if fall > 0:
        say("Run {:.3f} m at 1:{:g} -> fall {:.0f} mm (manhole end is the low "
            "end).".format(ft2mm(run_xy) / 1000.0, slope_ratio, ft2mm(fall)))

    t = Transaction(doc, "Gully -> Manhole connection")
    t.Start()
    down = horiz = elbow = None
    try:
        if run_xy < MIN:
            # gully essentially above the manhole centre -> single vertical pipe
            down = Pipe.Create(doc, st.Id, pt.Id, lvl.Id,
                               outlet, XYZ(mx, my, inv_z))
            doc.Regenerate(); _set_dia(down, dia_ft)
            say("Gully sits above the manhole centre - single vertical drop, "
                "no bend or slope.")
        else:
            bend = XYZ(outlet.X, outlet.Y, bend_z)
            if outlet.Z - bend_z < MIN:
                say("NOTE: the slope/offset raises the bend to {:.3f} m, at or "
                    "above the gully outlet {:.3f} m, so the downpipe is short "
                    "or rises. Lower the offset, ease the slope, or check the "
                    "gully level.".format(ft2mm(bend_z) / 1000.0,
                                          ft2mm(outlet.Z) / 1000.0))
            down = Pipe.Create(doc, st.Id, pt.Id, lvl.Id, outlet, bend)
            horiz = Pipe.Create(doc, st.Id, pt.Id, lvl.Id, bend,
                                XYZ(mx, my, inv_z))
            doc.Regenerate()
            _set_dia(down, dia_ft); _set_dia(horiz, dia_ft)
            doc.Regenerate()
            c_down = _conn_near(down, bend)
            c_horiz = _conn_near(horiz, bend)
            if c_down is not None and c_horiz is not None:
                try:
                    elbow = doc.Create.NewElbowFitting(c_down, c_horiz)
                    say("Bend placed at the top of the downpipe.")
                except Exception as ex:
                    say("Couldn't auto-place the elbow ({}); the two pipes "
                        "still meet at the bend point.".format(ex))

        # best-effort: physically connect the downpipe top to the gully outlet
        if down is not None:
            try:
                gconn = None
                for c in get_connectors(gully):
                    if c.Origin.DistanceTo(outlet) < mm2ft(10.0):
                        gconn = c
                        break
                if gconn is not None and not gconn.IsConnected:
                    c_up = _conn_near(down, outlet)
                    if c_up is not None and not c_up.IsConnected:
                        gconn.ConnectTo(c_up)
                        say("Connected the downpipe to the gully outlet.")
            except Exception:
                pass

        t.Commit()
    except Exception:
        t.RollBack()
        raise

    say("### Done.")
    return down, horiz, elbow


# ===========================================================================
# single-element modes
# ===========================================================================
def draw_gully_downpipe(doc, gully, length_mm=300.0, log=None,
                        pipe_type_name=None, system_type_name=None,
                        default_dia_mm=100.0):
    """Only the gully selected: drop a vertical downpipe of `length_mm` from the
    gully's outlet (size from the outlet connector, snapped to the type)."""
    def say(m):
        if log:
            log(m)

    outlet, gdia_mm = gully_outlet(gully)
    if outlet is None:
        raise RuntimeError("Couldn't find the gully's outlet point.")
    pt = _find_pipe_type(doc, pipe_type_name)
    st = _find_system_type(doc, system_type_name)
    if pt is None or st is None:
        raise RuntimeError("No pipe type / piping system type in this model.")
    lvl = _host_level(doc, gully)
    if lvl is None:
        raise RuntimeError("No level found in this model.")

    dia_mm = gdia_mm or default_dia_mm
    dia_ft = _snap_dia_ft(doc, pt, mm2ft(dia_mm))
    end = XYZ(outlet.X, outlet.Y, outlet.Z - mm2ft(length_mm))

    say("Downpipe: **{:.0f} mm** long, **{:.0f} mm** dia, straight down from "
        "the gully outlet (Z {:.3f} m).".format(
            length_mm, ft2mm(dia_ft), ft2mm(outlet.Z) / 1000.0))

    t = Transaction(doc, "Gully downpipe")
    t.Start()
    down = None
    try:
        down = Pipe.Create(doc, st.Id, pt.Id, lvl.Id, outlet, end)
        doc.Regenerate()
        _set_dia(down, dia_ft)
        try:
            gconn = None
            for c in get_connectors(gully):
                if c.Origin.DistanceTo(outlet) < mm2ft(10.0):
                    gconn = c
                    break
            if gconn is not None and not gconn.IsConnected:
                c_up = _conn_near(down, outlet)
                if c_up is not None and not c_up.IsConnected:
                    gconn.ConnectTo(c_up)
                    say("Connected the downpipe to the gully outlet.")
        except Exception:
            pass
        t.Commit()
    except Exception:
        t.RollBack()
        raise

    say("### Done.")
    return down


def draw_manhole_run(doc, manhole, end_pt, invert_offset_mm=0.0,
                     slope_ratio=0.0, log=None, pipe_type_name=None,
                     system_type_name=None, default_dia_mm=100.0):
    """Only the manhole selected: a single pipe from the manhole centre to the
    picked point's plan position. The manhole end sits at (bottom + offset) and
    is the LOW end; the far end rises at 1:slope_ratio."""
    def say(m):
        if log:
            log(m)

    mp = _loc_point(manhole)
    if mp is None:
        raise RuntimeError("The manhole has no location point.")
    mx, my = mp.X, mp.Y
    bottom_z = _bbox_min_z(manhole)
    if bottom_z is None:
        bottom_z = mp.Z
    inv_z = bottom_z + mm2ft(invert_offset_mm)

    pt = _find_pipe_type(doc, pipe_type_name)
    st = _find_system_type(doc, system_type_name)
    if pt is None or st is None:
        raise RuntimeError("No pipe type / piping system type in this model.")
    lvl = _host_level(doc, manhole)
    if lvl is None:
        raise RuntimeError("No level found in this model.")
    dia_ft = _snap_dia_ft(doc, pt, mm2ft(default_dia_mm))

    run = XYZ(mx, my, 0.0).DistanceTo(XYZ(end_pt.X, end_pt.Y, 0.0))
    if run < mm2ft(50.0):
        raise RuntimeError("Pick a point away from the manhole centre to set "
                           "the pipe's direction and length.")
    fall = (run / slope_ratio) if (slope_ratio and slope_ratio > 0) else 0.0
    far_z = inv_z + fall
    start = XYZ(mx, my, inv_z)
    end = XYZ(end_pt.X, end_pt.Y, far_z)

    say("Manhole bottom {:.3f} m + offset {:.0f} mm -> entry **{:.3f} m** "
        "(low end).".format(ft2mm(bottom_z) / 1000.0, invert_offset_mm,
                            ft2mm(inv_z) / 1000.0))
    say("Run {:.3f} m, dia {:.0f} mm.".format(
        ft2mm(run) / 1000.0, ft2mm(dia_ft)))
    if fall > 0:
        say("Slope 1:{:g} -> far end rises {:.0f} mm above the manhole entry."
            .format(slope_ratio, ft2mm(fall)))

    t = Transaction(doc, "Manhole run")
    t.Start()
    pipe = None
    try:
        pipe = Pipe.Create(doc, st.Id, pt.Id, lvl.Id, start, end)
        doc.Regenerate()
        _set_dia(pipe, dia_ft)
        t.Commit()
    except Exception:
        t.RollBack()
        raise

    say("### Done.")
    return pipe
