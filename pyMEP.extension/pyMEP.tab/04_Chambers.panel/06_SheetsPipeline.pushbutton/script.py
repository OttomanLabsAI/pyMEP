# -*- coding: utf-8 -*-
"""Sheets Full Pipeline - the whole chamber drawing set from one dialog.

Steps, in order, each run by the button that owns it (headless):
  1. Chamber Plans    - a plan view per chamber.
  2. Create Sections  - the sections that cut pipework.
  3. Sheets           - numbered sheets from a title block, N chambers per
                        sheet, each chamber's plan then sections in a row.
  4. Dimension        - optional: the centreline strings in every new
                        section.

The dialog has a TAB PER STEP carrying every setting that step's own
button asks for: the chambers (tick family types, search the Marks, tick
chambers); the source plan, plan extents and plan template; the section
box, section types and pipework check; the title block, sheet number and
name patterns with {n}, first number, chambers per sheet, scale, spacing,
sheet templates and viewport type; the dimension type. Everything is
written through to the settings each button remembers, so the buttons and
the pipeline always agree, and then the steps run.

ONE UNDO: the whole run sits inside a TransactionGroup that is assimilated
at the end, so every step's transactions - plans, sections, each sheet and
its viewports, the dimensions - collapse into a single entry on Revit's
undo list. One Ctrl+Z takes the lot back.

How the hand-off works: each step's options are left on the sys module
(sys._pymep_pipeline), which survives the pymep_* module purge every
button does, and the button's script is compiled and exec'd; it finds
the options, skips its own dialog, runs, and writes a summary back to the
same place. A step that stops early (an alert with exitscript) raises
SystemExit, which is caught and reported here; later steps still run.

IronPython 2.7: pure ASCII, no f-strings, LF endings.
"""

__title__ = "Sheets Full\nPipeline"
__author__ = "Glent Group"

import os
import sys

# Reload pymep_* lib modules so the script picks up the latest helpers.
for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (
    Transaction, TransactionGroup, View, ViewSheet, ViewType,
    ViewFamilyType, ViewFamily, FilteredElementCollector, FamilyInstance,
    LocationPoint, BuiltInParameter, BuiltInCategory, ElementId, Element,
    DimensionType, DimensionStyleType, FilteredWorksetCollector, WorksetKind,
)

from pyrevit import revit, forms, script

import pymep_chamber_sections as CS
import pymep_sheet_setup as SS
from pymep_config import load_settings, save_settings

doc = revit.doc
uidoc = revit.uidoc
out = script.get_output()

XAML_PATH = os.path.join(os.path.dirname(os.path.abspath(CS.__file__)),
                         "pymep_sheets_pipeline.xaml")
STEP_PLANS = "00_ChamberPlans.pushbutton"
STEP_SECTIONS = "02_CreateSections.pushbutton"
STEP_SHEET = "01_SheetSetup.pushbutton"
STEP_DIMS = "05_DimensionSection.pushbutton"
NO_TITLEBLOCK = u"(no title block)"
SIDE_LETTERS = ("A", "B", "C", "D")
PLAN_TYPES = (ViewType.FloorPlan, ViewType.CeilingPlan,
              ViewType.EngineeringPlan, ViewType.AreaPlan)


def _panel_dir():
    # The Chambers panel folder holding the step buttons. pyRevit's
    # script path may be the script file or its bundle folder depending
    # on the version, so walk up from every candidate until a folder that
    # holds the step buttons appears; the lib folder is the fixed fallback.
    starts = []
    try:
        starts.append(script.get_script_path())
    except Exception:
        pass
    try:
        starts.append(os.path.abspath(__file__))
    except Exception:
        pass
    starts.append(os.path.join(os.path.dirname(os.path.abspath(CS.__file__)),
                               "..", "pyMEP.tab", "04_Chambers.panel"))
    for start in starts:
        p = os.path.abspath(start)
        for _ in range(5):
            if os.path.isfile(os.path.join(p, STEP_PLANS, "script.py")):
                return p
            parent = os.path.dirname(p)
            if parent == p:
                break
            p = parent
    return os.path.abspath(starts[-1])


PANEL_DIR = _panel_dir()


def _name(el):
    try:
        n = el.Name
        if n:
            return n
    except Exception:
        pass
    try:
        return Element.Name.GetValue(el) or "?"
    except Exception:
        return "?"


def _get_mark(inst):
    p = None
    try:
        p = inst.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
    except Exception:
        p = None
    if p is None:
        try:
            p = inst.LookupParameter("Mark")
        except Exception:
            p = None
    if p is None:
        return None
    for getter in ("AsString", "AsValueString"):
        try:
            v = getattr(p, getter)()
        except Exception:
            v = None
        if v and v.strip():
            return v.strip()
    return None


