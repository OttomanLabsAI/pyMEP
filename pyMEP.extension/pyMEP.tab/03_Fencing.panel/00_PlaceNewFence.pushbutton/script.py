# -*- coding: utf-8 -*-
"""Place New Fence - pick a line and a terrain, place a family
along the line.

One dialog first: the family (searchable), the fence CONFIGURATION
(spacing + endpoints + custom rotation - created and edited with the
Fence Configurations button), and the justification (Start / Centre
/ End - where the spacing counts from; Centre splits the leftover
evenly). Then pick the LINE and the TERRAIN in the view. Every station is ray-cast
straight down onto the picked terrain and the instance lands ON the
ground, rotated so its X axis follows the line's direction there.

Every fence is RECORDED (line, terrain, settings, instances) in the
project's file store, so Update Fence re-drapes it after the line or
the terrain changes. Straight, curved and closed model lines all
work. Revit 2022-2026.
"""

__title__  = "Place New\nFence"
__author__ = "Glent Group"

import datetime
import math
import os
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

from pymep_config import load_settings, save_settings, get_export_folder
from pymep_log import Logger
import pymep_fence as F
import pymep_fence_revit as FR

from Autodesk.Revit.DB import (
    BuiltInCategory,
    CurveElement,
    FamilySymbol,
    FilteredElementCollector,
    Transaction,
    UnitTypeId,
    UnitUtils,
)
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()
log = Logger(output, "PlaceNewFence")

XAML_PATH = os.path.join(
    os.path.dirname(os.path.abspath(sys.modules["pymep_config"].__file__)),
    "pymep_fence.xaml")

log("### Place New Fence")


def _name(el):
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


# ------------------------------------------------------------------ families
def placeable_symbols():
    """[(label, symbol)] sorted - one-level and work-plane point
    families (site furniture, posts, trees, generic models)."""
    out = []
    for fs in FilteredElementCollector(doc).OfClass(FamilySymbol):
        try:
            fam = fs.Family
            if str(fam.FamilyPlacementType) not in (
                    "OneLevelBased", "WorkPlaneBased"):
                continue
            out.append((u"{} : {}".format(_name(fam), _name(fs)), fs))
        except Exception:
            continue
    out.sort(key=lambda t: t[0].lower())
    return out


# -------------------------------------------------------------------- dialog
class FenceWindow(forms.WPFWindow):
    """Family + spacing configuration + justification; the
    Configurations group creates / edits / deletes the named configs
    saved in pyMEP settings."""

    def __init__(self, settings, fam_items, info_text):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self.result = None
        self.settings = settings
        self.fam_items = fam_items          # [(label, symbol)]
        self.TxtInfo.Text = info_text
        self._fill_families("")
        want = settings.get(F.SETTINGS_FAMILY) or ""
        self._select_family(want)
        just = settings.get(F.SETTINGS_JUSTIFY, F.JUSTIFY_START)
        if just == F.JUSTIFY_CENTRE:
            self.RadJustCentre.IsChecked = True
        elif just == F.JUSTIFY_END:
            self.RadJustEnd.IsChecked = True
        else:
            self.RadJustStart.IsChecked = True
        self._fill_configs(settings.get(F.SETTINGS_LAST))

    # ---- families ----------------------------------------------------
    def _fill_families(self, needle):
        self.CmbFamily.Items.Clear()
        needle = (needle or "").strip().lower()
        for label, _fs in self.fam_items:
            if needle and needle not in label.lower():
                continue
            self.CmbFamily.Items.Add(label)
        if self.CmbFamily.Items.Count:
            self.CmbFamily.SelectedIndex = 0

    def _select_family(self, label):
        for i in range(self.CmbFamily.Items.Count):
            if str(self.CmbFamily.Items[i]) == label:
                self.CmbFamily.SelectedIndex = i
                return

    def on_family_search(self, sender, args):
        try:
            keep = str(self.CmbFamily.SelectedItem or "")
            self._fill_families(self.TxtFamilySearch.Text)
            self._select_family(keep)
        except Exception:
            pass

    # ---- configs -----------------------------------------------------
    def _fill_configs(self, want):
        cfgs = F.get_configs(self.settings)
        self.CmbConfig.Items.Clear()
        names = sorted(cfgs.keys(), key=lambda s: s.lower())
        for n in names:
            self.CmbConfig.Items.Add(n)
        pick = want if want in cfgs else names[0]
        self.CmbConfig.SelectedIndex = names.index(pick)

    def on_config_pick(self, sender, args):
        try:
            name = str(self.CmbConfig.SelectedItem or "")
            cfg = F.get_configs(self.settings).get(name)
            if cfg is None:
                return
            self.TxtCfgSummary.Text = (
                u"{:g} mm spacing, endpoints {}, rotation "
                u"{:+g}\u00b0".format(
                    cfg["spacing_mm"],
                    "ON" if cfg["endpoints"] else "off",
                    cfg["rotation_deg"]))
            self.StatusText.Text = ""
        except Exception:
            pass

    # ---- go / cancel -------------------------------------------------
    def justify(self):
        if self.RadJustCentre.IsChecked:
            return F.JUSTIFY_CENTRE
        if self.RadJustEnd.IsChecked:
            return F.JUSTIFY_END
        return F.JUSTIFY_START

    def on_go(self, sender, args):
        label = str(self.CmbFamily.SelectedItem or "")
        if not label:
            self.StatusText.Text = "Pick a family to place."
            return
        cfg = F.get_configs(self.settings).get(
            str(self.CmbConfig.SelectedItem or ""))
        if cfg is None:
            self.StatusText.Text = "Pick a configuration."
            return
        symbol = None
        for lbl, fs in self.fam_items:
            if lbl == label:
                symbol = fs
                break
        if symbol is None:
            self.StatusText.Text = "Pick a family to place."
            return
        self.result = {
            "label": label, "symbol": symbol,
            "spacing_mm": cfg["spacing_mm"],
            "endpoints": cfg["endpoints"],
            "rotation_deg": cfg["rotation_deg"],
            "justify": self.justify(),
            "config": str(self.CmbConfig.SelectedItem or ""),
        }
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


