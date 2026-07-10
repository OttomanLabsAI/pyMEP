# -*- coding: utf-8 -*-
"""Dimension Section - auto-populate dimensions and spot elevations for
everything visible in the active SECTION view.

What it does (built in layers, ducts first as the reliable foundation):

  1. DUCT SPACING: collects every pipe / conduit visible in the section,
     sorts them left-to-right along the section's RightDirection, builds a
     ReferenceArray of their centrelines, and creates ONE chained linear
     dimension through all of them (the 300/300/300 runs). Uses the
     dimension type named in DIM_TYPE_NAME ('RHD_2.5').

  2. CHAMBER EXTENTS (best-effort): if a single chamber family instance is
     visible, dimensions are added for its overall width and height and the
     edge gaps to the outermost ducts. Skipped quietly if the chamber's
     edges cannot be referenced.

  3. SPOT ELEVATIONS: a spot elevation (type SPOT_TYPE_NAME,
     'RHD_2.5_Project') is placed on each duct centreline.

Run it with a section view open and active - no selection needed.

Reuses the centreline-reference strategy proven in the Pipe End Elev button.

IronPython 2.7: pure ASCII, no f-strings, LF endings.
"""

__title__  = "Dimension\nSection"
__author__ = "Glent Group"

import sys

# Reload pymep_* lib modules so the script picks up the latest helpers.
for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (
    Transaction, ViewType, BuiltInCategory, XYZ, Options, Curve,
    GeometryInstance, ReferenceArray, Line, FilteredElementCollector,
)

from pyrevit import revit, forms, script

from pymep_config import get_chamber_dim_pairs

doc = revit.doc
view = doc.ActiveView
out = script.get_output()

MM_PER_FOOT = 304.8

# Chamber reference-plane dimension pairs (configurable in Settings).
chamber_dim_pairs = get_chamber_dim_pairs()

# --- Type names to use (edit here if your standards change) ---------------
DIM_TYPE_NAME = "RHD_2.5"
SPOT_TYPE_NAME = "RHD_2.5_Project"

# Categories treated as "ducts" (round MEP elements).
DUCT_CATS = (
    int(BuiltInCategory.OST_PipeCurves),
    int(BuiltInCategory.OST_Conduit),
    int(BuiltInCategory.OST_DuctCurves),
)


# ---------------------------------------------------------------------------
# 0. Pre-flight: must be a section view
# ---------------------------------------------------------------------------
if view is None or view.ViewType != ViewType.Section:
    forms.alert("Open a SECTION view and try again.\n\n"
                "This tool dimensions the ducts (and chamber) visible in a "
                "chamber section.", exitscript=True)


def _cat_int(elem):
    if elem is None or elem.Category is None:
        return None
    cid = elem.Category.Id
    try:
        return cid.Value
    except AttributeError:
        return cid.IntegerValue


# ---------------------------------------------------------------------------
# 1. Helpers
# ---------------------------------------------------------------------------
def _find_dim_type(name):
    # Find a DimensionType by name; return None if not present.
    from Autodesk.Revit.DB import DimensionType
    for dt in FilteredElementCollector(doc).OfClass(DimensionType):
        try:
            if dt.Name == name:
                return dt
        except Exception:
            continue
    return None


def _find_spot_type(name):
    # Find a SpotDimensionType by name; return None if not present.
    from Autodesk.Revit.DB import SpotDimensionType
    for st in FilteredElementCollector(doc).OfClass(SpotDimensionType):
        try:
            if st.Name == name:
                return st
        except Exception:
            continue
    return None


def _pipe_endpoints(elem):
    loc = getattr(elem, "Location", None)
    if loc is not None and hasattr(loc, "Curve") and loc.Curve is not None:
        c = loc.Curve
        return c.GetEndPoint(0), c.GetEndPoint(1)
    return None, None


