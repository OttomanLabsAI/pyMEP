# -*- coding: utf-8 -*-
"""Chamber Plans - one button for chamber scope boxes AND their plan views.

One dialog (pymep_chamber_plans.xaml) asks for everything: WHICH chambers
(the current selection, or a family type with a search box and a tick list
of its instances by Mark), the PLAN EXTENTS, and the VIEW TEMPLATE for the
plan views - and shows live what will be created, moved or skipped before
you press Create. Everything is remembered.

PLAN EXTENTS, two routes:
  * EXACT CROP (default, no scope box): the plan's crop region is set to a
    size along the chamber's own X and Y - typed in mm, or the chamber's
    length parameters plus a clearance each side - around the chamber, and
    the crop region is then turned so the chamber face nearest 'up' is on
    top (turning the crop region is how a plan view is rotated through the
    API). Precise, any size, no seed.
  * SCOPE BOX: a copy of a seed box applied to the plan (steps 1-2 below).
    Revit's API can copy, move and turn a scope box but CANNOT resize one,
    so every box is the seed's size.

For each chamber:
  0. NAME = the chamber's instance parameter "Mark", whole ("LV1/Z1" - the
     zone part is identity, LV numbers repeat across zones). Nothing else
     is read. A chamber with a blank Mark is NOT processed - it is listed
     as skipped in the preview and the report so it can be populated and
     re-run.
  1. ENSURE a scope box named after the Mark: if one exists and sits over
     the chamber in plan it is used as is; if it exists but sits somewhere
     else (a copy that never got moved, a chamber that moved) it is MOVED
     over the chamber in plan; otherwise the seed scope box is copied,
     moved to the chamber centre, rotated and renamed.
     HEIGHT: the API cannot resize a scope box, only move it, and a plan
     only shows - and only takes - a box its CUT PLANE passes through. So
     each box is set as LOW as that allows: its bottom 500 mm under the
     chamber when the seed is tall enough to still reach 300 mm above this
     plan's cut plane, else as low as the cut plane permits (the report
     then says how tall the seed needs to be).
     ROTATION: a rotated scope box turns its plan view with it, so the box
     is aligned to the chamber with the chamber face MOST ALIGNED TO 'UP'
     at the top - never more than 45 degrees off. 'Up' is Project North,
     or True North when the active plan is set to True North (the
     project's angle to True North is read from the shared site).
  2. ENSURE a plan view named after the Mark with that scope box applied -
     only for the chambers being processed (nothing else in the project is
     touched). The plan is a duplicate of the active plan, or a FRESH plan
     on the active plan's level (carrying its view template) when the
     active plan is a callout, a dependent or an assembly view: duplicates
     of those cannot take a scope box at all. The apply is CHECKED and the
     other method is tried before giving up; a view that cannot take its
     box is removed and the reason reported, so no view is ever left
     showing the wrong crop.

IMPORTANT - Revit API limitation: there is NO API to create a scope box from
nothing; scope boxes can only be COPIED from an existing one. So one seed
scope box must already exist. A box named 'sample_scope_box' is preferred;
otherwise the only box is used, or you pick one.

Run it in a PLAN view.

IronPython 2.7: pure ASCII, no f-strings, LF endings.
"""

__title__  = "Chamber\nPlans"
__author__ = "Glent Group"

import math
import os
import sys

# Reload pymep_* lib modules so the script picks up the latest helpers.
for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (
    Transaction, ViewType, View, ViewDuplicateOption, ViewPlan,
    ViewFamilyType, ViewFamily, XYZ, Line, ElementTransformUtils, ElementId,
    FilteredElementCollector, FamilyInstance, BuiltInParameter,
    BuiltInCategory, Element, Options, PlanViewPlane, Transform,
    BoundingBoxXYZ, StorageType, FilteredWorksetCollector, WorksetKind,
)

from pyrevit import revit, forms, script

# Chamber names use the whole Mark (chamber_key trims it); the box
# rotation snaps the chamber face nearest 'up' to the top of the plan.
import pymep_chamber_sections as CS
from pymep_chamber_sections import (
    chamber_key, upright_rotation, wrap_angle, RIGHT_ANGLE, box_bottom,
    plans_settings, pick_seed_name, PLANS_TEMPLATE_ACTIVE,
    PLANS_TEMPLATE_NONE, SETTINGS_PLANS_TEMPLATE, SETTINGS_PLANS_SEED,
    SETTINGS_PLANS_EXTENTS, SETTINGS_PLANS_WIDTH, SETTINGS_PLANS_DEPTH,
    EXTENTS_SCOPE, EXTENTS_CROP, size_settings, SIZE_FIXED, SIZE_PARAMS,
    SETTINGS_SIZE_MODE, SETTINGS_SIZE_PARAM_X, SETTINGS_SIZE_PARAM_Y,
    SETTINGS_SIZE_CLEAR, plan_crop_from_dims, parse_mm, mm_text,
    SETTINGS_PLANS_WORKSET, CURRENT_WORKSET,
)
from pymep_config import load_settings, save_settings

doc = revit.doc
uidoc = revit.uidoc
# Sheets Full Pipeline drives this script headless: it leaves its options
# on the sys module (which survives the pymep_* purge above) and reads the
# outcome back from the same place.
_PIPE = getattr(sys, "_pymep_pipeline", None) or {}
_HEADLESS = _PIPE.get("plans")

view = _HEADLESS["view"] if _HEADLESS else doc.ActiveView
out = script.get_output()

XAML_PATH = os.path.join(os.path.dirname(os.path.abspath(CS.__file__)),
                         "pymep_chamber_plans.xaml")

FT = 304.8
CHAMBER_MARGIN_FT = 500.0 / FT    # box bottom this far under the chamber
CUT_MARGIN_FT = 300.0 / FT        # box top at least this far above the cut


# ---------------------------------------------------------------------------
# Pre-flight: a plan view must be active
# ---------------------------------------------------------------------------
PLAN_TYPES = (ViewType.FloorPlan, ViewType.CeilingPlan,
              ViewType.EngineeringPlan, ViewType.AreaPlan)
