# -*- coding: utf-8 -*-
"""Fence - the Revit-bound half shared by the Fence and Update Fence
buttons: tessellation, the terrain ray-cast, and placing / moving the
instances. The caller owns the Transaction; every function here is
fail-soft per instance and reports what happened.

IronPython 2.7 / Revit 2022-2026.
"""

import clr
clr.AddReference("RevitAPI")

import math

from Autodesk.Revit.DB import (
    BuiltInParameter,
    ElementTransformUtils,
    FilteredElementCollector,
    FindReferenceTarget,
    Level,
    Line,
    ReferenceIntersector,
    RevitLinkInstance,
    View3D,
    XYZ,
)
from Autodesk.Revit.DB.Structure import StructuralType

import pymep_fence as F

MIN_HIT_PROXIMITY = 1e-9


def id_value(eid):
    try:
        return eid.Value            # Revit 2024+
    except AttributeError:
        return eid.IntegerValue     # Revit 2023 and earlier


def find_view3d(doc):
    av = doc.ActiveView
    if isinstance(av, View3D) and not av.IsTemplate:
        return av
    for v in FilteredElementCollector(doc).OfClass(View3D):
        if not v.IsTemplate:
            return v
    return None


def make_intersector(view3d):
    ri = ReferenceIntersector(view3d)
    ri.TargetType = FindReferenceTarget.Face
    return ri


def tessellate(line_el):
    """The line element's curve as [(x, y, z)] - None when it has no
    geometry."""
    curve = line_el.GeometryCurve
    if curve is None:
        return None
    return [(p.X, p.Y, p.Z) for p in curve.Tessellate()]


def ray_start_z(elements):
    """Just above the given elements' bounding boxes -
    ReferenceIntersector silently misses when the origin is
    kilometres from the geometry."""
    tops = []
    for el in elements:
        try:
            bb = el.get_BoundingBox(None)
            if bb is not None:
                tops.append(bb.Max.Z)
        except Exception:
            pass
    return (max(tops) + 10.0) if tops else 30000.0


def topmost_hit(doc, ri, origin, terrain_id):
    """The picked terrain's TOP surface under a straight-down ray -
    smallest proximity among hits on that element only."""
    refs = ri.Find(origin, XYZ(0, 0, -1))
    if refs is None or refs.Count == 0:
        return None
    best = None
    for rc in refs:
        if rc.Proximity <= MIN_HIT_PROXIMITY:
            continue
        ref = rc.GetReference()
        el = doc.GetElement(ref.ElementId)
        if isinstance(el, RevitLinkInstance):
            continue
        if id_value(ref.ElementId) != terrain_id:
            continue
        if best is None or rc.Proximity < best.Proximity:
            best = rc
    if best is None:
        return None
    return best.GetReference().GlobalPoint


def sorted_levels(doc):
    return sorted(
        [l for l in FilteredElementCollector(doc).OfClass(Level)],
        key=lambda l: l.Elevation)


def level_for(levels, z):
    best = None
    for l in levels:
        if l.Elevation <= z + 1e-6:
            best = l
    return best or (levels[0] if levels else None)


def _rotate_about(doc, elem_id, pt, angle):
    if abs(angle) <= 1e-9:
        return
    axis = Line.CreateBound(XYZ(pt.X, pt.Y, pt.Z),
                            XYZ(pt.X, pt.Y, pt.Z + 1.0))
    ElementTransformUtils.RotateElement(doc, elem_id, axis, angle)


def place_instances(doc, symbol, poly, dists, terrain_id, ri, ray_z,
                    levels, extra_rot=0.0):
    """Place ``symbol`` at every station, draped and rotated - the
    line's direction plus ``extra_rot`` (radians, the config's custom
    rotation). Runs inside the caller's open Transaction. Returns
    (records, missed, failed): records = [{"uid", "station_ft",
    "angle"}] for the registry, missed = stations with no terrain
    hit, failed = count of placement errors (first reason in
    failed_reason)."""
    records, missed, failed = [], [], 0
    failed_reason = [None]
    try:
        if not symbol.IsActive:
            symbol.Activate()
            doc.Regenerate()
    except Exception:
        pass
    for d in dists:
        p, tang = F.point_at(poly, d)
        hit = topmost_hit(doc, ri, XYZ(p[0], p[1], ray_z), terrain_id)
        if hit is None:
            missed.append(d)
            continue
        lvl = level_for(levels, hit.Z)
        try:
            inst = doc.Create.NewFamilyInstance(
                XYZ(hit.X, hit.Y, hit.Z), symbol, lvl,
                StructuralType.NonStructural)
            # belt and braces: the level overload usually honours the
            # Z, but forcing the offset makes it certain
            if lvl is not None:
                for bip in ("INSTANCE_FREE_HOST_OFFSET_PARAM",
                            "INSTANCE_ELEVATION_PARAM"):
                    try:
                        par = inst.get_Parameter(
                            getattr(BuiltInParameter, bip))
                        if par is not None and not par.IsReadOnly:
                            par.Set(hit.Z - lvl.Elevation)
                            break
                    except Exception:
                        continue
            ang = math.atan2(tang[1], tang[0]) + extra_rot
            _rotate_about(doc, inst.Id, hit, ang)
            records.append({"uid": inst.UniqueId, "station_ft": d,
                            "angle": ang})
        except Exception as ex:
            failed += 1
            if failed_reason[0] is None:
                failed_reason[0] = "{}".format(ex)
    return records, missed, failed, failed_reason[0]


def move_instances(doc, pairs, poly, terrain_id, ri, ray_z,
                   extra_rot=0.0):
    """MOVE each stored instance to its new station: pairs =
    [(instance_dict, element, new_station)]. The rotation applied is
    the DELTA from the stored angle (which includes any config
    rotation), so user tweaks on top survive AND a changed config
    rotation lands. Returns (records, missed, failed) like
    place_instances."""
    records, missed, failed = [], [], 0
    for inst_d, el, d in pairs:
        p, tang = F.point_at(poly, d)
        hit = topmost_hit(doc, ri, XYZ(p[0], p[1], ray_z), terrain_id)
        if hit is None:
            missed.append(d)
            records.append(inst_d)      # stays where it is
            continue
        try:
            loc = el.Location.Point
            delta = XYZ(hit.X - loc.X, hit.Y - loc.Y, hit.Z - loc.Z)
            if delta.GetLength() > 1e-9:
                ElementTransformUtils.MoveElement(doc, el.Id, delta)
            old_ang = float(inst_d.get("angle") or 0.0)
            new_ang = math.atan2(tang[1], tang[0]) + extra_rot
            _rotate_about(doc, el.Id, hit, new_ang - old_ang)
            records.append({"uid": inst_d.get("uid"), "station_ft": d,
                            "angle": new_ang})
        except Exception:
            failed += 1
            records.append(inst_d)
    return records, missed, failed
