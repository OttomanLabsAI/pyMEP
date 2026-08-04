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

import json
import math
import os

import clr
clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    CurveElement, FamilyInstance, FilteredElementCollector, Level, Line,
    ModelLine, Transaction, XYZ,
)
from Autodesk.Revit.DB.Plumbing import Pipe

from pymep_revit import safe_name

MM_PER_FT = 304.8
LINES_REGISTRY = "lines_network.json"
JOIN_TOL_MM = 50.0        # how close counts as touching
OVERSHOOT_MM = 2000.0     # terminal stub past a junction = drawing overshoot
MIN_RUN_MM = 160.0        # runs shorter than this cannot be built
DEPTH_EPS_MM = 1.0        # grade-linearity check along a through run


# ---------------------------------------------------------------------------
# pure geometry (stdlib only - unit-tested without Revit)
# ---------------------------------------------------------------------------
def load_lines_record(base):
    """The stored Lines-to-Pipes build record, or {} when there is
    none (or it is unreadable)."""
    try:
        with open(os.path.join(base, LINES_REGISTRY), "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def save_lines_record(base, record):
    if not os.path.isdir(base):
        os.makedirs(base)
    with open(os.path.join(base, LINES_REGISTRY), "w") as f:
        json.dump(record, f, indent=2, sort_keys=True)


def parse_style_slope(name):
    """The slope a line style NAME carries.

    'Pipe 1-80' -> 80.0 (a run at 1:80); anything containing 'custom'
    (case-insensitive) -> the string 'custom' (the user is asked per
    line); no trailing '1-<n>' -> None (the dialog's default gradient
    applies)."""
    if not name:
        return None
    if "custom" in name.lower():
        return "custom"
    import re
    m = re.search(r"1\s*[-:]\s*(\d+(?:\.\d+)?)\s*$", name)
    if m:
        try:
            v = float(m.group(1))
            return v if v > 0 else None
        except Exception:
            return None
    return None


def normalize_slopes(lines, slopes):
    """A per-line slope dict whatever the caller gave: a number means
    every line at that 1:n; a dict is taken as line-index -> n (missing
    indices get 1.0, i.e. rise == distance)."""
    if isinstance(slopes, dict):
        out = {}
        for i in range(len(lines)):
            out[i] = float(slopes.get(i, 1.0))
        return out
    n = float(slopes)
    return dict((i, n) for i in range(len(lines)))


def fit_plan(lines, width, height, pad=16.0):
    """Scale + offset that fits the lines' bounding box into a canvas
    of width x height with ``pad`` clear on every side, Y flipped so
    north is up. Returns (scale, ox, oy); canvas position of a model
    point is (x * scale + ox, -y * scale + oy)."""
    xs = [p[0] for l in lines for p in l]
    ys = [p[1] for l in lines for p in l]
    if not xs:
        return 1.0, pad, pad
    bw = max(xs) - min(xs) or 1.0
    bh = max(ys) - min(ys) or 1.0
    scale = min((width - 2 * pad) / bw, (height - 2 * pad) / bh)
    ox = pad - min(xs) * scale + ((width - 2 * pad) - bw * scale) / 2.0
    oy = height - pad + min(ys) * scale - \
        ((height - 2 * pad) - bh * scale) / 2.0
    return scale, ox, oy


def aim_pick(o_xy, dir_xys, segs, ray_fn, dist_fn):
    """Which network segment a node belongs to. ``segs`` is
    [(key, (ax, ay), (bx, by)), ...]; ``dir_xys`` the node's direction
    candidates in priority order (facing pair first). The first
    direction whose ray meets any segment wins, nearest hit first;
    with no hit at all the plan-nearest segment is returned instead.
    Returns (key, 'aimed' | 'nearest') or (None, None) when there are
    no segments. ``ray_fn``/``dist_fn`` are ray_hits_main and
    plan_dist_to_segment (injected so this stays pure and testable)."""
    for d in dir_xys:
        best, best_s = None, None
        for key, a_xy, b_xy in segs:
            hit = ray_fn(o_xy, d, a_xy, b_xy)
            if hit is None:
                continue
            s = math.hypot(hit[0] - o_xy[0], hit[1] - o_xy[1])
            if best is None or s < best_s:
                best, best_s = key, s
        if best is not None:
            return best, "aimed"
    best, best_d = None, None
    for key, a_xy, b_xy in segs:
        d = dist_fn(o_xy, a_xy, b_xy)
        if best is None or d < best_d:
            best, best_d = key, d
    return (best, "nearest") if best is not None else (None, None)


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


def assign_depths(net, outfall, slopes=None, init=None, descend=False):
    """The RISE of every reachable node above the outfall (Dijkstra
    over the segments, each weighted length / its line's 1:n slope).
    With ``slopes`` None every line is 1:1, so the result is plain
    network distance. {node_index: rise}.

    ``init`` ({node: starting_value}) seeds SEVERAL sources at their
    own levels - used when Invert Level marker families pin absolute
    levels at more than one node.

    ``descend`` flips the seeds from low points to HIGH points: levels
    FALL away from every source by length/slope instead of rising (the
    Invert Level node is the network's head - water enters there and
    falls away along the node's direction), and where two sources
    reach the same node the higher feed wins."""
    adj = {}
    for n0, n1, li, length in net["segs"]:
        n_slope = 1.0 if slopes is None else slopes.get(li, 1.0)
        w = length / n_slope
        adj.setdefault(n0, []).append((n1, w))
        adj.setdefault(n1, []).append((n0, w))
    if init:
        if descend:
            init = dict((n, -d) for n, d in init.items())
        dist = dict(init)
        todo = [(d, n) for n, d in init.items()]
    else:
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
    if init and descend:
        dist = dict((n, -d) for n, d in dist.items())
    return dist


def orient_tree(net, root, slopes=None):
    """Orient the network as a TREE rooted at the outfall: BFS from
    ``root`` gives every reachable node its parent (the next node on
    the way down to the outfall) and the edge weight length/slope.
    Segments that would close a loop are returned separately - a
    looped grid cannot carry one well-defined flow direction.

    Returns (parent, order, loops) where parent = {node: (parent_node
    or None, weight_to_parent)}, order = nodes in BFS sequence (root
    first - parents always precede children), loops = [seg, ...]."""
    adj = {}
    for s in net["segs"]:
        n0, n1, li, length = s
        n_slope = 1.0 if slopes is None else slopes.get(li, 1.0)
        w = length / n_slope
        adj.setdefault(n0, []).append((n1, w, s))
        adj.setdefault(n1, []).append((n0, w, s))
    parent = {root: (None, 0.0)}
    order = [root]
    k = 0
    while k < len(order):
        n = order[k]
        k += 1
        for m, w, s in adj.get(n, []):
            if m not in parent:
                parent[m] = (n, w)
                order.append(m)
    loops = []
    for s in net["segs"]:
        n0, n1 = s[0], s[1]
        if n0 in parent and n1 in parent:
            if parent[n0][0] != n1 and parent[n1][0] != n0:
                loops.append(s)
    return parent, order, loops


def assign_levels_heads(net, root, heads, slopes=None):
    """Levels for a network fed by Invert Level HEAD nodes, flowing to
    ONE outfall (``root`` - the picked low end, which needs no node).

    Water falls from every head toward the root at each line's own
    grade. Where two feeds MERGE the lower one governs and the run
    continues falling from it (the higher feed arrives steeper - it
    still drains). A branch with NO head above it is an upstream
    inlet: it RISES away from the main at its grade, so it falls INTO
    the network. Heads pin their level exactly; a feed arriving BELOW
    a pinned head is reported.

    Returns (z, loops, notes): {node: level}, loop-closing segments
    (left out - no defined direction), human-readable notes. z is
    empty when no head reaches the root's part of the network."""
    parent, order, loops = orient_tree(net, root, slopes)
    heads = {n: float(v) for n, v in heads.items() if n in parent}
    notes = []
    if not heads:
        return {}, loops, notes
    # feed(N) = the lowest level any head above N delivers AT N,
    # falling at grade all the way down - children before parents in
    # reversed BFS order
    feed = {}
    kids = {}
    for n in order[1:]:
        p, w = parent[n]
        kids.setdefault(p, []).append((n, w))
    for n in reversed(order):
        cands = []
        for c, w in kids.get(n, []):
            if c in feed:
                cands.append(feed[c] - w)
        if n in heads:
            for v in cands:
                if v < heads[n] - DEPTH_EPS_MM:
                    notes.append(
                        "a feed arrives {:.0f} below the Invert Level "
                        "node pinned at node {} - check the node "
                        "levels".format(heads[n] - v, n))
            feed[n] = heads[n]
        elif cands:
            feed[n] = min(cands)
    z = {}
    for n in order:
        if n in feed:
            z[n] = feed[n]
        else:
            p, w = parent[n]
            if p is None or p not in z:
                continue
            z[n] = z[p] + w        # headless branch rises upstream
    return z, loops, notes


def plan_runs(net, depths, slopes=None, steeper_ok=False):
    """Chop every line into buildable RUNS and classify the joints.

    A run is a maximal stretch of consecutive segments of one line all
    reachable from the outfall, with the rise changing by exactly the
    segment length over the line's slope (grade continuity - anything
    else means a loop fed the line from both sides, and that piece is
    left out with a note). ``steeper_ok`` also accepts MORE fall than
    the grade - head-fed solving drops a merge's higher feed onto the
    lower one, which makes that one stretch steeper, never flatter.

    Returns {"runs", "tees", "elbows", "notes"} where each run is
      {"line", "nodes": [node, ...], "a", "b"}  (a = shallowest end)
    tees   {"host_line", "branch_line", "node"}
    elbows {"la", "lb", "node"}
    """
    notes = []
    runs = []
    by_line = {}
    for li, seq in net["line_nodes"].items():
        n_slope = 1.0 if slopes is None else slopes.get(li, 1.0)
        chain = []
        for k in range(len(seq) - 1):
            (t0, n0), (t1, n1) = seq[k], seq[k + 1]
            length = _dist(net["nodes"][n0], net["nodes"][n1])
            rise = length / n_slope
            fall = (abs(depths[n1] - depths[n0])
                    if n0 in depths and n1 in depths else None)
            # exact grade; with steeper_ok also MORE fall than the
            # grade (a merge's higher feed drops onto the lower one -
            # it still drains), never less
            ok = fall is not None and (
                abs(fall - rise) <= DEPTH_EPS_MM or
                (steeper_ok and fall >= rise - DEPTH_EPS_MM))
            present = any(s[0] == n0 and s[1] == n1 and s[2] == li
                          for s in net["segs"])
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

    tees, elbows, joins = [], [], []
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
        elif len(ends) >= 3:
            # a MERGE point: three run ENDS meet (two feeds + the
            # continuation - two heads joining the outfall run). A tee
            # fitting joins all three; the most OPPOSITE pair carries
            # straight through, the third is the branch.
            def _dir_from(ri):
                seq = out_runs[ri]["nodes"]
                nx = seq[1] if seq[0] == n else seq[-2]
                ax, ay = net["nodes"][n]
                bx, by = net["nodes"][nx]
                dx, dy = bx - ax, by - ay
                ln = (dx * dx + dy * dy) ** 0.5 or 1.0
                return dx / ln, dy / ln
            three = ends[:3]
            best_pair, best_dot = (0, 1), 2.0
            for i2 in range(3):
                for j2 in range(i2 + 1, 3):
                    d1 = _dir_from(three[i2])
                    d2 = _dir_from(three[j2])
                    dot = d1[0] * d2[0] + d1[1] * d2[1]
                    if dot < best_dot:
                        best_dot, best_pair = dot, (i2, j2)
            k3 = [x for x in range(3) if x not in best_pair][0]
            joins.append({"node": n,
                          "runs": [out_runs[three[best_pair[0]]],
                                   out_runs[three[best_pair[1]]],
                                   out_runs[three[k3]]]})
            for ri in ends[3:]:
                notes.append("node {}: more than three runs end here - "
                             "line {} left unconnected".format(
                                 n, out_runs[ri]["line"]))

    return {"runs": out_runs, "tees": tees, "elbows": elbows,
            "joins": joins, "notes": notes}


def solve(lines, pick, slopes, join_tol=JOIN_TOL_MM,
          overshoot=OVERSHOOT_MM, min_run=MIN_RUN_MM, sources=None):
    """The full plan: lines (+ the picked outfall point) in, buildable
    geometry out. All coordinates in the input unit. ``slopes`` is one
    1:n for the whole network or a {line_index: n} dict - every line is
    graded at ITS OWN slope, and "depths" holds each node's RISE above
    the outfall (unit as the coordinates).

    ``sources`` ([((x, y), z), ...], same units) overrides the pick:
    each source pins its nearest network node at the ABSOLUTE level z
    (an Invert Level marker family). Markers are HEADS - the network
    FALLS from every one of them toward the OUTFALL at ``pick`` (the
    low end, which needs no node): merges continue from the LOWER
    feed, branches with no head above them RISE off the mains so they
    drain into the network, and loop-closing segments are left out.
    "depths" then holds absolute levels - build with invert 0.
    Markers on islands the pick cannot reach keep the simple rule:
    the network falls away from the marker.

    Returns {"outfall_node", "nodes", "depths", "runs", "tees",
    "elbows", "skipped", "source_nodes"} where each run carries
    "a"/"b" node indices (a = shallow), and skipped is a list of
    human-readable sentences covering everything that is NOT built.
    """
    slopes = normalize_slopes(lines, slopes)
    net = build_network(lines, join_tol, overshoot)
    skipped = []
    for li, kept in net["dup_dropped"]:
        skipped.append("line {} lies along line {} - drawn twice, the "
                       "shorter copy is dropped".format(li, kept))
    for li, length in net["tail_dropped"]:
        skipped.append("line {}: {:.0f} long stub past its junction "
                       "looks like drawing overshoot - dropped"
                       .format(li, length))

    source_nodes = []
    steeper = False
    if sources:
        heads = {}
        for (sx, sy), z in sources:
            n = nearest_node(net, (sx, sy))
            if n is not None and (n not in heads or z > heads[n]):
                heads[n] = float(z)
        source_nodes = sorted(heads)
        out = nearest_node(net, pick)
        depths, loop_segs, head_notes = assign_levels_heads(
            net, out, heads, slopes)
        skipped.extend(head_notes)
        for s in loop_segs:
            skipped.append("line {}: a segment closes a LOOP - flow "
                           "has no single direction there, left for "
                           "hand-modelling".format(s[2]))
        if loop_segs:
            drop = set(id(s) for s in loop_segs)
            net["segs"] = [s for s in net["segs"]
                           if id(s) not in drop]
        # markers on islands the outfall pick cannot reach: the
        # network still falls away from each of them
        left = {n: v for n, v in heads.items() if n not in depths}
        if left:
            isl = assign_depths(net, None, slopes, init=left,
                                descend=True)
            for n, v in isl.items():
                if n not in depths:
                    depths[n] = v
        steeper = True
    else:
        out = nearest_node(net, pick)
        depths = assign_depths(net, out, slopes)

    reachable_lines = set()
    for n0, n1, li, _l in net["segs"]:
        if n0 in depths and n1 in depths:
            reachable_lines.add(li)
    for li in net["line_nodes"]:
        if net["line_nodes"][li] and li not in reachable_lines:
            skipped.append("line {} touches nothing on the way to the "
                           "outfall - not piped".format(li))

    plan = plan_runs(net, depths, slopes, steeper_ok=steeper)
    runs = []
    for r in plan["runs"]:
        length = sum(_dist(net["nodes"][r["nodes"][k]],
                           net["nodes"][r["nodes"][k + 1]])
                     for k in range(len(r["nodes"]) - 1))
        if length < min_run:
            skipped.append("line {}: a {:.0f} long piece is too short "
                           "to build - skipped".format(r["line"], length))
            continue
        runs.append(r)
    skipped.extend(plan["notes"])

    kept_ids = set(id(r) for r in runs)
    tees = [t for t in plan["tees"]]
    elbows = [e for e in plan["elbows"]]
    joins = [j for j in plan.get("joins", [])
             if all(id(r) in kept_ids for r in j["runs"])]
    return {"outfall_node": out, "nodes": net["nodes"], "depths": depths,
            "runs": runs, "tees": tees, "elbows": elbows,
            "joins": joins, "skipped": skipped,
            "source_nodes": source_nodes}


def node_z_m(rise_mm, invert_m):
    """Invert level (metres) of a node ``rise_mm`` above the outfall,
    whose invert is ``invert_m``. The slope is already inside the rise
    - solve() accumulates length/n per line."""
    return invert_m + rise_mm / 1000.0


# ---------------------------------------------------------------------------
# Revit API access
# ---------------------------------------------------------------------------
INVERT_MARKER_PARAM = "Invert Level"


def workplane_z_ft(el):
    """The internal elevation of a model line's work plane (the lines
    are drawn ON the datum level - 'Level : Datum Level' in their
    properties), or None. This is the authoritative datum the typed
    marker inverts are measured above."""
    try:
        sp = el.SketchPlane
        if sp is not None:
            return sp.GetPlane().Origin.Z
    except Exception:
        pass
    return None


def _marker_z_ft(doc, el, loc_z_ft, datum_z_ft=None):
    """A marker's invert elevation in feet.

    First choice: the family's own 'Invert Level' parameter - the user
    types the invert straight into the marker and the family can sit
    flat on its level (a zero value means not filled in and falls
    through). Fallback: the Level's PROJECT elevation (the number the
    level displays - NOT its distance from Revit's internal origin)
    plus 'Elevation from Level'. The location point Z is the last
    resort."""
    try:
        prm = el.LookupParameter(INVERT_MARKER_PARAM)
        if prm is not None and prm.HasValue and \
                str(prm.StorageType) == "Double":
            v = prm.AsDouble()
            if abs(v) > 1e-9:
                # the typed invert is measured ABOVE THE DATUM - the
                # work plane the lines are drawn on when known (the
                # authoritative base), else the marker's own level.
                # Pipe elevations display relative to that same level,
                # so the displayed invert reads the typed number.
                base = datum_z_ft
                if base is None:
                    base = 0.0
                    try:
                        lvl = doc.GetElement(el.LevelId)
                        if lvl is not None:
                            base = lvl.Elevation
                    except Exception:
                        pass
                return base + v
    except Exception:
        pass
    lvl_elev = None
    try:
        lvl = doc.GetElement(el.LevelId)
        if lvl is not None:
            try:
                lvl_elev = lvl.ProjectElevation
            except Exception:
                lvl_elev = lvl.Elevation
    except Exception:
        pass
    offset = None
    for name in ("Elevation from Level", "Offset from Host", "Offset",
                 "Elevation"):
        try:
            prm = el.LookupParameter(name)
            if prm is not None and prm.HasValue and \
                    str(prm.StorageType) == "Double":
                offset = prm.AsDouble()
                break
        except Exception:
            continue
    if lvl_elev is not None and offset is not None:
        return lvl_elev + offset
    return loc_z_ft


def find_invert_markers(doc, name_hint="invert", datum_z_ft=None):
    """Placed marker families that pin a HEAD level: any family
    instance whose FAMILY name contains ``name_hint`` (the user's
    'Node - Invert Level' generic model). Returns
    [(element, (x_mm, y_mm), z_mm), ...] where z = the typed invert
    over the datum (else Level + 'Elevation from Level') - the HIGH
    point the network falls away from at that spot."""
    out = []
    for el in FilteredElementCollector(doc).OfClass(FamilyInstance):
        try:
            fam = el.Symbol.Family.Name
        except Exception:
            continue
        if name_hint not in (fam or "").lower():
            continue
        try:
            p = el.Location.Point
        except Exception:
            continue
        if p is None:
            continue
        z_ft = _marker_z_ft(doc, el, p.Z, datum_z_ft)
        out.append((el, (p.X * MM_PER_FT, p.Y * MM_PER_FT),
                    z_ft * MM_PER_FT))
    return out


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
    [(element, (ax_mm, ay_mm), (bx_mm, by_mm), style_name), ...]. Arcs
    and splines never qualify - a pipe run is straight."""
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
                    (b.X * MM_PER_FT, b.Y * MM_PER_FT),
                    _gstyle_name(el)))
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


def build_network_pipes(doc, sol, sys_id, type_id, dia_mm, invert_m,
                        log=None, segment_id=None, reuse=None):
    """Create OR UPDATE the pipes, tees and elbows of a solved plan in
    ONE transaction (warnings dismissed as they come). XY and the rises
    in sol["depths"] are in mm - each line's slope is already inside
    its rise. ``segment_id`` (optional) sets each pipe's Pipe Segment.

    ``reuse`` ({line_index: [existing Pipe elements]}) makes this an
    IN-PLACE update: each run takes a pipe from its line's pool and
    RE-SETS its location curve instead of creating a new element - the
    element id survives, so tags in drawings keep their host. Pool
    pipes that cannot be re-curved fall back to a fresh pipe (logged).

    Returns {"pipes", "updated", "fittings", "failed", "notes",
    "elements", "pieces_by_line", "fitting_elements",
    "reused_elements"}."""
    from pymep_connect_fixtures import _tee_into_main, _conn_near, \
        _set_dia
    from pymep_replace_structure import _quiet

    def say(m):
        if log is not None:
            log(m)

    lvl_id = _first_level_id(doc)
    if lvl_id is None:
        raise RuntimeError("The model has no levels - cannot create pipes.")
    dia_ft = dia_mm / MM_PER_FT

    def xyz(node, rise_mm):
        x, y = sol["nodes"][node]
        z_m = node_z_m(rise_mm, invert_m)
        return XYZ(x / MM_PER_FT, y / MM_PER_FT,
                   z_m * 1000.0 / MM_PER_FT + dia_ft / 2.0)

    pipes_by_line = {}
    pieces_by_line = {}
    created = []
    fitting_elements = []
    reused_elements = []
    made, updated, fitted, failed = 0, 0, 0, 0
    notes = []

    t = Transaction(doc, "Lines to Pipes")
    _quiet(t)
    t.Start()
    try:
        for r in sol["runs"]:
            pa = xyz(r["a"], sol["depths"][r["a"]])
            pb = xyz(r["b"], sol["depths"][r["b"]])
            pipe = None
            pool = (reuse or {}).get(r["line"]) or []
            while pool:
                cand = pool.pop(0)
                try:
                    cand.Location.Curve = Line.CreateBound(pa, pb)
                    _set_segment(cand, segment_id)
                    _set_dia(cand, dia_ft)
                    pipe = cand
                    updated += 1
                    reused_elements.append(cand)
                    break
                except Exception as ex:
                    notes.append("line {}: an existing pipe would not "
                                 "take the new geometry ({}) - replaced "
                                 "with a fresh one".format(r["line"], ex))
            if pipe is None:
                try:
                    pipe = Pipe.Create(doc, sys_id, type_id, lvl_id,
                                       pa, pb)
                    _set_segment(pipe, segment_id)
                    _set_dia(pipe, dia_ft)
                except Exception as ex:
                    failed += 1
                    say("  ! line {}: pipe not created ({})".format(
                        r["line"], ex))
                    continue
                made += 1
                created.append(pipe)
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
                created.append(other)
            if fit is not None:
                fitted += 1
                created.append(fit)
                fitting_elements.append(fit)

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
                fit = doc.Create.NewElbowFitting(c1, c2)
                created.append(fit)
                fitting_elements.append(fit)
                fitted += 1
            except Exception as ex:
                notes.append("elbow at node {} not placed ({})".format(
                    el["node"], ex))

        # 3-way MERGES: three run ends joined by one tee fitting (two
        # head feeds meeting the outfall run) - the solver ordered the
        # straight-through pair first
        run_pipe_by_id = {}
        for _li2, lst in pipes_by_line.items():
            for _r2, _p2 in lst:
                run_pipe_by_id[id(_r2)] = _p2
        for jn in sol.get("joins", []):
            p = xyz(jn["node"], sol["depths"][jn["node"]])
            ps = [run_pipe_by_id.get(id(r2)) for r2 in jn["runs"]]
            if any(pp is None for pp in ps):
                notes.append("3-way join at node {}: a pipe is "
                             "missing".format(jn["node"]))
                continue
            cs = [_conn_near(pp, p) for pp in ps]
            if any(c is None for c in cs):
                notes.append("3-way join at node {}: connectors not "
                             "free".format(jn["node"]))
                continue
            try:
                fit = doc.Create.NewTeeFitting(cs[0], cs[1], cs[2])
                created.append(fit)
                fitting_elements.append(fit)
                fitted += 1
            except Exception as ex:
                notes.append("3-way join at node {} not placed ({})"
                             .format(jn["node"], ex))

        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise

    return {"pipes": made, "updated": updated, "fittings": fitted,
            "failed": failed, "notes": notes, "elements": created,
            "pieces_by_line": pieces_by_line,
            "fitting_elements": fitting_elements,
            "reused_elements": reused_elements}
