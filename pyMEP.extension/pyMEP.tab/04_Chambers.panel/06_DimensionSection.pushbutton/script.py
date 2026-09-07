# -*- coding: utf-8 -*-
"""Dimension Section - dimension the pipe / conduit / duct CENTRELINES in
chamber sections, and nothing else.

WHERE it runs:
  * From a SECTION view: that one view is dimensioned. The dialog only
    asks for the dimension type.
  * From anywhere else (a sheet, a plan): the dialog offers a tick list of
    sections - the ones placed on the open sheet when you start from a
    sheet, or every section in the project - plus the dimension type.

WHAT it does in each section - the STRINGS in a list of RULES you build in
the dialog (+ opens the editor, Add to list saves; Edit / Remove; the list
is remembered, and can be saved under a name and picked from a dropdown):
  * REFERENCE PLANE STRINGS. A rule names the planes by axis - the chamber
    family's reference planes named x1, x2..., y1, y2... or z1, z2... -
    and by number: '1-5' (z1 to z5), '1-' (z1 up to the highest found),
    'all', or a list '2,3,5'; chain through each plane or one overall
    dimension first-to-last; 'dimension anyway' uses the planes that exist
    and that this view can take when some are missing; 'inside the
    outline' drops a plane that reaches beyond the chamber's visible box
    (read back from the string's own segments). z strings go to the right
    of the chamber, x and y strings below it, stacked when there are
    several.
  * WHERE the strings sit: 10 mm on paper (times the view scale) off the
    chamber's extent, each further string on a side 7 mm further out. The
    extent is taken from the chamber's OUTERMOST NAMED REFERENCE PLANES -
    x or y planes across the view, z planes up it - read back off a
    throwaway dimension through them, because a family's bounding box
    often reaches well beyond what is drawn. The box is the fallback when
    the view cannot dimension those planes.
  * PIPE / CONDUIT / DUCT CENTRELINE STRINGS. A rule picks the categories
    and the strings: every run of those categories visible in the section
    (inside the chamber's footprint when a chamber family is in view) is
    located where it crosses the section plane and grouped into COLUMNS
    (same position across the view) and ROWS (same height). The column
    string goes ABOVE the bank through one centreline per column, the row
    string to the LEFT of the bank through one centreline per row. A
    single column or a single row gets no string. Nothing is added unless
    such a rule is in the list.
  * The dimension type is picked in the dialog (remembered; the house
    'RHD_2.5' is offered first when the project has it).

IronPython 2.7: pure ASCII, no f-strings, LF endings.
"""

__title__  = "Dimension\nSection"
__author__ = "Glent Group"

import os
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
    FamilyInstance, View, ViewSheet, DimensionType, DimensionStyleType,
    Element, FamilyInstanceReferenceType,
)

from pyrevit import revit, forms, script

import pymep_chamber_sections as CS
from pymep_dim_rules_ui import RuleList
from pymep_config import load_settings, save_settings

doc = revit.doc
out = script.get_output()

XAML_PATH = os.path.join(os.path.dirname(os.path.abspath(CS.__file__)),
                         "pymep_dimension_section.xaml")
MM_PER_FOOT = 304.8

# Categories treated as "ducts" (round MEP elements).
DUCT_CATS = (
    int(BuiltInCategory.OST_PipeCurves),
    int(BuiltInCategory.OST_Conduit),
    int(BuiltInCategory.OST_DuctCurves),
)

DUCT_MARGIN_MM = 300.0      # allow ducts just outside the chamber shell
COL_TOL_MM = 50.0           # ducts within this are the same column
ROW_TOL_MM = 50.0           # ducts within this are the same row
# Strings sit CS.STRING_GAP_PAPER_MM (on paper, times the view scale) off
# the chamber's extent, further strings on a side CS.STRING_STEP_PAPER_MM
# apart - see CS.string_offset_mm.


def _cat_int(elem):
    if elem is None or elem.Category is None:
        return None
    cid = elem.Category.Id
    try:
        return cid.Value
    except AttributeError:
        return cid.IntegerValue


def _name(el):
    try:
        n = el.Name
        if n:
            return n
    except Exception:
        pass
    try:
        return Element.Name.GetValue(el) or "?"
    except Exception:
        return "?"


# ---------------------------------------------------------------------------
# 1. Geometry helpers (all take the section view they work in)
# ---------------------------------------------------------------------------
def _pipe_endpoints(elem):
    loc = getattr(elem, "Location", None)
    if loc is not None and hasattr(loc, "Curve") and loc.Curve is not None:
        c = loc.Curve
        return c.GetEndPoint(0), c.GetEndPoint(1)
    return None, None


