# -*- coding: utf-8 -*-
"""Pipes to Conduits - a conduit on every selected pipe's line, at the
pipe's nominal size, behind a dialog.

Select pipes and click (nothing selected drops into pick mode after
the dialog - pick pipes, then ENTER or Finish). Each STRAIGHT pipe
gets a conduit on the same line, hosted on the pipe's reference
level, taking the pipe's nominal diameter, workset and Mark. The
pipes are left untouched - delete them once you're happy.

The dialog picks the CONDUIT TYPE and shows the STANDARD it follows
(Electrical Settings > Conduit Settings > Sizes) - the standard
dropdown auto-follows the type and can be overridden when the model
reports it oddly. Missing pipe sizes are CREATED on that standard in
their own committed step BEFORE any conduit is placed: the TRADE SIZE
and the OUTER diameter are both the pipe's size, and the INNER
diameter is trade minus twice the CONDUIT THICKNESS from the dialog.
With size creation off (or when the standard refuses) the conduit
snaps to the nearest existing size instead - every add, refusal and
snap is reported with its reason. Everything is remembered in
Settings.
"""

__title__ = "Pipes to\nConduits"
__author__ = "Glent Group"

import os
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

from pymep_conduit import (
    SETTINGS_CONDUIT_TYPE, SETTINGS_CONDUIT_ADD_SIZES,
    SETTINGS_CONDUIT_WALL, SIZE_TOL_MM,
    conduit_settings, inner_from_trade, match_standard, missing_sizes,
    pick_size,
)
from pymep_config import load_settings, save_settings
from pymep_revit import safe_name, ft2mm, mm2ft
from pymep_log import Logger
import pymep_pickui as PU

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
from Autodesk.Revit.DB import (
    FilteredElementCollector, Transaction, SubTransaction,
    BuiltInParameter, ElementId, Level, Line, LocationCurve,
)
from Autodesk.Revit.DB.Plumbing import Pipe
from Autodesk.Revit.DB.Electrical import (
    Conduit, ConduitType, ConduitSizeSettings, ConduitSize,
)
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter

output = script.get_output()
log = Logger(output, "PipesToConduits")
doc = revit.doc
uidoc = revit.uidoc

log("### Pipes to Conduits")

XAML_PATH = os.path.join(
    os.path.dirname(os.path.abspath(sys.modules["pymep_config"].__file__)),
    "pymep_conduit.xaml")

# ---------------------------------------------------------------------------
# 1. Pre-selection (pick mode comes AFTER the dialog when empty)
# ---------------------------------------------------------------------------
pipes = []
for eid in uidoc.Selection.GetElementIds():
    el = doc.GetElement(eid)
    if isinstance(el, Pipe):
        pipes.append(el)

# ---------------------------------------------------------------------------
# 2. Conduit types + the STANDARD each one follows
# ---------------------------------------------------------------------------
ctypes = list(FilteredElementCollector(doc).OfClass(ConduitType))
if not ctypes:
    forms.alert("This model has no CONDUIT TYPES - load or create one "
                "first (Electrical > Conduit).", exitscript=True)


def _standard_of(ct):
    """The type's Standard parameter as TEXT, however this Revit build
    stores it (string, display string, or an element id)."""
    sp = None
    try:
        sp = ct.get_Parameter(BuiltInParameter.CONDUIT_STANDARD_TYPE_PARAM)
    except Exception:
        sp = None
    if sp is None:
        try:
            sp = ct.LookupParameter("Standard")
        except Exception:
            sp = None
    if sp is None:
        return ""
    for getter in ("AsString", "AsValueString"):
        try:
            v = getattr(sp, getter)()
            if v:
                return v
        except Exception:
            pass
    try:
        eid = sp.AsElementId()
        if eid is not None and eid != ElementId.InvalidElementId:
            el = doc.GetElement(eid)
            if el is not None and el.Name:
                return el.Name
    except Exception:
        pass
    return ""


by_name = {}
std_by_name = {}
for ct in ctypes:
    nm = safe_name(ct)
    by_name[nm] = ct
    std_by_name[nm] = _standard_of(ct)


def _read_size_settings():
    """{standard key: [nominal mm, ...]} straight from the document's
    conduit size settings - called again after sizes are added so the
    report shows what REALLY landed."""
    out = {}
    try:
        for kv in ConduitSizeSettings.GetConduitSizeSettings(doc):
            mm_list = []
            for cs in kv.Value:
                mm_list.append(ft2mm(cs.NominalDiameter))
            out[kv.Key] = mm_list
    except Exception as ex:
        log("NOTE: couldn't read the conduit size settings: {}".format(ex))
    return out


