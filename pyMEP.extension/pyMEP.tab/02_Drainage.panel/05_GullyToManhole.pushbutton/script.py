# -*- coding: utf-8 -*-
"""Gully -> MH. Modes are chosen by what you select:

  * Gully + Manhole       -> downpipe from the gully, a bend, and a run into
                             the manhole centre (entry at bottom+offset,
                             falling at 1:N).
  * Many Gullies + 1 MH   -> the same connection is made from EACH selected
                             gully into the single selected manhole. Offset and
                             slope are asked once and applied to all of them.
  * Gully only            -> a vertical downpipe of a length you choose
                             (default 300 mm) from the gully outlet.
  * Manhole only          -> a single pipe from the manhole centre to a point
                             you pick, at the level (bottom+offset) and slope
                             you choose.

Select the element(s) first, then click the button (the full mode also works if
you click first and pick the gully then the manhole). The gully is recognised by
its outlet pipe connector; name keywords are a fallback.
"""

__title__  = "Gully\nto MH"
__author__ = "Glent Group"

import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

from pymep_gully_connect import (
    connect_gully_to_manhole, draw_gully_downpipe, draw_manhole_run,
    identify_pair, has_pipe_connector, looks_like_gully, looks_like_manhole,
)
from pymep_revit import safe_name
from pymep_config import (
    get_pipe_type_name, get_pipe_system_type_name,
    load_settings, save_settings,
)
from pymep_log import Logger

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
from Autodesk.Revit.DB import FamilyInstance
from Autodesk.Revit.UI.Selection import ObjectType

output = script.get_output()
log = Logger(output, "GullyToManhole")
doc = revit.doc
uidoc = revit.uidoc


def _selected_instances():
    out = []
    try:
        for eid in uidoc.Selection.GetElementIds():
            el = doc.GetElement(eid)
            if isinstance(el, FamilyInstance):
                out.append(el)
    except Exception:
        pass
    return out


def _pick(msg):
    try:
        ref = uidoc.Selection.PickObject(ObjectType.Element, msg)
        return doc.GetElement(ref.ElementId)
    except Exception:
        return None


def _ask_num(key, prompt, default, blank=None):
    """Prompt for a number, remembering it between runs. Empty input -> `blank`
    (which defaults to `default`)."""
    if blank is None:
        blank = default
    s = load_settings()
    d = str(s.get(key, default))
    ans = forms.ask_for_string(default=d, prompt=prompt, title="Gully to MH")
    if ans is None:
        script.exit()
    if not ans.strip():
        v = float(blank)
    else:
        try:
            v = float(ans)
        except Exception:
            forms.alert("'{}' isn't a number - using {}.".format(ans, blank),
                        title="Gully to MH")
            v = float(blank)
    s[key] = v
    try:
        save_settings(s)
    except Exception:
        pass
    return v


# ---- work out the mode from the selection --------------------------------
sel = _selected_instances()
mode = gully = manhole = None
gullies = []   # used by the multi-gully mode

if len(sel) >= 2:
    # Split the selection into manholes vs gullies. A manhole is the structure
    # with no pipe outlet (name keywords as a fallback); everything else with a
    # pipe connector (or a gully-ish name) is treated as a gully.
    mh_candidates = [e for e in sel if looks_like_manhole(e)
                     and not has_pipe_connector(e)]
    if len(mh_candidates) == 1:
        manhole = mh_candidates[0]
        _mh_id = manhole.Id
        gullies = [e for e in sel
                   if e.Id != _mh_id
                   and (has_pipe_connector(e) or looks_like_gully(e))]
        if len(gullies) >= 2:
            mode = "multi"
        elif len(gullies) == 1:
            gully, mode = gullies[0], "both"

if mode is None and len(sel) == 2:
    gully, manhole = identify_pair(sel[0], sel[1])
    if gully is not None and manhole is not None:
        mode = "both"
elif mode is None and len(sel) == 1:
    e = sel[0]
    if has_pipe_connector(e) or looks_like_gully(e):
        gully, mode = e, "gully"
    elif looks_like_manhole(e):
        manhole, mode = e, "manhole"
    else:
        choice = forms.CommandSwitchWindow.show(
            ["Gully (downpipe only)", "Manhole (run only)"],
            message="What did you select?")
        if choice is None:
            script.exit()
        if choice.startswith("Gully"):
            gully, mode = e, "gully"
        else:
            manhole, mode = e, "manhole"

if mode is None:
    # nothing usable selected -> fall back to the full pick flow
    forms.alert("Pick the GULLY, then the MANHOLE.", title="Gully to MH")
    gully = _pick("Pick the GULLY")
    if gully is None:
        script.exit()
    manhole = _pick("Pick the MANHOLE")
    if manhole is None:
        script.exit()
    mode = "both"


