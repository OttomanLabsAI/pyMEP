# -*- coding: utf-8 -*-
"""Place Structures - place manholes AND drop pipes from one AutoCAD
S2CSV export, applying the SAME survey -> project transform as the
pipes builder.

CSV schema (S2CSV.lsp / S2CSV4.lsp output, drawing units typically m):
    id, layer, top_x, top_y, top_z,
    dia_1..5, z_off_2..5 [, workset]

Both kinds share the whole pipeline (file pick, transform, workset
mapping - all in pymep_manholes.place_manholes_from_csv); they differ
only in WHICH family/type is placed and WHICH parameters are set:

  Manholes:
    height parameter (default 'Height')
        <- deepest non-empty z_off_N + manhole_slab_thickness_mm

  Drop pipes (via param_specs):
    DIA parameter    (default 'DIA')     <- CSV `dia_4`
    Height parameter (default 'Height')  <- CSV `z_off_4`

A kind is only placed when its family + type are configured in
Settings AND actually loaded in the document; kinds that aren't
available are skipped with a note in the combined summary.

Settings used (all on the categorised Settings dialog):

  Pipes - Basics:
    pipes_csv_unit                - 'm', 'mm', or 'ft'
    pipe_host_level               - reused as host level

  Pipes - Coordinates:
    pipes_use_project_location    - AUTO toggle
    pipes_x_offset_m, ..._y_, ..._z_ - manual XY/Z offsets
    pipes_rotation_deg            - manual True-North rotation
    pipes_post_x_shift_mm, ..._y_ - post-rotation fine alignment
    pipes_default_workset         - fallback when no row workset / layer match

  Manholes:
    manhole_family_name, manhole_type_name
    manhole_slab_thickness_mm     - added to deepest z_off_N for the height
    manhole_height_param          - target parameter name (default 'Height')

  Drop Pipes:
    drop_pipe_family_name, drop_pipe_type_name
    drop_pipe_dia_param           - target for CSV dia_4 (default 'DIA')
    drop_pipe_height_param        - target for CSV z_off_4 (default 'Height')
"""

__title__  = "Place Structures\n(CSV)"
__author__ = "Glent Group"

import sys

# Force-reload pymep_* lib modules so the script picks up latest code
for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pyrevit import revit, forms, script, DB

from pymep_config import (
    get_manhole_family_name, get_manhole_type_name,
    get_manhole_slab_thickness_mm, get_manhole_height_param,
    get_drop_pipe_family_name, get_drop_pipe_type_name,
    get_drop_pipe_dia_param, get_drop_pipe_height_param,
    get_pipe_host_level_name, get_pipes_csv_unit,
    get_pipes_xyz_offset_m, get_pipes_default_workset,
    get_pipes_rotation_deg, get_pipes_post_shift_mm,
    get_pipes_use_project_location,
)
from pymep_manholes import (
    place_manholes_from_csv, _find_family_symbol,
    _list_family_types, _list_all_families,
)
from pymep_log      import Logger

output = script.get_output()
log    = Logger(output, "PlaceStructures")
doc    = revit.doc

log("### Place Structures  (manholes + drop pipes from one S2CSV)")

# ---------------------------------------------------------------------------
# 1. Settings - shared transform (identical to the pipes builder) plus the
#    per-kind family/type/parameter configs.
# ---------------------------------------------------------------------------
host_level_name      = get_pipe_host_level_name()
csv_unit             = get_pipes_csv_unit()
x_off, y_off, z_off  = get_pipes_xyz_offset_m()
rotation_deg         = get_pipes_rotation_deg()
post_x_mm, post_y_mm = get_pipes_post_shift_mm()
auto_mode            = get_pipes_use_project_location()
default_workset      = get_pipes_default_workset()

mh_family       = get_manhole_family_name()
mh_type         = get_manhole_type_name()
mh_slab_mm      = get_manhole_slab_thickness_mm()
mh_height_param = get_manhole_height_param()

