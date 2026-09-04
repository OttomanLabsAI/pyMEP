# -*- coding: utf-8 -*-
"""Chamber Sheet Setup - lay chamber plans and sections out on the open sheet.

Run it on a SHEET. One dialog asks:
  * WHICH chambers - a tick list of chamber Marks with a search box. A view
    belongs to the chamber whose whole Mark its name carries as a token, so
    "LV1/Z1", "LV1/Z1 SIDE A" and "Plan LV1/Z1" are LV1/Z1's while
    "LV1/Z10" is not; when several Marks fit, the longest wins. Marks come
    from the chambers in the model and from every "... SIDE X" section
    name. Each entry says what it owns.
  * The VIEW TEMPLATES - one for the plan views, one for the sections (Revit
    keeps them separate) - applied to every placed view first, or left as
    they are.
  * The VIEWPORT TYPE - the title style under each view - or Revit's
    default.
  * The SCALE (set on every placed view after the template; a view whose
    template locks the scale is reported and placed as is).
  * The spacing: gap between views, left / top margin, room for the view
    title - all in sheet mm.

Then, per ticked chamber, one ROW: the plan view(s) first, then the sections
in SIDE letter order, left to right from the sheet's top-left; a row that
would pass the right edge wraps to a new line; the next chamber starts below
the tallest view of the row plus the title room. Views already on a sheet
are skipped and reported. Values are remembered in Settings.

IronPython 2.7: pure ASCII, no f-strings, LF endings.
"""

__title__ = "Sheet\nSetup"
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
    Transaction, View, ViewSheet, ViewType, Viewport, XYZ,
    FilteredElementCollector, FamilyInstance, LocationPoint,
    BuiltInParameter, BuiltInCategory, Element,
)

from pyrevit import revit, forms, script

import pymep_sheet_setup as SS
from pymep_config import load_settings, save_settings

doc = revit.doc
uidoc = revit.uidoc
out = script.get_output()

XAML_PATH = os.path.join(os.path.dirname(os.path.abspath(SS.__file__)),
                         "pymep_sheet_setup.xaml")
MM_PER_FOOT = 304.8
PLAN_TYPES = (ViewType.FloorPlan, ViewType.CeilingPlan,
              ViewType.EngineeringPlan, ViewType.AreaPlan)


# ---------------------------------------------------------------------------
# Pre-flight: a sheet must be open
# ---------------------------------------------------------------------------
sheet = doc.ActiveView
if not isinstance(sheet, ViewSheet):
    forms.alert("Open the SHEET first.\n\n"
                "Sheet Setup places chamber plans and sections on the "
                "sheet you are looking at.", exitscript=True)


def _name(el):
    try:
        n = el.Name
        if n:
            return n
    except Exception:
        pass
    return "?"


def _sheet_label(sh):
    num = ""
    try:
        num = sh.SheetNumber or ""
    except Exception:
        num = ""
    return "{0} - {1}".format(num, _name(sh)).strip(" -")


def _sheet_of(v):
    # The sheet number a view already sits on ("" when unplaced).
    try:
        p = v.get_Parameter(BuiltInParameter.VIEWPORT_SHEET_NUMBER)
        if p is not None and p.HasValue:
            return (p.AsString() or "").strip()
    except Exception:
        pass
    return ""


def _mm0(text):
    # A non-negative mm field, or None.
    if text is None:
        return None
    try:
        s = text.strip().lower().replace(",", ".")
    except Exception:
        return None
    if s.endswith("mm"):
        s = s[:-2].strip()
    if not s:
        return None
    try:
        v = float(s)
    except Exception:
        return None
    if v != v or v - v != 0 or v < 0:
        return None
    return v


