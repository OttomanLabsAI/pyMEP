# -*- coding: utf-8 -*-
"""Merge runs of collinear pipes into single pipes.

The selected pipes are grouped into CHAINS - pipes lying on the same
infinite line (parallel directions AND coaxial within tolerance; the
gaps where couplings sit don't matter). Each chain becomes ONE new pipe
spanning the chain's two extreme endpoints - the EXACT endpoint XYZs of
the outermost segments, never re-projected or rounded - and the original
pipes are deleted, together with the fittings that lived entirely
INSIDE the chain (the couplings between consecutive segments). Fittings
that connect the run to the outside (elbows, tees) are kept and
reconnected to the new pipe, whose end lands on the same coordinates.

The new pipe takes its type, system type, level, workset, Mark and
comments from the chain's LONGEST segment; the diameter is the chain's
(largest, when mixed - reported, never silent).

Pure geometry/decision functions at the top (unit-tested under CPython
by ``tests/test_merge_pipes.py`` - keep them stdlib-only); Revit API
access below. IronPython 2.7 / Revit 2021-2026 safe.
"""

import clr
clr.AddReference("RevitAPI")

import math

from Autodesk.Revit.DB import (
    BuiltInParameter, ElementId, Transaction, XYZ,
)
from Autodesk.Revit.DB.Plumbing import Pipe

from pymep_revit import safe_name, ft2mm


# ---------------------------------------------------------------------------
# pure geometry + decisions (stdlib only - unit-tested without Revit)
# ---------------------------------------------------------------------------
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _len(a):
    return math.sqrt(_dot(a, a))


def _unit(a):
    n = _len(a)
    if n < 1e-12:
        return (0.0, 0.0, 0.0)
    return (a[0] / n, a[1] / n, a[2] / n)


def _line_dist(point, origin, direction):
    """Perpendicular distance of ``point`` from the infinite line through
    ``origin`` along unit ``direction``."""
    w = _sub(point, origin)
    return _len(_cross(w, direction))


def group_collinear(rows, off_tol_ft=0.13, off_scale=1.0):
    """Group pipe rows into chains that line up end-to-end - ANGLE
    IGNORED.

    rows: [{"id", "p0": (x,y,z), "p1": (x,y,z), "dia_ft", "len_ft"}].
    Two pipes chain when they sit on a common line: the offset is
    measured at the endpoint where they MEET (the nearer end of each to
    the other's line), so however sharply the run turns from one pipe to
    the next, they still join - the resulting pipe spans the run's two
    extreme ends. The allowance is ``max(off_tol_ft, off_scale * bore)``
    (~40 mm, or one pipe diameter, whichever is larger).

    Direction is NOT checked, so a run may turn any amount and still be
    one chain. What still stays apart: pipes that run PARALLEL and OFFSET
    to the side, or otherwise don't touch a common line - both their ends
    sit off the other's line, beyond the offset allowance. Returns
    ``(chains, singles)``: chains of 2+ rows, and rows that pair with
    nothing (left untouched by the caller)."""
    n = len(rows)
    dirs = [_unit(_sub(r["p1"], r["p0"])) for r in rows]

    def coaxial(i, j):
        tol = max(off_tol_ft,
                  off_scale * max(rows[i]["dia_ft"], rows[j]["dia_ft"]))
        # offset of j from i's line, taken at j's NEARER end (where the
        # two pipes meet - it aligns however the run turns there)
        oi = rows[i]["p0"]
        off_j = min(_line_dist(rows[j]["p0"], oi, dirs[i]),
                    _line_dist(rows[j]["p1"], oi, dirs[i]))
        # symmetric: also i's nearer end vs j's line, so which pipe is
        # taken as the axis never changes the verdict
        oj = rows[j]["p0"]
        off_i = min(_line_dist(rows[i]["p0"], oj, dirs[j]),
                    _line_dist(rows[i]["p1"], oj, dirs[j]))
        return min(off_j, off_i) <= tol

    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            if coaxial(i, j):
                pi, pj = find(i), find(j)
                if pi != pj:
                    parent[pj] = pi

    buckets = {}
    for i in range(n):
        buckets.setdefault(find(i), []).append(rows[i])
    chains = []
    singles = []
    for members in buckets.values():
        if len(members) >= 2:
            chains.append(members)
        else:
            singles.append(members[0])
    return chains, singles