sizes_by_std = _read_size_settings()
std_keys = sorted(sizes_by_std)
if std_keys:
    log("Conduit standards in this model: {}.".format(
        ", ".join("{} ({} sizes)".format(k, len(sizes_by_std[k]))
                  for k in std_keys)))
else:
    log("NOTE: no conduit standards found - sizes can't be created, "
        "diameters snap to the type's existing sizes.")


# ---------------------------------------------------------------------------
# 3. The dialog
# ---------------------------------------------------------------------------
class ConduitWindow(forms.WPFWindow):

    def __init__(self, type_names, remembered, add_sizes, wall_mm,
                 n_selected):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self.result = None
        if n_selected:
            self.TxtInfo.Text = ("{} pipe(s) selected - each straight one "
                                 "gets a conduit on its line at its "
                                 "size.".format(n_selected))
        else:
            self.TxtInfo.Text = ("No pipes selected yet - Create drops "
                                 "into pick mode (pick pipes, then ENTER "
                                 "or Finish).")
        self.CmbStandard.Items.Clear()
        for k in std_keys:
            self.CmbStandard.Items.Add(k)
        self.CmbType.Items.Clear()
        for n in type_names:
            self.CmbType.Items.Add(n)
        if remembered and remembered in type_names:
            self.CmbType.SelectedItem = remembered
        elif self.CmbType.Items.Count:
            self.CmbType.SelectedIndex = 0
        self.ChkAddSizes.IsChecked = bool(add_sizes)
        self.TxtThickness.Text = "{:g}".format(wall_mm)
        self._follow_type()
        self._sync()

    def _follow_type(self):
        """Point the standard dropdown at the picked TYPE's standard."""
        try:
            nm = self.CmbType.SelectedItem
            key = match_standard(std_keys, std_by_name.get(nm))
            if key is not None:
                self.CmbStandard.SelectedItem = key
            elif (self.CmbStandard.SelectedIndex < 0
                    and self.CmbStandard.Items.Count):
                self.CmbStandard.SelectedIndex = 0
        except Exception:
            pass

    def _sync(self):
        try:
            self.TxtThickness.IsEnabled = bool(self.ChkAddSizes.IsChecked)
            nm = self.CmbType.SelectedItem
            reported = std_by_name.get(nm) or ""
            key = self.CmbStandard.SelectedItem
            if key:
                n = len(sizes_by_std.get(key) or [])
                extra = ""
                if reported and match_standard([key], reported) is None:
                    extra = (" - NOTE: the type itself reports '{}'"
                             .format(reported))
                self.TxtStandard.Text = (
                    "{} size(s) defined - missing pipe sizes are created "
                    "HERE{}.".format(n, extra))
            elif reported:
                self.TxtStandard.Text = (
                    "The type reports standard '{}' but the size settings "
                    "have no such standard - pick one above.".format(
                        reported))
            else:
                self.TxtStandard.Text = (
                    "No conduit standards in this model - sizes can't be "
                    "created, diameters snap to what exists.")
        except Exception:
            pass

    def on_type_changed(self, sender, args):
        self._follow_type()
        self._sync()

    def on_std_changed(self, sender, args):
        self._sync()

    def on_add_sizes_changed(self, sender, args):
        self._sync()

    def on_go(self, sender, args):
        nm = self.CmbType.SelectedItem
        if not nm:
            self.StatusText.Text = "Pick a conduit type."
            return
        add = bool(self.ChkAddSizes.IsChecked)
        std = self.CmbStandard.SelectedItem
        if add and not std:
            self.StatusText.Text = ("No conduit standard to create sizes "
                                    "on - untick size creation or add a "
                                    "standard in Electrical Settings.")
            return
        wall = None
        if add:
            try:
                wall = float((self.TxtThickness.Text or "").strip())
            except Exception:
                wall = None
            if wall is None or wall <= 0:
                self.StatusText.Text = ("Conduit thickness must be a "
                                        "positive number of mm.")
                return
        self.result = {"type": nm, "std": std, "add_sizes": add,
                       "wall": wall}
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


_settings = load_settings()
_rem_type, _rem_add, _rem_wall = conduit_settings(_settings)
win = ConduitWindow(sorted(by_name), _rem_type, _rem_add, _rem_wall,
                    len(pipes))
win.ShowDialog()
if not win.result:
    forms.alert("Cancelled - nothing was created.", exitscript=True)

ctype = by_name[win.result["type"]]
add_sizes = win.result["add_sizes"]
wall_mm = win.result["wall"]
std_name = win.result["std"]

try:
    _settings[SETTINGS_CONDUIT_TYPE] = win.result["type"]
    _settings[SETTINGS_CONDUIT_ADD_SIZES] = add_sizes
    if wall_mm:
        _settings[SETTINGS_CONDUIT_WALL] = wall_mm
    save_settings(_settings)