if view is None or view.ViewType not in PLAN_TYPES:
    forms.alert("Open a PLAN view first.\n\n"
                "This tool creates a scope box per chamber and duplicates the "
                "active plan for each chamber scope box.", exitscript=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_mark(inst):
    # The chamber's INSTANCE "Mark": the built-in one first, then any
    # instance parameter literally named Mark. Blank -> None (never an Id).
    p = None
    try:
        p = inst.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
    except Exception:
        p = None
    if p is None:
        try:
            p = inst.LookupParameter("Mark")
        except Exception:
            p = None
    if p is None:
        return None
    v = None
    for getter in ("AsString", "AsValueString"):
        try:
            v = getattr(p, getter)()
        except Exception:
            v = None
        if v and v.strip():
            return v.strip()
    return None


def _elem_name(elem):
    for getter in (lambda e: e.Name,
                   lambda e: Element.Name.GetValue(e)):
        try:
            n = getter(elem)
            if n:
                return n
        except Exception:
            pass
    for bip in (BuiltInParameter.ALL_MODEL_TYPE_NAME,
                BuiltInParameter.SYMBOL_NAME_PARAM):
        try:
            p = elem.get_Parameter(bip)
            if p is not None:
                v = p.AsString()
                if v:
                    return v
        except Exception:
            pass
    return "?"


def _type_label(sym):
    try:
        fam = sym.Family.Name
    except Exception:
        fam = "?"
    return "{0} : {1}".format(fam, _elem_name(sym))


def _chamber_pose(inst):
    loc = inst.Location
    if loc is None or not hasattr(loc, "Point") or loc.Point is None:
        return None
    pt = loc.Point
    ang = 0.0
    try:
        ang = loc.Rotation
    except Exception:
        ang = 0.0
    return pt, ang


def _world_centre(inst):
    bb = None
    try:
        bb = inst.get_BoundingBox(view)
    except Exception:
        bb = None
    if bb is None:
        try:
            bb = inst.get_BoundingBox(None)
        except Exception:
            bb = None
    if bb is None:
        loc = getattr(inst, "Location", None)
        if loc is not None and hasattr(loc, "Point") and loc.Point is not None:
            p = loc.Point
            return XYZ(p.X, p.Y, p.Z)
        return None
    return XYZ((bb.Min.X + bb.Max.X) * 0.5,
               (bb.Min.Y + bb.Max.Y) * 0.5,
               (bb.Min.Z + bb.Max.Z) * 0.5)


def _chamber_plan_halfspan(inst, angle):
    # Half-size of the chamber footprint along its LOCAL X and Y, from its
    # world bounding box projected into the chamber frame (a slight
    # over-estimate for a rotated chamber). ~0.5 m each when unreadable.
    try:
        bb = inst.get_BoundingBox(None)
    except Exception:
        bb = None
    centre = _world_centre(inst)
    if bb is None or centre is None:
        return (0.5 / 0.3048, 0.5 / 0.3048)
    ca, sa = math.cos(-angle), math.sin(-angle)
    max_lx = max_ly = 0.0
    for x in (bb.Min.X, bb.Max.X):
        for y in (bb.Min.Y, bb.Max.Y):
            dx, dy = x - centre.X, y - centre.Y
            lx = dx * ca - dy * sa
            ly = dx * sa + dy * ca
            max_lx = max(max_lx, abs(lx))
            max_ly = max(max_ly, abs(ly))
    return (max_lx, max_ly)


def _param_len_ft(inst, name):
    # A LENGTH parameter by name - instance first, then its type. None when
    # absent, not a length, or not positive.
    if not name:
        return None
    holders = [inst]
    try:
        holders.append(doc.GetElement(inst.GetTypeId()))
    except Exception:
        pass
    for holder in holders:
        if holder is None:
            continue
        try:
            p = holder.LookupParameter(name)
        except Exception:
            p = None
        if p is None:
            continue
        try:
            if p.StorageType != StorageType.Double or not p.HasValue:
                continue
            v = p.AsDouble()
        except Exception:
            continue
        if v is not None and v > 0:
            return v
    return None


def _mm0(text):
    # A non-negative mm field, or None.
    if text is None:
        return None
    try:
        t = text.strip().lower().replace(",", ".")
    except Exception:
        return None
    if t.endswith("mm"):
        t = t[:-2].strip()
    if not t:
        return None
    try:
        v = float(t)
    except Exception:
        return None
    if v != v or v - v != 0 or v < 0:
        return None
    return v


def _scopebox_centre(sb):
    # World centre of a scope box from its bounding box (model coords).
    try:
        bb = sb.get_BoundingBox(None)
    except Exception:
        bb = None
    if bb is None:
        return None
    return XYZ((bb.Min.X + bb.Max.X) * 0.5,
               (bb.Min.Y + bb.Max.Y) * 0.5,
               (bb.Min.Z + bb.Max.Z) * 0.5)


def _sanitize(name):
    bad = "\\:{}[]|;<>?`~"
    return "".join("_" if ch in bad else ch for ch in name).strip()


def _scopebox_plan_angle(sb):
    # The seed box's own plan rotation folded to (-45, 45] degrees, read
    # from its edge lines; None when the geometry can't be read. Which of
    # its edges is 'up' cannot be told from geometry, so a seed drawn
    # upright is assumed and only this residual skew is taken out.
    try:
        geo = sb.get_Geometry(Options())
    except Exception:
        return None
    if geo is None:
        return None
    try:
        for g in geo:
            try:
                p0 = g.GetEndPoint(0)
                p1 = g.GetEndPoint(1)
            except Exception:
                continue
            dx, dy, dz = p1.X - p0.X, p1.Y - p0.Y, p1.Z - p0.Z
            if abs(dz) > 1.0e-6 or (dx * dx + dy * dy) < 1.0e-9:
                continue
            return wrap_angle(math.atan2(dy, dx), RIGHT_ANGLE)
    except Exception:
        return None
    return None


def _z_range(el):
    # (bottom, top) Z of an element's model bounding box, or (None, None).
    try:
        bb = el.get_BoundingBox(None)
    except Exception:
        bb = None
    if bb is None:
        return None, None
    return bb.Min.Z, bb.Max.Z


def _cut_plane_z(v):
    # Absolute Z of the plan's cut plane (view range), or None.
    try:
        vr = v.GetViewRange()
        off = vr.GetOffset(PlanViewPlane.CutPlane)
        lvl = None
        try:
            lvl = doc.GetElement(vr.GetLevelId(PlanViewPlane.CutPlane))
        except Exception:
            lvl = None
        if lvl is None or not hasattr(lvl, "ProjectElevation"):
            lvl = v.GenLevel
        if lvl is None:
            return None
        return lvl.ProjectElevation + off
    except Exception:
        return None


def _drop(bottom_now, height, chamber_bottom):
    # Vertical move for a box of `height` whose bottom is at `bottom_now`,
    # and a note. (0, note) when the cut plane is unknown.
    if cut_z is None or not height or chamber_bottom is None:
        return 0.0, "height kept (cut plane unknown)"
    bottom, reaches = box_bottom(chamber_bottom, height, cut_z,
                                 CHAMBER_MARGIN_FT, CUT_MARGIN_FT)
    if reaches:
        note = "box wraps the chamber"
    else:
        need = (cut_z + CUT_MARGIN_FT) - (chamber_bottom - CHAMBER_MARGIN_FT)
        note = ("box as low as this plan's cut plane allows - a {0:.1f} m "
                "tall seed would reach the chamber".format(need * 0.3048))
    return bottom - bottom_now, note


def _true_north_angle():
    # Rotation from Project North to True North (radians, anticlockwise).
    try:
        return doc.ActiveProjectLocation.GetProjectPosition(XYZ.Zero).Angle
    except Exception:
        return 0.0


def _plan_true_north(v):
    # True when the plan is set to True North (Orientation parameter).
    try:
        p = v.get_Parameter(BuiltInParameter.PLAN_VIEW_NORTH)
        return p is not None and p.AsInteger() == 1
    except Exception:
        return False


PLACE_TOL_FT = 100.0 / 304.8      # 100 mm slack on the box edge


def _box_over_point(sb, pt):
    # True when the scope box's plan footprint (its world AABB, so a
    # rotated box is judged generously) contains the point. None when the
    # box has no readable bounding box.
    try:
        bb = sb.get_BoundingBox(None)
    except Exception:
        bb = None
    if bb is None or pt is None:
        return None
    return (bb.Min.X - PLACE_TOL_FT <= pt.X <= bb.Max.X + PLACE_TOL_FT and
            bb.Min.Y - PLACE_TOL_FT <= pt.Y <= bb.Max.Y + PLACE_TOL_FT)


def _valid_id(eid):
    return eid is not None and eid != ElementId.InvalidElementId


def _source_kind(v):
    # What sort of plan the active view is. Callouts, dependents and
    # assembly views cannot carry a scope box - nor can their duplicates.
    try:
        if v.IsCallout:
            return "callout"
    except Exception:
        pass
    try:
        p = v.get_Parameter(BuiltInParameter.SECTION_PARENT_VIEW_NAME)
        if p is not None and (p.AsString() or "").strip():
            return "callout"
    except Exception:
        pass
    try:
        if _valid_id(v.GetPrimaryViewId()):
            return "dependent view"
    except Exception:
        pass
    try:
        if v.IsAssemblyView:
            return "assembly view"
    except Exception:
        pass
    return "plain plan"


def _diag_view(v):
    # Why a view might not take a scope box, for the report.
    bits = [_source_kind(v)]
    try:
        tid = v.ViewTemplateId
        if _valid_id(tid):
            bits.append("template '{0}'".format(_elem_name(doc.GetElement(tid))))
    except Exception:
        pass
    try:
        p = v.get_Parameter(BuiltInParameter.VIEWER_VOLUME_OF_INTEREST_CROP)
        if p is not None:
            eid = p.AsElementId()
            if _valid_id(eid):
                bits.append("scope box '{0}' already on it".format(
                    _elem_name(doc.GetElement(eid))))
    except Exception:
        pass
    try:
        bits.append("crop {0}".format("on" if v.CropBoxActive else "off"))
    except Exception:
        pass
    return ", ".join(bits)


def _scope_param(v):
    try:
        return v.get_Parameter(BuiltInParameter.VIEWER_VOLUME_OF_INTEREST_CROP)
    except Exception:
        return None


def _try_set_box(v, sb):
    # One attempt. None on success, else a short reason. "read-only" is
    # special-cased by the caller.
    p = _scope_param(v)
    if p is None:
        return "the view has no Scope Box parameter"
    if p.IsReadOnly:
        return "read-only"
    try:
        done = p.Set(sb.Id)
    except Exception as ex:
        return "Revit refused the scope box: {0}".format(ex)
    if not done:
        return ("Revit refused the scope box - it is probably not visible in "
                "this plan (its vertical extent misses the view's level / "
                "cut plane)")
    try:
        got = p.AsElementId()
        if not _valid_id(got) or got.IntegerValue != sb.Id.IntegerValue:
            return "the view did not keep the scope box"
    except Exception:
        pass
    return None


def _apply_scope_box(v, sb):
    # Attach the scope box to the view and CHECK it took. Returns (ok, note)
    # - the note says which extra step was needed, or why it failed.
    # Revit's Set returns False (no exception) when a box can't be applied,
    # and reports the parameter read-only on views that can't carry one.
    why = _try_set_box(v, sb)
    if why is None:
        return True, ""
    if why == "read-only":
        try:
            doc.Regenerate()
        except Exception:
            pass
        why = _try_set_box(v, sb)
        if why is None:
            return True, "after regenerate"
    if why == "read-only":
        # A view template can lock parameters: detach, set, re-attach.
        tid = None
        try:
            tid = v.ViewTemplateId
        except Exception:
            tid = None
        if _valid_id(tid):
            try:
                v.ViewTemplateId = ElementId.InvalidElementId
                why2 = _try_set_box(v, sb)
            except Exception as ex:
                why2 = "detaching the template failed: {0}".format(ex)
            try:
                v.ViewTemplateId = tid
            except Exception:
                pass
            if why2 is None:
                p = _scope_param(v)
                try:
                    got = p.AsElementId() if p is not None else None
                except Exception:
                    got = None
                if _valid_id(got) and got.IntegerValue == sb.Id.IntegerValue:
                    return True, "template detached and re-applied"
                why = "the view template resets the scope box"
            else:
                why = why2
    if why == "read-only":
        why = ("the Scope Box parameter is read-only on this view "
               "({0})".format(_diag_view(v)))
    return False, why


# ---------------------------------------------------------------------------
# 1. What the dialog offers: scope boxes (the seed), chamber types and the
#    selection, plan view templates; and what the active view is.
# ---------------------------------------------------------------------------
scope_boxes = []
for el in FilteredElementCollector(doc)\
        .OfCategory(BuiltInCategory.OST_VolumeOfInterest)\
        .WhereElementIsNotElementType():
    scope_boxes.append(el)

# No scope box in the project only rules out the scope-box route; the
# exact-crop route needs none.
sb_by_name = {}
for sb in scope_boxes:
    sb_by_name.setdefault(_elem_name(sb), sb)
seed_names = sorted(sb_by_name, key=lambda s: s.lower())

# User worksets (workshared models only) for the boxes made or moved.
worksets = {}
try:
    if doc.IsWorkshared:
        for ws in FilteredWorksetCollector(doc).OfKind(WorksetKind.UserWorkset):
            worksets.setdefault(ws.Name, ws.Id)
except Exception:
    worksets = {}
workset_names = sorted(worksets, key=lambda s: s.lower())


def _set_workset(el, ws_id):
    # Move an element onto a workset. Returns an error text, or None.
    if ws_id is None:
        return None
    try:
        p = el.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM)
        if p is None or p.IsReadOnly:
            return "workset parameter not editable"
        p.Set(ws_id.IntegerValue)
    except Exception as ex:
        return "workset not set: {0}".format(ex)
    return None


