# -*- coding: utf-8 -*-
"""Create Chamber Sections - pick one or more chamber family instances and build
FOUR section views around each (one per side), each looking inward toward the
chamber centre and aligned to the chamber's rotation.

For each chosen chamber:
  * Four sections are created on the chamber's four LOCAL sides (so they follow
    the chamber's plan rotation): +X, +Y, -X, -Y.
  * Each section plane sits `offset` (mm) out from the chamber centre and looks
    back through the chamber.
  * The cut box width spans the chamber footprint (+ a margin); the height and
    depth are the values you give (height centred on the chamber centre
    elevation, depth measured from the section plane inward).
  * Each section view is named "{Mark} SIDE A", "{Mark} SIDE B", "{Mark} SIDE C",
    "{Mark} SIDE D" from the chamber's Mark. If the chamber has no Mark, the
    ElementId is used as the stem. (This matches the naming Match Sections
    produces for manually-drawn sections.)
  * Each section's placement relative to its chamber is stored automatically
    (the same association records Match Sections saves), so Update Positions
    can re-place the sections after the chamber moves or rotates. No separate
    associate step is needed.

Prompts:
  1. The chamber family TYPE (searchable) - if exactly one instance of that type
     exists it is used; otherwise you tick the chambers to section by Mark
     (multi-select). If chambers are pre-selected you are offered the selection
     instead.
  2. Offset from the centre point to each section plane, in mm.
  3. Section height, in mm.
  4. Section depth (total view depth / far clip from the plane), in mm.
  5. Section view type: first whether all four sides use the same type, then
     either one type for all or one per side (A, B, C, D).

A Section ViewFamilyType must exist in the project (every template has one).

IronPython 2.7: pure ASCII, no f-strings, LF endings.
"""

__title__  = "Create\nChamber Sections"
__author__ = "Glent Group"

import math
import sys

# Reload pymep_* lib modules so the script picks up the latest helpers.
for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (
    Transaction, XYZ, Transform, BoundingBoxXYZ,
    FilteredElementCollector, FamilyInstance, ViewFamilyType,
    ViewFamily, ViewSection, View, BuiltInParameter, ElementId, Element,
    LocationPoint,
)

from pyrevit import revit, forms, script

doc = revit.doc
uidoc = revit.uidoc
out = script.get_output()

MM_PER_FOOT = 304.8
SIDE_LETTERS = ("A", "B", "C", "D")

# Local outward directions for the four sides, BEFORE the chamber's rotation is
# applied. Index lines up with SIDE_LETTERS: A=+X, B=+Y, C=-X, D=-Y.
SIDE_OUTWARD = ((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0))


# ---------------------------------------------------------------------------
# Helpers (mirrors the other Chamber Sections buttons)
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
    # (origin_xyz, angle_rad) for a point-based family instance.
    loc = inst.Location
    if not isinstance(loc, LocationPoint):
        return None
    pt = loc.Point
    ang = 0.0
    try:
        ang = loc.Rotation
    except Exception:
        ang = 0.0
    return pt, ang


def _world_centre(inst):
    # World centre of the chamber from its model bounding box; falls back to the
    # location point. Used for the section box height anchor and look target.
    bb = None
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
    # Half-width / half-depth of the chamber footprint measured in the chamber's
    # LOCAL XY frame (so a rotated chamber gives its true cross-section width).
    # Returns (half_local_x_ft, half_local_y_ft). Falls back to a 1 m default if
    # no bounding box is available.
    bb = None
    try:
        bb = inst.get_BoundingBox(None)
    except Exception:
        bb = None
    if bb is None:
        return (0.5 / 0.3048, 0.5 / 0.3048)  # ~0.5 m half-span fallback

    centre = _world_centre(inst)
    ca = math.cos(-angle)
    sa = math.sin(-angle)
    max_lx = 0.0
    max_ly = 0.0
    # Project all 8 corners of the world AABB into the chamber-local frame and
    # take the extents. (The AABB is axis-aligned in world, so for a rotated
    # chamber this is a slight over-estimate, which is fine - we add margin.)
    xs = (bb.Min.X, bb.Max.X)
    ys = (bb.Min.Y, bb.Max.Y)
    zs = (bb.Min.Z, bb.Max.Z)
    for x in xs:
        for y in ys:
            for _z in zs:
                dx = x - centre.X
                dy = y - centre.Y
                lx = dx * ca - dy * sa
                ly = dx * sa + dy * ca
                if abs(lx) > max_lx:
                    max_lx = abs(lx)
                if abs(ly) > max_ly:
                    max_ly = abs(ly)
    return (max_lx, max_ly)


