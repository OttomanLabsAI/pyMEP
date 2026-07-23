# -*- coding: utf-8 -*-
"""Place chambers from an OttomanLabs utilities-dashboard export.

The 3D dashboard (GBR1-DCZZ viewer) exports a JSON of the structures that
are currently in view - each with its layer (network), shape (box / cyl),
OS coordinates, rim / sump levels and plan dimensions.

This module drives the Place Structures button (boxes AND cylinders in
one run):

  * the EXPORT is picked first - it decides which shapes need a family
    (a STRUCTS-*.json or a combined MODEL-*.json both work),
  * one FAMILY per shape present, one TYPE per layer (duplicated from
    the picked type and named exactly after the layer, e.g.
    "SW - Phase 1"),
  * each layer is mapped to a WORKSET exactly like Place Pipes - same
    saved map, one confirm when it already covers every layer,
  * every size / level value is written to INSTANCE parameters - the types
    carry no dimensions, they only tag which layer an instance belongs to,
  * placement uses the SAME survey->internal transform as the pipe placer
    (explicit Settings offset first, model project position fallback), at Z=0
    with the offset-from-level driven to the family's vertical origin
    (sump / rim / mid-height, auto-detected per family).
"""

import json
import math
import os

import clr
clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    BuiltInParameter, FilteredElementCollector, FilteredWorksetCollector,
    Level, Transaction, SubTransaction, WorksetKind, XYZ, FamilySymbol,
    Line, ElementTransformUtils, Family,
)
from Autodesk.Revit.DB.Structure import StructuralType

from pymep_revit import safe_name, mm2ft
from pymep_landxml_place2 import (
    HEL18_OFF_E_M, HEL18_OFF_N_M, HEL18_OFF_Z_M, HEL18_ROT_DEG,
    USE_MODEL_GEOREFERENCE, clean_mark, _LIMIT_FT, _el_name,
)
from pymep_structures_place import (
    RIM_PARAM_NAMES, INVERT_PARAM_NAMES,
    _activate, _set_named_param_length_m,
)

__version__ = "2.0"

EXPORT_KIND = "ol-utilities-structures"
MODEL_KIND = "ol-utilities-model"

# Curtain-wall system types (mullion profiles, system panels) are
# FamilySymbol-class but can never be point-placed as a chamber.
_CURTAIN_CAT_IDS = None


def _curtain_cat_ids():
    global _CURTAIN_CAT_IDS
    if _CURTAIN_CAT_IDS is None:
        from Autodesk.Revit.DB import BuiltInCategory
        ids = set()
        for bic in ("OST_CurtainWallMullions", "OST_CurtainWallPanels",
                    "OST_CurtainWallMullionsCut", "OST_Curtain_Systems"):
            try:
                ids.add(int(getattr(BuiltInCategory, bic)))
            except Exception:
                pass
        _CURTAIN_CAT_IDS = ids
    return _CURTAIN_CAT_IDS


# Only these can NEVER be placed at a point - everything else stays,
# including Generic Models, Electrical Fixtures, hosted and face-based.
_BAD_PLACEMENT = ("CurveBased", "CurveBasedDetail", "ViewBased",
                  "CurveDrivenStructural", "Adaptive")


def list_chamber_symbols(doc):
    """list_family_symbols minus curtain-wall system types and
    curve/view/adaptive families that cannot take point placement."""
    from pymep_structures_place import list_family_symbols
    out = []
    for lbl, sym in list_family_symbols(doc):
        try:
            cat = sym.Category
            if cat is not None and cat.Id.IntegerValue in _curtain_cat_ids():
                continue
        except Exception:
            pass
        try:
            if str(sym.Family.FamilyPlacementType) in _BAD_PLACEMENT:
                continue
        except Exception:
            pass    # can't tell - keep it, placement errors will report
        out.append((lbl, sym))
    return out

# Candidate INSTANCE parameter names for the plan dimensions and the
# chamber height. First match wins; every hit is reported so you can see
# exactly which parameter carried each value.
WIDTH_PARAM_NAMES = ["W", "Width", "Internal Width", "Chamber Width",
                     "Plan Width", "Breadth"]
LENGTH_PARAM_NAMES = ["L", "Length", "Internal Length", "Chamber Length",
                      "Plan Length"]
DIA_PARAM_NAMES = ["DIA", "Dia", "Diameter", "Internal Diameter",
                   "Chamber Diameter", "Nominal Diameter", "D", "OD"]
HEIGHT_PARAM_NAMES = ["H", "Height", "Chamber Depth", "Internal Depth",
                      "Chamber Height", "Depth"]
LAYER_PARAM_NAMES = ["Layer", "Network", "System Name"]


def _say(log, m):
    if log is not None:
        log(m)


# ---------------------------------------------------------------------------
# export reading
# ---------------------------------------------------------------------------
def read_export(path):
    """Read a dashboard structures export. Returns (meta, rows).

    meta: dict with source / generated / scope / origin / epsg.
    rows: list of dicts - name, layer, shape ('box'|'cyl'), x (easting m),
          y (northing m), z_m (rim, for the transform), rim_m, sump_m,
          depth_m, length_m, width_m, dia_m, material, desc.
    """
    # Binary read + json.loads (not io.open + json.load): the TextIOWrapper
    # path can raise a bare .NET NullReferenceException under IronPython.
    with open(path, "rb") as f:
        raw_bytes = f.read()
    data = json.loads(raw_bytes.decode("utf-8-sig", "replace"))
    if data.get("kind") not in (EXPORT_KIND, MODEL_KIND):
        raise ValueError(
            "Not a dashboard structures or model export (kind='{}', "
            "expected '{}' or '{}'). Use the Export model / Export "
            "structs button in the 3D dashboard.".format(
                data.get("kind"), MODEL_KIND, EXPORT_KIND))
    rows = []
    for s in data.get("structures", []):
        rim = s.get("rim_m")
        sump = s.get("sump_m")
        z = s.get("z_m")
        if z is None:
            z = sump if sump is not None else (rim if rim is not None else 0.0)
        rows.append({
            "name": s.get("name") or "?",
            "layer": s.get("layer") or "(no layer)",
            "shape": s.get("shape"),
            "x": float(s["easting"]),
            "y": float(s["northing"]),
            "z_m": float(z),
            "rot_deg": float(s.get("rotation_deg") or 0.0),
            "rim_m": rim,
            "sump_m": s.get("sump_m"),
            "depth_m": s.get("depth_m"),
            "length_m": s.get("length_m"),
            "width_m": s.get("width_m"),
            "dia_m": s.get("dia_m"),
            "material": s.get("material"),
            "desc": s.get("desc"),
        })
    meta = {k: data.get(k) for k in
            ("source", "generated", "scope", "origin", "epsg", "count",
             "workset_map")}
    return meta, rows


def rows_by_layer(rows):
    out = {}
    for r in rows:
        out.setdefault(r["layer"], []).append(r)
    return out