dp_family       = get_drop_pipe_family_name()
dp_type         = get_drop_pipe_type_name()
dp_dia_param    = get_drop_pipe_dia_param()
dp_height_param = get_drop_pipe_height_param()

# Echo the effective settings to the output panel / log file (no modal).
log("**Settings:**")
log("  - Host level:          {}".format(host_level_name))
log("  - CSV unit:            {}".format(csv_unit))
log("  - Placement mode:      **{}**".format(
    "AUTO  (project survey point)" if auto_mode
    else "MANUAL  (offsets + rotation)"))
log("  - XYZ offsets (m):     X={:.3f}  Y={:.3f}  Z={:.3f}{}".format(
    x_off, y_off, z_off, "  [ignored in auto]" if auto_mode else ""))
log("  - Rotation (deg):      {:.4f}{}".format(
    rotation_deg, "  [ignored in auto]" if auto_mode else ""))
log("  - Post-shift (mm):     X={:+.1f}  Y={:+.1f}  (always applied)".format(
    post_x_mm, post_y_mm))
log("  - Default workset:     {}".format(
    default_workset or "(none - active workset)"))
log("  - Manhole family:      {} : {}".format(
    mh_family or "(none)", mh_type or "(none)"))
log("  - Manhole height:      `{}` = deepest z_off_N + {:.1f} mm slab".format(
    mh_height_param, mh_slab_mm))
log("  - Drop pipe family:    {} : {}".format(
    dp_family or "(none)", dp_type or "(none)"))
log("  - Drop pipe params:    `{}` <- dia_4,  `{}` <- z_off_4".format(
    dp_dia_param, dp_height_param))

# ---------------------------------------------------------------------------
# 2. Which kinds can we actually place? A kind is available when its
#    family/type names are configured AND that FamilySymbol is loaded
#    in the document.
# ---------------------------------------------------------------------------
def _log_family_diagnostics(family_name, type_name):
    """Log what IS available when a family/type lookup fails - the same
    diagnostic the lib's ValueError used to surface (available types for
    the family, or near-match family names)."""
    try:
        types = _list_family_types(doc, family_name)
    except Exception:
        types = []
    if types:
        log("  Family '{}' is loaded, but has no type '{}'. "
            "Available types:".format(family_name, type_name))
        for t in types:
            log("    - {}".format(t))
        return
    try:
        all_fams = _list_all_families(doc)
    except Exception:
        all_fams = []
    target = (family_name or "").strip().lower()
    near = [f for f, _ts in all_fams if target and target in f.lower()]
    if near:
        log("  No family named '{}' is loaded. Families whose name "
            "contains it:".format(family_name))
        for f in near[:10]:
            log("    - {}".format(f))
    else:
        log("  No family named '{}' is loaded ({} families in the "
            "document).".format(family_name, len(all_fams)))


def _kind_availability(family_name, type_name, settings_hint):
    """Return (available, reason). reason is '' when available."""
    if not family_name or not type_name:
        return False, "not configured (set family/type in {})".format(
            settings_hint)
    try:
        sym = _find_family_symbol(doc, family_name, type_name)
    except Exception as ex:
        return False, "family lookup failed ({}: {})".format(
            type(ex).__name__, ex)
    if sym is None:
        _log_family_diagnostics(family_name, type_name)
        return False, "family '{}' : '{}' not loaded in the document".format(
            family_name, type_name)
    return True, ""

mh_ok, mh_reason = _kind_availability(mh_family, mh_type,
                                      "Settings > Manholes")
dp_ok, dp_reason = _kind_availability(dp_family, dp_type,
                                      "Settings > Drop Pipes")

log("Manholes:   {}".format("available" if mh_ok else "SKIPPED - " + mh_reason))
log("Drop pipes: {}".format("available" if dp_ok else "SKIPPED - " + dp_reason))