def _unique_name(base, used):
    if base not in used:
        return base
    i = 2
    while True:
        cand = base + "_" + str(i)
        if cand not in used:
            return cand
        i += 1


def _sanitize(name):
    # Same transform Chamber Plans uses: strip Revit-forbidden name characters
    # so a Mark like 'K1:2' still yields a legal view name.
    bad = "\\:{}[]|;<>?`~"
    return "".join("_" if ch in bad else ch for ch in name).strip()


def _ask_mm(prompt, title, default):
    s = forms.ask_for_string(default=default, prompt=prompt, title=title)
    if s is None:
        script.exit()
    s = s.strip()
    try:
        return float(s)
    except Exception:
        forms.alert("Enter a number in mm.", exitscript=True)


# ---------------------------------------------------------------------------
# 1. Collect the Section ViewFamilyTypes (CreateSection needs one; the user
#    picks which after the offset/height prompts).
# ---------------------------------------------------------------------------
section_vfts = []
for vft in FilteredElementCollector(doc).OfClass(ViewFamilyType):
    try:
        if vft.ViewFamily == ViewFamily.Section:
            section_vfts.append(vft)
    except Exception:
        continue

if not section_vfts:
    forms.alert("No Section view type found in this project.\n\n"
                "Add a Section view family type, then run again.",
                exitscript=True)


# ---------------------------------------------------------------------------
# 2. Pick the target chamber: selection (ask) or a single instance of a type
# ---------------------------------------------------------------------------
def _selected_point_instances():
    out_list = []
    try:
        ids = uidoc.Selection.GetElementIds()
    except Exception:
        ids = []
    for eid in ids:
        el = doc.GetElement(eid)
        if isinstance(el, FamilyInstance) and isinstance(el.Location,
                                                         LocationPoint):
            out_list.append(el)
    return out_list


# Index every placed point-based instance by its type id (for the type picker).
inst_by_typeid = {}
sym_by_typeid = {}
for fi in FilteredElementCollector(doc).OfClass(FamilyInstance)\
        .WhereElementIsNotElementType().ToElements():
    if not isinstance(fi.Location, LocationPoint):
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

sel_insts = _selected_point_instances()
target_chambers = []
picked_type_label = ""

if sel_insts:
    use_sel = forms.alert(
        "{0} family instance(s) selected.\n\n"
        "Create sections around the SELECTED chamber(s)?\n\n"
        "Yes = use selection.  No = pick a family type instead.".format(
            len(sel_insts)),
        yes=True, no=True)
    if use_sel:
        target_chambers = sel_insts
        if len(sel_insts) == 1:
            sym = doc.GetElement(sel_insts[0].GetTypeId())
            picked_type_label = _type_label(sym) if sym is not None \
                else "(selection)"
        else:
            picked_type_label = "(selection)"

