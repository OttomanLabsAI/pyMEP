# -*- coding: utf-8 -*-
"""Pipes to Conduits - a conduit on every selected pipe's line, at the
pipe's nominal size.

Select pipes and click (nothing selected drops into pick mode - pick
pipes, then ENTER or Finish). Each STRAIGHT pipe gets a conduit on the
same line, hosted on the pipe's reference level, taking the pipe's
nominal diameter, workset and Mark. The pipes are left untouched -
delete them once you're happy.

Conduit sizes are not free values: Revit only accepts nominals that
exist in the conduit type's STANDARD (Electrical Settings > Conduit
Settings > Sizes). Any pipe size the standard is missing is ADDED to
it first (inner/outer taken from the pipe); only when the standard
can't be extended does the conduit snap to the nearest existing size
- every add and snap is reported.

The conduit type is remembered in Settings (conduit_type_name); the
first run - or a model without the remembered type - asks.
"""

__title__ = "Pipes to\nConduits"
__author__ = "Glent Group"

import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

from pymep_conduit import (
    SETTINGS_CONDUIT_TYPE, SIZE_TOL_MM, missing_sizes, pick_size,
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
    BuiltInParameter, Level, Line, LocationCurve,
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

# ---------------------------------------------------------------------------
# 1. The pipes: pre-selection, else pick mode (ENTER or Finish locks in)
# ---------------------------------------------------------------------------
pipes = []
for eid in uidoc.Selection.GetElementIds():
    el = doc.GetElement(eid)
    if isinstance(el, Pipe):
        pipes.append(el)

if not pipes:
    log("No pipes pre-selected - pick the pipes in the view, then press "
        "**ENTER** (or hit Finish on the options bar).")

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
# 2. The conduit type: remembered in Settings, first run asks
# ---------------------------------------------------------------------------
ctypes = list(FilteredElementCollector(doc).OfClass(ConduitType))
if not ctypes:
    forms.alert("This model has no CONDUIT TYPES - load or create one "
                "first (Electrical > Conduit).", exitscript=True)

by_name = {}
for ct in ctypes:
    by_name[safe_name(ct)] = ct

settings = load_settings()
want_name = str(settings.get(SETTINGS_CONDUIT_TYPE) or "")
ctype = by_name.get(want_name)
if ctype is None:
    if len(ctypes) == 1:
        ctype = ctypes[0]
    else:
        name = forms.SelectFromList.show(
            sorted(by_name), title="Conduit type",
            button_name="Use this conduit type", multiselect=False)
        if not name:
            forms.alert("Cancelled - nothing was created.", exitscript=True)
        ctype = by_name[name]
try:
    settings[SETTINGS_CONDUIT_TYPE] = safe_name(ctype)
    save_settings(settings)
except Exception:
    pass
log("Conduit type: **{}** (remembered).".format(safe_name(ctype)))


# ---------------------------------------------------------------------------
# 3. Read the pipes: line, nominal / inner / outer, level
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
sample = {}          # dia_mm key -> (inner_ft, outer_ft) from a pipe
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
    key = round(ft2mm(dia), 3)
    if key not in sample:
        inner = _dbl(p, BuiltInParameter.RBS_PIPE_INNER_DIAM_PARAM)
        outer = _dbl(p, BuiltInParameter.RBS_PIPE_OUTER_DIAMETER)
        sample[key] = (inner or dia, outer or dia)
    rows.append((p, crv, dia,
                 (lvl.Id if lvl is not None else _any_level.Id)))

if not rows:
    forms.alert("Nothing usable: {} curved pipe(s) skipped (conduits "
                "are straight-only), {} with no diameter.".format(
                    skipped_curved, skipped_nodia), exitscript=True)

# ---------------------------------------------------------------------------
# 4. The conduit STANDARD's size list - add what the pipes need
# ---------------------------------------------------------------------------
std_name = None
try:
    _sp = ctype.get_Parameter(BuiltInParameter.CONDUIT_STANDARD_TYPE_PARAM)
    if _sp is not None:
        std_name = _sp.AsValueString() or _sp.AsString()
except Exception:
    std_name = None

css = None
avail_mm = []
if std_name:
    try:
        css = ConduitSizeSettings.GetConduitSizeSettings(doc)
        for kv in css:
            if kv.Key == std_name:
                for cs in kv.Value:
                    avail_mm.append(ft2mm(cs.NominalDiameter))
                break
    except Exception:
        css = None
if std_name:
    log("Conduit standard: **{}** with {} size(s).".format(
        std_name, len(avail_mm)))
else:
    log("NOTE: couldn't read this conduit type's STANDARD - missing "
        "sizes can't be added, diameters snap to what exists.")

wanted_mm = sorted(set(round(ft2mm(d), 3) for (_p, _c, d, _l) in rows))
to_add = missing_sizes(avail_mm, wanted_mm) if css is not None else []

# ---------------------------------------------------------------------------
# 5. Place - sizes first, then one conduit per pipe
# ---------------------------------------------------------------------------
created = 0
failed = 0
dia_set = 0
snapped = {}         # want_mm -> used_mm
added_mm = []
add_failed_mm = []

t = Transaction(doc, "Pipes to Conduits")
t.Start()

for w in to_add:
    inner, outer = sample.get(w, (mm2ft(w), mm2ft(w)))
    try:
        ok = css.AddSize(std_name, ConduitSize(mm2ft(w), inner, outer,
                                               True, True))
        if ok:
            added_mm.append(w)
            avail_mm.append(w)
        else:
            add_failed_mm.append(w)
    except Exception:
        add_failed_mm.append(w)

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
                if use is not None:
                    dp.Set(mm2ft(use))
                else:
                    dp.Set(dia)
                dia_set += 1
                if use is not None and not exact:
                    snapped[want] = use
        except Exception:
            pass
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
# 6. Report
# ---------------------------------------------------------------------------
log("Created **{}** conduit(s), diameters set on {}, failed {}.".format(
    created, dia_set, failed))
if added_mm:
    log("Sizes ADDED to standard '{}': {}.".format(
        std_name, ", ".join("{:g} mm".format(v) for v in added_mm)))
if add_failed_mm:
    log("Sizes the standard would NOT take: {}.".format(
        ", ".join("{:g} mm".format(v) for v in add_failed_mm)))
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
if snapped:
    msg.append("Snapped to nearest size: {}".format(len(snapped)))
if added_mm:
    msg.append("Sizes added to '{}': {}".format(
        std_name, ", ".join("{:g}".format(v) for v in added_mm)))
if skipped_curved:
    msg.append("Curved pipes skipped: {}".format(skipped_curved))
if failed:
    msg.append("Failed: {}".format(failed))
msg.append("")
msg.append("The pipes are untouched.")
forms.alert("\n".join(msg), title="Pipes to Conduits")
log.close()
