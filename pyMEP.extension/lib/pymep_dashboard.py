# -*- coding: utf-8 -*-
"""Place chambers from an OttomanLabs utilities-dashboard export.

The 3D dashboard (GBR1-DCZZ viewer) exports a JSON of the structures that
are currently in view - each with its layer (network), shape (box / cyl),
OS coordinates, rim / sump levels and plan dimensions.

This module drives the two Dashboard-panel buttons:

  * one FAMILY is asked for FIRST,
  * one TYPE per layer is created (duplicated from the picked type and
    named exactly after the layer, e.g. "SW - Phase 1"),
  * every size / level value is written to INSTANCE parameters - the types
    carry no dimensions, they only tag which layer an instance belongs to,
  * placement uses the SAME survey->internal transform as the pipe placer
    (model georeference first, explicit survey offset fallback), at Z=0
    with the rim driven through Offset-from-Host - matching
    pymep_structures_place so everything lands in one frame.
"""

import io
import json
import math

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

__version__ = "1.8"

EXPORT_KIND = "ol-utilities-structures"

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
    with io.open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if data.get("kind") != EXPORT_KIND:
        raise ValueError(
            "Not a dashboard structures export (kind='{}', expected '{}'). "
            "Use the EXPORT button in the 3D dashboard.".format(
                data.get("kind"), EXPORT_KIND))
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
            ("source", "generated", "scope", "origin", "epsg", "count")}
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
def solve_points(doc, rows, log=None, force_offset=None):
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
                               pts_info=None):
    """Place every row with its layer's type. Sizes and levels are written
    to INSTANCE parameters only. Returns (created, failed, mode)."""
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

    # ---- workset -----------------------------------------------------------
    ws_id = None
    if workset_name and doc.IsWorkshared:
        for ws in FilteredWorksetCollector(doc).OfKind(WorksetKind.UserWorkset):
            if ws.Name.strip() == workset_name.strip():
                ws_id = ws.Id
                break

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
    if placed:
        t2 = Transaction(doc, "Dashboard structure params")
        t2.Start()
        try:
            doc.Regenerate()
        except Exception:
            pass
        lvl_elev = lvl.Elevation
        moved = 0

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
def run_place(shape):
    """The whole button flow. shape: 'box' or 'cyl'.
    Family is asked FIRST, then the dashboard export, then workset."""
    from pyrevit import revit, forms, script
    from pymep_config import get_pipe_host_level_name
    from pymep_structures_place import list_worksets
    from pymep_log import Logger

    label = "BOX" if shape == "box" else "CYLINDRICAL"
    output = script.get_output()
    log = Logger(output, "DashboardPlace{}".format(label.title()))
    doc = revit.doc

    log("### Place {} chambers from a dashboard export".format(label.lower()))
    log("pymep_dashboard **v{}**".format(__version__))

    # 1. family FIRST -------------------------------------------------------
    from pymep_structures_place import list_family_symbols as _raw_syms

    def _counts():
        raw = _raw_syms(doc)
        fams = [f for f in FilteredElementCollector(doc).OfClass(Family)]
        return raw, len(fams)

    syms = list_chamber_symbols(doc)
    while not syms:
        raw, n_fams = _counts()
        choice = forms.alert(
            "pymep_dashboard v{}\n\n"
            "No point-placeable family types found.\n"
            "  - loadable families in this project: {}\n"
            "  - family types of any kind (incl. curtain system): {}\n\n"
            "If the first number is 0, this project has no families "
            "loaded - I can load an .rfa for you now.\n"
            "If it is NOT 0, pick 'Show all types anyway' and tell me "
            "what you see.".format(__version__, n_fams, len(raw)),
            title="Place chambers",
            options=["Load a family (.rfa)...",
                     "Show all types anyway", "Cancel"])
        if choice == "Load a family (.rfa)...":
            rfa = forms.pick_file(file_ext="rfa",
                                  title="Pick the chamber family (.rfa)")
            if rfa:
                t = Transaction(doc, "Load dashboard family")
                t.Start()
                try:
                    ok = doc.LoadFamily(rfa)
                    t.Commit()
                    log("Loaded family: **{}** ({})".format(
                        rfa, "ok" if ok else "already loaded / unchanged"))
                except Exception as ex:
                    t.RollBack()
                    forms.alert("Could not load:\n{}".format(ex))
            syms = list_chamber_symbols(doc)
        elif choice == "Show all types anyway":
            syms = _raw_syms(doc)
            if not syms:
                forms.alert("This project contains zero family types of any "
                            "kind. Load a family first.", exitscript=True)
            break
        else:
            forms.alert("Cancelled.", exitscript=True)

    class SymOpt(object):
        def __init__(self, lbl, sym):
            self.sym = sym
            self.name = lbl

    sym_pick = forms.SelectFromList.show(
        [SymOpt(lbl, sym) for lbl, sym in syms],
        title="Pick the family for {} chambers (one type per layer will be "
              "made from it)".format(label),
        button_name="Use this family", multiselect=False, name_attr="name")
    if not sym_pick:
        forms.alert("No family picked.", exitscript=True)
    base_symbol = sym_pick.sym
    log("Family: **{}**".format(sym_pick.name))

    # 1b. map the size parameters (asked up front) ---------------------------
    keys = ["L", "W", "H"] if shape == "box" else ["DIA", "H"]
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
                    k, sym_pick.name),
                button_name="Map {}".format(k), multiselect=False)
            if not pick:
                forms.alert("Mapping cancelled.", exitscript=True)
            param_map[k] = None if pick == SKIP else pick
        log("Param map: " + ", ".join(
            "{} -> {}".format(k, param_map[k] or "(skip)") for k in keys))
    else:
        log("Could not probe the family's instance parameters - falling "
            "back to automatic name matching (L/W/H/DIA tried first).")

    # 2. dashboard export ---------------------------------------------------
    json_path = forms.pick_file(
        file_ext="json", title="Pick the dashboard structures export (.json)")
    if not json_path:
        forms.alert("No export selected.", exitscript=True)
    try:
        meta, rows = read_export(json_path)
    except Exception as ex:
        forms.alert("Could not read the export:\n\n{}".format(ex),
                    exitscript=True)
    rows = [r for r in rows if r["shape"] == shape]
    if not rows:
        forms.alert("The export contains no {} structures "
                    "(scope was: {}).".format(label.lower(),
                                              meta.get("scope")),
                    exitscript=True)
    by_layer = rows_by_layer(rows)
    log("Export **{}** - scope: *{}* - generated {}".format(
        meta.get("source"), meta.get("scope"), meta.get("generated")))
    log("**{}** {} structures across **{}** layers:".format(
        len(rows), label.lower(), len(by_layer)))
    for lay in sorted(by_layer, key=lambda k: -len(by_layer[k])):
        log("  - {}  x{}".format(lay, len(by_layer[lay])))

    # 3. level (auto) + workset --------------------------------------------
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

    ACTIVE = "(active workset)"
    worksets = list_worksets(doc)
    workset_name = ""
    if worksets:
        ws_pick = forms.SelectFromList.show(
            [ACTIVE] + worksets, title="Pick a workset",
            button_name="Use this", multiselect=False)
        if not ws_pick:
            forms.alert("No workset picked - aborting.", exitscript=True)
        workset_name = "" if ws_pick == ACTIVE else ws_pick

    # 4. transform first - fail here and NOTHING gets created ----------------
    pts_info = None
    try:
        pts_info = solve_points(doc, rows, log=log)
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

    # 5. confirm + go --------------------------------------------------------
    if forms.alert(
            "Place {} {} chambers from:\n  {}\n\nFamily: {}\n"
            "Types: one per layer ({})\nSizes -> mapped instance params "
            "(see output window)\nZ: via 'Offset from level' (level at 0)\n"
            "Workset: {}\nLevel (auto): {}\n\nPlace now?".format(
                len(rows), label.lower(), meta.get("source"),
                sym_pick.name, len(by_layer),
                workset_name or ACTIVE, host_level_name),
            title="Confirm", options=["Place", "Cancel"]) != "Place":
        forms.alert("Cancelled.", exitscript=True)

    try:
        symbols_by_layer = ensure_layer_types(doc, base_symbol,
                                              by_layer.keys(), log=log)
        created, failed, mode, instances = place_dashboard_structures(
            doc, rows, symbols_by_layer, host_level_name=host_level_name,
            workset_name=workset_name, log=log, param_map=param_map,
            pts_info=pts_info)
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
        forms.alert("Placed {} of {} {} chambers at true elevations.\n"
                    "Transform: {}\n\nThey are SELECTED and the view has "
                    "zoomed to them. The output window shows the offsets, "
                    "the position span and the parameter report.".format(
                        created, len(rows), label.lower(), mode))
