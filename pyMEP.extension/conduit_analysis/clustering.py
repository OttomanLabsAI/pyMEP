"""Cluster pipes and fittings into connected runs."""
import numpy as np
from collections import defaultdict


TOLERANCE = 5.0  # mm


def dist(a, b):
    return np.linalg.norm(np.array(a) - np.array(b))


def build_elements(pipes, fittings):
    """Create unified element list from parsed pipe/fitting data."""
    elems = []
    for p in pipes:
        elems.append({
            "id": p["ID"].strip(),
            "type": "Pipe",
            "sp": p["_sp"],
            "ep": p["_ep"],
            "row": p,
        })
    for f in fittings:
        elems.append({
            "id": f["ID"].strip(),
            "type": "Fitting",
            "sp": f["_sp"],
            "ep": f["_ep"],
            "row": f,
        })
    return elems


def build_adjacency(elems):
    """Build adjacency graph based on endpoint proximity."""
    adj = defaultdict(set)
    for i in range(len(elems)):
        for j in range(i + 1, len(elems)):
            a, b = elems[i], elems[j]
            connected = False
            for pa in (a["sp"], a["ep"]):
                for pb in (b["sp"], b["ep"]):
                    if dist(pa, pb) < TOLERANCE:
                        connected = True
                        break
                if connected:
                    break
            if connected:
                adj[i].add(j)
                adj[j].add(i)
    return adj


def cluster_elements(elems, adj):
    """BFS clustering into connected runs."""
    visited = set()
    clusters = []
    for start in range(len(elems)):
        if start in visited:
            continue
        cluster = []
        queue = [start]
        while queue:
            node = queue.pop(0)
            if node in visited:
                continue
            visited.add(node)
            cluster.append(node)
            for nb in adj[node]:
                if nb not in visited:
                    queue.append(nb)
        clusters.append(cluster)
    return clusters


def order_chain(cluster, elems, adj):
    """Walk the chain from an endpoint to produce ordered element indices."""
    cluster_set = set(cluster)

    # Find endpoints (≤1 neighbour within cluster)
    endpoints = []
    for idx in cluster:
        nbrs = adj[idx] & cluster_set
        if len(nbrs) <= 1:
            endpoints.append(idx)
    if not endpoints:
        endpoints = [cluster[0]]

    # Walk from first endpoint
    chain = []
    cur = endpoints[0]
    seen = set()
    while cur is not None:
        seen.add(cur)
        chain.append(cur)
        nxt = None
        for nb in adj[cur]:
            if nb in cluster_set and nb not in seen:
                nxt = nb
                break
        cur = nxt
    return chain


def orient_chains(ordered_runs, elems):
    """Ensure all chains start from the same direction (smallest X free endpoint)."""
    for ri, chain in enumerate(ordered_runs):
        first = elems[chain[0]]
        last = elems[chain[-1]]

        # Free endpoint of first element
        first_free = first["sp"]
        if len(chain) > 1:
            nxt = elems[chain[1]]
            for pt in (first["sp"], first["ep"]):
                connected = False
                for pt2 in (nxt["sp"], nxt["ep"]):
                    if dist(pt, pt2) < TOLERANCE:
                        connected = True
                        break
                if not connected:
                    first_free = pt
                    break

        # Free endpoint of last element
        last_free = last["ep"]
        if len(chain) > 1:
            prev = elems[chain[-2]]
            for pt in (last["sp"], last["ep"]):
                connected = False
                for pt2 in (prev["sp"], prev["ep"]):
                    if dist(pt, pt2) < TOLERANCE:
                        connected = True
                        break
                if not connected:
                    last_free = pt
                    break

        # Start from smallest X
        if last_free[0] < first_free[0]:
            ordered_runs[ri] = list(reversed(chain))

    # Sort runs by Z then Y of start point
    def sort_key(chain):
        e = elems[chain[0]]
        sp = min([e["sp"], e["ep"]], key=lambda p: p[0])
        return (sp[2], sp[1])

    ordered_runs.sort(key=sort_key)
    return ordered_runs


def cluster_and_order(pipes, fittings):
    """Full pipeline: build elements → cluster → order → orient. Returns (elems, ordered_runs)."""
    elems = build_elements(pipes, fittings)
    adj = build_adjacency(elems)
    clusters = cluster_elements(elems, adj)

    ordered_runs = []
    for cluster in clusters:
        chain = order_chain(cluster, elems, adj)
        ordered_runs.append(chain)

    ordered_runs = orient_chains(ordered_runs, elems)
    return elems, ordered_runs


def cluster_runs_into_collections(all_run_curves, tolerance=2000.0):
    """Group runs into collections based on endpoint proximity.

    Two runs belong to the same collection if BOTH their start points
    are within tolerance AND their end points are within tolerance.

    Returns: list of collections, each a list of run indices.
             E.g. [[0,1,2,3], [4,5,6,7]] for two collections of 4 runs each.
    """
    n = len(all_run_curves)
    if n == 0:
        return []

    endpoints = []
    for curves in all_run_curves:
        start = np.array(curves[0]["points"][0])
        end = np.array(curves[-1]["points"][-1])
        endpoints.append((start, end))

    # Build adjacency: runs are linked if their starts AND ends are close
    adj = {i: set() for i in range(n)}
    for i in range(n):
        for j in range(i + 1, n):
            s_dist = np.linalg.norm(endpoints[i][0] - endpoints[j][0])
            e_dist = np.linalg.norm(endpoints[i][1] - endpoints[j][1])
            if s_dist < tolerance and e_dist < tolerance:
                adj[i].add(j)
                adj[j].add(i)

    # BFS clustering
    visited = set()
    collections = []
    for start in range(n):
        if start in visited:
            continue
        cluster = []
        queue = [start]
        while queue:
            node = queue.pop(0)
            if node in visited:
                continue
            visited.add(node)
            cluster.append(node)
            for nb in adj[node]:
                if nb not in visited:
                    queue.append(nb)
        collections.append(sorted(cluster))

    return collections