# ----------------------------------------------------------------- selection
class LineFilter(ISelectionFilter):
    def AllowElement(self, elem):
        return isinstance(elem, CurveElement)

    def AllowReference(self, ref, point):
        return False


_TERRAIN_CATS = set()
for _n in ("OST_Toposolid", "OST_Topography", "OST_Floors",
           "OST_Roofs"):
    if hasattr(BuiltInCategory, _n):
        _TERRAIN_CATS.add(int(getattr(BuiltInCategory, _n)))


class TerrainFilter(ISelectionFilter):
    def AllowElement(self, elem):
        try:
            return FR.id_value(elem.Category.Id) in _TERRAIN_CATS
        except Exception:
            return False

    def AllowReference(self, ref, point):
        return False


def pick_one(sel_filter, prompt):
    try:
        r = uidoc.Selection.PickObject(ObjectType.Element, sel_filter,
                                       prompt)
        return doc.GetElement(r.ElementId)
    except Exception:            # ESC / right-click
        return None


# ----------------------------------------------------------------------- run
def main():
    settings = load_settings()

    fams = placeable_symbols()
    if not fams:
        log("No placeable point families in this model.")
        log.close()
        forms.alert("This model has no placeable point families "
                    "(one-level / work-plane based) - load one and "
                    "re-run.", exitscript=True)

    win = FenceWindow(settings, fams,
                      "{} placeable family type(s) in this "
                      "model".format(len(fams)))
    win.ShowDialog()
    if win.result is None:
        log("Cancelled - nothing placed.")
        log.close()
        script.exit()
    opt = win.result

    settings[F.SETTINGS_FAMILY] = opt["label"]
    settings[F.SETTINGS_JUSTIFY] = opt["justify"]
    settings[F.SETTINGS_LAST] = opt["config"]
    try:
        save_settings(settings)
    except Exception:
        pass

    log("Family **{}**, config **{}**: spacing **{:g} mm**, "
        "justification **{}**, endpoints **{}**, rotation "
        "**{:+g} deg**.".format(
            opt["label"], opt["config"], opt["spacing_mm"],
            opt["justify"], "on" if opt["endpoints"] else "off",
            opt["rotation_deg"]))

    line_el = pick_one(LineFilter(), "Pick the LINE to fence along")
    if line_el is None:
        log("No line picked - nothing placed.")
        log.close()
        script.exit()
    terrain = pick_one(TerrainFilter(),
                       "Pick the TERRAIN (toposolid / topography / "
                       "floor / roof)")
    if terrain is None:
        log("No terrain picked - nothing placed.")
        log.close()
        script.exit()

    poly = FR.tessellate(line_el)
    if not poly:
        log.close()
        forms.alert("That line has no geometry curve.",
                    exitscript=True)
    length = F.poly_length(poly)
    if length <= 1e-9:
        log.close()
        forms.alert("That line has no length.", exitscript=True)
    closed = F.is_closed(poly)
    log("Line **{}**: **{:.3f} m** along its length{}.".format(
        line_el.Id, length * 0.3048, ", CLOSED loop" if closed else ""))

    spacing_ft = UnitUtils.ConvertToInternalUnits(
        opt["spacing_mm"], UnitTypeId.Millimeters)
    dists = F.stations(length, spacing_ft, opt["justify"],
                       opt["endpoints"], closed)
    if not dists:
        log.close()
        forms.alert("No stations to place - spacing {} mm on a "
                    "{:.3f} m line with endpoints off leaves "
                    "nothing.".format(opt["spacing_mm"],
                                      length * 0.3048),
                    exitscript=True)
    if len(dists) > F.MAX_INSTANCES:
        log.close()
        forms.alert("{} instances would be placed - more than the "
                    "{} sanity cap. Check the spacing ({} mm on a "
                    "{:.1f} m line).".format(
                        len(dists), F.MAX_INSTANCES,
                        opt["spacing_mm"], length * 0.3048),
                    exitscript=True)
    log("**{}** station(s) along the line.".format(len(dists)))

    view3d = FR.find_view3d(doc)
    if view3d is None:
        log.close()
        forms.alert("No 3D view found in the model - create one and "
                    "re-run.", exitscript=True)

    ri = FR.make_intersector(view3d)
    ray_z = FR.ray_start_z([terrain, line_el])
    terrain_id = FR.id_value(terrain.Id)
    log("Ray-casting in 3D view **{}** onto **{}** (id {}).".format(
        view3d.Name, _name(terrain), terrain_id))
    levels = FR.sorted_levels(doc)

    t = Transaction(doc, "Fence")
    t.Start()
    try:
        records, missed, failed, why = FR.place_instances(
            doc, opt["symbol"], poly, dists, terrain_id, ri, ray_z,
            levels, extra_rot=math.radians(opt["rotation_deg"]))
        t.Commit()
    except Exception:
        try:
            t.RollBack()
        except Exception:
            pass
        raise

    if missed:
        log("! **{}** station(s) had NO terrain hit (the line runs "
            "off the terrain?): {}".format(
                len(missed),
                ", ".join("{:.1f} m".format(d * 0.3048)
                          for d in missed[:12]) +
                (" ..." if len(missed) > 12 else "")))
    if failed:
        log("! **{}** placement(s) failed ({})".format(
            failed, why or "family not placeable by point + level?"))
    log("**{}** instance(s) placed.".format(len(records)))

    fence_id = None
    if records:
        try:
            base = os.path.join(get_export_folder(doc),
                                "project_files")
            rec = {
                "line_uid": line_el.UniqueId,
                "terrain_uid": terrain.UniqueId,
                "family": opt["label"],
                "spacing_mm": opt["spacing_mm"],
                "endpoints": opt["endpoints"],
                "rotation_deg": opt["rotation_deg"],
                "justify": opt["justify"],
                "config": opt["config"],
                "instances": records,
                "updated": datetime.datetime.now().strftime(
                    "%Y-%m-%dT%H:%M:%S"),
            }
            fence_id = F.add_fence(base, rec)
            log("Recorded as **fence {}** - move the line or reshape "
                "the terrain, then run **Update Fence**.".format(
                    fence_id))
        except Exception as ex:
            log("! could not record the fence for updates: "
                "{}".format(ex))

    log.close()
    if not records:
        forms.alert("Nothing placed - no terrain hits under the "
                    "line. Check the terrain is visible in the 3D "
                    "view used for ray-casting ('{}') and the line "
                    "runs above it.".format(view3d.Name))
    else:
        forms.alert("Fence done: {} instance(s) placed{}{}{}.".format(
            len(records),
            ", {} station(s) missed the terrain".format(len(missed))
            if missed else "",
            ", {} failed".format(failed) if failed else "",
            " - recorded as fence {} for Update Fence".format(
                fence_id) if fence_id else ""))


main()
