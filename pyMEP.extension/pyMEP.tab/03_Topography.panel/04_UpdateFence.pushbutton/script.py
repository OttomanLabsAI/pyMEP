# -*- coding: utf-8 -*-
"""Update Fence - re-drape recorded fences after the line or the
terrain changed.

Every fence the Fence button places is recorded with its LINE, its
TERRAIN and its settings (project file store: fences.json). This
button walks the picked records against the model as it is NOW:

  - same number of stations  -> every instance is MOVED onto the
    line's current path and the terrain's current surface (element
    ids survive; the rotation applied is the DELTA, so a manual
    rotation tweak on a post survives too);
  - station count changed (line lengthened / reshaped) -> the old
    instances are deleted and the fence is REBUILT with its stored
    family + spacing + justification;
  - line or terrain DELETED -> reported; a record with no surviving
    posts is dropped.

IronPython 2.7 / Revit 2022-2026.
"""

__title__  = "Update\nFence"
__author__ = "Glent Group"

import datetime
import os
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

from pymep_config import get_export_folder
from pymep_log import Logger
from pymep_project_data_ui import PickListWindow
import pymep_fence as F
import pymep_fence_revit as FR

from Autodesk.Revit.DB import (FamilySymbol, FilteredElementCollector,
                               Transaction, UnitTypeId, UnitUtils)

doc = revit.doc
output = script.get_output()
log = Logger(output, "UpdateFence")

log("### Update Fence")

base = os.path.join(get_export_folder(doc), "project_files")
data = F.load_fences(base)
if not data["fences"]:
    log("No fences recorded for this model yet.")
    log.close()
    forms.alert("No fences recorded for this model yet - place one "
                "with the Fence button first.", exitscript=True)


def _nm(el):
    try:
        n = el.Name
        if n:
            return n
    except Exception:
        pass
    try:
        from Autodesk.Revit.DB import Element
        return Element.Name.__get__(el)
    except Exception:
        return "?"


def _symbol_by_label(label):
    for fs in FilteredElementCollector(doc).OfClass(FamilySymbol):
        try:
            if u"{} : {}".format(_nm(fs.Family), _nm(fs)) == label:
                return fs
        except Exception:
            continue
    return None


def _by_uid(uid):
    if not uid:
        return None
    try:
        return doc.GetElement(uid)
    except Exception:
        return None


# resolve every record against the model as it stands
rows = []           # (label, rec, line_el, terrain_el, survivors)
for rec in data["fences"]:
    line_el = _by_uid(rec.get("line_uid"))
    terrain = _by_uid(rec.get("terrain_uid"))
    survivors = []
    for inst_d in rec.get("instances") or []:
        el = _by_uid(inst_d.get("uid"))
        if el is not None:
            survivors.append((inst_d, el))
    state = []
    if line_el is None:
        state.append("line DELETED")
    if terrain is None:
        state.append("terrain DELETED")
    gone = len(rec.get("instances") or []) - len(survivors)
    if gone:
        state.append("{} post(s) deleted".format(gone))
    label = F.fence_label(rec) + \
        (" - " + ", ".join(state) if state else "")
    rows.append((label, rec, line_el, terrain, survivors))

log("**{}** fence(s) recorded.".format(len(rows)))
win = PickListWindow("Update fences - pick which to re-drape",
                     [(None, lbl, i) for i, (lbl, _r, _l, _t, _s)
                      in enumerate(rows)])
win.ShowDialog()
if win.result is None:
    log("Cancelled - nothing changed.")
    log.close()
    script.exit()
picked = [rows[payload] for _g, _lbl, payload in win.result]
if not picked:
    log("Nothing picked - nothing changed.")
    log.close()
    script.exit()

view3d = FR.find_view3d(doc)
if view3d is None:
    log.close()
    forms.alert("No 3D view found in the model - create one and "
                "re-run.", exitscript=True)
ri = FR.make_intersector(view3d)
levels = FR.sorted_levels(doc)
log("Ray-casting in 3D view **{}**.".format(view3d.Name))

results = []        # (fence label, action, notes)
updates = []        # records to write back after the commit
drops = []          # fence ids to drop

