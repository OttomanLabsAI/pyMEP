# -*- coding: utf-8 -*-
# IronPython 2.7 - pyRevit
# Chamber Sections > Update Section Positions
#
# Reads the per-project chamber-section association JSON (written by Create
# Sections and Match Sections) and moves/rotates each section view back to its stored
# position relative to its chamber. Use after a chamber has been moved or
# rotated so its sections follow.
#
# Chamber re-find order: Mark first, then ElementId backup.

import math
import sys

# Reload pymep_* lib modules so the script picks up the latest helpers.
for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, DB, forms, script

import pymep_chamber_links as links

doc = revit.doc
out = script.get_output()

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


def _build_mark_index():
    # Mark -> list of family instances carrying that Mark.
    idx = {}
    coll = DB.FilteredElementCollector(doc)\
        .OfClass(DB.FamilyInstance)\
        .WhereElementIsNotElementType()\
        .ToElements()
    for fi in coll:
        if not isinstance(fi.Location, DB.LocationPoint):
            continue
        m = _get_mark(fi)
        if not m:
            continue
        idx.setdefault(m, [])
        idx[m].append(fi)
    return idx


def _find_chamber(rec, mark_index):
    # Try Mark first; if unique, use it. Else fall back to stored ElementId.
    mark = rec.get("chamber_mark")
    if mark:
        hits = mark_index.get(mark, [])
        if len(hits) == 1:
            return hits[0], "mark"
        if len(hits) > 1:
            # Ambiguous mark; fall through to ElementId to disambiguate.
            eid = rec.get("chamber_eid")
            if eid is not None:
                for fi in hits:
                    if fi.Id.IntegerValue == eid:
                        return fi, "mark+eid"
            # Still ambiguous: return None so it is reported, not guessed.
            return None, "ambiguous-mark"
    eid = rec.get("chamber_eid")
    if eid is not None:
        el = doc.GetElement(DB.ElementId(eid))
        if el is not None and isinstance(el, DB.FamilyInstance):
            return el, "eid"
    return None, "not-found"


def _move_section(view, target_origin, target_angle):
    # Delegate to the shared helper, which tries MoveElement/RotateElement and
    # measures what actually happened. Returns (ok, message, achieved_dict).
    return links.set_section_pose(view, target_origin, target_angle)


# ---------------------------------------------------------------------------
# Load associations
# ---------------------------------------------------------------------------
try:
    data = links.load_links(doc)
except links.LinksReadError as ex:
    forms.alert(
        "The chamber-section links file exists but is unreadable or "
        "corrupt:\n\n{0}\n\nNothing was changed. Fix or delete the file, "
        "then re-associate the sections (Create Sections / Match Sections) "
        "and run again.".format(ex),
        exitscript=True)
if not data:
    forms.alert(
        "No stored associations found for this model.\n\n"
        "Run 'Create Sections' (auto-associates) or 'Match Sections' first.",
        exitscript=True)

mark_index = _build_mark_index()

# Resolve each record to (view, chamber, target pose) before applying.
plan = []
missing_section = []
missing_chamber = []
bad_records = 0
for sid_str, rec in data.items():
    if not isinstance(rec, dict):
        bad_records += 1
        continue
    try:
        sid = int(sid_str)
    except Exception:
        bad_records += 1
        continue
    view = doc.GetElement(DB.ElementId(sid))
    if (view is None or not isinstance(view, DB.View)
            or not isinstance(view, DB.ViewSection)):
        missing_section.append(rec.get("section_name", sid_str))
        continue

    chamber, how = _find_chamber(rec, mark_index)
    if chamber is None:
        missing_chamber.append((rec.get("section_name", sid_str),
                                rec.get("chamber_mark", ""), how))
        continue

    tp = links.target_pose_from_record(rec, chamber)
    if tp is None:
        missing_chamber.append((rec.get("section_name", sid_str),
                                rec.get("chamber_mark", ""), "bad-record"))
        continue
    target_origin, target_angle = tp

    # Current vs target delta for preview.
    cur = links.section_origin(view)
    dft = ((target_origin.X - cur.X) ** 2 +
           (target_origin.Y - cur.Y) ** 2 +
           (target_origin.Z - cur.Z) ** 2) ** 0.5
    dang = math.degrees(target_angle - links.section_angle_from_crop(view))
    while dang > 180.0:
        dang -= 360.0
    while dang < -180.0:
        dang += 360.0

    plan.append({
        "view": view,
        "chamber": chamber,
        "how": how,
        "target_origin": target_origin,
        "target_angle": target_angle,
        "move_mm": dft * MM_PER_FOOT,
        "rot_deg": dang,
        "name": view.Name,
        "mark": rec.get("chamber_mark", ""),
    })

