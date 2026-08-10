# -*- coding: utf-8 -*-
"""Update Fence - re-drape recorded fences after the line, the
terrain OR the configuration changed.

Every fence Place New Fence places is recorded with its LINE, its
TERRAIN, its CONFIGURATION name and the values used (project file
store: fences.json). This button re-reads the configuration by name
- so an edit in Fence Configurations (spacing, endpoints, rotation)
flows in - and walks the picked records against the model as it is
NOW:

  - same number of stations  -> every instance is MOVED onto the
    line's current path and the terrain's current surface (element
    ids survive; rotation is applied as the DELTA, so a config
    rotation change lands and a manual tweak on a post survives);
  - station count changed (line reshaped / spacing edited) -> the
    old instances are deleted and the fence REBUILT;
  - line or terrain DELETED -> reported; a record with no surviving
    posts is dropped.

Fence NETWORK records are re-solved from scratch - current lines,
styles, priorities and touching-circle packing - and rebuilt.

The dialog lists each fence with what WILL happen before you commit.
IronPython 2.7 / Revit 2022-2026.
"""

__title__  = "Update\nFence"
__author__ = "Glent Group"

import datetime
import math
import os
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

from pymep_config import get_export_folder, load_settings
from pymep_log import Logger
import pymep_fence as F
import pymep_fence_revit as FR

from Autodesk.Revit.DB import Transaction, UnitTypeId, UnitUtils

doc = revit.doc
output = script.get_output()
log = Logger(output, "UpdateFence")

XAML_PATH = os.path.join(
    os.path.dirname(os.path.abspath(sys.modules["pymep_config"].__file__)),
    "pymep_update_fence.xaml")

log("### Update Fence")

base = os.path.join(get_export_folder(doc), "project_files")
data = F.load_fences(base)
if not data["fences"]:
    log("No fences recorded for this model yet.")
    log.close()
    forms.alert("No fences recorded for this model yet - place one "
                "with Place New Fence first.", exitscript=True)

settings = load_settings()


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


def _by_uid(uid):
    if not uid:
        return None
    try:
        return doc.GetElement(uid)
    except Exception:
        return None


def _mm2ft(mm):
    return UnitUtils.ConvertToInternalUnits(float(mm),
                                            UnitTypeId.Millimeters)


def _config_notes(rec, eff):
    """What changed in the config since this fence was placed."""
    notes = []
    try:
        if abs(float(rec.get("spacing_mm") or 0.0) -
               eff["spacing_mm"]) > 1e-6:
            notes.append("spacing {:g} -> {:g} mm".format(
                float(rec.get("spacing_mm") or 0.0),
                eff["spacing_mm"]))
    except Exception:
        pass
    if bool(rec.get("endpoints", True)) != eff["endpoints"]:
        notes.append("endpoints {} -> {}".format(
            "on" if rec.get("endpoints", True) else "off",
            "on" if eff["endpoints"] else "off"))
    try:
        if abs(float(rec.get("rotation_deg") or 0.0) -
               eff["rotation_deg"]) > 1e-6:
            notes.append(u"rotation {:+g} -> {:+g} deg".format(
                float(rec.get("rotation_deg") or 0.0),
                eff["rotation_deg"]))
    except Exception:
        pass
    if _rec_post(rec) != eff["post"]:
        notes.append("post '{}' -> '{}'".format(
            _rec_post(rec) or "none", eff["post"] or "none"))
    if str(rec.get("foundation") or "") != eff["foundation"]:
        notes.append("foundation '{}' -> '{}'".format(
            rec.get("foundation") or "none",
            eff["foundation"] or "none"))
    if F.end_families(_rec_cfg(rec)) != F.end_families(eff):
        ep, ef = F.end_families(eff)
        notes.append("endpoint families -> post '{}' / foundation "
                     "'{}'".format(ep or "none", ef or "none"))
    return notes


def _rec_post(rec):
    return str(rec.get("post") or rec.get("family") or "")