def _centre_point(elem):
    # Midpoint of the element's centreline (Revit ft), or None.
    p0, p1 = _pipe_endpoints(elem)
    if p0 is None or p1 is None:
        return None
    return XYZ((p0.X + p1.X) * 0.5,
               (p0.Y + p1.Y) * 0.5,
               (p0.Z + p1.Z) * 0.5)


def _section_cross_point(elem, plane_origin, plane_normal):
    # Where the pipe centreline crosses the section's cut plane. This is the
    # point that shows as the duct circle in the section - the correct point
    # for both the chamber filter and dimension placement.
    #
    # Pipes run perpendicular to the section, so their 3D midpoint can be far
    # from the cut plane; the crossing point is what matters.
    p0, p1 = _pipe_endpoints(elem)
    if p0 is None or p1 is None:
        return None

    dx = p1.X - p0.X
    dy = p1.Y - p0.Y
    dz = p1.Z - p0.Z
    denom = dx * plane_normal.X + dy * plane_normal.Y + dz * plane_normal.Z
    if abs(denom) < 1.0e-9:
        # Pipe is parallel to the cut plane - it does not cross. Use midpoint.
        return XYZ((p0.X + p1.X) * 0.5,
                   (p0.Y + p1.Y) * 0.5,
                   (p0.Z + p1.Z) * 0.5)

    # Parametric t where the line p0 + t*d meets the plane.
    num = ((plane_origin.X - p0.X) * plane_normal.X +
           (plane_origin.Y - p0.Y) * plane_normal.Y +
           (plane_origin.Z - p0.Z) * plane_normal.Z)
    t = num / denom
    # Clamp to the segment so a pipe that ends before the plane still yields a
    # sensible point (its nearest end) rather than an extrapolated one.
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    return XYZ(p0.X + dx * t, p0.Y + dy * t, p0.Z + dz * t)


def _get_centreline_ref(elem):
    # Multi-strategy reference extraction (proven in Pipe End Elev).
    strategies = (
        (True, None),
        (True, view),
        (False, None),
        (False, view),
    )
    for include_non_vis, opts_view in strategies:
        opts = Options()
        opts.ComputeReferences = True
        opts.IncludeNonVisibleObjects = include_non_vis
        if opts_view is not None:
            try:
                opts.View = opts_view
            except Exception:
                pass
        try:
            geom = elem.get_Geometry(opts)
        except Exception:
            continue
        if geom is None:
            continue
        for obj in geom:
            if isinstance(obj, Curve):
                ref = getattr(obj, "Reference", None)
                if ref is not None:
                    return ref
        for obj in geom:
            if not isinstance(obj, GeometryInstance):
                continue
            try:
                inst_geom = obj.GetInstanceGeometry()
            except Exception:
                continue
            if inst_geom is None:
                continue
            for inner in inst_geom:
                if isinstance(inner, Curve):
                    ref = getattr(inner, "Reference", None)
                    if ref is not None:
                        return ref
        for obj in geom:
            if obj is None:
                continue
            ref = getattr(obj, "Reference", None)
            if ref is not None:
                return ref
    return None



# ---------------------------------------------------------------------------
# 2a. Find the chamber FIRST (the single largest non-duct family in the view).
#     Ducts are then filtered to those inside the chamber, so pipes from other
#     nearby chambers that happen to fall in the view's crop depth are ignored.
# ---------------------------------------------------------------------------
from Autodesk.Revit.DB import FamilyInstance
chamber_candidates = []
for el in FilteredElementCollector(doc, view.Id).OfClass(FamilyInstance):
    if _cat_int(el) in DUCT_CATS:
        continue
    try:
        bb = el.get_BoundingBox(view)
    except Exception:
        bb = None
    if bb is None:
        continue
    span = ((bb.Max.X - bb.Min.X) ** 2 +
            (bb.Max.Y - bb.Min.Y) ** 2 +
            (bb.Max.Z - bb.Min.Z) ** 2) ** 0.5
    chamber_candidates.append((el, span, bb))

