# -*- coding: utf-8 -*-
"""Lines to Pipes - turn a filtered set of model lines into a graded
pipe network.

The user draws the layout in plan as model lines, filters them here by
line style and workset, gives a pipe type, size, gradient 1:n and the
invert level at the outfall, then picks the outfall point on one of the
lines. Every line becomes a pipe falling toward that outfall at the
gradient; where one line crosses or ends on another, the branch is
placed at the through run's level and teed in; where two lines meet
end to end they are elbowed.

Real drawings are messy, and the network builder deals with the mess
it has actually been shown (the HEL18 IFC this was built against):

- laterals are drawn CROSSING their main, overshooting by up to a
  couple of metres - a terminal stub shorter than ``overshoot`` on the
  far side of a junction is treated as overshoot and dropped;
- the same run drawn twice (two colinear overlapping lines) - the
  shorter duplicate is dropped;
- a line touching nothing cannot be graded from the outfall - it is
  reported and skipped, never guessed at.

All the geometry below the Revit layer works on plain (x, y) tuples in
ANY consistent unit (the tests feed it millimetres straight out of an
IFC) and is unit-tested under CPython by ``tests/test_lines_to_pipes.py``.
The invert convention matches the rest of pyMEP: ``invert_m`` is the
TRUE invert - pipe centreline = invert + dia/2. IronPython 2.7 safe.
"""

import math

import clr
clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    CurveElement, FilteredElementCollector, Level, Line, ModelLine,
    Transaction, XYZ,
)
from Autodesk.Revit.DB.Plumbing import Pipe

from pymep_revit import safe_name

MM_PER_FT = 304.8
JOIN_TOL_MM = 50.0        # how close counts as touching
OVERSHOOT_MM = 2000.0     # terminal stub past a junction = drawing overshoot
MIN_RUN_MM = 160.0        # runs shorter than this cannot be built
DEPTH_EPS_MM = 1.0        # grade-linearity check along a through run


