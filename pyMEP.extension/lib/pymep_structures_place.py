# -*- coding: utf-8 -*-
"""Place a Revit family instance at every LandXML structure of a chosen
network + type.

Uses the SAME survey->internal transform as the pipe placer
(`pymep_landxml_place2.choose_survey_transform`): the explicit Settings
transform first, then the model's own survey position (GetProjectPosition)
as an automatic fallback - so structures land in exactly the same
coordinate frame as the pipes.

Each instance is placed at the structure's plan position (E, N) and at its
rim elevation (falling back to the lowest invert, then 0). The structure
name is written to the instance Mark; rim and invert levels are written to
instance parameters when matching ones exist.
"""

import clr
clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    BuiltInParameter, FilteredElementCollector, FilteredWorksetCollector,
    Level, Element, Transaction, SubTransaction, WorksetKind, XYZ,
)
from Autodesk.Revit.DB import FamilySymbol, FamilyInstance
from Autodesk.Revit.DB.Structure import StructuralType

from pymep_revit import safe_name, mm2ft
from pymep_landxml_place2 import (
    HEL18_OFF_E_M, HEL18_OFF_N_M, HEL18_OFF_Z_M, HEL18_ROT_DEG,
    USE_MODEL_GEOREFERENCE, clean_mark, _LIMIT_FT, _el_name, list_worksets,
    choose_survey_transform, survey_transform_error, make_survey_fn,
    model_survey_position,
)


def _say(log, m):
    if log is not None:
        log(m)


# --------------------------------------------------------------------------
# family symbol discovery
# --------------------------------------------------------------------------
def list_family_symbols(doc):
    """Return [(label, FamilySymbol), ...] for placeable family types,
    sorted by 'Family : Type'. Label is what the picker shows."""
    out = []
    for sym in FilteredElementCollector(doc).OfClass(FamilySymbol):
        try:
            fam = sym.Family.Name
            typ = _el_name(sym) or "?"
            out.append(("{} : {}".format(fam, typ), sym))
        except Exception:
            continue
    out.sort(key=lambda t: t[0].lower())
    return out


def _activate(doc, sym):
    if not sym.IsActive:
        sym.Activate()
        doc.Regenerate()


def _set_param_double(inst, bip, value_internal):
    try:
        p = inst.get_Parameter(bip)
        if p is not None and not p.IsReadOnly:
            p.Set(value_internal)
            return True
    except Exception:
        pass
    return False


def _set_named_param_length_m(inst, names, value_m):
    """Set the first matching instance parameter (by name) to a length, given
    in metres -> internal feet. Returns the matched parameter name, or None."""
    for nm in names:
        try:
            p = inst.LookupParameter(nm)
            if p is not None and not p.IsReadOnly:
                p.Set(mm2ft(value_m * 1000.0))
                return nm
        except Exception:
            pass
    return None


# Candidate instance-parameter names for the rim/cover level and the invert.
RIM_PARAM_NAMES = ["Rim Level", "Cover Level", "Rim Elevation", "Cover Elevation",
                   "Top Elevation", "Top Level", "Lid Level", "Elevation",
                   "Level", "MH Cover Level"]
