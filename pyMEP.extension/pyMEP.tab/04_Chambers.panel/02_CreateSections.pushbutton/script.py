# -*- coding: utf-8 -*-
"""Create Chamber Sections - pick chamber family instances in ONE dialog and
build a section view on each side that cuts pipework, each looking inward
toward the chamber centre and aligned to the chamber's rotation.

The dialog (pymep_chamber_sections.xaml) asks for everything at once:
  * WHICH chambers: the current selection, or a chamber family type (with a
    search box) and a tick list of its placed instances by Mark.
  * The section box, either TYPED in mm - OFFSET from the chamber centre to
    each section plane, HEIGHT (centred on the chamber centre elevation)
    and DEPTH (far clip measured from the plane inward), the width
    following the chamber footprint plus a 500 mm margin each side - or
    FROM THE CHAMBER'S PARAMETERS: the names of its length parameters along
    its own X and Y and for its height (instance first, then type) plus a
    CLEARANCE, so every section is the chamber plus the clearance on each
    side. A chamber missing a parameter falls back to its bounding box and
    is reported.
  * The section view type: one for every side, or one per final side letter.
  * Whether to run the PIPEWORK CHECK (on by default).
Everything is remembered in Settings for the next run.

For each chosen chamber:
  * Candidate sections sit on the chamber's four LOCAL sides (so they follow
    the chamber's plan rotation): +X, +Y, -X, -Y.
  * With the pipework check on, each side's section plane is tested against
    every pipe, conduit, duct, cable tray and their fittings in the model
    and its loaded links. Runs are tested on their centreline; fittings on
    the runs between their CONNECTORS (never on their bounding box). The
    chambers being sectioned, every other instance of their family types
    and anything nested in them are left out of the test - a chamber whose
    family is a fitting or accessory category is not pipework, and its
    footprint would otherwise 'cut' every one of its own sides. A side
    whose plane nothing crosses (within the crop width / height) would
    show an empty vault wall, so it is dropped.
    If NO side cuts anything (empty chamber, or no MEP in the model at all)
    all four sides are kept and the report says so.
  * The surviving sections are named "{Mark} SIDE A", "{Mark} SIDE B", ...
    in side order, so the letters always run A, B, C without gaps. The
    whole Mark is used ("LV1/Z1 SIDE A" - the zone part is identity, LV
    numbers repeat across zones). If the chamber has no Mark, the
    ElementId is used as the stem. (This matches the naming Match Sections
    and Chamber Plans produce.)
  * Each section's placement relative to its chamber is stored automatically
    (the same association records Match Sections saves), so Update Positions
    can re-place the sections after the chamber moves or rotates. No separate
    associate step is needed.

A Section ViewFamilyType must exist in the project (every template has one).

IronPython 2.7: pure ASCII, no f-strings, LF endings.
"""

__title__  = "Create\nChamber Sections"
__author__ = "Glent Group"

import math
import os
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
    ViewFamily, ViewSection, View, BuiltInParameter, BuiltInCategory,
    ElementId, Element, LocationPoint, Line, RevitLinkInstance, StorageType,
)

from pyrevit import revit, forms, script

import pymep_section_cut as SC
import pymep_chamber_sections as CS
from pymep_config import load_settings, save_settings

doc = revit.doc
uidoc = revit.uidoc
out = script.get_output()

XAML_PATH = os.path.join(os.path.dirname(os.path.abspath(CS.__file__)),
                         "pymep_chamber_sections.xaml")

MM_PER_FOOT = 304.8
SIDE_LETTERS = ("A", "B", "C", "D")

# Local outward directions for the four sides, BEFORE the chamber's rotation is
# applied. Index lines up with SIDE_LETTERS: A=+X, B=+Y, C=-X, D=-Y.
SIDE_OUTWARD = ((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0))

# Plan-view label of each local side, for the report.
SIDE_LABELS = ("+X", "+Y", "-X", "-Y")


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


# ---------------------------------------------------------------------------
# 1. Section ViewFamilyTypes (CreateSection needs one)
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

vft_options = []
for vft in section_vfts:
    vft_options.append({"label": _elem_name(vft), "vft": vft})
vft_options.sort(key=lambda d: d["label"].lower())
vft_labels = [d["label"] for d in vft_options]
vft_by_label = {}
for d in vft_options:
    vft_by_label[d["label"]] = d["vft"]


# ---------------------------------------------------------------------------
# 2. Chamber candidates: the selection, and every placed point-based family
#    type (for the dialog's type list)
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

type_options = []
for key, insts in inst_by_typeid.items():
    sym = sym_by_typeid.get(key)
    if sym is None:
        continue
    type_options.append({
        "label": "{0}   ({1} placed)".format(_type_label(sym), len(insts)),
        "typeid": key,
        "insts": insts,
    })
type_options.sort(key=lambda d: d["label"].lower())


