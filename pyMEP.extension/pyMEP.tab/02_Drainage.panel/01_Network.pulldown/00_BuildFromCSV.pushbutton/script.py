# -*- coding: utf-8 -*-
"""Build Pipes from CSV - place pipes from a user-supplied CSV.

The user is asked for:
  * the CSV file
  * which columns hold start/end XYZ
  * which column holds the workset name (optional)
  * which column holds the pipe-type name (optional)
  * which column holds the piping-system-type name (optional)
  * which worksets to include (filter), if a workset column was mapped

Pipes are placed at the literal coordinates in the CSV (assumed to be
in the unit set via Settings, default ``m``); each pipe is then assigned
to the workset named by the workset column, with the pipe type and
piping system type taken from their respective columns (per row) or the
Settings defaults. Worksets that don't exist are created. Non-workshared
documents skip workset assignment cleanly.
"""

__title__  = "Build\nfrom CSV"
__author__ = "Glent Group"

import os
import sys

# ---------------------------------------------------------------------------
# Force-reload pymep_* lib modules so this script always picks up the
# latest code on disk. pyRevit re-reads script.py per click but holds onto
# imported lib modules in sys.modules across runs - that has bitten us
# before with offset/host-level changes appearing to "not run".
# ---------------------------------------------------------------------------
for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script

from pymep_config import (
    get_pipe_type_name, get_pipe_system_type_name, get_pipes_csv_unit,
    get_pipe_host_level_name, get_pipes_xyz_offset_m,
    get_pipes_default_workset, get_pipes_rotation_deg,
    get_pipes_post_shift_mm, get_pipes_use_project_location,
)
from pymep_pipes import (
    build_pipes_from_csv, auto_detect_columns,
)
from pymep_csv  import read_csv_dicts
from pymep_log  import Logger

output = script.get_output()
log    = Logger(output, "BuildPipesFromCSV")
doc    = revit.doc

log("### Build Pipes from CSV")

# Echo the effective settings to the output panel / log file (no modal).
_x_off, _y_off, _z_off = get_pipes_xyz_offset_m()
_dflt_ws = get_pipes_default_workset()
_rot     = get_pipes_rotation_deg()
_psx, _psy = get_pipes_post_shift_mm()
_auto    = get_pipes_use_project_location()
log("**Settings:**")
log("  - Placement mode:     **{}**".format(
    "AUTO  (project survey point)" if _auto else "MANUAL  (offsets + rotation)"))
log("  - Host level setting: {}".format(get_pipe_host_level_name()))
log("  - Default workset:    {}".format(_dflt_ws or "(none - active workset)"))
log("  - XYZ offsets (m):    X={:.3f}  Y={:.3f}  Z={:.3f}{}".format(
    _x_off, _y_off, _z_off, "  [ignored in auto]" if _auto else ""))
log("  - Rotation (deg):     {:.4f}{}".format(
    _rot, "  [ignored in auto]" if _auto else ""))
log("  - Post-shift (mm):    X={:+.1f}  Y={:+.1f}  (always applied)".format(
    _psx, _psy))
log("  - Z handling: internal_z = csv_z - z_offset (no host-level shift).")
log("  - Auto mode reads doc.ActiveProjectLocation and ignores manual "
    "XY offset / rotation / Z offset (post-shift still applies).")


# ---------------------------------------------------------------------------
# 1. Pick CSV
# ---------------------------------------------------------------------------
csv_path = forms.pick_file(file_ext="csv", title="Pick a pipes CSV")
if not csv_path:
    forms.alert("No CSV selected.", exitscript=True)

log("CSV: `{}`".format(os.path.basename(csv_path)))


# ---------------------------------------------------------------------------
# 2. Read header, propose mapping
# ---------------------------------------------------------------------------
try:
    rows = read_csv_dicts(csv_path)
except Exception as ex:
    forms.alert("Could not read CSV:\n\n{}".format(ex), exitscript=True)

if not rows:
    forms.alert("CSV has no data rows.", exitscript=True)

