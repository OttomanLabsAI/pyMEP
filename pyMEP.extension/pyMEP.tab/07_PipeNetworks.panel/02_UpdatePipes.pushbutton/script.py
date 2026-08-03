# -*- coding: utf-8 -*-
"""Update Pipes - re-run the last Lines to Pipes build against the
lines as they are NOW.

The last build recorded its inputs (filters, sizes, invert, outfall
point, custom slopes) and every element it created. This deletes those
elements, re-reads the lines - moved, redrawn, added or removed - and
rebuilds the network with the same inputs. Custom lines keep their
remembered gradients; only NEW custom lines are asked for, in the same
clickable plan.
"""

__title__ = "Update\nPipes"
__author__ = "Glent Group"

import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

import os

from pyrevit import revit, forms, script

from pymep_config import get_export_folder
from pymep_log import Logger
from pymep_connect_fixtures import (
    list_pipe_type_options, list_system_type_options,
)
from pymep_lines_custom_ui import ask_custom_slopes
from pymep_lines_to_pipes import (
    build_network_pipes, collect_lines, load_lines_record,
    parse_style_slope, save_lines_record, solve,
)
from pymep_pipesizes import list_pipe_segments
from pymep_replace_structure import _quiet

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import Transaction

output = script.get_output()
log = Logger(output, "UpdatePipes")
doc = revit.doc

log("### Update Pipes")

_LIB_DIR = os.path.dirname(
    os.path.abspath(sys.modules["pymep_config"].__file__))
CUSTOM_XAML_PATH = os.path.join(_LIB_DIR, "pymep_lines_custom.xaml")

ANY_STYLE = "(any line style)"
SLOPE_STYLES = "(all slope-named styles - Pipe 1-n / Custom)"

base = os.path.join(get_export_folder(doc), "project_files")
rec = load_lines_record(base)
if not rec.get("element_uids") and not rec.get("pick_mm"):
    forms.alert("No Lines to Pipes build is recorded for this model - "
                "run Lines to Pipes first.", exitscript=True)

log("Last build: **{}** - {} element(s), dia **{:.0f} mm**, outfall "
    "invert **{:.3f} m**".format(
        rec.get("when", "?"), len(rec.get("element_uids", [])),
        float(rec.get("dia_mm", 0)), float(rec.get("invert_m", 0))))

# ---------------------------------------------------------------------------
# 1. The lines as they are NOW, under the recorded filters
# ---------------------------------------------------------------------------
rows = collect_lines(doc, None, rec.get("workset"))
style_sel = rec.get("style") or SLOPE_STYLES
if style_sel == SLOPE_STYLES:
    rows = [r for r in rows if parse_style_slope(r[3]) is not None]
elif style_sel != ANY_STYLE:
    rows = [r for r in rows if r[3] == style_sel]
if not rows and rec.get("workset"):
    # the lines may have moved workset since - fall back rather than
    # deleting the network and rebuilding nothing
    rows = collect_lines(doc, None, None)
    if style_sel == SLOPE_STYLES:
        rows = [r for r in rows if parse_style_slope(r[3]) is not None]
    elif style_sel != ANY_STYLE:
        rows = [r for r in rows if r[3] == style_sel]
    if rows:
        log("The recorded workset filter finds nothing any more - "
            "using the lines regardless of workset.")
if not rows:
    forms.alert("No model lines match the recorded filters - nothing "
                "deleted, nothing rebuilt.", exitscript=True)
log("**{}** line(s) match the recorded filters.".format(len(rows)))

# ---------------------------------------------------------------------------
# 2. Slopes: style names + remembered custom gradients; ask only for
#    custom lines the record does not know
# ---------------------------------------------------------------------------
default_n = float(rec.get("slope_default", 200.0) or 200.0)
known_custom = rec.get("custom_slopes", {}) or {}
lines_mm = [(a, b) for _el, a, b, _st in rows]
slopes = {}
new_custom = []
for i, (el, _a, _b, st) in enumerate(rows):
    got = parse_style_slope(st)
    if got == "custom":
        uid = el.UniqueId
        if uid in known_custom:
            slopes[i] = float(known_custom[uid])
        else:
            new_custom.append(i)
    elif got is not None:
        slopes[i] = got
    else:
        slopes[i] = default_n

