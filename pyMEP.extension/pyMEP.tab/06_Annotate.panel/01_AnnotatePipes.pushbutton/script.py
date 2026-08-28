# -*- coding: utf-8 -*-
"""Annotate Pipes - label the selected PIPES or CONDUITS in the active
plan view, one label per BANK of runs travelling together.

Workflow:
  1. Pre-select the runs in a plan view - either pipes or conduits,
     never a mixture; fittings and anything else are ignored.
  2. Click the button and fill in the dialog: the PREFIX, the TEXT
     TYPE, and the order of the label's parts, each with its own
     SUFFIX and an optional line break after it.
  3. One label per bank is placed at the bank's mid-run point, offset
     perpendicular by Settings > Annotate > pipe annotation offset
     (default 500 mm), with a leader back to the bank.

The label is built from up to four parts, in the dialog's order:

    PREFIX        free text, e.g. 'HV'
    COMBINATION   the bank's arrangement ACROSS x UP, e.g. '2x2'
    DIAMETER      '150' - every size listed when a bank is mixed
    SLOPE         '1:150' - pipes only, off by default

Each part's SUFFIX is written straight after it: the diameter's starts
as the diameter symbol, so a bank reads '150\u00d8'.

A BANK is worked out from the geometry: runs that are parallel, sit
within the dialog's BANK GAP of each other (the clear distance between
their surfaces, default 600 mm) and overlap along their length.
Segments of the same run count once, so a bank split into three
lengths is still '2x2'. Raise the gap to pull a wider spread into one
label; lower it to keep neighbouring trenches apart.

Selection rules - pipes and conduits are never annotated together: a
selection holding both stops with a message rather than a half-right
label. Anything else caught in the selection (conduit fittings above
all) is simply ignored and reported at the end.
"""

__title__  = "Annotate\nPipes"
__author__ = "Glent Group"

import os
import sys

# Force-reload pymep_* lib modules so the script picks up latest code
for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (
    Transaction, TextNote, ElementTypeGroup, ElementId,
    ViewType, FilteredElementCollector, TextNoteType, BuiltInCategory,
    BuiltInParameter, XYZ,
)

# TextNoteLeaderType - some Revit builds don't expose this name under
# Autodesk.Revit.DB even though TextNote.AddLeader() still takes the same
# underlying integer values (StraightL=0, StraightR=1, ArcL=2, ArcR=3).
# Try the enum first; fall back to int constants so AddLeader still works.
_LEADER_LEFT  = 0
_LEADER_RIGHT = 1
try:
    from Autodesk.Revit.DB import TextNoteLeaderType
    _LEADER_LEFT  = TextNoteLeaderType.StraightL
    _LEADER_RIGHT = TextNoteLeaderType.StraightR
except (ImportError, AttributeError):
    pass

# LeaderAtachement (the Revit API misspells this since 2018). Try the
# typo first, then the correct spelling, fall through to None.
try:
    from Autodesk.Revit.DB import LeaderAtachement as _LeaderAttach
except ImportError:
    try:
        from Autodesk.Revit.DB import LeaderAttachement as _LeaderAttach
    except ImportError:
        _LeaderAttach = None

from pyrevit import revit, forms, script

from pymep_config import (get_annotate_pipe_offset_mm, load_settings,
                          save_settings)
from pymep_revit import (get_connectors, get_od, get_slope, ft2mm,
                         mm2ft, safe_name)
import pymep_annotate as A

doc    = revit.doc
uidoc  = revit.uidoc
view   = doc.ActiveView

XAML_PATH = os.path.join(
    os.path.dirname(os.path.abspath(sys.modules["pymep_config"].__file__)),
    "pymep_annotate.xaml")


from pymep_log import Logger

output = script.get_output()
log = Logger(output, "AnnotatePipes")
log("### Annotate Pipes")

