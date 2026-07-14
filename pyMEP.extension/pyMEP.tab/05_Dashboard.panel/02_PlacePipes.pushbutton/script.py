# -*- coding: utf-8 -*-
"""Place Pipes - read a pipes export from the OttomanLabs utilities
dashboard and place Revit pipes, exactly like the LandXML Model Pipes
button but fed from the dashboard JSON.

Flow (mirrors Drainage > Model Pipes):
  1. Pick the dashboard pipes export (.json) - use the EXPORT button in
     the 3D viewer; it exports whatever is currently in view, so isolate
     a layer/group first to place just that subset.
  2. Read the export (start/end/diameter per pipe; rectangular duct-bank
     rows are reported and skipped - only circular runs become pipes).
  3. Pick which layers to model.
  4. Map each layer -> a project workset ('' = active). Saved mappings
     that still resolve are confirmed in ONE dialog.
  5. Pipe type / piping system type / host level come straight from
     Settings when the configured names exist in the model; a picker
     only appears for the ones that don't resolve.
  6. Place (after the final confirm):
       - silently ensure the export's circular sizes exist on the pipe
         Segment configured in Settings > LandXML (idempotent, non-fatal);
       - transform survey -> internal exactly as Model Pipes does
         (Settings offsets first, model survey position fallback); if
         neither fits, offers to place at the model's internal origin
         using the EXPORT's own origin as the offset;
       - create pipes (+worksets), set Marks from the pipe name, set
         diameters snapped to the pipe type's available sizes.
"""

__title__  = "Place Pipes\n(Dashboard)"
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
    place_landxml_pipes, list_type_names, list_worksets,
)
from pymep_pipesizes import list_pipe_segments, add_sizes_to_segment
from pymep_revit import safe_name
from pymep_log import Logger

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import FilteredElementCollector, Level
from Autodesk.Revit.DB.Plumbing import PipeType, PipingSystemType

output = script.get_output()
log = Logger(output, "DashboardPlacePipes")
doc = revit.doc

default_pt = get_pipe_type_name()
default_st = get_pipe_system_type_name()
default_lvl = get_pipe_host_level_name()
saved_map = get_dashboard_layer_workset_map()

log("### Place Pipes from dashboard export")

# ---------------------------------------------------------------------------
# 1. Pick export + read
# ---------------------------------------------------------------------------
json_path = forms.pick_file(file_ext="json",
                            title="Pick a dashboard PIPES export (.json)")
if not json_path:
    forms.alert("No export selected.", exitscript=True)
log("Export: **{}**".format(os.path.basename(json_path)))

try:
    meta, rows, notes = read_pipes_export(json_path)
except Exception as ex:
    import traceback
    log("Read failed:")
    log(traceback.format_exc())
    log.close()
    forms.alert("Could not read the export:\n\n{}\n\nThe full traceback is "
                "in the pyRevit output window and the log file."
                .format(ex), exitscript=True)
for n in notes:
    log(n)
if meta.get("source"):
    log("Source: **{}**   scope: {}".format(meta.get("source"),
                                            meta.get("scope") or "?"))

# Per-layer placeable tally (circular only - duct banks are Encasement's job)
lay_place = {}
skipped_box = 0
for r in rows:
    if r["is_circular"]:
        lay_place[r["layer"]] = lay_place.get(r["layer"], 0) + 1
    else:
        skipped_box += 1
if skipped_box:
    log("{} rectangular duct-bank row(s) will be skipped (pipes are round; "
        "use the Encasement workflow for duct banks).".format(skipped_box))

total_place = sum(lay_place.values())
if total_place == 0:
    forms.alert("No placeable (circular) pipes in this export.",
                exitscript=True)

# ---------------------------------------------------------------------------
# 2. Pick layers
# ---------------------------------------------------------------------------
class LayerOption(object):
    def __init__(self, name):
        self.name_raw = name
        self.name = "{}   -   {} placeable".format(name, lay_place.get(name, 0))

lay_opts = [LayerOption(n) for n in sorted(lay_place) if lay_place.get(n, 0)]
log("{} placeable pipes across {} layer(s).".format(total_place,
                                                    len(lay_opts)))
chosen = forms.SelectFromList.show(
    lay_opts, title="Pick layers to model", button_name="Map worksets ->",
    multiselect=True, name_attr="name")
if not chosen:
    forms.alert("No layers picked.", exitscript=True)
chosen_layers = [o.name_raw for o in chosen]

# ---------------------------------------------------------------------------
# 3. Map each layer -> workset (saved map first, one confirm)
# ---------------------------------------------------------------------------
ACTIVE = "(active workset)"
worksets = list_worksets(doc)
layer_workset_map = {}
if worksets:
    ws_choices = [ACTIVE] + worksets

    proposed = {}
    for lay in chosen_layers:
        if lay not in saved_map:
            proposed = None
            break
        val = str(saved_map.get(lay) or "").strip()
        if val and val not in worksets:
            proposed = None
            break
        proposed[lay] = val

    use_saved = False
    if proposed is not None:
        prop_lines = ["  {}  ->  {}".format(l, proposed[l] or ACTIVE)
                      for l in chosen_layers]
        answer = forms.alert(
            "Saved workset mappings cover every chosen layer:\n\n{}\n\n"
            "Use these?".format("\n".join(prop_lines)),
            title="Layer -> workset",
            options=["Use these", "Re-pick"])
        if answer == "Use these":
            layer_workset_map = dict(proposed)
            use_saved = True
            log("Using saved layer -> workset map:")
            for l in chosen_layers:
                log("  - {} -> {}".format(l, proposed[l] or ACTIVE))

    if not use_saved:
        for lay in chosen_layers:
            preset = str(saved_map.get(lay) or "").strip()
            log("Map '{}' ({} pipes) to a workset.{}".format(
                lay, lay_place.get(lay, 0),
                "  Previously: {}".format(preset) if preset else ""))
            picked = forms.SelectFromList.show(
                ws_choices, title="Workset for: {}".format(lay),
                button_name="Assign", multiselect=False)
            if not picked:
                forms.alert("Cancelled workset mapping - aborting.",
                            exitscript=True)
            layer_workset_map[lay] = "" if picked == ACTIVE else picked