def _section_cross_point(elem, plane_origin, plane_normal):
    # Where the pipe centreline crosses the section's cut plane - the point
    # that shows as the duct circle. Pipes run perpendicular to the section,
    # so their 3D midpoint can be far from the cut plane.
    p0, p1 = _pipe_endpoints(elem)
    if p0 is None or p1 is None:
        return None
    dx = p1.X - p0.X
    dy = p1.Y - p0.Y
    dz = p1.Z - p0.Z
    denom = dx * plane_normal.X + dy * plane_normal.Y + dz * plane_normal.Z
    if abs(denom) < 1.0e-9:
        # Parallel to the cut plane - it does not cross. Use the midpoint.
        return XYZ((p0.X + p1.X) * 0.5, (p0.Y + p1.Y) * 0.5,
                   (p0.Z + p1.Z) * 0.5)
    num = ((plane_origin.X - p0.X) * plane_normal.X +
           (plane_origin.Y - p0.Y) * plane_normal.Y +
           (plane_origin.Z - p0.Z) * plane_normal.Z)
    t = num / denom
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    return XYZ(p0.X + dx * t, p0.Y + dy * t, p0.Z + dz * t)


def _get_centreline_ref(elem, v):
    # Multi-strategy reference extraction (proven in Pipe End Elev).
    strategies = ((True, None), (True, v), (False, None), (False, v))
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


def _find_chamber(v):
    # The single largest non-duct family instance in the view, or None.
    best = None
    best_span = 0.0
    for el in FilteredElementCollector(doc, v.Id).OfClass(FamilyInstance):
        if _cat_int(el) in DUCT_CATS:
            continue
        try:
            bb = el.get_BoundingBox(v)
        except Exception:
            bb = None
        if bb is None:
            continue
        span = ((bb.Max.X - bb.Min.X) ** 2 + (bb.Max.Y - bb.Min.Y) ** 2 +
                (bb.Max.Z - bb.Min.Z) ** 2) ** 0.5
        if span > best_span:
            best, best_span = el, span
    return best


def _inside_model_bb(el, pt, margin_ft):
    # Point against the element's MODEL bounding box plus a margin. True
    # when there is no element / box to test against.
    if el is None:
        return True
    try:
        mbb = el.get_BoundingBox(None)
    except Exception:
        mbb = None
    if mbb is None:
        return True
    return (mbb.Min.X - margin_ft <= pt.X <= mbb.Max.X + margin_ft and
            mbb.Min.Y - margin_ft <= pt.Y <= mbb.Max.Y + margin_ft and
            mbb.Min.Z - margin_ft <= pt.Z <= mbb.Max.Z + margin_ft)


def _named_planes(inst, axis):
    # {number: (name, Reference)} of the family instance's reference planes
    # named <axis><number>. Named planes are reported by the instance as
    # strong or weak references.
    found = {}
    for kind in ("StrongReference", "WeakReference"):
        rt = getattr(FamilyInstanceReferenceType, kind, None)
        if rt is None:
            continue
        try:
            refs = inst.GetReferences(rt)
        except Exception:
            continue
        for r in refs:
            try:
                nm = inst.GetReferenceName(r)
            except Exception:
                nm = None
            n = CS.plane_number(nm, axis)
            if n is None or n in found:
                continue
            try:
                ref = inst.GetReferenceByName(nm)
            except Exception:
                ref = None
            if ref is not None:
                found[n] = (nm, ref)
    return found


def _visible_extent(v, el, along_right, along_up):
    # (min_r, max_r, min_u, max_u) of what the view shows of the element:
    # its view-specific bounding box when Revit gives one, else its model
    # box. None when neither can be read.
    try:
        bb = el.get_BoundingBox(v)
    except Exception:
        bb = None
    if bb is not None:
        try:
            tf = bb.Transform
            rs, us = [], []
            for x in (bb.Min.X, bb.Max.X):
                for y in (bb.Min.Y, bb.Max.Y):
                    for z in (bb.Min.Z, bb.Max.Z):
                        p = tf.OfPoint(XYZ(x, y, z))
                        rs.append(along_right(p))
                        us.append(along_up(p))
            return min(rs), max(rs), min(us), max(us)
        except Exception:
            pass
    return _bbox_frame(el, along_right, along_up)


