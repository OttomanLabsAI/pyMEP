# -*- coding: utf-8 -*-
"""Fence Network - model a whole fence layout from its LINES: each
line's LINE STYLE picks its fence configuration, corners get the
highest-priority end post, and the in-between posts run at the
spacing with the leftover shortening the last bay.

Draw the layout as model lines using the styles bound in Fence
Configs (each config: line style, spacing, priority, families).
Pick the lines - click them or drag a selection box, ENTER (or
FINISH) locks the selection in, a pre-selection is used as-is -
pick the TERRAIN, confirm the mapping - then:

  - corners come FIRST, and EVERY intersection of the lines is a
    corner: shared endpoints, mid-line crossings, T-junctions. The
    incident config nearest the TOP of the config list wins the
    corner and its END post + foundation stand there;
  - with the winner's endpoint PRIORITY ticked, other lines
    TERMINATING at the corner place their OWN end post right next
    to it on their line - the two end foundation circles TOUCH
    (family 'Diameter' parameters);
  - then the in-between posts fill each stretch at the config's
    spacing, counted from the corner (or from its double post) -
    the leftover only SHORTENS the last bay, posts never double
    up along a run;
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
import pymep_pickui as PU

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
    """Pre-selected model lines, or a normal multi-select: click
    lines AND/OR drag a selection box (the filter lets only curves
    in) - press ENTER (or hit FINISH on the options bar) to lock
    the selection in; ESC cancels."""
    picked = [el for el in revit.get_selection().elements
              if isinstance(el, CurveElement)]
    if picked:
        return picked
    try:
        with PU.EnterFinishesPick(uidoc.Application):
            refs = uidoc.Selection.PickObjects(
                ObjectType.Element, LineFilter(),
                "Pick the fence LINES - click them or drag a "
                "selection box, then press ENTER (or hit FINISH)")
    except Exception:
        return []
    got, seen = [], set()
    for r in refs:
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
                    "line style, spacing and priority on the "
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
                u"'{}' x{} -> {} ({:g} mm spacing, prio {}, end "
                u"fnd: {})".format(
                    st or "?", styles[st], cfg_name, c["spacing_mm"],
                    c["priority"], F.end_families(c)[1] or "none"))
        else:
            lines_txt.append(u"'{}' x{} -> NO CONFIG - will be "
                             u"skipped".format(st or "?", styles[st]))
        log("- " + lines_txt[-1])
    # a skipped style next to a config bound to a style NO picked
    # line carries is almost always a RENAMED line style
    hint = None
    if any(st not in bound for st in styles):
        idle = [(n, cfgs[n]["line_style"]) for n in sorted(cfgs)
                if cfgs[n].get("line_style") and
                cfgs[n]["line_style"] not in styles]
        if idle:
            hint = (u"{} - if a line style was RENAMED in the "
                    u"model, open Fence Configs, Edit the "
                    u"configuration and re-pick its line "
                    u"style.").format(
                u"; ".join(u"config '{}' is bound to '{}', which "
                           u"NO picked line uses".format(n, s)
                           for n, s in idle))
            log("! " + hint)
    order = [n for n in F.priority_order(cfgs)
             if cfgs[n].get("line_style")]
    prio_txt = " > ".join(
        u"{}{}".format(n, " [END-PRIORITY]"
                       if cfgs[n].get("end_priority") else "")
        for n in order)
    log("Priority (top wins corners): **{}**".format(prio_txt))
    if not forms.alert(
            u"Model the fence network?\n\n{}{}\n\nPriority (first "
            u"wins corners): {}\n\nEvery intersection of the lines "
            u"is a corner - the winner's END post stands there; "
            u"END-PRIORITY configs make other terminating lines set "
            u"their own end post right next to it (touching "
            u"circles).".format(
                u"\n".join(lines_txt),
                u"\n\n! {}".format(hint) if hint else u"",
                prio_txt),
            yes=True, no=True):
        log("Cancelled - nothing modelled.")
        log.close()
        script.exit()

    view3d = FR.find_view3d(doc)
    if view3d is None:
        log.close()
        forms.alert("No 3D view found in the model - create one and "
                    "re-run.", exitscript=True)

    # re-modelling over lines an OLD network record covers REPLACES
    # it - its posts go, its record goes, no doubling
    base = os.path.join(get_export_folder(doc), "project_files")
    line_uids = set(el.UniqueId for el in lines)
    polys = [pl for pl in (FR.tessellate(el) for el in lines) if pl]
    near_tol = FR.mm2ft(FR.NODE_TOL_MM)

    def _covers(r0):
        """An old network record over the SAME fence: shared line
        uid, or (after the lines were SPLIT into new ids) surviving
        posts sitting ON the new lines."""
        if set(l.get("uid") for l in r0.get("lines") or []) & \
                line_uids:
            return True
        for inst_d in r0.get("instances") or []:
            try:
                el0 = doc.GetElement(inst_d.get("uid") or "")
                lp = el0.Location.Point
            except Exception:
                continue
            if el0 is None:
                continue
            for pl in polys:
                pr = F.project_to_poly(pl, lp.X, lp.Y)
                if pr is not None and pr[1] <= near_tol:
                    return True
        return False

    stale = []
    for r0 in F.load_fences(base)["fences"]:
        if r0.get("kind") == "network" and _covers(r0):
            stale.append(r0)

    t = Transaction(doc, "Fence Network")
    t.Start()
    try:
        for r0 in stale:
            gone = 0
            for inst_d in r0.get("instances") or []:
                for uid in (inst_d.get("uid"),
                            inst_d.get("foundation_uid")):
                    if not uid:
                        continue
                    el0 = doc.GetElement(uid)
                    if el0 is not None:
                        try:
                            doc.Delete(el0.Id)
                            gone += 1
                        except Exception:
                            pass
            log("Superseding **fence network {}** - {} old "
                "instance(s) removed.".format(r0.get("id"), gone))
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

    for r0 in stale:
        try:
            F.drop_fence(base, r0.get("id"))
        except Exception:
            pass

    net_id = None
    if records:
        try:
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