def _type_label(sym):
    try:
        fam = sym.Family.Name
    except Exception:
        fam = "?"
    return "{0} : {1}".format(fam, _name(sym))


def _int_field(text, low):
    try:
        v = int((text or "").strip())
    except Exception:
        return None
    return v if v >= low else None


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


def _fill(cmb, names, want, first=None):
    # Fill a combo (optionally with a leading fixed entry) and select
    # `want` when present, else the first item.
    cmb.Items.Clear()
    if first is not None:
        cmb.Items.Add(first)
    for n in names:
        cmb.Items.Add(n)
    if want and cmb.Items.Contains(want):
        cmb.SelectedItem = want
    elif cmb.Items.Count:
        cmb.SelectedIndex = 0


# ---------------------------------------------------------------------------
# 1. What the dialog offers
# ---------------------------------------------------------------------------
inst_by_typeid = {}
sym_by_typeid = {}
for fi in FilteredElementCollector(doc).OfClass(FamilyInstance)\
        .WhereElementIsNotElementType().ToElements():
    if not isinstance(fi.Location, LocationPoint):
        continue
    if not _get_mark(fi):
        continue                     # the whole set hangs off the Mark
    tid = fi.GetTypeId()
    if tid is None or tid == ElementId.InvalidElementId:
        continue
    key = tid.IntegerValue
    inst_by_typeid.setdefault(key, []).append(fi)
    if key not in sym_by_typeid:
        sym_by_typeid[key] = doc.GetElement(tid)

if not inst_by_typeid:
    forms.alert("No placed point-based family instances with a Mark found.\n\n"
                "Chamber plans, sections and sheets are all named from the "
                "chamber's Mark - populate it first.", exitscript=True)

type_options = []
for key, insts in inst_by_typeid.items():
    sym = sym_by_typeid.get(key)
    if sym is None:
        continue
    type_options.append({
        "label": "{0}   ({1} placed)".format(_type_label(sym), len(insts)),
        "typeid": key, "insts": insts,
    })
type_options.sort(key=lambda d: d["label"].lower())

active = doc.ActiveView

# Views: plans (source), plan / section templates.
plan_by_name = {}
plan_templates = {}
section_templates = {}
for v in FilteredElementCollector(doc).OfClass(View):
    try:
        vt = v.ViewType
        is_tmpl = bool(v.IsTemplate)
    except Exception:
        continue
    nm = _name(v)
    if not nm or nm == "?":
        continue
    if vt in PLAN_TYPES:
        if is_tmpl:
            plan_templates.setdefault(nm, v)
        else:
            plan_by_name.setdefault(nm, v)
    elif vt == ViewType.Section and is_tmpl:
        section_templates.setdefault(nm, v)
if not plan_by_name:
    forms.alert("No plan views in this project - the chamber plans need one "
                "to duplicate or to take a level from.", exitscript=True)
plan_names = sorted(plan_by_name, key=lambda s: s.lower())
plan_template_names = sorted(plan_templates, key=lambda s: s.lower())
section_template_names = sorted(section_templates, key=lambda s: s.lower())

# Scope boxes (seeds for the scope-box route).
sb_by_name = {}
for el in FilteredElementCollector(doc)\
        .OfCategory(BuiltInCategory.OST_VolumeOfInterest)\
        .WhereElementIsNotElementType():
    sb_by_name.setdefault(_name(el), el)
seed_names = sorted(sb_by_name, key=lambda s: s.lower())

# User worksets for the scope boxes (workshared models only).
workset_names = []
try:
    if doc.IsWorkshared:
        workset_names = sorted(
            set(ws.Name for ws in FilteredWorksetCollector(doc)
                .OfKind(WorksetKind.UserWorkset)),
            key=lambda s: s.lower())
except Exception:
    workset_names = []

# Section view family types.
vft_by_label = {}
for vft in FilteredElementCollector(doc).OfClass(ViewFamilyType):
    try:
        if vft.ViewFamily == ViewFamily.Section:
            vft_by_label.setdefault(_name(vft), vft)
    except Exception:
        continue
vft_labels = sorted(vft_by_label, key=lambda s: s.lower())
if not vft_labels:
    forms.alert("No Section view type found in this project.", exitscript=True)

# Title blocks, viewport types, dimension types.
titleblocks = {}
try:
    for tb in FilteredElementCollector(doc)\
            .OfCategory(BuiltInCategory.OST_TitleBlocks)\
            .WhereElementIsElementType().ToElements():
        titleblocks.setdefault(_type_label(tb), tb)
