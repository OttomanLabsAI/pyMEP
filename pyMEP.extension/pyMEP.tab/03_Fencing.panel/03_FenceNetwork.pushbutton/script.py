# -*- coding: utf-8 -*-
"""Fence Network - model a whole fence layout from its LINES: each
line's LINE STYLE picks its fence configuration, corners get the
highest-priority end post, and the posts are packed so their circles
TOUCH at a single point.

Draw the layout as model lines using the styles bound in Fence
Configs (each config: line style, post + end post diameters,
priority). Pick the lines one by one (ESC finishes; a pre-selection
is used as-is), pick the TERRAIN, confirm the mapping - then:

  - shared line endpoints become corner NODES; the incident config
    with the SMALLEST priority number wins the corner and its END
    post + foundation stand there (the impact rated one at
    priority 1 beats the rest);
  - along every line the in-between posts are placed tangent - the
    first touches the corner post's circle (its end diameter), each
    next touches the previous (post diameter) - packed from the
    higher-priority end, the leftover gap reported at the other;
  - everything is ray-cast onto the terrain like Place New Fence.

The network is RECORDED, so Update Fence can rebuild it after the
lines, the terrain or the configurations change.
IronPython 2.7 / Revit 2022-2026.
"""

__title__  = "Fence\nNetwork"
__author__ = "Glent Group"

import datetime
import os
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

from pymep_config import load_settings, get_export_folder
from pymep_log import Logger
import pymep_fence as F
import pymep_fence_revit as FR

from Autodesk.Revit.DB import (BuiltInCategory, CurveElement,
                               Transaction)
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()
log = Logger(output, "FenceNetwork")

log("### Fence Network")


class LineFilter(ISelectionFilter):
    def AllowElement(self, elem):
        return isinstance(elem, CurveElement)

    def AllowReference(self, ref, point):
        return False


_TERRAIN_CATS = set()
for _n in ("OST_Toposolid", "OST_Topography", "OST_Floors",
           "OST_Roofs"):
    if hasattr(BuiltInCategory, _n):
        _TERRAIN_CATS.add(int(getattr(BuiltInCategory, _n)))


class TerrainFilter(ISelectionFilter):
    def AllowElement(self, elem):
        try:
            return FR.id_value(elem.Category.Id) in _TERRAIN_CATS
        except Exception:
            return False

    def AllowReference(self, ref, point):
        return False


def get_lines():
    """Pre-selected model lines, or pick ONE BY ONE - a single ESC
    (or right-click) continues with what was picked."""
    picked = [el for el in revit.get_selection().elements
              if isinstance(el, CurveElement)]
    if picked:
        return picked
    got, seen = [], set()
    while True:
        try:
            r = uidoc.Selection.PickObject(
                ObjectType.Element, LineFilter(),
                "Pick the fence LINES one by one ({} picked) - "
                "press ESC to continue".format(len(got)))
        except Exception:
            break
        el = doc.GetElement(r.ElementId)
        if isinstance(el, CurveElement) and \
                FR.id_value(el.Id) not in seen:
            seen.add(FR.id_value(el.Id))
            got.append(el)
    return got


def main():
    settings = load_settings()
    cfgs = F.get_configs(settings)
    bound = dict((c["line_style"], n) for n, c in cfgs.items()
                 if c.get("line_style"))
    if not bound:
        log("No configuration is bound to a line style.")
        log.close()
        forms.alert("No fence configuration is bound to a LINE "
                    "STYLE yet - open Fence Configs and set the "
                    "line style, diameters and priority on the "
                    "configurations first.", exitscript=True)

    lines = get_lines()
    if not lines:
        log("No lines picked - nothing modelled.")
        log.close()
        script.exit()
    log("**{}** line(s) picked.".format(len(lines)))
    try:
        terrain = doc.GetElement(uidoc.Selection.PickObject(
            ObjectType.Element, TerrainFilter(),
            "Pick the TERRAIN (toposolid / topography / floor / "
            "roof)").ElementId)
    except Exception:
        log("No terrain picked - nothing modelled.")
        log.close()
        script.exit()

    # mapping preview - what will happen, before anything is placed
    styles = {}
    for el in lines:
        st = FR.line_style_name(el)
        styles[st] = styles.get(st, 0) + 1
    lines_txt = []
    for st in sorted(styles):
        cfg_name = bound.get(st)
        if cfg_name:
            c = cfgs[cfg_name]
            lines_txt.append(
                u"'{}' x{} -> {} (⌀{:g}/{:g} mm, prio {})".format(
                    st or "?", styles[st], cfg_name, c["dia_mm"],
                    c["end_dia_mm"] or c["dia_mm"], c["priority"]))
        else:
            lines_txt.append(u"'{}' x{} -> NO CONFIG - will be "
                             u"skipped".format(st or "?", styles[st]))
        log("- " + lines_txt[-1])
    if not forms.alert(
            "Model the fence network?\n\n{}\n\nCorners get the "
            "highest-priority (smallest number) incident config's "
            "END post; posts are packed so their circles touch at a "
            "single point.".format("\n".join(lines_txt)),
            yes=True, no=True):
        log("Cancelled - nothing modelled.")
        log.close()
        script.exit()

    view3d = FR.find_view3d(doc)
    if view3d is None:
        log.close()
        forms.alert("No 3D view found in the model - create one and "
                    "re-run.", exitscript=True)

    t = Transaction(doc, "Fence Network")
    t.Start()
    try:
        records, notes, placed, missed = FR.model_network(
            doc, lines, terrain, cfgs, view3d, say=log)
        t.Commit()
    except ValueError as ex:
        try:
            t.RollBack()
        except Exception:
            pass
        log("! {}".format(ex))
        log.close()
        forms.alert("Fence Network stopped:\n\n{}".format(ex),
                    exitscript=True)
    except Exception:
        try:
            t.RollBack()
        except Exception:
            pass
        raise

    if missed:
        log("! **{}** point(s) had NO terrain hit.".format(missed))
    log("**{}** instance(s) placed.".format(placed))

    net_id = None
    if records:
        try:
            base = os.path.join(get_export_folder(doc),
                                "project_files")
            rec = {
                "kind": "network",
                "terrain_uid": terrain.UniqueId,
                "lines": [{"uid": el.UniqueId,
                           "style": FR.line_style_name(el)}
                          for el in lines],
                "instances": records,
                "updated": datetime.datetime.now().strftime(
                    "%Y-%m-%dT%H:%M:%S"),
            }
            net_id = F.add_fence(base, rec)
            log("Recorded as **fence network {}** - change the "
                "lines, terrain or configs, then run **Update "
                "Fence**.".format(net_id))
        except Exception as ex:
            log("! could not record the network for updates: "
                "{}".format(ex))

    log.close()
    if placed == 0:
        forms.alert("Nothing placed - see the pyMEP report (line "
                    "styles unmapped, families missing, or no "
                    "terrain hits).")
    else:
        forms.alert("Fence network done: {} instance(s) placed{}"
                    "{}.".format(
                        placed,
                        ", {} point(s) missed the terrain".format(
                            missed) if missed else "",
                        " - recorded as network {} for Update "
                        "Fence".format(net_id) if net_id else ""))


main()