chamber = None
if chamber_candidates:
    chamber_candidates.sort(key=lambda c: c[1], reverse=True)
    chamber = chamber_candidates[0][0]


def _inside_chamber_model_bb(pt, margin_ft):
    # Test a model-space point against the chamber's MODEL bounding box
    # (transform=None), expanded by a margin. Returns True if no chamber.
    if chamber is None:
        return True
    try:
        mbb = chamber.get_BoundingBox(None)   # model coords
    except Exception:
        mbb = None
    if mbb is None:
        return True
    return (mbb.Min.X - margin_ft <= pt.X <= mbb.Max.X + margin_ft and
            mbb.Min.Y - margin_ft <= pt.Y <= mbb.Max.Y + margin_ft and
            mbb.Min.Z - margin_ft <= pt.Z <= mbb.Max.Z + margin_ft)


# ---------------------------------------------------------------------------
# Section frame + cut plane. Defined here because duct filtering needs the
# plane to find where each pipe crosses the section.
# ---------------------------------------------------------------------------
right = view.RightDirection      # unit XYZ across the section, left->right
up = view.UpDirection            # unit XYZ up the section
view_dir = view.ViewDirection    # plane normal (into the screen)
plane_origin = view.Origin       # a point on the section cut plane


def _along_right(pt):
    return pt.X * right.X + pt.Y * right.Y + pt.Z * right.Z


def _along_up(pt):
    return pt.X * up.X + pt.Y * up.Y + pt.Z * up.Z


def _point_at(anchor, r_vec, u_vec, target_r, target_u):
    # Build a world point that has the given coordinates along Right and Up,
    # keeping the anchor's component along the view direction (depth). This
    # places dimension lines in the section plane at chosen r/u positions.
    cur_r = _along_right(anchor)
    cur_u = _along_up(anchor)
    dr = target_r - cur_r
    du = target_u - cur_u
    return XYZ(anchor.X + r_vec.X * dr + u_vec.X * du,
               anchor.Y + r_vec.Y * dr + u_vec.Y * du,
               anchor.Z + r_vec.Z * dr + u_vec.Z * du)


def _inst_xy_anchor(elem):
    # A fallback anchor point: the family instance's location point.
    loc = getattr(elem, "Location", None)
    if loc is not None and hasattr(loc, "Point") and loc.Point is not None:
        return loc.Point
    try:
        bb = elem.get_BoundingBox(None)
        return XYZ((bb.Min.X + bb.Max.X) * 0.5,
                   (bb.Min.Y + bb.Max.Y) * 0.5,
                   (bb.Min.Z + bb.Max.Z) * 0.5)
    except Exception:
        return XYZ(0.0, 0.0, 0.0)


def _get_ref_by_name(inst, plane_name):
    # Fetch a named reference (e.g. a named reference plane) from a family
    # instance. Returns the Reference or None. The plane must be named exactly
    # and have 'Is Reference' set to a real reference inside the family.
    if not plane_name:
        return None
    try:
        return inst.GetReferenceByName(plane_name)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 2b. Collect ducts, filtered to those within the chamber's footprint.
#     The test point is where each pipe CROSSES the section plane (the duct
#     circle), not the pipe's 3D midpoint - pipes run perpendicular to the
#     section and their midpoint can be far outside the chamber depth.
# ---------------------------------------------------------------------------
DUCT_MARGIN_MM = 300.0      # allow ducts just outside the chamber shell
duct_margin_ft = DUCT_MARGIN_MM / MM_PER_FOOT

collector = FilteredElementCollector(doc, view.Id)\
    .WhereElementIsNotElementType()
ducts = []
ducts_rejected_outside = 0
for el in collector:
    if _cat_int(el) in DUCT_CATS:
        c = _section_cross_point(el, plane_origin, view_dir)
        if c is None:
            continue
        if not _inside_chamber_model_bb(c, duct_margin_ft):
            ducts_rejected_outside += 1
            continue
        ducts.append((el, c))

