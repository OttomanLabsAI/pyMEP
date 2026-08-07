# -*- coding: utf-8 -*-
"""Track the branches Inflow Drop Pipe to Collector builds, so moving a node and hitting
UPDATE adapts the pipework.

Every built branch is recorded in the project's file store
(<exports>/<model>/project_files/node_branches.json): the node's
UniqueId, the created pipes/fittings' UniqueIds, the settings used
(gradient, diameter, invert, pipe/system type names) and the main's
line. Update Nodes walks the records:

  - node unmoved and branch intact  -> left alone;
  - node MOVED                      -> the old branch (drop, sloped run,
    elbow, tee) is deleted, the main is HEALED (the tee's two open ends
    stretched back into one pipe), and the branch is REBUILT with the
    stored settings against the main as it now lies;
  - node DELETED                    -> the branch is removed and the
    main healed; the record is dropped.

Pure record/registry + geometry helpers at the top (unit-tested under
CPython by ``tests/test_nodes_track.py`` - stdlib only); Revit API
access below. IronPython 2.7 / Revit 2021-2026 safe.
"""

import clr
clr.AddReference("RevitAPI")

import json

import pymep_json
import math
import os

from Autodesk.Revit.DB import (
    XYZ, Transaction, TransactionGroup, Line, FilteredElementCollector,
)
from Autodesk.Revit.DB.Plumbing import Pipe

from pymep_revit import safe_name, mm2ft, ft2mm
from pymep_connect_fixtures import (
    fixture_outlet_info, main_pipe_info, connect_fixture_to_main,
    plan_dist_to_segment, node_pose, node_drop_pipe, node_directions,
    ray_hits_main, branch_points,
)
from pymep_net_param import (ensure_network_param, stamp_network,
                             with_connected_fittings, node_network_name)


REGISTRY = "node_branches.json"

MOVE_TOL_FT = mm2ft(5.0)     # closer than this = "didn't move"
LINE_TOL_FT = mm2ft(75.0)    # a pipe this close to the stored main line
                             # counts as a piece of the main
HEAL_TOL_FT = mm2ft(500.0)   # open ends this close to the old tee heal


# ---------------------------------------------------------------------------
# pure: registry + geometry decisions (stdlib only)
# ---------------------------------------------------------------------------
def load_branches(base):
    """{"branches": [...]} - missing/corrupt -> fresh empty."""
    try:
        with open(os.path.join(base, REGISTRY), "r") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("branches"),
                                                 list):
            return data
    except Exception:
        pass
    return {"branches": []}


def save_branches(base, data):
    if not os.path.isdir(base):
        os.makedirs(base)
    with open(os.path.join(base, REGISTRY), "w") as f:
        pymep_json.dump(data, f, indent=2, sort_keys=True)


def add_branch(base, record):
    data = load_branches(base)
    # one record per node: a rebuild replaces the node's old record
    data["branches"] = [r for r in data["branches"]
                        if r.get("node_uid") != record.get("node_uid")]
    data["branches"].append(record)
    save_branches(base, data)
    return len(data["branches"])


def outlet_moved(stored_xyz, current_xyz, tol_ft):
    """True when the outlet strayed farther than ``tol_ft``."""
    dx = current_xyz[0] - stored_xyz[0]
    dy = current_xyz[1] - stored_xyz[1]
    dz = current_xyz[2] - stored_xyz[2]
    return math.sqrt(dx * dx + dy * dy + dz * dz) > tol_ft


def on_main_line(p0, p1, line_a, line_b, tol_ft):
    """True when BOTH endpoints of a pipe sit within ``tol_ft`` of the
    stored main line (3D distance to the infinite line) - how the
    update finds the main's current pieces after tees split it."""
    ax, ay, az = line_a
    dx = line_b[0] - ax
    dy = line_b[1] - ay
    dz = line_b[2] - az
    dd = dx * dx + dy * dy + dz * dz
    if dd < 1e-12:
        return False

    def dist(p):
        wx, wy, wz = p[0] - ax, p[1] - ay, p[2] - az
        cx = wy * dz - wz * dy
        cy = wz * dx - wx * dz
        cz = wx * dy - wy * dx
        return math.sqrt((cx * cx + cy * cy + cz * cz) / dd)

    return dist(p0) <= tol_ft and dist(p1) <= tol_ft