def _mm0(text):
    # A non-negative mm field, or None.
    if text is None:
        return None
    try:
        t = text.strip().lower().replace(",", ".")
    except Exception:
        return None
    if t.endswith("mm"):
        t = t[:-2].strip()
    if not t:
        return None
    try:
        v = float(t)
    except Exception:
        return None
    if v != v or v - v != 0 or v < 0:
        return None
    return v


# ---------------------------------------------------------------------------
# 3. The dialog
# ---------------------------------------------------------------------------
class SectionsWindow(forms.WPFWindow):

    def __init__(self, types, selected, type_labels, remembered):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self.result = None
        self._ready = False
        self._filling = False
        self._types = types
        self._visible = []            # indexes into _types shown in LstTypes
        self._boxes = []              # (CheckBox, FamilyInstance)
        self._sel = list(selected)

        n_sel = len(self._sel)
        if n_sel:
            self.RbSelection.Content = (
                "use the {0} selected chamber(s)".format(n_sel))
            self.RbSelection.IsChecked = True
        else:
            self.RbSelection.Content = (
                "use the selected chambers (nothing is selected)")
            self.RbSelection.IsEnabled = False
            self.RbType.IsChecked = True

        for cmb in (self.CmbTypeAll,) + self._side_combos():
            cmb.Items.Clear()
            for lb in type_labels:
                cmb.Items.Add(lb)
        self._select(self.CmbTypeAll, remembered["type"])
        for letter, cmb in zip(SIDE_LETTERS, self._side_combos()):
            self._select(cmb, remembered["side_types"].get(letter)
                         or remembered["type"])
        self.ChkSameType.IsChecked = bool(remembered["same"])
        self.ChkCutOnly.IsChecked = bool(remembered["cut_only"])
        self.TxtOffset.Text = CS.mm_text(remembered["offset"])
        self.TxtHeight.Text = CS.mm_text(remembered["height"])
        self.TxtDepth.Text = CS.mm_text(remembered["depth"])
        self.TxtParamX.Text = sizing["px"]
        self.TxtParamY.Text = sizing["py"]
        self.TxtParamH.Text = sizing["ph"]
        self.TxtClear.Text = CS.mm_text(sizing["clear"])
        if sizing["mode"] == CS.SIZE_PARAMS:
            self.RbSizeParams.IsChecked = True
        else:
            self.RbSizeFixed.IsChecked = True

        self._fill_types()
        self._ready = True
        self._sync()

    # -- section type combos -------------------------------------------------
    def _side_combos(self):
        return (self.CmbTypeA, self.CmbTypeB, self.CmbTypeC, self.CmbTypeD)

    @staticmethod
    def _select(cmb, label):
        try:
            if label and cmb.Items.Contains(label):
                cmb.SelectedItem = label
            elif cmb.Items.Count:
                cmb.SelectedIndex = 0
        except Exception:
            pass

    # -- family type list ----------------------------------------------------
    def _current_type(self):
        try:
            idx = self.LstTypes.SelectedIndex
        except Exception:
            return None
        if idx < 0 or idx >= len(self._visible):
            return None
        return self._types[self._visible[idx]]

    def _fill_types(self):
        query = ""
        try:
            query = self.TxtTypeFilter.Text or ""
        except Exception:
            pass
        keep = CS.filter_labels([d["label"] for d in self._types], query)
        current = None
        cur = self._current_type()
        if cur is not None:
            current = self._types.index(cur)
        self._filling = True
        try:
            self._visible = keep
            self.LstTypes.Items.Clear()
            for i in keep:
                self.LstTypes.Items.Add(self._types[i]["label"])
            if current in keep:
                self.LstTypes.SelectedIndex = keep.index(current)
            elif len(keep) == 1:
                self.LstTypes.SelectedIndex = 0
        finally:
            self._filling = False
        self._fill_chambers(self._current_type())

    def _fill_chambers(self, tdict):
        from System.Windows.Controls import CheckBox
        from System.Windows import Thickness
        self.PnlChambers.Children.Clear()
        self._boxes = []
        rows = []
        for fi in (tdict["insts"] if tdict else []):
            mk = _get_mark(fi)
            rows.append(("{0}   (Id {1})".format(mk if mk else "<no mark>",
                                                  fi.Id.IntegerValue), fi))
        rows.sort(key=lambda r: r[0].lower())
        for label, fi in rows:
            cb = CheckBox()
            cb.Content = label
            cb.IsChecked = (len(rows) == 1)
            cb.Margin = Thickness(0, 2, 0, 2)
            cb.Checked += self._on_box
            cb.Unchecked += self._on_box
            self.PnlChambers.Children.Add(cb)
            self._boxes.append((cb, fi))
        self._sync()

    def _ticked(self):
        return [fi for cb, fi in self._boxes if cb.IsChecked]

    def _set_all(self, on):
        for cb, _fi in self._boxes:
            cb.IsChecked = on
        self._sync()

    # -- state -> UI -----------------------------------------------------------
    def _sync(self):
        if not getattr(self, "_ready", False):
            return
        try:
            from System.Windows import Visibility
            by_type = bool(self.RbType.IsChecked)
            self.PnlType.IsEnabled = by_type
            params = bool(self.RbSizeParams.IsChecked)
            self.PnlFixed.IsEnabled = not params
            self.PnlParams.IsEnabled = params
            same = bool(self.ChkSameType.IsChecked)
            self.CmbTypeAll.Visibility = (Visibility.Visible if same
                                          else Visibility.Collapsed)
            self.PnlPerSide.Visibility = (Visibility.Collapsed if same
                                          else Visibility.Visible)
            if by_type:
                total = len(self._boxes)
                if total:
                    self.TxtChamberCount.Text = (
                        "{0} of {1} chamber(s) ticked.".format(
                            len(self._ticked()), total))
                else:
                    self.TxtChamberCount.Text = (
                        "Pick a chamber family type above.")
            else:
                self.TxtChamberCount.Text = (
                    "Sections go around the {0} selected chamber(s).".format(
                        len(self._sel)))
            self.StatusText.Text = ""
        except Exception:
            pass

    # -- handlers ----------------------------------------------------------------
    def on_source_changed(self, sender, args):
        self._sync()

    def on_type_filter(self, sender, args):
        if not getattr(self, "_ready", False):
            return
        self._fill_types()

    def on_type_selected(self, sender, args):
        if not getattr(self, "_ready", False) or self._filling:
            return
        self._fill_chambers(self._current_type())

    def on_tick_all(self, sender, args):
        self._set_all(True)

    def on_tick_none(self, sender, args):
        self._set_all(False)

    def on_same_changed(self, sender, args):
        self._sync()

    def on_size_mode(self, sender, args):
        self._sync()

    def _on_box(self, sender, args):
        self._sync()

    def on_go(self, sender, args):
        # Chambers
        if self.RbSelection.IsChecked and self._sel:
            chambers = list(self._sel)
            if len(chambers) == 1:
                sym = doc.GetElement(chambers[0].GetTypeId())
                source = _type_label(sym) if sym is not None else "(selection)"
            else:
                source = "(selection)"
        else:
            tdict = self._current_type()
            if tdict is None:
                self.StatusText.Text = "Pick a chamber family type."
                return
            chambers = self._ticked()
            if not chambers:
                self.StatusText.Text = "Tick at least one chamber to section."
                return
            source = tdict["label"]
        # Box: typed, or from the chamber's parameters + clearance
        vals = {"offset": None, "height": None, "depth": None}
        by_params = bool(self.RbSizeParams.IsChecked)
        names = {}
        clear = None
        if by_params:
            for key, box, label in (("px", self.TxtParamX, "Along X"),
                                    ("py", self.TxtParamY, "Along Y"),
                                    ("ph", self.TxtParamH, "Height")):
                nm = (box.Text or "").strip()
                if not nm:
                    self.StatusText.Text = (
                        "Give the chamber's parameter name for '{0}'."
                        .format(label))
                    return
                names[key] = nm
            clear = _mm0(self.TxtClear.Text)
            if clear is None:
                self.StatusText.Text = (
                    "Clearance must be a number of mm (0 or more).")
                return
        else:
            for key, box, label in (("offset", self.TxtOffset, "Offset"),
                                    ("height", self.TxtHeight, "Height"),
                                    ("depth", self.TxtDepth, "Depth")):
                v = CS.parse_mm(box.Text)
                if v is None:
                    self.StatusText.Text = (
                        "{0} must be a positive number of mm.".format(label))
                    return
                vals[key] = v
        # Section types, by FINAL letter
        same = bool(self.ChkSameType.IsChecked)
        types = {}
        if same:
            lb = self.CmbTypeAll.SelectedItem
            if not lb:
                self.StatusText.Text = "Pick a section type."
                return
            for letter in SIDE_LETTERS:
                types[letter] = lb
        else:
            for letter, cmb in zip(SIDE_LETTERS, self._side_combos()):
                lb = cmb.SelectedItem
                if not lb:
                    self.StatusText.Text = (
                        "Pick a section type for SIDE {0}.".format(letter))
                    return
                types[letter] = lb
        self.result = {
            "chambers": chambers, "source": source,
            "offset": vals["offset"], "height": vals["height"],
            "depth": vals["depth"], "same": same, "types": types,
            "cut_only": bool(self.ChkCutOnly.IsChecked),
            "size_mode": CS.SIZE_PARAMS if by_params else CS.SIZE_FIXED,
            "px": names.get("px", ""), "py": names.get("py", ""),
            "ph": names.get("ph", ""), "clear": clear,
        }
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