def _selected_family_instances():
    out_list = []
    try:
        ids = uidoc.Selection.GetElementIds()
    except Exception:
        ids = []
    for eid in ids:
        el = doc.GetElement(eid)
        if not isinstance(el, FamilyInstance):
            continue
        loc = getattr(el, "Location", None)
        if loc is not None and hasattr(loc, "Point") and loc.Point is not None:
            out_list.append(el)
    return out_list


sel_insts = _selected_family_instances()

inst_by_typeid = {}
sym_by_typeid = {}
for fi in FilteredElementCollector(doc).OfClass(FamilyInstance)\
        .WhereElementIsNotElementType().ToElements():
    if fi.Location is None or not hasattr(fi.Location, "Point") \
            or fi.Location.Point is None:
        continue
    tid = fi.GetTypeId()
    if tid is None or tid == ElementId.InvalidElementId:
        continue
    key = tid.IntegerValue
    inst_by_typeid.setdefault(key, [])
    inst_by_typeid[key].append(fi)
    if key not in sym_by_typeid:
        sym_by_typeid[key] = doc.GetElement(tid)

if not inst_by_typeid:
    forms.alert("No placed point-based family instances found.",
                exitscript=True)

type_options = []
for key, insts in inst_by_typeid.items():
    sym = sym_by_typeid.get(key)
    if sym is None:
        continue
    type_options.append({
        "label": "{0}   ({1} placed)".format(_type_label(sym), len(insts)),
        "typeid": key,
        "insts": insts,
    })
type_options.sort(key=lambda d: d["label"].lower())

plan_templates = {}          # name -> template view (plan kinds only)
view_names = set()
for v in FilteredElementCollector(doc).OfClass(View):
    nm = _elem_name(v)
    try:
        is_tmpl = bool(v.IsTemplate)
    except Exception:
        is_tmpl = False
    if is_tmpl:
        try:
            if v.ViewType in PLAN_TYPES and nm and nm != "?":
                plan_templates.setdefault(nm, v)
        except Exception:
            pass
        continue
    if nm and nm != "?":
        view_names.add(nm)
template_choices = [PLANS_TEMPLATE_ACTIVE, PLANS_TEMPLATE_NONE] + \
    sorted(plan_templates, key=lambda s: s.lower())

# How the plan views will be made. Duplicating a callout / dependent /
# assembly view gives another view of the same kind, which cannot carry a
# scope box, so those get FRESH plans on the active plan's level instead.
src_kind = _source_kind(view)
src_locked = False
try:
    _sp = _scope_param(view)
    src_locked = bool(_sp is not None and _sp.IsReadOnly)
except Exception:
    src_locked = False
prefer_fresh = (src_kind != "plain plan") or src_locked
src_level = None
try:
    src_level = view.GenLevel
except Exception:
    src_level = None

# The plan's cut plane and the seed's height decide how low a box can go.
cut_z = _cut_plane_z(view)

# What counts as 'up' for the box rotation: True North when the active
# plan is oriented that way, else Project North.
use_true_north = _plan_true_north(view)
up_ref = _true_north_angle() if use_true_north else 0.0
if use_true_north:
    up_label = "True North ({0:.2f} deg from Project North)".format(
        math.degrees(up_ref) % 360.0)
else:
    up_label = "Project North"