# ---------------------------------------------------------------------------
# Revit API access
# ---------------------------------------------------------------------------
def make_record(node, result, slope, dia_mm, invert_m, pt_name, st_name,
                main_line, label):
    """The registry record for one built branch."""
    def uid(el):
        try:
            return el.UniqueId if el is not None else None
        except Exception:
            return None

    o, _d = fixture_outlet_info(node)
    try:
        pose = node_pose(node)
    except Exception:
        pose = None
    return {"node_uid": uid(node), "node_label": label,
            "outlet": list(o) if o else None,
            "down_uid": uid(result.get("down")),
            "sloped_uid": uid(result.get("sloped")),
            "stub_uid": uid(result.get("stub")),
            "elbow_uid": uid(result.get("elbow")),
            "elbow2_uid": uid(result.get("elbow2")),
            "tee_uid": uid(result.get("tee")),
            "end": list(result.get("end") or ()) or None,
            "mode": result.get("mode"), "pose": pose,
            "slope": slope, "dia_mm": dia_mm, "invert_m": invert_m,
            "pipe_type": pt_name or "", "sys_type": st_name or "",
            "main_line": [list(main_line[0]), list(main_line[1])]}


def _by_uid(doc, uid):
    if not uid:
        return None
    try:
        return doc.GetElement(uid)
    except Exception:
        return None


def tracked_node_uids(doc, base):
    """UniqueIds of nodes whose tracked branch is still alive - the
    'already connected' signal for branches that don't physically hook
    to the outlet connector (Drop Pipe OFF runs)."""
    out = set()
    for rec in load_branches(base)["branches"]:
        uid = rec.get("node_uid")
        if not uid:
            continue
        for k in ("down_uid", "sloped_uid", "stub_uid"):
            if rec.get(k) and _by_uid(doc, rec[k]) is not None:
                out.add(uid)
                break
    return out


def _stored_mode(doc, rec):
    """drop_first / drop_last of an OLD record (no mode key), inferred
    from which tracked pipe starts at the outlet. None = ambiguous."""
    if rec.get("mode"):
        return rec["mode"]
    o = rec.get("outlet")
    down = _by_uid(doc, rec.get("down_uid"))
    sloped = _by_uid(doc, rec.get("sloped_uid"))
    if not o or down is None or sloped is None:
        return None
    ox = XYZ(o[0], o[1], o[2])
    for el, mode in ((down, "drop_first"), (sloped, "drop_last")):
        try:
            crv = el.Location.Curve
            for i in (0, 1):
                if crv.GetEndPoint(i).DistanceTo(ox) <= MOVE_TOL_FT * 4:
                    return mode
        except Exception:
            continue
    return None


def _shape_changed(doc, rec, node, line_a, line_b):
    """(changed, why): would the branch be built differently TODAY -
    node turned, or its Drop Pipe toggled? Records carry the pose
    signature; older ones fall back to inference."""
    try:
        pose_now = node_pose(node)
    except Exception:
        return False, ""
    stored = rec.get("pose")
    if stored is not None:
        if list(stored) != list(pose_now):
            return True, "node turned / Drop Pipe changed"
        return False, ""
    # legacy record: infer what was built vs what would be built
    want = "drop_first" if node_drop_pipe(node) else "drop_last"
    was = _stored_mode(doc, rec)
    if was is not None and was != want:
        return True, "Drop Pipe changed"
    o, _d = fixture_outlet_info(node)
    if o is not None and rec.get("end"):
        direction = None
        for cand in node_directions(node):
            if ray_hits_main(o, cand, line_a, line_b) is not None:
                direction = cand
                break
        pts = branch_points(o, line_a, line_b,
                            rec.get("slope") or 100.0,
                            mm2ft(rec.get("dia_mm") or 100.0), None,
                            direction=direction,
                            drop_pipe=(want == "drop_first"))
        dx = pts["end"][0] - rec["end"][0]
        dy = pts["end"][1] - rec["end"][1]
        if math.sqrt(dx * dx + dy * dy) > LINE_TOL_FT:
            return True, "node turned"
    return False, ""