except Exception:
    pass
tb_names = sorted(titleblocks, key=lambda s: s.lower())

viewport_types = {}
try:
    for vpt in FilteredElementCollector(doc)\
            .OfCategory(BuiltInCategory.OST_Viewports)\
            .WhereElementIsElementType().ToElements():
        nm = None
        try:
            nm = Element.Name.GetValue(vpt)
        except Exception:
            nm = _name(vpt)
        if nm and nm != "?":
            viewport_types.setdefault(nm, vpt)
except Exception:
    pass
viewport_names = sorted(viewport_types, key=lambda s: s.lower())

dim_types = {}
for dt in FilteredElementCollector(doc).OfClass(DimensionType):
    try:
        if dt.StyleType != DimensionStyleType.Linear:
            continue
    except Exception:
        continue
    nm = _name(dt)
    if nm and nm != "?":
        dim_types.setdefault(nm, dt)
dim_names = sorted(dim_types, key=lambda s: s.lower())

existing_numbers = set()
for sh in FilteredElementCollector(doc).OfClass(ViewSheet):
    try:
        existing_numbers.add((sh.SheetNumber or "").strip())
    except Exception:
        pass

_settings = load_settings()
rem = CS.pipeline_settings(_settings)
rem_plans = CS.plans_settings(_settings)
rem_size = CS.size_settings(_settings)
rem_sec = CS.section_settings(_settings)
rem_sheet = SS.sheet_settings(_settings)
rem_dim = CS.dim_settings(_settings)