# ---------------------------------------------------------------------------
# 2. Jobs: one per chamber Mark - built live for the dialog's preview and
#    again for the run. Chambers without a Mark are NOT processed.
# ---------------------------------------------------------------------------
def _crop_halves(inst, angle):
    # (half_x, half_y, note) of the exact crop for a chamber, in feet, in
    # the chamber's own frame.
    if crop_size_mode == SIZE_PARAMS:
        hx, hy = _chamber_plan_halfspan(inst, angle)
        missing = []
        dx = _param_len_ft(inst, crop_param_x)
        if dx is None:
            dx = 2.0 * hx
            missing.append(crop_param_x)
        dy = _param_len_ft(inst, crop_param_y)
        if dy is None:
            dy = 2.0 * hy
            missing.append(crop_param_y)
        half_x, half_y = plan_crop_from_dims(dx, dy, crop_clear_ft)
        note = ""
        if missing:
            note = "parameter(s) {0} not found - bounding box used".format(
                ", ".join("'{0}'".format(m) for m in missing))
        return half_x, half_y, note
    return crop_w_ft * 0.5, crop_d_ft * 0.5, ""


def _build_jobs(instances, seed):
    # -> (jobs, no_mark, dup_marks)
    no_mark = []             # (ident, instance)
    dup_marks = []           # (mark, instance) sharing a Mark with a job
    jobs = []
    seen = set()
    for inst in instances:
        mark = _get_mark(inst)
        if not mark:
            no_mark.append(("Id {0} ({1})".format(
                inst.Id.IntegerValue, _elem_name(inst)), inst))
            continue
        base = _sanitize(chamber_key(mark))   # the whole Mark, trimmed
        if base in seen:
            dup_marks.append((mark, inst))
            continue
        seen.add(base)
        zmin, zmax = _z_range(inst)
        job = {"inst": inst, "mark": mark, "base": base,
               "centre": _world_centre(inst), "box": None, "box_note": "-",
               "view_note": "-", "zmin": zmin, "zmax": zmax}
        if extents == EXTENTS_CROP:
            job["box_state"] = "none"
        else:
            sb = sb_by_name.get(base)
            if sb is None or (seed is not None
                              and sb.Id.IntegerValue == seed.Id.IntegerValue):
                job["box_state"] = "create"
            else:
                job["box"] = sb
                over = _box_over_point(sb, job["centre"])
                job["box_state"] = "misplaced" if over is False else "exists"
        job["view_state"] = "exists" if base in view_names else "create"
        jobs.append(job)
    return jobs, no_mark, dup_marks


def _name_list(names, cap=8):
    lines = ["  - " + n for n in names[:cap]]
    if len(names) > cap:
        lines.append("  ... and {0} more".format(len(names) - cap))
    return lines


def _plan_text(jobs, no_mark, dup_marks):
    creating = [j["base"] for j in jobs if j["box_state"] == "create"]
    moving = [j["base"] for j in jobs if j["box_state"] == "misplaced"]
    views = [j["base"] for j in jobs if j["view_state"] == "create"]
    lines = []
    if extents == EXTENTS_CROP:
        if not views:
            lines.append("Nothing to do: every chamber with a Mark already "
                         "has a plan view of that name.")
        lines.append("Plan views to create: {0}".format(len(views)))
        lines.extend(_name_list(views))
        if crop_size_mode == SIZE_PARAMS:
            lines.append("Exact crop: '{0}' x '{1}' from each chamber + "
                         "{2:.0f} mm clearance each side, turned to the "
                         "chamber. No scope boxes.".format(
                             crop_param_x, crop_param_y,
                             crop_clear_ft * 304.8))
        else:
            lines.append("Exact crop: {0:.0f} x {1:.0f} mm along each "
                         "chamber's X and Y, turned to the chamber. No "
                         "scope boxes.".format(crop_w_ft * 304.8,
                                               crop_d_ft * 304.8))
    else:
        if not creating and not moving and not views:
            lines.append("Nothing to do: every chamber with a Mark already "
                         "has a scope box over it and a plan view of that "
                         "name.")
        lines.append("Scope boxes to create: {0}".format(len(creating)))
        lines.extend(_name_list(creating))
        if moving:
            lines.append("Existing scope boxes moved onto their chamber: {0}"
                         .format(len(moving)))
            lines.extend(_name_list(moving))
        lines.append("Plan views to create: {0}".format(len(views)))
        lines.extend(_name_list(views))
        if cut_z is None:
            lines.append("This plan's cut plane could not be read: boxes "
                         "keep the seed's height.")
        else:
            lines.append("Boxes go as low as this plan's cut plane allows: "
                         "500 mm under the chamber when the seed is tall "
                         "enough, else the report says how tall the seed "
                         "must be.")
    if prefer_fresh:
        lines.append("Plans are created FRESH on level '{0}' - the active "
                     "view is a {1}, whose duplicates cannot carry a scope "
                     "box.".format(
                         _elem_name(src_level) if src_level is not None
                         else "?", src_kind))
    if no_mark:
        lines.append("SKIPPED - blank Mark (populate it and re-run): {0}"
                     .format(len(no_mark)))
        lines.extend(_name_list([ident for ident, _i in no_mark]))
    if dup_marks:
        lines.append("Duplicate Marks (one box + plan per Mark): {0}".format(
            len(dup_marks)))
        lines.extend(_name_list(sorted(set(m for m, _i in dup_marks))))
    return "\n".join(lines)


# Extents state the preview and the run read. The dialog updates these as
# the user changes the controls, then the run takes the final values.
extents = EXTENTS_CROP
crop_size_mode = SIZE_FIXED
crop_w_ft = 0.0
crop_d_ft = 0.0
crop_param_x = u""
crop_param_y = u""
crop_clear_ft = 0.0


