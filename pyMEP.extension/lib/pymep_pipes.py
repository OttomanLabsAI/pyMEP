# -*- coding: utf-8 -*-
"""Place Revit pipes from an arbitrary CSV.

Entry point:
    build_pipes_from_csv(doc, csv_path, col_map, csv_unit,
                         default_pipe_type_name, default_system_type_name,
                         ws_filter=None, log=None)
                         -> (created, failed, skipped, ws_matched)

Unlike the duct builder this is NOT bound to the toolkit's own export schema;
the caller supplies a column map so any straight-line CSV from Civil 3D,
AutoLISP S2L extracts, FBX-derived centrelines, etc. can drive placement.

`col_map` keys: ``sx``, ``sy``, ``sz``, ``ex``, ``ey``, ``ez``, ``ws``,
``pt``, ``st``, where:
  * ``ws`` (optional) is the column whose value names the target workset
    (one workset per unique value; missing workset names are skipped, not
    crashed),
  * ``pt`` (optional) is the column whose value names the Revit pipe type
    for that row (overrides the default; types are resolved lazily and
    cached),
  * ``st`` (optional) is the column whose value names the Revit piping
    system type for that row (overrides the default; resolved lazily and
    cached the same way as ``pt``).

`ws_filter` (optional) restricts placement to rows whose workset value is
in the given iterable - used by the UI's "filter by workset" step.

Worksets are created BEFORE the placement transaction opens because
``Workset.Create`` must not be called inside an open transaction.
"""

import clr
clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    BuiltInParameter, FilteredElementCollector, FilteredWorksetCollector,
    Level, Transaction, WorksetKind, XYZ,
)
from Autodesk.Revit.DB.Plumbing import (
    Pipe, PipeType, PipingSystemType,
)

from pymep_revit import safe_name, mm2ft
from pymep_csv   import read_csv_dicts


# --------------------------------------------------------------------------
# Unit helpers
# --------------------------------------------------------------------------
def _to_ft(val_str, unit):
    """Convert a CSV value (string) to Revit internal feet."""
    v = float(val_str)
    if unit == "mm":
        return mm2ft(v)
    if unit == "m":
        return mm2ft(v * 1000.0)
    if unit == "ft":
        return v
    raise ValueError("Unsupported csv_unit '{}' (use 'm', 'mm', or 'ft')."
                     .format(unit))


# --------------------------------------------------------------------------
# Type / system / level lookup
# --------------------------------------------------------------------------
def _say(log, msg):
    if log is not None:
        log(msg)


def _find_by_name(doc, cls, name):
    for el in FilteredElementCollector(doc).OfClass(cls):
        try:
            if safe_name(el) == name:
                return el
        except Exception:
            continue
    return None


# --------------------------------------------------------------------------
# Workset handling
# --------------------------------------------------------------------------
def _existing_user_worksets(doc):
    """Return {name: WorksetId} for user worksets in the active document."""
    out = {}
    for ws in FilteredWorksetCollector(doc).OfKind(WorksetKind.UserWorkset):
        out[ws.Name] = ws.Id
    return out


def _norm_ws_name(s):
    """Normalise a workset name for fuzzy matching: trim, collapse internal
    whitespace, lowercase. Used to match names that differ only by spacing
    or case (the most common cause of duplicate worksets being spawned)."""
    if s is None:
        return ""
    return " ".join(s.strip().split()).lower()


