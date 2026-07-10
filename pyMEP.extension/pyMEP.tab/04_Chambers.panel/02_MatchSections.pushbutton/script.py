# -*- coding: utf-8 -*-
"""Match Sections - pair MANUALLY-DRAWN section views with their chambers,
then rename them, store their placement (associate), or both.

Sections made by Create Sections are already named and associated
automatically - this tool serves sections that were drawn by hand around
chambers.

One shared dialog sequence:
  1. Pick the chamber family TYPE (searchable).
  2. Pick the WORKSET (searchable) - instances must match type AND workset.
     (Skipped when the model is not workshared.)
  3. Pick the section TYPE(s) (searchable) - the SIDE (A/B) is read from the
     section's type name, e.g. 'GLT_MV CHAMBER SECTION SIDE A'.
  4. Give a max match distance in mm (blank = no limit).

Each section is then matched to its nearest chamber by XY centre (greedy,
closest pairing first); each chamber takes at most one SIDE A and one SIDE B
section. You then choose what to do with the matches:
  * Rename + Associate - rename each section "{chamber Mark} SIDE {A/B}" AND
    store its placement relative to the chamber (offset in the chamber's
    local rotated frame plus relative rotation) to the per-project JSON that
    Update Positions reads.
  * Rename only        - just rename the section views.
  * Associate only     - just store the associations.

A preview table is shown and must be confirmed before anything changes.

IronPython 2.7: pure ASCII, no f-strings, LF endings.
"""

__title__  = "Match\nSections"
__author__ = "Glent Group"

import sys

# Reload pymep_* lib modules so the script picks up the latest helpers.
for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, DB, forms, script

import pymep_chamber_links as links

doc = revit.doc
out = script.get_output()

# Revit internal units are feet; convert mm -> feet where needed.
MM_PER_FOOT = 304.8


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_mark(inst):
    p = inst.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
    if p is not None:
        v = p.AsString()
        if v:
            return v.strip()
    return None


def _side_from_type_name(type_name):
    # Read 'A' or 'B' from a section type name like
    # 'GLT_MV CHAMBER SECTION SIDE A'. Matches 'SIDE A'/'SIDE B' or a trailing
    # '_A'/'_B'/' A'/' B', case-insensitive.
    if not type_name:
        return None
    u = type_name.upper()
    idx = u.rfind("SIDE")
    if idx != -1:
        tail = u[idx + 4:].strip().replace("_", " ").strip()
        if tail[:1] == "A":
            return "A"
        if tail[:1] == "B":
            return "B"
    stripped = u.rstrip()
    if stripped.endswith(" A") or stripped.endswith("_A"):
        return "A"
    if stripped.endswith(" B") or stripped.endswith("_B"):
        return "B"
    return None


def _elem_name(elem):
    for getter in (lambda e: e.Name,
                   lambda e: DB.Element.Name.GetValue(e)):
        try:
            n = getter(elem)
            if n:
                return n
        except Exception:
            pass
    for bip in (DB.BuiltInParameter.ALL_MODEL_TYPE_NAME,
                DB.BuiltInParameter.SYMBOL_NAME_PARAM):
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


def _inst_workset_id(inst):
    try:
        wid = inst.WorksetId
        if wid is not None:
            return wid.IntegerValue
    except Exception:
        pass
    try:
        wid = doc.GetWorksetId(inst.Id)
        if wid is not None:
            return wid.IntegerValue
    except Exception:
        pass
    return None


def _workset_name(ws_int):
    try:
        wtable = doc.GetWorksetTable()
        ws = wtable.GetWorkset(DB.WorksetId(ws_int))
        return ws.Name
    except Exception:
        return "<workset {0}>".format(ws_int)


def _inst_xy(inst):
    loc = inst.Location
    if not isinstance(loc, DB.LocationPoint):
        return None
    pt = loc.Point
    return (pt.X, pt.Y)


def _dist2(a_xy, b_xy):
    dx = a_xy[0] - b_xy[0]
    dy = a_xy[1] - b_xy[1]
    return dx * dx + dy * dy


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


# ---------------------------------------------------------------------------
# 1) Pick chamber family TYPE
# ---------------------------------------------------------------------------
inst_collector = DB.FilteredElementCollector(doc)\
    .OfClass(DB.FamilyInstance)\
    .WhereElementIsNotElementType()\
    .ToElements()

