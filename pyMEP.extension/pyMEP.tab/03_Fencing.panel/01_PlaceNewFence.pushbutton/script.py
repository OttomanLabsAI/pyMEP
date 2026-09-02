# -*- coding: utf-8 -*-
"""Place New Fence - pick a line and a terrain, place the fence
configuration's post + foundation along the line.

One dialog first: the fence CONFIGURATION (post family, foundation
family, spacing, endpoints, custom rotation - created and edited
with the Fence Configs button) and the justification (Start / Centre
/ End - where the spacing counts from; Centre splits the leftover
evenly). Then pick the LINE and the TERRAIN in the view. Every
station is ray-cast straight down onto the picked terrain; the post
(and the foundation under it) land ON the ground, rotated so the
family's X axis follows the line's direction plus the config's
rotation.

Every fence is RECORDED (line, terrain, config, each post) in the
project's file store, so Update Fence re-drapes it after the line,
the terrain or the configuration changes. Straight, curved and
closed model lines all work. Revit 2022-2026.
"""

__title__  = "Place New\nFence"
__author__ = "Glent Group"

import datetime
import math
import os
import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

from pymep_config import load_settings, save_settings, get_export_folder
from pymep_log import Logger
import pymep_fence as F
import pymep_fence_revit as FR

from Autodesk.Revit.DB import (
    BuiltInCategory,
    CurveElement,
    Transaction,
    UnitTypeId,
    UnitUtils,
)
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()
log = Logger(output, "PlaceNewFence")

XAML_PATH = os.path.join(
    os.path.dirname(os.path.abspath(sys.modules["pymep_config"].__file__)),
    "pymep_fence.xaml")

log("### Place New Fence")


# -------------------------------------------------------------------- dialog
class FenceWindow(forms.WPFWindow):
    """Configuration + justification; the config carries the post
    and foundation families, spacing, endpoints and rotation."""

    def __init__(self, settings, info_text):
        forms.WPFWindow.__init__(self, XAML_PATH)
        self.result = None
        self.settings = settings
        self.TxtInfo.Text = info_text
        just = settings.get(F.SETTINGS_JUSTIFY, F.JUSTIFY_START)
        if just == F.JUSTIFY_CENTRE:
            self.RadJustCentre.IsChecked = True
        elif just == F.JUSTIFY_END:
            self.RadJustEnd.IsChecked = True
        else:
            self.RadJustStart.IsChecked = True
        self._fill_configs(settings.get(F.SETTINGS_LAST))

    def _fill_configs(self, want):
        cfgs = F.get_configs(self.settings)
        self.CmbConfig.Items.Clear()
        names = sorted(cfgs.keys(), key=lambda s: s.lower())
        for n in names:
            self.CmbConfig.Items.Add(n)
        pick = want if want in cfgs else names[0]
        self.CmbConfig.SelectedIndex = names.index(pick)

    def on_config_pick(self, sender, args):
        try:
            name = str(self.CmbConfig.SelectedItem or "")
            cfg = F.get_configs(self.settings).get(name)
            if cfg is None:
                return
            txt = (u"post: {}  |  foundation: {}\n"
                   u"{:g} mm spacing, endpoints {}, rotation "
                   u"{:+g}°".format(
                       cfg["post"] or "none",
                       cfg["foundation"] or "none",
                       cfg["spacing_mm"],
                       "ON" if cfg["endpoints"] else "off",
                       cfg["rotation_deg"]))
            if cfg["panel"]:
                txt += u"\npanel: {}".format(cfg["panel"])
            if not cfg["same_end_posts"]:
                txt += u"\nENDPOINTS get post: {}".format(
                    cfg["end_post"] or "none")
            if not cfg["same_end_foundations"]:
                txt += u"\nENDPOINTS get foundation: {}".format(
                    cfg["end_foundation"] or "none")
            self.TxtCfgSummary.Text = txt
            self.StatusText.Text = ""
        except Exception:
            pass

    def justify(self):
        if self.RadJustCentre.IsChecked:
            return F.JUSTIFY_CENTRE
        if self.RadJustEnd.IsChecked:
            return F.JUSTIFY_END
        return F.JUSTIFY_START

    def on_go(self, sender, args):
        cfg = F.get_configs(self.settings).get(
            str(self.CmbConfig.SelectedItem or ""))
        if cfg is None:
            self.StatusText.Text = "Pick a configuration."
            return
        if not F.places_something(cfg):
            self.StatusText.Text = (
                "This configuration places NOTHING - give it a post "
                "or a foundation with the Fence Configs button.")
            return
        self.result = {
            "spacing_mm": cfg["spacing_mm"],
            "endpoints": cfg["endpoints"],
            "rotation_deg": cfg["rotation_deg"],
            "post": cfg["post"],
            "foundation": cfg["foundation"],
            "same_ends": cfg["same_ends"],
            "same_end_posts": cfg["same_end_posts"],
            "same_end_foundations": cfg["same_end_foundations"],
            "end_post": cfg["end_post"],
            "end_foundation": cfg["end_foundation"],
            "panel": cfg["panel"],
            "panel_width_param": cfg["panel_width_param"],
            "easting_param": cfg["easting_param"],
            "northing_param": cfg["northing_param"],
            "terrain_mode": cfg["terrain_mode"],
            "terrains": cfg["terrains"],
            "align_to": cfg.get("align_to") or F.ALIGN_TOPO,
            "floor_type": cfg.get("floor_type") or "",
            "link_terrain": bool(cfg.get("link_terrain")),
            "justify": self.justify(),
            "dims": dict((k, cfg[k]) for k in F.DIM_KEYS),
            "config": str(self.CmbConfig.SelectedItem or ""),
        }
        self.Close()

    def on_cancel(self, sender, args):
        self.result = None
        self.Close()