if not ducts:
    forms.alert("No ducts found inside the chamber in this section.\n\n"
                "Found ducts in the view but none within the chamber bounds "
                "({0} rejected as outside). If there is no chamber family, "
                "every visible duct is used - check the view.".format(
                    ducts_rejected_outside),
                exitscript=True)


# Sort ducts left-to-right as seen in the section.
ducts.sort(key=lambda d: _along_right(d[1]))

# Group ducts into COLUMNS by their position along RightDirection. Two ducts
# stacked vertically (top row + bottom row) share a column and must NOT both
# feed the horizontal dimension - otherwise the chain dimensions the vertical
# (parallel) pairs too. Keep one representative per column (the topmost) for
# the horizontal spacing dimension.
COL_TOL_MM = 50.0                      # ducts within this are the same column
col_tol_ft = COL_TOL_MM / MM_PER_FOOT

columns = []        # list of dicts: {"r": along-right, "ducts": [(el, c), ...]}
for el, c in ducts:
    r_pos = _along_right(c)
    placed_in_col = False
    for col in columns:
        if abs(col["r"] - r_pos) <= col_tol_ft:
            col["ducts"].append((el, c))
            placed_in_col = True
            break
    if not placed_in_col:
        columns.append({"r": r_pos, "ducts": [(el, c)]})

columns.sort(key=lambda col: col["r"])

# One representative per column for the horizontal dimension: the TOPMOST duct
# (largest 'up' coordinate) so the dimension line ties to a consistent row.
col_reps = []
for col in columns:
    rep = max(col["ducts"], key=lambda d: _along_up(d[1]))
    col_reps.append(rep)

# Group ducts into ROWS by their position along UpDirection (mirror of the
# column grouping). Two ducts side-by-side in the same row share a row and must
# NOT both feed the vertical dimension. Keep one representative per row (the
# leftmost) for the vertical row-to-row spacing dimension.
ROW_TOL_MM = 50.0
row_tol_ft = ROW_TOL_MM / MM_PER_FOOT

rows_grp = []       # list of dicts: {"u": along-up, "ducts": [(el, c), ...]}
for el, c in ducts:
    u_pos = _along_up(c)
    placed_in_row = False
    for rw in rows_grp:
        if abs(rw["u"] - u_pos) <= row_tol_ft:
            rw["ducts"].append((el, c))
            placed_in_row = True
            break
    if not placed_in_row:
        rows_grp.append({"u": u_pos, "ducts": [(el, c)]})

rows_grp.sort(key=lambda rw: rw["u"])

# One representative per row: the LEFTMOST duct (smallest 'right' coordinate).
row_reps = []
for rw in rows_grp:
    rep = min(rw["ducts"], key=lambda d: _along_right(d[1]))
    row_reps.append(rep)


# ---------------------------------------------------------------------------
# 3. Find the dimension / spot types
# ---------------------------------------------------------------------------
dim_type = _find_dim_type(DIM_TYPE_NAME)
spot_type = _find_spot_type(SPOT_TYPE_NAME)

warn_lines = []
if dim_type is None:
    warn_lines.append("Dimension type '{0}' not found - the duct dimension "
                      "will use the view's default type.".format(DIM_TYPE_NAME))
if spot_type is None:
    warn_lines.append("Spot elevation type '{0}' not found - spot elevations "
                      "will be skipped.".format(SPOT_TYPE_NAME))


# ---------------------------------------------------------------------------
# 4. Build references + a dimension line below the ducts
#    Use ONE duct per column so the horizontal chain ignores the vertical
#    (parallel) pairs.
# ---------------------------------------------------------------------------
refs = ReferenceArray()
ref_pts = []        # parallel list of column-rep centre points with a reference
no_ref = 0
for el, c in col_reps:
    r = _get_centreline_ref(el)
    if r is None:
        no_ref += 1
        continue
    refs.Append(r)
    ref_pts.append(c)

