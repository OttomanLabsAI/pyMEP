# -*- coding: utf-8 -*-
"""Dimension Section - dimension the pipe / conduit / duct CENTRELINES in
chamber sections, and nothing else.

WHERE it runs:
  * From a SECTION view: that one view is dimensioned. The dialog only
    asks for the dimension type.
  * From anywhere else (a sheet, a plan): the dialog offers a tick list of
    sections - the ones placed on the open sheet when you start from a
    sheet, or every section in the project - plus the dimension type.

WHAT it does in each section:
  * Every pipe, conduit and duct visible in the section (inside the
    chamber's footprint when a chamber family is in view) is located where
    it crosses the section plane.
  * The runs are grouped into COLUMNS (same position across the view) and
    ROWS (same height). One chained dimension goes ABOVE the bank through
    one centreline per column (the column spacing), and one chained
    dimension goes to the LEFT of the bank through one centreline per row
    (the row spacing). A single row or a single column gets no dimension
    in that direction.
  * CHAMBER LEVELS (optional, on by default): the chamber family's
    reference planes named z1, z2, z3... are found by name and dimensioned
    to the right of the chamber, either as one chain through every plane
    in number order or as one overall dimension from z1 straight to the
    highest - the dialog asks which. The planes must be named in the
    family and set as a reference. Skipped when fewer than two are found.
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
DIM_OFFSET_MM = 600.0       # column string this far above the bank
VDIM_OFFSET_MM = 900.0      # row string this far left of the bank
ZDIM_OFFSET_MM = 900.0      # z-plane string this far right of the chamber


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


def _z_planes(inst):
    # [(name, Reference)] of the family instance's reference planes named
    # z<number>, lowest number first. Named planes are reported by the
    # instance as strong or weak references.
    names = []
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
            if CS.z_plane_number(nm) is not None:
                names.append(nm)
    out_list = []
    for nm in CS.z_plane_order(names):
        try:
            ref = inst.GetReferenceByName(nm)
        except Exception:
            ref = None
        if ref is not None:
            out_list.append((nm, ref))
    return out_list


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
def dimension_view(v, dim_type, z_planes=True, z_mode=CS.Z_CHAIN):
    res = {"view": _name(v), "ducts": 0, "cols": 0, "rows": 0,
           "col": "-", "row": "-", "z": "-", "notes": []}
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
    margin_ft = DUCT_MARGIN_MM / MM_PER_FOOT

    # --- chamber levels: z1, z2, ... reference planes, right of the chamber
    if z_planes:
        res["z"] = _dimension_z_planes(v, dim_type, chamber, along_right,
                                       along_up, right, up, z_mode)

    ducts = []
    rejected = 0
    for el in FilteredElementCollector(doc, v.Id).WhereElementIsNotElementType():
        if _cat_int(el) not in DUCT_CATS:
            continue
        c = _section_cross_point(el, plane_origin, view_dir)
        if c is None:
            continue
        if not _inside_model_bb(chamber, c, margin_ft):
            rejected += 1
            continue
        ducts.append((el, c))
    res["ducts"] = len(ducts)
    if rejected:
        res["notes"].append("{0} duct(s) outside the chamber ignored".format(
            rejected))
    if not ducts:
        res["col"] = res["row"] = "no ducts in view"
        return res

    ducts.sort(key=lambda d: along_right(d[1]))
    columns = _group(ducts, along_right, COL_TOL_MM / MM_PER_FOOT)
    rows = _group(ducts, along_up, ROW_TOL_MM / MM_PER_FOOT)
    res["cols"] = len(columns)
    res["rows"] = len(rows)

    # One representative per column (its topmost duct) and per row (its
    # leftmost duct), so each chain shows one spacing only.
    col_reps = [max(cl["items"], key=lambda d: along_up(d[1]))
                for cl in columns]
    row_reps = [min(rw["items"], key=lambda d: along_right(d[1]))
                for rw in rows]

    bank_top_u = max(along_up(c) for _el, c in ducts)
    bank_left_r = min(along_right(c) for _el, c in ducts)

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

    # --- column spacing, above the bank ---
    if len(columns) < 2:
        res["col"] = "skipped (single column)"
    else:
        arr, pts, missing = refs_of(col_reps)
        if missing:
            res["notes"].append("{0} column(s) gave no centreline "
                                "reference".format(missing))
        if arr.Size < 2:
            res["col"] = "NOT created - fewer than two references"
        else:
            left_pt = pts[0]
            lift = (bank_top_u - along_up(left_pt)) + DIM_OFFSET_MM / MM_PER_FOOT
            o = XYZ(left_pt.X + up.X * lift, left_pt.Y + up.Y * lift,
                    left_pt.Z + up.Z * lift)
            span = along_right(pts[-1]) - along_right(left_pt)
            e = XYZ(o.X + right.X * (span + 1.0), o.Y + right.Y * (span + 1.0),
                    o.Z + right.Z * (span + 1.0))
            try:
                d = make(Line.CreateBound(o, e), arr)
                res["col"] = "created" if d is not None else "NOT created"
            except Exception as ex:
                res["col"] = "NOT created - {0}".format(ex)

    # --- row spacing, left of the bank ---
    if len(rows) < 2:
        res["row"] = "skipped (single row)"
    else:
        arr, pts, missing = refs_of(row_reps)
        if missing:
            res["notes"].append("{0} row(s) gave no centreline "
                                "reference".format(missing))
        if arr.Size < 2:
            res["row"] = "NOT created - fewer than two references"
        else:
            low_pt = pts[0]
            shift = (along_right(low_pt) - bank_left_r) + \
                VDIM_OFFSET_MM / MM_PER_FOOT
            o = XYZ(low_pt.X - right.X * shift, low_pt.Y - right.Y * shift,
                    low_pt.Z - right.Z * shift)
            vspan = along_up(pts[-1]) - along_up(low_pt)
            e = XYZ(o.X + up.X * (vspan + 1.0), o.Y + up.Y * (vspan + 1.0),
                    o.Z + up.Z * (vspan + 1.0))
            try:
                d = make(Line.CreateBound(o, e), arr)
                res["row"] = "created" if d is not None else "NOT created"
            except Exception as ex:
                res["row"] = "NOT created - {0}".format(ex)
    return res


def _dimension_z_planes(v, dim_type, chamber, along_right, along_up, right,
                        up, z_mode=CS.Z_CHAIN):
    # The chamber's z planes in number order: one chain through all of
    # them, or one overall dimension from the first to the last. Tried
    # along the view's up direction first (level planes), then along right
    # (if the family's z planes turn out vertical). Returns the row text.
    if chamber is None:
        return "no chamber in view"
    planes = _z_planes(chamber)
    if len(planes) < 2:
        return ("no z planes on '{0}'".format(_name(chamber)) if not planes
                else "only {0} found".format(planes[0][0]))
    used = planes if z_mode == CS.Z_CHAIN else [planes[0], planes[-1]]
    arr = ReferenceArray()
    for _nm, ref in used:
        arr.Append(ref)
    ext = _bbox_frame(chamber, along_right, along_up)
    if ext is None:
        return "chamber has no bounding box"
    min_r, max_r, min_u, max_u = ext
    try:
        cbb = chamber.get_BoundingBox(None)
        anchor = XYZ((cbb.Min.X + cbb.Max.X) * 0.5,
                     (cbb.Min.Y + cbb.Max.Y) * 0.5,
                     (cbb.Min.Z + cbb.Max.Z) * 0.5)
    except Exception:
        return "chamber has no centre"
    off = ZDIM_OFFSET_MM / MM_PER_FOOT

    def at(r_val, u_val):
        dr = r_val - along_right(anchor)
        du = u_val - along_up(anchor)
        return XYZ(anchor.X + right.X * dr + up.X * du,
                   anchor.Y + right.Y * dr + up.Y * du,
                   anchor.Z + right.Z * dr + up.Z * du)

    label = "{0} to {1} ({2})".format(
        planes[0][0], planes[-1][0],
        "chain of {0}".format(len(planes)) if z_mode == CS.Z_CHAIN
        else "direct")
    attempts = (
        ("vertical", at(max_r + off, min_u - 1.0), at(max_r + off, max_u + 1.0)),
        ("horizontal", at(min_r - 1.0, min_u - off), at(max_r + 1.0, min_u - off)),
    )
    last = ""
    for how, p0, p1 in attempts:
        try:
            line = Line.CreateBound(p0, p1)
            if dim_type is not None:
                d = doc.Create.NewDimension(v, line, arr, dim_type)
            else:
                d = doc.Create.NewDimension(v, line, arr)
            if d is not None:
                return "created {0}, {1}".format(label, how)
            last = "NewDimension returned nothing"
        except Exception as ex:
            last = "{0}".format(ex)
    return "NOT created {0} - {1}".format(label, last)


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
        self.ChkZPlanes.IsChecked = bool(remembered["z_planes"])
        if remembered["z_mode"] == CS.Z_DIRECT:
            self.RbZDirect.IsChecked = True
        else:
            self.RbZChain.IsChecked = True
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
        self.result = {"views": views, "dim_type": name,
                       "z_planes": bool(self.ChkZPlanes.IsChecked),
                       "z_mode": (CS.Z_DIRECT if self.RbZDirect.IsChecked
                                  else CS.Z_CHAIN)}
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
    want_z = _HEADLESS.get("z_planes")
    if want_z is None:
        want_z = CS.dim_settings(_settings)["z_planes"]
    z_mode = _HEADLESS.get("z_mode") or CS.dim_settings(_settings)["z_mode"]
else:
    win = DimWindow(CS.dim_settings(_settings))
    win.ShowDialog()
    if not win.result:
        script.exit()
    target_views = win.result["views"]
    dim_name = win.result["dim_type"]
    want_z = win.result["z_planes"]
    z_mode = win.result["z_mode"]
dim_type = dim_types.get(dim_name) if dim_name else None
try:
    if dim_name:
        _settings[CS.SETTINGS_DIM_TYPE] = dim_name
    _settings[CS.SETTINGS_DIM_Z_PLANES] = bool(want_z)
    _settings[CS.SETTINGS_DIM_Z_MODE] = z_mode
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
            results.append(dimension_view(v, dim_type, want_z, z_mode))
        except Exception as ex:
            results.append({"view": _name(v), "ducts": 0, "cols": 0,
                            "rows": 0, "col": "FAILED", "row": "FAILED",
                            "z": "FAILED", "chamber": "",
                            "notes": ["{0}".format(ex)]})
    t.Commit()
except Exception as ex:
    t.RollBack()
    forms.alert("Failed, no changes made:\n\n{0}".format(ex), exitscript=True)


# ---------------------------------------------------------------------------
# 6. Report
# ---------------------------------------------------------------------------
made = sum(1 for r in results for k in ("col", "row") if r[k] == "created")
made += sum(1 for r in results if str(r.get("z", "")).startswith("created"))
out.print_md("### Dimension section")
out.print_md("**Sections:** {0}  |  **Dimension type:** {1}  |  "
             "**Strings created:** {2}".format(
                 len(results), dim_name or "(view default)", made))
if dim_name and dim_type is None:
    out.print_md("- Dimension type '{0}' was not found; the view default "
                 "was used.".format(dim_name))
rows = []
for r in results:
    rows.append([r["view"], r.get("chamber") or "-", str(r["ducts"]),
                 "{0} x {1}".format(r["cols"], r["rows"]), r["col"], r["row"],
                 r.get("z", "-") if want_z else "off",
                 "; ".join(r["notes"]) if r["notes"] else ""])
out.print_table(table_data=rows,
                columns=["Section", "Chamber", "Ducts", "Cols x rows",
                         "Column spacing", "Row spacing", "Chamber levels",
                         "Notes"])

if _HEADLESS:
    _PIPE["out_dims"] = {"sections": len(results), "strings": made}

# Keep the output window open (matches the other Chambers buttons).
