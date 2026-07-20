# -*- coding: utf-8 -*-
"""Align family instances to the top of a Toposolid / Topography / Floor.

Used by Topography > Align to Topo: for every instance of the chosen
family types, the instance's plan position (X,Y) is projected straight
down onto the top of the chosen surface elements (a vertical ray from
above, nearest hit wins - so stacked surfaces resolve to the TOP one),
and the instance's 'Elevation from Level' / 'Offset from Host' is set so
the instance sits exactly on the surface at that point:

    offset = surface_Z(x, y) - host_level_elevation

The projection uses ReferenceIntersector, which needs a (non-template)
3D view but works identically for Toposolids, legacy TopographySurfaces
and Floors - including curved / slabbed tops.
"""

import clr
clr.AddReference("RevitAPI")
clr.AddReference("System")

from System.Collections.Generic import List

from Autodesk.Revit.DB import (
    BuiltInParameter, ElementId, FamilyInstance, FilteredElementCollector,
    FindReferenceTarget, Floor, LocationPoint, ReferenceIntersector,
    SubTransaction, Transaction, View3D, XYZ,
)

try:
    from Autodesk.Revit.DB.Architecture import TopographySurface
except Exception:
    TopographySurface = None
try:
    from Autodesk.Revit.DB import Toposolid
except Exception:
    Toposolid = None      # pre-2024 Revit has no Toposolid class

from pymep_revit import safe_name


def _say(log, m):
    if log is not None:
        log(m)


def is_surface_element(el):
    """True for the element kinds Align to Topo can project onto."""
    if el is None:
        return False
    if isinstance(el, Floor):
        return True
    if TopographySurface is not None and isinstance(el, TopographySurface):
        return True
    if Toposolid is not None and isinstance(el, Toposolid):
        return True
    return False


def list_leveled_family_types(doc):
    """[(label, symbol_id, count)] for every placed family type whose
    instances can take a level offset, sorted by label. The label carries
    'Family : Type   (N placed)' for the picker."""
    groups = {}
    for inst in FilteredElementCollector(doc).OfClass(FamilyInstance):
        try:
            if not isinstance(inst.Location, LocationPoint):
                continue
            sym = inst.Symbol
            key = sym.Id.IntegerValue
            if key not in groups:
                groups[key] = [
                    "{} : {}".format(sym.Family.Name, safe_name(sym)),
                    sym.Id, 0]
            groups[key][2] += 1
        except Exception:
            continue
    out = [(g[0], g[1], g[2]) for g in groups.values()]
    out.sort(key=lambda t: t[0].lower())
    return out


def _get_3d_view(doc):
    """A non-template 3D view for the ReferenceIntersector (the default
    {3D} view preferred)."""
    fallback = None
    for v in FilteredElementCollector(doc).OfClass(View3D):
        try:
            if v.IsTemplate or v.IsPerspective:
                continue
            if v.Name.startswith("{3D"):
                return v
            if fallback is None:
                fallback = v
        except Exception:
            continue
    return fallback


def _top_z(doc, targets_bbox_top, intersector, x, y):
    """Z (internal ft) of the topmost target surface at plan point (x, y),
    or None when the vertical ray misses every target."""
    origin = XYZ(x, y, targets_bbox_top)
    hit = intersector.FindNearest(origin, XYZ(0, 0, -1))
    if hit is None:
        return None
    try:
        return hit.GetReference().GlobalPoint.Z
    except Exception:
        return None


def align_instances_to_surfaces(doc, symbol_ids, surface_ids, log=None):
    """Set each instance's level offset so it sits on the surfaces' top.

    symbol_ids:  iterable of FamilySymbol ElementIds (chosen types).
    surface_ids: iterable of target ElementIds (Toposolid / Topo / Floor).
    Returns (adjusted, missed, skipped, unchanged):
      adjusted  - offset written,
      missed    - instance XY not above any chosen surface,
      skipped   - no location point / host level / writable offset param,
      unchanged - already within 0.5 mm of the surface.
    """
    sym_keys = set(s.IntegerValue for s in symbol_ids)

    instances = []
    for inst in FilteredElementCollector(doc).OfClass(FamilyInstance):
        try:
            if inst.Symbol.Id.IntegerValue in sym_keys:
                instances.append(inst)
        except Exception:
            continue
    if not instances:
        raise ValueError("No placed instances of the chosen types.")

    view3d = _get_3d_view(doc)
    if view3d is None:
        raise ValueError(
            "No non-template 3D view in this model - open/create the "
            "default {3D} view once and re-run (the surface projection "
            "needs it).")

    id_list = List[ElementId]()
    top = None
    for sid in surface_ids:
        id_list.Add(sid)
        try:
            bb = doc.GetElement(sid).get_BoundingBox(None)
            if bb is not None and (top is None or bb.Max.Z > top):
                top = bb.Max.Z
        except Exception:
            pass
    if top is None:
        top = 0.0
    ray_top = top + 100.0     # well above every target

    intersector = ReferenceIntersector(
        id_list, FindReferenceTarget.Face, view3d)

    adjusted = 0
    missed = 0
    skipped = 0
    unchanged = 0
    TOL = 0.5 / 304.8         # 0.5 mm in ft

    t = Transaction(doc, "Align to Topo")
    t.Start()
    for inst in instances:
        sub = SubTransaction(doc)
        try:
            loc = inst.Location
            if not isinstance(loc, LocationPoint):
                skipped += 1
                continue
            p = loc.Point

            level = None
            if inst.LevelId is not None and \
                    inst.LevelId != ElementId.InvalidElementId:
                level = doc.GetElement(inst.LevelId)
            if level is None:
                lp = inst.get_Parameter(BuiltInParameter.FAMILY_LEVEL_PARAM)
                if lp is not None:
                    level = doc.GetElement(lp.AsElementId())
            if level is None:
                skipped += 1
                continue

            z = _top_z(doc, ray_top, intersector, p.X, p.Y)
            if z is None:
                missed += 1
                continue

            offset = z - level.Elevation
            op = None
            for bip in (BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM,
                        BuiltInParameter.INSTANCE_ELEVATION_PARAM):
                cand = inst.get_Parameter(bip)
                if cand is not None and not cand.IsReadOnly:
                    op = cand
                    break
            if op is None:
                skipped += 1
                continue

            if abs(op.AsDouble() - offset) <= TOL:
                unchanged += 1
                continue

            sub.Start()
            op.Set(offset)
            sub.Commit()
            adjusted += 1
        except Exception:
            try:
                sub.RollBack()
            except Exception:
                pass
            skipped += 1
    t.Commit()

    _say(log, "Adjusted **{}**, already on surface {}, no surface under "
              "XY {}, skipped {}.".format(adjusted, unchanged, missed,
                                          skipped))
    return adjusted, missed, skipped, unchanged


