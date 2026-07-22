# -*- coding: utf-8 -*-
"""Place Pipes - read a dashboard export (MODEL-*.json or PIPES-*.json)
and place Revit pipes, driven from ONE setup window:

  * browse to the export - layers list with per-row workset assignment
    (pre-filled from the export's workset map / previous runs),
  * pipe type / system type / segment / host level dropdowns
    (Settings-configured names preselected),
  * option: every pipe takes the piping system type named exactly like
    its layer (created by Project Setup); unmapped layers fall back to
    the picked system type.

Placement mechanics are unchanged: the export's circular sizes are
ensured on the picked Segment (idempotent, non-fatal), the survey
transform tries the Settings offsets then the model's own position -
with the place-at-internal-origin rescue when neither fits - and every
pipe gets its Mark, snapped diameter, workset and 'Pipe Segment'.
Rectangular duct-bank rows are skipped: only circular runs become pipes.
"""

__title__  = "Place\nPipes"
__author__ = "Glent Group"

import os
import sys

# Force-reload pymep_* libs so edits on disk always take effect.
for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

from pymep_config import (
    get_pipe_type_name, get_pipe_system_type_name, get_pipe_host_level_name,
    get_landxml_segment_name,
    get_dashboard_layer_workset_map, save_dashboard_layer_workset_map,
)
from pymep_dashboard_pipes import (
    read_pipes_export, placement_rows, distinct_circular_sizes,
)
from pymep_landxml_place2 import (
    place_landxml_pipes, list_type_names, list_worksets, _el_name,
    model_survey_position,
)
from pymep_pipesizes import list_pipe_segments, add_sizes_to_segment
from pymep_revit import safe_name
from pymep_log import Logger

import clr
clr.AddReference("RevitAPI")
from System.Collections import ArrayList, Hashtable
from Autodesk.Revit.DB import FilteredElementCollector, Level
from Autodesk.Revit.DB.Plumbing import PipeType, PipingSystemType

output = script.get_output()
log = Logger(output, "DashboardPlacePipes")
doc = revit.doc

default_pt = get_pipe_type_name()
default_st = get_pipe_system_type_name()
default_lvl = get_pipe_host_level_name()
default_seg = get_landxml_segment_name()
saved_map = get_dashboard_layer_workset_map()
worksets = list_worksets(doc)
ACTIVE = "(active workset)"
SEG_ROUTE = "(leave to the pipe type's routing preferences)"
COORD_MODEL = "Model's Revit coordinates (Manage > Coordinates)"
COORD_SETTINGS = "pyMEP Settings offsets (E/N/Z/rotation)"
_mp = model_survey_position(doc)
HAS_GEOREF = _mp is not None and (abs(_mp[0]) > 1e-6
                                  or abs(_mp[1]) > 1e-6)

log("### Place Pipes from dashboard export")

XAML_PATH = os.path.join(
    os.path.dirname(os.path.abspath(sys.modules["pymep_config"].__file__)),
    "pymep_place_pipes.xaml")


def _fill_names(combo, names, preferred):
    combo.Items.Clear()
    for n in names:
        combo.Items.Add(n)
    if preferred and preferred in names:
        combo.SelectedItem = preferred
    elif combo.Items.Count:
        combo.SelectedIndex = 0