if refs.Size < 2:
    forms.alert("Could not build enough duct references to dimension.\n\n"
                "Found {0} duct(s) but only {1} usable reference(s). "
                "Dimensions need at least two.".format(len(ducts), refs.Size),
                exitscript=True)

# Dimension line: parallel to RightDirection, offset BELOW the lowest duct so
# the dimension sits clear of the circles. Offset in feet (~600 mm).
DIM_OFFSET_MM = 600.0
dim_offset_ft = DIM_OFFSET_MM / MM_PER_FOOT

# A point on the dimension line: take the leftmost ref point, drop it down by
# the offset along the (negative) up direction.
left_pt = ref_pts[0]
line_origin = XYZ(
    left_pt.X - up.X * dim_offset_ft,
    left_pt.Y - up.Y * dim_offset_ft,
    left_pt.Z - up.Z * dim_offset_ft,
)
# The line runs along RightDirection; make it long enough to span all ducts.
right_pt = ref_pts[-1]
span = _along_right(right_pt) - _along_right(left_pt)
line_end = XYZ(
    line_origin.X + right.X * (span + 1.0),
    line_origin.Y + right.Y * (span + 1.0),
    line_origin.Z + right.Z * (span + 1.0),
)
try:
    dim_line = Line.CreateBound(line_origin, line_end)
except Exception as ex:
    forms.alert("Could not build the dimension line:\n{0}".format(ex),
                exitscript=True)


# ---------------------------------------------------------------------------
# 4b. Build references + a VERTICAL dimension line to the side of the ducts,
#     using ONE duct per row so the chain shows row-to-row spacing only.
# ---------------------------------------------------------------------------
vrefs = ReferenceArray()
vref_pts = []
v_no_ref = 0
for el, c in row_reps:
    r = _get_centreline_ref(el)
    if r is None:
        v_no_ref += 1
        continue
    vrefs.Append(r)
    vref_pts.append(c)

# Only meaningful with 2+ rows. If there is a single row, skip the vertical dim.
have_vertical = vrefs.Size >= 2
vdim_line = None
if have_vertical:
    # Vertical line runs along UpDirection, offset to the LEFT of the leftmost
    # duct so it sits in the margin clear of the circles (~900 mm).
    VDIM_OFFSET_MM = 900.0
    vdim_offset_ft = VDIM_OFFSET_MM / MM_PER_FOOT

    # Anchor at the lowest row rep, shifted left along -RightDirection.
    low_row_pt = vref_pts[0]    # rows sorted ascending by 'up'
    high_row_pt = vref_pts[-1]
    vline_origin = XYZ(
        low_row_pt.X - right.X * vdim_offset_ft,
        low_row_pt.Y - right.Y * vdim_offset_ft,
        low_row_pt.Z - right.Z * vdim_offset_ft,
    )
    vspan = _along_up(high_row_pt) - _along_up(low_row_pt)
    vline_end = XYZ(
        vline_origin.X + up.X * (vspan + 1.0),
        vline_origin.Y + up.Y * (vspan + 1.0),
        vline_origin.Z + up.Z * (vspan + 1.0),
    )
    try:
        vdim_line = Line.CreateBound(vline_origin, vline_end)
    except Exception:
        vdim_line = None
        have_vertical = False


