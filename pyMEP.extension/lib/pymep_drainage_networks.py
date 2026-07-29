# -*- coding: utf-8 -*-
"""Drainage Networks - a 3D dashboard over the model's node families and
the branch pipework Nodes to Main built from them, with EDITS flowing
back into Revit.

The scan takes every placed family instance whose FAMILY name contains
a filter word (default "node") and groups the instances by their TYPE
name - 'STORMWATER - IN - N1' reads as system STORMWATER, flow IN,
network N1. Each group joins up with the branch tracker's registry
(node_branches.json): the branches Nodes to Main built for those nodes,
and the main runs they tee into, come out as live geometry. The whole
picture is written as one JSON and preloaded into the drainage 3D
viewer - so RUNNING NODES TO MAIN POPULATES THE DASHBOARD (it is
rebuilt from the model + registry on every launch).

Edits made in the dashboard (branch/main sizes, gradients, worksets,
a main end's invert) download as pymep_network_edits.json; Apply
Dashboard Edits picks the newest one out of Downloads and adapts the
model with the same delete-heal-rebuild machinery Update Nodes uses.

Pure helpers at the top (unit-tested under CPython by
``tests/test_drainage_networks.py`` - stdlib only); Revit API access
below. IronPython 2.7 / Revit 2021-2026 safe.
"""

import clr
clr.AddReference("RevitAPI")

import json
import math
import os

from Autodesk.Revit.DB import (
    XYZ, Transaction, TransactionGroup, Line, BuiltInParameter,
    FilteredElementCollector, FamilyInstance,
)
from Autodesk.Revit.DB.Plumbing import Pipe

from pymep_revit import safe_name, mm2ft, ft2mm
from pymep_connect_fixtures import (
    fixture_outlet_info, main_pipe_info, connect_fixture_to_main,
    plan_dist_to_segment, main_gradient, regrade_main_ends,
    list_pipe_type_options, list_system_type_options, set_pipe_dia,
    node_dia_mm, _has_point,
)
from pymep_nodes_track import (
    load_branches, save_branches, add_branch, make_record,
    _by_uid, _resolve_named, _main_pieces, _delete_branch, _heal_main,
)


NETWORKS_JSON = "drainage_networks.json"
EDITS_PREFIX = "pymep_network_edits"
EDITS_KIND = "pymep-drainage-edits"
DATA_KIND = "pymep-drainage"

FT_TO_M = 304.8 / 1000.0


# ---------------------------------------------------------------------------
# pure (stdlib only - unit-tested without Revit)
# ---------------------------------------------------------------------------
def parse_network(type_name):
    """'STORMWATER - IN - N1' -> {name, system, flow, label}: the parts
    of the type name split on '-'. Fewer parts degrade gracefully - the
    full type name is always the network's identity."""
    parts = [p.strip() for p in (type_name or "").split("-")]
    parts = [p for p in parts if p]
    return {"name": (type_name or "").strip() or "(unnamed)",
            "system": parts[0] if parts else "(unnamed)",
            "flow": parts[1] if len(parts) > 1 else "",
            "label": parts[2] if len(parts) > 2 else ""}


def line_key(line):
    """A stable key for a stored main line: endpoints rounded to 3 dp
    (feet), lower-sorted so direction doesn't matter."""
    a = tuple(round(float(v), 3) for v in line[0])
    b = tuple(round(float(v), 3) for v in line[1])
    return tuple(sorted((a, b)))


def run_extremes(ends):
    """The farthest-apart pair among a run's piece endpoints - the
    overall (a, b) of a main that tees have split. Plan distance
    decides; returns (a, b) as given."""
    if not ends:
        return None, None
    if len(ends) == 1:
        return ends[0], ends[0]
    best = (ends[0], ends[1])
    bestd = -1.0
    for i in range(len(ends)):
        for j in range(i + 1, len(ends)):
            dx = ends[i][0] - ends[j][0]
            dy = ends[i][1] - ends[j][1]
            d = dx * dx + dy * dy
            if d > bestd:
                bestd = d
                best = (ends[i], ends[j])
    return best


