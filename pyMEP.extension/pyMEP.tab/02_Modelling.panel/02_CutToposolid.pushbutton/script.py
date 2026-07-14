# -*- coding: utf-8 -*-
"""Cut Toposolid - excavate a Toposolid using the bottom outline of the
current selection of ducts / conduit and electrical equipment.

Workflow:

  1. Reads the *current selection*. Keeps model elements that have real
     solid geometry (conduit, cable tray, ducts, fittings, electrical
     equipment, etc.). Annotations and elements with no solid are skipped.
  2. Asks you to pick the target Toposolid.
  3. For each selected element it projects the solid straight down to get
     the true bottom outline, builds a vertical void from that outline up to
     just past the top of the Toposolid, and cuts the Toposolid with it via
     InstanceVoidCutUtils.

No 45 degree batter yet - the cut is a straight vertical prism. The cutter
instances are left in the model (deleting them removes the cut) and tagged
with the comment 'pyMEP_TopoCut' so they can be found later.
"""

__title__  = "Cut\nToposolid"
__author__ = "Glent Group"

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (
    FilteredElementCollector, Options, ViewDetailLevel,
    Solid, GeometryInstance, BuiltInCategory,
)
from Autodesk.Revit.UI.Selection import ObjectType

from pyrevit import revit, forms, script

from pymep_revit    import safe_name
from pymep_log      import Logger
from pymep_topo_cut import cut_toposolid_from_elements, CUTTER_MARK


output = script.get_output()
log    = Logger(output, "CutToposolid")
doc    = revit.doc
uidoc  = revit.uidoc

log("### Cut Toposolid")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _has_solid(elem):
    """True if the element yields at least one non-empty solid (recursing
    into family geometry)."""
    opts = Options()
    opts.ComputeReferences = False
    opts.IncludeNonVisibleObjects = False
    opts.DetailLevel = ViewDetailLevel.Fine
    try:
        ge = elem.get_Geometry(opts)
    except Exception:
        return False
    if ge is None:
        return False
    return _geom_has_solid(ge)


def _geom_has_solid(geom_elem):
    for g in geom_elem:
        if isinstance(g, Solid):
            if g.Volume > 1e-9 and g.Faces.Size > 0:
                return True
        elif isinstance(g, GeometryInstance):
            try:
                if _geom_has_solid(g.GetInstanceGeometry()):
                    return True
            except Exception:
                continue
    return False


def _cat_name(elem):
    try:
        c = elem.Category
        return c.Name if c is not None else "(no category)"
    except Exception:
        return "(no category)"


def _is_toposolid(elem):
    """Identify a Toposolid by its built-in category, without importing the
    Toposolid type (which only exists in Revit 2024+ assemblies)."""
    if elem is None:
        return False
    try:
        cat = elem.Category
        if cat is None:
            return False
        return cat.Id.IntegerValue == int(BuiltInCategory.OST_Toposolid)
    except Exception:
        # OST_Toposolid may not exist on very old API; fall back to name.
        return _cat_name(elem).strip().lower() == "toposolid"


# ---------------------------------------------------------------------------
# 1. Gather the current selection -> cuttable elements
# ---------------------------------------------------------------------------
sel_ids = list(uidoc.Selection.GetElementIds())
if not sel_ids:
    forms.alert(
        "Nothing is selected.\n\n"
        "Select the ducts / conduit and electrical equipment you want to cut "
        "out of the Toposolid, then run this again.",
        title="Cut Toposolid", exitscript=True)

cut_ids   = []
skipped   = 0
cat_count = {}
for eid in sel_ids:
    el = doc.GetElement(eid)
    if el is None:
        skipped += 1
        continue
    # Skip any Toposolid that happens to be in the selection - it is a target,
    # not a cutter.
    if _is_toposolid(el):
        skipped += 1
        continue
    if _has_solid(el):
        cut_ids.append(eid)
        cn = _cat_name(el)
        cat_count[cn] = cat_count.get(cn, 0) + 1
    else:
        skipped += 1