headers = list(rows[0].keys())
log("Columns: {}".format(", ".join("`{}`".format(h) for h in headers)))

auto = auto_detect_columns(headers)


# ---------------------------------------------------------------------------
# 3. Confirm or re-pick column mapping
# ---------------------------------------------------------------------------
# (role_key, label, required)
ROLE_LABELS = [
    ("sx", "Start X",     True),
    ("sy", "Start Y",     True),
    ("sz", "Start Z",     True),
    ("ex", "End X",       True),
    ("ey", "End Y",       True),
    ("ez", "End Z",       True),
    ("ws", "Workset",     False),
    ("pt", "Pipe type",   False),
    ("st", "System type", False),
]

col_map = {}
need_prompt = False

# One-click confirmation when at least the required (XYZ) roles auto-match.
# Any optional role (ws, pt, st) that didn't auto-match is silently skipped -
# the build code already handles missing optionals.
required_auto_match = all(role in auto for role, _, req in ROLE_LABELS if req)

if required_auto_match:
    summary_lines = ["Auto-detected column mapping:\n"]
    for role, label, req in ROLE_LABELS:
        val = auto.get(role)
        if val:
            summary_lines.append("  {:<12}  ->  {}".format(label, val))
        elif not req:
            summary_lines.append("  {:<12}  ->  (skipped, no match)".format(label))
    summary_lines.append("\nUse these mappings?")
    use_auto = forms.alert(
        "\n".join(summary_lines),
        title="Column mapping",
        options=["Use auto-detected", "Pick manually"])
    if use_auto == "Use auto-detected":
        col_map = {role: auto[role] for role, _, _ in ROLE_LABELS if role in auto}
    else:
        need_prompt = True
else:
    need_prompt = True

# Per-role prompts (manual or partial)
if need_prompt:
    for role, label, required in ROLE_LABELS:
        suggested = auto.get(role, "")
        info_msg = ("Pick the column for **{}**".format(label) +
                    ("" if required else "  (optional - press Cancel to skip)") +
                    ("  (suggested: `{}`)".format(suggested) if suggested else ""))
        log(info_msg)
        choice = forms.SelectFromList.show(
            headers,
            title="Build Pipes from CSV - {} column".format(label),
            button_name="Use as {}".format(label),
            multiselect=False,
        )
        if not choice:
            if required:
                forms.alert("No column picked for {} - aborting.".format(label),
                            exitscript=True)
            log("{} column skipped.".format(label))
            continue
        col_map[role] = choice

# Echo the final mapping
log("---")
log("**Column mapping:**")
for role, label, _ in ROLE_LABELS:
    log("  - {}: `{}`".format(label, col_map.get(role) or "(none)"))


# ---------------------------------------------------------------------------
# 4. Filter by workset (only if a workset column was mapped)
# ---------------------------------------------------------------------------
ws_filter = None
if col_map.get("ws"):
    ws_col = col_map["ws"]
    unique_ws = []
    seen = set()
    for r in rows:
        nm = (r.get(ws_col) or "").strip()
        if nm and nm not in seen:
            seen.add(nm)
            unique_ws.append(nm)

    if len(unique_ws) <= 1:
        log("Workset filter: only {} unique value(s), skipping prompt."
            .format(len(unique_ws)))
    else:
        log("Pick which worksets to build pipes for. "
            "Pick all of them to build everything.")
        chosen = forms.SelectFromList.show(
            sorted(unique_ws),
            title="Filter by workset",
            button_name="Build these",
            multiselect=True)
        if not chosen:
            forms.alert("No worksets picked - aborting.", exitscript=True)
        ws_filter = list(chosen)
        log("Workset filter: **{}** of {} worksets selected."
            .format(len(ws_filter), len(unique_ws)))


# ---------------------------------------------------------------------------
# 5. Build
# ---------------------------------------------------------------------------
csv_unit = get_pipes_csv_unit()
default_pipe_type_name   = get_pipe_type_name()
default_system_type_name = get_pipe_system_type_name()
host_level_name          = get_pipe_host_level_name()
x_off_m, y_off_m, z_off_m = get_pipes_xyz_offset_m()
default_workset_name     = get_pipes_default_workset()
rotation_deg             = get_pipes_rotation_deg()
post_x_mm, post_y_mm     = get_pipes_post_shift_mm()
use_project_location     = get_pipes_use_project_location()

