# -*- coding: utf-8 -*-
"""Chamber Plans - one button for chamber scope boxes AND their plan views.

For each chamber (current selection, or a family type you pick):
  1. ENSURE a scope box: if a scope box named after the chamber's Mark
     already exists, the chamber is skipped; otherwise the seed scope box is
     copied, moved to the chamber centre, rotated to the chamber's rotation
     and renamed. Scope boxes are named by the chamber Mark only (the
     ElementId is used if the chamber has no Mark).
  2. Then EVERY chamber scope box in the project (excluding the seed) that
     has no plan view of the same name gets one: the active plan is
     duplicated, renamed to the scope box name and the scope box applied.
     This backfills plans for boxes made in earlier runs too.

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
    Transaction, ViewType, View, ViewDuplicateOption,
    XYZ, Line, ElementTransformUtils, ElementId,
    FilteredElementCollector, FamilyInstance, BuiltInParameter,
    BuiltInCategory, Element,
)

from pyrevit import revit, forms, script

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
    p = inst.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
    if p is not None:
        v = p.AsString()
        if v:
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
# 3. Existing names: scope boxes (all) + chamber boxes eligible for plans.
#    Backfill is restricted to boxes whose name matches the (sanitized) Mark
#    of an instance of the chosen family type - collected over ALL placed
#    instances of that type, not just this run's chambers, so boxes made in
#    earlier runs still backfill but unrelated scope boxes are never touched.
# ---------------------------------------------------------------------------
target_typeids = set()
for inst in target_instances:
    try:
        tid = inst.GetTypeId()
        if tid is not None and tid != ElementId.InvalidElementId:
            target_typeids.add(tid.IntegerValue)
    except Exception:
        pass

allowed_box_names = set()
for fi in FilteredElementCollector(doc).OfClass(FamilyInstance)\
        .WhereElementIsNotElementType().ToElements():
    try:
        tid = fi.GetTypeId()
        if tid is None or tid.IntegerValue not in target_typeids:
            continue
    except Exception:
        continue
    mk = _get_mark(fi)
    allowed_box_names.add(_sanitize(mk if mk else "Id{0}".format(
        fi.Id.IntegerValue)))

sb_names = set()
boxes_for_plans = {}     # scope box name -> element
for sb in scope_boxes:
    nm = _elem_name(sb)
    sb_names.add(nm)
    if sb.Id.IntegerValue == seed.Id.IntegerValue:
        continue
    if nm.strip().lower() == SEED_PREFERRED_NAME:
        continue
    if nm not in allowed_box_names:
        continue     # not a chamber box of the chosen type - never backfill
    boxes_for_plans[nm] = sb

view_names = set()
for v in FilteredElementCollector(doc).OfClass(View):
    try:
        view_names.add(v.Name)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 3b. Preview + confirm BEFORE anything is created.
# ---------------------------------------------------------------------------
planned_boxes = []       # scope boxes this run will create (by name)
for inst in target_instances:
    mk = _get_mark(inst)
    base = _sanitize(mk if mk else "Id{0}".format(inst.Id.IntegerValue))
    if base not in sb_names and base not in planned_boxes:
        planned_boxes.append(base)

planned_views = []       # plan views this run will create (by name)
for nm in sorted(set(list(boxes_for_plans.keys()) + planned_boxes),
                 key=lambda s: s.lower()):
    if nm not in view_names:
        planned_views.append(nm)

if not planned_boxes and not planned_views:
    forms.alert("Nothing to do.\n\n"
                "Every chamber of this type already has a scope box, and "
                "every chamber scope box already has a plan view.",
                exitscript=True)


def _name_list(names, cap=15):
    lines = ["  - " + n for n in names[:cap]]
    if len(names) > cap:
        lines.append("  ... and {0} more".format(len(names) - cap))
    return lines

_msg = ["This will create:", ""]
_msg.append("Scope boxes: {0}".format(len(planned_boxes)))
_msg.extend(_name_list(planned_boxes))
_msg.append("")
_msg.append("Plan views: {0}".format(len(planned_views)))
_msg.extend(_name_list(planned_views))
_msg.append("")
_msg.append("Proceed?")
if not forms.alert("\n".join(_msg), yes=True, no=True):
    script.exit()


# ---------------------------------------------------------------------------
# 4. One transaction: (a) ensure a scope box per chamber, then (b) create a
#    plan view for every chamber scope box that lacks one (by name).
# ---------------------------------------------------------------------------
created_sb = 0
existing_sb = 0
created_views = 0
chamber_entries = []     # (ident, box name or None, scope box note)
plan_status = {}         # scope box name -> plan view result text

t = Transaction(doc, "pyMEP: Chamber plans ({0} chamber(s))".format(
    len(target_instances)))
t.Start()
try:
    # --- (a) Ensure a scope box per chamber ---
    for inst in target_instances:
        mark = _get_mark(inst)
        ident = mark if mark else "Id {0}".format(inst.Id.IntegerValue)
        base = _sanitize(mark if mark else "Id{0}".format(
            inst.Id.IntegerValue))

        # Skip chambers that already have a matching box by Mark name.
        if base in sb_names:
            existing_sb += 1
            chamber_entries.append(
                (ident, base if base in boxes_for_plans else None,
                 "already exists: " + base))
            continue

        pose = _chamber_pose(inst)
        if pose is None:
            chamber_entries.append((ident, None, "no location point"))
            continue
        _origin_pt, angle = pose

        centre = _world_centre(inst)
        if centre is None:
            chamber_entries.append((ident, None, "no centre"))
            continue

        # Copy the seed scope box.
        try:
            ids = ElementTransformUtils.CopyElement(
                doc, seed.Id, XYZ(0, 0, 0))
            new_sb = doc.GetElement(list(ids)[0]) if ids else None
        except Exception as ex:
            chamber_entries.append(
                (ident, None, "copy scopebox failed: {0}".format(ex)))
            continue
        if new_sb is None:
            chamber_entries.append((ident, None, "copy returned nothing"))
            continue

        notes = []

        # Move it so its centre sits on the chamber centre.
        try:
            sb_c = _scopebox_centre(new_sb)
            if sb_c is not None:
                move = XYZ(centre.X - sb_c.X,
                           centre.Y - sb_c.Y,
                           centre.Z - sb_c.Z)
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
        # the auto-named copy: it would produce un-previewed plan views and
        # duplicate boxes on re-runs. Delete it inside this transaction and
        # skip registration entirely.
        try:
            new_sb.Name = base
        except Exception as ex:
            try:
                doc.Delete(new_sb.Id)
            except Exception:
                pass
            chamber_entries.append(
                (ident, None,
                 "rename failed - box removed ({0})".format(ex)))
            continue

        sb_names.add(base)
        boxes_for_plans[base] = new_sb
        created_sb += 1
        note = "created: " + base
        if notes:
            note += "  (" + "; ".join(notes) + ")"
        chamber_entries.append((ident, base, note))

    # --- (b) Backfill: a plan view for every chamber scope box lacking one ---
    for nm in sorted(boxes_for_plans.keys(), key=lambda s: s.lower()):
        sb = boxes_for_plans[nm]
        target_name = _sanitize(nm)
        if target_name in view_names:
            plan_status[nm] = "view exists"
            continue

        try:
            new_id = view.Duplicate(ViewDuplicateOption.Duplicate)
            new_view = doc.GetElement(new_id)
        except Exception as ex:
            plan_status[nm] = "duplicate failed: {0}".format(ex)
            continue
        if new_view is None:
            plan_status[nm] = "duplicate returned null"
            continue

        try:
            new_view.Name = target_name
            view_names.add(target_name)
        except Exception as ex:
            # Never keep a stray 'Copy of ...' view - it breaks idempotency
            # and piles up on re-runs. Remove it inside this transaction.
            try:
                doc.Delete(new_view.Id)
            except Exception:
                pass
            plan_status[nm] = "rename failed - view removed ({0})".format(ex)
            continue

        # Apply the scope box to the view (crops + orients to it).
        try:
            p = new_view.get_Parameter(
                BuiltInParameter.VIEWER_VOLUME_OF_INTEREST_CROP)
            if p is not None and not p.IsReadOnly:
                p.Set(sb.Id)
            new_view.CropBoxActive = True
            new_view.CropBoxVisible = True
            plan_status[nm] = "created"
            created_views += 1
        except Exception as ex:
            plan_status[nm] = "apply scope box failed: {0}".format(ex)

    t.Commit()
except Exception as ex:
    t.RollBack()
    forms.alert("Failed, no changes made:\n\n{0}".format(ex), exitscript=True)


# ---------------------------------------------------------------------------
# 5. Report (one table: chambers first, then backfilled boxes)
# ---------------------------------------------------------------------------
out.print_md("### Chamber plans")
out.print_md("**Target:** {0}  |  **Seed scope box:** {1}".format(
    picked_type_label, _elem_name(seed)))
out.print_md("**Scope boxes created:** {0}  |  **Already existed:** {1}  |  "
             "**Plan views created:** {2}".format(
                 created_sb, existing_sb, created_views))

rows = []
covered = set()
for ident, box_name, note in chamber_entries:
    if box_name is not None:
        covered.add(box_name)
        vnote = plan_status.get(box_name, "-")
    else:
        vnote = "-"
    rows.append([ident, note, vnote])
for nm in sorted(boxes_for_plans.keys(), key=lambda s: s.lower()):
    if nm in covered:
        continue
    rows.append(["(backfill)", nm, plan_status.get(nm, "-")])
out.print_table(table_data=rows,
                columns=["Chamber", "Scope box", "Plan view"])

# Keep the output window open.