t = Transaction(doc, "Update Fence")
t.Start()
try:
    for label, rec, line_el, terrain, survivors in picked:
        fid = rec.get("id")
        if line_el is None or terrain is None:
            if not survivors:
                drops.append(fid)
                results.append((label, "dropped",
                                "line / terrain gone and no posts "
                                "left - record removed"))
            else:
                results.append((label, "skipped",
                                "line / terrain deleted - re-place "
                                "with the Fence button"))
            continue

        poly = FR.tessellate(line_el)
        if not poly or F.poly_length(poly) <= 1e-9:
            results.append((label, "skipped",
                            "the line has no length any more"))
            continue
        length = F.poly_length(poly)
        spacing_ft = UnitUtils.ConvertToInternalUnits(
            float(rec.get("spacing_mm") or 0.0),
            UnitTypeId.Millimeters)
        dists = F.stations(length, spacing_ft,
                           rec.get("justify") or F.JUSTIFY_START,
                           bool(rec.get("endpoints", True)),
                           F.is_closed(poly))
        if not dists:
            results.append((label, "skipped",
                            "no stations on the line as it is now"))
            continue
        if len(dists) > F.MAX_INSTANCES:
            results.append((label, "skipped",
                            "{} stations - over the {} sanity "
                            "cap".format(len(dists),
                                         F.MAX_INSTANCES)))
            continue

        ray_z = FR.ray_start_z([terrain, line_el])
        terrain_id = FR.id_value(terrain.Id)

        pairs = F.pair_stations([d for d, _el in survivors], dists)
        if pairs is not None:
            el_by_uid = dict((d.get("uid"), el)
                             for d, el in survivors)
            triples = [(inst_d, el_by_uid[inst_d.get("uid")], nd)
                       for inst_d, nd in pairs]
            records, missed, failed = FR.move_instances(
                doc, triples, poly, terrain_id, ri, ray_z)
            action = "moved"
            note = "{} post(s) re-draped".format(
                len(records) - len(missed) - failed)
        else:
            for _d, el in survivors:
                try:
                    doc.Delete(el.Id)
                except Exception:
                    pass
            symbol = _symbol_by_label(rec.get("family") or "")
            if symbol is None:
                results.append((label, "failed",
                                "family '{}' no longer in the "
                                "model".format(rec.get("family"))))
                continue
            records, missed, failed, why = FR.place_instances(
                doc, symbol, poly, dists, terrain_id, ri, ray_z,
                levels)
            action = "rebuilt"
            note = "{} -> {} post(s)".format(len(survivors),
                                             len(records))
            if why:
                note += "; first failure: {}".format(why)
        if missed:
            note += "; {} station(s) missed the terrain".format(
                len(missed))
        if failed:
            note += "; {} failed".format(failed)

        rec = dict(rec)
        rec["instances"] = records
        rec["updated"] = datetime.datetime.now().strftime(
            "%Y-%m-%dT%H:%M:%S")
        updates.append(rec)
        results.append((label, action, note))
    t.Commit()
except Exception:
    try:
        t.RollBack()
    except Exception:
        pass
    raise

# registry writes AFTER the model commit, so a rollback never leaves
# the record pointing at instances that were not really changed
for rec in updates:
    try:
        F.update_fence(base, rec)
    except Exception as ex:
        log("! could not save fence {}: {}".format(rec.get("id"), ex))
for fid in drops:
    try:
        F.drop_fence(base, fid)
    except Exception:
        pass

log("#### Summary")
log("| fence | action | notes |")
log("|---|---|---|")
for label, action, note in results:
    log("| {} | **{}** | {} |".format(label, action, note or "-"))
counts = {}
for _l, action, _n in results:
    counts[action] = counts.get(action, 0) + 1
log("- " + ", ".join("{}: **{}**".format(k, v)
                     for k, v in sorted(counts.items())))
log.close()
forms.alert("Update Fence finished:\n" +
            "\n".join("  {}: {}".format(k, v)
                      for k, v in sorted(counts.items())) +
            "\n\nDetails are in the pyMEP report.",
            title="Fences updated")
