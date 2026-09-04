# -*- coding: utf-8 -*-
"""Sheets Full Pipeline - the whole chamber drawing set from one dialog.

Steps, in order, each run by the button that owns it (headless):
  1. Chamber Plans    - a plan view per chamber (its remembered settings:
                        exact crop / scope box, sizes, template).
  2. Create Sections  - the sections that cut pipework (its remembered
                        settings: box size, section types, pipework check).
  3. Sheets           - numbered sheets from a title block, N chambers per
                        sheet, each chamber's plan then sections in a row
                        (Sheet Setup's remembered spacing, templates and
                        viewport type; the scale from this dialog).
  4. Dimension        - optional: the centreline strings in every new
                        section.

The dialog picks the chambers (tick family types, search the Marks, tick
chambers), the source plan, the title block, the sheet number and name
patterns with {n}, the first number, chambers per sheet, the scale, and
whether to dimension.

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
    FilteredElementCollector,
    FamilyInstance, LocationPoint, BuiltInParameter, BuiltInCategory,
    ElementId, Element, DimensionType, DimensionStyleType,
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
NO_TITLEBLOCK = u"(no title block)"
PLAN_TYPES = (ViewType.FloorPlan, ViewType.CeilingPlan,
              ViewType.EngineeringPlan, ViewType.AreaPlan)


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
plan_views = []
for v in FilteredElementCollector(doc).OfClass(View):
    try:
        if v.IsTemplate or v.ViewType not in PLAN_TYPES:
            continue
    except Exception:
        continue
    plan_views.append(v)
plan_views.sort(key=lambda v: _name(v).lower())
if not plan_views:
    forms.alert("No plan views in this project - the chamber plans need one "
                "to duplicate or to take a level from.", exitscript=True)
plan_by_name = {}
for v in plan_views:
    plan_by_name.setdefault(_name(v), v)
plan_names = sorted(plan_by_name, key=lambda s: s.lower())

titleblocks = {}
try:
    for tb in FilteredElementCollector(doc)\
            .OfCategory(BuiltInCategory.OST_TitleBlocks)\
            .WhereElementIsElementType().ToElements():
        titleblocks.setdefault(_type_label(tb), tb)
except Exception:
    pass
tb_names = [NO_TITLEBLOCK] + sorted(titleblocks, key=lambda s: s.lower())

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


def _settings_summary():
    lines = []
    if rem_plans["extents"] == CS.EXTENTS_CROP:
        if rem_size["mode"] == CS.SIZE_PARAMS:
            lines.append("Plans:    exact crop from '{0}' x '{1}' + {2:g} mm; "
                         "template {3}".format(rem_size["px"], rem_size["py"],
                                               rem_size["clear"],
                                               rem_plans["template"]))
        else:
            lines.append("Plans:    exact crop {0:g} x {1:g} mm; template {2}"
                         .format(rem_plans["width"], rem_plans["depth"],
                                 rem_plans["template"]))
    else:
        lines.append("Plans:    scope box from '{0}'; template {1}".format(
            rem_plans["seed"] or "sample_scope_box", rem_plans["template"]))
    if rem_size["mode"] == CS.SIZE_PARAMS:
        box = "from '{0}' x '{1}' x '{2}' + {3:g} mm".format(
            rem_size["px"], rem_size["py"], rem_size["ph"], rem_size["clear"])
    else:
        box = "offset {0:g} / height {1:g} / depth {2:g} mm".format(
            rem_sec["offset"], rem_sec["height"], rem_sec["depth"])
    lines.append("Sections: {0}; type {1}; pipework check {2}".format(
        box, rem_sec["type"] or "(first)",
        "on" if rem_sec["cut_only"] else "OFF"))
    lines.append("Sheets:   gap {0:g}, margins {1:g}/{2:g}, title room {3:g} "
                 "mm; templates {4} / {5}; viewport {6}".format(
                     rem_sheet["gap"], rem_sheet["left"], rem_sheet["top"],
                     rem_sheet["label"],
                     rem_sheet["plan_template"] or "(as is)",
                     rem_sheet["section_template"] or "(as is)",
                     rem_sheet["viewport_type"] or "(default)"))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 2. The dialog
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

        self.CmbSourcePlan.Items.Clear()
        for n in plan_names:
            self.CmbSourcePlan.Items.Add(n)
        if active is not None and active.ViewType in PLAN_TYPES \
                and _name(active) in plan_by_name:
            self.CmbSourcePlan.SelectedItem = _name(active)
        else:
            self.CmbSourcePlan.SelectedIndex = 0
        self.TxtSettingsInfo.Text = _settings_summary()

        self.CmbTitleBlock.Items.Clear()
        for n in tb_names:
            self.CmbTitleBlock.Items.Add(n)
        if rem["titleblock"] in tb_names:
            self.CmbTitleBlock.SelectedItem = rem["titleblock"]
        elif len(tb_names) > 1:
            self.CmbTitleBlock.SelectedIndex = 1
        else:
            self.CmbTitleBlock.SelectedIndex = 0
        self.TxtNumber.Text = rem["number"]
        self.TxtName.Text = rem["name"]
        self.TxtStart.Text = "{0}".format(rem["start"])
        self.TxtPerSheet.Text = "{0}".format(rem["per_sheet"])
        self.CmbScale.Items.Clear()
        for n in SS.SCALE_CHOICES:
            self.CmbScale.Items.Add(SS.scale_text(n))
        self.CmbScale.Text = SS.scale_text(rem_sheet["scale"])

        self.ChkDims.IsChecked = bool(rem["dims"])
        self.CmbDimType.Items.Clear()
        for n in dim_names:
            self.CmbDimType.Items.Add(n)
        first = CS.pick_dim_type_name(dim_names, rem_dim["dim_type"])
        if first is not None:
            self.CmbDimType.SelectedItem = first

        self._fill_types()
        self._ready = True
        self._sync()

    # -- family types ----------------------------------------------------------
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

    # -- chambers ----------------------------------------------------------------
    def _fill_chambers(self):
        from System.Windows.Controls import CheckBox
        from System.Windows import Thickness
        pool = []
        for d in type_options:
            if self._type_state.get(d["typeid"]):
                for fi in d["insts"]:
                    pool.append((fi, d))
        rows = []
        for fi, d in pool:
            mk = _get_mark(fi) or ""
            rows.append(("{0}   ({1})".format(mk, d["label"].split("   (")[0]),
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
        out_list = []
        for label, fi in self._pool:
            if self._chamber_state.get(fi.Id.IntegerValue):
                out_list.append(fi)
        return out_list

    def _set_all(self, on):
        for cb in self._chamber_boxes:
            cb.IsChecked = on
        self._sync()

    def _sync(self):
        if not getattr(self, "_ready", False):
            return
        try:
            n = len(self._chambers())
            per = _int_field(self.TxtPerSheet.Text, 1) or 0
            sheets = ((n + per - 1) // per) if per else 0
            self.TxtCount.Text = ("{0} chamber(s) ticked of {1} shown - {2} "
                                  "sheet(s) at {3} per sheet.".format(
                                      n, len(self._pool), sheets, per))
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

    def on_dims(self, sender, args):
        self._sync()

    def on_go(self, sender, args):
        chambers = self._chambers()
        if not chambers:
            self.StatusText.Text = ("Tick at least one family type and one "
                                    "chamber.")
            return
        src = plan_by_name.get(self.CmbSourcePlan.SelectedItem)
        if src is None:
            self.StatusText.Text = "Pick the source plan."
            return
        tb_label = self.CmbTitleBlock.SelectedItem or NO_TITLEBLOCK
        number = (self.TxtNumber.Text or "").strip()
        name = (self.TxtName.Text or "").strip()
        if not number:
            self.StatusText.Text = "Give a sheet number pattern (with {n})."
            return
        start = _int_field(self.TxtStart.Text, 0)
        if start is None:
            self.StatusText.Text = "First number must be a whole number."
            return
        per = _int_field(self.TxtPerSheet.Text, 1)
        if per is None:
            self.StatusText.Text = "Chambers per sheet must be 1 or more."
            return
        scale = SS.parse_scale(self.CmbScale.Text)
        if scale is None:
            self.StatusText.Text = "Scale must look like 1:20 (or just 20)."
            return
        dims = bool(self.ChkDims.IsChecked)
        dim_name = self.CmbDimType.SelectedItem
        if dims and dim_names and not dim_name:
            self.StatusText.Text = "Pick a dimension type."
            return
        self.result = {"chambers": chambers, "source": src,
                       "titleblock": tb_label, "number": number,
                       "name": name, "start": start, "per": per,
                       "scale": scale, "dims": dims, "dim_type": dim_name}
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


win = PipeWindow()
win.ShowDialog()
if not win.result:
    script.exit()
opt = win.result

try:
    _settings[CS.SETTINGS_PIPE_NUMBER] = opt["number"]
    _settings[CS.SETTINGS_PIPE_NAME] = opt["name"]
    _settings[CS.SETTINGS_PIPE_START] = opt["start"]
    _settings[CS.SETTINGS_PIPE_PER_SHEET] = opt["per"]
    _settings[CS.SETTINGS_PIPE_TITLEBLOCK] = opt["titleblock"]
    _settings[CS.SETTINGS_PIPE_DIMS] = opt["dims"]
    _settings[SS.SETTINGS_SHEET_SCALE] = opt["scale"]
    if opt["dim_type"]:
        _settings[CS.SETTINGS_DIM_TYPE] = opt["dim_type"]
    save_settings(_settings)
except Exception:
    pass


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
                             {"view": opt["source"], "chambers": chambers})
    summary.append(("Chamber Plans",
                    "{0} created, {1} already there, {2} failed".format(
                        res.get("created", 0), res.get("existing", 0),
                        res.get("failed", 0)) if ok else "FAILED - " + why))

    # --- 2. sections ---
    ok, why, res = _run_step("Step 2 - Create Sections", STEP_SECTIONS,
                             "sections", {"chambers": chambers})
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