# ---------------------------------------------------------------------------
# 1. Chamber views in the project, grouped by chamber key
# ---------------------------------------------------------------------------
view_by_name = {}
view_list = []               # (name, "plan" | "section")
plan_templates = {}          # name -> template view (plan kinds)
section_templates = {}       # name -> template view (sections)
for v in FilteredElementCollector(doc).OfClass(View):
    try:
        vt = v.ViewType
    except Exception:
        continue
    if vt in PLAN_TYPES:
        kind = "plan"
    elif vt == ViewType.Section:
        kind = "section"
    else:
        continue
    nm = _name(v)
    if not nm or nm == "?":
        continue
    is_template = False
    try:
        is_template = bool(v.IsTemplate)
    except Exception:
        is_template = False
    if is_template:
        (plan_templates if kind == "plan" else section_templates)\
            .setdefault(nm, v)
        continue
    view_by_name.setdefault(nm, v)
    view_list.append((nm, kind))

# Viewport types (the title style under each view).
viewport_types = {}          # name -> ElementType
try:
    _vpts = FilteredElementCollector(doc)\
        .OfCategory(BuiltInCategory.OST_Viewports)\
        .WhereElementIsElementType().ToElements()
except Exception:
    _vpts = []
for _vt in _vpts:
    _nm = None
    try:
        _nm = Element.Name.GetValue(_vt)
    except Exception:
        _nm = _name(_vt)
    if _nm and _nm != "?":
        viewport_types.setdefault(_nm, _vt)

known_marks = set()
for fi in FilteredElementCollector(doc).OfClass(FamilyInstance)\
        .WhereElementIsNotElementType().ToElements():
    try:
        if not isinstance(fi.Location, LocationPoint):
            continue
        p = fi.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
        mk = p.AsString() if p is not None else None
    except Exception:
        mk = None
    if mk and mk.strip():
        known_marks.add(mk.strip())

groups = SS.group_chamber_views(view_list, known_marks)
if not groups:
    forms.alert("No chamber views found.\n\n"
                "Sheet Setup looks for plan views and '... SIDE A' sections "
                "whose names carry a chamber Mark. Run Chamber Plans / "
                "Create Sections first.",
                exitscript=True)

# Rows for the dialog: label, key, and whether anything is left to place.
rows = []
for key in sorted(groups, key=SS.natural_key):
    g = groups[key]
    names = SS.ordered_views(g)
    placed = [(n, _sheet_of(view_by_name[n])) for n in names
              if _sheet_of(view_by_name[n])]
    label = SS.group_label(key, g)
    free = len(names) - len(placed)
    if placed and not free:
        sheets = sorted(set(s for _n, s in placed))
        label += "   - all on sheet {0}".format(", ".join(sheets))
    elif placed:
        label += "   - {0} already on a sheet".format(len(placed))
    rows.append({"key": key, "label": label, "free": free})