def chain_extremes(chain):
    """The chain's two end points - the EXACT input tuples of the
    endpoint pair that are FARTHEST apart. Distance-based (not projected
    onto any axis), so a run that changes direction still yields the pipe
    between its true ends. Returned lexicographically ordered so the
    result is deterministic; a pipe is undirected, so the order is
    cosmetic."""
    pts = []
    for r in chain:
        pts.append(r["p0"])
        pts.append(r["p1"])
    best = None
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            d = _sub(pts[i], pts[j])
            dd = _dot(d, d)
            if best is None or dd > best[0]:
                best = (dd, pts[i], pts[j])
    a, b = best[1], best[2]
    return (a, b) if a <= b else (b, a)


def chain_gaps(chain, min_gap_ft=0.35):
    """Gaps along the run larger than ``min_gap_ft`` (couplings are
    shorter) - reported so an accidental merge across a genuine break is
    a conscious choice, not a surprise. Returns [(gap_ft, at_ft), ...]
    with ``at_ft`` measured from the run's low end."""
    longest = max(chain, key=lambda r: r["len_ft"])
    axis = _unit(_sub(longest["p1"], longest["p0"]))
    o = longest["p0"]
    spans = []
    for r in chain:
        a = _dot(_sub(r["p0"], o), axis)
        b = _dot(_sub(r["p1"], o), axis)
        spans.append((min(a, b), max(a, b)))
    spans.sort()
    base = spans[0][0]
    gaps = []
    reach = spans[0][1]
    for a, b in spans[1:]:
        if a - reach > min_gap_ft:
            gaps.append((a - reach, reach - base))
        if b > reach:
            reach = b
    return gaps


def classify_fittings(fitting_links, chain_ids):
    """Which fittings die with the chain: ``fitting_links`` maps
    fitting id -> list of connected pipe ids; a fitting is INTERNAL
    (deletable) when it touches 2+ chain pipes and nothing outside the
    chain. Everything else (elbow/tee to the outside world) survives.
    Returns (internal_ids, boundary_ids)."""
    chain_ids = set(chain_ids)
    internal = []
    boundary = []
    for fid, links in fitting_links.items():
        inside = [p for p in links if p in chain_ids]
        outside = [p for p in links if p not in chain_ids]
        if len(inside) >= 2 and not outside:
            internal.append(fid)
        elif inside:
            boundary.append(fid)
    return internal, boundary


# ---------------------------------------------------------------------------
# Revit API access
# ---------------------------------------------------------------------------
_CONN_TOL_FT = 0.01   # connector-to-endpoint match tolerance (~3 mm)