def _bbox_frame(el, along_right, along_up):
    # (min_r, max_r, min_u, max_u) of an element's model box in the frame.
    try:
        bb = el.get_BoundingBox(None)
    except Exception:
        bb = None
    if bb is None:
        return None
    rs = []
    us = []
    for x in (bb.Min.X, bb.Max.X):
        for y in (bb.Min.Y, bb.Max.Y):
            for z in (bb.Min.Z, bb.Max.Z):
                p = XYZ(x, y, z)
                rs.append(along_right(p))
                us.append(along_up(p))
    return min(rs), max(rs), min(us), max(us)


def _frame_at(chamber, along_right, along_up, right, up):
    # A function (r, u) -> model XYZ on the section plane through the
    # chamber's centre, or None when the chamber has no box.
    try:
        cbb = chamber.get_BoundingBox(None)
        anchor = XYZ((cbb.Min.X + cbb.Max.X) * 0.5,
                     (cbb.Min.Y + cbb.Max.Y) * 0.5,
                     (cbb.Min.Z + cbb.Max.Z) * 0.5)
    except Exception:
        return None
    a_r = along_right(anchor)
    a_u = along_up(anchor)

    def at(r_val, u_val):
        dr = r_val - a_r
        du = u_val - a_u
        return XYZ(anchor.X + right.X * dr + up.X * du,
                   anchor.Y + right.Y * dr + up.Y * du,
                   anchor.Z + right.Z * dr + up.Z * du)
    return at


def _plane_extent(v, chamber, along_right, along_up, at, box):
    # (min_r, max_r, min_u, max_u, across_from_planes, up_from_planes):
    # the chamber's extent from its outermost named reference planes - x
    # or y planes across the view, z planes up it - each read back off a
    # throwaway dimension through them. A direction the view cannot
    # dimension keeps the box extent.
    min_r, max_r, min_u, max_u = box

    def probe(axes, line, along):
        for axis in axes:
            found = _named_planes(chamber, axis)
            if len(found) < 2:
                continue
            planes = [found[n] for n in sorted(found)]
            for cand in (planes, [planes[0], planes[-1]]):
                d = None
                try:
                    arr = ReferenceArray()
                    for _nm, ref in cand:
                        arr.Append(ref)
                    d = doc.Create.NewDimension(v, line, arr)
                except Exception:
                    d = None
                if d is None:
                    continue
                pos = _ref_positions(d, along)
                try:
                    doc.Delete(d.Id)
                except Exception:
                    pass
                if len(pos) >= 2:
                    return min(pos), max(pos)
        return None

    try:
        h_line = Line.CreateBound(at(min_r - 1.0, min_u - 1.0),
                                  at(max_r + 1.0, min_u - 1.0))
        v_line = Line.CreateBound(at(max_r + 1.0, min_u - 1.0),
                                  at(max_r + 1.0, max_u + 1.0))
    except Exception:
        return min_r, max_r, min_u, max_u, False, False
    h = probe(("x", "y"), h_line, along_right)
    u = probe(("z",), v_line, along_up)
    if h is not None:
        min_r, max_r = h
    if u is not None:
        min_u, max_u = u
    return min_r, max_r, min_u, max_u, h is not None, u is not None


def _view_scale(v):
    try:
        return int(v.Scale) or 1
    except Exception:
        return 1


def _group(items, key, tol):
    # Cluster (el, point) items whose key() values lie within tol of a
    # cluster's first member. Returns clusters sorted by key.
    clusters = []
    for it in items:
        k = key(it[1])
        for cl in clusters:
            if abs(cl["k"] - k) <= tol:
                cl["items"].append(it)
                break
        else:
            clusters.append({"k": k, "items": [it]})
    clusters.sort(key=lambda cl: cl["k"])
    return clusters


# ---------------------------------------------------------------------------
# 2. One section: analyse + dimension (inside the caller's transaction)
# ---------------------------------------------------------------------------
CAT_BY_KEY = {
    "pipe": int(BuiltInCategory.OST_PipeCurves),
    "conduit": int(BuiltInCategory.OST_Conduit),
    "duct": int(BuiltInCategory.OST_DuctCurves),
}