# Sheets Full Pipeline drives this script headless: options arrive on the
# sys module (which survives the pymep_* purge above), the outcome goes
# back the same way.
_PIPE = getattr(sys, "_pymep_pipeline", None) or {}
_HEADLESS = _PIPE.get("sections")

_settings = load_settings()
sizing = CS.size_settings(_settings)
if _HEADLESS:
    _rem = CS.section_settings(_settings)
    _types = {}
    for _letter in SIDE_LETTERS:
        _want = (_rem["type"] if _rem["same"]
                 else (_rem["side_types"].get(_letter) or _rem["type"]))
        _types[_letter] = _want if _want in vft_labels else vft_labels[0]
    _result = {
        "chambers": list(_HEADLESS["chambers"]), "source": "(pipeline)",
        "offset": _rem["offset"], "height": _rem["height"],
        "depth": _rem["depth"], "same": _rem["same"], "types": _types,
        "cut_only": _rem["cut_only"], "size_mode": sizing["mode"],
        "px": sizing["px"], "py": sizing["py"], "ph": sizing["ph"],
        "clear": sizing["clear"],
    }

    class _Result(object):
        pass

    win = _Result()
    win.result = _result
else:
    win = SectionsWindow(type_options, sel_insts, vft_labels,
                         CS.section_settings(_settings))
    win.ShowDialog()
    if not win.result:
        script.exit()