inst_by_typeid = {}
sym_by_typeid = {}
for fi in inst_collector:
    if not isinstance(fi.Location, DB.LocationPoint):
        continue
    tid = fi.GetTypeId()
    if tid is None or tid == DB.ElementId.InvalidElementId:
        continue
    key = tid.IntegerValue
    inst_by_typeid.setdefault(key, [])
    inst_by_typeid[key].append(fi)
    if key not in sym_by_typeid:
        sym_by_typeid[key] = doc.GetElement(tid)

if not inst_by_typeid:
    forms.alert("No placed point-based family instances found.", exitscript=True)

type_options = []
for key, insts in inst_by_typeid.items():
    sym = sym_by_typeid.get(key)
    if sym is None:
        continue
    type_options.append({
        "label": "{0}   ({1} placed)".format(_type_label(sym), len(insts)),
        "typeid": key,
        "symbol": sym,
    })
type_options.sort(key=lambda d: d["label"].lower())

picked_fam = forms.SelectFromList.show(
    [d["label"] for d in type_options],
    title="Select chamber family TYPE (type to search)",
    button_name="Use this family type",
    multiselect=False
)
if not picked_fam:
    script.exit()

fam_choice = None
for d in type_options:
    if d["label"] == picked_fam:
        fam_choice = d
        break

target_instances = inst_by_typeid[fam_choice["typeid"]]
ws_display = "<not workshared>"


# ---------------------------------------------------------------------------
# 2) Pick workset
# ---------------------------------------------------------------------------
if getattr(doc, "IsWorkshared", False):
    ws_counts = {}
    for fi in target_instances:
        wid = _inst_workset_id(fi)
        if wid is None:
            continue
        ws_counts.setdefault(wid, 0)
        ws_counts[wid] += 1

    if not ws_counts:
        forms.alert("Could not read worksets; proceeding unfiltered.")
        ws_display = "<unfiltered>"
    else:
        ws_options = []
        for wid, n in ws_counts.items():
            ws_options.append({
                "label": "{0}   ({1} placed)".format(_workset_name(wid), n),
                "wid": wid,
            })
        ws_options.sort(key=lambda d: d["label"].lower())

        picked_ws = forms.SelectFromList.show(
            [d["label"] for d in ws_options],
            title="Select WORKSET (type to search)",
            button_name="Use this workset",
            multiselect=False
        )
        if not picked_ws:
            script.exit()

        ws_choice = None
        for d in ws_options:
            if d["label"] == picked_ws:
                ws_choice = d
                break

        target_instances = [
            fi for fi in target_instances
            if _inst_workset_id(fi) == ws_choice["wid"]
        ]
        ws_display = _workset_name(ws_choice["wid"])

        if not target_instances:
            forms.alert("No instances of '{0}' on workset '{1}'.".format(
                _type_label(fam_choice["symbol"]),
                _workset_name(ws_choice["wid"])), exitscript=True)


# ---------------------------------------------------------------------------
# 3) Pick section TYPE(s) - SIDE read from type name
# ---------------------------------------------------------------------------
all_views = DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements()

sections_all = []
for v in all_views:
    try:
        if v.IsTemplate:
            continue
        if v.ViewType != DB.ViewType.Section:
            continue
        if v.Origin is None:
            continue
    except Exception:
        continue
    sections_all.append(v)

if not sections_all:
    forms.alert("No section views found in this model.", exitscript=True)

sec_side_of = {}
groups = {}
for v in sections_all:
    try:
        vft = doc.GetElement(v.GetTypeId())
        tname = _elem_name(vft)
    except Exception:
        tname = "?"
    side = _side_from_type_name(tname)
    if side is None:
        continue
    sid = v.Id.IntegerValue
    sec_side_of[sid] = side
    groups.setdefault(tname, [])
    groups[tname].append(v)

if not groups:
    forms.alert(
        "No section TYPES have 'SIDE A' or 'SIDE B' in their name.",
        exitscript=True)

grp_labels = sorted(
    ["{0}   ({1} sections)".format(k, len(v)) for k, v in groups.items()],
    key=lambda s: s.lower()
)
label_to_type = {}
for k, v in groups.items():
    label_to_type["{0}   ({1} sections)".format(k, len(v))] = k