# ---------------------------------------------------------------------------
# 2. The dialog
# ---------------------------------------------------------------------------
class SheetWindow(forms.WPFWindow):

    def __init__(self, rows, remembered, info):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self.result = None
        self._ready = False
        self._rows = rows
        self._state = dict((r["key"], False) for r in rows)
        self._boxes = []
        self.TxtInfo.Text = info
        self.CmbScale.Items.Clear()
        for n in SS.SCALE_CHOICES:
            self.CmbScale.Items.Add(SS.scale_text(n))
        self.CmbScale.Text = SS.scale_text(remembered["scale"])
        self._fill_templates(self.CmbPlanTemplate, sorted(plan_templates),
                             remembered["plan_template"])
        self._fill_templates(self.CmbSectionTemplate,
                             sorted(section_templates),
                             remembered["section_template"])
        self._fill_templates(self.CmbViewportType, sorted(viewport_types),
                             remembered["viewport_type"],
                             first=SS.DEFAULT_VIEWPORT)
        self.TxtGap.Text = "{0:g}".format(remembered["gap"])
        self.TxtLeft.Text = "{0:g}".format(remembered["left"])
        self.TxtTop.Text = "{0:g}".format(remembered["top"])
        self.TxtLabel.Text = "{0:g}".format(remembered["label"])
        self._rebuild()
        self._ready = True

    @staticmethod
    def _fill_templates(cmb, names, remembered, first=SS.LEAVE_TEMPLATE):
        cmb.Items.Clear()
        cmb.Items.Add(first)
        for n in names:
            cmb.Items.Add(n)
        if remembered and remembered in names:
            cmb.SelectedItem = remembered
        else:
            cmb.SelectedIndex = 0

    def _visible_rows(self):
        query = ""
        try:
            query = self.TxtFilter.Text or ""
        except Exception:
            pass
        keep = SS.filter_labels([r["label"] for r in self._rows], query)
        return [self._rows[i] for i in keep]

    def _rebuild(self):
        from System.Windows.Controls import CheckBox
        from System.Windows import Thickness
        self.PnlMarks.Children.Clear()
        self._boxes = []
        for r in self._visible_rows():
            cb = CheckBox()
            cb.Content = r["label"]
            cb.IsChecked = self._state.get(r["key"], False)
            cb.IsEnabled = r["free"] > 0
            cb.Margin = Thickness(0, 2, 0, 2)
            cb.Tag = r["key"]
            cb.Checked += self._on_box
            cb.Unchecked += self._on_box
            self.PnlMarks.Children.Add(cb)
            self._boxes.append(cb)
        self._count()

    def _on_box(self, sender, args):
        try:
            self._state[sender.Tag] = bool(sender.IsChecked)
        except Exception:
            pass
        self._count()

    def _count(self):
        try:
            n = len([k for k, on in self._state.items() if on])
            self.TxtCount.Text = "{0} of {1} chamber(s) ticked.".format(
                n, len(self._rows))
            self.StatusText.Text = ""
        except Exception:
            pass

    def _set_all(self, on):
        for cb in self._boxes:
            if cb.IsEnabled:
                cb.IsChecked = on
        self._count()

    def on_filter(self, sender, args):
        if getattr(self, "_ready", False):
            self._rebuild()

    def on_tick_all(self, sender, args):
        self._set_all(True)

    def on_tick_none(self, sender, args):
        self._set_all(False)

    def on_go(self, sender, args):
        keys = [r["key"] for r in self._rows if self._state.get(r["key"])]
        if not keys:
            self.StatusText.Text = "Tick at least one chamber."
            return
        scale = SS.parse_scale(self.CmbScale.Text)
        if scale is None:
            self.StatusText.Text = "Scale must look like 1:20 (or just 20)."
            return
        vals = {}
        for name, box, label in (("gap", self.TxtGap, "Gap"),
                                 ("left", self.TxtLeft, "Left margin"),
                                 ("top", self.TxtTop, "Top margin"),
                                 ("label", self.TxtLabel, "Title room")):
            v = _mm0(box.Text)
            if v is None:
                self.StatusText.Text = (
                    "{0} must be a number of mm (0 or more).".format(label))
                return
            vals[name] = v
        self.result = {"keys": keys, "scale": scale, "gap": vals["gap"],
                       "left": vals["left"], "top": vals["top"],
                       "label": vals["label"],
                       "plan_template": SS.template_choice(
                           self.CmbPlanTemplate.SelectedItem),
                       "section_template": SS.template_choice(
                           self.CmbSectionTemplate.SelectedItem),
                       "viewport_type": SS.template_choice(
                           self.CmbViewportType.SelectedItem)}
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


_settings = load_settings()
win = SheetWindow(rows, SS.sheet_settings(_settings),
                  "Sheet {0}. One row per chamber: plan first, then its "
                  "sections A, B, C...".format(_sheet_label(sheet)))
win.ShowDialog()
if not win.result:
    script.exit()

