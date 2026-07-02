# -*- coding: utf-8 -*-
"""Place Revit pipes from a LandXML export - a direct port of the proven
HEL18 Dynamo node (`HEL18_pipes_place.dyn`).

This does EXACTLY what that Dynamo script does, but reads the LandXML
directly (resolving pipe endpoints from the structures) instead of a
pre-made CSV:

  * survey -> internal transform: try the model's OWN georeference first
    (ActiveProjectLocation); if that leaves coordinates out of Revit's
    workable range (model not georeferenced), fall back to the explicit
    HEL18 survey transform baked in below;
  * coordinate-range guard so a non-georeferenced model can never trigger
    the "protect your project from corruption" abort;
  * three isolated transactions - create pipes (+worksets), set Marks, set
    diameters (snapped to the pipe type's available sizes) - so the risky
    diameter step is last and can't lose the pipes/marks;
  * short-curve skip (~0.78 mm), diameter from the LandXML bore, Mark from
    the LandXML pipe name.

The transform constants are the HEL18 values verified against the model's
Survey Point. If a future site differs, change OFF_*/ROT_DEG (or pass them
in). ROT_DEG is the one value that flips the network's rotation.
"""

import math
import re
import clr
clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    BuiltInParameter, FilteredElementCollector, FilteredWorksetCollector,
    Level, Element, Transaction, SubTransaction, WorksetKind, XYZ,
)
from Autodesk.Revit.DB.Plumbing import Pipe, PipeType, PipingSystemType

from pymep_revit import safe_name, mm2ft


# ===========================================================================
# Explicit survey transform (fallback when model isn't georeferenced AND
# when Settings has no override). These are SITE DEFAULTS - the live values
# come from Settings (config.get_landxml_survey_transform), so moving sites
# needs NO code edit. Current defaults: HNU1A.
#   E 3,498,151.6589   N 5,554,088.8918   Z0 0.0   True North 40.36 deg
# ROTATION SIGN unverified: if a network comes in rotated/mirrored wrong,
# negate the rotation in Settings (landxml_rot_deg: 40.36 <-> -40.36).
# ===========================================================================
HEL18_OFF_E_M = 3498151.6589
HEL18_OFF_N_M = 5554088.8918
# Z offset = 0: pipes are placed at their TRUE (absolute / AOD) elevations, the
# same values written into the manholes' rim/invert parameters - so the two
# reference one datum. The model's base point / survey point should be at 0 so
# these absolute internal elevations also DISPLAY as the correct site levels.
HEL18_OFF_Z_M = 0.0
HEL18_ROT_DEG = 40.36

# Transform selection. The model's georeference and the explicit transform can
# disagree on the VERTICAL datum (the model's Survey Point elevation differs
# from the site datum by 45.667 m), which made pipes and structures land on
# different Z. To guarantee pipes and structures ALWAYS share one transform,
# force the explicit transform below. Set this True only if the model is
# cleanly georeferenced (Survey Point E/N AND elevation set to the real site).
USE_MODEL_GEOREFERENCE = False


def clean_mark(name):
    """Strip the bracketed network suffix (and any other parenthetical group)
    from a LandXML name, so 'SW56 (HEL18 - STORM WATER)' becomes 'SW56'."""
    if not name:
        return name
    return re.sub(r"\s*\([^)]*\)", "", name).strip()


# Revit gets unstable beyond ~16 km (~52,500 ft) from the internal origin.
_LIMIT_FT = 52500.0
# Revit short-curve tolerance (~0.78 mm); shorter pipes are invalid.
_MIN_PIPE_LEN_FT = 0.0026


def _say(log, msg):
    if log is not None:
        log(msg)


# --------------------------------------------------------------------------
# robust name read (pythonnet/IronPython both fine; mirrors the Dynamo node)
# --------------------------------------------------------------------------
try:
    _NAME_PROP = clr.GetClrType(Element).GetProperty("Name")