def new_main_ends(a, b, dia_ft, slope_n=None, keep="lower",
                  invert_end=None, invert_m=None):
    """The main run's new endpoints (feet), from the dashboard's edit:

    - ``invert_m`` + ``invert_end`` ("upper"/"lower"): that end's
      CENTRELINE becomes invert + dia/2 and the other end derives from
      ``slope_n`` when given, else the run's current fall is preserved;
    - ``slope_n`` alone: re-graded at 1:n keeping the ``keep`` end
      ("upper"/"lower") where it is;
    - neither: unchanged.

    Ends level -> ``b`` counts as the lower end (same convention as
    regrade_main_ends). Pure."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    run = math.sqrt(dx * dx + dy * dy)
    a_low = a[2] < b[2]

    if invert_m is not None and invert_end in ("upper", "lower"):
        if slope_n and slope_n > 0:
            fall = run / slope_n
        else:
            fall = abs(b[2] - a[2])
        z_set = invert_m / FT_TO_M + dia_ft / 2.0
        if invert_end == "lower":
            lo, hi = z_set, z_set + fall
        else:
            hi, lo = z_set, z_set - fall
        if a_low:
            return (a[0], a[1], lo), (b[0], b[1], hi)
        return (a[0], a[1], hi), (b[0], b[1], lo)

    if slope_n and slope_n > 0:
        return regrade_main_ends(a, b, slope_n,
                                 keep="high" if keep == "upper" else "low")
    return a, b


def project_z(p, a, b):
    """Z on the line a-b at ``p``'s plan position (param clamped to the
    segment). Pure - how every piece of a split main lands on the run's
    new gradient."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    dd = dx * dx + dy * dy
    if dd < 1e-12:
        t = 0.0
    else:
        t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / dd
        t = max(0.0, min(1.0, t))
    return a[2] + t * (b[2] - a[2])


def parse_edits(text):
    """Validate a dashboard edits file. Returns the parsed dict; raises
    ValueError with a human reason when it isn't one."""
    try:
        data = json.loads(text)
    except Exception:
        raise ValueError("the file isn't valid JSON")
    if not isinstance(data, dict) or data.get("kind") != EDITS_KIND:
        raise ValueError("not a pyMEP drainage-edits file "
                         "(kind != {})".format(EDITS_KIND))
    edits = data.get("edits")
    if not isinstance(edits, list) or not edits:
        raise ValueError("the file contains no edits")
    return data


def find_edits_file(folder):
    """The NEWEST pymep_network_edits*.json in ``folder`` (browsers
    number repeat downloads), skipping ones already marked applied.
    None when there is none."""
    try:
        names = os.listdir(folder)
    except Exception:
        return None
    cands = []
    for n in names:
        low = n.lower()
        if low.startswith(EDITS_PREFIX) and low.endswith(".json") \
                and ".applied" not in low:
            p = os.path.join(folder, n)
            try:
                cands.append((os.path.getmtime(p), p))
            except Exception:
                continue
    if not cands:
        return None
    cands.sort()
    return cands[-1][1]


def mark_applied(path):
    """Rename an applied edits file to *.applied.json so it can't be
    picked up twice. Returns the new path (or the old one when the
    rename fails)."""
    base = path[:-5] if path.lower().endswith(".json") else path
    target = base + ".applied.json"
    n = 1
    while os.path.exists(target):
        n += 1
        target = "{}.applied{}.json".format(base, n)
    try:
        os.rename(path, target)
        return target
    except Exception:
        return path


# ---------------------------------------------------------------------------
# Revit API access - the scan
# ---------------------------------------------------------------------------
def collect_node_groups(doc, name_filter):
    """{type_name: [instances]} over every placed, point-placed family
    instance whose FAMILY name contains ``name_filter``
    (case-insensitive; empty matches everything)."""
    filt = (name_filter or "").strip().lower()
    groups = {}
    for inst in FilteredElementCollector(doc).OfClass(FamilyInstance):
        try:
            sym = inst.Symbol
            try:
                fam = safe_name(sym.Family)
            except Exception:
                fam = ""
            if filt and filt not in fam.lower():
                continue
            if not _has_point(inst):
                continue
            groups.setdefault(safe_name(sym), []).append(inst)
        except Exception:
            continue
    return groups


