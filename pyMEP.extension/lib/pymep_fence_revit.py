# -*- coding: utf-8 -*-
"""Fence - the Revit-bound half shared by the Fence and Update Fence
buttons: tessellation, the terrain ray-cast, and placing / moving the
instances. The caller owns the Transaction; every function here is
fail-soft per instance and reports what happened.

IronPython 2.7 / Revit 2022-2026.
"""

import clr
clr.AddReference("RevitAPI")

import math

from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    ElementTransformUtils,
    FamilyInstance,
    FamilySymbol,
    FilteredElementCollector,
    FindReferenceTarget,
    Level,
    Line,
    ReferenceIntersector,
    RevitLinkInstance,
    StorageType,
    Transaction,
    UnitTypeId,
    UnitUtils,
    View3D,
    XYZ,
)
from Autodesk.Revit.DB.Structure import StructuralType

import pymep_fence as F

MIN_HIT_PROXIMITY = 1e-9


def id_value(eid):
    try:
        return eid.Value            # Revit 2024+
    except AttributeError:
        return eid.IntegerValue     # Revit 2023 and earlier


def element_name(el):
    try:
        n = el.Name
        if n:
            return n
    except Exception:
        pass
    try:
        from Autodesk.Revit.DB import Element
        return Element.Name.__get__(el)
    except Exception:
        return "?"


def _cat_ids(cat_names):
    out = set()
    for n in cat_names or []:
        if hasattr(BuiltInCategory, n):
            out.add(int(getattr(BuiltInCategory, n)))
    return out


def placeable_symbols(doc, categories=None):
    """[(label, symbol)] sorted - point-placeable families, optionally
    limited to the given BuiltInCategory names (e.g. the fence post /
    foundation category lists in pymep_fence). Two-level families
    (structural columns) count as placeable too - they place from a
    base level."""
    want = _cat_ids(categories)
    out = []
    for fs in FilteredElementCollector(doc).OfClass(FamilySymbol):
        try:
            fam = fs.Family
            if str(fam.FamilyPlacementType) not in (
                    "OneLevelBased", "WorkPlaneBased",
                    "TwoLevelsBased"):
                continue
            if want:
                cat = fs.Category
                if cat is None or id_value(cat.Id) not in want:
                    continue
            out.append((u"{} : {}".format(element_name(fam),
                                          element_name(fs)), fs))
        except Exception:
            continue
    out.sort(key=lambda t: t[0].lower())
    return out


def symbol_by_label(doc, label, categories=None):
    if not label:
        return None
    for lbl, fs in placeable_symbols(doc, categories):
        if lbl == label:
            return fs
    return None


def all_symbols(doc, categories=None):
    """[(label, symbol)] like placeable_symbols but WITHOUT the
    placement-type filter - the nested / shared types a COLUMN SIZE
    family-type parameter points at show up here."""
    want = _cat_ids(categories)
    out = []
    for fs in FilteredElementCollector(doc).OfClass(FamilySymbol):
        try:
            if want:
                cat = fs.Category
                if cat is None or id_value(cat.Id) not in want:
                    continue
            out.append((u"{} : {}".format(
                element_name(fs.Family), element_name(fs)), fs))
        except Exception:
            continue
    out.sort(key=lambda t: t[0].lower())
    return out


def _fam_type_label(el):
    try:
        return u"{} : {}".format(element_name(el.Family),
                                 element_name(el))
    except Exception:
        return element_name(el)


def _family_type_param_id(doc, symbol, param_name):
    """The FamilyType parameter's id - read off an EXISTING instance
    of the family when one stands in the model, else off a TEMPORARY
    instance placed and rolled straight back."""
    fam_id = id_value(symbol.Family.Id)
    for inst in FilteredElementCollector(doc).OfClass(
            FamilyInstance):
        try:
            if id_value(inst.Symbol.Family.Id) != fam_id:
                continue
            par = inst.LookupParameter(param_name)
            return par.Id if par is not None else None
        except Exception:
            continue
    # no instance yet: probe with a rolled-back placement
    t = Transaction(doc, "pyMEP column size probe")
    pid = None
    try:
        t.Start()
        if not symbol.IsActive:
            symbol.Activate()
            doc.Regenerate()
        lvls = sorted_levels(doc)
        inst = doc.Create.NewFamilyInstance(
            XYZ(0, 0, 0), symbol,
            lvls[0] if lvls else None,
            _structural_type(symbol))
        par = inst.LookupParameter(param_name)
        pid = par.Id if par is not None else None
    except Exception:
        pid = None
    finally:
        try:
            t.RollBack()
        except Exception:
            pass
    return pid


def family_type_options(doc, symbol, param_name):
    """The VALID values of the family's FamilyType parameter (the
    post's COLUMN SIZE options) - pulled from the FAMILY itself, so
    the dropdown shows exactly what the family offers. [] when the
    family has no such parameter."""
    if symbol is None:
        return []
    try:
        pid = _family_type_param_id(doc, symbol, param_name)
        if pid is None:
            return []
        labels = []
        for eid in symbol.Family.GetFamilyTypeParameterValues(pid):
            el = doc.GetElement(eid)
            if el is not None:
                labels.append(_fam_type_label(el))
        return sorted(set(labels), key=lambda x: x.lower())
    except Exception:
        return []


def line_style_name(el):
    """The CurveElement's line style name ('' when unreadable)."""
    try:
        return el.LineStyle.Name or ""
    except Exception:
        return ""


def _structural_type(symbol):
    """Column families place as structural COLUMNS, foundations as
    FOOTINGS - everything else non-structural."""
    try:
        cid = id_value(symbol.Category.Id)
        if hasattr(BuiltInCategory, "OST_StructuralColumns") and \
                cid == int(BuiltInCategory.OST_StructuralColumns):
            return StructuralType.Column
        if hasattr(BuiltInCategory, "OST_StructuralFoundation") and \
                cid == int(BuiltInCategory.OST_StructuralFoundation):
            return StructuralType.Footing
    except Exception:
        pass
    return StructuralType.NonStructural


def find_view3d(doc):
    av = doc.ActiveView
    if isinstance(av, View3D) and not av.IsTemplate:
        return av
    for v in FilteredElementCollector(doc).OfClass(View3D):
        if not v.IsTemplate:
            return v
    return None


