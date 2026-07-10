"""Compute centrelines and bounding profiles for conduit arrays."""
import math
import numpy as np
from .clustering import dist, TOLERANCE


def norm(v):
    n = np.linalg.norm(v)
    return v / n if n > 1e-10 else v


def _resolve_od(od_mm, col_id, default=205.0):
    """Resolve the OD to use for a given collection.

    od_mm may be a plain float (one OD for the whole network - legacy) or a
    dict {col_id: od_mm}. Falls back to `default` if a collection is missing.
    """
    if isinstance(od_mm, dict):
        v = od_mm.get(col_id)
        if v is None:
            return float(default)
        return float(v)
    return float(od_mm)


def compute_collection_ods(all_run_curves, collections, default_od=205.0):
    """For each collection, the encasement OD = the LARGEST pipe OD in it.

    A bundle is encased to fit its biggest pipe; a lone pipe uses its own OD.
    Curves without an OD (e.g. fittings) are ignored. Returns {col_id: od_mm}.
    """
    od_by_col = {}
    for col_id, run_ids in enumerate(collections):
        ods = []
        for r in run_ids:
            for c in all_run_curves[r]:
                od = c.get("od")
                if od is not None and od > 0:
                    ods.append(float(od))
        od_by_col[col_id] = max(ods) if ods else float(default_od)
    return od_by_col


def arc_points_from_3pts(sp, ep, mid, n_pts=20):
    """Generate arc sample points from start, end, and midpoint."""
    sp = np.array(sp, dtype=float)
    ep = np.array(ep, dtype=float)
    mid = np.array(mid, dtype=float)

    # Find circumscribed circle centre from 3 points
    ax, ay, az = sp
    bx, by, bz = mid
    cx, cy, cz = ep

    # Use the plane defined by the 3 points
    v1 = mid - sp
    v2 = ep - sp
    plane_n = np.cross(v1, v2)
    pn_len = np.linalg.norm(plane_n)
    if pn_len < 1e-10:
        # Collinear — just return straight line
        return [sp + (ep - sp) * (i / float(n_pts)) for i in range(n_pts + 1)]
    plane_n = plane_n / pn_len

    # Project to 2D in the plane, solve for circumcentre
    u = norm(v1)
    v = norm(np.cross(plane_n, u))
    origin = sp

    def to2d(p):
        d = p - origin
        return np.array([np.dot(d, u), np.dot(d, v)])

    def to3d(p2d):
        return origin + p2d[0] * u + p2d[1] * v

    a2 = to2d(sp)
    b2 = to2d(mid)
    c2 = to2d(ep)

    D = 2 * (a2[0] * (b2[1] - c2[1]) + b2[0] * (c2[1] - a2[1]) + c2[0] * (a2[1] - b2[1]))
    if abs(D) < 1e-12:
        return [sp + (ep - sp) * (i / float(n_pts)) for i in range(n_pts + 1)]

    ux = ((a2[0]**2 + a2[1]**2) * (b2[1] - c2[1]) +
          (b2[0]**2 + b2[1]**2) * (c2[1] - a2[1]) +
          (c2[0]**2 + c2[1]**2) * (a2[1] - b2[1])) / D
    uy = ((a2[0]**2 + a2[1]**2) * (c2[0] - b2[0]) +
          (b2[0]**2 + b2[1]**2) * (a2[0] - c2[0]) +
          (c2[0]**2 + c2[1]**2) * (b2[0] - a2[0])) / D

    centre = to3d(np.array([ux, uy]))
    r = np.linalg.norm(centre - sp)

    # Generate arc via Rodrigues rotation
    v_start = norm(sp - centre)
    v_end = norm(ep - centre)

    dot_val = np.clip(np.dot(v_start, v_end), -1, 1)
    total_angle = math.acos(dot_val)

    # Determine rotation direction
    cross = np.cross(v_start, v_end)
    if np.dot(cross, plane_n) < 0:
        plane_n = -plane_n

    # Check midpoint is on the correct side
    v_mid_check = norm(mid - centre)
    mid_angle = math.acos(np.clip(np.dot(v_start, v_mid_check), -1, 1))
    cross_mid = np.cross(v_start, v_mid_check)
    if np.dot(cross_mid, plane_n) < 0:
        # Wrong direction — go the long way around
        total_angle = 2 * math.pi - total_angle
        plane_n = -plane_n

    points = []
    for i in range(n_pts + 1):
        frac = i / float(n_pts)
        angle = total_angle * frac
        v_rot = (v_start * math.cos(angle)
                 + np.cross(plane_n, v_start) * math.sin(angle)
                 + plane_n * np.dot(plane_n, v_start) * (1 - math.cos(angle)))
        points.append(centre + v_rot * r)

    return points


