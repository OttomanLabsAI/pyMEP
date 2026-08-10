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
    FamilySymbol,
    FilteredElementCollector,
    FindReferenceTarget,
    Level,
    Line,
    ReferenceIntersector,
    RevitLinkInstance,
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
    """The picked terrain's TOP surface under a straight-down ray -
    smallest proximity among hits on that element only."""
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
        if id_value(ref.ElementId) != terrain_id:
            continue
        if best is None or rc.Proximity < best.Proximity:
            best = rc
    if best is None:
        return None
    return best.GetReference().GlobalPoint


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


def station_pick(dists, length, primary, secondary,
                 end_primary=None, end_secondary=None,
                 same_ends=True, tol=1e-6):
    """The per-station family chooser for place_instances: endpoint
    stations (0 / length) get the END pair when ``same_ends`` is
    off, everything else the in-between pair. Either slot may be
    None ('none' in the config)."""
    if same_ends:
        return lambda d: (primary, secondary)
    ends = set(d for d in dists
               if d <= tol or d >= length - tol)

    def pick(d):
        if d in ends:
            return end_primary, end_secondary
        return primary, secondary
    return pick


def place_instances(doc, pick, poly, dists, terrain_id, ri, ray_z,
                    levels, extra_rot=0.0):
    """Place at every station, draped and rotated - the line's
    direction plus ``extra_rot`` (radians, the config's custom
    rotation). ``pick(d)`` returns (symbol, foundation_symbol) for
    the station - either may be None (a station picking (None,
    None) is silently left empty). Runs inside the caller's open
    Transaction. Returns (records, missed, failed, why): records =
    [{"uid", "station_ft", "angle"(, "foundation_uid")}] for the
    registry, missed = stations with no terrain hit, failed = count
    of placement errors (first reason in why)."""
    records, missed, failed = [], [], 0
    failed_reason = [None]
    activated = set()
    for d in dists:
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
        rec = {"uid": inst.UniqueId, "station_ft": d, "angle": ang}
        if foundation_symbol is not None:
            try:
                f_inst = _place_one(doc, foundation_symbol, hit, lvl,
                                    ang)
                rec["foundation_uid"] = f_inst.UniqueId
            except Exception as ex:
                failed += 1
                if failed_reason[0] is None:
                    failed_reason[0] = "foundation: {}".format(ex)
        records.append(rec)
    return records, missed, failed, failed_reason[0]


def _move_one(doc, el, hit, rot_delta):
    loc = el.Location.Point
    delta = XYZ(hit.X - loc.X, hit.Y - loc.Y, hit.Z - loc.Z)
    if delta.GetLength() > 1e-9:
        ElementTransformUtils.MoveElement(doc, el.Id, delta)
    _rotate_about(doc, el.Id, hit, rot_delta)


def move_instances(doc, pairs, poly, terrain_id, ri, ray_z,
                   extra_rot=0.0):
    """MOVE each stored instance (and its foundation, when the record
    has one that still exists) to its new station: pairs =
    [(instance_dict, element, new_station)]. The rotation applied is
    the DELTA from the stored angle (which includes any config
    rotation), so user tweaks on top survive AND a changed config
    rotation lands. Returns (records, missed, failed) like
    place_instances."""
    records, missed, failed = [], [], 0
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
            rec = {"uid": inst_d.get("uid"), "station_ft": d,
                   "angle": new_ang}
            f_uid = inst_d.get("foundation_uid")
            if f_uid:
                try:
                    f_el = doc.GetElement(f_uid)
                    if f_el is not None:
                        _move_one(doc, f_el, hit, new_ang - old_ang)
                        rec["foundation_uid"] = f_uid
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