# ---------------------------------------------------------------------------
# 5. Create dimension + spot elevations in one transaction
# ---------------------------------------------------------------------------
created_dim = False
created_vdim = False
chamber_note = ""
chamber_results = []     # list of (label, ok_bool, message) per pair
spots_placed = 0
spot_errors = []
t = Transaction(doc, "pyMEP: Dimension section ({0} ducts)".format(len(ducts)))
t.Start()
try:
    # --- Chained duct dimension (horizontal: column spacing) ---
    try:
        if dim_type is not None:
            dim = doc.Create.NewDimension(view, dim_line, refs, dim_type)
        else:
            dim = doc.Create.NewDimension(view, dim_line, refs)
        created_dim = dim is not None
    except Exception as ex:
        spot_errors.append("Horizontal dimension failed: {0}".format(ex))

    # --- Chained duct dimension (vertical: row-to-row spacing) ---
    if have_vertical and vdim_line is not None:
        try:
            if dim_type is not None:
                vdim = doc.Create.NewDimension(view, vdim_line, vrefs, dim_type)
            else:
                vdim = doc.Create.NewDimension(view, vdim_line, vrefs)
            created_vdim = vdim is not None
        except Exception as ex:
            spot_errors.append("Vertical dimension failed: {0}".format(ex))

    # --- Chamber dimensions from NAMED REFERENCE PLANES (live dims) ---
    # For each configured pair, fetch the two named reference planes from the
    # chamber family instance and dimension between them. This is deterministic
    # across every box of the family - no face/geometry guessing.
    if chamber is not None:
        # Chamber extent in the section frame, to position the dimension lines.
        cmin_r = cmax_r = cmin_u = cmax_u = None
        try:
            mbb = chamber.get_BoundingBox(None)
            corners = [
                XYZ(mbb.Min.X, mbb.Min.Y, mbb.Min.Z),
                XYZ(mbb.Max.X, mbb.Min.Y, mbb.Min.Z),
                XYZ(mbb.Min.X, mbb.Max.Y, mbb.Min.Z),
                XYZ(mbb.Max.X, mbb.Max.Y, mbb.Min.Z),
                XYZ(mbb.Min.X, mbb.Min.Y, mbb.Max.Z),
                XYZ(mbb.Max.X, mbb.Min.Y, mbb.Max.Z),
                XYZ(mbb.Min.X, mbb.Max.Y, mbb.Max.Z),
                XYZ(mbb.Max.X, mbb.Max.Y, mbb.Max.Z),
            ]
            for cp in corners:
                pr = _along_right(cp)
                pu = _along_up(cp)
                cmin_r = pr if cmin_r is None else min(cmin_r, pr)
                cmax_r = pr if cmax_r is None else max(cmax_r, pr)
                cmin_u = pu if cmin_u is None else min(cmin_u, pu)
                cmax_u = pu if cmax_u is None else max(cmax_u, pu)
        except Exception:
            pass

        anchor = ref_pts[0] if ref_pts else _inst_xy_anchor(chamber)
        base_r = cmin_r if cmin_r is not None else _along_right(anchor)
        base_u = cmin_u if cmin_u is not None else _along_up(anchor)
        ext_r = (cmax_r - cmin_r) if (cmax_r is not None) else 10.0
        ext_u = (cmax_u - cmin_u) if (cmax_u is not None) else 10.0
        off_ft = 900.0 / MM_PER_FOOT

        # Stagger successive width dims (and height dims) so they don't overlap.
        width_rank = 0
        height_rank = 0

        for pair in chamber_dim_pairs:
            ra = _get_ref_by_name(chamber, pair["plane_a"])
            rb = _get_ref_by_name(chamber, pair["plane_b"])
            if ra is None or rb is None:
                missing = []
                if ra is None:
                    missing.append(pair["plane_a"])
                if rb is None:
                    missing.append(pair["plane_b"])
                chamber_results.append(
                    (pair["label"], False,
                     "plane(s) not found: {0}".format(", ".join(missing))))
                continue

            arr = ReferenceArray()
            arr.Append(ra)
            arr.Append(rb)

            try:
                if pair["axis"] == "width":
                    # Horizontal dimension line, below the chamber.
                    drop = base_u - off_ft * (1 + width_rank)
                    width_rank += 1
                    o = _point_at(anchor, right, up, base_r - 1.0, drop)
                    e = XYZ(o.X + right.X * (ext_r + 2.0),
                            o.Y + right.Y * (ext_r + 2.0),
                            o.Z + right.Z * (ext_r + 2.0))
                else:
                    # Vertical dimension line, left of the chamber.
                    leftr = base_r - off_ft * (1 + height_rank)
                    height_rank += 1
                    o = _point_at(anchor, right, up, leftr, base_u - 1.0)
                    e = XYZ(o.X + up.X * (ext_u + 2.0),
                            o.Y + up.Y * (ext_u + 2.0),
                            o.Z + up.Z * (ext_u + 2.0))

                dline = Line.CreateBound(o, e)
                if dim_type is not None:
                    d = doc.Create.NewDimension(view, dline, arr, dim_type)
                else:
                    d = doc.Create.NewDimension(view, dline, arr)
                ok = d is not None
                chamber_results.append((pair["label"], ok,
                                        "" if ok else "NewDimension returned null"))
            except Exception as ex:
                chamber_results.append((pair["label"], False, str(ex)))

    # --- Spot elevations on each duct centreline ---
    if spot_type is not None:
        for el, c in ducts:
            ref = _get_centreline_ref(el)
            if ref is None:
                continue
            # Text position: offset to the LEFT of the duct so labels sit in
            # the margin (matching the screenshots' left-side tags).
            text_off_mm = 1500.0
            text_off_ft = text_off_mm / MM_PER_FOOT
            text_pos = XYZ(
                c.X - right.X * text_off_ft,
                c.Y - right.Y * text_off_ft,
                c.Z - right.Z * text_off_ft,
            )
            try:
                spot = doc.Create.NewSpotElevation(
                    view, ref, c, text_pos, text_pos, c, True)
                if spot is not None:
                    try:
                        spot.ChangeTypeId(spot_type.Id)
                    except Exception:
                        pass
                    spots_placed += 1
            except Exception as ex:
                spot_errors.append("Spot on {0}: {1}".format(el.Id, ex))

    t.Commit()