target_chambers = win.result["chambers"]
picked_type_label = win.result["source"]
offset_mm = win.result["offset"]
height_mm = win.result["height"]
depth_mm = win.result["depth"]
cut_only = win.result["cut_only"]
same_type = win.result["same"]
# side_vfts maps each FINAL side letter to the chosen ViewFamilyType.
side_vfts = {}
for letter in SIDE_LETTERS:
    side_vfts[letter] = vft_by_label[win.result["types"][letter]]

size_mode = win.result["size_mode"]
param_x, param_y, param_h = (win.result["px"], win.result["py"],
                             win.result["ph"])
clear_mm = win.result["clear"]

try:
    if offset_mm is not None:
        _settings[CS.SETTINGS_SECTION_OFFSET] = offset_mm
        _settings[CS.SETTINGS_SECTION_HEIGHT] = height_mm
        _settings[CS.SETTINGS_SECTION_DEPTH] = depth_mm
    _settings[CS.SETTINGS_SIZE_MODE] = size_mode
    if size_mode == CS.SIZE_PARAMS:
        _settings[CS.SETTINGS_SIZE_PARAM_X] = param_x
        _settings[CS.SETTINGS_SIZE_PARAM_Y] = param_y
        _settings[CS.SETTINGS_SIZE_PARAM_H] = param_h
        _settings[CS.SETTINGS_SIZE_CLEAR] = clear_mm
    _settings[CS.SETTINGS_SECTION_SAME_TYPE] = same_type
    _settings[CS.SETTINGS_SECTION_CUT_ONLY] = cut_only
    _settings[CS.SETTINGS_SECTION_SIDE_TYPES] = dict(win.result["types"])
    if same_type:
        _settings[CS.SETTINGS_SECTION_TYPE] = win.result["types"]["A"]
    save_settings(_settings)
except Exception:
    pass

offset_ft = (offset_mm / MM_PER_FOOT) if offset_mm else None
height_ft = (height_mm / MM_PER_FOOT) if height_mm else None
depth_ft = (depth_mm / MM_PER_FOOT) if depth_mm else None
clear_ft = (clear_mm / MM_PER_FOOT) if clear_mm else 0.0


def _param_len_ft(inst, name):
    # A LENGTH parameter by name - instance first, then its type. None when
    # absent, not a length, or not positive.
    if not name:
        return None
    holders = [inst]
    try:
        holders.append(doc.GetElement(inst.GetTypeId()))
    except Exception:
        pass
    for holder in holders:
        if holder is None:
            continue
        try:
            p = holder.LookupParameter(name)
        except Exception:
            p = None
        if p is None:
            continue
        try:
            if p.StorageType != StorageType.Double or not p.HasValue:
                continue
            v = p.AsDouble()
        except Exception:
            continue
        if v is not None and v > 0:
            return v
    return None


def _chamber_dims_ft(inst, half_lx, half_ly):
    # (dx, dy, dh, note): the chamber's size along its local X and Y and
    # its height from the named parameters, falling back to the bounding
    # box for any that is missing (the note says which).
    missing = []
    dx = _param_len_ft(inst, param_x)
    if dx is None:
        dx = 2.0 * half_lx
        missing.append(param_x)
    dy = _param_len_ft(inst, param_y)
    if dy is None:
        dy = 2.0 * half_ly
        missing.append(param_y)
    dh = _param_len_ft(inst, param_h)
    if dh is None:
        try:
            bb = inst.get_BoundingBox(None)
            dh = (bb.Max.Z - bb.Min.Z) if bb is not None else None
        except Exception:
            dh = None
        if not dh:
            dh = 2.0 * max(half_lx, half_ly)
        missing.append(param_h)
    note = ""
    if missing:
        note = "parameter(s) {0} not found - bounding box used".format(
            ", ".join("'{0}'".format(m) for m in missing))
    return dx, dy, dh, note


