# -*- coding: utf-8 -*-
"""Drape Floor - drape selected floors onto the Toposolid / Topography
below them.

Samples points along each floor's sketch boundary (and optionally an
interior grid with its OWN X / Y spacings), ray-casts straight DOWN
onto terrain in the host model or any loaded link, then adds the hits
as slab-shape points so the floor follows the ground. Floors with
openings are handled - hole loops are respected when generating the
interior grid. One dialog collects everything; every value is
remembered between runs.

Works in Revit 2022-2026: targets OST_Toposolid where it exists and
falls back to legacy OST_Topography.
"""

__title__  = "Drape Floor\nto Topo"
__author__ = "Fid / Glent Group"

import math
import os
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

from pymep_config import load_settings, save_settings
from pymep_log import Logger
from pymep_mesh import surface_z

from Autodesk.Revit.DB import (
    BuiltInCategory,
    FilteredElementCollector,
    FindReferenceTarget,
    Floor,
    GeometryInstance,
    Mesh as DBMesh,
    Options,
    ReferenceIntersector,
    RevitLinkInstance,
    Solid,
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

RAY_START_Z = 30000.0   # ft - LAST-RESORT ray start when no bbox reads;
                        # the real start is computed just above the
                        # candidates (ReferenceIntersector silently
                        # misses hits when the origin is kilometres
                        # from the geometry - a fixed 9 km start found
                        # NOTHING over a 12 m-high site)
DEDUPE_TOL = 0.01       # ft (~3 mm) - merge coincident sample points
MIN_HIT_PROXIMITY = 1e-9
NUDGE_FT = 50.0 / 304.8  # ~50 mm inward retry when the slab rejects a
                         # boundary point (curved edges are approximated
                         # by straight segments in shape editing, so a
                         # point ON the true arc can sit just outside)

XAML_PATH = os.path.join(
    os.path.dirname(os.path.abspath(sys.modules["pymep_config"].__file__)),
    "pymep_drape_floor.xaml")
ALL_TOPO = "(all terrain - host model + links)"

log("### Drape floor to topo")


# --------------------------------------------------------------------- dialog
class DrapeWindow(forms.WPFWindow):
    """One dialog for everything: existing-corners-only mode, edge
    spacing, the interior grid on/off, and the grid's OWN X / Y
    spacings with a 'match X' tick that keeps Y equal to X."""

    def __init__(self, settings, info_text, topo_labels):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self.result = None
        self.TxtInfo.Text = info_text
        self.CmbTopo.Items.Clear()
        self.CmbTopo.Items.Add(ALL_TOPO)
        for lbl in topo_labels:
            self.CmbTopo.Items.Add(lbl)
        self.CmbTopo.SelectedIndex = 0
        want = settings.get("drape_topo") or ALL_TOPO
        for i in range(self.CmbTopo.Items.Count):
            if str(self.CmbTopo.Items[i]) == want:
                self.CmbTopo.SelectedIndex = i
                break
        if settings.get("drape_hit", "top") == "low":
            self.RadHitLow.IsChecked = True
        else:
            self.RadHitTop.IsChecked = True
        self.ChkCorners.IsChecked = bool(settings.get(
            "drape_corners_only", False))
        self.TxtEdge.Text = "{:g}".format(
            float(settings.get("drape_spacing_mm", 5000.0)))
        self.ChkGrid.IsChecked = bool(settings.get("drape_grid", False))
        self.TxtGridX.Text = "{:g}".format(
            float(settings.get("drape_grid_x_mm",
                               settings.get("drape_spacing_mm", 5000.0))))
        self.TxtGridY.Text = "{:g}".format(
            float(settings.get("drape_grid_y_mm",
                               settings.get("drape_spacing_mm", 5000.0))))
        self.ChkMatch.IsChecked = bool(settings.get("drape_grid_match",
                                                    True))
        self.ChkMatchEdge.IsChecked = bool(settings.get(
            "drape_grid_match_edge", False))
        self.on_match_edge(None, None)
        self.on_match(None, None)
        self.on_corners(None, None)

    @staticmethod
    def _num(box):
        try:
            v = float(box.Text)
            return v if v > 0 else None
        except Exception:
            return None

    def read_values(self):
        """{'corners', 'edge', 'grid', 'gx', 'gy'} (mm), or None when
        a needed field is not a positive number."""
        corners = bool(self.ChkCorners.IsChecked)
        topo_idx = self.CmbTopo.SelectedIndex
        topo_lbl = str(self.CmbTopo.SelectedItem or ALL_TOPO)
        hit = "low" if self.RadHitLow.IsChecked else "top"
        if corners:
            return {"corners": True, "edge": None, "grid": False,
                    "gx": None, "gy": None,
                    "match": bool(self.ChkMatch.IsChecked),
                    "match_edge": bool(self.ChkMatchEdge.IsChecked),
                    "topo_idx": topo_idx, "topo_lbl": topo_lbl,
                    "hit": hit}
        edge = self._num(self.TxtEdge)
        if edge is None:
            return None
        grid = bool(self.ChkGrid.IsChecked)
        gx = gy = None
        if grid:
            gx = edge if self.ChkMatchEdge.IsChecked \
                else self._num(self.TxtGridX)
            gy = gx if self.ChkMatch.IsChecked else self._num(self.TxtGridY)
            if gx is None or gy is None:
                return None
        return {"corners": False, "edge": edge, "grid": grid,
                "gx": gx, "gy": gy,
                "match": bool(self.ChkMatch.IsChecked),
                "match_edge": bool(self.ChkMatchEdge.IsChecked),
                "topo_idx": topo_idx, "topo_lbl": topo_lbl,
                "hit": hit}

    def on_corners(self, sender, args):
        try:
            corners = bool(self.ChkCorners.IsChecked)
            self.TxtEdge.IsEnabled = not corners
            self.ChkGrid.IsEnabled = not corners
            if corners:
                self.ChkGrid.IsChecked = False
            self.on_grid(None, None)
        except Exception:
            pass

    def on_grid(self, sender, args):
        try:
            on = bool(self.ChkGrid.IsChecked)
            self.ChkMatchEdge.IsEnabled = on
            self.ChkMatch.IsEnabled = on
            self.TxtGridX.IsEnabled = (on and
                                       not self.ChkMatchEdge.IsChecked)
            self.TxtGridY.IsEnabled = (on and
                                       not self.ChkMatch.IsChecked)
        except Exception:
            pass

    def on_match_edge(self, sender, args):
        try:
            match = bool(self.ChkMatchEdge.IsChecked)
            self.TxtGridX.IsEnabled = (not match
                                       and bool(self.ChkGrid.IsChecked))
            if match:
                self.TxtGridX.Text = self.TxtEdge.Text
        except Exception:
            pass

    def on_edge(self, sender, args):
        try:
            if self.ChkMatchEdge.IsChecked:
                self.TxtGridX.Text = self.TxtEdge.Text
        except Exception:
            pass

    def on_match(self, sender, args):
        try:
            match = bool(self.ChkMatch.IsChecked)
            self.TxtGridY.IsEnabled = (not match
                                       and bool(self.ChkGrid.IsChecked))
            if match:
                self.TxtGridY.Text = self.TxtGridX.Text
        except Exception:
            pass

    def on_grid_x(self, sender, args):
        try:
            if self.ChkMatch.IsChecked:
                self.TxtGridY.Text = self.TxtGridX.Text
        except Exception:
            pass

    def on_drape(self, sender, args):
        v = self.read_values()
        if v is None:
            self.StatusText.Text = ("Every spacing must be a positive "
                                    "number (mm).")
            return
        self.result = v
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


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
    # pick ONE BY ONE: every click adds a floor, and a single ESC (or
    # right-click) CONTINUES with what was picked - no options-bar
    # Finish click. (Revit never routes the Enter key to a script in
    # pick mode, so ESC is the keyboard's 'done'.)
    got = []
    seen = set()
    while True:
        try:
            r = uidoc.Selection.PickObject(
                ObjectType.Element, FloorFilter(),
                "Pick floors to drape one by one ({} picked) - press "
                "ESC to continue".format(len(got)))
        except Exception:               # ESC / right-click: done
            break
        el = doc.GetElement(r.ElementId)
        if isinstance(el, Floor) and el.Id.IntegerValue not in seen:
            seen.add(el.Id.IntegerValue)
            got.append(el)
    return got


# ------------------------------------------------------------------- geometry
def boundary_loops(floor):
    """Sketch profile as a list of loops, each a list of DB curves."""
    sketch = doc.GetElement(floor.SketchId)
    return [list(loop) for loop in sketch.Profile]


def resample_poly(pts, spacing):
    """Walk a tessellated polyline, keeping the first point and every
    point at least ``spacing`` along from the last kept one (plus the
    final point). pts are (x, y, z) tuples; pure, unit-tested."""
    if not pts:
        return []
    out = [pts[0]]
    run = 0.0
    for i in range(1, len(pts)):
        a, b = pts[i - 1], pts[i]
        run += math.sqrt((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2 +
                         (b[2] - a[2]) ** 2)
        if run >= spacing or i == len(pts) - 1:
            out.append(b)
            run = 0.0
    return out


def sample_curve(curve, spacing):
    """Points along a sketch curve at <= spacing apart, endpoints
    included. UNBOUND curves (a full circle or ellipse drawn as one
    sketch edge - planters, kerb islands) cannot Evaluate(normalized),
    so they are tessellated and resampled at the spacing instead."""
    try:
        bound = curve.IsBound
    except Exception:
        bound = True
    if not bound:
        tess = [(q.X, q.Y, q.Z) for q in curve.Tessellate()]
        return [XYZ(x, y, z) for x, y, z in resample_poly(tess, spacing)]
    n = max(1, int(math.ceil(curve.Length / spacing)))
    return [curve.Evaluate(float(i) / n, True) for i in range(n + 1)]


def curve_corners(curve):
    """The curve's end points; an UNBOUND closed edge (full circle)
    has none, so its quarter points stand in."""
    try:
        if curve.IsBound:
            return [curve.GetEndPoint(0), curve.GetEndPoint(1)]
    except Exception:
        pass
    tess = list(curve.Tessellate())
    if not tess:
        return []
    step = max(1, len(tess) // 4)
    return [tess[i] for i in range(0, len(tess), step)]


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


def nudge_inward(p, polys, step=NUDGE_FT):
    """A point ~50 mm from ``p`` that lies INSIDE the loops - the
    retry position when the slab rejects a boundary point. Tries the
    8 compass directions; None when none lands inside (a needle-thin
    corner)."""
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                   (1, 1), (1, -1), (-1, 1), (-1, -1)):
        qx = p.X + dx * step
        qy = p.Y + dy * step
        if point_in_polys(qx, qy, polys):
            return XYZ(qx, qy, p.Z)
    return None


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


def grid_points(floor, polys, spacing_x, spacing_y):
    bb = floor.get_BoundingBox(None)
    if bb is None:
        return []
    nx = max(1, int(math.ceil((bb.Max.X - bb.Min.X) / spacing_x)))
    ny = max(1, int(math.ceil((bb.Max.Y - bb.Min.Y) / spacing_y)))
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


def _terrain_cats():
    return [getattr(BuiltInCategory, n)
            for n in ("OST_Toposolid", "OST_Topography")
            if hasattr(BuiltInCategory, n)]


def _el_label(el, prefix=""):
    try:
        nm = el.Name or ""
    except Exception:
        nm = ""
    return "{}{} (id {})".format(prefix, nm or "Terrain",
                                 id_value(el.Id))


def terrain_elements():
    """[(label, key)] for every toposolid / topography in the host
    model and every loaded link. key = ('host', ElementId) or
    (link_instance_ElementId, linked_ElementId)."""
    items = []
    for cat in _terrain_cats():
        try:
            for el in FilteredElementCollector(doc).OfCategory(
                    cat).WhereElementIsNotElementType():
                items.append((_el_label(el), ("host", el.Id)))
        except Exception:
            pass
    for li in FilteredElementCollector(doc).OfClass(RevitLinkInstance):
        ldoc = li.GetLinkDocument()
        if ldoc is None:
            continue
        try:
            title = ldoc.Title
        except Exception:
            title = "link"
        for cat in _terrain_cats():
            try:
                for el in FilteredElementCollector(ldoc).OfCategory(
                        cat).WhereElementIsNotElementType():
                    items.append((_el_label(
                        el, "{}: ".format(title)), (li.Id, el.Id)))
            except Exception:
                pass
    return items


def make_intersector(view3d):
    ri = ReferenceIntersector(view3d)
    ri.TargetType = FindReferenceTarget.Face
    ri.FindReferencesInRevitLinks = True
    return ri


def ray_start_z(floors, topo_items, target):
    """The rays' start level: ~3 m above the HIGHEST candidate terrain
    (and the floors), read from bounding boxes. ReferenceIntersector
    silently returns nothing when the origin sits kilometres above the
    geometry, so the start must hug the model."""
    tops = []
    keys = [k for _lbl, k in topo_items] if target is None else [target]
    for key in keys:
        try:
            el = doc.GetElement(key[1] if key[0] == "host" else key[0])
            bb = el.get_BoundingBox(None)
            if bb is not None:
                tops.append(bb.Max.Z)
        except Exception:
            pass
    for f in floors:
        try:
            bb = f.get_BoundingBox(None)
            if bb is not None:
                tops.append(bb.Max.Z)
        except Exception:
            pass
    if not tops:
        return RAY_START_Z
    return max(tops) + 10.0


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


def hit_matcher(target_ids, target):
    """The per-hit filter: ``target`` None accepts ANY toposolid /
    topography (host + links); ('host', eid) only that host element;
    (link_instance_eid, linked_eid) only that element of that link."""
    if target is None:
        return lambda rc: is_terrain_hit(rc, target_ids)

    def match(rc):
        ref = rc.GetReference()
        if target[0] == "host":
            el = doc.GetElement(ref.ElementId)
            if isinstance(el, RevitLinkInstance):
                return False
            return id_value(ref.ElementId) == id_value(target[1])
        return (id_value(ref.ElementId) == id_value(target[0]) and
                id_value(ref.LinkedElementId) == id_value(target[1]))
    return match


def nearest_terrain_hit(ri, origin, direction, match, lowest=False):
    """The ray's terrain hit: the ray starts ~9 km up, so the SMALLEST
    proximity is the TOPMOST surface and the LARGEST the LOWEST."""
    refs = ri.Find(origin, direction)
    if refs is None or refs.Count == 0:
        return None
    best = None
    for rc in refs:
        if rc.Proximity <= MIN_HIT_PROXIMITY:
            continue
        if not match(rc):
            continue
        if best is None or (rc.Proximity > best.Proximity
                            if lowest else
                            rc.Proximity < best.Proximity):
            best = rc
    if best is None:
        return None
    return best.GetReference().GlobalPoint


def probe_ray(ri, origin, direction):
    """One diagnostic ray, logged: did the intersector return ANYTHING,
    and on which categories? Explains a silent all-miss instead of
    leaving the user with a bare 'no terrain hits'."""
    try:
        refs = ri.Find(origin, direction)
    except Exception as ex:
        log("- probe: the intersector THREW: {}".format(ex))
        return
    if refs is None or refs.Count == 0:
        log("- probe: the intersector returned NO hits at all from "
            "{:.1f} m up - the terrain isn't reachable in that view "
            "(hidden category / filter / workset, or an API miss).".format(
                origin.Z * 0.3048))
        return
    cats = {}
    for rc in refs:
        try:
            ref = rc.GetReference()
            el = doc.GetElement(ref.ElementId)
            if isinstance(el, RevitLinkInstance):
                ldoc = el.GetLinkDocument()
                el = (ldoc.GetElement(ref.LinkedElementId)
                      if ldoc is not None else None)
            nm = (el.Category.Name if el is not None
                  and el.Category is not None else "?")
        except Exception:
            nm = "?"
        cats[nm] = cats.get(nm, 0) + 1
    log("- probe: {} hit(s), but NONE on terrain - they landed on: "
        "{}.".format(sum(cats.values()),
                     ", ".join("{} x{}".format(k, v)
                               for k, v in sorted(cats.items(),
                                                  key=lambda kv: -kv[1]))))


# ----------------------------------------------------- geometry fallback
def _mesh_tris(m, tf, out):
    if m is None:
        return
    try:
        n = m.NumTriangles
    except Exception:
        return
    for i in range(n):
        try:
            t = m.get_Triangle(i)
            vs = []
            for k in range(3):
                p = t.get_Vertex(k)
                if tf is not None:
                    p = tf.OfPoint(p)
                vs.append((p.X, p.Y, p.Z))
            out.append(tuple(vs))
        except Exception:
            pass


def _tris_from_geom(geo, tf, out):
    if geo is None:
        return
    for g in geo:
        if isinstance(g, Solid):
            for f in g.Faces:
                try:
                    _mesh_tris(f.Triangulate(), tf, out)
                except Exception:
                    pass
        elif isinstance(g, DBMesh):
            _mesh_tris(g, tf, out)
        elif isinstance(g, GeometryInstance):
            try:
                # instance geometry comes back in MODEL coordinates
                _tris_from_geom(g.GetInstanceGeometry(), tf, out)
            except Exception:
                pass


def terrain_triangles(topo_items, target):
    """Every candidate terrain's OWN triangles in model coordinates -
    the view-independent ground the fallback reads. Links are carried
    through their instance transform."""
    tris = []
    keys = [k for _lbl, k in topo_items] if target is None else [target]
    for key in keys:
        try:
            if key[0] == "host":
                el = doc.GetElement(key[1])
                tf = None
            else:
                li = doc.GetElement(key[0])
                ldoc = li.GetLinkDocument()
                if ldoc is None:
                    continue
                el = ldoc.GetElement(key[1])
                tf = li.GetTotalTransform()
            if el is None:
                continue
            _tris_from_geom(el.get_Geometry(Options()), tf, tris)
        except Exception as ex:
            log("- couldn't read geometry of a terrain candidate: {}"
                .format(ex))
    return tris


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
        pass    # Enable() deprecated in newer APIs; adding a point
                # enables it
    return editor


def point_drawer(editor):
    """The editor's add-a-point method, whatever this Revit calls it:
    AddPoint (Revit 2024+) or DrawPoint (2023 and earlier). A build
    that has dropped DrawPoint makes every point 'rejected by the
    slab' if the old name is called blind."""
    fn = getattr(editor, "AddPoint", None)
    if fn is None:
        fn = getattr(editor, "DrawPoint", None)
    if fn is None:
        raise AttributeError(
            "this Revit's SlabShapeEditor has neither AddPoint nor "
            "DrawPoint")
    return fn


def vertex_modifier(editor):
    """The editor's move-an-existing-vertex method, or None. Moving
    the slab's OWN sketch vertices onto the terrain is what stops the
    surface tearing down to the level at every corner."""
    return getattr(editor, "ModifySubElement", None)


def reset_shape(editor):
    """Wipe any previous shape edits so a re-run starts CLEAN instead
    of stacking new points on old spikes."""
    fn = getattr(editor, "ResetSlabShape", None)
    if fn is None:
        return False
    fn()
    return True


VERTEX_Z_TOL = 0.003        # ft (~1 mm) - close enough counts as ON
VERTEX_XY_TOL = 0.033       # ft (~10 mm) - a sample THIS near an
                            # existing vertex IS that vertex


def set_vertex_z(modify, v, want_z):
    """Drive one existing slab-shape vertex to ``want_z`` (absolute
    model Z). The move is applied as a delta from the vertex's CURRENT
    elevation - on a freshly reset slab the delta and offset-from-base
    conventions coincide, and the read-back pass corrects the result
    either way."""
    try:
        for _i in range(2):
            cur = v.Position.Z
            if abs(cur - want_z) <= VERTEX_Z_TOL:
                return True
            modify(v, want_z - cur)
        return abs(v.Position.Z - want_z) <= VERTEX_Z_TOL
    except Exception:
        return False


# ------------------------------------------------------------------------ run
def main():
    settings = load_settings()

    floors = get_floors()
    if not floors:
        log("Nothing selected - nothing changed.")
        log.close()
        script.exit()
    log("Draping **{}** floor(s).".format(len(floors)))

    topo_items = terrain_elements()
    win = DrapeWindow(settings, "{} floor(s) selected".format(
        len(floors)), [lbl for lbl, _k in topo_items])
    win.ShowDialog()
    if win.result is None:
        log("Cancelled - nothing changed.")
        log.close()
        script.exit()
    opt = win.result

    settings["drape_topo"] = opt["topo_lbl"]
    settings["drape_hit"] = opt["hit"]
    settings["drape_corners_only"] = opt["corners"]
    if not opt["corners"]:
        settings["drape_spacing_mm"] = opt["edge"]
        settings["drape_grid"] = opt["grid"]
        if opt["grid"]:
            settings["drape_grid_x_mm"] = opt["gx"]
            settings["drape_grid_y_mm"] = opt["gy"]
        settings["drape_grid_match"] = opt["match"]
        settings["drape_grid_match_edge"] = opt["match_edge"]
    try:
        save_settings(settings)
    except Exception:
        pass

    def _mm2ft(v):
        return UnitUtils.ConvertToInternalUnits(v, UnitTypeId.Millimeters)

    corners_only = opt["corners"]
    include_grid = opt["grid"]
    spacing = gx_ft = gy_ft = None
    if corners_only:
        log("Existing sketch CORNERS only - no extra edge points, no "
            "grid.")
    elif include_grid:
        spacing = _mm2ft(opt["edge"])
        gx_ft = _mm2ft(opt["gx"])
        gy_ft = _mm2ft(opt["gy"])
        ties = []
        if opt["match_edge"]:
            ties.append("X matches the edges")
        if opt["match"]:
            ties.append("Y matches X")
        log("Edges at **{:g} mm**; interior grid at **{:g} x {:g} "
            "mm**{}.".format(opt["edge"], opt["gx"], opt["gy"],
                             " ({})".format(", ".join(ties))
                             if ties else ""))
    else:
        spacing = _mm2ft(opt["edge"])
        log("Edges at **{:g} mm**, no interior grid.".format(
            opt["edge"]))

    view3d = find_view3d()
    if view3d is None:
        log.close()
        forms.alert("No 3D view found in the model - create one and "
                    "re-run.", exitscript=True)

    target = None
    if opt["topo_idx"] > 0 and opt["topo_idx"] <= len(topo_items):
        target = topo_items[opt["topo_idx"] - 1][1]
    lowest = (opt["hit"] == "low")
    log("Ray-casting in 3D view **{}** onto **{}** - the **{}** "
        "surface wins where they stack.".format(
            view3d.Name,
            opt["topo_lbl"] if target is not None
            else "every toposolid / topography (host model + links)",
            "LOWEST" if lowest else "TOPMOST"))

    match = hit_matcher(terrain_category_ids(), target)
    ri = make_intersector(view3d)
    down = XYZ(0, 0, -1)
    ray_z = ray_start_z(floors, topo_items, target)
    log("Rays start at internal **{:.1f} m** (just above the "
        "candidates).".format(ray_z * 0.3048))

    # geometry fallback state: built lazily on the FIRST ray miss, a
    # single diagnostic probe explains WHY the rays are missing
    state = {"tris": None, "probed": False, "from_mesh": 0}

    def ground_hit(x, y):
        hit = nearest_terrain_hit(ri, XYZ(x, y, ray_z), down, match,
                                  lowest=lowest)
        if hit is not None:
            return hit
        if not state["probed"]:
            state["probed"] = True
            probe_ray(ri, XYZ(x, y, ray_z), down)
        if state["tris"] is None:
            state["tris"] = terrain_triangles(topo_items, target)
            log("Falling back to the terrain's OWN geometry "
                "(view-independent): {} triangle(s) read.".format(
                    len(state["tris"])))
        z = surface_z(state["tris"], x, y, lowest=lowest)
        if z is None:
            return None
        state["from_mesh"] += 1
        return XYZ(x, y, z)

    total_added, total_missed, total_rejected = 0, 0, 0
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
                    if corners_only:
                        pts.extend(curve_corners(c))
                    else:
                        pts.extend(sample_curve(c, spacing))
            pts = dedupe(pts)

            # the loop polygons serve the grid AND the inward-nudge
            # retry for boundary points the slab rejects
            polys = [loop_to_poly(l) for l in loops]
            if include_grid:
                pts = dedupe(pts + grid_points(floor, polys,
                                               gx_ft, gy_ft))

            try:
                editor = get_shape_editor(floor)
                draw = point_drawer(editor)
            except Exception as err:
                log("! floor {}: cannot be shape-edited (slope arrow "
                    "/ span direction / in a group?): {} - skipped"
                    .format(floor.Id, err))
                continue
            modify = vertex_modifier(editor)

            # start CLEAN: wipe previous shape edits so a re-run heals
            # a spiked floor instead of stacking new points on top
            try:
                if reset_shape(editor):
                    doc.Regenerate()
            except Exception:
                pass

            # 1) MOVE the slab's OWN vertices (sketch corners - outer
            #    boundary AND hole loops) onto the terrain. Only ever
            #    ADDING points left these behind at the level, which
            #    tore the surface down to a 'set at zero' with a
            #    doubled point beside every corner.
            corners_up, corners_left, missed = 0, 0, 0
            vert_xy = []
            if modify is not None:
                try:
                    verts = list(editor.SlabShapeVertices)
                except Exception:
                    verts = []
                for v in verts:
                    try:
                        vp = v.Position
                    except Exception:
                        continue
                    vert_xy.append((vp.X, vp.Y))
                    hit = ground_hit(vp.X, vp.Y)
                    if hit is None:
                        missed += 1
                    elif set_vertex_z(modify, v, hit.Z):
                        corners_up += 1
                    else:
                        corners_left += 1
            else:
                log("- this Revit's shape editor can't MOVE existing "
                    "vertices - the sketch corners keep their level.")

            # 2) the sampled points: an existing vertex is already
            #    handled, so a sample sitting ON one is dropped - THAT
            #    was the doubled point
            if vert_xy:
                keep = []
                for p in pts:
                    on_v = False
                    for (vx, vy) in vert_xy:
                        if ((p.X - vx) ** 2 + (p.Y - vy) ** 2
                                <= VERTEX_XY_TOL ** 2):
                            on_v = True
                            break
                    if not on_v:
                        keep.append(p)
                pts = keep
            if corners_only and modify is not None:
                pts = []    # the slab's vertices ARE the corners

            first_err = [None]
            zadj = [None]   # measured AddPoint Z datum: None = untested

            def try_draw(pt):
                want = pt.Z
                try:
                    if zadj[0]:
                        pt = XYZ(pt.X, pt.Y, pt.Z - zadj[0])
                    v = draw(pt)
                except Exception as ex:
                    if first_err[0] is None:
                        first_err[0] = "{}".format(ex)
                    return False
                if zadj[0] is None:
                    # calibrate on the FIRST added vertex: an AddPoint
                    # that measures Z from the slab instead of
                    # absolutely would land every point wrong
                    zadj[0] = 0.0
                    try:
                        if v is not None:
                            off = v.Position.Z - want
                            if abs(off) > VERTEX_Z_TOL:
                                zadj[0] = off
                                log("- AddPoint here measures Z from "
                                    "the SLAB, not absolutely - "
                                    "compensating by {:+.3f} m."
                                    .format(off * 0.3048))
                                if modify is not None:
                                    set_vertex_z(modify, v, want)
                    except Exception:
                        pass
                return True

            added, rejected, nudged = 0, 0, 0
            for p in pts:
                hit = ground_hit(p.X, p.Y)
                if hit is None:
                    missed += 1
                    continue
                if try_draw(hit):
                    added += 1
                    continue
                # the slab rejected the point (curved edges are
                # approximated by straight segments, so a point ON the
                # true arc can sit just outside) - retry ~50 mm inward
                # with a FRESH ray so the level stays on the terrain
                q = nudge_inward(hit, polys)
                h2 = None
                if q is not None:
                    h2 = ground_hit(q.X, q.Y)
                if h2 is not None and try_draw(h2):
                    added += 1
                    nudged += 1
                    continue
                rejected += 1

            total_added += added + corners_up
            total_missed += missed
            total_rejected += rejected
            log("**Floor {}** - {} corner(s) lifted, {} point(s) "
                "added{}{}{}{}".format(
                    floor.Id, corners_up, added,
                    " ({} nudged inward off a curved edge)".format(
                        nudged) if nudged else "",
                    ", {} no terrain hit".format(missed)
                    if missed else "",
                    ", {} rejected by the slab".format(rejected)
                    if rejected else "",
                    ", {} corner(s) would not move".format(corners_left)
                    if corners_left else ""))
            if rejected and first_err[0]:
                log("- the slab's FIRST rejection said: **{}**".format(
                    first_err[0]))

    if state["from_mesh"]:
        log("**{}** point(s) came from the terrain's own geometry - "
            "the view ray-cast missed them.".format(state["from_mesh"]))
    log.close()
    if total_added == 0 and total_rejected > 0:
        forms.alert("The terrain WAS found, but the slab REJECTED every "
                    "point ({}).\n\nThe report shows the slab's own "
                    "reason (first rejection). Usual causes: the floor "
                    "is in a GROUP, has a SLOPE ARROW or a sloped "
                    "sketch line, or its type blocks shape editing."
                    .format(total_rejected))
    elif total_added == 0:
        forms.alert('No terrain hits found - not by ray-casting in '
                    'view "{}", and not in the terrain geometry under '
                    'the floor(s) either.\n\nThe report window lists '
                    'what the rays DID hit and which terrain '
                    'candidates were read - check the floors actually '
                    'sit over the terrain in plan.'.format(view3d.Name))
    else:
        forms.alert("Done: {} point(s) added across {} floor(s), {} "
                    "ray(s) missed, {} rejected by the slab{}.".format(
                        total_added, len(floors), total_missed,
                        total_rejected,
                        " ({} from the terrain's own geometry)".format(
                            state["from_mesh"])
                        if state["from_mesh"] else ""))


main()