if not target_chambers:
    # Pick a family TYPE (searchable), then resolve to one or more instances.
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

    typeid = None
    for d in type_options:
        if d["label"] == picked:
            typeid = d["typeid"]
            picked_type_label = picked
            break

    insts = inst_by_typeid.get(typeid, [])
    if len(insts) == 1:
        target_chambers = [insts[0]]
    else:
        # More than one instance: TICK the chambers to section (multi-select).
        # Labels are made unique (Mark + ElementId) so duplicate/blank Marks
        # still map each tick to one specific instance.
        mark_options = []
        for fi in insts:
            mk = _get_mark(fi)
            base = mk if mk else "<no mark>"
            label = "{0}   (Id {1})".format(base, fi.Id.IntegerValue)
            mark_options.append({"label": label, "inst": fi})
        mark_options.sort(key=lambda d: d["label"].lower())
        picked_marks = forms.SelectFromList.show(
            [d["label"] for d in mark_options],
            title="Tick the chamber(s) to section",
            button_name="Create sections for ticked chambers",
            multiselect=True)
        if not picked_marks:
            script.exit()
        if not isinstance(picked_marks, list):
            picked_marks = [picked_marks]
        chosen = set(picked_marks)
        for d in mark_options:
            if d["label"] in chosen:
                target_chambers.append(d["inst"])

if not target_chambers:
    forms.alert("No chamber selected.", exitscript=True)


# ---------------------------------------------------------------------------
# 3. Prompts: offset, height and depth (mm)
# ---------------------------------------------------------------------------
offset_mm = _ask_mm(
    "Offset from the chamber CENTRE to each section plane, in mm.\n"
    "(How far out from the centre each of the four sections sits.)",
    "Section offset", "1500")
height_mm = _ask_mm(
    "Section HEIGHT, in mm.\n"
    "(Total vertical extent of each section, centred on the chamber centre.)",
    "Section height", "3000")
depth_mm = _ask_mm(
    "Section DEPTH, in mm.\n"
    "(Total view depth / far clip, measured from the section plane inward.\n"
    "Make it larger than the offset so the cut reaches through the chamber.)",
    "Section depth", "3000")

offset_ft = offset_mm / MM_PER_FOOT
height_ft = height_mm / MM_PER_FOOT
depth_ft = depth_mm / MM_PER_FOOT


# ---------------------------------------------------------------------------
# 3b. Prompt: which Section type(s) to create the views with (last prompt).
#     First ask whether all four sides use the same type; if not, pick one per
#     side (A, B, C, D). Always shown, even when only one Section type exists.
# ---------------------------------------------------------------------------
vft_options = []
for vft in section_vfts:
    vft_options.append({"label": _elem_name(vft), "vft": vft})
vft_options.sort(key=lambda d: d["label"].lower())

vft_labels = [d["label"] for d in vft_options]
vft_by_label = {}
for d in vft_options:
    vft_by_label[d["label"]] = d["vft"]


def _pick_section_type(title):
    picked = forms.SelectFromList.show(
        vft_labels,
        title=title,
        button_name="Use this section type",
        multiselect=False)
    if not picked:
        script.exit()
    return vft_by_label.get(picked)


same_for_all = forms.alert(
    "Use the SAME section type for all four sides?\n\n"
    "Yes = pick one type for A, B, C and D.\n"
    "No  = pick a section type per side.",
    yes=True, no=True)

# side_vfts maps each side letter (A/B/C/D) to the chosen ViewFamilyType.
side_vfts = {}
if same_for_all:
    chosen = _pick_section_type("Select the SECTION type for ALL sides")
    if chosen is None:
        forms.alert("No section type selected.", exitscript=True)
    for letter in SIDE_LETTERS:
        side_vfts[letter] = chosen
else:
    for letter in SIDE_LETTERS:
        chosen = _pick_section_type(
            "Select the SECTION type for SIDE {0}".format(letter))
        if chosen is None:
            forms.alert("No section type selected for SIDE {0}.".format(letter),
                        exitscript=True)
        side_vfts[letter] = chosen


# ---------------------------------------------------------------------------
# 4. Build the four section boxes per chamber and create the sections
# ---------------------------------------------------------------------------
# Width margin each side of the chamber footprint (depth now comes from the
# user-supplied section depth prompt).
WIDTH_MARGIN_FT = 500.0 / MM_PER_FOOT      # 500 mm each side

# Existing view names for uniqueness.
used_view_names = set()
for v in FilteredElementCollector(doc).OfClass(View):
    try:
        used_view_names.add(v.Name)
    except Exception:
        pass
view_names = set(used_view_names)