def dimension_view(v, dim_type, rules):
    res = {"view": _name(v), "strings": [], "made": 0, "notes": []}
    right = v.RightDirection
    up = v.UpDirection
    view_dir = v.ViewDirection
    plane_origin = v.Origin

    def along_right(pt):
        return pt.X * right.X + pt.Y * right.Y + pt.Z * right.Z

    def along_up(pt):
        return pt.X * up.X + pt.Y * up.Y + pt.Z * up.Z

    chamber = _find_chamber(v)
    res["chamber"] = _name(chamber) if chamber is not None else ""

    # The chamber's extent the strings are placed off: its outermost
    # named reference planes where this view can dimension them, else its
    # box. Worked out once per view.
    ext = None
    at = None
    if chamber is not None:
        box = _visible_extent(v, chamber, along_right, along_up)
        at = _frame_at(chamber, along_right, along_up, right, up)
        if box is not None and at is not None and rules:
            e = _plane_extent(v, chamber, along_right, along_up, at, box)
            ext = e[:4]
            if not e[4] and not e[5]:
                res["notes"].append("extent from the chamber's box (no x/y "
                                    "or z planes could be dimensioned here)")
            elif not e[4]:
                res["notes"].append("extent across the view from the box "
                                    "(no x/y planes dimensionable here)")
            elif not e[5]:
                res["notes"].append("extent up the view from the box (no z "
                                    "planes dimensionable here)")
        elif box is not None:
            ext = box

    # One string per rule, in list order; strings on the same side of the
    # chamber stack outward through the slot counters.
    slots = {"vertical": 0, "horizontal": 0, "pipe_col": 0, "pipe_row": 0}
    for rule in (rules or []):
        rule = CS.normalise_rule(rule)
        if rule is None:
            continue
        if rule["kind"] == CS.RULE_PIPES:
            text, n = _dimension_pipes(v, dim_type, chamber, rule,
                                       along_right, along_up, right, up,
                                       plane_origin, view_dir, slots,
                                       res["notes"], ext)
        else:
            text = _dimension_rule(v, dim_type, chamber, rule, along_right,
                                   along_up, right, up, slots, ext, at)
            n = 1 if ": created" in text else 0
        res["strings"].append(text)
        res["made"] += n
    return res


def _dimension_pipes(v, dim_type, chamber, rule, along_right, along_up,
                     right, up, plane_origin, view_dir, slots, notes, ext):
    # The centreline strings for one rule: column spacing above the
    # chamber (or the bank when it sticks out / there is no chamber), row
    # spacing to the left of it. Returns (row text, strings created).
    label = " + ".join(c + "s" for c in rule["cats"])
    cats = set(CAT_BY_KEY[c] for c in rule["cats"])
    margin_ft = DUCT_MARGIN_MM / MM_PER_FOOT
    ducts = []
    rejected = 0
    for el in FilteredElementCollector(doc, v.Id).WhereElementIsNotElementType():
        if _cat_int(el) not in cats:
            continue
        c = _section_cross_point(el, plane_origin, view_dir)
        if c is None:
            continue
        if not _inside_model_bb(chamber, c, margin_ft):
            rejected += 1
            continue
        ducts.append((el, c))
    if rejected:
        notes.append("{0} {1} outside the chamber ignored".format(
            rejected, label))
    if not ducts:
        return "{0}: none in view".format(label), 0

    ducts.sort(key=lambda d: along_right(d[1]))
    columns = _group(ducts, along_right, COL_TOL_MM / MM_PER_FOOT)
    rows = _group(ducts, along_up, ROW_TOL_MM / MM_PER_FOOT)

    # One representative per column (its topmost run) and per row (its
    # leftmost run), so each chain shows one spacing only.
    col_reps = [max(cl["items"], key=lambda d: along_up(d[1]))
                for cl in columns]
    row_reps = [min(rw["items"], key=lambda d: along_right(d[1]))
                for rw in rows]

    bank_top_u = max(along_up(c) for _el, c in ducts)
    bank_left_r = min(along_right(c) for _el, c in ducts)
    if ext is not None:
        bank_top_u = max(bank_top_u, ext[3])
        bank_left_r = min(bank_left_r, ext[0])
    scale = _view_scale(v)

    def refs_of(reps):
        arr = ReferenceArray()
        pts = []
        missing = 0
        for el, c in reps:
            r = _get_centreline_ref(el, v)
            if r is None:
                missing += 1
                continue
            arr.Append(r)
            pts.append(c)
        return arr, pts, missing

    def make(line, arr):
        if dim_type is not None:
            return doc.Create.NewDimension(v, line, arr, dim_type)
        return doc.Create.NewDimension(v, line, arr)

    parts = ["{0} run(s), {1} column(s) x {2} row(s)".format(
        len(ducts), len(columns), len(rows))]
    made = 0

    # --- column spacing, above the bank ---
    if rule["cols"]:
        if len(columns) < 2:
            parts.append("columns skipped (single column)")
        else:
            arr, pts, missing = refs_of(col_reps)
            if missing:
                notes.append("{0} column(s) gave no centreline "
                             "reference".format(missing))
            if arr.Size < 2:
                parts.append("columns NOT created - fewer than two "
                             "references")
            else:
                left_pt = pts[0]
                lift = (bank_top_u - along_up(left_pt)) + \
                    CS.string_offset_mm(scale, slots["pipe_col"]) / MM_PER_FOOT
                o = XYZ(left_pt.X + up.X * lift, left_pt.Y + up.Y * lift,
                        left_pt.Z + up.Z * lift)
                span = along_right(pts[-1]) - along_right(left_pt)
                e = XYZ(o.X + right.X * (span + 1.0),
                        o.Y + right.Y * (span + 1.0),
                        o.Z + right.Z * (span + 1.0))
                try:
                    d = make(Line.CreateBound(o, e), arr)
                    if d is not None:
                        parts.append("columns created")
                        made += 1
                        slots["pipe_col"] += 1
                    else:
                        parts.append("columns NOT created")
                except Exception as ex:
                    parts.append("columns NOT created - {0}".format(ex))

    # --- row spacing, left of the bank ---
    if rule["rows"]:
        if len(rows) < 2:
            parts.append("rows skipped (single row)")
        else:
            arr, pts, missing = refs_of(row_reps)
            if missing:
                notes.append("{0} row(s) gave no centreline "
                             "reference".format(missing))
            if arr.Size < 2:
                parts.append("rows NOT created - fewer than two references")
            else:
                low_pt = pts[0]
                shift = (along_right(low_pt) - bank_left_r) + \
                    CS.string_offset_mm(scale, slots["pipe_row"]) / MM_PER_FOOT
                o = XYZ(low_pt.X - right.X * shift, low_pt.Y - right.Y * shift,
                        low_pt.Z - right.Z * shift)
                vspan = along_up(pts[-1]) - along_up(low_pt)
                e = XYZ(o.X + up.X * (vspan + 1.0), o.Y + up.Y * (vspan + 1.0),
                        o.Z + up.Z * (vspan + 1.0))
                try:
                    d = make(Line.CreateBound(o, e), arr)
                    if d is not None:
                        parts.append("rows created")
                        made += 1
                        slots["pipe_row"] += 1
                    else:
                        parts.append("rows NOT created")
                except Exception as ex:
                    parts.append("rows NOT created - {0}".format(ex))
    return "{0}: {1}".format(label, ", ".join(parts)), made


