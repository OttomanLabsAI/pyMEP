# -*- coding: utf-8 -*-
"""Fence - pick a line and a terrain, place a family along the line.

One dialog first: the family (searchable), a named spacing
CONFIGURATION (spacing + place-at-endpoints, with create / edit /
delete), and the justification (Start / Centre / End - where the
spacing counts from; Centre splits the leftover evenly). Then pick
the LINE and the TERRAIN in the view. Every station is ray-cast
straight down onto the picked terrain and the instance lands ON the
ground, rotated so its X axis follows the line's direction there.

Works on straight, curved and closed model lines (tessellated and
walked by distance). Revit 2022-2026.
"""

__title__  = "Fence"
__author__ = "Glent Group"

import math
import os
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

from pymep_config import load_settings, save_settings
from pymep_log import Logger
import pymep_fence as F

from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    CurveElement,
    ElementTransformUtils,
    FamilySymbol,
    FilteredElementCollector,
    FindReferenceTarget,
    Level,
    Line,
    ReferenceIntersector,
    RevitLinkInstance,
    Transaction,
    UnitTypeId,
    UnitUtils,
    View3D,
    XYZ,
)
from Autodesk.Revit.DB.Structure import StructuralType
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()
log = Logger(output, "Fence")

XAML_PATH = os.path.join(
    os.path.dirname(os.path.abspath(sys.modules["pymep_config"].__file__)),
    "pymep_fence.xaml")

MIN_HIT_PROXIMITY = 1e-9

log("### Fence")


def id_value(eid):
    try:
        return eid.Value            # Revit 2024+
    except AttributeError:
        return eid.IntegerValue     # Revit 2023 and earlier


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
            self.TxtCfgName.Text = name
            self.TxtSpacing.Text = "{:g}".format(cfg["spacing_mm"])
            self.ChkEnds.IsChecked = bool(cfg["endpoints"])
            self.StatusText.Text = ""
        except Exception:
            pass

    def on_cfg_save(self, sender, args):
        try:
            F.upsert_config(self.settings, self.TxtCfgName.Text,
                            self.TxtSpacing.Text,
                            bool(self.ChkEnds.IsChecked))
        except ValueError as ex:
            self.StatusText.Text = str(ex)
            return
        except Exception as ex:
            self.StatusText.Text = str(ex)
            return
        try:
            save_settings(self.settings)
        except Exception:
            pass
        name = (self.TxtCfgName.Text or "").strip()
        self._fill_configs(name)
        self.StatusText.Text = ""

    def on_cfg_delete(self, sender, args):
        try:
            name = str(self.CmbConfig.SelectedItem or "")
            F.delete_config(self.settings, name)
            try:
                save_settings(self.settings)
            except Exception:
                pass
            self._fill_configs(None)
        except Exception as ex:
            self.StatusText.Text = str(ex)

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
        try:
            spacing = float(self.TxtSpacing.Text)
        except Exception:
            spacing = 0.0
        if spacing <= 0:
            self.StatusText.Text = ("Spacing must be a positive "
                                    "number (mm).")
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
            "label": label, "symbol": symbol, "spacing_mm": spacing,
            "endpoints": bool(self.ChkEnds.IsChecked),
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
            return id_value(elem.Category.Id) in _TERRAIN_CATS
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


# ----------------------------------------------------------------- raybounce
def find_view3d():
    av = doc.ActiveView
    if isinstance(av, View3D) and not av.IsTemplate:
        return av
    for v in FilteredElementCollector(doc).OfClass(View3D):
        if not v.IsTemplate:
            return v
    return None


