# -*- coding: utf-8 -*-
"""Read PIPE exports from the OttomanLabs utilities dashboard and shape
them for the LandXML pipe placer.

The dashboard's EXPORT button produces JSON; pipes are expected as kind
"ol-utilities-pipes" with the same envelope as the structures export
(source / generated / epsg / origin / scope / count). This module is
deliberately tolerant about the row schema so viewer versions can vary:

  * start/end as nested dicts ({"start": {"easting","northing","z_m"}, ...})
    or flat keys (start_easting/..., sx/sy/sz/ex/ey/ez, x1/y1/z1/x2/y2/z2);
  * diameter as dia_mm / diameter_mm / dia_m / diameter_m; rectangular
    duct banks (shape "box"/"r", width x height) are kept but flagged
    non-circular - the pipe placer only places circular runs (duct banks
    belong to the Encasement workflow);
  * coordinates in absolute survey metres; when every value is small and
    the export carries an origin, they are treated as dashboard-local
    metres and the origin is added (the structures export bakes the
    origin in, but a pipes export that keeps viewer-local values is
    still handled).

Placement itself reuses pymep_landxml_place2.place_landxml_pipes
unchanged, so Place Pipes behaves exactly like the LandXML Model Pipes
button: same transform selection (Settings offsets first, model survey
position fallback), same workset mapping, Marks, diameter snapping and
transaction phases.

This module has NO Revit imports on purpose - the pushbutton wires it to
the placer - so the parsing logic stays testable outside Revit.
"""

import json
import re

PIPES_EXPORT_KIND = "ol-utilities-pipes"
STRUCTURES_EXPORT_KIND = "ol-utilities-structures"
MODEL_EXPORT_KIND = "ol-utilities-model"

# Anything at or below this is 'dashboard-local metres', not a survey grid
# coordinate (real eastings/northings are 6+ digits).
_LOCAL_COORD_LIMIT_M = 50000.0


def _num(v):
    # The None guard matters: IronPython 2.7's float(None) raises
    # SystemError (.NET NullReferenceException), NOT TypeError, so it
    # would sail past a (TypeError, ValueError) except clause. Catch
    # broadly for the same reason.
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


def _first(d, keys):
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return None


_ENDPOINT_KEYS = {
    "start": (("start_easting", "sx", "x1", "e1"),
              ("start_northing", "sy", "y1", "n1"),
              ("start_z_m", "sz", "z1", "start_z")),
    "end":   (("end_easting", "ex", "x2", "e2"),
              ("end_northing", "ey", "y2", "n2"),
              ("end_z_m", "ez", "z2", "end_z")),
}


def _endpoint(row, which):
    """Return (e, n, z) in metres for 'start'/'end', or None when the row
    has no readable coordinates. Missing z reads as 0.0."""
    nest = row.get(which)
    if isinstance(nest, dict):
        e = _num(_first(nest, ("easting", "e", "x")))
        n = _num(_first(nest, ("northing", "n", "y")))
        z = _num(_first(nest, ("z_m", "z", "elevation_m", "elev_m")))
        if e is not None and n is not None:
            return e, n, (z if z is not None else 0.0)
    ek, nk, zk = _ENDPOINT_KEYS[which]
    e = _num(_first(row, ek))
    n = _num(_first(row, nk))
    z = _num(_first(row, zk))
    if e is not None and n is not None:
        return e, n, (z if z is not None else 0.0)
    return None


def _dia_mm(row):
    v = _num(_first(row, ("dia_mm", "diameter_mm")))
    if v is not None and v > 0:
        return v
    v = _num(_first(row, ("dia_m", "diameter_m")))
    if v is not None and v > 0:
        return v * 1000.0
    return None


def _is_circular(row, dia_mm):
    shape = str(row.get("shape") or "").strip().lower()
    if shape in ("cyl", "c", "circ", "circular", "round"):
        return True
    if shape in ("box", "r", "rect", "rectangular"):
        return False
    return dia_mm is not None and dia_mm > 0


def mark_name(name, layer):
    """'Pipe - (2562) (ELV-P1)' with layer 'ELV-P1' -> 'Pipe - 2562'.

    The trailing '(<layer>)' suffix is dropped and leftover parentheses
    removed BEFORE the name reaches the placer, because the LandXML
    placer's clean_mark strips EVERY bracketed group - which would
    collapse all dashboard pipe names to one duplicate Mark."""
    nm = str(name or "").strip()
    if layer:
        suffix = "({})".format(layer)
        if nm.endswith(suffix):
            nm = nm[:-len(suffix)].strip()
    nm = nm.replace("(", "").replace(")", "")
    return re.sub(r"\s{2,}", " ", nm).strip()


