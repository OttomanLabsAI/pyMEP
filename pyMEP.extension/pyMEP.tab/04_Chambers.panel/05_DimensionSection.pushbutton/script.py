# -*- coding: utf-8 -*-
"""Dimension Section - dimension the pipe / conduit / duct CENTRELINES in
the active SECTION view, and nothing else.

  * Every pipe, conduit and duct visible in the section (inside the
    chamber's footprint when a chamber family is in view) is located where
    it crosses the section plane.
  * The runs are grouped into COLUMNS (same position across the view) and
    ROWS (same height). One chained dimension goes ABOVE the bank through
    one centreline per column (the column spacing), and one chained
    dimension goes to the LEFT of the bank through one centreline per row
    (the row spacing). A single row or a single column gets no dimension
    in that direction.
  * The dimension type named in DIM_TYPE_NAME ('RHD_2.5') is used when the
    project has it, else the view's default.

Run it with a section view open and active - no selection needed.

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

doc = revit.doc
view = doc.ActiveView
out = script.get_output()

MM_PER_FOOT = 304.8

# --- Type name to use (edit here if your standards change) -----------------
DIM_TYPE_NAME = "RHD_2.5"

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
                "This tool dimensions the pipe / conduit centrelines visible "
                "in a chamber section.", exitscript=True)


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

warn_lines = []
if dim_type is None:
    warn_lines.append("Dimension type '{0}' not found - the dimensions use "
                      "the view's default type.".format(DIM_TYPE_NAME))

# The bank's extent in the section frame, for placing the two strings.
bank_top_u = max(_along_up(c) for _el, c in ducts)
bank_left_r = min(_along_right(c) for _el, c in ducts)


# ---------------------------------------------------------------------------
# 4. Build references + a dimension line ABOVE the bank
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

# A single column has no horizontal spacing to show; the vertical string
# below may still apply.
have_horizontal = refs.Size >= 2
dim_line = None

# Dimension line: parallel to RightDirection, ABOVE the topmost duct so the
# string sits clear of the circles (~600 mm up).
DIM_OFFSET_MM = 600.0
dim_offset_ft = DIM_OFFSET_MM / MM_PER_FOOT

if have_horizontal:
    # Start over the leftmost column, lifted to the bank top plus the offset.
    left_pt = ref_pts[0]
    lift = (bank_top_u - _along_up(left_pt)) + dim_offset_ft
    line_origin = XYZ(
        left_pt.X + up.X * lift,
        left_pt.Y + up.Y * lift,
        left_pt.Z + up.Z * lift,
    )
    # The line runs along RightDirection; long enough to span all columns.
    right_pt = ref_pts[-1]
    span = _along_right(right_pt) - _along_right(left_pt)
    line_end = XYZ(
        line_origin.X + right.X * (span + 1.0),
        line_origin.Y + right.Y * (span + 1.0),
        line_origin.Z + right.Z * (span + 1.0),
    )
    try:
        dim_line = Line.CreateBound(line_origin, line_end)
    except Exception:
        dim_line = None
        have_horizontal = False


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

    # Anchor at the lowest row rep, shifted left past the bank's leftmost
    # duct by the offset.
    low_row_pt = vref_pts[0]    # rows sorted ascending by 'up'
    high_row_pt = vref_pts[-1]
    shift = (_along_right(low_row_pt) - bank_left_r) + vdim_offset_ft
    vline_origin = XYZ(
        low_row_pt.X - right.X * shift,
        low_row_pt.Y - right.Y * shift,
        low_row_pt.Z - right.Z * shift,
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


if not have_horizontal and not have_vertical:
    forms.alert("Nothing to dimension.\n\n"
                "Found {0} duct(s) in {1} column(s) and {2} row(s), with {3} "
                "usable centreline reference(s). A string needs two columns "
                "(or two rows).".format(len(ducts), len(columns),
                                       len(rows_grp), refs.Size),
                exitscript=True)


# ---------------------------------------------------------------------------
# 5. Create the two centreline strings in one transaction
# ---------------------------------------------------------------------------
created_dim = False
created_vdim = False
errors = []
t = Transaction(doc, "pyMEP: Dimension section ({0} ducts)".format(len(ducts)))
t.Start()
try:
    # --- horizontal: column spacing, above the bank ---
    if have_horizontal and dim_line is not None:
        try:
            if dim_type is not None:
                dim = doc.Create.NewDimension(view, dim_line, refs, dim_type)
            else:
                dim = doc.Create.NewDimension(view, dim_line, refs)
            created_dim = dim is not None
        except Exception as ex:
            errors.append("Horizontal dimension failed: {0}".format(ex))

    # --- vertical: row spacing, left of the bank ---
    if have_vertical and vdim_line is not None:
        try:
            if dim_type is not None:
                vdim = doc.Create.NewDimension(view, vdim_line, vrefs, dim_type)
            else:
                vdim = doc.Create.NewDimension(view, vdim_line, vrefs)
            created_vdim = vdim is not None
        except Exception as ex:
            errors.append("Vertical dimension failed: {0}".format(ex))

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
out.print_md("**Column spacing (above the bank):** {0}".format(
    "created" if created_dim else
    ("NOT created" if have_horizontal else "skipped (single column)")))
out.print_md("**Row spacing (left of the bank):** {0}".format(
    "created" if created_vdim else
    ("NOT created" if have_vertical else "skipped (single row)")))
if no_ref or v_no_ref:
    out.print_md("- {0} column rep(s) and {1} row rep(s) gave no usable "
                 "centreline reference (skipped).".format(no_ref, v_no_ref))
for w in warn_lines:
    out.print_md("- " + w)
for e in errors[:20]:
    out.print_md("- " + e)

# Keep the output window open (matches the other Chambers buttons).