def _pipe_dia_ft(pipe):
    try:
        p = pipe.get_Parameter(BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
        if p is not None:
            return p.AsDouble()
    except Exception:
        pass
    return 0.0


def _pipe_ends(pipe):
    crv = pipe.Location.Curve
    a = crv.GetEndPoint(0)
    b = crv.GetEndPoint(1)
    return (a.X, a.Y, a.Z), (b.X, b.Y, b.Z)


def _pipe_workset(doc, pipe):
    try:
        if doc.IsWorkshared:
            return doc.GetWorksetTable().GetWorkset(pipe.WorksetId).Name
    except Exception:
        pass
    return ""


def _pipe_sys_name(doc, pipe):
    try:
        p = pipe.get_Parameter(BuiltInParameter.RBS_PIPING_SYSTEM_TYPE_PARAM)
        if p is not None:
            el = doc.GetElement(p.AsElementId())
            if el is not None:
                return safe_name(el)
    except Exception:
        pass
    return ""


def list_worksets(doc):
    """User workset names, sorted - the dashboard's workset dropdown."""
    out = []
    try:
        if doc.IsWorkshared:
            from Autodesk.Revit.DB import (FilteredWorksetCollector,
                                           WorksetKind)
            for ws in FilteredWorksetCollector(doc).OfKind(
                    WorksetKind.UserWorkset):
                out.append(ws.Name)
    except Exception:
        pass
    out.sort(key=lambda s: s.lower())
    return out


def _workset_id_int(doc, name):
    if not name:
        return None
    try:
        if not doc.IsWorkshared:
            return None
        from Autodesk.Revit.DB import FilteredWorksetCollector, WorksetKind
        for ws in FilteredWorksetCollector(doc).OfKind(
                WorksetKind.UserWorkset):
            if ws.Name == name:
                return ws.Id.IntegerValue
    except Exception:
        pass
    return None


def _set_workset(el, ws_int):
    if el is None or ws_int is None:
        return
    try:
        p = el.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM)
        if p is not None and not p.IsReadOnly:
            p.Set(ws_int)
    except Exception:
        pass


