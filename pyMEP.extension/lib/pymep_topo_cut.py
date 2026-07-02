# -*- coding: utf-8 -*-
"""Cut a Toposolid upward using the bottom outlines of selected MEP elements.

Entry point:
    cut_toposolid_from_elements(doc, element_ids, toposolid, options, log=None)
        -> CutResult

Approach (deliberately chosen for reliability against Revit's flaky native
Toposolid cutting - see module notes at the bottom):

  1. For each selected element, collect its solid geometry and find the
     lowest point (min Z over all solid vertices).
  2. Project the solid straight down onto a horizontal plane at that min Z
     using ExtrusionAnalyzer - this returns the *true bottom outline* (the
     plan silhouette / shadow boundary) as a Face, for any cross-section
     (round conduit, sloped runs, boxy equipment) without us having to find
     a flat bottom face that may not exist.
  3. Build ONE in-memory generic-model void family whose void extrusion is
     that outline, extruded from the element's min Z up to just above the
     top of the Toposolid (so the cut fully clears the solid). The family's
     "Cut with Voids When Loaded" flag is set so the void is *unattached*
     and therefore usable by InstanceVoidCutUtils.
  4. Load the family, place an instance at the origin (the void carries its
     own world coordinates), and call
        InstanceVoidCutUtils.AddInstanceVoidCut(doc, toposolid, instance)
     gating on InstanceVoidCutUtils.CanBeCutWithVoid(toposolid) first.

One void family *type* is generated per element (each has a unique outline
and height), all under a single family document that is reloaded per type.
The instances are left in the model joined to the Toposolid as the cutters -
deleting them would remove the cut, so they are kept (and tagged via a
shared comment so a future "uncut" button can find and remove them).

No 45 deg batter here - the cut is a straight vertical prism. The slope is a
later step.
"""

import os
import tempfile

import clr
clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    XYZ, Plane, Transaction, Options, ViewDetailLevel,
    Solid, GeometryInstance, ExtrusionAnalyzer,
    CurveLoop, BuiltInParameter,
    FilteredElementCollector, Family,
)

try:
    # StructuralType lives in DB.Structure
    from Autodesk.Revit.DB.Structure import StructuralType
except Exception:
    StructuralType = None

from pymep_revit import mm2ft, ft2mm, safe_name


# Comment stamped on every cutter instance so they can be found / removed later.
CUTTER_MARK = "pyMEP_TopoCut"


# ---------------------------------------------------------------------------
# Small result container
# ---------------------------------------------------------------------------
class CutResult(object):
    def __init__(self):
        self.cut_count = 0          # elements that successfully cut the topo
        self.no_geometry = []       # element ids with no usable solid
        self.silhouette_failed = [] # element ids where ExtrusionAnalyzer failed
        self.cut_failed = []        # element ids where AddInstanceVoidCut threw
        self.family_failed = []     # element ids where family build/load failed
        self.cutter_ids = []        # ElementIds of placed void cutter instances

    def total_attempted(self):
        return (self.cut_count + len(self.no_geometry) +
                len(self.silhouette_failed) + len(self.cut_failed) +
                len(self.family_failed))


def _say(log, msg):
    if log is not None:
        log(msg)


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def _iter_solids(geom_elem):
    """Yield every non-empty Solid in a GeometryElement, recursing into
    GeometryInstances (so family geometry like equipment is included)."""
    if geom_elem is None:
        return
    for g in geom_elem:
        if isinstance(g, Solid):
            if g.Volume > 1e-9 and g.Faces.Size > 0:
                yield g
        elif isinstance(g, GeometryInstance):
            inst_geom = g.GetInstanceGeometry()
            for s in _iter_solids(inst_geom):
                yield s


def _element_solids(elem):
    """All solids for an element at fine detail, in model coordinates."""
    opts = Options()
    opts.ComputeReferences = False
    opts.IncludeNonVisibleObjects = False
    opts.DetailLevel = ViewDetailLevel.Fine
    ge = elem.get_Geometry(opts)
    return list(_iter_solids(ge))