except Exception as ex:
    t.RollBack()
    forms.alert("Failed, no changes made:\n\n{0}".format(ex), exitscript=True)


# ---------------------------------------------------------------------------
# 6. Report
# ---------------------------------------------------------------------------
out.print_md("### Dimension section")
out.print_md("**Ducts in chamber:** {0}  |  **{1} column(s) x {2} row(s)**".format(
    len(ducts), len(columns), len(rows_grp)))
if ducts_rejected_outside:
    out.print_md("- {0} duct(s) outside the chamber bounds were ignored "
                 "(other chambers / nearby runs).".format(
                     ducts_rejected_outside))
out.print_md("**Chamber found:** {0}".format(
    "yes" if chamber is not None else "NO - all visible ducts used"))
out.print_md("**Duct dimension - horizontal (column spacing):** {0}".format(
    "created" if created_dim else "NOT created"))
out.print_md("**Duct dimension - vertical (row spacing):** {0}".format(
    "created" if created_vdim else
    ("not created" if have_vertical else "skipped (single row)")))
out.print_md("**Chamber dimensions (from named reference planes):**")
if chamber is None:
    out.print_md("- No chamber family instance found in the view.")
elif not chamber_results:
    out.print_md("- No dimension pairs configured. Set them in "
                 "Settings > Section Dims.")
else:
    for label, ok, msg in chamber_results:
        if ok:
            out.print_md("- {0}: created".format(label))
        else:
            out.print_md("- {0}: NOT created - {1}".format(label, msg))
out.print_md("**Spot elevations placed:** {0}".format(spots_placed))
if no_ref:
    out.print_md("- {0} column rep(s) gave no usable reference (skipped).".format(
        no_ref))
for w in warn_lines:
    out.print_md("- " + w)
for e in spot_errors[:20]:
    out.print_md("- " + e)

# Keep the output window open (matches the other Chambers buttons).