# ---------------------------------------------------------------------------
# pure geometry (stdlib only - unit-tested without Revit)
# ---------------------------------------------------------------------------
def _dist(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def _seg_point_t(p, a, b):
    """(distance, t) from point p to segment a-b."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    if L2 <= 0:
        return _dist(p, a), 0.0
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2
    t = max(0.0, min(1.0, t))
    c = (a[0] + t * dx, a[1] + t * dy)
    return _dist(p, c), t


def _intersect(a, b, c, d):
    """Proper 2D intersection of segments a-b and c-d:
    (t_ab, t_cd, (x, y)) with both t in [0, 1], or None when parallel
    or not crossing."""
    r = (b[0] - a[0], b[1] - a[1])
    s = (d[0] - c[0], d[1] - c[1])
    den = r[0] * s[1] - r[1] * s[0]
    if abs(den) < 1e-12:
        return None
    qp = (c[0] - a[0], c[1] - a[1])
    t = (qp[0] * s[1] - qp[1] * s[0]) / den
    u = (qp[0] * r[1] - qp[1] * r[0]) / den
    if -1e-9 <= t <= 1 + 1e-9 and -1e-9 <= u <= 1 + 1e-9:
        t = max(0.0, min(1.0, t))
        u = max(0.0, min(1.0, u))
        return t, u, _lerp(a, b, t)
    return None


def drop_duplicates(lines, tol=JOIN_TOL_MM):
    """Colinear overlapping pairs (the same run drawn twice): keep the
    longer, drop the shorter. Returns (kept_indices, dropped) with
    dropped = [(index, kept_index)]."""
    dropped = {}
    n = len(lines)
    for i in range(n):
        for j in range(n):
            if i == j or i in dropped or j in dropped:
                continue
            a, b = lines[i]
            c, d = lines[j]
            li, lj = _dist(a, b), _dist(c, d)
            if li > lj or (li == lj and i < j):
                continue          # only consider dropping the shorter
            da, _ = _seg_point_t(a, c, d)
            db, _ = _seg_point_t(b, c, d)
            if da <= tol and db <= tol:
                dropped[i] = j    # i lies along j entirely
    kept = [i for i in range(n) if i not in dropped]
    return kept, sorted(dropped.items())


def build_network(lines, join_tol=JOIN_TOL_MM, overshoot=OVERSHOOT_MM):
    """The junction graph of a set of 2-point lines.

    Returns a dict:
      nodes        [(x, y), ...]
      line_nodes   {line_index: [(t, node_index), ...] sorted by t}
      segs         [(node_a, node_b, line_index, length), ...]
      dup_dropped  [(line_index, kept_line_index), ...]
      tail_dropped [(line_index, length), ...]  overshoot stubs removed
    Lines dropped as duplicates get no nodes and no segs.
    """
    kept, dups = drop_duplicates(lines, join_tol)

    nodes = []

    def node_at(p):
        for i, q in enumerate(nodes):
            if _dist(p, q) <= join_tol:
                return i
        nodes.append((p[0], p[1]))
        return len(nodes) - 1

    line_nodes = {}
    for li in kept:
        a, b = lines[li]
        line_nodes[li] = {0.0: node_at(a), 1.0: node_at(b)}

    # ends touching another line's interior, and proper crossings
    for li in kept:
        a, b = lines[li]
        for lj in kept:
            if li == lj:
                continue
            c, d = lines[lj]
            hit = _intersect(a, b, c, d)
            if hit is None:
                # not crossing - but an END of li may still sit on lj
                for t_end, p in ((0.0, a), (1.0, b)):
                    dd, u = _seg_point_t(p, c, d)
                    if dd <= join_tol:
                        ni = line_nodes[li][t_end]
                        line_nodes[lj][u] = ni
                continue
            t, u, p = hit
            ni = node_at(p)
            line_nodes[li][t] = ni
            line_nodes[lj][u] = ni

    # collapse t-keys that mapped to the same node (end snaps)
    for li in kept:
        seen = {}
        for t in sorted(line_nodes[li]):
            n = line_nodes[li][t]
            if n in seen.values():
                del line_nodes[li][t]
            else:
                seen[t] = n
        line_nodes[li] = sorted(seen.items())

    # segments between consecutive nodes; drop overshoot tails - a
    # TERMINAL segment (nothing else meets its outer end) shorter than
    # ``overshoot`` on a line that has at least one real junction
    incident = {}
    for li in kept:
        for _t, n in line_nodes[li]:
            incident[n] = incident.get(n, 0) + 1

    segs = []
    tails = []
    for li in kept:
        seq = line_nodes[li]
        for k in range(len(seq) - 1):
            (t0, n0), (t1, n1) = seq[k], seq[k + 1]
            length = _dist(nodes[n0], nodes[n1])
            terminal_out = None
            if len(seq) > 2:
                if k == 0 and incident.get(n0, 0) == 1:
                    terminal_out = n0
                elif k == len(seq) - 2 and incident.get(n1, 0) == 1:
                    terminal_out = n1
            if terminal_out is not None and length < overshoot:
                tails.append((li, length))
                continue
            segs.append((n0, n1, li, length))

    return {"nodes": nodes, "line_nodes": dict(line_nodes), "segs": segs,
            "dup_dropped": dups, "tail_dropped": tails}


def nearest_node(net, p):
    """The network node closest to a picked point."""
    best, best_d = None, None
    for i, q in enumerate(net["nodes"]):
        d = _dist(p, q)
        if best is None or d < best_d:
            best, best_d = i, d
    return best


def assign_depths(net, outfall):
    """Network distance of every reachable node from the outfall node
    (Dijkstra over the segments). {node_index: distance}."""
    adj = {}
    for n0, n1, _li, length in net["segs"]:
        adj.setdefault(n0, []).append((n1, length))
        adj.setdefault(n1, []).append((n0, length))
    dist = {outfall: 0.0}
    todo = [(0.0, outfall)]
    while todo:
        todo.sort()
        d, n = todo.pop(0)
        if d > dist.get(n, 1e30):
            continue
        for m, w in adj.get(n, []):
            nd = d + w
            if nd < dist.get(m, 1e30) - 1e-9:
                dist[m] = nd
                todo.append((nd, m))
    return dist


def plan_runs(net, depths):
    """Chop every line into buildable RUNS and classify the joints.

    A run is a maximal stretch of consecutive segments of one line all
    reachable from the outfall, with the depth changing by exactly the
    segment length (grade continuity - anything else means a loop fed
    the line from both sides, and that piece is left out with a note).

    Returns {"runs", "tees", "elbows", "notes"} where each run is
      {"line", "nodes": [node, ...], "a", "b"}  (a = shallowest end)
    tees   {"host_line", "branch_line", "node"}
    elbows {"la", "lb", "node"}
    """
    notes = []
    runs = []
    by_line = {}
    for li, seq in net["line_nodes"].items():
        chain = []
        for k in range(len(seq) - 1):
            (t0, n0), (t1, n1) = seq[k], seq[k + 1]
            length = _dist(net["nodes"][n0], net["nodes"][n1])
            ok = (n0 in depths and n1 in depths and
                  abs(abs(depths[n1] - depths[n0]) - length) <= DEPTH_EPS_MM)
            present = (n0, n1, li, length) in [
                (s[0], s[1], s[2], s[3]) for s in net["segs"]]
            if ok and present:
                chain.append((n0, n1))
            else:
                if chain:
                    runs.append({"line": li, "pairs": list(chain)})
                    chain = []
                if present and n0 in depths and n1 in depths:
                    notes.append("line {}: fed from both ends between "
                                 "two junctions - that piece is left "
                                 "for hand-modelling".format(li))
        if chain:
            runs.append({"line": li, "pairs": list(chain)})

    # merge monotone chains into node lists, orient shallow -> deep
    out_runs = []
    for r in runs:
        ns = [r["pairs"][0][0]]
        for n0, n1 in r["pairs"]:
            ns.append(n1)
        if depths[ns[0]] > depths[ns[-1]]:
            ns.reverse()
        # monotone check: split where direction of fall flips (a line
        # entered at its middle rises both ways - two separate runs)
        parts, cur = [], [ns[0]]
        rising = None
        for k in range(len(ns) - 1):
            up = depths[ns[k + 1]] > depths[ns[k]]
            if rising is None or up == rising:
                rising = up
                cur.append(ns[k + 1])
            else:
                parts.append(cur)
                cur = [ns[k], ns[k + 1]]
                rising = up
        parts.append(cur)
        for p in parts:
            if depths[p[0]] > depths[p[-1]]:
                p.reverse()
            out_runs.append({"line": r["line"], "nodes": p,
                             "a": p[0], "b": p[-1]})
        by_line.setdefault(r["line"], 0)
        by_line[r["line"]] += len(parts)

    # joints: what meets at every node
    run_ends = {}      # node -> [run_index, ...]
    run_through = {}   # node -> [run_index, ...]
    for ri, r in enumerate(out_runs):
        for n in (r["nodes"][0], r["nodes"][-1]):
            run_ends.setdefault(n, []).append(ri)
        for n in r["nodes"][1:-1]:
            run_through.setdefault(n, []).append(ri)

    tees, elbows = [], []
    for n in sorted(set(list(run_ends.keys()) + list(run_through.keys()))):
        through = run_through.get(n, [])
        ends = run_ends.get(n, [])
        if through:
            host = out_runs[through[0]]
            if len(through) > 1:
                notes.append("node {}: two runs cross straight through "
                             "each other - only '{}' was kept as the "
                             "host".format(n, host["line"]))
            if ends:
                tees.append({"host_line": host["line"],
                             "branch_line": out_runs[ends[0]]["line"],
                             "node": n})
                for ri in ends[1:]:
                    notes.append("node {}: a second branch (line {}) "
                                 "meets the same point - left "
                                 "unconnected".format(
                                     n, out_runs[ri]["line"]))
        elif len(ends) == 2:
            ra, rb = out_runs[ends[0]], out_runs[ends[1]]
            if ra is not rb:
                elbows.append({"la": ra["line"], "lb": rb["line"],
                               "node": n})
        elif len(ends) > 2:
            ra, rb = out_runs[ends[0]], out_runs[ends[1]]
            elbows.append({"la": ra["line"], "lb": rb["line"], "node": n})
            for ri in ends[2:]:
                notes.append("node {}: more than two runs end here - "
                             "line {} left unconnected".format(
                                 n, out_runs[ri]["line"]))

    return {"runs": out_runs, "tees": tees, "elbows": elbows,
            "notes": notes}


def solve(lines, pick, slope_n, join_tol=JOIN_TOL_MM,
          overshoot=OVERSHOOT_MM, min_run=MIN_RUN_MM):
    """The full plan: lines (+ the picked outfall point) in, buildable
    geometry out. All coordinates in the input unit; depths too.

    Returns {"outfall_node", "nodes", "depths", "runs", "tees",
    "elbows", "skipped"} where each run carries "a"/"b" node indices
    (a = shallow), and skipped is a list of human-readable sentences
    covering everything that is NOT built.
    """
    net = build_network(lines, join_tol, overshoot)
    skipped = []
    for li, kept in net["dup_dropped"]:
        skipped.append("line {} lies along line {} - drawn twice, the "
                       "shorter copy is dropped".format(li, kept))
    for li, length in net["tail_dropped"]:
        skipped.append("line {}: {:.0f} long stub past its junction "
                       "looks like drawing overshoot - dropped"
                       .format(li, length))

    out = nearest_node(net, pick)
    depths = assign_depths(net, out)

    reachable_lines = set()
    for n0, n1, li, _l in net["segs"]:
        if n0 in depths and n1 in depths:
            reachable_lines.add(li)
    for li in net["line_nodes"]:
        if net["line_nodes"][li] and li not in reachable_lines:
            skipped.append("line {} touches nothing on the way to the "
                           "outfall - not piped".format(li))

    plan = plan_runs(net, depths)
    runs = []
    for r in plan["runs"]:
        length = depths[r["b"]] - depths[r["a"]]
        if length < min_run:
            skipped.append("line {}: a {:.0f} long piece is too short "
                           "to build - skipped".format(r["line"], length))
            continue
        runs.append(r)
    skipped.extend(plan["notes"])

    kept_ids = set(id(r) for r in runs)
    tees = [t for t in plan["tees"]]
    elbows = [e for e in plan["elbows"]]
    return {"outfall_node": out, "nodes": net["nodes"], "depths": depths,
            "runs": runs, "tees": tees, "elbows": elbows,
            "skipped": skipped}


def node_z_m(depth_mm, invert_m, slope_n):
    """Invert level (metres) of a node ``depth_mm`` of network distance
    upstream of the outfall, whose invert is ``invert_m``, at 1:n."""
    return invert_m + (depth_mm / 1000.0) / float(slope_n)


# ---------------------------------------------------------------------------
# Revit API access
# ---------------------------------------------------------------------------
def _gstyle_name(el):
    try:
        return el.LineStyle.Name
    except Exception:
        return None


def _workset_name(doc, el):
    try:
        ws = doc.GetWorksetTable().GetWorkset(el.WorksetId)
        return ws.Name
    except Exception:
        return None


def collect_lines(doc, style=None, workset=None):
    """Straight model lines matching the filters:
    [(element, (ax_mm, ay_mm), (bx_mm, by_mm)), ...]. Arcs and splines
    never qualify - a pipe run is straight."""
    out = []
    for el in FilteredElementCollector(doc).OfClass(CurveElement):
        if not isinstance(el, ModelLine):
            continue
        try:
            crv = el.GeometryCurve
            if not isinstance(crv, Line):
                continue
        except Exception:
            continue
        if style and _gstyle_name(el) != style:
            continue
        if workset and _workset_name(doc, el) != workset:
            continue
        a = crv.GetEndPoint(0)
        b = crv.GetEndPoint(1)
        out.append((el, (a.X * MM_PER_FT, a.Y * MM_PER_FT),
                    (b.X * MM_PER_FT, b.Y * MM_PER_FT)))
    return out


def line_style_options(doc):
    """Sorted line-style names carried by the model's straight lines."""
    names = set()
    for el in FilteredElementCollector(doc).OfClass(CurveElement):
        if not isinstance(el, ModelLine):
            continue
        nm = _gstyle_name(el)
        if nm:
            names.add(nm)
    return sorted(names, key=lambda s: s.lower())


def workset_options(doc):
    """Sorted user workset names (empty when not workshared)."""
    try:
        from Autodesk.Revit.DB import FilteredWorksetCollector, WorksetKind
        out = [w.Name for w in FilteredWorksetCollector(doc)
               .OfKind(WorksetKind.UserWorkset)]
        return sorted(out, key=lambda s: s.lower())
    except Exception:
        return []


def _first_level_id(doc):
    for l in FilteredElementCollector(doc).OfClass(Level):
        return l.Id
    return None


def _set_segment(pipe, segment_id):
    """Point one pipe at a specific Pipe Segment (the 'Plastic -
    Schedule 40' style choice in the instance properties)."""
    if segment_id is None:
        return
    try:
        from Autodesk.Revit.DB import BuiltInParameter
        p = pipe.get_Parameter(BuiltInParameter.RBS_PIPE_SEGMENT_PARAM)
        if p is not None and not p.IsReadOnly:
            p.Set(segment_id)
    except Exception:
        pass


def build_network_pipes(doc, sol, sys_id, type_id, dia_mm, slope_n,
                        invert_m, log=None, segment_id=None):
    """Create the pipes, tees and elbows of a solved plan in ONE
    transaction (warnings dismissed as they come). Depth/XY are in mm.
    ``segment_id`` (optional) sets each pipe's Pipe Segment - the
    material/schedule choice - after creation.

    Returns {"pipes", "tees", "elbows", "failed", "notes"}."""
    from pymep_connect_fixtures import _tee_into_main, _conn_near, \
        set_pipe_dia
    from pymep_replace_structure import _quiet

    def say(m):
        if log is not None:
            log(m)

    lvl_id = _first_level_id(doc)
    if lvl_id is None:
        raise RuntimeError("The model has no levels - cannot create pipes.")
    dia_ft = dia_mm / MM_PER_FT

    def xyz(node, depth_mm):
        x, y = sol["nodes"][node]
        z_m = node_z_m(depth_mm, invert_m, slope_n)
        return XYZ(x / MM_PER_FT, y / MM_PER_FT,
                   z_m * 1000.0 / MM_PER_FT + dia_ft / 2.0)

    pipes_by_line = {}
    pieces_by_line = {}
    made, fitted, failed = 0, 0, 0
    notes = []

    t = Transaction(doc, "Lines to Pipes")
    _quiet(t)
    t.Start()
    try:
        for r in sol["runs"]:
            pa = xyz(r["a"], sol["depths"][r["a"]])
            pb = xyz(r["b"], sol["depths"][r["b"]])
            try:
                pipe = Pipe.Create(doc, sys_id, type_id, lvl_id, pa, pb)
                _set_segment(pipe, segment_id)
                set_pipe_dia(pipe, dia_ft)
            except Exception as ex:
                failed += 1
                say("  ! line {}: pipe not created ({})".format(
                    r["line"], ex))
                continue
            made += 1
            pipes_by_line.setdefault(r["line"], []).append((r, pipe))
            pieces_by_line.setdefault(r["line"], []).append(pipe)

        def piece_at(line, node):
            """The current piece of ``line`` whose curve passes the
            node (hosts get broken as tees land)."""
            p = xyz(node, sol["depths"][node])
            best, best_d = None, None
            for piece in pieces_by_line.get(line, []):
                try:
                    crv = piece.Location.Curve
                    d = crv.Distance(p)
                except Exception:
                    continue
                if best is None or d < best_d:
                    best, best_d = piece, d
            return best

        def run_pipe_ending(line, node):
            for r, pipe in pipes_by_line.get(line, []):
                if node in (r["a"], r["b"]):
                    return pipe
            return None

        for tee in sol["tees"]:
            host = piece_at(tee["host_line"], tee["node"])
            branch = run_pipe_ending(tee["branch_line"], tee["node"])
            if host is None or branch is None:
                failed += 1
                say("  ! tee at node {}: host or branch pipe missing"
                    .format(tee["node"]))
                continue
            p = xyz(tee["node"], sol["depths"][tee["node"]])
            c_end = _conn_near(branch, p)
            if c_end is None:
                failed += 1
                say("  ! tee at node {}: branch has no free connector"
                    .format(tee["node"]))
                continue
            other, fit = _tee_into_main(doc, c_end, host, p, notes)
            if other is not None:
                pieces_by_line[tee["host_line"]].append(other)
            if fit is not None:
                fitted += 1

        for el in sol["elbows"]:
            p = xyz(el["node"], sol["depths"][el["node"]])
            pa = run_pipe_ending(el["la"], el["node"])
            pb = run_pipe_ending(el["lb"], el["node"])
            if pa is None or pb is None:
                continue
            c1 = _conn_near(pa, p)
            c2 = _conn_near(pb, p)
            if c1 is None or c2 is None:
                notes.append("elbow at node {}: connectors not free"
                             .format(el["node"]))
                continue
            try:
                doc.Create.NewElbowFitting(c1, c2)
                fitted += 1
            except Exception as ex:
                notes.append("elbow at node {} not placed ({})".format(
                    el["node"], ex))

        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise

    return {"pipes": made, "fittings": fitted, "failed": failed,
            "notes": notes}