# ----------------------------------------------------------------- selection
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


def pick_one(sel_filter, prompt):
    try:
        r = uidoc.Selection.PickObject(ObjectType.Element, sel_filter,
                                       prompt)
        return doc.GetElement(r.ElementId)
    except Exception:            # ESC / right-click
        return None


# ----------------------------------------------------------------------- run
def main():
    settings = load_settings()

    win = FenceWindow(settings,
                      "{} configuration(s) saved".format(
                          len(F.get_configs(settings))))
    win.ShowDialog()
    if win.result is None:
        log("Cancelled - nothing placed.")
        log.close()
        script.exit()
    opt = win.result

    settings[F.SETTINGS_JUSTIFY] = opt["justify"]
    settings[F.SETTINGS_LAST] = opt["config"]
    mark_on, mark_prefix = F.mark_settings(settings)
    toc_on, toc_param, toc_eq = F.toc_settings(settings)
    toc = (toc_param, toc_eq) if toc_on else None
    toc_probs = []

    try:
        save_settings(settings)
    except Exception:
        pass

    log("Config **{}**: post **{}**, foundation **{}**, spacing "
        "**{:g} mm**, justification **{}**, endpoints **{}**, "
        "rotation **{:+g} deg**{}.".format(
            opt["config"], opt["post"] or "none",
            opt["foundation"] or "none", opt["spacing_mm"],
            opt["justify"], "on" if opt["endpoints"] else "off",
            opt["rotation_deg"],
            "" if (opt["same_end_posts"] and
                   opt["same_end_foundations"]) else
            "; ENDPOINTS: post {} / foundation {}".format(
                opt["end_post"] or "none",
                opt["end_foundation"] or "none")))

    def _resolve(label, cats, what):
        """The config's family in THIS model - a named-but-missing
        family aborts loudly rather than placing a wrong fence."""
        if not label:
            return None
        sym = FR.symbol_by_label(doc, label, cats)
        if sym is None:
            log("! {} family **{}** is NOT in this model.".format(
                what, label))
            log.close()
            forms.alert("The {} family '{}' is not in this model - "
                        "load it or edit the configuration.".format(
                            what, label), exitscript=True)
        return sym

    post_symbol = _resolve(opt["post"], F.POST_CATEGORIES, "post")
    foundation_symbol = _resolve(opt["foundation"],
                                 F.FOUNDATION_CATEGORIES,
                                 "foundation")
    panel_symbol = None
    if opt["panel"]:
        panel_symbol = FR.symbol_by_label(doc, opt["panel"],
                                          F.PANEL_CATEGORIES)
        if panel_symbol is None:
            log("! panel family **{}** is NOT in this model - no "
                "panels.".format(opt["panel"]))
    if opt["same_end_posts"] and opt["same_end_foundations"]:
        end_post_symbol = post_symbol
        end_found_symbol = foundation_symbol
    else:
        end_post_symbol = post_symbol if opt["same_end_posts"] \
            else _resolve(opt["end_post"],
                                   F.POST_CATEGORIES, "end post")
        end_found_symbol = foundation_symbol \
            if opt["same_end_foundations"] \
            else _resolve(opt["end_foundation"],
                                    F.FOUNDATION_CATEGORIES,
                                    "end foundation")
    # the PRIMARY carries the record; a foundation-only pair
    # promotes the foundation to primary
    primary, secondary = post_symbol, foundation_symbol
    if primary is None:
        primary, secondary = foundation_symbol, None
    end_primary, end_secondary = end_post_symbol, end_found_symbol
    if end_primary is None:
        end_primary, end_secondary = end_found_symbol, None

    line_el = pick_one(LineFilter(), "Pick the LINE to fence along")
    if line_el is None:
        log("No line picked - nothing placed.")
        log.close()
        script.exit()

    poly = FR.tessellate(line_el)
    if not poly:
        log.close()
        forms.alert("That line has no geometry curve.",
                    exitscript=True)
    length = F.poly_length(poly)
    if length <= 1e-9:
        log.close()
        forms.alert("That line has no length.", exitscript=True)
    closed = F.is_closed(poly)
    log("Line **{}**: **{:.3f} m** along its length{}.".format(
        line_el.Id, length * 0.3048, ", CLOSED loop" if closed else ""))

    # terrain: the CONFIG decides (named topos / AUTO under the
    # line) - an empty answer falls back to picking it
    terrains, t_note = FR.resolve_terrains(doc, opt, [poly])
    if t_note:
        log("- " + t_note)
    if not terrains:
        terrain = FR.pick_terrain(uidoc, doc,
                                  links=bool(opt.get("link_terrain")))
        if terrain is None:
            log("No terrain picked - nothing placed.")
            log.close()
            script.exit()
        terrains = [terrain]

    spacing_ft = UnitUtils.ConvertToInternalUnits(
        opt["spacing_mm"], UnitTypeId.Millimeters)
    dists = F.stations(length, spacing_ft, opt["justify"],
                       opt["endpoints"], closed)
    if not dists:
        log.close()
        forms.alert("No stations to place - spacing {} mm on a "
                    "{:.3f} m line with endpoints off leaves "
                    "nothing.".format(opt["spacing_mm"],
                                      length * 0.3048),
                    exitscript=True)
    if len(dists) > F.MAX_INSTANCES:
        log.close()
        forms.alert("{} instances would be placed - more than the "
                    "{} sanity cap. Check the spacing ({} mm on a "
                    "{:.1f} m line).".format(
                        len(dists), F.MAX_INSTANCES,
                        opt["spacing_mm"], length * 0.3048),
                    exitscript=True)
    log("**{}** station(s) along the line.".format(len(dists)))

    view3d = FR.find_view3d(doc)
    if view3d is None:
        log.close()
        forms.alert("No 3D view found in the model - create one and "
                    "re-run.", exitscript=True)

    ri = FR.make_intersector(view3d,
                             links=FR.any_linked(terrains))
    ray_z = FR.ray_start_z(terrains + [line_el])
    terrain_id = FR.terrain_keys(terrains)
    log("Ray-casting in 3D view **{}** onto **{}**.".format(
        view3d.Name, ", ".join(
            FR.terrain_display(t) for t in terrains)))
    levels = FR.sorted_levels(doc)

    _cfg_for_dims = dict(opt.get("dims") or {})
    _cfg_for_dims["same_ends"] = opt["same_ends"]
    _cfg_for_dims["same_end_posts"] = opt["same_end_posts"]
    _cfg_for_dims["same_end_foundations"] = \
        opt["same_end_foundations"]
    pick = FR.station_pick(dists, length, primary, secondary,
                           end_primary, end_secondary,
                           opt["same_end_posts"],
                           opt["same_end_foundations"])
    t = Transaction(doc, "Place New Fence")
    t.Start()
    try:
        records, missed, failed, why = FR.place_instances(
            doc, pick, poly, dists, terrain_id, ri, ray_z,
            levels, extra_rot=math.radians(opt["rotation_deg"]),
            panel_symbol=panel_symbol,
            panel_width_param=opt["panel_width_param"] or None,
            coord_params=(opt["easting_param"],
                          opt["northing_param"]),
            marks=[mark_prefix + str(i + 1)
                   for i in range(len(dists))]
            if mark_on else None,
            toc=toc, toc_problems=toc_probs, cfg=_cfg_for_dims)
        t.Commit()
    except Exception:
        try:
            t.RollBack()
        except Exception:
            pass
        raise

    if toc_probs:
        log("! TOC: {}".format(toc_probs[0]))
    if missed:
        log("! **{}** station(s) had NO terrain hit (the line runs "
            "off the terrain?): {}".format(
                len(missed),
                ", ".join("{:.1f} m".format(d * 0.3048)
                          for d in missed[:12]) +
                (" ..." if len(missed) > 12 else "")))
    if failed:
        log("! **{}** placement(s) failed ({})".format(
            failed, why or "family not placeable by point + level?"))
    log("**{}** instance(s) placed.".format(len(records)))

    fence_id = None
    if records:
        try:
            base = os.path.join(get_export_folder(doc),
                                "project_files")
            rec = {
                "line_uid": line_el.UniqueId,
                "terrain_uid": FR.terrain_uid(terrains[0]),
                "terrain_uids": [FR.terrain_uid(t)
                                 for t in terrains],
                "family": opt["post"] if post_symbol is not None
                else "",
                "spacing_mm": opt["spacing_mm"],
                "endpoints": opt["endpoints"],
                "rotation_deg": opt["rotation_deg"],
                "post": opt["post"] if post_symbol is not None
                else "",
                "foundation": opt["foundation"]
                if foundation_symbol is not None else "",
                "same_ends": opt["same_ends"],
                "same_end_posts": opt["same_end_posts"],
                "same_end_foundations":
                    opt["same_end_foundations"],
                "end_post": opt["end_post"],
                "end_foundation": opt["end_foundation"],
                "panel": opt["panel"]
                if panel_symbol is not None else "",
                "panel_width_param": opt["panel_width_param"],
                "easting_param": opt["easting_param"],
                "northing_param": opt["northing_param"],

                "mark": mark_on,
                "mark_prefix": mark_prefix,
                "justify": opt["justify"],
                "config": opt["config"],
                "instances": records,
                "updated": datetime.datetime.now().strftime(
                    "%Y-%m-%dT%H:%M:%S"),
            }
            for _k in F.DIM_KEYS:
                rec[_k] = (opt.get("dims") or {}).get(_k, "")
            fence_id = F.add_fence(base, rec)
            log("Recorded as **fence {}** - move the line, reshape "
                "the terrain or edit the config, then run **Update "
                "Fence**.".format(fence_id))
        except Exception as ex:
            log("! could not record the fence for updates: "
                "{}".format(ex))

    log.close()
    if not records:
        forms.alert("Nothing placed - no terrain hits under the "
                    "line. Check the terrain is visible in the 3D "
                    "view used for ray-casting ('{}') and the line "
                    "runs above it.".format(view3d.Name))
    else:
        forms.alert("Fence done: {} instance(s) placed{}{}{}.".format(
            len(records),
            ", {} station(s) missed the terrain".format(len(missed))
            if missed else "",
            ", {} failed".format(failed) if failed else "",
            " - recorded as fence {} for Update Fence".format(
                fence_id) if fence_id else ""))


main()
