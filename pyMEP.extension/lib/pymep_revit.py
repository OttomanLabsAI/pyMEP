# -*- coding: utf-8 -*-
"""Unit conversions, element helpers, and Revit element extractors for pyMEP."""

import math
import clr
clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import BuiltInParameter, BuiltInCategory, XYZ

# ------------- units --------------

def ft2mm(ft):  return ft * 304.8
def mm2ft(mm): return mm / 304.8

def xyz_mm(x, y, z):
    """Build a Revit XYZ from millimetre coordinates."""
    return XYZ(mm2ft(x), mm2ft(y), mm2ft(z))

def safe_name(e):
    try:
        return e.Name
    except AttributeError:
        from Autodesk.Revit.DB import Element
        return Element.Name.__get__(e)

# ------------- element extractors --------------

def get_connectors(elem):
    out = []
    try:
        cm = elem.ConnectorManager if hasattr(elem, "ConnectorManager") and elem.ConnectorManager else None
        if not cm and hasattr(elem, "MEPModel") and elem.MEPModel:
            cm = elem.MEPModel.ConnectorManager
        if cm:
            for c in cm.Connectors:
                out.append(c)
    except Exception:
        pass
    return out


def get_param_mm(elem, *names):
    for name in names:
        p = elem.LookupParameter(name)
        if p and p.HasValue:
            return ft2mm(p.AsDouble())
    return None


def get_bip_mm(elem, bip):
    try:
        p = elem.get_Parameter(bip)
        if p and p.HasValue:
            return ft2mm(p.AsDouble())
    except Exception:
        pass
    return None


def get_od(elem, conns):
    v = get_param_mm(elem, "Outside Diameter", "Outer Diameter")
    if v: return v
    v = get_bip_mm(elem, BuiltInParameter.RBS_CONDUIT_OUTER_DIAM_PARAM)
    if v: return v
    v = get_bip_mm(elem, BuiltInParameter.RBS_PIPE_OUTER_DIAMETER)
    if v: return v
    try:
        for c in conns:
            if c.Shape == 0:  # Round
                return ft2mm(c.Radius * 2.0)
    except Exception:
        pass
    return None


def get_id(elem):
    v = get_param_mm(elem, "Inside Diameter", "Inner Diameter")
    if v: return v
    v = get_bip_mm(elem, BuiltInParameter.RBS_CONDUIT_INNER_DIAM_PARAM)
    if v: return v
    v = get_bip_mm(elem, BuiltInParameter.RBS_PIPE_INNER_DIAM_PARAM)
    if v: return v
    return None


def get_slope(pipe):
    """Return the pipe's slope as a dimensionless rise/run ratio (e.g.
    0.005 for a 1:200 gradient). RBS_PIPE_SLOPE is read-only in Revit and
    is computed from the pipe's endpoints. Returns 0.0 for horizontal
    pipes, fittings without a slope param, or anything that throws.
    Use abs() before converting to '1:X' form - sign indicates direction
    of fall along the pipe, not magnitude."""
    try:
        p = pipe.get_Parameter(BuiltInParameter.RBS_PIPE_SLOPE)
        if p is None or not p.HasValue:
            return 0.0
        return p.AsDouble()
    except Exception:
        return 0.0


def arc_from_connectors(elem):
    """For a fitting with two connectors, return (centre XYZ, radius in ft) or
    (None, None) if the two connector directions are parallel."""
    conns = get_connectors(elem)
    if len(conns) < 2:
        return None, None
    cl = list(conns)
    p0, p1 = cl[0].Origin, cl[1].Origin
    d0 = cl[0].CoordinateSystem.BasisZ
    d1 = cl[1].CoordinateSystem.BasisZ
    if abs(d0.DotProduct(d1)) > 0.9999:
        return None, None
    pn = d0.CrossProduct(d1).Normalize()
    pp0 = pn.CrossProduct(d0).Normalize()
    pp1 = pn.CrossProduct(d1).Normalize()
    w = p0 - p1
    a = pp0.DotProduct(pp0); b = pp0.DotProduct(pp1); c = pp1.DotProduct(pp1)
    dd = pp0.DotProduct(w); ee = pp1.DotProduct(w)
    dn = a * c - b * b
    if abs(dn) < 1e-12:
        return None, None
    t = (b * ee - c * dd) / dn
    ctr = p0 + pp0 * t
    r = ctr.DistanceTo(p0)
    if abs(r - ctr.DistanceTo(p1)) > 0.01:
        pp0 = pp0.Negate(); pp1 = pp1.Negate()
        w = p0 - p1; dd = pp0.DotProduct(w); ee = pp1.DotProduct(w)
        dn = pp0.DotProduct(pp0) * pp1.DotProduct(pp1) - pp0.DotProduct(pp1) ** 2
        if abs(dn) < 1e-12:
            return None, None
        t = (pp0.DotProduct(pp1) * ee - pp1.DotProduct(pp1) * dd) / dn
        ctr = p0 + pp0 * t
        r = ctr.DistanceTo(p0)
    return ctr, r


def get_bend_angle(elem):
    conns = get_connectors(elem)
    if len(conns) < 2:
        return None
    cl = list(conns)
    d0 = cl[0].CoordinateSystem.BasisZ
    d1 = cl[1].CoordinateSystem.BasisZ
    dot = max(-1.0, min(1.0, d0.DotProduct(d1)))
    return 180.0 - math.degrees(math.acos(dot))


# ------------- category sets --------------

PIPE_CATS = {
    int(BuiltInCategory.OST_Conduit),
    int(BuiltInCategory.OST_PipeCurves),
}
FIT_CATS = {
    int(BuiltInCategory.OST_ConduitFitting),
    int(BuiltInCategory.OST_PipeFitting),
}