def topmost_hit(ri, origin, direction, terrain_id):
    """The picked terrain's TOP surface under the ray - smallest
    proximity among hits on that element only."""
    refs = ri.Find(origin, direction)
    if refs is None or refs.Count == 0:
        return None
    best = None
    for rc in refs:
        if rc.Proximity <= MIN_HIT_PROXIMITY:
            continue
        ref = rc.GetReference()
        el = doc.GetElement(ref.ElementId)
        if isinstance(el, RevitLinkInstance):
            continue
        if id_value(ref.ElementId) != terrain_id:
            continue
        if best is None or rc.Proximity < best.Proximity:
            best = rc
    if best is None:
        return None
    return best.GetReference().GlobalPoint


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

    log("Family **{}**, spacing **{:g} mm**, justification "
        "**{}**, endpoints **{}**.".format(
            opt["label"], opt["spacing_mm"], opt["justify"],
            "on" if opt["endpoints"] else "off"))

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

    curve = line_el.GeometryCurve
    if curve is None:
        log.close()
        forms.alert("That line has no geometry curve.",
                    exitscript=True)
    poly = [(p.X, p.Y, p.Z) for p in curve.Tessellate()]
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

    view3d = find_view3d()
    if view3d is None:
        log.close()
        forms.alert("No 3D view found in the model - create one and "
                    "re-run.", exitscript=True)

    ri = ReferenceIntersector(view3d)
    ri.TargetType = FindReferenceTarget.Face
    down = XYZ(0, 0, -1)
    # ray start: just above the terrain + line (ReferenceIntersector
    # silently misses when the origin is kilometres from the geometry)
    tops = []
    for el in (terrain, line_el):
        try:
            bb = el.get_BoundingBox(None)
            if bb is not None:
                tops.append(bb.Max.Z)
        except Exception:
            pass
    ray_z = (max(tops) + 10.0) if tops else 30000.0
    terrain_id = id_value(terrain.Id)
    log("Ray-casting in 3D view **{}** onto **{}** (id {}).".format(
        view3d.Name, _name(terrain), terrain_id))

    levels = sorted(
        [l for l in FilteredElementCollector(doc).OfClass(Level)],
        key=lambda l: l.Elevation)

    def level_for(z):
        best = None
        for l in levels:
            if l.Elevation <= z + 1e-6:
                best = l
        return best or (levels[0] if levels else None)

    placed, missed, failed = 0, [], 0
    symbol = opt["symbol"]
    t = Transaction(doc, "Fence")
    t.Start()
    try:
        try:
            if not symbol.IsActive:
                symbol.Activate()
                doc.Regenerate()
        except Exception:
            pass
        for d in dists:
            p, tang = F.point_at(poly, d)
            hit = topmost_hit(ri, XYZ(p[0], p[1], ray_z), down,
                              terrain_id)
            if hit is None:
                missed.append(d)
                continue
            lvl = level_for(hit.Z)
            try:
                inst = doc.Create.NewFamilyInstance(
                    XYZ(hit.X, hit.Y, hit.Z), symbol, lvl,
                    StructuralType.NonStructural)
                # belt and braces: the level overload usually honours
                # the Z, but forcing the offset makes it certain
                if lvl is not None:
                    for bip in ("INSTANCE_FREE_HOST_OFFSET_PARAM",
                                "INSTANCE_ELEVATION_PARAM"):
                        try:
                            par = inst.get_Parameter(
                                getattr(BuiltInParameter, bip))
                            if par is not None and not par.IsReadOnly:
                                par.Set(hit.Z - lvl.Elevation)
                                break
                        except Exception:
                            continue
                ang = math.atan2(tang[1], tang[0])
                if abs(ang) > 1e-9:
                    axis = Line.CreateBound(
                        XYZ(hit.X, hit.Y, hit.Z),
                        XYZ(hit.X, hit.Y, hit.Z + 1.0))
                    ElementTransformUtils.RotateElement(
                        doc, inst.Id, axis, ang)
                placed += 1
            except Exception as ex:
                failed += 1
                if failed == 1:
                    log("! placement failed at {:.3f} m: {}".format(
                        d * 0.3048, ex))
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
        log("! **{}** placement(s) failed (family not placeable "
            "by point + level?).".format(failed))
    log("**{}** instance(s) placed.".format(placed))
    log.close()
    if placed == 0:
        forms.alert("Nothing placed - no terrain hits under the "
                    "line. Check the terrain is visible in the 3D "
                    "view used for ray-casting ('{}') and the line "
                    "runs above it.".format(view3d.Name))
    else:
        forms.alert("Fence done: {} instance(s) placed{}{}.".format(
            placed,
            ", {} station(s) missed the terrain".format(len(missed))
            if missed else "",
            ", {} failed".format(failed) if failed else ""))


main()