def _solids_min_z_and_top(solids):
    """Return (min_z, max_z) in feet across all solid vertices, or (None, None)."""
    lo = None
    hi = None
    for s in solids:
        bb = s.GetBoundingBox()  # solid-local box; transform corners
        # GetBoundingBox returns a box in the solid's own coordinate space.
        # For Revit solids from get_Geometry this space is the model space,
        # but the Transform may be non-identity, so apply it.
        tf = bb.Transform
        for ix in (bb.Min.X, bb.Max.X):
            for iy in (bb.Min.Y, bb.Max.Y):
                for iz in (bb.Min.Z, bb.Max.Z):
                    p = tf.OfPoint(XYZ(ix, iy, iz))
                    z = p.Z
                    lo = z if lo is None or z < lo else lo
                    hi = z if hi is None or z > hi else hi
    return lo, hi


def _bottom_outline_loops(solids, z_plane_ft):
    """Project the union of solids straight down onto a horizontal plane at
    z_plane_ft and return a list of CurveLoop describing the outer bottom
    outline. Uses ExtrusionAnalyzer on each solid; loops are returned in the
    analyzer's plane (which we set at z_plane_ft) so they already sit at the
    correct elevation.

    Returns [] if no analyzer succeeds.
    """
    origin = XYZ(0.0, 0.0, z_plane_ft)
    # Plane through origin with normal +Z; in-plane axes X and Y.
    plane = Plane.CreateByNormalAndOrigin(XYZ.BasisZ, origin)

    loops = []
    for s in solids:
        try:
            analyzer = ExtrusionAnalyzer.Create(s, plane, XYZ.BasisZ)
        except Exception:
            continue
        try:
            base_face = analyzer.GetExtrusionBase()
        except Exception:
            continue
        if base_face is None:
            continue
        # The face's edge loops are the projected outline. Pull each loop's
        # curves into a CurveLoop. The face lies in our plane (z = z_plane_ft).
        try:
            face_loops = base_face.GetEdgesAsCurveLoops()
        except Exception:
            face_loops = None

        if face_loops:
            for cl in face_loops:
                loops.append(cl)
        else:
            # Fallback: rebuild from EdgeLoops if GetEdgesAsCurveLoops missing.
            for edge_array in base_face.EdgeLoops:
                curves = [e.AsCurve() for e in edge_array]
                cl = _curves_to_loop(curves)
                if cl is not None:
                    loops.append(cl)
    return loops


def _curves_to_loop(curves):
    """Build a CurveLoop from an unordered list of contiguous curves."""
    try:
        cl = CurveLoop()
        for c in curves:
            cl.Append(c)
        return cl
    except Exception:
        # Try reversing / reordering minimally - Revit edge arrays are
        # usually already contiguous, so a plain append normally works.
        return None


def _outer_loop_only(loops):
    """Given projected loops (outer boundary + any holes), keep the loops that
    form the outer boundary. For a bottom-cut we want a *solid* prism, so we
    drop interior holes: return the single largest-area loop, plus any other
    loops that are disjoint outer boundaries (multiple separate solids).

    Heuristic: compute each loop's signed plan area; treat loops with the
    largest absolute areas as outers. To stay simple and robust we return all
    loops whose area is positive after normalising orientation, which for the
    silhouette of a single element is the outer boundary. Holes (negative
    orientation) are discarded so the cut is a full prism.
    """
    keep = []
    for cl in loops:
        a = _loop_plan_area(cl)
        if a > 0:
            keep.append((a, cl))
    if not keep:
        # All came back negative (orientation); flip the decision and keep
        # the largest by absolute area.
        alled = [(abs(_loop_plan_area(cl)), cl) for cl in loops]
        alled.sort(key=lambda t: t[0], reverse=True)
        return [alled[0][1]] if alled else []
    keep.sort(key=lambda t: t[0], reverse=True)
    return [cl for _a, cl in keep]


def _loop_plan_area(cl):
    """Signed area of a CurveLoop projected to XY (shoelace on tessellation)."""
    pts = []
    for c in cl:
        for p in c.Tessellate():
            pts.append(p)
    if len(pts) < 3:
        return 0.0
    area = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i].X, pts[i].Y
        x2, y2 = pts[(i + 1) % n].X, pts[(i + 1) % n].Y
        area += (x1 * y2 - x2 * y1)
    return area * 0.5