def build_dashboard_data(doc, base, name_filter):
    """The drainage dashboard's whole JSON payload: node groups joined
    with the tracker registry and the mains' LIVE geometry. Coordinates
    go out in metres, X/Y shifted onto a local origin; Z stays absolute.
    ``base`` is the project_files folder holding node_branches.json."""
    groups = collect_node_groups(doc, name_filter)
    registry = load_branches(base)
    recs_by_uid = {}
    for r in registry["branches"]:
        if r.get("node_uid"):
            recs_by_uid[r["node_uid"]] = r

    xs, ys = [], []

    def m3(p):
        return [round(p[0] * FT_TO_M, 3), round(p[1] * FT_TO_M, 3),
                round(p[2] * FT_TO_M, 3)]

    networks = []
    for tname in sorted(groups.keys(), key=lambda s: s.lower()):
        insts = groups[tname]
        net = parse_network(tname)
        nodes, branches, uids = [], [], []
        line_keys, mains = [], []
        settings = None
        for inst in insts:
            o, _d = fixture_outlet_info(inst)
            if o is None:
                try:
                    lp = inst.Location.Point
                    o = (lp.X, lp.Y, lp.Z)
                except Exception:
                    continue
            uid = inst.UniqueId
            uids.append(uid)
            rec = recs_by_uid.get(uid)
            dia = None
            try:
                dia = node_dia_mm(inst)
            except Exception:
                pass
            nodes.append({"uid": uid, "xyz": m3(o),
                          "dia_mm": round(dia, 1) if dia else None,
                          "tracked": rec is not None})
            xs.append(o[0])
            ys.append(o[1])
            if rec is None:
                continue
            if settings is None:
                settings = {"slope": rec.get("slope"),
                            "dia_mm": rec.get("dia_mm"),
                            "invert_m": rec.get("invert_m"),
                            "pipe_type": rec.get("pipe_type") or "",
                            "sys_type": rec.get("sys_type") or ""}
            segs = []
            for k in ("down_uid", "sloped_uid"):
                p = _by_uid(doc, rec.get(k))
                if isinstance(p, Pipe):
                    try:
                        pa, pb = _pipe_ends(p)
                    except Exception:
                        continue
                    segs.append([m3(pa), m3(pb),
                                 round(ft2mm(_pipe_dia_ft(p)), 1)])
                    xs.extend([pa[0], pb[0]])
                    ys.extend([pa[1], pb[1]])
            if segs:
                branches.append({"node_uid": uid, "segs": segs})
            if rec.get("main_line"):
                k = line_key(rec["main_line"])
                if k not in line_keys:
                    line_keys.append(k)
                    mains.append(rec["main_line"])

        main_rows = []
        for line in mains:
            la, lb = [tuple(x) for x in line]
            pieces = _main_pieces(doc, la, lb)
            if not pieces:
                continue
            ends = []
            for p in pieces:
                try:
                    pa, pb = _pipe_ends(p)
                    ends.extend([pa, pb])
                except Exception:
                    continue
            if not ends:
                continue
            ra, rb = run_extremes(ends)
            dia_ft = _pipe_dia_ft(pieces[0])
            grad = main_gradient(ra, rb)
            hi, lo = (ra, rb) if ra[2] >= rb[2] else (rb, ra)
            main_rows.append({
                "line_ft": [list(la), list(lb)],
                "a": m3(ra), "b": m3(rb),
                "dia_mm": round(ft2mm(dia_ft), 1),
                "slope_n": round(grad, 1) if grad else None,
                "upper_invert_m": round((hi[2] - dia_ft / 2.0) * FT_TO_M,
                                        3),
                "lower_invert_m": round((lo[2] - dia_ft / 2.0) * FT_TO_M,
                                        3),
                "workset": _pipe_workset(doc, pieces[0]),
                "pipe_type": safe_name(doc.GetElement(
                    pieces[0].GetTypeId())),
                "sys_type": _pipe_sys_name(doc, pieces[0]),
                "pieces": len(pieces)})
            for e in ends:
                xs.append(e[0])
                ys.append(e[1])

        if nodes:
            networks.append({"name": net["name"], "system": net["system"],
                             "flow": net["flow"], "label": net["label"],
                             "node_uids": uids, "nodes": nodes,
                             "branches": branches, "mains": main_rows,
                             "settings": settings})

    ox = min(xs) * FT_TO_M if xs else 0.0
    oy = min(ys) * FT_TO_M if ys else 0.0
    ox, oy = round(ox, 3), round(oy, 3)
    for nw in networks:
        for nd in nw["nodes"]:
            nd["xyz"][0] = round(nd["xyz"][0] - ox, 3)
            nd["xyz"][1] = round(nd["xyz"][1] - oy, 3)
        for br in nw["branches"]:
            for seg in br["segs"]:
                for p in (seg[0], seg[1]):
                    p[0] = round(p[0] - ox, 3)
                    p[1] = round(p[1] - oy, 3)
        for mr in nw["mains"]:
            for p in (mr["a"], mr["b"]):
                p[0] = round(p[0] - ox, 3)
                p[1] = round(p[1] - oy, 3)

    try:
        model = doc.Title
    except Exception:
        model = "model"
    return {"kind": DATA_KIND, "version": 1, "model": model,
            "filter": name_filter, "origin": [ox, oy],
            "worksets": list_worksets(doc),
            "pipe_types": [n for n, _i in list_pipe_type_options(doc)],
            "sys_types": [n for n, _i in list_system_type_options(doc)],
            "networks": networks}


def write_networks_json(base, data):
    """Store the dashboard payload in the project's file store; returns
    the path."""
    if not os.path.isdir(base):
        os.makedirs(base)
    path = os.path.join(base, NETWORKS_JSON)
    f = open(path, "w")
    try:
        json.dump(data, f, indent=1, sort_keys=True)
    finally:
        f.close()
    return path


# ---------------------------------------------------------------------------
# Revit API access - applying the dashboard's edits
# ---------------------------------------------------------------------------
def _set_main_run(doc, pieces, a2, b2):
    """Drop every piece of the run onto the a2-b2 gradient (plan
    positions stay; Z projects), one transaction."""
    t = Transaction(doc, "Re-grade main run")
    t.Start()
    try:
        for p in pieces:
            pa, pb = _pipe_ends(p)
            na = XYZ(pa[0], pa[1], project_z(pa, a2, b2))
            nb = XYZ(pb[0], pb[1], project_z(pb, a2, b2))
            p.Location.Curve = Line.CreateBound(na, nb)
        t.Commit()
    except Exception:
        t.RollBack()
        raise


def _set_worksets(doc, elements, ws_int):
    if ws_int is None:
        return
    els = [e for e in elements if e is not None]
    if not els:
        return
    t = Transaction(doc, "Set workset")
    t.Start()
    try:
        for e in els:
            _set_workset(e, ws_int)
        t.Commit()
    except Exception:
        t.RollBack()
        raise


def _branch_elements(doc, rec):
    out = []
    for k in ("down_uid", "sloped_uid", "elbow_uid", "tee_uid"):
        el = _by_uid(doc, rec.get(k))
        if el is not None:
            out.append(el)
    return out