picked_grp = forms.SelectFromList.show(
    grp_labels,
    title="Select section TYPE(s) to match - pick SIDE A and SIDE B types",
    button_name="Use these sections",
    multiselect=True
)
if not picked_grp:
    script.exit()
if not isinstance(picked_grp, list):
    picked_grp = [picked_grp]

chosen_types = [label_to_type[p] for p in picked_grp]
target_sections = []
for tname in chosen_types:
    for sec in groups[tname]:
        target_sections.append(sec)

chosen_vft = ", ".join(chosen_types)   # for the preview header


# ---------------------------------------------------------------------------
# 4) Match each section to nearest chamber (cap one per side per chamber)
# ---------------------------------------------------------------------------
maxd_str = forms.ask_for_string(
    default="3000",
    prompt="Max distance (mm) a section can be from a chamber centre to match.\n"
           "Leave blank for no limit.",
    title="Max match distance"
)
if maxd_str is None:
    script.exit()
maxd_str = maxd_str.strip()
if maxd_str == "":
    max_d2 = None
else:
    try:
        max_d2 = (float(maxd_str) / MM_PER_FOOT) ** 2
    except Exception:
        forms.alert("Enter a number in mm, or blank for no limit.",
                    exitscript=True)

fam_data = []
for fi in target_instances:
    centre = _inst_xy(fi)
    if centre is None:
        continue
    fam_data.append({
        "inst": fi,
        "centre": centre,
        "mark": _get_mark(fi),
    })

if not fam_data:
    forms.alert("Selected chamber type has no usable placed instances.",
                exitscript=True)

# Build every candidate (chamber, section, distance) pair within the limit,
# then assign greedily by ascending distance so the closest pairings win.
candidates = []
for fi_idx, fd in enumerate(fam_data):
    for sec in target_sections:
        o = sec.Origin
        d2 = _dist2((o.X, o.Y), fd["centre"])
        if max_d2 is not None and d2 > max_d2:
            continue
        candidates.append((d2, fi_idx, sec))
candidates.sort(key=lambda c: c[0])

claimed_sections = set()
fam_side_claimed = set()
pairs = []          # (section view, chamber inst, mark, side, dist_ft)
for d2, fi_idx, sec in candidates:
    sid = sec.Id.IntegerValue
    if sid in claimed_sections:
        continue
    side = sec_side_of.get(sid)
    if side is None:
        continue
    if (fi_idx, side) in fam_side_claimed:
        continue
    fd = fam_data[fi_idx]
    pairs.append((sec, fd["inst"], fd["mark"], side, d2 ** 0.5))
    claimed_sections.add(sid)
    fam_side_claimed.add((fi_idx, side))

if not pairs:
    msg = "No sections matched a '{0}' instance.".format(
        _type_label(fam_choice["symbol"]))
    nearest = None
    for fd in fam_data:
        for sec in target_sections:
            o = sec.Origin
            d = _dist2((o.X, o.Y), fd["centre"]) ** 0.5
            if nearest is None or d < nearest:
                nearest = d
    if nearest is not None:
        msg += "\n\nClosest chamber-to-section distance: {0:.0f} mm.".format(
            nearest * MM_PER_FOOT)
        if max_d2 is not None and (nearest ** 2) > max_d2:
            msg += "\nThis exceeds your max distance - raise it and re-run."
    forms.alert(msg, exitscript=True)


# ---------------------------------------------------------------------------
# 5) Mode: rename, associate, or both
# ---------------------------------------------------------------------------
mode = forms.alert(
    "{0} section(s) matched to '{1}' chamber(s).\n\n"
    "What should be done with the matched sections?".format(
        len(pairs), _type_label(fam_choice["symbol"])),
    options=["Rename + Associate", "Rename only", "Associate only"])
if not mode:
    script.exit()

do_rename = mode in ("Rename + Associate", "Rename only")
do_assoc = mode in ("Rename + Associate", "Associate only")


# ---------------------------------------------------------------------------
# 6) Build association records (also feeds the preview table)
# ---------------------------------------------------------------------------
new_records = {}
for sec, inst, mark, side, dist_ft in pairs:
    rec = links.make_record(sec, inst, mark)
    if rec is None:
        continue
    rec["side"] = side
    new_records[str(sec.Id.IntegerValue)] = rec

if do_assoc and not new_records:
    forms.alert("Could not build any association records.", exitscript=True)