def _resolve_worksets(doc, names, log=None):
    """Look up which of `names` correspond to worksets that ALREADY exist
    in the active document. Returns:

        (matched, missing)
        matched : {original_csv_name: WorksetId}     - found in project
        missing : [original_csv_name, ...]           - not found

    Tries exact match first, then case/whitespace-insensitive match. NEVER
    creates new worksets - that's the previous behaviour the user explicitly
    rejected ("set the workset to what is in the project, not create a new
    one of the same name"). Pipes whose workset can't be matched fall
    through to the active workset.
    """
    if not doc.IsWorkshared:
        _say(log, "Document is not workshared - skipping workset assignment.")
        return {}, []

    existing = _existing_user_worksets(doc)
    norm_to_existing = {}
    for nm, wid in existing.items():
        norm_to_existing[_norm_ws_name(nm)] = (nm, wid)

    matched = {}
    missing = []
    for name in names:
        if not name:
            continue
        # 1. exact
        if name in existing:
            matched[name] = existing[name]
            continue
        # 2. fuzzy
        norm = _norm_ws_name(name)
        if norm in norm_to_existing:
            orig_nm, wid = norm_to_existing[norm]
            matched[name] = wid
            _say(log, "  ~ workset `{}` matched existing `{}`"
                      .format(name, orig_nm))
            continue
        # 3. miss
        missing.append(name)

    if missing:
        _say(log, "")
        _say(log, "WARNING: {} workset name(s) in CSV not found in project:"
                  .format(len(missing)))
        for m in missing:
            _say(log, "  - `{}`".format(m))
        _say(log, "Pipes for these worksets will land on the ACTIVE workset.")
        _say(log, "")
        _say(log, "User worksets that DO exist in this project:")
        for nm in sorted(existing.keys()):
            _say(log, "  - `{}`".format(nm))
    else:
        _say(log, "All {} CSV workset(s) matched existing project worksets."
                  .format(len(matched)))

    return matched, missing


def _set_workset(elem, ws_id, log=None):
    p = elem.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM)
    if p is None or p.IsReadOnly:
        return False
    p.Set(ws_id.IntegerValue)
    return True


# --------------------------------------------------------------------------
# Column-name auto-detection
# --------------------------------------------------------------------------
_AUTO_DETECT = {
    "sx": ["start_x", "startx", "sx", "x1", "x_start", "startx_mm"],
    "sy": ["start_y", "starty", "sy", "y1", "y_start", "starty_mm"],
    "sz": ["start_z", "startz", "sz", "z1", "z_start", "startz_mm"],
    "ex": ["end_x", "endx", "ex", "x2", "x_end", "endx_mm"],
    "ey": ["end_y", "endy", "ey", "y2", "y_end", "endy_mm"],
    "ez": ["end_z", "endz", "ez", "z2", "z_end", "endz_mm"],
    "ws": ["workset", "layer", "group", "category"],
    "pt": ["pipe_type", "pipetype", "pipe"],
    "st": ["system_type", "systemtype", "pipingsystemtype", "pipingsystem"],
}


def _norm(s):
    return s.lower().replace("_", "").replace(" ", "").replace("-", "")


def auto_detect_columns(headers):
    """Best-effort {role: header} from a CSV header list. Missing keys are
    omitted so callers can prompt for them."""
    norm_to_orig = {}
    for h in headers:
        norm_to_orig.setdefault(_norm(h), h)
    out = {}
    for role, candidates in _AUTO_DETECT.items():
        for cand in candidates:
            n = _norm(cand)
            if n in norm_to_orig:
                out[role] = norm_to_orig[n]
                break
    return out


