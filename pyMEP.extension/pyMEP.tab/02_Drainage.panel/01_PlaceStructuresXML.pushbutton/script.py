# -*- coding: utf-8 -*-
"""Place Structures - place a Revit family instance at every LandXML
structure of a chosen network and type.

Flow:
  1. Pick the LandXML file.
  2. Pick a network (Storm / Foul / Fire / Glycol / ...).
  3. Pick a structure TYPE within that network (e.g. "Concentric Structure
     1,200 dia ...", "375 dia road gully", "Downpipe with rest bend", ...).
     The picker shows the count of each type.
  4. Pick the Revit family type to place.
  5. Pick a workset (the host level is chosen automatically - the saved
     pipe level, or the lowest level).
  6. Place one instance at every matching structure - at its plan position
     and rim level (falling back to lowest invert) - using the SAME
     survey->internal transform as the pipe placer, so structures land in
     the same frame as the pipes. Structure name -> instance Mark; rim and
     invert levels written to instance params when matching ones exist.
"""

__title__  = "Place Structures\n(LandXML)"
__author__ = "Glent Group"

import os
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

from pymep_config import get_pipe_host_level_name
from pymep_landxml import (
    parse_landxml, structure_networks_and_types, structure_rows,
)
from pymep_structures_place import (
    place_structures, list_family_symbols, list_worksets,
)
from pymep_revit import safe_name
from pymep_log import Logger

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import FilteredElementCollector, Level

output = script.get_output()
log = Logger(output, "LandXMLPlaceStructures")
doc = revit.doc
default_lvl = get_pipe_host_level_name()

log("### Place Structures from LandXML")

# ---------------------------------------------------------------------------
# 1. LandXML + parse
# ---------------------------------------------------------------------------
xml_path = forms.pick_file(file_ext="xml", title="Pick a Civil 3D LandXML export")
if not xml_path:
    forms.alert("No LandXML selected.", exitscript=True)
log("LandXML: **{}**".format(os.path.basename(xml_path)))

log("Parsing (large exports take ~10-20s)...")
# Create the progress bar in its OWN try/except so a TypeError from an
# older forms.ProgressBar signature can't swallow a TypeError raised by
# the parse itself (which would silently re-run the whole 10-20s parse).
try:
    pb = forms.ProgressBar(title="Parsing LandXML...", indeterminate=True)
except TypeError:
    pb = None
try:
    if pb is not None:
        with pb:
            parsed = parse_landxml(xml_path, log=log)
    else:
        parsed = parse_landxml(xml_path, log=log)
except Exception as ex:
    import traceback
    log(traceback.format_exc())
    forms.alert("Failed to parse LandXML:\n\n{}".format(ex), exitscript=True)

nt = structure_networks_and_types(parsed)
if not nt:
    forms.alert("No placeable structures found in this LandXML.", exitscript=True)

# ---------------------------------------------------------------------------
# 2. Pick network
# ---------------------------------------------------------------------------
class NetOpt(object):
    def __init__(self, net, types):
        self.net = net
        self.name = "{}   -   {} structures, {} types".format(
            net, sum(types.values()), len(types))

net_opts = [NetOpt(net, types) for net, types in
            sorted(nt.items(), key=lambda kv: -sum(kv[1].values()))]
net_pick = forms.SelectFromList.show(
    net_opts, title="Pick a network", button_name="Next: structure type ->",
    multiselect=False, name_attr="name")
if not net_pick:
    forms.alert("No network picked.", exitscript=True)
network = net_pick.net

# ---------------------------------------------------------------------------
# 3. Pick structure type within the network
# ---------------------------------------------------------------------------
class TypeOpt(object):
    def __init__(self, desc, count):
        self.desc = desc
        self.count = count
        self.name = "{}  x{}".format(desc, count)

type_opts = [TypeOpt(d, c) for d, c in
             sorted(nt[network].items(), key=lambda kv: -kv[1])]