def _resolve_named(doc, cls, name):
    if not name:
        return None
    for t in FilteredElementCollector(doc).OfClass(cls):
        try:
            if safe_name(t) == name:
                return t.Id
        except Exception:
            continue
    return None


def _main_pieces(doc, line_a, line_b):
    """Every pipe currently lying ON the stored main line."""
    out = []
    for p in FilteredElementCollector(doc).OfClass(Pipe):
        try:
            crv = p.Location.Curve
            a = crv.GetEndPoint(0)
            b = crv.GetEndPoint(1)
        except Exception:
            continue
        if on_main_line((a.X, a.Y, a.Z), (b.X, b.Y, b.Z),
                        line_a, line_b, LINE_TOL_FT):
            out.append(p)
    return out


def _delete_branch(doc, rec, log=None):
    """Delete the branch's elements (tee, elbow, pipes) in one
    transaction. Returns the tee's location for the heal (or the stored
    branch end)."""
    tee = _by_uid(doc, rec.get("tee_uid"))
    tee_pt = None
    try:
        if tee is not None and tee.Location is not None:
            lp = tee.Location.Point
            tee_pt = (lp.X, lp.Y, lp.Z)
    except Exception:
        pass
    if tee_pt is None and rec.get("end"):
        tee_pt = tuple(rec["end"])
    t = Transaction(doc, "Remove node branch")
    t.Start()
    try:
        for key in ("tee_uid", "elbow_uid", "elbow2_uid", "stub_uid",
                    "sloped_uid", "down_uid"):
            el = _by_uid(doc, rec.get(key))
            if el is not None:
                try:
                    doc.Delete(el.Id)
                except Exception:
                    pass
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    return tee_pt


def _heal_main(doc, tee_pt, line_a, line_b, log=None):
    """After the tee is gone, two main pieces end open at its point:
    stretch one over the other and delete the second, restoring one
    pipe. Skipped (with a note) when the open ends can't be found."""
    if tee_pt is None:
        return False
    tp = XYZ(tee_pt[0], tee_pt[1], tee_pt[2])
    cands = []
    for p in _main_pieces(doc, line_a, line_b):
        try:
            for c in p.ConnectorManager.Connectors:
                if not c.IsConnected \
                        and c.Origin.DistanceTo(tp) <= HEAL_TOL_FT:
                    cands.append((p, c))
                    break
        except Exception:
            continue
    if len(cands) != 2 or cands[0][0].Id == cands[1][0].Id:
        if log is not None:
            log("  ! couldn't heal the main at the old tee ({} open "
                "end(s) found) - join it by hand if needed".format(
                    len(cands)))
        return False
    (pa, _ca), (pb, _cb) = cands
    ca_crv = pa.Location.Curve
    cb_crv = pb.Location.Curve
    # keep pa: run it from ITS far end to pb's far end
    a0, a1 = ca_crv.GetEndPoint(0), ca_crv.GetEndPoint(1)
    b0, b1 = cb_crv.GetEndPoint(0), cb_crv.GetEndPoint(1)
    far_a = a0 if a0.DistanceTo(tp) > a1.DistanceTo(tp) else a1
    far_b = b0 if b0.DistanceTo(tp) > b1.DistanceTo(tp) else b1
    t = Transaction(doc, "Heal main pipe")
    t.Start()
    try:
        doc.Delete(pb.Id)
        pa.Location.Curve = Line.CreateBound(far_a, far_b)
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    if log is not None:
        log("  main healed across the old tee point")
    return True


