# -*- coding: utf-8 -*-
"""
pymep_manholes
================

Place a Revit family instance at every row of a manholes CSV.

CSV schema (S2CSV.lsp output, survey-grid units by default):
    id, top_x, top_y, top_z,
    dia_1, dia_2, dia_3, dia_4, dia_5,
    z_off_2, z_off_3, z_off_4, z_off_5
    [, workset]

For each row the lib:
  1. Locates the configured FamilySymbol (family + type names from caller).
  2. Activates it if needed.
  3. Applies the survey-to-project transform:
       internal_xy = R(rotation_deg) . (csv_top_xy - xy_offset_m)
       internal_z  = csv_top_z - z_offset_m
     (same convention as the pipes builder).
  4. Calls Document.Create.NewFamilyInstance(point, symbol, level, NonStructural).
  5. Sets the `total_height` instance parameter to (deepest z_off_N + slab_thickness_mm).
  6. Optionally assigns the row's workset (resolve-only - never creates).

Returns (created, failed, skipped, ws_matched).

The workset helpers are imported from pymep_pipes to keep behaviour
identical between the two builders.
"""

import os
import csv

import clr
clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    BuiltInParameter, FilteredElementCollector, Family, FamilySymbol,
    Level, Structure, Transaction, XYZ,
)

from pymep_revit  import mm2ft
from pymep_pipes   import (
    _resolve_worksets, _set_workset, _say, _to_ft,
)
from pymep_config  import (
    MANHOLE_LAYER_WORKSET_MAP,
    SURVEY_TO_PROJECT_ROTATION_DEG,
    SURVEY_TO_PROJECT_TRANSLATION_MM,
)


def _norm(s):
    return (s or "").strip().lower()


def _elem_name(e):
    """Robust name accessor. In some Revit/IronPython combos, accessing
    .Name on Family or FamilySymbol throws because Name is overloaded
    (property + indexer). Fall back to Element.Name property descriptor."""
    try:
        return e.Name
    except Exception:
        try:
            from Autodesk.Revit.DB import Element
            return Element.Name.GetValue(e)
        except Exception:
            return ""


def _find_level(doc, name):
    nrm = _norm(name)
    for lvl in FilteredElementCollector(doc).OfClass(Level):
        if _norm(_elem_name(lvl)) == nrm:
            return lvl
    return None


def _find_family_symbol(doc, family_name, type_name):
    """Find the FamilySymbol matching family_name + type_name (case-
    insensitive, trimmed). Returns the symbol element, or None."""
    target_fam = _norm(family_name)
    target_typ = _norm(type_name)
    for sym in FilteredElementCollector(doc).OfClass(FamilySymbol):
        try:
            fam_nm = _elem_name(sym.Family)
            typ_nm = _elem_name(sym)
        except Exception:
            continue
        if _norm(fam_nm) != target_fam:
            continue
        if _norm(typ_nm) != target_typ:
            continue
        return sym
    return None


def _list_family_types(doc, family_name):
    """For diagnostics: list all type names available under a family."""
    target = _norm(family_name)
    out = []
    for sym in FilteredElementCollector(doc).OfClass(FamilySymbol):
        try:
            fn = _elem_name(sym.Family)
        except Exception:
            continue
        if _norm(fn) == target:
            out.append(_elem_name(sym))
    return sorted(set(out))


def _list_all_families(doc):
    """List every (family, [types]) loaded in the doc - for diagnostics."""
    fams = {}
    for sym in FilteredElementCollector(doc).OfClass(FamilySymbol):
        try:
            fn = _elem_name(sym.Family)
            tn = _elem_name(sym)
        except Exception:
            continue
        fams.setdefault(fn, []).append(tn)
    return [(f, sorted(set(ts))) for f, ts in sorted(fams.items())]


def _set_param_mm(elem, name, value_mm, log=None):
    """Set an instance parameter to (value_mm) - converted to internal feet
    for length params. Returns True if set, False otherwise."""
    p = elem.LookupParameter(name)
    if p is None:
        _say(log, "  ! parameter `{}` not found on instance".format(name))
        return False
    if p.IsReadOnly:
        _say(log, "  ! parameter `{}` is read-only".format(name))
        return False
    try:
        p.Set(mm2ft(float(value_mm)))
        return True
    except Exception as ex:
        _say(log, "  ! could not set `{}` = {}: {}".format(name, value_mm, ex))
        return False