# ---------------------------------------------------------------------------
# 3. The dialog
# ---------------------------------------------------------------------------
class PlansWindow(forms.WPFWindow):

    def __init__(self, types, selected, remembered):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self.result = None
        self._ready = False
        self._filling = False
        self._types = types
        self._visible = []
        self._boxes = []
        self._sel = list(selected)

        level_txt = ""
        if src_level is not None:
            level_txt = ", level '{0}'".format(_elem_name(src_level))
        self.TxtInfo.Text = (
            "Active view: {0} ({1}{2}). Up for the box rotation: {3}."
            .format(_elem_name(view), src_kind, level_txt, up_label))

        n_sel = len(self._sel)
        if n_sel:
            self.RbSelection.Content = (
                "use the {0} selected chamber(s)".format(n_sel))
            self.RbSelection.IsChecked = True
        else:
            self.RbSelection.Content = (
                "use the selected chambers (nothing is selected)")
            self.RbSelection.IsEnabled = False
            self.RbType.IsChecked = True

        self.CmbSeed.Items.Clear()
        for n in seed_names:
            self.CmbSeed.Items.Add(n)
        first = pick_seed_name(seed_names, remembered["seed"])
        if first is not None:
            self.CmbSeed.SelectedItem = first
        self.CmbWorkset.Items.Clear()
        self.CmbWorkset.Items.Add(CURRENT_WORKSET)
        for n in workset_names:
            self.CmbWorkset.Items.Add(n)
        if remembered["workset"] in workset_names:
            self.CmbWorkset.SelectedItem = remembered["workset"]
        else:
            self.CmbWorkset.SelectedIndex = 0
        if not workset_names:
            self.CmbWorkset.IsEnabled = False

        if not seed_names:
            self.RbExtScope.Content = (
                "scope box: (no scope box in the project to copy - Revit "
                "cannot create one from nothing)")
            self.RbExtScope.IsEnabled = False
        if remembered["extents"] == EXTENTS_SCOPE and seed_names:
            self.RbExtScope.IsChecked = True
        else:
            self.RbExtCrop.IsChecked = True
        self.TxtW.Text = mm_text(remembered["width"])
        self.TxtD.Text = mm_text(remembered["depth"])
        self.TxtParamX.Text = sizing["px"]
        self.TxtParamY.Text = sizing["py"]
        self.TxtClear.Text = mm_text(sizing["clear"])
        if sizing["mode"] == SIZE_PARAMS:
            self.RbCropParams.IsChecked = True
        else:
            self.RbCropFixed.IsChecked = True
        self.CmbTemplate.Items.Clear()
        for n in template_choices:
            self.CmbTemplate.Items.Add(n)
        if remembered["template"] in template_choices:
            self.CmbTemplate.SelectedItem = remembered["template"]
        else:
            self.CmbTemplate.SelectedIndex = 0

        self._fill_types()
        self._ready = True
        self._sync()

    # -- family type list ----------------------------------------------------
    def _current_type(self):
        try:
            idx = self.LstTypes.SelectedIndex
        except Exception:
            return None
        if idx < 0 or idx >= len(self._visible):
            return None
        return self._types[self._visible[idx]]

    def _fill_types(self):
        query = ""
        try:
            query = self.TxtTypeFilter.Text or ""
        except Exception:
            pass
        keep = CS.filter_labels([d["label"] for d in self._types], query)
        current = None
        cur = self._current_type()
        if cur is not None:
            current = self._types.index(cur)
        self._filling = True
        try:
            self._visible = keep
            self.LstTypes.Items.Clear()
            for i in keep:
                self.LstTypes.Items.Add(self._types[i]["label"])
            if current in keep:
                self.LstTypes.SelectedIndex = keep.index(current)
            elif len(keep) == 1:
                self.LstTypes.SelectedIndex = 0
        finally:
            self._filling = False
        self._fill_chambers(self._current_type())

    def _fill_chambers(self, tdict):
        from System.Windows.Controls import CheckBox
        from System.Windows import Thickness
        self.PnlChambers.Children.Clear()
        self._boxes = []
        rows = []
        for fi in (tdict["insts"] if tdict else []):
            mk = _get_mark(fi)
            rows.append(("{0}   (Id {1})".format(mk if mk else "<no mark>",
                                                  fi.Id.IntegerValue), fi))
        rows.sort(key=lambda r: r[0].lower())
        for label, fi in rows:
            cb = CheckBox()
            cb.Content = label
            cb.IsChecked = True
            cb.Margin = Thickness(0, 2, 0, 2)
            cb.Checked += self._on_box
            cb.Unchecked += self._on_box
            self.PnlChambers.Children.Add(cb)
            self._boxes.append((cb, fi))
        self._sync()

    def _ticked(self):
        return [fi for cb, fi in self._boxes if cb.IsChecked]

    def _set_all(self, on):
        for cb, _fi in self._boxes:
            cb.IsChecked = on
        self._sync()

    def _chambers(self):
        if self.RbSelection.IsChecked and self._sel:
            return list(self._sel)
        return self._ticked()

    def _seed(self):
        try:
            return sb_by_name.get(self.CmbSeed.SelectedItem)
        except Exception:
            return None

    def _read_extents(self):
        # Push the extents controls into the module state the preview and
        # the run read. Returns an error message, or None.
        global extents, crop_size_mode, crop_w_ft, crop_d_ft
        global crop_param_x, crop_param_y, crop_clear_ft
        extents = EXTENTS_SCOPE if self.RbExtScope.IsChecked else EXTENTS_CROP
        if extents == EXTENTS_SCOPE:
            return None
        if self.RbCropParams.IsChecked:
            crop_size_mode = SIZE_PARAMS
            px = (self.TxtParamX.Text or "").strip()
            py = (self.TxtParamY.Text or "").strip()
            if not px or not py:
                return "Give the chamber's parameter names along X and Y."
            clear = _mm0(self.TxtClear.Text)
            if clear is None:
                return "Clearance must be a number of mm (0 or more)."
            crop_param_x, crop_param_y = px, py
            crop_clear_ft = clear / FT
        else:
            crop_size_mode = SIZE_FIXED
            w = parse_mm(self.TxtW.Text)
            d = parse_mm(self.TxtD.Text)
            if w is None or d is None:
                return "Crop size along X and Y must be positive mm."
            crop_w_ft, crop_d_ft = w / FT, d / FT
        return None

    # -- state -> UI -----------------------------------------------------------
    def _sync(self):
        if not getattr(self, "_ready", False):
            return
        try:
            by_type = bool(self.RbType.IsChecked)
            self.PnlType.IsEnabled = by_type
            scope = bool(self.RbExtScope.IsChecked)
            self.PnlScope.IsEnabled = scope
            self.PnlCrop.IsEnabled = not scope
            params = bool(self.RbCropParams.IsChecked)
            self.PnlCropFixed.IsEnabled = not params
            self.PnlCropParams.IsEnabled = params
            if by_type:
                total = len(self._boxes)
                if total:
                    self.TxtChamberCount.Text = (
                        "{0} of {1} chamber(s) ticked.".format(
                            len(self._ticked()), total))
                else:
                    self.TxtChamberCount.Text = (
                        "Pick a chamber family type above.")
            else:
                self.TxtChamberCount.Text = (
                    "Boxes and plans for the {0} selected chamber(s)."
                    .format(len(self._sel)))
            self.StatusText.Text = ""
        except Exception:
            pass
        self._refresh_plan()

    def _refresh_plan(self):
        try:
            err = self._read_extents()
            chambers = self._chambers()
            if not chambers:
                self.TxtPlan.Text = "Pick or tick chambers above."
                return
            if err:
                self.TxtPlan.Text = err
                return
            jobs, no_mark, dups = _build_jobs(chambers, self._seed())
            self.TxtPlan.Text = _plan_text(jobs, no_mark, dups)
        except Exception as ex:
            self.TxtPlan.Text = "(could not preview: {0})".format(ex)

    # -- handlers ----------------------------------------------------------------
    def on_source_changed(self, sender, args):
        self._sync()

    def on_type_filter(self, sender, args):
        if getattr(self, "_ready", False):
            self._fill_types()

    def on_type_selected(self, sender, args):
        if not getattr(self, "_ready", False) or self._filling:
            return
        self._fill_chambers(self._current_type())

    def on_tick_all(self, sender, args):
        self._set_all(True)

    def on_tick_none(self, sender, args):
        self._set_all(False)

    def on_seed_changed(self, sender, args):
        if getattr(self, "_ready", False):
            self._refresh_plan()

    def on_extents(self, sender, args):
        self._sync()

    def on_size_mode(self, sender, args):
        self._sync()

    def _on_box(self, sender, args):
        self._sync()

    def on_go(self, sender, args):
        chambers = self._chambers()
        if not chambers:
            if self.RbType.IsChecked and self._current_type() is None:
                self.StatusText.Text = "Pick a chamber family type."
            else:
                self.StatusText.Text = "Tick at least one chamber."
            return
        err = self._read_extents()
        if err:
            self.StatusText.Text = err
            return
        seed = self._seed() if extents == EXTENTS_SCOPE else None
        if extents == EXTENTS_SCOPE and seed is None:
            self.StatusText.Text = "Pick the seed scope box to copy."
            return
        tmpl = self.CmbTemplate.SelectedItem
        if not tmpl:
            self.StatusText.Text = "Pick a plan view template option."
            return
        jobs, no_mark, dups = _build_jobs(chambers, seed)
        if not jobs:
            self.StatusText.Text = (
                "None of the {0} chamber(s) has a Mark - scope boxes and "
                "plans are named from the instance parameter 'Mark'."
                .format(len(chambers)))
            return
        todo = [j for j in jobs if j["box_state"] in ("create", "misplaced")
                or j["view_state"] == "create"]
        if not todo:
            self.StatusText.Text = (
                "Nothing to do - every chamber with a Mark already has "
                "its plan view" + (" and scope box." if extents ==
                                   EXTENTS_SCOPE else "."))
            return
        if self.RbSelection.IsChecked and self._sel:
            source = "(selection)"
        else:
            source = self._current_type()["label"]
        ws = self.CmbWorkset.SelectedItem
        self.result = {"chambers": chambers, "source": source,
                       "seed": seed, "template": tmpl,
                       "workset": ws if ws in worksets else u""}
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