# ---------------------------------------------------------------------------
# Proactive sanity check: if offsets are 0 and CSV coordinates are huge
# (typical of survey-grid CSVs), pipes will land far from the project origin.
# Show a hard-to-miss confirmation dialog with a one-click "Use centroid"
# option so the user can't accidentally place 2,000 pipes 24,500 km from
# the model just by mashing OK on auto-mappings.
# ---------------------------------------------------------------------------
if x_off_m == 0.0 and y_off_m == 0.0:
    try:
        sx_col, sy_col = col_map["sx"], col_map["sy"]
        ex_col, ey_col = col_map["ex"], col_map["ey"]
        sz_col, ez_col = col_map["sz"], col_map["ez"]
        all_x = [float(r[sx_col]) for r in rows] + [float(r[ex_col]) for r in rows]
        all_y = [float(r[sy_col]) for r in rows] + [float(r[ey_col]) for r in rows]
        all_z = [float(r[sz_col]) for r in rows] + [float(r[ez_col]) for r in rows]
        cx, cy, cz = (min(all_x) + max(all_x)) / 2.0, \
                     (min(all_y) + max(all_y)) / 2.0, \
                     (min(all_z) + max(all_z)) / 2.0

        # Threshold: only nag if the centroid is well outside what a typical
        # Revit project lives in. 1 km is generous - typical projects fit in
        # a few hundred metres.
        if abs(cx) > 1000.0 or abs(cy) > 1000.0:
            choice = forms.alert(
                "XYZ offsets are 0,0,0 but the CSV coordinates are large.\n\n"
                "CSV centroid (m):  X={:.1f}  Y={:.1f}  Z={:.1f}\n\n"
                "Placing now will put the pipes ~{:.0f} km from the project "
                "origin, far from the rest of the model.\n\n"
                "What do you want to do?".format(
                    cx, cy, cz, max(abs(cx), abs(cy)) / 1000.0),
                title="Offsets check",
                options=[
                    "Use CSV centroid as offset and continue",
                    "Continue anyway (place at huge coords)",
                    "Cancel - I'll set offsets in Settings",
                ])
            if choice == "Use CSV centroid as offset and continue":
                x_off_m, y_off_m = cx, cy
                # Leave Z offset alone - typical CSVs already have sensible
                # Z values, and forcing Z to centroid would shift them too.
                log("Override: using CSV centroid as XY offset for this run "
                    "(X={:.3f}  Y={:.3f}). Save it via Settings to make it "
                    "stick.".format(x_off_m, y_off_m))
            elif choice == "Cancel - I'll set offsets in Settings":
                forms.alert("Cancelled. Open pyMEP -> Settings -> "
                            "Set pipes XYZ offset (m).", exitscript=True)
            # else: continue with 0,0,0 as the user explicitly chose
    except Exception as ex:
        log("(could not compute centroid for offset check: {})".format(ex))

try:
    created, failed, skipped, ws_matched = build_pipes_from_csv(
        doc, csv_path, col_map, csv_unit,
        default_pipe_type_name=default_pipe_type_name,
        default_system_type_name=default_system_type_name,
        host_level_name=host_level_name,
        x_offset_m=x_off_m,
        y_offset_m=y_off_m,
        z_offset_m=z_off_m,
        rotation_deg=rotation_deg,
        post_x_shift_mm=post_x_mm,
        post_y_shift_mm=post_y_mm,
        use_project_location=use_project_location,
        default_workset_name=default_workset_name,
        ws_filter=ws_filter,
        log=log)
    log("### Done - created **{}**, failed **{}**, skipped **{}**, "
        "worksets matched **{}**".format(created, failed, skipped, ws_matched))
except Exception as ex:
    forms.alert(str(ex))
    log("Error: {}".format(ex))
finally:
    log.close()