def make_intersector(view3d):
    ri = ReferenceIntersector(view3d)
    ri.TargetType = FindReferenceTarget.Face
    return ri


def tessellate(line_el):
    """The line element's curve as [(x, y, z)] - None when it has no
    geometry."""
    curve = line_el.GeometryCurve
    if curve is None:
        return None
    return [(p.X, p.Y, p.Z) for p in curve.Tessellate()]


def ray_start_z(elements):
    """Just above the given elements' bounding boxes -
    ReferenceIntersector silently misses when the origin is
    kilometres from the geometry."""
    tops = []
    for el in elements:
        try:
            bb = el.get_BoundingBox(None)
            if bb is not None:
                tops.append(bb.Max.Z)
        except Exception:
            pass
    return (max(tops) + 10.0) if tops else 30000.0


def topmost_hit(doc, ri, origin, terrain_id):
    """The terrain's TOP surface under a straight-down ray - smallest
    proximity among hits on the terrain element(s) only.
    ``terrain_id`` is one element id or a set/list of them (a config
    may drape over MULTIPLE topos)."""
    if isinstance(terrain_id, (set, frozenset, list, tuple)):
        ids = terrain_id
    else:
        ids = (terrain_id,)
    refs = ri.Find(origin, XYZ(0, 0, -1))
    if refs is None or refs.Count == 0:
        return None
    best = None
    for rc in refs:
        if rc.Proximity <= MIN_HIT_PROXIMITY:
            continue
        ref = rc.GetReference()
        el = doc.GetElement(ref.ElementId)
        if isinstance(el, RevitLinkInstance):
            continue
        if id_value(ref.ElementId) not in ids:
            continue
        if best is None or rc.Proximity < best.Proximity:
            best = rc
    if best is None:
        return None
    return best.GetReference().GlobalPoint


# everything a fence may drape onto; AUTO terrain only considers the
# actual topo categories - floors and roofs everywhere would
# false-positive, they stay pick/named-only
TERRAIN_CAT_NAMES = ("OST_Toposolid", "OST_Topography",
                     "OST_Floors", "OST_Roofs")
AUTO_TERRAIN_CAT_NAMES = ("OST_Toposolid", "OST_Topography")

# AUTO matches a topo whose plan footprint comes this close to the
# lines' footprint
AUTO_TERRAIN_MARGIN_MM = 500.0


def terrain_cat_ids(names=TERRAIN_CAT_NAMES):
    out = set()
    for n in names:
        if hasattr(BuiltInCategory, n):
            out.add(int(getattr(BuiltInCategory, n)))
    return out


def terrain_elements(doc, names=TERRAIN_CAT_NAMES):
    """Every model element of the terrain categories."""
    out = []
    for n in names:
        if not hasattr(BuiltInCategory, n):
            continue
        try:
            for el in FilteredElementCollector(doc).OfCategory(
                    getattr(BuiltInCategory, n)) \
                    .WhereElementIsNotElementType():
                out.append(el)
        except Exception:
            continue
    return out


def terrains_by_name(doc, names):
    """The terrain-category elements whose NAME matches any of the
    stored config names (case-insensitive) - every element carrying
    the name matches, so 'the topo called X' can be several pieces.
    Returns (elements, missing_names)."""
    want = dict((str(n).strip().lower(), str(n)) for n in names or []
                if str(n).strip())
    found, hit = [], set()
    for el in terrain_elements(doc):
        key = element_name(el).strip().lower()
        if key in want:
            found.append(el)
            hit.add(key)
    missing = [want[k] for k in want if k not in hit]
    return found, missing


def _el_bbox2d(el):
    try:
        bb = el.get_BoundingBox(None)
        if bb is None:
            return None
        return (bb.Min.X, bb.Min.Y, bb.Max.X, bb.Max.Y)
    except Exception:
        return None


def auto_terrains(doc, polys):
    """AUTO terrain: every Toposolid / Topography element whose plan
    footprint overlaps the lines' footprint (bounding boxes, with a
    small margin). Over-inclusion is harmless - the ray-cast only
    ever lands on the surfaces that are really under a station."""
    lines_bb = F.bbox2d(polys)
    if lines_bb is None:
        return []
    margin = mm2ft(AUTO_TERRAIN_MARGIN_MM)
    out = []
    for el in terrain_elements(doc, AUTO_TERRAIN_CAT_NAMES):
        if F.boxes_overlap_2d(lines_bb, _el_bbox2d(el), margin):
            out.append(el)
    return out


def resolve_terrains(doc, cfg, polys):
    """The terrain elements a config asks for: (elements, note).
    Empty elements means the CALLER should fall back to picking -
    the note says why (also logged when elements were found)."""
    mode = str(cfg.get("terrain_mode") or "").strip().lower()
    if mode == F.TERRAIN_AUTO:
        els = auto_terrains(doc, polys)
        if not els:
            return [], ("AUTO terrain found no topo under the "
                        "lines - pick it instead")
        return els, "AUTO terrain: {}".format(", ".join(
            sorted(set(element_name(e) for e in els))))
    if mode == F.TERRAIN_NAMED:
        names = cfg.get("terrains") or []
        if not names:
            return [], ("the config names no terrain - pick it "
                        "instead")
        els, missing = terrains_by_name(doc, names)
        note = "config terrain: {}".format(", ".join(
            str(n) for n in names))
        if missing:
            note += " (NOT in the model: {})".format(", ".join(
                str(n) for n in missing))
        if not els:
            return [], note + " - pick it instead"
        return els, note
    return [], ""


def sorted_levels(doc):
    return sorted(
        [l for l in FilteredElementCollector(doc).OfClass(Level)],
        key=lambda l: l.Elevation)


def level_for(levels, z):
    best = None
    for l in levels:
        if l.Elevation <= z + 1e-6:
            best = l
    return best or (levels[0] if levels else None)


def _rotate_about(doc, elem_id, pt, angle):
    if abs(angle) <= 1e-9:
        return
    axis = Line.CreateBound(XYZ(pt.X, pt.Y, pt.Z),
                            XYZ(pt.X, pt.Y, pt.Z + 1.0))
    ElementTransformUtils.RotateElement(doc, elem_id, axis, angle)