except Exception:
    _NAME_PROP = None


def _el_name(el):
    if _NAME_PROP is not None:
        try:
            v = _NAME_PROP.GetValue(el, None)
            if v:
                return v
        except Exception:
            pass
    try:
        v = el.Name
        if v:
            return v
    except Exception:
        pass
    for bip in (BuiltInParameter.SYMBOL_NAME_PARAM,
                BuiltInParameter.ALL_MODEL_TYPE_NAME):
        try:
            p = el.get_Parameter(bip)
            if p is not None:
                s = p.AsString()
                if s:
                    return s
        except Exception:
            pass
    return None


def _by_name(doc, cls, name):
    for el in FilteredElementCollector(doc).OfClass(cls):
        try:
            if _el_name(el) == name:
                return el
        except Exception:
            pass
    return None


def list_type_names(doc, cls):
    out = []
    for el in FilteredElementCollector(doc).OfClass(cls):
        nm = _el_name(el)
        if nm:
            out.append(nm)
    return sorted(set(out))


def list_worksets(doc):
    if not doc.IsWorkshared:
        return []
    out = []
    for ws in FilteredWorksetCollector(doc).OfKind(WorksetKind.UserWorkset):
        try:
            out.append(ws.Name)
        except Exception:
            pass
    return sorted(out)


# --------------------------------------------------------------------------
# diameter snapping (mirrors the Dynamo routing_sizes_ft + snap)
# --------------------------------------------------------------------------
def _routing_sizes_ft(doc, pipe_type):
    sizes = set()
    try:
        from Autodesk.Revit.DB.Plumbing import PipeSegment
        from Autodesk.Revit.DB import (
            RoutingPreferenceRuleGroupType, ElementId)
        rpm = pipe_type.RoutingPreferenceManager
        seg_ids = set()
        try:
            nrules = rpm.GetNumberOfRules(
                RoutingPreferenceRuleGroupType.Segments)
        except Exception:
            nrules = 0
        for i in range(nrules):
            try:
                rule = rpm.GetRule(RoutingPreferenceRuleGroupType.Segments, i)
                mid = rule.MEPPartId
                if mid is not None and mid != ElementId.InvalidElementId:
                    seg_ids.add(mid.IntegerValue)
            except Exception:
                pass
        segs = []
        for sid in seg_ids:
            el = doc.GetElement(ElementId(sid))
            if el is not None:
                segs.append(el)
        if not segs:
            segs = list(FilteredElementCollector(doc).OfClass(PipeSegment))
        for seg in segs:
            try:
                for s in seg.GetSizes():
                    sizes.add(s.NominalDiameter)
            except Exception:
                pass
    except Exception:
        pass
    return sizes


