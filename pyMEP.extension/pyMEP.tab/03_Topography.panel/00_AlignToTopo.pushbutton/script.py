# -*- coding: utf-8 -*-
"""Align to Topo - drop family instances onto a surface.

Flow:
  1. Pick the family types to adjust (searchable checkbox list of every
     placed 'Family : Type', with instance counts).
  2. The surfaces: a pre-selection of Toposolids / Topography / Floors
     is used when you have one; otherwise pick them in the model
     (multi-select, Finish when done).
  3. Every instance of the chosen types is projected straight down onto
     the TOP of the chosen surfaces at its own X,Y, and its
     'Elevation from Level' / 'Offset from Host' is set so the instance
     sits exactly on the surface there.

Instances whose X,Y is not above any chosen surface are reported and
left untouched, as are instances already on the surface (within 0.5 mm).
"""

__title__  = "Align\nto Topo"
__author__ = "Glent Group"

import sys

# Force-reload pymep_* libs so edits on disk always take effect.
for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

from pymep_topo_align import (
    is_surface_element, list_leveled_family_types,
    align_instances_to_surfaces,
)
from pymep_log import Logger

from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from Autodesk.Revit.Exceptions import OperationCanceledException

output = script.get_output()
log = Logger(output, "AlignToTopo")
doc = revit.doc
uidoc = revit.uidoc

log("### Align to Topo")

# ---------------------------------------------------------------------------
# 1. Which family types (searchable, checkboxes)
# ---------------------------------------------------------------------------
types = list_leveled_family_types(doc)
if not types:
    forms.alert("No point-placed family instances in this model.",
                exitscript=True)


class TypeOption(object):
    def __init__(self, label, sym_id, count):
        self.sym_id = sym_id
        self.name = "{}   ({} placed)".format(label, count)


opts = [TypeOption(*t) for t in types]
picked = forms.SelectFromList.show(
    opts,
    title="Align to Topo - pick the family types to adjust",
    button_name="Pick surfaces ->",
    multiselect=True,
    name_attr="name")
if not picked:
    forms.alert("Nothing picked.", exitscript=True)
symbol_ids = [o.sym_id for o in picked]
log("Types picked: **{}**".format(len(symbol_ids)))
for o in picked:
    log("  - {}".format(o.name))


# ---------------------------------------------------------------------------
# 2. Which surfaces: pre-selection first, else pick in the model
# ---------------------------------------------------------------------------
class SurfaceFilter(ISelectionFilter):
    def AllowElement(self, el):
        return is_surface_element(el)

    def AllowReference(self, ref, pt):
        return True


surface_ids = []
try:
    for eid in uidoc.Selection.GetElementIds():
        if is_surface_element(doc.GetElement(eid)):
            surface_ids.append(eid)
except Exception:
    pass

if surface_ids:
    log("Using the current selection: **{}** surface(s).".format(
        len(surface_ids)))
else:
    forms.alert(
        "Now pick the surface(s) to align to - Toposolids, Topography "
        "or Floors - then press Finish.",
        title="Pick surfaces")
    try:
        refs = uidoc.Selection.PickObjects(
            ObjectType.Element, SurfaceFilter(),
            "Pick Toposolids / Topography / Floors, then Finish")
    except OperationCanceledException:
        forms.alert("Cancelled - nothing was changed.", exitscript=True)
    surface_ids = [r.ElementId for r in refs]
    if not surface_ids:
        forms.alert("No surfaces picked - nothing was changed.",
                    exitscript=True)
    log("Surfaces picked: **{}**.".format(len(surface_ids)))

# ---------------------------------------------------------------------------
# 3. Confirm + run
# ---------------------------------------------------------------------------
if forms.alert(
        "Set 'Elevation from Level' on every instance of {} type(s) so "
        "each sits on the TOP of the {} chosen surface(s) at its own "
        "X,Y?\n\nInstances not above any chosen surface are left "
        "untouched and reported.".format(len(symbol_ids), len(surface_ids)),
        title="Align to Topo",
        options=["Align", "Cancel"]) != "Align":
    forms.alert("Cancelled.", exitscript=True)

try:
    adjusted, missed, skipped, unchanged = align_instances_to_surfaces(
        doc, symbol_ids, surface_ids, log=log)
    forms.alert(
        "Done.\n\nAdjusted: {}\nAlready on surface: {}\n"
        "No surface under X,Y: {}\nSkipped (no level / offset param / "
        "location point): {}".format(adjusted, unchanged, missed, skipped),
        title="Align to Topo")
except Exception as ex:
    import traceback
    log("Error: {}".format(ex))
    log(traceback.format_exc())
    forms.alert("{}:\n\n{}".format(type(ex).__name__, ex))
finally:
    log.close()