def _place_one(doc, symbol, hit, lvl, ang):
    """One draped, rotated instance at the hit point."""
    inst = doc.Create.NewFamilyInstance(
        XYZ(hit.X, hit.Y, hit.Z), symbol, lvl,
        _structural_type(symbol))
    # belt and braces: the level overload usually honours the Z, but
    # forcing the offset makes it certain (structural columns carry
    # it as the base-level offset instead)
    if lvl is not None:
        for bip in ("INSTANCE_FREE_HOST_OFFSET_PARAM",
                    "INSTANCE_ELEVATION_PARAM",
                    "FAMILY_BASE_LEVEL_OFFSET_PARAM"):
            try:
                par = inst.get_Parameter(
                    getattr(BuiltInParameter, bip))
                if par is not None and not par.IsReadOnly:
                    par.Set(hit.Z - lvl.Elevation)
                    break
            except Exception:
                continue
    _rotate_about(doc, inst.Id, hit, ang)
    return inst


def set_panel_width(inst, width_ft, param_name=None):
    """Drive the panel family's width to the bay: the config's own
    spacing parameter NAME first, then the usual suspects; False
    when none exists (the family keeps its native size)."""
    names = ("Width", "width", "Panel Width", "Length", "length")
    if param_name:
        names = (param_name,) + names
    for nm in names:
        try:
            par = inst.LookupParameter(nm)
            if par is not None and not par.IsReadOnly:
                par.Set(width_ft)
                return True
        except Exception:
            continue
    return False


def survey_xy(doc, pt):
    """Internal point -> (EASTING, NORTHING) in the model's SHARED
    (survey) coordinates, internal feet - what a setting-out schedule
    wants. Falls back to the internal XY when the model has no
    project location."""
    try:
        tf = doc.ActiveProjectLocation.GetTotalTransform().Inverse
        sp = tf.OfPoint(pt)
        return sp.X, sp.Y
    except Exception:
        return pt.X, pt.Y


def _is_foundation(el):
    try:
        return el.Category is not None and id_value(
            el.Category.Id) == int(
                BuiltInCategory.OST_StructuralFoundation)
    except Exception:
        return False


def set_coord_params(doc, inst, pt, east_name, north_name):
    """Write the placement point's survey EASTING / NORTHING into
    the instance's parameters of those names. A Length parameter
    gets the internal value (displays in the project units), a Text
    parameter the value in METRES to 3 decimals. Instances without
    the parameters are silently left alone."""
    if inst is None:
        return
    e, n = survey_xy(doc, pt)
    for nm, val in ((east_name, e), (north_name, n)):
        if not nm:
            continue
        try:
            par = inst.LookupParameter(nm)
            if par is None or par.IsReadOnly:
                continue
            if par.StorageType == StorageType.Double:
                par.Set(val)
            elif par.StorageType == StorageType.String:
                par.Set("{:.3f}".format(
                    UnitUtils.ConvertFromInternalUnits(
                        val, UnitTypeId.Meters)))
        except Exception:
            continue


def _toc_params(doc, inst):
    """{parameter name: value} for the TOC equation - the
    foundation's INSTANCE parameters (Doubles as millimetres), its
    TYPE's filling the gaps."""
    out = {}

    def scan(el, overwrite):
        try:
            pars = el.Parameters
        except Exception:
            return
        for par in pars:
            try:
                nm = par.Definition.Name
                if not par.HasValue:
                    continue
                st = par.StorageType
                if st == StorageType.Double:
                    if overwrite or nm not in out:
                        out[nm] = UnitUtils.ConvertFromInternalUnits(
                            par.AsDouble(), UnitTypeId.Millimeters)
                elif st == StorageType.Integer:
                    out.setdefault(nm, float(par.AsInteger()))
                elif st == StorageType.String:
                    try:
                        out.setdefault(nm, float(par.AsString()))
                    except Exception:
                        pass
            except Exception:
                continue

    scan(inst, True)
    try:
        typ = doc.GetElement(inst.GetTypeId())
        if typ is not None:
            scan(typ, False)
    except Exception:
        pass
    return out


def _survey_zen(doc, pt):
    """(z_mm, e_m, n_m) of a placement point in the SURVEY basis."""
    try:
        tf = doc.ActiveProjectLocation.GetTotalTransform().Inverse
        sp = tf.OfPoint(pt)
    except Exception:
        sp = pt
    return (UnitUtils.ConvertFromInternalUnits(
                sp.Z, UnitTypeId.Millimeters),
            UnitUtils.ConvertFromInternalUnits(
                sp.X, UnitTypeId.Meters),
            UnitUtils.ConvertFromInternalUnits(
                sp.Y, UnitTypeId.Meters))


def _note_problem(problems, msg):
    if problems is not None and msg not in problems and             len(problems) < 5:
        problems.append(msg)


def _set_type_param(doc, inst, name, label, problems=None):
    """A FAMILY TYPE parameter (the post's COLUMN SIZE): set it to
    the type picked in the config, matched by 'Family : Type' or
    the plain type name."""
    try:
        par = inst.LookupParameter(name)
        if par is None or par.IsReadOnly:
            _note_problem(problems,
                          "parameter '{}' is missing or read-only "
                          "on the post".format(name))
            return
        if par.StorageType != StorageType.ElementId:
            _note_problem(problems,
                          "'{}' is not a type parameter".format(
                              name))
            return
        # the family's own options first (nested types), then any
        # loose symbol carrying the label
        try:
            for eid in inst.Symbol.Family \
                    .GetFamilyTypeParameterValues(par.Id):
                el = doc.GetElement(eid)
                if el is not None and (
                        _fam_type_label(el) == label or
                        element_name(el) == label):
                    par.Set(eid)
                    return
        except Exception:
            pass
        for fs in FilteredElementCollector(doc).OfClass(
                FamilySymbol):
            try:
                if _fam_type_label(fs) == label or \
                        element_name(fs) == label:
                    par.Set(fs.Id)
                    return
            except Exception:
                continue
        _note_problem(problems,
                      "column size '{}' is not among the family's "
                      "options".format(label))
    except Exception:
        pass