else:
    forms.alert("Document is not workshared - pipes go on the active "
                "workset.", title="Not workshared")
    for lay in chosen_layers:
        layer_workset_map[lay] = ""

# Persist only when workshared (same rule as Model Pipes: the
# non-workshared branch forces '' and would clobber real names).
if worksets:
    merged = dict(saved_map)
    merged.update(layer_workset_map)
    try:
        save_dashboard_layer_workset_map(merged)
    except Exception:
        pass

# ---------------------------------------------------------------------------
# 4. Pipe type / system type / level from Settings, pickers as fallback
# ---------------------------------------------------------------------------
def _pick(cls, title, default, what):
    names = list_type_names(doc, cls)
    if not names:
        forms.alert("This project has no {}s.".format(what), exitscript=True)
    if default and default in names:
        log("Using configured {}: **{}** (change in Settings).".format(
            what, default))
        return default
    if default:
        log("Configured default {} '{}' not found in this model - "
            "pick one.".format(what, default))
    picked = forms.SelectFromList.show(
        names, title=title, button_name="Use this", multiselect=False)
    if not picked:
        forms.alert("Nothing picked - aborting.", exitscript=True)
    return picked

pipe_type_name = _pick(PipeType, "Pick the pipe type", default_pt,
                       "pipe type")
system_type_name = _pick(PipingSystemType, "Pick the piping system type",
                         default_st, "piping system type")

_levels = sorted(FilteredElementCollector(doc).OfClass(Level).ToElements(),
                 key=lambda lv: lv.Elevation)
if not _levels:
    forms.alert("This project has no levels.", exitscript=True)

host_level_name = None
if default_lvl and any(safe_name(lv) == default_lvl for lv in _levels):
    host_level_name = default_lvl
    log("Using configured host level: **{}** (change in Settings)."
        .format(default_lvl))
else:
    class LvlOpt(object):
        def __init__(self, lv):
            self.name_raw = safe_name(lv)
            self.name = "{}   (elev {:.3f} m)".format(self.name_raw,
                                                      lv.Elevation * 0.3048)

    _lvl_opts = [LvlOpt(lv) for lv in _levels]
    if default_lvl:
        log("Configured default level '{}' not found in this model - "
            "pick one.".format(default_lvl))
    _lvl_pick = forms.SelectFromList.show(
        _lvl_opts, title="Pick the host level", button_name="Use this",
        multiselect=False, name_attr="name")
    if not _lvl_pick:
        forms.alert("No level picked.", exitscript=True)
    host_level_name = _lvl_pick.name_raw

# ---------------------------------------------------------------------------
# 5. Confirm
# ---------------------------------------------------------------------------
map_lines = ["  {}  ->  {}".format(l, layer_workset_map.get(l) or ACTIVE)
             for l in chosen_layers]
if forms.alert(
        "Ready to place.\n\nLayers -> worksets:\n{}\n\n"
        "Pipe type: {}\nSystem type: {}\nLevel: {}\n\nPlace now?".format(
            "\n".join(map_lines), pipe_type_name, system_type_name,
            host_level_name),
        title="Confirm", options=["Place pipes", "Cancel"]) != "Place pipes":
    forms.alert("Cancelled.", exitscript=True)

# ---------------------------------------------------------------------------
# 5b. Ensure the export's circular sizes exist on the configured Segment
#     (identical to Model Pipes; idempotent, non-fatal)
# ---------------------------------------------------------------------------
_seg_name = get_landxml_segment_name()
if not _seg_name:
    log("Pipe sizes: no segment configured in Settings > LandXML - skipped "
        "(use Create Pipe Sizes for a manual run).")
else:
    _segment = None
    for _nm, _sg in list_pipe_segments(doc):
        if _nm == _seg_name:
            _segment = _sg
            break
    if _segment is None:
        log("Pipe sizes: configured segment '{}' not found in this model - "
            "skipped (use Create Pipe Sizes for a manual run)."
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
# 6. Place - same engine as Model Pipes; on a transform failure, offer the
#    export origin as the offset (same rescue the structure placer offers)
# ---------------------------------------------------------------------------
p_rows = placement_rows(rows, only_circular=True, layers=set(chosen_layers))


def _place(off_e=None, off_n=None, off_z=None, rot=None):
    return place_landxml_pipes(
        doc, p_rows, layer_workset_map,
        pipe_type_name=pipe_type_name, system_type_name=system_type_name,
        host_level_name=host_level_name,
        off_e_m=off_e, off_n_m=off_n, off_z_m=off_z, rot_deg=rot,
        network_filter=set(chosen_layers), log=log)


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