def apply_edits(doc, base, edits_data, log=None):
    """Adapt the model to a dashboard edits file, all in ONE undo step.

    Per network edit: branch settings (dia / gradient / pipe / system
    type) rebuild the network's tracked branches; main settings (dia /
    gradient / an end invert) delete-heal ALL branches teeing into that
    main line first (whatever network they belong to - the main is
    shared geometry), reshape the main, then rebuild everything against
    it; a workset name moves main + branches over. Returns a summary
    dict; the registry is refreshed."""
    def say(m):
        if log is not None:
            log(m)

    from Autodesk.Revit.DB.Plumbing import PipeType, PipingSystemType

    registry = load_branches(base)
    recs = registry["branches"]
    summary = {"networks": 0, "mains": 0, "branches": 0, "worksets": 0,
               "failed": 0, "notes": []}

    tg = TransactionGroup(doc, "Drainage dashboard edits")
    tg.Start()
    try:
        for edit in edits_data.get("edits", []):
            net_name = edit.get("network") or "?"
            say("**{}**:".format(net_name))
            uids = set(edit.get("node_uids") or [])
            if uids:
                net_recs = [r for r in recs if r.get("node_uid") in uids]
            else:
                net_recs = [r for r in recs if (r.get("node_label") or ""
                                                ).endswith(net_name)]
            branch_edit = edit.get("branch") or {}
            ws_name = edit.get("workset")
            ws_int = _workset_id_int(doc, ws_name)
            if ws_name and ws_int is None:
                say("  ! workset '{}' not found in this model - workset "
                    "left alone".format(ws_name))
            main_edits = edit.get("mains") or []
            geom_branch = any(k in branch_edit for k in
                              ("dia_mm", "slope", "pipe_type", "sys_type"))
            touched = False

            # ---- mains first: reshape shared geometry --------------------
            rebuilt_lines = {}
            for me in main_edits:
                line = me.get("line_ft")
                if not line:
                    continue
                key = line_key(line)
                la, lb = tuple(line[0]), tuple(line[1])
                geom_main = any(me.get(k) is not None for k in
                                ("dia_mm", "slope", "invert_m"))
                if not geom_main:
                    if ws_int is not None:
                        pcs = _main_pieces(doc, la, lb)
                        _set_worksets(doc, pcs, ws_int)
                        summary["worksets"] += len(pcs)
                        touched = True
                    continue
                touched = True
                line_recs = [r for r in recs if r.get("main_line") and
                             line_key(r["main_line"]) == key]
                for r in line_recs:
                    try:
                        tee_pt = _delete_branch(doc, r, log=log)
                        _heal_main(doc, tee_pt, la, lb, log=log)
                    except Exception as ex:
                        say("  ! couldn't clear a branch off the main "
                            "({})".format(ex))
                pieces = _main_pieces(doc, la, lb)
                if not pieces:
                    say("  ! no pipe found on the stored main line - "
                        "main edit skipped")
                    summary["failed"] += 1
                    continue
                if me.get("dia_mm"):
                    for p in pieces:
                        try:
                            set_pipe_dia(doc, p, float(me["dia_mm"]))
                        except Exception as ex:
                            say("  ! couldn't resize a main piece "
                                "({})".format(ex))
                ends = []
                for p in pieces:
                    pa, pb = _pipe_ends(p)
                    ends.extend([pa, pb])
                ra, rb = run_extremes(ends)
                dia_ft = _pipe_dia_ft(pieces[0])
                a2, b2 = new_main_ends(
                    ra, rb, dia_ft,
                    slope_n=me.get("slope"),
                    keep=me.get("keep") or "lower",
                    invert_end=me.get("invert_end"),
                    invert_m=me.get("invert_m"))
                if (a2, b2) != (ra, rb):
                    _set_main_run(doc, pieces, a2, b2)
                    say("  main re-shaped: {:.0f} mm, ends Z {:.3f} / "
                        "{:.3f} m".format(
                            ft2mm(_pipe_dia_ft(pieces[0])),
                            a2[2] * FT_TO_M, b2[2] * FT_TO_M))
                else:
                    a2, b2 = ra, rb
                if ws_int is not None:
                    _set_worksets(doc, pieces, ws_int)
                    summary["worksets"] += len(pieces)
                rebuilt_lines[key] = ((la, lb), (a2, b2), line_recs)
                summary["mains"] += 1

            # ---- rebuild branches ---------------------------------------
            pt_id = st_id = None
            if branch_edit.get("pipe_type"):
                pt_id = _resolve_named(doc, PipeType,
                                       branch_edit["pipe_type"])
            if branch_edit.get("sys_type"):
                st_id = _resolve_named(doc, PipingSystemType,
                                       branch_edit["sys_type"])

            to_rebuild = []
            for key, (_old, new_line, line_recs) in rebuilt_lines.items():
                for r in line_recs:
                    to_rebuild.append((r, new_line, True))
            if geom_branch:
                covered = set(id(r) for r, _l, _m in to_rebuild)
                for r in net_recs:
                    if id(r) in covered or not r.get("main_line"):
                        continue
                    la, lb = [tuple(x) for x in r["main_line"]]
                    try:
                        tee_pt = _delete_branch(doc, r, log=log)
                        _heal_main(doc, tee_pt, la, lb, log=log)
                        to_rebuild.append((r, (la, lb), False))
                    except Exception as ex:
                        say("  ! couldn't clear the branch of {} "
                            "({})".format(r.get("node_label") or "?", ex))
                        summary["failed"] += 1

            for r, (la, lb), main_moved in to_rebuild:
                mine = not uids or r.get("node_uid") in uids
                label = r.get("node_label") or r.get("node_uid") or "?"
                node = _by_uid(doc, r.get("node_uid"))
                if node is None:
                    say("  {}: node gone - branch dropped".format(label))
                    recs.remove(r)
                    continue
                slope = r.get("slope")
                dia_mm = r.get("dia_mm")
                invert = r.get("invert_m")
                ptn, stn = r.get("pipe_type"), r.get("sys_type")
                rpt_id = rst_id = None
                if mine and geom_branch:
                    if branch_edit.get("slope"):
                        slope = float(branch_edit["slope"])
                    if branch_edit.get("dia_mm"):
                        dia_mm = float(branch_edit["dia_mm"])
                    if branch_edit.get("pipe_type"):
                        ptn, rpt_id = branch_edit["pipe_type"], pt_id
                    if branch_edit.get("sys_type"):
                        stn, rst_id = branch_edit["sys_type"], st_id
                if rpt_id is None:
                    rpt_id = _resolve_named(doc, PipeType, ptn)
                if rst_id is None:
                    rst_id = _resolve_named(doc, PipingSystemType, stn)
                if main_moved:
                    invert = None      # the main moved - meet it as it lies
                try:
                    pieces = _main_pieces(doc, la, lb)
                    if not pieces:
                        say("  ! {}: no main pipe on its line - branch "
                            "not rebuilt".format(label))
                        summary["failed"] += 1
                        recs.remove(r)
                        continue
                    o, _d = fixture_outlet_info(node)
                    best, bestd = pieces[0], None
                    for p in pieces:
                        pa, pb, _t2, _s2, _l2, _d2 = main_pipe_info(p)
                        d = plan_dist_to_segment(o, pa, pb)
                        if bestd is None or d < bestd:
                            best, bestd = p, d
                    res = connect_fixture_to_main(
                        doc, node, best, slope, dia_mm, invert_m=invert,
                        log=None, pipe_type_id=rpt_id,
                        system_type_id=rst_id)
                    new_rec = make_record(node, res, slope, dia_mm,
                                          invert, ptn or "", stn or "",
                                          (la, lb), label)
                    recs[recs.index(r)] = new_rec
                    if ws_int is not None and mine:
                        _set_worksets(doc, _branch_elements(doc, new_rec),
                                      ws_int)
                        summary["worksets"] += 1
                    summary["branches"] += 1
                    touched = True
                except Exception as ex:
                    say("  ! {}: rebuild failed ({})".format(label, ex))
                    summary["failed"] += 1
                    recs.remove(r)

            # ---- workset-only edits (no geometry touched) ---------------
            if ws_int is not None and not main_edits and not geom_branch:
                for r in net_recs:
                    els = _branch_elements(doc, r)
                    _set_worksets(doc, els, ws_int)
                    summary["worksets"] += len(els)
                    touched = True

            if touched:
                summary["networks"] += 1
            else:
                say("  nothing to change")
        tg.Assimilate()
    except Exception:
        try:
            tg.RollBack()
        except Exception:
            pass
        raise

    registry["branches"] = recs
    save_branches(base, registry)
    return summary
