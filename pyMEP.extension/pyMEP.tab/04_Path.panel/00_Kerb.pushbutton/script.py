# -*- coding: utf-8 -*-
"""Kerb - lay kerb units along a picked line, draped onto the
terrain like a fence.

One dialog first: the kerb FAMILY (searchable), the UNIT LENGTH, the
ANGLE parameter and an optional LENGTH parameter. Then pick the LINE
and the TERRAIN. The units lay END-TO-END from the line's start -
each sits at its bay's CENTRE, rotated to the line's plan direction
there (curved lines get the curve's tangent), ray-cast straight down
onto the terrain. The terrain's slope ALONG the line at each unit is
written to the angle parameter as -90..+90 degrees (positive climbs
in the line's direction); the length parameter (when named) receives
the unit's length, so the LAST unit comes up short instead of
overhanging.

Kerbs are placed, not tracked - re-run after the line or terrain
changes. IronPython 2.7 / Revit 2022-2026.
"""

__title__  = "Kerb"
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
import pymep_fence_revit as FR
import pymep_path as P

from Autodesk.Revit.DB import (BuiltInCategory, CurveElement,
                               Transaction, UnitTypeId, UnitUtils,
                               XYZ)
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()
log = Logger(output, "Kerb")

XAML_PATH = os.path.join(
    os.path.dirname(os.path.abspath(sys.modules["pymep_config"].__file__)),
    "pymep_kerb.xaml")

log("### Kerb")

NONE_LABEL = "(none)"


class KerbWindow(forms.WPFWindow):
    """Family + unit length + parameter names; remembered in pyMEP
    settings."""

    def __init__(self, settings, labels):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self.result = None
        self.labels = labels
        self._last = {}
        fam, ln, ang, lnp, sfit = P.kerb_settings(settings)
        self._param_cache = {}
        self._fill(self.CmbKerb, labels, "")
        self._select(self.CmbKerb, fam)
        self.TxtLength.Text = "{:g}".format(ln)
        self._fill_params()
        self.CmbAngleParam.Text = ang
        self.CmbLenParam.Text = lnp
        self.ChkSlopeFit.IsChecked = bool(sfit)
        self.CmbKerb.SelectionChanged += self._on_family_changed
        self.TxtInfo.Text = "{} placeable famil{} found.".format(
            len(labels), "y" if len(labels) == 1 else "ies")

    def _fill_params(self):
        """The parameter dropdowns list the PICKED family's own
        writable instance parameters."""
        try:
            lbl = self._picked()
            if lbl in self._param_cache:
                names = self._param_cache[lbl]
            else:
                sym = FR.symbol_by_label(doc, lbl,
                                         P.KERB_CATEGORIES) \
                    if lbl else None
                names = FR.family_instance_params(doc, sym)
                self._param_cache[lbl] = names
            for combo in (self.CmbAngleParam, self.CmbLenParam):
                keep = combo.Text
                combo.Items.Clear()
                for nm in names:
                    combo.Items.Add(nm)
                combo.Text = keep
        except Exception:
            pass

    def _on_family_changed(self, sender, args):
        self._fill_params()

    @staticmethod
    def _fill(combo, labels, needle):
        combo.Items.Clear()
        combo.Items.Add(NONE_LABEL)
        needle = (needle or "").strip().lower()
        for lbl in labels:
            if needle and needle not in lbl.lower():
                continue
            combo.Items.Add(lbl)
        combo.SelectedIndex = 0

    @staticmethod
    def _select(combo, label):
        if not label:
            combo.SelectedIndex = 0
            return
        for i in range(combo.Items.Count):
            if str(combo.Items[i]) == label:
                combo.SelectedIndex = i
                return
        combo.Items.Add(label)
        combo.SelectedIndex = combo.Items.Count - 1

    def _picked(self):
        lbl = str(self.CmbKerb.SelectedItem or NONE_LABEL)
        return "" if lbl == NONE_LABEL else lbl

    def on_kerb_search(self, sender, args):
        try:
            needle = (self.TxtKerbSearch.Text or "").strip()
            current = self._picked()
            if current:
                self._last["kerb"] = current
            self._fill(self.CmbKerb, self.labels, needle)
            want = None
            for cand in (current, self._last.get("kerb")):
                if want is not None:
                    break
                if cand:
                    for i in range(self.CmbKerb.Items.Count):
                        if str(self.CmbKerb.Items[i]) == cand:
                            want = i
                            break
            if want is None:
                want = 1 if (needle and
                             self.CmbKerb.Items.Count > 1) else 0
            self.CmbKerb.SelectedIndex = want
            box = self.TxtKerbSearch
            if not box.IsKeyboardFocusWithin:
                box.Focus()
                box.CaretIndex = len(box.Text or "")
        except Exception:
            pass

    def on_go(self, sender, args):
        fam = self._picked()
        if not fam:
            self.StatusText.Text = "Pick a kerb family."
            return
        try:
            ln = float(self.TxtLength.Text)
        except Exception:
            ln = 0.0
        if ln <= 0:
            self.StatusText.Text = ("Unit length must be a positive "
                                    "number (mm).")
            return
        self.result = {
            "family": fam,
            "length_mm": ln,
            "angle_param": (self.CmbAngleParam.Text or "").strip(),
            "length_param": (self.CmbLenParam.Text or "").strip(),
            "slope_fit": bool(self.ChkSlopeFit.IsChecked),
        }
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