def read_pipes_export(path):
    """Read a dashboard pipes export. Returns (meta, rows, notes).

    meta:  dict with source / generated / scope / origin / epsg / count.
    rows:  list of dicts - name (Mark-ready), raw_name, layer, desc,
           material, is_circular, dia_mm, width_m, height_m, length_m,
           slope, and sx/sy/sz/ex/ey/ez in ABSOLUTE survey metres.
    notes: human-readable remarks to log (local-origin shift applied,
           rows skipped, unexpected kind...).
    """
    # Plain binary read + json.loads on the decoded string: io.open's
    # TextIOWrapper + json.load(fileobj) can die with a bare .NET
    # NullReferenceException under IronPython, while this is the exact
    # pattern the (known-working) Settings loader uses. utf-8-sig also
    # swallows a BOM if one ever appears.
    with open(path, "rb") as f:
        raw_bytes = f.read()
    data = json.loads(raw_bytes.decode("utf-8-sig", "replace"))

    kind = data.get("kind")
    if kind == STRUCTURES_EXPORT_KIND:
        raise ValueError(
            "This is a dashboard STRUCTURES export - it has no pipe "
            "geometry. Export MODEL or PIPES from the dashboard, or use "
            "Place Structures for structures.")

    raw = data.get("pipes")
    if raw is None and isinstance(data.get("rows"), list):
        raw = data.get("rows")
    if not isinstance(raw, list) or not raw:
        raise ValueError(
            "No 'pipes' list in this export (kind='{}'). Expected a "
            "dashboard pipes export (kind '{}').".format(
                kind, PIPES_EXPORT_KIND))

    notes = []
    if kind == MODEL_EXPORT_KIND:
        notes.append("Combined MODEL export - reading its 'pipes' list "
                     "({} pipes).".format(len(raw)))
    elif kind != PIPES_EXPORT_KIND:
        notes.append("Export kind is '{}' (expected '{}') - reading its "
                     "'pipes' list anyway.".format(kind, PIPES_EXPORT_KIND))

    rows = []
    skipped_geo = 0
    for p in raw:
        if not isinstance(p, dict):
            skipped_geo += 1
            continue
        a = _endpoint(p, "start")
        b = _endpoint(p, "end")
        if a is None or b is None:
            skipped_geo += 1
            continue
        layer = p.get("layer") or p.get("network") or "(no layer)"
        dia = _dia_mm(p)
        rows.append({
            "raw_name": p.get("name") or "?",
            "name": mark_name(p.get("name"), layer),
            "layer": layer,
            "desc": p.get("desc"),
            "material": p.get("material"),
            "is_circular": _is_circular(p, dia),
            "dia_mm": dia,
            "width_m": _num(_first(p, ("width_m", "length_m_1", "w_m"))),
            "height_m": _num(_first(p, ("height_m", "h_m"))),
            "length_m": _num(p.get("length_m")),
            "slope": _num(p.get("slope")),
            "sx": a[0], "sy": a[1], "sz": a[2],
            "ex": b[0], "ey": b[1], "ez": b[2],
        })
    if skipped_geo:
        notes.append("{} row(s) skipped - no readable start/end "
                     "coordinates.".format(skipped_geo))

    # Dashboard-local coordinates: every |E|/|N| small while the export's
    # origin is a real grid coordinate -> shift by the origin.
    origin = data.get("origin") or {}
    oe = _num(origin.get("easting"))
    on = _num(origin.get("northing"))
    if rows and oe is not None and on is not None:
        m_abs = max(max(abs(r["sx"]), abs(r["sy"]),
                        abs(r["ex"]), abs(r["ey"])) for r in rows)
        if (m_abs <= _LOCAL_COORD_LIMIT_M and
                max(abs(oe), abs(on)) > _LOCAL_COORD_LIMIT_M):
            for r in rows:
                r["sx"] += oe
                r["sy"] += on
                r["ex"] += oe
                r["ey"] += on
            notes.append("Coordinates were dashboard-local - export origin "
                         "E {:.3f}  N {:.3f} added to every point."
                         .format(oe, on))

    meta = {k: data.get(k) for k in
            ("source", "generated", "scope", "origin", "epsg", "count",
             "workset_map")}
    return meta, rows, notes


def placement_rows(rows, only_circular=True, layers=None):
    """Shape reader rows for pymep_landxml_place2.place_landxml_pipes:
    name / network (= layer) / dia_mm / sx,sy,sz / ex,ey,ez. Filters to
    circular rows (and to ``layers`` when given)."""
    lset = set(layers) if layers is not None else None
    out = []
    for r in rows:
        if only_circular and not r["is_circular"]:
            continue
        if lset is not None and r["layer"] not in lset:
            continue
        # Dashboard z = INVERT level (inside bottom of pipe); Revit pipes
        # are CENTERLINE-defined, so without this half-diameter lift every
        # pipe sits D/2 too low (450 mm on a 900 dia main).
        half_m = (float(r["dia_mm"]) / 2000.0) if r["dia_mm"] else 0.0
        out.append({
            "name": r["name"],
            "network": r["layer"],
            "dia_mm": r["dia_mm"],
            "sx": r["sx"], "sy": r["sy"], "sz": r["sz"] + half_m,
            "ex": r["ex"], "ey": r["ey"], "ez": r["ez"] + half_m,
        })
    return out


def distinct_circular_sizes(rows):
    """Sorted distinct circular sizes across the export, shaped for
    pymep_pipesizes.add_sizes_to_segment: dicts with nominal_mm /
    inner_mm / outer_mm (+ count). The dashboard carries one diameter per
    pipe, so nominal == inner == outer (same rule the LandXML reader
    applies when no wall thickness is present)."""
    by_key = {}
    for r in rows:
        if not r["is_circular"] or not r["dia_mm"] or r["dia_mm"] <= 0:
            continue
        key = round(float(r["dia_mm"]), 1)
        if key not in by_key:
            by_key[key] = {"nominal_mm": key, "inner_mm": key,
                           "outer_mm": key, "count": 0}
        by_key[key]["count"] += 1
    return [by_key[k] for k in sorted(by_key)]