def _to_mm(val_str, csv_unit):
    """Coerce a CSV string in csv_unit into millimetres."""
    v = float(val_str)
    if csv_unit == "mm": return v
    if csv_unit == "m":  return v * 1000.0
    if csv_unit == "ft": return v * 304.8
    raise ValueError("unknown csv_unit '{}'".format(csv_unit))


def place_manholes_from_csv(doc, csv_path,
                            family_name, type_name,
                            host_level_name,
                            csv_unit="m",
                            x_offset_m=0.0, y_offset_m=0.0, z_offset_m=0.0,
                            rotation_deg=0.0,
                            post_x_shift_mm=0.0, post_y_shift_mm=0.0,
                            use_project_location=False,
                            slab_thickness_mm=0.0,
                            total_height_param="Height",
                            default_workset_name="",
                            param_specs=None,
                            log=None):
    """Read an S2CSV-format CSV and place a family instance per row.

    Used by both Place Manholes and Place Drop Pipes - the geometry,
    transform, layer-to-workset mapping, CSV pre-transform, and host-
    level resolution are identical between them. The two differ only
    in WHICH family/type to place and WHICH parameters to set on each
    instance.

    Expected CSV columns (one row per solid):
        id, top_x, top_y, top_z, dia_1, dia_2, dia_3, dia_4, dia_5,
        z_off_2, z_off_3, z_off_4, z_off_5 [, layer] [, workset]

    Transforms CSV survey-grid coordinates to Revit internal coordinates
    using the SAME settings as the pipes builder. Two paths:

      AUTO   - use doc.ActiveProjectLocation.GetTotalTransform.Inverse
               (Manage > Coordinates handles XY translation, True
               North rotation, and Z elevation in one go).
      MANUAL - subtract XY offsets, rotate around the XY offset point.
               (z_offset_m is IGNORED for these structures - top_z is
                taken as absolute elevation.)

    Post-shift (mm) is applied in BOTH modes after the main transform.

    Worksets resolve from (in priority order):
        1. row's `layer` column -> looked up in MANHOLE_LAYER_WORKSET_MAP
        2. row's explicit `workset` column
        3. default_workset_name

    Parameter assignment:

    If `param_specs` is None (default - manhole behaviour), one parameter
    is set per row:
        total_height_param  =  deepest non-empty z_off_N (mm)
                              + slab_thickness_mm

    If `param_specs` is supplied, each entry sets one parameter and
    replaces the manhole defaults. Each spec is a dict:

        {
            "param":     "DIA",          # parameter name on the family
            "source":    "dia_4",        # CSV column to read
            "add_mm":    0.0,            # optional - added after unit conv
        }

    The CSV value is read in csv_unit, converted to mm, optionally has
    `add_mm` added (e.g. slab thickness for a Height param), and the
    result is set on the instance via the parameter's storage type
    (length params expect feet internally; mm2ft handles that).

    csv_unit:               'm' (default), 'mm', or 'ft' for ALL length
                            columns (top_x/y/z, dia_*, z_off_*).
    rotation_deg:           True-North rotation, CCW positive (manual).
    use_project_location:   True -> use ActiveProjectLocation transform
                            and ignore manual XY offset / rotation
                            (post-shift still applies).
    slab_thickness_mm:      legacy - manhole-only; added to deepest z_off_N
                            when param_specs is None.
    """
    if not os.path.isfile(csv_path):
        raise IOError("CSV not found: {}".format(csv_path))

    _say(log, "Looking up family **{}** : **{}** ...".format(family_name, type_name))

    # ---- Load + validate FamilySymbol ----
    try:
        sym = _find_family_symbol(doc, family_name, type_name)
    except Exception as ex:
        # Surface the real exception type/message instead of swallowing it
        raise RuntimeError(
            "Family lookup failed with {}: {}".format(type(ex).__name__, ex))

    if sym is None:
        # Build a useful diagnostic - list every family loaded in the doc
        all_fams = _list_all_families(doc)
        if not all_fams:
            raise ValueError(
                "No families are loaded in the document at all. Load the "
                "manhole family via Insert > Load Family, then re-run.")
        # Try to find close matches by family name
        target_fam = _norm(family_name)
        partial = [(f, ts) for (f, ts) in all_fams if target_fam and target_fam in _norm(f)]
        msg = ["Family '{}' / type '{}' not found.".format(family_name, type_name)]
        if partial:
            msg.append("\nFamilies whose name contains '{}':".format(family_name))
            for f, ts in partial[:10]:
                msg.append("  - {}".format(f))
                for t in ts[:5]:
                    msg.append("      type: {}".format(t))
                if len(ts) > 5:
                    msg.append("      ... and {} more types".format(len(ts) - 5))
        else:
            msg.append("\nNo family name contains '{}'. First 20 loaded families:"
                       .format(family_name))
            for f, _ in all_fams[:20]:
                msg.append("  - {}".format(f))
            if len(all_fams) > 20:
                msg.append("  ... and {} more".format(len(all_fams) - 20))
        raise ValueError("\n".join(msg))

    if not sym.IsActive:
        with Transaction(doc, "Activate manhole family symbol") as t:
            t.Start()
            sym.Activate()
            doc.Regenerate()
            t.Commit()
        _say(log, "Activated family symbol **{}** : **{}**".format(family_name, type_name))

    # ---- Host level ----
    host_level = _find_level(doc, host_level_name)
    if host_level is None:
        names = sorted(l.Name for l in FilteredElementCollector(doc).OfClass(Level))
        raise ValueError(
            "Level '{}' not found. Available levels: {}".format(
                host_level_name, ", ".join(names)))

    # ---- Read CSV ----
    rows = []
    with open(csv_path, "rb" if str is bytes else "r") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or []
    _say(log, "Read **{}** rows from {}".format(len(rows), os.path.basename(csv_path)))
    _say(log, "Columns found: {}".format(", ".join(fieldnames) or "(none)"))

    # ---- Validate it's actually an S2CSV export ----
    REQUIRED = ["top_x", "top_y", "top_z"]
    missing = [c for c in REQUIRED if c not in fieldnames]
    if missing:
        raise ValueError(
            "CSV is missing required column(s) {}.\n\n"
            "Place Manholes expects an S2CSV export from AutoCAD, with "
            "columns: id, top_x, top_y, top_z, dia_1..5, z_off_2..5.\n\n"
            "Found columns: {}\n\n"
            "If you picked drainage_pipes.csv by mistake, that's the pipes "
            "builder's input - this button needs the manholes/structures "
            "export."
            .format(missing, fieldnames))

    # ---- Pre-transform: survey-grid -> project-local ----
    # The S2CSV export from AutoCAD is in raw survey-grid drawing units
    # (which is metres for HEL11). The pipes pipeline pre-transforms
    # this in xlsx_to_pipes_csv.py before the CSV is read; the manhole
    # pipeline does the same conversion inline here.
    #
    # IMPORTANT: the pre-transform writes back values in the SAME unit
    # that csv_unit declares (m or mm). The per-row code then converts
    # csv_unit -> internal feet exactly once. This avoids the
    # double-conversion bug (m -> mm here, then m -> mm again per-row).
    #
    # The transform itself is rigid: 2D rotation + translation in mm,
    # converted back to csv_unit at the end.
    import math as _math
    _theta = _math.radians(SURVEY_TO_PROJECT_ROTATION_DEG)
    _cos, _sin = _math.cos(_theta), _math.sin(_theta)
    _tx, _ty = SURVEY_TO_PROJECT_TRANSLATION_MM  # already in mm
    if csv_unit == "m":
        _to_csv_unit = 0.001          # mm -> m
        _from_csv_to_mm = 1000.0      # m -> mm for the transform input
    elif csv_unit == "mm":
        _to_csv_unit = 1.0            # mm -> mm
        _from_csv_to_mm = 1.0
    elif csv_unit == "ft":
        _to_csv_unit = 1.0 / 304.8    # mm -> ft
        _from_csv_to_mm = 304.8
    else:
        raise ValueError("Unsupported csv_unit '{}'".format(csv_unit))

    _say(log, "")
    _say(log, "**Pre-transform applied to survey-grid CSV input:**")
    _say(log, "  rotation    = {:.6f} deg".format(SURVEY_TO_PROJECT_ROTATION_DEG))
    _say(log, "  translation = ({:.3f}, {:.3f}) mm".format(_tx, _ty))
    _say(log, "  pre-transform output is in the same unit as Settings "
              "csv_unit = `{}` (no double-conversion).".format(csv_unit))
    _say(log, "  Settings (csv_unit / offsets / rotation / post-shift /")
    _say(log, "  AUTO mode) are then applied identically to the pipes builder.")
    _say(log, "")

    for r in rows:
        try:
            tx_csv = float(r["top_x"])
            ty_csv = float(r["top_y"])
            tz_csv = float(r["top_z"])
        except (TypeError, ValueError):
            continue
        # Pull XY into mm for the transform, apply, then convert back to csv_unit
        tx_mm = tx_csv * _from_csv_to_mm
        ty_mm = ty_csv * _from_csv_to_mm
        px_mm = tx_mm * _cos - ty_mm * _sin + _tx
        py_mm = tx_mm * _sin + ty_mm * _cos + _ty
        r["top_x"] = "{:.6f}".format(px_mm * _to_csv_unit)
        r["top_y"] = "{:.6f}".format(py_mm * _to_csv_unit)
        # Z is identity in space; keep it in csv_unit untouched.
        r["top_z"] = "{:.6f}".format(tz_csv)
        # z_off_5 is also already in csv_unit (it's a vertical distance from
        # the same drawing). Leave it alone - the per-row code reads it via
        # _to_ft(csv_unit) just like every other length value.

    # ---- Worksets ----
    # Three sources, in priority order:
    #   1. row's `layer` column -> looked up in MANHOLE_LAYER_WORKSET_MAP
    #   2. row's explicit `workset` column
    #   3. default_workset_name (Settings)
    ws_lookup = {}
    has_layer = "layer" in fieldnames
    has_ws    = "workset" in fieldnames

    layer_misses = {}  # {layer: count} for rows whose layer wasn't in the map

    def _row_workset(r):
        """Pick the workset name for a row using the priority above."""
        if has_layer:
            lyr = (r.get("layer") or "").strip()
            if lyr:
                mapped = MANHOLE_LAYER_WORKSET_MAP.get(lyr)
                if mapped:
                    return mapped
                layer_misses[lyr] = layer_misses.get(lyr, 0) + 1
        if has_ws:
            nm = (r.get("workset") or "").strip()
            if nm: return nm
        return default_workset_name

    if doc.IsWorkshared:
        names = []
        seen  = set()
        for r in rows:
            nm = _row_workset(r)
            if nm and nm not in seen:
                seen.add(nm); names.append(nm)
        if names:
            ws_lookup, _ = _resolve_worksets(doc, names, log=log)

    # Reset miss counter so we don't double-count when iterating to place
    layer_misses = {}

    # ---- Transform setup ----
    # Mirrors the pipes builder exactly. AUTO mode uses
    # ActiveProjectLocation; MANUAL uses XY offset + rotation. Either
    # way, post-shift (mm) is added at the end.
    import math
    x_off_ft  = mm2ft(x_offset_m * 1000.0)
    y_off_ft  = mm2ft(y_offset_m * 1000.0)
    z_off_ft  = mm2ft(z_offset_m * 1000.0)
    theta     = math.radians(rotation_deg)
    cos_t     = math.cos(theta)
    sin_t     = math.sin(theta)
    rotates   = abs(rotation_deg) > 1e-9
    post_x_ft = mm2ft(post_x_shift_mm)
    post_y_ft = mm2ft(post_y_shift_mm)
    post_shifts = (abs(post_x_shift_mm) > 1e-9 or
                   abs(post_y_shift_mm) > 1e-9)

    inv_xform = None
    if use_project_location:
        active_loc = doc.ActiveProjectLocation
        if active_loc is None:
            raise RuntimeError(
                "Document has no ActiveProjectLocation - cannot use "
                "'auto' placement mode. Disable it in Settings and use "
                "manual offsets instead.")
        total_xform = active_loc.GetTotalTransform()
        inv_xform   = total_xform.Inverse
        og  = total_xform.Origin
        bx  = total_xform.BasisX
        impl_theta_deg = math.degrees(math.atan2(bx.Y, bx.X))
        _say(log, "")
        _say(log, "**AUTO MODE**  -  using ActiveProjectLocation.GetTotalTransform()")
        _say(log, "  Survey origin in internal frame (m): "
                  "X={:.3f}  Y={:.3f}  Z={:.3f}"
                  .format(og.X * 0.3048, og.Y * 0.3048, og.Z * 0.3048))
        _say(log, "  Implied True-North rotation: **{:.4f} deg**"
                  .format(impl_theta_deg))
        _say(log, "  Manual XY offset, rotation, Z offset are IGNORED.")
        if post_shifts:
            _say(log, "  Post-shift (mm) IS still applied: X={:+.1f} Y={:+.1f}"
                      .format(post_x_shift_mm, post_y_shift_mm))
        _say(log, "")

    _say(log, "Family:        **{}** : **{}**".format(family_name, type_name))
    _say(log, "Host level:    **{}**  (Elevation = {:.3f} m)"
              .format(host_level_name, host_level.Elevation * 0.3048))
    _say(log, "CSV units:     **{}**  (top_x/top_y/top_z columns)"
              .format(csv_unit))
    _say(log, "Mode:          **{}**".format(
              "AUTO  (project survey point)" if use_project_location
              else "MANUAL  (offsets + rotation)"))
    if not use_project_location:
        _say(log, "Offsets (m):   X={:.3f}  Y={:.3f}  Z={:.3f}  (Z IGNORED for manholes)"
                  .format(x_offset_m, y_offset_m, z_offset_m))
        _say(log, "Rotation:      **{:.4f} deg** (CCW around XY offset point)"
                  .format(rotation_deg))
    _say(log, "Z handling:    **top_z used as absolute elevation** "
              "(no z_offset_m subtraction)")
    _say(log, "Post-shift:    X={:+.1f} mm  Y={:+.1f} mm"
              .format(post_x_shift_mm, post_y_shift_mm))
    _say(log, "Slab thickness:**{:.1f} mm** (added to height parameter)"
              .format(slab_thickness_mm))
    _say(log, "Height param:  `{}`".format(total_height_param))

    # ---- Place ----
    created = 0
    failed  = 0
    skipped = 0
    NS = Structure.StructuralType.NonStructural

    with Transaction(doc, "Place Manholes From CSV") as t:
        t.Start()
        for ri, row in enumerate(rows):
            try:
                # XY: full transform (auto / manual + rotation + post-shift).
                # Z:  manhole top_z in CSV is already the correct absolute
                #     elevation (in csv_unit). Skip the z_offset_m
                #     subtraction the pipes builder applies; just convert
                #     csv_unit -> internal feet directly.
                if inv_xform is not None:
                    sx_in = _to_ft(row["top_x"], csv_unit)
                    sy_in = _to_ft(row["top_y"], csv_unit)
                    sz_in = _to_ft(row["top_z"], csv_unit)
                    p = inv_xform.OfPoint(XYZ(sx_in, sy_in, sz_in))
                    rx, ry = p.X, p.Y
                else:
                    sx_raw = _to_ft(row["top_x"], csv_unit) - x_off_ft
                    sy_raw = _to_ft(row["top_y"], csv_unit) - y_off_ft
                    if rotates:
                        rx = sx_raw * cos_t - sy_raw * sin_t
                        ry = sx_raw * sin_t + sy_raw * cos_t
                    else:
                        rx, ry = sx_raw, sy_raw

                # Z: top_z taken as absolute elevation, no offset applied
                sz = _to_ft(row["top_z"], csv_unit)

                if post_shifts:
                    rx += post_x_ft
                    ry += post_y_ft

                pt = XYZ(rx, ry, sz)

                # Diagnostic trace on first row
                if ri == 0:
                    _say(log, "")
                    _say(log, "**TRACE (first row)** -- check XYZ math is sane:")
                    _say(log, "  CSV input (csv_unit = {}):".format(csv_unit))
                    _say(log, "    top_x = {}".format(row["top_x"]))
                    _say(log, "    top_y = {}".format(row["top_y"]))
                    _say(log, "    top_z = {}".format(row["top_z"]))
                    _say(log, "  Internal Revit XYZ (Revit's own native units = feet):")
                    _say(log, "    ({:.4f}, {:.4f}, {:.4f}) ft".format(rx, ry, sz))
                    _say(log, "  Same in millimetres (what Revit shows in mm projects):")
                    _say(log, "    ({:.1f}, {:.1f}, {:.1f}) mm"
                              .format(rx*304.8, ry*304.8, sz*304.8))
                    _say(log, "  Same in metres (what you'd see in a m project):")
                    _say(log, "    ({:.3f}, {:.3f}, {:.3f}) m"
                              .format(rx*0.3048, ry*0.3048, sz*0.3048))
                    # If we can, also report what the placed manhole should
                    # display when Revit converts back to shared (survey) coords
                    if inv_xform is not None:
                        try:
                            shared_pt = total_xform.OfPoint(XYZ(rx, ry, sz))
                            _say(log, "  Shared (survey) coords this point will display as in Revit:")
                            _say(log, "    E = {:.4f} m, N = {:.4f} m, Elev = {:.4f} m"
                                      .format(shared_pt.X*0.3048,
                                              shared_pt.Y*0.3048,
                                              shared_pt.Z*0.3048))
                            _say(log, "    -> compare to CSV input  E={}  N={}  Z={}"
                                      .format(row["top_x"], row["top_y"], row["top_z"]))
                            _say(log, "    (these should MATCH if AUTO mode + project survey are correct)")
                        except Exception as ex:
                            _say(log, "  (couldn't compute shared coords: {})".format(ex))
                    _say(log, "")

                inst = doc.Create.NewFamilyInstance(pt, sym, host_level, NS)

                if param_specs:
                    # Generic mode: walk supplied (param, source[, add_mm]) specs.
                    # Each spec sets one parameter from one CSV column. Source
                    # column is read in csv_unit, converted to mm, optional
                    # add_mm added, then written via _set_param_mm.
                    for spec in param_specs:
                        pname = spec.get("param")
                        src   = spec.get("source")
                        addv  = float(spec.get("add_mm", 0.0) or 0.0)
                        if not pname or not src:
                            continue
                        raw = (row.get(src) or "").strip()
                        if raw == "":
                            _say(log, "  ! row {}: column `{}` empty - skipping `{}`"
                                      .format(ri, src, pname))
                            continue
                        try:
                            val_mm = _to_ft(raw, csv_unit) * 304.8 + addv
                        except (TypeError, ValueError):
                            _say(log, "  ! row {}: could not parse `{}`={}"
                                      .format(ri, src, raw))
                            continue
                        _set_param_mm(inst, pname, val_mm, log=log)
                else:
                    # Default (manhole) behaviour: height from deepest z_off_N + slab.
                    deepest_off = 0.0
                    for key in ("z_off_5", "z_off_4", "z_off_3", "z_off_2"):
                        raw = (row.get(key) or "").strip()
                        if raw == "":
                            continue
                        try:
                            deepest_off = _to_ft(raw, csv_unit) * 304.8
                            break
                        except (TypeError, ValueError):
                            continue
                    total_h = deepest_off + slab_thickness_mm
                    _set_param_mm(inst, total_height_param, total_h, log=log)

                ws_name = _row_workset(row)
                if ws_name and ws_name in ws_lookup:
                    _set_workset(inst, ws_lookup[ws_name], log=log)

                created += 1
            except Exception as ex:
                _say(log, "  ! row {} ({}) failed: {}".format(
                    ri, row.get("id", "?"), ex))
                failed += 1

        t.Commit()

    _say(log, "")
    _say(log, "Placed **{}** instances ({} failed, {} skipped, "
              "{} worksets matched)"
              .format(created, failed, skipped, len(ws_lookup)))
    if layer_misses:
        _say(log, "")
        _say(log, "Layers in CSV that aren't in MANHOLE_LAYER_WORKSET_MAP "
                  "(those instances fell back to row.workset / default):")
        for lyr, n in sorted(layer_misses.items(), key=lambda kv: -kv[1]):
            _say(log, "  - `{}`  ({} rows)".format(lyr, n))
    return created, failed, skipped, len(ws_lookup)