log("One family instance will be placed at each of these.")
type_pick = forms.SelectFromList.show(
    type_opts, title="Pick a structure type on '{}'".format(
        network.split(" - ")[-1]),
    button_name="Next: family ->", multiselect=False, name_attr="name")
if not type_pick:
    forms.alert("No structure type picked.", exitscript=True)
desc = type_pick.desc

rows = structure_rows(parsed, network=network, desc=desc)
if not rows:
    forms.alert("No structures matched.", exitscript=True)
log("Network **{}**, type **{}**: **{}** instances to place.".format(
    network, desc, len(rows)))

# ---------------------------------------------------------------------------
# 4. Pick family type
# ---------------------------------------------------------------------------
syms = list_family_symbols(doc)
if not syms:
    forms.alert("This project has no loadable family types. Load a family "
                "first (e.g. your manhole / gully / RWP family).",
                exitscript=True)

class SymOpt(object):
    def __init__(self, label, sym):
        self.label = label
        self.sym = sym
        self.name = label

sym_opts = [SymOpt(lbl, sym) for lbl, sym in syms]
log("Shown as 'Family : Type'. Tip: type in the search box to filter.")
sym_pick = forms.SelectFromList.show(
    sym_opts, title="Pick the family type to place ({} instances)".format(
        len(rows)),
    button_name="Use this family", multiselect=False, name_attr="name")
if not sym_pick:
    forms.alert("No family picked.", exitscript=True)
symbol = sym_pick.sym

# ---------------------------------------------------------------------------
# 5. Workset (level is resolved silently - see below)
# ---------------------------------------------------------------------------
# No level prompt: structures are placed at their rim elevation (absolute Z),
# so the level is only a host reference. Use the saved pipe host level if it
# exists, otherwise the lowest level in the model - matching the pipes.
_levels = sorted(FilteredElementCollector(doc).OfClass(Level).ToElements(),
                 key=lambda lv: lv.Elevation)
if not _levels:
    forms.alert("This project has no levels.", exitscript=True)
host_level_name = None
for lv in _levels:
    if safe_name(lv) == default_lvl:
        host_level_name = default_lvl
        break
if host_level_name is None:
    host_level_name = safe_name(_levels[0])
log("Host level (auto): **{}**".format(host_level_name))

ACTIVE = "(active workset)"
worksets = list_worksets(doc)
workset_name = ""
if worksets:
    log("'{}' places on the current active workset.".format(ACTIVE))
    ws_pick = forms.SelectFromList.show(
        [ACTIVE] + worksets, title="Pick a workset", button_name="Use this",
        multiselect=False)
    if not ws_pick:
        forms.alert("No workset picked - aborting.", exitscript=True)
    workset_name = "" if ws_pick == ACTIVE else ws_pick

# ---------------------------------------------------------------------------
# 6. Confirm + place
# ---------------------------------------------------------------------------
if forms.alert(
        "Place {} instances of:\n  {}\n\n"
        "at every '{}' on {}\n\n"
        "Workset: {}\nLevel (auto): {}\n\nPlace now?".format(
            len(rows), sym_pick.label, desc, network.split(" - ")[-1],
            workset_name or ACTIVE, host_level_name),
        title="Confirm", options=["Place", "Cancel"]) != "Place":
    forms.alert("Cancelled.", exitscript=True)

try:
    created, failed, skipped, mode = place_structures(
        doc, rows, symbol, host_level_name=host_level_name,
        workset_name=workset_name, log=log)
    forms.alert(
        "Done.\n\nPlaced: {}\nFailed: {}\nSkipped: {}\n\nTransform: {}".format(
            created, failed, skipped, mode),
        title="Structures placed")
except Exception as ex:
    import traceback
    log("Error: {}".format(ex))
    log(traceback.format_exc())
    forms.alert("{}:\n\n{}".format(type(ex).__name__, ex))
finally:
    log.close()