def post_dims_of(cfg, use_ends):
    """The post dimension fields for a station: the end_* set when
    the station is an END and the posts' own 'keep the same' tick
    is OFF."""
    legacy = cfg.get("same_ends", True)
    ends = use_ends and not cfg.get("same_end_posts", legacy)
    pre = "end_post_" if ends else "post_"
    return {"col_size": str(cfg.get(pre + "col_size") or ""),
            "fnd_depth": str(cfg.get(pre + "fnd_depth") or ""),
            "height": str(cfg.get(pre + "height") or "")}


def fnd_dims_of(cfg, use_ends):
    legacy = cfg.get("same_ends", True)
    ends = use_ends and not cfg.get("same_end_foundations", legacy)
    pre = "end_fnd_" if ends else "fnd_"
    return {"embedment": str(cfg.get(pre + "embedment") or ""),
            "diameter": str(cfg.get(pre + "diameter") or ""),
            "depth": str(cfg.get(pre + "depth") or "")}


def apply_post_dims(doc, inst, pt, dims, problems=None):
    """Write the config's POST dimensions: Column Size (family
    type), Foundation Depth, Height. Empty fields leave the
    parameter alone; number fields take equations too."""
    if inst is None or not dims:
        return
    set_assignments(doc, inst, pt,
                    [(nm, v) for nm, v in
                     (("Foundation Depth", dims.get("fnd_depth")),
                      ("Height", dims.get("height")))
                     if (v or "").strip()], problems)
    label = (dims.get("col_size") or "").strip()
    if label:
        _set_type_param(doc, inst, F.COL_SIZE_PARAM, label,
                        problems)


def apply_fnd_dims(doc, inst, pt, dims, problems=None):
    """Write the config's FOUNDATION dimensions: Embedment,
    Diameter, Depth - empty fields leave the parameter alone."""
    if inst is None or not dims:
        return
    set_assignments(doc, inst, pt,
                    [(nm, v) for nm, v in
                     (("Embedment", dims.get("embedment")),
                      ("Diameter", dims.get("diameter")),
                      ("Depth", dims.get("depth")))
                     if (v or "").strip()], problems)


def set_assignments(doc, inst, pt, assigns, problems=None):
    """Write the config's 'Parameter = equation' lines onto a POST:
    each right-hand side may use the post's OWN parameters by name,
    z / e / n from the survey-basis point, and quoted TEXT. Numbers
    land as millimetres on Length parameters, whole numbers on
    Integer ones, '{:g}' text on Text ones; quoted text needs a
    Text parameter. Problems are collected, placement never
    fails."""
    if inst is None or not assigns:
        return
    z_mm, e_m, n_m = _survey_zen(doc, pt)
    pvals = _toc_params(doc, inst)
    for name, expr in assigns:
        try:
            val = F.eval_assign(expr, z_mm, e_m, n_m, pvals)
        except ValueError as ex:
            _note_problem(problems, "'{}': {}".format(name, ex))
            continue
        try:
            par = inst.LookupParameter(name)
            if par is None or par.IsReadOnly:
                _note_problem(problems,
                              "parameter '{}' is missing or "
                              "read-only on the post".format(name))
                continue
            if isinstance(val, str) or                     type(val).__name__ == "unicode":
                if par.StorageType == StorageType.String:
                    par.Set(u"{}".format(val))
                else:
                    _note_problem(problems,
                                  "'{}' gets TEXT but is not a "
                                  "text parameter".format(name))
            elif par.StorageType == StorageType.Double:
                par.Set(mm2ft(val))
            elif par.StorageType == StorageType.Integer:
                par.Set(int(round(val)))
            elif par.StorageType == StorageType.String:
                par.Set(u"{:g}".format(val))
        except Exception:
            continue


def set_toc(doc, inst, pt, param_name, formula, problems=None):
    """TOC: evaluate the equation at the placement point and write
    the result into the named instance parameter. The equation may
    use the foundation's OWN parameters by name (instance first,
    then type - lengths in mm) plus z / e / n from the SURVEY-basis
    point. A Length parameter gets the value as millimetres
    (converted to internal), a Text parameter the number to 1
    decimal. The FIRST failure reason is appended to ``problems``
    when a list is given."""
    if inst is None or not param_name:
        return
    try:
        z_mm, e_m, n_m = _survey_zen(doc, pt)
        val = F.eval_toc(formula, z_mm, e_m, n_m,
                         _toc_params(doc, inst))
    except Exception as ex:
        if problems is not None and not problems:
            problems.append("{}".format(ex))
        return
    try:
        par = inst.LookupParameter(param_name)
        if par is None or par.IsReadOnly:
            if problems is not None and not problems:
                problems.append(
                    "parameter '{}' is missing or read-only on the "
                    "foundation".format(param_name))
            return
        if par.StorageType == StorageType.Double:
            par.Set(mm2ft(val))
        elif par.StorageType == StorageType.String:
            par.Set("{:.1f}".format(val))
    except Exception:
        pass


def set_mark(inst, value):
    """Write the Mark instance parameter - silently skipped when the
    element has none or it is read-only."""
    if inst is None or not value:
        return
    try:
        par = inst.LookupParameter("Mark")
        if par is None:
            par = inst.get_Parameter(
                BuiltInParameter.ALL_MODEL_MARK)
        if par is not None and not par.IsReadOnly:
            par.Set(u"{}".format(value))
    except Exception:
        pass


def place_panels(doc, panel_symbol, poly, post_stations, terrain_id,
                 ri, ray_z, levels, records, missed,
                 width_param=None, skip=None):
    """One panel per BAY between consecutive posts: placed at the
    bay's CENTRE, aligned to the line, draped, width driven to the
    bay length. ``skip`` spans (corner post -> its DOUBLE post) get
    no panel. Appends to records / missed in place."""
    if panel_symbol is None or len(post_stations) < 2:
        return
    try:
        if not panel_symbol.IsActive:
            panel_symbol.Activate()
            doc.Regenerate()
    except Exception:
        pass
    for mid, width in F.panel_bays(sorted(post_stations),
                                   mm2ft(F.PANEL_MIN_MM),
                                   skip=skip):
        p, tang = F.point_at(poly, mid)
        hit = topmost_hit(doc, ri, XYZ(p[0], p[1], ray_z), terrain_id)
        if hit is None:
            missed.append(mid)
            continue
        lvl = level_for(levels, hit.Z)
        ang = math.atan2(tang[1], tang[0])
        try:
            inst = _place_one(doc, panel_symbol, hit, lvl, ang)
            set_panel_width(inst, width, width_param)
            records.append({"uid": inst.UniqueId, "station_ft": mid,
                            "angle": ang, "panel": True})
        except Exception:
            pass