if new_custom:
    log("**{}** new 'Slope Custom' line(s) need a gradient.".format(
        len(new_custom)))
    got = ask_custom_slopes(CUSTOM_XAML_PATH, lines_mm, new_custom)
    if got is None:
        log("Cancelled - nothing changed.")
        log.close()
        script.exit()
    for i, n in got.items():
        slopes[i] = n

# ---------------------------------------------------------------------------
# 3. Resolve the recorded names back to elements
# ---------------------------------------------------------------------------
def _by_name(opts, want, what):
    for nm, thing in opts:
        if nm == want:
            return thing
    if opts:
        log("! recorded {} '{}' is gone - using '{}'".format(
            what, want, opts[0][0]))
        return opts[0][1]
    return None


pt_id = _by_name(list_pipe_type_options(doc), rec.get("pipe_type"),
                 "pipe type")
st_id = _by_name(list_system_type_options(doc), rec.get("sys_type"),
                 "system type")
seg = None
if rec.get("segment"):
    seg = _by_name(list_pipe_segments(doc), rec.get("segment"),
                   "pipe segment")
if pt_id is None or st_id is None:
    forms.alert("The model no longer has a pipe type / system type to "
                "build with.", exitscript=True)

# ---------------------------------------------------------------------------
# 4. Delete the old network (only what the record created, and only
#    what still exists), then rebuild
# ---------------------------------------------------------------------------
t = Transaction(doc, "Update Pipes - remove old network")
_quiet(t)
t.Start()
gone = 0
try:
    for uid in rec.get("element_uids", []):
        try:
            el = doc.GetElement(uid)
        except Exception:
            el = None
        if el is not None:
            try:
                doc.Delete(el.Id)
                gone += 1
            except Exception:
                pass
    t.Commit()
except Exception:
    if t.HasStarted() and not t.HasEnded():
        t.RollBack()
    raise
log("Old network removed: **{}** element(s).".format(gone))

pick = rec.get("pick_mm") or [0.0, 0.0]
sol = solve(lines_mm, (float(pick[0]), float(pick[1])), slopes)
for m in sol["skipped"]:
    log("- {}".format(m))
log("**{}** run(s), **{}** tee(s), **{}** elbow(s) to build.".format(
    len(sol["runs"]), len(sol["tees"]), len(sol["elbows"])))
if not sol["runs"]:
    forms.alert("The old network was removed but nothing is buildable "
                "from the lines - see the report.", exitscript=True)

try:
    res = build_network_pipes(doc, sol, st_id, pt_id,
                              float(rec.get("dia_mm", 150.0)),
                              float(rec.get("invert_m", 0.0)), log=log,
                              segment_id=(seg.Id if seg is not None
                                          else None))
except Exception as ex:
    import traceback
    log(traceback.format_exc())
    forms.alert("The rebuild failed after the old network was removed - "
                "undo (Ctrl+Z twice) restores it.\n\n{}".format(ex),
                title="Update Pipes", exitscript=True)

# ---------------------------------------------------------------------------
# 5. Re-record
# ---------------------------------------------------------------------------
try:
    import datetime
    custom_by_uid = {}
    for i, (el, _a, _b, st) in enumerate(rows):
        if parse_style_slope(st) == "custom" and i in slopes:
            custom_by_uid[el.UniqueId] = slopes[i]
    uids = []
    for el in res.get("elements", []):
        try:
            uids.append(el.UniqueId)
        except Exception:
            pass
    rec.update({"custom_slopes": custom_by_uid, "element_uids": uids,
                "when": datetime.datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S")})
    save_lines_record(base, rec)
except Exception as ex:
    log("! build record not saved ({})".format(ex))

log("#### Summary")
log("- Old elements removed: **{}**".format(gone))
log("- Pipes created: **{}**".format(res["pipes"]))
log("- Fittings placed: **{}**".format(res["fittings"]))
if res["failed"]:
    log("- Failed: **{}**".format(res["failed"]))
for n in res["notes"]:
    log("- {}".format(n))

forms.alert("Updated: removed {} old element(s), rebuilt {} pipe(s) "
            "and {} fitting(s) from {} line(s).{}".format(
                gone, res["pipes"], res["fittings"], len(rows),
                "\n{} failed - see the report.".format(res["failed"])
                if res["failed"] else ""),
            title="Update Pipes")
log.close()