except Exception:
    pass

log("Conduit type: **{}** - standard **{}** (type reports '{}').".format(
    win.result["type"], std_name or "(none)",
    std_by_name.get(win.result["type"]) or "?"))
if add_sizes:
    log("Missing sizes WILL be created on '{}': trade = outer = the "
        "pipe size, inner = trade - 2 x {:g} mm.".format(std_name,
                                                         wall_mm))
else:
    log("Size creation is OFF - diameters snap to the standard's "
        "existing sizes.")

# ---------------------------------------------------------------------------
# 4. Pick mode when nothing was pre-selected
# ---------------------------------------------------------------------------
if not pipes:
    log("Pick the pipes in the view, then press **ENTER** (or hit "
        "Finish on the options bar).")

    class _PipesOnly(ISelectionFilter):
        def AllowElement(self, e):
            return isinstance(e, Pipe)

        def AllowReference(self, r, p):
            return False

    try:
        with PU.EnterFinishesPick(uidoc.Application):
            refs = uidoc.Selection.PickObjects(
                ObjectType.Element, _PipesOnly(),
                "Pick the PIPES to turn into conduits - click them or "
                "drag a selection box, then press ENTER (or hit FINISH)")
        seen = set()
        for r in refs:
            if r.ElementId.IntegerValue in seen:
                continue
            seen.add(r.ElementId.IntegerValue)
            el = doc.GetElement(r.ElementId)
            if isinstance(el, Pipe):
                pipes.append(el)
    except Exception:
        pipes = []

if not pipes:
    forms.alert("No pipes selected - nothing to do.", exitscript=True)

log("**{}** pipe(s) selected.".format(len(pipes)))


# ---------------------------------------------------------------------------
# 5. Read the pipes: line, nominal, level
# ---------------------------------------------------------------------------
def _dbl(el, bip):
    try:
        p = el.get_Parameter(bip)
        if p is not None and p.HasValue:
            return p.AsDouble()
    except Exception:
        pass
    return None


_any_level = None
for _l in FilteredElementCollector(doc).OfClass(Level):
    if _any_level is None or _l.Elevation < _any_level.Elevation:
        _any_level = _l
if _any_level is None:
    forms.alert("This model has no levels - nothing to host the "
                "conduits on.", exitscript=True)