# ---------------------------------------------------------------------------
# 2. The dialog - one tab per step
# ---------------------------------------------------------------------------
class PipeWindow(forms.WPFWindow):

    def __init__(self):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self.result = None
        self._ready = False
        self._type_state = dict((d["typeid"], False) for d in type_options)
        self._chamber_state = {}      # element id -> ticked
        self._type_boxes = []
        self._chamber_boxes = []
        self._pool = []               # chambers of the ticked types

        # --- 2 Plans ---
        want_plan = None
        if active is not None and active.ViewType in PLAN_TYPES \
                and _name(active) in plan_by_name:
            want_plan = _name(active)
        _fill(self.CmbSourcePlan, plan_names, want_plan)
        if not seed_names:
            self.RbExtScope.Content = (
                "scope box: (no scope box in the project to copy)")
            self.RbExtScope.IsEnabled = False
        if rem_plans["extents"] == CS.EXTENTS_SCOPE and seed_names:
            self.RbExtScope.IsChecked = True
        else:
            self.RbExtCrop.IsChecked = True
        self.TxtW.Text = CS.mm_text(rem_plans["width"])
        self.TxtD.Text = CS.mm_text(rem_plans["depth"])
        if rem_size["mode"] == CS.SIZE_PARAMS:
            self.RbCropParams.IsChecked = True
        else:
            self.RbCropFixed.IsChecked = True
        _fill(self.CmbSeed, seed_names,
              CS.pick_seed_name(seed_names, rem_plans["seed"]))
        _fill(self.CmbWorkset, workset_names, rem_plans["workset"],
              first=CS.CURRENT_WORKSET)
        if not workset_names:
            self.CmbWorkset.IsEnabled = False
        tmpl_choices = [CS.PLANS_TEMPLATE_ACTIVE, CS.PLANS_TEMPLATE_NONE] + \
            plan_template_names
        _fill(self.CmbPlanTemplate, tmpl_choices, rem_plans["template"])

        # --- 3 Sections ---
        self.TxtOffset.Text = CS.mm_text(rem_sec["offset"])
        self.TxtHeight.Text = CS.mm_text(rem_sec["height"])
        self.TxtDepth.Text = CS.mm_text(rem_sec["depth"])
        self.TxtParamX.Text = rem_size["px"]
        self.TxtParamY.Text = rem_size["py"]
        self.TxtParamH.Text = rem_size["ph"]
        self.TxtClear.Text = CS.mm_text(rem_size["clear"])
        if rem_size["mode"] == CS.SIZE_PARAMS:
            self.RbSizeParams.IsChecked = True
        else:
            self.RbSizeFixed.IsChecked = True
        self.ChkSameType.IsChecked = bool(rem_sec["same"])
        _fill(self.CmbTypeAll, vft_labels, rem_sec["type"])
        for letter, cmb in zip(SIDE_LETTERS, self._side_combos()):
            _fill(cmb, vft_labels,
                  rem_sec["side_types"].get(letter) or rem_sec["type"])
        self.ChkCutOnly.IsChecked = bool(rem_sec["cut_only"])

        # --- 4 Sheets ---
        _fill(self.CmbTitleBlock, tb_names, rem["titleblock"],
              first=NO_TITLEBLOCK)
        if not rem["titleblock"] and len(tb_names):
            self.CmbTitleBlock.SelectedIndex = 1
        self.TxtNumber.Text = rem["number"]
        self.TxtName.Text = rem["name"]
        self.TxtStart.Text = "{0}".format(rem["start"])
        self.TxtPerSheet.Text = "{0}".format(rem["per_sheet"])
        self.CmbScale.Items.Clear()
        for n in SS.SCALE_CHOICES:
            self.CmbScale.Items.Add(SS.scale_text(n))
        self.CmbScale.Text = SS.scale_text(rem_sheet["scale"])
        self.TxtGap.Text = CS.mm_text(rem_sheet["gap"])
        self.TxtLeft.Text = CS.mm_text(rem_sheet["left"])
        self.TxtTop.Text = CS.mm_text(rem_sheet["top"])
        self.TxtLabel.Text = CS.mm_text(rem_sheet["label"])
        _fill(self.CmbSheetPlanTemplate, plan_template_names,
              rem_sheet["plan_template"], first=SS.LEAVE_TEMPLATE)
        _fill(self.CmbSheetSectionTemplate, section_template_names,
              rem_sheet["section_template"], first=SS.LEAVE_TEMPLATE)
        _fill(self.CmbViewportType, viewport_names,
              rem_sheet["viewport_type"], first=SS.DEFAULT_VIEWPORT)

        # --- 5 Dimensions ---
        self.ChkDims.IsChecked = bool(rem["dims"])
        _fill(self.CmbDimType, dim_names,
              CS.pick_dim_type_name(dim_names, rem_dim["dim_type"]))

        self._fill_types()
        self._ready = True
        self._sync()

    def _side_combos(self):
        return (self.CmbTypeA, self.CmbTypeB, self.CmbTypeC, self.CmbTypeD)

    # -- 1 Chambers ------------------------------------------------------------
    def _fill_types(self):
        from System.Windows.Controls import CheckBox
        from System.Windows import Thickness
        self.PnlTypes.Children.Clear()
        self._type_boxes = []
        query = ""
        try:
            query = self.TxtTypeFilter.Text or ""
        except Exception:
            pass
        keep = CS.filter_labels([d["label"] for d in type_options], query)
        for i in keep:
            d = type_options[i]
            cb = CheckBox()
            cb.Content = d["label"]
            cb.IsChecked = self._type_state.get(d["typeid"], False)
            cb.Margin = Thickness(0, 2, 0, 2)
            cb.Tag = d["typeid"]
            cb.Checked += self._on_type_box
            cb.Unchecked += self._on_type_box
            self.PnlTypes.Children.Add(cb)
            self._type_boxes.append(cb)

    def _on_type_box(self, sender, args):
        try:
            self._type_state[int(sender.Tag)] = bool(sender.IsChecked)
        except Exception:
            pass
        if getattr(self, "_ready", False):
            self._fill_chambers()

    def _fill_chambers(self):
        from System.Windows.Controls import CheckBox
        from System.Windows import Thickness
        rows = []
        for d in type_options:
            if not self._type_state.get(d["typeid"]):
                continue
            short = d["label"].split("   (")[0]
            for fi in d["insts"]:
                rows.append(("{0}   ({1})".format(_get_mark(fi) or "", short),
                             fi))
        rows.sort(key=lambda r: SS.natural_key(r[0]))
        self._pool = rows
        query = ""
        try:
            query = self.TxtMarkFilter.Text or ""
        except Exception:
            pass
        keep = CS.filter_labels([r[0] for r in rows], query)
        self.PnlChambers.Children.Clear()
        self._chamber_boxes = []
        for i in keep:
            label, fi = rows[i]
            cb = CheckBox()
            cb.Content = label
            cb.IsChecked = self._chamber_state.get(fi.Id.IntegerValue, False)
            cb.Margin = Thickness(0, 2, 0, 2)
            cb.Tag = fi.Id.IntegerValue
            cb.Checked += self._on_chamber_box
            cb.Unchecked += self._on_chamber_box
            self.PnlChambers.Children.Add(cb)
            self._chamber_boxes.append(cb)
        self._sync()

    def _on_chamber_box(self, sender, args):
        try:
            self._chamber_state[int(sender.Tag)] = bool(sender.IsChecked)
        except Exception:
            pass
        self._sync()

    def _chambers(self):
        # Ticked chambers, in Mark order, restricted to the ticked types.
        return [fi for _label, fi in self._pool
                if self._chamber_state.get(fi.Id.IntegerValue)]

    def _set_all(self, on):
        for cb in self._chamber_boxes:
            cb.IsChecked = on
        self._sync()

    # -- state -> UI -------------------------------------------------------------
    def _sync(self):
        if not getattr(self, "_ready", False):
            return
        try:
            from System.Windows import Visibility
            n = len(self._chambers())
            per = _int_field(self.TxtPerSheet.Text, 1) or 0
            sheets = ((n + per - 1) // per) if per else 0
            self.TxtCount.Text = ("{0} chamber(s) ticked of {1} shown - {2} "
                                  "sheet(s) at {3} per sheet.".format(
                                      n, len(self._pool), sheets, per))
            scope = bool(self.RbExtScope.IsChecked)
            self.PnlScope.IsEnabled = scope
            self.PnlCrop.IsEnabled = not scope
            self.PnlCropFixed.IsEnabled = bool(self.RbCropFixed.IsChecked)
            params = bool(self.RbSizeParams.IsChecked)
            self.PnlFixed.IsEnabled = not params
            self.PnlParams.IsEnabled = params
            same = bool(self.ChkSameType.IsChecked)
            self.CmbTypeAll.Visibility = (Visibility.Visible if same
                                          else Visibility.Collapsed)
            self.PnlPerSide.Visibility = (Visibility.Collapsed if same
                                          else Visibility.Visible)
            self.CmbDimType.IsEnabled = bool(self.ChkDims.IsChecked)
            self.StatusText.Text = ""
        except Exception:
            pass

    # -- handlers -----------------------------------------------------------------
    def on_type_filter(self, sender, args):
        if getattr(self, "_ready", False):
            self._fill_types()

    def on_mark_filter(self, sender, args):
        if getattr(self, "_ready", False):
            self._fill_chambers()

    def on_tick_all(self, sender, args):
        self._set_all(True)

    def on_tick_none(self, sender, args):
        self._set_all(False)

    def on_extents(self, sender, args):
        self._sync()

    def on_crop_size(self, sender, args):
        self._sync()

    def on_size_mode(self, sender, args):
        self._sync()

    def on_same_changed(self, sender, args):
        self._sync()

    def on_dims(self, sender, args):
        self._sync()

    def _fail(self, text):
        self.StatusText.Text = text
        return None

    def on_go(self, sender, args):
        o = {}
        # 1 chambers
        o["chambers"] = self._chambers()
        if not o["chambers"]:
            return self._fail("Chambers tab: tick at least one family type "
                              "and one chamber.")
        # 2 plans
        o["source"] = plan_by_name.get(self.CmbSourcePlan.SelectedItem)
        if o["source"] is None:
            return self._fail("Plans tab: pick the source plan.")
        o["extents"] = (CS.EXTENTS_SCOPE if self.RbExtScope.IsChecked
                        else CS.EXTENTS_CROP)
        o["crop_mode"] = (CS.SIZE_PARAMS if self.RbCropParams.IsChecked
                          else CS.SIZE_FIXED)
        if o["extents"] == CS.EXTENTS_CROP and o["crop_mode"] == CS.SIZE_FIXED:
            w = CS.parse_mm(self.TxtW.Text)
            d = CS.parse_mm(self.TxtD.Text)
            if w is None or d is None:
                return self._fail("Plans tab: crop size along X and Y must "
                                  "be positive mm.")
            o["width"], o["depth"] = w, d
        else:
            o["width"], o["depth"] = rem_plans["width"], rem_plans["depth"]
        o["seed"] = self.CmbSeed.SelectedItem if seed_names else None
        if o["extents"] == CS.EXTENTS_SCOPE and not o["seed"]:
            return self._fail("Plans tab: pick the seed scope box.")
        ws = self.CmbWorkset.SelectedItem
        o["workset"] = ws if ws in workset_names else u""
        o["plan_template"] = self.CmbPlanTemplate.SelectedItem or \
            CS.PLANS_TEMPLATE_ACTIVE
        # 3 sections
        o["size_mode"] = (CS.SIZE_PARAMS if self.RbSizeParams.IsChecked
                          else CS.SIZE_FIXED)
        need_params = (o["size_mode"] == CS.SIZE_PARAMS or
                       (o["extents"] == CS.EXTENTS_CROP and
                        o["crop_mode"] == CS.SIZE_PARAMS))
        o["px"] = (self.TxtParamX.Text or "").strip()
        o["py"] = (self.TxtParamY.Text or "").strip()
        o["ph"] = (self.TxtParamH.Text or "").strip()
        o["clear"] = _mm0(self.TxtClear.Text)
        if need_params:
            if not o["px"] or not o["py"] or not o["ph"]:
                return self._fail("Sections tab: give the chamber's parameter "
                                  "names along X, along Y and for the height.")
            if o["clear"] is None:
                return self._fail("Sections tab: clearance must be a number "
                                  "of mm (0 or more).")
        if o["clear"] is None:
            o["clear"] = rem_size["clear"]
        if o["size_mode"] == CS.SIZE_FIXED:
            vals = {}
            for key, box, label in (("offset", self.TxtOffset, "Offset"),
                                    ("height", self.TxtHeight, "Height"),
                                    ("depth", self.TxtDepth, "Depth")):
                v = CS.parse_mm(box.Text)
                if v is None:
                    return self._fail("Sections tab: {0} must be a positive "
                                      "number of mm.".format(label))
                vals[key] = v
            o.update(vals)
        else:
            o["offset"], o["height"], o["depth"] = (
                rem_sec["offset"], rem_sec["height"], rem_sec["depth"])
        o["same"] = bool(self.ChkSameType.IsChecked)
        types = {}
        if o["same"]:
            lb = self.CmbTypeAll.SelectedItem
            if not lb:
                return self._fail("Sections tab: pick a section type.")
            for letter in SIDE_LETTERS:
                types[letter] = lb
        else:
            for letter, cmb in zip(SIDE_LETTERS, self._side_combos()):
                lb = cmb.SelectedItem
                if not lb:
                    return self._fail("Sections tab: pick a section type for "
                                      "SIDE {0}.".format(letter))
                types[letter] = lb
        o["types"] = types
        o["cut_only"] = bool(self.ChkCutOnly.IsChecked)
        # 4 sheets
        o["titleblock"] = self.CmbTitleBlock.SelectedItem or NO_TITLEBLOCK
        o["number"] = (self.TxtNumber.Text or "").strip()
        o["name"] = (self.TxtName.Text or "").strip()
        if not o["number"]:
            return self._fail("Sheets tab: give a sheet number pattern "
                              "(with {n}).")
        o["start"] = _int_field(self.TxtStart.Text, 0)
        if o["start"] is None:
            return self._fail("Sheets tab: first number must be a whole "
                              "number.")
        o["per"] = _int_field(self.TxtPerSheet.Text, 1)
        if o["per"] is None:
            return self._fail("Sheets tab: chambers per sheet must be 1 or "
                              "more.")
        o["scale"] = SS.parse_scale(self.CmbScale.Text)
        if o["scale"] is None:
            return self._fail("Sheets tab: scale must look like 1:20 (or "
                              "just 20).")
        for key, box, label in (("gap", self.TxtGap, "Gap"),
                                ("left", self.TxtLeft, "Left margin"),
                                ("top", self.TxtTop, "Top margin"),
                                ("label", self.TxtLabel, "Title room")):
            v = _mm0(box.Text)
            if v is None:
                return self._fail("Sheets tab: {0} must be a number of mm "
                                  "(0 or more).".format(label))
            o[key] = v
        o["sheet_plan_template"] = SS.template_choice(
            self.CmbSheetPlanTemplate.SelectedItem)
        o["sheet_section_template"] = SS.template_choice(
            self.CmbSheetSectionTemplate.SelectedItem)
        o["viewport_type"] = SS.template_choice(
            self.CmbViewportType.SelectedItem)
        # 5 dimensions
        o["dims"] = bool(self.ChkDims.IsChecked)
        o["dim_type"] = self.CmbDimType.SelectedItem
        if o["dims"] and dim_names and not o["dim_type"]:
            return self._fail("Dimensions tab: pick a dimension type.")
        self.result = o
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


win = PipeWindow()
win.ShowDialog()
if not win.result:
    script.exit()
opt = win.result

# Write every choice through to the settings each step's button reads, so
# the buttons remember the same values and the headless runs pick them up.
try:
    S = _settings
    S[CS.SETTINGS_PLANS_EXTENTS] = opt["extents"]
    S[CS.SETTINGS_PLANS_WIDTH] = opt["width"]
    S[CS.SETTINGS_PLANS_DEPTH] = opt["depth"]
    if opt["seed"]:
        S[CS.SETTINGS_PLANS_SEED] = opt["seed"]
    S[CS.SETTINGS_PLANS_WORKSET] = opt["workset"]
    S[CS.SETTINGS_PLANS_TEMPLATE] = opt["plan_template"]
    S[CS.SETTINGS_SIZE_MODE] = opt["size_mode"]
    S[CS.SETTINGS_SIZE_PARAM_X] = opt["px"] or CS.DEFAULT_SIZE_PARAM_X
    S[CS.SETTINGS_SIZE_PARAM_Y] = opt["py"] or CS.DEFAULT_SIZE_PARAM_Y
    S[CS.SETTINGS_SIZE_PARAM_H] = opt["ph"] or CS.DEFAULT_SIZE_PARAM_H
    S[CS.SETTINGS_SIZE_CLEAR] = opt["clear"]
    S[CS.SETTINGS_SECTION_OFFSET] = opt["offset"]
    S[CS.SETTINGS_SECTION_HEIGHT] = opt["height"]
    S[CS.SETTINGS_SECTION_DEPTH] = opt["depth"] if opt["size_mode"] == \
        CS.SIZE_FIXED else rem_sec["depth"]
    S[CS.SETTINGS_SECTION_SAME_TYPE] = opt["same"]
    S[CS.SETTINGS_SECTION_SIDE_TYPES] = dict(opt["types"])
    S[CS.SETTINGS_SECTION_TYPE] = opt["types"]["A"]
    S[CS.SETTINGS_SECTION_CUT_ONLY] = opt["cut_only"]
    S[SS.SETTINGS_SHEET_SCALE] = opt["scale"]
    S[SS.SETTINGS_SHEET_GAP] = opt["gap"]
    S[SS.SETTINGS_SHEET_LEFT] = opt["left"]
    S[SS.SETTINGS_SHEET_TOP] = opt["top"]
    S[SS.SETTINGS_SHEET_LABEL] = opt["label"]
    S[SS.SETTINGS_SHEET_PLAN_TEMPLATE] = opt["sheet_plan_template"]
    S[SS.SETTINGS_SHEET_SECTION_TEMPLATE] = opt["sheet_section_template"]
    S[SS.SETTINGS_SHEET_VIEWPORT_TYPE] = opt["viewport_type"]
    S[CS.SETTINGS_PIPE_NUMBER] = opt["number"]
    S[CS.SETTINGS_PIPE_NAME] = opt["name"]
    S[CS.SETTINGS_PIPE_START] = opt["start"]
    S[CS.SETTINGS_PIPE_PER_SHEET] = opt["per"]
    S[CS.SETTINGS_PIPE_TITLEBLOCK] = opt["titleblock"]
    S[CS.SETTINGS_PIPE_DIMS] = opt["dims"]
    if opt["dim_type"]:
        S[CS.SETTINGS_DIM_TYPE] = opt["dim_type"]
    save_settings(S)
except Exception as ex:
    out.print_md("- Could not save the settings: {0}".format(ex))


# ---------------------------------------------------------------------------
# 3. Running a step: the owning button's script, headless
# ---------------------------------------------------------------------------
def _run_step(label, folder, key, options):
    path = os.path.join(PANEL_DIR, folder, "script.py")
    pipe = getattr(sys, "_pymep_pipeline", None)
    if pipe is None:
        pipe = {}
        sys._pymep_pipeline = pipe
    # Only this step's options are visible while it runs.
    for k in ("plans", "sections", "sheet", "dims"):
        pipe.pop(k, None)
    pipe[key] = options
    pipe.pop("out_" + key, None)
    out.print_md("## {0}".format(label))
    try:
        fh = open(path, "rb")
        try:
            code = compile(fh.read(), path, "exec")
        finally:
            fh.close()
        exec(code, {"__name__": "_pipeline_step", "__file__": path})
        ok, why = True, ""
    except SystemExit:
        ok, why = False, "stopped early - see its message above"
    except Exception as ex:
        ok, why = False, "{0}".format(ex)
    pipe.pop(key, None)
    return ok, why, pipe.get("out_" + key) or {}


summary = []
chambers = opt["chambers"]
marks = []
for inst in chambers:
    mk = CS.chamber_key(_get_mark(inst))
    if mk and mk not in marks:
        marks.append(mk)

out.print_md("# Sheets Full Pipeline - {0} chamber(s), {1} per sheet".format(
    len(chambers), opt["per"]))

# Every step's transactions are gathered into one undo entry.
group = TransactionGroup(doc, "pyMEP: Sheets Full Pipeline ({0} chamber(s))"
                         .format(len(chambers)))
group.Start()
sheet_rows = []
try:
    # --- 1. plans ---
    ok, why, res = _run_step("Step 1 - Chamber Plans", STEP_PLANS, "plans",
                             {"view": opt["source"], "chambers": chambers,
                              "extents": opt["extents"],
                              "size_mode": opt["crop_mode"],
                              "seed": opt["seed"],
                              "workset": opt["workset"],
                              "template": opt["plan_template"]})
    summary.append(("Chamber Plans",
                    "{0} created, {1} already there, {2} failed".format(
                        res.get("created", 0), res.get("existing", 0),
                        res.get("failed", 0)) if ok else "FAILED - " + why))

    # --- 2. sections ---
    ok, why, res = _run_step("Step 2 - Create Sections", STEP_SECTIONS,
                             "sections", {"chambers": chambers,
                                          "size_mode": opt["size_mode"]})
    section_ids = list(res.get("view_ids") or [])
    summary.append(("Create Sections",
                    "{0} of {1} planned created, {2} failed".format(
                        res.get("created", 0), res.get("planned", 0),
                        res.get("failed", 0)) if ok else "FAILED - " + why))

    # --- 3. sheets ---
    tb = titleblocks.get(opt["titleblock"])
    tb_id = tb.Id if tb is not None else ElementId.InvalidElementId
    n = opt["start"]
    for chunk in CS.chunks(marks, opt["per"]):
        while CS.sheet_text(opt["number"], n) in existing_numbers:
            n += 1
        number = CS.sheet_text(opt["number"], n)
        name = CS.sheet_text(opt["name"], n) if opt["name"] else number
        sheet = None
        t = Transaction(doc, "pyMEP: Sheet {0}".format(number))
        t.Start()
        try:
            sheet = ViewSheet.Create(doc, tb_id)
            sheet.SheetNumber = number
            try:
                sheet.Name = name
            except Exception:
                pass
            t.Commit()
            existing_numbers.add(number)
        except Exception as ex:
            t.RollBack()
            sheet_rows.append([number, name, ", ".join(chunk),
                               "sheet NOT created: {0}".format(ex)])
            n += 1
            continue
        ok, why, res = _run_step(
            "Step 3 - Sheet {0} ({1})".format(number, ", ".join(chunk)),
            STEP_SHEET, "sheet",
            {"sheet": sheet, "keys": chunk, "scale": opt["scale"]})
        if ok:
            note = "{0} view(s) placed".format(res.get("placed", 0))
            if res.get("skipped"):
                note += ", {0} skipped".format(res.get("skipped"))
            if res.get("below"):
                note += ", {0} below the sheet edge".format(res.get("below"))
            if res.get("missing"):
                note += ", no views for {0}".format(
                    ", ".join(res.get("missing")))
        else:
            note = "placement FAILED - " + why
        sheet_rows.append([number, name, ", ".join(chunk), note])
        n += 1
    summary.append(("Sheets", "{0} sheet(s)".format(len(sheet_rows))))

    # --- 4. dimensions ---
    if opt["dims"]:
        views = []
        for vid in section_ids:
            try:
                v = doc.GetElement(ElementId(vid))
            except Exception:
                v = None
            if v is not None:
                views.append(v)
        if views:
            ok, why, res = _run_step("Step 4 - Dimension Section", STEP_DIMS,
                                     "dims", {"views": views,
                                              "dim_type": opt["dim_type"]})
            summary.append(("Dimension Section",
                            "{0} string(s) in {1} section(s)".format(
                                res.get("strings", 0),
                                res.get("sections", 0))
                            if ok else "FAILED - " + why))
        else:
            summary.append(("Dimension Section",
                            "skipped - no new sections to dimension"))
finally:
    try:
        del sys._pymep_pipeline
    except Exception:
        pass
    # Collapse everything into ONE undo step. If the group cannot be
    # assimilated (a step left something open), roll it back so nothing is
    # left half-done.
    undo_note = ""
    try:
        if group.HasStarted() and not group.HasEnded():
            group.Assimilate()
            undo_note = "one undo step"
    except Exception as ex:
        try:
            group.RollBack()
            undo_note = "ROLLED BACK - {0}".format(ex)
        except Exception:
            undo_note = "could not close the undo group: {0}".format(ex)


# ---------------------------------------------------------------------------
# 4. Summary
# ---------------------------------------------------------------------------
out.print_md("# Pipeline summary")
if undo_note:
    out.print_md("**Undo:** {0}.".format(undo_note))
out.print_md("**Chambers:** {0}  |  **Source plan:** {1}  |  **Title block:** "
             "{2}  |  **Scale:** {3}".format(
                 ", ".join(marks), _name(opt["source"]), opt["titleblock"],
                 SS.scale_text(opt["scale"])))
for label, note in summary:
    out.print_md("- **{0}:** {1}".format(label, note))
if sheet_rows:
    out.print_table(table_data=sheet_rows,
                    columns=["Sheet number", "Sheet name", "Chambers",
                             "Result"])

# Keep the output window open.