def _rec_cfg(rec):
    """The record's snapshot in config shape, for end_families()."""
    return {"post": _rec_post(rec),
            "foundation": str(rec.get("foundation") or ""),
            "same_ends": bool(rec.get("same_ends", True)),
            "end_post": str(rec.get("end_post") or ""),
            "end_foundation": str(rec.get("end_foundation") or "")}


def _families_changed(rec, eff):
    """A post or foundation swap / add / remove - in-between OR at
    the endpoints - always REBUILDS: moving cannot change what
    stands at the stations."""
    return (_rec_post(rec) != eff["post"] or
            str(rec.get("foundation") or "") != eff["foundation"] or
            F.end_families(_rec_cfg(rec)) != F.end_families(eff))


# ---- resolve every record against the model + the CURRENT config ----
rows = []   # dicts: rec, line_el, terrain, survivors, eff, plan text
for rec in data["fences"]:
    if rec.get("kind") == "network":
        terrain = _by_uid(rec.get("terrain_uid"))
        net_lines = []
        gone_lines = 0
        for l in rec.get("lines") or []:
            el = _by_uid(l.get("uid"))
            if el is None:
                gone_lines += 1
            else:
                net_lines.append(el)
        survivors = []
        for inst_d in rec.get("instances") or []:
            el = _by_uid(inst_d.get("uid"))
            if el is not None:
                survivors.append((inst_d, el))
        notes = []
        if gone_lines:
            notes.append("{} line(s) deleted".format(gone_lines))
        if terrain is None:
            plan = ("record will be DROPPED (terrain gone, no "
                    "posts left)" if not survivors else
                    "SKIP - the terrain was deleted")
        elif not net_lines:
            plan = ("record will be DROPPED (all lines gone, no "
                    "posts left)" if not survivors else
                    "SKIP - every line was deleted")
        else:
            plan = ("REBUILD network: {} line(s), currently {} "
                    "post(s) - re-solved against the lines, "
                    "terrain and configs as they are now".format(
                        len(net_lines), len(survivors)))
        rows.append({"rec": rec, "line": None, "terrain": terrain,
                     "survivors": survivors, "eff": None,
                     "net_lines": net_lines,
                     "plan": plan + ("; " + "; ".join(notes)
                                     if notes else "")})
        continue
    line_el = _by_uid(rec.get("line_uid"))
    terrain = _by_uid(rec.get("terrain_uid"))
    survivors = []
    for inst_d in rec.get("instances") or []:
        el = _by_uid(inst_d.get("uid"))
        if el is not None:
            survivors.append((inst_d, el))
    eff = F.effective_config(settings, rec.get("config"), rec)
    notes = _config_notes(rec, eff)
    gone = len(rec.get("instances") or []) - len(survivors)
    if gone:
        notes.append("{} post(s) deleted by hand".format(gone))

    dists = None
    if line_el is None:
        plan = ("record will be DROPPED (line gone, no posts left)"
                if not survivors else
                "SKIP - the line was deleted; re-place with Place "
                "New Fence")
    elif terrain is None:
        plan = ("record will be DROPPED (terrain gone, no posts "
                "left)" if not survivors else
                "SKIP - the terrain was deleted")
    else:
        poly = FR.tessellate(line_el)
        if not poly or F.poly_length(poly) <= 1e-9:
            plan = "SKIP - the line has no length any more"
        else:
            dists = F.stations(F.poly_length(poly),
                               _mm2ft(eff["spacing_mm"]),
                               rec.get("justify") or F.JUSTIFY_START,
                               eff["endpoints"], F.is_closed(poly))
            if len(dists) == len(survivors) and survivors and \
                    not _families_changed(rec, eff):
                plan = "MOVE {} post(s) onto the current line + " \
                    "terrain".format(len(survivors))
            else:
                plan = "REBUILD: {} post(s) -> {} station(s)".format(
                    len(survivors), len(dists))
    rows.append({"rec": rec, "line": line_el, "terrain": terrain,
                 "survivors": survivors, "eff": eff,
                 "net_lines": None,
                 "plan": plan + ("; " + "; ".join(notes)
                                 if notes else "")})

log("**{}** fence(s) recorded.".format(len(rows)))