opt = win.result
try:
    _settings[SS.SETTINGS_SHEET_SCALE] = opt["scale"]
    _settings[SS.SETTINGS_SHEET_GAP] = opt["gap"]
    _settings[SS.SETTINGS_SHEET_LEFT] = opt["left"]
    _settings[SS.SETTINGS_SHEET_TOP] = opt["top"]
    _settings[SS.SETTINGS_SHEET_LABEL] = opt["label"]
    _settings[SS.SETTINGS_SHEET_PLAN_TEMPLATE] = opt["plan_template"]
    _settings[SS.SETTINGS_SHEET_SECTION_TEMPLATE] = opt["section_template"]
    _settings[SS.SETTINGS_SHEET_VIEWPORT_TYPE] = opt["viewport_type"]
    save_settings(_settings)
except Exception:
    pass


# ---------------------------------------------------------------------------
# 3. Place: scale the views, drop the viewports, then lay them out in rows
# ---------------------------------------------------------------------------
results = []              # (key, view name, note)
scale_notes = []          # (view name, why the scale stayed)
template_notes = []       # (view name, why the template was not applied)
templated = 0
plan_tmpl = plan_templates.get(opt["plan_template"]) \
    if opt["plan_template"] else None
section_tmpl = section_templates.get(opt["section_template"]) \
    if opt["section_template"] else None
vp_type = viewport_types.get(opt["viewport_type"]) \
    if opt["viewport_type"] else None
vp_type_notes = []        # (view name, why the viewport type stayed)
placed_total = 0
skipped_total = 0
below = 0

outline = sheet.Outline
sheet_w = outline.Max.U - outline.Min.U
sheet_h = outline.Max.V - outline.Min.V
ox, oy = outline.Min.U, outline.Min.V

t = Transaction(doc, "pyMEP: Sheet setup ({0} chamber(s))".format(
    len(opt["keys"])))
t.Start()
try:
    # (a) which views go where, and their scale
    plan = []             # per chamber: (key, [(name, view), ...])
    for key in opt["keys"]:
        todo = []
        for nm in SS.ordered_views(groups[key]):
            v = view_by_name.get(nm)
            if v is None:
                results.append((key, nm, "view not found"))
                skipped_total += 1
                continue
            on = _sheet_of(v)
            if on:
                results.append((key, nm, "skipped - already on sheet {0}"
                                .format(on)))
                skipped_total += 1
                continue
            try:
                can = Viewport.CanAddViewToSheet(doc, sheet.Id, v.Id)
            except Exception:
                can = True
            if not can:
                results.append((key, nm, "skipped - Revit will not place "
                                "this view on this sheet"))
                skipped_total += 1
                continue
            tmpl = section_tmpl if v.ViewType == ViewType.Section \
                else plan_tmpl
            if tmpl is not None:
                try:
                    if v.ViewTemplateId != tmpl.Id:
                        v.ViewTemplateId = tmpl.Id
                    templated += 1
                except Exception as ex:
                    template_notes.append((nm, "{0}".format(ex)))
            try:
                if v.Scale != opt["scale"]:
                    v.Scale = opt["scale"]
            except Exception as ex:
                scale_notes.append((nm, "{0}".format(ex)))
            todo.append((nm, v))
        if todo:
            plan.append((key, todo))
    try:
        doc.Regenerate()
    except Exception:
        pass

    # (b) create the viewports at the origin, then measure them
    vp_rows = []          # per chamber: [(key, name, viewport), ...]
    size_rows = []        # per chamber: [(w, h), ...]
    for key, todo in plan:
        vps = []
        for nm, v in todo:
            try:
                vp = Viewport.Create(doc, sheet.Id, v.Id, XYZ(0, 0, 0))
            except Exception as ex:
                results.append((key, nm, "place failed: {0}".format(ex)))
                skipped_total += 1
                continue
            if vp is None:
                results.append((key, nm, "place failed: no viewport"))
                skipped_total += 1
                continue
            if vp_type is not None:
                try:
                    if vp.GetTypeId() != vp_type.Id:
                        vp.ChangeTypeId(vp_type.Id)
                except Exception as ex:
                    vp_type_notes.append((nm, "{0}".format(ex)))
            vps.append((key, nm, vp))
        if vps:
            vp_rows.append(vps)
    try:
        doc.Regenerate()
    except Exception:
        pass
    for vps in vp_rows:
        sizes = []
        for key, nm, vp in vps:
            try:
                ol = vp.GetBoxOutline()
                w = ol.MaximumPoint.X - ol.MinimumPoint.X
                h = ol.MaximumPoint.Y - ol.MinimumPoint.Y
            except Exception:
                w, h = 0.0, 0.0
            sizes.append((w, h))
        size_rows.append(sizes)

    # (c) lay them out and move each viewport onto its spot
    centres, below = SS.layout(
        size_rows, sheet_w, sheet_h,
        opt["left"] / MM_PER_FOOT, opt["top"] / MM_PER_FOOT,
        opt["gap"] / MM_PER_FOOT, opt["gap"] / MM_PER_FOOT,
        opt["label"] / MM_PER_FOOT)
    for vps, cs in zip(vp_rows, centres):
        for (key, nm, vp), (cx, cy) in zip(vps, cs):
            try:
                vp.SetBoxCenter(XYZ(ox + cx, oy + cy, 0.0))
                results.append((key, nm, "placed at {0:.0f}, {1:.0f} mm".format(
                    cx * MM_PER_FOOT, cy * MM_PER_FOOT)))
                placed_total += 1
            except Exception as ex:
                results.append((key, nm, "placed but could not be moved: "
                                "{0}".format(ex)))
                placed_total += 1
    t.Commit()