def drape_floor_to_surfaces(doc, floor, surface_ids, log=None):
    """Move every slab-shape sub-element point of ``floor`` so it sits on
    the chosen surfaces' TOP at that plan position (vertical ray from
    above, nearest hit wins). Any previous shape edits are reset first,
    so each point gets exactly one move from the flat plane - which makes
    the ModifySubElement offset unambiguous. Points whose vertical ray
    misses every surface stay on the flat plane and are reported.

    Returns (moved, missed, total_points)."""
    view3d = _get_3d_view(doc)
    if view3d is None:
        raise RuntimeError(
            "No usable (non-template) 3D view for the projection - open "
            "the default {3D} view once and re-run.")

    id_list = List[ElementId]()
    top = None
    for sid in surface_ids:
        id_list.Add(sid)
        try:
            bb = doc.GetElement(sid).get_BoundingBox(None)
            if bb is not None:
                top = bb.Max.Z if top is None else max(top, bb.Max.Z)
        except Exception:
            pass
    if top is None:
        raise RuntimeError("Could not read a bounding box off the picked "
                           "surface - is it hidden or empty?")
    shoot_from = top + 10.0
    intersector = ReferenceIntersector(id_list, FindReferenceTarget.Face,
                                       view3d)

    # Revit 2024+ has GetSlabShapeEditor(); older builds the property.
    editor = None
    try:
        editor = floor.GetSlabShapeEditor()
    except Exception:
        editor = None
    if editor is None:
        try:
            editor = floor.SlabShapeEditor
        except Exception:
            editor = None
    if editor is None:
        raise RuntimeError(
            "This floor exposes no slab shape editor (sloped-by-arrow "
            "and some in-place floors cannot be shape edited).")

    moved = 0
    missed = 0
    total = 0
    t = Transaction(doc, "Drape floor to topo")
    t.Start()
    try:
        # ResetSlabShape FIRST: it clears prior edits AND disables shape
        # editing (wiping the vertices), so Enable() must come AFTER it -
        # the other way round reads back zero points.
        try:
            editor.ResetSlabShape()
        except Exception:
            pass    # never shape-edited yet - nothing to reset
        try:
            if not editor.IsEnabled:
                editor.Enable()
        except Exception:
            pass
        try:
            doc.Regenerate()
        except Exception:
            pass

        verts = list(editor.SlabShapeVertices)
        if not verts:
            # some builds surface the vertices only after another
            # enable + regenerate round
            try:
                editor.Enable()
                doc.Regenerate()
            except Exception:
                pass
            verts = list(editor.SlabShapeVertices)
        total = len(verts)
        if not total:
            raise RuntimeError(
                "The floor has no slab-shape points even after enabling "
                "shape editing. Floors sloped by a SLOPE ARROW (and some "
                "in-place / curved floors) cannot be shape edited - "
                "remove the slope arrow and re-run.")

        # read every target FIRST, then modify - keeps the ray casting
        # independent of the floor deforming under the loop
        plan = []
        for v in verts:
            p = v.Position
            plan.append((v, p, _top_z(doc, shoot_from, intersector,
                                      p.X, p.Y)))
        for (v, p, z) in plan:
            if z is None:
                missed += 1
                continue
            try:
                editor.ModifySubElement(v, z - p.Z)
                moved += 1
            except Exception:
                missed += 1
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    _say(log, "Slab shape: **{}** point(s) moved onto the surface, {} "
              "missed (no surface below/above that XY), {} total.".format(
                  moved, missed, total))
    return moved, missed, total