# ---------------------------------------------------------------------------
# 3b. Resolve each chamber's geometry and work out which sides actually CUT
#     pipework. A side whose section plane no pipe / conduit / duct crosses
#     is not created; the surviving sides are lettered A, B, C... in order.
# ---------------------------------------------------------------------------
# Width margin each side of the chamber footprint (depth comes from the
# dialog's section depth).
WIDTH_MARGIN_FT = 500.0 / MM_PER_FOOT      # 500 mm each side

# MEP runs tested against each section plane, and the fittings tested by
# bounding box. Names are resolved with getattr so a category missing from
# an older API is simply skipped.
RUN_CATS = ("OST_PipeCurves", "OST_Conduit", "OST_DuctCurves",
            "OST_FlexPipeCurves", "OST_FlexDuctCurves", "OST_CableTray")
FITTING_CATS = ("OST_PipeFitting", "OST_ConduitFitting", "OST_DuctFitting",
                "OST_CableTrayFitting", "OST_PipeAccessory",
                "OST_DuctAccessory")
RADIUS_PARAMS = ("RBS_PIPE_OUTER_DIAMETER", "RBS_CONDUIT_OUTER_DIAM_PARAM",
                 "RBS_CURVE_DIAMETER_PARAM", "RBS_CURVE_WIDTH_PARAM",
                 "RBS_CURVE_HEIGHT_PARAM", "RBS_CABLETRAY_WIDTH_PARAM",
                 "RBS_CABLETRAY_HEIGHT_PARAM")


def _t(p):
    return (p.X, p.Y, p.Z)


def _radius_ft(el):
    # Half the largest cross-section dimension readable on the run; 0 when
    # none. Only widens the cut test slightly, so a rough value is fine.
    best = 0.0
    for name in RADIUS_PARAMS:
        bip = getattr(BuiltInParameter, name, None)
        if bip is None:
            continue
        try:
            p = el.get_Parameter(bip)
            if p is not None and p.HasValue:
                v = p.AsDouble()
                if v is not None and v > best:
                    best = v
        except Exception:
            continue
    return best * 0.5


def _run_points(el, tf):
    # Centreline points of a run in WORLD feet (tf = link transform or None).
    loc = getattr(el, "Location", None)
    crv = getattr(loc, "Curve", None) if loc is not None else None
    if crv is None:
        return None
    try:
        if isinstance(crv, Line):
            pts = [crv.GetEndPoint(0), crv.GetEndPoint(1)]
        else:
            pts = list(crv.Tessellate())
    except Exception:
        return None
    if tf is not None:
        pts = [tf.OfPoint(p) for p in pts]
    return [_t(p) for p in pts]


def _connector_points(el, tf):
    # Connector origins of a fitting / accessory in WORLD feet, plus the
    # largest connector radius. (None, 0) when it has no connectors.
    cm = None
    try:
        mm = el.MEPModel
        cm = mm.ConnectorManager if mm is not None else None
    except Exception:
        cm = None
    if cm is None:
        return None, 0.0
    try:
        conns = list(cm.Connectors)
    except Exception:
        return None, 0.0
    pts = []
    radius = 0.0
    for c in conns:
        try:
            o = c.Origin
        except Exception:
            continue
        if o is None:
            continue
        if tf is not None:
            o = tf.OfPoint(o)
        pts.append(_t(o))
        r = 0.0
        try:
            r = c.Radius
        except Exception:
            r = 0.0
        if not r:
            try:
                r = max(c.Width, c.Height) * 0.5
            except Exception:
                r = 0.0
        if r and r > radius:
            radius = r
    return pts, radius


def _is_excluded(el, skip_ids):
    # The chambers themselves, and anything nested in them, are never
    # pipework - whatever category their family uses.
    if not skip_ids:
        return False
    try:
        if el.Id.IntegerValue in skip_ids:
            return True
    except Exception:
        pass
    try:
        sup = el.SuperComponent
        if sup is not None and sup.Id.IntegerValue in skip_ids:
            return True
    except Exception:
        pass
    return False