def _make_string(v, dim_type, line, planes, rule):
    # One dimension along `line` through `planes` [(name, ref)], honouring
    # the rule's chain / direct mode. When Revit refuses and the rule says
    # 'dimension anyway', each plane is probed with a throwaway dimension
    # and the ones this view can take are used. Returns
    # (dimension or None, planes used, names skipped, error text).
    def make(refs):
        arr = ReferenceArray()
        picked = refs if rule["mode"] == CS.Z_CHAIN else [refs[0], refs[-1]]
        for _nm, ref in picked:
            arr.Append(ref)
        if dim_type is not None:
            return doc.Create.NewDimension(v, line, arr, dim_type)
        return doc.Create.NewDimension(v, line, arr)

    try:
        d = make(planes)
        if d is not None:
            return d, planes, [], ""
        err = "NewDimension returned nothing"
    except Exception as ex:
        err = "{0}".format(ex)
    if not rule["skip"]:
        return None, planes, [], err
    usable = []
    for i, p in enumerate(planes):
        for j, q in enumerate(planes):
            if i == j:
                continue
            try:
                arr = ReferenceArray()
                arr.Append(p[1])
                arr.Append(q[1])
                probe = doc.Create.NewDimension(v, line, arr)
            except Exception:
                continue
            if probe is not None:
                try:
                    doc.Delete(probe.Id)
                except Exception:
                    pass
                usable.append(p)
                break
    skipped = [p[0] for p in planes if p not in usable]
    if len(usable) < 2:
        return None, usable, skipped, (
            "only {0} can be dimensioned here".format(usable[0][0])
            if usable else "none of the planes can be dimensioned here")
    try:
        d = make(usable)
    except Exception as ex:
        return None, usable, skipped, "{0}".format(ex)
    return d, usable, skipped, ("" if d is not None
                                else "NewDimension returned nothing")


def _ref_positions(d, along):
    # Where a dimension's references sit along an axis, read back from its
    # own segments (geometric order). [] when it cannot be read.
    try:
        crv = d.Curve
        dv = crv.Direction
        line_dir = (dv.X, dv.Y, dv.Z)
        origins, values = [], []
        if d.NumberOfSegments > 1:
            for seg in d.Segments:
                o = seg.Origin
                origins.append((o.X, o.Y, o.Z))
                values.append(float(seg.Value or 0.0))
        else:
            o = d.Origin
            origins.append((o.X, o.Y, o.Z))
            values.append(float(d.Value or 0.0))
        return CS.positions_from_segments(origins, values, line_dir, along)
    except Exception:
        return []


