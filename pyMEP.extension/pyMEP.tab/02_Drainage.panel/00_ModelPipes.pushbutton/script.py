# -*- coding: utf-8 -*-
"""Model Pipes - read a Civil 3D LandXML export and place Revit pipes
straight from the LandXML (no intermediate CSV).

Flow:
  1. Pick the LandXML file.
  2. Parse + resolve each pipe's endpoints from its structures.
  3. Pick which networks to model.
  4. Map each network -> a project workset ('' = active). Saved mappings
     that still resolve are confirmed in ONE dialog; per-network pickers
     only appear when a mapping is missing or you ask to re-pick.
  5. Pipe type / piping system type / host level come straight from
     Settings when the configured names exist in the model; a picker only
     appears for the ones that don't resolve.
  6. Place (after the final confirm):
       - silently ensure the LandXML's circular sizes exist on the pipe
         Segment configured in Settings > LandXML (idempotent; non-fatal);
       - transform survey -> internal using the explicit survey transform
         from Settings > Pipes-Coordinates (E/N/Z offsets + rotation);
       - create pipes (+worksets), set Marks from the pipe name, set
         diameters snapped to the pipe type's available sizes;
       - all guarded so it can't trigger the corruption abort.

If a network comes in rotated/mirrored wrong, negate landxml_rot_deg in
Settings (e.g. 40.36 <-> -40.36).
"""

