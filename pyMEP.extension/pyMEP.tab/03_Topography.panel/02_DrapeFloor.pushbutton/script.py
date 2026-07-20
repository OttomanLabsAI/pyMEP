# -*- coding: utf-8 -*-
"""Drape Floor - pick a floor, then a topo surface; every slab-shape
sub-element point of the floor is moved to the surface's level at that
X,Y (vertical projection from above, nearest hit wins - stacked
surfaces resolve to the top one).

Prior shape edits on the floor are reset first, so the result follows
the surface exactly at the floor's existing points. For a denser drape
add points to the floor first (Modify | Floors > Shape Editing > Add
Point) and re-run - added points are draped too.
"""

__title__  = "Drape Floor\nto Topo"
__author__ = "Glent Group"

import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

from Autodesk.Revit.DB import Floor

from pymep_log import Logger
from pymep_revit import safe_name
from pymep_topo_align import drape_floor_to_surfaces, is_surface_element

output = script.get_output()
log = Logger(output, "DrapeFloor")
doc = revit.doc

log("### Drape floor to topo")

floor_el = revit.pick_element("Pick the FLOOR to drape")
if floor_el is None:
    forms.alert("Nothing picked.", exitscript=True)
if not isinstance(floor_el, Floor):
    forms.alert("That element is a {} - pick a FLOOR.".format(
        type(floor_el).__name__), exitscript=True)

topo_el = revit.pick_element(
    "Pick the surface to follow (toposolid / topography / floor)")
if topo_el is None:
    forms.alert("No surface picked.", exitscript=True)
if not is_surface_element(topo_el):
    forms.alert("That element is a {} - pick a toposolid, a legacy "
                "topography surface or a floor.".format(
                    type(topo_el).__name__), exitscript=True)
if topo_el.Id == floor_el.Id:
    forms.alert("The floor and the surface are the same element.",
                exitscript=True)

log("Floor: **{}** (id {})".format(safe_name(doc.GetElement(
    floor_el.GetTypeId())) or "Floor", floor_el.Id))
log("Surface: **{}** (id {})".format(type(topo_el).__name__, topo_el.Id))

try:
    moved, missed, total = drape_floor_to_surfaces(
        doc, floor_el, [topo_el.Id], log=log)
except Exception as ex:
    import traceback
    log(traceback.format_exc())
    log.close()
    forms.alert("Drape failed - nothing was changed:\n\n{}".format(ex),
                exitscript=True)

log.close()
if moved:
    forms.alert("Draped the floor: {} of {} slab-shape point(s) moved "
                "onto the surface{}.\n\nNeed it to follow the surface "
                "more closely? Add points to the floor (Shape Editing > "
                "Add Point) and run this again.".format(
                    moved, total,
                    ", {} missed (no surface at that XY)".format(missed)
                    if missed else ""))
else:
    forms.alert("No points could be moved ({} of {} missed the surface "
                "in plan) - does the floor sit over the topo?".format(
                    missed, total))