if not cut_ids:
    forms.alert(
        "None of the selected elements have solid geometry to cut with.\n\n"
        "Select ducts / conduit / electrical equipment (model elements with "
        "3D geometry).",
        title="Cut Toposolid", exitscript=True)

log("Selected **{}** cuttable element(s); skipped {}.".format(
    len(cut_ids), skipped))
for cn in sorted(cat_count):
    log("  - {} x {}".format(cat_count[cn], cn))


# ---------------------------------------------------------------------------
# 2. Pick the Toposolid
# ---------------------------------------------------------------------------
# If exactly one Toposolid exists in the model, offer to use it directly;
# otherwise (or if the user prefers) pick interactively.
all_topo = [e for e in
            FilteredElementCollector(doc)
            .OfCategory(BuiltInCategory.OST_Toposolid)
            .WhereElementIsNotElementType()
            .ToElements()]

toposolid = None
if len(all_topo) == 1:
    only = all_topo[0]
    use_it = forms.alert(
        "Cut this Toposolid?\n\n  {}  (Id {})".format(
            safe_name(only), only.Id.IntegerValue),
        title="Cut Toposolid", yes=True, no=True)
    if use_it:
        toposolid = only

if toposolid is None:
    try:
        ref = uidoc.Selection.PickObject(
            ObjectType.Element, "Pick the Toposolid to cut")
        picked = doc.GetElement(ref.ElementId) if ref else None
    except Exception:
        picked = None
    if picked is None:
        log("No Toposolid picked - cancelled.")
        log.close()
        script.exit()
    if not _is_toposolid(picked):
        forms.alert(
            "That element is a '{}', not a Toposolid.\n\nRun again and pick a "
            "Toposolid.".format(_cat_name(picked)),
            title="Cut Toposolid", exitscript=True)
    toposolid = picked

log("Toposolid: **{}** (Id {}).".format(
    safe_name(toposolid), toposolid.Id.IntegerValue))


# ---------------------------------------------------------------------------
# 3. Cut
# ---------------------------------------------------------------------------
try:
    res = cut_toposolid_from_elements(
        doc, cut_ids, toposolid,
        top_clearance_mm=50.0,
        log=log)
except Exception as ex:
    forms.alert(str(ex), title="Cut Toposolid")
    log("Error: {}".format(ex))
    log.close()
    script.exit()

log("---")
log("### Done")
log("Cut **{}** of {} element(s) into the Toposolid.".format(
    res.cut_count, len(cut_ids)))

if res.no_geometry:
    log("- {} had no usable solid.".format(len(res.no_geometry)))
if res.silhouette_failed:
    log("- {} could not be projected to a bottom outline "
        "(or sit above the topo).".format(len(res.silhouette_failed)))
if res.family_failed:
    log("- {} failed during void-family creation.".format(len(res.family_failed)))
if res.cut_failed:
    log("- {} threw during the cut itself.".format(len(res.cut_failed)))

if res.cut_count:
    log("")
    log("Cutter instances are tagged with the comment "
        "`{}` so they can be found later. Deleting a cutter removes its "
        "cut.".format(CUTTER_MARK))

# Surface a short summary dialog too.
summary = "Cut {} of {} selected element(s) into the Toposolid.".format(
    res.cut_count, len(cut_ids))
fails = (len(res.no_geometry) + len(res.silhouette_failed) +
         len(res.family_failed) + len(res.cut_failed))
if fails:
    summary += "\n\n{} element(s) were skipped or failed - see the report "\
               "window for the breakdown.".format(fails)
forms.alert(summary, title="Cut Toposolid")

log.close()
# Only auto-close the report when everything cut cleanly - if anything
# failed, the summary dialog points the user at this window.
if not fails:
    output.self_destruct(8)