def _section_box(side_idx, centre, angle, half_lx, half_ly):
    # Outward direction for this side, rotated by the chamber angle.
    ox, oy = SIDE_OUTWARD[side_idx]
    ca, sa = math.cos(angle), math.sin(angle)
    out_x = ox * ca - oy * sa
    out_y = ox * sa + oy * ca
    out_dir = XYZ(out_x, out_y, 0.0).Normalize()

    # Section plane origin: offset out from the chamber centre.
    sec_origin = XYZ(centre.X + out_dir.X * offset_ft,
                     centre.Y + out_dir.Y * offset_ft,
                     centre.Z)

    # Look direction = back toward the centre.
    look = out_dir.Negate()
    up = XYZ(0.0, 0.0, 1.0)
    # Right = up x look (consistent perpendicular; CreateSection recomputes
    # right internally, but a clean orthonormal frame keeps the box square).
    right = up.CrossProduct(look).Normalize()

    t = Transform.Identity
    t.Origin = sec_origin
    t.BasisX = right
    t.BasisY = up
    t.BasisZ = look

    # Width of the cut = the chamber half-span perpendicular to the look
    # direction. For an A/C section (looks along local X) the visible width is
    # the local-Y span; for B/D it is the local-X span.
    if side_idx in (0, 2):       # A / C - looking along local X, width = local Y
        half_w = half_ly + WIDTH_MARGIN_FT
    else:                        # B / D - looking along local Y, width = local X
        half_w = half_lx + WIDTH_MARGIN_FT

    half_h = height_ft * 0.5

    # Local box: X = width (across), Y = height (world Z), Z = depth (look).
    # Far clip = the user-supplied section depth, measured from the plane inward
    # (CreateSection sets far clip = Max.Z - Min.Z, and Min.Z is 0 here).
    box = BoundingBoxXYZ()
    box.Transform = t
    box.Min = XYZ(-half_w, -half_h, 0.0)
    box.Max = XYZ(half_w, half_h, depth_ft)
    return box


# Resolve each chamber's geometry up front; skip any without a location point.
chamber_jobs = []
skipped = []
for inst in target_chambers:
    pose = _chamber_pose(inst)
    if pose is None:
        skipped.append(("Id {0}".format(inst.Id.IntegerValue),
                        "no location point"))
        continue
    _origin_pt, angle = pose
    centre = _world_centre(inst)
    if centre is None:
        skipped.append(("Id {0}".format(inst.Id.IntegerValue),
                        "no centre"))
        continue
    mark = _get_mark(inst)
    stem = _sanitize(mark) if mark else "Id{0}".format(inst.Id.IntegerValue)
    half_lx, half_ly = _chamber_plan_halfspan(inst, angle)
    chamber_jobs.append({
        "inst": inst, "centre": centre, "angle": angle,
        "half_lx": half_lx, "half_ly": half_ly,
        "mark": mark, "stem": stem,
    })

if not chamber_jobs:
    forms.alert("None of the selected chambers had a usable location.",
                exitscript=True)


created = []          # (stem, letter, name)
assoc_jobs = []       # (section view, chamber inst, mark, letter) for records
errors = []           # (stem, letter, message)
t = Transaction(doc, "pyMEP: Create chamber sections ({0} chamber(s))".format(
    len(chamber_jobs)))
t.Start()
try:
    for job in chamber_jobs:
        for i, letter in enumerate(SIDE_LETTERS):
            try:
                box = _section_box(i, job["centre"], job["angle"],
                                   job["half_lx"], job["half_ly"])
                sec = ViewSection.CreateSection(
                    doc, side_vfts[letter].Id, box)
            except Exception as ex:
                errors.append((job["stem"], letter,
                               "create failed: {0}".format(ex)))
                continue
            if sec is None:
                errors.append((job["stem"], letter, "create returned nothing"))
                continue
            base = "{0} SIDE {1}".format(job["stem"], letter)
            name = _unique_name(base, view_names)
            try:
                sec.Name = name
                view_names.add(name)
            except Exception as ex:
                # Keep the auto-generated name; report the failure as an
                # error instead of stuffing the exception into the name.
                errors.append((job["stem"], letter,
                               "rename to '{0}' failed: {1}".format(name, ex)))
                try:
                    name = sec.Name
                except Exception:
                    name = "(auto name)"
            created.append((job["stem"], letter, name))
            assoc_jobs.append((sec, job["inst"], job["mark"], letter))
    t.Commit()