def update_branches(doc, base, log=None):
    """The UPDATE pass over every tracked branch. Returns a summary
    dict; the registry is rewritten with the surviving/refreshed
    records."""
    def say(m):
        if log is not None:
            log(m)

    from Autodesk.Revit.DB.Plumbing import PipeType, PipingSystemType

    data = load_branches(base)
    if not data["branches"]:
        return {"unchanged": 0, "rebuilt": 0, "removed": 0, "failed": 0,
                "none": True}

    unchanged = rebuilt = removed = failed = 0
    kept = []

    tg = TransactionGroup(doc, "Update Nodes")
    tg.Start()
    try:
        ensure_network_param(doc)
        for rec in data["branches"]:
            label = rec.get("node_label") or rec.get("node_uid") or "?"
            node = _by_uid(doc, rec.get("node_uid"))
            line_a, line_b = [tuple(x) for x in rec["main_line"]]

            if node is None:
                say("**{}**: node deleted - removing its branch".format(
                    label))
                try:
                    tee_pt = _delete_branch(doc, rec, log=log)
                    _heal_main(doc, tee_pt, line_a, line_b, log=log)
                    removed += 1
                except Exception as ex:
                    say("  ! couldn't remove the branch: {}".format(ex))
                    failed += 1
                    kept.append(rec)
                continue

            o, _d = fixture_outlet_info(node)
            if o is None:
                say("**{}**: node has no outlet any more - left "
                    "alone".format(label))
                kept.append(rec)
                continue

            branch_alive = all(
                _by_uid(doc, rec.get(k)) is not None
                for k in ("down_uid", "sloped_uid", "stub_uid")
                if rec.get(k))
            if rec.get("outlet") and branch_alive and \
                    not outlet_moved(tuple(rec["outlet"]), o, MOVE_TOL_FT):
                changed, why = _shape_changed(doc, rec, node, line_a,
                                              line_b)
                if not changed:
                    unchanged += 1
                    kept.append(rec)
                    continue
                say("**{}**: {} - rebuilding its branch".format(label,
                                                                why))
            else:
                say("**{}**: node moved - rebuilding its "
                    "branch".format(label))
            try:
                tee_pt = _delete_branch(doc, rec, log=log)
                _heal_main(doc, tee_pt, line_a, line_b, log=log)
                pieces = _main_pieces(doc, line_a, line_b)
                if not pieces:
                    say("  ! no pipe found on the stored main line - "
                        "record dropped")
                    failed += 1
                    continue
                best, bestd = pieces[0], None
                for p in pieces:
                    pa, pb, _t2, _s2, _l2, _d2 = main_pipe_info(p)
                    d = plan_dist_to_segment(o, pa, pb)
                    if bestd is None or d < bestd:
                        best, bestd = p, d
                pt_id = _resolve_named(doc, PipeType, rec.get("pipe_type"))
                st_id = _resolve_named(doc, PipingSystemType,
                                       rec.get("sys_type"))
                r = connect_fixture_to_main(
                    doc, node, best, rec["slope"], rec["dia_mm"],
                    invert_m=rec.get("invert_m"), log=log,
                    pipe_type_id=pt_id, system_type_id=st_id,
                    use_rotation=True)
                kept.append(make_record(
                    node, r, rec["slope"], rec["dia_mm"],
                    rec.get("invert_m"), rec.get("pipe_type"),
                    rec.get("sys_type"), (line_a, line_b), label))
                if rec.get("collector"):
                    kept[-1]["collector"] = rec["collector"]
                rebuilt += 1
                try:
                    els = [node, r.get("down"), r.get("sloped"),
                           r.get("stub"), r.get("elbow"),
                           r.get("elbow2"), r.get("tee"),
                           r.get("new_main_segment")]
                    els += with_connected_fittings(
                        [r.get("down"), r.get("sloped"), r.get("stub")])
                    stamp_network(doc, els, rec.get("collector")
                                  or node_network_name(node))
                except Exception:
                    pass
            except Exception as ex:
                say("  ! rebuild failed: {}".format(ex))
                failed += 1
        tg.Assimilate()
    except Exception:
        try:
            tg.RollBack()
        except Exception:
            pass
        raise

    data["branches"] = kept
    save_branches(base, data)
    return {"unchanged": unchanged, "rebuilt": rebuilt,
            "removed": removed, "failed": failed, "none": False}