def station_pick(dists, length, primary, secondary,
                 end_primary=None, end_secondary=None,
                 same_posts=True, same_fnds=True, tol=1e-6):
    """The per-station family chooser for place_instances: endpoint
    stations (0 / length) swap in the END post and/or the END
    foundation - each behind its OWN 'keep the same' tick, so the
    ends can mix a different foundation under the same post and
    vice versa. Either slot may be None ('none' in the config)."""
    if same_posts and same_fnds:
        return lambda d: (primary, secondary)
    ends = set(d for d in dists
               if d <= tol or d >= length - tol)

    def pick(d):
        if d in ends:
            return (primary if same_posts else end_primary,
                    secondary if same_fnds else end_secondary)
        return primary, secondary
    return pick


def place_instances(doc, pick, poly, dists, terrain_id, ri, ray_z,
                    levels, extra_rot=0.0, panel_symbol=None,
                    panel_width_param=None, coord_params=None,
                    marks=None, toc=None, toc_problems=None,
                    cfg=None):
    """Place at every station, draped and rotated - the line's
    direction plus ``extra_rot`` (radians, the config's custom
    rotation). ``pick(d)`` returns (symbol, foundation_symbol) for
    the station - either may be None (a station picking (None,
    None) is silently left empty). ``coord_params`` = (easting,
    northing) parameter names - every FOUNDATION placed gets the
    survey coordinates written into them. Runs inside the caller's
    open Transaction. Returns (records, missed, failed, why):
    records = [{"uid", "station_ft", "angle"(, "foundation_uid")}]
    for the registry, missed = stations with no terrain hit, failed
    = count of placement errors (first reason in why)."""
    records, missed, failed = [], [], 0
    failed_reason = [None]
    activated = set()
    length = F.poly_length(poly)
    end_sts = set(d for d in dists
                  if d <= 1e-6 or d >= length - 1e-6)
    for d_i, d in enumerate(dists):
        mark = marks[d_i] if marks and d_i < len(marks) else None
        symbol, foundation_symbol = pick(d)
        if symbol is None and foundation_symbol is not None:
            symbol, foundation_symbol = foundation_symbol, None
        if symbol is None:
            continue
        for s in (symbol, foundation_symbol):
            if s is None or id_value(s.Id) in activated:
                continue
            activated.add(id_value(s.Id))
            try:
                if not s.IsActive:
                    s.Activate()
                    doc.Regenerate()
            except Exception:
                pass
        p, tang = F.point_at(poly, d)
        hit = topmost_hit(doc, ri, XYZ(p[0], p[1], ray_z), terrain_id)
        if hit is None:
            missed.append(d)
            continue
        lvl = level_for(levels, hit.Z)
        ang = math.atan2(tang[1], tang[0]) + extra_rot
        try:
            inst = _place_one(doc, symbol, hit, lvl, ang)
        except Exception as ex:
            failed += 1
            if failed_reason[0] is None:
                failed_reason[0] = "{}".format(ex)
            continue
        use_ends = d in end_sts
        if _is_foundation(inst):
            if coord_params:
                set_coord_params(doc, inst, hit, coord_params[0],
                                 coord_params[1])
            if toc:
                set_toc(doc, inst, hit, toc[0], toc[1],
                        toc_problems)
            if cfg is not None:
                apply_fnd_dims(doc, inst, hit,
                               fnd_dims_of(cfg, use_ends),
                               toc_problems)
        elif cfg is not None:
            apply_post_dims(doc, inst, hit,
                            post_dims_of(cfg, use_ends),
                            toc_problems)
        set_mark(inst, mark)
        rec = {"uid": inst.UniqueId, "station_ft": d, "angle": ang}
        if foundation_symbol is not None:
            try:
                f_inst = _place_one(doc, foundation_symbol, hit, lvl,
                                    ang)
                rec["foundation_uid"] = f_inst.UniqueId
                if coord_params:
                    set_coord_params(doc, f_inst, hit,
                                     coord_params[0],
                                     coord_params[1])
                if toc:
                    set_toc(doc, f_inst, hit, toc[0], toc[1],
                            toc_problems)
                if cfg is not None:
                    apply_fnd_dims(doc, f_inst, hit,
                                   fnd_dims_of(cfg, use_ends),
                                   toc_problems)
                set_mark(f_inst, mark)
            except Exception as ex:
                failed += 1
                if failed_reason[0] is None:
                    failed_reason[0] = "foundation: {}".format(ex)
        records.append(rec)
    place_panels(doc, panel_symbol, poly, dists, terrain_id, ri,
                 ray_z, levels, records, missed, panel_width_param)
    return records, missed, failed, failed_reason[0]


def _move_one(doc, el, hit, rot_delta):
    loc = el.Location.Point
    delta = XYZ(hit.X - loc.X, hit.Y - loc.Y, hit.Z - loc.Z)
    if delta.GetLength() > 1e-9:
        ElementTransformUtils.MoveElement(doc, el.Id, delta)
    _rotate_about(doc, el.Id, hit, rot_delta)