def _dimension_rule(v, dim_type, chamber, rule, along_right, along_up, right,
                    up, slots, ext, at):
    # One reference-plane string for one rule, placed off the chamber
    # extent `ext` (min_r, max_r, min_u, max_u). Returns the row text.
    rule = CS.normalise_rule(rule) or rule
    axis = rule["axis"]
    label = "{0} {1}".format(axis, rule["spec"])
    if chamber is None:
        return label + ": no chamber in view"
    found = _named_planes(chamber, axis)
    if not found:
        return "{0}: no {1} planes on '{2}'".format(label, axis, _name(chamber))
    wanted = CS.wanted_numbers(CS.parse_plane_spec(rule["spec"]), found.keys())
    missing = ["{0}{1}".format(axis, n) for n in wanted if n not in found]
    if missing and not rule["skip"]:
        return "{0}: NOT created - {1} not in the family".format(
            label, ", ".join(missing))
    planes = [found[n] for n in sorted(n for n in wanted if n in found)]
    if len(planes) < 2:
        return "{0}: only {1} usable plane(s){2}".format(
            label, len(planes),
            " (missing " + ", ".join(missing) + ")" if missing else "")
    if ext is None:
        return label + ": chamber has no bounding box"
    if at is None:
        return label + ": chamber has no centre"
    min_r, max_r, min_u, max_u = ext
    # 'inside the outline' is judged against what the view shows of the
    # chamber (its box), not the plane extent the string is placed off.
    box = _visible_extent(v, chamber, along_right, along_up) or ext
    scale = _view_scale(v)
    natural = "vertical" if axis == "z" else "horizontal"
    order = [natural, "horizontal" if natural == "vertical" else "vertical"]
    tol = 100.0 / MM_PER_FOOT
    last = ""
    for how in order:
        off = CS.string_offset_mm(scale, slots[how]) / MM_PER_FOOT
        if how == "vertical":
            p0, p1 = at(max_r + off, min_u - 1.0), at(max_r + off, max_u + 1.0)
            along, low, high = along_up, box[2], box[3]
        else:
            p0, p1 = at(min_r - 1.0, min_u - off), at(max_r + 1.0, min_u - off)
            along, low, high = along_right, box[0], box[1]
        try:
            line = Line.CreateBound(p0, p1)
        except Exception as ex:
            last = "{0}".format(ex)
            continue
        d, used, skipped, err = _make_string(v, dim_type, line, planes, rule)
        if d is None:
            last = err
            continue
        dropped = []
        if rule["inside"]:
            # Planes beyond the chamber's visible box: read their positions
            # off the string and remake it without them (numbers are taken
            # to rise with position, z1 lowest, x1 leftmost).
            geo = used if rule["mode"] == CS.Z_CHAIN else [used[0], used[-1]]
            pos = _ref_positions(d, along)
            if len(pos) == len(geo):
                out_ranks = CS.outside_span(pos, low, high, tol)
                if out_ranks:
                    drop_names = set(geo[r][0] for r in out_ranks
                                     if r < len(geo))
                    keep = [p for p in used if p[0] not in drop_names]
                    try:
                        doc.Delete(d.Id)
                    except Exception:
                        pass
                    d = None
                    dropped = sorted(drop_names)
                    if len(keep) >= 2:
                        d, used, skipped2, err = _make_string(
                            v, dim_type, line, keep, rule)
                        skipped = skipped + skipped2
                    if d is None:
                        last = (err or "fewer than two planes left inside "
                                "the outline")
                        continue
        slots[how] += 1
        text = "{0}: created {1} to {2} ({3}, {4})".format(
            label, used[0][0], used[-1][0],
            "chain of {0}".format(len(used)) if rule["mode"] == CS.Z_CHAIN
            else "direct", how)
        if skipped:
            text += ", skipped " + ", ".join(skipped)
        if dropped:
            text += ", outside the outline: " + ", ".join(dropped)
        if missing:
            text += ", not in family: " + ", ".join(missing)
        return text
    return "{0}: NOT created - {1}".format(label, last)


# ---------------------------------------------------------------------------
# 3. Which sections, and the dimension types on offer
# ---------------------------------------------------------------------------
# Sheets Full Pipeline drives this script headless: options arrive on the
# sys module (which survives the pymep_* purge above), the outcome goes
# back the same way.
_PIPE = getattr(sys, "_pymep_pipeline", None) or {}
_HEADLESS = _PIPE.get("dims")