rows = []            # (pipe, line, dia_ft, level_id)
skipped_curved = 0
skipped_nodia = 0
for p in pipes:
    loc = p.Location
    crv = loc.Curve if isinstance(loc, LocationCurve) else None
    if not isinstance(crv, Line):
        skipped_curved += 1        # conduits are straight-only
        continue
    dia = _dbl(p, BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
    if not dia:
        skipped_nodia += 1
        continue
    lvl = None
    try:
        lvl = p.ReferenceLevel
    except Exception:
        pass
    rows.append((p, crv, dia,
                 (lvl.Id if lvl is not None else _any_level.Id)))

if not rows:
    forms.alert("Nothing usable: {} curved pipe(s) skipped (conduits "
                "are straight-only), {} with no diameter.".format(
                    skipped_curved, skipped_nodia), exitscript=True)

wanted_mm = sorted(set(round(ft2mm(d), 3) for (_p, _c, d, _l) in rows))
log("Pipe sizes: {}.".format(
    ", ".join("{:g} mm".format(v) for v in wanted_mm)))

# ---------------------------------------------------------------------------
# 6. CREATE the missing sizes on the standard - own transaction,
#    committed and verified BEFORE any conduit is placed
# ---------------------------------------------------------------------------
avail_mm = list(sizes_by_std.get(std_name) or [])
added = []           # (trade_mm, inner_mm)
add_failed = []      # (trade_mm, reason)

if add_sizes and std_name:
    to_add = missing_sizes(avail_mm, wanted_mm)
    if to_add:
        t1 = Transaction(doc, "Conduit sizes for standard " + std_name)
        t1.Start()
        css = None
        try:
            css = ConduitSizeSettings.GetConduitSizeSettings(doc)
        except Exception as ex:
            log("Couldn't open the conduit size settings: {}".format(ex))
        for w in to_add:
            if css is None:
                add_failed.append((w, "size settings unavailable"))
                continue
            inner = inner_from_trade(w, wall_mm)
            try:
                # trade size = outer diameter = the pipe's size; inner
                # from the thickness the dialog asked for
                ok = css.AddSize(std_name,
                                 ConduitSize(mm2ft(w), mm2ft(inner),
                                             mm2ft(w), True, True))
                if ok:
                    added.append((w, inner))
                else:
                    add_failed.append((w, "the standard refused it "
                                          "(AddSize returned False)"))
            except Exception as ex:
                add_failed.append((w, "{}".format(ex)))
        t1.Commit()
        try:
            doc.Regenerate()
        except Exception:
            pass
        # re-read what REALLY landed - the size list the conduits get
        sizes_by_std = _read_size_settings()
        avail_mm = list(sizes_by_std.get(std_name) or [])
        log("Standard '{}' now defines {} size(s).".format(
            std_name, len(avail_mm)))
        for (w, i) in added:
            log("- created {:g} mm (trade = outer, inner {:g} mm)."
                .format(w, i))
        for (w, reason) in add_failed:
            log("- could NOT create {:g} mm: {}".format(w, reason))
    else:
        log("Standard '{}' already has every pipe size.".format(std_name))

still_missing = missing_sizes(avail_mm, wanted_mm)
if still_missing:
    log("Sizes still missing from '{}' (their conduits SNAP to the "
        "nearest existing size): {}.".format(
            std_name or "(no standard)",
            ", ".join("{:g} mm".format(v) for v in still_missing)))

# ---------------------------------------------------------------------------
# 7. Place - one conduit per pipe
# ---------------------------------------------------------------------------
created = 0
failed = 0
dia_set = 0
dia_failed = 0
snapped = {}         # want_mm -> used_mm

t = Transaction(doc, "Pipes to Conduits")
t.Start()

for (p, crv, dia, lvl_id) in rows:
    sub = SubTransaction(doc)
    try:
        sub.Start()
        c = Conduit.Create(doc, ctype.Id, crv.GetEndPoint(0),
                           crv.GetEndPoint(1), lvl_id)
        want = round(ft2mm(dia), 3)
        use, exact = pick_size(avail_mm, want) if avail_mm else (None,
                                                                 False)
        try:
            dp = c.get_Parameter(BuiltInParameter.RBS_CONDUIT_DIAMETER_PARAM)
            if dp is not None and not dp.IsReadOnly:
                dp.Set(mm2ft(use) if use is not None else dia)
                got = dp.AsDouble()
                target = use if use is not None else want
                if abs(ft2mm(got) - target) <= SIZE_TOL_MM:
                    dia_set += 1
                    if use is not None and not exact:
                        snapped[want] = use
                else:
                    dia_failed += 1
            else:
                dia_failed += 1
        except Exception as ex:
            dia_failed += 1
            log("- diameter refused on pipe {} ({:g} mm): {}".format(
                p.Id, want, ex))
        # keep the pipe's workset and Mark
        try:
            if doc.IsWorkshared:
                wp = p.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM)
                wc = c.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM)
                if (wp is not None and wc is not None
                        and not wc.IsReadOnly):
                    wc.Set(wp.AsInteger())
        except Exception:
            pass
        try:
            mk = p.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
            mc = c.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
            if (mk is not None and mc is not None and not mc.IsReadOnly
                    and mk.AsString()):
                mc.Set(mk.AsString())
        except Exception:
            pass
        sub.Commit()
        created += 1
    except Exception as ex:
        try:
            sub.RollBack()
        except Exception:
            pass
        failed += 1
        log("- failed on pipe {}: {}".format(p.Id, ex))

t.Commit()

# ---------------------------------------------------------------------------
# 8. Report
# ---------------------------------------------------------------------------
log("Created **{}** conduit(s), diameters set on {}, diameter refused "
    "on {}, failed {}.".format(created, dia_set, dia_failed, failed))
for wnt in sorted(snapped):
    log("- {:g} mm pipe SNAPPED to the nearest conduit size "
        "{:g} mm.".format(wnt, snapped[wnt]))
if skipped_curved:
    log("Skipped {} CURVED pipe(s) - conduits are straight-only.".format(
        skipped_curved))
if skipped_nodia:
    log("Skipped {} pipe(s) with no diameter.".format(skipped_nodia))
log("The pipes are untouched - delete them once you're happy.")

msg = ["Created: {}".format(created),
       "Diameters set: {}".format(dia_set)]
if added:
    msg.append("Sizes created on '{}': {}".format(
        std_name, ", ".join("{:g}".format(w) for (w, _i) in added)))
if add_failed:
    msg.append("Sizes refused: {} (see report)".format(len(add_failed)))
if snapped:
    msg.append("Snapped to nearest size: {}".format(len(snapped)))
if dia_failed:
    msg.append("Diameter refused: {} (see report)".format(dia_failed))
if skipped_curved:
    msg.append("Curved pipes skipped: {}".format(skipped_curved))
if failed:
    msg.append("Failed: {}".format(failed))
msg.append("")
msg.append("The pipes are untouched.")
forms.alert("\n".join(msg), title="Pipes to Conduits")
log.close()