# --------------------------------------------------------------------------
# Main entry
# --------------------------------------------------------------------------
def build_pipes_from_csv(doc, csv_path, col_map, csv_unit,
                         default_pipe_type_name, default_system_type_name,
                         host_level_name,
                         x_offset_m=0.0, y_offset_m=0.0, z_offset_m=0.0,
                         rotation_deg=0.0,
                         post_x_shift_mm=0.0, post_y_shift_mm=0.0,
                         use_project_location=False,
                         default_workset_name="",
                         ws_filter=None, log=None):
    """Place a pipe per row in the CSV.

    col_map keys:
      sx, sy, sz, ex, ey, ez   - required, name of the XYZ columns
      ws                       - optional, name of the workset column
      pt                       - optional, name of the pipe-type column
                                 (per-row override of default_pipe_type_name)
      st                       - optional, name of the system-type column
                                 (per-row override of default_system_type_name)

    host_level_name: the Revit Level (looked up by exact name) that every
        pipe is hosted on. The pipe's actual end elevations come from the
        CSV's Z values; the host level is the reference Revit attaches
        the pipe to AND the elevation datum (see Z handling below). Fails
        fast if the named level isn't in the document.

    x_offset_m, y_offset_m, z_offset_m:
        Survey-to-project offsets in metres, subtracted from each CSV
        XYZ value before placement.

    rotation_deg:
        True-North rotation (CCW positive, degrees) applied to
        (csv_xy - xy_offset) before placing. Default 0 = no rotation.
        For HEL11-style survey-grid drawings, ~+/-124.703 deg.

    post_x_shift_mm, post_y_shift_mm:
        Post-rotation XY shift in MILLIMETRES, applied in Revit's frame
        AFTER the rotation. Use this for fine alignment to a project
        reference point (e.g. building grid intersection). The values
        are intuitive because they match Revit's display units. Default
        (0, 0) = no shift. Applied in BOTH manual and auto modes.

    use_project_location:
        If True, the document's ActiveProjectLocation transform is used
        to convert CSV survey/shared coordinates into internal
        coordinates - this is the "automatic" mode that lets Revit do
        the work. The manual XYZ offsets and rotation are IGNORED in
        this mode (post-shift still applies for fine tuning).

    Z handling:
        ``internal_z = csv_z - z_offset_m``  (no host-level addition).
        This matches the project's Dynamo workflow exactly: a CSV Z of
        76.4 m with z_offset_m = 90.5498 places the pipe at internal
        Z = -14.15 m, which is what Revit will show as the absolute
        endpoint elevation. The host level is still used as the
        Pipe.Create() reference but doesn't shift the Z position.

    ws_filter: optional iterable of workset names. When set, only rows whose
        workset value is in the set are placed (others are skipped, not
        counted as failures). Ignored if no workset column is mapped.

    Returns (created, failed, skipped, ws_matched).
    """
    rows = read_csv_dicts(csv_path)
    if not rows:
        raise ValueError("CSV has no data rows.")

    required = ("sx", "sy", "sz", "ex", "ey", "ez")
    missing = [k for k in required if k not in col_map or not col_map[k]]
    if missing:
        raise ValueError("Column mapping missing keys: {}".format(missing))

    # Verify mapped columns exist in the CSV
    sample = rows[0]
    optional_present = [r for r in ("ws", "pt", "st") if col_map.get(r)]
    for role in list(required) + optional_present:
        col = col_map[role]
        if col not in sample:
            raise ValueError("Column '{}' (mapped to {}) not in CSV header."
                             .format(col, role))

    # ------------- pipe type lookup (cached) ------------------------------
    # If pt column is mapped, types are picked per row from the CSV; missing
    # pipe types fail individual rows rather than the whole batch. The
    # default name is resolved up front so non-pt-column mode fails fast.
    pt_col = col_map.get("pt")
    pipe_type_cache = {}

    def resolve_pipe_type(name):
        if name not in pipe_type_cache:
            pipe_type_cache[name] = _find_by_name(doc, PipeType, name)
        return pipe_type_cache[name]

    if not pt_col:
        if resolve_pipe_type(default_pipe_type_name) is None:
            raise ValueError("PipeType '{}' not found in the active document."
                             .format(default_pipe_type_name))

    # ------------- piping system type lookup (cached, mirrors pt) --------
    st_col = col_map.get("st")
    sys_type_cache = {}

    def resolve_sys_type(name):
        if name not in sys_type_cache:
            sys_type_cache[name] = _find_by_name(doc, PipingSystemType, name)
        return sys_type_cache[name]

    if not st_col:
        if resolve_sys_type(default_system_type_name) is None:
            raise ValueError("PipingSystemType '{}' not found in the active "
                             "document.".format(default_system_type_name))

    # ------------- host level (named, fail fast) ---------------------------
    host_level = None
    for lv in FilteredElementCollector(doc).OfClass(Level).ToElements():
        if safe_name(lv) == host_level_name:
            host_level = lv
            break
    if host_level is None:
        available = sorted(safe_name(lv) for lv in
                           FilteredElementCollector(doc).OfClass(Level).ToElements())
        raise ValueError(
            "Host level '{}' not found. Available levels: {}".format(
                host_level_name, ", ".join(available) or "(none)"))

    _say(log, "Parsed **{}** rows.".format(len(rows)))
    _say(log, "Default pipe type:   **{}**{}".format(
        default_pipe_type_name,
        " (per-row override via `{}`)".format(pt_col) if pt_col else ""))
    _say(log, "Default system type: **{}**{}".format(
        default_system_type_name,
        " (per-row override via `{}`)".format(st_col) if st_col else ""))
    _say(log, "Host level:  **{}**  (Elevation = **{:.3f} m** in project; "
              "informational only, NOT added to placed Z)"
              .format(host_level_name, host_level.Elevation * 0.3048))
    _say(log, "CSV units:   **{}**".format(csv_unit))
    _say(log, "Offsets (m): X={:.3f}  Y={:.3f}  Z={:.3f}"
              .format(x_offset_m, y_offset_m, z_offset_m))
    _say(log, "Rotation:    **{:.4f} deg** (CCW around XY offset point)"
              .format(rotation_deg))
    _say(log, "Post-shift (mm, applied AFTER rotation in Revit frame): "
              "X={:.1f}  Y={:.1f}"
              .format(post_x_shift_mm, post_y_shift_mm))

    # Bounding box of the CSV's coordinates, in CSV units, so the user can
    # eyeball whether the offsets they've set look right.
    try:
        sxs = [float(r[col_map["sx"]]) for r in rows]
        sys = [float(r[col_map["sy"]]) for r in rows]
        szs = [float(r[col_map["sz"]]) for r in rows]
        exs = [float(r[col_map["ex"]]) for r in rows]
        eys = [float(r[col_map["ey"]]) for r in rows]
        ezs = [float(r[col_map["ez"]]) for r in rows]
        all_x = sxs + exs
        all_y = sys + eys
        all_z = szs + ezs
        _say(log,
             "CSV range ({}): X [{:.3f} .. {:.3f}]  Y [{:.3f} .. {:.3f}]  "
             "Z [{:.3f} .. {:.3f}]".format(
                 csv_unit,
                 min(all_x), max(all_x),
                 min(all_y), max(all_y),
                 min(all_z), max(all_z)))
    except Exception:
        pass

    ws_filter_set = set(ws_filter) if ws_filter is not None else None
    if ws_filter_set is not None:
        _say(log, "Workset filter: **{}** workset(s) - {}"
                  .format(len(ws_filter_set),
                          ", ".join("`{}`".format(w) for w in sorted(ws_filter_set))))

    # ----------------------------------------------------------------------
    # Phase 1 - resolve worksets (read-only - never creates new ones)
    # ----------------------------------------------------------------------
    ws_col = col_map.get("ws")
    ws_lookup = {}      # {name: WorksetId}
    ws_missing = []     # CSV-named worksets that don't exist in project
    if ws_col:
        unique_names = []
        seen = set()
        for r in rows:
            nm = (r.get(ws_col) or "").strip()
            if not nm or nm in seen:
                continue
            if ws_filter_set is not None and nm not in ws_filter_set:
                continue
            seen.add(nm)
            unique_names.append(nm)
        _say(log, "Workset column **{}** - {} unique value(s) to resolve."
                  .format(ws_col, len(unique_names)))
        ws_lookup, ws_missing = _resolve_worksets(doc, unique_names, log=log)
    elif default_workset_name:
        _say(log, "No workset column - looking up default workset **{}**"
                  .format(default_workset_name))
        ws_lookup, ws_missing = _resolve_worksets(
            doc, [default_workset_name], log=log)

    ws_matched = len(ws_lookup)

    # Pre-compute offsets (in feet), rotation trig, and post-shift once
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

    # If auto mode is on, fetch the project's survey-to-internal transform
    # and log what we found. The inverse of GetTotalTransform() takes a
    # point expressed in shared/survey coordinates and gives the matching
    # internal-coordinate point. This automatically handles XY translation,
    # True North rotation, and Z elevation in one step.
    inv_xform = None
    if use_project_location:
        active_loc = doc.ActiveProjectLocation
        if active_loc is None:
            raise RuntimeError(
                "Document has no ActiveProjectLocation - cannot use "
                "'auto' placement mode. Disable 'Use project survey "
                "point' in Settings and use manual offsets instead.")
        total_xform = active_loc.GetTotalTransform()
        inv_xform   = total_xform.Inverse

        # The forward transform takes internal -> shared. Its Origin is
        # where internal (0,0,0) lands in shared coords - i.e., where the
        # internal origin sits on the survey grid. BasisX is the project's
        # +X direction expressed in shared coords; the angle of BasisX in
        # the XY plane is the project's True-North rotation.
        og  = total_xform.Origin
        bx  = total_xform.BasisX
        impl_theta_deg = math.degrees(math.atan2(bx.Y, bx.X))
        _say(log, "")
        _say(log, "**AUTO MODE**  -  using ActiveProjectLocation.GetTotalTransform()")
        _say(log, "  Survey origin in internal frame (m): "
                  "X={:.3f}  Y={:.3f}  Z={:.3f}"
                  .format(og.X * 0.3048, og.Y * 0.3048, og.Z * 0.3048))
        _say(log, "  Implied True-North rotation: **{:.4f} deg** "
                  "(angle of project +X in shared frame)"
                  .format(impl_theta_deg))
        _say(log, "  Manual XY offset, rotation, Z offset are IGNORED.")
        if post_shifts:
            _say(log, "  Post-shift (mm) IS still applied: X={:+.1f} Y={:+.1f}"
                      .format(post_x_shift_mm, post_y_shift_mm))
        _say(log, "")

    # ----------------------------------------------------------------------
    # Phase 2 - place pipes
    # ----------------------------------------------------------------------
    created = 0
    failed  = 0
    skipped = 0
    traced  = False  # log the transform math for the first row that places
    with Transaction(doc, "Build Pipes From CSV") as t:
        t.Start()

        for ri, row in enumerate(rows):
            try:
                ws_name = ""
                if ws_col:
                    ws_name = (row.get(ws_col) or "").strip()
                    if ws_filter_set is not None and ws_name not in ws_filter_set:
                        skipped += 1
                        continue
                elif default_workset_name:
                    ws_name = default_workset_name

                # Pick pipe type for this row
                if pt_col:
                    pt_name = (row.get(pt_col) or "").strip() or default_pipe_type_name
                else:
                    pt_name = default_pipe_type_name
                pt_elem = resolve_pipe_type(pt_name)
                if pt_elem is None:
                    raise ValueError("PipeType '{}' not in document"
                                     .format(pt_name))

                # Pick system type for this row
                if st_col:
                    st_name = (row.get(st_col) or "").strip() or default_system_type_name
                else:
                    st_name = default_system_type_name
                st_elem = resolve_sys_type(st_name)
                if st_elem is None:
                    raise ValueError("PipingSystemType '{}' not in document"
                                     .format(st_name))

                # Convert CSV strings -> internal feet. Two paths:
                #
                #   AUTO   - apply ActiveProjectLocation.GetTotalTransform
                #            inverse (Revit handles XY translation, True
                #            North rotation, and Z elevation in one go).
                #   MANUAL - subtract XY offsets, rotate around the XY
                #            offset point, subtract Z offset.
                #
                # Post-shift (mm) is applied in BOTH modes after the main
                # transform, in Revit's frame, for fine alignment.
                if inv_xform is not None:
                    sx_in = _to_ft(row[col_map["sx"]], csv_unit)
                    sy_in = _to_ft(row[col_map["sy"]], csv_unit)
                    sz_in = _to_ft(row[col_map["sz"]], csv_unit)
                    ex_in = _to_ft(row[col_map["ex"]], csv_unit)
                    ey_in = _to_ft(row[col_map["ey"]], csv_unit)
                    ez_in = _to_ft(row[col_map["ez"]], csv_unit)

                    p_start = inv_xform.OfPoint(XYZ(sx_in, sy_in, sz_in))
                    p_end   = inv_xform.OfPoint(XYZ(ex_in, ey_in, ez_in))
                    sx, sy, sz = p_start.X, p_start.Y, p_start.Z
                    ex, ey, ez = p_end.X,   p_end.Y,   p_end.Z
                else:
                    sx_raw = _to_ft(row[col_map["sx"]], csv_unit) - x_off_ft
                    sy_raw = _to_ft(row[col_map["sy"]], csv_unit) - y_off_ft
                    ex_raw = _to_ft(row[col_map["ex"]], csv_unit) - x_off_ft
                    ey_raw = _to_ft(row[col_map["ey"]], csv_unit) - y_off_ft

                    if rotates:
                        sx = sx_raw * cos_t - sy_raw * sin_t
                        sy = sx_raw * sin_t + sy_raw * cos_t
                        ex = ex_raw * cos_t - ey_raw * sin_t
                        ey = ex_raw * sin_t + ey_raw * cos_t
                    else:
                        sx, sy, ex, ey = sx_raw, sy_raw, ex_raw, ey_raw

                    sz = _to_ft(row[col_map["sz"]], csv_unit) - z_off_ft
                    ez = _to_ft(row[col_map["ez"]], csv_unit) - z_off_ft

                if post_shifts:
                    sx += post_x_ft;  sy += post_y_ft
                    ex += post_x_ft;  ey += post_y_ft

                start_pt = XYZ(sx, sy, sz)
                end_pt   = XYZ(ex, ey, ez)

                # Diagnostic trace - first successfully placed row only.
                if not traced:
                    csv_sx_raw = float(row[col_map["sx"]])
                    csv_sy_raw = float(row[col_map["sy"]])
                    csv_sz_raw = float(row[col_map["sz"]])
                    _say(log, "")
                    _say(log, "**TRACE (first row)** -- if numbers below look wrong, that's where to look:")
                    _say(log, "  CSV value (raw {}): start = ({}, {}, {})"
                              .format(csv_unit, csv_sx_raw, csv_sy_raw, csv_sz_raw))
                    if inv_xform is not None:
                        _say(log, "  Auto mode: applied "
                                  "ActiveProjectLocation.GetTotalTransform.Inverse")
                    else:
                        _say(log, "  After XY offset (raw {}): ({:.3f}, {:.3f})"
                                  .format(csv_unit,
                                          csv_sx_raw - x_offset_m,
                                          csv_sy_raw - y_offset_m))
                        if rotates:
                            _say(log, "  After rotation by {:.4f} deg (raw {}): "
                                      "({:.3f}, {:.3f})"
                                      .format(rotation_deg, csv_unit,
                                              (csv_sx_raw - x_offset_m) * cos_t -
                                              (csv_sy_raw - y_offset_m) * sin_t,
                                              (csv_sx_raw - x_offset_m) * sin_t +
                                              (csv_sy_raw - y_offset_m) * cos_t))
                        else:
                            _say(log, "  Rotation: 0 deg, no change")
                        _say(log, "  Z after Z-offset (raw {}): {:.3f}"
                                  .format(csv_unit, csv_sz_raw - z_offset_m))
                    if post_shifts:
                        _say(log, "  After post-shift (Revit-frame mm): "
                                  "X{:+.1f}  Y{:+.1f}"
                                  .format(post_x_shift_mm, post_y_shift_mm))
                    _say(log, "  Internal Revit XYZ (feet): "
                              "({:.4f}, {:.4f}, {:.4f})".format(sx, sy, sz))
                    _say(log, "  Internal Revit XYZ in metres: "
                              "({:.3f}, {:.3f}, {:.3f})"
                              .format(sx * 0.3048, sy * 0.3048, sz * 0.3048))
                    _say(log, "")
                    traced = True

                if start_pt.DistanceTo(end_pt) < 1e-6:
                    raise ValueError("coincident start/end")

                pipe = Pipe.Create(doc, st_elem.Id, pt_elem.Id, host_level.Id,
                                   start_pt, end_pt)

                if ws_name and ws_name in ws_lookup:
                    _set_workset(pipe, ws_lookup[ws_name], log=log)

                created += 1
                bits = []
                if ws_name: bits.append("ws=`{}`".format(ws_name))
                if pt_col:  bits.append("type=`{}`".format(pt_name))
                if st_col:  bits.append("sys=`{}`".format(st_name))
                tag = (" | " + " | ".join(bits)) if bits else ""
                _say(log, "  Row {}: [OK]{}".format(ri + 1, tag))

            except Exception as ex:
                failed += 1
                _say(log, "  Row {}: FAILED - {}".format(ri + 1, ex))

        t.Commit()

    return created, failed, skipped, ws_matched