INVERT_PARAM_NAMES = ["Invert Level", "Invert Elevation", "Base Level",
                      "Sump Level", "Bottom Level", "Channel Invert Level"]


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def place_structures(doc, rows, symbol, host_level_name, workset_name="",
                     off_e_m=None, off_n_m=None,
                     off_z_m=None, rot_deg=None, log=None):
    """Place one instance of ``symbol`` at each row.

    rows: list of dicts from pymep_landxml.structure_rows - name, x, y
        (survey metres), rim_m, invert_m, z_m.
    symbol: FamilySymbol to place.
    host_level_name: level to host the instances on.
    workset_name: '' -> active workset.
    off_e_m/off_n_m/off_z_m/rot_deg: survey transform. If any is None it is
        read from Settings (config.get_landxml_survey_transform), which falls
        back to the site DEFAULT_LANDXML_* constants.
    Returns (created, failed, skipped, mode).
    """
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

    if not rows:
        raise ValueError("No structures to place.")

    lvl = None
    for l in FilteredElementCollector(doc).OfClass(Level):
        if _el_name(l) == host_level_name:
            lvl = l
            break
    if lvl is None:
        avail = ", ".join(_el_name(l) for l in
                          FilteredElementCollector(doc).OfClass(Level)
                          if _el_name(l))
        raise ValueError("Level '{}' not found. Available: {}".format(
            host_level_name, avail or "(none)"))

    # ---- transform (explicit first, model-derived fallback) --------------
    # Same construction as place_landxml_pipes: both candidates share
    # make_survey_fn and off_z_m, so structures and pipes always land in
    # one frame whichever transform wins.
    to_internal_explicit = make_survey_fn(off_e_m, off_n_m, rot_deg, off_z_m)
    mp = model_survey_position(doc)
    to_internal_model = (make_survey_fn(mp[0], mp[1], mp[2], off_z_m)
                         if mp is not None else None)

    def transform_all(fn):
        # X/Y go to the survey position; Z is forced to 0. The manhole's real
        # rim/invert levels are written as PARAMETERS (below), not as the
        # geometric Z - this keeps placement independent of the model's
        # vertical datum (base point / survey point), which is what kept
        # throwing the elevation off.
        out = []
        m_abs = 0.0
        for r in rows:
            p = fn(r["x"], r["y"], r["z_m"])
            p = XYZ(p.X, p.Y, 0.0)
            out.append((p, r))
            mm = max(abs(p.X), abs(p.Y), abs(p.Z))
            if mm > m_abs:
                m_abs = mm
        return out, m_abs

    pts, max_abs, mode, tried = choose_survey_transform(
        transform_all, to_internal_model, to_internal_explicit,
        has_model=to_internal_model is not None)

    if max_abs > _LIMIT_FT:
        en_pairs = [(r["x"], r["y"]) for r in rows]
        raise survey_transform_error("structures", tried, en_pairs,
                                     off_e_m, off_n_m)

    _say(log, "Transform used: **{}**".format(mode))
    _say(log, "Family: **{}**, level **{}**".format(safe_name(symbol),
                                                    host_level_name))

    # ---- workset ---------------------------------------------------------
    ws_id = None
    if workset_name and doc.IsWorkshared:
        for ws in FilteredWorksetCollector(doc).OfKind(WorksetKind.UserWorkset):
            if ws.Name.strip() == workset_name.strip():
                ws_id = ws.Id
                break

    # ---- place -----------------------------------------------------------
    created = 0
    failed = 0
    placed = []   # (instance, row)

    t = Transaction(doc, "Place LandXML structures")
    t.Start()
    try:
        _activate(doc, symbol)
    except Exception:
        pass
    for (p, r) in pts:
        try:
            inst = doc.Create.NewFamilyInstance(
                p, symbol, lvl, StructuralType.NonStructural)
            if ws_id is not None:
                wp = inst.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM)
                if wp is not None and not wp.IsReadOnly:
                    wp.Set(ws_id.IntegerValue)
            created += 1
            placed.append((inst, r))
        except Exception:
            failed += 1
    t.Commit()

    _say(log, "Created **{}** instances (failed {}).".format(created, failed))

    # ---- params (Mark + rim/invert) in a second, isolated pass -----------
    mark_set = 0
    offset_set = 0
    rim_set = 0
    inv_set = 0
    rim_param_hits = {}
    inv_param_hits = {}
    if placed:
        t2 = Transaction(doc, "Structure params")
        t2.Start()
        try:
            doc.Regenerate()
        except Exception:
            pass
        for (inst, r) in placed:
            sub = SubTransaction(doc)
            try:
                sub.Start()
                # Mark = structure name
                try:
                    mp = inst.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
                    if mp is not None and not mp.IsReadOnly:
                        mp.Set(clean_mark(r["name"]))
                        mark_set += 1
                except Exception:
                    pass
                # PRIMARY: drive the standard vertical parameter so the manhole
                # sits at its real elevation. The instance is hosted on a level
                # at 0; set "Offset from Host" (and "Elevation from Level" as a
                # fallback) to the rim/cover level. This is what positions a
                # standard level-based family vertically.
                if r.get("rim_m") is not None:
                    off_ft = mm2ft(r["rim_m"] * 1000.0)
                    for bip in (BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM,
                                BuiltInParameter.INSTANCE_ELEVATION_PARAM):
                        try:
                            op = inst.get_Parameter(bip)
                            if op is not None and not op.IsReadOnly:
                                op.Set(off_ft)
                                offset_set += 1
                                break
                        except Exception:
                            pass
                # Also write rim/invert to named schedule parameters if the
                # family happens to have them (harmless if not).
                if r.get("rim_m") is not None:
                    hit = _set_named_param_length_m(inst, RIM_PARAM_NAMES,
                                                    r["rim_m"])
                    if hit:
                        rim_set += 1
                        rim_param_hits[hit] = rim_param_hits.get(hit, 0) + 1
                if r.get("invert_m") is not None:
                    hit = _set_named_param_length_m(inst, INVERT_PARAM_NAMES,
                                                    r["invert_m"])
                    if hit:
                        inv_set += 1
                        inv_param_hits[hit] = inv_param_hits.get(hit, 0) + 1
                sub.Commit()
            except Exception:
                try:
                    sub.RollBack()
                except Exception:
                    pass
        t2.Commit()

    def _fmt_hits(d):
        if not d:
            return "(no matching parameter found)"
        return ", ".join("'%s' x%d" % (k, v)
                         for k, v in sorted(d.items(), key=lambda kv: -kv[1]))

    _say(log, "Inserted at Z=0; Mark set on **{}**.".format(mark_set))
    _say(log, "Offset from Host set to the rim level on **{}** of {}.".format(
        offset_set, created))
    if offset_set == 0 and created > 0:
        _say(log, "NOTE: couldn't write 'Offset from Host' / 'Elevation from "
                  "Level' on this family - tell me the parameter that controls "
                  "its height and I'll target it.")
    if rim_set or inv_set:
        _say(log, "Also wrote named rim/invert params (rim {}, invert {}).".format(
            _fmt_hits(rim_param_hits), _fmt_hits(inv_param_hits)))
    _say(log, "### Placed **{}** structures, failed **{}** (transform: {})"
              .format(created, failed, mode))
    return created, failed, len(rows) - created - failed, mode