# -------------------------------------------------------------------- dialog
class UpdateWindow(forms.WPFWindow):
    """One checkbox row per fence: the fence, then what WILL happen -
    ticked rows are updated."""

    def __init__(self, rows):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self.result = None
        self._boxes = []
        self.TxtInfo.Text = ("{} fence(s) recorded - untick what "
                             "should stay as it is.".format(len(rows)))
        from System.Windows import TextWrapping, Thickness
        from System.Windows.Controls import CheckBox, StackPanel, \
            TextBlock
        from System.Windows.Media import Brushes
        for i, row in enumerate(rows):
            body = StackPanel()
            head = TextBlock()
            head.Text = F.fence_label(row["rec"])
            head.FontWeight = self._bold()
            head.TextWrapping = TextWrapping.Wrap
            body.Children.Add(head)
            sub = TextBlock()
            sub.Text = row["plan"]
            sub.TextWrapping = TextWrapping.Wrap
            sub.FontSize = 11.0
            try:
                sub.Foreground = (Brushes.Firebrick
                                  if row["plan"].startswith("SKIP")
                                  or "DROPPED" in row["plan"]
                                  else Brushes.Gray)
            except Exception:
                pass
            body.Children.Add(sub)
            cb = CheckBox()
            cb.Content = body
            cb.IsChecked = True
            cb.Margin = Thickness(0, 6, 0, 6)
            cb.Tag = i
            self.LstFences.Children.Add(cb)
            self._boxes.append(cb)

    @staticmethod
    def _bold():
        from System.Windows import FontWeights
        return FontWeights.SemiBold

    def _set_all(self, value):
        for cb in self._boxes:
            cb.IsChecked = value

    def on_all(self, sender, args):
        self._set_all(True)

    def on_none(self, sender, args):
        self._set_all(False)

    def on_go(self, sender, args):
        self.result = [int(cb.Tag) for cb in self._boxes
                       if cb.IsChecked]
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


win = UpdateWindow(rows)
win.ShowDialog()
if win.result is None:
    log("Cancelled - nothing changed.")
    log.close()
    script.exit()