_settings = load_settings()
sizing = size_settings(_settings)
if _HEADLESS:
    # Options from the pipeline; everything it does not say comes from the
    # values this dialog last remembered.
    _rem = plans_settings(_settings)
    extents = _HEADLESS.get("extents") or _rem["extents"]
    crop_size_mode = _HEADLESS.get("size_mode") or sizing["mode"]
    crop_w_ft = _rem["width"] / FT
    crop_d_ft = _rem["depth"] / FT
    crop_param_x = sizing["px"]
    crop_param_y = sizing["py"]
    crop_clear_ft = sizing["clear"] / FT
    _seed = None
    if extents == EXTENTS_SCOPE:
        _sn = pick_seed_name(seed_names,
                             _HEADLESS.get("seed") or _rem["seed"])
        _seed = sb_by_name.get(_sn) if _sn else None
        if _seed is None:
            extents = EXTENTS_CROP     # nothing to copy: exact crop instead
    _tl = _HEADLESS.get("template") or _rem["template"]
    if _tl not in template_choices:
        _tl = PLANS_TEMPLATE_ACTIVE
    _ws = _HEADLESS.get("workset")
    if _ws is None:
        _ws = _rem["workset"]
    _result = {"chambers": list(_HEADLESS["chambers"]),
               "source": "(pipeline)", "seed": _seed, "template": _tl,
               "workset": _ws if _ws in worksets else u""}
else:
    win = PlansWindow(type_options, sel_insts, plans_settings(_settings))
    win.ShowDialog()
    if not win.result:
        script.exit()
    _result = win.result

seed = _result["seed"]
target_instances = _result["chambers"]
picked_type_label = _result["source"]
tmpl_label = _result["template"]
workset_name = _result.get("workset") or u""
workset_id = worksets.get(workset_name)
if tmpl_label == PLANS_TEMPLATE_ACTIVE:
    tmpl_mode, tmpl_id = "active", None
elif tmpl_label == PLANS_TEMPLATE_NONE:
    tmpl_mode, tmpl_id = "none", None
else:
    tmpl_mode, tmpl_id = "named", plan_templates[tmpl_label].Id

try:
    _settings[SETTINGS_PLANS_TEMPLATE] = tmpl_label
    _settings[SETTINGS_PLANS_EXTENTS] = extents
    _settings[SETTINGS_PLANS_WORKSET] = workset_name
    if seed is not None:
        _settings[SETTINGS_PLANS_SEED] = _elem_name(seed)
    if extents == EXTENTS_CROP:
        _settings[SETTINGS_SIZE_MODE] = crop_size_mode
        if crop_size_mode == SIZE_PARAMS:
            _settings[SETTINGS_SIZE_PARAM_X] = crop_param_x
            _settings[SETTINGS_SIZE_PARAM_Y] = crop_param_y
            _settings[SETTINGS_SIZE_CLEAR] = crop_clear_ft * FT
        else:
            _settings[SETTINGS_PLANS_WIDTH] = crop_w_ft * FT
            _settings[SETTINGS_PLANS_DEPTH] = crop_d_ft * FT
    save_settings(_settings)
except Exception:
    pass

jobs, no_mark, dup_marks = _build_jobs(target_instances, seed)
if not jobs:
    forms.alert("None of the {0} chamber(s) has a Mark.\n\n"
                "Scope boxes and plan views are named from the chamber's "
                "instance parameter 'Mark'. Populate it, then run again."
                .format(len(target_instances)), exitscript=True)


# ---------------------------------------------------------------------------
# 4. Plan-view makers: duplicate of the active plan, or a fresh plan on its
#    level. Both get the box applied and CHECKED; the caller tries the other
#    when one cannot take it.
# ---------------------------------------------------------------------------
plan_vft_by_family = {}      # ViewFamily -> [ViewFamilyType id]
for _vft in FilteredElementCollector(doc).OfClass(ViewFamilyType):
    try:
        plan_vft_by_family.setdefault(_vft.ViewFamily, []).append(_vft.Id)
    except Exception:
        continue


def _duplicate_plan():
    try:
        new_id = view.Duplicate(ViewDuplicateOption.Duplicate)
        nv = doc.GetElement(new_id)
    except Exception as ex:
        return None, "duplicate failed: {0}".format(ex)
    if nv is None:
        return None, "duplicate returned nothing"
    return nv, ""


def _fresh_plan():
    if view.ViewType == ViewType.AreaPlan:
        return None, "area plans cannot be created fresh"
    if src_level is None:
        return None, "the active view has no level to create a plan on"
    candidates = []
    fam = None
    try:
        own = doc.GetElement(view.GetTypeId())
        fam = own.ViewFamily
        candidates.append(own.Id)
    except Exception:
        pass
    if fam is not None:
        for vid in plan_vft_by_family.get(fam, []):
            if vid not in candidates:
                candidates.append(vid)
    # A detail callout has no plan family type of its own: fall back to
    # any Floor Plan type so a plan can still be made on the level.
    for vid in plan_vft_by_family.get(ViewFamily.FloorPlan, []):
        if vid not in candidates:
            candidates.append(vid)
    last = "no plan view family type found"
    for vid in candidates:
        try:
            nv = ViewPlan.Create(doc, vid, src_level.Id)
        except Exception as ex:
            last = "ViewPlan.Create failed: {0}".format(ex)
            continue
        if nv is None:
            continue
        # The plan's look: the chosen template, or the active plan's own
        # template, else its scale and detail level.
        try:
            tid = None
            if tmpl_mode == "named":
                tid = tmpl_id
            elif tmpl_mode == "active":
                tid = view.ViewTemplateId
            if _valid_id(tid):
                nv.ViewTemplateId = tid
            else:
                try:
                    nv.Scale = view.Scale
                except Exception:
                    pass
                try:
                    nv.DetailLevel = view.DetailLevel
                except Exception:
                    pass
        except Exception:
            pass
        # Same north (Project / True) as the active plan, unless locked.
        try:
            src_p = view.get_Parameter(BuiltInParameter.PLAN_VIEW_NORTH)
            dst_p = nv.get_Parameter(BuiltInParameter.PLAN_VIEW_NORTH)
            if (src_p is not None and dst_p is not None
                    and not dst_p.IsReadOnly):
                dst_p.Set(src_p.AsInteger())
        except Exception:
            pass
        return nv, ""
    return None, last


def _delete_quietly(el, name=None):
    try:
        doc.Delete(el.Id)
    except Exception:
        pass
    if name:
        view_names.discard(name)


def _clear_scope_box(v):
    # Take any inherited scope box off a view (a crop can't be set while
    # one is applied). Returns an error text, or None.
    p = _scope_param(v)
    if p is None:
        return None
    try:
        eid = p.AsElementId()
        if not _valid_id(eid):
            return None
        if p.IsReadOnly:
            return "the inherited scope box is locked on this view"
        p.Set(ElementId.InvalidElementId)
    except Exception as ex:
        return "could not take the inherited scope box off: {0}".format(ex)
    return None


def _crop_element_id(v):
    # The view's crop region element, found as the one element that appears
    # when the crop is shown. (Revit exposes no direct handle to it.)
    try:
        was = v.CropBoxVisible
        v.CropBoxVisible = False
        doc.Regenerate()
        before = set(e.IntegerValue for e in
                     FilteredElementCollector(doc, v.Id).ToElementIds())
        v.CropBoxVisible = True
        doc.Regenerate()
        after = set(e.IntegerValue for e in
                    FilteredElementCollector(doc, v.Id).ToElementIds())
        v.CropBoxVisible = was
        diff = after - before
        if len(diff) == 1:
            return ElementId(diff.pop())
    except Exception:
        pass
    return None