# ---------------------------------------------------------------------------
# 7) Build the rename plan (skips chambers without a Mark)
# ---------------------------------------------------------------------------
plan = []
no_mark = []        # sections whose chamber has no Mark (cannot be renamed)
for sec, inst, mark, side, dist_ft in pairs:
    if not mark:
        if sec.Name not in no_mark:
            no_mark.append(sec.Name)
        continue
    plan.append({
        "view": sec,
        "base": "{0} SIDE {1}".format(_sanitize(mark), side),
        "old": sec.Name,
        "mark": mark,
        "side": side,
        "dist": dist_ft,
    })

# Existing view names for uniqueness. Exclude ONLY the sections actually
# planned for a rename - a matched-but-unrenamed (or markless) section keeps
# its current name, so that name must stay "taken"; excluding it would let a
# new name collide with it and blow up the rename pass.
batch_ids = set()
for r in plan:
    batch_ids.add(r["view"].Id.IntegerValue)

used_names = set()
for v in all_views:
    try:
        if v.Id.IntegerValue in batch_ids:
            continue
        used_names.add(v.Name)
    except Exception:
        pass

plan.sort(key=lambda r: (r["mark"], r["side"], r["dist"]))
assigned = set(used_names)
for r in plan:
    name = _unique_name(r["base"], assigned)
    r["new"] = name
    assigned.add(name)
    r["changed"] = (r["old"] != r["new"])

changed_plan = [r for r in plan if r["changed"]]
unchanged_count = len(plan) - len(changed_plan)

plan_by_sid = {}
for r in plan:
    plan_by_sid[r["view"].Id.IntegerValue] = r


# ---------------------------------------------------------------------------
# 8) Preview
# ---------------------------------------------------------------------------
out.print_md("### Match sections - preview ({0})".format(mode))
out.print_md("**Chamber type:** {0}  |  **Workset:** {1}  |  "
             "**Section group(s):** {2}".format(
                 _type_label(fam_choice["symbol"]), ws_display, chosen_vft))
rows = []
for sec, inst, mark, side, dist_ft in pairs:
    sid = sec.Id.IntegerValue
    r = plan_by_sid.get(sid)
    if not do_rename:
        new_name = "-"
    elif r is None:
        new_name = "<no Mark - not renamed>"
    elif r["changed"]:
        new_name = r["new"]
    else:
        new_name = "(already ok)"
    rec = new_records.get(str(sid))
    if rec is not None:
        ox, oy, oz = rec["local_offset_mm"]
        loc_txt = "{0:.0f}, {1:.0f}".format(ox, oy)
        rot_txt = "{0:.1f}".format(rec["rel_angle_deg"])
    else:
        loc_txt = "-"
        rot_txt = "-"
    rows.append([
        sec.Name,
        new_name,
        mark if mark else "<no mark>",
        side,
        loc_txt,
        rot_txt,
        "{0:.0f} mm".format(dist_ft * MM_PER_FOOT),
    ])
out.print_table(
    table_data=rows,
    columns=["Section", "New name", "Mark", "Side", "Local dX,dY (mm)",
             "Rel rot (deg)", "Dist"]
)
if do_rename and unchanged_count:
    out.print_md("**{0} section(s) already correctly named** "
                 "(left unchanged).".format(unchanged_count))
if do_rename and no_mark:
    out.print_md("**{0} section(s) cannot be renamed** "
                 "(nearest chamber has no Mark).".format(len(no_mark)))

# Rename-only mode with nothing to rename: stop here.
if do_rename and not do_assoc:
    if not plan:
        forms.alert("None of the matched chambers has a Mark - "
                    "nothing to rename.", exitscript=True)
    if not changed_plan:
        forms.alert("All matched sections are already correctly named. "
                    "Nothing to rename.", exitscript=True)


# ---------------------------------------------------------------------------
# 9) Confirm
# ---------------------------------------------------------------------------
actions = []
if do_rename:
    actions.append("rename {0} section(s) ({1} already correct)".format(
        len(changed_plan), unchanged_count))
if do_assoc:
    actions.append("store {0} association(s)".format(len(new_records)))
if not forms.alert("Proceed - {0}?".format(" and ".join(actions)),
                   yes=True, no=True):
    script.exit()