# ---------------------------------------------------------------------------
# family parameter probing (for the L/W/H/DIA mapping prompts)
# ---------------------------------------------------------------------------
def probe_instance_param_names(doc, symbol):
    """Names of the writable, Double-storage INSTANCE parameters of
    ``symbol`` - found by creating a temporary instance inside a
    transaction that is rolled back (nothing stays in the model).
    Falls back to reading an existing instance of the same type."""
    names = set()

    def _harvest(inst):
        for p in inst.Parameters:
            try:
                if p.IsReadOnly:
                    continue
                if str(p.StorageType) != "Double":
                    continue
                nm = p.Definition.Name
                if nm:
                    names.add(nm)
            except Exception:
                continue

    lvl = None
    for l in FilteredElementCollector(doc).OfClass(Level):
        lvl = l
        break
    t = Transaction(doc, "Probe family params")
    t.Start()
    try:
        try:
            _activate(doc, symbol)
        except Exception:
            pass
        inst = None
        if lvl is not None:
            try:
                inst = doc.Create.NewFamilyInstance(
                    XYZ(0, 0, 0), symbol, lvl, StructuralType.NonStructural)
            except Exception:
                inst = None
        if inst is None:
            inst = doc.Create.NewFamilyInstance(
                XYZ(0, 0, 0), symbol, StructuralType.NonStructural)
        try:
            doc.Regenerate()
        except Exception:
            pass
        _harvest(inst)
    except Exception:
        pass
    finally:
        try:
            t.RollBack()
        except Exception:
            pass

    if not names:
        try:
            from Autodesk.Revit.DB import FamilyInstance
            for fi in FilteredElementCollector(doc).OfClass(FamilyInstance):
                try:
                    if fi.Symbol.Id == symbol.Id:
                        _harvest(fi)
                        break
                except Exception:
                    continue
        except Exception:
            pass
    return sorted(names)


def detect_vertical_anchor(doc, symbol, h_param_name, test_h_m=3.0):
    """Where the family's insertion point sits vertically: drive its
    height parameter to ``test_h_m`` on a throwaway instance (inside a
    transaction that is ALWAYS rolled back - nothing stays in the model),
    then read the bounding box around the placement point.

    Returns (anchor, detail):
      ('base',   detail) - bbox ~ [0, +H]      family grows UP
      ('top',    detail) - bbox ~ [-H, 0]      family grows DOWN
      ('center', detail) - bbox ~ [-H/2, +H/2] origin at mid-height
      (None,     reason) - could not tell (param not drivable, no
                           bounding box, creation failed, ...)
    """
    lvl = None
    for l in FilteredElementCollector(doc).OfClass(Level):
        lvl = l
        break
    t = Transaction(doc, "Probe family vertical origin")
    t.Start()
    try:
        try:
            _activate(doc, symbol)
        except Exception:
            pass
        inst = None
        if lvl is not None:
            try:
                inst = doc.Create.NewFamilyInstance(
                    XYZ(0, 0, 0), symbol, lvl, StructuralType.NonStructural)
            except Exception:
                inst = None
        if inst is None:
            inst = doc.Create.NewFamilyInstance(
                XYZ(0, 0, 0), symbol, StructuralType.NonStructural)

        drove = False
        for p in inst.GetParameters(h_param_name):
            try:
                if p.IsReadOnly or str(p.StorageType) != "Double":
                    continue
                if p.Set(mm2ft(test_h_m * 1000.0)):
                    drove = True
            except Exception:
                continue
        if not drove:
            return (None, "height parameter '{}' is not drivable on an "
                          "instance".format(h_param_name))
        try:
            doc.Regenerate()
        except Exception:
            pass
        bb = inst.get_BoundingBox(None)
        if bb is None:
            return (None, "the probe instance has no bounding box")
        base_z = 0.0
        try:
            base_z = inst.Location.Point.Z
        except Exception:
            pass
        bot = (bb.Min.Z - base_z) * 0.3048
        top = (bb.Max.Z - base_z) * 0.3048
        detail = "bbox {:+.2f}..{:+.2f} m at H={:.1f} m".format(
            bot, top, test_h_m)
        tol = max(0.15 * test_h_m, 0.08)
        if abs(bot) <= tol and abs(top - test_h_m) <= tol:
            return ("base", detail)
        if abs(bot + test_h_m) <= tol and abs(top) <= tol:
            return ("top", detail)
        if (abs(bot + test_h_m / 2.0) <= tol
                and abs(top - test_h_m / 2.0) <= tol):
            return ("center", detail)
        return (None, "unclassifiable " + detail)
    except Exception as ex:
        return (None, "probe failed: {}".format(ex))
    finally:
        try:
            t.RollBack()
        except Exception:
            pass


def anchor_z(anchor, rim_m, sump_m, z_m):
    """The level (m) the family ORIGIN must sit at for one row, given
    where the family's insertion point is vertically: 'base' (grows up)
    -> sump, 'top' (grows down) -> rim, 'center' -> mid-height. Missing
    sump falls back to the row's z_m, missing rim to the sump."""
    sump = sump_m if sump_m is not None else z_m
    rim = rim_m if rim_m is not None else sump
    if anchor == "top":
        return float(rim)
    if anchor == "center":
        return (float(rim) + float(sump)) / 2.0
    return float(sump)


# ---------------------------------------------------------------------------
# per-layer types
# ---------------------------------------------------------------------------
_BAD_TYPE_CHARS = u"\\:{}[]|;<>?`~\n\r\t"


def type_name_for_layer(layer):
    name = u"".join((u"-" if ch in _BAD_TYPE_CHARS else ch) for ch in layer)
    name = name.strip()
    return name or u"Layer"


def ensure_layer_types(doc, base_symbol, layers, log=None):
    """One FamilySymbol per layer, named after the layer, duplicated from
    ``base_symbol``. Existing types of the same family are reused.
    Returns {layer: FamilySymbol}. Runs its own transaction."""
    fam = base_symbol.Family
    existing = {}
    for sid in fam.GetFamilySymbolIds():
        s = doc.GetElement(sid)
        nm = _el_name(s)
        if nm:
            existing[nm] = s

    out = {}
    made = []
    t = Transaction(doc, "Dashboard layer types")
    t.Start()
    try:
        for layer in sorted(set(layers)):
            tname = type_name_for_layer(layer)
            sym = existing.get(tname)
            if sym is None:
                try:
                    sym = base_symbol.Duplicate(tname)
                    existing[tname] = sym
                    made.append(tname)
                except Exception:
                    # name raced into existence / duplicate - re-scan
                    for sid in fam.GetFamilySymbolIds():
                        s = doc.GetElement(sid)
                        if _el_name(s) == tname:
                            sym = s
                            break
            if sym is None:
                raise RuntimeError(
                    "Could not create or find type '{}'.".format(tname))
            out[layer] = sym
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    _say(log, "Types: **{}** created ({}), **{}** reused.".format(
        len(made), ", ".join(made) or "-", len(out) - len(made)))
    return out