class LineFilter(ISelectionFilter):
    def AllowElement(self, elem):
        return isinstance(elem, CurveElement)

    def AllowReference(self, ref, point):
        return False


_TERRAIN_CATS = FR.terrain_cat_ids()


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
    except Exception:
        return None


def main():
    settings = load_settings()
    labels = [lbl for lbl, _fs in
              FR.placeable_symbols(doc, P.KERB_CATEGORIES)]
    if not labels:
        log("No placeable kerb families found.")
        log.close()
        forms.alert("No placeable family found in Generic Models / "
                    "Structural Framing / Site - load the kerb "
                    "family first.", exitscript=True)

    win = KerbWindow(settings, labels)
    win.ShowDialog()
    if win.result is None:
        log("Cancelled - nothing placed.")
        log.close()
        script.exit()
    opt = win.result

    settings[P.SETTINGS_KERB_FAMILY] = opt["family"]
    settings[P.SETTINGS_KERB_LENGTH] = opt["length_mm"]
    settings[P.SETTINGS_KERB_ANGLE_PARAM] = opt["angle_param"]
    settings[P.SETTINGS_KERB_LENGTH_PARAM] = opt["length_param"]
    settings[P.SETTINGS_KERB_SLOPE_FIT] = opt["slope_fit"]
    try:
        save_settings(settings)
    except Exception:
        pass

    symbol = FR.symbol_by_label(doc, opt["family"],
                                P.KERB_CATEGORIES)
    if symbol is None:
        log.close()
        forms.alert("Family '{}' is not in this model.".format(
            opt["family"]), exitscript=True)

    line_el = pick_one(LineFilter(), "Pick the LINE to kerb along")
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
    if not poly or F.poly_length(poly) <= 1e-9:
        log.close()
        forms.alert("That line has no length.", exitscript=True)
    length = F.poly_length(poly)
    closed = F.is_closed(poly)
    log("Line **{}**: **{:.3f} m**{}.".format(
        line_el.Id, length * 0.3048,
        ", CLOSED loop" if closed else ""))

    unit_ft = FR.mm2ft(opt["length_mm"])
    min_ft = FR.mm2ft(P.KERB_MIN_MM)

    view3d = FR.find_view3d(doc)
    if view3d is None:
        log.close()
        forms.alert("No 3D view found in the model - create one and "
                    "re-run.", exitscript=True)
    ri = FR.make_intersector(view3d)
    ray_z = FR.ray_start_z([terrain, line_el])
    terrain_id = FR.id_value(terrain.Id)
    levels = FR.sorted_levels(doc)

    def _z_at(st):
        pt = F.point_at(poly, st)[0]
        h = FR.topmost_hit(doc, ri, XYZ(pt[0], pt[1], ray_z),
                           terrain_id)
        return h.Z if h is not None else None

    # entries = (centre station, length for the LENGTH param, angle)
    entries = []
    if opt["slope_fit"]:
        # PYTHAGORAS WALK: each unit is the hypotenuse lying on the
        # ground - its PLAN advance is sqrt(unit^2 - rise^2), so the
        # next unit starts exactly where this one ends and they
        # TOUCH on any slope
        s_pos = 0.0
        while len(entries) <= F.MAX_INSTANCES:
            rem = length - s_pos
            if rem <= min_ft:
                break
            z0 = _z_at(s_pos)
            adv = min(unit_ft, rem)
            dz = 0.0
            for _i in range(3):     # settle the fit
                z1 = _z_at(min(s_pos + adv, length))
                dz = (z1 - z0) if (z0 is not None and
                                   z1 is not None) else 0.0
                adv2 = P.slope_fit_advance(unit_ft, dz)
                if abs(adv2 - adv) <= FR.mm2ft(1.0):
                    adv = adv2
                    break
                adv = adv2
            if adv + 1e-9 >= rem:
                # the CUT closing piece spans what is left
                z1 = _z_at(length)
                dzr = (z1 - z0) if (z0 is not None and
                                    z1 is not None) else 0.0
                entries.append((s_pos + rem / 2.0,
                                (rem * rem + dzr * dzr) ** 0.5,
                                P.slope_angle_deg(dzr, rem)))
                break
            entries.append((s_pos + adv / 2.0, unit_ft,
                            P.slope_angle_deg(dz, adv)))
            s_pos += adv
    else:
        # flat PLAN spacing (the original behaviour)
        marks = F.stations(length, unit_ft, F.JUSTIFY_START, True,
                           closed)
        if closed and marks:
            marks = marks + [length]
        for mid, width in F.panel_bays(marks, min_ft):
            d = min(width / 2.0, FR.mm2ft(500.0))
            pb = F.point_at(poly, mid - d)[0]
            pf = F.point_at(poly, mid + d)[0]
            zb, zf = _z_at(mid - d), _z_at(mid + d)
            ang_deg = 0.0
            if zb is not None and zf is not None:
                run = ((pf[0] - pb[0]) ** 2 +
                       (pf[1] - pb[1]) ** 2) ** 0.5
                ang_deg = P.slope_angle_deg(zf - zb, run)
            entries.append((mid, width, ang_deg))

    if not entries:
        log.close()
        forms.alert("No kerb units fit - the line is shorter than "
                    "the unit length.", exitscript=True)
    if len(entries) > F.MAX_INSTANCES:
        log.close()
        forms.alert("{} units would be placed - over the {} sanity "
                    "cap. Check the unit length.".format(
                        len(entries), F.MAX_INSTANCES),
                    exitscript=True)
    log("**{}** unit(s) along the line{}.".format(
        len(entries),
        " (slope fit: units touch on the slope)"
        if opt["slope_fit"] else ""))

    placed, missed, no_angle = 0, 0, 0
    t = Transaction(doc, "Kerb")
    t.Start()
    try:
        try:
            if not symbol.IsActive:
                symbol.Activate()
                doc.Regenerate()
        except Exception:
            pass
        for mid, width, ang_deg in entries:
            p, tang = F.point_at(poly, mid)
            hit = FR.topmost_hit(doc, ri, XYZ(p[0], p[1], ray_z),
                                 terrain_id)
            if hit is None:
                missed += 1
                continue
            lvl = FR.level_for(levels, hit.Z)
            rot = math.atan2(tang[1], tang[0])
            try:
                inst = FR.place_one(doc, symbol, hit, lvl, rot)
            except Exception as ex:
                log("! placement failed: {}".format(ex))
                continue
            placed += 1
            if opt["angle_param"]:
                if not FR.set_angle_param(inst, opt["angle_param"],
                                          ang_deg):
                    no_angle += 1
            if opt["length_param"]:
                FR.set_panel_width(inst, width,
                                   opt["length_param"])
        t.Commit()
    except Exception:
        try:
            t.RollBack()
        except Exception:
            pass
        raise

    if missed:
        log("! **{}** unit(s) had NO terrain hit.".format(missed))
    if no_angle:
        log("! parameter '{}' missing on {} unit(s).".format(
            opt["angle_param"], no_angle))
    log("**{}** kerb unit(s) placed. Kerbs are not tracked - "
        "re-run after the line or terrain changes.".format(placed))
    log.close()
    forms.alert("Kerb done: {} unit(s) placed{}.".format(
        placed, ", {} missed the terrain".format(missed)
        if missed else ""))


main()