# ---------------------------------------------------------------------------
# Void family generation
# ---------------------------------------------------------------------------
def _void_family_template_path(app):
    """Locate a Generic Model family template (.rft). Tries the app's family
    template path for common localised names; raises if none found."""
    base = ""
    try:
        base = app.FamilyTemplatePath or ""
    except Exception:
        base = ""
    candidates = [
        "Metric Generic Model.rft",
        "Generic Model.rft",
        "Metric Generic Model face based.rft",
    ]
    search_dirs = []
    if base:
        search_dirs.append(base)
        # English/Metric library layouts often nest templates one level down.
        search_dirs.append(os.path.join(base, "English"))
        search_dirs.append(os.path.join(base, "English_I"))
        search_dirs.append(os.path.join(base, "Metric"))
    for d in search_dirs:
        for name in candidates:
            p = os.path.join(d, name)
            if os.path.isfile(p):
                return p
    # Last resort: walk the template dir for the first Generic Model template.
    if base and os.path.isdir(base):
        for root, _dirs, files in os.walk(base):
            for f in files:
                fl = f.lower()
                if fl.endswith(".rft") and "generic model" in fl:
                    return os.path.join(root, f)
    raise IOError(
        "Could not find a Generic Model family template (.rft). Set the Revit "
        "family template path in Options, or check your library install.")


def _flatten_curve_to_z(curve, z_ft):
    """Return a copy of `curve` with every point moved to z = z_ft, so the
    profile lies exactly on the horizontal sketch plane. Lines and arcs are
    rebuilt analytically; anything else is approximated by a polyline through
    its tessellation. Returns None if it cannot be rebuilt.
    """
    from Autodesk.Revit.DB import Line, Arc

    def flat(p):
        return XYZ(p.X, p.Y, z_ft)

    try:
        if isinstance(curve, Line):
            p0 = flat(curve.GetEndPoint(0))
            p1 = flat(curve.GetEndPoint(1))
            if p0.DistanceTo(p1) < 1e-9:
                return None
            return Line.CreateBound(p0, p1)

        if isinstance(curve, Arc):
            p0 = flat(curve.GetEndPoint(0))
            p1 = flat(curve.GetEndPoint(1))
            mid = flat(curve.Evaluate(0.5, True))
            try:
                return Arc.Create(p0, p1, mid)
            except Exception:
                # Degenerate arc after flattening - fall back to a chord.
                if p0.DistanceTo(p1) < 1e-9:
                    return None
                return Line.CreateBound(p0, p1)

        # Generic curve: approximate with the first/last tessellation chord.
        pts = list(curve.Tessellate())
        if len(pts) >= 2:
            p0 = flat(pts[0])
            p1 = flat(pts[-1])
            if p0.DistanceTo(p1) < 1e-9:
                return None
            return Line.CreateBound(p0, p1)
    except Exception:
        return None
    return None


def _build_void_family_doc(app, template_path, loops, z_bottom_ft, z_top_ft):
    """Create a family document containing a single void extrusion of `loops`
    from z_bottom_ft to z_top_ft, with 'Cut with Voids When Loaded' = yes.
    Returns the open family Document (caller loads then closes it).
    """
    from Autodesk.Revit.DB import (
        CurveArray, CurveArrArray, SketchPlane, BuiltInParameter as BIP,
    )

    fdoc = app.NewFamilyDocument(template_path)

    t = Transaction(fdoc, "Build void extrusion")
    t.Start()
    try:
        # Sketch plane at z_bottom (the family create extrusion takes a plane
        # and a positive extrusion depth measured along the plane normal).
        origin = XYZ(0.0, 0.0, z_bottom_ft)
        plane = Plane.CreateByNormalAndOrigin(XYZ.BasisZ, origin)
        sp = SketchPlane.Create(fdoc, plane)

        # Convert CurveLoops -> CurveArrArray expected by NewExtrusion.
        # NewExtrusion requires every profile curve to lie *on* the sketch
        # plane. The analyzer base face is already at z_bottom_ft, but float
        # noise can put a vertex a hair off-plane and make NewExtrusion throw,
        # so each curve is flattened to exactly z_bottom_ft first.
        arr_arr = CurveArrArray()
        for cl in loops:
            ca = CurveArray()
            for c in cl:
                fc = _flatten_curve_to_z(c, z_bottom_ft)
                if fc is not None:
                    ca.Append(fc)
            if ca.Size > 0:
                arr_arr.Append(ca)

        depth = z_top_ft - z_bottom_ft
        if depth <= 0:
            raise ValueError("Void extrusion depth must be positive.")

        # is_solid = False -> a void form.
        ext = fdoc.FamilyCreate.NewExtrusion(False, arr_arr, sp, depth)

        # Make the void unattached & cutting: set the family's
        # "Cut with Voids When Loaded" flag. This is a *family parameter*,
        # so it must be set through the FamilyManager, not via
        # OwnerFamily.get_Parameter(...).Set(...) (which returns null).
        # The family has no solid of its own, so the void cuts nothing
        # inside the family and therefore stays "unattached" - exactly what
        # InstanceVoidCutUtils.AddInstanceVoidCut requires.
        try:
            fm = fdoc.FamilyManager
            fp = fm.get_Parameter(BIP.FAMILY_ALLOW_CUT_WITH_VOIDS)
            if fp is not None:
                fm.Set(fp, 1)
        except Exception:
            pass

        t.Commit()
    except Exception:
        t.RollBack()
        try:
            fdoc.Close(False)
        except Exception:
            pass
        raise

    return fdoc