class PipesWindow(forms.WPFWindow):

    def __init__(self):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self.result = None
        self.meta = None
        self.rows = None
        self.notes = []
        self.path = None
        self.skipped_box = 0
        self.CmbWorkset.Items.Clear()
        self.CmbWorkset.Items.Add(ACTIVE)
        for w in worksets:
            self.CmbWorkset.Items.Add(w)
        self.CmbWorkset.SelectedIndex = 0
        self.CmbCoords.Items.Clear()
        self.CmbCoords.Items.Add(COORD_MODEL)
        self.CmbCoords.Items.Add(COORD_SETTINGS)
        self.CmbCoords.SelectedIndex = 0 if HAS_GEOREF else 1
        _fill_names(self.CmbPipeType, list_type_names(doc, PipeType),
                    default_pt)
        _fill_names(self.CmbSystemType,
                    list_type_names(doc, PipingSystemType), default_st)
        seg_names = [n for n, _s in list_pipe_segments(doc)]
        self.CmbSegment.Items.Clear()
        self.CmbSegment.Items.Add(SEG_ROUTE)
        for n in seg_names:
            self.CmbSegment.Items.Add(n)
        if default_seg and default_seg in seg_names:
            self.CmbSegment.SelectedItem = default_seg
        else:
            self.CmbSegment.SelectedIndex = 0
        self._levels = sorted(
            FilteredElementCollector(doc).OfClass(Level).ToElements(),
            key=lambda lv: lv.Elevation)
        self._level_names = [safe_name(lv) for lv in self._levels]
        _fill_names(self.CmbLevel, self._level_names, default_lvl)
        self.StatusText.Text = "Pick a dashboard MODEL or PIPES export " \
                               "to begin."

    def on_browse(self, sender, args):
        path = forms.pick_file(
            file_ext="json",
            title="Pick a dashboard MODEL or PIPES export (.json)")
        if not path:
            return
        try:
            meta, rows, notes = read_pipes_export(path)
        except Exception as ex:
            forms.alert("Could not read the export:\n\n{}".format(ex))
            return
        lay_place = {}
        skipped_box = 0
        for r in rows:
            if r["is_circular"]:
                lay_place[r["layer"]] = lay_place.get(r["layer"], 0) + 1
            else:
                skipped_box += 1
        if not lay_place:
            forms.alert("No placeable (circular) pipes in this export.")
            return
        self.path = path
        self.meta = meta
        self.rows = rows
        self.notes = notes
        self.skipped_box = skipped_box
        self.TxtExport.Text = path
        export_map = meta.get("workset_map")
        export_map = export_map if isinstance(export_map, dict) else {}
        items = ArrayList()
        for lay in sorted(lay_place, key=lambda s: s.lower()):
            ws = str(export_map.get(lay) or saved_map.get(lay) or "")
            if ws and worksets and ws not in worksets:
                ws = ""
            row = Hashtable()
            row["layer"] = lay
            row["pipes"] = str(lay_place[lay])
            row["workset"] = ws
            items.Add(row)
        self.LstLayers.ItemsSource = items
        self.LstLayers.SelectAll()
        self.StatusText.Text = ("{} placeable pipes across {} layers"
                                "{} - {}".format(
                                    sum(lay_place.values()), len(lay_place),
                                    ", {} duct-bank rows skipped".format(
                                        skipped_box) if skipped_box else "",
                                    meta.get("source") or ""))

    def on_assign_ws(self, sender, args):
        pick = self.CmbWorkset.SelectedItem
        if pick is None or self.LstLayers.ItemsSource is None:
            return
        ws = "" if str(pick) == ACTIVE else str(pick)
        for row in self.LstLayers.SelectedItems:
            row["workset"] = ws
        self.LstLayers.Items.Refresh()

    def on_place(self, sender, args):
        if not self.rows:
            forms.alert("Pick a dashboard export first.")
            return
        chosen = [str(row["layer"]) for row in self.LstLayers.SelectedItems]
        if not chosen:
            forms.alert("Select at least one layer in the list.")
            return
        if self.CmbPipeType.SelectedItem is None:
            forms.alert("This project has no pipe types - load one first.")
            return
        if self.CmbSystemType.SelectedItem is None:
            forms.alert("This project has no piping system types.")
            return
        if self.CmbLevel.SelectedItem is None:
            forms.alert("This project has no levels.")
            return
        ws_map = {}
        for row in self.LstLayers.ItemsSource:
            if str(row["layer"]) in set(chosen):
                ws_map[str(row["layer"])] = str(row["workset"] or "")
        seg = str(self.CmbSegment.SelectedItem)
        self.result = {
            "layers": chosen, "ws_map": ws_map,
            "pipe_type": str(self.CmbPipeType.SelectedItem),
            "system_type": str(self.CmbSystemType.SelectedItem),
            "segment": "" if seg == SEG_ROUTE else seg,
            "level": str(self.CmbLevel.SelectedItem),
            "layer_systems": bool(self.ChkLayerSystems.IsChecked),
            "prefer_model": str(self.CmbCoords.SelectedItem) == COORD_MODEL,
        }
        self.Close()

    def on_cancel(self, sender, args):
        self.Close()


win = PipesWindow()
win.ShowDialog()
res = win.result
if not res:
    log("Cancelled.")
    log.close()
    script.exit()

meta = win.meta
rows = win.rows
skipped_box = win.skipped_box
for n in win.notes:
    log(n)
if meta.get("source"):
    log("Source: **{}**   scope: {}".format(meta.get("source"),
                                            meta.get("scope") or "?"))
chosen_layers = res["layers"]
layer_workset_map = res["ws_map"]
pipe_type_name = res["pipe_type"]
system_type_name = res["system_type"]
segment_name = res["segment"]
host_level_name = res["level"]
log("Layers: " + ", ".join(
    "{} -> {}".format(l, layer_workset_map.get(l) or ACTIVE)
    for l in chosen_layers))
log("Coordinates: **{}**".format(
    COORD_MODEL if res["prefer_model"] else COORD_SETTINGS))
log("Pipe type **{}**, fallback system **{}**, segment **{}**, level "
    "**{}**.".format(pipe_type_name, system_type_name,
                     segment_name or "(routing preferences)",
                     host_level_name))

# Persist only when workshared (the non-workshared branch forces '' and
# would clobber real names) - same rule as before.
if worksets:
    merged = dict(saved_map)
    merged.update(layer_workset_map)
    try:
        save_dashboard_layer_workset_map(merged)
    except Exception:
        pass