def _set_all_named_length_m(inst, names, value_m):
    """Like _set_named_param_length_m, but when a name matches it writes
    EVERY writable Double parameter carrying that name - family parameter
    and same-named project parameter both receive the value, so duplicated
    definitions can never disagree. Returns the matched name or None."""
    for nm in names:
        try:
            plist = inst.GetParameters(nm)
        except Exception:
            plist = None
        if not plist:
            continue
        wrote = False
        for p in plist:
            try:
                if p is None or p.IsReadOnly:
                    continue
                if str(p.StorageType) != "Double":
                    continue
                p.Set(mm2ft(value_m * 1000.0))
                wrote = True
            except Exception:
                pass
        if wrote:
            return nm
    return None


# ---------------------------------------------------------------------------
# transform solving (validated BEFORE anything is created)
# ---------------------------------------------------------------------------
def solve_points(doc, rows, log=None, force_offset=None,
                 prefer_model=None):
    """Solve the survey->internal transform for ``rows``: the Settings
    offset first, then the model's own project position. Every attempt is
    logged with the resulting distance. Returns (pts, mode, offsets) where
    pts is [(plan XYZ at Z=0, z_internal_ft, row), ...] and offsets is
    (off_e_m, off_n_m, off_z_m, rot_deg). Raises with the full numbers if
    nothing lands within the sanity limit - in that case NOTHING has been
    created or modified."""
    if not rows:
        raise ValueError("No structures to place.")
    try:
        from pymep_config import get_landxml_survey_transform
        s_off = get_landxml_survey_transform()
    except Exception:
        s_off = (HEL18_OFF_E_M, HEL18_OFF_N_M, HEL18_OFF_Z_M, HEL18_ROT_DEG)

    cx = sum(r["x"] for r in rows) / len(rows)
    cy = sum(r["y"] for r in rows) / len(rows)
    _say(log, "Export centroid: E **{:.1f}**  N **{:.1f}**".format(cx, cy))

    loc = doc.ActiveProjectLocation

    def transform_all(fn):
        out = []
        m_abs = 0.0
        for r in rows:
            p = fn(r["x"], r["y"], r["z_m"])
            out.append((XYZ(p.X, p.Y, 0.0), p.Z, r))
            mm = max(abs(p.X), abs(p.Y))
            if mm > m_abs:
                m_abs = mm
        return out, m_abs

    def explicit_fn(e0, n0, z0, rd):
        th = math.radians(rd)
        c, s = math.cos(th), math.sin(th)

        def fn(e_m, n_m, z_m):
            dx = e_m - e0
            dy = n_m - n0
            return XYZ((dx * c - dy * s) / 0.3048,
                       (dx * s + dy * c) / 0.3048,
                       (z_m - z0) / 0.3048)
        return fn

    candidates = [("Settings offset", s_off)]
    pp = None
    try:
        pos = loc.GetProjectPosition(XYZ.Zero)
        pp = (pos.EastWest * 0.3048, pos.NorthSouth * 0.3048,
              pos.Elevation * 0.3048, math.degrees(pos.Angle))
        candidates.append(("model project position", pp))
    except Exception:
        pass

    if prefer_model and pp is not None:
        candidates.reverse()

    if force_offset is not None:
        candidates = [("export origin (site at internal origin)",
                       (float(force_offset[0]), float(force_offset[1]),
                        float(force_offset[2]), float(force_offset[3])))]

    tried = []
    for name, off in candidates:
        pts, m = transform_all(explicit_fn(off[0], off[1], off[2], off[3]))
        km = m * 0.0003048
        tried.append((name, off, km))
        _say(log, "{}: E {:.3f}  N {:.3f}  rot {:.2f} deg -> max |XY| "
                  "**{:.2f} km**".format(name, off[0], off[1], off[3], km))
        if m <= _LIMIT_FT:
            _say(log, "Transform used: **{}**".format(name))
            if abs(off[3]) > 0.01:
                _say(log, "(a **{:.2f} deg** plan rotation is applied to "
                          "every instance)".format(off[3]))
            return pts, name, off

    lines = ["No transform brings the structures within {:.1f} km of the "
             "model origin - NOTHING was created.".format(
                 _LIMIT_FT * 0.0003048),
             "Export centroid: E {:.1f}  N {:.1f}".format(cx, cy)]
    for name, off, km in tried:
        lines.append("  {}: E {:.3f}  N {:.3f}  rot {:.2f} -> {:.1f} km"
                     .format(name, off[0], off[1], off[3], km))
    if pp is not None:
        lines.append("Fix: Settings > Pipes-Coordinates should hold the OS "
                     "coordinates of this model's internal origin. The "
                     "model itself reports E {:.3f}  N {:.3f}  rot {:.2f} "
                     "deg - enter those.".format(pp[0], pp[1], pp[3]))
    else:
        lines.append("Fix: set Settings > Pipes-Coordinates E/N to the OS "
                     "coordinates of this model's internal origin (near "
                     "E {:.0f}  N {:.0f} if the origin sits on the site)."
                     .format(cx, cy))
    raise RuntimeError("\n".join(lines))