def build_run_curves(chain, elems, n_arc_pts=20):
    """Build oriented curve points for a single run.

    Returns list of dicts with 'type', 'id', 'points', 'sp', 'ep', 'od'.
    Handles fitting orientation based on chain connectivity.
    'od' is the element's own outer diameter in mm (None if unavailable, e.g.
    for fittings, which inherit size from neighbouring pipes downstream).
    """
    curves = []

    def _od_of(elem):
        row = elem.get("row") or {}
        val = row.get("OD_mm", "")
        if val in (None, "", " "):
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    for i, idx in enumerate(chain):
        elem = elems[idx]
        prev_idx = chain[i - 1] if i > 0 else None
        next_idx = chain[i + 1] if i < len(chain) - 1 else None

        if elem["type"] == "Fitting":
            fit_a = elem["sp"].copy()
            fit_b = elem["ep"].copy()
            row = elem["row"]
            def _float(v, default=0.0):
                try:
                    return float(v) if v not in (None, "", " ") else default
                except (ValueError, TypeError):
                    return default
            radius = _float(row.get("BendRadius_mm"))
            angle  = _float(row.get("BendAngle_deg"))

            # Orient fitting endpoints based on chain connectivity
            if prev_idx is not None:
                prev = elems[prev_idx]
                da = min(dist(fit_a, prev["sp"]), dist(fit_a, prev["ep"]))
                db = min(dist(fit_b, prev["sp"]), dist(fit_b, prev["ep"]))
                if db < da:
                    sp, ep = fit_b, fit_a
                else:
                    sp, ep = fit_a, fit_b
            elif next_idx is not None:
                nxt = elems[next_idx]
                da = min(dist(fit_a, nxt["sp"]), dist(fit_a, nxt["ep"]))
                db = min(dist(fit_b, nxt["sp"]), dist(fit_b, nxt["ep"]))
                if da < db:
                    sp, ep = fit_b, fit_a
                else:
                    sp, ep = fit_a, fit_b
            else:
                sp, ep = fit_a, fit_b

            # Get adjacent pipe directions for arc computation
            prev_dir = next_dir = None
            if prev_idx is not None:
                prev = elems[prev_idx]
                ps, pe = prev["sp"], prev["ep"]
                if np.linalg.norm(pe - sp) < np.linalg.norm(ps - sp):
                    prev_dir = pe - ps
                else:
                    prev_dir = ps - pe

            if next_idx is not None:
                nxt = elems[next_idx]
                ps, pe = nxt["sp"], nxt["ep"]
                if np.linalg.norm(ps - ep) < np.linalg.norm(pe - ep):
                    next_dir = pe - ps
                else:
                    next_dir = ps - pe

            if prev_dir is None:
                prev_dir = ep - sp
            if next_dir is None:
                next_dir = ep - sp

            # Compute arc using perpendicular-to-approach direction
            prev_dir = norm(np.array(prev_dir, dtype=float))
            next_dir = norm(np.array(next_dir, dtype=float))
            plane_n = np.cross(prev_dir, next_dir)
            pn_len = np.linalg.norm(plane_n)

            if pn_len < 1e-10 or radius < 1.0:
                # No bend — straight line
                curves.append({"type": "Line", "id": elem["id"],
                               "points": [sp, ep], "sp": sp, "ep": ep,
                               "od": _od_of(elem)})
                continue

            plane_n = plane_n / pn_len
            perp = norm(np.cross(plane_n, prev_dir))
            if np.dot(perp, ep - sp) < 0:
                perp = -perp
            centre = sp + perp * radius
            r_check = np.linalg.norm(centre - ep)
            if abs(radius - r_check) > 50:
                # Fallback: use midpoint-based arc
                mid = (sp + ep) / 2.0 + perp * radius * 0.1
                pts = arc_points_from_3pts(sp, ep, mid, n_arc_pts)
            else:
                v0 = norm(sp - centre)
                v1 = norm(ep - centre)
                vm = norm((v0 + v1))
                mid = centre + vm * radius
                pts = arc_points_from_3pts(sp, ep, mid, n_arc_pts)

            curves.append({"type": "Arc", "id": elem["id"],
                           "points": pts, "sp": sp, "ep": ep,
                           "radius": radius, "angle": angle,
                           "od": _od_of(elem)})

        else:
            # Pipe — straight line
            sp = elem["sp"].copy()
            ep = elem["ep"].copy()

            # Orient based on connectivity
            if next_idx is not None:
                nxt = elems[next_idx]
                d_sp = min(dist(sp, nxt["sp"]), dist(sp, nxt["ep"]))
                d_ep = min(dist(ep, nxt["sp"]), dist(ep, nxt["ep"]))
                if d_sp < d_ep:
                    sp, ep = ep, sp
            elif prev_idx is not None:
                prev = elems[prev_idx]
                d_sp = min(dist(sp, prev["sp"]), dist(sp, prev["ep"]))
                d_ep = min(dist(ep, prev["sp"]), dist(ep, prev["ep"]))
                if d_ep < d_sp:
                    sp, ep = ep, sp

            curves.append({"type": "Line", "id": elem["id"],
                           "points": [sp, ep], "sp": sp, "ep": ep,
                           "od": _od_of(elem)})

    # Orient curve 0 against curve 1
    if len(curves) >= 2:
        c0s, c0e = curves[0]["points"][0], curves[0]["points"][-1]
        c1s, c1e = curves[1]["points"][0], curves[1]["points"][-1]
        ds = [dist(c0e, c1s), dist(c0e, c1e), dist(c0s, c1s), dist(c0s, c1e)]
        if ds.index(min(ds)) >= 2:
            curves[0]["points"] = list(reversed(curves[0]["points"]))

    # Fix connectivity: reverse subsequent curves if needed
    for i in range(1, len(curves)):
        prev_end = np.array(curves[i - 1]["points"][-1])
        curr_start = np.array(curves[i]["points"][0])
        curr_end = np.array(curves[i]["points"][-1])
        if np.linalg.norm(prev_end - curr_end) < np.linalg.norm(prev_end - curr_start):
            curves[i]["points"] = list(reversed(curves[i]["points"]))

    return curves