# layer-named system types (same automation as Place Structures) -----------
network_system_map = None
if res["layer_systems"]:
    by_name = {}
    for pst in FilteredElementCollector(doc).OfClass(PipingSystemType):
        nm = _el_name(pst).strip().lower()
        if nm and nm not in by_name:
            by_name[nm] = pst.Id
    network_system_map = {}
    missing = []
    for lay in chosen_layers:
        stid = by_name.get(lay.strip().lower())
        if stid is not None:
            network_system_map[lay] = stid
        else:
            missing.append(lay)
    log("Layer-named system types found for **{}** of {} layer(s){}."
        .format(len(network_system_map), len(chosen_layers),
                " - falling back to '{}' for: {} (run Project Setup from "
                "this export to create them)".format(
                    system_type_name, ", ".join(missing))
                if missing else ""))
    if not network_system_map:
        network_system_map = None

# ---------------------------------------------------------------------------
# Ensure the export's circular sizes exist on the picked Segment
# (identical to Model Pipes; idempotent, non-fatal)
# ---------------------------------------------------------------------------
_seg_name = segment_name or get_landxml_segment_name()
if not _seg_name:
    log("Pipe sizes: no segment picked or configured - skipped "
        "(use Create Pipe Sizes for a manual run).")
else:
    _segment = None
    for _nm, _sg in list_pipe_segments(doc):
        if _nm == _seg_name:
            _segment = _sg
            break
    if _segment is None:
        log("Pipe sizes: segment '{}' not found in this model - skipped."
            .format(_seg_name))
    else:
        try:
            _sizes = distinct_circular_sizes(
                [r for r in rows if r["layer"] in set(chosen_layers)])
            if _sizes:
                _added, _present, _failed = add_sizes_to_segment(
                    doc, _segment, _sizes, log=log)
                log("Pipe sizes on '{}': added {}, already present {}, "
                    "failed {}.".format(_seg_name, _added, _present, _failed))
            else:
                log("Pipe sizes: no circular sizes in this export - "
                    "nothing to add.")
        except Exception as ex:
            log("Pipe sizes: could not update segment '{}' (non-fatal): {}"
                .format(_seg_name, ex))

# ---------------------------------------------------------------------------
# Place - same engine as Model Pipes; on a transform failure, offer the
# export origin as the offset (same rescue the structure placer offers)
# ---------------------------------------------------------------------------
p_rows = placement_rows(rows, only_circular=True, layers=set(chosen_layers))


def _place(off_e=None, off_n=None, off_z=None, rot=None):
    return place_landxml_pipes(
        doc, p_rows, layer_workset_map,
        pipe_type_name=pipe_type_name, system_type_name=system_type_name,
        host_level_name=host_level_name,
        off_e_m=off_e, off_n_m=off_n, off_z_m=off_z, rot_deg=rot,
        network_filter=set(chosen_layers), log=log,
        segment_name=segment_name or None,
        network_system_map=network_system_map,
        prefer_model=res["prefer_model"])


try:
    try:
        created, failed, skipped, mode, dia_set, mark_set = _place()
    except RuntimeError as ex:
        # Transform failure happens BEFORE anything is created.
        log("{}".format(ex))
        o = meta.get("origin") or {}
        oe, on = o.get("easting"), o.get("northing")
        if oe is None or on is None:
            raise
        choice = forms.alert(
            "{}\n\nI can place the site at the model's INTERNAL ORIGIN "
            "instead, using the export origin as the offset:\n"
            "    E {:.3f}    N {:.3f}    rot 0\n\n"
            "Everything stays correctly positioned relative to itself; "
            "you can set shared coordinates later.".format(ex, oe, on),
            title="No usable survey transform",
            options=["Place at internal origin",
                     "Place + save offset to Settings", "Cancel"])
        if not choice or not choice.startswith("Place"):
            forms.alert("Cancelled - nothing was created.", exitscript=True)
        if "save" in choice:
            try:
                from pymep_config import load_settings, save_settings
                s = load_settings()
                s["landxml_off_e_m"] = str(oe)
                s["landxml_off_n_m"] = str(on)
                s["landxml_off_z_m"] = "0.0"
                s["landxml_rot_deg"] = "0.0"
                save_settings(s)
                log("Saved to Settings: E {}  N {}  Z 0  rot 0".format(oe, on))
            except Exception as ex2:
                log("Could not save Settings: {}".format(ex2))
        created, failed, skipped, mode, dia_set, mark_set = _place(
            off_e=float(oe), off_n=float(on), off_z=0.0, rot=0.0)

    forms.alert(
        "Done.\n\nPlaced: {}\nFailed: {}\nShort-skipped: {}\n"
        "Diameters set: {}\nMarks set: {}\nDuct-bank rows skipped: {}\n\n"
        "Transform: {}".format(created, failed, skipped, dia_set, mark_set,
                               skipped_box, mode),
        title="Pipes modelled")
except Exception as ex:
    import traceback
    log("Error: {}".format(ex))
    log(traceback.format_exc())
    forms.alert("{}:\n\n{}".format(type(ex).__name__, ex))
finally:
    log.close()