def model_network(doc, line_els, terrain, cfgs, view3d, say=None):
    """Model a fence NETWORK inside the caller's open Transaction.

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
      - each line then fills in between at its config's spacing, the
        first post touching the nearest circle (corner post or
        double post); a family without a Diameter parameter drops
        that run to normal spacing with its first post skipped.

    Returns (records, notes, placed, missed). Raises ValueError
    (before anything is placed) on the sanity cap or when nothing is
    mappable."""
    import pymep_fence as F

    notes = []

    def note(msg):
        notes.append(msg)
        if say:
            say(msg)

    ri = make_intersector(view3d)
    levels = sorted_levels(doc)
    terrain_id = id_value(terrain.Id)
    ray_z = ray_start_z([terrain] + list(line_els))
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
        first_e = sorted(per.keys())[0]
        first_st = sorted(per[first_e])[0]
        tangent = F.point_at(edges[first_e]["poly"], first_st)[1]
        win_name, win = F.pick_priority(incident)
        nodes.append({"xy": centers[ni], "cfg": win,
                      "name": win_name, "tangent": tangent,
                      "r": _fnd_radius(win, True),
                      "end_priority": bool(win.get("end_priority"))})

    # ---- plan the DOUBLE posts + per-edge node marks ------------------
    # doubles: winner has END PRIORITY on -> every OTHER config's line
    # TERMINATING there sets its own end post tangent to the winner's
    doubles = []            # (e_i, station, ni)
    clear_override = {}     # (e_i, ni) -> in-between clearance
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
                    doubles.append((e_i, min(d, e["length"]), ni))
                elif st >= e["length"] - tol:       # line ends here
                    doubles.append((e_i, max(e["length"] - d, 0.0),
                                    ni))
                else:
                    continue                        # passes through
                r_fnd = _fnd_radius(e["cfg"], False)
                if r_fnd is not None:
                    clear_override[(e_i, ni)] = d + r_end + r_fnd

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
        spacing_ft = mm2ft(e["cfg"]["spacing_mm"])
        sts_all = []
        for k in range(len(merged) - 1):
            s0, n0 = merged[k]
            s1, n1 = merged[k + 1]
            sub = s1 - s0
            if sub <= tol:
                continue

            def _clear(ni2):
                ov = clear_override.get((e_i, ni2))
                if ov is not None:
                    return ov
                rn = nodes[ni2]["r"]
                if rn is not None and r_edge is not None:
                    return rn + r_edge
                return None

            p0 = int(nodes[n0]["cfg"].get("priority") or 99)
            p1 = int(nodes[n1]["cfg"].get("priority") or 99)
            if p0 <= p1:
                seg = F.edge_stations(sub, spacing_ft, _clear(n0),
                                      _clear(n1))
                sts_all.extend(s0 + d for d in seg)
            else:
                seg = F.edge_stations(sub, spacing_ft, _clear(n1),
                                      _clear(n0))
                sts_all.extend(s1 - d for d in seg)
        e["stations"] = sorted(sts_all)
        total += len(e["stations"])
        note("line {} ('{}' -> {}): {} corner(s), {} in-between "
             "post(s)".format(
                 id_value(e["el"].Id), e["style"], e["name"],
                 len(merged), len(e["stations"])))
    if total > F.MAX_INSTANCES:
        raise ValueError("{} instances would be placed - over the "
                         "{} sanity cap. Check the spacing.".format(
                             total, F.MAX_INSTANCES))

    # ---- place: corners FIRST, then doubles, then in-between ---------
    records = []
    missed = [0]
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

    def put(x, y, tang, cfg, use_ends):
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
        rec = {"uid": inst.UniqueId}
        if secondary is not None:
            try:
                f_inst = _place_one(doc, secondary, hit, lvl, ang)
                rec["foundation_uid"] = f_inst.UniqueId
            except Exception as ex:
                note("! foundation placement failed: {}".format(ex))
        records.append(rec)

    for nd in nodes:
        put(nd["xy"][0], nd["xy"][1], nd["tangent"], nd["cfg"],
            True)
    for e_i, st, _ni in doubles:
        e = edges[e_i]
        pnt, tang = F.point_at(e["poly"], st)
        put(pnt[0], pnt[1], tang, e["cfg"], True)
    for e in edges:
        for d in e["stations"]:
            pnt, tang = F.point_at(e["poly"], d)
            put(pnt[0], pnt[1], tang, e["cfg"], False)

    return records, notes, len(records), missed[0]