picked = [rows[i] for i in win.result]
if not picked:
    log("Nothing ticked - nothing changed.")
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
    for row in picked:
        rec = row["rec"]
        line_el, terrain = row["line"], row["terrain"]
        survivors, eff = row["survivors"], row["eff"]
        label = F.fence_label(rec)
        fid = rec.get("id")
        if rec.get("kind") == "network":
            if terrain is None or not row["net_lines"]:
                if not survivors:
                    drops.append(fid)
                    results.append((label, "dropped",
                                    "terrain / lines gone and no "
                                    "posts left - record removed"))
                else:
                    results.append((label, "skipped",
                                    "terrain or every line deleted "
                                    "- re-model with Fence "
                                    "Network"))
                continue
            for inst_d, el in survivors:
                try:
                    doc.Delete(el.Id)
                except Exception:
                    pass
                f_el = _by_uid(inst_d.get("foundation_uid"))
                if f_el is not None:
                    try:
                        doc.Delete(f_el.Id)
                    except Exception:
                        pass
            try:
                records, net_notes, placed, missed_n = \
                    FR.model_network(doc, row["net_lines"], terrain,
                                     F.get_configs(settings),
                                     view3d)
            except ValueError as ex:
                results.append((label, "failed", str(ex)))
                continue
            for nn in net_notes:
                log("- network {}: {}".format(fid, nn))
            rec = dict(rec)
            rec["instances"] = records
            rec["lines"] = [{"uid": el.UniqueId,
                             "style": FR.line_style_name(el)}
                            for el in row["net_lines"]]
            rec["updated"] = datetime.datetime.now().strftime(
                "%Y-%m-%dT%H:%M:%S")
            updates.append(rec)
            results.append((label, "rebuilt",
                            "{} -> {} post(s){}".format(
                                len(survivors), placed,
                                "; {} point(s) missed the "
                                "terrain".format(missed_n)
                                if missed_n else "")))
            continue
        if line_el is None or terrain is None:
            if not survivors:
                drops.append(fid)
                results.append((label, "dropped",
                                "line / terrain gone and no posts "
                                "left - record removed"))
            else:
                results.append((label, "skipped",
                                "line / terrain deleted - re-place "
                                "with Place New Fence"))
            continue

        poly = FR.tessellate(line_el)
        if not poly or F.poly_length(poly) <= 1e-9:
            results.append((label, "skipped",
                            "the line has no length any more"))
            continue
        length = F.poly_length(poly)
        dists = F.stations(length, _mm2ft(eff["spacing_mm"]),
                           rec.get("justify") or F.JUSTIFY_START,
                           eff["endpoints"], F.is_closed(poly))
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
        extra_rot = math.radians(eff["rotation_deg"])
        cfg_notes = _config_notes(rec, eff)

        pairs = F.pair_stations([d for d, _el in survivors], dists)
        if _families_changed(rec, eff):
            pairs = None
        if pairs is not None:
            el_by_uid = dict((d.get("uid"), el)
                             for d, el in survivors)
            triples = [(inst_d, el_by_uid[inst_d.get("uid")], nd)
                       for inst_d, nd in pairs]
            records, missed, failed = FR.move_instances(
                doc, triples, poly, terrain_id, ri, ray_z, extra_rot)
            action = "moved"
            note = "{} post(s) re-draped".format(
                len(records) - len(missed) - failed)
        else:
            for inst_d, el in survivors:
                try:
                    doc.Delete(el.Id)
                except Exception:
                    pass
                f_el = _by_uid(inst_d.get("foundation_uid"))
                if f_el is not None:
                    try:
                        doc.Delete(f_el.Id)
                    except Exception:
                        pass
            bad = []

            def _resolve(lbl, cats, what):
                if not lbl:
                    return None
                sym = FR.symbol_by_label(doc, lbl, cats)
                if sym is None:
                    bad.append("{} family '{}' not in the "
                               "model".format(what, lbl))
                return sym

            post_symbol = _resolve(eff["post"], F.POST_CATEGORIES,
                                   "post")
            foundation_symbol = _resolve(eff["foundation"],
                                         F.FOUNDATION_CATEGORIES,
                                         "foundation")
            if eff["same_ends"]:
                end_post_symbol = post_symbol
                end_found_symbol = foundation_symbol
            else:
                end_post_symbol = _resolve(eff["end_post"],
                                           F.POST_CATEGORIES,
                                           "end post")
                end_found_symbol = _resolve(eff["end_foundation"],
                                            F.FOUNDATION_CATEGORIES,
                                            "end foundation")
            if bad:
                results.append((label, "failed", "; ".join(bad)))
                continue
            primary, secondary = post_symbol, foundation_symbol
            if primary is None:
                primary, secondary = foundation_symbol, None
            end_primary, end_secondary = (end_post_symbol,
                                          end_found_symbol)
            if end_primary is None:
                end_primary, end_secondary = end_found_symbol, None
            if primary is None and end_primary is None:
                results.append((label, "failed",
                                "the config places nothing (every "
                                "family is none)"))
                continue
            pick = FR.station_pick(dists, length, primary,
                                   secondary, end_primary,
                                   end_secondary, eff["same_ends"])
            records, missed, failed, why = FR.place_instances(
                doc, pick, poly, dists, terrain_id, ri, ray_z,
                levels, extra_rot)
            action = "rebuilt"
            note = "{} -> {} post(s)".format(len(survivors),
                                             len(records))
            if why:
                note += "; first failure: {}".format(why)
        if cfg_notes:
            note += "; config change applied: " + ", ".join(cfg_notes)
        if missed:
            note += "; {} station(s) missed the terrain".format(
                len(missed))
        if failed:
            note += "; {} failed".format(failed)

        rec = dict(rec)
        rec["instances"] = records
        rec["spacing_mm"] = eff["spacing_mm"]
        rec["endpoints"] = eff["endpoints"]
        rec["rotation_deg"] = eff["rotation_deg"]
        rec["post"] = eff["post"]
        rec["family"] = eff["post"]
        rec["foundation"] = eff["foundation"]
        rec["same_ends"] = eff["same_ends"]
        rec["end_post"] = eff["end_post"]
        rec["end_foundation"] = eff["end_foundation"]
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