__title__  = "Model\nPipes"
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
    get_landxml_network_workset_map, save_landxml_network_workset_map,
)
from pymep_landxml import (
    parse_landxml, resolve_pipe_geometry, placement_rows,
    distinct_circular_sizes,
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
log = Logger(output, "LandXMLModelPipes2")
doc = revit.doc

default_pt = get_pipe_type_name()
default_st = get_pipe_system_type_name()
default_lvl = get_pipe_host_level_name()
saved_map = get_landxml_network_workset_map()

log("### Model Pipes from LandXML")

# ---------------------------------------------------------------------------
# 1. Pick LandXML + parse + resolve
# ---------------------------------------------------------------------------
xml_path = forms.pick_file(file_ext="xml", title="Pick a Civil 3D LandXML export")
if not xml_path:
    forms.alert("No LandXML selected.", exitscript=True)
log("LandXML: **{}**".format(os.path.basename(xml_path)))

log("Parsing + resolving (large exports take ~10-20s)...")
# Create the progress bar in its OWN try/except so a TypeError from an
# older forms.ProgressBar signature can't swallow a TypeError raised by
# the parse itself (which would silently re-run the whole 10-20s parse).
try:
    pb = forms.ProgressBar(title="Parsing + resolving LandXML...",
                           indeterminate=True)
except TypeError:
    pb = None
try:
    if pb is not None:
        with pb:
            parsed = parse_landxml(xml_path, log=log)
            resolve_pipe_geometry(parsed, log=log)
    else:
        parsed = parse_landxml(xml_path, log=log)
        resolve_pipe_geometry(parsed, log=log)
except Exception as ex:
    import traceback
    log(traceback.format_exc())
    forms.alert("Failed to parse/resolve LandXML:\n\n{}".format(ex),
                exitscript=True)

# Per-network placeable tally (circular + resolved)
net_place = {}
for p in parsed["pipes"]:
    if p.is_circular and p.resolved:
        n = p.network or "(unnamed)"
        net_place[n] = net_place.get(n, 0) + 1

networks = parsed["networks"]
total_place = sum(net_place.values())
if total_place == 0:
    forms.alert("No placeable pipes resolved from this LandXML.", exitscript=True)

# ---------------------------------------------------------------------------
# 2. Pick networks
# ---------------------------------------------------------------------------
class NetOption(object):
    def __init__(self, name):
        self.name_raw = name
        self.name = "{}   -   {} placeable".format(name, net_place.get(name, 0))

net_opts = [NetOption(n) for n in networks if net_place.get(n, 0) > 0]
log("{} placeable pipes across {} network(s).".format(
    total_place, len(net_opts)))
chosen = forms.SelectFromList.show(
    net_opts, title="Pick networks to model", button_name="Map worksets ->",
    multiselect=True, name_attr="name")
if not chosen:
    forms.alert("No networks picked.", exitscript=True)
chosen_networks = [o.name_raw for o in chosen]

# ---------------------------------------------------------------------------
# 3. Map each network -> workset
# ---------------------------------------------------------------------------
ACTIVE = "(active workset)"
worksets = list_worksets(doc)
network_workset_map = {}
if worksets:
    ws_choices = [ACTIVE] + worksets

    # Try the saved map first: if EVERY chosen network has a saved answer
    # that still resolves in this model ('' = active workset), offer the
    # whole map in ONE confirm instead of a dialog per network.
    proposed = {}
    for net in chosen_networks:
        if net not in saved_map:
            proposed = None
            break
        val = str(saved_map.get(net) or "").strip()
        if val and val not in worksets:
            proposed = None
            break
        proposed[net] = val

    use_saved = False
    if proposed is not None:
        prop_lines = ["  {}  ->  {}".format(n.split(" - ")[-1],
                      proposed[n] or ACTIVE) for n in chosen_networks]
        answer = forms.alert(
            "Saved workset mappings cover every chosen network:\n\n{}\n\n"
            "Use these?".format("\n".join(prop_lines)),
            title="Network -> workset",
            options=["Use these", "Re-pick"])
        if answer == "Use these":
            network_workset_map = dict(proposed)
            use_saved = True
            log("Using saved network -> workset map:")
            for n in chosen_networks:
                log("  - {} -> {}".format(n, proposed[n] or ACTIVE))

    if not use_saved:
        for net in chosen_networks:
            preset = str(saved_map.get(net) or "").strip()
            log("Map '{}' ({} pipes) to a workset.{}".format(
                net, net_place.get(net, 0),
                "  Previously: {}".format(preset) if preset else ""))
            picked = forms.SelectFromList.show(
                ws_choices,
                title="Workset for: {}".format(net.split(" - ")[-1]),
                button_name="Assign", multiselect=False)
            if not picked:
                forms.alert("Cancelled workset mapping - aborting.",
                            exitscript=True)
            network_workset_map[net] = "" if picked == ACTIVE else picked
else:
    forms.alert("Document is not workshared - pipes go on the active workset.",
                title="Not workshared")
    for net in chosen_networks:
        network_workset_map[net] = ""

# Persist the map only when the document is workshared - the
# non-workshared branch forces '' for every network, and saving that
# would clobber real workset names saved from a workshared session.
if worksets:
    merged = dict(saved_map); merged.update(network_workset_map)
    try:
        save_landxml_network_workset_map(merged)
    except Exception:
        pass

# ---------------------------------------------------------------------------
# 4. Pipe type / system type / level: use the configured names when they
#    resolve in the model; only show a picker for the ones that don't.
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

# Host level: configured name if it resolves, else picker (with elevation).
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
# 5. Confirm + place
# ---------------------------------------------------------------------------
map_lines = ["  {}  ->  {}".format(n.split(" - ")[-1],
             network_workset_map.get(n) or ACTIVE) for n in chosen_networks]
if forms.alert(
        "Ready to place.\n\nNetworks -> worksets:\n{}\n\n"
        "Pipe type: {}\nSystem type: {}\nLevel: {}\n\nPlace now?".format(
            "\n".join(map_lines), pipe_type_name, system_type_name,
            host_level_name),
        title="Confirm", options=["Place pipes", "Cancel"]) != "Place pipes":
    forms.alert("Cancelled.", exitscript=True)

# ---------------------------------------------------------------------------
# 5b. Now that the user confirmed, silently ensure the LandXML pipe sizes
#     exist on the configured Segment BEFORE placement - the diameter
#     snapping needs them (idempotent - existing sizes are skipped;
#     failures are non-fatal).
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
            _xml_sizes = distinct_circular_sizes(parsed)
            if _xml_sizes:
                _added, _present, _failed = add_sizes_to_segment(
                    doc, _segment, _xml_sizes, log=log)
                log("Pipe sizes on '{}': added {}, already present {}, "
                    "failed {}.".format(_seg_name, _added, _present, _failed))
            else:
                log("Pipe sizes: no circular sizes in this LandXML - "
                    "nothing to add.")
        except Exception as ex:
            log("Pipe sizes: could not update segment '{}' (non-fatal): {}"
                .format(_seg_name, ex))

rows = placement_rows(parsed, only_resolved=True, only_circular=True)
try:
    created, failed, skipped, mode, dia_set, mark_set = place_landxml_pipes(
        doc, rows, network_workset_map,
        pipe_type_name=pipe_type_name, system_type_name=system_type_name,
        host_level_name=host_level_name,
        network_filter=set(chosen_networks), log=log)
    forms.alert(
        "Done.\n\nPlaced: {}\nFailed: {}\nShort-skipped: {}\n"
        "Diameters set: {}\nMarks set: {}\n\nTransform: {}".format(
            created, failed, skipped, dia_set, mark_set, mode),
        title="Pipes modelled")
except Exception as ex:
    import traceback
    log("Error: {}".format(ex))
    log(traceback.format_exc())
    forms.alert("{}:\n\n{}".format(type(ex).__name__, ex))
finally:
    log.close()