def read_pipe_rows(pipes):
    """[{'id', 'p0', 'p1', 'dia_ft', 'len_ft'}, ...] from Pipe elements
    (straight LocationCurve pipes only; others are skipped with a
    note). Returns (rows, notes)."""
    rows = []
    notes = []
    for p in pipes:
        try:
            crv = p.Location.Curve
            a = crv.GetEndPoint(0)
            b = crv.GetEndPoint(1)
        except Exception:
            notes.append("'{}' (id {}): no straight location curve - "
                         "skipped".format(safe_name(p), p.Id))
            continue
        dia = 0.0
        try:
            dp = p.get_Parameter(BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
            if dp is not None:
                dia = dp.AsDouble()
        except Exception:
            pass
        p0 = (a.X, a.Y, a.Z)
        p1 = (b.X, b.Y, b.Z)
        rows.append({"id": p.Id.IntegerValue, "p0": p0, "p1": p1,
                     "dia_ft": dia, "len_ft": _len(_sub(p1, p0))})
    return rows, notes


def _pipe_connections(pipe):
    """[(owner_element, owner_connector_origin)] for everything hooked
    to this pipe's connectors."""
    out = []
    try:
        conns = pipe.ConnectorManager.Connectors
    except Exception:
        return out
    for c in conns:
        try:
            if not c.IsConnected:
                continue
            for ref in c.AllRefs:
                own = ref.Owner
                if own is None or own.Id == pipe.Id:
                    continue
                try:
                    org = ref.Origin
                except Exception:
                    org = c.Origin
                out.append((own, org))
        except Exception:
            continue
    return out


def _copy_param(src, dst, bip):
    try:
        sp = src.get_Parameter(bip)
        dp = dst.get_Parameter(bip)
        if sp is None or dp is None or dp.IsReadOnly or not sp.HasValue:
            return
        st = str(sp.StorageType)
        if st == "String":
            dp.Set(sp.AsString() or "")
        elif st == "ElementId":
            dp.Set(sp.AsElementId())
        elif st == "Integer":
            dp.Set(sp.AsInteger())
        elif st == "Double":
            dp.Set(sp.AsDouble())
    except Exception:
        pass


def merge_chain(doc, pipes_by_id, chain, log=None):
    """Replace one chain with a single pipe, inside ONE transaction.
    Returns a summary dict; raises only when the new pipe cannot be
    created (in which case the transaction rolled back and nothing was
    deleted)."""
    def say(m):
        if log is not None:
            log(m)

    chain_ids = [r["id"] for r in chain]
    donor_row = max(chain, key=lambda r: r["len_ft"])
    donor = pipes_by_id[donor_row["id"]]

    e0, e1 = chain_extremes(chain)
    dias = sorted(set(round(r["dia_ft"], 6) for r in chain))
    dia = dias[-1]
    if len(dias) > 1:
        say("  ! mixed diameters {} mm - using the largest".format(
            ", ".join("{:.0f}".format(ft2mm(d)) for d in dias)))

    # fittings touching the chain, and where the run meets the world
    links = {}
    fit_elems = {}
    for rid in chain_ids:
        for own, org in _pipe_connections(pipes_by_id[rid]):
            oid = own.Id.IntegerValue
            if oid in [c for c in chain_ids]:
                continue
            links.setdefault(oid, []).append(rid)
            fit_elems[oid] = own
    internal, boundary = classify_fittings(links, chain_ids)

    type_id = donor.GetTypeId()
    lvl_id = None
    sys_id = None
    try:
        lp = donor.get_Parameter(BuiltInParameter.RBS_START_LEVEL_PARAM)
        if lp is not None:
            lvl_id = lp.AsElementId()
    except Exception:
        pass
    try:
        sp = donor.get_Parameter(
            BuiltInParameter.RBS_PIPING_SYSTEM_TYPE_PARAM)
        if sp is not None:
            sys_id = sp.AsElementId()
    except Exception:
        pass
    if lvl_id is None or lvl_id == ElementId.InvalidElementId \
            or sys_id is None or sys_id == ElementId.InvalidElementId:
        raise RuntimeError(
            "Chain donor pipe '{}' has no level/system type - cannot "
            "rebuild the run.".format(safe_name(donor)))

    t = Transaction(doc, "Merge pipe run")
    t.Start()
    try:
        for fid in internal:
            try:
                doc.Delete(fit_elems[fid].Id)
            except Exception:
                pass
        for rid in chain_ids:
            try:
                doc.Delete(pipes_by_id[rid].Id)
            except Exception:
                pass

        new_pipe = Pipe.Create(doc, sys_id, type_id, lvl_id,
                               XYZ(e0[0], e0[1], e0[2]),
                               XYZ(e1[0], e1[1], e1[2]))
        dp = new_pipe.get_Parameter(
            BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
        if dp is not None and not dp.IsReadOnly:
            dp.Set(dia)
        for bip_name in ("ALL_MODEL_MARK",
                         "ALL_MODEL_INSTANCE_COMMENTS",
                         "ELEM_PARTITION_PARAM"):
            bip = getattr(BuiltInParameter, bip_name, None)
            if bip is not None:
                _copy_param(donor, new_pipe, bip)

        # reconnect the run's boundary fittings: their orphaned
        # connector sits exactly on the new pipe's endpoint
        reconnected = 0
        try:
            new_conns = list(new_pipe.ConnectorManager.Connectors)
        except Exception:
            new_conns = []
        for fid in boundary:
            el = fit_elems[fid]
            try:
                cm = el.MEPModel.ConnectorManager if hasattr(
                    el, "MEPModel") and el.MEPModel else el.ConnectorManager
                for c in cm.Connectors:
                    if c.IsConnected:
                        continue
                    for nc in new_conns:
                        if c.Origin.DistanceTo(nc.Origin) <= _CONN_TOL_FT:
                            c.ConnectTo(nc)
                            reconnected += 1
                            break
            except Exception:
                continue
        t.Commit()
    except Exception:
        t.RollBack()
        raise

    say("  merged {} pipes -> 1 ({:.0f} mm), deleted {} coupling(s), "
        "reconnected {} end(s)".format(
            len(chain_ids), ft2mm(dia), len(internal), reconnected))
    return {"pipes": len(chain_ids), "internal": len(internal),
            "reconnected": reconnected, "dia_ft": dia,
            "new_id": new_pipe.Id, "e0": e0, "e1": e1}