except Exception as ex:
    t.RollBack()
    forms.alert("Failed, no changes made:\n\n{0}".format(ex), exitscript=True)


# ---------------------------------------------------------------------------
# 4b. Store the chamber-section association records (the same records Match
#     Sections saves), so Update Positions can re-place these sections after
#     the chamber moves. Done AFTER the commit, wrapped so an association
#     failure can never roll back or hide the created sections.
# ---------------------------------------------------------------------------
assoc_stored = 0
assoc_error = None
assoc_read_error = None
try:
    import pymep_chamber_links as links
    new_records = {}
    for sec, inst, mark, letter in assoc_jobs:
        try:
            rec = links.make_record(sec, inst, mark)
        except Exception:
            rec = None
        if rec is None:
            continue
        rec["side"] = letter
        new_records[str(sec.Id.IntegerValue)] = rec
    if new_records:
        try:
            data = links.load_links(doc)   # merge into any existing links
        except links.LinksReadError as ex:
            # Existing links file is unreadable/corrupt: do NOT overwrite it,
            # that would wipe every stored association.
            assoc_read_error = ex
        else:
            # New records overwrite old ones for the same section; one save.
            data.update(new_records)
            links.save_links(doc, data)
            assoc_stored = len(new_records)
except Exception as ex:
    assoc_error = ex


# ---------------------------------------------------------------------------
# 5. Report
# ---------------------------------------------------------------------------
out.print_md("### Create chamber sections")
# Section type(s): collapse to one label if all four sides share it, else list.
_side_type_names = [_elem_name(side_vfts[l]) for l in SIDE_LETTERS]
if len(set(_side_type_names)) == 1:
    _section_type_summary = _side_type_names[0]
else:
    _section_type_summary = ", ".join(
        "{0}={1}".format(l, _elem_name(side_vfts[l])) for l in SIDE_LETTERS)
out.print_md("**Family / source:** {0}  |  **Chambers:** {1}  |  "
             "**Section type:** {2}".format(
                 picked_type_label, len(chamber_jobs), _section_type_summary))
out.print_md("**Offset:** {0:.0f} mm  |  **Height:** {1:.0f} mm  |  "
             "**Depth:** {2:.0f} mm  |  **Sections created:** {3} of {4}  |  "
             "**Associations stored:** {5}".format(
                 offset_mm, height_mm, depth_mm, len(created),
                 len(chamber_jobs) * 4, assoc_stored))
if assoc_read_error is not None:
    out.print_md("**Links file unreadable - associations NOT saved** "
                 "(sections were still created). The existing links file was "
                 "left untouched. Fix or delete it, then run Match Sections "
                 "(Associate only) to store the associations. Detail: "
                 "{0}".format(assoc_read_error))
if assoc_error is not None:
    out.print_md("**Association save FAILED:** {0}  (the sections were still "
                 "created - run Match Sections to associate them).".format(
                     assoc_error))
rows = []
for stem, letter, name in created:
    rows.append([stem, "SIDE " + letter, _elem_name(side_vfts[letter]), name])
out.print_table(table_data=rows,
                columns=["Chamber", "Side", "Section type", "Section view"])
if skipped:
    out.print_md("**{0} chamber(s) skipped:**".format(len(skipped)))
    for ident, msg in skipped:
        out.print_md("- {0}: {1}".format(ident, msg))
if errors:
    out.print_md("**{0} section(s) failed:**".format(len(errors)))
    for stem, letter, msg in errors:
        out.print_md("- {0} SIDE {1}: {2}".format(stem, letter, msg))

# Keep the output window open.