def compute_average_centreline(all_run_curves, od_mm, cover_mm):
    """Compute average centreline and bounding profiles across all runs.

    Uses the longest run as reference. For each segment, evaluates all runs'
    actual curves at params 0.0, 0.5, 1.0 and averages.

    Returns list of dicts: {type, points, sp, ep, half_w, half_h}
    """
    # Find longest run (most points = most segments)
    ref_idx = 0
    ref_len = 0
    for i, curves in enumerate(all_run_curves):
        total = sum(np.linalg.norm(np.array(c["points"][-1]) - np.array(c["points"][0]))
                    for c in curves)
        if total > ref_len:
            ref_len = total
            ref_idx = i
    ref_curves = all_run_curves[ref_idx]
    # Viz-only helper: if given a per-collection OD map, use the largest OD as a
    # single representative value (this function builds one global centreline).
    if isinstance(od_mm, dict):
        _vals = [v for v in od_mm.values() if v]
        half_od = (max(_vals) if _vals else 205.0) / 2.0
    else:
        half_od = od_mm / 2.0

    avg_segments = []

    for si, ref_crv in enumerate(ref_curves):
        ref_pts = ref_crv["points"]
        ref_sp = np.array(ref_pts[0])
        ref_ep = np.array(ref_pts[-1])
        ref_mid_idx = len(ref_pts) // 2
        ref_mp = np.array(ref_pts[ref_mid_idx])

        # Collect start/mid/end from all runs at this segment
        starts, mids, ends, all_mids = [], [], [], []

        for curves in all_run_curves:
            if si >= len(curves):
                continue
            crv = curves[si]
            pts = crv["points"]
            starts.append(np.array(pts[0]))
            ends.append(np.array(pts[-1]))
            mid_i = len(pts) // 2
            mids.append(np.array(pts[mid_i]))
            all_mids.append(np.array(pts[mid_i]))

        if not starts:
            continue

        avg_s = np.mean(starts, axis=0)
        avg_m = np.mean(mids, axis=0)
        avg_e = np.mean(ends, axis=0)

        # Compute profile at midpoint
        tangent = norm(avg_e - avg_s)
        up = np.array([0., 0., 1.])
        if abs(np.dot(tangent, up)) > 0.95:
            up = np.array([1., 0., 0.])
        xax = norm(np.cross(tangent, up))
        yax = norm(np.cross(xax, tangent))

        us = [np.dot(p - avg_m, xax) for p in all_mids]
        vs = [np.dot(p - avg_m, yax) for p in all_mids]
        hw = max((max(us) - min(us)) / 2.0 + half_od + cover_mm, half_od + cover_mm)
        hh = max((max(vs) - min(vs)) / 2.0 + half_od + cover_mm, half_od + cover_mm)

        # Build average curve
        if ref_crv["type"] == "Line":
            avg_pts = [avg_s, avg_e]
            curve_type = "Line"
        else:
            # Check collinearity
            v_sm = avg_m - avg_s
            v_se = avg_e - avg_s
            cross = np.cross(v_sm, v_se)
            if np.linalg.norm(cross) < 1e-8:
                avg_pts = [avg_s, avg_e]
                curve_type = "Line"
            else:
                avg_pts = arc_points_from_3pts(avg_s, avg_e, avg_m, n_pts=20)
                curve_type = "Arc"

        avg_segments.append({
            "type": curve_type,
            "points": avg_pts,
            "sp": avg_s,
            "ep": avg_e,
            "mid": avg_m,
            "half_w": hw,
            "half_h": hh,
        })

    return avg_segments


def _circumcentre_2d(a, b, c):
    """Find 2D circumcentre of 3 points. Returns None if collinear."""
    ax, ay = a[0], a[1]
    bx, by = b[0], b[1]
    cx, cy = c[0], c[1]
    D = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(D) < 1e-10:
        return None
    ux = ((ax**2 + ay**2) * (by - cy) + (bx**2 + by**2) * (cy - ay) + (cx**2 + cy**2) * (ay - by)) / D
    uy = ((ax**2 + ay**2) * (cx - bx) + (bx**2 + by**2) * (ax - cx) + (cx**2 + cy**2) * (bx - ax)) / D
    return np.array([ux, uy])