if not mh_ok and not dp_ok:
    log.close()
    forms.alert(
        "Neither structure kind can be placed:\n\n"
        "Manholes:   {}\n"
        "Drop pipes: {}\n\n"
        "Configure the families in Settings and make sure they are "
        "loaded in the document, then re-run.".format(mh_reason, dp_reason),
        exitscript=True)

# ---------------------------------------------------------------------------
# 3. ONE file pick - both kinds read the same S2CSV export.
# ---------------------------------------------------------------------------
csv_path = forms.pick_file(file_ext="csv",
                           title="Pick the S2CSV export from AutoCAD")
if not csv_path:
    log.close()
    forms.alert("No CSV selected.", exitscript=True)

log("CSV: **{}**".format(csv_path))

# ---------------------------------------------------------------------------
# 4. Place each available kind. The lib call is identical apart from the
#    family/type and the parameter mapping.
# ---------------------------------------------------------------------------
results = {}  # kind -> (created, failed, skipped, ws_matched) or None on error

def _place_kind(kind, family_name, type_name, extra_kwargs):
    log("---")
    log("### Placing {} - **{}** : **{}**".format(kind, family_name, type_name))
    try:
        created, failed, skipped, ws_matched = place_manholes_from_csv(
            doc, csv_path,
            family_name=family_name,
            type_name=type_name,
            host_level_name=host_level_name,
            csv_unit=csv_unit,
            x_offset_m=x_off,
            y_offset_m=y_off,
            z_offset_m=z_off,
            rotation_deg=rotation_deg,
            post_x_shift_mm=post_x_mm,
            post_y_shift_mm=post_y_mm,
            use_project_location=auto_mode,
            default_workset_name=default_workset,
            log=log,
            **extra_kwargs)
        results[kind] = (created, failed, skipped, ws_matched)
        log("{}: placed **{}**, failed **{}**, "
            "worksets matched **{}**"
            .format(kind, created, failed, ws_matched))
    except Exception as ex:
        import traceback
        tb = traceback.format_exc()
        results[kind] = None
        log("{} - error type: {}".format(kind, type(ex).__name__))
        log("{} - error: {}".format(kind, ex))
        log("Traceback:")
        log(tb)

try:
    # One TransactionGroup around both kinds so one click = ONE undo
    # entry. The lib opens its own Transactions inside; the group
    # assimilates them under a single name.
    tgroup = DB.TransactionGroup(doc, "Place Structures")
    tgroup.Start()
    try:
        if mh_ok:
            _place_kind("Manholes", mh_family, mh_type, {
                "slab_thickness_mm": mh_slab_mm,
                "total_height_param": mh_height_param,
            })

        if dp_ok:
            # Param specs: which CSV column drives which family parameter.
            # The placer reads each column in csv_unit, converts to mm,
            # then sets it on the instance.
            log("Note: rows with an empty drop column (dia_4 / z_off_4) "
                "still get an instance placed, with the family's default "
                "parameter values.")
            _place_kind("Drop pipes", dp_family, dp_type, {
                "param_specs": [
                    {"param": dp_dia_param,    "source": "dia_4"},
                    {"param": dp_height_param, "source": "z_off_4"},
                ],
            })
        tgroup.Assimilate()
    except Exception:
        tgroup.RollBack()
        raise

    # -----------------------------------------------------------------------
    # 5. ONE combined summary.
    # -----------------------------------------------------------------------
    lines = ["Place Structures - summary\n"]
    for kind, ok, reason in [("Manholes",   mh_ok, mh_reason),
                             ("Drop pipes", dp_ok, dp_reason)]:
        if not ok:
            lines.append("{}:  skipped - {}".format(kind, reason))
        elif results.get(kind) is None:
            lines.append("{}:  ERROR - see output panel / log file".format(kind))
        else:
            created, failed, skipped, ws_matched = results[kind]
            lines.append("{}:  placed {}, failed {}"
                         "  (worksets matched: {})"
                         .format(kind, created, failed, ws_matched))
    summary = "\n".join(lines)
    log("---")
    log("### Done")
    log(summary)
    forms.alert(summary)
finally:
    log.close()