except Exception as ex:
    t.RollBack()
    forms.alert("Failed, no changes made:\n\n{0}".format(ex), exitscript=True)


# ---------------------------------------------------------------------------
# 4. Report
# ---------------------------------------------------------------------------
out.print_md("### Chamber sheet setup")
out.print_md("**Sheet:** {0}  |  **Scale:** {1}  |  **Chambers:** {2}  |  "
             "**Views placed:** {3}  |  **Skipped:** {4}".format(
                 _sheet_label(sheet), SS.scale_text(opt["scale"]),
                 len(opt["keys"]), placed_total, skipped_total))
out.print_md("**Gap:** {0:g} mm  |  **Left / top margin:** {1:g} / {2:g} mm  "
             "|  **Title room:** {3:g} mm  |  **Sheet:** {4:.0f} x {5:.0f} mm"
             .format(opt["gap"], opt["left"], opt["top"], opt["label"],
                     sheet_w * MM_PER_FOOT, sheet_h * MM_PER_FOOT))
out.print_md("**Plan template:** {0}  |  **Section template:** {1}  |  "
             "**Templates applied:** {2}  |  **Viewport type:** {3}".format(
                 opt["plan_template"] or "(left as is)",
                 opt["section_template"] or "(left as is)", templated,
                 opt["viewport_type"] or "(Revit default)"))
if vp_type_notes:
    out.print_md("**{0} viewport(s) kept their type:**".format(
        len(vp_type_notes)))
    for nm, why in vp_type_notes:
        out.print_md("- {0}: {1}".format(nm, why))
if template_notes:
    out.print_md("**{0} view(s) did not take the template:**".format(
        len(template_notes)))
    for nm, why in template_notes:
        out.print_md("- {0}: {1}".format(nm, why))
if below:
    out.print_md("**{0} view(s) fall below the bottom edge of the sheet** - "
                 "the rows did not fit. Move them, use a larger sheet, or "
                 "place fewer chambers per sheet.".format(below))
if scale_notes:
    out.print_md("**{0} view(s) kept their own scale** (a view template "
                 "locks it or Revit refused):".format(len(scale_notes)))
    for nm, why in scale_notes:
        out.print_md("- {0}: {1}".format(nm, why))
out.print_table(table_data=[[k, n, note] for k, n, note in results],
                columns=["Chamber", "View", "Result"])

# Keep the output window open.