def compute_plan_bend_outlines(all_run_curves, collections, od_mm, cover_mm=0.0):
    """For each collection, find plan bends and compute 2D outline data.

    Each outline has TWO arcs with DIFFERENT centres:
    - Outer arc: matches outermost pipe's actual path + (half_od + cover) radially outward
    - Inner arc: matches innermost pipe's actual path - (half_od + cover) radially inward
    - Two connecting straight lines close the shape at each end

    A plan bend is an arc whose start/end Z coordinates match (within 5mm).
    """
    outlines = []

    for col_id, run_ids in enumerate(collections):
        if not run_ids:
            continue

        # Per-collection OD: each collection sizes to its own (largest) pipe.
        half_od = _resolve_od(od_mm, col_id) / 2.0
        offset = half_od + cover_mm

        def run_length(r):
            return sum(np.linalg.norm(np.array(c["points"][-1]) - np.array(c["points"][0]))
                       for c in all_run_curves[r])
        ref_run_idx = max(run_ids, key=run_length)
        ref_curves = all_run_curves[ref_run_idx]

        for bend_idx, ref_crv in enumerate(ref_curves):
            if ref_crv["type"] != "Arc":
                continue
            sp = np.array(ref_crv["points"][0])
            ep = np.array(ref_crv["points"][-1])
            if abs(ep[2] - sp[2]) > 5.0:
                continue

            # Gather arcs from all runs
            arcs = []
            for r in run_ids:
                if bend_idx < len(all_run_curves[r]):
                    c = all_run_curves[r][bend_idx]
                    if c["type"] == "Arc":
                        arcs.append(c["points"])
            if len(arcs) < 1:
                continue

            # Compute each arc's centre and radius
            arc_data = []
            for pts in arcs:
                p0 = np.array(pts[0])
                pm = np.array(pts[len(pts) // 2])
                pe = np.array(pts[-1])
                ctr = _circumcentre_2d(p0, pm, pe)
                if ctr is None:
                    continue
                r = math.sqrt((pm[0] - ctr[0]) ** 2 + (pm[1] - ctr[1]) ** 2)
                arc_data.append({
                    "pts": pts,
                    "centre_2d": ctr,
                    "radius": r,
                    "sp": p0,
                    "ep": pe,
                    "mp": pm,
                })
            if not arc_data:
                continue

            # Reference centroid: average of all arc centres
            ref_pt = np.mean([a["centre_2d"] for a in arc_data], axis=0)

            # Distance from ref_pt to each arc's midpoint — determines inner vs outer
            for a in arc_data:
                dx = a["mp"][0] - ref_pt[0]
                dy = a["mp"][1] - ref_pt[1]
                a["dist_from_ref"] = math.sqrt(dx * dx + dy * dy)

            outermost = max(arc_data, key=lambda a: a["dist_from_ref"])
            innermost = min(arc_data, key=lambda a: a["dist_from_ref"])

            # Z from average
            avg_z = float(np.mean([a["sp"][2] for a in arc_data]))

            def angles_of(arc):
                c = arc["centre_2d"]
                a_start = math.atan2(arc["sp"][1] - c[1], arc["sp"][0] - c[0])
                a_end   = math.atan2(arc["ep"][1] - c[1], arc["ep"][0] - c[0])
                delta = a_end - a_start
                while delta > math.pi:  delta -= 2 * math.pi
                while delta < -math.pi: delta += 2 * math.pi
                return a_start, a_start + delta, delta

            def round_angle_deg(rad):
                """Round an angle in radians to the nearest 5° and return radians."""
                deg = math.degrees(rad)
                deg = round(deg / 5.0) * 5.0
                return math.radians(deg)

            o_start_ang, o_end_ang, o_delta = angles_of(outermost)
            i_start_ang, i_end_ang, i_delta = angles_of(innermost)

            # Enforce bend angle ≤ 90°; if larger, we picked the wrong way.
            # Flip delta to go the short way the OTHER direction.
            if abs(math.degrees(o_delta)) > 90.5:
                o_delta = o_delta - math.copysign(2 * math.pi, o_delta)
            if abs(math.degrees(i_delta)) > 90.5:
                i_delta = i_delta - math.copysign(2 * math.pi, i_delta)

            # Both arcs must rotate the same way; if not, flip inner to match outer.
            if o_delta * i_delta < 0:
                i_delta = -i_delta
                # Recompute inner end as swapped start point for consistent rendering
                i_start_ang, i_end_ang = i_end_ang, i_start_ang
                i_delta = -i_delta  # final: same sign as o_delta

            # Round start/end angles to nearest 5°; preserve delta sign
            o_start_ang_r = round_angle_deg(o_start_ang)
            i_start_ang_r = round_angle_deg(i_start_ang)
            o_delta_r = round_angle_deg(o_delta)
            i_delta_r = round_angle_deg(i_delta)
            o_start_ang, o_delta = o_start_ang_r, o_delta_r
            i_start_ang, i_delta = i_start_ang_r, i_delta_r
            o_end_ang = o_start_ang + o_delta
            i_end_ang = i_start_ang + i_delta

            outer_R = outermost["radius"] + offset
            inner_R = max(innermost["radius"] - offset, 0.1)

            # Mean of individual pipe bend radii in this turn (used by the
            # Build Connections button to set elbow fitting radius).
            pipe_bend_radius_mm = float(
                sum(a["radius"] for a in arc_data) / len(arc_data))

            # Pipe envelope Z range for this bend (all runs in collection).
            # Height = (max centre Z + half_od + cover) - (min centre Z - half_od - cover)
            # i.e. Δcentres + OD + 2*cover. `offset` already equals half_od + cover.
            arc_zs = [float(a["sp"][2]) for a in arc_data]
            top_z_mm    = max(arc_zs) + offset
            bottom_z_mm = min(arc_zs) - offset

            def pt_on(centre_2d, r, ang):
                return np.array([
                    centre_2d[0] + r * math.cos(ang),
                    centre_2d[1] + r * math.sin(ang),
                    avg_z,
                ])

            outer_start = pt_on(outermost["centre_2d"], outer_R, o_start_ang)
            outer_end   = pt_on(outermost["centre_2d"], outer_R, o_end_ang)
            inner_start = pt_on(innermost["centre_2d"], inner_R, i_start_ang)
            inner_end   = pt_on(innermost["centre_2d"], inner_R, i_end_ang)

            # Sample arc points
            n_pts = 40
            outer_arc_pts = []
            inner_arc_pts = []
            for i in range(n_pts + 1):
                frac = i / float(n_pts)
                oa = o_start_ang + frac * o_delta
                ia = i_start_ang + frac * i_delta
                outer_arc_pts.append(pt_on(outermost["centre_2d"], outer_R, oa).tolist())
                inner_arc_pts.append(pt_on(innermost["centre_2d"], inner_R, ia).tolist())

            outlines.append({
                "collection_id": col_id,
                "bend_idx": bend_idx,
                "outer_centre": [float(outermost["centre_2d"][0]),
                                 float(outermost["centre_2d"][1]), avg_z],
                "inner_centre": [float(innermost["centre_2d"][0]),
                                 float(innermost["centre_2d"][1]), avg_z],
                "outer_radius": float(outer_R),
                "inner_radius": float(inner_R),
                "outer_start_angle_deg": math.degrees(o_start_ang) % 360,
                "outer_end_angle_deg":   math.degrees(o_end_ang) % 360,
                "inner_start_angle_deg": math.degrees(i_start_ang) % 360,
                "inner_end_angle_deg":   math.degrees(i_end_ang) % 360,
                "outer_start": outer_start.tolist(),
                "outer_end":   outer_end.tolist(),
                "inner_start": inner_start.tolist(),
                "inner_end":   inner_end.tolist(),
                "line1_start": outer_start.tolist(),
                "line1_end":   inner_start.tolist(),
                "line2_start": outer_end.tolist(),
                "line2_end":   inner_end.tolist(),
                "outer_arc_points": outer_arc_pts,
                "inner_arc_points": inner_arc_pts,
                "top_z_mm":    float(top_z_mm),
                "bottom_z_mm": float(bottom_z_mm),
                "pipe_bend_radius_mm": pipe_bend_radius_mm,
            })

    return outlines


def outlines_to_csv(outlines, output_path):
    """Write plan bend outlines as CSV."""
    header = ("Collection,BendIdx,"
              "OuterCentre_X,OuterCentre_Y,OuterCentre_Z,"
              "InnerCentre_X,InnerCentre_Y,InnerCentre_Z,"
              "OuterRadius_mm,InnerRadius_mm,PipeBendRadius_mm,"
              "OuterStartAngle_deg,OuterEndAngle_deg,"
              "InnerStartAngle_deg,InnerEndAngle_deg,"
              "TopZ_mm,BottomZ_mm,ZBase_mm,Height_mm,"
              "OuterArc_Start_X,OuterArc_Start_Y,OuterArc_Start_Z,"
              "OuterArc_End_X,OuterArc_End_Y,OuterArc_End_Z,"
              "InnerArc_Start_X,InnerArc_Start_Y,InnerArc_Start_Z,"
              "InnerArc_End_X,InnerArc_End_Y,InnerArc_End_Z,"
              "ConnectLine1_Start_X,ConnectLine1_Start_Y,ConnectLine1_Start_Z,"
              "ConnectLine1_End_X,ConnectLine1_End_Y,ConnectLine1_End_Z,"
              "ConnectLine2_Start_X,ConnectLine2_Start_Y,ConnectLine2_Start_Z,"
              "ConnectLine2_End_X,ConnectLine2_End_Y,ConnectLine2_End_Z")
    rows = []
    for o in outlines:
        vals = [
            o["collection_id"] + 1, o["bend_idx"],
            *o["outer_centre"], *o["inner_centre"],
            o["outer_radius"], o["inner_radius"], o["pipe_bend_radius_mm"],
            o["outer_start_angle_deg"], o["outer_end_angle_deg"],
            o["inner_start_angle_deg"], o["inner_end_angle_deg"],
            o["top_z_mm"], o["bottom_z_mm"], o["bottom_z_mm"], o["top_z_mm"] - o["bottom_z_mm"],
            *o["outer_start"], *o["outer_end"],
            *o["inner_start"], *o["inner_end"],
            *o["line1_start"], *o["line1_end"],
            *o["line2_start"], *o["line2_end"],
        ]
        rows.append(",".join("{:.2f}".format(v) if isinstance(v, float) else str(v)
                             for v in vals))

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(header + "\n")
        for r in rows:
            f.write(r + "\n")
    return len(rows)


def compute_straight_run_outlines(all_run_curves, collections, od_mm, cover_mm=0.0,
                                  plan_bend_outlines=None):
    """For each collection, find STRAIGHT segments and compute a 4-corner PLAN outline
    (to be extruded vertically).

    - Footprint in XY is a quadrilateral (trapezoid when adjacent to a plan bend;
      rectangle otherwise) following the pipe axis in plan.
    - Per-segment TopZ/BottomZ come from the pipe envelope; these can be unified
      per-collection later via unify_collection_z_ranges().

    Returns list of dicts:
      {collection_id, segment_idx,
       corner1..corner4 (XYZ, CCW viewed from above, Z = bottom_z_mm),
       top_z_mm, bottom_z_mm,
       centre_start, centre_end,
       half_width_mm, is_horizontal,
       duct_start, duct_end (centreline endpoints at z-mid, XY = end-face midpoints),
       duct_width_mm, duct_height_mm}
    """
    outlines = []

    bend_lookup = {}
    if plan_bend_outlines:
        for o in plan_bend_outlines:
            bend_lookup[(o["collection_id"], o["bend_idx"])] = o

    for col_id, run_ids in enumerate(collections):
        if not run_ids:
            continue

        # Per-collection OD: each collection sizes to its own (largest) pipe.
        half_od = _resolve_od(od_mm, col_id) / 2.0
        expand = half_od + cover_mm

        def run_length(r):
            return sum(np.linalg.norm(np.array(c["points"][-1]) - np.array(c["points"][0]))
                       for c in all_run_curves[r])
        ref_run_idx = max(run_ids, key=run_length)
        ref_curves = all_run_curves[ref_run_idx]

        for seg_idx, ref_crv in enumerate(ref_curves):
            if ref_crv["type"] != "Line":
                continue

            segs = []
            for r in run_ids:
                if seg_idx < len(all_run_curves[r]):
                    c = all_run_curves[r][seg_idx]
                    if c["type"] == "Line":
                        segs.append({
                            "sp": np.array(c["points"][0]),
                            "ep": np.array(c["points"][-1]),
                        })
            if not segs:
                continue

            avg_sp = np.mean([s["sp"] for s in segs], axis=0)
            avg_ep = np.mean([s["ep"] for s in segs], axis=0)

            # 3D axis check for tilt reporting
            axis3d = avg_ep - avg_sp
            length3d = float(np.linalg.norm(axis3d))
            if length3d < 1e-3:
                continue

            # PLAN axis (project to XY)
            dx = avg_ep[0] - avg_sp[0]
            dy = avg_ep[1] - avg_sp[1]
            plan_len = math.sqrt(dx * dx + dy * dy)
            if plan_len < 1e-3:
                continue  # purely vertical pipe — no plan footprint

            plan_axis = np.array([dx / plan_len, dy / plan_len])
            width_axis = np.array([-plan_axis[1], plan_axis[0]])  # 90° CCW perp in XY

            # Pipe spread in width (XY only)
            w_offsets = []
            for s in segs:
                for p in (s["sp"], s["ep"]):
                    d = np.array([p[0] - avg_sp[0], p[1] - avg_sp[1]])
                    w_offsets.append(float(np.dot(d, width_axis)))
            half_width = (max(w_offsets) - min(w_offsets)) / 2.0 + expand
            w_mid = (max(w_offsets) + min(w_offsets)) / 2.0

            centre_start_xy = np.array([avg_sp[0] + w_mid * width_axis[0],
                                        avg_sp[1] + w_mid * width_axis[1]])
            centre_end_xy   = np.array([avg_ep[0] + w_mid * width_axis[0],
                                        avg_ep[1] + w_mid * width_axis[1]])

            # Z envelope from pipes. Height = Δcentres + OD + 2*cover, matching
            # plan bends in the same collection. `expand` already = half_od + cover.
            all_z_top = []
            all_z_bot = []
            for s in segs:
                for p in (s["sp"], s["ep"]):
                    all_z_top.append(p[2] + expand)
                    all_z_bot.append(p[2] - expand)
            top_z_mm    = float(max(all_z_top))
            bottom_z_mm = float(min(all_z_bot))

            is_horizontal = abs(axis3d[2]) < 0.01 * length3d
            if not is_horizontal:
                # Skip tilted straights — handled by a separate button later
                continue

            prev_bend = bend_lookup.get((col_id, seg_idx - 1))
            next_bend = bend_lookup.get((col_id, seg_idx + 1))

            def bend_xy_corners(bend, at_start_of_bend, centre_ref_xy):
                """Return (plus_side_xy, minus_side_xy) from bend's connecting line."""
                if at_start_of_bend:
                    p_outer = np.array(bend["outer_start"][:2])
                    p_inner = np.array(bend["inner_start"][:2])
                else:
                    p_outer = np.array(bend["outer_end"][:2])
                    p_inner = np.array(bend["inner_end"][:2])
                d_outer = np.dot(p_outer - centre_ref_xy, width_axis)
                d_inner = np.dot(p_inner - centre_ref_xy, width_axis)
                if d_outer > d_inner:
                    return p_outer, p_inner
                return p_inner, p_outer

            # Start corners in XY
            if prev_bend:
                s_plus_xy, s_minus_xy = bend_xy_corners(prev_bend, False, centre_start_xy)
            else:
                s_plus_xy  = centre_start_xy + half_width * width_axis
                s_minus_xy = centre_start_xy - half_width * width_axis

            # End corners in XY
            if next_bend:
                e_plus_xy, e_minus_xy = bend_xy_corners(next_bend, True, centre_end_xy)
            else:
                e_plus_xy  = centre_end_xy + half_width * width_axis
                e_minus_xy = centre_end_xy - half_width * width_axis

            # CCW traversal viewed from +Z (above):
            # start_plus -> start_minus -> end_minus -> end_plus
            ref_z = bottom_z_mm
            c1 = [float(s_plus_xy[0]),  float(s_plus_xy[1]),  ref_z]
            c2 = [float(s_minus_xy[0]), float(s_minus_xy[1]), ref_z]
            c3 = [float(e_minus_xy[0]), float(e_minus_xy[1]), ref_z]
            c4 = [float(e_plus_xy[0]),  float(e_plus_xy[1]),  ref_z]

            # Duct parameters for the Build Ducts tool:
            #   - centreline = midpoint of each end face (c1/c2 start, c3/c4 end)
            #   - Z at mid-height of the pipe envelope; the build tool re-applies
            #     cover offset + 50 mm rounding, same as the extrusion
            #   - width = nominal rectangular profile. Ducts are constant cross-
            #     section; the extrusion may be trapezoidal when mating bends.
            z_mid = float((top_z_mm + bottom_z_mm) / 2.0)
            duct_start = [float((c1[0] + c2[0]) / 2.0),
                          float((c1[1] + c2[1]) / 2.0),
                          z_mid]
            duct_end   = [float((c3[0] + c4[0]) / 2.0),
                          float((c3[1] + c4[1]) / 2.0),
                          z_mid]

            outlines.append({
                "collection_id": col_id,
                "segment_idx":   seg_idx,
                "corner1": c1, "corner2": c2, "corner3": c3, "corner4": c4,
                "top_z_mm":      top_z_mm,
                "bottom_z_mm":   bottom_z_mm,
                "centre_start":  [float(avg_sp[0]), float(avg_sp[1]), float(avg_sp[2])],
                "centre_end":    [float(avg_ep[0]), float(avg_ep[1]), float(avg_ep[2])],
                "half_width_mm": float(half_width),
                "is_horizontal": bool(is_horizontal),
                "duct_start":    duct_start,
                "duct_end":      duct_end,
                "duct_width_mm":  float(half_width * 2.0),
                "duct_height_mm": float(top_z_mm - bottom_z_mm),
            })

    return outlines


def compute_sloped_straight_outlines(all_run_curves, collections, od_mm, cover_mm=0.0):
    """For each collection, find TILTED straight segments (pipes that drop in Z)
    and compute a 3D tilted-prism outline.

    Cross-section is a rectangle perpendicular to the pipe axis:
      width_axis  = horizontal perpendicular to pipe axis (stays horizontal)
      height_axis = perpendicular to both axis and width (tilts with pipe)

    End faces are perpendicular cross-sections. When vertical-bend outlines are
    built later, their connecting-line logic will handle end-face mating.

    Returns list of dicts with axis, length, half dims, and 8 corners
    (4 start + 4 end).
    """
    outlines = []

    for col_id, run_ids in enumerate(collections):
        if not run_ids:
            continue

        # Per-collection OD: each collection sizes to its own (largest) pipe.
        half_od = _resolve_od(od_mm, col_id) / 2.0
        expand = half_od + cover_mm

        def run_length(r):
            return sum(np.linalg.norm(np.array(c["points"][-1]) - np.array(c["points"][0]))
                       for c in all_run_curves[r])
        ref_run_idx = max(run_ids, key=run_length)
        ref_curves = all_run_curves[ref_run_idx]

        for seg_idx, ref_crv in enumerate(ref_curves):
            if ref_crv["type"] != "Line":
                continue

            segs = []
            for r in run_ids:
                if seg_idx < len(all_run_curves[r]):
                    c = all_run_curves[r][seg_idx]
                    if c["type"] == "Line":
                        segs.append({
                            "sp": np.array(c["points"][0]),
                            "ep": np.array(c["points"][-1]),
                        })
            if not segs:
                continue

            avg_sp = np.mean([s["sp"] for s in segs], axis=0)
            avg_ep = np.mean([s["ep"] for s in segs], axis=0)

            axis_vec = avg_ep - avg_sp
            length_mm = float(np.linalg.norm(axis_vec))
            if length_mm < 1e-3:
                continue
            axis = axis_vec / length_mm

            # Skip horizontal — those are handled by compute_straight_run_outlines
            if abs(axis[2]) < 0.01 * 1.0:
                continue

            # Skip pure verticals — those are risers, not sloped segments
            # (they'll be handled by a separate button later)
            if abs(axis[2]) > 0.99:
                continue

            # width_axis = horizontal perpendicular
            horiz = np.array([axis[0], axis[1], 0.0])
            h_len = np.linalg.norm(horiz)
            if h_len > 0.01:
                horiz_unit = horiz / h_len
                width_axis = np.array([-horiz_unit[1], horiz_unit[0], 0.0])
            else:
                # near-vertical pipe — very rare for straights; pick any horizontal
                width_axis = np.array([1.0, 0.0, 0.0])

            # height_axis perpendicular to both, points generally upward
            height_axis = np.cross(axis, width_axis)
            hn = np.linalg.norm(height_axis)
            if hn < 1e-6:
                height_axis = np.array([0.0, 0.0, 1.0])
            else:
                height_axis = height_axis / hn
            if height_axis[2] < 0:
                height_axis = -height_axis

            # Measure pipe spread in width and height axes (perpendicular plane)
            w_offsets = []; h_offsets = []
            for s in segs:
                for p in (s["sp"], s["ep"]):
                    delta = p - avg_sp
                    delta_perp = delta - np.dot(delta, axis) * axis
                    w_offsets.append(float(np.dot(delta_perp, width_axis)))
                    h_offsets.append(float(np.dot(delta_perp, height_axis)))

            half_width  = (max(w_offsets) - min(w_offsets)) / 2.0 + expand
            half_height = (max(h_offsets) - min(h_offsets)) / 2.0 + expand
            w_mid = (max(w_offsets) + min(w_offsets)) / 2.0
            h_mid = (max(h_offsets) + min(h_offsets)) / 2.0

            # Shift centre to group's geometric centre in the perpendicular plane
            centre_shift = w_mid * width_axis + h_mid * height_axis
            centre_start = avg_sp + centre_shift
            centre_end   = avg_ep + centre_shift

            def corner(centre, sign_w, sign_h):
                return centre + sign_w * half_width * width_axis \
                              + sign_h * half_height * height_axis

            # Order: -w-h, +w-h, +w+h, -w+h (CCW viewed from outside start face)
            sc1 = corner(centre_start, -1, -1)
            sc2 = corner(centre_start, +1, -1)
            sc3 = corner(centre_start, +1, +1)
            sc4 = corner(centre_start, -1, +1)
            ec1 = corner(centre_end,   -1, -1)
            ec2 = corner(centre_end,   +1, -1)
            ec3 = corner(centre_end,   +1, +1)
            ec4 = corner(centre_end,   -1, +1)

            outlines.append({
                "collection_id":  col_id,
                "segment_idx":    seg_idx,
                "centre_start":   centre_start.tolist(),
                "centre_end":     centre_end.tolist(),
                "length_mm":      length_mm,
                "axis":           axis.tolist(),
                "width_axis":     width_axis.tolist(),
                "height_axis":    height_axis.tolist(),
                "half_width_mm":  float(half_width),
                "half_height_mm": float(half_height),
                "start_corner1":  sc1.tolist(),
                "start_corner2":  sc2.tolist(),
                "start_corner3":  sc3.tolist(),
                "start_corner4":  sc4.tolist(),
                "end_corner1":    ec1.tolist(),
                "end_corner2":    ec2.tolist(),
                "end_corner3":    ec3.tolist(),
                "end_corner4":    ec4.tolist(),
            })

    return outlines


def sloped_straight_outlines_to_csv(outlines, output_path):
    """Write tilted-prism outlines as CSV."""
    header = ("Collection,SegmentIdx,"
              "CentreStart_X,CentreStart_Y,CentreStart_Z,"
              "CentreEnd_X,CentreEnd_Y,CentreEnd_Z,"
              "Length_mm,Axis_X,Axis_Y,Axis_Z,"
              "WidthAxis_X,WidthAxis_Y,WidthAxis_Z,"
              "HeightAxis_X,HeightAxis_Y,HeightAxis_Z,"
              "HalfWidth_mm,HalfHeight_mm,"
              "StartC1_X,StartC1_Y,StartC1_Z,"
              "StartC2_X,StartC2_Y,StartC2_Z,"
              "StartC3_X,StartC3_Y,StartC3_Z,"
              "StartC4_X,StartC4_Y,StartC4_Z,"
              "EndC1_X,EndC1_Y,EndC1_Z,"
              "EndC2_X,EndC2_Y,EndC2_Z,"
              "EndC3_X,EndC3_Y,EndC3_Z,"
              "EndC4_X,EndC4_Y,EndC4_Z")
    rows = []
    for o in outlines:
        vals = [
            o["collection_id"] + 1, o["segment_idx"],
            *o["centre_start"], *o["centre_end"],
            o["length_mm"], *o["axis"],
            *o["width_axis"], *o["height_axis"],
            o["half_width_mm"], o["half_height_mm"],
            *o["start_corner1"], *o["start_corner2"],
            *o["start_corner3"], *o["start_corner4"],
            *o["end_corner1"],   *o["end_corner2"],
            *o["end_corner3"],   *o["end_corner4"],
        ]
        rows.append(",".join("{:.4f}".format(v) if isinstance(v, float) else str(v)
                             for v in vals))
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(header + "\n")
        for r in rows:
            f.write(r + "\n")
    return len(rows)


def unify_sloped_collection_dims(plan_bend_outlines, straight_outlines,
                                  sloped_outlines):
    """Set each sloped-straight's half_width and half_height to match its
    ADJACENT horizontal straight segments (not the whole collection).

    Per segment:
      - half_width  = max HalfWidth_mm across horizontal straights at
                      segment_idx - 1 or segment_idx + 1 in the same collection.
      - half_height = max (TopZ - BottomZ) / 2 across those same adjacent
                      horizontal straights.
      - Fallback: keep the sloped segment's own computed values.

    After overriding, the 8 corners are rebuilt from the new dims so the
    written CSV matches the Revit extrusion shape.

    Modifies `sloped_outlines` in place.
    """
    import numpy as np

    # Lookup horizontal straights by (col_id, seg_idx)
    hstraight_lookup = {}
    for s in straight_outlines:
        hstraight_lookup[(s["collection_id"], s["segment_idx"])] = s

    # Per-collection max seg_idx (to cap walks)
    max_seg_by_col = {}
    for s in straight_outlines:
        cid = s["collection_id"]
        max_seg_by_col[cid] = max(max_seg_by_col.get(cid, 0), s["segment_idx"])
    for o in plan_bend_outlines:
        cid = o["collection_id"]
        max_seg_by_col[cid] = max(max_seg_by_col.get(cid, 0), o["bend_idx"])

    def nearest_hstraight(cid, seg, direction):
        """Walk outward from seg (not including seg itself) in +/-1 steps,
        return the first horizontal straight found in that direction."""
        step = 1 if direction > 0 else -1
        limit = max_seg_by_col.get(cid, 100)
        k = seg + step
        while 0 <= k <= limit + 2:  # +2 for safety margin
            found = hstraight_lookup.get((cid, k))
            if found:
                return found
            k += step
        return None

    for s in sloped_outlines:
        cid = s["collection_id"]
        seg = s["segment_idx"]

        # Nearest horizontal straight in each direction
        adj = []
        prev_s = nearest_hstraight(cid, seg, -1)
        next_s = nearest_hstraight(cid, seg, +1)
        if prev_s: adj.append(prev_s)
        if next_s: adj.append(next_s)

        if adj:
            new_hw = max(a["half_width_mm"] for a in adj)
            new_hh = max(a["top_z_mm"] - a["bottom_z_mm"] for a in adj) / 2.0
        else:
            new_hw = s["half_width_mm"]
            new_hh = s["half_height_mm"]

        s["half_width_mm"]  = float(new_hw)
        s["half_height_mm"] = float(new_hh)

        # Rebuild 8 corners from new dims
        centre_start = np.array(s["centre_start"])
        centre_end   = np.array(s["centre_end"])
        width_axis   = np.array(s["width_axis"])
        height_axis  = np.array(s["height_axis"])

        def corner(centre, sign_w, sign_h):
            return centre + sign_w * new_hw * width_axis \
                          + sign_h * new_hh * height_axis

        s["start_corner1"] = corner(centre_start, -1, -1).tolist()
        s["start_corner2"] = corner(centre_start, +1, -1).tolist()
        s["start_corner3"] = corner(centre_start, +1, +1).tolist()
        s["start_corner4"] = corner(centre_start, -1, +1).tolist()
        s["end_corner1"]   = corner(centre_end,   -1, -1).tolist()
        s["end_corner2"]   = corner(centre_end,   +1, -1).tolist()
        s["end_corner3"]   = corner(centre_end,   +1, +1).tolist()
        s["end_corner4"]   = corner(centre_end,   -1, +1).tolist()


def unify_collection_z_ranges(plan_bend_outlines, straight_outlines):
    """Set each straight's TopZ/BottomZ to match the plan bend(s) it connects to.

    For each straight:
      - If adjacent to a plan bend on either side, use max(TopZ) / min(BottomZ)
        across those adjacent bends only.
      - If no adjacent plan bend, keep the straight's own pipe envelope.

    Plan bends keep their own pipe-envelope Z range (unchanged).

    Modifies lists in place. Also updates corner Z values of straight outlines.

    Returns (straight_ranges_dict, plan_bend_ranges_dict) for optional printing.
    """
    bend_lookup = {}
    for o in plan_bend_outlines:
        bend_lookup[(o["collection_id"], o["bend_idx"])] = o

    for s in straight_outlines:
        cid = s["collection_id"]
        seg = s["segment_idx"]
        adj_bends = []
        pb = bend_lookup.get((cid, seg - 1))
        nb = bend_lookup.get((cid, seg + 1))
        if pb: adj_bends.append(pb)
        if nb: adj_bends.append(nb)
        if adj_bends:
            s["top_z_mm"]    = max(b["top_z_mm"]    for b in adj_bends)
            s["bottom_z_mm"] = min(b["bottom_z_mm"] for b in adj_bends)
            for key in ("corner1", "corner2", "corner3", "corner4"):
                s[key][2] = s["bottom_z_mm"]
            # Keep duct z-mid and height aligned with the unified envelope
            if "duct_start" in s and "duct_end" in s:
                z_mid = (s["top_z_mm"] + s["bottom_z_mm"]) / 2.0
                s["duct_start"][2] = z_mid
                s["duct_end"][2]   = z_mid
                s["duct_height_mm"] = s["top_z_mm"] - s["bottom_z_mm"]

    # Return a summary dict keyed by (col, seg) for optional printing
    s_ranges  = {(s["collection_id"], s["segment_idx"]):
                 (s["bottom_z_mm"], s["top_z_mm"]) for s in straight_outlines}
    pb_ranges = {(o["collection_id"], o["bend_idx"]):
                 (o["bottom_z_mm"], o["top_z_mm"]) for o in plan_bend_outlines}
    return s_ranges, pb_ranges


def straight_outlines_to_csv(outlines, output_path):
    """Write straight-run outlines as CSV (4 plan corners + TopZ/BottomZ +
    duct parameters for the Build Ducts tool)."""
    header = ("Collection,SegmentIdx,"
              "TopZ_mm,BottomZ_mm,ZBase_mm,Height_mm,"
              "Corner1_X,Corner1_Y,Corner1_Z,"
              "Corner2_X,Corner2_Y,Corner2_Z,"
              "Corner3_X,Corner3_Y,Corner3_Z,"
              "Corner4_X,Corner4_Y,Corner4_Z,"
              "CentreStart_X,CentreStart_Y,CentreStart_Z,"
              "CentreEnd_X,CentreEnd_Y,CentreEnd_Z,"
              "HalfWidth_mm,IsHorizontal,"
              "DuctStart_X,DuctStart_Y,DuctStart_Z,"
              "DuctEnd_X,DuctEnd_Y,DuctEnd_Z,"
              "DuctWidth_mm,DuctHeight_mm")
    rows = []
    for o in outlines:
        vals = [
            o["collection_id"] + 1, o["segment_idx"],
            o["top_z_mm"], o["bottom_z_mm"], o["bottom_z_mm"], o["top_z_mm"] - o["bottom_z_mm"],
            *o["corner1"], *o["corner2"], *o["corner3"], *o["corner4"],
            *o["centre_start"], *o["centre_end"],
            o["half_width_mm"], 1 if o["is_horizontal"] else 0,
            *o["duct_start"], *o["duct_end"],
            o["duct_width_mm"], o["duct_height_mm"],
        ]
        rows.append(",".join("{:.4f}".format(v) if isinstance(v, float) else str(v)
                             for v in vals))
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(header + "\n")
        for r in rows:
            f.write(r + "\n")
    return len(rows)