# ===========================================================================
# main
# ===========================================================================
def place_landxml_pipes(doc, rows, network_workset_map,
                        pipe_type_name, system_type_name, host_level_name,
                        off_e_m=None, off_n_m=None,
                        off_z_m=None, rot_deg=None,
                        network_filter=None, log=None):
    """Place pipes from resolved LandXML rows, mirroring the Dynamo node.

    rows: list of dicts (from pymep_landxml.placement_rows) - name,
        network, dia_mm, sx,sy,sz, ex,ey,ez in **survey-grid metres**.
    network_workset_map: {network_name: workset_name} ('' = active).
    off_e_m/off_n_m/off_z_m/rot_deg: survey transform. If any is None it is
        read from Settings (config.get_landxml_survey_transform), which falls
        back to the site DEFAULT_LANDXML_* constants. Pass explicit values
        only to override Settings for a one-off run.
    Returns (created, failed, skipped, mode, dia_set, mark_set).
    """
    # Resolve transform from Settings unless the caller overrode it.
    if off_e_m is None or off_n_m is None or off_z_m is None or rot_deg is None:
        try:
            from pymep_config import get_landxml_survey_transform
            s_e, s_n, s_z, s_rot = get_landxml_survey_transform()
        except Exception:
            s_e, s_n = HEL18_OFF_E_M, HEL18_OFF_N_M
            s_z, s_rot = HEL18_OFF_Z_M, HEL18_ROT_DEG
        if off_e_m is None:
            off_e_m = s_e
        if off_n_m is None:
            off_n_m = s_n
        if off_z_m is None:
            off_z_m = s_z
        if rot_deg is None:
            rot_deg = s_rot

    net_filter = set(network_filter) if network_filter is not None else None

    pt = _by_name(doc, PipeType, pipe_type_name)
    st = _by_name(doc, PipingSystemType, system_type_name)
    lvl = None
    for l in FilteredElementCollector(doc).OfClass(Level):
        if _el_name(l) == host_level_name:
            lvl = l
            break

    if pt is None or st is None or lvl is None:
        miss = []
        if pt is None:
            miss.append("PIPE TYPE '{}' not found. Available: {}".format(
                pipe_type_name, ", ".join(list_type_names(doc, PipeType))
                or "(none)"))
        if st is None:
            miss.append("SYSTEM TYPE '{}' not found. Available: {}".format(
                system_type_name,
                ", ".join(list_type_names(doc, PipingSystemType)) or "(none)"))
        if lvl is None:
            miss.append("LEVEL '{}' not found. Available: {}".format(
                host_level_name,
                ", ".join(_el_name(l) for l in
                          FilteredElementCollector(doc).OfClass(Level)
                          if _el_name(l)) or "(none)"))
        raise ValueError("\n".join(miss))

    # ---- transforms ------------------------------------------------------
    loc = doc.ActiveProjectLocation
    inv = loc.GetTotalTransform().Inverse if loc is not None else None

    def to_internal_georef(e_m, n_m, z_m):
        shared = XYZ(mm2ft(e_m * 1000.0), mm2ft(n_m * 1000.0),
                     mm2ft(z_m * 1000.0))
        return inv.OfPoint(shared)

    th = math.radians(rot_deg)
    c, s = math.cos(th), math.sin(th)

    def to_internal_explicit(e_m, n_m, z_m):
        dx = e_m - off_e_m
        dy = n_m - off_n_m
        rx = dx * c - dy * s
        ry = dx * s + dy * c
        return XYZ(rx / 0.3048, ry / 0.3048, (z_m - off_z_m) / 0.3048)

    # filter rows to chosen networks
    work = []
    for r in rows:
        net = r.get("network") or ""
        if net_filter is not None and net not in net_filter:
            continue
        work.append(r)

    def transform_all(fn):
        out = []
        m_abs = 0.0
        for r in work:
            p0 = fn(r["sx"], r["sy"], r["sz"])
            p1 = fn(r["ex"], r["ey"], r["ez"])
            out.append((p0, p1, r.get("dia_mm"), r.get("name") or "",
                        r.get("network") or ""))
            for p in (p0, p1):
                mm = max(abs(p.X), abs(p.Y), abs(p.Z))
                if mm > m_abs:
                    m_abs = mm
        return out, m_abs

    pts = None
    mode = None
    max_abs = None
    if inv is not None and USE_MODEL_GEOREFERENCE:
        pts, max_abs = transform_all(to_internal_georef)
        mode = "model georeference"
    if pts is None or max_abs > _LIMIT_FT:
        pts, max_abs = transform_all(to_internal_explicit)
        mode = "explicit HEL18 survey transform"

    if max_abs > _LIMIT_FT:
        raise RuntimeError(
            "Even after the explicit transform the coordinates are {:.0f} km "
            "from the origin - the survey origin doesn't match this model. "
            "Open Settings > Pipes-Coordinates and set the LandXML E/N "
            "offset to this model's Project Base Point (survey E/N in "
            "metres). Current offset: E {:.4f}  N {:.4f}."
            .format(max_abs * 0.0003048, off_e_m, off_n_m))

    _say(log, "Transform used: **{}**".format(mode))
    _say(log, "Pipe type **{}**, system **{}**, level **{}**".format(
        pipe_type_name, system_type_name, host_level_name))

    # ---- workset resolution (per network) --------------------------------
    ws_lookup = {}
    if doc.IsWorkshared:
        existing = {}
        for ws in FilteredWorksetCollector(doc).OfKind(WorksetKind.UserWorkset):
            existing[ws.Name.strip()] = ws.Id
        for net, wsname in (network_workset_map or {}).items():
            nm = (wsname or "").strip()
            if nm and nm in existing:
                ws_lookup[net] = existing[nm]

    # ---- snapping --------------------------------------------------------
    avail = _routing_sizes_ft(doc, pt)
    if avail:
        _say(log, "Pipe type routes through {} size(s); diameters will snap "
                  "to the nearest.".format(len(avail)))
    else:
        _say(log, "No readable sizes on the pipe type - diameters left at the "
                  "type default (no corruption risk).")

    def snap_ft(dia_mm):
        if not dia_mm or not avail:
            return None
        raw = mm2ft(float(dia_mm))
        best = None
        bd = None
        for sz in avail:
            d = abs(sz - raw)
            if bd is None or d < bd:
                bd = d
                best = sz
        return best

    # ---- PHASE 1: create pipes + worksets --------------------------------
    created = []
    placed = []   # (pipe, snap_ft, name)
    failed = 0
    skipped_short = 0

    t1 = Transaction(doc, "LandXML pipes - place")
    t1.Start()
    for (p0, p1, dia, name, net) in pts:
        try:
            if p0.DistanceTo(p1) < _MIN_PIPE_LEN_FT:
                skipped_short += 1
                continue
            pipe = Pipe.Create(doc, st.Id, pt.Id, lvl.Id, p0, p1)
            wsid = ws_lookup.get(net)
            if wsid is not None:
                wp = pipe.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM)
                if wp is not None and not wp.IsReadOnly:
                    wp.Set(wsid.IntegerValue)
            created.append(pipe.Id.IntegerValue)
            placed.append((pipe, snap_ft(dia), name))
        except Exception:
            failed += 1
    t1.Commit()
    _say(log, "Phase 1: created **{}** pipes (failed {}, short-skipped {})."
              .format(len(created), failed, skipped_short))

    # ---- PHASE 2: Marks --------------------------------------------------
    mark_set = 0
    if placed:
        t2 = Transaction(doc, "LandXML pipes - marks")
        t2.Start()
        try:
            doc.Regenerate()
        except Exception:
            pass
        for (pipe, snap, name) in placed:
            if not name:
                continue
            try:
                mp = pipe.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
                if mp is not None and not mp.IsReadOnly:
                    mp.Set(clean_mark(name))
                    mark_set += 1
            except Exception:
                pass
        t2.Commit()

    # ---- PHASE 3: diameters (snapped, isolated) --------------------------
    dia_set = 0
    if placed:
        t3 = Transaction(doc, "LandXML pipes - diameters")
        t3.Start()
        try:
            doc.Regenerate()
        except Exception:
            pass
        for (pipe, snap, name) in placed:
            if not snap:
                continue
            sub = SubTransaction(doc)
            try:
                sub.Start()
                dp = pipe.get_Parameter(BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
                if dp is not None and not dp.IsReadOnly:
                    dp.Set(snap)
                    sub.Commit()
                    dia_set += 1
                else:
                    sub.RollBack()
            except Exception:
                try:
                    sub.RollBack()
                except Exception:
                    pass
        t3.Commit()

    _say(log, "Phase 2/3: marks **{}**, diameters **{}**.".format(
        mark_set, dia_set))
    _say(log, "### Placed **{}**, failed **{}**, short-skipped **{}** "
              "(transform: {})".format(len(created), failed, skipped_short,
                                       mode))
    return len(created), failed, skipped_short, mode, dia_set, mark_set