def _collect_mep(src_doc, tf, runs, skip_ids, tally):
    for name in RUN_CATS:
        bic = getattr(BuiltInCategory, name, None)
        if bic is None:
            continue
        try:
            els = FilteredElementCollector(src_doc).OfCategory(bic)\
                .WhereElementIsNotElementType().ToElements()
        except Exception:
            continue
        for el in els:
            if _is_excluded(el, skip_ids):
                tally["excluded"] += 1
                continue
            pts = _run_points(el, tf)
            if pts and len(pts) >= 2:
                runs.append((pts, _radius_ft(el)))
                tally["runs"] += 1
    for name in FITTING_CATS:
        bic = getattr(BuiltInCategory, name, None)
        if bic is None:
            continue
        try:
            els = FilteredElementCollector(src_doc).OfCategory(bic)\
                .WhereElementIsNotElementType().ToElements()
        except Exception:
            continue
        for el in els:
            if _is_excluded(el, skip_ids):
                tally["excluded"] += 1
                continue
            pts, radius = _connector_points(el, tf)
            if not pts:
                tally["no_conn"] += 1
                continue
            for p0, p1 in SC.pair_segments(pts):
                runs.append(([p0, p1], radius))
            tally["fittings"] += 1


def _mep_geometry(skip_ids):
    # Pipework runs (centreline points + radius) in world feet from the
    # host model and every LOADED link; fittings contribute the runs
    # between their connectors. Returns (runs, tally, links that
    # contributed).
    runs = []
    tally = {"runs": 0, "fittings": 0, "no_conn": 0, "excluded": 0}
    _collect_mep(doc, None, runs, skip_ids, tally)
    linked = 0
    try:
        link_insts = list(FilteredElementCollector(doc)
                          .OfClass(RevitLinkInstance))
    except Exception:
        link_insts = []
    for li in link_insts:
        try:
            ldoc = li.GetLinkDocument()
        except Exception:
            ldoc = None
        if ldoc is None:
            continue
        try:
            tf = li.GetTotalTransform()
        except Exception:
            continue
        before = len(runs)
        _collect_mep(ldoc, tf, runs, set(), tally)
        if len(runs) > before:
            linked += 1
    return runs, tally, linked


def _side_frame(side_idx, centre, angle, plane_ft, half_w, half_h, depth):
    # The crop frame of one side's section: origin on the section plane
    # (plane_ft out from the chamber centre), the right / up / look axes,
    # the crop half-width / half-height and the far-clip depth. Shared by
    # the cut test and the section box so both see the same cut.
    ox, oy = SIDE_OUTWARD[side_idx]
    ca, sa = math.cos(angle), math.sin(angle)
    out_x = ox * ca - oy * sa
    out_y = ox * sa + oy * ca
    out_dir = XYZ(out_x, out_y, 0.0).Normalize()

    # Section plane origin: out from the chamber centre.
    sec_origin = XYZ(centre.X + out_dir.X * plane_ft,
                     centre.Y + out_dir.Y * plane_ft,
                     centre.Z)

    # Look direction = back toward the centre.
    look = out_dir.Negate()
    up = XYZ(0.0, 0.0, 1.0)
    # Right = up x look (consistent perpendicular; CreateSection recomputes
    # right internally, but a clean orthonormal frame keeps the box square).
    right = up.CrossProduct(look).Normalize()

    return {"origin": sec_origin, "right": right, "up": up, "look": look,
            "half_w": half_w, "half_h": half_h, "depth": depth}


def _section_box(frame):
    t = Transform.Identity
    t.Origin = frame["origin"]
    t.BasisX = frame["right"]
    t.BasisY = frame["up"]
    t.BasisZ = frame["look"]

    # Local box: X = width (across), Y = height (world Z), Z = depth (look).
    # Far clip = the frame's depth, measured from the plane inward
    # (CreateSection sets far clip = Max.Z - Min.Z, and Min.Z is 0 here).
    box = BoundingBoxXYZ()
    box.Transform = t
    box.Min = XYZ(-frame["half_w"], -frame["half_h"], 0.0)
    box.Max = XYZ(frame["half_w"], frame["half_h"], frame["depth"])
    return box


def _count_cuts(frame, runs):
    # How many pipework runs this side's section plane cuts.
    sc_frame = (_t(frame["origin"]), _t(frame["right"]), _t(frame["up"]),
                _t(frame["look"]))
    hw, hh = frame["half_w"], frame["half_h"]
    n = 0
    for pts, radius in runs:
        if SC.polyline_cut(sc_frame, pts, hw, hh, radius):
            n += 1
    return n


# The chambers being sectioned - and every other placed instance of their
# family types - are never pipework, whatever category the family uses.
skip_ids = set()
_target_typeids = set()
for _inst in target_chambers:
    skip_ids.add(_inst.Id.IntegerValue)
    try:
        _target_typeids.add(_inst.GetTypeId().IntegerValue)
    except Exception:
        pass
for _tid, _insts in inst_by_typeid.items():
    if _tid in _target_typeids:
        for _fi in _insts:
            skip_ids.add(_fi.Id.IntegerValue)

mep_runs, mep_tally, mep_links = _mep_geometry(skip_ids)
have_mep = bool(mep_runs)