def move_instances(doc, pairs, poly, terrain_id, ri, ray_z,
                   extra_rot=0.0, coord_params=None, toc=None,
                   toc_problems=None, cfg=None):
    """MOVE each stored instance (and its foundation, when the record
    has one that still exists) to its new station: pairs =
    [(instance_dict, element, new_station)]. The rotation applied is
    the DELTA from the stored angle (which includes any config
    rotation), so user tweaks on top survive AND a changed config
    rotation lands. ``coord_params`` = (easting, northing) parameter
    names - moved FOUNDATIONS get their survey coordinates
    refreshed. Returns (records, missed, failed) like
    place_instances."""
    records, missed, failed = [], [], 0
    length = F.poly_length(poly)
    for inst_d, el, d in pairs:
        p, tang = F.point_at(poly, d)
        hit = topmost_hit(doc, ri, XYZ(p[0], p[1], ray_z), terrain_id)
        if hit is None:
            missed.append(d)
            records.append(inst_d)      # stays where it is
            continue
        try:
            old_ang = float(inst_d.get("angle") or 0.0)
            new_ang = math.atan2(tang[1], tang[0]) + extra_rot
            _move_one(doc, el, hit, new_ang - old_ang)
            use_ends = d <= 1e-6 or d >= length - 1e-6
            if _is_foundation(el):
                if coord_params:
                    set_coord_params(doc, el, hit, coord_params[0],
                                     coord_params[1])
                if toc:
                    set_toc(doc, el, hit, toc[0], toc[1],
                            toc_problems)
                if cfg is not None:
                    apply_fnd_dims(doc, el, hit,
                                   fnd_dims_of(cfg, use_ends),
                                   toc_problems)
            elif cfg is not None:
                apply_post_dims(doc, el, hit,
                                post_dims_of(cfg, use_ends),
                                toc_problems)
            rec = {"uid": inst_d.get("uid"), "station_ft": d,
                   "angle": new_ang}
            f_uid = inst_d.get("foundation_uid")
            if f_uid:
                try:
                    f_el = doc.GetElement(f_uid)
                    if f_el is not None:
                        _move_one(doc, f_el, hit, new_ang - old_ang)
                        rec["foundation_uid"] = f_uid
                        if coord_params:
                            set_coord_params(doc, f_el, hit,
                                             coord_params[0],
                                             coord_params[1])
                        if toc:
                            set_toc(doc, f_el, hit, toc[0],
                                    toc[1], toc_problems)
                        if cfg is not None:
                            apply_fnd_dims(doc, f_el, hit,
                                           fnd_dims_of(cfg,
                                                       use_ends),
                                           toc_problems)
                except Exception:
                    failed += 1
            records.append(rec)
        except Exception:
            failed += 1
            records.append(inst_d)
    return records, missed, failed


# ---------------------------------------------------------------------------
# fence NETWORK - shared by Fence Network and Update Fence
# ---------------------------------------------------------------------------
NODE_TOL_MM = 50.0      # endpoints this close share a corner node


def mm2ft(mm):
    return UnitUtils.ConvertToInternalUnits(float(mm),
                                            UnitTypeId.Millimeters)


def symbol_diameter_ft(symbol):
    """The family type's 'Diameter' parameter (internal feet) - None
    when the family has no such parameter or it is not set. This is
    what sizes the touching circles in Fence Network."""
    if symbol is None:
        return None
    for nm in ("Diameter", "diameter", "DIA", "Dia"):
        try:
            par = symbol.LookupParameter(nm)
            if par is not None and par.HasValue:
                v = par.AsDouble()
                if v and v > 0:
                    return v
        except Exception:
            continue
    return None