active = doc.ActiveView
active_is_section = active is not None and active.ViewType == ViewType.Section

all_sections = []
for v in FilteredElementCollector(doc).OfClass(View):
    try:
        if v.IsTemplate or v.ViewType != ViewType.Section:
            continue
    except Exception:
        continue
    all_sections.append(v)
all_sections.sort(key=lambda v: _name(v).lower())

sheet_sections = []
if isinstance(active, ViewSheet):
    try:
        placed = set(i.IntegerValue for i in active.GetAllPlacedViews())
    except Exception:
        placed = set()
    sheet_sections = [v for v in all_sections if v.Id.IntegerValue in placed]

if not _HEADLESS and not active_is_section and not all_sections:
    forms.alert("No section views in this project.\n\n"
                "Open a section, or make some with Create Sections, then "
                "run again.", exitscript=True)

dim_types = {}
for dt in FilteredElementCollector(doc).OfClass(DimensionType):
    try:
        if dt.StyleType != DimensionStyleType.Linear:
            continue
    except Exception:
        continue
    nm = _name(dt)
    if nm and nm != "?":
        dim_types.setdefault(nm, dt)
dim_names = sorted(dim_types, key=lambda s: s.lower())


# ---------------------------------------------------------------------------
# 4. The dialog
# ---------------------------------------------------------------------------
class DimWindow(forms.WPFWindow):

    def __init__(self, remembered):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self.result = None
        self._ready = False
        self._state = {}      # view id -> ticked
        self._boxes = []
        from System.Windows import Visibility
        if active_is_section:
            self.GrpSections.Visibility = Visibility.Collapsed
            self.TxtInfo.Text = ("Section '{0}' is open: it is the one "
                                 "dimensioned.".format(_name(active)))
        else:
            if sheet_sections:
                self.TxtInfo.Text = (
                    "Sheet {0}: its {1} section(s) are listed and ticked. "
                    "Tick more from the whole project if you like.".format(
                        _name(active), len(sheet_sections)))
                self.ChkAllProject.IsChecked = False
            else:
                self.TxtInfo.Text = ("No section is open: tick the sections "
                                     "to dimension.")
                self.ChkAllProject.IsChecked = True
                self.ChkAllProject.IsEnabled = False
            for v in (sheet_sections or []):
                self._state[v.Id.IntegerValue] = True
        self.CmbDimType.Items.Clear()
        for n in dim_names:
            self.CmbDimType.Items.Add(n)
        first = CS.pick_dim_type_name(dim_names, remembered["dim_type"])
        if first is not None:
            self.CmbDimType.SelectedItem = first
        self._rules = RuleList(self, remembered["rules"],
                               settings=_settings, save=save_settings)
        self._rebuild()
        self._ready = True

    def _pool(self):
        if self.ChkAllProject.IsChecked or not sheet_sections:
            return all_sections
        return sheet_sections

    def _rebuild(self):
        from System.Windows.Controls import CheckBox
        from System.Windows import Thickness
        self.PnlSections.Children.Clear()
        self._boxes = []
        pool = self._pool()
        query = ""
        try:
            query = self.TxtFilter.Text or ""
        except Exception:
            pass
        keep = CS.filter_labels([_name(v) for v in pool], query)
        for i in keep:
            v = pool[i]
            cb = CheckBox()
            cb.Content = _name(v)
            cb.IsChecked = self._state.get(v.Id.IntegerValue, False)
            cb.Margin = Thickness(0, 2, 0, 2)
            cb.Tag = v.Id.IntegerValue
            cb.Checked += self._on_box
            cb.Unchecked += self._on_box
            self.PnlSections.Children.Add(cb)
            self._boxes.append(cb)
        self._count()

    def _on_box(self, sender, args):
        try:
            self._state[int(sender.Tag)] = bool(sender.IsChecked)
        except Exception:
            pass
        self._count()

    def _count(self):
        try:
            n = len([k for k, on in self._state.items() if on])
            self.TxtCount.Text = "{0} section(s) ticked.".format(n)
            self.StatusText.Text = ""
        except Exception:
            pass

    def _set_all(self, on):
        for cb in self._boxes:
            cb.IsChecked = on
        self._count()

    def on_filter(self, sender, args):
        if getattr(self, "_ready", False):
            self._rebuild()

    def on_scope_changed(self, sender, args):
        if getattr(self, "_ready", False):
            self._rebuild()

    def on_tick_all(self, sender, args):
        self._set_all(True)

    def on_tick_none(self, sender, args):
        self._set_all(False)

    def on_rule_add(self, sender, args):
        self._rules.on_add()

    def on_rule_edit(self, sender, args):
        self._rules.on_edit()

    def on_rule_remove(self, sender, args):
        self._rules.on_remove()

    def on_rule_save(self, sender, args):
        self._rules.on_save()

    def on_rule_cancel(self, sender, args):
        self._rules.on_cancel()

    def on_set_changed(self, sender, args):
        r = getattr(self, "_rules", None)
        if r is not None:
            r.on_set_changed()

    def on_set_save(self, sender, args):
        self._rules.on_set_save()

    def on_set_save_ok(self, sender, args):
        self._rules.on_set_save_ok()

    def on_set_save_cancel(self, sender, args):
        self._rules.on_set_save_cancel()

    def on_set_delete(self, sender, args):
        self._rules.on_set_delete()

    def on_kind_changed(self, sender, args):
        r = getattr(self, "_rules", None)
        if r is not None:
            r.on_kind_changed()

    def on_go(self, sender, args):
        if active_is_section:
            views = [active]
        else:
            ids = set(k for k, on in self._state.items() if on)
            views = [v for v in all_sections if v.Id.IntegerValue in ids]
            if not views:
                self.StatusText.Text = "Tick at least one section."
                return
        name = self.CmbDimType.SelectedItem
        if dim_names and not name:
            self.StatusText.Text = "Pick a dimension type."
            return
        rules = list(self._rules.rules)
        if not rules:
            self.StatusText.Text = ("Nothing to add - use + to put a string "
                                    "in the list.")
            return
        self.result = {"views": views, "dim_type": name, "rules": rules}
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


