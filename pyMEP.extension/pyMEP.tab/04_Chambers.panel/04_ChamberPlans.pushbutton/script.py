# -*- coding: utf-8 -*-
"""Chamber Plans - one button for chamber scope boxes AND their plan views.

For each chamber (current selection, or a family type you pick):
  0. NAME = the chamber's KEY: its instance parameter "Mark" up to the first
     slash (LV1 for a Mark LV1/Z1 - the "/Z1" is a zone guide and never
     reaches a name). Nothing else is read. A chamber with a blank Mark is
     NOT processed - it is listed as skipped in the preview and the report
     so it can be populated and re-run.
  1. ENSURE a scope box named after the Mark: if one exists and sits over
     the chamber in plan it is used as is; if it exists but sits somewhere
     else (a copy that never got moved, a chamber that moved) it is MOVED
     over the chamber in plan; otherwise the seed scope box is copied,
     moved IN PLAN to the chamber centre (it keeps the seed's vertical
     extent, so it stays visible in this plan view), rotated to the
     chamber's rotation and renamed.
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

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (
    Transaction, ViewType, View, ViewDuplicateOption, ViewPlan,
    ViewFamilyType, ViewFamily, XYZ, Line, ElementTransformUtils, ElementId,
    FilteredElementCollector, FamilyInstance, BuiltInParameter,
    BuiltInCategory, Element,
)

from pyrevit import revit, forms, script

# Chamber names use the Mark's KEY (before any '/zone' tail).
from pymep_chamber_sections import chamber_key

doc = revit.doc
uidoc = revit.uidoc
view = doc.ActiveView
out = script.get_output()

SEED_PREFERRED_NAME = "sample_scope_box"


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
# 1. Find a seed scope box to copy (API cannot create one from nothing)
# ---------------------------------------------------------------------------
scope_boxes = []
for el in FilteredElementCollector(doc)\
        .OfCategory(BuiltInCategory.OST_VolumeOfInterest)\
        .WhereElementIsNotElementType():
    scope_boxes.append(el)

if not scope_boxes:
    forms.alert("No scope box found in the project.\n\n"
                "Revit's API cannot create a scope box from nothing - it can "
                "only copy an existing one.\n\n"
                "Please create ONE scope box anywhere (any size), then run "
                "this again. The tool will copy it for each chamber and "
                "position, rotate and rename each copy.", exitscript=True)

seed_label_to_el = {}
for sb in scope_boxes:
    seed_label_to_el[_elem_name(sb)] = sb

# Prefer a seed named exactly "sample_scope_box" if present (case-insensitive),
# so repeated runs always use the template and not a previously-created box.
seed = None
for nm, sb in seed_label_to_el.items():
    if nm.strip().lower() == SEED_PREFERRED_NAME:
        seed = sb
        break

if seed is None:
    if len(scope_boxes) == 1:
        seed = scope_boxes[0]
    else:
        pick = forms.SelectFromList.show(
            sorted(seed_label_to_el.keys(), key=lambda s: s.lower()),
            title="No 'sample_scope_box' found - pick a SEED scope box to copy",
            button_name="Use this seed",
            multiselect=False)
        if not pick:
            script.exit()
        seed = seed_label_to_el[pick]


# ---------------------------------------------------------------------------
# 2. Target chambers: selection (ask) or batch by family type
# ---------------------------------------------------------------------------
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
use_selection = False
if sel_insts:
    use_selection = forms.alert(
        "{0} family instance(s) are selected.\n\n"
        "Create scope boxes + plans for the SELECTED chambers?\n\n"
        "Yes = use selection.  No = pick a family type instead.".format(
            len(sel_insts)),
        yes=True, no=True)

target_instances = []
picked_type_label = ""
if use_selection:
    target_instances = sel_insts
    picked_type_label = "(selection)"
else:
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
        })
    type_options.sort(key=lambda d: d["label"].lower())

    picked = forms.SelectFromList.show(
        [d["label"] for d in type_options],
        title="Select chamber family TYPE (type to search)",
        button_name="Use this family type",
        multiselect=False)
    if not picked:
        script.exit()
    for d in type_options:
        if d["label"] == picked:
            target_instances = inst_by_typeid[d["typeid"]]
            picked_type_label = picked
            break

if not target_instances:
    forms.alert("No chambers to process.", exitscript=True)


# ---------------------------------------------------------------------------
# 3. Jobs: one per chamber Mark. Chambers without a Mark are NOT processed.
# ---------------------------------------------------------------------------
no_mark = []             # (ident, instance)
marked_instances = []
for inst in target_instances:
    if _get_mark(inst):
        marked_instances.append(inst)
    else:
        no_mark.append(("Id {0} ({1})".format(
            inst.Id.IntegerValue, _elem_name(inst)), inst))
if not marked_instances:
    forms.alert("None of the {0} chamber(s) has a Mark.\n\n"
                "Scope boxes and plan views are named from the chamber's "
                "instance parameter 'Mark'. Populate it, then run again."
                .format(len(target_instances)), exitscript=True)
target_instances = marked_instances

sb_by_name = {}
for sb in scope_boxes:
    sb_by_name.setdefault(_elem_name(sb), sb)

view_names = set()
for v in FilteredElementCollector(doc).OfClass(View):
    try:
        view_names.add(v.Name)
    except Exception:
        pass

jobs = []                # one per distinct Mark
dup_marks = []           # (mark, instance) sharing a Mark with an earlier job
seen_bases = set()
for inst in target_instances:
    mark = _get_mark(inst)
    base = _sanitize(chamber_key(mark))   # KEY: Mark before any '/zone'
    if base in seen_bases:
        dup_marks.append((mark, inst))
        continue
    seen_bases.add(base)
    job = {"inst": inst, "mark": mark, "base": base,
           "centre": _world_centre(inst), "box": None, "box_note": "-",
           "view_note": "-"}
    sb = sb_by_name.get(base)
    if sb is None or sb.Id.IntegerValue == seed.Id.IntegerValue:
        job["box_state"] = "create"
    else:
        job["box"] = sb
        over = _box_over_point(sb, job["centre"])
        job["box_state"] = "misplaced" if over is False else "exists"
    job["view_state"] = "exists" if base in view_names else "create"
    jobs.append(job)

planned_boxes = [j["base"] for j in jobs if j["box_state"] == "create"]
moving_boxes = [j["base"] for j in jobs if j["box_state"] == "misplaced"]
planned_views = [j["base"] for j in jobs if j["view_state"] == "create"]

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


# ---------------------------------------------------------------------------
# 3b. Preview + confirm BEFORE anything is created.
# ---------------------------------------------------------------------------
if not planned_boxes and not planned_views and not moving_boxes:
    extra = ""
    if no_mark:
        extra += ("\n\n{0} chamber(s) were skipped because their Mark is "
                  "blank.".format(len(no_mark)))
    forms.alert("Nothing to do.\n\n"
                "Every chamber with a Mark already has a scope box over it "
                "and a plan view of the same name." + extra,
                exitscript=True)


def _name_list(names, cap=15):
    lines = ["  - " + n for n in names[:cap]]
    if len(names) > cap:
        lines.append("  ... and {0} more".format(len(names) - cap))
    return lines

_msg = ["This will create:", ""]
_msg.append("Scope boxes: {0}".format(len(planned_boxes)))
_msg.extend(_name_list(planned_boxes))
if moving_boxes:
    _msg.append("")
    _msg.append("Existing scope boxes MOVED onto their chamber: {0}".format(
        len(moving_boxes)))
    _msg.extend(_name_list(moving_boxes))
_msg.append("")
_msg.append("Plan views: {0}".format(len(planned_views)))
_msg.extend(_name_list(planned_views))
if prefer_fresh:
    _msg.append("")
    _msg.append("The active view is a {0}, which cannot carry a scope box, "
                "so the plan views are created FRESH on level '{1}' (with "
                "the active view's template) instead of duplicated.".format(
                    src_kind,
                    _elem_name(src_level) if src_level is not None else "?"))
if no_mark:
    _msg.append("")
    _msg.append("SKIPPED - blank Mark (populate it and re-run): {0}".format(
        len(no_mark)))
    _msg.extend(_name_list([ident for ident, _i in no_mark]))
if dup_marks:
    _msg.append("")
    _msg.append("Duplicate Marks (one box + plan per Mark): {0}".format(
        len(dup_marks)))
    _msg.extend(_name_list(sorted(set(m for m, _i in dup_marks))))
_msg.append("")
_msg.append("Proceed?")
if not forms.alert("\n".join(_msg), yes=True, no=True):
    script.exit()


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
        # Carry the active plan's look across: its template, else scale
        # and detail level.
        try:
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
        return nv, ""
    return None, last


def _delete_quietly(el, name=None):
    try:
        doc.Delete(el.Id)
    except Exception:
        pass
    if name:
        view_names.discard(name)


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
seed_centre = _scopebox_centre(seed)
if seed_centre is None:
    forms.alert("The seed scope box '{0}' has no readable bounding box, so "
                "copies can't be positioned. Pick another seed.".format(
                    _elem_name(seed)), exitscript=True)

created_sb = 0
moved_sb = 0
existing_sb = 0
created_views = 0
view_failed = 0

t = Transaction(doc, "pyMEP: Chamber plans ({0} chamber(s))".format(
    len(jobs)))
t.Start()
try:
    # --- (a) a scope box per chamber ---
    for job in jobs:
        base = job["base"]
        centre = job["centre"]
        if centre is None:
            job["box"] = None
            job["box_note"] = "no centre"
            continue

        if job["box_state"] == "exists":
            existing_sb += 1
            job["box_note"] = "exists: " + base
            continue

        if job["box_state"] == "misplaced":
            sb = job["box"]
            try:
                sb_c = _scopebox_centre(sb)
                move = XYZ(centre.X - sb_c.X, centre.Y - sb_c.Y, 0.0)
                ElementTransformUtils.MoveElement(doc, sb.Id, move)
                moved_sb += 1
                job["box_note"] = "moved onto chamber ({0:.1f} m): {1}".format(
                    (move.X ** 2 + move.Y ** 2) ** 0.5 * 0.3048, base)
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

        # Move it IN PLAN so its centre sits on the chamber centre. The
        # copy starts exactly on the seed (zero-offset copy), so the seed's
        # centre is the reliable origin - a just-copied element's own
        # bounding box can read back empty before regeneration. Z is left
        # alone on purpose: the box keeps the seed's vertical extent, so it
        # stays visible in this plan and can be applied to the views.
        try:
            move = XYZ(centre.X - seed_centre.X,
                       centre.Y - seed_centre.Y,
                       0.0)
            ElementTransformUtils.MoveElement(doc, new_sb.Id, move)
        except Exception as ex:
            notes.append("move failed: {0}".format(ex))

        # Rotate it to the chamber angle about the chamber centre.
        try:
            if abs(angle) > 1.0e-6:
                axis = Line.CreateBound(
                    XYZ(centre.X, centre.Y, centre.Z),
                    XYZ(centre.X, centre.Y, centre.Z + 1.0))
                ElementTransformUtils.RotateElement(
                    doc, new_sb.Id, axis, angle)
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

    # --- (b) a plan view per box ---
    for job in jobs:
        if job["box"] is None:
            job["view_note"] = "-"
            continue
        if job["view_state"] == "exists":
            job["view_note"] = "view exists"
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
out.print_md("**Target:** {0}  |  **Seed scope box:** {1}  |  "
             "**Active view:** {2} ({3}{4})".format(
                 picked_type_label, _elem_name(seed), _elem_name(view),
                 src_kind, ", scope box locked" if src_locked else ""))
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

# Keep the output window open.