# Resolve each chamber's geometry and side plan; skip any without a location.
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
    # Names use the whole Mark (chamber_key trims it).
    stem = _sanitize(CS.chamber_key(mark)) if mark else "Id{0}".format(
        inst.Id.IntegerValue)
    half_lx, half_ly = _chamber_plan_halfspan(inst, angle)
    dims = None
    dims_note = ""
    frames = []
    if size_mode == CS.SIZE_PARAMS:
        dx, dy, dh, dims_note = _chamber_dims_ft(inst, half_lx, half_ly)
        dims = (dx, dy, dh)
        for i in range(len(SIDE_LETTERS)):
            plane, hw, hh, depth = CS.section_box_from_dims(
                i, dx, dy, dh, clear_ft)
            frames.append(_side_frame(i, centre, angle, plane, hw, hh,
                                      depth))
    else:
        for i in range(len(SIDE_LETTERS)):
            # Width = the chamber half-span across the look direction plus
            # the margin: local Y for the +X / -X sides, local X for +Y / -Y.
            hw = (half_ly if i in (0, 2) else half_lx) + WIDTH_MARGIN_FT
            frames.append(_side_frame(i, centre, angle, offset_ft, hw,
                                      height_ft * 0.5, depth_ft))
    if have_mep:
        counts = [_count_cuts(f, mep_runs) for f in frames]
    else:
        counts = [0] * len(frames)      # nothing to test: keep every side
    if cut_only:
        sides, all_kept = SC.plan_sides(counts)
    else:
        # Check off: every side under its own letter.
        sides = [(i, SIDE_LETTERS[i], SIDE_LETTERS[i])
                 for i in range(len(frames))]
        all_kept = False
    chamber_jobs.append({
        "inst": inst, "centre": centre, "angle": angle,
        "half_lx": half_lx, "half_ly": half_ly,
        "mark": mark, "stem": stem,
        "frames": frames, "counts": counts,
        "sides": sides, "all_kept": all_kept,
        "dims": dims, "dims_note": dims_note,
    })

if not chamber_jobs:
    forms.alert("None of the selected chambers had a usable location.",
                exitscript=True)

planned_total = sum(len(j["sides"]) for j in chamber_jobs)
needed_letters = SC.letters_needed([j["sides"] for j in chamber_jobs])
if not needed_letters:
    needed_letters = tuple(SIDE_LETTERS)


# ---------------------------------------------------------------------------
# 4. Create the planned sections
# ---------------------------------------------------------------------------
# Existing view names for uniqueness.
used_view_names = set()
for v in FilteredElementCollector(doc).OfClass(View):
    try:
        used_view_names.add(v.Name)
    except Exception:
        pass
view_names = set(used_view_names)

created = []          # (stem, local side idx, final letter, cuts, name)
assoc_jobs = []       # (section view, chamber inst, mark, letter, local letter)
errors = []           # (stem, letter, message)
t = Transaction(doc, "pyMEP: Create chamber sections ({0} chamber(s))".format(
    len(chamber_jobs)))
t.Start()
try:
    for job in chamber_jobs:
        for idx, local_letter, letter in job["sides"]:
            try:
                box = _section_box(job["frames"][idx])
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
            # Rename, falling back through _2, _3... if Revit still says
            # the name is taken (a clash our view list did not see).
            renamed = False
            last_ex = None
            for cand in [name] + ["{0}_{1}".format(name, i)
                                  for i in range(2, 7)]:
                try:
                    sec.Name = cand
                except Exception as ex:
                    last_ex = ex
                    continue
                name = cand
                view_names.add(cand)
                renamed = True
                break
            if not renamed:
                # Keep the auto-generated name; report the failure as an
                # error instead of stuffing the exception into the name.
                errors.append((job["stem"], letter,
                               "rename to '{0}' failed: {1}".format(
                                   name, last_ex)))
                try:
                    name = sec.Name
                except Exception:
                    name = "(auto name)"
            created.append((job["stem"], idx, letter, job["counts"][idx],
                            name))
            assoc_jobs.append((sec, job["inst"], job["mark"], letter,
                               local_letter))
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
    for sec, inst, mark, letter, local_letter in assoc_jobs:
        try:
            rec = links.make_record(sec, inst, mark)
        except Exception:
            rec = None
        if rec is None:
            continue
        rec["side"] = letter               # the letter in the view name
        rec["local_side"] = local_letter   # which chamber side it sits on
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
# Section type(s): collapse to one label if every side shares it, else list.
_side_type_names = [_elem_name(side_vfts[l]) for l in needed_letters]
if len(set(_side_type_names)) == 1:
    _section_type_summary = _side_type_names[0]
else:
    _section_type_summary = ", ".join(
        "{0}={1}".format(l, _elem_name(side_vfts[l])) for l in needed_letters)