_settings = load_settings()
if _HEADLESS:
    target_views = list(_HEADLESS["views"])
    dim_name = _HEADLESS.get("dim_type") or CS.pick_dim_type_name(
        dim_names, CS.dim_settings(_settings)["dim_type"])
    if dim_name not in dim_types:
        dim_name = CS.pick_dim_type_name(dim_names, dim_name)
    rules = _HEADLESS.get("rules")
    if rules is None:
        rules = CS.dim_rules(_settings)
    rules = [r for r in (CS.normalise_rule(x) for x in rules) if r]
else:
    _rem = CS.dim_settings(_settings)
    _rem["rules"] = CS.dim_rules(_settings)
    win = DimWindow(_rem)
    win.ShowDialog()
    if not win.result:
        script.exit()
    target_views = win.result["views"]
    dim_name = win.result["dim_type"]
    rules = win.result["rules"]
dim_type = dim_types.get(dim_name) if dim_name else None
try:
    if dim_name:
        _settings[CS.SETTINGS_DIM_TYPE] = dim_name
    _settings[CS.SETTINGS_DIM_RULES] = [dict(r) for r in rules]
    save_settings(_settings)
except Exception:
    pass


# ---------------------------------------------------------------------------
# 5. Run - one transaction, one result row per section
# ---------------------------------------------------------------------------
results = []
t = Transaction(doc, "pyMEP: Dimension {0} section(s)".format(
    len(target_views)))
t.Start()
try:
    for v in target_views:
        try:
            results.append(dimension_view(v, dim_type, rules))
        except Exception as ex:
            results.append({"view": _name(v), "strings": ["FAILED"],
                            "made": 0, "chamber": "",
                            "notes": ["{0}".format(ex)]})
    t.Commit()
except Exception as ex:
    t.RollBack()
    forms.alert("Failed, no changes made:\n\n{0}".format(ex), exitscript=True)


# ---------------------------------------------------------------------------
# 6. Report
# ---------------------------------------------------------------------------
made = sum(r.get("made", 0) for r in results)
out.print_md("### Dimension section")
out.print_md("**Sections:** {0}  |  **Dimension type:** {1}  |  "
             "**Strings created:** {2}  |  **Rules:** {3}".format(
                 len(results), dim_name or "(view default)", made,
                 "; ".join(CS.rule_label(r) for r in rules) or "none"))
if dim_name and dim_type is None:
    out.print_md("- Dimension type '{0}' was not found; the view default "
                 "was used.".format(dim_name))
rows = []
for r in results:
    rows.append([r["view"], r.get("chamber") or "-",
                 " | ".join(r.get("strings") or []) or "-",
                 "; ".join(r["notes"]) if r["notes"] else ""])
out.print_table(table_data=rows,
                columns=["Section", "Chamber", "Strings", "Notes"])

if _HEADLESS:
    _PIPE["out_dims"] = {"sections": len(results), "strings": made}

# Keep the output window open (matches the other Chambers buttons).