def _set_crop(v, centre, phi, half_a, half_b):
    # Crop the plan to half_a x half_b about the chamber centre, its X axis
    # turned to phi (a turned crop region turns the plan view with it).
    # For a plan view Revit ignores the transform on an assigned crop box
    # and reads its extents in the view's OWN frame, so: 1) set the extents
    # in that frame around the chamber, then 2) rotate the crop region
    # element about the chamber by the difference between phi and the
    # frame's current X direction. Returns an error text, or None.
    try:
        v.CropBoxActive = True
        old = v.CropBox
        frame = old.Transform
        z0, z1 = old.Min.Z, old.Max.Z
    except Exception as ex:
        return "could not read the crop: {0}".format(ex)
    try:
        c = frame.Inverse.OfPoint(centre)
    except Exception as ex:
        return "could not place the crop: {0}".format(ex)
    box = BoundingBoxXYZ()
    box.Transform = frame
    box.Min = XYZ(c.X - half_a, c.Y - half_b, z0)
    box.Max = XYZ(c.X + half_a, c.Y + half_b, z1)
    try:
        v.CropBox = box
        v.CropBoxVisible = True
    except Exception as ex:
        return "Revit refused the crop: {0}".format(ex)
    try:
        got = v.CropBox
        gw = got.Max.X - got.Min.X
        gh = got.Max.Y - got.Min.Y
        if abs(gw - 2.0 * half_a) > 0.01 or abs(gh - 2.0 * half_b) > 0.01:
            return ("the view kept a {0:.2f} x {1:.2f} m crop instead of "
                    "{2:.2f} x {3:.2f} m".format(
                        gw * 0.3048, gh * 0.3048, 2.0 * half_a * 0.3048,
                        2.0 * half_b * 0.3048))
    except Exception:
        pass
    # 2) turn the crop region so its X axis lies at phi.
    try:
        bx = frame.BasisX
        current = math.atan2(bx.Y, bx.X)
    except Exception:
        current = 0.0
    turn = wrap_angle(phi - current, 2.0 * math.pi)
    if abs(turn) > 1.0e-6:
        crop_id = _crop_element_id(v)
        if crop_id is None:
            return ("crop set but not turned - the crop region element "
                    "could not be found")
        try:
            axis = Line.CreateBound(
                XYZ(centre.X, centre.Y, centre.Z),
                XYZ(centre.X, centre.Y, centre.Z + 1.0))
            ElementTransformUtils.RotateElement(doc, crop_id, axis, turn)
        except Exception as ex:
            return "crop set but not turned: {0}".format(ex)
    return None


def _make_plan_crop(name, centre, angle, half_x, half_y):
    # (view, how, reasons): a named plan view cropped exactly around the
    # chamber, or None with every attempt's reason. half_x / half_y are in
    # the chamber's own frame; the crop turns with the chamber face nearest
    # 'up' on top, swapping the halves when that is a quarter turn away.
    phi = upright_rotation(angle, up_ref)
    quarter = abs(wrap_angle(phi - angle, math.pi)) > math.pi / 4.0
    half_a, half_b = (half_y, half_x) if quarter else (half_x, half_y)
    methods = [("fresh plan", _fresh_plan), ("duplicate", _duplicate_plan)]
    if not prefer_fresh:
        methods.reverse()
    tried = []
    for label, maker in methods:
        nv, err = maker()
        if nv is None:
            tried.append("{0}: {1}".format(label, err))
            continue
        try:
            nv.Name = name
            view_names.add(name)
        except Exception as ex:
            _delete_quietly(nv)
            tried.append("{0}: rename failed ({1})".format(label, ex))
            continue
        if label == "duplicate":
            try:
                if tmpl_mode == "named":
                    nv.ViewTemplateId = tmpl_id
                elif tmpl_mode == "none":
                    nv.ViewTemplateId = ElementId.InvalidElementId
            except Exception as ex:
                template_notes.append((name, "{0}".format(ex)))
        why = _clear_scope_box(nv) or _set_crop(nv, centre, phi, half_a,
                                                half_b)
        if why is None:
            how = label
            if label == "fresh plan" and src_level is not None:
                how = "fresh plan on '{0}'".format(_elem_name(src_level))
            return nv, how, math.degrees(phi)
        _delete_quietly(nv, name)
        tried.append("{0}: {1}".format(label, why))
    return None, "", "; ".join(tried)


def _make_plan(name, sb):
    # (view, how, reasons): a named plan view with the scope box on it, or
    # None with every attempt's reason.
    methods = [("fresh plan", _fresh_plan), ("duplicate", _duplicate_plan)]
    if not prefer_fresh:
        methods.reverse()
    tried = []
    for label, maker in methods:
        nv, err = maker()
        if nv is None:
            tried.append("{0}: {1}".format(label, err))
            continue
        try:
            nv.Name = name
            view_names.add(name)
        except Exception as ex:
            _delete_quietly(nv)
            tried.append("{0}: rename failed ({1})".format(label, ex))
            continue
        if label == "duplicate":
            # A duplicate inherits the active plan's template; swap it for
            # the chosen one, or take it off, as asked.
            try:
                if tmpl_mode == "named":
                    nv.ViewTemplateId = tmpl_id
                elif tmpl_mode == "none":
                    nv.ViewTemplateId = ElementId.InvalidElementId
            except Exception as ex:
                template_notes.append((name, "{0}".format(ex)))
        ok, note = _apply_scope_box(nv, sb)
        if ok:
            try:
                nv.CropBoxActive = True
                nv.CropBoxVisible = True
            except Exception:
                pass
            how = label
            if label == "fresh plan" and src_level is not None:
                how = "fresh plan on '{0}'".format(_elem_name(src_level))
            if note:
                how += ", " + note
            return nv, how, ""
        _delete_quietly(nv, name)
        tried.append("{0}: {1}".format(label, note))
    return None, "", "; ".join(tried)


# ---------------------------------------------------------------------------
# 5. One transaction: (a) a scope box per chamber, (b) a plan view per box.
# ---------------------------------------------------------------------------
seed_skew = 0.0
seed_h = None
seed_zmin = None
seed_centre = None
if extents == EXTENTS_SCOPE:
    seed_skew = _scopebox_plan_angle(seed)
    if seed_skew is None:
        seed_skew = 0.0
    seed_zmin, seed_zmax = _z_range(seed)
    seed_h = (seed_zmax - seed_zmin) if seed_zmin is not None else None
    seed_centre = _scopebox_centre(seed)
    if seed_centre is None:
        forms.alert("The seed scope box '{0}' has no readable bounding box, "
                    "so copies can't be positioned. Pick another seed."
                    .format(_elem_name(seed)), exitscript=True)

created_sb = 0
moved_sb = 0
existing_sb = 0
created_views = 0
view_failed = 0
template_notes = []       # (view name, why the template was not applied)

t = Transaction(doc, "pyMEP: Chamber plans ({0} chamber(s))".format(
    len(jobs)))