def model_network(doc, line_els, terrain, cfgs, view3d, say=None,
                  mark_opts=None, toc_opts=None):
    """Model a fence NETWORK inside the caller's open Transaction.
    ``terrain`` is one element or a list of them - stations drape
    onto whichever of the terrain surfaces the ray actually hits.

    Strategy: CORNERS FIRST, then the in-between posts.

      - a corner is ANY intersection of the lines: shared endpoints,
        mid-line crossings, and T-junctions (a line ending on
        another's run);
      - at each corner the incident configuration nearest the TOP of
        the config list wins, and its END post + foundation stand
        there;
      - when the winner's END PRIORITY is ON, every OTHER line that
        TERMINATES at that corner places its own end post right next
        to the winner's, on its own line - offset so the two end
        foundation circles TOUCH (family 'Diameter' parameters);
      - each line then fills in between at its config's spacing,
        counted from the corner (or from its double post) - the
        leftover only SHORTENS the last bay, so posts never double
        up along a run.

    Returns (records, notes, placed, missed). Raises ValueError
    (before anything is placed) on the sanity cap or when nothing is
    mappable. ``mark_opts`` = (enabled, prefix) from
    F.mark_settings: when enabled EVERY post + foundation gets a
    Mark, numbered clockwise from the top-priority line.
    ``toc_opts`` = (param name, equation) from F.toc_settings:
    every foundation gets the evaluated TOC written."""
    import pymep_fence as F

    notes = []

    def note(msg):
        notes.append(msg)
        if say:
            say(msg)

    ri = make_intersector(view3d)
    levels = sorted_levels(doc)
    terrains = list(terrain) if isinstance(
        terrain, (list, tuple)) else [terrain]
    terrain_id = set(id_value(t.Id) for t in terrains)
    ray_z = ray_start_z(terrains + list(line_els))
    tol = mm2ft(NODE_TOL_MM)

    # ---- lines -> configs by style -----------------------------------
    edges = []
    for el in line_els:
        style = line_style_name(el)
        poly = tessellate(el)
        if not poly or F.poly_length(poly) <= 1e-9:
            note("line {}: no length - skipped".format(
                id_value(el.Id)))
            continue
        m = F.config_for_style(cfgs, style)
        if m is None:
            note("line {} ('{}'): NO configuration is bound to this "
                 "line style - skipped".format(
                     id_value(el.Id), style or "?"))
            continue
        name, cfg = m
        edges.append({"el": el, "style": style, "name": name,
                      "cfg": cfg, "poly": poly,
                      "length": F.poly_length(poly)})
    if not edges:
        raise ValueError("no line could be mapped to a "
                         "configuration - bind line styles in "
                         "Fence Configs")

    # foundation radii from the families' Diameter parameter
    def _fnd_radius(cfg, use_ends):
        lbl = F.end_families(cfg)[1] if use_ends \
            else (cfg.get("foundation") or "")
        if not lbl:
            return None
        dia = symbol_diameter_ft(
            symbol_by_label(doc, lbl, F.FOUNDATION_CATEGORIES))
        return dia / 2.0 if dia else None

    # ---- corners: EVERY intersection of the lines --------------------
    # candidates carry which (edge, station) they pin
    cands, pins = [], []

    def cand(x, y, e_i, st):
        cands.append((x, y))
        pins.append((e_i, st))

    for i, e in enumerate(edges):
        cand(e["poly"][0][0], e["poly"][0][1], i, 0.0)
        cand(e["poly"][-1][0], e["poly"][-1][1], i, e["length"])
    for i in range(len(edges)):
        for j in range(i + 1, len(edges)):
            for da, db, x, y in F.poly_intersections(
                    edges[i]["poly"], edges[j]["poly"]):
                cand(x, y, i, da)
                cand(x, y, j, db)
    # T-junctions with a snap gap: an endpoint NEAR another line
    for i, e in enumerate(edges):
        for pt, _st in ((e["poly"][0], 0.0),
                        (e["poly"][-1], e["length"])):
            for j, o in enumerate(edges):
                if j == i:
                    continue
                pr = F.project_to_poly(o["poly"], pt[0], pt[1])
                if pr is not None and pr[1] <= tol and \
                        tol < pr[0] < o["length"] - tol:
                    cand(pr[2], pr[3], j, pr[0])

    centers, idx = F.cluster_nodes(cands, tol)
    node_pins = {}          # ni -> {e_i: sorted set of stations}
    for c_i, ni in enumerate(idx):
        e_i, st = pins[c_i]
        node_pins.setdefault(ni, {}).setdefault(e_i, set()).add(st)

    nodes = []
    for ni in range(len(centers)):
        per = node_pins.get(ni) or {}
        incident = [(edges[e_i]["name"], edges[e_i]["cfg"])
                    for e_i in sorted(per.keys())]
        win_name, win = F.pick_priority(incident)
        # the corner post ALIGNS to the PRIORITY line: the winning
        # config's own edge at this node sets the tangent
        pick_e = None
        for e_i in sorted(per.keys()):
            if edges[e_i]["name"] == win_name:
                pick_e = e_i
                break
        if pick_e is None:
            pick_e = sorted(per.keys())[0]
        st = sorted(per[pick_e])[0]
        tangent = F.point_at(edges[pick_e]["poly"], st)[1]
        nodes.append({"xy": centers[ni], "cfg": win,
                      "name": win_name, "tangent": tangent,
                      "r": _fnd_radius(win, True),
                      "end_priority": bool(win.get("end_priority"))})

    # ---- plan the DOUBLE posts + per-edge node marks ------------------
    # doubles: winner has END PRIORITY on -> every OTHER config's line
    # TERMINATING there sets its own end post tangent to the winner's
    # (ONE post next to the big one - nothing else touches)
    doubles = []            # (e_i, station, ni)
    dbl_at = {}             # (e_i, ni) -> the double's station
    for ni, nd in enumerate(nodes):
        if not nd["end_priority"]:
            continue
        for e_i, sts in (node_pins.get(ni) or {}).items():
            e = edges[e_i]
            if e["name"] == nd["name"]:
                continue
            r_end = _fnd_radius(e["cfg"], True)
            if nd["r"] is None or r_end is None:
                note("corner at node {}: no Diameter on a family - "
                     "no double post for line {}".format(
                         ni + 1, id_value(e["el"].Id)))
                continue
            d = nd["r"] + r_end
            for st in sts:
                if st <= tol:                       # line starts here
                    dbl = min(d, e["length"])
                elif st >= e["length"] - tol:       # line ends here
                    dbl = max(e["length"] - d, 0.0)
                else:
                    continue                        # passes through
                doubles.append((e_i, dbl, ni))
                dbl_at[(e_i, ni)] = dbl

    # ---- in-between stations per edge segment ------------------------
    total = len(nodes) + len(doubles)
    for e_i, e in enumerate(edges):
        marks = []
        for ni, per in node_pins.items():
            if e_i in per:
                for st in per[e_i]:
                    marks.append((min(max(st, 0.0), e["length"]), ni))
        marks.sort()
        merged = []
        for st, ni in marks:
            if merged and abs(st - merged[-1][0]) <= tol:
                continue
            merged.append((st, ni))
        r_edge = _fnd_radius(e["cfg"], False)
        r_end_own = _fnd_radius(e["cfg"], True)
        spacing_ft = mm2ft(e["cfg"]["spacing_mm"])
        sts_all = []
        for k in range(len(merged) - 1):
            s0, n0 = merged[k]
            s1, n1 = merged[k + 1]
            sub = s1 - s0
            if sub <= tol:
                continue

            # the spacing counts from the near boundary's DOUBLE post
            # when one sits on this line; the far end stops clear of
            # the far double / corner circle - the leftover only
            # SHORTENS the last bay
            def _anchor(ni2, boundary):
                st2 = dbl_at.get((e_i, ni2))
                return abs(st2 - boundary) if st2 is not None else 0.0

            def _far_clear(ni2, boundary):
                st2 = dbl_at.get((e_i, ni2))
                if st2 is not None:
                    gap = (r_end_own + r_edge) \
                        if (r_end_own is not None and
                            r_edge is not None) else spacing_ft
                    return abs(st2 - boundary) + gap
                rn = nodes[ni2]["r"]
                if rn is not None and r_edge is not None:
                    return rn + r_edge
                return None

            p0 = int(nodes[n0]["cfg"].get("priority") or 99)
            p1 = int(nodes[n1]["cfg"].get("priority") or 99)
            if p0 <= p1:
                seg = F.edge_stations(sub, spacing_ft,
                                      _anchor(n0, s0),
                                      _far_clear(n1, s1))
                sts_all.extend(s0 + d for d in seg)
            else:
                seg = F.edge_stations(sub, spacing_ft,
                                      _anchor(n1, s1),
                                      _far_clear(n0, s0))
                sts_all.extend(s1 - d for d in seg)
        e["stations"] = sorted(sts_all)
        e["node_marks"] = merged
        e["post_stations"] = sorted(
            [st for st, _ni in merged] +
            [st for (ei2, st, _ni) in doubles if ei2 == e_i] +
            sts_all)
        # the corner-post-to-DOUBLE-post bay gets a (short) panel
        # like any other bay - the fence is continuous through a
        # style-to-style connection
        n_panels = len(F.panel_bays(e["post_stations"],
                                    mm2ft(F.PANEL_MIN_MM))) \
            if e["cfg"].get("panel") else 0
        total += len(e["stations"]) + n_panels
        note("line {} ('{}' -> {}): {} corner(s), {} in-between "
             "post(s){}".format(
                 id_value(e["el"].Id), e["style"], e["name"],
                 len(merged), len(e["stations"]),
                 ", {} panel(s)".format(n_panels)
                 if n_panels else ""))
    if total > F.MAX_INSTANCES:
        raise ValueError("{} instances would be placed - over the "
                         "{} sanity cap. Check the spacing.".format(
                             total, F.MAX_INSTANCES))

    # ---- MARK numbering: clockwise from the top-priority line --------
    mark_on, mark_prefix = mark_opts if mark_opts else (False, "")
    net_marks = {}
    if mark_on:
        m_edges = {}
        for e_i, e in enumerate(edges):
            posts = [(st, "node", ni)
                     for st, ni in e.get("node_marks") or []]
            posts += [(st, "double", (ei2, ni))
                      for ei2, st, ni in doubles if ei2 == e_i]
            posts += [(st, "post", (e_i, st))
                      for st in e["stations"]]
            m_edges[e_i] = {"poly": e["poly"],
                            "posts": sorted(posts,
                                            key=lambda p: p[0]),
                            "group": e["name"]}
        start_e = min(range(len(edges)),
                      key=lambda i: (int(edges[i]["cfg"].get(
                          "priority") or 99), i))
        try:
            net_marks = F.network_marks(m_edges, centers, start_e,
                                        tol, mark_prefix)
        except Exception as ex:
            note("! MARK numbering failed: {}".format(ex))
            net_marks = {}

    # ---- place: corners FIRST, then doubles, then in-between ---------
    records = []
    missed = [0]
    toc_problems = []
    sym_cache = {}

    def _sym(label, cats, what):
        key = (label, what)
        if key in sym_cache:
            return sym_cache[key]
        sym = symbol_by_label(doc, label, cats) if label else None
        if label and sym is None:
            note("! {} family '{}' is NOT in this model - "
                 "skipped".format(what, label))
        sym_cache[key] = sym
        return sym

    def put(x, y, tang, cfg, use_ends, mark=None):
        if use_ends:
            post_lbl, fnd_lbl = F.end_families(cfg)
        else:
            post_lbl = cfg.get("post") or ""
            fnd_lbl = cfg.get("foundation") or ""
        post_sym = _sym(post_lbl, F.POST_CATEGORIES, "post")
        fnd_sym = _sym(fnd_lbl, F.FOUNDATION_CATEGORIES,
                       "foundation")
        primary, secondary = post_sym, fnd_sym
        if primary is None:
            primary, secondary = fnd_sym, None
        if primary is None:
            return
        hit = topmost_hit(doc, ri, XYZ(x, y, ray_z), terrain_id)
        if hit is None:
            missed[0] += 1
            return
        lvl = level_for(levels, hit.Z)
        ang = math.atan2(tang[1], tang[0]) + math.radians(
            float(cfg.get("rotation_deg") or 0.0))
        for sy in (primary, secondary):
            if sy is None:
                continue
            try:
                if not sy.IsActive:
                    sy.Activate()
                    doc.Regenerate()
            except Exception:
                pass
        try:
            inst = _place_one(doc, primary, hit, lvl, ang)
        except Exception as ex:
            note("! placement failed: {}".format(ex))
            return
        coords = (cfg.get("easting_param") or F.EASTING_PARAM,
                  cfg.get("northing_param") or F.NORTHING_PARAM)
        if _is_foundation(inst):
            set_coord_params(doc, inst, hit, coords[0], coords[1])
            if toc_opts:
                set_toc(doc, inst, hit, toc_opts[0], toc_opts[1],
                        toc_problems)
            apply_fnd_dims(doc, inst, hit,
                           fnd_dims_of(cfg, use_ends),
                           toc_problems)
        else:
            apply_post_dims(doc, inst, hit,
                            post_dims_of(cfg, use_ends),
                            toc_problems)
        if mark_on:
            set_mark(inst, mark)
        rec = {"uid": inst.UniqueId}
        if secondary is not None:
            try:
                f_inst = _place_one(doc, secondary, hit, lvl, ang)
                rec["foundation_uid"] = f_inst.UniqueId
                set_coord_params(doc, f_inst, hit, coords[0],
                                 coords[1])
                if toc_opts:
                    set_toc(doc, f_inst, hit, toc_opts[0],
                            toc_opts[1], toc_problems)
                apply_fnd_dims(doc, f_inst, hit,
                               fnd_dims_of(cfg, use_ends),
                               toc_problems)
                if mark_on:
                    set_mark(f_inst, mark)
            except Exception as ex:
                note("! foundation placement failed: {}".format(ex))
        records.append(rec)

    for ni, nd in enumerate(nodes):
        put(nd["xy"][0], nd["xy"][1], nd["tangent"], nd["cfg"],
            True, net_marks.get(("node", ni)))
    for e_i, st, ni in doubles:
        e = edges[e_i]
        pnt, tang = F.point_at(e["poly"], st)
        put(pnt[0], pnt[1], tang, e["cfg"], True,
            net_marks.get(("double", (e_i, ni))))
    for e_i, e in enumerate(edges):
        for d in e["stations"]:
            pnt, tang = F.point_at(e["poly"], d)
            put(pnt[0], pnt[1], tang, e["cfg"], False,
                net_marks.get(("post", (e_i, d))))
    # panels: one per bay, centred, aligned to the line, width = bay
    net_missed = []
    for e in edges:
        pnl_lbl = e["cfg"].get("panel") or ""
        if not pnl_lbl:
            continue
        pnl_sym = _sym(pnl_lbl, F.PANEL_CATEGORIES, "panel")
        if pnl_sym is None:
            continue
        place_panels(doc, pnl_sym, e["poly"], e["post_stations"],
                     terrain_id, ri, ray_z, levels, records,
                     net_missed,
                     e["cfg"].get("panel_width_param") or None)
    missed[0] += len(net_missed)
    if toc_problems:
        note("! TOC: {}".format(toc_problems[0]))

    return records, notes, len(records), missed[0]