out.print_md("**Family / source:** {0}  |  **Chambers:** {1}  |  "
             "**Section type:** {2}".format(
                 picked_type_label, len(chamber_jobs), _section_type_summary))
if size_mode == CS.SIZE_PARAMS:
    _box_desc = ("**Box:** from parameters '{0}' x '{1}' x '{2}' + {3:.0f} mm "
                 "clearance each side".format(param_x, param_y, param_h,
                                              clear_mm))
else:
    _box_desc = ("**Offset:** {0:.0f} mm  |  **Height:** {1:.0f} mm  |  "
                 "**Depth:** {2:.0f} mm".format(offset_mm, height_mm,
                                                 depth_mm))
out.print_md("{0}  |  **Sections created:** {1} of {2} planned  |  "
             "**Associations stored:** {3}".format(
                 _box_desc, len(created), planned_total, assoc_stored))
if size_mode == CS.SIZE_PARAMS:
    out.print_md("**Chamber sizes used** (X x Y x height):")
    for job in chamber_jobs:
        dx, dy, dh = job["dims"]
        out.print_md("- {0}: {1:.2f} x {2:.2f} x {3:.2f} m{4}".format(
            job["stem"], dx * 0.3048, dy * 0.3048, dh * 0.3048,
            "  -  " + job["dims_note"] if job["dims_note"] else ""))
_mep_note = ("**Pipework checked:** {0} run(s), {1} fitting(s) by their "
             "connectors".format(mep_tally["runs"], mep_tally["fittings"]))
if mep_tally["no_conn"]:
    _mep_note += " ({0} without connectors ignored)".format(
        mep_tally["no_conn"])
if mep_links:
    _mep_note += ", including {0} linked model(s)".format(mep_links)
if mep_tally["excluded"]:
    _mep_note += ("; {0} element(s) left out as chambers of the sectioned "
                  "family type(s) or nested in them".format(
                      mep_tally["excluded"]))
if not cut_only:
    _mep_note += ("  -  the pipework check is OFF, so every side was "
                  "created (counts shown for information).")
elif not have_mep:
    _mep_note += ("  -  no pipes, conduits, ducts or cable trays found in the "
                  "model or its loaded links, so the cut check was skipped and "
                  "every side was kept.")
out.print_md(_mep_note)
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
for stem, idx, letter, cuts, name in created:
    rows.append([stem, "SIDE " + letter,
                 "{0} ({1})".format(SIDE_LETTERS[idx], SIDE_LABELS[idx]),
                 str(cuts), _elem_name(side_vfts[letter]), name])
out.print_table(table_data=rows,
                columns=["Chamber", "Section", "Chamber side",
                         "Pipework cut", "Section type", "Section view"])

# Cuts counted on EVERY side, created or not, so a wrong keep / drop can
# be read straight off the report.
if have_mep:
    out.print_md("**Cuts per chamber side** (side letter = chamber side, "
                 "before re-lettering):")
    for job in chamber_jobs:
        out.print_md("- {0}: {1}".format(job["stem"], ", ".join(
            "{0} ({1}) = {2}".format(SIDE_LETTERS[i], SIDE_LABELS[i],
                                     job["counts"][i])
            for i in range(len(SIDE_LETTERS)))))

# Sides not created because nothing crosses their plane.
dropped = []
empty_chambers = []
if cut_only:
    for job in chamber_jobs:
        if job["all_kept"]:
            if have_mep:
                empty_chambers.append(job["stem"])
            continue
        kept_idx = set(s[0] for s in job["sides"])
        for i in range(len(SIDE_LETTERS)):
            if i not in kept_idx:
                dropped.append((job["stem"], i))
if dropped:
    out.print_md("**{0} side(s) not created - no pipework crosses the "
                 "section plane:**".format(len(dropped)))
    for stem, i in dropped:
        out.print_md("- {0}: chamber side {1} ({2})".format(
            stem, SIDE_LETTERS[i], SIDE_LABELS[i]))
if empty_chambers:
    out.print_md("**{0} chamber(s) where NO side cuts any pipework - all "
                 "four sides kept:** {1}".format(
                     len(empty_chambers), ", ".join(empty_chambers)))
if skipped:
    out.print_md("**{0} chamber(s) skipped:**".format(len(skipped)))
    for ident, msg in skipped:
        out.print_md("- {0}: {1}".format(ident, msg))
if errors:
    out.print_md("**{0} section(s) failed:**".format(len(errors)))
    for stem, letter, msg in errors:
        out.print_md("- {0} SIDE {1}: {2}".format(stem, letter, msg))

if _HEADLESS:
    _PIPE["out_sections"] = {
        "created": len(created), "failed": len(errors),
        "planned": planned_total,
        "view_ids": [sec.Id.IntegerValue for sec, _i, _m, _l, _ll in
                     assoc_jobs],
        "names": [name for _s, _i, _l, _c, name in created],
    }

# Keep the output window open.