log("### Gully -> MH")

if mode == "gully":
    log("Mode: **gully only** - {}".format(safe_name(gully)))
    length_mm = _ask_num("gully_downpipe_length_mm",
                         "Downpipe length (mm):", 300)
    try:
        draw_gully_downpipe(
            doc, gully, length_mm=length_mm, log=log,
            pipe_type_name=get_pipe_type_name(),
            system_type_name=get_pipe_system_type_name())
        forms.alert("Downpipe drawn - {:.0f} mm from the gully outlet.".format(
            length_mm), title="Gully to MH")
    except Exception as ex:
        log("**ERROR**: {}".format(ex))
        forms.alert("Couldn't draw the downpipe:\n\n{}".format(ex),
                    title="Gully to MH")

elif mode == "manhole":
    log("Mode: **manhole only** - {}".format(safe_name(manhole)))
    offset = _ask_num("gully_invert_offset_mm",
                      "Pipe entry offset ABOVE the manhole bottom (mm):", 0)
    slope = _ask_num("gully_slope_ratio",
                     "Slope 1:N  (enter N, e.g. 50; blank = level):", 0, blank=0)
    forms.alert("Now pick the point the pipe should run TO (its far end).",
                title="Gully to MH")
    try:
        end_pt = uidoc.Selection.PickPoint("Pick the pipe's far end")
    except Exception:
        forms.alert("Couldn't pick a point - try running this in a plan view.",
                    title="Gully to MH")
        script.exit()
    try:
        draw_manhole_run(
            doc, manhole, end_pt, invert_offset_mm=offset, slope_ratio=slope,
            log=log, pipe_type_name=get_pipe_type_name(),
            system_type_name=get_pipe_system_type_name())
        forms.alert("Run drawn from the manhole centre.\nSee the output for the "
                    "levels used.", title="Gully to MH")
    except Exception as ex:
        log("**ERROR**: {}".format(ex))
        forms.alert("Couldn't draw the run:\n\n{}".format(ex),
                    title="Gully to MH")

elif mode == "multi":
    log("Mode: **{} gullies -> one manhole** ({})".format(
        len(gullies), safe_name(manhole)))
    offset = _ask_num("gully_invert_offset_mm",
                      "Pipe entry offset ABOVE the manhole bottom (mm):", 0)
    slope = _ask_num("gully_slope_ratio",
                     "Slope 1:N  (enter N, e.g. 50; blank = level):", 0, blank=0)
    done = 0
    failed = 0
    for i, g in enumerate(gullies, start=1):
        gname = safe_name(g)
        log("--- Gully {}/{}: **{}** -> {}".format(
            i, len(gullies), gname, safe_name(manhole)))
        try:
            # Each gully gets its own connection (downpipe + bend + run into the
            # manhole centre), in its own transaction. One failure is logged and
            # the rest continue.
            connect_gully_to_manhole(
                doc, g, manhole, log=log,
                pipe_type_name=get_pipe_type_name(),
                system_type_name=get_pipe_system_type_name(),
                invert_offset_mm=offset, slope_ratio=slope)
            done += 1
        except Exception as ex:
            failed += 1
            log("**ERROR** on {}: {}".format(gname, ex))
    forms.alert(
        "Connected {} of {} gullies into {}.{}\n\n"
        "See the output window for the levels and sizes used.".format(
            done, len(gullies), safe_name(manhole),
            "" if failed == 0 else "\n{} failed - see the output.".format(failed)),
        title="Gully to MH")

else:  # both
    log("Gully: **{}**   |   Manhole: **{}**".format(
        safe_name(gully), safe_name(manhole)))
    offset = _ask_num("gully_invert_offset_mm",
                      "Pipe entry offset ABOVE the manhole bottom (mm):", 0)
    slope = _ask_num("gully_slope_ratio",
                     "Slope 1:N  (enter N, e.g. 50; blank = level):", 0, blank=0)
    try:
        connect_gully_to_manhole(
            doc, gully, manhole, log=log,
            pipe_type_name=get_pipe_type_name(),
            system_type_name=get_pipe_system_type_name(),
            invert_offset_mm=offset, slope_ratio=slope)
        forms.alert("Done.\n\nDownpipe + bend + run into the manhole centre.\n"
                    "See the output window for the levels and sizes used.",
                    title="Gully to MH")
    except Exception as ex:
        log("**ERROR**: {}".format(ex))
        forms.alert("Couldn't complete the connection:\n\n{}".format(ex),
                    title="Gully to MH")