def _load_family_doc(doc, fdoc, family_name):
    """Save the family doc to a temp .rfa, load it into `doc`, return the
    loaded Family element. The temp file is removed afterwards."""
    from Autodesk.Revit.DB import SaveAsOptions, IFamilyLoadOptions  # noqa

    tmp_dir = tempfile.mkdtemp(prefix="pymep_topocut_")
    rfa_path = os.path.join(tmp_dir, family_name + ".rfa")

    sao = SaveAsOptions()
    sao.OverwriteExistingFile = True
    fdoc.SaveAs(rfa_path, sao)
    fdoc.Close(False)

    loaded_family = clr.Reference[Family]()
    ok = doc.LoadFamily(rfa_path, loaded_family)
    fam = loaded_family.Value if ok else None

    try:
        os.remove(rfa_path)
        os.rmdir(tmp_dir)
    except Exception:
        pass

    if not ok or fam is None:
        # LoadFamily returns False if a family of the same name already
        # exists; fetch it by name in that case.
        fam = None
        for f in FilteredElementCollector(doc).OfClass(Family):
            if safe_name(f) == family_name:
                fam = f
                break
    return fam


def _first_symbol(doc, family):
    """Return the (activated) first FamilySymbol of a loaded family."""
    sym_ids = list(family.GetFamilySymbolIds())
    if not sym_ids:
        return None
    sym = doc.GetElement(sym_ids[0])
    if sym is None:
        return None
    if not sym.IsActive:
        sym.Activate()
        doc.Regenerate()
    return sym


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def cut_toposolid_from_elements(doc, element_ids, toposolid,
                                top_clearance_mm=50.0, log=None):
    """Cut `toposolid` upward with the bottom outline of each element.

    Args:
        doc:           active Document.
        element_ids:   iterable of ElementId to cut with.
        toposolid:     the Toposolid Element to cut.
        top_clearance_mm: extra height (mm) the void rises ABOVE the top of
                       the Toposolid, so the cut fully breaks the surface.
        log:           optional Logger.

    Returns: CutResult.
    """
    app = doc.Application
    result = CutResult()

    if not InstanceVoidCutUtils_can_cut(toposolid):
        raise ValueError(
            "The chosen Toposolid reports it cannot be cut by a void "
            "(InstanceVoidCutUtils.CanBeCutWithVoid == False). Check that the "
            "element really is a Toposolid and not a legacy Toposurface, and "
            "that it is not in a linked model.")

    # Top of the toposolid bounding box (model Z, feet) - all voids rise to
    # this plus clearance so the cut clears the surface everywhere.
    topo_bb = toposolid.get_BoundingBox(None)
    if topo_bb is None:
        raise ValueError("Could not read the Toposolid bounding box.")
    topo_top_ft = topo_bb.Max.Z
    clearance_ft = mm2ft(top_clearance_mm)
    z_top_ft = topo_top_ft + clearance_ft

    template_path = _void_family_template_path(app)
    _say(log, "Void template: `{}`".format(os.path.basename(template_path)))
    _say(log, "Cut rises to Z = top of topo + {:.0f} mm.".format(top_clearance_mm))

    idx = 0
    for eid in element_ids:
        idx += 1
        elem = doc.GetElement(eid)
        if elem is None:
            continue

        solids = _element_solids(elem)
        if not solids:
            result.no_geometry.append(eid)
            continue

        min_z_ft, _max_z_ft = _solids_min_z_and_top(solids)
        if min_z_ft is None:
            result.no_geometry.append(eid)
            continue

        if min_z_ft >= z_top_ft:
            # Element sits above the topo top - nothing meaningful to cut.
            result.silhouette_failed.append(eid)
            continue

        # Bottom outline at the element's lowest plane.
        loops = _bottom_outline_loops(solids, min_z_ft)
        loops = _outer_loop_only(loops)
        if not loops:
            result.silhouette_failed.append(eid)
            continue

        family_name = "pyMEP_TopoCutVoid_{}".format(eid.IntegerValue)

        # Build + load the void family for this element.
        try:
            fdoc = _build_void_family_doc(
                app, template_path, loops, min_z_ft, z_top_ft)
            fam = _load_family_doc(doc, fdoc, family_name)
            if fam is None:
                result.family_failed.append(eid)
                continue
            sym = _first_symbol(doc, fam)
            if sym is None:
                result.family_failed.append(eid)
                continue
        except Exception as ex:
            _say(log, "  - element {}: family build failed ({})".format(
                eid.IntegerValue, ex))
            result.family_failed.append(eid)
            continue

        # Place the instance at the origin (void carries world coords) and cut.
        t = Transaction(doc, "Cut Toposolid with element {}".format(
            eid.IntegerValue))
        t.Start()
        try:
            # Generic-model void: the 2-arg (location, symbol) overload is the
            # unambiguous one for a free-floating instance. Fall back to the
            # (location, symbol, StructuralType) overload if needed.
            try:
                inst = doc.Create.NewFamilyInstance(XYZ(0, 0, 0), sym)
            except Exception:
                ns = StructuralType.NonStructural if StructuralType else 0
                inst = doc.Create.NewFamilyInstance(XYZ(0, 0, 0), sym, ns)
            doc.Regenerate()

            # Stamp a comment so cutters can be found / removed later.
            try:
                cp = inst.get_Parameter(
                    BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
                if cp is not None and not cp.IsReadOnly:
                    cp.Set(CUTTER_MARK)
            except Exception:
                pass

            InstanceVoidCutUtils_add(doc, toposolid, inst)
            t.Commit()
            result.cut_count += 1
            result.cutter_ids.append(inst.Id)
        except Exception as ex:
            t.RollBack()
            _say(log, "  - element {}: cut failed ({})".format(
                eid.IntegerValue, ex))
            result.cut_failed.append(eid)

    return result


# ---------------------------------------------------------------------------
# Thin wrappers around InstanceVoidCutUtils so the import sits in one place
# and the module imports cleanly even if the name moves between API versions.
# ---------------------------------------------------------------------------
def InstanceVoidCutUtils_can_cut(element):
    from Autodesk.Revit.DB import InstanceVoidCutUtils
    return InstanceVoidCutUtils.CanBeCutWithVoid(element)


def InstanceVoidCutUtils_add(doc, element, cutting_instance):
    from Autodesk.Revit.DB import InstanceVoidCutUtils
    InstanceVoidCutUtils.AddInstanceVoidCut(doc, element, cutting_instance)


# ---------------------------------------------------------------------------
# Module notes
# ---------------------------------------------------------------------------
# Why generate void families instead of native Toposolid void editing?
#
#   * Revit's interactive "Cut Geometry" / model-in-place void workflow on
#     Toposolids is notoriously unreliable (Autodesk + RevitForum threads
#     report "nothing happens", "cannot delete joined elements", phase and
#     join conflicts). There is no public API to add a sketched void to a
#     Toposolid's own definition.
#   * InstanceVoidCutUtils.AddInstanceVoidCut is the documented, stable API
#     for cutting a host with an unattached family void, and Toposolid is a
#     supported host (CanBeCutWithVoid gates this). One cutter family
#     instance per element keeps each cut independently removable.
#
# Coordinate handling: ExtrusionAnalyzer base loops and the void extrusion
# are built directly in model coordinates (feet) at the element's true min Z,
# so the placed family instance needs no transform - it is dropped at the
# origin and its geometry already lands in the right place.
