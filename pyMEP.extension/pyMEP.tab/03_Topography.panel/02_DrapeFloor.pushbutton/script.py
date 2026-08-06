# -*- coding: utf-8 -*-
"""Drape Floor - drape selected floors onto the Toposolid / Topography
below them.

Samples points along each floor's sketch boundary (and optionally an
interior grid at the same spacing), ray-casts straight DOWN onto
terrain in the host model or any loaded link, then adds the hits as
slab-shape points so the floor follows the ground. Floors with
openings are handled - hole loops are respected when generating the
interior grid.

Works in Revit 2022-2026: targets OST_Toposolid where it exists and
falls back to legacy OST_Topography.
"""

__title__  = "Drape Floor\nto Topo"
__author__ = "Fid / Glent Group"

import math
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

from pymep_config import load_settings, save_settings
from pymep_log import Logger

from Autodesk.Revit.DB import (
    BuiltInCategory,
    FilteredElementCollector,
    FindReferenceTarget,
    Floor,
    ReferenceIntersector,
    RevitLinkInstance,
    UnitTypeId,
    UnitUtils,
    View3D,
    XYZ,
)
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()
log = Logger(output, "DrapeFloor")

RAY_START_Z = 30000.0   # ft (~9 km) - cast rays from safely above any terrain
DEDUPE_TOL = 0.01       # ft (~3 mm) - merge coincident sample points
MIN_HIT_PROXIMITY = 1e-9

log("### Drape floor to topo")


# ------------------------------------------------------------------ selection
class FloorFilter(ISelectionFilter):
    def AllowElement(self, elem):
        return isinstance(elem, Floor)

    def AllowReference(self, ref, point):
        return False


def get_floors():
    picked = [el for el in revit.get_selection().elements
              if isinstance(el, Floor)]
    if picked:
        return picked
    try:
        refs = uidoc.Selection.PickObjects(
            ObjectType.Element, FloorFilter(),
            "Pick floors to drape, then hit Finish")
    except Exception:
        return []
    return [doc.GetElement(r) for r in refs]


# ------------------------------------------------------------------- geometry
def boundary_loops(floor):
    """Sketch profile as a list of loops, each a list of DB curves."""
    sketch = doc.GetElement(floor.SketchId)
    return [list(loop) for loop in sketch.Profile]


def sample_curve(curve, spacing):
    """Points along a bound curve at <= spacing apart, endpoints
    included."""
    n = max(1, int(math.ceil(curve.Length / spacing)))
    return [curve.Evaluate(float(i) / n, True) for i in range(n + 1)]


def dedupe(points, tol=DEDUPE_TOL):
    out = []
    for p in points:
        if all(p.DistanceTo(q) > tol for q in out):
            out.append(p)
    return out


def loop_to_poly(loop_curves):
    """Tessellate a sketch loop into a closed 2D polygon (handles
    arcs)."""
    poly = []
    for c in loop_curves:
        tess = list(c.Tessellate())
        poly.extend(tess[:-1])          # last pt = next curve's first pt
    return [(p.X, p.Y) for p in poly]