# ---------------------------------------------------------------------------
# placement
# ---------------------------------------------------------------------------
def place_dashboard_structures(doc, rows, symbols_by_layer, host_level_name,
                               workset_name="", log=None, param_map=None,
                               pts_info=None, layer_workset_map=None,
                               system_type_map=None):
    """Place every row with its layer's type. Sizes and levels are written
    to INSTANCE parameters only. ``layer_workset_map`` ({layer: workset
    name, '' = active}) wins over the single ``workset_name``.
    ``system_type_map`` ({lower layer: PipingSystemType ElementId}) makes
    each instance take its layer-named system type - the same automation
    as the pipes; families with no writable system parameter are counted
    and reported, never failed. Returns (created, failed, mode)."""
    if not rows:
        raise ValueError("No structures to place.")

    lvl = None
    for l in FilteredElementCollector(doc).OfClass(Level):
        if _el_name(l) == host_level_name:
            lvl = l
            break
    if lvl is None:
        raise ValueError("Level '{}' not found.".format(host_level_name))

    # ---- transform: pre-solved by solve_points (or solved now) ------------
    if pts_info is None:
        pts_info = solve_points(doc, rows, log=log)
    pts, mode, offs = pts_info

    # ---- worksets: one per layer (map wins over the single name) -----------
    ws_by_layer = {}
    if doc.IsWorkshared:
        wanted = {}
        if layer_workset_map:
            wanted = dict(layer_workset_map)
        elif workset_name:
            for lay in symbols_by_layer:
                wanted[lay] = workset_name
        if any((v or "").strip() for v in wanted.values()):
            ids_by_name = {}
            for ws in FilteredWorksetCollector(doc).OfKind(
                    WorksetKind.UserWorkset):
                ids_by_name[ws.Name.strip()] = ws.Id
            for lay, wn in wanted.items():
                wid = ids_by_name.get((wn or "").strip())
                if wid is not None:
                    ws_by_layer[lay] = wid

    # ---- place -------------------------------------------------------------
    created = 0
    failed = 0
    placed = []

    t = Transaction(doc, "Place dashboard structures")
    t.Start()
    for sym in set(symbols_by_layer.values()):
        try:
            _activate(doc, sym)
        except Exception:
            pass
    errors = {}
    rot_base = math.radians(offs[3] or 0.0)
    for (p, pz, r) in pts:
        sym = symbols_by_layer[r["layer"]]
        inst = None
        try:
            inst = doc.Create.NewFamilyInstance(
                p, sym, lvl, StructuralType.NonStructural)
        except Exception as ex1:
            try:
                # some families refuse the level overload - place free,
                # the level association is cosmetic for these placeholders
                inst = doc.Create.NewFamilyInstance(
                    p, sym, StructuralType.NonStructural)
            except Exception as ex2:
                msg = "{} / {}".format(ex1, ex2)[:160]
                errors[msg] = errors.get(msg, 0) + 1
        if inst is None:
            failed += 1
            continue
        # plan rotation: survey transform rotation + per-structure rotation
        rot = rot_base + math.radians(r.get("rot_deg", 0.0))
        if abs(rot) > 1e-9:
            try:
                axis = Line.CreateBound(p, XYZ(p.X, p.Y, p.Z + 10.0))
                ElementTransformUtils.RotateElement(doc, inst.Id, axis, rot)
            except Exception:
                pass
        try:
            ws_id = ws_by_layer.get(r["layer"])
            if ws_id is not None:
                wp = inst.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM)
                if wp is not None and not wp.IsReadOnly:
                    wp.Set(ws_id.IntegerValue)
        except Exception:
            pass
        created += 1
        placed.append((inst, r, p, pz))
    t.Commit()
    _say(log, "Created **{}** instances (failed {}).".format(created, failed))
    if errors:
        _say(log, "Placement errors (family '{}'):".format(
            safe_name(list(symbols_by_layer.values())[0])))
        for msg, n in sorted(errors.items(), key=lambda kv: -kv[1])[:3]:
            _say(log, "  - x{}: {}".format(n, msg))

    # ---- instance params, isolated pass ------------------------------------
    hits = {}

    def _hit(name):
        hits[name] = hits.get(name, 0) + 1

    def _try_text(inst, names, value):
        for nm in names:
            try:
                pp = inst.LookupParameter(nm)
                if pp is not None and not pp.IsReadOnly:
                    pp.Set(value)
                    return nm
            except Exception:
                pass
        return None

    mark_set = 0
    offset_set = 0
    moved = 0
    sys_set = 0
    sys_ro = 0

    def _try_system_type(inst, r):
        """True when the layer-named system type stuck on the instance."""
        stid = system_type_map.get((r.get("layer") or "").strip().lower())
        if stid is None:
            return False
        bip = getattr(BuiltInParameter, "RBS_PIPING_SYSTEM_TYPE_PARAM",
                      None)
        if bip is not None:
            try:
                p = inst.get_Parameter(bip)
                if p is not None and not p.IsReadOnly:
                    p.Set(stid)
                    return True
            except Exception:
                pass
        try:
            p = inst.LookupParameter("System Type")
            if p is not None and not p.IsReadOnly \
                    and str(p.StorageType) == "ElementId":
                p.Set(stid)
                return True
        except Exception:
            pass
        return False
    if placed:
        t2 = Transaction(doc, "Dashboard structure params")
        t2.Start()
        try:
            doc.Regenerate()
        except Exception:
            pass
        lvl_elev = lvl.Elevation

        def _names_for(key, fallback):
            if param_map is None:
                return fallback
            nm = param_map.get(key)
            return [nm] if nm else []

        H_NAMES = _names_for("H", HEIGHT_PARAM_NAMES)
        L_NAMES = _names_for("L", LENGTH_PARAM_NAMES)
        W_NAMES = _names_for("W", WIDTH_PARAM_NAMES)
        D_NAMES = _names_for("DIA", DIA_PARAM_NAMES)
        for (inst, r, p, pz) in placed:
            sub = SubTransaction(doc)
            try:
                sub.Start()
                try:
                    mp = inst.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
                    if mp is not None and not mp.IsReadOnly:
                        mp.Set(clean_mark(r["name"]))
                        mark_set += 1
                except Exception:
                    pass
                # Offset from level = structure Z (sump) minus the level
                # elevation - with your Level 0 at 0 this is simply the Z.
                off_ft = pz - lvl_elev
                ok = False
                for bip in (BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM,
                            BuiltInParameter.INSTANCE_ELEVATION_PARAM):
                    try:
                        op = inst.get_Parameter(bip)
                        if op is not None and not op.IsReadOnly:
                            if abs(op.AsDouble() - off_ft) > 1e-6:
                                op.Set(off_ft)
                            offset_set += 1
                            ok = True
                            break
                    except Exception:
                        pass
                if not ok and abs(off_ft) > 1e-6:
                    # family exposes no offset param - physically move it up
                    try:
                        ElementTransformUtils.MoveElement(
                            doc, inst.Id, XYZ(0, 0, off_ft))
                        moved += 1
                    except Exception:
                        pass
                if r.get("rim_m") is not None:
                    nm = _set_all_named_length_m(inst, RIM_PARAM_NAMES,
                                                 r["rim_m"])
                    if nm:
                        _hit(nm)
                if r.get("sump_m") is not None:
                    nm = _set_all_named_length_m(inst, INVERT_PARAM_NAMES,
                                                 r["sump_m"])
                    if nm:
                        _hit(nm)
                if r.get("depth_m") is not None:
                    nm = _set_all_named_length_m(inst, H_NAMES,
                                                 r["depth_m"])
                    if nm:
                        _hit(nm)
                if r["shape"] == "box":
                    if r.get("length_m") is not None:
                        nm = _set_all_named_length_m(
                            inst, L_NAMES, r["length_m"])
                        if nm:
                            _hit(nm)
                    if r.get("width_m") is not None:
                        nm = _set_all_named_length_m(
                            inst, W_NAMES, r["width_m"])
                        if nm:
                            _hit(nm)
                else:
                    if r.get("dia_m"):
                        nm = _set_all_named_length_m(
                            inst, D_NAMES, r["dia_m"])
                        if nm:
                            _hit(nm)
                nm = _try_text(inst, LAYER_PARAM_NAMES, r["layer"])
                if nm:
                    _hit(nm)
                if system_type_map:
                    if _try_system_type(inst, r):
                        sys_set += 1
                    else:
                        sys_ro += 1
                if r.get("desc"):
                    try:
                        cp = inst.get_Parameter(
                            BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
                        if cp is not None and not cp.IsReadOnly:
                            cp.Set(r["desc"])
                    except Exception:
                        pass
                sub.Commit()
            except Exception:
                try:
                    sub.RollBack()
                except Exception:
                    pass
        t2.Commit()

    if system_type_map and (sys_set or sys_ro):
        if sys_set:
            _say(log, "Layer-named system type set on **{}** instance(s)"
                      "{}.".format(sys_set,
                                   " ({} had no writable system "
                                   "parameter)".format(sys_ro)
                                   if sys_ro else ""))
        else:
            _say(log, "This family exposes no writable system-type "
                      "parameter - the layer name still went to the "
                      "text parameters above.")
    _say(log, "Hosted on the level at Z=0; 'Offset from level' driven to "
              "the structure Z on **{}** (physically moved: {}). Mark set "
              "on **{}**.".format(offset_set, moved, mark_set))
    if placed:
        f2m = 0.3048
        xs = [pp.X for (_i, _r, pp, _z) in placed]
        ys = [pp.Y for (_i, _r, pp, _z) in placed]
        zs = [zz for (_i, _r, _pp, zz) in placed]
        _say(log, "Internal position span (m): X {:.1f} .. {:.1f}   "
                  "Y {:.1f} .. {:.1f}   Z {:.2f} .. {:.2f}".format(
                      min(xs)*f2m, max(xs)*f2m, min(ys)*f2m, max(ys)*f2m,
                      min(zs)*f2m, max(zs)*f2m))
        cx = sum(xs)/len(xs)*f2m
        cy = sum(ys)/len(ys)*f2m
        _say(log, "Centroid distance from internal origin: **{:.0f} m** "
                  "(X {:.0f}, Y {:.0f}).".format(
                      (cx*cx+cy*cy)**0.5, cx, cy))
    if hits:
        _say(log, "Instance params written: " + ", ".join(
            "'%s' x%d" % (k, v)
            for k, v in sorted(hits.items(), key=lambda kv: -kv[1])))
    else:
        _say(log, "NOTE: no named size/level parameters matched this family - "
                  "tell me its parameter names and I'll add them to the "
                  "candidate lists in pymep_dashboard.py.")
    return created, failed, mode, [i for (i, _r, _p, _z) in placed]


# ---------------------------------------------------------------------------
# shared button flow
# ---------------------------------------------------------------------------
def _pick_family_symbol(doc, forms, log, what):
    """Family picker with the load-an-.rfa fallback loop (legacy list
    flow, kept for reuse). Returns (label, FamilySymbol)."""
    from pymep_structures_place import list_family_symbols as _raw_syms

    syms = list_chamber_symbols(doc)
    if not syms:
        syms = _raw_syms(doc)
    if not syms:
        forms.alert("This project contains zero placeable family types - "
                    "load a family first.", exitscript=True)

    class SymOpt(object):
        def __init__(self, lbl, sym):
            self.sym = sym
            self.name = lbl

    sym_pick = forms.SelectFromList.show(
        [SymOpt(lbl, sym) for lbl, sym in syms],
        title="Pick the family for {}".format(what),
        button_name="Use this family", multiselect=False, name_attr="name")
    if not sym_pick:
        forms.alert("No family picked.", exitscript=True)
    log("Family for {}: **{}**".format(what, sym_pick.name))
    return sym_pick.name, sym_pick.sym


def _map_params(doc, forms, log, base_symbol, fam_label, keys):
    """The L/W/H/DIA -> instance-parameter mapping prompts for one
    family. Returns the param_map dict (or None when unprobeable)."""
    pnames = probe_instance_param_names(doc, base_symbol)
    param_map = None
    if pnames:
        param_map = {}
        SKIP = "(skip - do not write this one)"
        for k in keys:
            ku = k.upper()
            ordered = sorted(pnames, key=lambda n: (
                0 if n.upper() == ku else (1 if ku in n.upper() else 2),
                n.lower()))
            pick = forms.SelectFromList.show(
                ordered + [SKIP],
                title="Map '{}' -> which instance parameter of {}?".format(
                    k, fam_label),
                button_name="Map {}".format(k), multiselect=False)
            if not pick:
                forms.alert("Mapping cancelled.", exitscript=True)
            param_map[k] = None if pick == SKIP else pick
        log("Param map ({}): ".format(fam_label) + ", ".join(
            "{} -> {}".format(k, param_map[k] or "(skip)") for k in keys))
    else:
        log("Could not probe the family's instance parameters - falling "
            "back to automatic name matching (L/W/H/DIA tried first).")
    return param_map


def _resolve_anchor(doc, forms, log, base_symbol, fam_label, param_map):
    """detect_vertical_anchor + the manual fallback dialog. Never
    ASSUMES - the Generic Box / Cylinder families insert at the TOP and
    grow down, which used to hang every chamber one height too low."""
    anchor = None
    h_name = param_map.get("H") if param_map else None
    if h_name:
        anchor, why = detect_vertical_anchor(doc, base_symbol, h_name)
        if anchor:
            log("Vertical origin detected for {}: **{}** ({})".format(
                fam_label, anchor, why))
        else:
            log("Vertical origin probe inconclusive for {}: {}".format(
                fam_label, why))
    if anchor is None:
        pick = forms.alert(
            "Where is the insertion point of '{}' vertically?\n\n"
            "Base - the family grows UP from its origin\n"
            "Top - the family grows DOWN from its origin (typical for "
            "chambers modelled from cover level)\n"
            "Mid-height - origin at half height".format(fam_label),
            title="Family vertical origin",
            options=["Base - grows up", "Top - grows down",
                     "Mid-height", "Cancel"])
        if not pick or pick == "Cancel":
            forms.alert("Cancelled.", exitscript=True)
        anchor = {"Base - grows up": "base",
                  "Top - grows down": "top",
                  "Mid-height": "center"}[pick]
        log("Vertical origin (picked by user) for {}: **{}**".format(
            fam_label, anchor))
    return anchor


def list_chamber_symbols_by_category(doc):
    """{category name: [(label, symbol), ...]} of every point-placeable
    family type, for the category-first picker."""
    cats = {}
    for lbl, sym in list_chamber_symbols(doc):
        try:
            cat = sym.Category
            cname = cat.Name if cat is not None else "(no category)"
        except Exception:
            cname = "(no category)"
        cats.setdefault(cname, []).append((lbl, sym))
    return cats


def run_place(shape=None):
    """The Place Structures button: ONE setup window (export, layers +
    per-layer worksets, category-first family pickers, layer-named
    system type option), then the parameter-mapping prompts, vertical
    origin probe, transform and placement. ``shape`` optionally
    restricts to 'box' / 'cyl' (legacy entry points)."""
    from pyrevit import revit, forms, script
    from pymep_config import (get_pipe_host_level_name,
                              get_dashboard_layer_workset_map,
                              save_dashboard_layer_workset_map)
    from pymep_landxml_place2 import list_worksets
    from pymep_log import Logger

    output = script.get_output()
    log = Logger(output, "DashboardPlaceStructures")
    doc = revit.doc

    log("### Place chambers from a dashboard export")
    log("pymep_dashboard **v{}**".format(__version__))

    saved_map = get_dashboard_layer_workset_map()
    worksets = list_worksets(doc)
    ACTIVE = "(active workset)"
    xaml_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "pymep_place_structures.xaml")

    from System.Collections import ArrayList, Hashtable

    SKIP_PARAM = "(skip - do not write)"
    ORIGIN_ITEMS = ["Auto-detect (probe the family)", "Base - grows up",
                    "Top - grows down", "Mid-height"]
    ORIGIN_VALUES = {"Base - grows up": "base", "Top - grows down": "top",
                     "Mid-height": "center"}
    COORD_MODEL = "Model's Revit coordinates (Manage > Coordinates)"
    COORD_SETTINGS = "pyMEP Settings offsets (E/N/Z/rotation)"
    from pymep_landxml_place2 import model_survey_position
    _mp = model_survey_position(doc)
    has_georef = _mp is not None and (abs(_mp[0]) > 1e-6
                                      or abs(_mp[1]) > 1e-6)

    class PlaceWindow(forms.WPFWindow):

        def __init__(self):
            forms.WPFWindow.__init__(self, xaml_path)
            self.result = None
            self.meta = None
            self.rows = None
            self.path = None
            self._order = []          # layer names in list order
            self._cats = {}           # category -> [(label, sym)]
            self._box_syms = []
            self._cyl_syms = []
            self.CmbWorkset.Items.Clear()
            self.CmbWorkset.Items.Add(ACTIVE)
            for w in worksets:
                self.CmbWorkset.Items.Add(w)
            self.CmbWorkset.SelectedIndex = 0
            for combo in (self.CmbBoxOrigin, self.CmbCylOrigin):
                combo.Items.Clear()
                for o in ORIGIN_ITEMS:
                    combo.Items.Add(o)
                combo.SelectedIndex = 0
            self.CmbCoords.Items.Clear()
            self.CmbCoords.Items.Add(COORD_MODEL)
            self.CmbCoords.Items.Add(COORD_SETTINGS)
            self.CmbCoords.SelectedIndex = 0 if has_georef else 1
            self._fill_categories()
            self._restore_defaults()
            self.StatusText.Text = "Pick a dashboard MODEL or STRUCTS " \
                                   "export to begin."

        # ---- export ----
        def on_browse(self, sender, args):
            path = forms.pick_file(
                file_ext="json",
                title="Pick a dashboard MODEL or STRUCTS export (.json)")
            if not path:
                return
            try:
                meta, rows = read_export(path)
            except Exception as ex:
                forms.alert("Could not read the export:\n\n{}".format(ex))
                return
            if shape:
                rows = [r for r in rows if r["shape"] == shape]
            else:
                rows = [r for r in rows if r["shape"] in ("box", "cyl")]
            if not rows:
                forms.alert("The export contains no placeable structures "
                            "(scope was: {}).".format(meta.get("scope")))
                return
            self.path = path
            self.meta = meta
            self.rows = rows
            self.TxtExport.Text = path
            export_map = meta.get("workset_map")
            export_map = export_map if isinstance(export_map, dict) else {}
            tally = {}
            for r in rows:
                t = tally.setdefault(r["layer"], {"box": 0, "cyl": 0})
                t[r["shape"]] += 1
            items = ArrayList()
            self._order = sorted(tally, key=lambda s: s.lower())
            for lay in self._order:
                ws = str(export_map.get(lay) or saved_map.get(lay) or "")
                if ws and worksets and ws not in worksets:
                    ws = ""
                row = Hashtable()
                row["layer"] = lay
                row["box"] = str(tally[lay]["box"] or "")
                row["cyl"] = str(tally[lay]["cyl"] or "")
                row["workset"] = ws
                items.Add(row)
            self.LstLayers.ItemsSource = items
            self.LstLayers.SelectAll()
            self.StatusText.Text = ("{} structures across {} layers - "
                                    "{}".format(len(rows), len(self._order),
                                                meta.get("source") or ""))

        # ---- worksets ----
        def on_assign_ws(self, sender, args):
            pick = self.CmbWorkset.SelectedItem
            if pick is None or self.LstLayers.ItemsSource is None:
                return
            ws = "" if str(pick) == ACTIVE else str(pick)
            for row in self.LstLayers.SelectedItems:
                row["workset"] = ws
            self.LstLayers.Items.Refresh()

        # ---- families ----
        def _fill_categories(self):
            self._cats = list_chamber_symbols_by_category(doc)
            names = sorted(self._cats, key=lambda s: s.lower())
            for combo in (self.CmbBoxCat, self.CmbCylCat):
                sel = combo.SelectedItem
                combo.Items.Clear()
                for c in names:
                    combo.Items.Add(c)
                if sel is not None and str(sel) in names:
                    combo.SelectedItem = str(sel)

        def _fill_fams(self, cat_combo, fam_combo, store):
            del store[:]
            fam_combo.Items.Clear()
            cname = cat_combo.SelectedItem
            if cname is None:
                return
            for lbl, sym in self._cats.get(str(cname), []):
                store.append(sym)
                fam_combo.Items.Add(lbl)
            if fam_combo.Items.Count:
                fam_combo.SelectedIndex = 0

        def on_box_cat_changed(self, sender, args):
            self._fill_fams(self.CmbBoxCat, self.CmbBoxFam, self._box_syms)

        def on_cyl_cat_changed(self, sender, args):
            self._fill_fams(self.CmbCylCat, self.CmbCylFam, self._cyl_syms)

        # ---- parameter mapping (probed when the family changes) ----
        def _fill_params(self, fam_combo, store, combo_keys):
            sym = None
            i = fam_combo.SelectedIndex
            if 0 <= i < len(store):
                sym = store[i]
            names = probe_instance_param_names(doc, sym) if sym else []
            for combo, key in combo_keys:
                combo.Items.Clear()
                combo.Items.Add(SKIP_PARAM)
                ordered = sorted(names, key=lambda n: (
                    0 if n.upper() == key
                    else (1 if key in n.upper() else 2), n.lower()))
                for n in ordered:
                    combo.Items.Add(n)
                if ordered and (ordered[0].upper() == key
                                or key in ordered[0].upper()):
                    combo.SelectedIndex = 1
                else:
                    combo.SelectedIndex = 0

        def on_box_fam_changed(self, sender, args):
            self._fill_params(self.CmbBoxFam, self._box_syms,
                              [(self.CmbBoxL, "L"), (self.CmbBoxW, "W"),
                               (self.CmbBoxH, "H")])

        def on_cyl_fam_changed(self, sender, args):
            self._fill_params(self.CmbCylFam, self._cyl_syms,
                              [(self.CmbCylDia, "DIA"),
                               (self.CmbCylH, "H")])

        def _param_pick(self, combo):
            v = combo.SelectedItem
            if v is None or str(v) == SKIP_PARAM:
                return None
            return str(v)

        def _origin_pick(self, combo):
            v = combo.SelectedItem
            return ORIGIN_VALUES.get(str(v)) if v is not None else None

        # ---- remembered defaults (last successful Place) ----
        def _combo_map(self):
            return {
                "box_cat": self.CmbBoxCat, "box_fam": self.CmbBoxFam,
                "box_l": self.CmbBoxL, "box_w": self.CmbBoxW,
                "box_h": self.CmbBoxH, "box_origin": self.CmbBoxOrigin,
                "cyl_cat": self.CmbCylCat, "cyl_fam": self.CmbCylFam,
                "cyl_dia": self.CmbCylDia, "cyl_h": self.CmbCylH,
                "cyl_origin": self.CmbCylOrigin,
                "coords": self.CmbCoords,
            }

        def _restore_defaults(self):
            try:
                from pymep_config import load_settings
                d = load_settings().get("place_structs_defaults") or {}
            except Exception:
                d = {}
            if not isinstance(d, dict) or not d:
                return

            def sel(combo, val):
                if not val:
                    return
                for i in range(combo.Items.Count):
                    if str(combo.Items[i]) == val:
                        combo.SelectedIndex = i
                        return
            # categories first (they cascade-fill the family combos),
            # then families (they cascade-probe the params), then the
            # param/origin overrides on top
            for key in ("box_cat", "cyl_cat", "box_fam", "cyl_fam",
                        "box_l", "box_w", "box_h", "box_origin",
                        "cyl_dia", "cyl_h", "cyl_origin", "coords"):
                sel(self._combo_map()[key], d.get(key))
            try:
                if d.get("assign_sys") is not None:
                    self.ChkSystemType.IsChecked = bool(d.get("assign_sys"))
            except Exception:
                pass

        def _save_defaults(self):
            try:
                from pymep_config import load_settings, save_settings
                s = load_settings()
                d = {}
                for key, combo in self._combo_map().items():
                    v = combo.SelectedItem
                    d[key] = str(v) if v is not None else ""
                d["assign_sys"] = bool(self.ChkSystemType.IsChecked)
                s["place_structs_defaults"] = d
                save_settings(s)
            except Exception:
                pass

        def on_load_rfa(self, sender, args):
            rfa = forms.pick_file(file_ext="rfa",
                                  title="Pick the chamber family (.rfa)")
            if not rfa:
                return
            t = Transaction(doc, "Load dashboard family")
            t.Start()
            try:
                ok = doc.LoadFamily(rfa)
                t.Commit()
                self.StatusText.Text = "Loaded {} ({}).".format(
                    rfa, "ok" if ok else "already loaded / unchanged")
            except Exception as ex:
                t.RollBack()
                forms.alert("Could not load:\n{}".format(ex))
                return
            self._fill_categories()

        # ---- bottom bar ----
        def _chosen_symbol(self, fam_combo, store):
            i = fam_combo.SelectedIndex
            if i < 0 or i >= len(store):
                return None, None
            return str(fam_combo.SelectedItem), store[i]

        def on_place(self, sender, args):
            if not self.rows:
                forms.alert("Pick a dashboard export first.")
                return
            chosen = [str(row["layer"])
                      for row in self.LstLayers.SelectedItems]
            if not chosen:
                forms.alert("Select at least one layer in the list.")
                return
            sel_rows = [r for r in self.rows if r["layer"] in set(chosen)]
            shapes_here = set(r["shape"] for r in sel_rows)
            fams = {}
            if "box" in shapes_here:
                lbl, sym = self._chosen_symbol(self.CmbBoxFam,
                                               self._box_syms)
                if sym is None:
                    forms.alert("Pick a category + family for the BOX "
                                "chambers.")
                    return
                pm = {"L": self._param_pick(self.CmbBoxL),
                      "W": self._param_pick(self.CmbBoxW),
                      "H": self._param_pick(self.CmbBoxH)}
                if self.CmbBoxH.Items.Count <= 1:
                    pm = None    # probe found nothing - auto name match
                fams["box"] = (lbl, sym, pm,
                               self._origin_pick(self.CmbBoxOrigin))
            if "cyl" in shapes_here:
                lbl, sym = self._chosen_symbol(self.CmbCylFam,
                                               self._cyl_syms)
                if sym is None:
                    forms.alert("Pick a category + family for the "
                                "CYLINDRICAL chambers.")
                    return
                pm = {"DIA": self._param_pick(self.CmbCylDia),
                      "H": self._param_pick(self.CmbCylH)}
                if self.CmbCylH.Items.Count <= 1:
                    pm = None    # probe found nothing - auto name match
                fams["cyl"] = (lbl, sym, pm,
                               self._origin_pick(self.CmbCylOrigin))
            ws_map = {}
            for row in self.LstLayers.ItemsSource:
                if str(row["layer"]) in set(chosen):
                    ws_map[str(row["layer"])] = str(row["workset"] or "")
            self.result = {
                "path": self.path, "meta": self.meta, "rows": sel_rows,
                "layers": chosen, "ws_map": ws_map, "fams": fams,
                "assign_sys": bool(self.ChkSystemType.IsChecked),
                "prefer_model":
                    str(self.CmbCoords.SelectedItem) == COORD_MODEL,
            }
            self._save_defaults()
            self.Close()

        def on_cancel(self, sender, args):
            self.Close()

    win = PlaceWindow()
    win.ShowDialog()
    res = win.result
    if not res:
        log("Cancelled.")
        log.close()
        script.exit()

    meta = res["meta"]
    rows = res["rows"]
    by_layer = rows_by_layer(rows)
    layer_workset_map = res["ws_map"]
    log("Export **{}** - scope: *{}* - generated {}".format(
        meta.get("source"), meta.get("scope"), meta.get("generated")))
    log("**{}** structures across **{}** layers.".format(
        len(rows), len(by_layer)))
    for lay in sorted(by_layer, key=lambda k: k.lower()):
        log("  - {}  x{}  ->  {}".format(
            lay, len(by_layer[lay]), layer_workset_map.get(lay) or ACTIVE))

    if worksets:
        merged = dict(saved_map)
        merged.update(layer_workset_map)
        try:
            save_dashboard_layer_workset_map(merged)
        except Exception:
            pass

    # per-shape parameter mapping + vertical origin (from the window) --------
    shapes = [s for s in ("box", "cyl") if s in res["fams"]]
    fams = {}
    for sp in shapes:
        fam_label, base_symbol, param_map, anchor = res["fams"][sp]
        log("Family for {}: **{}**".format(
            "BOX chambers" if sp == "box" else "CYLINDRICAL chambers",
            fam_label))
        if param_map is not None:
            log("Param map ({}): ".format(fam_label) + ", ".join(
                "{} -> {}".format(k, param_map[k] or "(skip)")
                for k in sorted(param_map)))
        else:
            log("No probeable instance parameters - automatic name "
                "matching (L/W/H/DIA tried first).")
        if anchor is None:
            # window said Auto-detect: probe, dialog fallback when the
            # probe cannot tell
            anchor = _resolve_anchor(doc, forms, log, base_symbol,
                                     fam_label, param_map)
        else:
            log("Vertical origin ({}): **{}**".format(fam_label, anchor))
        fams[sp] = {"label": fam_label, "symbol": base_symbol,
                    "param_map": param_map, "anchor": anchor}

    # Rewrite every row's Z to the level the family ORIGIN must sit at,
    # BEFORE solve_points / the offset writer consume z_m downstream.
    for r in rows:
        r["z_m"] = anchor_z(fams[r["shape"]]["anchor"],
                            r["rim_m"], r["sump_m"], r["z_m"])
    for sp in shapes:
        log("Placement Z basis ({}): origin at **{}** -> offset driven "
            "to **{}**.".format(
                sp, fams[sp]["anchor"],
                {"base": "SUMP", "top": "RIM",
                 "center": "mid-height"}[fams[sp]["anchor"]]))

    # host level (auto) ------------------------------------------------------
    _levels = sorted(FilteredElementCollector(doc).OfClass(Level).ToElements(),
                     key=lambda lv: lv.Elevation)
    if not _levels:
        forms.alert("This project has no levels.", exitscript=True)
    default_lvl = get_pipe_host_level_name()
    host_level_name = None
    for lv in _levels:
        if safe_name(lv) == default_lvl:
            host_level_name = default_lvl
            break
    if host_level_name is None:
        host_level_name = safe_name(_levels[0])
    log("Host level (auto): **{}**".format(host_level_name))

    # layer-named system types (same automation as the pipes) ----------------
    system_type_map = None
    if res["assign_sys"]:
        from Autodesk.Revit.DB.Plumbing import PipingSystemType
        by_name = {}
        for pst in FilteredElementCollector(doc).OfClass(PipingSystemType):
            nm = _el_name(pst).strip().lower()
            if nm and nm not in by_name:
                by_name[nm] = pst.Id
        system_type_map = {}
        missing = []
        for lay in by_layer:
            stid = by_name.get(lay.strip().lower())
            if stid is not None:
                system_type_map[lay.strip().lower()] = stid
            else:
                missing.append(lay)
        log("Layer-named system types found for **{}** of {} layer(s){}."
            .format(len(system_type_map), len(by_layer),
                    " - missing: {} (run Project Setup from this export "
                    "to create them)".format(", ".join(missing))
                    if missing else ""))
        if not system_type_map:
            system_type_map = None

    # transform once - fail here and NOTHING gets created --------------------
    log("Coordinates: **{}**".format(
        COORD_MODEL if res["prefer_model"] else COORD_SETTINGS))
    pts_info = None
    try:
        pts_info = solve_points(doc, rows, log=log,
                                prefer_model=res["prefer_model"])
    except Exception as ex:
        import traceback
        log(traceback.format_exc())
        o = meta.get("origin") or {}
        oe, on = o.get("easting"), o.get("northing")
        if oe is not None and on is not None:
            choice = forms.alert(
                "{}\n\nThis model has no usable georeference, so I can "
                "place the site at the model's INTERNAL ORIGIN instead, "
                "using the export origin as the offset:\n"
                "    E {:.3f}    N {:.3f}    rot 0\n\n"
                "Everything stays correctly positioned relative to itself "
                "(model X/Y will equal the dashboard's local metres); you "
                "can set shared coordinates later.".format(ex, oe, on),
                title="No georeference",
                options=["Place at internal origin",
                         "Place + save offset to Settings", "Cancel"])
            if choice and choice.startswith("Place"):
                if "save" in choice:
                    try:
                        from pymep_config import load_settings, save_settings
                        s = load_settings()
                        s["landxml_off_e_m"] = str(oe)
                        s["landxml_off_n_m"] = str(on)
                        s["landxml_off_z_m"] = "0.0"
                        s["landxml_rot_deg"] = "0.0"
                        save_settings(s)
                        log("Saved to Settings: E {}  N {}  Z 0  rot 0"
                            .format(oe, on))
                    except Exception as ex2:
                        log("Could not save Settings: {}".format(ex2))
                pts_info = solve_points(
                    doc, rows, log=log,
                    force_offset=(float(oe), float(on), 0.0, 0.0))
        if pts_info is None:
            forms.alert("Transform failed - nothing was created.\n\n{}"
                        "\n\nFull details are in the output window."
                        .format(ex), exitscript=True)

    # place - one pass per shape, same pre-solved transform ------------------
    created = 0
    failed = 0
    instances = []
    mode = pts_info[1]
    try:
        for sp in shapes:
            sp_rows = [r for r in rows if r["shape"] == sp]
            symbols_by_layer = ensure_layer_types(
                doc, fams[sp]["symbol"],
                set(r["layer"] for r in sp_rows), log=log)
            sp_pts = ([pt for pt in pts_info[0] if pt[2]["shape"] == sp],
                      pts_info[1], pts_info[2])
            c, f, mode, insts = place_dashboard_structures(
                doc, sp_rows, symbols_by_layer,
                host_level_name=host_level_name,
                layer_workset_map=layer_workset_map, log=log,
                param_map=fams[sp]["param_map"], pts_info=sp_pts,
                system_type_map=system_type_map)
            created += c
            failed += f
            instances.extend(insts)
    except Exception as ex:
        import traceback
        log(traceback.format_exc())
        forms.alert("Placement stopped: {}\n\nFull details are in the "
                    "output window.".format(ex), exitscript=True)
    if instances:
        try:
            ids = [i.Id for i in instances]
            revit.get_selection().set_to(ids)
            try:
                log("All placed instances are now SELECTED. First one: {}"
                    .format(output.linkify(ids[0])))
            except Exception:
                pass
            from System.Collections.Generic import List as _NetList
            from Autodesk.Revit.DB import ElementId as _EID
            revit.uidoc.ShowElements(_NetList[_EID](ids))
        except Exception:
            pass
    if created == 0:
        forms.alert("Nothing was placed ({} attempted).\n\nThe exact "
                    "placement errors are listed in the output window - "
                    "send them to me. Most likely causes: the family "
                    "rejects point placement (work-plane based) or the "
                    "survey offset in Settings doesn't match this model."
                    .format(len(rows)))
    else:
        forms.alert("Placed {} of {} chambers at true elevations.\n"
                    "Transform: {}\n\nThey are SELECTED and the view has "
                    "zoomed to them. The output window shows the offsets, "
                    "the position span and the parameter report.".format(
                        created, len(rows), mode))