# ---------------------------------------------------------------------------
# 10) Apply renames (two-pass temp-name to avoid transient collisions).
#     Only sections whose name actually changes are touched.
# ---------------------------------------------------------------------------
renamed = 0
rename_errors = []
if do_rename and changed_plan:
    t = DB.Transaction(doc, "pyMEP: Match sections - rename")
    t.Start()
    try:
        temp_map = {}
        for r in changed_plan:
            # Temp name is unique per element (keyed on the section's
            # ElementId), so it can never collide with a leftover temp name
            # from a previous failed run.
            eid = r["view"].Id.IntegerValue
            try:
                r["view"].Name = "__pymep_tmp_{0}".format(eid)
                temp_map[eid] = r
            except Exception as ex:
                rename_errors.append("{0}: {1}".format(r["old"], ex))

        for eid, r in temp_map.items():
            try:
                r["view"].Name = r["new"]
                renamed += 1
                continue
            except Exception as ex:
                first_ex = ex
            # Retry ONCE with a fresh unique name computed against the LIVE
            # current view names (some other view may hold the planned name).
            live_names = set()
            for v in DB.FilteredElementCollector(doc).OfClass(DB.View):
                try:
                    live_names.add(v.Name)
                except Exception:
                    pass
            retry_name = _unique_name(r["base"], live_names)
            try:
                r["view"].Name = retry_name
                r["new"] = retry_name
                renamed += 1
                continue
            except Exception:
                pass
            # Both attempts failed: NEVER leave the temp name behind -
            # restore the section's original name and mark the row failed.
            try:
                r["view"].Name = r["old"]
            except Exception as restore_ex:
                rename_errors.append(
                    "{0}: could not restore original name - view left "
                    "with temp name '__pymep_tmp_{1}' ({2})".format(
                        r["old"], eid, restore_ex))
            rename_errors.append("{0} -> {1}: {2}".format(
                r["old"], r["new"], first_ex))
        t.Commit()
    except Exception as ex:
        t.RollBack()
        forms.alert("Rename transaction failed, no changes made:\n{0}".format(
            ex), exitscript=True)


# ---------------------------------------------------------------------------
# 11) Save associations (merge into any existing links; one save)
# ---------------------------------------------------------------------------
assoc_saved = False
assoc_read_error = None
assoc_write_error = None
if do_assoc:
    # Keep the stored section names in sync with any renames just applied
    # (reads the LIVE view name, so retries/restores are reflected too).
    for r in plan:
        key = str(r["view"].Id.IntegerValue)
        if key in new_records:
            try:
                new_records[key]["section_name"] = r["view"].Name
            except Exception:
                pass
    try:
        data = links.load_links(doc)
    except links.LinksReadError as ex:
        # Existing links file is unreadable/corrupt: do NOT overwrite it.
        assoc_read_error = ex
        forms.alert(
            "The chamber-section links file exists but could not be read:\n\n"
            "{0}\n\nAssociations were NOT saved (the file was left "
            "untouched; any renames above were still applied).\n\n"
            "Fix or delete the file, then run 'Associate only' "
            "again.".format(ex))
    else:
        data.update(new_records)
        # Never let an IO failure here kill the script: the rename
        # transaction has already committed, so the final report (renames
        # included) must still print.
        try:
            path = links.save_links(doc, data)
            assoc_saved = True
        except Exception as ex:
            assoc_write_error = ex


# ---------------------------------------------------------------------------
# 12) Report
# ---------------------------------------------------------------------------
out.print_md("### Match sections - done")
if do_rename:
    out.print_md("**Renamed {0} of {1} section(s) "
                 "({2} already correct).**".format(
                     renamed, len(changed_plan), unchanged_count))
if do_assoc:
    if assoc_saved:
        out.print_md("**Saved {0} association(s).**".format(len(new_records)))
        out.print_md("Stored at: `{0}`".format(path))
        out.print_md("Total associations on file: {0}".format(len(data)))
    elif assoc_write_error is not None:
        out.print_md("**associations NOT saved: {0}**".format(
            assoc_write_error))
    else:
        out.print_md("**Associations NOT saved - links file unreadable:** "
                     "{0}".format(assoc_read_error))
if rename_errors:
    out.print_md("**{0} rename error(s):**".format(len(rename_errors)))
    for e in rename_errors:
        out.print_md("- " + e)