def point_in_polys(x, y, polys):
    """Even-odd test across all loops, so hole loops toggle back
    out."""
    inside = False
    for poly in polys:
        j = len(poly) - 1
        for i in range(len(poly)):
            xi, yi = poly[i]
            xj, yj = poly[j]
            if ((yi > y) != (yj > y)) and \
                    (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
                inside = not inside
            j = i
    return inside


def grid_points(floor, polys, spacing):
    bb = floor.get_BoundingBox(None)
    if bb is None:
        return []
    nx = max(1, int(math.ceil((bb.Max.X - bb.Min.X) / spacing)))
    ny = max(1, int(math.ceil((bb.Max.Y - bb.Min.Y) / spacing)))
    pts = []
    for i in range(nx + 1):
        x = bb.Min.X + (bb.Max.X - bb.Min.X) * i / nx
        for j in range(ny + 1):
            y = bb.Min.Y + (bb.Max.Y - bb.Min.Y) * j / ny
            if point_in_polys(x, y, polys):
                pts.append(XYZ(x, y, 0.0))
    return pts


# ------------------------------------------------------------------ raybounce
def find_view3d():
    av = doc.ActiveView
    if isinstance(av, View3D) and not av.IsTemplate:
        return av
    for v in FilteredElementCollector(doc).OfClass(View3D):
        if not v.IsTemplate:
            return v
    return None


def id_value(eid):
    try:
        return eid.Value            # Revit 2024+
    except AttributeError:
        return eid.IntegerValue     # Revit 2023 and earlier


def terrain_category_ids():
    ids = set()
    for name in ("OST_Toposolid", "OST_Topography"):
        if hasattr(BuiltInCategory, name):
            ids.add(int(getattr(BuiltInCategory, name)))
    return ids


def make_intersector(view3d):
    ri = ReferenceIntersector(view3d)
    ri.TargetType = FindReferenceTarget.Face
    ri.FindReferencesInRevitLinks = True
    return ri


def is_terrain_hit(ref_ctx, target_ids):
    ref = ref_ctx.GetReference()
    el = doc.GetElement(ref.ElementId)
    if isinstance(el, RevitLinkInstance):
        ldoc = el.GetLinkDocument()
        if ldoc is None:
            return False
        el = ldoc.GetElement(ref.LinkedElementId)
    if el is None or el.Category is None:
        return False
    return id_value(el.Category.Id) in target_ids


def nearest_terrain_hit(ri, origin, direction, target_ids):
    refs = ri.Find(origin, direction)
    if refs is None or refs.Count == 0:
        return None
    best = None
    for rc in refs:
        if rc.Proximity <= MIN_HIT_PROXIMITY:
            continue
        if not is_terrain_hit(rc, target_ids):
            continue
        if best is None or rc.Proximity < best.Proximity:
            best = rc
    if best is None:
        return None
    return best.GetReference().GlobalPoint


# --------------------------------------------------------------- shape editor
def get_shape_editor(floor):
    try:
        editor = floor.GetSlabShapeEditor()     # Revit 2024+
    except AttributeError:
        editor = floor.SlabShapeEditor          # Revit 2023 and earlier
    try:
        if not editor.IsEnabled:
            editor.Enable()
    except Exception:
        pass    # Enable() deprecated in newer APIs; DrawPoint enables it
    return editor


# ------------------------------------------------------------------------ run
def main():
    settings = load_settings()

    floors = get_floors()
    if not floors:
        log("Nothing selected - nothing changed.")
        log.close()
        script.exit()
    log("Draping **{}** floor(s).".format(len(floors)))

    spacing_str = forms.ask_for_string(
        default="{:g}".format(float(settings.get("drape_spacing_mm",
                                                 5000.0))),
        prompt="Max point spacing (mm):",
        title="Drape Floor to Topo")
    if not spacing_str:
        log.close()
        script.exit()
    try:
        spacing_mm = float(spacing_str)
        spacing = UnitUtils.ConvertToInternalUnits(
            spacing_mm, UnitTypeId.Millimeters)
    except ValueError:
        log.close()
        forms.alert('"{}" is not a number.'.format(spacing_str),
                    exitscript=True)

    grid_opt = "Edges + interior grid" \
        if settings.get("drape_grid", False) else "Edges only"
    mode = forms.CommandSwitchWindow.show(
        ["Edges only", "Edges + interior grid"],
        message="Where should drape points go? (last time: {})".format(
            grid_opt))
    if not mode:
        log.close()
        script.exit()
    include_grid = (mode == "Edges + interior grid")

    settings["drape_spacing_mm"] = spacing_mm
    settings["drape_grid"] = include_grid
    try:
        save_settings(settings)
    except Exception:
        pass
    log("Spacing **{:g} mm**, {}.".format(
        spacing_mm, mode.lower()))

    view3d = find_view3d()
    if view3d is None:
        log.close()
        forms.alert("No 3D view found in the model - create one and "
                    "re-run.", exitscript=True)
    log("Ray-casting in 3D view **{}** (toposolid / topography, host "
        "model + links).".format(view3d.Name))

    target_ids = terrain_category_ids()
    ri = make_intersector(view3d)
    down = XYZ(0, 0, -1)

    total_added, total_missed = 0, 0
    with revit.Transaction("Drape floors to topo"):
        for floor in floors:
            try:
                loops = boundary_loops(floor)
            except Exception as err:
                log("! floor {}: could not read its sketch ({}) - "
                    "skipped".format(floor.Id, err))
                continue

            pts = []
            for loop in loops:
                for c in loop:
                    pts.extend(sample_curve(c, spacing))
            pts = dedupe(pts)

            if include_grid:
                polys = [loop_to_poly(l) for l in loops]
                pts = dedupe(pts + grid_points(floor, polys, spacing))

            try:
                editor = get_shape_editor(floor)
            except Exception as err:
                log("! floor {}: cannot be shape-edited (slope arrow "
                    "/ span direction?): {} - skipped".format(
                        floor.Id, err))
                continue

            added, missed = 0, 0
            for p in pts:
                hit = nearest_terrain_hit(
                    ri, XYZ(p.X, p.Y, RAY_START_Z), down, target_ids)
                if hit is None:
                    missed += 1
                    continue
                try:
                    editor.DrawPoint(hit)
                    added += 1
                except Exception:
                    missed += 1

            total_added += added
            total_missed += missed
            log("**Floor {}** - {} point(s) added, {} missed".format(
                floor.Id, added, missed))

    log.close()
    if total_added == 0:
        forms.alert('No terrain hits found.\n\nCheck there is a '
                    'Toposolid or Topography below the floor(s), and '
                    'that it is visible in the 3D view used for '
                    'ray-casting ("{}").'.format(view3d.Name))
    else:
        forms.alert("Done: {} point(s) added across {} floor(s), {} "
                    "ray(s) missed.".format(total_added, len(floors),
                                            total_missed))


main()