if not plan:
    msg = "Nothing to update."
    if missing_section:
        msg += "\n\n{0} stored section(s) no longer exist.".format(
            len(missing_section))
    if missing_chamber:
        msg += "\n\n{0} chamber(s) could not be re-found.".format(
            len(missing_chamber))
    forms.alert(msg, exitscript=True)


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------
out.print_md("### Update section positions - preview")
rows = []
for r in plan:
    rows.append([
        r["name"],
        r["mark"] if r["mark"] else "<no mark>",
        r["how"],
        "{0:.0f} mm".format(r["move_mm"]),
        "{0:.1f} deg".format(r["rot_deg"]),
    ])
out.print_table(
    table_data=rows,
    columns=["Section", "Chamber Mark", "Found by", "Will move", "Will rotate"]
)
if missing_section:
    out.print_md("**{0} stored section(s) no longer exist** (skipped).".format(
        len(missing_section)))
if missing_chamber:
    out.print_md("**{0} chamber(s) not re-found** (skipped):".format(
        len(missing_chamber)))
    for nm, mk, how in missing_chamber[:20]:
        out.print_md("- {0}  (mark '{1}', {2})".format(nm, mk, how))
if bad_records:
    out.print_md("**{0} malformed record(s) in the links file** "
                 "(skipped).".format(bad_records))

if not forms.alert(
        "Reposition {0} section(s) to match their chambers?".format(len(plan)),
        yes=True, no=True):
    script.exit()


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------
moved = 0
errors = []
diag_rows = []
t = DB.Transaction(doc, "Update section positions")
t.Start()
try:
    for r in plan:
        try:
            ok, msg, ach = _move_section(
                r["view"], r["target_origin"], r["target_angle"])
            if ok:
                moved += 1
            else:
                errors.append("{0}: {1}".format(r["name"], msg))
            diag_rows.append([
                r["name"],
                "{0:.0f}".format(ach.get("moved_mm", 0.0)),
                "{0:.0f}".format(ach.get("miss_mm", 0.0)),
                "{0:.1f}".format(ach.get("rotated_deg", 0.0)),
                ach.get("method", "?"),
            ])
        except Exception as ex:
            errors.append("{0}: {1}".format(r["name"], ex))
            diag_rows.append([r["name"], "-", "-", "-", "EXCEPTION: %s" % ex])
    t.Commit()
except Exception as ex:
    t.RollBack()
    forms.alert("Transaction failed, no changes made:\n{0}".format(ex),
                exitscript=True)

out.print_md("**Done. Repositioned {0} of {1} section(s).**".format(
    moved, len(plan)))

# Diagnostic: what actually happened to each section (achieved move/rotate and
# which API method was used). This is how we tell if the move really took.
out.print_md("#### What actually moved")
out.print_table(
    table_data=diag_rows,
    columns=["Section", "Moved (mm)", "Off target (mm)", "Rotated (deg)",
             "Method"]
)

if errors:
    out.print_md("**{0} not fully placed:**".format(len(errors)))
    for e in errors[:50]:
        out.print_md("- " + e)