# The WHOLE run sits in one try so NOTHING can die silently - any
# failure lands in the output window AND an alert, instead of the
# blank-window-and-nothing the first conduit runs produced.
try:
    # ---------------------------------------------------------------------------
    # 0. PRE-FLIGHT: plan view, and a selection of pipes OR conduits ONLY
    # ---------------------------------------------------------------------------
    PLAN_VIEW_TYPES = (
        ViewType.FloorPlan,
        ViewType.CeilingPlan,
        ViewType.EngineeringPlan,
        ViewType.AreaPlan,
    )
    if view is None or view.ViewType not in PLAN_VIEW_TYPES:
        forms.alert("Open a plan view (Floor / Ceiling / Structural / Area) and "
                    "try again.",
                    exitscript=True)


    def _cat_int(elem):
        """Element category id as int, compatible with Revit 2024+ (.Value) and
        earlier (.IntegerValue)."""
        if elem is None or elem.Category is None:
            return None
        cid = elem.Category.Id
        try:
            return cid.Value
        except AttributeError:
            return cid.IntegerValue


    PIPE_CAT = int(BuiltInCategory.OST_PipeCurves)
    CONDUIT_CAT = int(BuiltInCategory.OST_Conduit)

    sel_ids = list(uidoc.Selection.GetElementIds())
    pipes, conduits, others = [], [], {}
    for eid in sel_ids:
        e = doc.GetElement(eid)
        cat = _cat_int(e)
        if cat == PIPE_CAT:
            pipes.append(e)
        elif cat == CONDUIT_CAT:
            conduits.append(e)
        else:
            try:
                nm = e.Category.Name if (e is not None and
                                         e.Category is not None) else "?"
            except Exception:
                nm = "?"
            others[nm] = others.get(nm, 0) + 1

    if pipes and conduits:
        forms.alert("The selection holds BOTH pipes ({}) and conduits ({}).\n\n"
                    "Annotate one kind at a time - a bank's arrangement and "
                    "diameter only mean something within a single category."
                    .format(len(pipes), len(conduits)), exitscript=True)

    # Anything else in the selection is simply IGNORED - a real conduit
    # selection nearly always drags in fittings, and refusing to run over
    # them stopped the button working at all.
    ignored = sum(others.values())

    runs = pipes or conduits
    is_pipe = bool(pipes)
    kind = "pipe" if is_pipe else "conduit"
    log("Selection: {} pipe(s), {} conduit(s), {} other element(s) "
        "ignored.".format(len(pipes), len(conduits), ignored))
    if not runs:
        forms.alert("Select one or more PIPES or CONDUITS in the view first, "
                    "then click the button.{}"
                    .format("\n\nThe {} selected element(s) are neither: {}."
                            .format(ignored,
                                    ", ".join(sorted(others)))
                            if ignored else ""), exitscript=True)


    # ---------------------------------------------------------------------------
    # 1. READ THE RUNS: direction, midpoint, ends, diameter, slope (all mm)
    # ---------------------------------------------------------------------------
    def _endpoints(el):
        """(XYZ, XYZ) endpoints of the centreline (Revit ft), or (None,
        None) when neither Location.Curve nor connectors are usable."""
        loc = getattr(el, "Location", None)
        if loc is not None and hasattr(loc, "Curve") and loc.Curve is not None:
            c = loc.Curve
            return c.GetEndPoint(0), c.GetEndPoint(1)
        conns = list(get_connectors(el))
        if len(conns) >= 2:
            return conns[0].Origin, conns[1].Origin
        return None, None


    def _dia_mm(el):
        """The size to label: a conduit's TRADE size (what a schedule
        calls it), a pipe's outside diameter as before."""
        if not is_pipe:
            try:
                p = el.get_Parameter(
                    BuiltInParameter.RBS_CONDUIT_DIAMETER_PARAM)
                if p is not None and p.HasValue:
                    v = ft2mm(p.AsDouble())
                    if v > 0:
                        return v
            except Exception:
                pass
        return get_od(el, list(get_connectors(el))) or 0.0


    items = []
    skipped = 0
    for el in runs:
        p0, p1 = _endpoints(el)
        if p0 is None or p1 is None:
            skipped += 1
            continue
        d = A.normalise_dir(p1.X - p0.X, p1.Y - p0.Y)
        if d is None:               # purely vertical or degenerate in plan
            skipped += 1
            continue
        dia = _dia_mm(el)
        if dia <= 0:
            skipped += 1
            continue
        items.append({
            "dir": d,
            "mid": (ft2mm((p0.X + p1.X) * 0.5), ft2mm((p0.Y + p1.Y) * 0.5),
                    ft2mm((p0.Z + p1.Z) * 0.5)),
            "ends": ((ft2mm(p0.X), ft2mm(p0.Y), ft2mm(p0.Z)),
                     (ft2mm(p1.X), ft2mm(p1.Y), ft2mm(p1.Z))),
            "dia": dia,
            "slope": abs(get_slope(el)) if is_pipe else 0.0,
        })

    log("Usable runs: {} ({} skipped - no size or vertical)."
        .format(len(items), skipped))
    if not items:
        forms.alert("None of the selected {}s could be annotated\n"
                    "(no diameter, or no run direction in plan)."
                    .format(kind), exitscript=True)


    # ---------------------------------------------------------------------------
    # 2. SANITY CAP (the bank grouping compares every pair)
    # ---------------------------------------------------------------------------
    MAX_RUNS = 1500

    if len(items) > MAX_RUNS:
        forms.alert("{} {}s selected - that is more than this tool groups "
                    "into banks at once ({}).\n\nSelect a smaller stretch "
                    "and run again.".format(len(items), kind, MAX_RUNS),
                    exitscript=True)


    # ---------------------------------------------------------------------------
    # 3. DIALOG: prefix, text type, order + line breaks
    # ---------------------------------------------------------------------------
    text_types = []
    for tt in FilteredElementCollector(doc).OfClass(TextNoteType):
        text_types.append((safe_name(tt), tt.Id))
    text_types.sort(key=lambda p: p[0])
    if not text_types:
        forms.alert("This document has no TextNoteType loaded - cannot place "
                    "a text note.", exitscript=True)

    _default_tt = doc.GetDefaultElementTypeId(ElementTypeGroup.TextNoteType)


    class AnnotateWindow(forms.WPFWindow):

        def __init__(self, settings, info):
            forms.WPFWindow.__init__(self, XAML_PATH)
            self.result = None
            self._ready = False
            self.TxtInfo.Text = info
            saved = A.annotate_settings(settings)
            prefix, ttype = saved["prefix"], saved["text_type"]
            order, breaks, gap = (saved["order"], saved["breaks"],
                                  saved["gap"])
            self._suffix = dict(saved["suffixes"])
            self._syncing = False
            self.slots = [self.CmbSlot1, self.CmbSlot2, self.CmbSlot3,
                          self.CmbSlot4]
            self.breaks = [self.ChkBreak1, self.ChkBreak2, self.ChkBreak3]
            self.sufs = [self.TxtSuf1, self.TxtSuf2, self.TxtSuf3,
                         self.TxtSuf4]
            for combo in self.slots:
                combo.Items.Clear()
                for _key, label in A.ITEM_LABELS:
                    combo.Items.Add(label)
            self._label_of = dict(A.ITEM_LABELS)
            self._key_of = dict((v, k) for k, v in A.ITEM_LABELS)
            for combo, key in zip(self.slots, order):
                combo.SelectedItem = self._label_of.get(key, "(none)")
            for chk, on in zip(self.breaks, breaks):
                chk.IsChecked = bool(on)
            self.TxtPrefix.Text = prefix
            self.TxtBankGap.Text = "{:g}".format(gap)
            self.CmbTextType.Items.Clear()
            for nm, _tid in text_types:
                self.CmbTextType.Items.Add(nm)
            want = ttype
            if not want and _default_tt is not None:
                for nm, tid in text_types:
                    if tid == _default_tt:
                        want = nm
                        break
            self.CmbTextType.SelectedIndex = 0
            for i, (nm, _tid) in enumerate(text_types):
                if nm == want:
                    self.CmbTextType.SelectedIndex = i
                    break
            self._sync_suffixes()
            self._ready = True
            self._preview()

        def _order(self):
            out = []
            for combo in self.slots:
                out.append(self._key_of.get(str(combo.SelectedItem or ""),
                                            A.ITEM_NONE))
            return out

        def _break_flags(self):
            return [bool(c.IsChecked) for c in self.breaks]

        def _sync_suffixes(self):
            """Show each slot's part suffix - so reordering carries the
            suffix with its part rather than leaving it in the row."""
            self._syncing = True
            try:
                for combo, box in zip(self.slots, self.sufs):
                    key = self._key_of.get(str(combo.SelectedItem or ""),
                                           A.ITEM_NONE)
                    box.IsEnabled = bool(key)
                    box.Text = self._suffix.get(key, "") if key else ""
            finally:
                self._syncing = False

        def on_suffix_changed(self, sender, args):
            if getattr(self, "_syncing", False):
                return
            try:
                for combo, box in zip(self.slots, self.sufs):
                    if box is sender:
                        key = self._key_of.get(
                            str(combo.SelectedItem or ""), A.ITEM_NONE)
                        if key:
                            self._suffix[key] = box.Text or ""
                        break
            except Exception:
                pass
            self._preview()

        def _preview(self):
            # the XAML's own handlers can fire while the window is still
            # being built, before these fields exist
            if not getattr(self, "_ready", False):
                return
            try:
                demo = {A.ITEM_PREFIX: (self.TxtPrefix.Text or "").strip(),
                        A.ITEM_COMBO: "2x2", A.ITEM_DIA: "150",
                        A.ITEM_SLOPE: "1:150"}
                txt = A.compose(demo, self._order(), self._break_flags(),
                                self._suffix)
                self.TxtPreview.Text = txt or "(empty label)"
            except Exception:
                pass

        def on_changed(self, sender, args):
            self._preview()

        def on_slot_changed(self, sender, args):
            if getattr(self, "_ready", False):
                self._sync_suffixes()
            self._preview()

        def on_go(self, sender, args):
            order = self._order()
            used = [k for k in order if k]
            if not used:
                self.StatusText.Text = ("Every slot is (none) - pick at least "
                                        "one part for the label.")
                return
            if len(set(used)) != len(used):
                self.StatusText.Text = ("Each part can only be used once - "
                                        "set the repeat to (none).")
                return
            try:
                gap = float((self.TxtBankGap.Text or "").strip())
            except ValueError:
                gap = -1.0
            if gap < 0:
                self.StatusText.Text = ("Bank gap must be a number of mm "
                                        "(0 or more).")
                return
            self.result = {
                "prefix": (self.TxtPrefix.Text or "").strip(),
                "text_type": str(self.CmbTextType.SelectedItem or ""),
                "order": order,
                "breaks": self._break_flags(),
                "gap": gap,
                "suffixes": dict(self._suffix),
            }
            self.Close()

        def on_cancel(self, sender, args):
            self.result = None
            self.Close()


    settings = load_settings()
    info = "{} {}(s) selected{}.".format(
        len(items), kind,
        " - {} skipped (no size / vertical)".format(skipped) if skipped else "")
    log("Opening the dialog ...")
    win = AnnotateWindow(settings, info)
    win.ShowDialog()
    if win.result is None:
        log("Dialog cancelled - nothing placed.")
        log.close()
        script.exit()
    opt = win.result
    log("Dialog OK: prefix '{}', text type '{}', bank gap {:g} mm."
        .format(opt["prefix"], opt["text_type"], opt["gap"]))

    settings[A.SETTINGS_PREFIX] = opt["prefix"]
    settings[A.SETTINGS_TEXT_TYPE] = opt["text_type"]
    settings[A.SETTINGS_ORDER] = opt["order"]
    settings[A.SETTINGS_BREAKS] = opt["breaks"]
    settings[A.SETTINGS_BANK_GAP] = opt["gap"]
    settings[A.SETTINGS_SUFFIXES] = opt["suffixes"]
    try:
        save_settings(settings)
    except Exception:
        pass

    text_type_id = None
    for nm, tid in text_types:
        if nm == opt["text_type"]:
            text_type_id = tid
            break
    if text_type_id is None:
        text_type_id = text_types[0][1]


    def _is_same(i, j):
        return A.same_bank(items[i], items[j], gap_mm=opt["gap"])


    # ---------------------------------------------------------------------------
    # 4. GROUP INTO BANKS (the dialog's bank gap decides them)
    # ---------------------------------------------------------------------------
    banks = A.cluster(len(items), _is_same)
    log("Grouped into {} bank(s): {}.".format(
        len(banks), ", ".join(str(len(b)) for b in banks)))

    offset_ft = mm2ft(get_annotate_pipe_offset_mm())
    records = []
    for members in banks:
        first = items[members[0]]
        ux, uy = first["dir"]
        px, py = -uy, ux                       # +90 deg CCW in plan
        ref = first["mid"]

        cells, alongs = [], []
        for i in members:
            it = items[i]
            dx = it["mid"][0] - ref[0]
            dy = it["mid"][1] - ref[1]
            cells.append((dx * px + dy * py, it["mid"][2] - ref[2]))
            for e in it["ends"]:
                alongs.append((e[0] - ref[0]) * ux + (e[1] - ref[1]) * uy)

        # a run split into segments must count ONCE: cells closer than
        # this are the same position in the bank
        tol = max(10.0, 0.4 * min(items[i]["dia"] for i in members))
        combo = A.combo_text(cells, tol)
        dia = A.dia_text([items[i]["dia"] for i in members])
        slope = (A.slope_text(max(items[i]["slope"] for i in members))
                 if is_pipe else "")

        # anchor: mid-run along the bank, centred across it
        along_mid = (min(alongs) + max(alongs)) / 2.0
        across_mid = sum(c[0] for c in cells) / float(len(cells))
        z_mid = sum(c[1] for c in cells) / float(len(cells)) + ref[2]
        bx = ref[0] + ux * along_mid + px * across_mid
        by = ref[1] + uy * along_mid + py * across_mid

        records.append({
            "values": {A.ITEM_COMBO: combo, A.ITEM_DIA: dia,
                       A.ITEM_SLOPE: slope},
            "point": XYZ(mm2ft(bx), mm2ft(by), mm2ft(z_mid)),
            "perp": (px, py),
            "n": len(members),
        })


    # ---------------------------------------------------------------------------
    # 5. PLACE ONE LABEL PER BANK, IN ONE TRANSACTION
    # ---------------------------------------------------------------------------
    labels = []
    for rec in records:
        values = dict(rec["values"])
        values[A.ITEM_PREFIX] = opt["prefix"]
        text = A.compose(values, opt["order"], opt["breaks"],
                         opt["suffixes"])
        if not text:
            continue
        px, py = rec["perp"]
        pt = rec["point"]
        labels.append({
            "text": text,
            "anchor": XYZ(pt.X + px * offset_ft, pt.Y + py * offset_ft, pt.Z),
            "leader_end": pt,
            # the run sits opposite the offset, so the leader exits the
            # side of the text nearest to it
            "leader_side": _LEADER_LEFT if px > 0 else _LEADER_RIGHT,
        })

    if not labels:
        forms.alert("Every label came out empty - check the order in the "
                    "dialog.", exitscript=True)

    t = Transaction(doc, "pyMEP: Annotate {}s ({})".format(kind, len(labels)))
    t.Start()
    placed = 0
    try:
        for rec in labels:
            note = TextNote.Create(
                doc, view.Id, rec["anchor"], rec["text"], text_type_id)

            # Anchor any leader at the vertical MIDDLE of the text.
            if _LeaderAttach is not None:
                try:
                    note.LeftAttachment  = _LeaderAttach.Midline
                    note.RightAttachment = _LeaderAttach.Midline
                except Exception:
                    pass

            # Leader from text mid-line back to the bank. Per-leader
            # failure is swallowed so one bad bank doesn't roll back the lot.
            try:
                leader = note.AddLeader(rec["leader_side"])
                leader.End = rec["leader_end"]
            except Exception:
                pass

            placed += 1

        t.Commit()
    except Exception as ex:
        t.RollBack()
        forms.alert("Failed during batch placement (placed {} of {} before "
                    "the error):\n\n{}: {}"
                    .format(placed, len(labels), type(ex).__name__, ex),
                    exitscript=True)

    log("Placed {} label(s).".format(placed))
    forms.alert("Annotated {} bank(s) of {}s from {} run(s){}{}."
                .format(placed, kind, len(items),
                        ", {} skipped".format(skipped) if skipped else "",
                        ", {} other element(s) ignored".format(ignored)
                        if ignored else ""))

    log.close()
except SystemExit:
    try:
        log.close()
    except Exception:
        pass
    raise
except Exception:
    import traceback
    _tb = traceback.format_exc()
    log("Error:")
    log("    " + _tb.replace("\n", "\n    "))
    try:
        log.close()
    except Exception:
        pass
    forms.alert("Annotate Pipes FAILED - the full trace is in the "
                "output window.\n\n{}".format(
                    _tb.strip().split("\n")[-1]))