t.Start()
try:
    # --- (a) a scope box per chamber (scope-box route only) ---
    for job in jobs:
        base = job["base"]
        centre = job["centre"]
        if centre is None:
            job["box"] = None
            job["box_note"] = "no centre"
            continue

        if job["box_state"] == "none":
            job["box_note"] = "exact crop (no scope box)"
            continue

        if job["box_state"] == "exists":
            existing_sb += 1
            job["box_note"] = "exists: " + base
            continue

        if job["box_state"] == "misplaced":
            sb = job["box"]
            try:
                sb_c = _scopebox_centre(sb)
                b0, b1 = _z_range(sb)
                dz, znote = _drop(b0, (b1 - b0) if b0 is not None else None,
                                  job["zmin"])
                move = XYZ(centre.X - sb_c.X, centre.Y - sb_c.Y, dz)
                ElementTransformUtils.MoveElement(doc, sb.Id, move)
                moved_sb += 1
                _wsn = _set_workset(sb, workset_id)
                if _wsn:
                    znote += "; " + _wsn
                job["box_note"] = ("moved onto chamber ({0:.1f} m): {1}  "
                                   "({2})".format(
                                       (move.X ** 2 + move.Y ** 2) ** 0.5
                                       * 0.3048, base, znote))
            except Exception as ex:
                job["box"] = None
                job["box_note"] = ("misplaced and the move failed - left "
                                   "alone ({0})".format(ex))
            continue

        pose = _chamber_pose(job["inst"])
        if pose is None:
            job["box_note"] = "no location point"
            continue
        _origin_pt, angle = pose

        # Copy the seed scope box.
        try:
            ids = ElementTransformUtils.CopyElement(
                doc, seed.Id, XYZ(0, 0, 0))
            new_sb = doc.GetElement(list(ids)[0]) if ids else None
        except Exception as ex:
            job["box_note"] = "copy scopebox failed: {0}".format(ex)
            continue
        if new_sb is None:
            job["box_note"] = "copy returned nothing"
            continue

        notes = []

        # Move it so its centre sits on the chamber centre in plan, and as
        # low as this plan's cut plane allows in height. The copy starts
        # exactly on the seed (zero-offset copy), so the seed's centre and
        # bottom are the reliable origins - a just-copied element's own
        # bounding box can read back empty before regeneration.
        try:
            dz, znote = _drop(seed_zmin, seed_h, job["zmin"])
            move = XYZ(centre.X - seed_centre.X,
                       centre.Y - seed_centre.Y,
                       dz)
            ElementTransformUtils.MoveElement(doc, new_sb.Id, move)
            notes.append(znote)
        except Exception as ex:
            notes.append("move failed: {0}".format(ex))

        # Rotate it about the chamber centre so the chamber face nearest
        # 'up' sits at the top of the plan (a rotated scope box turns its
        # plan view with it). The seed's own skew, if any, is taken out.
        phi = upright_rotation(angle, up_ref)
        turn = phi - seed_skew
        try:
            if abs(turn) > 1.0e-6:
                axis = Line.CreateBound(
                    XYZ(centre.X, centre.Y, centre.Z),
                    XYZ(centre.X, centre.Y, centre.Z + 1.0))
                ElementTransformUtils.RotateElement(
                    doc, new_sb.Id, axis, turn)
            notes.append("box at {0:.1f} deg, chamber at {1:.1f} deg".format(
                math.degrees(phi), math.degrees(angle)))
        except Exception as ex:
            notes.append("rotate failed: {0}".format(ex))

        # Rename it to the chamber Mark. If the rename fails, do NOT keep
        # the auto-named copy: it would produce duplicate boxes on re-runs.
        try:
            new_sb.Name = base
        except Exception as ex:
            _delete_quietly(new_sb)
            job["box_note"] = "rename failed - box removed ({0})".format(ex)
            continue

        _wsn = _set_workset(new_sb, workset_id)
        if _wsn:
            notes.append(_wsn)
        job["box"] = new_sb
        created_sb += 1
        note = "created: " + base
        if notes:
            note += "  (" + "; ".join(notes) + ")"
        job["box_note"] = note

    # Make sure the boxes' geometry is current before views use them.
    try:
        doc.Regenerate()
    except Exception:
        pass

    # --- (b) a plan view per chamber ---
    for job in jobs:
        if job["view_state"] == "exists":
            job["view_note"] = "view exists"
            continue
        if extents == EXTENTS_CROP:
            if job["centre"] is None:
                job["view_note"] = "-"
                continue
            pose = _chamber_pose(job["inst"])
            angle = pose[1] if pose is not None else 0.0
            half_x, half_y, dnote = _crop_halves(job["inst"], angle)
            nv, how, extra = _make_plan_crop(job["base"], job["centre"],
                                             angle, half_x, half_y)
            if nv is None:
                job["view_note"] = "NOT created - " + extra
                view_failed += 1
                continue
            created_views += 1
            job["box_note"] = ("exact crop {0:.2f} x {1:.2f} m, turned "
                               "{2:.1f} deg{3}".format(
                                   2.0 * half_x * 0.3048,
                                   2.0 * half_y * 0.3048, extra,
                                   "  -  " + dnote if dnote else ""))
            job["view_note"] = "created" + (
                " ({0})".format(how) if how and how != "duplicate" else "")
            continue
        if job["box"] is None:
            job["view_note"] = "-"
            continue
        nv, how, reasons = _make_plan(job["base"], job["box"])
        if nv is None:
            job["view_note"] = "NOT created - " + reasons
            view_failed += 1
            continue
        created_views += 1
        job["view_note"] = "created" + (
            " ({0})".format(how) if how and how != "duplicate" else "")

    t.Commit()
except Exception as ex:
    t.RollBack()
    forms.alert("Failed, no changes made:\n\n{0}".format(ex), exitscript=True)


# ---------------------------------------------------------------------------
# 6. Report
# ---------------------------------------------------------------------------
out.print_md("### Chamber plans")
out.print_md("**Target:** {0}  |  **Active view:** {1} ({2}{3})".format(
    picked_type_label, _elem_name(view), src_kind,
    ", scope box locked" if src_locked else ""))
if extents == EXTENTS_CROP:
    if crop_size_mode == SIZE_PARAMS:
        _ext_desc = ("exact crop from parameters '{0}' x '{1}' + {2:.0f} mm "
                     "clearance each side, no scope boxes".format(
                         crop_param_x, crop_param_y, crop_clear_ft * FT))
    else:
        _ext_desc = ("exact crop {0:.0f} x {1:.0f} mm, no scope boxes".format(
            crop_w_ft * FT, crop_d_ft * FT))
else:
    _ext_desc = "scope box copied from '{0}'{1}".format(
        _elem_name(seed),
        ", workset '{0}'".format(workset_name) if workset_name else "")
out.print_md("**Plan extents:** {0}  |  **Plan template:** {1}".format(
    _ext_desc, tmpl_label))
if template_notes:
    out.print_md("**{0} plan view(s) did not take the template:**".format(
        len(template_notes)))
    for nm, why in template_notes:
        out.print_md("- {0}: {1}".format(nm, why))
if extents == EXTENTS_CROP:
    pass
elif cut_z is not None and seed_h:
    out.print_md("**Box height:** seed '{0}' is {1:.1f} m tall; this plan's "
                 "cut plane must pass through every box, so each box sits "
                 "as low as that allows (500 mm under the chamber when the "
                 "seed reaches).".format(_elem_name(seed), seed_h * 0.3048))
elif cut_z is None:
    out.print_md("**Box height:** this plan's cut plane could not be read, "
                 "so boxes keep the seed's height.")
out.print_md("**Up reference for box rotation:** {0}{1}".format(
    up_label,
    "  |  seed box skew of {0:.1f} deg taken out".format(
        math.degrees(seed_skew)) if abs(seed_skew) > math.radians(0.05)
    else ""))
out.print_md("**Scope boxes created:** {0}  |  **Moved onto chamber:** {1}  |  "
             "**Already in place:** {2}  |  **Plan views created:** {3}  |  "
             "**Plan views failed:** {4}  |  **Skipped (blank Mark):** {5}"
             .format(created_sb, moved_sb, existing_sb, created_views,
                     view_failed, len(no_mark)))
if view_failed:
    out.print_md("**{0} plan view(s) could not take their scope box** - each "
                 "row says what was tried. 'read-only' means that kind of "
                 "view cannot carry a scope box (callouts, dependents, "
                 "assembly views); 'refused' means the box is not visible "
                 "in this plan, so its vertical extent must be stretched to "
                 "cover this view's level. New boxes keep the SEED box's "
                 "vertical extent.".format(view_failed))

rows = []
for job in jobs:
    rows.append([job["mark"], job["box_note"], job["view_note"]])
for mark, inst in dup_marks:
    rows.append([mark, "duplicate Mark - shares the box above",
                 "shares the plan above"])
for ident, _inst in no_mark:
    rows.append([ident, "SKIPPED - blank Mark", "-"])
out.print_table(table_data=rows,
                columns=["Chamber", "Scope box", "Plan view"])

if _HEADLESS:
    _PIPE["out_plans"] = {
        "created": created_views, "failed": view_failed,
        "existing": sum(1 for j in jobs if j["view_state"] == "exists"),
        "no_mark": len(no_mark),
        "names": [j["base"] for j in jobs],
    }

# Keep the output window open.
